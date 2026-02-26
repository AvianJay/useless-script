import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, start_bot, get_user_data, set_user_data, get_all_user_data, get_server_config, set_server_config, modules, config, get_command_mention
from datetime import datetime, timezone, timedelta
import asyncio
from typing import Optional
from difflib import SequenceMatcher
import re
import emoji
import sqlite3
import io
from logger import log
import logging

if "Moderate" in modules:
    import Moderate
else:
    log("Moderate module not found", level=logging.ERROR, module_name="AutoModerate")


all_settings = [
    "escape_punish-punishment",
    "escape_punish-duration",
    "too_many_h1-max_length",
    "too_many_h1-action",
    "too_many_emojis-max_emojis",
    "too_many_emojis-action",
    "scamtrap-channel_id",
    "scamtrap-action",
    "anti_uispam-max_count",
    "anti_uispam-time_window",
    "anti_uispam-action",
    "anti_raid-max_joins",
    "anti_raid-time_window",
    "anti_raid-action",
    "anti_spam-max_messages",
    "anti_spam-time_window",
    "anti_spam-similarity",
    "anti_spam-action",
]

# 用於追蹤 user install spam 的記憶體字典
# 結構: {guild_id: {user_id: [timestamp1, timestamp2, ...]}}
_uispam_tracker: dict[int, dict[int, list[datetime]]] = {}

# 用於追蹤 raid（大量用戶加入）的記憶體字典
# 結構: {guild_id: [(member, join_time), ...]}
_raid_tracker: dict[int, list[tuple[discord.Member, datetime]]] = {}

# 用於追蹤用戶刷頻的記憶體字典
# 結構: {guild_id: {user_id: [(content, timestamp), ...]}}
_spam_tracker: dict[int, dict[int, list[tuple[str, datetime]]]] = {}

