from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import discord
from discord import app_commands
from discord.ext import commands

from globalenv import (
    bot,
    config,
    failed_modules,
    get_command_mention,
    get_server_config,
    localized_panel_settings,
    modules,
    panel_settings,
    set_server_config,
)
from logger import log
import i18n
from i18n import t


if "Moderate" in modules:
    try:
        import Moderate
    except Exception:
        Moderate = None
else:
    Moderate = None


if "AutoReply" in modules:
    try:
        from AutoReply import AutoReplyBuilderView
    except Exception:
        AutoReplyBuilderView = None
else:
    AutoReplyBuilderView = None


if AutoReplyBuilderView is None:
    class AutoReplyBuilderView(discord.ui.View):
        def __init__(self, *args, **kwargs):
            super().__init__(timeout=900)


if "FixLink" in modules:
    try:
        import FixLink as FixLinkModule
    except Exception:
        FixLinkModule = None
else:
    FixLinkModule = None


if "JoinNotify" in modules:
    try:
        from JoinNotify import get_join_prompt_recipient
    except Exception:
        get_join_prompt_recipient = None
else:
    get_join_prompt_recipient = None


if "StickyMessage" in modules:
    try:
        import StickyMessage as StickyMessageModule
    except Exception:
        StickyMessageModule = None
else:
    StickyMessageModule = None


if get_join_prompt_recipient is None:
    async def get_join_prompt_recipient(guild: discord.Guild, bot_user_id: int | None = None):
        target_bot_id = bot_user_id or (bot.user.id if bot.user else None)
        if target_bot_id is not None:
            try:
                async for entry in guild.audit_logs(limit=10, action=discord.AuditLogAction.bot_add):
                    target = getattr(entry, "target", None)
                    if target is not None and target.id == target_bot_id:
                        return entry.user
            except (discord.Forbidden, discord.HTTPException):
                pass
        return guild.owner


PAGE_SIZE = 25
SESSION_TIMEOUT = 900


def paginate(values: list[Any], page: int, page_size: int = PAGE_SIZE) -> tuple[list[Any], int, int]:
    total_pages = max(1, math.ceil(len(values) / page_size))
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    return values[start:start + page_size], page, total_pages


def truncate(value: Any, limit: int = 100) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def resolve_select_value(value):
    if value is not None and hasattr(value, "resolve"):
        resolved = value.resolve()
        if resolved is not None:
            return resolved
    return value


def available_panel_modules() -> list[tuple[str, dict]]:
    unavailable = set(failed_modules)
    # 依當前 locale 取在地化後的 registry 拷貝（display/description/options）；
    # 傳入本模組的 panel_settings binding，讓測試 patch 得到
    return [
        (module_name, data)
        for module_name, data in localized_panel_settings(panel_settings).items()
        if module_name in modules and module_name not in unavailable
    ]


def find_setup_channel(guild: discord.Guild, recipient) -> discord.TextChannel | None:
    bot_member = guild.me
    if bot_member is None or recipient is None:
        return None

    candidates = []
    if guild.system_channel is not None:
        candidates.append(guild.system_channel)
    candidates.extend(
        channel
        for channel in sorted(guild.text_channels, key=lambda item: (item.position, item.id))
        if channel not in candidates
    )

    for channel in candidates:
        bot_permissions = channel.permissions_for(bot_member)
        recipient_permissions = channel.permissions_for(recipient)
        if (
            bot_permissions.view_channel
            and bot_permissions.send_messages
            and recipient_permissions.view_channel
        ):
            return channel
    return None


def format_setting_value(guild: discord.Guild, setting: dict, value: Any) -> str:
    stype = setting.get("type", "string")
    if value is None:
        # 有在地化預設值的設定：顯示渲染後的預設並標記（值本身仍是未設定）
        if setting.get("default_i18n_key"):
            return t("common.state.default_value",
                     value=truncate(t(setting["default_i18n_key"]), 200))
        return t("common.state.unset")
    if stype in ("channel", "voice_channel", "category"):
        channel = guild.get_channel(int(value)) if str(value).isdigit() else None
        return channel.mention if channel else t("gettingstarted.value.unknown_channel", value=value)
    if stype == "role":
        role = guild.get_role(int(value)) if str(value).isdigit() else None
        return role.mention if role else t("gettingstarted.value.unknown_role", value=value)
    if stype in ("channel_list", "role_list"):
        values = value if isinstance(value, list) else []
        mentions = []
        for raw_id in values[:15]:
            if stype == "channel_list":
                item = guild.get_channel(int(raw_id)) if str(raw_id).isdigit() else None
            else:
                item = guild.get_role(int(raw_id)) if str(raw_id).isdigit() else None
            mentions.append(item.mention if item else str(raw_id))
        if len(values) > 15:
            mentions.append(t("gettingstarted.value.more_items", count=len(values)))
        return i18n.join_list(mentions) if mentions else t("gettingstarted.value.empty_list")
    if stype == "boolean":
        return t("common.state.enabled") if bool(value) else t("common.state.disabled")
    if stype == "stickymessage_config":
        entries = value.get("entries", []) if isinstance(value, dict) else []
        return t("gettingstarted.value.entries_configured", count=len(entries))
    if isinstance(value, (dict, list)):
        return t("gettingstarted.value.items_configured", count=len(value))
    return truncate(value, 900)


def coerce_scalar_setting_value(setting: dict, raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return None

    stype = setting.get("type", "string")
    try:
        if stype == "number":
            value: Any = int(raw)
        elif stype == "float":
            value = float(raw)
        else:
            value = raw
    except ValueError as error:
        raise ValueError(t("gettingstarted.err.invalid_number")) from error

    minimum = setting.get("min")
    maximum = setting.get("max")
    if minimum is not None and isinstance(value, (int, float)) and value < minimum:
        raise ValueError(t("gettingstarted.err.value_too_small", minimum=minimum))
    if maximum is not None and isinstance(value, (int, float)) and value > maximum:
        raise ValueError(t("gettingstarted.err.value_too_large", maximum=maximum))
    return value


async def apply_registered_setting(
    guild_id: int,
    module_name: str,
    setting: dict,
    value: Any,
) -> str | None:
    previous_value = get_server_config(guild_id, setting["database_key"], setting.get("default"))
    if not set_server_config(guild_id, setting["database_key"], value):
        raise RuntimeError(t("gettingstarted.err.save_failed"))

    trigger = setting.get("trigger")
    if not callable(trigger):
        return None

    try:
        trigger_args = (
            (guild_id, value, previous_value)
            if setting.get("trigger_with_previous")
            else (guild_id, value)
        )
        result = trigger(*trigger_args)
        if inspect.isawaitable(result):
            await result
    except Exception as error:
        log(
            f"Quick setting trigger failed: {module_name}.{setting['database_key']}: {error}",
            level=logging.ERROR,
            module_name="gettingstarted",
        )
        return t("gettingstarted.err.trigger_failed")
    return None


@dataclass
class GettingStartedSession:
    guild: discord.Guild
    owner_id: int
    message: discord.InteractionMessage | discord.Message | None = None
    changes: set[tuple[str, str]] = field(default_factory=set)
    active_view: discord.ui.View | None = None

    async def ensure_owner(self, interaction: discord.Interaction) -> bool:
        permissions = getattr(interaction.user, "guild_permissions", None)
        if (
            interaction.guild is None
            or interaction.guild.id != self.guild.id
            or interaction.user.id != self.owner_id
            or permissions is None
            or not permissions.manage_guild
        ):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    t("gettingstarted.err.not_setup_owner"),
                    ephemeral=True,
                )
            return False
        return True

    def mark_changed(self, module_name: str, key: str):
        self.changes.add((module_name, key))

    async def render(
        self,
        interaction: discord.Interaction,
        *,
        embed: discord.Embed,
        view: discord.ui.View | None,
    ):
        if self.active_view is not None and self.active_view is not view:
            self.active_view.stop()
        self.active_view = view

        if not interaction.response.is_done():
            if interaction.message is not None:
                await interaction.response.edit_message(embed=embed, view=view)
            else:
                await interaction.response.defer(ephemeral=True)
                if self.message is not None:
                    await self.message.edit(embed=embed, view=view)
        elif self.message is not None:
            await self.message.edit(embed=embed, view=view)

    async def save(
        self,
        interaction: discord.Interaction,
        module_name: str,
        setting: dict,
        value: Any,
    ) -> bool:
        try:
            warning = await apply_registered_setting(self.guild.id, module_name, setting, value)
        except Exception as error:
            if not interaction.response.is_done():
                await interaction.response.send_message(str(error), ephemeral=True)
            else:
                await interaction.followup.send(str(error), ephemeral=True)
            return False

        self.mark_changed(module_name, setting["database_key"])
        if warning:
            if interaction.response.is_done():
                await interaction.followup.send(warning, ephemeral=True)
            else:
                await interaction.response.send_message(warning, ephemeral=True)
        return True


class SetupView(discord.ui.View):
    def __init__(self, session: GettingStartedSession, *, timeout: float = SESSION_TIMEOUT):
        super().__init__(timeout=timeout)
        self.session = session

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.session.ensure_owner(interaction)

    async def on_timeout(self):
        if self.session.active_view is not self:
            return
        for item in self.children:
            item.disabled = True
        if self.session.message is not None:
            try:
                with i18n.use_locale(i18n.resolve_locale(
                        user_id=self.session.owner_id, guild_id=self.session.guild.id)):
                    embed = discord.Embed(
                        title=t("gettingstarted.hub.timed_out_title"),
                        description=t("gettingstarted.hub.timed_out_desc"),
                        color=discord.Color.red(),
                    )
                await self.session.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass


class ModuleSelect(discord.ui.Select):
    def __init__(self, parent: "GettingStartedHubView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(placeholder=t("gettingstarted.hub.select_module_ph"), options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        module_name = self.values[0]
        await self.parent_view.session.render(
            interaction,
            embed=ModuleSettingsView.build_embed(self.parent_view.session, module_name),
            view=ModuleSettingsView(self.parent_view.session, module_name),
        )


class GettingStartedHubView(SetupView):
    def __init__(self, session: GettingStartedSession, page: int = 0):
        super().__init__(session)
        self.entries = available_panel_modules()
        current, self.page, self.total_pages = paginate(self.entries, page)
        options = [
            discord.SelectOption(
                label=truncate(data.get("display_name", module_name), 100),
                value=module_name,
                description=truncate(data.get("description") or t("gettingstarted.hub.default_module_desc"), 100),
                emoji=data.get("icon") or None,
            )
            for module_name, data in current
        ]
        if options:
            self.add_item(ModuleSelect(self, options))

        previous = discord.ui.Button(label=t("common.btn.prev"), style=discord.ButtonStyle.secondary, row=1)
        previous.disabled = self.page == 0
        previous.callback = self.previous_page
        self.add_item(previous)

        next_button = discord.ui.Button(label=t("common.btn.next"), style=discord.ButtonStyle.secondary, row=1)
        next_button.disabled = self.page >= self.total_pages - 1
        next_button.callback = self.next_page
        self.add_item(next_button)

        finish = discord.ui.Button(label=t("gettingstarted.btn.finish"), style=discord.ButtonStyle.success, row=1)
        finish.callback = self.finish
        self.add_item(finish)

    @staticmethod
    def build_embed(session: GettingStartedSession, page: int = 0) -> discord.Embed:
        entries = available_panel_modules()
        _, page, total_pages = paginate(entries, page)
        embed = discord.Embed(
            title=t("gettingstarted.hub.title", guild=session.guild.name),
            description=t("gettingstarted.hub.desc"),
            color=discord.Color.blurple(),
        )
        embed.add_field(name=t("gettingstarted.hub.field.available_modules"), value=str(len(entries)), inline=True)
        embed.add_field(name=t("gettingstarted.hub.field.changes_this_session"), value=str(len(session.changes)), inline=True)
        embed.set_footer(text=t("gettingstarted.hub.page_footer", page=page + 1, total=total_pages))
        return embed

    async def previous_page(self, interaction: discord.Interaction):
        target = GettingStartedHubView(self.session, self.page - 1)
        await self.session.render(
            interaction,
            embed=self.build_embed(self.session, target.page),
            view=target,
        )

    async def next_page(self, interaction: discord.Interaction):
        target = GettingStartedHubView(self.session, self.page + 1)
        await self.session.render(
            interaction,
            embed=self.build_embed(self.session, target.page),
            view=target,
        )

    async def finish(self, interaction: discord.Interaction):
        if self.session.changes:
            lines = [f"- `{module}.{key}`" for module, key in sorted(self.session.changes)]
            description = t("gettingstarted.hub.finish.summary") + "\n" + "\n".join(lines[:30])
            if len(lines) > 30:
                description += "\n" + t("gettingstarted.hub.finish.more_items", count=len(lines) - 30)
        else:
            description = t("gettingstarted.hub.finish.no_changes")
        embed = discord.Embed(title=t("gettingstarted.hub.finish.title"), description=description, color=discord.Color.green())
        await self.session.render(interaction, embed=embed, view=None)


class SettingSelect(discord.ui.Select):
    def __init__(self, parent: "ModuleSettingsView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(placeholder=t("gettingstarted.module.select_setting_ph"), options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        setting = next(
            item for item in self.parent_view.settings if item["database_key"] == key
        )
        stype = setting.get("type", "string")
        if stype == "autoreply_list":
            target = AutoReplyManagerView(self.parent_view.session, self.parent_view.module_name)
        elif stype == "automod_config":
            target = AutoModerateManagerView(self.parent_view.session, self.parent_view.module_name)
        elif stype == "webverify_config":
            target = WebVerifySetupView(self.parent_view.session, self.parent_view.module_name)
        elif stype == "fixlink_config":
            target = build_gettingstarted_fixlink_view(
                self.parent_view.session,
                self.parent_view.module_name,
                interaction,
            )
        elif stype == "antibeast_config":
            target = AntiBeastManagerView(
                self.parent_view.session,
                self.parent_view.module_name,
            )
        elif stype == "stickymessage_config":
            target = StickyMessageManagerView(
                self.parent_view.session,
                self.parent_view.module_name,
            )
        elif stype in ("channel_list", "role_list"):
            target = ListSettingView(
                self.parent_view.session,
                self.parent_view.module_name,
                setting,
            )
        else:
            target = SingleSettingView(
                self.parent_view.session,
                self.parent_view.module_name,
                setting,
            )
        await self.parent_view.session.render(
            interaction,
            embed=target.build_embed(),
            view=target,
        )


class ModuleSettingsView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str, page: int = 0):
        super().__init__(session)
        self.module_name = module_name
        # localized_panel_settings()：與 GettingStartedHubView 一致，否則點進
        # 模組後 display/description 會固定顯示中文，跟外層選單語言不一致。
        self.module_data = localized_panel_settings(panel_settings)[module_name]
        self.settings = self.module_data.get("settings", [])
        current, self.page, self.total_pages = paginate(self.settings, page)
        options = [
            discord.SelectOption(
                label=truncate(setting.get("display", setting["database_key"]), 100),
                value=setting["database_key"],
                description=truncate(setting.get("description") or setting.get("type", "string"), 100),
            )
            for setting in current
        ]
        if options:
            self.add_item(SettingSelect(self, options))

        previous = discord.ui.Button(label=t("common.btn.prev"), style=discord.ButtonStyle.secondary, row=1)
        previous.disabled = self.page == 0
        previous.callback = self.previous_page
        self.add_item(previous)

        next_button = discord.ui.Button(label=t("common.btn.next"), style=discord.ButtonStyle.secondary, row=1)
        next_button.disabled = self.page >= self.total_pages - 1
        next_button.callback = self.next_page
        self.add_item(next_button)

        back = discord.ui.Button(label=t("gettingstarted.btn.back_to_module"), style=discord.ButtonStyle.secondary, row=1)
        back.callback = self.back
        self.add_item(back)

    @staticmethod
    def build_embed(session: GettingStartedSession, module_name: str, page: int = 0) -> discord.Embed:
        module_data = localized_panel_settings(panel_settings)[module_name]
        settings = module_data.get("settings", [])
        current, page, total_pages = paginate(settings, page)
        lines = []
        for setting in current:
            value = get_server_config(
                session.guild.id,
                setting["database_key"],
                setting.get("default"),
            )
            lines.append(
                f"**{setting.get('display', setting['database_key'])}**\n"
                f"{format_setting_value(session.guild, setting, value)}"
            )
        embed = discord.Embed(
            title=f"{module_data.get('icon', '⚙️')} {module_data.get('display_name', module_name)}",
            description="\n\n".join(lines) if lines else t("gettingstarted.module.no_settings"),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=t("gettingstarted.module.page_footer", page=page + 1, total=total_pages))
        return embed

    async def previous_page(self, interaction: discord.Interaction):
        target = ModuleSettingsView(self.session, self.module_name, self.page - 1)
        await self.session.render(
            interaction,
            embed=self.build_embed(self.session, self.module_name, target.page),
            view=target,
        )

    async def next_page(self, interaction: discord.Interaction):
        target = ModuleSettingsView(self.session, self.module_name, self.page + 1)
        await self.session.render(
            interaction,
            embed=self.build_embed(self.session, self.module_name, target.page),
            view=target,
        )

    async def back(self, interaction: discord.Interaction):
        target = GettingStartedHubView(self.session)
        await self.session.render(
            interaction,
            embed=GettingStartedHubView.build_embed(self.session),
            view=target,
        )


