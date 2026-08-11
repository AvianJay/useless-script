import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AICommands


class AIToolCallParsingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace(get_guild=lambda _guild_id: None))

    def test_emulated_tool_call_requires_explicit_envelope(self):
        message = SimpleNamespace(
            content=(
                '{"tool_calls":['
                '{"name":"get_bot_status","arguments":{}}'
                "]}"
            ),
            tool_calls=None,
        )

        calls = self.cog._extract_emulated_tool_calls(message)

        self.assertEqual(
            calls,
            [
                {
                    "id": "call_1",
                    "name": "get_bot_status",
                    "arguments": {},
                }
            ],
        )

    def test_activity_json_array_is_not_treated_as_tool_calls(self):
        message = SimpleNamespace(
            content=(
                "```json\n"
                '[{"type":"playing","name":"反覆重啟人生模擬器"},'
                '{"type":"watching","name":"Genshin Impact 啟動"},'
                '{"type":"listening","name":"你按下了 Alt+F4 試試"}]\n'
                "```"
            ),
            tool_calls=None,
        )

        self.assertEqual(self.cog._extract_emulated_tool_calls(message), [])
        progress_text = self.cog._extract_emulated_tool_progress_text(message.content)
        self.assertIn("反覆重啟人生模擬器", progress_text)
        self.assertIn("Genshin Impact 啟動", progress_text)

    def test_native_tool_call_is_still_supported(self):
        message = SimpleNamespace(
            content="我查一下。",
            tool_calls=[
                {
                    "id": "native-call-1",
                    "function": {
                        "name": "get_bot_status",
                        "arguments": "{}",
                    },
                }
            ],
        )

        self.assertEqual(
            self.cog._extract_native_tool_calls(message),
            [
                {
                    "id": "native-call-1",
                    "name": "get_bot_status",
                    "arguments": "{}",
                }
            ],
        )

    def test_single_named_json_object_is_not_treated_as_tool_call(self):
        message = SimpleNamespace(
            content='{"type":"playing","name":"角蛙 yt.ai"}',
            tool_calls=None,
        )

        self.assertEqual(self.cog._extract_emulated_tool_calls(message), [])

    async def test_native_response_never_parses_emulated_json_from_content(self):
        content = '{"tool_calls":[{"name":"get_bot_status","arguments":{}}]}'
        response = SimpleNamespace(
            model="test-model",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=content,
                        tool_calls=None,
                        images=None,
                    )
                )
            ],
        )

        with (
            patch.object(
                self.cog,
                "_request_ai_completion",
                new=AsyncMock(return_value=(response, "native")),
            ),
            patch.object(
                self.cog,
                "_execute_ai_tool",
                new=AsyncMock(),
            ) as execute_tool,
        ):
            response_text, model, _elapsed = await self.cog.generate_response(
                [{"role": "user", "content": "給我幾個 Discord 狀態"}],
                model="test-model",
                tool_context={"user": SimpleNamespace(id=1)},
            )

        self.assertEqual(response_text, content)
        self.assertEqual(model, "test-model")
        execute_tool.assert_not_awaited()

    async def test_native_success_does_not_prepare_emulation_prompt(self):
        messages = [{"role": "user", "content": "bot 在線嗎"}]
        tools = [{"type": "function", "function": {"name": "get_bot_status"}}]
        response = SimpleNamespace(model="test-model", choices=[])

        with (
            patch("ai._resolve_ai_tool_call_mode", return_value="native"),
            patch.object(
                self.cog,
                "_generate_ai_completion",
                new=AsyncMock(return_value=response),
            ) as generate,
            patch.object(
                self.cog,
                "_prepare_tool_emulation_messages",
                new=MagicMock(),
            ) as prepare_emulation,
        ):
            actual_response, mode = await self.cog._request_ai_completion(
                messages,
                model="test-model",
                tools=tools,
            )

        self.assertIs(actual_response, response)
        self.assertEqual(mode, "native")
        prepare_emulation.assert_not_called()
        generate.assert_awaited_once_with(
            messages=messages,
            model="test-model",
            tools=tools,
        )

    async def test_unsupported_native_tools_error_falls_back_to_emulation(self):
        class NativeToolsUnsupportedError(Exception):
            status_code = 400

        messages = [{"role": "user", "content": "bot 在線嗎"}]
        tools = [{"type": "function", "function": {"name": "get_bot_status"}}]
        emulated_messages = messages + [{"role": "system", "content": "tool protocol"}]
        response = SimpleNamespace(model="test-model", choices=[])
        generate = AsyncMock(
            side_effect=[
                NativeToolsUnsupportedError("tools are not supported"),
                response,
            ]
        )

        with (
            patch("ai._resolve_ai_tool_call_mode", return_value="native"),
            patch.object(self.cog, "_generate_ai_completion", new=generate),
            patch.object(
                self.cog,
                "_prepare_tool_emulation_messages",
                new=MagicMock(return_value=emulated_messages),
            ) as prepare_emulation,
            patch("ai._mark_ai_native_tools_unsupported") as mark_unsupported,
        ):
            actual_response, mode = await self.cog._request_ai_completion(
                messages,
                model="test-model",
                tools=tools,
            )

        self.assertIs(actual_response, response)
        self.assertEqual(mode, "emulated")
        prepare_emulation.assert_called_once_with(messages, tools)
        mark_unsupported.assert_called_once_with("test-model")
        self.assertEqual(generate.await_count, 2)
        self.assertEqual(generate.await_args_list[0].kwargs["messages"], messages)
        self.assertIs(generate.await_args_list[0].kwargs["tools"], tools)
        self.assertEqual(generate.await_args_list[1].kwargs["messages"], emulated_messages)
        self.assertNotIn("tools", generate.await_args_list[1].kwargs)

    async def test_unrelated_native_error_does_not_enable_emulation(self):
        class NativeServerError(Exception):
            status_code = 500

        messages = [{"role": "user", "content": "bot 在線嗎"}]
        tools = [{"type": "function", "function": {"name": "get_bot_status"}}]

        with (
            patch("ai._resolve_ai_tool_call_mode", return_value="native"),
            patch.object(
                self.cog,
                "_generate_ai_completion",
                new=AsyncMock(side_effect=NativeServerError("tool backend failed")),
            ),
            patch.object(
                self.cog,
                "_prepare_tool_emulation_messages",
                new=MagicMock(),
            ) as prepare_emulation,
        ):
            with self.assertRaises(NativeServerError):
                await self.cog._request_ai_completion(
                    messages,
                    model="test-model",
                    tools=tools,
                )

        prepare_emulation.assert_not_called()

    async def test_explicit_emulated_mode_prepares_protocol_without_native_request(self):
        messages = [{"role": "user", "content": "bot 在線嗎"}]
        tools = [{"type": "function", "function": {"name": "get_bot_status"}}]
        emulated_messages = messages + [{"role": "system", "content": "tool protocol"}]
        response = SimpleNamespace(model="test-model", choices=[])

        with (
            patch("ai._resolve_ai_tool_call_mode", return_value="emulated"),
            patch.object(
                self.cog,
                "_generate_ai_completion",
                new=AsyncMock(return_value=response),
            ) as generate,
            patch.object(
                self.cog,
                "_prepare_tool_emulation_messages",
                new=MagicMock(return_value=emulated_messages),
            ) as prepare_emulation,
        ):
            actual_response, mode = await self.cog._request_ai_completion(
                messages,
                model="test-model",
                tools=tools,
            )

        self.assertIs(actual_response, response)
        self.assertEqual(mode, "emulated")
        prepare_emulation.assert_called_once_with(messages, tools)
        generate.assert_awaited_once_with(
            messages=emulated_messages,
            model="test-model",
        )


if __name__ == "__main__":
    unittest.main()
