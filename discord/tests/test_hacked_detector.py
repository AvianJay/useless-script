import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
if str(DISCORD_DIR) not in sys.path:
    sys.path.insert(0, str(DISCORD_DIR))

import HackedDetector as hacked
import globalenv


class FakeMember:
    def __init__(self, member_id, guild, *, timeout_until=None, administrator=False):
        self.id = member_id
        self.guild = guild
        self.bot = False
        self.timed_out_until = timeout_until
        self.guild_permissions = SimpleNamespace(administrator=administrator)
        self.roles = []
        self.mention = f"<@{member_id}>"
        self.name = "Test123"
        self.global_name = "Test User"
        self.avatar = None
        self.created_at = datetime.now(timezone.utc) - timedelta(days=2)
        self.send = AsyncMock()
        self.timeout_calls = []
        self.kick = AsyncMock()

    def __str__(self):
        return f"member-{self.id}"

    def is_timed_out(self):
        return bool(
            self.timed_out_until
            and self.timed_out_until > datetime.now(timezone.utc)
        )

    async def timeout(self, until, *, reason=None):
        self.timeout_calls.append((until, reason))
        self.timed_out_until = until

    async def remove_roles(self, role, *, reason=None):
        self.roles = [current for current in self.roles if current.id != role.id]

    async def add_roles(self, role, *, reason=None):
        if all(current.id != role.id for current in self.roles):
            self.roles.append(role)


class FakeGuild:
    def __init__(self, guild_id):
        self.id = guild_id
        self.name = f"guild-{guild_id}"
        self.me = SimpleNamespace(id=999)
        self.roles = []
        self.member = None
        self.fetch_member = AsyncMock(side_effect=self._fetch_member)

    async def _fetch_member(self, member_id):
        return self.member if self.member and self.member.id == member_id else None

    def get_member(self, member_id):
        return self.member if self.member and self.member.id == member_id else None

    def get_role(self, role_id):
        return next((role for role in self.roles if role.id == role_id), None)


class HackedDetectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user_data = {}
        self.server_data = {}

        def get_user_data(guild_id, user_id, key, default=None):
            return self.user_data.get((guild_id, user_id, key), default)

        def set_user_data(guild_id, user_id, key, value):
            self.user_data[(guild_id, user_id, key)] = value
            return True

        def get_all_user_data(guild_id, key, value=None):
            result = {}
            for (stored_guild_id, user_id, stored_key), stored_value in self.user_data.items():
                if stored_guild_id != guild_id or stored_key != key:
                    continue
                if value is not None and stored_value != value:
                    continue
                result[user_id] = {key: stored_value}
            return result

        def get_server_config(guild_id, key, default=None):
            return self.server_data.get((guild_id, key), default)

        patchers = [
            patch.object(hacked, "get_user_data", side_effect=get_user_data),
            patch.object(hacked, "set_user_data", side_effect=set_user_data),
            patch.object(hacked, "get_all_user_data", side_effect=get_all_user_data),
            patch.object(hacked, "get_server_config", side_effect=get_server_config),
            patch.object(hacked, "get_global_config", return_value=[]),
            patch.object(hacked, "ignore_user"),
            patch.object(hacked, "log"),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        hacked.sussy_thumbhashs.clear()
        self.cog = hacked.HackedDetector()

    def _stored(self, user_id, key, default=None):
        return self.user_data.get((0, user_id, key), default)

    def test_defaults_and_guild_settings_are_isolated(self):
        registered = {
            setting["database_key"]: setting
            for setting in globalenv.panel_settings["HackedDetector"]["settings"]
        }
        self.assertEqual(
            set(registered),
            {
                hacked.JOIN_DETECTION_ENABLED_KEY,
                hacked.JOIN_DETECTION_ACTION_KEY,
                hacked.CROSS_GUILD_DEFENSE_ENABLED_KEY,
            },
        )
        self.assertEqual(
            registered[hacked.JOIN_DETECTION_ACTION_KEY]["action_context"],
            "member_join",
        )
        self.assertTrue(self.cog._is_join_detection_enabled(1))
        self.assertTrue(self.cog._is_cross_guild_defense_enabled(1))
        self.assertEqual(
            self.cog._get_join_detection_action(1),
            hacked.DEFAULT_JOIN_DETECTION_ACTION,
        )

        self.server_data[(1, hacked.JOIN_DETECTION_ENABLED_KEY)] = False
        self.server_data[(1, hacked.JOIN_DETECTION_ACTION_KEY)] = "kick test"
        self.assertFalse(self.cog._is_join_detection_enabled(1))
        self.assertEqual(self.cog._get_join_detection_action(1), "kick test")
        self.assertTrue(self.cog._is_join_detection_enabled(2))
        self.assertEqual(
            self.cog._get_join_detection_action(2),
            hacked.DEFAULT_JOIN_DETECTION_ACTION,
        )

    async def test_disabled_join_detection_skips_action(self):
        guild = FakeGuild(1)
        member = FakeMember(10, guild)
        guild.member = member
        self.server_data[(1, hacked.JOIN_DETECTION_ENABLED_KEY)] = False
        with patch.object(hacked.Moderate, "do_action_str", new=AsyncMock()) as execute:
            self.assertFalse(await self.cog.handle_suspicious_join(member))
        execute.assert_not_awaited()

    async def test_invalid_or_message_dependent_join_action_is_not_executed(self):
        guild = FakeGuild(1)
        member = FakeMember(10, guild)
        guild.member = member
        self.server_data[(1, hacked.JOIN_DETECTION_ACTION_KEY)] = "warn hello"
        with patch.object(hacked.Moderate, "do_action_str", new=AsyncMock()) as execute:
            self.assertFalse(await self.cog.handle_suspicious_join(member))
        execute.assert_not_awaited()
        self.assertIsNone(self._stored(member.id, "hacked_timed_out_channel"))

    async def test_custom_alias_and_multiple_actions_record_actual_timeout(self):
        guild = FakeGuild(1)
        member = FakeMember(10, guild)
        guild.member = member
        self.server_data[(1, hacked.JOIN_DETECTION_ACTION_KEY)] = "safe"
        actual_expiry = datetime.now(timezone.utc) + timedelta(hours=2)

        async def execute(*args, **kwargs):
            member.timed_out_until = actual_expiry
            return ["muted", "message sent"], "success"

        with (
            patch.object(
                hacked.Moderate,
                "_load_custom_action_strings",
                return_value={"safe": "mute 2h test, smm"},
            ),
            patch.object(hacked.Moderate, "do_action_str", side_effect=execute),
        ):
            self.assertTrue(await self.cog.handle_suspicious_join(member))

        self.assertEqual(self._stored(member.id, "hacked_timed_out_channel"), [1])
        expiries = self._stored(member.id, self.cog.TIMEOUT_EXPIRIES_KEY)
        self.assertEqual(self.cog._parse_datetime(expiries["1"]), actual_expiry)
        member.send.assert_awaited_once()

    async def test_permission_skip_without_timeout_creates_no_recovery_case(self):
        guild = FakeGuild(1)
        member = FakeMember(10, guild)
        guild.member = member
        with patch.object(
            hacked.Moderate,
            "do_action_str",
            new=AsyncMock(return_value=(["missing permission"], "skipped")),
        ):
            self.assertTrue(await self.cog.handle_suspicious_join(member))
        self.assertIsNone(self._stored(member.id, "hacked_timed_out_channel"))
        member.send.assert_not_awaited()

    async def test_non_timeout_action_creates_no_recovery_case(self):
        guild = FakeGuild(1)
        member = FakeMember(10, guild)
        guild.member = member
        self.server_data[(1, hacked.JOIN_DETECTION_ACTION_KEY)] = "kick test"
        with patch.object(
            hacked.Moderate,
            "do_action_str",
            new=AsyncMock(return_value=(["kicked"], "success")),
        ):
            self.assertTrue(await self.cog.handle_suspicious_join(member))
        guild.fetch_member.assert_not_awaited()
        self.assertIsNone(self._stored(member.id, "hacked_timed_out_channel"))

    def test_per_guild_expiries_and_legacy_value_are_merged_lazily(self):
        user_id = 10
        first = datetime.now(timezone.utc) + timedelta(hours=1)
        legacy = datetime.now(timezone.utc) + timedelta(hours=3)
        self.cog._merge_hacked_user_records(user_id, {1: first})
        self.user_data[(0, user_id, "hacked_timed_out_channel")] = [1, 2]
        self.user_data[(0, user_id, self.cog.LEGACY_TIMEOUT_EXPIRY_KEY)] = legacy.isoformat()

        expiries = self.cog._get_timeout_expiries(user_id)

        self.assertEqual(expiries[1], first)
        self.assertEqual(expiries[2], legacy)
        self.assertIsNone(self._stored(user_id, self.cog.LEGACY_TIMEOUT_EXPIRY_KEY))

    async def test_expiry_processing_only_kicks_due_guild(self):
        now = datetime.now(timezone.utc)
        guild_due = FakeGuild(1)
        member_due = FakeMember(10, guild_due, timeout_until=now + timedelta(minutes=1))
        guild_due.member = member_due
        guild_future = FakeGuild(2)
        member_future = FakeMember(10, guild_future, timeout_until=now + timedelta(hours=2))
        guild_future.member = member_future
        self.cog._merge_hacked_user_records(
            10,
            {1: now - timedelta(seconds=1), 2: now + timedelta(hours=1)},
        )

        with (
            patch.object(hacked.bot, "get_guild", side_effect=lambda gid: {1: guild_due, 2: guild_future}.get(gid)),
            patch.object(hacked, "check_member_hierarchy", return_value=(True, "ok")),
        ):
            await self.cog._kick_expired_unverified_user(10, now)

        member_due.kick.assert_awaited_once()
        member_future.kick.assert_not_awaited()
        self.assertEqual(self._stored(10, "hacked_timed_out_channel"), [2])
        self.assertEqual(set(self._stored(10, self.cog.TIMEOUT_EXPIRIES_KEY)), {"2"})

    def test_disabled_cached_events_are_removed_before_threshold(self):
        self.server_data[(2, hacked.CROSS_GUILD_DEFENSE_ENABLED_KEY)] = False
        events = [
            {"guild_id": 1, "channel_id": 11, "time": 1},
            {"guild_id": 2, "channel_id": 22, "time": 1},
        ]
        self.cog.usercache[10] = list(events)
        filtered = self.cog._filter_enabled_cross_guild_events(self.cog.usercache, 10, events)
        self.assertEqual([event["guild_id"] for event in filtered], [1])
        self.assertEqual(self.cog.usercache[10], filtered)

    async def test_setting_change_rechecked_before_cached_message_delete(self):
        self.server_data[(1, hacked.CROSS_GUILD_DEFENSE_ENABLED_KEY)] = False
        message = SimpleNamespace(
            id=100,
            author=SimpleNamespace(id=10),
            channel=SimpleNamespace(id=11),
            guild=SimpleNamespace(id=1),
            delete=AsyncMock(),
        )
        deleted, failed = await self.cog._delete_detected_messages([
            {"guild_id": 1, "message_id": message.id, "message": message},
        ])
        self.assertEqual((deleted, failed), (0, 0))
        message.delete.assert_not_awaited()

    async def test_disabled_source_does_not_record_or_delete_messages(self):
        self.server_data[(1, hacked.CROSS_GUILD_DEFENSE_ENABLED_KEY)] = False
        message = SimpleNamespace(
            author=SimpleNamespace(id=10, bot=False),
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=11),
            content="https://discord.gg/example",
            attachments=[],
        )
        with (
            patch.object(self.cog, "_record_suspicious_event") as record,
            patch.object(self.cog, "_delete_detected_messages", new=AsyncMock()) as delete,
        ):
            await self.cog.on_message(message)
        record.assert_not_called()
        delete.assert_not_awaited()

    async def test_disabled_raw_source_does_not_record_events(self):
        self.server_data[(1, hacked.CROSS_GUILD_DEFENSE_ENABLED_KEY)] = False
        hacked.sussy_thumbhashs.append("known-hash")
        payload = (
            '{"t":"MESSAGE_CREATE","d":{"guild_id":"1","channel_id":"11",'
            '"author":{"id":"10","bot":false},'
            '"attachments":[{"placeholder":"known-hash"},{"placeholder":"known-hash"}]}}'
        )
        with patch.object(self.cog, "_record_raw_suspicious_event") as record:
            await self.cog.on_socket_raw_receive(payload)
        record.assert_not_called()

    async def test_cross_guild_targets_respect_each_guild_switch(self):
        enabled_guild = FakeGuild(1)
        enabled_member = FakeMember(10, enabled_guild)
        enabled_guild.member = enabled_member
        disabled_guild = FakeGuild(2)
        disabled_member = FakeMember(10, disabled_guild)
        disabled_guild.member = disabled_member
        self.server_data[(2, hacked.CROSS_GUILD_DEFENSE_ENABLED_KEY)] = False
        user = SimpleNamespace(
            id=10,
            mention="<@10>",
            mutual_guilds=[enabled_guild, disabled_guild],
            send=AsyncMock(),
        )

        with patch.object(hacked, "check_member_hierarchy", return_value=(True, "ok")):
            await self.cog.handle_hacked_user(user)

        self.assertEqual(len(enabled_member.timeout_calls), 1)
        self.assertEqual(disabled_member.timeout_calls, [])
        self.assertEqual(self._stored(10, "hacked_timed_out_channel"), [1])

    async def test_existing_case_can_unlock_after_features_are_disabled(self):
        guild = FakeGuild(1)
        member = FakeMember(10, guild, timeout_until=datetime.now(timezone.utc) + timedelta(hours=1))
        guild.member = member
        self.server_data[(1, hacked.JOIN_DETECTION_ENABLED_KEY)] = False
        self.server_data[(1, hacked.CROSS_GUILD_DEFENSE_ENABLED_KEY)] = False
        self.cog._merge_hacked_user_records(10, {1: member.timed_out_until})
        user = SimpleNamespace(id=10)

        with patch.object(hacked.bot, "get_guild", return_value=guild):
            self.assertTrue(await self.cog.unlock_user(user))

        self.assertIsNone(member.timed_out_until)
        self.assertEqual(self._stored(10, "hacked_timed_out_channel"), [])
        self.assertTrue(self._stored(10, "verified"))


if __name__ == "__main__":
    unittest.main()