class CompoundSettingUnavailableView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str, message: str):
        super().__init__(session)
        self.module_name = module_name
        self.message = message
        back = discord.ui.Button(label=t("common.btn.back"), style=discord.ButtonStyle.secondary)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self) -> discord.Embed:
        return discord.Embed(
            title=t("gettingstarted.compound.unavailable_title"),
            description=self.message,
            color=discord.Color.red(),
        )

    async def back(self, interaction: discord.Interaction):
        target = ModuleSettingsView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(self.session, self.module_name), view=target)


def build_gettingstarted_fixlink_view(
    session: GettingStartedSession,
    module_name: str,
    interaction: discord.Interaction,
):
    fixlink = FixLinkModule
    if fixlink is None:
        try:
            import FixLink as fixlink
        except Exception:
            return CompoundSettingUnavailableView(session, module_name, t("gettingstarted.fixlink.err.module_not_loaded"))

    cog = bot.get_cog("fixlink")
    if cog is None:
        return CompoundSettingUnavailableView(session, module_name, t("gettingstarted.fixlink.err.cog_not_running"))

    class GettingStartedFixLinkSettingsView(fixlink.FixLinkSettingsView):
        def __init__(self):
            super().__init__(cog, interaction)
            self.session = session
            self.module_name = module_name
            self.initial_config = copy.deepcopy(self.config)
            self.message = session.message
            back = discord.ui.Button(
                label=t("gettingstarted.fixlink.btn.back_to_center"),
                style=discord.ButtonStyle.secondary,
                row=0,
            )
            back.callback = self.back_to_settings
            self.add_item(back)

        async def interaction_check(self, current: discord.Interaction) -> bool:
            return await self.session.ensure_owner(current)

        def record_change(self):
            if self.config != self.initial_config:
                self.session.mark_changed(self.module_name, "fixlink")

        async def mutate_config(self, current: discord.Interaction, mutator):
            await super().mutate_config(current, mutator)
            self.record_change()

        async def refresh_message(self):
            await super().refresh_message()
            self.record_change()

        async def back_to_settings(self, current: discord.Interaction):
            self.record_change()
            target = ModuleSettingsView(self.session, self.module_name)
            await self.session.render(
                current,
                embed=target.build_embed(self.session, self.module_name),
                view=target,
            )

    return GettingStartedFixLinkSettingsView()


def get_stickymessage_module():
    global StickyMessageModule
    if StickyMessageModule is None:
        try:
            import StickyMessage as StickyMessageModule
        except Exception:
            return None
    return StickyMessageModule


def get_stickymessage_setting(module_name: str) -> dict:
    return next(
        setting
        for setting in panel_settings[module_name]["settings"]
        if setting["database_key"] == "stickymessage"
    )


def load_stickymessage_config(guild_id: int) -> dict:
    module = get_stickymessage_module()
    if module is None:
        return {"quiet_seconds": 10, "min_interval_seconds": 30, "entries": []}
    return module.normalize_config(get_server_config(guild_id, module.CONFIG_KEY, module.DEFAULT_CONFIG))


class StickyMessageEntrySelect(discord.ui.Select):
    def __init__(self, parent: "StickyMessageManagerView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(placeholder=t("gettingstarted.stickymessage.select_entry_ph"), options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        index = int(self.values[0])
        target = StickyMessageEditorView(
            self.parent_view.session,
            self.parent_view.module_name,
            index=index,
        )
        await self.parent_view.session.render(interaction, embed=target.build_embed(), view=target)


class StickyMessageManagerView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str):
        super().__init__(session)
        self.module_name = module_name
        config_value = load_stickymessage_config(session.guild.id)
        module = get_stickymessage_module()
        limit = module.get_stickymessage_limit(session.guild.id) if module else 5
        options = []
        for index, entry in enumerate(config_value["entries"][:PAGE_SIZE]):
            channel = session.guild.get_channel(entry["channel_id"])
            channel_name = f"#{channel.name}" if channel else t("gettingstarted.stickymessage.unknown_channel", channel_id=entry["channel_id"])
            content = entry["content"].replace("\n", " ")
            status_prefix = t("gettingstarted.stickymessage.status_over_quota") if index >= limit else t("gettingstarted.stickymessage.status_active")
            options.append(discord.SelectOption(
                label=truncate(f"{index + 1}. {channel_name}", 100),
                value=str(index),
                description=truncate(f"{status_prefix} · {content}", 100),
            ))
        if options:
            self.add_item(StickyMessageEntrySelect(self, options))

        add = discord.ui.Button(
            label=t("gettingstarted.btn.add"),
            style=discord.ButtonStyle.success,
            row=1,
            disabled=len(config_value["entries"]) >= limit,
        )
        add.callback = self.add_entry
        self.add_item(add)
        timing = discord.ui.Button(label=t("gettingstarted.stickymessage.btn.timing"), style=discord.ButtonStyle.primary, row=1)
        timing.callback = self.edit_timing
        self.add_item(timing)
        back = discord.ui.Button(label=t("gettingstarted.btn.back_to_settings"), style=discord.ButtonStyle.secondary, row=1)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self) -> discord.Embed:
        config_value = load_stickymessage_config(self.session.guild.id)
        module = get_stickymessage_module()
        limit = module.get_stickymessage_limit(self.session.guild.id) if module else 5
        lines = []
        for index, entry in enumerate(config_value["entries"]):
            status = t("gettingstarted.stickymessage.status_active") if index < limit else t("gettingstarted.stickymessage.status_over_quota")
            lines.append(f"{index + 1}. <#{entry['channel_id']}> · {status}")
        embed = discord.Embed(
            title=t("gettingstarted.stickymessage.manager_title"),
            description="\n".join(lines) if lines else t("gettingstarted.stickymessage.no_entries"),
            color=discord.Color.blurple(),
        )
        embed.add_field(name=t("gettingstarted.field.quota"), value=f"{len(config_value['entries'])} / {limit}", inline=True)
        embed.add_field(name=t("gettingstarted.stickymessage.field.quiet_seconds"), value=i18n.tn("common.unit.seconds", config_value["quiet_seconds"]), inline=True)
        embed.add_field(name=t("gettingstarted.stickymessage.field.min_interval"), value=i18n.tn("common.unit.seconds", config_value["min_interval_seconds"]), inline=True)
        embed.set_footer(text=t("gettingstarted.stickymessage.mentions_footer"))
        return embed

    async def add_entry(self, interaction: discord.Interaction):
        target = StickyMessageChannelPickerView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def edit_timing(self, interaction: discord.Interaction):
        await interaction.response.send_modal(StickyMessageTimingModal(self.session, self.module_name))

    async def back(self, interaction: discord.Interaction):
        target = ModuleSettingsView(self.session, self.module_name)
        await self.session.render(
            interaction,
            embed=ModuleSettingsView.build_embed(self.session, self.module_name),
            view=target,
        )


class StickyMessageChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "StickyMessageChannelPickerView"):
        self.parent_view = parent
        super().__init__(
            placeholder=t("gettingstarted.stickymessage.pick_channel_ph"),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = resolve_select_value(self.values[0])
        channel_id = selected.id if selected is not None else int(interaction.data["values"][0])
        config_value = load_stickymessage_config(self.parent_view.session.guild.id)
        if any(entry["channel_id"] == channel_id for entry in config_value["entries"]):
            await interaction.response.send_message(t("gettingstarted.stickymessage.err.channel_taken"), ephemeral=True)
            return
        target = StickyMessageEditorView(
            self.parent_view.session,
            self.parent_view.module_name,
            channel_id=channel_id,
        )
        await self.parent_view.session.render(interaction, embed=target.build_embed(), view=target)


class StickyMessageChannelPickerView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str):
        super().__init__(session)
        self.module_name = module_name
        self.add_item(StickyMessageChannelSelect(self))
        back = discord.ui.Button(label=t("common.btn.back"), style=discord.ButtonStyle.secondary, row=1)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self) -> discord.Embed:
        return discord.Embed(
            title=t("gettingstarted.stickymessage.add_title"),
            description=t("gettingstarted.stickymessage.add_desc"),
            color=discord.Color.blurple(),
        )

    async def back(self, interaction: discord.Interaction):
        target = StickyMessageManagerView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)


class StickyMessageContentModal(i18n.I18nModal, title=i18n.K("gettingstarted.stickymessage.content_modal_title")):
    content = discord.ui.TextInput(
        label=i18n.K("gettingstarted.stickymessage.content_label"),
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=2000,
        required=True,
    )

    def __init__(self, parent: "StickyMessageEditorView"):
        super().__init__()
        self.parent_view = parent
        self.content.default = parent.entry["content"]

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.entry["content"] = str(self.content.value).strip()
        target = StickyMessageEditorView(
            self.parent_view.session,
            self.parent_view.module_name,
            index=self.parent_view.index,
            channel_id=self.parent_view.entry["channel_id"],
            entry=self.parent_view.entry,
        )
        await self.parent_view.session.render(
            interaction,
            embed=target.build_embed(),
            view=target,
        )


class StickyMessageEditorView(SetupView):
    def __init__(
        self,
        session: GettingStartedSession,
        module_name: str,
        *,
        index: int | None = None,
        channel_id: int | None = None,
        entry: dict | None = None,
    ):
        super().__init__(session)
        self.module_name = module_name
        self.index = index
        config_value = load_stickymessage_config(session.guild.id)
        if entry is not None:
            self.entry = copy.deepcopy(entry)
        elif index is None:
            self.entry = {"channel_id": int(channel_id), "content": "", "allow_mentions": False}
        else:
            self.entry = copy.deepcopy(config_value["entries"][index])

        content = discord.ui.Button(label=t("gettingstarted.stickymessage.btn.edit_content"), style=discord.ButtonStyle.primary, row=0)
        content.callback = self.edit_content
        self.add_item(content)
        mentions = discord.ui.Button(
            label=t("gettingstarted.stickymessage.btn.first_mention", state=t("common.state.on") if self.entry["allow_mentions"] else t("common.state.off")),
            style=discord.ButtonStyle.danger if self.entry["allow_mentions"] else discord.ButtonStyle.secondary,
            row=0,
        )
        mentions.callback = self.toggle_mentions
        self.add_item(mentions)
        save = discord.ui.Button(
            label=t("gettingstarted.stickymessage.btn.save_and_publish"),
            style=discord.ButtonStyle.success,
            row=1,
            disabled=not bool(self.entry["content"]),
        )
        save.callback = self.save
        self.add_item(save)
        if index is not None:
            publish = discord.ui.Button(label=t("gettingstarted.stickymessage.btn.publish_now"), style=discord.ButtonStyle.primary, row=1)
            publish.callback = self.publish
            self.add_item(publish)
            up = discord.ui.Button(label=t("gettingstarted.btn.move_up"), style=discord.ButtonStyle.secondary, row=2, disabled=index == 0)
            up.callback = self.move_up
            self.add_item(up)
            down = discord.ui.Button(
                label=t("gettingstarted.btn.move_down"),
                style=discord.ButtonStyle.secondary,
                row=2,
                disabled=index >= len(config_value["entries"]) - 1,
            )
            down.callback = self.move_down
            self.add_item(down)
            remove = discord.ui.Button(label=t("gettingstarted.btn.remove"), style=discord.ButtonStyle.danger, row=2)
            remove.callback = self.remove
            self.add_item(remove)
        back = discord.ui.Button(label=t("common.btn.back"), style=discord.ButtonStyle.secondary, row=3)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self) -> discord.Embed:
        preview = self.entry["content"] or t("gettingstarted.stickymessage.no_content_yet")
        embed = discord.Embed(
            title=t("gettingstarted.stickymessage.edit_title") if self.index is not None else t("gettingstarted.stickymessage.add_title"),
            description=truncate(preview, 4000),
            color=discord.Color.blurple(),
        )
        embed.add_field(name=t("gettingstarted.field.channel"), value=f"<#{self.entry['channel_id']}>", inline=True)
        embed.add_field(
            name=t("gettingstarted.stickymessage.field.mentions"),
            value=t("gettingstarted.stickymessage.mentions_allowed") if self.entry["allow_mentions"] else t("gettingstarted.stickymessage.mentions_suppressed"),
            inline=True,
        )
        embed.set_footer(text=t("gettingstarted.stickymessage.auto_repost_footer"))
        return embed

    async def edit_content(self, interaction: discord.Interaction):
        await interaction.response.send_modal(StickyMessageContentModal(self))

    async def toggle_mentions(self, interaction: discord.Interaction):
        self.entry["allow_mentions"] = not self.entry["allow_mentions"]
        target = StickyMessageEditorView(
            self.session,
            self.module_name,
            index=self.index,
            channel_id=self.entry["channel_id"],
            entry=self.entry,
        )
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def save(self, interaction: discord.Interaction):
        if not self.entry["content"].strip():
            await interaction.response.send_message(t("gettingstarted.stickymessage.err.no_content"), ephemeral=True)
            return
        module = get_stickymessage_module()
        config_value = load_stickymessage_config(self.session.guild.id)
        if self.index is None:
            limit = module.get_stickymessage_limit(self.session.guild.id) if module else 5
            if len(config_value["entries"]) >= limit:
                await interaction.response.send_message(t("gettingstarted.stickymessage.err.quota_reached", count=limit), ephemeral=True)
                return
            config_value["entries"].append(copy.deepcopy(self.entry))
        else:
            if self.index >= len(config_value["entries"]):
                await interaction.response.send_message(t("gettingstarted.stickymessage.err.entry_gone"), ephemeral=True)
                return
            config_value["entries"][self.index] = copy.deepcopy(self.entry)
        if not await self.session.save(
            interaction,
            self.module_name,
            get_stickymessage_setting(self.module_name),
            module.normalize_config(config_value, strict=True) if module else config_value,
        ):
            return
        target = StickyMessageManagerView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def publish(self, interaction: discord.Interaction):
        cog = bot.get_cog("StickyMessage")
        if cog is None:
            await interaction.response.send_message(t("gettingstarted.stickymessage.err.module_unavailable"), ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True)
            await cog.publish_entry(self.session.guild.id, self.entry["channel_id"], notify_mentions=True)
            await interaction.followup.send(t("gettingstarted.stickymessage.msg.published"), ephemeral=True)
        except Exception as error:
            await interaction.followup.send(str(error) or t("gettingstarted.stickymessage.err.publish_failed"), ephemeral=True)

    async def _move(self, interaction: discord.Interaction, offset: int):
        module = get_stickymessage_module()
        config_value = load_stickymessage_config(self.session.guild.id)
        target_index = self.index + offset
        if self.index is None or target_index < 0 or target_index >= len(config_value["entries"]):
            return
        config_value["entries"][self.index], config_value["entries"][target_index] = (
            config_value["entries"][target_index],
            config_value["entries"][self.index],
        )
        if await self.session.save(
            interaction,
            self.module_name,
            get_stickymessage_setting(self.module_name),
            module.normalize_config(config_value, strict=True) if module else config_value,
        ):
            target = StickyMessageEditorView(self.session, self.module_name, index=target_index)
            await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def move_up(self, interaction: discord.Interaction):
        await self._move(interaction, -1)

    async def move_down(self, interaction: discord.Interaction):
        await self._move(interaction, 1)

    async def remove(self, interaction: discord.Interaction):
        module = get_stickymessage_module()
        config_value = load_stickymessage_config(self.session.guild.id)
        if self.index is not None and self.index < len(config_value["entries"]):
            config_value["entries"].pop(self.index)
        if await self.session.save(
            interaction,
            self.module_name,
            get_stickymessage_setting(self.module_name),
            module.normalize_config(config_value, strict=True) if module else config_value,
        ):
            target = StickyMessageManagerView(self.session, self.module_name)
            await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def back(self, interaction: discord.Interaction):
        target = StickyMessageManagerView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)


