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
    modules,
    panel_settings,
    set_server_config,
)
from logger import log


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


if "JoinNotify" in modules:
    try:
        from JoinNotify import get_join_prompt_recipient
    except Exception:
        get_join_prompt_recipient = None
else:
    get_join_prompt_recipient = None


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
    return [
        (module_name, data)
        for module_name, data in panel_settings.items()
        if module_name in modules and module_name not in unavailable
    ]


def find_setup_channel(guild: discord.Guild, recipient) -> discord.TextChannel | None:
    bot_member = guild.me
    if bot_member is None or recipient is None:
        return None

    candidates = []
    if guild.system_channel is not None:
        candidates.append(guild.system_channel)
    # candidates.extend(
    #     channel
    #     for channel in sorted(guild.text_channels, key=lambda item: (item.position, item.id))
    #     if channel not in candidates
    # )

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
        return "未設定"
    if stype in ("channel", "voice_channel", "category"):
        channel = guild.get_channel(int(value)) if str(value).isdigit() else None
        return channel.mention if channel else f"未知頻道 ({value})"
    if stype == "role":
        role = guild.get_role(int(value)) if str(value).isdigit() else None
        return role.mention if role else f"未知身分組 ({value})"
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
            mentions.append(f"... 共 {len(values)} 項")
        return "、".join(mentions) if mentions else "空清單"
    if stype == "boolean":
        return "啟用" if bool(value) else "停用"
    if isinstance(value, (dict, list)):
        return f"已設定 {len(value)} 項"
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
        raise ValueError("請輸入有效的數字。") from error

    minimum = setting.get("min")
    maximum = setting.get("max")
    if minimum is not None and isinstance(value, (int, float)) and value < minimum:
        raise ValueError(f"設定值不可小於 {minimum}。")
    if maximum is not None and isinstance(value, (int, float)) and value > maximum:
        raise ValueError(f"設定值不可大於 {maximum}。")
    return value


async def apply_registered_setting(
    guild_id: int,
    module_name: str,
    setting: dict,
    value: Any,
) -> str | None:
    if not set_server_config(guild_id, setting["database_key"], value):
        raise RuntimeError("寫入伺服器設定失敗。")

    trigger = setting.get("trigger")
    if not callable(trigger):
        return None

    try:
        result = trigger(guild_id, value)
        if inspect.isawaitable(result):
            await result
    except Exception as error:
        log(
            f"快速設定 trigger 失敗: {module_name}.{setting['database_key']}: {error}",
            level=logging.ERROR,
            module_name="gettingstarted",
        )
        return "設定已儲存，但套用即時效果時發生錯誤。"
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
                    "只有開啟這次設定流程、且具備管理伺服器權限的成員可以操作。",
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
                embed = discord.Embed(
                    title="快速設定已逾時",
                    description="已確認的設定仍然保留；請使用 `/gettingstarted` 繼續設定。",
                    color=discord.Color.red(),
                )
                await self.session.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass


class ModuleSelect(discord.ui.Select):
    def __init__(self, parent: "GettingStartedHubView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(placeholder="選擇要設定的功能模組", options=options, row=0)

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
                description=truncate(data.get("description") or "開啟此模組的設定", 100),
                emoji=data.get("icon") or None,
            )
            for module_name, data in current
        ]
        if options:
            self.add_item(ModuleSelect(self, options))

        previous = discord.ui.Button(label="上一頁", style=discord.ButtonStyle.secondary, row=1)
        previous.disabled = self.page == 0
        previous.callback = self.previous_page
        self.add_item(previous)

        next_button = discord.ui.Button(label="下一頁", style=discord.ButtonStyle.secondary, row=1)
        next_button.disabled = self.page >= self.total_pages - 1
        next_button.callback = self.next_page
        self.add_item(next_button)

        finish = discord.ui.Button(label="完成", style=discord.ButtonStyle.success, row=1)
        finish.callback = self.finish
        self.add_item(finish)

    @staticmethod
    def build_embed(session: GettingStartedSession, page: int = 0) -> discord.Embed:
        entries = available_panel_modules()
        _, page, total_pages = paginate(entries, page)
        embed = discord.Embed(
            title=f"{session.guild.name} 快速設定",
            description="從下拉選單選擇功能。每個確認的設定都會立即儲存。",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="可設定模組", value=str(len(entries)), inline=True)
        embed.add_field(name="本次已修改", value=str(len(session.changes)), inline=True)
        embed.set_footer(text=f"模組頁面 {page + 1}/{total_pages}")
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
            description = "本次已完成以下設定：\n" + "\n".join(lines[:30])
            if len(lines) > 30:
                description += f"\n... 另有 {len(lines) - 30} 項"
        else:
            description = "這次沒有變更任何設定。"
        embed = discord.Embed(title="快速設定完成", description=description, color=discord.Color.green())
        await self.session.render(interaction, embed=embed, view=None)


class SettingSelect(discord.ui.Select):
    def __init__(self, parent: "ModuleSettingsView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(placeholder="選擇設定項目", options=options, row=0)

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
        self.module_data = panel_settings[module_name]
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

        previous = discord.ui.Button(label="上一頁", style=discord.ButtonStyle.secondary, row=1)
        previous.disabled = self.page == 0
        previous.callback = self.previous_page
        self.add_item(previous)

        next_button = discord.ui.Button(label="下一頁", style=discord.ButtonStyle.secondary, row=1)
        next_button.disabled = self.page >= self.total_pages - 1
        next_button.callback = self.next_page
        self.add_item(next_button)

        back = discord.ui.Button(label="返回模組", style=discord.ButtonStyle.secondary, row=1)
        back.callback = self.back
        self.add_item(back)

    @staticmethod
    def build_embed(session: GettingStartedSession, module_name: str, page: int = 0) -> discord.Embed:
        module_data = panel_settings[module_name]
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
            description="\n\n".join(lines) if lines else "這個模組目前沒有可設定項目。",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"設定頁面 {page + 1}/{total_pages}")
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


