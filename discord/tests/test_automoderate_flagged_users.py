import asyncio
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import AutoModerate
import Moderate


def api_payload(items):
    return {"code": 200, "message": "OK", "data": {"items": items}}


def api_item(user_id="1242219785325117442", *, reason="spam", status=1):
    return {
        "id": 1,
        "userid": user_id,
        "reason": reason,
        "reporter": {"id": "852006773854306304", "name": "Main Admin"},
        "reported_at": "2026-05-12T12:00:00Z",
        "status": status,
    }


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status = status
        self.payload = payload
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.closed = False
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class BlacklistPayloadTests(unittest.TestCase):
    def test_normalizes_string_ids_and_multiple_records(self):
        payload = api_payload([api_item(), api_item(reason="abuse")])
        result = AutoModerate._normalize_blacklist_payload(payload)
        self.assertEqual(len(result[1242219785325117442]), 2)
        self.assertEqual(result[1242219785325117442][0]["reporter_name"], "Main Admin")

    def test_rejects_incomplete_or_mixed_status_snapshots(self):
        with self.assertRaisesRegex(ValueError, "data.items"):
            AutoModerate._normalize_blacklist_payload({"code": 200, "data": {}})
        with self.assertRaisesRegex(ValueError, "非有效紀錄"):
            AutoModerate._normalize_blacklist_payload(api_payload([api_item(status=0)]))
        bad_date = api_item()
        bad_date["reported_at"] = "not-a-date"
        with self.assertRaisesRegex(ValueError, "reported_at"):
            AutoModerate._normalize_blacklist_payload(api_payload([bad_date]))

    def test_embed_record_format_never_exceeds_discord_limit(self):
        value = AutoModerate._format_embed_record_lines(["x" * 2000, "second"])
        self.assertLessEqual(len(value), 1024)
        self.assertIn("另有 1 筆", value)


class BlacklistSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = AutoModerate.AutoModerate(MagicMock())

    async def asyncTearDown(self):
        if self.cog._blacklist_session is not None and not self.cog._blacklist_session.closed:
            close = getattr(self.cog._blacklist_session, "close", None)
            if close:
                result = close()
                if asyncio.iscoroutine(result):
                    await result

    async def test_429_retries_once_using_retry_after(self):
        session = FakeSession([
            FakeResponse(429, headers={"Retry-After": "0"}),
            FakeResponse(200, api_payload([api_item()])),
        ])
        self.cog._blacklist_session = session
        with (
            patch.object(AutoModerate, "config", return_value="secret-not-logged"),
            patch.object(AutoModerate.asyncio, "sleep", new=AsyncMock()) as sleep,
        ):
            snapshot = await self.cog._fetch_blacklist_snapshot()
        self.assertIn(1242219785325117442, snapshot)
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(session.calls[0][0].endswith("/blacklist?status=1"))
        self.assertNotIn("params", session.calls[0][1])
        sleep.assert_awaited_once_with(0.0)

    async def test_failed_sync_keeps_last_successful_cache(self):
        old = {1: [{"reason": "old"}]}
        self.cog._blacklist_cache = old
        with (
            patch.object(AutoModerate, "config", return_value="configured"),
            patch.object(self.cog, "_fetch_blacklist_snapshot", side_effect=RuntimeError("offline")),
            patch.object(AutoModerate, "log"),
        ):
            self.assertFalse(await self.cog.sync_blacklist_cache())
        self.assertIs(self.cog._blacklist_cache, old)
        self.assertIsNone(self.cog._blacklist_last_success)

    async def test_auth_failure_keeps_last_successful_cache(self):
        old = {1: [{"reason": "old"}]}
        self.cog._blacklist_cache = old
        self.cog._blacklist_session = FakeSession([FakeResponse(401)])
        with (
            patch.object(AutoModerate, "config", return_value="configured"),
            patch.object(AutoModerate, "log"),
        ):
            self.assertFalse(await self.cog.sync_blacklist_cache())
        self.assertIs(self.cog._blacklist_cache, old)
        self.assertIn("HTTP 401", self.cog._blacklist_last_error)

    async def test_invalid_payload_keeps_last_successful_cache(self):
        old = {1: [{"reason": "old"}]}
        self.cog._blacklist_cache = old
        self.cog._blacklist_session = FakeSession([
            FakeResponse(200, {"code": 200, "data": {}}),
        ])
        with (
            patch.object(AutoModerate, "config", return_value="configured"),
            patch.object(AutoModerate, "log"),
        ):
            self.assertFalse(await self.cog.sync_blacklist_cache())
        self.assertIs(self.cog._blacklist_cache, old)
        self.assertIn("data.items", self.cog._blacklist_last_error)

    async def test_successful_sync_atomically_replaces_cache(self):
        new_snapshot = {2: [{"reason": "new"}]}
        self.cog._blacklist_cache = {1: [{"reason": "old"}]}
        with (
            patch.object(AutoModerate, "config", return_value="configured"),
            patch.object(self.cog, "_fetch_blacklist_snapshot", new=AsyncMock(return_value=new_snapshot)),
            patch.object(AutoModerate, "log"),
        ):
            self.assertTrue(await self.cog.sync_blacklist_cache())
        self.assertIs(self.cog._blacklist_cache, new_snapshot)
        self.assertIsNotNone(self.cog._blacklist_last_success)


class LocalFlagTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "flagged.db"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript("""
                CREATE TABLE guilds (id INTEGER UNIQUE, name TEXT NOT NULL);
                CREATE TABLE flagged_users (
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER,
                    flagged_at TIMESTAMP,
                    flagged_role BOOLEAN DEFAULT 0,
                    UNIQUE(user_id, guild_id)
                );
                INSERT INTO guilds VALUES (10, 'Test Guild');
                INSERT INTO flagged_users VALUES (1, 10, '2026-08-01 00:00:00', 1);
                INSERT INTO flagged_users VALUES (2, 10, '2026-08-01 00:00:00', 0);
                INSERT INTO flagged_users VALUES (3, 10, '2026-04-01 00:00:00', 1);
                INSERT INTO flagged_users VALUES (4, 10, '2026-05-12 00:00:00', 1);
                INSERT INTO flagged_users VALUES (5, 10, '2026-05-11 23:59:59', 1);
            """)
            conn.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_active_and_history_modes_respect_three_calendar_months(self):
        now = datetime(2026, 8, 12, tzinfo=timezone.utc)
        with patch.object(AutoModerate, "config", return_value=str(self.db_path)):
            active = AutoModerate._load_local_flagged_records(match_mode="active", now=now)
            history = AutoModerate._load_local_flagged_records(match_mode="history", now=now)
        self.assertEqual(set(active), {1, 4})
        self.assertEqual(set(history), {1, 2, 4})