class StickyMessageTimingModal(i18n.I18nModal, title=i18n.K("gettingstarted.stickymessage.timing_modal_title")):
    quiet_seconds = discord.ui.TextInput(
        label=i18n.K("gettingstarted.stickymessage.quiet_seconds_label"),
        min_length=1,
        max_length=3,
    )
    min_interval_seconds = discord.ui.TextInput(
        label=i18n.K("gettingstarted.stickymessage.min_interval_label"),
        min_length=1,
        max_length=4,
    )

    def __init__(self, session: GettingStartedSession, module_name: str):
        super().__init__()
        self.session = session
        self.module_name = module_name
        config_value = load_stickymessage_config(session.guild.id)
        self.quiet_seconds.default = str(config_value["quiet_seconds"])
        self.min_interval_seconds.default = str(config_value["min_interval_seconds"])

    async def on_submit(self, interaction: discord.Interaction):
        module = get_stickymessage_module()
        config_value = load_stickymessage_config(self.session.guild.id)
        try:
            config_value["quiet_seconds"] = int(str(self.quiet_seconds.value).strip())
            config_value["min_interval_seconds"] = int(str(self.min_interval_seconds.value).strip())
            normalized = module.normalize_config(config_value, strict=True) if module else config_value
        except (TypeError, ValueError) as error:
            await interaction.response.send_message(str(error) or t("gettingstarted.stickymessage.err.invalid_seconds"), ephemeral=True)
            return
        if await self.session.save(
            interaction,
            self.module_name,
            get_stickymessage_setting(self.module_name),
            normalized,
        ):
            target = StickyMessageManagerView(self.session, self.module_name)
            await self.session.render(interaction, embed=target.build_embed(), view=target)


def get_antibeast_cog():
    return bot.get_cog("antibeast")


def get_antibeast_setting(module_name: str) -> dict:
    return next(
        setting
        for setting in panel_settings[module_name]["settings"]
        if setting["database_key"] == "antibeast"
    )


def load_antibeast_config(guild_id: int) -> dict:
    cog = get_antibeast_cog()
    if cog is None:
        raise RuntimeError(t("gettingstarted.antibeast.module_not_loaded"))
    return cog._get_config(guild_id)


async def save_antibeast_config(
    session: GettingStartedSession,
    interaction: discord.Interaction,
    module_name: str,
    config_value: dict,
) -> bool:
    return await session.save(
        interaction,
        module_name,
        get_antibeast_setting(module_name),
        config_value,
    )


class AntiBeastManagerView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str):
        super().__init__(session)
        self.module_name = module_name
        try:
            self.config = load_antibeast_config(session.guild.id)
        except RuntimeError:
            self.config = None
            back = discord.ui.Button(label=t("common.btn.back"), style=discord.ButtonStyle.secondary)
            back.callback = self.back
            self.add_item(back)
            return

        toggle = discord.ui.Button(
            label=t("gettingstarted.antibeast.btn.disable" if self.config["enabled"] else "gettingstarted.antibeast.btn.enable"),
            style=discord.ButtonStyle.danger if self.config["enabled"] else discord.ButtonStyle.success,
            row=0,
        )
        toggle.callback = self.toggle_enabled
        self.add_item(toggle)
        bypass = discord.ui.Button(label=t("gettingstarted.antibeast.btn.bypass_roles"), style=discord.ButtonStyle.primary, row=0)
        bypass.callback = self.open_bypass
        self.add_item(bypass)
        action = discord.ui.Button(label=t("gettingstarted.field.trigger_action"), style=discord.ButtonStyle.primary, row=0)
        action.callback = self.open_action
        self.add_item(action)
        back = discord.ui.Button(label=t("common.btn.back"), style=discord.ButtonStyle.secondary, row=1)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self) -> discord.Embed:
        if self.config is None:
            return discord.Embed(
                title=t("gettingstarted.antibeast.title"),
                description=t("gettingstarted.antibeast.module_not_loaded"),
                color=discord.Color.red(),
            )
        cog = get_antibeast_cog()
        kick = self.config["kick"]
        roles = [
            self.session.guild.get_role(role_id)
            for role_id in self.config["bypass_roles"]
        ]
        roles = [role for role in roles if role is not None]
        action_status = (
            t("gettingstarted.antibeast.action_status", seconds=kick["time_window"], count=kick["threshold"], action=kick["action"])
            if kick["enabled"]
            else t("common.state.disabled")
        )
        embed = discord.Embed(
            title=t("gettingstarted.antibeast.manager_title"),
            description=(
                t("gettingstarted.antibeast.enabled_desc")
                if self.config["enabled"]
                else t("gettingstarted.antibeast.disabled_desc")
            ),
            color=discord.Color.green() if self.config["enabled"] else discord.Color.blurple(),
        )
        embed.add_field(name=t("gettingstarted.field.trigger_action"), value=action_status, inline=False)
        embed.add_field(
            name=t("gettingstarted.field.action_scope"),
            value=cog._format_action_scope(kick) if cog is not None else t("gettingstarted.state.unknown"),
            inline=False,
        )
        embed.add_field(
            name=t("gettingstarted.antibeast.field.bypass_roles"),
            value=i18n.join_list([role.mention for role in roles]) if roles else t("gettingstarted.value.not_set"),
            inline=False,
        )
        return embed

    async def toggle_enabled(self, interaction: discord.Interaction):
        config_value = copy.deepcopy(load_antibeast_config(self.session.guild.id))
        config_value["enabled"] = not config_value["enabled"]
        if await save_antibeast_config(self.session, interaction, self.module_name, config_value):
            target = AntiBeastManagerView(self.session, self.module_name)
            await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def open_bypass(self, interaction: discord.Interaction):
        target = AntiBeastBypassView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def open_action(self, interaction: discord.Interaction):
        target = AntiBeastActionView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def back(self, interaction: discord.Interaction):
        target = ModuleSettingsView(self.session, self.module_name)
        await self.session.render(
            interaction,
            embed=target.build_embed(self.session, self.module_name),
            view=target,
        )


class AntiBeastBypassRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "AntiBeastBypassView"):
        self.parent_view = parent
        super().__init__(
            placeholder=t("gettingstarted.antibeast.bypass_select_ph"),
            min_values=1,
            max_values=25,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        role_ids = [role.id for role in self.values if not role.is_default()]
        await self.parent_view.save_roles(interaction, role_ids)


class AntiBeastBypassView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str):
        super().__init__(session)
        self.module_name = module_name
        self.config = load_antibeast_config(session.guild.id)
        self.add_item(AntiBeastBypassRoleSelect(self))
        clear = discord.ui.Button(label=t("gettingstarted.btn.clear_all"), style=discord.ButtonStyle.danger, row=1)
        clear.callback = self.clear
        self.add_item(clear)
        back = discord.ui.Button(label=t("gettingstarted.antibeast.btn.back_to_antibeast"), style=discord.ButtonStyle.secondary, row=1)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self) -> discord.Embed:
        roles = [self.session.guild.get_role(role_id) for role_id in self.config["bypass_roles"]]
        roles = [role for role in roles if role is not None]
        current_line = (
            t("gettingstarted.antibeast.bypass_current", roles=i18n.join_list([role.mention for role in roles]))
            if roles else t("gettingstarted.antibeast.bypass_none")
        )
        return discord.Embed(
            title=t("gettingstarted.antibeast.bypass_title"),
            description=t("gettingstarted.antibeast.bypass_desc") + "\n\n" + current_line,
            color=discord.Color.blurple(),
        )

    async def save_roles(self, interaction: discord.Interaction, role_ids: list[int]):
        config_value = copy.deepcopy(load_antibeast_config(self.session.guild.id))
        config_value["bypass_roles"] = role_ids
        if await save_antibeast_config(self.session, interaction, self.module_name, config_value):
            target = AntiBeastBypassView(self.session, self.module_name)
            await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def clear(self, interaction: discord.Interaction):
        await self.save_roles(interaction, [])

    async def back(self, interaction: discord.Interaction):
        target = AntiBeastManagerView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)


class AntiBeastActionPresetSelect(discord.ui.Select):
    def __init__(self, parent: "AntiBeastActionView"):
        self.parent_view = parent
        options = [
            discord.SelectOption(label=label, value=value)
            for label, value in (Moderate._action_input_suggestions() if Moderate is not None else [])[:25]
        ]
        super().__init__(placeholder=t("gettingstarted.select_preset_ph"), options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        analysis = Moderate.analyze_action_string(self.values[0], self.parent_view.session.guild.id)
        if not analysis["valid"]:
            await interaction.response.send_message(analysis["error"], ephemeral=True)
            return
        kick = copy.deepcopy(self.parent_view.config["kick"])
        kick["enabled"] = True
        kick["action"] = analysis["normalized"]
        await self.parent_view.save_kick(interaction, kick)


class AntiBeastActionView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str):
        super().__init__(session)
        self.module_name = module_name
        self.config = load_antibeast_config(session.guild.id)
        if Moderate is not None and Moderate.ACTION_INPUT_SUGGESTION_KEYS:
            self.add_item(AntiBeastActionPresetSelect(self))
        edit = discord.ui.Button(label=t("gettingstarted.antibeast.btn.edit_action"), style=discord.ButtonStyle.primary, row=1)
        edit.callback = self.edit
        self.add_item(edit)
        toggle = discord.ui.Button(
            label=t("gettingstarted.antibeast.btn.disable_action" if self.config["kick"]["enabled"] else "gettingstarted.antibeast.btn.enable_action"),
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        toggle.callback = self.toggle
        self.add_item(toggle)
        scope = discord.ui.Button(
            label=t("gettingstarted.antibeast.btn.everyone_here_only", state=t("common.state.on") if self.config["kick"]["only_everyone_here"] else t("common.state.off")),
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        scope.callback = self.toggle_scope
        self.add_item(scope)
        back = discord.ui.Button(label=t("gettingstarted.antibeast.btn.back_to_antibeast"), style=discord.ButtonStyle.secondary, row=2)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self, *, saved=False) -> discord.Embed:
        kick = self.config["kick"]
        cog = get_antibeast_cog()
        embed = discord.Embed(
            title=t("gettingstarted.antibeast.action_view_title"),
            description=t("gettingstarted.antibeast.action_view_desc"),
            color=discord.Color.green() if saved else discord.Color.blurple(),
        )
        embed.add_field(name=t("gettingstarted.field.status"), value=t("common.state.enabled") if kick["enabled"] else t("common.state.disabled"), inline=True)
        embed.add_field(name=t("gettingstarted.antibeast.field.threshold"), value=t("gettingstarted.antibeast.threshold_value", seconds=kick["time_window"], count=kick["threshold"]), inline=True)
        embed.add_field(
            name=t("gettingstarted.field.action_scope"),
            value=cog._format_action_scope(kick) if cog is not None else t("gettingstarted.state.unknown"),
            inline=False,
        )
        embed.add_field(name=t("gettingstarted.field.action"), value=f"```text\n{kick['action']}\n```", inline=False)
        if Moderate is not None:
            analysis = Moderate.analyze_action_string(kick["action"], self.session.guild.id)
            if analysis["valid"]:
                embed.add_field(
                    name=t("gettingstarted.field.execution_preview"),
                    value="\n".join(
                        f"{index}. {line}"
                        for index, line in enumerate(analysis.get("preview", []), 1)
                    ),
                    inline=False,
                )
        return embed

    async def save_kick(self, interaction: discord.Interaction, kick: dict):
        cog = get_antibeast_cog()
        config_value = copy.deepcopy(load_antibeast_config(self.session.guild.id))
        config_value["kick"] = cog._normalize_kick_config(kick)
        if await save_antibeast_config(self.session, interaction, self.module_name, config_value):
            target = AntiBeastActionView(self.session, self.module_name)
            await self.session.render(interaction, embed=target.build_embed(saved=True), view=target)

    async def edit(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AntiBeastGettingStartedActionModal(self))

    async def toggle(self, interaction: discord.Interaction):
        kick = copy.deepcopy(self.config["kick"])
        kick["enabled"] = not kick["enabled"]
        await self.save_kick(interaction, kick)

    async def toggle_scope(self, interaction: discord.Interaction):
        kick = copy.deepcopy(self.config["kick"])
        kick["only_everyone_here"] = not kick["only_everyone_here"]
        await self.save_kick(interaction, kick)

    async def back(self, interaction: discord.Interaction):
        target = AntiBeastManagerView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)


