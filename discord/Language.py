"""語言設定模組。

/language show    - 顯示目前語言設定與判定過程
/language set     - 設定個人語言（跨伺服器、DM 皆有效）
/language server  - 設定伺服器預設語言（需要管理伺服器權限）

本模組是第一個完整走 i18n 的模組，所有回覆文字都來自 locales/。
"""
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

import i18n
from i18n import t
from globalenv import bot, start_bot, register_panel_settings
from logger import log


def _language_choices() -> list[app_commands.Choice]:
    choices = [
        app_commands.Choice(
            name=app_commands.locale_str("Auto", i18n_key="cmd.language.choice.auto"),
            value="auto",
        )
    ]
    for locale in i18n.available_locales():
        # autonym 永不翻譯，直接用純字串
        choices.append(app_commands.Choice(name=i18n.locale_display_name(locale), value=locale))
    return choices


def _setting_display(value: str | None) -> str:
    if value is None:
        return t("language.show.auto")
    return i18n.locale_display_name(value)


@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
class Language(commands.GroupCog,
               name=app_commands.locale_str("language", i18n_key="cmd.language.root.name"),
               description=app_commands.locale_str("Language settings", i18n_key="cmd.language.root.desc")):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    @app_commands.command(
        name=app_commands.locale_str("show", i18n_key="cmd.language.show.name"),
        description=app_commands.locale_str(
            "Show your current language and how it was determined",
            i18n_key="cmd.language.show.desc"),
    )
    async def show(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        guild_id = interaction.guild_id
        steps = i18n.explain_locale(user_id=user_id, guild_id=guild_id,
                                    discord_locale=interaction.locale)
        values = dict(steps)

        na = t("language.show.not_applicable")
        lines = [
            f"## {t('language.show.title')}",
            f"{t('language.show.user')}: {_setting_display(values.get('user'))}",
            f"{t('language.show.guild')}: {_setting_display(values.get('guild')) if guild_id else na}",
            f"{t('language.show.discord')}: {values.get('discord') or na}",
            f"**{t('language.show.effective')}: {i18n.locale_display_name(values.get('effective'))}**",
            "",
            t("language.msg.metadata_note"),
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(
        name=app_commands.locale_str("set", i18n_key="cmd.language.set.name"),
        description=app_commands.locale_str(
            "Set your personal language", i18n_key="cmd.language.set.desc"),
    )
    @app_commands.describe(
        language=app_commands.locale_str(
            "Language (auto = follow your Discord client language)",
            i18n_key="cmd.language.set.param.language"),
    )
    @app_commands.choices(language=_language_choices())
    async def set_user(self, interaction: discord.Interaction, language: str):
        if language != "auto" and language not in i18n.available_locales():
            await interaction.response.send_message(
                t("language.msg.invalid", value=language), ephemeral=True)
            return
        i18n.set_user_locale(interaction.user.id, language)
        # 立即以新語言回覆
        with i18n.use_locale(i18n.resolve_locale(
                user_id=interaction.user.id, guild_id=interaction.guild_id,
                discord_locale=interaction.locale)):
            if language == "auto":
                message = t("language.msg.user_auto")
            else:
                message = t("language.msg.user_set",
                            language=i18n.locale_display_name(language))
            message += "\n" + t("language.msg.metadata_note")
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name=app_commands.locale_str("server", i18n_key="cmd.language.server.name"),
        description=app_commands.locale_str(
            "Set the default language for this server",
            i18n_key="cmd.language.server.desc"),
    )
    @app_commands.describe(
        language=app_commands.locale_str(
            "Language (auto = follow each user's Discord client language)",
            i18n_key="cmd.language.server.param.language"),
    )
    @app_commands.choices(language=_language_choices())
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def set_server(self, interaction: discord.Interaction, language: str):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                t("common.err.guild_only"), ephemeral=True)
            return
        if language != "auto" and language not in i18n.available_locales():
            await interaction.response.send_message(
                t("language.msg.invalid", value=language), ephemeral=True)
            return
        i18n.set_guild_locale(interaction.guild_id, language)
        log(f"Guild locale set to {language}", module_name="Language",
            guild=interaction.guild, user=interaction.user)
        with i18n.use_locale(i18n.resolve_locale(
                user_id=interaction.user.id, guild_id=interaction.guild_id,
                discord_locale=interaction.locale)):
            if language == "auto":
                message = t("language.msg.guild_auto")
            else:
                message = t("language.msg.guild_set",
                            language=i18n.locale_display_name(language))
            await interaction.response.send_message(message, ephemeral=True)


def _register_language_panel_settings():
    options = [{"label": t("language.panel.option_auto", locale=i18n.SOURCE_LOCALE),
                "label_key": "language.panel.option_auto", "value": "auto"}]
    for locale in i18n.available_locales():
        options.append({"label": i18n.locale_display_name(locale), "value": locale})
    register_panel_settings(
        "Language",
        t("language.panel.module_display", locale=i18n.SOURCE_LOCALE),
        [
            {
                "display": t("language.panel.display", locale=i18n.SOURCE_LOCALE),
                "display_key": "language.panel.display",
                "description": t("language.panel.description", locale=i18n.SOURCE_LOCALE),
                "description_key": "language.panel.description",
                "database_key": i18n.GUILD_LOCALE_KEY,
                "type": "select",
                "default": "auto",
                "options": options,
                "trigger": lambda gid, value: i18n.invalidate_guild_locale_cache(gid),
            },
        ],
        description=t("language.panel.module_description", locale=i18n.SOURCE_LOCALE),
        icon="🌐",
    )


_register_language_panel_settings()
asyncio.run(bot.add_cog(Language(bot)))

if __name__ == "__main__":
    start_bot()
