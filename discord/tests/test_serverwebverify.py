import asyncio
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import globalenv


_required_modules = list(globalenv.modules)
for _module_name in ("Website", "Moderate", "UtilCommands"):
    if _module_name not in _required_modules:
        _required_modules.append(_module_name)
with patch.object(globalenv, "modules", _required_modules):
    import ServerWebVerify as webverify


class IsolatedWebVerifyDatabase:
    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.database_path = Path(self.tempdir.name) / "webverify.db"

        @contextmanager
        def connect():
            connection = sqlite3.connect(self.database_path)
            try:
                yield connection
            finally:
                connection.close()

        self.connection_patcher = patch.object(webverify, "get_db_connection", side_effect=connect)
        self.connection_patcher.start()
        self.addCleanup(self.connection_patcher.stop)
        webverify.init_db()


class WebVerifyNormalizationTests(IsolatedWebVerifyDatabase, unittest.TestCase):
    def test_country_modes_and_codes_share_canonical_rules(self):
        self.assertEqual(webverify.normalize_country_mode("blocklist"), "blacklist")
        self.assertEqual(webverify.normalize_country_mode("BLACKLIST"), "blacklist")
        self.assertEqual(webverify.normalize_country_mode("allowlist"), "whitelist")
        self.assertEqual(webverify.normalize_country_mode("WHITELIST"), "whitelist")
        self.assertEqual(webverify.normalize_country_codes("tw, TW jp invalid"), ["TW", "JP"])

        self.assertTrue(webverify.country_alert_matches("tw", {"mode": "blocklist", "countries": ["TW"]}))
        self.assertFalse(webverify.country_alert_matches("jp", {"mode": "blacklist", "countries": ["TW"]}))
        self.assertFalse(webverify.country_alert_matches("tw", {"mode": "allowlist", "countries": ["TW"]}))
        self.assertTrue(webverify.country_alert_matches("jp", {"mode": "whitelist", "countries": ["TW"]}))

    def test_extract_client_ip_normalizes_proxy_lists_and_rejects_invalid_values(self):
        self.assertEqual(
            webverify.extract_client_ip(
                {"CF-Connecting-IP": " 2001:0db8::1 ", "X-Forwarded-For": "198.51.100.1"},
                "127.0.0.1",
            ),
            "2001:db8::1",
        )
        self.assertEqual(
            webverify.extract_client_ip(
                {"CF-Connecting-IP": "invalid", "X-Forwarded-For": "bad, 198.51.100.2, 198.51.100.3"},
                None,
            ),
            "198.51.100.2",
        )
        self.assertIsNone(webverify.extract_client_ip({"X-Forwarded-For": "not-an-ip"}, "also-invalid"))

    def test_location_cache_only_accepts_two_letter_country_codes(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"city": "Taipei", "region": "Taipei", "country": "TWN"}
        with patch.object(webverify.requests, "get", return_value=response):
            self.assertIsNone(webverify.get_ip_location("198.51.100.10"))
        with webverify.get_db_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM webverify_ip_location").fetchone()[0], 0)

        response.json.return_value = {"city": "Taipei", "region": "Taipei", "country": "tw"}
        with patch.object(webverify.requests, "get", return_value=response):
            self.assertEqual(webverify.get_ip_location("198.51.100.10")["country"], "TW")
        with webverify.get_db_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM webverify_ip_location").fetchone()[0], 1)

    def test_config_migration_converts_legacy_country_mode(self):
        original = {
            "enabled": True,
            "webverify_country_alert": {
                "enabled": True,
                "mode": "allowlist",
                "countries": ["tw", "TW"],
                "channel_id": "123",
            },
        }
        with patch.object(webverify, "set_server_config") as save:
            migrated = webverify.migrate_webverify_config(9, original)
        self.assertEqual(migrated["webverify_country_alert"]["mode"], "whitelist")
        self.assertEqual(migrated["webverify_country_alert"]["countries"], ["TW"])
        self.assertEqual(migrated["webverify_country_alert"]["channel_id"], 123)
        self.assertEqual(migrated["relation_blacklist"], webverify.DEFAULT_RELATION_BLACKLIST_CONFIG)
        save.assert_called_once_with(9, "webverify_config", migrated)

    def test_sqlite_timestamp_parser_treats_naive_values_as_utc(self):
        parsed = webverify.parse_history_timestamp("2026-08-26 01:02:03")
        self.assertEqual(parsed.isoformat(), "2026-08-26T01:02:03+00:00")
        self.assertIsNone(webverify.parse_history_timestamp("not-a-time"))