class AntiBeastGettingStartedActionModal(i18n.I18nModal, title=i18n.K("gettingstarted.antibeast.action_modal_title")):
    def __init__(self, parent: AntiBeastActionView):
        super().__init__(timeout=300)
        self.parent_view = parent
        kick = parent.config["kick"]
        self.threshold = discord.ui.TextInput(label=t("gettingstarted.antibeast.threshold_label"), default=str(kick["threshold"]), max_length=2)
        self.time_window = discord.ui.TextInput(label=t("gettingstarted.antibeast.time_window_label"), default=str(kick["time_window"]), max_length=4)
        self.action = discord.ui.TextInput(
            label=t("gettingstarted.field.moderate_action"),
            default=kick["action"],
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.add_item(self.threshold)
        self.add_item(self.time_window)
        self.add_item(self.action)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            threshold = int(self.threshold.value.strip())
            time_window = int(self.time_window.value.strip())
        except ValueError:
            await interaction.response.send_message(t("gettingstarted.antibeast.err.not_int"), ephemeral=True)
            return
        if not 1 <= threshold <= 20:
            await interaction.response.send_message(t("gettingstarted.antibeast.err.threshold_range"), ephemeral=True)
            return
        if not 5 <= time_window <= 3600:
            await interaction.response.send_message(t("gettingstarted.antibeast.err.time_window_range"), ephemeral=True)
            return
        analysis = Moderate.analyze_action_string(self.action.value.strip(), self.parent_view.session.guild.id)
        if not analysis["valid"]:
            await interaction.response.send_message(
                embed=Moderate.build_action_preview_embed(analysis),
                ephemeral=True,
            )
            return
        kick = copy.deepcopy(self.parent_view.config["kick"])
        kick.update({
            "enabled": True,
            "threshold": threshold,
            "time_window": time_window,
            "action": analysis["normalized"],
        })
        if analysis["requires_confirmation"]:
            target = AntiBeastActionConfirmView(self.parent_view, kick, analysis)
            await self.parent_view.session.render(interaction, embed=target.build_embed(), view=target)
            return
        await self.parent_view.save_kick(interaction, kick)


class AntiBeastActionConfirmView(SetupView):
    def __init__(self, parent_view: AntiBeastActionView, kick: dict, analysis: dict):
        super().__init__(parent_view.session, timeout=120)
        self.parent_view = parent_view
        self.kick = kick
        self.analysis = analysis
        confirm = discord.ui.Button(label=t("gettingstarted.antibeast.btn.confirm_action"), style=discord.ButtonStyle.success)
        confirm.callback = self.confirm
        self.add_item(confirm)
        retry = discord.ui.Button(label=t("gettingstarted.antibeast.btn.retry_action"), style=discord.ButtonStyle.secondary)
        retry.callback = self.retry
        self.add_item(retry)
        cancel = discord.ui.Button(label=t("common.btn.cancel"), style=discord.ButtonStyle.danger)
        cancel.callback = self.cancel
        self.add_item(cancel)

    def build_embed(self) -> discord.Embed:
        return Moderate.build_action_preview_embed(self.analysis, title=t("moderate.confirm_your_intent_title"))

    async def confirm(self, interaction: discord.Interaction):
        await self.parent_view.save_kick(interaction, self.kick)

    async def retry(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AntiBeastGettingStartedActionModal(self.parent_view))

    async def cancel(self, interaction: discord.Interaction):
        target = AntiBeastActionView(self.parent_view.session, self.parent_view.module_name)
        await self.parent_view.session.render(interaction, embed=target.build_embed(), view=target)


class ScalarSettingModal(discord.ui.Modal):
    def __init__(self, parent: "SingleSettingView"):
        title = truncate(parent.setting.get("display", t("gettingstarted.setting.edit_default_title")), 45)
        super().__init__(title=title)
        self.parent_view = parent
        stype = parent.setting.get("type", "string")
        current = get_server_config(
            parent.session.guild.id,
            parent.setting["database_key"],
            parent.setting.get("default"),
        )
        self.value_input = discord.ui.TextInput(
            label=t("gettingstarted.setting.value_label"),
            default="" if current is None else truncate(current, 4000),
            required=False,
            max_length=4000,
            style=discord.TextStyle.paragraph if stype == "text" else discord.TextStyle.short,
            placeholder=t("gettingstarted.setting.value_ph"),
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        setting = self.parent_view.setting
        try:
            value = coerce_scalar_setting_value(setting, self.value_input.value)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        if not await self.parent_view.session.save(
            interaction,
            self.parent_view.module_name,
            setting,
            value,
        ):
            return
        target = SingleSettingView(
            self.parent_view.session,
            self.parent_view.module_name,
            setting,
        )
        await self.parent_view.session.render(
            interaction,
            embed=target.build_embed(),
            view=target,
        )


class ValueSelect(discord.ui.Select):
    def __init__(self, parent: "SingleSettingView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(placeholder=t("gettingstarted.setting.select_value_ph"), options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if await self.parent_view.session.save(
            interaction,
            self.parent_view.module_name,
            self.parent_view.setting,
            value,
        ):
            target = SingleSettingView(
                self.parent_view.session,
                self.parent_view.module_name,
                self.parent_view.setting,
            )
            await self.parent_view.session.render(
                interaction,
                embed=target.build_embed(),
                view=target,
            )


class SettingChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "SingleSettingView", channel_types):
        self.parent_view = parent
        super().__init__(
            placeholder=t("gettingstarted.setting.select_channel_ph"),
            channel_types=channel_types,
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = resolve_select_value(self.values[0])
        value = selected.id if selected is not None else int(interaction.data["values"][0])
        if await self.parent_view.session.save(
            interaction,
            self.parent_view.module_name,
            self.parent_view.setting,
            value,
        ):
            target = SingleSettingView(
                self.parent_view.session,
                self.parent_view.module_name,
                self.parent_view.setting,
            )
            await self.parent_view.session.render(interaction, embed=target.build_embed(), view=target)


class SettingRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "SingleSettingView"):
        self.parent_view = parent
        super().__init__(placeholder=t("gettingstarted.setting.select_role_ph"), min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction):
        selected = resolve_select_value(self.values[0])
        value = selected.id if selected is not None else int(interaction.data["values"][0])
        if await self.parent_view.session.save(
            interaction,
            self.parent_view.module_name,
            self.parent_view.setting,
            value,
        ):
            target = SingleSettingView(
                self.parent_view.session,
                self.parent_view.module_name,
                self.parent_view.setting,
            )
            await self.parent_view.session.render(interaction, embed=target.build_embed(), view=target)


class SingleSettingView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str, setting: dict):
        super().__init__(session)
        self.module_name = module_name
        self.setting = setting
        stype = setting.get("type", "string")

        if stype == "boolean":
            enable = discord.ui.Button(label=t("common.state.enabled"), style=discord.ButtonStyle.success, row=0)
            enable.callback = self.enable
            self.add_item(enable)
            disable = discord.ui.Button(label=t("common.state.disabled"), style=discord.ButtonStyle.danger, row=0)
            disable.callback = self.disable
            self.add_item(disable)
        elif stype == "select":
            options = [
                discord.SelectOption(label=truncate(item["label"], 100), value=str(item["value"]))
                for item in setting.get("options", [])[:PAGE_SIZE]
            ]
            if options:
                self.add_item(ValueSelect(self, options))
        elif stype in ("channel", "voice_channel", "category"):
            channel_types = {
                "channel": [discord.ChannelType.text, discord.ChannelType.news],
                "voice_channel": [discord.ChannelType.voice, discord.ChannelType.stage_voice],
                "category": [discord.ChannelType.category],
            }[stype]
            self.add_item(SettingChannelSelect(self, channel_types))
        elif stype == "role":
            self.add_item(SettingRoleSelect(self))
        else:
            edit = discord.ui.Button(label=t("gettingstarted.btn.edit"), style=discord.ButtonStyle.primary, row=0)
            edit.callback = self.edit
            self.add_item(edit)

        if stype not in ("boolean",):
            clear = discord.ui.Button(label=t("gettingstarted.btn.clear"), style=discord.ButtonStyle.danger, row=1)
            clear.callback = self.clear
            self.add_item(clear)

        back = discord.ui.Button(label=t("common.btn.back"), style=discord.ButtonStyle.secondary, row=1)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self) -> discord.Embed:
        value = get_server_config(
            self.session.guild.id,
            self.setting["database_key"],
            self.setting.get("default"),
        )
        embed = discord.Embed(
            title=self.setting.get("display", self.setting["database_key"]),
            description=self.setting.get("description") or t("gettingstarted.setting.default_desc"),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name=t("gettingstarted.setting.field.current_value"),
            value=format_setting_value(self.session.guild, self.setting, value),
            inline=False,
        )
        embed.set_footer(text=t("gettingstarted.setting.key_footer", key=self.setting["database_key"]))
        return embed

    async def enable(self, interaction: discord.Interaction):
        if await self.session.save(interaction, self.module_name, self.setting, True):
            target = SingleSettingView(self.session, self.module_name, self.setting)
            await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def disable(self, interaction: discord.Interaction):
        if await self.session.save(interaction, self.module_name, self.setting, False):
            target = SingleSettingView(self.session, self.module_name, self.setting)
            await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def edit(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ScalarSettingModal(self))

    async def clear(self, interaction: discord.Interaction):
        if await self.session.save(interaction, self.module_name, self.setting, None):
            target = SingleSettingView(self.session, self.module_name, self.setting)
            await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def back(self, interaction: discord.Interaction):
        target = ModuleSettingsView(self.session, self.module_name)
        await self.session.render(
            interaction,
            embed=ModuleSettingsView.build_embed(self.session, self.module_name),
            view=target,
        )


class ListAddChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "ListSettingView"):
        self.parent_view = parent
        super().__init__(
            placeholder=t("gettingstarted.list.add_channel_ph"),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=25,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        ids = [int(value) for value in interaction.data.get("values", []) if str(value).isdigit()]
        await self.parent_view.add_values(interaction, ids)


class ListAddRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "ListSettingView"):
        self.parent_view = parent
        super().__init__(placeholder=t("gettingstarted.list.add_role_ph"), min_values=1, max_values=25, row=0)

    async def callback(self, interaction: discord.Interaction):
        ids = [int(value) for value in interaction.data.get("values", []) if str(value).isdigit()]
        await self.parent_view.add_values(interaction, ids)


class ListRemoveSelect(discord.ui.Select):
    def __init__(self, parent: "ListSettingView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(
            placeholder=t("gettingstarted.list.select_remove_ph"),
            min_values=1,
            max_values=len(options),
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        ids = [int(value) for value in self.values if str(value).isdigit()]
        await self.parent_view.remove_values(interaction, ids)


class ListSettingView(SetupView):
    def __init__(
        self,
        session: GettingStartedSession,
        module_name: str,
        setting: dict,
        page: int = 0,
    ):
        super().__init__(session)
        self.module_name = module_name
        self.setting = setting
        self.values = self.current_values()
        current, self.page, self.total_pages = paginate(self.values, page)

        if setting.get("type") == "channel_list":
            self.add_item(ListAddChannelSelect(self))
        else:
            self.add_item(ListAddRoleSelect(self))

        options = []
        for item_id in current:
            item = (
                session.guild.get_channel(int(item_id))
                if setting.get("type") == "channel_list"
                else session.guild.get_role(int(item_id))
            )
            options.append(
                discord.SelectOption(
                    label=truncate(getattr(item, "name", item_id), 100),
                    value=str(item_id),
                )
            )
        if options:
            self.add_item(ListRemoveSelect(self, options))

        previous = discord.ui.Button(label=t("common.btn.prev"), style=discord.ButtonStyle.secondary, row=2)
        previous.disabled = self.page == 0
        previous.callback = self.previous_page
        self.add_item(previous)
        next_button = discord.ui.Button(label=t("common.btn.next"), style=discord.ButtonStyle.secondary, row=2)
        next_button.disabled = self.page >= self.total_pages - 1
        next_button.callback = self.next_page
        self.add_item(next_button)
        clear = discord.ui.Button(label=t("gettingstarted.btn.clear_all"), style=discord.ButtonStyle.danger, row=2)
        clear.disabled = not self.values
        clear.callback = self.clear
        self.add_item(clear)
        back = discord.ui.Button(label=t("common.btn.back"), style=discord.ButtonStyle.secondary, row=2)
        back.callback = self.back
        self.add_item(back)

    def current_values(self) -> list[int]:
        raw = get_server_config(
            self.session.guild.id,
            self.setting["database_key"],
            self.setting.get("default", []),
        )
        return [int(value) for value in (raw or []) if str(value).isdigit()]

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.setting.get("display", self.setting["database_key"]),
            description=self.setting.get("description") or t("gettingstarted.list.default_desc"),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name=t("gettingstarted.list.count_field", count=len(self.values)),
            value=format_setting_value(self.session.guild, self.setting, self.values),
            inline=False,
        )
        embed.set_footer(text=t("gettingstarted.list.page_footer", page=self.page + 1, total=self.total_pages))
        return embed

    async def save_values(self, interaction: discord.Interaction, values: list[int]):
        if await self.session.save(interaction, self.module_name, self.setting, values):
            target = ListSettingView(self.session, self.module_name, self.setting, self.page)
            await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def add_values(self, interaction: discord.Interaction, values: list[int]):
        merged = list(dict.fromkeys(self.values + values))
        await self.save_values(interaction, merged)

    async def remove_values(self, interaction: discord.Interaction, values: list[int]):
        removed = set(values)
        await self.save_values(interaction, [value for value in self.values if value not in removed])

    async def clear(self, interaction: discord.Interaction):
        await self.save_values(interaction, [])

    async def previous_page(self, interaction: discord.Interaction):
        target = ListSettingView(self.session, self.module_name, self.setting, self.page - 1)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def next_page(self, interaction: discord.Interaction):
        target = ListSettingView(self.session, self.module_name, self.setting, self.page + 1)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def back(self, interaction: discord.Interaction):
        target = ModuleSettingsView(self.session, self.module_name)
        await self.session.render(
            interaction,
            embed=ModuleSettingsView.build_embed(self.session, self.module_name),
            view=target,
        )


class AutoReplyRuleSelect(discord.ui.Select):
    def __init__(self, parent: "AutoReplyManagerView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(placeholder=t("gettingstarted.autoreply.select_rule_ph"), options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        rule_index = int(self.values[0])
        target = AutoReplyRuleView(
            self.parent_view.session,
            self.parent_view.module_name,
            rule_index,
        )
        await self.parent_view.session.render(
            interaction,
            embed=target.build_embed(),
            view=target,
        )


class AutoReplyManagerView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str, page: int = 0):
        super().__init__(session)
        self.module_name = module_name
        self.rules = get_server_config(session.guild.id, "autoreplies", []) or []
        indexed = list(enumerate(self.rules))
        current, self.page, self.total_pages = paginate(indexed, page)
        options = []
        for rule_index, rule in current:
            triggers = ", ".join(str(value) for value in rule.get("trigger", [])) or t("gettingstarted.autoreply.unnamed_rule")
            responses = ", ".join(str(value) for value in rule.get("response", [])) or t("gettingstarted.autoreply.no_response")
            options.append(
                discord.SelectOption(
                    label=truncate(triggers, 100),
                    value=str(rule_index),
                    description=truncate(responses, 100),
                )
            )
        if options:
            self.add_item(AutoReplyRuleSelect(self, options))

        add_button = discord.ui.Button(label=t("gettingstarted.autoreply.btn.add_rule"), style=discord.ButtonStyle.success, row=1)
        add_button.callback = self.add_rule
        self.add_item(add_button)
        clear_button = discord.ui.Button(label=t("gettingstarted.btn.clear_all"), style=discord.ButtonStyle.danger, row=1)
        clear_button.disabled = not self.rules
        clear_button.callback = self.clear_rules
        self.add_item(clear_button)

        previous = discord.ui.Button(label=t("common.btn.prev"), style=discord.ButtonStyle.secondary, row=2)
        previous.disabled = self.page == 0
        previous.callback = self.previous_page
        self.add_item(previous)
        next_button = discord.ui.Button(label=t("common.btn.next"), style=discord.ButtonStyle.secondary, row=2)
        next_button.disabled = self.page >= self.total_pages - 1
        next_button.callback = self.next_page
        self.add_item(next_button)
        back = discord.ui.Button(label=t("common.btn.back"), style=discord.ButtonStyle.secondary, row=2)
        back.callback = self.back
        self.add_item(back)

    def get_cog(self):
        return bot.get_cog("AutoReply")

    def build_embed(self) -> discord.Embed:
        cog = self.get_cog()
        limit = cog._get_autoreply_limit(self.session.guild.id) if cog else 0
        embed = discord.Embed(
            title=t("gettingstarted.autoreply.manager_title"),
            description=t("gettingstarted.autoreply.manager_desc"),
            color=discord.Color.blurple(),
        )
        embed.add_field(name=t("gettingstarted.autoreply.field.current_rules"), value=f"{len(self.rules)} / {limit or '?'}", inline=True)
        embed.set_footer(text=t("gettingstarted.autoreply.rule_page_footer", page=self.page + 1, total=self.total_pages))
        return embed

    async def add_rule(self, interaction: discord.Interaction):
        cog = self.get_cog()
        if cog is None:
            await interaction.response.send_message(t("gettingstarted.autoreply.err.module_unavailable"), ephemeral=True)
            return
        builder = GettingStartedAutoReplyBuilderView(self.session, self.module_name, cog, interaction)
        await self.session.render(interaction, embed=builder.build_embed(), view=builder)

    async def clear_rules(self, interaction: discord.Interaction):
        target = AutoReplyClearConfirmView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def previous_page(self, interaction: discord.Interaction):
        target = AutoReplyManagerView(self.session, self.module_name, self.page - 1)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def next_page(self, interaction: discord.Interaction):
        target = AutoReplyManagerView(self.session, self.module_name, self.page + 1)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def back(self, interaction: discord.Interaction):
        target = ModuleSettingsView(self.session, self.module_name)
        await self.session.render(
            interaction,
            embed=ModuleSettingsView.build_embed(self.session, self.module_name),
            view=target,
        )


