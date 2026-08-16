import sys
import unittest
from pathlib import Path
from unittest.mock import patch

DISCORD_DIR = Path(__file__).resolve().parents[1]
if str(DISCORD_DIR) not in sys.path:
    sys.path.insert(0, str(DISCORD_DIR))

import i18n
import database
import globalenv
from tests.i18n_base import FakeDB, clear_i18n_caches


class LocalizedRegistryTests(unittest.TestCase):
    def setUp(self):
        i18n.reload_catalogs()
        self._saved = dict(globalenv.panel_settings)
        globalenv.panel_settings.clear()
        globalenv.register_panel_settings("ReportSystem", "檢舉系統", [
            {"display": "檢舉通知頻道", "description": "檢舉訊息將發送到此頻道",
             "database_key": "REPORT_CHANNEL_ID", "type": "channel", "default": None},
            {"display": "未知設定", "database_key": "NOT_IN_CATALOG",
             "type": "string", "default": None},
        ], description="管理檢舉相關設定", icon="📋")

        def restore():
            globalenv.panel_settings.clear()
            globalenv.panel_settings.update(self._saved)
        self.addCleanup(restore)

    def test_english_locale_localizes_registry(self):
        with i18n.use_locale("en"):
            data = globalenv.localized_panel_settings()["ReportSystem"]
        self.assertEqual(data["display_name"], "Report System")
        self.assertEqual(data["settings"][0]["display"], "Report channel")
        self.assertEqual(data["settings"][0]["description"],
                         "Reports are sent to this channel")

    def test_zh_locale_keeps_original(self):
        with i18n.use_locale("zh-TW"):
            data = globalenv.localized_panel_settings()["ReportSystem"]
        self.assertEqual(data["display_name"], "檢舉系統")
        self.assertEqual(data["settings"][0]["display"], "檢舉通知頻道")

    def test_missing_catalog_key_falls_back_to_inline(self):
        with i18n.use_locale("en"):
            data = globalenv.localized_panel_settings()["ReportSystem"]
        self.assertEqual(data["settings"][1]["display"], "未知設定")


class ServerConfigI18nTests(unittest.TestCase):
    def setUp(self):
        i18n.reload_catalogs()
        self.fake_db = FakeDB()
        for target in (i18n, globalenv):
            patcher = patch.object(target, "db", self.fake_db)
            patcher.start()
            self.addCleanup(patcher.stop)
        clear_i18n_caches()
        self.addCleanup(clear_i18n_caches)

    def test_unset_returns_localized_default(self):
        with i18n.use_locale("zh-TW"):
            value = globalenv.get_server_config_i18n(
                1, "REPORTED_MESSAGE", "panel.reportsystem.reported_message.default")
        self.assertEqual(value, "感謝您的檢舉，我們會盡快處理您的檢舉。")
        with i18n.use_locale("en"):
            value = globalenv.get_server_config_i18n(
                1, "REPORTED_MESSAGE", "panel.reportsystem.reported_message.default")
        self.assertEqual(
            value, "Thank you for your report. We will handle it as soon as possible.")

    def test_stored_value_returned_verbatim(self):
        self.fake_db.set_server_config(1, "REPORTED_MESSAGE", "自訂回覆")
        with i18n.use_locale("en"):
            value = globalenv.get_server_config_i18n(
                1, "REPORTED_MESSAGE", "panel.reportsystem.reported_message.default")
        self.assertEqual(value, "自訂回覆")  # guild 資料永不翻譯

    def test_empty_string_treated_as_unset(self):
        self.fake_db.set_server_config(1, "REPORTED_MESSAGE", "  ")
        with i18n.use_locale("en"):
            value = globalenv.get_server_config_i18n(
                1, "REPORTED_MESSAGE", "panel.reportsystem.reported_message.default")
        self.assertTrue(value.startswith("Thank you"))

    def test_explicit_locale_overrides_scope(self):
        with i18n.use_locale("en"):
            value = globalenv.get_server_config_i18n(
                1, "ticket_welcome_message",
                "panel.ticket.ticket_welcome_message.default", locale="zh-TW")
        self.assertIn("你好", value)

    def test_placeholders_survive_default_rendering(self):
        # {user} 佔位符必須原樣保留給下游 .format() 用
        with i18n.use_locale("en"):
            value = globalenv.get_server_config_i18n(
                1, "dynamic_voice_channel_name",
                "panel.dynamicvoice.dynamic_voice_channel_name.default")
        self.assertIn("{user}", value)


class NoChineseDefaultInDbSchemaTests(unittest.TestCase):
    def test_default_server_config_has_no_cjk(self):
        import re
        cjk = re.compile(r"[一-鿿]")
        for key, value in database.DEFAULT_SERVER_CONFIG.items():
            if isinstance(value, str):
                self.assertIsNone(cjk.search(value),
                                  f"DEFAULT_SERVER_CONFIG[{key}] contains Chinese: {value!r}")


if __name__ == "__main__":
    unittest.main()
