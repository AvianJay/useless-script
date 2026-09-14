"""內建處置動作字串與備援精靈版面的回歸測試。

修掉的兩個既有問題：

1. 內建動作預設值在 en/ja 無效（29 處）。兩個成因：
     en -- 訊息文字裡用了半形逗號。`,` 是動作分隔符
           （Moderate._split_action_chunks），所以 `delete {user}, please ...`
           會被解析成第二個動作、動詞是 `please`。
     ja -- DSL 動詞本身被翻譯（mute→ミュート、ban→禁止、smm→うーん）
           或整個消失，字串開頭變成 `{user}`。
   結果是 en/ja 語系照精靈建議值設定會被 check-action 拒絕。

2. 備援精靈的 anti_spam 版面會拋
   ValueError: could not find open space for item
   （3 個專屬 Select + 忽略頻道 + 處置動作 = 5 個 Select 佔滿 5 列，
   完成按鈕沒地方放）。
"""
import sys
import unittest
from pathlib import Path

DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import discord  # noqa: E402

import AutoModerate  # noqa: E402
import Moderate  # noqa: E402
import i18n  # noqa: E402

LOCALES = ("zh-TW", "en", "ja")


def _action_default_keys() -> list[str]:
    """所有存放動作 DSL 字串的語言檔鍵。"""
    catalog = i18n._catalogs[i18n.SOURCE_LOCALE]
    return sorted(
        key for key in catalog
        if key.startswith("automoderate.default_action.")
        or key.startswith("moderate.suggest.value.")
        or (key.startswith("gettingstarted.automod.feature.") and key.endswith(".field.action.default"))
    )


class ActionDefaultValidityTests(unittest.TestCase):
    """每一個內建動作字串，在每一種語言，都必須通過真正的解析器。"""

    @classmethod
    def setUpClass(cls):
        i18n.reload_catalogs()
        cls.keys = _action_default_keys()

    def _resolve(self, key, locale):
        token = i18n.push_locale(locale)
        try:
            return i18n.t(key, reason=i18n.t("moderate.suggest.sample_reason"))
        finally:
            i18n.reset_locale(token)

    def test_catalog_actually_contains_action_defaults(self):
        # 防止上面的鍵搜尋失效後整個測試變成空跑
        self.assertGreaterEqual(len(self.keys), 20, "找不到動作預設值鍵，測試會變成空跑")

    def test_every_action_default_parses(self):
        for key in self.keys:
            for locale in LOCALES:
                action = self._resolve(key, locale)
                with self.subTest(key=key, locale=locale):
                    result = Moderate.analyze_action_string(action)
                    self.assertTrue(
                        result["valid"],
                        f"{locale} {key} 無法解析：{result['error']}（值：{action!r}）",
                    )

    def test_dsl_verbs_are_never_translated(self):
        """每個 chunk 的第一個 token 必須是 BUILTIN_ACTIONS 裡的 ASCII 動詞。"""
        for key in self.keys:
            for locale in LOCALES:
                action = self._resolve(key, locale)
                for chunk in action.split(","):
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    verb = chunk.split()[0]
                    with self.subTest(key=key, locale=locale, verb=verb):
                        self.assertTrue(verb.isascii(), f"{locale} {key} 的動詞被翻譯了：{verb!r}")
                        self.assertIn(verb, Moderate.BUILTIN_ACTIONS,
                                      f"{locale} {key} 的動詞不在 BUILTIN_ACTIONS：{verb!r}")

    def test_chunk_count_matches_source_locale(self):
        """半形逗號是分隔符，所以各語言的動作數必須與 zh-TW 一致。

        這正是 en 壞掉的方式：訊息文字裡多一個半形逗號就多一個假動作。
        """
        for key in self.keys:
            source = self._resolve(key, i18n.SOURCE_LOCALE)
            expected = len([c for c in source.split(",") if c.strip()])
            for locale in LOCALES:
                action = self._resolve(key, locale)
                actual = len([c for c in action.split(",") if c.strip()])
                with self.subTest(key=key, locale=locale):
                    self.assertEqual(actual, expected,
                                     f"{locale} {key} 的動作數與來源語言不同"
                                     f"（可能是訊息文字裡有半形逗號）：{action!r}")

    def test_messageless_features_use_valid_messageless_actions(self):
        """在沒有觸發訊息的情境執行的功能，其預設動作必須通過嚴格驗證器。"""
        for feature in sorted(AutoModerate.MESSAGELESS_ACTION_FEATURES):
            key = f"gettingstarted.automod.feature.{feature}.field.action.default"
            if key not in i18n._catalogs[i18n.SOURCE_LOCALE]:
                continue
            for locale in LOCALES:
                action = self._resolve(key, locale)
                if not action.strip():
                    continue  # flagged_user 的處置是選用的，預設為空
                with self.subTest(feature=feature, locale=locale):
                    result = Moderate.analyze_member_join_action(action)
                    self.assertTrue(result["valid"],
                                    f"{locale} {feature} 預設動作不適用於無訊息情境：{result['error']}")


class QuickSetupLayoutTests(unittest.TestCase):
    """備援精靈的每一個功能都必須能在 Discord 的 5 列限制內組出版面。"""

    ALL_FEATURES = (
        "scamtrap", "escape_punish", "too_many_h1", "too_many_emojis",
        "anti_invite_link", "anti_uispam", "anti_raid", "anti_spam",
        "anti_voice_spam", "automod_detect", "flagged_user",
    )

    def setUp(self):
        i18n.reload_catalogs()

    def test_every_feature_builds_without_row_overflow(self):
        for feature in self.ALL_FEATURES:
            with self.subTest(feature=feature):
                view = AutoModerate.QuickSetupView(guild_id=1)
                view.feature = feature
                view.config = {}
                try:
                    view._update_components_step2(guild=None)
                except ValueError as error:  # could not find open space for item
                    self.fail(f"{feature} 的版面超出 Discord 的 5 列限制：{error}")
                selects = [c for c in view.children if isinstance(c, discord.ui.Select)
                           or isinstance(c, discord.ui.ChannelSelect)]
                self.assertLessEqual(
                    len(selects), AutoModerate.AUTOMOD_MAX_WIZARD_SELECTS,
                    f"{feature} 用了 {len(selects)} 個 Select，超過上限")

    def test_every_feature_still_offers_an_action_select(self):
        """砍版面時不能把必要的處置動作選單砍掉。"""
        for feature in self.ALL_FEATURES:
            if feature == "escape_punish":
                continue  # escape_punish 不需要 action，會提早 return
            with self.subTest(feature=feature):
                view = AutoModerate.QuickSetupView(guild_id=1)
                view.feature = feature
                view.config = {}
                view._update_components_step2(guild=None)
                placeholders = [getattr(c, "placeholder", None) for c in view.children]
                self.assertIn(i18n.t("automoderate.quick_setup.action_ph"), placeholders,
                              f"{feature} 沒有處置動作選單")

    def test_every_feature_still_offers_the_finish_button(self):
        for feature in self.ALL_FEATURES:
            with self.subTest(feature=feature):
                view = AutoModerate.QuickSetupView(guild_id=1)
                view.feature = feature
                view.config = {}
                view._update_components_step2(guild=None)
                buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
                self.assertTrue(buttons, f"{feature} 沒有完成按鈕")


if __name__ == "__main__":
    unittest.main()
