import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import ai_provider
from ai import AICommands


def completion_response(content="done", *, model="text-model", tool_calls=None):
    return SimpleNamespace(
        model=model,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    tool_calls=tool_calls,
                    images=None,
                )
            )
        ],
    )


class AIVisionProviderConfigTests(unittest.TestCase):
    def setUp(self):
        self.store = {
            ai_provider.AI_MODELS_CONFIG_KEY: {
                "text-model": 0.1,
                "vision-model": 0.2,
                "other-vision": 0.3,
            }
        }

        def get_config(key, default=None):
            return self.store.get(key, default)

        def set_config(key, value):
            self.store[key] = value

        self.get_patch = patch.object(ai_provider, "get_global_config", side_effect=get_config)
        self.set_patch = patch.object(ai_provider, "set_global_config", side_effect=set_config)
        self.get_patch.start()
        self.set_patch.start()

    def tearDown(self):
        self.set_patch.stop()
        self.get_patch.stop()

    def test_vision_config_defaults_are_empty_and_malformed_values_are_ignored(self):
        self.assertEqual(ai_provider.get_ai_vision_models(), [])
        self.assertEqual(ai_provider.get_ai_vision_model(), "")

        self.store[ai_provider.AI_VISION_MODELS_CONFIG_KEY] = {"vision-model": True}
        self.store[ai_provider.AI_VISION_MODEL_CONFIG_KEY] = "vision-model"

        self.assertEqual(ai_provider.get_ai_vision_models(), [])
        self.assertEqual(ai_provider.get_ai_vision_model(), "")

    def test_setting_delegate_marks_model_and_resolution_prefers_current_vision_model(self):
        ai_provider.set_ai_vision_model("vision-model")
        ai_provider.set_ai_vision_models(["vision-model", "other-vision"])

        self.assertEqual(ai_provider.get_ai_vision_model(), "vision-model")
        self.assertEqual(
            ai_provider.get_ai_vision_models(),
            ["vision-model", "other-vision"],
        )
        self.assertEqual(ai_provider.resolve_ai_vision_model("other-vision"), "other-vision")
        self.assertEqual(ai_provider.resolve_ai_vision_model("text-model"), "vision-model")

    def test_removing_models_prunes_vision_tags_and_delegate(self):
        ai_provider.set_ai_vision_models(["vision-model", "other-vision"])
        ai_provider.set_ai_vision_model("other-vision")

        ai_provider.set_ai_model_rates({"text-model": 0.1, "vision-model": 0.2})

        self.assertEqual(ai_provider.get_ai_vision_models(), ["vision-model"])
        self.assertEqual(ai_provider.get_ai_vision_model(), "")

    def test_removing_delegate_vision_tag_clears_delegate(self):
        ai_provider.set_ai_vision_model("vision-model")

        ai_provider.set_ai_vision_models([])

        self.assertEqual(ai_provider.get_ai_vision_models(), [])
        self.assertEqual(ai_provider.get_ai_vision_model(), "")

    def test_delegate_rejects_unknown_text_model(self):
        with self.assertRaisesRegex(ValueError, "Model not found"):
            ai_provider.set_ai_vision_model("missing-model")

    def test_model_display_marks_vision_capability(self):
        rendered = ai_provider.format_ai_models_for_display(
            {"text-model": 0.1, "vision-model": 0.2},
            ["vision-model"],
        )

        self.assertIn("text-model: 0.10/C", rendered)
        self.assertIn("vision-model: 0.20/C [vision]", rendered)


class AIVisionRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_autocomplete_marks_vision_models(self):
        cog = AICommands(SimpleNamespace())
        with (
            patch("ai._get_ai_model_rates", return_value={"text-model": 0.1, "vision-model": 0.2}),
            patch("ai._get_ai_vision_models", return_value=["vision-model"]),
        ):
            choices = await cog.model_select_autocomplete(SimpleNamespace(), "")

        labels = {choice.value: choice.name for choice in choices}
        self.assertNotIn("[vision]", labels["text-model"])
        self.assertIn("[vision]", labels["vision-model"])

    async def test_visual_model_receives_direct_image(self):
        cog = AICommands(SimpleNamespace())
        request = AsyncMock(return_value=(completion_response(model="vision-model"), "native"))

        with (
            patch("ai._is_ai_vision_model", return_value=True),
            patch.object(cog, "_request_ai_completion", new=request),
        ):
            await cog.generate_response(
                [{"role": "user", "content": "describe"}],
                model="vision-model",
                image=b"image-bytes",
                tool_context={"user": SimpleNamespace(id=1)},
            )

        self.assertEqual(request.await_args_list[0].kwargs["image"], b"image-bytes")

    async def test_non_visual_model_never_receives_direct_image(self):
        cog = AICommands(SimpleNamespace())
        request = AsyncMock(return_value=(completion_response(model="text-model"), "native"))

        with (
            patch("ai._is_ai_vision_model", return_value=False),
            patch.object(cog, "_request_ai_completion", new=request),
        ):
            await cog.generate_response(
                [{"role": "user", "content": "describe"}],
                model="text-model",
                image=b"hidden-image",
                tool_context={"user": SimpleNamespace(id=1)},
            )

        self.assertIsNone(request.await_args_list[0].kwargs["image"])

    async def test_native_image_tool_result_returns_to_original_text_model(self):
        cog = AICommands(SimpleNamespace())
        first = completion_response(
            "I will inspect it.",
            model="text-model",
            tool_calls=[
                {
                    "id": "vision-call",
                    "function": {
                        "name": "image_analyze",
                        "arguments": '{"source":"current_attachment"}',
                    },
                }
            ],
        )
        second = completion_response("The image contains a cat.", model="text-model")
        request = AsyncMock(side_effect=[(first, "native"), (second, "native")])
        execute = AsyncMock(return_value={"ok": True, "data": {"summary": "a cat"}})

        with (
            patch("ai._is_ai_vision_model", return_value=False),
            patch.object(cog, "_request_ai_completion", new=request),
            patch.object(cog, "_execute_ai_tool", new=execute),
        ):
            text, model, _ = await cog.generate_response(
                [{"role": "user", "content": "what is this?"}],
                model="text-model",
                image=b"hidden-image",
                tool_context={"user": SimpleNamespace(id=1)},
            )

        self.assertEqual(text, "The image contains a cat.")
        self.assertEqual(model, "text-model")
        self.assertTrue(all(call.kwargs["model"] == "text-model" for call in request.await_args_list))
        self.assertIsNone(request.await_args_list[0].kwargs["image"])
        execute.assert_awaited_once_with(
            "image_analyze",
            {"source": "current_attachment"},
            {"user": unittest.mock.ANY},
        )

    async def test_emulated_image_tool_result_returns_to_original_text_model(self):
        cog = AICommands(SimpleNamespace())
        first = completion_response(
            '{"tool_calls":[{"name":"image_analyze","arguments":{"source":"current_attachment"}}]}',
            model="text-model",
        )
        second = completion_response("The image contains a dog.", model="text-model")
        request = AsyncMock(side_effect=[(first, "emulated"), (second, "emulated")])
        execute = AsyncMock(return_value={"ok": True, "data": {"summary": "a dog"}})

        with (
            patch("ai._is_ai_vision_model", return_value=False),
            patch.object(cog, "_request_ai_completion", new=request),
            patch.object(cog, "_execute_ai_tool", new=execute),
        ):
            text, model, _ = await cog.generate_response(
                [{"role": "user", "content": "what is this?"}],
                model="text-model",
                image=b"hidden-image",
                tool_context={"user": SimpleNamespace(id=1)},
            )

        self.assertEqual((text, model), ("The image contains a dog.", "text-model"))
        execute.assert_awaited_once()


class AIImageAnalyzeDelegationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())
        self.cog._fetch_discord_image_bytes = AsyncMock(return_value=(b"image-bytes", None))
        self.cog._resolve_ai_billing_target = AsyncMock(
            return_value={
                "payer_id": 7,
                "payer_user": None,
                "display_name": "payer",
            }
        )
        self.cog._get_global_balance = Mock(return_value=1000.0)
        self.cog._charge_global_balance = Mock(return_value=(25.0, 975.0))
        self.cog._refund_global_balance = Mock(return_value=1000.0)
        self.cog._log_economy_transaction = Mock()
        self.cog._queue_economy_audit_log = Mock()
        self.context = {
            "user": SimpleNamespace(id=11),
            "guild": None,
            "model": "text-model",
            "request_image_attachment": SimpleNamespace(
                url="https://cdn.discordapp.com/attachments/1/2/image.png",
            ),
        }

    async def test_current_attachment_uses_resolved_delegate_and_charges_once(self):
        self.cog._generate_ai_completion = AsyncMock(
            return_value=completion_response("a blue circle", model="provider-vision")
        )

        with patch("ai._resolve_ai_vision_model", return_value="vision-delegate"):
            result = await self.cog._tool_image_analyze(
                {"source": "current_attachment", "prompt": "What is visible?"},
                self.context,
            )

        self.assertEqual(result["cost"], 25.0)
        self.assertEqual(result["analysis_model"], "provider-vision")
        self.assertEqual(result["summary"], "a blue circle")
        self.assertEqual(
            self.cog._generate_ai_completion.await_args.kwargs["model"],
            "vision-delegate",
        )
        self.assertEqual(self.cog._generate_ai_completion.await_args.kwargs["image"], b"image-bytes")
        self.cog._charge_global_balance.assert_called_once_with(7, 25.0)

    async def test_discord_cdn_url_source_is_still_supported(self):
        self.cog._generate_ai_completion = AsyncMock(
            return_value=completion_response("a green square", model="provider-vision")
        )
        image_url = "https://media.discordapp.net/attachments/1/2/image.png"

        with patch("ai._resolve_ai_vision_model", return_value="vision-delegate"):
            result = await self.cog._tool_image_analyze(
                {"image_url": image_url},
                self.context,
            )

        self.assertEqual(result["image_url"], image_url)
        self.assertEqual(result["cost"], 25.0)
        self.cog._fetch_discord_image_bytes.assert_awaited_once_with(image_url)

    async def test_tool_rejects_ambiguous_or_missing_source_before_charging(self):
        both = await self.cog._tool_image_analyze(
            {
                "source": "current_attachment",
                "image_url": "https://cdn.discordapp.com/attachments/1/2/image.png",
            },
            self.context,
        )
        neither = await self.cog._tool_image_analyze({}, self.context)

        self.assertIn("exactly one", both["error"])
        self.assertIn("exactly one", neither["error"])
        self.cog._charge_global_balance.assert_not_called()

    async def test_tool_without_current_attachment_fails_before_charging(self):
        context = dict(self.context)
        context.pop("request_image_attachment")

        result = await self.cog._tool_image_analyze(
            {"source": "current_attachment"},
            context,
        )

        self.assertIn("no image attachment", result["error"])
        self.cog._charge_global_balance.assert_not_called()

    async def test_tool_without_visual_model_fails_before_charging(self):
        with patch("ai._resolve_ai_vision_model", return_value=""):
            result = await self.cog._tool_image_analyze(
                {"source": "current_attachment"},
                self.context,
            )

        self.assertIn("not configured", result["error"])
        self.cog._charge_global_balance.assert_not_called()

    async def test_provider_failure_refunds_charge(self):
        self.cog._generate_ai_completion = AsyncMock(side_effect=RuntimeError("provider down"))

        with patch("ai._resolve_ai_vision_model", return_value="vision-delegate"):
            result = await self.cog._tool_image_analyze(
                {"source": "current_attachment"},
                self.context,
            )

        self.assertIn("provider down", result["error"])
        self.assertEqual(result["refunded"], 25.0)
        self.cog._refund_global_balance.assert_called_once_with(7, 25.0)

    def test_tool_schema_exposes_current_attachment_but_not_model_override(self):
        tools = self.cog._build_ai_tools()
        image_tool = next(item["function"] for item in tools if item["function"]["name"] == "image_analyze")
        properties = image_tool["parameters"]["properties"]

        self.assertIn("source", properties)
        self.assertIn("image_url", properties)
        self.assertNotIn("model", properties)

    def test_runtime_and_emulated_prompts_describe_current_attachment_rule(self):
        with patch("ai._is_ai_vision_model", return_value=False):
            runtime = self.cog._build_runtime_prompt_context(self.context)
        emulated = self.cog._prepare_tool_emulation_messages(
            [{"role": "user", "content": "what is this?"}],
            self.cog._build_ai_tools(),
        )

        self.assertIn("source=current_attachment", runtime)
        self.assertIn("source=current_attachment", emulated[0]["content"])

    def test_owner_config_group_registers_vision_commands(self):
        command_names = {command.name for command in AICommands.ai_config_text.commands}

        self.assertTrue(
            {"vision-models", "vision-tag", "vision-delegate"}.issubset(command_names)
        )


if __name__ == "__main__":
    unittest.main()
