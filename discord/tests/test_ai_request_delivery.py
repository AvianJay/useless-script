import asyncio
import sys
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AICommands


class AIRequestDeliveryTests(unittest.IsolatedAsyncioTestCase):
    """Exercise billing and history in real handlers through their request guard."""

    def setUp(self):
        self.user = SimpleNamespace(id=42, display_name="Requester")
        payer = SimpleNamespace(id=99, display_name="Payer")
        self.cog = AICommands(SimpleNamespace(user=SimpleNamespace(id=999)))
        self.channel = SimpleNamespace(id=200, send=AsyncMock(), fetch_message=AsyncMock())
        self.source = SimpleNamespace(
            id=101,
            author=self.user,
            channel=self.channel,
            attachments=[],
            reference=None,
            created_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        )
        self.ctx = SimpleNamespace(
            author=self.user,
            guild=None,
            channel=self.channel,
            message=self.source,
            typing=MagicMock(return_value=AsyncMock()),
            reply=AsyncMock(return_value=SimpleNamespace(id=1001)),
            send=AsyncMock(),
        )
        self.balance = 100.0
        self.tasks = []
        patches = ExitStack()
        self.addCleanup(patches.close)

        def cog_patch(name, **kwargs):
            return patches.enter_context(patch.object(self.cog, name, **kwargs))

        def module_patch(name, **kwargs):
            return patches.enter_context(patch(f"ai.{name}", **kwargs))

        # Keep configuration, prompts, Discord delivery, and the database local
        # while retaining the handlers' actual ordering and cancellation paths.
        cog_patch("_begin_ai_request")
        cog_patch("_build_guild_emoji_context", new=AsyncMock(return_value=("", {})))
        cog_patch("_get_default_model", new=AsyncMock(return_value="openai-fast"))
        cog_patch("check_rate_limit", return_value=True)
        module_patch("MentionResolver.resolve_mentions", new=AsyncMock(return_value="ask"))
        module_patch("PromptGuard.is_safe", return_value=(True, []))
        module_patch("PromptGuard.sanitize_input", return_value=("ask", []))
        module_patch("_get_ai_text_model_rate", return_value=1.0)
        cog_patch(
            "_resolve_ai_billing_target",
            new=AsyncMock(
                return_value={
                    "payer_id": payer.id,
                    "payer_user": payer,
                    "display_name": payer.display_name,
                    "uses_guild_billing": True,
                }
            ),
        )
        cog_patch("_get_global_balance", side_effect=lambda _payer_id: self.balance)
        self.charge = cog_patch("_charge_global_balance", side_effect=self._charge)
        self.refund = cog_patch("_refund_global_balance", side_effect=self._refund)
        cog_patch("_log_economy_transaction")
        self.audit = cog_patch("_queue_economy_audit_log")
        cog_patch("_check_overdue_ai_idle", new=AsyncMock())
        cog_patch("_build_message_visual_metadata", return_value={"items": []})
        cog_patch("_build_text_visual_metadata", return_value={"items": []})
        cog_patch("_format_visual_metadata_for_context", return_value="")
        cog_patch("_build_system_with_context", return_value="system prompt")
        cog_patch("_make_browser_approval_presenter", return_value=None)
        self.generate = cog_patch(
            "generate_response",
            new=AsyncMock(return_value=("answer", "test model", 0.1, None)),
        )
        cog_patch("_resolve_ai_custom_emojis", side_effect=lambda text, _emoji_map: text)
        cog_patch(
            "_prepare_ai_response_delivery",
            new=AsyncMock(return_value=("answer", "answer", None, [])),
        )
        cog_patch("_pop_uploaded_html_chars", return_value=0)
        cog_patch("_build_ai_billing_info_suffix", return_value="")
        cog_patch("_get_response_view_config", return_value={})
        cog_patch("_build_pending_image_files", return_value=[])
        module_patch("AIResponseBuilder.create_response_view", return_value=object())
        self.get_history = module_patch("ConversationManager.get_history", return_value=[])
        self.add_message = module_patch("ConversationManager.add_message")
        self.remember_response = cog_patch("_remember_ai_response_message")
        self.record_idle = cog_patch("_record_ai_idle_activity")
        cog_patch("_deliver_pending_image_attachments", new=AsyncMock())

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def _charge(self, payer_id, amount):
        self.assertEqual(payer_id, 99)
        charged = min(self.balance, amount)
        self.balance = round(self.balance - charged, 2)
        return charged, self.balance

    def _refund(self, payer_id, amount):
        self.assertEqual(payer_id, 99)
        self.balance = round(self.balance + amount, 2)
        return self.balance

    def _start(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    def _start_text(self):
        return self._start(AICommands.ai_text_command.callback(self.cog, self.ctx, message="ask"))

    async def _wait(self, awaitable):
        return await asyncio.wait_for(awaitable, timeout=2)

    async def _delete_origin(self):
        await self.cog.on_raw_message_delete(
            SimpleNamespace(message_id=self.source.id, channel_id=self.channel.id)
        )

    def _assert_cancelled(self, amount):
        self.refund.assert_called_once_with(99, amount)
        self.assertEqual(self.balance, 100.0)
        self.add_message.assert_not_called()
        self.remember_response.assert_not_called()
        self.record_idle.assert_not_called()
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.channel.send.await_args.args[0], "❌ <@42>，你取消了請求。")
        self.assertEqual(self.cog._active_ai_requests, {})
        self.assertEqual(self.cog._ai_request_message_ids, {})
        refund_audit = [call for call in self.audit.call_args_list if call.kwargs["action"] == "ai_refund"]
        self.assertEqual(len(refund_audit), 1)
        self.assertEqual(refund_audit[0].kwargs["amount"], amount)
        self.assertIs(refund_audit[0].kwargs["ctx"], self.ctx)

    async def test_deletion_during_generation_refunds_input_without_history(self):
        generation_started = asyncio.Event()

        async def generate(*_args, **_kwargs):
            generation_started.set()
            await asyncio.Event().wait()

        self.generate.side_effect = generate
        pending = self._start_text()
        await self._wait(generation_started.wait())
        self.assertEqual(self.balance, 97.0)
        await self._delete_origin()
        await self._wait(pending)

        self.charge.assert_called_once_with(99, 3.0)
        self.ctx.reply.assert_not_awaited()
        self._assert_cancelled(3.0)

    async def test_deletion_during_response_delivery_refunds_input_and_output(self):
        delivery_started = asyncio.Event()

        async def reply(*_args, **_kwargs):
            delivery_started.set()
            await asyncio.Event().wait()

        self.ctx.reply.side_effect = reply
        pending = self._start_text()
        await self._wait(delivery_started.wait())
        self.assertEqual([call.args for call in self.charge.call_args_list], [(99, 3.0), (99, 6.0)])
        self.assertEqual(self.balance, 91.0)
        self.add_message.assert_not_called()
        await self._delete_origin()
        await self._wait(pending)

        self.ctx.reply.assert_awaited_once()
        self._assert_cancelled(9.0)

    async def test_deletion_during_reply_fetch_refunds_charge_before_generation_try(self):
        fetch_started = asyncio.Event()
        self.source.reference = SimpleNamespace(message_id=900)

        async def fetch_message(_message_id):
            fetch_started.set()
            await asyncio.Event().wait()

        self.channel.fetch_message.side_effect = fetch_message
        pending = self._start_text()
        await self._wait(fetch_started.wait())
        self.assertEqual(self.balance, 97.0)
        await self._delete_origin()
        await self._wait(pending)

        self.channel.fetch_message.assert_awaited_once_with(900)
        self.generate.assert_not_awaited()
        self.get_history.assert_not_called()
        self.ctx.reply.assert_not_awaited()
        self._assert_cancelled(3.0)

    async def test_deletion_during_required_file_delivery_refunds_without_history(self):
        file_send_started = asyncio.Event()
        pending_file = {"filename": "answer.txt", "content": "filedata", "char_count": 8}
        attachment = object()

        async def send_file(*_args, **_kwargs):
            file_send_started.set()
            await asyncio.Event().wait()

        self.ctx.send.side_effect = send_file
        with (
            patch.object(
                self.cog,
                "_prepare_ai_response_delivery",
                new=AsyncMock(return_value=("answer", "answer", pending_file, [])),
            ),
            patch.object(self.cog, "_build_pending_file_attachment", return_value=attachment),
            patch.object(self.cog, "_can_inline_file_response", return_value=False),
        ):
            pending = self._start_text()
            await self._wait(file_send_started.wait())
            self.ctx.reply.assert_awaited_once()
            self.assertEqual(self.balance, 83.0)
            self.add_message.assert_not_called()
            self.assertFalse(await self.cog._run_ai_request(42, AsyncMock()))
            await self._delete_origin()
            await self._wait(pending)

        self.assertIs(self.ctx.send.await_args.kwargs["file"], attachment)
        self._assert_cancelled(17.0)

    async def test_file_response_is_attached_to_the_reply(self):
        pending_file = {"filename": "answer.txt", "content": "filedata", "char_count": 8}
        attachment = object()

        with (
            patch.object(
                self.cog,
                "_prepare_ai_response_delivery",
                new=AsyncMock(return_value=("answer", "answer", pending_file, [])),
            ),
            patch.object(self.cog, "_build_pending_file_attachment", return_value=attachment),
        ):
            await self._wait(self._start_text())

        self.assertEqual(self.ctx.reply.await_args.kwargs["files"], [attachment])
        self.ctx.send.assert_not_awaited()
        self.assertEqual(self.add_message.call_count, 2)

    async def test_success_records_history_only_after_response_delivery(self):
        delivery_started = asyncio.Event()
        finish_delivery = asyncio.Event()
        delivered = False

        async def reply(*_args, **_kwargs):
            nonlocal delivered
            delivery_started.set()
            await finish_delivery.wait()
            delivered = True
            return SimpleNamespace(id=1001)

        def add_message(*_args, **_kwargs):
            self.assertTrue(delivered)
            self.assertIn(42, self.cog._active_ai_requests)

        self.ctx.reply.side_effect = reply
        self.add_message.side_effect = add_message
        pending = self._start_text()
        await self._wait(delivery_started.wait())
        self.add_message.assert_not_called()
        self.assertFalse(await self.cog._run_ai_request(42, AsyncMock()))
        finish_delivery.set()
        await self._wait(pending)

        self.assertEqual(self.add_message.call_count, 2)
        self.assertEqual(self.add_message.call_args_list[0].args, (42, "user", "ask", None))
        self.assertEqual(self.add_message.call_args_list[1].args, (42, "assistant", "answer", None))
        self.refund.assert_not_called()
        self.assertEqual(self.balance, 91.0)
        self.channel.send.assert_not_awaited()
        self.remember_response.assert_called_once_with(None, 1001)
        self.record_idle.assert_called_once_with(42, None)
        self.assertEqual(self.cog._active_ai_requests, {})
        self.assertEqual(self.cog._ai_request_message_ids, {})

    async def test_text_request_preserves_all_image_attachments_without_reading_for_text_model(self):
        first = SimpleNamespace(content_type="image/png", read=AsyncMock(return_value=b"first image"))
        document = SimpleNamespace(content_type="text/plain", read=AsyncMock())
        second = SimpleNamespace(content_type="image/webp", read=AsyncMock(return_value=b"second image"))
        self.source.attachments = [first, document, second]
        with patch("ai._is_ai_vision_model", return_value=False):
            await self._wait(self._start_text())
        context = self.generate.await_args.kwargs["tool_context"]
        self.assertEqual(context["request_image_attachments"], [first, second])
        self.assertIs(context["request_image_attachment"], first)
        self.assertIsNone(self.generate.await_args.kwargs["image"])
        first.read.assert_not_awaited()
        second.read.assert_not_awaited()
        document.read.assert_not_awaited()

    async def test_text_request_passes_every_image_to_vision_model_in_upload_order(self):
        first = SimpleNamespace(content_type="image/png", read=AsyncMock(return_value=b"first image"))
        second = SimpleNamespace(content_type="image/webp", read=AsyncMock(return_value=b"second image"))
        self.source.attachments = [first, second]
        with patch("ai._is_ai_vision_model", return_value=True):
            await self._wait(self._start_text())
        self.assertEqual(self.generate.await_args.kwargs["image"], [b"first image", b"second image"])
        first.read.assert_awaited_once()
        second.read.assert_awaited_once()

    async def test_slash_request_accepts_image2_with_or_without_the_first_slot(self):
        for include_first in (True, False):
            with self.subTest(include_first=include_first):
                first = SimpleNamespace(content_type="image/png", read=AsyncMock(return_value=b"first image"))
                second = SimpleNamespace(content_type="image/png", read=AsyncMock(return_value=b"second image"))
                interaction = SimpleNamespace(
                    user=self.user,
                    guild=None,
                    channel=self.channel,
                    created_at=self.source.created_at,
                    response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                    edit_original_response=AsyncMock(return_value=SimpleNamespace(id=1001)),
                )
                with patch("ai._is_ai_vision_model", return_value=True):
                    await AICommands.ai_chat.callback(
                        self.cog, interaction, message="ask", image=first if include_first else None, image2=second,
                    )
                context = self.generate.await_args.kwargs["tool_context"]
                self.assertEqual(context["request_image_attachments"], [first, second] if include_first else [second])
                self.assertIs(context["request_image_attachment"], first if include_first else second)
                self.assertEqual(
                    self.generate.await_args.kwargs["image"],
                    [b"first image", b"second image"] if include_first else b"second image",
                )
                interaction.edit_original_response.assert_awaited_once()
                self.refund.assert_not_called()

    async def test_slash_request_rejects_non_image_in_a_later_slot_before_charge(self):
        first = SimpleNamespace(content_type="image/png")
        document = SimpleNamespace(content_type="text/plain")
        interaction = SimpleNamespace(
            user=self.user,
            guild=None,
            channel=self.channel,
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await AICommands.ai_chat.callback(self.cog, interaction, message="ask", image=first, image2=document)
        interaction.response.send_message.assert_awaited_once()
        self.generate.assert_not_awaited()
        self.charge.assert_not_called()

    async def test_slash_cancellation_during_defer_refunds_pre_try_charge(self):
        defer_started = asyncio.Event()

        async def defer(**_kwargs):
            defer_started.set()
            await asyncio.Event().wait()

        interaction = SimpleNamespace(
            user=self.user,
            guild=None,
            channel=self.channel,
            created_at=self.source.created_at,
            response=SimpleNamespace(defer=AsyncMock(side_effect=defer), send_message=AsyncMock()),
        )
        pending = self._start(AICommands.ai_chat.callback(self.cog, interaction, message="ask"))
        await self._wait(defer_started.wait())
        self.assertEqual(self.balance, 97.0)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await self._wait(pending)

        self.refund.assert_called_once_with(99, 3.0)
        self.assertEqual(self.balance, 100.0)
        self.generate.assert_not_awaited()
        self.get_history.assert_not_called()
        self.add_message.assert_not_called()
        interaction.response.send_message.assert_not_awaited()
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.cog._active_ai_requests, {})
        self.assertEqual(self.cog._ai_request_message_ids, {})
        refund_audit = [call for call in self.audit.call_args_list if call.kwargs["action"] == "ai_refund"]
        self.assertEqual(len(refund_audit), 1)
        self.assertIs(refund_audit[0].kwargs["interaction"], interaction)


if __name__ == "__main__":
    unittest.main()
