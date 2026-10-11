import asyncio
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AICommands


class AIToolCancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())
        self.context = {"user": SimpleNamespace(id=11), "model": "openai"}
        self.balance = 1000.0
        self.cog._resolve_ai_billing_target = AsyncMock(return_value={
            "payer_id": 7,
            "payer_user": None,
            "display_name": "payer",
        })
        self.cog._get_global_balance = Mock(side_effect=lambda _payer: self.balance)
        self.cog._charge_global_balance = Mock(side_effect=self._charge)
        self.cog._refund_global_balance = Mock(side_effect=self._refund)
        self.cog._log_economy_transaction = Mock()
        self.cog._queue_economy_audit_log = Mock()
        self.cog._get_visual_description_cache = Mock(return_value={})
        self.cog._set_visual_description_cache = Mock()
        self.cog._fetch_discord_image_bytes = AsyncMock(return_value=(b"image", None))
        self.cog._build_visual_contact_sheet = Mock(return_value=b"sheet")
        self.asset = {
            "cache_key": "emoji:123",
            "asset_type": "custom_emoji",
            "url": "https://cdn.discordapp.com/emojis/123.png",
            "analyzable": True,
        }
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("ai._resolve_ai_vision_model", return_value="openai"))
        self.stack.enter_context(patch("ai._get_ai_image_model_rates", return_value={"image-model": 100.0}))
        self.stack.enter_context(patch("ai._get_ai_image_model", return_value="image-model"))

    def _charge(self, _payer, amount):
        self.balance -= amount
        return amount, self.balance

    def _refund(self, _payer, amount):
        self.balance += amount
        return self.balance

    async def _cancel_while_waiting(self, awaitable, started):
        task = asyncio.create_task(awaitable)
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    def _waiting_provider(started):
        async def wait(**_kwargs):
            started.set()
            await asyncio.Event().wait()

        return wait

    def _assert_full_refund(self, amount, action):
        self.cog._charge_global_balance.assert_called_once_with(7, amount)
        self.cog._refund_global_balance.assert_called_once_with(7, amount)
        self.assertEqual(self.balance, 1000.0)
        self.assertEqual(
            self.cog._queue_economy_audit_log.call_args.kwargs["action"],
            action,
        )

    async def test_visual_analysis_cancellation_during_download_refunds(self):
        started = asyncio.Event()

        async def download(_url):
            started.set()
            await asyncio.Event().wait()

        self.cog._fetch_discord_image_bytes.side_effect = download
        await self._cancel_while_waiting(
            self.cog._analyze_visual_assets([self.asset], self.context), started,
        )
        self._assert_full_refund(float(self.cog.IMAGE_ANALYZE_COST), "ai_visual_description_refund")
        self.cog._set_visual_description_cache.assert_not_called()
        self.assertFalse(self.cog._visual_description_cache_fill_lock.locked())

    async def test_visual_analysis_cancellation_during_provider_refunds(self):
        started = asyncio.Event()
        self.cog._generate_ai_completion = AsyncMock(side_effect=self._waiting_provider(started))
        await self._cancel_while_waiting(
            self.cog._analyze_visual_assets([self.asset], self.context), started,
        )
        self._assert_full_refund(float(self.cog.IMAGE_ANALYZE_COST), "ai_visual_description_refund")
        self.cog._set_visual_description_cache.assert_not_called()

    async def test_image_analysis_cancellation_refunds(self):
        started = asyncio.Event()
        self.cog._generate_ai_completion = AsyncMock(side_effect=self._waiting_provider(started))
        await self._cancel_while_waiting(
            self.cog._analyze_image_bytes_for_tool(
                b"image",
                prompt="Describe the image",
                max_chars=200,
                tool_context=self.context,
                system_prompt="Describe images",
                source_detail="attachment",
            ),
            started,
        )
        self._assert_full_refund(float(self.cog.IMAGE_ANALYZE_COST), "ai_image_analyze_refund")

    async def test_image_generation_cancellation_refunds_and_discards_only_new_attachments(self):
        existing_attachment = {"filename": "earlier.png"}
        self.context["pending_image_attachments"] = [existing_attachment]
        started = asyncio.Event()

        async def generate(**_kwargs):
            self.context["pending_image_attachments"].append({"filename": "cancelled.png"})
            started.set()
            await asyncio.Event().wait()

        self.cog._create_image_generation_response = AsyncMock(side_effect=generate)
        await self._cancel_while_waiting(
            self.cog._tool_generate_image({"prompt": "Draw a cat", "n": 2}, self.context),
            started,
        )
        self._assert_full_refund(200.0, "ai_image_refund")
        self.assertEqual(self.context["pending_image_attachments"], [existing_attachment])


if __name__ == "__main__":
    unittest.main()