class AutoReplyRuleView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str, rule_index: int):
        super().__init__(session)
        self.module_name = module_name
        self.rule_index = rule_index

        edit = discord.ui.Button(label=t("gettingstarted.btn.edit"), style=discord.ButtonStyle.primary, row=0)
        edit.callback = self.edit
        self.add_item(edit)
        delete = discord.ui.Button(label=t("gettingstarted.btn.remove"), style=discord.ButtonStyle.danger, row=0)
        delete.callback = self.delete
        self.add_item(delete)
        back = discord.ui.Button(label=t("gettingstarted.autoreply.btn.back_to_rules"), style=discord.ButtonStyle.secondary, row=0)
        back.callback = self.back
        self.add_item(back)

    def get_rule(self) -> dict | None:
        rules = get_server_config(self.session.guild.id, "autoreplies", []) or []
        if 0 <= self.rule_index < len(rules):
            return rules[self.rule_index]
        return None

    def build_embed(self) -> discord.Embed:
        rule = self.get_rule()
        cog = bot.get_cog("AutoReply")
        if rule is None:
            return discord.Embed(title=t("gettingstarted.autoreply.rule_not_found"), color=discord.Color.red())
        if cog is not None:
            return cog._build_autoreply_rule_embed(
                title=t("gettingstarted.autoreply.rule_title", index=self.rule_index + 1),
                rule=rule,
                guild=self.session.guild,
            )
        return discord.Embed(
            title=t("gettingstarted.autoreply.rule_title", index=self.rule_index + 1),
            description=truncate(rule, 4000),
            color=discord.Color.blurple(),
        )

    async def edit(self, interaction: discord.Interaction):
        rule = self.get_rule()
        cog = bot.get_cog("AutoReply")
        if rule is None or cog is None:
            await interaction.response.send_message(t("gettingstarted.autoreply.err.rule_gone_or_unavailable"), ephemeral=True)
            return
        builder = GettingStartedAutoReplyBuilderView(
            self.session,
            self.module_name,
            cog,
            interaction,
            rule_index=self.rule_index,
        )
        await self.session.render(interaction, embed=builder.build_embed(title=t("gettingstarted.autoreply.edit_rule_title")), view=builder)

    async def delete(self, interaction: discord.Interaction):
        target = AutoReplyDeleteConfirmView(
            self.session,
            self.module_name,
            self.rule_index,
        )
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def back(self, interaction: discord.Interaction):
        target = AutoReplyManagerView(self.session, self.module_name, self.rule_index // PAGE_SIZE)
        await self.session.render(interaction, embed=target.build_embed(), view=target)


def save_autoreply_rule(cog, guild_id: int, rule: dict, rule_index: int | None = None):
    if rule_index is None:
        return cog._save_new_autoreply_rule(guild_id, rule)

    rules = get_server_config(guild_id, "autoreplies", []) or []
    if not 0 <= rule_index < len(rules):
        raise ValueError(t("gettingstarted.autoreply.err.edit_target_gone"))

    duplicate = cog._find_duplicate_triggers_in_list(rule.get("trigger", []))
    if duplicate:
        raise ValueError(cog._format_autoreply_trigger_conflict_message(duplicate, existing=False))

    original = rules[rule_index]
    conflicts = cog._find_conflicting_autoreply_triggers(
        rules,
        rule.get("trigger", []),
        skip_rule=original,
    )
    if conflicts:
        raise ValueError(cog._format_autoreply_trigger_conflict_message(conflicts, existing=True))

    rules[rule_index] = rule
    if not set_server_config(guild_id, "autoreplies", rules):
        raise ValueError(t("gettingstarted.autoreply.err.save_failed"))
    return len(rules), cog._get_autoreply_limit(guild_id)


class GettingStartedAutoReplyBuilderView(AutoReplyBuilderView):
    def __init__(
        self,
        session: GettingStartedSession,
        module_name: str,
        cog,
        interaction: discord.Interaction,
        rule_index: int | None = None,
    ):
        self.session = session
        self.module_name = module_name
        self.rule_index = rule_index
        super().__init__(cog, interaction)
        self.timeout = SESSION_TIMEOUT
        self.message = session.message

        if rule_index is not None:
            rules = get_server_config(session.guild.id, "autoreplies", []) or []
            if 0 <= rule_index < len(rules):
                rule = rules[rule_index]
                self.state = {
                    "trigger_text": "\n".join(str(value) for value in rule.get("trigger", [])),
                    "response_text": "\n".join(str(value) for value in rule.get("response", [])),
                    "mode": rule.get("mode", "contains"),
                    "reply": bool(rule.get("reply", False)),
                    "channel_mode": rule.get("channel_mode", "all"),
                    "channels": [int(value) for value in rule.get("channels", []) if str(value).isdigit()],
                    "random_chance": int(rule.get("random_chance", 100)),
                }
                self._rebuild_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.session.ensure_owner(interaction)

    async def ensure_owner(self, interaction: discord.Interaction) -> bool:
        return await self.session.ensure_owner(interaction)

    async def save_rule(self, interaction: discord.Interaction):
        if not await self.ensure_owner(interaction):
            return
        try:
            rule = self.cog._build_autoreply_rule(
                guild=self.guild,
                mode=self.state["mode"],
                trigger_input=self.state["trigger_text"],
                response_input=self.state["response_text"],
                reply=self.state["reply"],
                channel_mode=self.state["channel_mode"],
                channels_input=self.state["channels"],
                random_chance=self.state["random_chance"],
            )
            save_autoreply_rule(self.cog, self.guild.id, rule, self.rule_index)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        self.session.mark_changed(self.module_name, "autoreplies")
        target = AutoReplyManagerView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def cancel_builder(self, interaction: discord.Interaction):
        if not await self.ensure_owner(interaction):
            return
        target = AutoReplyManagerView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def on_timeout(self):
        if self.session.active_view is not self:
            return
        for item in self.children:
            item.disabled = True
        if self.session.message is not None:
            try:
                with i18n.use_locale(i18n.resolve_locale(
                        user_id=self.session.owner_id, guild_id=self.session.guild.id)):
                    embed = discord.Embed(
                        title=t("gettingstarted.autoreply.builder_timed_out_title"),
                        description=t("gettingstarted.autoreply.builder_timed_out_desc"),
                        color=discord.Color.red(),
                    )
                await self.session.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass


class AutoReplyDeleteConfirmView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str, rule_index: int):
        super().__init__(session)
        self.module_name = module_name
        self.rule_index = rule_index
        confirm = discord.ui.Button(label=t("gettingstarted.autoreply.btn.confirm_delete"), style=discord.ButtonStyle.danger)
        confirm.callback = self.confirm
        self.add_item(confirm)
        cancel = discord.ui.Button(label=t("common.btn.cancel"), style=discord.ButtonStyle.secondary)
        cancel.callback = self.cancel
        self.add_item(cancel)

    def build_embed(self) -> discord.Embed:
        return discord.Embed(
            title=t("gettingstarted.autoreply.delete_confirm_title"),
            description=t("gettingstarted.autoreply.delete_confirm_desc"),
            color=discord.Color.red(),
        )

    async def confirm(self, interaction: discord.Interaction):
        rules = get_server_config(self.session.guild.id, "autoreplies", []) or []
        if 0 <= self.rule_index < len(rules):
            rules.pop(self.rule_index)
            set_server_config(self.session.guild.id, "autoreplies", rules)
            self.session.mark_changed(self.module_name, "autoreplies")
        target = AutoReplyManagerView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def cancel(self, interaction: discord.Interaction):
        target = AutoReplyRuleView(self.session, self.module_name, self.rule_index)
        await self.session.render(interaction, embed=target.build_embed(), view=target)


class AutoReplyClearConfirmView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str):
        super().__init__(session)
        self.module_name = module_name
        confirm = discord.ui.Button(label=t("gettingstarted.autoreply.btn.confirm_clear_all"), style=discord.ButtonStyle.danger)
        confirm.callback = self.confirm
        self.add_item(confirm)
        cancel = discord.ui.Button(label=t("common.btn.cancel"), style=discord.ButtonStyle.secondary)
        cancel.callback = self.cancel
        self.add_item(cancel)

    def build_embed(self) -> discord.Embed:
        count = len(get_server_config(self.session.guild.id, "autoreplies", []) or [])
        return discord.Embed(
            title=t("gettingstarted.autoreply.clear_confirm_title"),
            description=t("gettingstarted.autoreply.clear_confirm_desc", count=count),
            color=discord.Color.red(),
        )

    async def confirm(self, interaction: discord.Interaction):
        set_server_config(self.session.guild.id, "autoreplies", [])
        self.session.mark_changed(self.module_name, "autoreplies")
        target = AutoReplyManagerView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def cancel(self, interaction: discord.Interaction):
        target = AutoReplyManagerView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)


def automod_feature_schemas(*, locale: str | None = None) -> list[dict]:
    """AutoMod 快速設定精靈的功能清單；每次呼叫都重新以 t() 解析，
    避免 label/description/選項文字在 import 期被凍結成中文。
    DSL 動作詞（delete/ban/kick/mute/warn 與時間後綴）不翻譯，只有
    嵌在動作字串裡給人看的理由文字走 t()，比照 AutoModerate.py
    default_action.* 系列函式的既有慣例。"""
    def tt(key, **params):
        return t(key, locale=locale, **params)

    return [
        {
            "id": "scamtrap",
            "label": tt("gettingstarted.automod.feature.scamtrap.label"),
            "description": tt("gettingstarted.automod.feature.scamtrap.desc"),
            "fields": [
                {"key": "channel_id", "label": tt("gettingstarted.automod.feature.scamtrap.field.channel_id"), "type": "channel", "required": True},
                {
                    "key": "action",
                    "label": tt("gettingstarted.automod.field.action"),
                    "type": "string",
                    "default": tt("gettingstarted.automod.feature.scamtrap.field.action.default"),
                    "required": True,
                },
            ],
        },
        {
            "id": "escape_punish",
            "label": tt("gettingstarted.automod.feature.escape_punish.label"),
            "description": tt("gettingstarted.automod.feature.escape_punish.desc"),
            "fields": [
                {
                    "key": "punishment",
                    "label": tt("gettingstarted.automod.feature.escape_punish.field.punishment"),
                    "type": "select",
                    "default": "ban",
                    "options": [{"label": tt("gettingstarted.automod.action.ban"), "value": "ban"}],
                },
                {"key": "duration", "label": tt("gettingstarted.automod.feature.escape_punish.field.duration"), "type": "string", "default": "0"},
            ],
        },
        {
            "id": "too_many_h1",
            "label": tt("gettingstarted.automod.feature.too_many_h1.label"),
            "description": tt("gettingstarted.automod.feature.too_many_h1.desc"),
            "fields": [
                {"key": "max_length", "label": tt("gettingstarted.automod.feature.too_many_h1.field.max_length"), "type": "number", "default": "20", "min": 1},
                {"key": "action", "label": tt("gettingstarted.automod.field.action"), "type": "string", "default": "warn", "required": True},
                {"key": "ignore_channels", "label": tt("gettingstarted.automod.field.ignore_channels"), "type": "channel_list", "default": []},
            ],
        },
        {
            "id": "too_many_emojis",
            "label": tt("gettingstarted.automod.feature.too_many_emojis.label"),
            "description": tt("gettingstarted.automod.feature.too_many_emojis.desc"),
            "fields": [
                {"key": "max_emojis", "label": tt("gettingstarted.automod.feature.too_many_emojis.field.max_emojis"), "type": "number", "default": "10", "min": 1},
                {"key": "action", "label": tt("gettingstarted.automod.field.action"), "type": "string", "default": "warn", "required": True},
                {"key": "ignore_channels", "label": tt("gettingstarted.automod.field.ignore_channels"), "type": "channel_list", "default": []},
            ],
        },
        {
            "id": "anti_invite_link",
            "label": tt("gettingstarted.automod.feature.anti_invite_link.label"),
            "description": tt("gettingstarted.automod.feature.anti_invite_link.desc"),
            "fields": [
                {"key": "allow_current_server", "label": tt("gettingstarted.automod.feature.anti_invite_link.field.allow_current_server"), "type": "boolean", "default": False},
                {
                    "key": "action",
                    "label": tt("gettingstarted.automod.field.action"),
                    "type": "string",
                    "default": tt("gettingstarted.automod.feature.anti_invite_link.field.action.default"),
                    "required": True,
                },
                {"key": "ignore_channels", "label": tt("gettingstarted.automod.field.ignore_channels"), "type": "channel_list", "default": []},
            ],
        },
        {
            "id": "anti_uispam",
            "label": tt("gettingstarted.automod.feature.anti_uispam.label"),
            "description": tt("gettingstarted.automod.feature.anti_uispam.desc"),
            "fields": [
                {"key": "max_count", "label": tt("gettingstarted.automod.feature.anti_uispam.field.max_count"), "type": "number", "default": "5", "min": 1},
                {"key": "time_window", "label": tt("gettingstarted.automod.field.time_window"), "type": "number", "default": "60", "min": 1},
                {
                    "key": "action",
                    "label": tt("gettingstarted.automod.field.action"),
                    "type": "string",
                    "default": tt("gettingstarted.automod.feature.anti_uispam.field.action.default"),
                    "required": True,
                },
                {"key": "ignore_channels", "label": tt("gettingstarted.automod.field.ignore_channels"), "type": "channel_list", "default": []},
            ],
        },
        {
            "id": "anti_raid",
            "label": tt("gettingstarted.automod.feature.anti_raid.label"),
            "description": tt("gettingstarted.automod.feature.anti_raid.desc"),
            "fields": [
                {"key": "max_joins", "label": tt("gettingstarted.automod.feature.anti_raid.field.max_joins"), "type": "number", "default": "5", "min": 1},
                {"key": "time_window", "label": tt("gettingstarted.automod.field.time_window"), "type": "number", "default": "60", "min": 1},
                {"key": "action", "label": tt("gettingstarted.automod.field.action"), "type": "string", "default": tt("gettingstarted.automod.feature.anti_raid.field.action.default"), "required": True},
            ],
        },
        {
            "id": "anti_spam",
            "label": tt("gettingstarted.automod.feature.anti_spam.label"),
            "description": tt("gettingstarted.automod.feature.anti_spam.desc"),
            "fields": [
                {"key": "max_messages", "label": tt("gettingstarted.automod.feature.anti_spam.field.max_messages"), "type": "number", "default": "5", "min": 1},
                {"key": "time_window", "label": tt("gettingstarted.automod.field.time_window"), "type": "number", "default": "30", "min": 1},
                {"key": "similarity", "label": tt("gettingstarted.automod.feature.anti_spam.field.similarity"), "type": "number", "default": "75", "min": 1, "max": 100},
                {
                    "key": "action",
                    "label": tt("gettingstarted.automod.field.action"),
                    "type": "string",
                    "default": tt("gettingstarted.automod.feature.anti_spam.field.action.default"),
                    "required": True,
                },
                {"key": "ignore_channels", "label": tt("gettingstarted.automod.field.ignore_channels"), "type": "channel_list", "default": []},
            ],
        },
        {
            "id": "automod_detect",
            "label": tt("gettingstarted.automod.feature.automod_detect.label"),
            "description": tt("gettingstarted.automod.feature.automod_detect.desc"),
            "fields": [
                {"key": "log_channel", "label": tt("gettingstarted.automod.field.notify_channel"), "type": "channel", "required": True},
                {"key": "action", "label": tt("gettingstarted.automod.feature.automod_detect.field.action"), "type": "string", "default": ""},
                {"key": "filter_rule", "label": tt("gettingstarted.automod.feature.automod_detect.field.filter_rule"), "type": "string", "default": ""},
                {"key": "filter_action_type", "label": tt("gettingstarted.automod.feature.automod_detect.field.filter_action_type"), "type": "string", "default": ""},
            ],
        },
        {
            "id": "flagged_user",
            "label": tt("gettingstarted.automod.feature.flagged_user.label"),
            "description": tt("gettingstarted.automod.feature.flagged_user.desc"),
            "fields": [
                {"key": "log_channel", "label": tt("gettingstarted.automod.field.notify_channel"), "type": "channel", "required": True},
                {"key": "action", "label": tt("gettingstarted.automod.field.action"), "type": "string", "default": "", "action_context": "member_join"},
                {
                    "key": "action_source",
                    "label": tt("gettingstarted.automod.feature.flagged_user.field.action_source"),
                    "type": "select",
                    "default": "both",
                    "options": [
                        {"label": tt("gettingstarted.automod.feature.flagged_user.option.both"), "value": "both"},
                        {"label": tt("gettingstarted.automod.feature.flagged_user.option.local"), "value": "local"},
                        {"label": tt("gettingstarted.automod.feature.flagged_user.option.api"), "value": "api"},
                    ],
                },
                {
                    "key": "local_match_mode",
                    "label": tt("gettingstarted.automod.feature.flagged_user.field.local_match_mode"),
                    "type": "select",
                    "default": "active",
                    "options": [
                        {"label": tt("gettingstarted.automod.feature.flagged_user.option.active"), "value": "active"},
                        {"label": tt("gettingstarted.automod.feature.flagged_user.option.history"), "value": "history"},
                    ],
                },
            ],
        },
    ]


def automod_feature_map(*, locale: str | None = None) -> dict:
    return {item["id"]: item for item in automod_feature_schemas(locale=locale)}


def get_automod_panel_setting() -> dict:
    return next(
        setting
        for setting in panel_settings["AutoModerate"]["settings"]
        if setting["database_key"] == "automod"
    )


def get_automod_config(guild_id: int) -> dict:
    value = get_server_config(guild_id, "automod", {})
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def get_automod_feature_data(guild_id: int, feature_id: str) -> dict:
    automod = get_automod_config(guild_id)
    stored = automod.get(feature_id, {})
    stored = copy.deepcopy(stored) if isinstance(stored, dict) else {}
    if feature_id == "flagged_user" and "flagged_user" not in automod:
        legacy_channel = get_server_config(guild_id, "flagged_user_onjoin_channel")
        if legacy_channel:
            stored.update({"enabled": True, "log_channel": str(legacy_channel)})
    schema = automod_feature_map()[feature_id]
    for field_schema in schema["fields"]:
        if field_schema["key"] not in stored and "default" in field_schema:
            stored[field_schema["key"]] = copy.deepcopy(field_schema["default"])
    stored.setdefault("enabled", False)
    return stored


async def save_automod_feature(
    session: GettingStartedSession,
    interaction: discord.Interaction,
    module_name: str,
    feature_id: str,
    feature_data: dict,
) -> bool:
    automod = get_automod_config(session.guild.id)
    automod[feature_id] = feature_data
    return await session.save(
        interaction,
        module_name,
        get_automod_panel_setting(),
        automod,
    )


def format_automod_field(guild: discord.Guild, field_schema: dict, value: Any) -> str:
    if value is None or value == "":
        return t("gettingstarted.value.not_set")
    field_type = field_schema.get("type")
    if field_type == "boolean":
        return t("common.state.yes") if bool(value) else t("common.state.no")
    if field_type == "channel":
        channel = guild.get_channel(int(value)) if str(value).isdigit() else None
        return channel.mention if channel else t("gettingstarted.value.unknown_channel", value=value)
    if field_type == "channel_list":
        values = value if isinstance(value, list) else []
        mentions = []
        for channel_id in values[:15]:
            channel = guild.get_channel(int(channel_id)) if str(channel_id).isdigit() else None
            mentions.append(channel.mention if channel else str(channel_id))
        if len(values) > 15:
            mentions.append(t("gettingstarted.value.more_items", count=len(values)))
        return i18n.join_list(mentions) if mentions else t("common.state.none")
    if field_type == "select":
        for option in field_schema.get("options", []):
            if str(option.get("value")) == str(value):
                return str(option.get("label", value))
    return truncate(value, 900)


class AutoModerateFeatureSelect(discord.ui.Select):
    def __init__(self, parent: "AutoModerateManagerView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(placeholder=t("gettingstarted.automod.select_feature_ph"), options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        target = AutoModerateFeatureView(
            self.parent_view.session,
            self.parent_view.module_name,
            self.values[0],
        )
        await self.parent_view.session.render(interaction, embed=target.build_embed(), view=target)


