import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import Moderate
import globalenv

_original_modules = list(globalenv.modules)
globalenv.modules[:] = ["Website"]
try:
    import GuildPanel
finally:
    globalenv.modules[:] = _original_modules

from AutoModerate import action_value_autocomplete


class ActionTimeParsingTests(unittest.TestCase):
    def test_time_parser_supports_compound_and_chinese_units(self):
        self.assertEqual(Moderate.timestr_to_seconds("1d12h"), 129600)
        self.assertEqual(Moderate.timestr_to_seconds("10分鐘"), 600)
        self.assertEqual(Moderate.timestr_to_seconds("2小時"), 7200)
        self.assertEqual(Moderate.timestr_to_seconds("300"), 300)
        self.assertEqual(Moderate.timestr_to_seconds("not-a-time"), 0)


class ActionAnalysisTests(unittest.TestCase):
    def test_bare_number_suggests_mute_minutes(self):
        analysis = Moderate.analyze_action_string("300")
        self.assertTrue(analysis["valid"])
        self.assertTrue(analysis["requires_confirmation"])
        self.assertEqual(analysis["normalized"], "mute 300m")
        self.assertIn("禁言 300 分鐘", analysis["confirmation"])
        self.assertIn("禁言 5 小時", analysis["preview"][0])

    def test_mute_without_unit_requires_confirmation(self):
        analysis = Moderate.analyze_action_string("mute 90 刷頻")
        self.assertTrue(analysis["requires_confirmation"])
        self.assertEqual(analysis["normalized"], "mute 90m 刷頻")

    def test_to_is_a_timeout_alias(self):
        analysis = Moderate.analyze_action_string("to 15m 刷頻")
        self.assertTrue(analysis["valid"])
        self.assertFalse(analysis["requires_confirmation"])
        self.assertIn("禁言 15 分鐘", analysis["preview"][0])
        self.assertIn("to", Moderate.BUILTIN_ACTIONS)

    def test_to_without_unit_uses_the_same_confirmation(self):
        analysis = Moderate.analyze_action_string("to 90 刷頻")
        self.assertTrue(analysis["requires_confirmation"])
        self.assertEqual(analysis["normalized"], "to 90m 刷頻")

    def test_explicit_multi_action_returns_preview(self):
        analysis = Moderate.analyze_action_string(
            "mute 30m 刷頻, delete {user} 請勿刷頻, smm"
        )
        self.assertTrue(analysis["valid"])
        self.assertFalse(analysis["requires_confirmation"])
        self.assertEqual(len(analysis["preview"]), 3)
        self.assertIn("禁言 30 分鐘", analysis["preview"][0])

    def test_unknown_action_is_rejected(self):
        analysis = Moderate.analyze_action_string("explode 10m")
        self.assertFalse(analysis["valid"])
        self.assertIn("不支援的動作", analysis["error"])

    def test_ban_with_reason_but_missing_delete_window_is_rejected(self):
        analysis = Moderate.analyze_action_string("ban 7d 違規")
        self.assertFalse(analysis["valid"])
        self.assertIn("刪除訊息時長", analysis["error"])
        self.assertIn("ban 7d 0 違規", analysis["error"])

    def test_timeout_over_discord_limit_is_rejected(self):
        analysis = Moderate.analyze_action_string("mute 29d")
        self.assertFalse(analysis["valid"])
        self.assertIn("28 天", analysis["error"])

    def test_custom_action_alias_is_expanded_for_preview(self):
        with patch.object(
            Moderate,
            "_load_custom_action_strings",
            return_value={"spam": "to 10m 刷頻, delete"},
        ):
            analysis = Moderate.analyze_action_string("spam", guild_id=1)
        self.assertTrue(analysis["valid"])
        self.assertEqual(len(analysis["preview"]), 2)
        self.assertEqual(analysis["normalized"], "spam")

    def test_saved_preview_no_longer_repeats_confirmation_question(self):
        analysis = Moderate.analyze_action_string("300")
        embed = Moderate.build_action_preview_embed(analysis, saved=True)
        self.assertEqual(embed.description, "設定已儲存。")


class ActionAutocompleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_numeric_action_autocomplete_suggests_mute_minutes(self):
        interaction = SimpleNamespace(namespace=SimpleNamespace(setting="anti_spam-action"))
        choices = await action_value_autocomplete(interaction, "300")
        self.assertGreater(len(choices), 0)
        self.assertEqual(choices[0].value, "mute 300m")
        self.assertIn("300 分鐘", choices[0].name)

    async def test_non_action_setting_has_no_action_suggestions(self):
        interaction = SimpleNamespace(namespace=SimpleNamespace(setting="anti_spam-time_window"))
        self.assertEqual(await action_value_autocomplete(interaction, "300"), [])

    async def test_shared_autocomplete_suggests_normalized_timeout(self):
        choices = await Moderate.action_input_autocomplete(None, "300")
        self.assertEqual(choices[0].value, "mute 300m")

    async def test_to_alias_is_discoverable(self):
        choices = await Moderate.action_input_autocomplete(None, "to")
        self.assertIn("to 10m 違規", {choice.value for choice in choices})


class ActionExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_to_alias_executes_as_timeout(self):
        logs = await Moderate.do_action_str("to 15m 刷頻")
        self.assertIn("持續秒數: 900秒", logs[0])

    async def test_custom_action_can_expand_to_timeout_alias(self):
        with patch.object(
            Moderate,
            "_load_custom_action_strings",
            return_value={"spam": "to 10m 刷頻, delete"},
        ):
            logs = await Moderate.do_action_str("spam")
        self.assertIn("持續秒數: 600秒", logs[0])
        self.assertEqual(logs[1], "刪除訊息")


class ActionConfirmationViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_is_bound_to_original_user(self):
        analysis = Moderate.analyze_action_string("300")
        callback = AsyncMock()
        view = Moderate.ActionConfirmationView(10, analysis, callback)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=11),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        self.assertFalse(await view.interaction_check(interaction))
        interaction.response.send_message.assert_awaited_once()
        callback.assert_not_awaited()


class GuildPanelActionValidationTests(unittest.TestCase):
    def _post_settings(self, action):
        schema = {
            "AutoModerate": {
                "settings": [
                    {"database_key": "automod", "type": "automod_config"},
                ],
            },
        }
        route = GuildPanel.api_set_settings
        while hasattr(route, "__wrapped__"):
            route = route.__wrapped__

        payload = {
            "module": "AutoModerate",
            "key": "automod",
            "value": {"anti_spam": {"enabled": True, "action": action}},
        }
        with GuildPanel.app.test_request_context(json=payload):
            with patch.dict(GuildPanel.settings, schema, clear=True):
                with patch.object(GuildPanel, "set_server_config") as save:
                    result = route("1")
        return result, save

    def test_numeric_shorthand_is_rejected_until_confirmed(self):
        (response, status), save = self._post_settings("300")
        self.assertEqual(status, 400)
        self.assertIn("禁言 300 分鐘", response.get_json()["error"])
        save.assert_not_called()

    def test_invalid_action_is_not_saved(self):
        (response, status), save = self._post_settings("explode 10m")
        self.assertEqual(status, 400)
        self.assertIn("動作指令無效", response.get_json()["error"])
        save.assert_not_called()

    def test_explicit_action_is_saved(self):
        response, save = self._post_settings("mute 10m 違規")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        save.assert_called_once()


