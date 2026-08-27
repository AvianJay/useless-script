import sys
import unittest
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import gettingstarted as gs
import Moderate
from JoinNotify import find_bot_inviter, get_join_prompt_recipient


class FakePermissions:
    def __init__(self, *, view_channel=True, send_messages=True, embed_links=True):
        self.view_channel = view_channel
        self.send_messages = send_messages
        self.embed_links = embed_links


class FakeChannel:
    def __init__(self, channel_id, name, permissions):
        self.id = channel_id
        self.name = name
        self.position = channel_id
        self._permissions = permissions
        self.send = AsyncMock()

    def permissions_for(self, member):
        return self._permissions[member]


class GettingStartedHelpersTests(unittest.TestCase):
    def test_paginate_clamps_page(self):
        values = list(range(60))
        current, page, total = gs.paginate(values, 99)
        self.assertEqual(page, 2)
        self.assertEqual(total, 3)
        self.assertEqual(current, list(range(50, 60)))

    def test_coerce_scalar_values_and_bounds(self):
        self.assertIsNone(gs.coerce_scalar_setting_value({"type": "string"}, "  "))
        self.assertEqual(gs.coerce_scalar_setting_value({"type": "string"}, " value "), "value")
        self.assertEqual(gs.coerce_scalar_setting_value({"type": "number", "min": 1}, "4"), 4)
        self.assertEqual(gs.coerce_scalar_setting_value({"type": "float", "max": 2}, "1.5"), 1.5)
        with self.assertRaisesRegex(ValueError, "不可小於"):
            gs.coerce_scalar_setting_value({"type": "number", "min": 2}, "1")
        with self.assertRaisesRegex(ValueError, "有效的數字"):
            gs.coerce_scalar_setting_value({"type": "float"}, "not-a-number")

    def test_find_setup_channel_prefers_accessible_system_channel(self):
        bot_member = object()
        recipient = object()
        system = FakeChannel(
            1,
            "system",
            {
                bot_member: FakePermissions(),
                recipient: FakePermissions(view_channel=True),
            },
        )
        fallback = FakeChannel(
            2,
            "general",
            {
                bot_member: FakePermissions(),
                recipient: FakePermissions(view_channel=True),
            },
        )
        guild = SimpleNamespace(me=bot_member, system_channel=system, text_channels=[fallback, system])
        self.assertIs(gs.find_setup_channel(guild, recipient), system)

    def test_find_setup_channel_skips_inaccessible_channel(self):
        bot_member = object()
        recipient = object()
        system = FakeChannel(
            1,
            "system",
            {
                bot_member: FakePermissions(send_messages=False),
                recipient: FakePermissions(),
            },
        )
        fallback = FakeChannel(
            2,
            "general",
            {
                bot_member: FakePermissions(),
                recipient: FakePermissions(),
            },
        )
        guild = SimpleNamespace(me=bot_member, system_channel=system, text_channels=[system, fallback])
        self.assertIs(gs.find_setup_channel(guild, recipient), fallback)

    def test_available_modules_excludes_failed_and_disabled(self):
        with (
            patch.object(gs, "modules", ["Enabled", "Failed"]),
            patch.object(gs, "failed_modules", ["Failed"]),
            patch.object(
                gs,
                "panel_settings",
                {
                    "Enabled": {"display_name": "Enabled", "settings": []},
                    "Failed": {"display_name": "Failed", "settings": []},
                    "Disabled": {"display_name": "Disabled", "settings": []},
                },
            ),
        ):
            self.assertEqual([name for name, _ in gs.available_panel_modules()], ["Enabled"])

    def test_persistent_launcher_has_stable_custom_id(self):
        view = gs.GettingStartedLauncherView()
        self.assertTrue(view.is_persistent())
        self.assertEqual(view.children[0].custom_id, "getting_started_open_server_setup")

    def test_automod_action_editor_offers_common_presets(self):
        parent = SimpleNamespace()
        with patch.object(gs, "Moderate", Moderate):
            select = gs.AutoModerateActionPresetSelect(parent)
        values = {option.value for option in select.options}
        self.assertIn("mute 10m 違規", values)
        self.assertIn("ban 0 0 違規", values)