class AutoModerateManagerView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str, page: int = 0):
        super().__init__(session)
        self.module_name = module_name
        current, self.page, self.total_pages = paginate(automod_feature_schemas(), page)
        automod = get_automod_config(session.guild.id)
        options = []
        for feature in current:
            feature_data = (
                get_automod_feature_data(session.guild.id, feature["id"])
                if feature["id"] == "flagged_user"
                else automod.get(feature["id"], {})
            )
            enabled = bool(feature_data.get("enabled", False))
            options.append(
                discord.SelectOption(
                    label=feature["label"],
                    value=feature["id"],
                    description=truncate(feature["description"], 100),
                    emoji="✅" if enabled else "⏸️",
                )
            )
        self.add_item(AutoModerateFeatureSelect(self, options))

        previous = discord.ui.Button(label=t("common.btn.prev"), style=discord.ButtonStyle.secondary, row=1)
        previous.disabled = self.page == 0
        previous.callback = self.previous_page
        self.add_item(previous)
        next_button = discord.ui.Button(label=t("common.btn.next"), style=discord.ButtonStyle.secondary, row=1)
        next_button.disabled = self.page >= self.total_pages - 1
        next_button.callback = self.next_page
        self.add_item(next_button)
        back = discord.ui.Button(label=t("common.btn.back"), style=discord.ButtonStyle.secondary, row=1)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self) -> discord.Embed:
        automod = get_automod_config(self.session.guild.id)
        schemas = automod_feature_schemas()
        enabled = sum(
            bool(
                (
                    get_automod_feature_data(self.session.guild.id, item["id"])
                    if item["id"] == "flagged_user"
                    else automod.get(item["id"], {})
                ).get("enabled", False)
            )
            for item in schemas
        )
        embed = discord.Embed(
            title=t("gettingstarted.automod.manager_title"),
            description=t("gettingstarted.automod.manager_desc"),
            color=discord.Color.blurple(),
        )
        embed.add_field(name=t("gettingstarted.automod.field.enabled_count"), value=f"{enabled} / {len(schemas)}", inline=True)
        embed.set_footer(text=t("gettingstarted.automod.feature_page_footer", page=self.page + 1, total=self.total_pages))
        return embed

    async def previous_page(self, interaction: discord.Interaction):
        target = AutoModerateManagerView(self.session, self.module_name, self.page - 1)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def next_page(self, interaction: discord.Interaction):
        target = AutoModerateManagerView(self.session, self.module_name, self.page + 1)
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def back(self, interaction: discord.Interaction):
        target = ModuleSettingsView(self.session, self.module_name)
        await self.session.render(
            interaction,
            embed=ModuleSettingsView.build_embed(self.session, self.module_name),
            view=target,
        )


class AutoModerateFieldSelect(discord.ui.Select):
    def __init__(self, parent: "AutoModerateFeatureView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(placeholder=t("gettingstarted.automod.select_field_ph"), options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        field_schema = next(
            item for item in self.parent_view.feature_schema["fields"] if item["key"] == self.values[0]
        )
        if field_schema.get("type") == "channel_list":
            target = AutoModerateChannelListView(
                self.parent_view.session,
                self.parent_view.module_name,
                self.parent_view.feature_id,
                field_schema,
            )
        else:
            target = AutoModerateFieldView(
                self.parent_view.session,
                self.parent_view.module_name,
                self.parent_view.feature_id,
                field_schema,
            )
        await self.parent_view.session.render(interaction, embed=target.build_embed(), view=target)


class AutoModerateFeatureView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str, feature_id: str):
        super().__init__(session)
        self.module_name = module_name
        self.feature_id = feature_id
        self.feature_schema = automod_feature_map()[feature_id]
        options = [
            discord.SelectOption(
                label=truncate(field_schema["label"], 100),
                value=field_schema["key"],
                description=field_schema.get("type", "string"),
            )
            for field_schema in self.feature_schema["fields"]
        ]
        self.add_item(AutoModerateFieldSelect(self, options))

        enable = discord.ui.Button(label=t("common.state.enabled"), style=discord.ButtonStyle.success, row=1)
        enable.callback = self.enable
        self.add_item(enable)
        disable = discord.ui.Button(label=t("common.state.disabled"), style=discord.ButtonStyle.danger, row=1)
        disable.callback = self.disable
        self.add_item(disable)
        back = discord.ui.Button(label=t("gettingstarted.automod.btn.back_to_feature"), style=discord.ButtonStyle.secondary, row=1)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self) -> discord.Embed:
        data = get_automod_feature_data(self.session.guild.id, self.feature_id)
        embed = discord.Embed(
            title=self.feature_schema["label"],
            description=self.feature_schema["description"],
            color=discord.Color.green() if data.get("enabled") else discord.Color.blurple(),
        )
        embed.add_field(name=t("gettingstarted.field.status"), value=t("common.state.enabled") if data.get("enabled") else t("common.state.disabled"), inline=False)
        for field_schema in self.feature_schema["fields"]:
            embed.add_field(
                name=field_schema["label"],
                value=format_automod_field(
                    self.session.guild,
                    field_schema,
                    data.get(field_schema["key"]),
                ),
                inline=False,
            )
        return embed

    async def enable(self, interaction: discord.Interaction):
        data = get_automod_feature_data(self.session.guild.id, self.feature_id)
        missing = [
            field_schema["label"]
            for field_schema in self.feature_schema["fields"]
            if field_schema.get("required") and data.get(field_schema["key"]) in (None, "", [])
        ]
        if missing:
            await interaction.response.send_message(
                t("gettingstarted.automod.err.missing_required", fields=i18n.join_list(missing)),
                ephemeral=True,
            )
            return
        data["enabled"] = True
        if await save_automod_feature(self.session, interaction, self.module_name, self.feature_id, data):
            target = AutoModerateFeatureView(self.session, self.module_name, self.feature_id)
            await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def disable(self, interaction: discord.Interaction):
        data = get_automod_feature_data(self.session.guild.id, self.feature_id)
        data["enabled"] = False
        if await save_automod_feature(self.session, interaction, self.module_name, self.feature_id, data):
            target = AutoModerateFeatureView(self.session, self.module_name, self.feature_id)
            await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def back(self, interaction: discord.Interaction):
        target = AutoModerateManagerView(self.session, self.module_name)
        await self.session.render(interaction, embed=target.build_embed(), view=target)