class ScalarSettingModal(discord.ui.Modal):
    def __init__(self, parent: "SingleSettingView"):
        title = truncate(parent.setting.get("display", "編輯設定"), 45)
        super().__init__(title=title)
        self.parent_view = parent
        stype = parent.setting.get("type", "string")
        current = get_server_config(
            parent.session.guild.id,
            parent.setting["database_key"],
            parent.setting.get("default"),
        )
        self.value_input = discord.ui.TextInput(
            label="設定值",
            default="" if current is None else truncate(current, 4000),
            required=False,
            max_length=4000,
            style=discord.TextStyle.paragraph if stype == "text" else discord.TextStyle.short,
            placeholder="留空會清除此設定",
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
        super().__init__(placeholder="選擇設定值", options=options, row=0)

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
            placeholder="選擇頻道",
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
        super().__init__(placeholder="選擇身分組", min_values=1, max_values=1, row=0)

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
            enable = discord.ui.Button(label="啟用", style=discord.ButtonStyle.success, row=0)
            enable.callback = self.enable
            self.add_item(enable)
            disable = discord.ui.Button(label="停用", style=discord.ButtonStyle.danger, row=0)
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
            edit = discord.ui.Button(label="編輯", style=discord.ButtonStyle.primary, row=0)
            edit.callback = self.edit
            self.add_item(edit)

        if stype not in ("boolean",):
            clear = discord.ui.Button(label="清除", style=discord.ButtonStyle.danger, row=1)
            clear.callback = self.clear
            self.add_item(clear)

        back = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, row=1)
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
            description=self.setting.get("description") or "修改此伺服器設定。",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="目前設定",
            value=format_setting_value(self.session.guild, self.setting, value),
            inline=False,
        )
        embed.set_footer(text=f"設定鍵：{self.setting['database_key']}")
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
            placeholder="新增頻道（可多選）",
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
        super().__init__(placeholder="新增身分組（可多選）", min_values=1, max_values=25, row=0)

    async def callback(self, interaction: discord.Interaction):
        ids = [int(value) for value in interaction.data.get("values", []) if str(value).isdigit()]
        await self.parent_view.add_values(interaction, ids)


