import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import discord

DISCORD_DIR = Path(__file__).resolve().parents[1]
if str(DISCORD_DIR) not in sys.path:
    sys.path.insert(0, str(DISCORD_DIR))

import i18n
from tests.i18n_base import FakeDB, clear_i18n_caches


def _fake_interaction(user_id=1, guild_id=None, locale=None):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.guild_id = guild_id
    interaction.locale = locale
    return interaction


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """整個 ContextVar 設計的正當性依據：併發 interaction 不得互相污染。"""

    async def test_gather_no_cross_contamination(self):
        results = {"en": [], "zh-TW": []}

        async def worker(locale):
            with i18n.use_locale(locale):
                for _ in range(20):
                    await asyncio.sleep(0)
                    results[locale].append(i18n.current_locale())

        await asyncio.gather(worker("en"), worker("zh-TW"))
        self.assertEqual(results["en"], ["en"] * 20)
        self.assertEqual(results["zh-TW"], ["zh-TW"] * 20)

    async def test_create_task_inherits_scope(self):
        async def child():
            return i18n.current_locale()

        with i18n.use_locale("en"):
            task = asyncio.create_task(child())
            self.assertEqual(await task, "en")

    async def test_task_created_before_scope_does_not_inherit(self):
        started = asyncio.Event()

        async def child():
            await started.wait()
            return i18n.current_locale()

        task = asyncio.create_task(child())
        await asyncio.sleep(0)  # 讓 task 先啟動（context 已複製）
        with i18n.use_locale("en"):
            started.set()
            self.assertEqual(await task, i18n.DEFAULT_LOCALE)

    async def test_scope_cleans_up(self):
        async with i18n.guild_scope(None):
            pass
        self.assertEqual(i18n.current_locale(), i18n.DEFAULT_LOCALE)
        with i18n.use_locale("en"):
            async with i18n.guild_scope(None):
                self.assertEqual(i18n.current_locale(), i18n.DEFAULT_LOCALE)
            # 離開內層 scope 後要回到外層的 en，不是預設值
            self.assertEqual(i18n.current_locale(), "en")


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.fake_db = FakeDB()
        patcher = patch.object(i18n, "db", self.fake_db)
        patcher.start()
        self.addCleanup(patcher.stop)
        clear_i18n_caches()
        self.addCleanup(clear_i18n_caches)
        i18n.ensure_loaded()

    def test_priority_user_over_guild_over_discord(self):
        self.fake_db.set_user_data(1, 0, i18n.USER_LOCALE_KEY, "en")
        self.fake_db.set_server_config(10, i18n.GUILD_LOCALE_KEY, "zh-TW")
        clear_i18n_caches()
        self.assertEqual(
            i18n.resolve_locale(user_id=1, guild_id=10,
                                discord_locale=discord.Locale.taiwan_chinese),
            "en")

    def test_guild_beats_discord_locale(self):
        self.fake_db.set_server_config(10, i18n.GUILD_LOCALE_KEY, "zh-TW")
        clear_i18n_caches()
        self.assertEqual(
            i18n.resolve_locale(user_id=1, guild_id=10,
                                discord_locale=discord.Locale.american_english),
            "zh-TW")

    def test_auto_falls_through(self):
        self.fake_db.set_user_data(1, 0, i18n.USER_LOCALE_KEY, "auto")
        self.fake_db.set_server_config(10, i18n.GUILD_LOCALE_KEY, "auto")
        clear_i18n_caches()
        self.assertEqual(
            i18n.resolve_locale(user_id=1, guild_id=10,
                                discord_locale=discord.Locale.american_english),
            "en")

    def test_default_when_no_signal(self):
        self.assertEqual(i18n.resolve_locale(user_id=1, guild_id=10),
                         i18n.DEFAULT_LOCALE)

    def test_discord_locale_mapping(self):
        self.assertEqual(i18n.map_discord_locale(discord.Locale.taiwan_chinese), "zh-TW")
        self.assertEqual(i18n.map_discord_locale(discord.Locale.chinese), "zh-TW")
        self.assertEqual(i18n.map_discord_locale(discord.Locale.american_english), "en")
        self.assertEqual(i18n.map_discord_locale(discord.Locale.british_english), "en")
        # catch-all：其他語言給英文
        self.assertEqual(i18n.map_discord_locale(discord.Locale.japanese), "en")
        self.assertIsNone(i18n.map_discord_locale(None))

    def test_last_locale_persisted_once(self):
        i18n.note_discord_locale(1, discord.Locale.american_english)
        writes_after_first = self.fake_db.user_writes
        self.assertEqual(
            self.fake_db.get_user_data(1, 0, i18n.LAST_LOCALE_KEY), "en-US")
        # 相同值不重複寫入
        i18n.note_discord_locale(1, discord.Locale.american_english)
        i18n.note_discord_locale(1, discord.Locale.american_english)
        self.assertEqual(self.fake_db.user_writes, writes_after_first)
        # 值改變才寫
        i18n.note_discord_locale(1, discord.Locale.japanese)
        self.assertEqual(self.fake_db.user_writes, writes_after_first + 1)

    def test_last_locale_used_for_text_command_path(self):
        # 使用者曾用過斜線指令（en 客戶端），之後的 text command 路徑讀 last_locale
        i18n.note_discord_locale(1, discord.Locale.american_english)
        self.assertEqual(i18n.resolve_locale(user_id=1, guild_id=None), "en")

    def test_resolve_from_interaction_records_last_locale(self):
        interaction = _fake_interaction(
            user_id=5, guild_id=None, locale=discord.Locale.american_english)
        self.assertEqual(i18n.resolve_from_interaction(interaction), "en")
        self.assertEqual(
            self.fake_db.get_user_data(5, 0, i18n.LAST_LOCALE_KEY), "en-US")

    def test_set_get_roundtrip(self):
        i18n.set_user_locale(1, "en")
        self.assertEqual(i18n.get_user_locale(1), "en")
        i18n.set_user_locale(1, "auto")
        self.assertIsNone(i18n.get_user_locale(1))
        i18n.set_guild_locale(10, "en")
        self.assertEqual(i18n.get_guild_locale(10), "en")
        i18n.set_guild_locale(10, None)
        self.assertIsNone(i18n.get_guild_locale(10))