class AutoModerateFieldModal(discord.ui.Modal):
    def __init__(self, parent: "AutoModerateFieldView"):
        super().__init__(title=truncate(parent.field_schema["label"], 45))
        self.parent_view = parent
        data = get_automod_feature_data(parent.session.guild.id, parent.feature_id)
        current = data.get(parent.field_schema["key"], "")
        self.value_input = discord.ui.TextInput(
            label=t("gettingstarted.setting.value_label"),
            default=truncate(current, 4000),
            required=False,
            max_length=4000,
            placeholder=t("gettingstarted.automod.field_value_ph"),
            style=discord.TextStyle.paragraph if parent.field_schema["key"] == "action" else discord.TextStyle.short,
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.value_input.value.strip()
        field_schema = self.parent_view.field_schema
        if field_schema.get("key") == "action" and Moderate is not None:
            analyzer = (
                Moderate.analyze_member_join_action
                if field_schema.get("action_context") == "member_join"
                else Moderate.analyze_action_string
            )
            analysis = analyzer(raw, self.parent_view.session.guild.id)
            if not analysis["valid"]:
                await interaction.response.send_message(analysis["error"], ephemeral=True)
                return
            if analysis["requires_confirmation"]:
                target = AutoModerateActionConfirmView(self.parent_view, analysis)
                await self.parent_view.session.render(
                    interaction,
                    embed=target.build_embed(),
                    view=target,
                )
                return
            await self.parent_view.save_value(interaction, analysis["normalized"])
            return
        value: Any = raw
        if field_schema.get("type") == "number" and raw:
            try:
                number = int(raw)
            except ValueError:
                await interaction.response.send_message(t("gettingstarted.err.invalid_integer"), ephemeral=True)
                return
            if field_schema.get("min") is not None and number < field_schema["min"]:
                await interaction.response.send_message(
                    t("gettingstarted.err.value_too_small", minimum=field_schema["min"]),
                    ephemeral=True,
                )
                return
            if field_schema.get("max") is not None and number > field_schema["max"]:
                await interaction.response.send_message(
                    t("gettingstarted.err.value_too_large", maximum=field_schema["max"]),
                    ephemeral=True,
                )
                return
            value = str(number)
        await self.parent_view.save_value(interaction, value if raw else None)


class AutoModerateValueSelect(discord.ui.Select):
    def __init__(self, parent: "AutoModerateFieldView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(placeholder=t("gettingstarted.setting.select_value_ph"), options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.save_value(interaction, self.values[0])


class AutoModerateChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "AutoModerateFieldView"):
        self.parent_view = parent
        super().__init__(
            placeholder=t("gettingstarted.setting.select_channel_ph"),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.save_value(interaction, str(interaction.data["values"][0]))


class AutoModerateActionPresetSelect(discord.ui.Select):
    def __init__(self, parent: "AutoModerateFieldView"):
        self.parent_view = parent
        field_schema = getattr(parent, "field_schema", {})
        suggestions = Moderate._action_input_suggestions() if Moderate is not None else []
        if field_schema.get("action_context") == "member_join" and Moderate is not None:
            suggestions = [
                (label, value)
                for label, value in suggestions
                if Moderate.analyze_member_join_action(value, parent.session.guild.id)["valid"]
            ]
        options = [
            discord.SelectOption(label=label, value=value)
            for label, value in suggestions
        ]
        super().__init__(placeholder=t("gettingstarted.select_preset_ph"), options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        analyzer = (
            Moderate.analyze_member_join_action
            if self.parent_view.field_schema.get("action_context") == "member_join"
            else Moderate.analyze_action_string
        )
        analysis = analyzer(self.values[0], self.parent_view.session.guild.id)
        if not analysis["valid"]:
            await interaction.response.send_message(analysis["error"], ephemeral=True)
            return
        await self.parent_view.save_value(interaction, analysis["normalized"])


class AutoModerateActionConfirmView(SetupView):
    def __init__(self, parent_view: "AutoModerateFieldView", analysis: dict):
        super().__init__(parent_view.session, timeout=120)
        self.parent_view = parent_view
        self.analysis = analysis
        confirm = discord.ui.Button(label=t("gettingstarted.antibeast.btn.confirm_action"), style=discord.ButtonStyle.success)
        confirm.callback = self.confirm
        self.add_item(confirm)
        retry = discord.ui.Button(label=t("gettingstarted.antibeast.btn.retry_action"), style=discord.ButtonStyle.secondary)
        retry.callback = self.retry
        self.add_item(retry)
        cancel = discord.ui.Button(label=t("common.btn.cancel"), style=discord.ButtonStyle.danger)
        cancel.callback = self.cancel
        self.add_item(cancel)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=t("moderate.confirm_your_intent_title"),
            description=self.analysis.get("confirmation"),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name=t("gettingstarted.automod.field.will_save_as"),
            value=f"```text\n{self.analysis['normalized']}\n```",
            inline=False,
        )
        embed.add_field(
            name=t("gettingstarted.field.execution_preview"),
            value="\n".join(
                f"{index}. {line}"
                for index, line in enumerate(self.analysis.get("preview", []), 1)
            ),
            inline=False,
        )
        return embed

    async def confirm(self, interaction: discord.Interaction):
        await self.parent_view.save_value(interaction, self.analysis["normalized"])

    async def retry(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AutoModerateFieldModal(self.parent_view))

    async def cancel(self, interaction: discord.Interaction):
        target = AutoModerateFieldView(
            self.parent_view.session,
            self.parent_view.module_name,
            self.parent_view.feature_id,
            self.parent_view.field_schema,
        )
        await self.parent_view.session.render(interaction, embed=target.build_embed(), view=target)


class AutoModerateFieldView(SetupView):
    def __init__(
        self,
        session: GettingStartedSession,
        module_name: str,
        feature_id: str,
        field_schema: dict,
        *,
        saved: bool = False,
    ):
        super().__init__(session)
        self.module_name = module_name
        self.feature_id = feature_id
        self.field_schema = field_schema
        self.saved = saved
        field_type = field_schema.get("type", "string")
        action_field = field_schema.get("key") == "action" and Moderate is not None
        if action_field:
            self.add_item(AutoModerateActionPresetSelect(self))
            edit = discord.ui.Button(label=t("gettingstarted.automod.btn.custom_input"), style=discord.ButtonStyle.primary, row=1)
            edit.callback = self.edit
            self.add_item(edit)
        elif field_type == "boolean":
            yes = discord.ui.Button(label=t("common.state.yes"), style=discord.ButtonStyle.success, row=0)
            yes.callback = self.set_true
            self.add_item(yes)
            no = discord.ui.Button(label=t("common.state.no"), style=discord.ButtonStyle.danger, row=0)
            no.callback = self.set_false
            self.add_item(no)
        elif field_type == "select":
            options = [
                discord.SelectOption(label=item["label"], value=str(item["value"]))
                for item in field_schema.get("options", [])
            ]
            self.add_item(AutoModerateValueSelect(self, options))
        elif field_type == "channel":
            self.add_item(AutoModerateChannelSelect(self))
        else:
            edit = discord.ui.Button(label=t("gettingstarted.btn.edit"), style=discord.ButtonStyle.primary, row=0)
            edit.callback = self.edit
            self.add_item(edit)

        footer_row = 2 if action_field else 1
        clear = discord.ui.Button(label=t("gettingstarted.btn.clear"), style=discord.ButtonStyle.danger, row=footer_row)
        clear.callback = self.clear
        self.add_item(clear)
        back = discord.ui.Button(label=t("gettingstarted.automod.btn.back_to_feature"), style=discord.ButtonStyle.secondary, row=footer_row)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self) -> discord.Embed:
        data = get_automod_feature_data(self.session.guild.id, self.feature_id)
        value = data.get(self.field_schema["key"])
        embed = discord.Embed(
            title=t("gettingstarted.automod.action_saved_title") if self.saved and self.field_schema["key"] == "action" else self.field_schema["label"],
            description=(t("gettingstarted.automod.setting_saved") + "\n\n" if self.saved else "")
            + t("gettingstarted.automod.current_setting_prefix")
            + format_automod_field(self.session.guild, self.field_schema, value),
            color=discord.Color.green() if self.saved else discord.Color.blurple(),
        )
        if self.field_schema["key"] == "action" and value and Moderate is not None:
            analyzer = (
                Moderate.analyze_member_join_action
                if self.field_schema.get("action_context") == "member_join"
                else Moderate.analyze_action_string
            )
            analysis = analyzer(str(value), self.session.guild.id)
            if analysis["valid"]:
                embed.add_field(
                    name=t("gettingstarted.field.execution_preview"),
                    value="\n".join(
                        f"{index}. {line}"
                        for index, line in enumerate(analysis.get("preview", []), 1)
                    ),
                    inline=False,
                )
            else:
                embed.add_field(name=t("gettingstarted.automod.syntax_issue"), value=analysis["error"], inline=False)
        return embed

    async def save_value(self, interaction: discord.Interaction, value: Any):
        data = get_automod_feature_data(self.session.guild.id, self.feature_id)
        if value is None:
            data.pop(self.field_schema["key"], None)
        else:
            data[self.field_schema["key"]] = value
        if await save_automod_feature(self.session, interaction, self.module_name, self.feature_id, data):
            target = AutoModerateFieldView(
                self.session,
                self.module_name,
                self.feature_id,
                self.field_schema,
                saved=self.field_schema["key"] == "action",
            )
            await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def set_true(self, interaction: discord.Interaction):
        await self.save_value(interaction, True)

    async def set_false(self, interaction: discord.Interaction):
        await self.save_value(interaction, False)

    async def edit(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AutoModerateFieldModal(self))

    async def clear(self, interaction: discord.Interaction):
        await self.save_value(interaction, None)

    async def back(self, interaction: discord.Interaction):
        target = AutoModerateFeatureView(self.session, self.module_name, self.feature_id)
        await self.session.render(interaction, embed=target.build_embed(), view=target)


class AutoModerateChannelListAdd(discord.ui.ChannelSelect):
    def __init__(self, parent: "AutoModerateChannelListView"):
        self.parent_view = parent
        super().__init__(
            placeholder=t("gettingstarted.automod.add_ignore_channel_ph"),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=25,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        values = [int(value) for value in interaction.data.get("values", []) if str(value).isdigit()]
        await self.parent_view.add_values(interaction, values)


class AutoModerateChannelListRemove(discord.ui.Select):
    def __init__(self, parent: "AutoModerateChannelListView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(
            placeholder=t("gettingstarted.automod.remove_ignore_channel_ph"),
            options=options,
            min_values=1,
            max_values=len(options),
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        values = [int(value) for value in self.values if str(value).isdigit()]
        await self.parent_view.remove_values(interaction, values)


class AutoModerateChannelListView(SetupView):
    def __init__(
        self,
        session: GettingStartedSession,
        module_name: str,
        feature_id: str,
        field_schema: dict,
        page: int = 0,
    ):
        super().__init__(session)
        self.module_name = module_name
        self.feature_id = feature_id
        self.field_schema = field_schema
        data = get_automod_feature_data(session.guild.id, feature_id)
        self.values = [int(value) for value in data.get(field_schema["key"], []) if str(value).isdigit()]
        current, self.page, self.total_pages = paginate(self.values, page)
        self.add_item(AutoModerateChannelListAdd(self))
        options = []
        for channel_id in current:
            channel = session.guild.get_channel(channel_id)
            options.append(
                discord.SelectOption(
                    label=truncate(channel.name if channel else channel_id, 100),
                    value=str(channel_id),
                )
            )
        if options:
            self.add_item(AutoModerateChannelListRemove(self, options))
        previous = discord.ui.Button(label=t("common.btn.prev"), style=discord.ButtonStyle.secondary, row=2)
        previous.disabled = self.page == 0
        previous.callback = self.previous_page
        self.add_item(previous)
        next_button = discord.ui.Button(label=t("common.btn.next"), style=discord.ButtonStyle.secondary, row=2)
        next_button.disabled = self.page >= self.total_pages - 1
        next_button.callback = self.next_page
        self.add_item(next_button)
        clear = discord.ui.Button(label=t("gettingstarted.btn.clear_all"), style=discord.ButtonStyle.danger, row=2)
        clear.disabled = not self.values
        clear.callback = self.clear
        self.add_item(clear)
        back = discord.ui.Button(label=t("gettingstarted.automod.btn.back_to_feature"), style=discord.ButtonStyle.secondary, row=2)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self) -> discord.Embed:
        return discord.Embed(
            title=self.field_schema["label"],
            description=format_automod_field(
                self.session.guild,
                self.field_schema,
                self.values,
            ),
            color=discord.Color.blurple(),
        ).set_footer(text=t("gettingstarted.list.page_footer", page=self.page + 1, total=self.total_pages))

    async def save_values(self, interaction: discord.Interaction, values: list[int]):
        data = get_automod_feature_data(self.session.guild.id, self.feature_id)
        data[self.field_schema["key"]] = list(dict.fromkeys(values))
        if await save_automod_feature(self.session, interaction, self.module_name, self.feature_id, data):
            target = AutoModerateChannelListView(
                self.session,
                self.module_name,
                self.feature_id,
                self.field_schema,
                self.page,
            )
            await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def add_values(self, interaction: discord.Interaction, values: list[int]):
        await self.save_values(interaction, self.values + values)

    async def remove_values(self, interaction: discord.Interaction, values: list[int]):
        removed = set(values)
        await self.save_values(interaction, [value for value in self.values if value not in removed])

    async def clear(self, interaction: discord.Interaction):
        await self.save_values(interaction, [])

    async def previous_page(self, interaction: discord.Interaction):
        target = AutoModerateChannelListView(
            self.session,
            self.module_name,
            self.feature_id,
            self.field_schema,
            self.page - 1,
        )
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def next_page(self, interaction: discord.Interaction):
        target = AutoModerateChannelListView(
            self.session,
            self.module_name,
            self.feature_id,
            self.field_schema,
            self.page + 1,
        )
        await self.session.render(interaction, embed=target.build_embed(), view=target)

    async def back(self, interaction: discord.Interaction):
        target = AutoModerateFeatureView(self.session, self.module_name, self.feature_id)
        await self.session.render(interaction, embed=target.build_embed(), view=target)


def default_webverify_config() -> dict:
    return {
        "enabled": True,
        "captcha_type": "turnstile",
        "unverified_role_id": None,
        "autorole_enabled": False,
        "autorole_trigger": "always",
        "min_age": 7,
        "notify": {
            "type": "dm",
            "channel_id": None,
            "title": t("gettingstarted.webverify.default.notify_title"),
            "message": t("gettingstarted.webverify.default.notify_message"),
        },
        "webverify_country_alert": {
            "enabled": False,
            "mode": "blacklist",
            "countries": [],
            "channel_id": None,
        },
    }


def load_webverify_config(guild_id: int) -> dict:
    base = default_webverify_config()
    stored = get_server_config(guild_id, "webverify_config", {})
    if not isinstance(stored, dict):
        return base
    for key in ("enabled", "captcha_type", "unverified_role_id", "autorole_enabled", "autorole_trigger", "min_age"):
        if key in stored:
            base[key] = copy.deepcopy(stored[key])
    if isinstance(stored.get("notify"), dict):
        base["notify"].update(copy.deepcopy(stored["notify"]))
    if isinstance(stored.get("webverify_country_alert"), dict):
        base["webverify_country_alert"].update(copy.deepcopy(stored["webverify_country_alert"]))
    return base


def get_webverify_panel_setting() -> dict:
    return next(
        setting
        for setting in panel_settings["ServerWebVerify"]["settings"]
        if setting["database_key"] == "webverify_config"
    )


class WebVerifyMinAgeModal(i18n.I18nModal, title=i18n.K("gettingstarted.webverify.modal.min_age_title")):
    def __init__(self, parent: "WebVerifySetupView"):
        super().__init__()
        self.parent_view = parent
        self.age_input = discord.ui.TextInput(
            label=t("gettingstarted.webverify.field.min_age_label"),
            default=str(parent.draft.get("min_age", 7)),
            required=True,
            max_length=5,
        )
        self.add_item(self.age_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            age = int(self.age_input.value.strip())
        except ValueError:
            await interaction.response.send_message(t("gettingstarted.webverify.err.invalid_days"), ephemeral=True)
            return
        if age < 0:
            await interaction.response.send_message(t("gettingstarted.webverify.err.min_age_negative"), ephemeral=True)
            return
        self.parent_view.draft["min_age"] = age
        await self.parent_view.refresh(interaction)


class WebVerifyNotificationModal(i18n.I18nModal, title=i18n.K("gettingstarted.webverify.modal.notify_title")):
    def __init__(self, parent: "WebVerifySetupView"):
        super().__init__()
        self.parent_view = parent
        notify = parent.draft["notify"]
        self.title_input = discord.ui.TextInput(
            label=t("gettingstarted.webverify.field.notify_title_label"),
            default=truncate(notify.get("title", t("gettingstarted.webverify.default.notify_title")), 256),
            required=True,
            max_length=256,
        )
        self.message_input = discord.ui.TextInput(
            label=t("gettingstarted.webverify.field.notify_message_label"),
            default=truncate(notify.get("message", t("gettingstarted.webverify.default.notify_message")), 4000),
            required=True,
            max_length=4000,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.title_input)
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.draft["notify"]["title"] = self.title_input.value.strip()
        self.parent_view.draft["notify"]["message"] = self.message_input.value.strip()
        await self.parent_view.refresh(interaction)


class WebVerifyCountriesModal(i18n.I18nModal, title=i18n.K("gettingstarted.webverify.modal.countries_title")):
    def __init__(self, parent: "WebVerifySetupView"):
        super().__init__()
        self.parent_view = parent
        countries = parent.draft["webverify_country_alert"].get("countries", [])
        self.countries_input = discord.ui.TextInput(
            label=t("gettingstarted.webverify.field.country_codes_label"),
            placeholder="TW, JP, US",
            default=", ".join(countries),
            required=False,
            max_length=1000,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.countries_input)

    async def on_submit(self, interaction: discord.Interaction):
        values = [
            token.strip().upper()
            for token in re.split(r"[,，\s]+", self.countries_input.value)
            if token.strip()
        ]
        invalid = [value for value in values if not re.fullmatch(r"[A-Z]{2}", value)]
        if invalid:
            await interaction.response.send_message(
                t("gettingstarted.webverify.err.invalid_country_code", codes=i18n.join_list(invalid[:10])),
                ephemeral=True,
            )
            return
        self.parent_view.draft["webverify_country_alert"]["countries"] = list(dict.fromkeys(values))
        await self.parent_view.refresh(interaction)


class WebVerifyRoleCreationModal(i18n.I18nModal, title=i18n.K("gettingstarted.webverify.modal.create_role_title")):
    def __init__(self, parent: "WebVerifySetupView"):
        super().__init__()
        self.parent_view = parent
        self.name_input = discord.ui.TextInput(
            label=t("gettingstarted.webverify.field.role_name_label"),
            default=t("gettingstarted.webverify.default.unverified_role_name"),
            required=True,
            max_length=100,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        guild = self.parent_view.session.guild
        bot_member = guild.me
        permissions = bot_member.guild_permissions if bot_member else None
        if permissions is None or not permissions.manage_roles or not permissions.manage_channels:
            await interaction.response.send_message(
                t("gettingstarted.webverify.err.create_role_permission"),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            role = await guild.create_role(
                name=self.name_input.value.strip(),
                reason=t("gettingstarted.webverify.audit.create_role_reason", user=interaction.user),
            )
            for channel in guild.text_channels:
                try:
                    await channel.set_permissions(
                        role,
                        send_messages=False,
                        create_public_threads=False,
                        create_private_threads=False,
                        reason=t("gettingstarted.webverify.audit.role_permission_reason"),
                    )
                except (discord.Forbidden, discord.HTTPException):
                    continue
        except (discord.Forbidden, discord.HTTPException) as error:
            await interaction.followup.send(t("gettingstarted.webverify.err.create_role_failed", error=error), ephemeral=True)
            return

        self.parent_view.draft["unverified_role_id"] = role.id
        await self.parent_view.refresh(interaction)


class WebVerifySetupView(SetupView):
    def __init__(
        self,
        session: GettingStartedSession,
        module_name: str,
        *,
        draft: dict | None = None,
        step: int = 1,
    ):
        super().__init__(session)
        self.module_name = module_name
        self.draft = copy.deepcopy(draft) if draft is not None else load_webverify_config(session.guild.id)
        self.step = max(1, min(step, 6))
        self.rebuild_components()

    def rebuild_components(self):
        self.clear_items()
        if self.step == 1:
            captcha = discord.ui.Select(
                placeholder=t("gettingstarted.webverify.select_captcha_ph"),
                options=[
                    discord.SelectOption(label=t("gettingstarted.webverify.captcha.none"), value="none", default=self.draft["captcha_type"] == "none"),
                    discord.SelectOption(label="Cloudflare Turnstile", value="turnstile", default=self.draft["captcha_type"] == "turnstile"),
                    discord.SelectOption(label="Google reCAPTCHA", value="recaptcha", default=self.draft["captcha_type"] == "recaptcha"),
                ],
                row=0,
            )
            captcha.callback = self.select_captcha
            self.add_item(captcha)
            toggle = discord.ui.Button(
                label=t("gettingstarted.webverify.btn.feature_state", state=t("common.state.enabled") if self.draft["enabled"] else t("common.state.disabled")),
                style=discord.ButtonStyle.success if self.draft["enabled"] else discord.ButtonStyle.danger,
                row=1,
            )
            toggle.callback = self.toggle_enabled
            self.add_item(toggle)
            self.add_navigation(next_step=True, row=2)
        elif self.step == 2:
            role_select = discord.ui.RoleSelect(
                placeholder=t("gettingstarted.webverify.select_role_ph"),
                min_values=1,
                max_values=1,
                row=0,
            )
            role_select.callback = self.select_role
            self.add_item(role_select)
            create = discord.ui.Button(label=t("gettingstarted.webverify.btn.auto_create_role"), style=discord.ButtonStyle.success, row=1)
            create.callback = self.create_role
            self.add_item(create)
            self.add_navigation(previous_step=True, next_step=True, row=2)
        elif self.step == 3:
            toggle = discord.ui.Button(
                label=t("gettingstarted.webverify.btn.autorole_state", state=t("common.state.enabled") if self.draft["autorole_enabled"] else t("common.state.disabled")),
                style=discord.ButtonStyle.success if self.draft["autorole_enabled"] else discord.ButtonStyle.secondary,
                row=0,
            )
            toggle.callback = self.toggle_autorole
            self.add_item(toggle)
            trigger_values = set(str(self.draft.get("autorole_trigger", "always")).split("+"))
            trigger = discord.ui.Select(
                placeholder=t("gettingstarted.webverify.select_autorole_trigger_ph"),
                min_values=1,
                max_values=5,
                options=[
                    discord.SelectOption(label=t("gettingstarted.webverify.trigger.always"), value="always", default="always" in trigger_values),
                    discord.SelectOption(label=t("gettingstarted.webverify.trigger.age_check"), value="age_check", default="age_check" in trigger_values),
                    discord.SelectOption(label=t("gettingstarted.webverify.trigger.no_history"), value="no_history", default="no_history" in trigger_values),
                    discord.SelectOption(label=t("gettingstarted.webverify.trigger.has_flagged_history"), value="has_flagged_history", default="has_flagged_history" in trigger_values),
                    discord.SelectOption(label=t("gettingstarted.webverify.trigger.left_guild_before"), value="left_guild_before", default="left_guild_before" in trigger_values),
                ],
                row=1,
            )
            trigger.callback = self.select_autorole_trigger
            self.add_item(trigger)
            age = discord.ui.Button(label=t("gettingstarted.webverify.btn.edit_min_age"), style=discord.ButtonStyle.primary, row=2)
            age.callback = self.edit_min_age
            self.add_item(age)
            self.add_navigation(previous_step=True, next_step=True, row=3)
        elif self.step == 4:
            notify_type = self.draft["notify"].get("type", "dm")
            notify_select = discord.ui.Select(
                placeholder=t("gettingstarted.webverify.select_notify_type_ph"),
                options=[
                    discord.SelectOption(label=t("gettingstarted.webverify.notify_type.dm"), value="dm", default=notify_type == "dm"),
                    discord.SelectOption(label=t("gettingstarted.webverify.notify_type.channel"), value="channel", default=notify_type == "channel"),
                    discord.SelectOption(label=t("gettingstarted.webverify.notify_type.both"), value="both", default=notify_type == "both"),
                ],
                row=0,
            )
            notify_select.callback = self.select_notify_type
            self.add_item(notify_select)
            if notify_type in ("channel", "both"):
                channel_select = discord.ui.ChannelSelect(
                    placeholder=t("gettingstarted.webverify.select_notify_channel_ph"),
                    channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                    min_values=1,
                    max_values=1,
                    row=1,
                )
                channel_select.callback = self.select_notify_channel
                self.add_item(channel_select)
            edit_text = discord.ui.Button(label=t("gettingstarted.webverify.btn.edit_notification"), style=discord.ButtonStyle.primary, row=2)
            edit_text.callback = self.edit_notification
            self.add_item(edit_text)
            self.add_navigation(previous_step=True, next_step=True, row=3)
        elif self.step == 5:
            country = self.draft["webverify_country_alert"]
            toggle = discord.ui.Button(
                label=t("gettingstarted.webverify.btn.country_alert_state", state=t("common.state.enabled") if country["enabled"] else t("common.state.disabled")),
                style=discord.ButtonStyle.success if country["enabled"] else discord.ButtonStyle.secondary,
                row=0,
            )
            toggle.callback = self.toggle_country_alert
            self.add_item(toggle)
            mode = discord.ui.Select(
                placeholder=t("gettingstarted.webverify.select_country_mode_ph"),
                options=[
                    discord.SelectOption(label=t("gettingstarted.webverify.mode.blacklist"), value="blacklist", default=country.get("mode") == "blacklist"),
                    discord.SelectOption(label=t("gettingstarted.webverify.mode.whitelist"), value="whitelist", default=country.get("mode") == "whitelist"),
                ],
                row=1,
            )
            mode.callback = self.select_country_mode
            self.add_item(mode)
            if country.get("enabled"):
                channel_select = discord.ui.ChannelSelect(
                    placeholder=t("gettingstarted.webverify.select_country_channel_ph"),
                    channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                    min_values=1,
                    max_values=1,
                    row=2,
                )
                channel_select.callback = self.select_country_channel
                self.add_item(channel_select)
            countries = discord.ui.Button(label=t("gettingstarted.webverify.btn.edit_countries"), style=discord.ButtonStyle.primary, row=3)
            countries.callback = self.edit_countries
            self.add_item(countries)
            self.add_navigation(previous_step=True, next_step=True, row=4)
        else:
            save = discord.ui.Button(label=t("gettingstarted.webverify.btn.save_only"), style=discord.ButtonStyle.success, row=0)
            save.callback = self.save_only
            self.add_item(save)
            notify_type = self.draft["notify"].get("type", "dm")
            save_send = discord.ui.Button(label=t("gettingstarted.webverify.btn.save_and_send"), style=discord.ButtonStyle.primary, row=0)
            save_send.disabled = notify_type not in ("channel", "both")
            save_send.callback = self.save_and_send
            self.add_item(save_send)
            self.add_navigation(previous_step=True, row=1)

    def add_navigation(
        self,
        *,
        previous_step: bool = False,
        next_step: bool = False,
        row: int,
    ):
        if previous_step:
            previous = discord.ui.Button(label=t("common.btn.prev_step"), style=discord.ButtonStyle.secondary, row=row)
            previous.callback = self.previous_step
            self.add_item(previous)
        if next_step:
            next_button = discord.ui.Button(label=t("common.btn.next_step"), style=discord.ButtonStyle.primary, row=row)
            next_button.callback = self.next_step
            self.add_item(next_button)
        cancel = discord.ui.Button(label=t("common.btn.cancel"), style=discord.ButtonStyle.danger, row=row)
        cancel.callback = self.cancel
        self.add_item(cancel)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=t("gettingstarted.webverify.setup_title", step=self.step, total=6),
            color=discord.Color.blurple(),
        )
        if self.step == 1:
            embed.description = t("gettingstarted.webverify.step1.desc")
            embed.add_field(name=t("gettingstarted.webverify.field.feature"), value=t("common.state.enabled") if self.draft["enabled"] else t("common.state.disabled"), inline=True)
            embed.add_field(name="CAPTCHA", value=self.draft["captcha_type"], inline=True)
        elif self.step == 2:
            embed.description = t("gettingstarted.webverify.step2.desc")
            role_id = self.draft.get("unverified_role_id")
            role = self.session.guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() else None
            embed.add_field(name=t("gettingstarted.webverify.field.unverified_role"), value=role.mention if role else t("common.state.unset"), inline=False)
        elif self.step == 3:
            embed.description = t("gettingstarted.webverify.step3.desc")
            embed.add_field(name=t("gettingstarted.webverify.field.autorole"), value=t("common.state.enabled") if self.draft["autorole_enabled"] else t("common.state.disabled"), inline=True)
            embed.add_field(name=t("gettingstarted.webverify.field.trigger"), value=self.draft.get("autorole_trigger", "always"), inline=True)
            embed.add_field(name=t("gettingstarted.webverify.field.min_age"), value=i18n.tn("common.unit.days", self.draft.get("min_age", 7)), inline=True)
        elif self.step == 4:
            notify = self.draft["notify"]
            embed.description = t("gettingstarted.webverify.step4.desc")
            embed.add_field(name=t("gettingstarted.webverify.field.notify_type"), value=notify.get("type", "dm"), inline=True)
            channel_id = notify.get("channel_id")
            channel = self.session.guild.get_channel(int(channel_id)) if channel_id and str(channel_id).isdigit() else None
            embed.add_field(name=t("gettingstarted.webverify.field.notify_channel"), value=channel.mention if channel else t("common.state.unset"), inline=True)
            embed.add_field(name=t("gettingstarted.webverify.field.title"), value=notify.get("title", t("gettingstarted.webverify.default.notify_title")), inline=False)
            embed.add_field(name=t("gettingstarted.webverify.field.message"), value=truncate(notify.get("message", ""), 1000), inline=False)
        elif self.step == 5:
            country = self.draft["webverify_country_alert"]
            embed.description = t("gettingstarted.webverify.step5.desc")
            embed.add_field(name=t("gettingstarted.webverify.field.country_alert"), value=t("common.state.enabled") if country.get("enabled") else t("common.state.disabled"), inline=True)
            embed.add_field(name=t("gettingstarted.webverify.field.mode"), value=country.get("mode", "blacklist"), inline=True)
            embed.add_field(name=t("gettingstarted.webverify.field.countries"), value=", ".join(country.get("countries", [])) or t("common.state.unset"), inline=False)
            channel_id = country.get("channel_id")
            channel = self.session.guild.get_channel(int(channel_id)) if channel_id and str(channel_id).isdigit() else None
            embed.add_field(name=t("gettingstarted.webverify.field.alert_channel"), value=channel.mention if channel else t("common.state.unset"), inline=False)
        else:
            notify = self.draft["notify"]
            country = self.draft["webverify_country_alert"]
            role_id = self.draft.get("unverified_role_id")
            role = self.session.guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() else None
            embed.description = t("gettingstarted.webverify.step6.desc")
            embed.add_field(name=t("gettingstarted.webverify.field.feature_captcha"), value=f"{t('common.state.enabled') if self.draft['enabled'] else t('common.state.disabled')} / {self.draft['captcha_type']}", inline=False)
            embed.add_field(name=t("gettingstarted.webverify.field.unverified_role"), value=role.mention if role else t("common.state.unset"), inline=False)
            embed.add_field(name=t("gettingstarted.webverify.field.autorole"), value=f"{t('common.state.enabled') if self.draft['autorole_enabled'] else t('common.state.disabled')} / {self.draft['autorole_trigger']}", inline=False)
            embed.add_field(name=t("gettingstarted.webverify.field.notify"), value=notify.get("type", "dm"), inline=True)
            embed.add_field(name=t("gettingstarted.webverify.field.country_alert"), value=t("common.state.enabled") if country.get("enabled") else t("common.state.disabled"), inline=True)
        return embed

    async def refresh(self, interaction: discord.Interaction):
        self.rebuild_components()
        await self.session.render(interaction, embed=self.build_embed(), view=self)

    async def select_captcha(self, interaction: discord.Interaction):
        self.draft["captcha_type"] = interaction.data["values"][0]
        await self.refresh(interaction)

    async def toggle_enabled(self, interaction: discord.Interaction):
        self.draft["enabled"] = not self.draft.get("enabled", False)
        await self.refresh(interaction)

    async def select_role(self, interaction: discord.Interaction):
        self.draft["unverified_role_id"] = int(interaction.data["values"][0])
        await self.refresh(interaction)

    async def create_role(self, interaction: discord.Interaction):
        await interaction.response.send_modal(WebVerifyRoleCreationModal(self))

    async def toggle_autorole(self, interaction: discord.Interaction):
        self.draft["autorole_enabled"] = not self.draft.get("autorole_enabled", False)
        await self.refresh(interaction)

    async def select_autorole_trigger(self, interaction: discord.Interaction):
        values = interaction.data.get("values", [])
        if "always" in values and len(values) > 1:
            values = [value for value in values if value != "always"]
        self.draft["autorole_trigger"] = "+".join(values or ["always"])
        await self.refresh(interaction)

    async def edit_min_age(self, interaction: discord.Interaction):
        await interaction.response.send_modal(WebVerifyMinAgeModal(self))

    async def select_notify_type(self, interaction: discord.Interaction):
        self.draft["notify"]["type"] = interaction.data["values"][0]
        await self.refresh(interaction)

    async def select_notify_channel(self, interaction: discord.Interaction):
        self.draft["notify"]["channel_id"] = int(interaction.data["values"][0])
        await self.refresh(interaction)

    async def edit_notification(self, interaction: discord.Interaction):
        await interaction.response.send_modal(WebVerifyNotificationModal(self))

    async def toggle_country_alert(self, interaction: discord.Interaction):
        country = self.draft["webverify_country_alert"]
        country["enabled"] = not country.get("enabled", False)
        await self.refresh(interaction)

    async def select_country_mode(self, interaction: discord.Interaction):
        self.draft["webverify_country_alert"]["mode"] = interaction.data["values"][0]
        await self.refresh(interaction)

    async def select_country_channel(self, interaction: discord.Interaction):
        self.draft["webverify_country_alert"]["channel_id"] = int(interaction.data["values"][0])
        await self.refresh(interaction)

    async def edit_countries(self, interaction: discord.Interaction):
        await interaction.response.send_modal(WebVerifyCountriesModal(self))

    async def previous_step(self, interaction: discord.Interaction):
        self.step = max(1, self.step - 1)
        await self.refresh(interaction)

    async def next_step(self, interaction: discord.Interaction):
        if self.step in (2, 3) and self.draft.get("autorole_enabled") and not self.draft.get("unverified_role_id"):
            await interaction.response.send_message(t("gettingstarted.webverify.err.select_role_before_autorole"), ephemeral=True)
            return
        self.step = min(6, self.step + 1)
        await self.refresh(interaction)

    def validate(self) -> str | None:
        if self.draft.get("autorole_enabled") and not self.draft.get("unverified_role_id"):
            return t("gettingstarted.webverify.err.autorole_needs_role")
        notify = self.draft["notify"]
        if notify.get("type") in ("channel", "both") and not notify.get("channel_id"):
            return t("gettingstarted.webverify.err.notify_needs_channel")
        country = self.draft["webverify_country_alert"]
        if country.get("enabled"):
            if not country.get("channel_id"):
                return t("gettingstarted.webverify.err.country_alert_needs_channel")
            if not country.get("countries"):
                return t("gettingstarted.webverify.err.country_alert_needs_codes")
        return None

    async def persist(self, interaction: discord.Interaction, *, send_message: bool):
        error = self.validate()
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        if not await self.session.save(
            interaction,
            self.module_name,
            get_webverify_panel_setting(),
            self.draft,
        ):
            return

        sent_message = None
        if send_message:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            sent_message = await self.send_verify_message(interaction)
        target = ModuleSettingsView(self.session, self.module_name)
        embed = ModuleSettingsView.build_embed(self.session, self.module_name)
        if sent_message:
            embed.description = t("gettingstarted.webverify.msg.sent_to_channel", channel=sent_message.channel.mention) + "\n\n" + (embed.description or "")
        await self.session.render(interaction, embed=embed, view=target)

    async def send_verify_message(self, interaction: discord.Interaction):
        notify = self.draft["notify"]
        channel_id = notify.get("channel_id")
        channel = self.session.guild.get_channel(int(channel_id)) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(t("gettingstarted.webverify.err.channel_not_found"), ephemeral=True)
            return None
        bot_member = self.session.guild.me
        permissions = channel.permissions_for(bot_member) if bot_member else None
        if permissions is None or not permissions.view_channel or not permissions.send_messages:
            await interaction.followup.send(t("gettingstarted.webverify.err.no_channel_permission"), ephemeral=True)
            return None
        application_id = bot.application_id or (bot.user.id if bot.user else None)
        if application_id is None:
            await interaction.followup.send(t("gettingstarted.webverify.err.no_application_id"), ephemeral=True)
            return None
        query = urlencode({"redirect_uri": config("webverify_url"), "state": self.session.guild.id})
        verify_url = (
            f"https://discord.com/oauth2/authorize?client_id={application_id}"
            f"&response_type=code&scope=identify&prompt=none&{query}"
        )
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label=t("gettingstarted.webverify.btn.go_verify"), style=discord.ButtonStyle.link, url=verify_url))
        embed = discord.Embed(
            title=notify.get("title") or t("gettingstarted.webverify.default.notify_title"),
            description=notify.get("message") or t("gettingstarted.webverify.default.notify_message"),
            color=discord.Color.green(),
        )
        try:
            return await channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException) as error:
            await interaction.followup.send(t("gettingstarted.webverify.err.send_failed", error=error), ephemeral=True)
            return None

    async def save_only(self, interaction: discord.Interaction):
        await self.persist(interaction, send_message=False)

    async def save_and_send(self, interaction: discord.Interaction):
        await self.persist(interaction, send_message=True)

    async def cancel(self, interaction: discord.Interaction):
        target = ModuleSettingsView(self.session, self.module_name)
        await self.session.render(
            interaction,
            embed=ModuleSettingsView.build_embed(self.session, self.module_name),
            view=target,
        )


async def start_getting_started(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    if interaction.guild is None or permissions is None or not permissions.manage_guild:
        await interaction.response.send_message(
            t("gettingstarted.err.manage_guild_required"),
            ephemeral=True,
        )
        return False

    session = GettingStartedSession(interaction.guild, interaction.user.id)
    view = GettingStartedHubView(session)
    session.active_view = view
    await interaction.response.send_message(
        embed=GettingStartedHubView.build_embed(session),
        view=view,
        ephemeral=True,
    )
    session.message = await interaction.original_response()
    return True


async def start_autoreply_builder(interaction: discord.Interaction):
    session = GettingStartedSession(interaction.guild, interaction.user.id)
    cog = bot.get_cog("AutoReply")
    if cog is None:
        await interaction.response.send_message(t("gettingstarted.err.autoreply_unavailable"), ephemeral=True)
        return
    view = GettingStartedAutoReplyBuilderView(session, "AutoReply", cog, interaction)
    session.active_view = view
    await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)
    session.message = await interaction.original_response()
    view.message = session.message


async def start_automod_quick_setup(interaction: discord.Interaction):
    session = GettingStartedSession(interaction.guild, interaction.user.id)
    view = AutoModerateManagerView(session, "AutoModerate")
    session.active_view = view
    await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)
    session.message = await interaction.original_response()