class ListRemoveSelect(discord.ui.Select):
    def __init__(self, parent: "ListSettingView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(
            placeholder="選擇要移除的項目",
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

        previous = discord.ui.Button(label="上一頁", style=discord.ButtonStyle.secondary, row=2)
        previous.disabled = self.page == 0
        previous.callback = self.previous_page
        self.add_item(previous)
        next_button = discord.ui.Button(label="下一頁", style=discord.ButtonStyle.secondary, row=2)
        next_button.disabled = self.page >= self.total_pages - 1
        next_button.callback = self.next_page
        self.add_item(next_button)
        clear = discord.ui.Button(label="全部清除", style=discord.ButtonStyle.danger, row=2)
        clear.disabled = not self.values
        clear.callback = self.clear
        self.add_item(clear)
        back = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, row=2)
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
            description=self.setting.get("description") or "新增或移除清單項目。",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name=f"目前共 {len(self.values)} 項",
            value=format_setting_value(self.session.guild, self.setting, self.values),
            inline=False,
        )
        embed.set_footer(text=f"移除清單頁面 {self.page + 1}/{self.total_pages}")
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
        super().__init__(placeholder="選擇 AutoReply 規則", options=options, row=0)

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
            triggers = ", ".join(str(value) for value in rule.get("trigger", [])) or "未命名規則"
            responses = ", ".join(str(value) for value in rule.get("response", [])) or "沒有回覆"
            options.append(
                discord.SelectOption(
                    label=truncate(triggers, 100),
                    value=str(rule_index),
                    description=truncate(responses, 100),
                )
            )
        if options:
            self.add_item(AutoReplyRuleSelect(self, options))

        add_button = discord.ui.Button(label="新增規則", style=discord.ButtonStyle.success, row=1)
        add_button.callback = self.add_rule
        self.add_item(add_button)
        clear_button = discord.ui.Button(label="全部清除", style=discord.ButtonStyle.danger, row=1)
        clear_button.disabled = not self.rules
        clear_button.callback = self.clear_rules
        self.add_item(clear_button)

        previous = discord.ui.Button(label="上一頁", style=discord.ButtonStyle.secondary, row=2)
        previous.disabled = self.page == 0
        previous.callback = self.previous_page
        self.add_item(previous)
        next_button = discord.ui.Button(label="下一頁", style=discord.ButtonStyle.secondary, row=2)
        next_button.disabled = self.page >= self.total_pages - 1
        next_button.callback = self.next_page
        self.add_item(next_button)
        back = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, row=2)
        back.callback = self.back
        self.add_item(back)

    def get_cog(self):
        return bot.get_cog("AutoReply")

    def build_embed(self) -> discord.Embed:
        cog = self.get_cog()
        limit = cog._get_autoreply_limit(self.session.guild.id) if cog else 0
        embed = discord.Embed(
            title="AutoReply 規則管理",
            description="選擇既有規則以檢視、編輯或刪除，也可以使用 Builder 新增規則。",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="目前規則", value=f"{len(self.rules)} / {limit or '?'}", inline=True)
        embed.set_footer(text=f"規則頁面 {self.page + 1}/{self.total_pages}")
        return embed

    async def add_rule(self, interaction: discord.Interaction):
        cog = self.get_cog()
        if cog is None:
            await interaction.response.send_message("AutoReply 模組目前無法使用。", ephemeral=True)
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

        edit = discord.ui.Button(label="編輯", style=discord.ButtonStyle.primary, row=0)
        edit.callback = self.edit
        self.add_item(edit)
        delete = discord.ui.Button(label="刪除", style=discord.ButtonStyle.danger, row=0)
        delete.callback = self.delete
        self.add_item(delete)
        back = discord.ui.Button(label="返回規則", style=discord.ButtonStyle.secondary, row=0)
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
            return discord.Embed(title="找不到規則", color=discord.Color.red())
        if cog is not None:
            return cog._build_autoreply_rule_embed(
                title=f"AutoReply 規則 #{self.rule_index + 1}",
                rule=rule,
                guild=self.session.guild,
            )
        return discord.Embed(
            title=f"AutoReply 規則 #{self.rule_index + 1}",
            description=truncate(rule, 4000),
            color=discord.Color.blurple(),
        )

    async def edit(self, interaction: discord.Interaction):
        rule = self.get_rule()
        cog = bot.get_cog("AutoReply")
        if rule is None or cog is None:
            await interaction.response.send_message("這條規則已不存在或模組目前無法使用。", ephemeral=True)
            return
        builder = GettingStartedAutoReplyBuilderView(
            self.session,
            self.module_name,
            cog,
            interaction,
            rule_index=self.rule_index,
        )
        await self.session.render(interaction, embed=builder.build_embed(title="編輯 AutoReply 規則"), view=builder)

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
        raise ValueError("要編輯的 AutoReply 規則已不存在。")

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
        raise ValueError("儲存 AutoReply 規則失敗。")
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
                await self.session.message.edit(
                    embed=discord.Embed(
                        title="AutoReply Builder 已逾時",
                        description="尚未儲存的規則已捨棄。",
                        color=discord.Color.red(),
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass


class AutoReplyDeleteConfirmView(SetupView):
    def __init__(self, session: GettingStartedSession, module_name: str, rule_index: int):
        super().__init__(session)
        self.module_name = module_name
        self.rule_index = rule_index
        confirm = discord.ui.Button(label="確認刪除", style=discord.ButtonStyle.danger)
        confirm.callback = self.confirm
        self.add_item(confirm)
        cancel = discord.ui.Button(label="取消", style=discord.ButtonStyle.secondary)
        cancel.callback = self.cancel
        self.add_item(cancel)

    def build_embed(self) -> discord.Embed:
        return discord.Embed(
            title="刪除 AutoReply 規則？",
            description="此操作會立即生效。",
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
        confirm = discord.ui.Button(label="確認全部清除", style=discord.ButtonStyle.danger)
        confirm.callback = self.confirm
        self.add_item(confirm)
        cancel = discord.ui.Button(label="取消", style=discord.ButtonStyle.secondary)
        cancel.callback = self.cancel
        self.add_item(cancel)

    def build_embed(self) -> discord.Embed:
        count = len(get_server_config(self.session.guild.id, "autoreplies", []) or [])
        return discord.Embed(
            title="清除所有 AutoReply 規則？",
            description=f"目前共有 {count} 條規則，此操作無法復原。",
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


AUTOMOD_FEATURE_SCHEMAS = [
    {
        "id": "scamtrap",
        "label": "詐騙陷阱",
        "description": "在蜜罐頻道發言時自動處置。",
        "fields": [
            {"key": "channel_id", "label": "陷阱頻道", "type": "channel", "required": True},
            {
                "key": "action",
                "label": "處置動作",
                "type": "string",
                "default": "delete {user} 是最後一個被封禁的帳號，不要在這裡講話！, ban {user} 5s 12h [自動封禁] 疑似被盜帳號",
                "required": True,
            },
        ],
    },
    {
        "id": "escape_punish",
        "label": "逃避責任懲處",
        "description": "禁言期間離開伺服器時追加處置。",
        "fields": [
            {
                "key": "punishment",
                "label": "懲處方式",
                "type": "select",
                "default": "ban",
                "options": [{"label": "封禁", "value": "ban"}],
            },
            {"key": "duration", "label": "持續時間", "type": "string", "default": "0"},
        ],
    },
    {
        "id": "too_many_h1",
        "label": "標題過多",
        "description": "限制 Markdown 大標題總字數。",
        "fields": [
            {"key": "max_length", "label": "最大字數", "type": "number", "default": "20", "min": 1},
            {"key": "action", "label": "處置動作", "type": "string", "default": "warn", "required": True},
            {"key": "ignore_channels", "label": "忽略頻道", "type": "channel_list", "default": []},
        ],
    },
    {
        "id": "too_many_emojis",
        "label": "表情符號過多",
        "description": "限制單則訊息的 emoji 數量。",
        "fields": [
            {"key": "max_emojis", "label": "最大數量", "type": "number", "default": "10", "min": 1},
            {"key": "action", "label": "處置動作", "type": "string", "default": "warn", "required": True},
            {"key": "ignore_channels", "label": "忽略頻道", "type": "channel_list", "default": []},
        ],
    },
    {
        "id": "anti_invite_link",
        "label": "邀請連結",
        "description": "偵測 Discord 邀請連結。",
        "fields": [
            {"key": "allow_current_server", "label": "允許本伺服器連結", "type": "boolean", "default": False},
            {
                "key": "action",
                "label": "處置動作",
                "type": "string",
                "default": "delete {user}，請勿發送其他伺服器的邀請連結。",
                "required": True,
            },
            {"key": "ignore_channels", "label": "忽略頻道", "type": "channel_list", "default": []},
        ],
    },
    {
        "id": "anti_uispam",
        "label": "用戶安裝應用程式濫用",
        "description": "限制 User Install 指令的觸發頻率。",
        "fields": [
            {"key": "max_count", "label": "最大觸發次數", "type": "number", "default": "5", "min": 1},
            {"key": "time_window", "label": "時間窗口（秒）", "type": "number", "default": "60", "min": 1},
            {
                "key": "action",
                "label": "處置動作",
                "type": "string",
                "default": "delete {user}，請勿濫用用戶安裝的應用程式指令。, mute 10m 濫用用戶安裝指令",
                "required": True,
            },
            {"key": "ignore_channels", "label": "忽略頻道", "type": "channel_list", "default": []},
        ],
    },
    {
        "id": "anti_raid",
        "label": "防突襲",
        "description": "偵測短時間內大量成員加入。",
        "fields": [
            {"key": "max_joins", "label": "最大加入數", "type": "number", "default": "5", "min": 1},
            {"key": "time_window", "label": "時間窗口（秒）", "type": "number", "default": "60", "min": 1},
            {"key": "action", "label": "處置動作", "type": "string", "default": "kick 突襲偵測自動踢出", "required": True},
        ],
    },
    {
        "id": "anti_spam",
        "label": "防刷頻",
        "description": "偵測短時間內的相似訊息。",
        "fields": [
            {"key": "max_messages", "label": "最大相似訊息數", "type": "number", "default": "5", "min": 1},
            {"key": "time_window", "label": "時間窗口（秒）", "type": "number", "default": "30", "min": 1},
            {"key": "similarity", "label": "相似度（%）", "type": "number", "default": "75", "min": 1, "max": 100},
            {
                "key": "action",
                "label": "處置動作",
                "type": "string",
                "default": "mute 10m 刷頻自動禁言, delete {user}，請勿刷頻。",
                "required": True,
            },
            {"key": "ignore_channels", "label": "忽略頻道", "type": "channel_list", "default": []},
        ],
    },
    {
        "id": "automod_detect",
        "label": "Discord AutoMod 偵測",
        "description": "接收 Discord 原生 AutoMod 事件並執行額外處置。",
        "fields": [
            {"key": "log_channel", "label": "通知頻道", "type": "channel", "required": True},
            {"key": "action", "label": "額外處置動作", "type": "string", "default": ""},
            {"key": "filter_rule", "label": "規則名稱過濾", "type": "string", "default": ""},
            {"key": "filter_action_type", "label": "動作類型過濾", "type": "string", "default": ""},
        ],
    },
]
AUTOMOD_FEATURE_MAP = {item["id"]: item for item in AUTOMOD_FEATURE_SCHEMAS}


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
    stored = get_automod_config(guild_id).get(feature_id, {})
    stored = copy.deepcopy(stored) if isinstance(stored, dict) else {}
    schema = AUTOMOD_FEATURE_MAP[feature_id]
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
        return "未設定"
    field_type = field_schema.get("type")
    if field_type == "boolean":
        return "是" if bool(value) else "否"
    if field_type == "channel":
        channel = guild.get_channel(int(value)) if str(value).isdigit() else None
        return channel.mention if channel else f"未知頻道 ({value})"
    if field_type == "channel_list":
        values = value if isinstance(value, list) else []
        mentions = []
        for channel_id in values[:15]:
            channel = guild.get_channel(int(channel_id)) if str(channel_id).isdigit() else None
            mentions.append(channel.mention if channel else str(channel_id))
        if len(values) > 15:
            mentions.append(f"... 共 {len(values)} 項")
        return "、".join(mentions) if mentions else "無"
    return truncate(value, 900)


class AutoModerateFeatureSelect(discord.ui.Select):
    def __init__(self, parent: "AutoModerateManagerView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(placeholder="選擇自動管理功能", options=options, row=0)

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
        current, self.page, self.total_pages = paginate(AUTOMOD_FEATURE_SCHEMAS, page)
        automod = get_automod_config(session.guild.id)
        options = []
        for feature in current:
            enabled = bool(automod.get(feature["id"], {}).get("enabled", False))
            options.append(
                discord.SelectOption(
                    label=feature["label"],
                    value=feature["id"],
                    description=truncate(feature["description"], 100),
                    emoji="✅" if enabled else "⏸️",
                )
            )
        self.add_item(AutoModerateFeatureSelect(self, options))

        previous = discord.ui.Button(label="上一頁", style=discord.ButtonStyle.secondary, row=1)
        previous.disabled = self.page == 0
        previous.callback = self.previous_page
        self.add_item(previous)
        next_button = discord.ui.Button(label="下一頁", style=discord.ButtonStyle.secondary, row=1)
        next_button.disabled = self.page >= self.total_pages - 1
        next_button.callback = self.next_page
        self.add_item(next_button)
        back = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, row=1)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self) -> discord.Embed:
        automod = get_automod_config(self.session.guild.id)
        enabled = sum(bool(automod.get(item["id"], {}).get("enabled", False)) for item in AUTOMOD_FEATURE_SCHEMAS)
        embed = discord.Embed(
            title="自動管理規則",
            description="選擇功能後可調整完整參數並啟用或停用。",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="已啟用", value=f"{enabled} / {len(AUTOMOD_FEATURE_SCHEMAS)}", inline=True)
        embed.set_footer(text=f"功能頁面 {self.page + 1}/{self.total_pages}")
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
        super().__init__(placeholder="選擇要修改的欄位", options=options, row=0)

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
        self.feature_schema = AUTOMOD_FEATURE_MAP[feature_id]
        options = [
            discord.SelectOption(
                label=truncate(field_schema["label"], 100),
                value=field_schema["key"],
                description=field_schema.get("type", "string"),
            )
            for field_schema in self.feature_schema["fields"]
        ]
        self.add_item(AutoModerateFieldSelect(self, options))

        enable = discord.ui.Button(label="啟用", style=discord.ButtonStyle.success, row=1)
        enable.callback = self.enable
        self.add_item(enable)
        disable = discord.ui.Button(label="停用", style=discord.ButtonStyle.danger, row=1)
        disable.callback = self.disable
        self.add_item(disable)
        back = discord.ui.Button(label="返回規則", style=discord.ButtonStyle.secondary, row=1)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self) -> discord.Embed:
        data = get_automod_feature_data(self.session.guild.id, self.feature_id)
        embed = discord.Embed(
            title=self.feature_schema["label"],
            description=self.feature_schema["description"],
            color=discord.Color.green() if data.get("enabled") else discord.Color.blurple(),
        )
        embed.add_field(name="狀態", value="啟用" if data.get("enabled") else "停用", inline=False)
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
                "請先完成必要設定：" + "、".join(missing),
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
            label="設定值",
            default=truncate(current, 4000),
            required=False,
            max_length=4000,
            placeholder="留空會清除此欄位",
            style=discord.TextStyle.paragraph if parent.field_schema["key"] == "action" else discord.TextStyle.short,
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.value_input.value.strip()
        field_schema = self.parent_view.field_schema
        if field_schema.get("key") == "action" and Moderate is not None:
            analysis = Moderate.analyze_action_string(raw, self.parent_view.session.guild.id)
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
                await interaction.response.send_message("請輸入有效的整數。", ephemeral=True)
                return
            if field_schema.get("min") is not None and number < field_schema["min"]:
                await interaction.response.send_message(
                    f"設定值不可小於 {field_schema['min']}。",
                    ephemeral=True,
                )
                return
            if field_schema.get("max") is not None and number > field_schema["max"]:
                await interaction.response.send_message(
                    f"設定值不可大於 {field_schema['max']}。",
                    ephemeral=True,
                )
                return
            value = str(number)
        await self.parent_view.save_value(interaction, value if raw else None)


class AutoModerateValueSelect(discord.ui.Select):
    def __init__(self, parent: "AutoModerateFieldView", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(placeholder="選擇設定值", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.save_value(interaction, self.values[0])


class AutoModerateChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "AutoModerateFieldView"):
        self.parent_view = parent
        super().__init__(
            placeholder="選擇頻道",
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
        options = [
            discord.SelectOption(label=label, value=value)
            for label, value in (Moderate.ACTION_INPUT_SUGGESTIONS if Moderate is not None else [])
        ]
        super().__init__(placeholder="選擇常用動作", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        analysis = Moderate.analyze_action_string(self.values[0], self.parent_view.session.guild.id)
        if not analysis["valid"]:
            await interaction.response.send_message(analysis["error"], ephemeral=True)
            return
        await self.parent_view.save_value(interaction, analysis["normalized"])


class AutoModerateActionConfirmView(SetupView):
    def __init__(self, parent_view: "AutoModerateFieldView", analysis: dict):
        super().__init__(parent_view.session, timeout=120)
        self.parent_view = parent_view
        self.analysis = analysis
        confirm = discord.ui.Button(label="是，使用這個動作", style=discord.ButtonStyle.success)
        confirm.callback = self.confirm
        self.add_item(confirm)
        retry = discord.ui.Button(label="不是，重新輸入", style=discord.ButtonStyle.secondary)
        retry.callback = self.retry
        self.add_item(retry)
        cancel = discord.ui.Button(label="取消", style=discord.ButtonStyle.danger)
        cancel.callback = self.cancel
        self.add_item(cancel)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="確認你的意思",
            description=self.analysis.get("confirmation"),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="將儲存為",
            value=f"```text\n{self.analysis['normalized']}\n```",
            inline=False,
        )
        embed.add_field(
            name="執行預覽",
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
            edit = discord.ui.Button(label="自訂輸入", style=discord.ButtonStyle.primary, row=1)
            edit.callback = self.edit
            self.add_item(edit)
        elif field_type == "boolean":
            yes = discord.ui.Button(label="是", style=discord.ButtonStyle.success, row=0)
            yes.callback = self.set_true
            self.add_item(yes)
            no = discord.ui.Button(label="否", style=discord.ButtonStyle.danger, row=0)
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
            edit = discord.ui.Button(label="編輯", style=discord.ButtonStyle.primary, row=0)
            edit.callback = self.edit
            self.add_item(edit)

        footer_row = 2 if action_field else 1
        clear = discord.ui.Button(label="清除", style=discord.ButtonStyle.danger, row=footer_row)
        clear.callback = self.clear
        self.add_item(clear)
        back = discord.ui.Button(label="返回功能", style=discord.ButtonStyle.secondary, row=footer_row)
        back.callback = self.back
        self.add_item(back)

    def build_embed(self) -> discord.Embed:
        data = get_automod_feature_data(self.session.guild.id, self.feature_id)
        value = data.get(self.field_schema["key"])
        embed = discord.Embed(
            title="動作設定完成" if self.saved and self.field_schema["key"] == "action" else self.field_schema["label"],
            description=("設定已儲存。\n\n" if self.saved else "")
            + "目前設定："
            + format_automod_field(self.session.guild, self.field_schema, value),
            color=discord.Color.green() if self.saved else discord.Color.blurple(),
        )
        if self.field_schema["key"] == "action" and value and Moderate is not None:
            analysis = Moderate.analyze_action_string(str(value), self.session.guild.id)
            if analysis["valid"]:
                embed.add_field(
                    name="執行預覽",
                    value="\n".join(
                        f"{index}. {line}"
                        for index, line in enumerate(analysis.get("preview", []), 1)
                    ),
                    inline=False,
                )
            else:
                embed.add_field(name="語法問題", value=analysis["error"], inline=False)
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
            placeholder="新增忽略頻道",
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
            placeholder="移除忽略頻道",
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
        previous = discord.ui.Button(label="上一頁", style=discord.ButtonStyle.secondary, row=2)
        previous.disabled = self.page == 0
        previous.callback = self.previous_page
        self.add_item(previous)
        next_button = discord.ui.Button(label="下一頁", style=discord.ButtonStyle.secondary, row=2)
        next_button.disabled = self.page >= self.total_pages - 1
        next_button.callback = self.next_page
        self.add_item(next_button)
        clear = discord.ui.Button(label="全部清除", style=discord.ButtonStyle.danger, row=2)
        clear.disabled = not self.values
        clear.callback = self.clear
        self.add_item(clear)
        back = discord.ui.Button(label="返回功能", style=discord.ButtonStyle.secondary, row=2)
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
        ).set_footer(text=f"移除清單頁面 {self.page + 1}/{self.total_pages}")

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
            "title": "伺服器網頁驗證",
            "message": "請點擊下方按鈕進行網頁驗證：",
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


class WebVerifyMinAgeModal(discord.ui.Modal, title="最小帳號年齡"):
    def __init__(self, parent: "WebVerifySetupView"):
        super().__init__()
        self.parent_view = parent
        self.age_input = discord.ui.TextInput(
            label="最小帳號年齡（天）",
            default=str(parent.draft.get("min_age", 7)),
            required=True,
            max_length=5,
        )
        self.add_item(self.age_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            age = int(self.age_input.value.strip())
        except ValueError:
            await interaction.response.send_message("請輸入有效的整數天數。", ephemeral=True)
            return
        if age < 0:
            await interaction.response.send_message("最小帳號年齡不可小於 0。", ephemeral=True)
            return
        self.parent_view.draft["min_age"] = age
        await self.parent_view.refresh(interaction)


class WebVerifyNotificationModal(discord.ui.Modal, title="驗證通知內容"):
    def __init__(self, parent: "WebVerifySetupView"):
        super().__init__()
        self.parent_view = parent
        notify = parent.draft["notify"]
        self.title_input = discord.ui.TextInput(
            label="通知標題",
            default=truncate(notify.get("title", "伺服器網頁驗證"), 256),
            required=True,
            max_length=256,
        )
        self.message_input = discord.ui.TextInput(
            label="通知內容",
            default=truncate(notify.get("message", "請點擊下方按鈕進行網頁驗證："), 4000),
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


class WebVerifyCountriesModal(discord.ui.Modal, title="地區代碼"):
    def __init__(self, parent: "WebVerifySetupView"):
        super().__init__()
        self.parent_view = parent
        countries = parent.draft["webverify_country_alert"].get("countries", [])
        self.countries_input = discord.ui.TextInput(
            label="ISO 國家／地區代碼",
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
                "國家／地區代碼必須是兩個英文字母：" + "、".join(invalid[:10]),
                ephemeral=True,
            )
            return
        self.parent_view.draft["webverify_country_alert"]["countries"] = list(dict.fromkeys(values))
        await self.parent_view.refresh(interaction)


class WebVerifyRoleCreationModal(discord.ui.Modal, title="建立未驗證身分組"):
    def __init__(self, parent: "WebVerifySetupView"):
        super().__init__()
        self.parent_view = parent
        self.name_input = discord.ui.TextInput(
            label="身分組名稱",
            default="未驗證成員",
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
                "自動建立未驗證身分組需要管理身分組與管理頻道權限。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            role = await guild.create_role(
                name=self.name_input.value.strip(),
                reason=f"由 {interaction.user} 使用快速設定建立未驗證身分組",
            )
            for channel in guild.text_channels:
                try:
                    await channel.set_permissions(
                        role,
                        send_messages=False,
                        create_public_threads=False,
                        create_private_threads=False,
                        reason="快速設定未驗證身分組權限",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    continue
        except (discord.Forbidden, discord.HTTPException) as error:
            await interaction.followup.send(f"建立身分組失敗：{error}", ephemeral=True)
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
                placeholder="選擇 CAPTCHA 驗證方式",
                options=[
                    discord.SelectOption(label="不使用 CAPTCHA", value="none", default=self.draft["captcha_type"] == "none"),
                    discord.SelectOption(label="Cloudflare Turnstile", value="turnstile", default=self.draft["captcha_type"] == "turnstile"),
                    discord.SelectOption(label="Google reCAPTCHA", value="recaptcha", default=self.draft["captcha_type"] == "recaptcha"),
                ],
                row=0,
            )
            captcha.callback = self.select_captcha
            self.add_item(captcha)
            toggle = discord.ui.Button(
                label=f"功能：{'啟用' if self.draft['enabled'] else '停用'}",
                style=discord.ButtonStyle.success if self.draft["enabled"] else discord.ButtonStyle.danger,
                row=1,
            )
            toggle.callback = self.toggle_enabled
            self.add_item(toggle)
            self.add_navigation(next_step=True, row=2)
        elif self.step == 2:
            role_select = discord.ui.RoleSelect(
                placeholder="選擇未驗證身分組",
                min_values=1,
                max_values=1,
                row=0,
            )
            role_select.callback = self.select_role
            self.add_item(role_select)
            create = discord.ui.Button(label="自動建立身分組", style=discord.ButtonStyle.success, row=1)
            create.callback = self.create_role
            self.add_item(create)
            self.add_navigation(previous_step=True, next_step=True, row=2)
        elif self.step == 3:
            toggle = discord.ui.Button(
                label=f"自動分配：{'啟用' if self.draft['autorole_enabled'] else '停用'}",
                style=discord.ButtonStyle.success if self.draft["autorole_enabled"] else discord.ButtonStyle.secondary,
                row=0,
            )
            toggle.callback = self.toggle_autorole
            self.add_item(toggle)
            trigger_values = set(str(self.draft.get("autorole_trigger", "always")).split("+"))
            trigger = discord.ui.Select(
                placeholder="選擇自動分配條件",
                min_values=1,
                max_values=5,
                options=[
                    discord.SelectOption(label="總是給予", value="always", default="always" in trigger_values),
                    discord.SelectOption(label="帳號年齡過小", value="age_check", default="age_check" in trigger_values),
                    discord.SelectOption(label="無驗證紀錄", value="no_history", default="no_history" in trigger_values),
                    discord.SelectOption(label="曾被標記", value="has_flagged_history", default="has_flagged_history" in trigger_values),
                    discord.SelectOption(label="曾離開伺服器", value="left_guild_before", default="left_guild_before" in trigger_values),
                ],
                row=1,
            )
            trigger.callback = self.select_autorole_trigger
            self.add_item(trigger)
            age = discord.ui.Button(label="設定最小帳號年齡", style=discord.ButtonStyle.primary, row=2)
            age.callback = self.edit_min_age
            self.add_item(age)
            self.add_navigation(previous_step=True, next_step=True, row=3)
        elif self.step == 4:
            notify_type = self.draft["notify"].get("type", "dm")
            notify_select = discord.ui.Select(
                placeholder="選擇通知方式",
                options=[
                    discord.SelectOption(label="私訊", value="dm", default=notify_type == "dm"),
                    discord.SelectOption(label="頻道", value="channel", default=notify_type == "channel"),
                    discord.SelectOption(label="私訊與頻道", value="both", default=notify_type == "both"),
                ],
                row=0,
            )
            notify_select.callback = self.select_notify_type
            self.add_item(notify_select)
            if notify_type in ("channel", "both"):
                channel_select = discord.ui.ChannelSelect(
                    placeholder="選擇驗證通知頻道",
                    channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                    min_values=1,
                    max_values=1,
                    row=1,
                )
                channel_select.callback = self.select_notify_channel
                self.add_item(channel_select)
            edit_text = discord.ui.Button(label="編輯通知文字", style=discord.ButtonStyle.primary, row=2)
            edit_text.callback = self.edit_notification
            self.add_item(edit_text)
            self.add_navigation(previous_step=True, next_step=True, row=3)
        elif self.step == 5:
            country = self.draft["webverify_country_alert"]
            toggle = discord.ui.Button(
                label=f"地區警示：{'啟用' if country['enabled'] else '停用'}",
                style=discord.ButtonStyle.success if country["enabled"] else discord.ButtonStyle.secondary,
                row=0,
            )
            toggle.callback = self.toggle_country_alert
            self.add_item(toggle)
            mode = discord.ui.Select(
                placeholder="選擇地區清單模式",
                options=[
                    discord.SelectOption(label="黑名單", value="blacklist", default=country.get("mode") == "blacklist"),
                    discord.SelectOption(label="白名單", value="whitelist", default=country.get("mode") == "whitelist"),
                ],
                row=1,
            )
            mode.callback = self.select_country_mode
            self.add_item(mode)
            if country.get("enabled"):
                channel_select = discord.ui.ChannelSelect(
                    placeholder="選擇地區警示頻道",
                    channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                    min_values=1,
                    max_values=1,
                    row=2,
                )
                channel_select.callback = self.select_country_channel
                self.add_item(channel_select)
            countries = discord.ui.Button(label="編輯地區代碼", style=discord.ButtonStyle.primary, row=3)
            countries.callback = self.edit_countries
            self.add_item(countries)
            self.add_navigation(previous_step=True, next_step=True, row=4)
        else:
            save = discord.ui.Button(label="只儲存", style=discord.ButtonStyle.success, row=0)
            save.callback = self.save_only
            self.add_item(save)
            notify_type = self.draft["notify"].get("type", "dm")
            save_send = discord.ui.Button(label="儲存並發送驗證訊息", style=discord.ButtonStyle.primary, row=0)
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
            previous = discord.ui.Button(label="上一步", style=discord.ButtonStyle.secondary, row=row)
            previous.callback = self.previous_step
            self.add_item(previous)
        if next_step:
            next_button = discord.ui.Button(label="下一步", style=discord.ButtonStyle.primary, row=row)
            next_button.callback = self.next_step
            self.add_item(next_button)
        cancel = discord.ui.Button(label="取消", style=discord.ButtonStyle.danger, row=row)
        cancel.callback = self.cancel
        self.add_item(cancel)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"網頁驗證設定（步驟 {self.step}/6）",
            color=discord.Color.blurple(),
        )
        if self.step == 1:
            embed.description = "設定驗證功能狀態與 CAPTCHA。"
            embed.add_field(name="功能", value="啟用" if self.draft["enabled"] else "停用", inline=True)
            embed.add_field(name="CAPTCHA", value=self.draft["captcha_type"], inline=True)
        elif self.step == 2:
            embed.description = "選擇或建立未驗證成員身分組。"
            role_id = self.draft.get("unverified_role_id")
            role = self.session.guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() else None
            embed.add_field(name="未驗證身分組", value=role.mention if role else "未設定", inline=False)
        elif self.step == 3:
            embed.description = "設定加入時是否自動分配未驗證身分組。"
            embed.add_field(name="自動分配", value="啟用" if self.draft["autorole_enabled"] else "停用", inline=True)
            embed.add_field(name="條件", value=self.draft.get("autorole_trigger", "always"), inline=True)
            embed.add_field(name="最小帳號年齡", value=f"{self.draft.get('min_age', 7)} 天", inline=True)
        elif self.step == 4:
            notify = self.draft["notify"]
            embed.description = "設定驗證提示的通知方式與內容。"
            embed.add_field(name="通知方式", value=notify.get("type", "dm"), inline=True)
            channel_id = notify.get("channel_id")
            channel = self.session.guild.get_channel(int(channel_id)) if channel_id and str(channel_id).isdigit() else None
            embed.add_field(name="通知頻道", value=channel.mention if channel else "未設定", inline=True)
            embed.add_field(name="標題", value=notify.get("title", "伺服器網頁驗證"), inline=False)
            embed.add_field(name="內容", value=truncate(notify.get("message", ""), 1000), inline=False)
        elif self.step == 5:
            country = self.draft["webverify_country_alert"]
            embed.description = "設定驗證來源地區的黑名單或白名單警示。"
            embed.add_field(name="地區警示", value="啟用" if country.get("enabled") else "停用", inline=True)
            embed.add_field(name="模式", value=country.get("mode", "blacklist"), inline=True)
            embed.add_field(name="地區", value=", ".join(country.get("countries", [])) or "未設定", inline=False)
            channel_id = country.get("channel_id")
            channel = self.session.guild.get_channel(int(channel_id)) if channel_id and str(channel_id).isdigit() else None
            embed.add_field(name="警示頻道", value=channel.mention if channel else "未設定", inline=False)
        else:
            notify = self.draft["notify"]
            country = self.draft["webverify_country_alert"]
            role_id = self.draft.get("unverified_role_id")
            role = self.session.guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() else None
            embed.description = "確認後會一次儲存整份網頁驗證設定。"
            embed.add_field(name="功能 / CAPTCHA", value=f"{'啟用' if self.draft['enabled'] else '停用'} / {self.draft['captcha_type']}", inline=False)
            embed.add_field(name="未驗證身分組", value=role.mention if role else "未設定", inline=False)
            embed.add_field(name="自動分配", value=f"{'啟用' if self.draft['autorole_enabled'] else '停用'} / {self.draft['autorole_trigger']}", inline=False)
            embed.add_field(name="通知", value=notify.get("type", "dm"), inline=True)
            embed.add_field(name="地區警示", value="啟用" if country.get("enabled") else "停用", inline=True)
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
            await interaction.response.send_message("啟用自動分配前，請先選擇未驗證身分組。", ephemeral=True)
            return
        self.step = min(6, self.step + 1)
        await self.refresh(interaction)

    def validate(self) -> str | None:
        if self.draft.get("autorole_enabled") and not self.draft.get("unverified_role_id"):
            return "啟用自動分配時必須設定未驗證身分組。"
        notify = self.draft["notify"]
        if notify.get("type") in ("channel", "both") and not notify.get("channel_id"):
            return "使用頻道通知時必須選擇通知頻道。"
        country = self.draft["webverify_country_alert"]
        if country.get("enabled"):
            if not country.get("channel_id"):
                return "啟用地區警示時必須選擇警示頻道。"
            if not country.get("countries"):
                return "啟用地區警示時必須至少設定一個地區代碼。"
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
            embed.description = f"驗證訊息已發送至 {sent_message.channel.mention}。\n\n" + (embed.description or "")
        await self.session.render(interaction, embed=embed, view=target)

    async def send_verify_message(self, interaction: discord.Interaction):
        notify = self.draft["notify"]
        channel_id = notify.get("channel_id")
        channel = self.session.guild.get_channel(int(channel_id)) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("找不到驗證通知頻道，設定已儲存但未發送訊息。", ephemeral=True)
            return None
        bot_member = self.session.guild.me
        permissions = channel.permissions_for(bot_member) if bot_member else None
        if permissions is None or not permissions.view_channel or not permissions.send_messages:
            await interaction.followup.send("我沒有權限在驗證通知頻道發送訊息。", ephemeral=True)
            return None
        application_id = bot.application_id or (bot.user.id if bot.user else None)
        if application_id is None:
            await interaction.followup.send("目前無法取得應用程式 ID。", ephemeral=True)
            return None
        query = urlencode({"redirect_uri": config("webverify_url"), "state": self.session.guild.id})
        verify_url = (
            f"https://discord.com/oauth2/authorize?client_id={application_id}"
            f"&response_type=code&scope=identify&prompt=none&{query}"
        )
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="前往驗證", style=discord.ButtonStyle.link, url=verify_url))
        embed = discord.Embed(
            title=notify.get("title") or "伺服器網頁驗證",
            description=notify.get("message") or "請點擊下方按鈕進行網頁驗證：",
            color=discord.Color.green(),
        )
        try:
            return await channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException) as error:
            await interaction.followup.send(f"發送驗證訊息失敗：{error}", ephemeral=True)
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
            "只有具備管理伺服器權限的成員可以開啟快速設定。",
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
        await interaction.response.send_message("AutoReply 模組目前無法使用。", ephemeral=True)
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
                "只有具備管理伺服器權限的成員可以開啟快速設定。",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="快速設定伺服器",
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
            await recipient.send(
                f"我找不到能在 **{guild.name}** 發送快速設定按鈕的頻道。"
                f"請到伺服器內使用 {command} 開啟設定中心。"
            )
            log(
                "找不到合適的快速設定頻道，已私訊管理員",
                module_name="gettingstarted",
                user=recipient,
                guild=guild,
            )
        except discord.Forbidden:
            log(
                "找不到合適的快速設定頻道，也無法私訊管理員",
                level=logging.WARNING,
                module_name="gettingstarted",
                user=recipient,
                guild=guild,
            )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
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
            title="快速設定機器人",
            description="使用下方按鈕設定這個伺服器的管理、通知、經濟、自動回覆、驗證與其他功能。",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)
        try:
            await channel.send(
                content=f"{recipient.mention} 要現在快速設定機器人嗎？",
                embed=embed,
                view=GettingStartedLauncherView(),
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    roles=False,
                    users=[recipient],
                ),
            )
            log(
                f"已在 {channel.name} 發送快速設定入口",
                module_name="gettingstarted",
                user=recipient,
                guild=guild,
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            log(
                f"發送快速設定入口失敗: {error}",
                level=logging.ERROR,
                module_name="gettingstarted",
                user=recipient,
                guild=guild,
            )
            await self.send_dm_fallback(guild, recipient)

    @app_commands.command(name="gettingstarted", description="開啟伺服器快速設定中心")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def getting_started(self, interaction: discord.Interaction):
        await start_getting_started(interaction)


asyncio.run(bot.add_cog(GettingStarted(bot)))