class LookupTests(unittest.TestCase):
    def setUp(self):
        i18n.ensure_loaded()
        self._saved = i18n._catalogs
        i18n._catalogs = {
            "zh-TW": {
                "x.msg.hello": "你好 {name}",
                "x.msg.only_zh": "只有中文",
                "x.unit.items": {"other": "{count} 個"},
            },
            "en": {
                "x.msg.hello": "Hello {name}",
                "x.unit.items": {"one": "{count} item", "other": "{count} items"},
                "x.msg.zero_demo": {"zero": "nothing", "one": "{count} thing",
                                    "other": "{count} things"},
            },
        }
        i18n._resolved.clear()
        i18n._loaded = True

        def restore():
            i18n._catalogs = self._saved
            i18n._resolved.clear()
        self.addCleanup(restore)

    def test_basic_and_params(self):
        with i18n.use_locale("en"):
            self.assertEqual(i18n.t("x.msg.hello", name="A"), "Hello A")
        with i18n.use_locale("zh-TW"):
            self.assertEqual(i18n.t("x.msg.hello", name="A"), "你好 A")

    def test_fallback_to_source(self):
        with i18n.use_locale("en"):
            self.assertEqual(i18n.t("x.msg.only_zh"), "只有中文")

    def test_base_language_fallback(self):
        with i18n.use_locale("en-GB"):
            self.assertEqual(i18n.t("x.msg.hello", name="A"), "Hello A")

    def test_missing_key_returns_key(self):
        with i18n.use_locale("en"):
            self.assertEqual(i18n.t("x.msg.nope"), "x.msg.nope")

    def test_missing_key_strict_raises(self):
        with patch.object(i18n, "STRICT", True):
            with i18n.use_locale("en"):
                with self.assertRaises(i18n.MissingTranslationError):
                    i18n.t("x.msg.nope")

    def test_default_kwarg(self):
        with i18n.use_locale("en"):
            self.assertEqual(i18n.t("x.msg.nope", default="fallback"), "fallback")

    def test_unknown_placeholder_left_literal(self):
        with i18n.use_locale("en"):
            self.assertEqual(i18n.t("x.msg.hello"), "Hello {name}")

    def test_plural_en(self):
        with i18n.use_locale("en"):
            self.assertEqual(i18n.tn("x.unit.items", 1), "1 item")
            self.assertEqual(i18n.tn("x.unit.items", 0), "0 items")
            self.assertEqual(i18n.tn("x.unit.items", 2), "2 items")

    def test_plural_zh_always_other(self):
        with i18n.use_locale("zh-TW"):
            self.assertEqual(i18n.tn("x.unit.items", 1), "1 個")
            self.assertEqual(i18n.tn("x.unit.items", 5), "5 個")

    def test_plural_zero_honored_when_present(self):
        with i18n.use_locale("en"):
            self.assertEqual(i18n.tn("x.msg.zero_demo", 0), "nothing")
            self.assertEqual(i18n.tn("x.msg.zero_demo", 1), "1 thing")

    def test_lookup_no_source_fallback(self):
        self.assertIsNone(i18n.lookup("x.msg.only_zh", "en"))
        self.assertEqual(i18n.lookup("x.msg.hello", "en"), "Hello {name}")

    def test_trace(self):
        with i18n.use_locale("en"):
            with i18n.trace() as tr:
                i18n.t("x.msg.hello", name="A")
                i18n.tn("x.unit.items", 3)
        self.assertIn("x.msg.hello", tr.keys)
        self.assertIn("x.unit.items", tr.keys)
        self.assertEqual(tr.params["x.msg.hello"], {"name": "A"})
        self.assertEqual(tr.params["x.unit.items"]["count"], 3)