async def start_webverify_quick_setup(interaction: discord.Interaction):
    session = GettingStartedSession(interaction.guild, interaction.user.id)
    view = WebVerifySetupView(session, "ServerWebVerify")
    session.active_view = view
    await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)
    session.message = await interaction.original_response()


class GettingStartedLauncherView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        permissions = getattr(interaction.user, "guild_permissions", None)
        if interaction.guild is None or permissions is None or not permissions.manage_guild:
            await interaction.response.send_message(
                t("gettingstarted.err.manage_guild_required"),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label=i18n.K("gettingstarted.launcher.btn.open_setup"),
        style=discord.ButtonStyle.primary,
        custom_id="getting_started_open_server_setup",
    )
    async def open_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await start_getting_started(interaction):
            return
        if interaction.message is not None:
            try:
                await interaction.message.delete()
            except discord.HTTPException:
                try:
                    await interaction.message.edit(view=None)
                except discord.HTTPException:
                    pass


class GettingStarted(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.bot = client
        self.persistent_view_registered = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self.persistent_view_registered:
            return
        self.bot.add_view(GettingStartedLauncherView())
        self.persistent_view_registered = True

    async def send_dm_fallback(self, guild: discord.Guild, recipient):
        try:
            try:
                command = await get_command_mention("gettingstarted")
            except Exception:
                command = "`/gettingstarted`"
            recipient_loc = i18n.resolve_locale(user_id=recipient.id)
            await recipient.send(
                t("gettingstarted.dm.no_setup_channel", locale=recipient_loc, guild=guild.name, command=command)
            )
            log(
                "No suitable quick-setup channel found; DMed the admin instead",
                module_name="gettingstarted",
                user=recipient,
                guild=guild,
            )
        except discord.Forbidden:
            log(
                "No suitable quick-setup channel found, and DM to admin failed as well",
                level=logging.WARNING,
                module_name="gettingstarted",
                user=recipient,
                guild=guild,
            )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        # listener 不在 choke point 內，顯式開伺服器語言 scope
        async with i18n.guild_scope(guild.id):
            await self._on_guild_join_impl(guild)

    async def _on_guild_join_impl(self, guild: discord.Guild):
        recipient = await get_join_prompt_recipient(
            guild,
            self.bot.user.id if self.bot.user else None,
        )
        await asyncio.sleep(1)
        if recipient is None:
            return

        recipient_member = guild.get_member(recipient.id)
        if recipient_member is None:
            try:
                recipient_member = await guild.fetch_member(recipient.id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException, AttributeError):
                recipient_member = guild.owner
        channel = find_setup_channel(guild, recipient_member)
        if channel is None:
            await self.send_dm_fallback(guild, recipient)
            return

        embed = discord.Embed(
            title=t("gettingstarted.launcher.embed.title"),
            description=t("gettingstarted.launcher.embed.desc"),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)
        try:
            await channel.send(
                content=t("gettingstarted.launcher.msg.prompt", mention=recipient.mention),
                embed=embed,
                view=GettingStartedLauncherView(),
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    roles=False,
                    users=[recipient],
                ),
            )
            log(
                f"Sent quick-setup launcher in #{channel.name}",
                module_name="gettingstarted",
                user=recipient,
                guild=guild,
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            log(
                f"Failed to send quick-setup launcher: {error}",
                level=logging.ERROR,
                module_name="gettingstarted",
                user=recipient,
                guild=guild,
            )
            await self.send_dm_fallback(guild, recipient)

    @app_commands.command(name=app_commands.locale_str("gettingstarted", i18n_key="cmd.gettingstarted.gettingstarted.name"), description=app_commands.locale_str("Open the server quick-setup center", i18n_key="cmd.gettingstarted.gettingstarted.desc"))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def getting_started(self, interaction: discord.Interaction):
        await start_getting_started(interaction)


asyncio.run(bot.add_cog(GettingStarted(bot)))
