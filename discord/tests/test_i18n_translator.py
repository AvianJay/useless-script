import asyncio
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import discord
from discord import app_commands
from discord.app_commands.translator import (
    TranslationContext,
    TranslationContextLocation,
)

DISCORD_DIR = Path(__file__).resolve().parents[1]
if str(DISCORD_DIR) not in sys.path:
    sys.path.insert(0, str(DISCORD_DIR))

import i18n


def _ctx(location, data=None):
    return TranslationContext(location=location, data=data)


def _run(coro):
    return asyncio.run(coro)


class CatalogTranslatorTests(unittest.TestCase):
    def setUp(self):
        i18n.ensure_loaded()
        self._saved = i18n._catalogs
        i18n._catalogs = {
            "zh-TW": {
                "cmd.x.ban.name": "ban",
                "cmd.x.ban.desc": "封禁一位用戶",
                "cmd.x.ban.param.user": "目標用戶",
                "cmd.x.bad.name": "大寫BAD",
            },
            "en": {
                "cmd.x.ban.name": "ban",
                "cmd.x.ban.desc": "Ban a user",
            },
            "ja": {
                "cmd.x.ban.name": "ban",
                "cmd.x.ban.desc": "ユーザーをBANします",
                "cmd.x.ban.param.user": "対象ユーザー",
                "cmd.x.ban.param_name.user": "ユーザー",
            },
        }
        i18n._resolved.clear()
        i18n._loaded = True

        def restore():
            i18n._catalogs = self._saved
            i18n._resolved.clear()
        self.addCleanup(restore)
        self.translator = i18n.CatalogTranslator()

    def test_description_translated_to_zh(self):
        s = app_commands.locale_str("Ban a user", i18n_key="cmd.x.ban.desc")
        result = _run(self.translator.translate(
            s, discord.Locale.taiwan_chinese,
            _ctx(TranslationContextLocation.command_description)))
        self.assertEqual(result, "封禁一位用戶")

    def test_identical_to_base_returns_none(self):
        s = app_commands.locale_str("Ban a user", i18n_key="cmd.x.ban.desc")
        result = _run(self.translator.translate(
            s, discord.Locale.american_english,
            _ctx(TranslationContextLocation.command_description)))
        self.assertIsNone(result)

    def test_unknown_key_returns_none(self):
        s = app_commands.locale_str("Foo", i18n_key="cmd.x.nope.desc")
        result = _run(self.translator.translate(
            s, discord.Locale.taiwan_chinese,
            _ctx(TranslationContextLocation.command_description)))
        self.assertIsNone(result)

    def test_japanese_metadata_translated(self):
        s = app_commands.locale_str("Ban a user", i18n_key="cmd.x.ban.desc")
        result = _run(self.translator.translate(
            s, discord.Locale.japanese,
            _ctx(TranslationContextLocation.command_description)))
        self.assertEqual(result, "ユーザーをBANします")

    def test_unmapped_locale_returns_none(self):
        s = app_commands.locale_str("Ban a user", i18n_key="cmd.x.ban.desc")
        result = _run(self.translator.translate(
            s, discord.Locale.french,
            _ctx(TranslationContextLocation.command_description)))
        self.assertIsNone(result)

    def test_japanese_parameter_name_translated(self):
        s = app_commands.locale_str(
            "user", i18n_key="cmd.x.ban.param_name.user")
        result = _run(self.translator.translate(
            s, discord.Locale.japanese,
            _ctx(TranslationContextLocation.parameter_name)))
        self.assertEqual(result, "ユーザー")

    def test_invalid_name_localization_rejected(self):
        s = app_commands.locale_str("bad", i18n_key="cmd.x.bad.name")
        result = _run(self.translator.translate(
            s, discord.Locale.taiwan_chinese,
            _ctx(TranslationContextLocation.command_name)))
        self.assertIsNone(result)  # 含大寫英文，不符合 Discord 名稱規則

    def test_all_eight_locations_no_crash(self):
        s = app_commands.locale_str("Ban a user", i18n_key="cmd.x.ban.desc")
        for location in TranslationContextLocation:
            _run(self.translator.translate(
                s, discord.Locale.taiwan_chinese, _ctx(location)))

    def test_no_i18n_key_returns_none(self):
        s = app_commands.locale_str("ban")  # 無 i18n_key
        data = SimpleNamespace(name="ban")
        for locale in (discord.Locale.taiwan_chinese, discord.Locale.american_english):
            result = _run(self.translator.translate(
                s, locale, _ctx(TranslationContextLocation.command_name, data)))
            self.assertIsNone(result)


class CommandNameCatalogTests(unittest.TestCase):
    """真實語言檔中所有 cmd.*.name 都必須是合法的 Discord 指令名稱。"""

    def test_all_name_keys_valid(self):
        i18n.reload_catalogs()
        pattern = re.compile(r"^cmd\..+\.name$")
        for locale, catalog in i18n._catalogs.items():
            for key, value in catalog.items():
                # .param./.choice. 底下的 name 是「參數叫 name」的描述；
                # .ctx. 是 context menu 名稱（允許空白與大寫）
                if ".param." in key or ".choice." in key or ".ctx." in key:
                    continue
                if pattern.fullmatch(key) and isinstance(value, str):
                    self.assertTrue(
                        i18n._valid_command_name(value),
                        f"{locale}:{key} = {value!r} is not a valid command name")

    def test_all_parameter_name_keys_valid(self):
        i18n.reload_catalogs()
        for locale, catalog in i18n._catalogs.items():
            for key, value in catalog.items():
                if ".param_name." not in key or not isinstance(value, str):
                    continue
                self.assertTrue(
                    i18n._valid_command_name(value),
                    f"{locale}:{key} = {value!r} is not a valid parameter name")

    def test_real_command_payload_contains_japanese_name_and_parameter(self):
        async def build_payload():
            client = discord.Client(intents=discord.Intents.none())
            tree = app_commands.CommandTree(client)

            async def callback(interaction: discord.Interaction, command: str):
                pass

            command = app_commands.Command(
                name=app_commands.locale_str(
                    "help", i18n_key="cmd.utilcommands.info.help.name"),
                description=app_commands.locale_str(
                    "Show command help",
                    i18n_key="cmd.utilcommands.info.help.desc"),
                callback=callback,
            )
            command._params["command"]._rename = app_commands.locale_str(
                "command",
                i18n_key="cmd.utilcommands.info.help.param_name.command",
            )
            tree.add_command(command)
            try:
                return await command.get_translated_payload(
                    tree, i18n.CatalogTranslator())
            finally:
                await client.close()

        payload = _run(build_payload())
        self.assertEqual(payload["name_localizations"]["ja"], "ヘルプ")
        self.assertEqual(
            payload["options"][0]["name_localizations"]["ja"], "コマンド")


if __name__ == "__main__":
    unittest.main()
