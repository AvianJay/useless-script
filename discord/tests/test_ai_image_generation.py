import base64
import io
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from PIL import Image


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import ai_provider
from ai import AICommands


IMAGE_URL = "https://cdn.discordapp.com/attachments/1/2/reference.png"


def image_bytes(image_format="PNG"):
    output = io.BytesIO()
    Image.new("RGB", (8, 6), (20, 40, 60)).save(output, format=image_format)
    return output.getvalue()


class AIImageInputConfigTests(unittest.TestCase):
    def setUp(self):
        self.store = {
            ai_provider.AI_IMAGE_MODELS_CONFIG_KEY: {
                "gpt-image-2": 250.0,
                "another-image-model": 100.0,
            }
        }
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(
            ai_provider, "get_global_config",
            side_effect=lambda key, default=None: self.store.get(key, default),
        ))
        self.stack.enter_context(patch.object(
            ai_provider, "set_global_config",
            side_effect=lambda key, value: self.store.__setitem__(key, value),
        ))

    def test_only_gpt_image_2_accepts_image_input_by_default(self):
        self.assertEqual(ai_provider.get_ai_image_input_models(), ["gpt-image-2"])

    def test_tags_filter_unknown_models_and_duplicates(self):
        ai_provider.set_ai_image_input_models([
            "another-image-model", "unknown", "another-image-model",
        ])
        self.assertEqual(ai_provider.get_ai_image_input_models(), ["another-image-model"])

    def test_empty_and_malformed_tags_do_not_enable_image_input(self):
        for value in ([], {"gpt-image-2": True}, "gpt-image-2", None):
            with self.subTest(value=value):
                self.store[ai_provider.AI_IMAGE_INPUT_MODELS_CONFIG_KEY] = value
                self.assertEqual(ai_provider.get_ai_image_input_models(), [])

    def test_removing_then_readding_a_model_does_not_restore_its_tag(self):
        ai_provider.set_ai_image_input_models(["gpt-image-2", "another-image-model"])
        ai_provider.set_ai_image_model_rates({"another-image-model": 100.0})
        self.assertEqual(ai_provider.get_ai_image_input_models(), ["another-image-model"])
        ai_provider.set_ai_image_model_rates({"gpt-image-2": 250.0, "another-image-model": 100.0})
        self.assertEqual(ai_provider.get_ai_image_input_models(), ["another-image-model"])

    def test_changing_prices_preserves_tags_and_new_models_start_without_a_tag(self):
        ai_provider.set_ai_image_model_rates({
            "gpt-image-2": 300.0, "another-image-model": 100.0, "new-model": 50.0,
        })
        self.assertEqual(ai_provider.get_ai_image_input_models(), ["gpt-image-2"])

    def test_display_marks_image_input_and_uses_per_image_rates(self):
        display = ai_provider.format_ai_image_models_for_display(
            ai_provider.get_ai_image_model_rates(), ai_provider.get_ai_image_input_models(),
        )
        self.assertIn("gpt-image-2: 250.00/image [image-input]", display)
        self.assertIn("another-image-model: 100.00/image\n", display + "\n")


class AIImageGenerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())
        self.context = {"user": SimpleNamespace(id=1)}
        self.png = image_bytes()
        self.response = SimpleNamespace(data=[{
            "b64_json": base64.b64encode(self.png).decode("ascii"),
        }])
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.rates = self.stack.enter_context(patch("ai._get_ai_image_model_rates", return_value={
            "gpt-image-2": 250.0, "another-image-model": 100.0,
        }))
        self.stack.enter_context(patch("ai._get_ai_image_model", return_value="gpt-image-2"))
        self.input_models = self.stack.enter_context(patch(
            "ai._get_ai_image_input_models", return_value=["gpt-image-2"],
        ))
        self.fetch = self.stack.enter_context(patch.object(
            self.cog, "_fetch_discord_image_bytes", AsyncMock(return_value=(self.png, None)),
        ))
        self.request = self.stack.enter_context(patch.object(
            self.cog, "_create_image_generation_response", AsyncMock(return_value=self.response),
        ))
        self.stack.enter_context(patch.object(
            self.cog, "_resolve_ai_billing_target", AsyncMock(return_value={
                "payer_id": 1, "payer_user": None, "display_name": "requester",
            }),
        ))
        self.stack.enter_context(patch.object(self.cog, "_get_global_balance", return_value=1000.0))
        self.charge = self.stack.enter_context(patch.object(
            self.cog, "_charge_global_balance",
            side_effect=lambda _payer, amount: (amount, 1000.0 - amount),
        ))
        self.refund = self.stack.enter_context(patch.object(
            self.cog, "_refund_global_balance", return_value=1000.0,
        ))
        self.stack.enter_context(patch.object(self.cog, "_log_economy_transaction"))
        self.stack.enter_context(patch.object(self.cog, "_queue_economy_audit_log"))

    async def test_text_only_generation_sends_no_image(self):
        result = await self.cog._tool_generate_image({"prompt": "Draw a cat"}, self.context)
        self.assertFalse(result["used_reference_image"])
        self.assertNotIn("image", self.request.await_args.kwargs)
        self.fetch.assert_not_awaited()
        self.charge.assert_called_once_with(1, 250.0)
        self.refund.assert_not_called()

    async def test_current_attachment_is_uploaded_without_vision_analysis(self):
        self.context["request_image_attachment"] = SimpleNamespace(url=IMAGE_URL)
        with patch.object(self.cog, "_analyze_image_bytes_for_tool", AsyncMock()) as analyze:
            result = await self.cog._tool_generate_image({
                "prompt": "Turn this into a watercolor", "source": "current_attachment",
            }, self.context)
        self.assertTrue(result["used_reference_image"])
        self.assertEqual(result["delivered_count"], 1)
        self.fetch.assert_awaited_once_with(IMAGE_URL)
        filename, content, mime = self.request.await_args.kwargs["image"]
        self.assertEqual((filename, mime), ("reference.png", "image/png"))
        with Image.open(io.BytesIO(content)) as reference:
            self.assertEqual(reference.size, (8, 6))
        analyze.assert_not_awaited()
        self.assertEqual(self.context["pending_image_attachments"][0]["content"], self.png)

    async def test_discord_url_reference_is_uploaded(self):
        result = await self.cog._tool_generate_image({
            "prompt": "Change the background", "image_url": IMAGE_URL,
        }, self.context)
        self.assertTrue(result["used_reference_image"])
        self.fetch.assert_awaited_once_with(IMAGE_URL)

    async def test_newly_tagged_model_can_receive_images(self):
        self.input_models.return_value = ["gpt-image-2", "another-image-model"]
        result = await self.cog._tool_generate_image({
            "prompt": "Edit this", "model": "another-image-model", "image_url": IMAGE_URL,
        }, self.context)
        self.assertTrue(result["used_reference_image"])
        self.assertEqual(result["cost"], 100.0)
        self.assertEqual(self.request.await_args.kwargs["model"], "another-image-model")

    async def test_unmarked_model_rejects_reference_before_download_or_charge(self):
        result = await self.cog._tool_generate_image({
            "prompt": "Edit this", "model": "another-image-model", "image_url": IMAGE_URL,
        }, self.context)
        self.assertIn("does not support image input", result["error"])
        self.assertEqual(result["available_models"], ["gpt-image-2"])
        self.fetch.assert_not_awaited()
        self.charge.assert_not_called()
        self.request.assert_not_awaited()

    async def test_invalid_sources_are_rejected_before_charge(self):
        for arguments in (
            {"source": "current_attachment", "image_url": IMAGE_URL},
            {"source": "unknown"},
            {"source": "current_attachment"},
            {"image_url": "https://example.com/reference.png"},
            {"image_url": "http://cdn.discordapp.com/reference.png"},
        ):
            with self.subTest(arguments=arguments):
                result = await self.cog._tool_generate_image({"prompt": "Edit this", **arguments}, self.context)
                self.assertIn("error", result)
        self.fetch.assert_not_awaited()
        self.charge.assert_not_called()
        self.request.assert_not_awaited()

    async def test_invalid_or_empty_image_is_rejected_before_charge(self):
        for content in (b"not an image", b""):
            with self.subTest(content=content):
                self.fetch.return_value = (content, None)
                result = await self.cog._tool_generate_image({
                    "prompt": "Edit this", "image_url": IMAGE_URL,
                }, self.context)
                self.assertIn("error", result)
        self.charge.assert_not_called()
        self.request.assert_not_awaited()

    async def test_download_failure_is_rejected_before_charge(self):
        self.fetch.return_value = (None, "Discord CDN returned HTTP 404")
        result = await self.cog._tool_generate_image({
            "prompt": "Edit this", "image_url": IMAGE_URL,
        }, self.context)
        self.assertIn("404", result["error"])
        self.charge.assert_not_called()
        self.request.assert_not_awaited()

    async def test_download_exception_is_rejected_before_charge(self):
        self.fetch.side_effect = aiohttp.ClientError("download failed")
        result = await self.cog._tool_generate_image({
            "prompt": "Edit this", "image_url": IMAGE_URL,
        }, self.context)
        self.assertIn("download failed", result["error"])
        self.charge.assert_not_called()

    async def test_oversized_reference_is_rejected_before_charge(self):
        with patch.object(self.cog, "IMAGE_ANALYZE_MAX_BYTES", len(self.png) - 1):
            result = await self.cog._tool_generate_image({
                "prompt": "Edit this", "image_url": IMAGE_URL,
            }, self.context)
        self.assertIn("too large", result["error"])
        self.charge.assert_not_called()

    def test_reference_conversion_accepts_gif_and_webp(self):
        for image_format in ("GIF", "WEBP", "JPEG"):
            with self.subTest(image_format=image_format):
                normalized = self.cog._prepare_image_generation_reference(image_bytes(image_format))
                with Image.open(io.BytesIO(normalized)) as reference:
                    self.assertEqual(reference.format, "PNG")
                    self.assertEqual(reference.size, (8, 6))

    def test_reference_conversion_checks_pixels_and_output_size(self):
        with patch.object(AICommands, "EXTERNAL_IMAGE_MAX_PIXELS", 47):
            with self.assertRaisesRegex(ValueError, "too many pixels"):
                self.cog._prepare_image_generation_reference(self.png)
        with patch.object(AICommands, "IMAGE_ANALYZE_MAX_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "too large after conversion"):
                self.cog._prepare_image_generation_reference(self.png)

    async def test_edit_api_failure_refunds_and_preserves_previous_attachments(self):
        previous = {"filename": "previous.png", "content": self.png}
        self.context["pending_image_attachments"] = [previous]
        self.request.side_effect = RuntimeError("provider unavailable")
        result = await self.cog._tool_generate_image({
            "prompt": "Edit this", "image_url": IMAGE_URL,
        }, self.context)
        self.assertIn("provider unavailable", result["error"])
        self.charge.assert_called_once_with(1, 250.0)
        self.refund.assert_called_once_with(1, 250.0)
        self.assertEqual(self.context["pending_image_attachments"], [previous])

    async def test_partial_delivery_refunds_missing_images(self):
        result = await self.cog._tool_generate_image({
            "prompt": "Edit this", "image_url": IMAGE_URL, "n": 2,
        }, self.context)
        self.assertEqual(result["delivered_count"], 1)
        self.assertEqual(result["cost"], 250.0)
        self.charge.assert_called_once_with(1, 500.0)
        self.refund.assert_called_once_with(1, 250.0)

    async def test_invalid_second_output_removes_only_new_attachments_and_refunds(self):
        previous = {"filename": "previous.png", "content": self.png}
        self.context["pending_image_attachments"] = [previous]
        self.response.data.append({"b64_json": "invalid"})
        result = await self.cog._tool_generate_image({
            "prompt": "Edit this", "image_url": IMAGE_URL, "n": 2,
        }, self.context)
        self.assertIn("error", result)
        self.refund.assert_called_once_with(1, 500.0)
        self.assertEqual(self.context["pending_image_attachments"], [previous])

    def test_tool_schema_exposes_reference_inputs_and_model_capabilities(self):
        tool = next(tool["function"] for tool in self.cog._build_ai_tools()
                    if tool["function"]["name"] == "generate_image")
        properties = tool["parameters"]["properties"]
        self.assertEqual(properties["source"]["enum"], ["current_attachment"])
        self.assertIn("image_url", properties)
        self.assertEqual(properties["model"]["enum"], ["another-image-model", "gpt-image-2"])
        self.assertIn("Models supporting image input: gpt-image-2.", tool["description"])

    async def test_owner_can_toggle_image_input_tag(self):
        ctx = SimpleNamespace(send=AsyncMock())
        with patch("ai._set_ai_image_input_models") as setter:
            await AICommands.ai_config_image_input_tag_text.callback(
                self.cog, ctx, model="another-image-model", enabled="on",
            )
        setter.assert_called_once_with(["gpt-image-2", "another-image-model"])
        with patch("ai._set_ai_image_input_models") as setter:
            await AICommands.ai_config_image_input_tag_text.callback(
                self.cog, ctx, model="gpt-image-2", enabled="off",
            )
        setter.assert_called_once_with(["another-image-model"])

    async def test_owner_command_rejects_unknown_model_and_invalid_toggle(self):
        ctx = SimpleNamespace(send=AsyncMock())
        with patch("ai._set_ai_image_input_models") as setter:
            for model, enabled in (("unknown", "on"), ("gpt-image-2", "maybe")):
                await AICommands.ai_config_image_input_tag_text.callback(
                    self.cog, ctx, model=model, enabled=enabled,
                )
        setter.assert_not_called()


class AIImageEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_and_edit_requests_use_the_corresponding_endpoint(self):
        cog = AICommands(SimpleNamespace())
        response = SimpleNamespace(data=[])
        client = SimpleNamespace(images=SimpleNamespace(
            generate=Mock(return_value=response), edit=Mock(return_value=response),
        ))
        with patch("ai._create_ai_client", return_value=client):
            await cog._create_image_generation_response(model="gpt-image-2", prompt="Draw a cat")
            reference = ("reference.png", image_bytes(), "image/png")
            await cog._create_image_generation_response(
                model="gpt-image-2", prompt="Edit this cat", image=reference,
            )
        client.images.generate.assert_called_once_with(model="gpt-image-2", prompt="Draw a cat")
        client.images.edit.assert_called_once_with(model="gpt-image-2", prompt="Edit this cat", image=reference)


if __name__ == "__main__":
    unittest.main()
