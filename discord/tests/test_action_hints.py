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


if __name__ == "__main__":
    unittest.main()