class JoinRecipientTests(unittest.IsolatedAsyncioTestCase):
    async def test_find_bot_inviter_uses_matching_audit_entry(self):
        expected = SimpleNamespace(id=10)
        entries = [
            SimpleNamespace(target=SimpleNamespace(id=1), user=SimpleNamespace(id=11)),
            SimpleNamespace(target=SimpleNamespace(id=99), user=expected),
        ]

        async def audit_entries():
            for entry in entries:
                yield entry

        guild = SimpleNamespace(audit_logs=lambda **kwargs: audit_entries())
        self.assertIs(await find_bot_inviter(guild, 99), expected)

    async def test_join_recipient_falls_back_to_owner(self):
        owner = SimpleNamespace(id=20)

        async def no_entries():
            if False:
                yield None

        guild = SimpleNamespace(owner=owner, audit_logs=lambda **kwargs: no_entries())
        self.assertIs(await get_join_prompt_recipient(guild, 99), owner)

    async def test_on_ready_registers_persistent_launcher_once(self):
        client = SimpleNamespace(add_view=MagicMock())
        cog = gs.GettingStarted(client)

        await cog.on_ready()
        await cog.on_ready()

        client.add_view.assert_called_once()
        registered = client.add_view.call_args.args[0]
        self.assertTrue(registered.is_persistent())

    async def test_guild_join_resolves_then_waits_then_sends(self):
        order = []
        recipient = SimpleNamespace(id=10, mention="<@10>")
        owner = recipient
        channel = SimpleNamespace(name="general", send=AsyncMock())
        channel.send.side_effect = lambda *args, **kwargs: order.append("send")
        guild = SimpleNamespace(
            id=5,
            name="Test Guild",
            icon=None,
            owner=owner,
            get_member=lambda user_id: recipient,
        )
        client = SimpleNamespace(user=SimpleNamespace(id=99), add_view=MagicMock())
        cog = gs.GettingStarted(client)

        async def resolve(*args, **kwargs):
            order.append("resolve")
            return recipient

        async def sleep(delay):
            self.assertEqual(delay, 1)
            order.append("sleep")

        with (
            patch.object(gs, "get_join_prompt_recipient", side_effect=resolve),
            patch.object(gs.asyncio, "sleep", side_effect=sleep),
            patch.object(gs, "find_setup_channel", return_value=channel),
            patch.object(gs, "log"),
        ):
            await cog.on_guild_join(guild)

        self.assertEqual(order, ["resolve", "sleep", "send"])
        channel.send.assert_awaited_once()

    async def test_guild_join_dm_fallback(self):
        recipient = SimpleNamespace(id=10, mention="<@10>", send=AsyncMock())
        guild = SimpleNamespace(
            id=5,
            name="Test Guild",
            icon=None,
            owner=recipient,
            get_member=lambda user_id: recipient,
        )
        client = SimpleNamespace(user=SimpleNamespace(id=99), add_view=MagicMock())
        cog = gs.GettingStarted(client)
        with (
            patch.object(gs, "get_join_prompt_recipient", new=AsyncMock(return_value=recipient)),
            patch.object(gs.asyncio, "sleep", new=AsyncMock()),
            patch.object(gs, "find_setup_channel", return_value=None),
            patch.object(gs, "get_command_mention", new=AsyncMock(return_value="</gettingstarted:1>")),
            patch.object(gs, "log"),
        ):
            await cog.on_guild_join(guild)
        recipient.send.assert_awaited_once()


class SettingPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_registered_setting_runs_sync_trigger(self):
        trigger = MagicMock()
        setting = {"database_key": "key", "trigger": trigger}
        with patch.object(gs, "set_server_config", return_value=True) as setter:
            warning = await gs.apply_registered_setting(1, "Module", setting, "value")
        self.assertIsNone(warning)
        setter.assert_called_once_with(1, "key", "value")
        trigger.assert_called_once_with(1, "value")

    async def test_apply_registered_setting_runs_async_trigger(self):
        trigger = AsyncMock()
        setting = {"database_key": "key", "trigger": trigger}
        with patch.object(gs, "set_server_config", return_value=True):
            warning = await gs.apply_registered_setting(1, "Module", setting, 3)
        self.assertIsNone(warning)
        trigger.assert_awaited_once_with(1, 3)

    async def test_trigger_failure_keeps_saved_value_and_returns_warning(self):
        def trigger(*args):
            raise RuntimeError("failed")

        setting = {"database_key": "key", "trigger": trigger}
        with (
            patch.object(gs, "set_server_config", return_value=True),
            patch.object(gs, "log"),
        ):
            warning = await gs.apply_registered_setting(1, "Module", setting, True)
        self.assertIn("設定已儲存", warning)

    async def test_apply_registered_setting_can_pass_previous_value(self):
        trigger = AsyncMock()
        setting = {
            "database_key": "stickymessage",
            "trigger": trigger,
            "trigger_with_previous": True,
            "default": {},
        }
        old = {"entries": [{"channel_id": 1, "content": "old"}]}
        new = {"entries": [{"channel_id": 1, "content": "new"}]}
        with (
            patch.object(gs, "get_server_config", return_value=old),
            patch.object(gs, "set_server_config", return_value=True),
        ):
            warning = await gs.apply_registered_setting(1, "StickyMessage", setting, new)
        self.assertIsNone(warning)
        trigger.assert_awaited_once_with(1, new, old)

    async def test_member_join_action_is_validated_before_save(self):
        setting = {
            "database_key": "hacked_join_detection_action",
            "type": "string",
            "action_context": "member_join",
        }
        with patch.object(gs, "set_server_config", return_value=True) as setter:
            with self.assertRaisesRegex(ValueError, "`warn`"):
                await gs.apply_registered_setting(1, "HackedDetector", setting, "warn hi")
        setter.assert_not_called()

    async def test_member_join_action_blank_restores_default(self):
        setting = {
            "database_key": "hacked_join_detection_action",
            "type": "string",
            "action_context": "member_join",
        }
        with patch.object(gs, "set_server_config", return_value=True) as setter:
            await gs.apply_registered_setting(1, "HackedDetector", setting, "  ")
        setter.assert_called_once_with(1, "hacked_join_detection_action", None)

    async def test_member_join_action_explicit_value_is_saved(self):
        setting = {
            "database_key": "hacked_join_detection_action",
            "type": "string",
            "action_context": "member_join",
        }
        with patch.object(gs, "set_server_config", return_value=True) as setter:
            await gs.apply_registered_setting(1, "HackedDetector", setting, "mute 2h reason")
        setter.assert_called_once_with(1, "hacked_join_detection_action", "mute 2h reason")

    def test_member_join_presets_exclude_message_dependent_actions(self):
        parent = SimpleNamespace(
            session=SimpleNamespace(guild=SimpleNamespace(id=1)),
        )
        select = gs.RegisteredActionPresetSelect(parent)
        values = {option.value for option in select.options}
        self.assertIn("mute 10m 違規", values)
        self.assertNotIn("delete", values)
        self.assertFalse(any(value.startswith("warn") for value in values))


class FakeAutoReplyCog:
    def __init__(self):
        self.skip_rule = None

    def _save_new_autoreply_rule(self, guild_id, rule):
        return 1, 50

    def _find_duplicate_triggers_in_list(self, triggers):
        return ["duplicate"] if triggers == ["duplicate", "duplicate"] else []

    def _find_conflicting_autoreply_triggers(self, rules, triggers, skip_rule=None):
        self.skip_rule = skip_rule
        return ["taken"] if triggers == ["taken"] else []

    def _format_autoreply_trigger_conflict_message(self, triggers, *, existing):
        return "existing conflict" if existing else "duplicate conflict"

    def _get_autoreply_limit(self, guild_id):
        return 50


class AutoReplyPersistenceTests(unittest.TestCase):
    def test_edit_replaces_rule_and_skips_original_for_conflict_check(self):
        original = {"trigger": ["old"], "response": ["old"]}
        replacement = {"trigger": ["new"], "response": ["new"]}
        cog = FakeAutoReplyCog()
        with (
            patch.object(gs, "get_server_config", return_value=[original]),
            patch.object(gs, "set_server_config", return_value=True) as setter,
        ):
            result = gs.save_autoreply_rule(cog, 1, replacement, 0)
        self.assertEqual(result, (1, 50))
        self.assertIs(cog.skip_rule, original)
        setter.assert_called_once_with(1, "autoreplies", [replacement])

    def test_edit_rejects_conflicting_trigger(self):
        original = {"trigger": ["old"], "response": ["old"]}
        cog = FakeAutoReplyCog()
        with patch.object(gs, "get_server_config", return_value=[original]):
            with self.assertRaisesRegex(ValueError, "existing conflict"):
                gs.save_autoreply_rule(cog, 1, {"trigger": ["taken"]}, 0)