class GuildPanelCompoundSettingsTests(unittest.TestCase):
    def test_registry_includes_fixlink_and_antibeast(self):
        with (
            patch.object(GuildPanel, "modules", ["FixLink", "AntiBeast"]),
            patch.dict(GuildPanel.settings, {}, clear=True),
        ):
            GuildPanel._register_all()
            self.assertEqual(
                GuildPanel.settings["FixLink"]["settings"][0]["type"],
                "fixlink_config",
            )
            self.assertEqual(
                GuildPanel.settings["AntiBeast"]["settings"][0]["type"],
                "antibeast_config",
            )
            self.assertTrue(
                callable(GuildPanel.settings["AntiBeast"]["settings"][0]["trigger"])
            )

    def test_fixlink_coercion_uses_canonical_custom_platform_validation(self):
        value = {
            "enabled": True,
            "remove_tracker": True,
            "webhook_mode": False,
            "webhook_only_with_tracker": False,
            "disabled_platforms": ["Twitter"],
            "preferred_fixers": {"Threads": "FixEmbed"},
            "custom_platforms": [{
                "name": "Example",
                "origins": ["example.com"],
                "path_prefixes": ["/post/"],
                "keep_query_keys": ["id"],
                "fixer": {
                    "name": "ExampleFix",
                    "endpoint": "https://fix.example.com/embed",
                    "source_param": "url",
                    "static_query": {"v": "1"},
                },
            }],
        }
        result = GuildPanel._coerce_fixlink_config(value)
        self.assertTrue(result["enabled"])
        self.assertEqual(result["preferred_fixers"]["Threads"], "FixEmbed")
        self.assertEqual(result["custom_platforms"][0]["origins"], ["example.com"])

    def test_fixlink_rejects_builtin_origin_in_custom_platform(self):
        value = {
            "custom_platforms": [{
                "name": "Fake Threads",
                "origins": ["threads.com"],
                "path_prefixes": ["/post/"],
                "fixer": {
                    "name": "ExampleFix",
                    "endpoint": "https://fix.example.com/embed",
                    "source_param": "url",
                },
            }],
        }
        with self.assertRaisesRegex(ValueError, "內建平台"):
            GuildPanel._coerce_fixlink_config(value)

    def test_fixlink_rejects_more_than_ten_custom_platforms(self):
        value = {"custom_platforms": [{} for _ in range(11)]}
        with self.assertRaisesRegex(ValueError, "最多 10"):
            GuildPanel._coerce_fixlink_config(value)

    def test_antibeast_coercion_preserves_internal_state_and_normalizes_action(self):
        current = {"rule_id": 123, "everyone_mention_before": True}
        value = {
            "enabled": True,
            "bypass_roles": ["10", 10, "20"],
            "kick": {
                "enabled": True,
                "threshold": 3,
                "time_window": 30,
                "action": "to 10m 刷頻",
                "only_everyone_here": True,
            },
        }
        with patch.object(GuildPanel, "get_server_config", return_value=current):
            result = GuildPanel._coerce_antibeast_config(value, guild_id=1)
        self.assertEqual(result["bypass_roles"], [10, 20])
        self.assertEqual(result["rule_id"], 123)
        self.assertTrue(result["everyone_mention_before"])
        self.assertEqual(result["kick"]["action"], "to 10m 刷頻")

    def test_antibeast_numeric_shorthand_requires_confirmation(self):
        value = {
            "enabled": False,
            "bypass_roles": [],
            "kick": {
                "enabled": True,
                "threshold": 2,
                "time_window": 10,
                "action": "300",
            },
        }
        with patch.object(GuildPanel, "get_server_config", return_value={}):
            with self.assertRaisesRegex(ValueError, "禁言 300 分鐘"):
                GuildPanel._coerce_antibeast_config(value, guild_id=1)


class GuildPanelAntiBeastTriggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_uninitialized_config_does_not_change_guild_state(self):
        cog = SimpleNamespace(
            _get_config=lambda guild_id: {
                "enabled": False,
                "rule_id": None,
                "everyone_mention_before": None,
            },
            _apply_state=AsyncMock(),
        )
        guild = SimpleNamespace(id=1)
        with (
            patch.object(GuildPanel.bot, "get_cog", return_value=cog),
            patch.object(GuildPanel.bot, "get_guild", return_value=guild),
            patch.object(GuildPanel, "set_server_config") as save,
        ):
            await GuildPanel._apply_antibeast_panel_config(1, {})
        cog._apply_state.assert_not_awaited()
        save.assert_called_once()

    async def test_enabled_config_applies_runtime_state(self):
        config = {
            "enabled": True,
            "rule_id": None,
            "everyone_mention_before": None,
        }
        cog = SimpleNamespace(
            _get_config=lambda guild_id: config,
            _apply_state=AsyncMock(),
        )
        guild = SimpleNamespace(id=1)
        with (
            patch.object(GuildPanel.bot, "get_cog", return_value=cog),
            patch.object(GuildPanel.bot, "get_guild", return_value=guild),
            patch.object(GuildPanel, "set_server_config"),
        ):
            await GuildPanel._apply_antibeast_panel_config(1, config)
        cog._apply_state.assert_awaited_once_with(
            guild,
            config,
            enabled=True,
            reason="AntiBeast updated from server settings panel",
        )


if __name__ == "__main__":
    unittest.main()