def _text_similarity(a: str, b: str) -> float:
    """計算兩個字串的相似度 (0.0 ~ 1.0)"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()

async def settings_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=app_commands.locale_str(key), value=key)
        for key in all_settings if current.lower() in key.lower()
    ][:25]  # Discord 限制最多 25 個選項

async def do_action_str(action: str, guild: Optional[discord.Guild] = None, user: Optional[discord.Member] = None, message: Optional[discord.Message] = None):
    moderator = bot.user
    # if user is none just check if action is valid
    action_parts = action.split(",")
    action_parts = [a.strip() for a in action_parts]
    if len(action_parts) > 5:
        return ["錯誤：一次只能執行最多5個動作。"]
    logs = []
    last_reason = "自動管理執行"
    actions = []
    for a in action_parts:
        cmd = a.split(" ")
        if cmd[0] == "ban":
            # ban <reason> <delete_messages> <duration>
            if len(cmd) == 1:
                cmd.append("0s")
            if len(cmd) == 2:
                cmd.append("0s")
            if len(cmd) == 3:
                cmd.append(last_reason)

            duration_seconds = Moderate.timestr_to_seconds(cmd[1]) if cmd[1] != "0" else 0
            delete_messages = Moderate.timestr_to_seconds(cmd[2]) if cmd[2] != "0" else 0
            cmd.pop(0)  # remove "ban"
            cmd.pop(0)  # remove duration
            cmd.pop(0)  # remove delete_messages
            reason = " ".join(cmd)
            last_reason = reason
            logs.append(f"封禁用戶，原因: {reason}，持續秒數: {duration_seconds}秒，刪除訊息時間: {delete_messages}秒")
            if user:
                await Moderate.ban_user(guild, user, reason=reason, duration=duration_seconds, delete_message_seconds=delete_messages)
            actions.append({"action": "ban", "duration": duration_seconds, "reason": reason})
        elif cmd[0] == "kick":
            # kick <reason>
            if len(cmd) == 1:
                cmd.append(last_reason)
            cmd.pop(0)  # remove "kick"
            reason = " ".join(cmd)
            logs.append(f"踢出用戶，原因: {reason}")
            if user:
                await user.kick(reason=reason)
            actions.append({"action": "kick", "reason": reason})
        elif cmd[0] == "mute" or cmd[0] == "timeout":
            # mute <duration> <reason>
            if len(cmd) == 1:
                cmd.append("10m")
            if len(cmd) == 2:
                cmd.append(last_reason)
            duration_seconds = Moderate.timestr_to_seconds(cmd[1]) if cmd[1] != "0" else 0
            cmd.pop(0)  # remove "mute" or "timeout"
            cmd.pop(0)  # remove duration
            reason = " ".join(cmd) if cmd else last_reason
            logs.append(f"禁言用戶，原因: {reason}，持續秒數: {duration_seconds}秒")
            if user:
                await user.timeout(datetime.now(timezone.utc) + timedelta(seconds=duration_seconds), reason=reason)
            actions.append({"action": "mute", "duration": duration_seconds, "reason": reason})
        elif cmd[0] == "unban":
            # unban <reason>
            if len(cmd) == 1:
                cmd.append(last_reason)
            cmd.pop(0)  # remove "unban"
            reason = " ".join(cmd)
            last_reason = reason
            logs.append(f"解封用戶，原因: {reason}")
            if guild and user:
                try:
                    await guild.unban(user, reason=reason)
                    set_user_data(guild.id, user.id, "unban_time", None)
                except Exception as e:
                    log(f"解封用戶 {user} 時發生錯誤：{e}", level=logging.ERROR, module_name="Moderate", guild=guild)
            actions.append({"action": "unban", "reason": reason})
        elif cmd[0] == "unmute" or cmd[0] == "untimeout":
            # unmute <reason>
            if len(cmd) == 1:
                cmd.append(last_reason)
            cmd.pop(0)  # remove "unmute" or "untimeout"
            reason = " ".join(cmd)
            logs.append(f"解除禁言用戶，原因: {reason}")
            if user:
                await user.timeout(None, reason=reason)
            actions.append({"action": "unmute", "reason": reason})
        elif cmd[0] == "delete" or cmd[0] == "delete_dm":
            # delete <warn_message>
            logs.append("刪除訊息")
            if message:
                await message.delete()
            if len(cmd) > 1:
                msg = cmd.copy()
                msg.pop(0)
                warn_message = " ".join(msg)
                warn_message = warn_message.replace("{user}", user.mention if user else "用戶")
                logs.append(f"並警告: {warn_message}")
                if cmd[0] == "delete_dm" and user:
                    await user.send(warn_message)
                elif message:
                    await message.channel.send(warn_message)
        elif cmd[0] == "warn" or cmd[0] == "warn_dm":
            # warn <warn_message>
            if len(cmd) == 1:
                cmd.append(f"{user.mention if user else '用戶'}，請注意你的行為。")
            msg = cmd.copy()
            msg.pop(0)
            warn_message = " ".join(msg)
            warn_message = warn_message.replace("{user}", user.mention if user else "用戶")
            logs.append(f"傳送警告訊息: {warn_message}")
            if cmd[0] == "warn_dm" and user:
                await user.send(warn_message)
            elif message:
                await message.reply(warn_message)
        elif cmd[0] == "send_mod_message" or cmd[0] == "smm":
            # send_mod_message
            if len(cmd) == 1:
                cmd.append("用戶被系統處置。")
            logs.append("傳送管理訊息")
            if guild and user and moderator:
                await Moderate.moderation_message_settings(None, user, moderator, actions, direct=True)
        elif cmd[0] == "force_verify":
            # force_verify <duration>
            if "ServerWebVerify" in modules:
                from ServerWebVerify import force_verify_user
                if user:
                    success, message = await force_verify_user(guild, user)
                    logs.append(message)
                if len(cmd) > 1:
                    duration_seconds = Moderate.timestr_to_seconds(cmd[1]) if cmd[1] != "0" else 0
                    until_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
                    logs.append(f"強制驗證持續秒數: {duration_seconds}秒")
                    set_server_config(guild.id, "force_verify_until", until_time.timestamp())
            else:
                logs.append("無法執行 force_verify，因為 ServerWebVerify 模組未找到")
    return logs


# 快速設定的處置預設選項（value 為 __custom__ 時會跳出 Modal 讓使用者輸入）
ACTION_PRESETS = [
    ("刪除訊息", "delete"),
    ("刪除＋警告", "delete {user}，請注意你的行為。"),
    ("公開警告", "warn {user}，請注意你的行為。"),
    ("禁言 10 分鐘", "mute 10m 違規"),
    ("禁言 1 小時", "mute 1h 違規"),
    ("踢出", "kick 違規"),
    ("封禁", "ban 0 0 違規"),
    ("強制驗證 1 天", "force_verify 1d"),
    ("自訂...", "__custom__"),
]


class CustomActionModal(discord.ui.Modal, title="自訂處置動作"):
    action_input = discord.ui.TextInput(
        label="處置動作指令",
        placeholder="例：mute 30m 刷頻, delete {user} 請勿刷頻",
        required=True,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, view: "QuickSetupView"):
        super().__init__()
        self.quick_setup_view = view

    async def on_submit(self, interaction: discord.Interaction):
        self.quick_setup_view.config["action"] = self.action_input.value.strip()
        await interaction.response.edit_message(
            embed=self.quick_setup_view._get_embed(interaction.guild),
            view=self.quick_setup_view,
        )


class QuickSetupView(discord.ui.View):
    """互動式快速設定精靈"""
    def __init__(self, guild_id: int, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.step = 1
        self.feature = None
        self.config = {}

    def _get_embed(self, guild: discord.Guild):
        embed = discord.Embed(title="⚡ 自動管理快速設定", color=0x5865F2)
        if self.step == 1:
            embed.description = "請選擇要設定的功能："
        elif self.step == 2 and self.feature:
            feat_names = {
                "scamtrap": "🪤 詐騙陷阱",
                "escape_punish": "🏃 逃避責任懲處",
                "too_many_h1": "📢 標題過多",
                "too_many_emojis": "😂 表情符號過多",
                "anti_uispam": "📲 用戶安裝應用程式濫用",
                "anti_raid": "🚨 防突襲",
                "anti_spam": "🔁 防刷頻",
            }
            embed.description = f"正在設定 **{feat_names.get(self.feature, self.feature)}**\n請完成下方選項後點擊「完成設定」。"
            if self.config:
                for k, v in self.config.items():
                    if k == "channel_id" and v:
                        ch = guild.get_channel(int(v))
                        embed.add_field(name="頻道", value=ch.mention if ch else v, inline=False)
                    elif k == "action":
                        embed.add_field(name="處置動作", value=f"`{str(v)[:50]}{'...' if len(str(v)) > 50 else ''}`", inline=False)
                    else:
                        embed.add_field(name=k, value=str(v), inline=True)
        return embed

    def _update_components_step1(self):
        self.clear_items()
        opts = [
            discord.SelectOption(label="詐騙陷阱", value="scamtrap", description="蜜罐頻道"),
            discord.SelectOption(label="逃避責任懲處", value="escape_punish", description="禁言期間離開者"),
            discord.SelectOption(label="標題過多", value="too_many_h1", description="Markdown 大標題洗版"),
            discord.SelectOption(label="表情符號過多", value="too_many_emojis", description="過多 emoji"),
            discord.SelectOption(label="用戶安裝應用程式濫用", value="anti_uispam", description="User Install 濫用"),
            discord.SelectOption(label="防突襲", value="anti_raid", description="大量加入偵測"),
            discord.SelectOption(label="防刷頻", value="anti_spam", description="相似訊息刷頻"),
        ]
        sel = discord.ui.Select(placeholder="選擇功能", options=opts)
        sel.callback = self._on_feature_select
        self.add_item(sel)

    def _update_components_step2(self, guild: discord.Guild):
        self.clear_items()
        automod_settings = get_server_config(self.guild_id, "automod", {}).get(self.feature, {})
        defaults = automod_settings.copy()

        if self.feature == "scamtrap":
            ch_sel = discord.ui.ChannelSelect(
                placeholder="選擇陷阱頻道",
                channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                min_values=1, max_values=1,
            )
            ch_sel.callback = self._on_scamtrap_channel
            self.add_item(ch_sel)
        elif self.feature == "escape_punish":
            punish_sel = discord.ui.Select(placeholder="懲處方式", options=[
                discord.SelectOption(label="封禁", value="ban", description="永久封禁"),
            ])
            punish_sel.callback = self._on_escape_punish_select
            self.add_item(punish_sel)
            dur_sel = discord.ui.Select(placeholder="封禁時長", options=[
                discord.SelectOption(label="永久", value="0"),
                discord.SelectOption(label="7 天", value="7d"),
                discord.SelectOption(label="30 天", value="30d"),
            ])
            dur_sel.callback = self._on_escape_duration_select
            self.add_item(dur_sel)
            # escape_punish 不需 action
            btn = discord.ui.Button(label="完成設定", style=discord.ButtonStyle.success)
            btn.callback = self._on_finish
            self.add_item(btn)
            return
        elif self.feature == "too_many_h1":
            len_sel = discord.ui.Select(placeholder="最大標題字數", options=[
                discord.SelectOption(label="15", value="15"),
                discord.SelectOption(label="20", value="20"),
                discord.SelectOption(label="30", value="30"),
                discord.SelectOption(label="50", value="50"),
            ])
            len_sel.callback = self._on_h1_length_select
            self.add_item(len_sel)
        elif self.feature == "too_many_emojis":
            emoji_sel = discord.ui.Select(placeholder="最大表情符號數", options=[
                discord.SelectOption(label="5", value="5"),
                discord.SelectOption(label="10", value="10"),
                discord.SelectOption(label="15", value="15"),
                discord.SelectOption(label="20", value="20"),
            ])
            emoji_sel.callback = self._on_emojis_select
            self.add_item(emoji_sel)
        elif self.feature == "anti_uispam":
            cnt_sel = discord.ui.Select(placeholder="時間窗口內最大觸發次數", options=[
                discord.SelectOption(label="3", value="3"),
                discord.SelectOption(label="5", value="5"),
                discord.SelectOption(label="10", value="10"),
            ])
            cnt_sel.callback = self._on_uispam_count_select
            self.add_item(cnt_sel)
            win_sel = discord.ui.Select(placeholder="偵測時間窗口（秒）", options=[
                discord.SelectOption(label="30 秒", value="30"),
                discord.SelectOption(label="60 秒", value="60"),
                discord.SelectOption(label="120 秒", value="120"),
            ])
            win_sel.callback = self._on_uispam_window_select
            self.add_item(win_sel)
        elif self.feature == "anti_raid":
            joins_sel = discord.ui.Select(placeholder="時間窗口內最大加入數", options=[
                discord.SelectOption(label="3", value="3"),
                discord.SelectOption(label="5", value="5"),
                discord.SelectOption(label="10", value="10"),
            ])
            joins_sel.callback = self._on_raid_joins_select
            self.add_item(joins_sel)
            win_sel = discord.ui.Select(placeholder="偵測時間窗口（秒）", options=[
                discord.SelectOption(label="30 秒", value="30"),
                discord.SelectOption(label="60 秒", value="60"),
                discord.SelectOption(label="120 秒", value="120"),
            ])
            win_sel.callback = self._on_raid_window_select
            self.add_item(win_sel)
        elif self.feature == "anti_spam":
            msg_sel = discord.ui.Select(placeholder="最大相似訊息數", options=[
                discord.SelectOption(label="3", value="3"),
                discord.SelectOption(label="5", value="5"),
                discord.SelectOption(label="10", value="10"),
            ])
            msg_sel.callback = self._on_spam_messages_select
            self.add_item(msg_sel)
            win_sel = discord.ui.Select(placeholder="偵測時間窗口（秒）", options=[
                discord.SelectOption(label="30 秒", value="30"),
                discord.SelectOption(label="60 秒", value="60"),
            ])
            win_sel.callback = self._on_spam_window_select
            self.add_item(win_sel)
            sim_sel = discord.ui.Select(placeholder="相似度閾值", options=[
                discord.SelectOption(label="50%", value="50"),
                discord.SelectOption(label="75%", value="75"),
                discord.SelectOption(label="90%", value="90"),
            ])
            sim_sel.callback = self._on_spam_similarity_select
            self.add_item(sim_sel)

        action_opts = [discord.SelectOption(label=l, value=v) for l, v in ACTION_PRESETS]
        action_sel = discord.ui.Select(placeholder="處置動作（選一個）", options=action_opts)
        action_sel.callback = self._on_action_select
        self.add_item(action_sel)

        btn = discord.ui.Button(label="完成設定", style=discord.ButtonStyle.success)
        btn.callback = self._on_finish
        self.add_item(btn)

    async def _on_feature_select(self, interaction: discord.Interaction):
        self.feature = interaction.data["values"][0]
        self.step = 2
        self.config = {}
        feat_defaults = {
            "too_many_h1": {"max_length": "20"},
            "too_many_emojis": {"max_emojis": "10"},
            "anti_uispam": {"max_count": "5", "time_window": "60"},
            "anti_raid": {"max_joins": "5", "time_window": "60"},
            "anti_spam": {"max_messages": "5", "time_window": "30", "similarity": "75"},
            "escape_punish": {"punishment": "ban", "duration": "0"},
        }
        self.config = feat_defaults.get(self.feature, {}).copy()
        self._update_components_step2(interaction.guild)
        await interaction.response.edit_message(embed=self._get_embed(interaction.guild), view=self)

    async def _on_scamtrap_channel(self, interaction: discord.Interaction):
        self.config["channel_id"] = str(interaction.data["values"][0])
        await interaction.response.defer_update()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_escape_punish_select(self, interaction: discord.Interaction):
        self.config["punishment"] = interaction.data["values"][0]
        await interaction.response.defer_update()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_escape_duration_select(self, interaction: discord.Interaction):
        self.config["duration"] = interaction.data["values"][0]
        await interaction.response.defer_update()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_h1_length_select(self, interaction: discord.Interaction):
        self.config["max_length"] = interaction.data["values"][0]
        await interaction.response.defer_update()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_emojis_select(self, interaction: discord.Interaction):
        self.config["max_emojis"] = interaction.data["values"][0]
        await interaction.response.defer_update()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_uispam_count_select(self, interaction: discord.Interaction):
        self.config["max_count"] = interaction.data["values"][0]
        await interaction.response.defer_update()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_uispam_window_select(self, interaction: discord.Interaction):
        self.config["time_window"] = interaction.data["values"][0]
        await interaction.response.defer_update()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_raid_joins_select(self, interaction: discord.Interaction):
        self.config["max_joins"] = interaction.data["values"][0]
        await interaction.response.defer_update()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_raid_window_select(self, interaction: discord.Interaction):
        self.config["time_window"] = interaction.data["values"][0]
        await interaction.response.defer_update()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_spam_messages_select(self, interaction: discord.Interaction):
        self.config["max_messages"] = interaction.data["values"][0]
        await interaction.response.defer_update()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_spam_window_select(self, interaction: discord.Interaction):
        self.config["time_window"] = interaction.data["values"][0]
        await interaction.response.defer_update()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_spam_similarity_select(self, interaction: discord.Interaction):
        self.config["similarity"] = interaction.data["values"][0]
        await interaction.response.defer_update()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_action_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        if value == "__custom__":
            modal = CustomActionModal(self)
            await interaction.response.send_modal(modal)
            return
        self.config["action"] = value
        await interaction.response.defer_update()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_finish(self, interaction: discord.Interaction):
        if self.feature not in ("scamtrap", "escape_punish", "too_many_h1", "too_many_emojis", "anti_uispam", "anti_raid", "anti_spam"):
            await interaction.response.send_message("無效的功能。", ephemeral=True)
            return
        if self.feature == "scamtrap" and "channel_id" not in self.config:
            await interaction.response.send_message("詐騙陷阱請先選擇陷阱頻道。", ephemeral=True)
            return
        if "action" not in self.config and self.feature in ("scamtrap", "too_many_h1", "too_many_emojis", "anti_uispam", "anti_raid", "anti_spam"):
            await interaction.response.send_message("請選擇處置動作。", ephemeral=True)
            return

        automod_settings = get_server_config(self.guild_id, "automod", {})
        automod_settings.setdefault(self.feature, {})
        automod_settings[self.feature]["enabled"] = True
        for k, v in self.config.items():
            if k and v is not None:
                automod_settings[self.feature][k] = str(v)
        set_server_config(self.guild_id, "automod", automod_settings)

        feat_names = {"scamtrap": "詐騙陷阱", "escape_punish": "逃避責任懲處", "too_many_h1": "標題過多",
                      "too_many_emojis": "表情符號過多", "anti_uispam": "用戶安裝應用程式濫用",
                      "anti_raid": "防突襲", "anti_spam": "防刷頻"}
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(title="✅ 設定完成", color=0x00ff00,
                description=f"已完成 **{feat_names.get(self.feature, self.feature)}** 的快速設定並啟用。"),
            view=None,
        )

    async def on_timeout(self):
        self.stop()


def parse_mention_to_id(mention: str) -> str:
    # 解析用戶、頻道或角色的提及格式，返回ID
    match = re.match(r"<@!?(\d+)>", mention)  # 用戶提及
    if match:
        return match.group(1)
    match = re.match(r"<#(\d+)>", mention)  # 頻道提及
    if match:
        return match.group(1)
    match = re.match(r"<@&(\d+)>", mention)  # 角色提及
    if match:
        return match.group(1)
    return mention  # 如果不是提及格式，直接返回原字符串
    


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class AutoModerate(commands.GroupCog, name=app_commands.locale_str("automod")):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()
        
    @app_commands.command(name=app_commands.locale_str("view"), description="查看自動管理設定")
    async def view_automod_settings(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        automod_settings = get_server_config(guild_id, "automod", {})
        if not automod_settings:
            await interaction.response.send_message("自動管理尚未啟用。", ephemeral=True)
            return

        embed = discord.Embed(title="自動管理設定", color=0x00ff00)
        desc = ""
        for key, value in automod_settings.items():
            desc += f"**{key}**:"
            for subkey, subvalue in value.items():
                desc += f"\n - {subkey}: {subvalue}"
            desc += "\n"
        embed.description = desc
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name=app_commands.locale_str("toggle"), description="啟用或停用自動管理設定")
    @app_commands.describe(setting="要啟用或停用的自動管理設定名稱", enable="是否啟用該設定")
    @app_commands.choices(
        setting=[
            app_commands.Choice(name="詐騙陷阱", value="scamtrap"),
            app_commands.Choice(name="逃避責任懲處", value="escape_punish"),
            app_commands.Choice(name="標題過多", value="too_many_h1"),
            app_commands.Choice(name="表情符號過多", value="too_many_emojis"),
            app_commands.Choice(name="用戶安裝應用程式濫用", value="anti_uispam"),
            app_commands.Choice(name="防突襲（大量加入偵測）", value="anti_raid"),
            app_commands.Choice(name="防刷頻", value="anti_spam"),
        ],
        enable=[
            app_commands.Choice(name="啟用", value="True"),
            app_commands.Choice(name="停用", value="False"),
        ]
    )
    async def toggle_automod_setting(self, interaction: discord.Interaction, setting: str, enable: str):
        guild_id = interaction.guild.id if interaction.guild else None
        automod_settings = get_server_config(guild_id, "automod", {})
        automod_settings.setdefault(setting, {})["enabled"] = (enable == "True")
        set_server_config(guild_id, "automod", automod_settings)
        await interaction.response.send_message(f"已將自動管理設定 '{setting}' 設為 {'啟用' if enable == 'True' else '停用'}。")
        
        if setting == "scamtrap" and enable == "True":
            # settings
            if "channel_id" not in automod_settings.get("scamtrap", {}):
                await interaction.followup.send(f"請注意，詐騙陷阱已啟用，但尚未設定頻道ID。請使用 {await get_command_mention('automod', 'settings')} 來設定頻道ID。", ephemeral=True)
            if "action" not in automod_settings.get("scamtrap", {}):
                await interaction.followup.send(f"請注意，詐騙陷阱已啟用，但尚未設定動作指令。請使用 {await get_command_mention('automod', 'settings')} 來設定動作指令。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("quick-setup"), description="互動式快速設定精靈（選單引導）")
    async def quick_setup_automod(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else 0
        view = QuickSetupView(guild_id)
        view._update_components_step1()
        await interaction.response.send_message(
            embed=view._get_embed(interaction.guild),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name=app_commands.locale_str("settings"), description="設定自動管理選項")
    @app_commands.describe(
        setting="要設定的自動管理選項",
        value="選項的值"
    )
    @app_commands.autocomplete(setting=settings_autocomplete)
    async def set_automod_setting(self, interaction: discord.Interaction, setting: str, value: str):
        guild_id = interaction.guild.id if interaction.guild else None
        automod_settings = get_server_config(guild_id, "automod", {})
        setting_base = setting.split("-")[0]
        setting_key = setting.split("-")[1] if len(setting.split("-")) > 1 else None
        if setting_base not in automod_settings:
            automod_settings[setting_base] = {}
        value = parse_mention_to_id(value) if setting_key in ["channel_id"] else value
        automod_settings[setting_base][setting_key] = value
        set_server_config(guild_id, "automod", automod_settings)
        await interaction.response.send_message(f"已將自動管理設定 '{setting}' 設為 {value}。")
    
    @app_commands.command(name=app_commands.locale_str("check-action"), description="檢查自動管理動作指令是否有效")
    @app_commands.describe(action="要檢查的動作指令")
    async def check_automod_action(self, interaction: discord.Interaction, action: str):
        try:
            actions = await do_action_str(action)
        except Exception as e:
            await interaction.response.send_message(f"無法解析動作指令: {e}", ephemeral=True)
            return
        actions = [f"- {a}" for a in actions]
        actions_str = "\n".join(actions) if actions else "無動作"
        msg = f"指令有效，解析出的動作:\n{actions_str}"
        await interaction.response.send_message(content=msg)

    @app_commands.command(name=app_commands.locale_str("action-builder"), description="產生動作指令字串")
    @app_commands.describe(
        action_type="動作類型",
        duration="時長（mute/ban/force_verify 用），如 10m、7d、0 表示永久",
        delete_message_duration="ban 專用：刪除該用戶最近多少時間的訊息，如 1d、0 表示不刪",
        reason="原因（mute/kick/ban 用）",
        message="警告訊息（delete/warn 用），可用 {user} 代表用戶",
        prepend="要接在此動作前面的既有指令（用逗號分隔多個動作時）",
    )
    @app_commands.choices(
        action_type=[
            app_commands.Choice(name="刪除訊息", value="delete"),
            app_commands.Choice(name="刪除訊息＋私訊警告", value="delete_dm"),
            app_commands.Choice(name="公開警告", value="warn"),
            app_commands.Choice(name="私訊警告", value="warn_dm"),
            app_commands.Choice(name="禁言", value="mute"),
            app_commands.Choice(name="踢出", value="kick"),
            app_commands.Choice(name="封禁", value="ban"),
            app_commands.Choice(name="傳送管理通知", value="send_mod_message"),
            app_commands.Choice(name="強制驗證", value="force_verify"),
        ],
    )
    async def action_builder(
        self,
        interaction: discord.Interaction,
        action_type: str,
        duration: Optional[str] = None,
        delete_message_duration: Optional[str] = None,
        reason: Optional[str] = None,
        message: Optional[str] = None,
        prepend: Optional[str] = None,
    ):
        parts = []
        if action_type == "delete":
            parts = ["delete"]
            if message:
                parts.append(message)
        elif action_type == "delete_dm":
            parts = ["delete_dm"]
            if message:
                parts.append(message)
        elif action_type == "warn":
            parts = ["warn"]
            parts.append(message or "{user}，請注意你的行為。")
        elif action_type == "warn_dm":
            parts = ["warn_dm"]
            parts.append(message or "{user}，請注意你的行為。")
        elif action_type == "force_verify":
            parts = ["force_verify"]
            if duration:
                parts.append(duration)
        elif action_type == "mute":
            parts = ["mute", duration or "10m"]
            if reason:
                parts.append(reason)
        elif action_type == "kick":
            parts = ["kick"]
            if reason:
                parts.append(reason)
        elif action_type == "ban":
            parts = ["ban", duration or "0", delete_message_duration or "0"]
            if reason:
                parts.append(reason)
        elif action_type == "send_mod_message":
            parts = ["send_mod_message"]

        generated = " ".join(parts)
        if prepend and prepend.strip():
            generated = f"{prepend.strip()}, {generated}"
        if len([a for a in generated.split(",")]) > 5:
            await interaction.response.send_message("錯誤：動作總數不得超過 5 個。", ephemeral=True)
            return

        embed = discord.Embed(title="動作指令產生結果", color=0x00ff00)
        embed.description = f"```\n{generated}\n```"
        embed.add_field(name="使用方式", value=f"複製上方字串，用於 {await get_command_mention('automod', 'settings')} 的 action 值，或 {await get_command_mention('automod', 'setup')} 的 action 參數。", inline=False)
        try:
            preview = await do_action_str(generated)
            embed.add_field(name="預覽效果", value="\n".join(f"• {a}" for a in preview), inline=False)
        except Exception:
            pass
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("scan-flagged-users"), description="掃描並更新伺服器中的標記用戶")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(user="要掃描的用戶，若不指定則掃描所有用戶")
    async def scan_flagged_users(self, interaction: discord.Interaction, user: discord.User = None):
        await interaction.response.send_message("開始掃描標記用戶...")
        database_file = config("flagged_database_path", "flagged_data.db")
        conn = sqlite3.connect(database_file)
        cursor = conn.cursor()
        flagged_user = {}
        if user:
            cursor.execute('SELECT user_id, guild_id, flagged_at, flagged_role FROM flagged_users WHERE user_id = ?', (user.id,))
            results = cursor.fetchall()
            results = [dict(zip([column[0] for column in cursor.description], row)) for row in results]
            if results:
                for result in results:
                    cursor.execute('SELECT name FROM guilds WHERE id = ?', (result['guild_id'],))
                    guild_info = cursor.fetchone()
                    result['guild_name'] = guild_info[0] if guild_info else "未知伺服器"
                flagged_user[result['user_id']] = results
        else:
            for member in interaction.guild.members:
                cursor.execute('SELECT user_id, guild_id, flagged_at, flagged_role FROM flagged_users WHERE user_id = ?', (member.id,))
                results = cursor.fetchall()
                # convert to dict
                results = [dict(zip([column[0] for column in cursor.description], row)) for row in results]
                # get server info
                if results:
                    for result in results:
                        cursor.execute('SELECT name FROM guilds WHERE id = ?', (result['guild_id'],))
                        guild_info = cursor.fetchone()
                        result["user_name"] = member.name
                        result['guild_name'] = guild_info[0] if guild_info else "未知伺服器"
                    flagged_user[member.id] = results
        conn.close()
        if flagged_user:
            msg_lines = []
            for user_id, fu in flagged_user.items():
                member = interaction.guild.get_member(user_id)
                user_name = member.name if member else "未知用戶"
                msg_lines.append(f"用戶: {user_name} (ID: {user_id})")
                for entry in fu:
                    guild_name = entry.get('guild_name', '未知伺服器')
                    flagged_at = entry.get('flagged_at', '未知時間')
                    flagged_role = entry.get('flagged_role', 0)
                    msg_lines.append(f" - 伺服器: {guild_name}, 標記時間: {flagged_at}{', 擁有被標記的身份組' if flagged_role else ''}")
                msg_lines.append("")  # 空行分隔不同用戶
            msg = "\n".join(msg_lines)
            file = discord.File(io.StringIO(msg), filename="flagged_users.txt")
            await interaction.followup.send(file=file)
        else:
            await interaction.followup.send("掃描完成！未找到任何標記用戶。")

    @app_commands.command(name=app_commands.locale_str("flagged-user-alert-channel"), description="設置用戶加入伺服器時的通知頻道")
    @app_commands.describe(channel="用於接收用戶加入通知的頻道")
    async def set_flagged_user_onjoin_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        set_server_config(interaction.guild.id, "flagged_user_onjoin_channel", channel.id)
        await interaction.response.send_message(f"已將用戶加入通知頻道設置為 {channel.mention}。")
    
    @app_commands.command(name=app_commands.locale_str("info"), description="查看自動管理功能介紹")
    async def automod_info(self, interaction: discord.Interaction):
        embed = discord.Embed(title="自動管理功能介紹", color=0x5865F2)
        embed.description = (
            "自動管理 (AutoModerate) 提供多種自動化保護功能，協助管理員維護伺服器秩序。\n"
            f"使用 {await get_command_mention('automod', 'quick-setup')} 互動式快速設定（推薦），"
            f"使用 {await get_command_mention('automod', 'setup')} 一次設定某功能的所有選項，"
            f"使用 {await get_command_mention('automod', 'toggle')} 啟用或停用功能，"
            f"使用 {await get_command_mention('automod', 'settings')} 單獨調整參數，"
            f"使用 {await get_command_mention('automod', 'view')} 查看目前設定。"
        )
        embed.add_field(
            name="🪤 詐騙陷阱 (scamtrap)",
            value="設定一個蜜罐頻道，任何在該頻道發送訊息的用戶將被自動處置。\n"
                  "設定項: `channel_id`（頻道）、`action`（處置動作）",
            inline=False
        )
        embed.add_field(
            name="🏃 逃避責任懲處 (escape_punish)",
            value="當用戶在禁言期間離開伺服器時，自動執行額外懲處（如封禁）。\n"
                  "設定項: `punishment`（懲處方式）、`duration`（持續時間）",
            inline=False
        )
        embed.add_field(
            name="📢 標題過多 (too_many_h1)",
            value="偵測訊息中 Markdown 大標題 (`# `) 的總字數過長，防止洗版。\n"
                  "設定項: `max_length`（最大字數，預設20）、`action`",
            inline=False
        )
        embed.add_field(
            name="😂 表情符號過多 (too_many_emojis)",
            value="偵測訊息中的表情符號數量（含自訂及 Unicode emoji），超過上限自動處置。\n"
                  "設定項: `max_emojis`（最大數量，預設10）、`action`",
            inline=False
        )
        embed.add_field(
            name="📲 用戶安裝應用程式濫用 (anti_uispam)",
            value="偵測用戶透過 User Install 方式觸發的指令頻率，防止濫用。\n"
                  "設定項: `max_count`（最大次數，預設5）、`time_window`（秒，預設60）、`action`",
            inline=False
        )
        embed.add_field(
            name="🚨 防突襲 (anti_raid)",
            value="偵測短時間內大量用戶加入伺服器，觸發時對所有新加入者執行處置。\n"
                  "設定項: `max_joins`（最大加入數，預設5）、`time_window`（秒，預設60）、`action`",
            inline=False
        )
        embed.add_field(
            name="🔁 防刷頻 (anti_spam)",
            value="偵測用戶短時間內發送相同或高度相似的訊息。\n"
                  "設定項: `max_messages`（最大訊息數，預設5）、`time_window`（秒，預設30）、`similarity`（相似度閾值 0~100，預設75）、`action`",
            inline=False
        )
        embed.add_field(
            name="⚙️ 動作指令語法",
            value="動作可用逗號 `,` 串接，最多5個。可用動作:\n"
                  "`delete` / `delete_dm` — 刪除訊息（可附帶警告）\n"
                  "`warn` / `warn_dm` — 發送警告訊息\n"
                  "`mute <時長>` — 禁言用戶\n"
                  "`kick` — 踢出用戶\n"
                  "`ban <時長> <刪除訊息時長>` — 封禁用戶\n"
                  "`send_mod_message` — 傳送管理通知\n"
                  "`force_verify <時長>` — 強制驗證用戶 (需先啟用網頁驗證) \n"
                  f"使用 {await get_command_mention('automod', 'action-builder')} 產生動作字串，"
                  f"或 {await get_command_mention('automod', 'check-action')} 預覽效果。",
            inline=False
        )
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild_id = member.guild.id

        # 防突襲檢查
        automod_settings = get_server_config(guild_id, "automod", {})
        if automod_settings.get("anti_raid", {}).get("enabled", False):
            max_joins = int(automod_settings["anti_raid"].get("max_joins", 5))
            time_window = int(automod_settings["anti_raid"].get("time_window", 60))
            action = automod_settings["anti_raid"].get("action", "kick 突襲偵測自動封禁")
            
            now = datetime.now(timezone.utc)
            join_list = _raid_tracker.setdefault(guild_id, [])
            join_list.append((member, now))
            
            # 清除過期的記錄
            join_list[:] = [(m, t) for m, t in join_list if (now - t).total_seconds() < time_window]
            
            if len(join_list) >= max_joins:
                # 觸發 raid 偵測，對所有在時間窗口內加入的用戶執行動作
                raid_members = [m for m, t in join_list]
                log(f"偵測到突襲！{time_window}秒內有 {len(raid_members)} 個用戶加入，開始處理。", module_name="AutoModerate", guild=member.guild)
                for raid_member in raid_members:
                    try:
                        await do_action_str(action, guild=member.guild, user=raid_member)
                        log(f"突襲用戶 {raid_member} 已被處理: {action}", module_name="AutoModerate", user=raid_member, guild=member.guild)
                    except Exception as e:
                        log(f"無法對突襲用戶 {raid_member} 執行處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=raid_member, guild=member.guild)
                # 重置追蹤器避免重複處罰
                join_list.clear()

        channel_id = get_server_config(guild_id, "flagged_user_onjoin_channel")
        if not channel_id:
            return
        database_file = config("flagged_database_path", "flagged_data.db")
        conn = sqlite3.connect(database_file)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, guild_id, flagged_at, flagged_role FROM flagged_users WHERE user_id = ?', (member.id,))
        results = cursor.fetchall()
        results = [dict(zip([column[0] for column in cursor.description], row)) for row in results]
        if results:
            log(f"被標記的用戶 {member} ({len(results)}) 加入伺服器，發送通知。", module_name="AutoModerate", user=member, guild=member.guild)
            channel = member.guild.get_channel(channel_id)
            if channel:
                embed = discord.Embed(title="標記用戶加入伺服器", color=0xff0000)
                embed.add_field(name="用戶", value=f"{member.mention} (ID: {member.id})", inline=False)
                for result in results:
                    cursor.execute('SELECT name FROM guilds WHERE id = ?', (result['guild_id'],))
                    guild_info = cursor.fetchone()
                    guild_name = guild_info[0] if guild_info else "未知伺服器"
                    flagged_at = result.get('flagged_at', '未知時間')
                    embed.add_field(name=guild_name, value=f"標記時間: {flagged_at}{', 擁有被標記的身份組' if result.get('flagged_role', 0) else ''}", inline=False)
                await channel.send(embed=embed)
        conn.close()
                

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not after.guild:
            return
        if not after.timed_out_until:
            return
        set_user_data(guild_id=after.guild.id, user_id=after.id, key="communication_disabled_until", value=after.timed_out_until.isoformat() if after.timed_out_until else None)
        
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not member.guild:
            return
        server_config = get_server_config(member.guild.id, "automod", {})
        if not server_config.get("escape_punish", {}).get("enabled", False):
            return
        communication_disabled_until = get_user_data(guild_id=member.guild.id, user_id=member.id, key="communication_disabled_until")
        if communication_disabled_until:
            communication_disabled_until = datetime.fromisoformat(communication_disabled_until)
            if communication_disabled_until > datetime.now(timezone.utc):
                # 用戶在禁言期間離開，進行懲處
                punishment = server_config["escape_punish"].get("punishment", "ban")
                duration = server_config["escape_punish"].get("duration", "0")
                duration_seconds = Moderate.timestr_to_seconds(duration) if Moderate else 0
                try:
                    if punishment == "ban":
                        if Moderate:
                            await Moderate.ban_user(member.guild, member, reason="逃避禁言", duration=duration_seconds if duration_seconds > 0 else 0)
                        else:
                            print("[!] Moderate module not loaded, cannot ban user.")
                            raise Exception("Moderate module not loaded")
                    # 好像也就只有 ban 可以用了，我在做什麼呀
                    print(f"[+] 用戶 {member} 因逃避禁言被 {punishment}")
                except Exception as e:
                    print(f"[!] 無法對用戶 {member} 執行懲處: {e}")
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        guild_id = message.guild.id
        automod_settings = get_server_config(guild_id, "automod", {})
        
        # 用戶安裝應用程式濫用檢查（需在 bot 訊息過濾之前，因為 user install 的訊息作者是 bot）
        is_user_install_message = (
            message.interaction_metadata is not None 
            and message.interaction_metadata.is_user_integration()
            and not message.interaction_metadata.is_guild_integration()
        )
        
        if is_user_install_message and automod_settings.get("anti_uispam", {}).get("enabled", False):
            triggering_user = message.interaction_metadata.user
            member = message.guild.get_member(triggering_user.id)
            if member and not member.guild_permissions.administrator:
                max_count = int(automod_settings["anti_uispam"].get("max_count", 5))
                time_window = int(automod_settings["anti_uispam"].get("time_window", 60))
                action = automod_settings["anti_uispam"].get("action", "delete {user}，請勿濫用用戶安裝的應用程式指令。")
                
                now = datetime.now(timezone.utc)
                guild_tracker = _uispam_tracker.setdefault(guild_id, {})
                user_timestamps = guild_tracker.setdefault(triggering_user.id, [])
                
                # 清除過期的時間戳
                user_timestamps[:] = [ts for ts in user_timestamps if (now - ts).total_seconds() < time_window]
                
                # 記錄本次觸發
                user_timestamps.append(now)
                
                if len(user_timestamps) > max_count:
                    try:
                        target_member = member or triggering_user
                        await do_action_str(action, guild=message.guild, user=target_member, message=message)
                        log(f"用戶 {triggering_user} 因濫用用戶安裝應用程式被處理 (在 {time_window}秒內觸發 {len(user_timestamps)} 次): {action}", module_name="AutoModerate", user=triggering_user, guild=message.guild)
                        # 重置計數器避免重複處罰
                        user_timestamps.clear()
                    except Exception as e:
                        log(f"無法對用戶 {triggering_user} 執行用戶安裝應用程式濫用的處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=triggering_user, guild=message.guild)
        
        
        # 詐騙陷阱檢查
        if automod_settings.get("scamtrap", {}).get("enabled", False):
            scamtrap_channel_id = int(automod_settings["scamtrap"].get("channel_id", 0))
            action = automod_settings["scamtrap"].get("action", "delete 請不要在此頻道發送訊息。")
            target = message.author
            if message.author.bot:
                if message.interaction_metadata:
                    target = message.interaction_metadata.user
                else:
                    message.delete()
                    return
            if scamtrap_channel_id != 0 and message.channel.id == scamtrap_channel_id:
                try:
                    result = await do_action_str(action, guild=message.guild, user=target, message=message)
                    # print(f"[+] 用戶 {message.author} 因進入詐騙陷阱頻道被處理: {action}")
                    log(f"用戶 {target} 因進入詐騙陷阱頻道被處理: {action}\n執行結果: {'\n'.join(result)}", module_name="AutoModerate", user=target, guild=message.guild)
                except Exception as e:
                    # print(f"[!] 無法對用戶 {message.author} 執行詐騙陷阱的處理: {e}")
                    log(f"無法對用戶 {target} 執行詐騙陷阱的處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=target, guild=message.guild)

        if message.author.bot:
            return
        if message.author.guild_permissions.administrator:
            return

        # 標題過多檢查
        if automod_settings.get("too_many_h1", {}).get("enabled", False):
            max_length = int(automod_settings["too_many_h1"].get("max_length", 20))
            action = automod_settings["too_many_h1"].get("action", "warn")
            h1_count = 0
            split_lines = message.content.split("\n")
            for line in split_lines:
                line = line.lstrip()
                if line.startswith("# "):
                    # find custom emoji and replace with single character
                    while re.search(r'<a?:\w+:\d+>', line):
                        line = re.sub(r'<a?:\w+:\d+>', 'E', line, count=1)
                    line = line[2:]
                    h1_count += len(line)
            if h1_count > max_length:
                try:
                    await do_action_str(action, guild=message.guild, user=message.author, message=message)
                    # print(f"[+] 用戶 {message.author} 因標題長度過長被處理: {action}")
                    log(f"用戶 {message.author} 因標題長度過長被處理: {action}", module_name="AutoModerate", user=message.author, guild=message.guild)
                except Exception as e:
                    # print(f"[!] 無法對用戶 {message.author} 執行標題過多的處理: {e}")
                    log(f"無法對用戶 {message.author} 執行標題過多的處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=message.author, guild=message.guild)
        
        # 表情符號過多檢查
        if automod_settings.get("too_many_emojis", {}).get("enabled", False):
            max_emojis = int(automod_settings["too_many_emojis"].get("max_emojis", 10))
            action = automod_settings["too_many_emojis"].get("action", "warn")
            emoji_count = len(re.findall(r'<a?:\w+:\d+>', message.content))
            emoji_count += len([c for c in message.content if emoji.is_emoji(c)])
            if emoji_count > max_emojis:
                try:
                    await do_action_str(action, guild=message.guild, user=message.author, message=message)
                    log(f"用戶 {message.author} 因表情符號過多被處理: {action}", module_name="AutoModerate", user=message.author, guild=message.guild)
                except Exception as e:
                    log(f"無法對用戶 {message.author} 執行表情符號過多的處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=message.author, guild=message.guild)
        
        # 刷頻偵測檢查
        if automod_settings.get("anti_spam", {}).get("enabled", False):
            max_messages = int(automod_settings["anti_spam"].get("max_messages", 5))
            time_window = int(automod_settings["anti_spam"].get("time_window", 30))
            similarity_threshold = int(automod_settings["anti_spam"].get("similarity", 75)) / 100.0
            action = automod_settings["anti_spam"].get("action", "mute 10m 刷頻自動禁言, delete {user}，請勿刷頻。")
            
            now = datetime.now(timezone.utc)
            content = message.content.strip()
            guild_spam = _spam_tracker.setdefault(guild_id, {})
            user_history = guild_spam.setdefault(message.author.id, [])
            
            # 清除過期的記錄
            user_history[:] = [(c, t) for c, t in user_history if (now - t).total_seconds() < time_window]
            
            # 記錄本次訊息
            user_history.append((content, now))
            
            # 檢查是否有足夠多的相似訊息
            if len(user_history) >= max_messages:
                # 計算相似訊息數量：與最新訊息比較
                similar_count = 0
                for old_content, _ in user_history[:-1]:
                    if content == old_content or _text_similarity(content, old_content) >= similarity_threshold:
                        similar_count += 1
                
                # 如果相似訊息數 >= max_messages - 1（加上自身就是 >= max_messages）
                if similar_count >= max_messages - 1:
                    try:
                        await do_action_str(action, guild=message.guild, user=message.author, message=message)
                        log(f"用戶 {message.author} 因刷頻被處理 (在 {time_window}秒內發送 {similar_count + 1} 條相似訊息): {action}", module_name="AutoModerate", user=message.author, guild=message.guild)
                        # 重置計數器避免重複處罰
                        user_history.clear()
                    except Exception as e:
                        log(f"無法對用戶 {message.author} 執行刷頻的處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=message.author, guild=message.guild)

asyncio.run(bot.add_cog(AutoModerate(bot)))

if __name__ == "__main__":
    start_bot()