class KAndViewTests(unittest.TestCase):
    def test_k_is_str(self):
        k = i18n.K("common.btn.confirm")
        self.assertIsInstance(k, str)
        self.assertEqual(k.key, "common.btn.confirm")

    def test_i18n_view_resolves_class_body_labels(self):
        i18n.ensure_loaded()

        class MyView(i18n.I18nView):
            @discord.ui.button(label=i18n.K("common.btn.confirm"))
            async def confirm(self, interaction, button):
                pass

        with i18n.use_locale("en"):
            view = MyView(timeout=None)
        labels = [child.label for child in view.children]
        self.assertIn("Confirm", labels)

        with i18n.use_locale("zh-TW"):
            view = MyView(timeout=None)
        labels = [child.label for child in view.children]
        self.assertIn("確認", labels)

    def test_plain_view_degrades_to_key(self):
        class PlainView(discord.ui.View):
            @discord.ui.button(label=i18n.K("common.btn.confirm"))
            async def confirm(self, interaction, button):
                pass

        view = PlainView(timeout=None)
        labels = [child.label for child in view.children]
        self.assertIn("common.btn.confirm", labels)  # 不 crash，顯示 key


class ChokePointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fake_db = FakeDB()
        patcher = patch.object(i18n, "db", self.fake_db)
        patcher.start()
        self.addCleanup(patcher.stop)
        clear_i18n_caches()
        self.addCleanup(clear_i18n_caches)

    async def test_ui_hooks_installed(self):
        from discord.ui.view import BaseView
        from discord.ui.modal import Modal
        self.assertTrue(getattr(BaseView._scheduled_task, "_i18n_wrapped", False))
        self.assertTrue(getattr(Modal._scheduled_task, "_i18n_wrapped", False))

    async def test_wrapper_sets_and_clears_locale(self):
        seen = {}

        class Dummy:
            async def _scheduled_task(self, item, interaction):
                seen["locale"] = i18n.current_locale()

        i18n._wrap_scheduled_task(Dummy)
        interaction = _fake_interaction(
            user_id=1, guild_id=None, locale=discord.Locale.american_english)
        await Dummy()._scheduled_task(None, interaction)
        self.assertEqual(seen["locale"], "en")
        # wrapper 離開後要清除
        self.assertEqual(i18n.current_locale(), i18n.DEFAULT_LOCALE)

    async def test_wrapper_without_interaction_passthrough(self):
        class Dummy:
            async def _scheduled_task(self, item, other):
                return i18n.current_locale()

        i18n._wrap_scheduled_task(Dummy)
        self.assertEqual(await Dummy()._scheduled_task(None, object()),
                         i18n.DEFAULT_LOCALE)

    async def test_tree_interaction_check_sets_locale(self):
        interaction = _fake_interaction(
            user_id=2, guild_id=None, locale=discord.Locale.american_english)
        check = i18n.I18nCommandTree.interaction_check
        result = await check(MagicMock(), interaction)
        self.assertTrue(result)
        self.assertEqual(i18n.current_locale(), "en")


if __name__ == "__main__":
    unittest.main()
