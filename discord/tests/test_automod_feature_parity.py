"""自動管理功能清單的跨副本一致性。

功能清單被手動維護在四個地方，彼此沒有任何機制保證同步：

  1. AutoModerate.all_settings          （斜線指令的設定項）
  2. GuildPanel.AUTOMOD_FEATURE_IDS     （網頁 API 的白名單）
  3. gettingstarted.automod_feature_schemas()（實際的設定精靈）
  4. static/js/panel.js 的 AUTOMOD_FEATURES  （網頁前端）

漏掉第 2 項最危險：它同時是 _serialize 與 _coerce 的白名單，
沒列到的功能會在網頁面板每次儲存時被靜默丟掉、GET API 也永遠不回傳。
"""
import re
import sys
import unittest
from pathlib import Path

DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import globalenv  # noqa: E402

_original_modules = list(globalenv.modules)
globalenv.modules[:] = ["Website"]
try:
    import GuildPanel  # noqa: E402
finally:
    globalenv.modules[:] = _original_modules

import AutoModerate  # noqa: E402
import gettingstarted as gs  # noqa: E402

PANEL_JS = DISCORD_DIR / "static" / "js" / "panel.js"
LOCALES = ("zh-TW", "en", "ja")


def _panel_js_automod_block() -> str:
    src = PANEL_JS.read_text(encoding="utf-8")
    start = src.index("const AUTOMOD_FEATURES = [")
    end = src.index("\n];", start)
    return src[start:end]


def _panel_js_features() -> dict[str, list[str]]:
    """從 panel.js 撈出 {feature_id: [field keys]}。"""
    block = _panel_js_automod_block()
    chunks = re.split(r"\{\s*id:\s*'([a-z0-9_]+)'", block)
    features = {}
    for feature_id, body in zip(chunks[1::2], chunks[2::2]):
        features[feature_id] = re.findall(r"\{\s*key:\s*'([a-z0-9_]+)'", body)
    return features


def _settings_pairs() -> dict[str, list[str]]:
    pairs: dict[str, list[str]] = {}
    for entry in AutoModerate.all_settings:
        feature, _, field = entry.partition("-")
        pairs.setdefault(feature, []).append(field)
    return pairs


def _schema_pairs() -> dict[str, list[str]]:
    return {
        schema["id"]: [field["key"] for field in schema["fields"]]
        for schema in gs.automod_feature_schemas()
    }


class AutomodFeatureParityTests(unittest.TestCase):
    def test_feature_id_sets_agree_across_all_four_copies(self):
        from_settings = set(_settings_pairs())
        from_panel = set(GuildPanel.AUTOMOD_FEATURE_IDS)
        from_schema = set(_schema_pairs())
        from_js = set(_panel_js_features())

        self.assertEqual(from_settings, from_panel,
                         "AutoModerate.all_settings 與 GuildPanel.AUTOMOD_FEATURE_IDS 不一致"
                         "（網頁面板會靜默丟掉缺少的功能）")
        self.assertEqual(from_settings, from_schema,
                         "all_settings 與 gettingstarted 設定精靈的功能清單不一致")
        self.assertEqual(from_settings, from_js,
                         "all_settings 與 static/js/panel.js 的功能清單不一致")

    def test_field_keys_agree_between_settings_and_wizard(self):
        settings, schema = _settings_pairs(), _schema_pairs()
        for feature in sorted(set(settings) & set(schema)):
            with self.subTest(feature=feature):
                self.assertEqual(sorted(settings[feature]), sorted(schema[feature]))

    def test_ignore_channel_features_match_wizard_schema(self):
        schema = _schema_pairs()
        with_ignore = {f for f, fields in schema.items() if "ignore_channels" in fields}
        self.assertEqual(with_ignore, AutoModerate.AUTOMOD_IGNORE_CHANNEL_FEATURES)

    def test_every_feature_is_fully_localized(self):
        """每個功能 id 在三種語言都要有完整的顯示字串。"""
        catalogs = {loc: _load_catalog(loc) for loc in LOCALES}
        for feature in sorted(_settings_pairs()):
            for loc in LOCALES:
                for key in (
                    f"automoderate.feature_name.{feature}",
                    f"automoderate.feature_desc.{feature}",
                    f"automoderate.feature_name_emoji.{feature}",
                    f"automoderate.info.feature_body.{feature}",
                    f"cmd.automoderate.automod.toggle.choice.{feature}",
                ):
                    with self.subTest(feature=feature, locale=loc, key=key):
                        self.assertTrue(catalogs[loc].get(key),
                                        f"{loc} 缺少 {key}")

    def test_fallback_wizard_tuples_cover_every_feature(self):
        """備援精靈的 _on_finish 白名單容易漏，用 AST 取出字面 tuple 比對。"""
        import ast

        source = (DISCORD_DIR / "AutoModerate.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_on_finish":
                target = node
                break
        self.assertIsNotNone(target, "找不到 QuickSetupView._on_finish")

        tuples = [
            {elt.value for elt in node.elts if isinstance(elt, ast.Constant)}
            for node in ast.walk(target)
            if isinstance(node, ast.Tuple) and len(node.elts) >= 8
        ]
        self.assertTrue(tuples, "_on_finish 內找不到功能白名單 tuple")
        self.assertIn(set(_settings_pairs()), tuples,
                      "_on_finish 的功能白名單與 all_settings 不一致")

    def test_boolean_settings_agree_between_cog_and_panel(self):
        self.assertEqual(set(AutoModerate.AUTOMOD_BOOLEAN_SETTINGS),
                         set(GuildPanel.AUTOMOD_BOOLEAN_KEYS))

    def test_boolean_fields_declared_as_boolean_in_wizard(self):
        """AUTOMOD_BOOLEAN_SETTINGS 列到的欄位，在精靈 schema 裡必須是 boolean 型別。"""
        for schema in gs.automod_feature_schemas():
            for field in schema["fields"]:
                if field["key"] in AutoModerate.AUTOMOD_BOOLEAN_SETTINGS:
                    with self.subTest(feature=schema["id"], field=field["key"]):
                        self.assertEqual(field["type"], "boolean")


def _load_catalog(locale: str) -> dict:
    """把 locales/<locale>/*.json 攤平成點號鍵，與 i18n 的載入方式一致。"""
    import i18n

    i18n.reload_catalogs()
    return i18n._catalogs[locale]


if __name__ == "__main__":
    unittest.main()