class ComplexSchemaTests(unittest.TestCase):
    def test_automod_schema_covers_all_runtime_features(self):
        expected = {
            "scamtrap",
            "escape_punish",
            "too_many_h1",
            "too_many_emojis",
            "anti_invite_link",
            "anti_uispam",
            "anti_raid",
            "anti_spam",
            "automod_detect",
            "flagged_user",
        }
        automod_feature_map = gs.automod_feature_map()
        self.assertEqual(set(automod_feature_map), expected)
        automod_detect_fields = {
            field["key"] for field in automod_feature_map["automod_detect"]["fields"]
        }
        self.assertEqual(
            automod_detect_fields,
            {"log_channel", "action", "filter_rule", "filter_action_type"},
        )
        flagged_user_fields = {
            field["key"] for field in automod_feature_map["flagged_user"]["fields"]
        }
        self.assertEqual(
            flagged_user_fields,
            {"log_channel", "action", "action_source", "local_match_mode"},
        )

    def test_flagged_user_quick_settings_inherit_legacy_channel(self):
        def get_config(_guild_id, key, default=None):
            if key == "automod":
                return {}
            if key == "flagged_user_onjoin_channel":
                return 555
            return default

        with patch.object(gs, "get_server_config", side_effect=get_config):
            data = gs.get_automod_feature_data(1, "flagged_user")
        self.assertTrue(data["enabled"])
        self.assertEqual(data["log_channel"], "555")
        self.assertEqual(data["action"], "")
        self.assertEqual(data["action_source"], "both")
        self.assertEqual(data["local_match_mode"], "active")

    def test_webverify_validation_requires_channels_and_countries(self):
        guild = MagicMock()
        guild.id = 1
        guild.name = "Guild"
        guild.get_role.return_value = None
        session = gs.GettingStartedSession(guild, 10)
        draft = gs.default_webverify_config()
        draft["notify"]["type"] = "both"
        view = gs.WebVerifySetupView(session, "ServerWebVerify", draft=draft, step=7)
        self.assertIn("通知頻道", view.validate())

        view.draft["notify"]["channel_id"] = 123
        view.draft["webverify_country_alert"]["enabled"] = True
        self.assertIn("警示頻道", view.validate())

        view.draft["webverify_country_alert"]["channel_id"] = 456
        self.assertIn("地區代碼", view.validate())

        view.draft["webverify_country_alert"]["countries"] = ["TW"]
        self.assertIsNone(view.validate())

    def test_webverify_relation_blacklist_validation_requires_ids_channel_and_action(self):
        guild = MagicMock()
        guild.id = 1
        guild.name = "Guild"
        guild.me = object()
        permissions = FakePermissions()
        channel = SimpleNamespace(mention="#alerts", permissions_for=lambda _member: permissions)
        guild.get_channel.side_effect = lambda channel_id: channel if channel_id == 789 else None
        session = gs.GettingStartedSession(guild, 10)
        draft = gs.default_webverify_config()
        draft["relation_blacklist"]["enabled"] = True
        view = gs.WebVerifySetupView(session, "ServerWebVerify", draft=draft, step=6)
        relation_id = "12345678-1234-5678-1234-567812345678"

        with (
            patch.object(gs, "normalize_webverify_relation_ids", side_effect=lambda values, **_kwargs: list(values)),
            patch.object(gs, "Moderate", Moderate),
        ):
            self.assertIn("關聯 ID", view.validate())
            view.draft["relation_blacklist"]["relation_ids"] = [relation_id]
            self.assertIn("警報頻道", view.validate())
            view.draft["relation_blacklist"]["channel_id"] = 789
            self.assertIn("處置指令", view.validate())
            view.draft["relation_blacklist"]["action"] = "warn hi"
            self.assertIn("`warn`", view.validate())
            view.draft["relation_blacklist"]["action"] = "kick test"
            self.assertIsNone(view.validate())

    def test_webverify_loader_migrates_country_alias_and_relation_defaults(self):
        relation_id = "12345678-1234-5678-1234-567812345678"
        stored = {
            "webverify_country_alert": {
                "enabled": True,
                "mode": "blocklist",
                "countries": ["tw", "TW", "bad"],
                "channel_id": 123,
            },
            "relation_blacklist": {
                "enabled": False,
                "relation_ids": [relation_id, relation_id.upper()],
                "action": " kick test ",
                "channel_id": 456,
            },
        }
        with patch.object(gs, "get_server_config", return_value=stored):
            loaded = gs.load_webverify_config(1)
        self.assertEqual(loaded["webverify_country_alert"]["mode"], "blacklist")
        self.assertEqual(loaded["webverify_country_alert"]["countries"], ["TW"])
        self.assertEqual(loaded["relation_blacklist"]["relation_ids"], [relation_id])
        self.assertEqual(loaded["relation_blacklist"]["action"], "kick test")

    def test_webverify_quick_setup_has_seven_steps(self):
        guild = MagicMock()
        guild.id = 1
        guild.name = "Guild"
        guild.get_role.return_value = None
        session = gs.GettingStartedSession(guild, 10)
        view = gs.WebVerifySetupView(session, "ServerWebVerify", draft=gs.default_webverify_config(), step=99)
        self.assertEqual(view.step, 7)
        self.assertIn("7/7", view.build_embed().title)

    def test_fixlink_gettingstarted_view_reuses_native_editor_and_adds_back(self):
        import FixLink

        guild = MagicMock()
        guild.id = 1
        guild.name = "Guild"
        session = gs.GettingStartedSession(guild, 10)
        interaction = SimpleNamespace(
            guild_id=1,
            user=SimpleNamespace(id=10),
        )
        cog = SimpleNamespace(
            get_config=lambda guild_id: FixLink.normalize_fixlink_config({}),
            save_config=MagicMock(return_value=True),
        )
        with (
            patch.object(gs, "FixLinkModule", FixLink),
            patch.object(gs.bot, "get_cog", return_value=cog),
        ):
            view = gs.build_gettingstarted_fixlink_view(session, "FixLink", interaction)
        self.assertIsInstance(view, FixLink.FixLinkSettingsView)
        self.assertIn("返回設定中心", {getattr(item, "label", None) for item in view.children})

    def test_antibeast_gettingstarted_view_covers_runtime_settings(self):
        config = {
            "enabled": True,
            "bypass_roles": [10],
            "rule_id": 99,
            "everyone_mention_before": False,
            "kick": {
                "enabled": True,
                "threshold": 3,
                "time_window": 30,
                "action": "to 10m 刷頻",
                "only_everyone_here": True,
            },
        }
        cog = SimpleNamespace(
            _get_config=lambda guild_id: copy.deepcopy(config),
            _format_action_scope=lambda kick: "只處理 @everyone / @here",
            _normalize_kick_config=lambda kick: kick,
        )
        role = SimpleNamespace(id=10, mention="<@&10>")
        guild = MagicMock()
        guild.id = 1
        guild.name = "Guild"
        guild.get_role.side_effect = lambda role_id: role if role_id == 10 else None
        session = gs.GettingStartedSession(guild, 10)
        with patch.object(gs, "get_antibeast_cog", return_value=cog):
            view = gs.AntiBeastManagerView(session, "AntiBeast")
            embed = view.build_embed()
            action_view = gs.AntiBeastActionView(session, "AntiBeast")
        self.assertIn("AntiBeast", embed.title)
        self.assertIn("to 10m", embed.fields[0].value)
        labels = {getattr(item, "label", None) for item in action_view.children}
        self.assertIn("編輯門檻與動作", labels)
        self.assertIn("僅 everyone/here：開", labels)

    def test_stickymessage_gettingstarted_uses_dedicated_manager(self):
        setting = {
            "display": "置底訊息設定",
            "database_key": "stickymessage",
            "type": "stickymessage_config",
            "default": {},
        }
        guild = MagicMock()
        guild.id = 1
        guild.name = "Guild"
        guild.get_channel.return_value = SimpleNamespace(name="general")
        session = gs.GettingStartedSession(guild, 10)
        with (
            patch.object(gs, "StickyMessageModule", SimpleNamespace(
                CONFIG_KEY="stickymessage",
                DEFAULT_CONFIG={},
                normalize_config=lambda value: {
                    "quiet_seconds": 10,
                    "min_interval_seconds": 30,
                    "entries": [],
                },
                get_stickymessage_limit=lambda guild_id: 5,
            )),
            patch.object(gs, "get_server_config", return_value={}),
            patch.dict(gs.panel_settings, {
                "StickyMessage": {
                    "display_name": "置底訊息",
                    "settings": [setting],
                },
            }, clear=True),
        ):
            view = gs.StickyMessageManagerView(session, "StickyMessage")
        self.assertIn("置底訊息", view.build_embed().title)
        self.assertIn("新增", {getattr(item, "label", None) for item in view.children})


if __name__ == "__main__":
    unittest.main()