class WebVerifyRelationDatabaseTests(IsolatedWebVerifyDatabase, unittest.TestCase):
    def test_relation_merge_rekeys_blacklists_and_success_history(self):
        first = webverify.add_webverify_history(1, 10, "198.51.100.1", "a" * 32)
        second = webverify.add_webverify_history(2, 10, "198.51.100.2", "b" * 32)
        canonical, old = sorted((first, second))
        action = "kick relation blacklist"
        webverify.record_relation_action_success(10, old, 2, action)
        server_configs = {
            10: {
                "relation_blacklist": {
                    "enabled": True,
                    "relation_ids": [old],
                    "action": action,
                    "channel_id": 123,
                }
            }
        }

        with (
            patch.object(webverify, "get_all_server_config_key", return_value=server_configs),
            patch.object(webverify, "set_server_config") as save,
        ):
            merged = webverify.add_webverify_history(2, 10, "198.51.100.1", "b" * 32)

        self.assertEqual(merged, canonical)
        self.assertEqual(webverify.get_user_relation_id(1), canonical)
        self.assertEqual(webverify.get_user_relation_id(2), canonical)
        self.assertTrue(webverify.relation_action_succeeded(10, canonical, 2, action))
        self.assertFalse(webverify.relation_action_succeeded(11, canonical, 2, action))
        saved_relation = save.call_args.args[2]["relation_blacklist"]
        self.assertEqual(saved_relation["relation_ids"], [canonical])

    def test_clearing_one_guild_history_does_not_touch_another_guild(self):
        relation_id = webverify.add_webverify_history(1, 10, "198.51.100.1", "a" * 32)
        webverify.record_relation_action_success(10, relation_id, 1, "kick one")
        webverify.record_relation_action_success(11, relation_id, 1, "kick one")
        webverify.clear_relation_action_history(10, relation_id)
        self.assertFalse(webverify.relation_action_succeeded(10, relation_id, 1, "kick one"))
        self.assertTrue(webverify.relation_action_succeeded(11, relation_id, 1, "kick one"))

    def test_changing_action_allows_a_new_successful_action(self):
        relation_id = webverify.add_webverify_history(1, 10, "198.51.100.1", "a" * 32)
        webverify.record_relation_action_success(10, relation_id, 1, "kick first")
        self.assertTrue(webverify.relation_action_succeeded(10, relation_id, 1, "kick first"))
        self.assertFalse(webverify.relation_action_succeeded(10, relation_id, 1, "kick changed"))


