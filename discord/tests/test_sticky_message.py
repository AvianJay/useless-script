import asyncio
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import StickyMessage as sticky


class StickyMessageConfigTests(unittest.TestCase):
    def test_normalize_config_round_trip(self):
        value = {
            "quiet_seconds": 15,
            "min_interval_seconds": 60,
            "entries": [{
                "channel_id": "123",
                "content": " Hello ",
                "allow_mentions": True,
                "message_id": 999,
            }],
        }
        self.assertEqual(
            sticky.normalize_config(value, strict=True),
            {
                "quiet_seconds": 15,
                "min_interval_seconds": 60,
                "entries": [{
                    "channel_id": 123,
                    "content": "Hello",
                    "allow_mentions": True,
                }],
            },
        )

    def test_strict_config_rejects_duplicate_channels(self):
        with self.assertRaisesRegex(ValueError, "同一個頻道"):
            sticky.normalize_config({
                "entries": [
                    {"channel_id": 1, "content": "one"},
                    {"channel_id": "1", "content": "two"},
                ],
            }, strict=True)

    def test_strict_config_rejects_empty_and_long_content(self):
        with self.assertRaisesRegex(ValueError, "不可為空"):
            sticky.normalize_config({"entries": [{"channel_id": 1, "content": "  "}]}, strict=True)
        with self.assertRaisesRegex(ValueError, "2000"):
            sticky.normalize_config({"entries": [{"channel_id": 1, "content": "x" * 2001}]}, strict=True)

    def test_strict_config_validates_timing_ranges(self):
        with self.assertRaisesRegex(ValueError, "quiet_seconds"):
            sticky.normalize_config({"quiet_seconds": 4}, strict=True)
        with self.assertRaisesRegex(ValueError, "min_interval_seconds"):
            sticky.normalize_config({"min_interval_seconds": 29}, strict=True)

    def test_limit_defaults_and_clamps(self):
        with patch.object(sticky, "get_server_config", return_value="invalid"):
            self.assertEqual(sticky.get_stickymessage_limit(1), 5)
        with patch.object(sticky, "get_server_config", return_value=0):
            self.assertEqual(sticky.get_stickymessage_limit(1), 1)
        with patch.object(sticky, "get_server_config", return_value=100):
            self.assertEqual(sticky.get_stickymessage_limit(1), 25)

    def test_state_keeps_only_internal_fields(self):
        state = sticky.normalize_state({
            "123": {
                "message_id": "456",
                "last_sent_at": "12.5",
                "content_digest": "abc",
                "content": "must not leak into state",
            },
        })
        self.assertEqual(state, {
            "123": {"message_id": 456, "last_sent_at": 12.5, "content_digest": "abc"},
        })


class StickyMessageRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self):
        cog = object.__new__(sticky.StickyMessage)
        cog.bot = MagicMock()
        cog._channel_tasks = {}
        cog._wake_events = {}
        cog._last_activity = {}
        cog._api_lock = asyncio.Lock()
        return cog

    async def test_on_message_only_schedules_human_guild_messages(self):
        cog = self.make_cog()
        cog._active_channel_ids = MagicMock(return_value={10})
        cog.schedule_refresh = MagicMock()

        human = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            author=SimpleNamespace(bot=False),
            webhook_id=None,
            channel=SimpleNamespace(id=10),
        )
        await sticky.StickyMessage.on_message(cog, human)
        cog.schedule_refresh.assert_called_once_with(1, 10)

        cog.schedule_refresh.reset_mock()
        for message in (
            SimpleNamespace(guild=None, author=SimpleNamespace(bot=False), webhook_id=None, channel=SimpleNamespace(id=10)),
            SimpleNamespace(guild=SimpleNamespace(id=1), author=SimpleNamespace(bot=True), webhook_id=None, channel=SimpleNamespace(id=10)),
            SimpleNamespace(guild=SimpleNamespace(id=1), author=SimpleNamespace(bot=False), webhook_id=5, channel=SimpleNamespace(id=10)),
            SimpleNamespace(guild=SimpleNamespace(id=1), author=SimpleNamespace(bot=False), webhook_id=None, channel=SimpleNamespace(id=11)),
        ):
            await sticky.StickyMessage.on_message(cog, message)
        cog.schedule_refresh.assert_not_called()

    async def test_publish_suppresses_mentions_during_automatic_refresh(self):
        cog = self.make_cog()
        channel = MagicMock(spec=discord.TextChannel)
        channel.type = discord.ChannelType.text
        channel.id = 10
        channel.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=True)
        guild = SimpleNamespace(
            me=object(),
            get_channel=lambda channel_id: channel if channel_id == 10 else None,
        )
        cog.bot.get_guild.return_value = guild
        cog.get_config = MagicMock(return_value={
            "quiet_seconds": 10,
            "min_interval_seconds": 30,
            "entries": [{"channel_id": 10, "content": "@everyone hello", "allow_mentions": True}],
        })
        cog.get_state = MagicMock(return_value={})
        cog.save_state = MagicMock(return_value=True)
        cog._delete_previous = AsyncMock()
        sent = SimpleNamespace(id=99)
        cog._send_with_retry = AsyncMock(return_value=sent)

        with patch.object(sticky, "get_stickymessage_limit", return_value=5):
            result = await sticky.StickyMessage.publish_entry(cog, 1, 10, notify_mentions=False)

        self.assertIs(result, sent)
        allowed_mentions = cog._send_with_retry.await_args.args[2]
        self.assertFalse(allowed_mentions.everyone)
        self.assertFalse(allowed_mentions.users)
        self.assertFalse(allowed_mentions.roles)

    async def test_manual_publish_uses_enabled_mentions(self):
        cog = self.make_cog()
        channel = MagicMock(spec=discord.TextChannel)
        channel.type = discord.ChannelType.news
        channel.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=True)
        guild = SimpleNamespace(me=object(), get_channel=lambda channel_id: channel)
        cog.bot.get_guild.return_value = guild
        cog.get_config = MagicMock(return_value={
            "quiet_seconds": 10,
            "min_interval_seconds": 30,
            "entries": [{"channel_id": 10, "content": "<@1>", "allow_mentions": True}],
        })
        cog.get_state = MagicMock(return_value={})
        cog.save_state = MagicMock(return_value=True)
        cog._delete_previous = AsyncMock()
        cog._send_with_retry = AsyncMock(return_value=SimpleNamespace(id=99))

        with patch.object(sticky, "get_stickymessage_limit", return_value=5):
            await sticky.StickyMessage.publish_entry(cog, 1, 10, notify_mentions=True)

        allowed_mentions = cog._send_with_retry.await_args.args[2]
        self.assertTrue(allowed_mentions.everyone)
        self.assertTrue(allowed_mentions.users)
        self.assertTrue(allowed_mentions.roles)

    async def test_raw_delete_clears_only_matching_sticky_message(self):
        cog = self.make_cog()
        cog.get_state = MagicMock(return_value={"10": {"message_id": 20, "last_sent_at": 1}})
        cog.save_state = MagicMock(return_value=True)
        payload = SimpleNamespace(guild_id=1, channel_id=10, message_id=20)
        await sticky.StickyMessage.on_raw_message_delete(cog, payload)
        saved = cog.save_state.call_args.args[1]
        self.assertNotIn("message_id", saved["10"])

        cog.save_state.reset_mock()
        payload.message_id = 21
        await sticky.StickyMessage.on_raw_message_delete(cog, payload)
        cog.save_state.assert_not_called()

    async def test_apply_config_removes_deleted_entry_and_keeps_over_limit_data(self):
        cog = self.make_cog()
        cog.remove_published_message = AsyncMock()
        cog.publish_entry = AsyncMock()
        cog._cancel_channel_task = MagicMock()
        cog._active_channel_ids = MagicMock(return_value={2})
        old = {
            "quiet_seconds": 10,
            "min_interval_seconds": 30,
            "entries": [{"channel_id": 1, "content": "old", "allow_mentions": False}],
        }
        new = {
            "quiet_seconds": 10,
            "min_interval_seconds": 30,
            "entries": [
                {"channel_id": 2, "content": "active", "allow_mentions": False},
                {"channel_id": 3, "content": "paused", "allow_mentions": False},
            ],
        }
        await sticky.StickyMessage.apply_config(cog, 1, new, old)
        cog.remove_published_message.assert_awaited_once_with(1, 1)
        cog.publish_entry.assert_awaited_once_with(1, 2, notify_mentions=True)
        cog._cancel_channel_task.assert_any_call(1, 3)

    async def test_changing_only_mention_policy_does_not_republish(self):
        cog = self.make_cog()
        cog.remove_published_message = AsyncMock()
        cog.publish_entry = AsyncMock()
        cog._cancel_channel_task = MagicMock()
        cog._active_channel_ids = MagicMock(return_value={2})
        old = {
            "quiet_seconds": 10,
            "min_interval_seconds": 30,
            "entries": [{"channel_id": 2, "content": "same", "allow_mentions": False}],
        }
        new = {
            "quiet_seconds": 10,
            "min_interval_seconds": 30,
            "entries": [{"channel_id": 2, "content": "same", "allow_mentions": True}],
        }
        await sticky.StickyMessage.apply_config(cog, 1, new, old)
        cog.publish_entry.assert_not_awaited()

    async def test_reordering_over_limit_swaps_active_messages(self):
        cog = self.make_cog()
        cog.remove_published_message = AsyncMock()
        cog.publish_entry = AsyncMock()
        cog._cancel_channel_task = MagicMock()
        cog._active_channel_ids = MagicMock(return_value={3})
        old = {
            "quiet_seconds": 10,
            "min_interval_seconds": 30,
            "entries": [
                {"channel_id": 2, "content": "first", "allow_mentions": False},
                {"channel_id": 3, "content": "second", "allow_mentions": False},
            ],
        }
        new = {
            "quiet_seconds": 10,
            "min_interval_seconds": 30,
            "entries": list(reversed(old["entries"])),
        }
        with patch.object(sticky, "get_stickymessage_limit", return_value=1):
            await sticky.StickyMessage.apply_config(cog, 1, new, old)
        cog.remove_published_message.assert_awaited_once_with(1, 2)
        cog.publish_entry.assert_awaited_once_with(1, 3, notify_mentions=True)

    async def test_previously_paused_entry_publishes_when_limit_increases(self):
        cog = self.make_cog()
        cog.remove_published_message = AsyncMock()
        cog.publish_entry = AsyncMock()
        cog._cancel_channel_task = MagicMock()
        cog._active_channel_ids = MagicMock(return_value={2, 3})
        config = {
            "quiet_seconds": 10,
            "min_interval_seconds": 30,
            "entries": [
                {"channel_id": 2, "content": "first", "allow_mentions": False},
                {"channel_id": 3, "content": "second", "allow_mentions": False},
            ],
        }
        with patch.object(sticky, "get_stickymessage_limit", side_effect=[1, 2]):
            await sticky.StickyMessage.apply_config(cog, 1, config, config)
        cog.publish_entry.assert_awaited_once_with(1, 3, notify_mentions=True)

    async def test_reconcile_limit_removes_and_restores_without_mentions(self):
        cog = self.make_cog()
        cog.get_config = MagicMock(return_value={
            "quiet_seconds": 10,
            "min_interval_seconds": 30,
            "entries": [
                {"channel_id": 1, "content": "one", "allow_mentions": False},
                {"channel_id": 2, "content": "two", "allow_mentions": True},
            ],
        })
        cog.remove_published_message = AsyncMock()
        cog.publish_entry = AsyncMock()
        with patch.object(sticky, "get_stickymessage_limit", return_value=1):
            await sticky.StickyMessage.reconcile_limit(cog, 1, previous_limit=2)
        cog.remove_published_message.assert_awaited_once_with(1, 2)

        cog.remove_published_message.reset_mock()
        with patch.object(sticky, "get_stickymessage_limit", return_value=2):
            await sticky.StickyMessage.reconcile_limit(cog, 1, previous_limit=1)
        cog.publish_entry.assert_awaited_once_with(1, 2, notify_mentions=False)

    async def test_wait_uses_quiet_and_minimum_interval(self):
        cog = self.make_cog()
        now = time.time()
        cog._last_activity[(1, 10)] = now - 100
        cog.get_config = MagicMock(return_value={
            "quiet_seconds": 5,
            "min_interval_seconds": 30,
            "entries": [{"channel_id": 10, "content": "x", "allow_mentions": False}],
        })
        cog._active_channel_ids = MagicMock(return_value={10})
        cog.get_state = MagicMock(return_value={"10": {"last_sent_at": now - 100}})
        cog.publish_entry = AsyncMock()
        task = asyncio.current_task()
        cog._channel_tasks[(1, 10)] = task
        cog._wake_events[(1, 10)] = asyncio.Event()
        await sticky.StickyMessage._wait_and_refresh(cog, 1, 10)
        cog.publish_entry.assert_awaited_once_with(1, 10, notify_mentions=False)
        self.assertNotIn((1, 10), cog._channel_tasks)

    async def test_new_activity_wakes_existing_waiter_without_cancelling_it(self):
        cog = self.make_cog()
        cog._active_channel_ids = MagicMock(return_value={10})
        existing = MagicMock()
        existing.done.return_value = False
        cog._channel_tasks[(1, 10)] = existing
        event = asyncio.Event()
        cog._wake_events[(1, 10)] = event
        sticky.StickyMessage.schedule_refresh(cog, 1, 10, activity_at=123)
        existing.cancel.assert_not_called()
        self.assertTrue(event.is_set())
        self.assertEqual(cog._last_activity[(1, 10)], 123)

    async def test_retry_after_prefers_discord_value(self):
        error = SimpleNamespace(retry_after=2.5, response=None)
        self.assertEqual(sticky.StickyMessage._retry_after(error, 0), 2.5)


class StickyMessageCommandTests(unittest.TestCase):
    def test_group_contains_expected_commands(self):
        command_names = {command.name for command in sticky.StickyMessage.__cog_app_commands__}
        self.assertEqual(
            command_names,
            {"add", "edit", "remove", "list", "publish", "move", "timing"},
        )
        payloads = [command.to_dict(sticky.bot.tree) for command in sticky.StickyMessage.__cog_app_commands__]
        self.assertTrue(all(payload["name"] in command_names for payload in payloads))


if __name__ == "__main__":
    unittest.main()
