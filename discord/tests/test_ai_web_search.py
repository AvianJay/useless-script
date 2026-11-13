import asyncio
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import ai_provider
from ai import AICommands


def search_response():
    return {
        "status": "completed",
        "model": "gpt-6.1-terra",
        "output": [
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {
                    "type": "search",
                    "sources": [
                        {"url": "https://example.com/retrieved"},
                        {"url": "https://example.com/cited"},
                    ],
                },
            },
            {
                "type": "message",
                "content": [{
                    "type": "output_text",
                    "text": "A verified answer.\ue200cite\ue202turn0search0\ue201",
                    "annotations": [{
                        "type": "url_citation",
                        "url": "https://example.com/cited",
                        "title": "Official source",
                    }],
                }],
            },
        ],
    }


class AISearchProviderConfigTests(unittest.TestCase):
    def setUp(self):
        self.store = {
            "ai_endpoint": "https://chat.example/v1",
            "ai_api_key": "test-chat-credential",
            "ai_default_model": "chat-model",
        }
        self.addCleanup(patch.stopall)
        patch.object(ai_provider, "get_global_config", side_effect=lambda key, default=None: self.store.get(key, default)).start()
        patch.object(ai_provider, "set_global_config", side_effect=self.store.__setitem__).start()

    def test_search_defaults_are_independent_of_chat(self):
        self.assertEqual(ai_provider.get_ai_search_model(), "gpt-6.1-terra")
        self.assertEqual(ai_provider.get_ai_search_endpoint(), "https://api.openai.com/v1")
        self.assertEqual(ai_provider.get_ai_search_api_key(), "")
        with self.assertRaisesRegex(RuntimeError, "ai_search_api_key is not configured"):
            ai_provider.create_ai_search_client()

    def test_search_settings_do_not_change_chat_settings(self):
        original = self.store.copy()
        ai_provider.set_ai_search_endpoint(" https://search.example/v1/ ")
        ai_provider.set_ai_search_api_key(" test-search-credential ")
        ai_provider.set_ai_search_model(" Custom-Search-Model ")
        self.assertEqual(ai_provider.get_ai_search_endpoint(), "https://search.example/v1")
        self.assertEqual(ai_provider.get_ai_search_api_key(), "test-search-credential")
        self.assertEqual(ai_provider.get_ai_search_model(), "Custom-Search-Model")
        for key, value in original.items():
            self.assertEqual(self.store[key], value)

    def test_async_client_has_bounded_retries_and_timeout(self):
        self.store.update(ai_search_api_key="test-search-credential", ai_search_endpoint="https://search.example/v1")
        with patch.object(ai_provider, "AsyncOpenAI") as factory:
            self.assertIs(ai_provider.create_ai_search_client(), factory.return_value)
        factory.assert_called_once_with(
            api_key="test-search-credential", base_url="https://search.example/v1", timeout=90.0, max_retries=2,
        )

    def test_empty_model_and_endpoint_use_defaults(self):
        self.store.update(ai_search_model="  ", ai_search_endpoint=None)
        self.assertEqual(ai_provider.get_ai_search_model(), "gpt-6.1-terra")
        self.assertEqual(ai_provider.get_ai_search_endpoint(), ai_provider.DEFAULT_AI_SEARCH_ENDPOINT)


class AIWebSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())
        self.response = search_response()
        self.create = AsyncMock(side_effect=lambda **_: copy.deepcopy(self.response))
        self.context = AsyncMock()
        self.context.__aenter__.return_value = SimpleNamespace(responses=SimpleNamespace(create=self.create))
        self.context.__aexit__.return_value = False
        self.addCleanup(patch.stopall)
        self.factory = patch("ai._create_ai_search_client", return_value=self.context).start()
        self.model = patch("ai._get_ai_search_model", return_value="gpt-6.1-terra").start()
        self.key = patch("ai._get_ai_search_api_key", return_value="test-search-credential").start()
        self.chat = patch.object(self.cog, "_generate_ai_completion", new_callable=AsyncMock).start()
        self.log = patch("ai.log").start()

    async def test_search_uses_responses_and_forces_native_search(self):
        result = await self.cog._tool_search_ai({"query": "current release"}, {})
        self.assertNotIn("error", result)
        kwargs = self.create.await_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-6.1-terra")
        self.assertEqual(kwargs["input"], "current release")
        self.assertEqual(kwargs["tools"], [{"type": "web_search", "search_context_size": "low"}])
        self.assertEqual(kwargs["tool_choice"], {"type": "web_search"})
        self.assertEqual(kwargs["include"], ["web_search_call.action.sources"])
        self.assertEqual(kwargs["reasoning"], {"effort": "low"})
        self.assertFalse(kwargs["store"])
        self.assertGreaterEqual(kwargs["max_output_tokens"], 1600)
        self.assertNotIn("max_tokens", kwargs)
        self.chat.assert_not_awaited()
        self.context.__aexit__.assert_awaited_once()

    async def test_citations_precede_retrieved_sources_and_are_deduplicated(self):
        result = await self.cog._tool_search_ai({"query": "release"}, {})
        self.assertEqual(result["sources"], ["https://example.com/cited", "https://example.com/retrieved"])
        self.assertEqual(result["citations"][0]["title"], "Official source")
        self.assertEqual(result["summary"], "A verified answer.")

    async def test_source_limit_and_invalid_source_urls(self):
        self.response["output"][0]["action"]["sources"] = [
            {"url": "javascript:alert(1)"},
            {"url": "http://localhost/private"},
            {"url": "https://user:password@example.com/secret"},
            *[{"url": f"https://example.com/source{i}"} for i in range(10)],
        ]
        result = await self.cog._tool_search_ai({"query": "release"}, {})
        self.assertEqual(len(result["sources"]), 4)
        self.assertTrue(all(url.startswith("https://example.com/") for url in result["sources"]))

    async def test_include_sources_false_omits_source_metadata(self):
        result = await self.cog._tool_search_ai({"query": "release", "include_sources": False}, {})
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["citations"], [])
        self.assertIn("Do not include source URLs", self.create.await_args.kwargs["instructions"])

    async def test_legacy_and_arbitrary_model_arguments_use_configured_model(self):
        for requested in ("perplexity-fast", "unconfigured-model"):
            with self.subTest(requested=requested):
                result = await self.cog._tool_search_ai({"query": "release", "model": requested}, {})
                self.assertEqual(result["requested_model"], "auto")
                self.assertEqual(self.create.await_args.kwargs["model"], "gpt-6.1-terra")

    async def test_explicit_configured_model_preserves_case(self):
        self.model.return_value = "Custom-Search-Model"
        result = await self.cog._tool_search_ai({"query": "release", "model": "Custom-Search-Model"}, {})
        self.assertEqual(result["requested_model"], "Custom-Search-Model")
        self.assertEqual(result["search_model"], "Custom-Search-Model")

    async def test_summary_length_and_reasoning_budget(self):
        self.response["output"][1]["content"][0]["text"] = "x" * 1000
        result = await self.cog._tool_search_ai({"query": "release", "max_chars": 120}, {})
        self.assertLessEqual(len(result["summary"]), 120)
        self.assertGreaterEqual(self.create.await_args.kwargs["max_output_tokens"], 1600)

    async def test_sdk_response_object_is_supported(self):
        self.create.side_effect = None
        self.create.return_value = SimpleNamespace(model_dump=lambda: self.response)
        result = await self.cog._tool_search_ai({"query": "release"}, {})
        self.assertEqual(result["returned_model"], "gpt-6.1-terra")

    async def test_plain_model_answer_without_search_is_rejected(self):
        self.response["output"].pop(0)
        result = await self.cog._tool_search_ai({"query": "release"}, {})
        self.assertIn("no completed web search call", result["error"])

    async def test_failed_search_call_is_rejected(self):
        self.response["output"][0]["status"] = "failed"
        result = await self.cog._tool_search_ai({"query": "release"}, {})
        self.assertIn("no completed web search call", result["error"])

    async def test_incomplete_response_is_rejected(self):
        self.response["status"] = "incomplete"
        result = await self.cog._tool_search_ai({"query": "release"}, {})
        self.assertIn("not completed", result["error"])

    async def test_malformed_output_is_rejected(self):
        self.response["output"] = "invalid output"
        result = await self.cog._tool_search_ai({"query": "release"}, {})
        self.assertIn("invalid output", result["error"])

    async def test_unknown_items_and_malformed_source_entries_are_ignored(self):
        self.response["output"].append(None)
        self.response["output"][0]["action"]["sources"].append(None)
        self.response["output"][1]["content"].append(None)
        self.response["output"][1]["content"][0]["annotations"].append(None)
        result = await self.cog._tool_search_ai({"query": "release"}, {})
        self.assertNotIn("error", result)
        self.assertEqual(len(result["sources"]), 2)

    async def test_empty_answer_is_rejected(self):
        self.response["output"].pop(1)
        result = await self.cog._tool_search_ai({"query": "release"}, {})
        self.assertIn("empty answer", result["error"])

    async def test_missing_query_or_key_never_contacts_provider(self):
        self.assertIn("error", await self.cog._tool_search_ai({"query": "  "}, {}))
        self.key.return_value = ""
        self.assertIn("ai_search_api_key", (await self.cog._tool_search_ai({"query": "release"}, {}))["error"])
        self.factory.assert_not_called()

    async def test_provider_error_body_is_not_exposed_or_logged(self):
        error = RuntimeError("upstream echoed test-search-credential")
        error.status_code = 403
        self.create.side_effect = error
        result = await self.cog._tool_search_ai({"query": "release"}, {})
        self.assertIn("HTTP 403", result["error"])
        self.assertNotIn("test-search-credential", str(result) + str(self.log.call_args))

    async def test_timeout_returns_error_and_closes_client(self):
        self.create.side_effect = TimeoutError()
        result = await self.cog._tool_search_ai({"query": "release"}, {})
        self.assertIn("timed out", result["error"])
        self.context.__aexit__.assert_awaited_once()

    async def test_cancellation_propagates_and_closes_client(self):
        self.create.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.cog._tool_search_ai({"query": "release"}, {})
        self.context.__aexit__.assert_awaited_once()

    async def test_api_key_command_never_echoes_key(self):
        ctx = SimpleNamespace(send=AsyncMock())
        with patch("ai._set_ai_search_api_key") as setter:
            await AICommands.ai_config_search_api_key_text.callback(self.cog, ctx, api_key="test-search-credential")
        setter.assert_called_once_with("test-search-credential")
        self.assertNotIn("test-search-credential", str(ctx.send.call_args))
        self.assertIn("configured", ctx.send.call_args.args[0])

    async def test_endpoint_command_rejects_credentials_without_echoing_them(self):
        ctx = SimpleNamespace(send=AsyncMock())
        with patch("ai._set_ai_search_endpoint") as setter:
            await AICommands.ai_config_search_endpoint_text.callback(
                self.cog, ctx, endpoint="https://user:test-search-credential@example.com/v1",
            )
        setter.assert_not_called()
        self.assertNotIn("test-search-credential", str(ctx.send.call_args))

    def test_schema_uses_configured_search_model(self):
        tools = self.cog._build_ai_tools()
        search = next(tool["function"] for tool in tools if tool["function"]["name"] == "search_ai")
        self.assertEqual(search["parameters"]["properties"]["model"]["enum"], ["auto", "gpt-6.1-terra"])


if __name__ == "__main__":
    unittest.main()
