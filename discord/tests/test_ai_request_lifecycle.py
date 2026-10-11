import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AICommands


class AIRequestLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace(get_guild=lambda _guild_id: None))
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def _start(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    async def _wait(self, awaitable):
        return await asyncio.wait_for(awaitable, timeout=2)

    def _message(self, message_id=101, *, user_id=42, channel_id=200):
        return SimpleNamespace(
            id=message_id,
            author=SimpleNamespace(id=user_id),
            channel=SimpleNamespace(id=channel_id, send=AsyncMock()),
        )

    def _context(self, message_id=101, *, user_id=42, channel_id=200, guild_id=300):
        message = self._message(message_id, user_id=user_id, channel_id=channel_id)
        return SimpleNamespace(
            author=message.author,
            channel=message.channel,
            guild=SimpleNamespace(id=guild_id),
            message=message,
            reply=AsyncMock(),
        )

    def _interaction(self, *, user_id=42, guild_id=301):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            guild=SimpleNamespace(id=guild_id),
            channel=SimpleNamespace(id=201),
            response=SimpleNamespace(
                send_message=AsyncMock(), edit_message=AsyncMock(), is_done=lambda: False
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_same_user_is_rejected_across_channels_until_completion(self):
        started = asyncio.Event()
        finish = asyncio.Event()

        async def request(_state):
            started.set()
            await finish.wait()

        origin = self._message()
        first = self._start(self.cog._run_ai_request(42, request, source_message=origin))
        await self._wait(started.wait())
        second = AsyncMock()

        self.assertFalse(
            await self.cog._run_ai_request(
                42, second, source_message=self._message(102, channel_id=201)
            )
        )
        second.assert_not_awaited()
        self.assertIn(42, self.cog._active_ai_requests)
        self.assertNotIn(102, self.cog._ai_request_message_ids)

        finish.set()
        self.assertTrue(await self._wait(first))
        self.assertEqual(self.cog._active_ai_requests, {})
        self.assertEqual(self.cog._ai_request_message_ids, {})
        self.assertTrue(await self.cog._run_ai_request(42, second))
        second.assert_awaited_once()

    async def test_different_users_can_run_concurrently(self):
        started = [asyncio.Event(), asyncio.Event()]
        finish = asyncio.Event()

        async def request(_state, index):
            started[index].set()
            await finish.wait()

        first = self._start(self.cog._run_ai_request(42, lambda state: request(state, 0)))
        second = self._start(self.cog._run_ai_request(43, lambda state: request(state, 1)))
        await self._wait(asyncio.gather(*(event.wait() for event in started)))
        self.assertEqual(set(self.cog._active_ai_requests), {42, 43})

        finish.set()
        self.assertEqual(await self._wait(asyncio.gather(first, second)), [True, True])
        self.assertEqual(self.cog._active_ai_requests, {})

    async def test_early_return_and_exception_release_user_and_origin(self):
        origin = self._message()
        self.assertTrue(
            await self.cog._run_ai_request(42, AsyncMock(return_value=None), source_message=origin)
        )
        self.assertEqual(self.cog._active_ai_requests, {})
        self.assertEqual(self.cog._ai_request_message_ids, {})

        with self.assertRaisesRegex(RuntimeError, "generation failed"):
            await self.cog._run_ai_request(
                42, AsyncMock(side_effect=RuntimeError("generation failed")), source_message=origin
            )
        self.assertEqual(self.cog._active_ai_requests, {})
        self.assertEqual(self.cog._ai_request_message_ids, {})
        self.assertTrue(await self.cog._run_ai_request(42, AsyncMock()))
        origin.channel.send.assert_not_awaited()

    async def test_external_cancellation_propagates_and_releases_guard(self):
        started = asyncio.Event()
        origin = self._message()

        async def request(_state):
            started.set()
            await asyncio.Event().wait()

        pending = self._start(self.cog._run_ai_request(42, request, source_message=origin))
        await self._wait(started.wait())
        pending.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await self._wait(pending)
        self.assertEqual(self.cog._active_ai_requests, {})
        self.assertEqual(self.cog._ai_request_message_ids, {})
        origin.channel.send.assert_not_awaited()

    async def test_uncached_origin_delete_cancels_and_holds_guard_through_cleanup(self):
        started = asyncio.Event()
        cleanup_started = asyncio.Event()
        finish_cleanup = asyncio.Event()
        notice_started = asyncio.Event()
        finish_notice = asyncio.Event()
        origin = self._message()
        final_response = AsyncMock()
        tool_notice = SimpleNamespace(delete=AsyncMock())
        cancelled_count = 0

        async def send_notice(*_args, **_kwargs):
            notice_started.set()
            await finish_notice.wait()

        origin.channel.send.side_effect = send_notice

        async def request(state):
            nonlocal cancelled_count
            state.tool_notice_message = tool_notice
            state.billing = {
                "payer_id": 99,
                "model": "test-model",
                "billing_actor": SimpleNamespace(id=99),
                "billing_detail_suffix": " requester=42",
                "audit_context": {"ctx": self._context()},
            }
            state.refundable_amount = 3.5
            started.set()
            try:
                await asyncio.Event().wait()
                await final_response()
            except asyncio.CancelledError:
                cancelled_count += 1
                raise
            finally:
                cleanup_started.set()
                await finish_cleanup.wait()

        with (
            patch.object(self.cog, "_get_global_balance", return_value=10),
            patch.object(self.cog, "_refund_global_balance", return_value=13.5) as refund,
            patch.object(self.cog, "_log_economy_transaction"),
            patch.object(self.cog, "_queue_economy_audit_log"),
        ):
            pending = self._start(self.cog._run_ai_request(42, request, source_message=origin))
            await self._wait(started.wait())
            payload = SimpleNamespace(message_id=origin.id, channel_id=origin.channel.id)
            await self.cog.on_raw_message_delete(payload)
            await self._wait(cleanup_started.wait())

            await self.cog.on_raw_message_delete(payload)
            await self.cog.on_raw_bulk_message_delete(
                SimpleNamespace(message_ids={origin.id, 9999}, channel_id=origin.channel.id)
            )
            rejected = AsyncMock()
            self.assertFalse(await self.cog._run_ai_request(42, rejected))
            self.assertFalse(pending.done())
            finish_cleanup.set()
            await self._wait(notice_started.wait())
            self.assertFalse(await self.cog._run_ai_request(42, rejected))
            self.assertIn(origin.id, self.cog._ai_request_message_ids)
            finish_notice.set()
            self.assertTrue(await self._wait(pending))

            refund.assert_called_once_with(99, 3.5)
            rejected.assert_not_awaited()
        self.assertEqual(cancelled_count, 1)
        final_response.assert_not_awaited()
        tool_notice.delete.assert_awaited_once()
        origin.channel.send.assert_awaited_once()
        notice = origin.channel.send.await_args
        self.assertEqual(notice.args[0], "❌ <@42>，你取消了請求。")
        self.assertEqual([user.id for user in notice.kwargs["allowed_mentions"].users], [42])
        self.assertFalse(notice.kwargs["allowed_mentions"].everyone)
        self.assertFalse(notice.kwargs["allowed_mentions"].roles)
        self.assertEqual(self.cog._active_ai_requests, {})
        self.assertEqual(self.cog._ai_request_message_ids, {})

    async def test_bulk_delete_cancels_each_matching_request_once(self):
        origins = [self._message(101), self._message(102, user_id=43)]
        started = [asyncio.Event(), asyncio.Event()]

        async def request(_state, index):
            started[index].set()
            await asyncio.Event().wait()

        pending = [
            self._start(
                self.cog._run_ai_request(
                    origin.author.id,
                    lambda state, index=index: request(state, index),
                    source_message=origin,
                )
            )
            for index, origin in enumerate(origins)
        ]
        await self._wait(asyncio.gather(*(event.wait() for event in started)))
        payload = SimpleNamespace(message_ids={101, 102, 9999}, channel_id=200)
        await self.cog.on_raw_bulk_message_delete(payload)
        self.assertEqual(await self._wait(asyncio.gather(*pending)), [True, True])
        await self.cog.on_raw_bulk_message_delete(payload)

        for origin in origins:
            origin.channel.send.assert_awaited_once()
        self.assertEqual(self.cog._active_ai_requests, {})
        self.assertEqual(self.cog._ai_request_message_ids, {})

    async def test_unrelated_wrong_channel_and_completed_deletes_are_ignored(self):
        started = asyncio.Event()
        finish = asyncio.Event()
        origin = self._message()

        async def request(_state):
            started.set()
            await finish.wait()

        pending = self._start(self.cog._run_ai_request(42, request, source_message=origin))
        await self._wait(started.wait())
        await self.cog.on_raw_message_delete(SimpleNamespace(message_id=999, channel_id=200))
        await self.cog.on_raw_message_delete(SimpleNamespace(message_id=101, channel_id=999))
        await self.cog.on_raw_bulk_message_delete(SimpleNamespace(message_ids={101}, channel_id=999))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        self.assertFalse(self.cog._active_ai_requests[42].cancelled)

        finish.set()
        self.assertTrue(await self._wait(pending))
        await self.cog.on_raw_message_delete(SimpleNamespace(message_id=101, channel_id=200))
        origin.channel.send.assert_not_awaited()

    async def test_prefix_and_slash_share_user_guard_in_both_directions(self):
        for first_kind in ("prefix", "slash"):
            with self.subTest(first_kind=first_kind):
                started = asyncio.Event()
                finish = asyncio.Event()
                context = self._context()
                interaction = self._interaction()

                async def request(*_args, **_kwargs):
                    started.set()
                    await finish.wait()

                text_request = AsyncMock(side_effect=request)
                slash_request = AsyncMock(side_effect=request)
                with (
                    patch.object(self.cog, "_ai_text_request", text_request),
                    patch.object(self.cog, "_ai_chat_request", slash_request),
                ):
                    if first_kind == "prefix":
                        pending = self._start(
                            AICommands.ai_text_command.callback(self.cog, context, message="first")
                        )
                        await self._wait(started.wait())
                        await AICommands.ai_chat.callback(self.cog, interaction, message="second")
                        slash_request.assert_not_awaited()
                        interaction.response.send_message.assert_awaited_once()
                        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
                    else:
                        pending = self._start(
                            AICommands.ai_chat.callback(self.cog, interaction, message="first")
                        )
                        await self._wait(started.wait())
                        await AICommands.ai_text_command.callback(self.cog, context, message="second")
                        text_request.assert_not_awaited()
                        context.reply.assert_awaited_once()
                    finish.set()
                    await self._wait(pending)
                self.assertEqual(self.cog._active_ai_requests, {})

    async def test_new_conversation_and_clear_cannot_erase_active_history(self):
        started = asyncio.Event()
        finish = asyncio.Event()

        async def request(_state):
            started.set()
            await finish.wait()

        pending = self._start(self.cog._run_ai_request(42, request))
        await self._wait(started.wait())
        with (
            patch("ai.ConversationManager.clear_history") as clear_history,
            patch.object(self.cog, "_clear_ai_idle_schedule") as clear_idle,
            patch.object(self.cog, "_ai_text_request", new=AsyncMock()) as text_request,
            patch("ai.ClearHistoryView") as clear_view,
        ):
            for message in (None, "new question"):
                context = self._context(guild_id=301)
                await AICommands.ai_new_conversation.callback(self.cog, context, message=message)
                context.reply.assert_awaited_once()
            context = self._context(guild_id=301)
            await AICommands.ai_clear_text.callback(self.cog, context)
            context.reply.assert_awaited_once()
            interaction = self._interaction()
            await AICommands.ai_clear.callback(self.cog, interaction)
            interaction.response.send_message.assert_awaited_once()
            clear_history.assert_not_called()
            clear_idle.assert_not_called()
            clear_view.assert_not_called()
            text_request.assert_not_awaited()
        finish.set()
        await self._wait(pending)

    async def test_new_conversation_acquires_guard_before_clearing_history(self):
        context = self._context()
        started = asyncio.Event()
        finish = asyncio.Event()

        def clear_history(*_args):
            self.assertIn(42, self.cog._active_ai_requests)
            self.assertIn(context.message.id, self.cog._ai_request_message_ids)

        async def request(*_args, **_kwargs):
            started.set()
            await finish.wait()

        with (
            patch("ai.ConversationManager.clear_history", side_effect=clear_history) as clear,
            patch.object(self.cog, "_clear_ai_idle_schedule"),
            patch.object(self.cog, "_ai_text_request", new=AsyncMock(side_effect=request)),
        ):
            pending = self._start(
                AICommands.ai_new_conversation.callback(self.cog, context, message="new question")
            )
            await self._wait(started.wait())
            clear.assert_called_once_with(42, context.guild.id)
            self.assertFalse(await self.cog._run_ai_request(42, AsyncMock()))
            finish.set()
            await self._wait(pending)
        self.assertEqual(self.cog._active_ai_requests, {})

    async def test_old_clear_confirmation_cannot_erase_newly_active_request(self):
        interaction = self._interaction()
        await AICommands.ai_clear.callback(self.cog, interaction)
        view = interaction.response.send_message.await_args.kwargs["view"]
        started = asyncio.Event()
        finish = asyncio.Event()

        async def request(_state):
            started.set()
            await finish.wait()

        pending = self._start(self.cog._run_ai_request(42, request))
        await self._wait(started.wait())
        confirmation = self._interaction()
        with (
            patch("ai.ConversationManager.clear_history") as clear,
            patch.object(self.cog, "_clear_ai_idle_schedule") as clear_idle,
        ):
            await view.confirm_callback(confirmation)
            clear.assert_not_called()
            clear_idle.assert_not_called()
            confirmation.response.send_message.assert_awaited_once()
            confirmation.response.edit_message.assert_not_awaited()
            self.assertFalse(view.confirmed)
        finish.set()
        await self._wait(pending)
        view.stop()

    async def test_background_idle_clear_and_summary_wait_for_active_request(self):
        for operation in ("clear", "summary"):
            with self.subTest(operation=operation):
                started = asyncio.Event()
                finish_request = asyncio.Event()
                wait_started = asyncio.Event()
                finish_wait = asyncio.Event()

                async def request(_state):
                    started.set()
                    await finish_request.wait()

                async def wait_for_request(_delay):
                    wait_started.set()
                    await finish_wait.wait()

                pending = self._start(self.cog._run_ai_request(42, request))
                await self._wait(started.wait())
                with (
                    patch("ai.asyncio.sleep", new=AsyncMock(side_effect=wait_for_request)) as sleep,
                    patch.object(
                        self.cog,
                        f"_get_auto_{operation}_config",
                        return_value={"enabled": True, "due_at": None},
                    ) as config,
                    patch.object(self.cog, f"_set_auto_{operation}_config") as set_config,
                    patch("ai.ConversationManager.get_history", return_value=[]) as history,
                ):
                    idle = self._start(getattr(self.cog, f"_run_auto_{operation}")(42, 300))
                    await self._wait(wait_started.wait())
                    config.assert_not_called()
                    history.assert_not_called()
                    self.assertFalse(idle.done())
                    finish_request.set()
                    await self._wait(pending)
                    finish_wait.set()
                    self.assertIsNone(await self._wait(idle))
                    sleep.assert_awaited_once_with(1)
                    config.assert_called_once_with(42, 300)
                    history.assert_called_once_with(42, 300)
                    set_config.assert_called_once_with(42, 300, {"enabled": True, "due_at": None})

    async def test_foreground_request_can_run_its_own_idle_clear_and_summary(self):
        for operation in ("clear", "summary"):
            with self.subTest(operation=operation):

                def get_config(_user_id, _guild_id):
                    state = self.cog._active_ai_requests[42]
                    self.assertIs(state.task, asyncio.current_task())
                    return {"enabled": True, "due_at": None}

                async def request(_state):
                    self.assertIsNone(await getattr(self.cog, f"_run_auto_{operation}")(42, 300))

                with (
                    patch(
                        "ai.asyncio.sleep",
                        new=AsyncMock(side_effect=AssertionError("foreground request waited for itself")),
                    ) as sleep,
                    patch.object(self.cog, f"_get_auto_{operation}_config", side_effect=get_config) as config,
                    patch.object(self.cog, f"_set_auto_{operation}_config") as set_config,
                    patch("ai.ConversationManager.get_history", return_value=[]) as history,
                ):
                    self.assertTrue(await self._wait(self.cog._run_ai_request(42, request)))
                    sleep.assert_not_awaited()
                    config.assert_called_once_with(42, 300)
                    history.assert_called_once_with(42, 300)
                    set_config.assert_called_once_with(42, 300, {"enabled": True, "due_at": None})
                self.assertEqual(self.cog._active_ai_requests, {})


if __name__ == "__main__":
    unittest.main()