class WebVerifyRelationExecutionTests(IsolatedWebVerifyDatabase, unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_guild(*members):
        member_map = {member.id: member for member in members}

        async def fetch_member(user_id):
            return member_map[user_id]

        return SimpleNamespace(
            id=10,
            me=SimpleNamespace(id=999),
            get_member=member_map.get,
            fetch_member=fetch_member,
            get_channel=lambda _channel_id: None,
        )

    async def test_execute_actions_all_present_members_and_deduplicates_successes(self):
        first = SimpleNamespace(id=1, mention="<@1>")
        second = SimpleNamespace(id=2, mention="<@2>")
        guild = self.make_guild(first, second)
        relation_id = "12345678-1234-5678-1234-567812345678"
        settings = {"enabled": True, "relation_ids": [relation_id], "action": "kick test", "channel_id": None}

        with (
            patch.object(webverify, "get_relation_user_ids", return_value=[1, 2]),
            patch.object(webverify, "relation_action_succeeded", side_effect=lambda _g, _r, uid, _a: uid == 2),
            patch.object(webverify, "record_relation_action_success") as record,
            patch.object(webverify.Moderate, "analyze_member_join_action", return_value={"valid": True, "requires_confirmation": False, "normalized": "kick test"}),
            patch.object(webverify.Moderate, "do_action_str", new=AsyncMock(return_value=(["done"], "success"))) as execute,
        ):
            result = await webverify.execute_relation_blacklist(guild, relation_id, settings, send_alert=False)

        execute.assert_awaited_once()
        self.assertEqual(execute.await_args.kwargs["user"].id, 1)
        record.assert_called_once_with(10, relation_id, 1, "kick test")
        self.assertEqual(result["completed"], [2])
        self.assertEqual(len(result["actioned"]), 1)

    async def test_failed_action_is_retryable_and_not_recorded(self):
        member = SimpleNamespace(id=1, mention="<@1>")
        guild = self.make_guild(member)
        relation_id = "12345678-1234-5678-1234-567812345678"
        settings = {"enabled": True, "relation_ids": [relation_id], "action": "kick test", "channel_id": None}
        execute = AsyncMock(return_value=(["failed"], "failed"))
        with (
            patch.object(webverify, "get_relation_user_ids", return_value=[1]),
            patch.object(webverify, "relation_action_succeeded", return_value=False),
            patch.object(webverify, "record_relation_action_success") as record,
            patch.object(webverify.Moderate, "analyze_member_join_action", return_value={"valid": True, "requires_confirmation": False, "normalized": "kick test"}),
            patch.object(webverify.Moderate, "do_action_str", new=execute),
        ):
            await webverify.execute_relation_blacklist(guild, relation_id, settings, send_alert=False)
            await webverify.execute_relation_blacklist(guild, relation_id, settings, send_alert=False)
        self.assertEqual(execute.await_count, 2)
        record.assert_not_called()

    async def test_permission_skip_is_reported_separately_and_not_recorded(self):
        member = SimpleNamespace(id=1, mention="<@1>")
        guild = self.make_guild(member)
        relation_id = "12345678-1234-5678-1234-567812345678"
        settings = {"enabled": True, "relation_ids": [relation_id], "action": "kick test", "channel_id": None}
        with (
            patch.object(webverify, "get_relation_user_ids", return_value=[1]),
            patch.object(webverify, "relation_action_succeeded", return_value=False),
            patch.object(webverify, "record_relation_action_success") as record,
            patch.object(webverify.Moderate, "analyze_member_join_action", return_value={"valid": True, "requires_confirmation": False, "normalized": "kick test"}),
            patch.object(webverify.Moderate, "do_action_str", new=AsyncMock(return_value=(["skipped"], "skipped"))),
        ):
            result = await webverify.execute_relation_blacklist(guild, relation_id, settings, send_alert=False)
        record.assert_not_called()
        self.assertEqual(result["failed"], [])
        self.assertEqual(len(result["skipped"]), 1)

    async def test_alert_send_failure_does_not_undo_successful_action(self):
        member = SimpleNamespace(id=1, mention="<@1>")
        response = MagicMock(status=403, reason="Forbidden")
        channel = SimpleNamespace(send=AsyncMock(side_effect=discord.Forbidden(response, "forbidden")))
        guild = self.make_guild(member)
        guild.get_channel = lambda _channel_id: channel
        relation_id = "12345678-1234-5678-1234-567812345678"
        settings = {"enabled": True, "relation_ids": [relation_id], "action": "kick test", "channel_id": 99}
        with (
            patch.object(webverify, "get_relation_user_ids", return_value=[1]),
            patch.object(webverify, "relation_action_succeeded", return_value=False),
            patch.object(webverify, "record_relation_action_success") as record,
            patch.object(webverify.Moderate, "analyze_member_join_action", return_value={"valid": True, "requires_confirmation": False, "normalized": "kick test"}),
            patch.object(webverify.Moderate, "do_action_str", new=AsyncMock(return_value=(["done"], "success"))),
        ):
            result = await webverify.execute_relation_blacklist(guild, relation_id, settings)
        record.assert_called_once_with(10, relation_id, 1, "kick test")
        self.assertEqual(len(result["actioned"]), 1)
        channel.send.assert_awaited_once()

    async def test_preview_performs_no_actions(self):
        actionable = SimpleNamespace(id=1, mention="<@1>")
        completed = SimpleNamespace(id=2, mention="<@2>")
        skipped = SimpleNamespace(id=3, mention="<@3>")
        guild = self.make_guild(actionable, completed, skipped)
        executor = SimpleNamespace(id=50)
        relation_id = "12345678-1234-5678-1234-567812345678"
        settings = {"enabled": True, "relation_ids": [relation_id], "action": "kick test", "channel_id": 1}
        with (
            patch.object(webverify, "_resolve_relation_members", new=AsyncMock(return_value=([actionable, completed, skipped], [4]))),
            patch.object(webverify, "relation_action_succeeded", side_effect=lambda _g, _r, uid, _a: uid == 2),
            patch.object(webverify.Moderate, "analyze_member_join_action", return_value={"valid": True, "requires_confirmation": False, "normalized": "kick test"}),
            patch.object(webverify.Moderate, "check_member_hierarchy", side_effect=lambda _e, member, _b: (member.id != 3, "too high")),
            patch.object(webverify.Moderate, "do_action_str", new=AsyncMock()) as execute,
        ):
            preview = await webverify.collect_relation_blacklist_preview(guild, settings, executor)
        execute.assert_not_awaited()
        self.assertEqual(len(preview["actionable"]), 1)
        self.assertEqual(len(preview["completed"]), 1)
        self.assertEqual(len(preview["missing"]), 1)
        self.assertEqual(len(preview["skipped"]), 1)

    async def test_country_alert_failure_does_not_block_relation_actions(self):
        member = SimpleNamespace(id=1, mention="<@1>", get_role=lambda _role_id: None, send=AsyncMock())
        guild = SimpleNamespace(id=10, name="Guild")
        relation_id = "12345678-1234-5678-1234-567812345678"
        config = {
            "webverify_country_alert": {"enabled": True, "mode": "blacklist", "countries": ["TW"], "channel_id": 1},
            "relation_blacklist": {"enabled": True, "relation_ids": [relation_id], "action": "kick test", "channel_id": 2},
        }
        relation_execute = AsyncMock()
        with (
            patch.object(webverify, "_send_country_alert", new=AsyncMock(side_effect=RuntimeError("lookup failed"))),
            patch.object(webverify, "execute_relation_blacklist", new=relation_execute),
        ):
            await webverify.complete_successful_verification(guild, member, config, "198.51.100.1", relation_id)
        relation_execute.assert_awaited_once()
        member.send.assert_awaited_once()

    async def test_geolocation_wait_does_not_delay_relation_actions(self):
        member = SimpleNamespace(id=1, mention="<@1>", get_role=lambda _role_id: None, send=AsyncMock())
        guild = SimpleNamespace(id=10, name="Guild")
        relation_id = "12345678-1234-5678-1234-567812345678"
        config = {
            "webverify_country_alert": {"enabled": True, "mode": "blacklist", "countries": ["TW"], "channel_id": 1},
            "relation_blacklist": {"enabled": True, "relation_ids": [relation_id], "action": "kick test", "channel_id": 2},
        }
        country_started = asyncio.Event()
        relation_finished = asyncio.Event()

        async def slow_country(*_args, **_kwargs):
            country_started.set()
            await relation_finished.wait()

        async def relation_action(*_args, **_kwargs):
            self.assertTrue(country_started.is_set())
            relation_finished.set()

        with (
            patch.object(webverify, "_send_country_alert", new=slow_country),
            patch.object(webverify, "execute_relation_blacklist", new=relation_action),
        ):
            await webverify.complete_successful_verification(guild, member, config, "198.51.100.1", relation_id)
        member.send.assert_awaited_once()

    async def test_scan_confirmation_rejects_non_owner(self):
        view = webverify.RelationBlacklistScanView(owner_id=10, guild_id=20)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=11),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        self.assertFalse(await view.interaction_check(interaction))
        interaction.response.send_message.assert_awaited_once()


class WebVerifyCommandSchemaTests(unittest.TestCase):
    def test_relation_blacklist_group_serializes_all_subcommands(self):
        payload = webverify.bot.tree.get_command("webverify").to_dict(webverify.bot.tree)
        relation_group = next(option for option in payload["options"] if option["name"] == "relation-blacklist")
        self.assertEqual(relation_group["type"], 2)
        self.assertEqual(
            {option["name"] for option in relation_group["options"]},
            {"add", "remove", "list", "configure", "disable", "scan"},
        )


if __name__ == "__main__":
    unittest.main()