class FlaggedUserBehaviorTests(unittest.IsolatedAsyncioTestCase):
    def test_join_action_rejects_message_only_commands(self):
        self.assertFalse(Moderate.analyze_member_join_action("delete")["valid"])
        self.assertFalse(Moderate.analyze_member_join_action("warn hi")["valid"])
        self.assertTrue(Moderate.analyze_member_join_action("mute 10m flagged")["valid"])

    def test_source_selection(self):
        self.assertTrue(AutoModerate._action_matches_sources("local", has_local=True, has_api=False))
        self.assertFalse(AutoModerate._action_matches_sources("local", has_local=False, has_api=True))
        self.assertTrue(AutoModerate._action_matches_sources("api", has_local=True, has_api=True))
        self.assertTrue(AutoModerate._action_matches_sources("both", has_local=False, has_api=True))

    def test_new_config_takes_priority_over_legacy_channel(self):
        def get_config(_guild_id, key, default=None):
            if key == "automod":
                return {"flagged_user": {"enabled": False, "log_channel": ""}}
            if key == "flagged_user_onjoin_channel":
                return 555
            return default

        with patch.object(AutoModerate, "get_server_config", side_effect=get_config):
            result = AutoModerate._normalize_flagged_user_config(10)
        self.assertFalse(result["enabled"])
        self.assertEqual(result["log_channel"], "")
        self.assertFalse(result["legacy"])

    def test_malformed_new_config_still_takes_priority_over_legacy_channel(self):
        def get_config(_guild_id, key, default=None):
            if key == "automod":
                return {"flagged_user": None}
            if key == "flagged_user_onjoin_channel":
                return 555
            return default

        with patch.object(AutoModerate, "get_server_config", side_effect=get_config):
            result = AutoModerate._normalize_flagged_user_config(10)
        self.assertFalse(result["enabled"])
        self.assertIsNone(result["log_channel"])
        self.assertFalse(result["legacy"])

    async def test_action_runs_once_even_when_notification_channel_is_missing(self):
        bot = MagicMock()
        cog = AutoModerate.AutoModerate(bot)
        guild = MagicMock()
        guild.id = 10
        guild.get_channel.return_value = None
        member = SimpleNamespace(id=99, guild=guild, mention="<@99>")
        cog._blacklist_cache = {99: [{
            "reason": "spam",
            "reporter_name": "Admin",
            "reporter_id": "123",
            "reported_at": "2026-08-01T00:00:00Z",
        }]}
        config = {
            "enabled": True,
            "log_channel": "555",
            "action": "mute 10m flagged",
            "action_source": "both",
            "local_match_mode": "active",
            "legacy": False,
        }
        with (
            patch.object(AutoModerate, "Moderate", Moderate, create=True),
            patch.object(AutoModerate, "get_server_config", return_value={}),
            patch.object(AutoModerate, "_normalize_flagged_user_config", return_value=config),
            patch.object(AutoModerate, "_load_local_flagged_records", return_value={99: [{
                "guild_name": "Local",
                "flagged_at": "2026-08-01",
                "flagged_role": True,
            }]}),
            patch.object(AutoModerate, "do_action_str", new=AsyncMock(return_value=["done"])) as action,
            patch.object(AutoModerate, "log"),
        ):
            await cog.on_member_join(member)
        action.assert_awaited_once()

    async def test_action_failure_still_sends_notification(self):
        cog = AutoModerate.AutoModerate(MagicMock())
        guild = MagicMock()
        guild.id = 10
        channel = MagicMock()
        channel.send = AsyncMock()
        guild.get_channel.return_value = channel
        member = SimpleNamespace(id=99, guild=guild, mention="<@99>")
        config = {
            "enabled": True,
            "log_channel": "555",
            "action": "kick flagged",
            "action_source": "api",
            "local_match_mode": "active",
            "legacy": False,
        }
        cog._blacklist_cache = AutoModerate._normalize_blacklist_payload(
            api_payload([api_item(user_id="99")])
        )
        with (
            patch.object(AutoModerate, "Moderate", Moderate, create=True),
            patch.object(AutoModerate, "_normalize_flagged_user_config", return_value=config),
            patch.object(AutoModerate, "_load_local_flagged_records", return_value={}),
            patch.object(AutoModerate, "do_action_str", new=AsyncMock(side_effect=RuntimeError("no permission"))) as action,
            patch.object(AutoModerate, "log"),
        ):
            await cog.on_member_join(member)
        action.assert_awaited_once()
        channel.send.assert_awaited_once()
        embed = channel.send.await_args.kwargs["embed"]
        self.assertIn("處置失敗", embed.fields[-1].value)

    async def test_action_source_filters_execution_without_hiding_notification(self):
        for action_source, should_act in (("local", False), ("api", True), ("both", True)):
            with self.subTest(action_source=action_source):
                cog = AutoModerate.AutoModerate(MagicMock())
                guild = MagicMock()
                guild.id = 10
                channel = MagicMock()
                channel.send = AsyncMock()
                guild.get_channel.return_value = channel
                member = SimpleNamespace(id=99, guild=guild, mention="<@99>")
                cog._blacklist_cache = AutoModerate._normalize_blacklist_payload(
                    api_payload([api_item(user_id="99")])
                )
                config = {
                    "enabled": True,
                    "log_channel": "555",
                    "action": "kick flagged",
                    "action_source": action_source,
                    "local_match_mode": "active",
                    "legacy": False,
                }
                with (
                    patch.object(AutoModerate, "Moderate", Moderate, create=True),
                    patch.object(AutoModerate, "_normalize_flagged_user_config", return_value=config),
                    patch.object(AutoModerate, "_load_local_flagged_records", return_value={}),
                    patch.object(AutoModerate, "do_action_str", new=AsyncMock(return_value=["done"])) as action,
                    patch.object(AutoModerate, "log"),
                ):
                    await cog.on_member_join(member)
                self.assertEqual(action.await_count, int(should_act))
                channel.send.assert_awaited_once()

    async def test_sqlite_failure_degrades_to_api_cache_without_action(self):
        cog = AutoModerate.AutoModerate(MagicMock())
        guild = MagicMock()
        guild.id = 10
        channel = MagicMock()
        channel.send = AsyncMock()
        guild.get_channel.return_value = channel
        member = SimpleNamespace(id=99, guild=guild, mention="<@99>")
        cog._blacklist_cache = AutoModerate._normalize_blacklist_payload(
            api_payload([api_item(user_id="99")])
        )
        config = {
            "enabled": True,
            "log_channel": "555",
            "action": "",
            "action_source": "both",
            "local_match_mode": "active",
            "legacy": False,
        }
        with (
            patch.object(AutoModerate, "_normalize_flagged_user_config", return_value=config),
            patch.object(
                AutoModerate,
                "_load_local_flagged_records",
                side_effect=sqlite3.OperationalError("database unavailable"),
            ),
            patch.object(AutoModerate, "do_action_str", new=AsyncMock()) as action,
            patch.object(AutoModerate, "log") as event_log,
        ):
            await cog.on_member_join(member)
        action.assert_not_awaited()
        channel.send.assert_awaited_once()
        self.assertTrue(any("本機標記資料失敗" in str(call) or "讀取本機標記資料失敗" in str(call) for call in event_log.call_args_list))

    async def test_scan_uses_current_cache_without_fetching_api(self):
        cog = AutoModerate.AutoModerate(MagicMock())
        cog._blacklist_cache = AutoModerate._normalize_blacklist_payload(
            api_payload([api_item(user_id="99")])
        )
        cog._blacklist_last_success = datetime(2026, 8, 12, tzinfo=timezone.utc)
        guild = MagicMock()
        guild.id = 10
        guild.members = [SimpleNamespace(id=99, name="Flagged")]
        guild.get_member.return_value = guild.members[0]
        interaction = MagicMock()
        interaction.guild = guild
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        config = {
            "enabled": True,
            "log_channel": "555",
            "action": "",
            "action_source": "both",
            "local_match_mode": "active",
            "legacy": False,
        }
        with (
            patch.object(AutoModerate, "_normalize_flagged_user_config", return_value=config),
            patch.object(AutoModerate, "_load_local_flagged_records", return_value={}),
            patch.object(cog, "_fetch_blacklist_snapshot", new=AsyncMock()) as fetch,
        ):
            await AutoModerate.AutoModerate.scan_flagged_users.callback(cog, interaction, None)
        fetch.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()

    async def test_toggle_seeds_new_config_from_legacy_channel(self):
        cog = AutoModerate.AutoModerate(MagicMock())
        interaction = MagicMock()
        interaction.guild.id = 10
        interaction.response.send_message = AsyncMock()
        interaction.followup.send = AsyncMock()

        def get_config(_guild_id, key, default=None):
            if key == "automod":
                return {}
            if key == "flagged_user_onjoin_channel":
                return 555
            return default

        with (
            patch.object(AutoModerate, "get_server_config", side_effect=get_config),
            patch.object(AutoModerate, "set_server_config") as save_config,
        ):
            await AutoModerate.AutoModerate.toggle_automod_setting.callback(
                cog,
                interaction,
                "flagged_user",
                "True",
            )

        saved = save_config.call_args.args[2]["flagged_user"]
        self.assertEqual(saved, {
            "enabled": True,
            "log_channel": "555",
            "action": "",
            "action_source": "both",
            "local_match_mode": "active",
        })
        interaction.followup.send.assert_not_awaited()

    async def test_settings_can_clear_flagged_user_action(self):
        cog = AutoModerate.AutoModerate(MagicMock())
        interaction = MagicMock()
        interaction.guild.id = 10
        interaction.response.send_message = AsyncMock()
        interaction.guild.get_channel.return_value = None
        current = {
            "flagged_user": {
                "enabled": True,
                "log_channel": "555",
                "action": "kick flagged",
                "action_source": "both",
                "local_match_mode": "active",
            },
        }
        with (
            patch.object(AutoModerate, "get_server_config", return_value=current),
            patch.object(AutoModerate, "set_server_config") as save_config,
        ):
            await AutoModerate.AutoModerate.set_automod_setting.callback(
                cog,
                interaction,
                "flagged_user-action",
                "clear",
            )
        self.assertEqual(save_config.call_args.args[2]["flagged_user"]["action"], "")


if __name__ == "__main__":
    unittest.main()
