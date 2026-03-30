import discord
from globalenv import bot, start_bot, set_server_config, get_server_config, get_user_data, set_user_data, get_db_connection, config, get_command_mention
from discord.ext import commands
from discord import app_commands
import asyncio
import ast
import copy
import math
import random
import json
import io
import aiohttp
from logger import log
import logging
import re
from datetime import datetime

DEFAULT_AUTOREPLY_CONFIG_LIMIT = 50
AUTOREPLY_RATE_LIMIT_COUNT = 3
AUTOREPLY_RATE_LIMIT_WINDOW = 1.0
AUTOREPLY_NEWMESSAGE_LIMIT = 2
AUTOREPLY_EDIT_LIMIT = 4
AUTOREPLY_DELAY_MIN_SECONDS = 1
AUTOREPLY_DELAY_MAX_SECONDS = 3
AUTOREPLY_USERVAR_LIMIT = 5
AUTOREPLY_GUILDVAR_LIMIT = 10
AUTOREPLY_VAR_MAX_LENGTH = 100
AUTOREPLY_MATH_EXPRESSION_MAX_LENGTH = 300
AUTOREPLY_MATH_AST_MAX_DEPTH = 64
AUTOREPLY_MATH_AST_MAX_NODES = 256
AUTOREPLY_VAR_KEY_PREFIX = "autoreply_var_"
AUTOREPLY_TEMPLATE_PACKS = {
    "daily_greetings": {
        "display_name": "日常問候包",
        "description": "早安 / 午安 / 晚安 / 安安，會依現在時間給不同回覆。",
        "rules": [
            {
                "trigger": ["早安", "早啊", "早安安"],
                "response": [
                    "{if:{hour}>=5:{if:{hour}<=11:早安 {user}，記得先喝水再開始今天。:現在都 {time24} 了，這句早安是不是送得有點晚。}:現在才 {time24}，你這不是早安，是熬夜安。}",
                    "{if:{hour}>=5:{if:{hour}<=11:早安！今天的待辦也要像鬧鐘一樣準時解決。:已經 {time} 了，現在比較像補發早安。}:凌晨 {time24} 說早安，我先當你還沒睡。}",
                    "{if:{hour}>=5:{if:{hour}<=11:早安！願你今天的 bug 比咖啡還少。:這個時間點說早安，我很難不吐槽一下。}:現在 {time24}，太拼了吧，先睡一下也行。}",
                ],
                "mode": "starts_with",
                "reply": True,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
            {
                "trigger": ["午安", "午安安"],
                "response": [
                    "{if:{hour}>=12:{if:{hour}<=17:午安 {user}，午餐有吃飽嗎？:現在都 {time24} 了，午安已經快過期了。}:現在才 {time24}，午安得等中午以後。}",
                    "{if:{hour}>=12:{if:{hour}<=17:午安！記得補充能量，下午繼續衝。:這個時間講午安，我只能說你時差有點自由。}:太早啦，現在還不到午安時段。}",
                    "{if:{hour}>=12:{if:{hour}<=17:午安！願你下午的進度比訊息通知還快。:現在是 {time}，這句午安送得很有個性。}:還沒中午就在午安，先等等太陽。}",
                ],
                "mode": "starts_with",
                "reply": True,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
            {
                "trigger": ["晚安", "晚安安"],
                "response": [
                    "{if:{hour}>=18:晚安 {user}，今天辛苦了，該休息就休息。:現在才 {time24}，這麼早就在晚安嗎？}",
                    "{if:{hour}>=18:晚安！希望你今晚的夢裡沒有 bug。:還沒到晚上耶，現在是 {time24}。}",
                    "{if:{hour}>=18:晚安安，記得把今天的煩惱留給昨天。:白天就晚安，這睡意來得有點快。}",
                ],
                "mode": "starts_with",
                "reply": True,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
            {
                "trigger": ["安安"],
                "response": [
                    "{if:{hour}<=11:安安，這邊自動幫你翻譯成早安。:else:{if:{hour}<=17:安安，午安版本已送達。:else:{if:{hour}>=22:安安，差不多可以準備睡了。:安安，晚餐時間過得還順利嗎？}}}",
                    "{if:{hour}<=11:安安！今天也要精神滿滿。:else:{if:{hour}<=17:安安！下午場繼續加油。:else:{if:{hour}>=22:安安，夜深了，記得休息。:安安，今晚也辛苦了。}}}",
                    "{if:{hour}<=11:安安，早晨模式啟動。:else:{if:{hour}<=17:安安，午后模式啟動。:else:{if:{hour}>=22:安安，睡前模式啟動。:安安，晚上模式啟動。}}}",
                ],
                "mode": "equals",
                "reply": True,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
        ],
    },
    "mini_commands": {
        "display_name": "迷你指令包",
        "description": "幾個常用小指令，像是 !say、!time、!date、!roll。",
        "rules": [
            {
                "trigger": ["!say"],
                "response": ["用法：!say 內容"],
                "mode": "equals",
                "reply": False,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
            {
                "trigger": ["!say "],
                "response": ["你剛剛說的是：{contentsplit:1-}"],
                "mode": "starts_with",
                "reply": False,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
            {
                "trigger": ["!time", "!時間"],
                "response": ["現在時間：{date} {time}（{time24}）"],
                "mode": "equals",
                "reply": False,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
            {
                "trigger": ["!date", "!日期"],
                "response": ["今天是 {date}"],
                "mode": "equals",
                "reply": False,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
            {
                "trigger": ["!roll", "!dice", "!骰子"],
                "response": ["🎲 {user} 擲出了 {randint:1-100}"],
                "mode": "equals",
                "reply": False,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
        ],
    },
    "chat_fun": {
        "display_name": "聊天互動包",
        "description": "簽到、點名、運勢和好耶反應，適合先讓聊天室動起來。",
        "rules": [
            {
                "trigger": ["抽一個人", "抽人", "點名"],
                "response": [
                    "{random_user}，就是你了，不要偷看旁邊。",
                    "今天就決定是 {random_user} 了。",
                    "我選中了 {random_user}，恭喜中獎。",
                ],
                "mode": "equals",
                "reply": False,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
            {
                "trigger": ["簽到", "!checkin", "!簽到"],
                "response": [
                    "{embedtitle:簽到成功}{embeddescription:{user} 在 {date} {time24} 完成簽到}{embedcolor:57F287}{embedfield:伺服器:{guild}}{embedfooter:AutoReply Template}{embedtime:true}"
                ],
                "mode": "equals",
                "reply": False,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
            {
                "trigger": ["今日運勢", "運勢"],
                "response": [
                    "{if:{random}>=90:今日運勢：{random}/100，大吉，適合把卡住的事一次解開。:else:{if:{random}>=60:今日運勢：{random}/100，普通偏順，穩穩來就好。:今日運勢：{random}/100，先補咖啡再開工會比較穩。}}"
                ],
                "mode": "equals",
                "reply": False,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
            {
                "trigger": ["好耶"],
                "response": [
                    "好耶！！！{react:🎉}{react:🔥}",
                    "真的好耶。{react:🎉}",
                ],
                "mode": "contains",
                "reply": False,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
        ],
    },
}


class TemplateSyntaxError(ValueError):
    pass


def percent_random(percent: int) -> bool:
    if percent == 100:
        return True
    try:
        percent = int(percent)
        if percent <= 0:
            return False
        return random.random() < percent / 100
    except Exception:
        return False


async def list_autoreply_autocomplete(interaction: discord.Interaction, current: str):
    guild_id = interaction.guild.id
    autoreplies = get_server_config(guild_id, "autoreplies", [])
    choices = []
    for ar in autoreplies:
        text = ", ".join(ar["trigger"])
        text = text if len(text) <= 100 else text[:97] + "..."
        if current.lower() in text.lower():
            choices.append(app_commands.Choice(name=text, value=text))
    return choices[:25]  # Discord 限制最多 25 個選項


async def list_template_pack_autocomplete(interaction: discord.Interaction, current: str):
    lowered_current = current.lower()
    choices = []
    for pack_key, pack_data in AUTOREPLY_TEMPLATE_PACKS.items():
        searchable_text = " ".join([
            pack_key,
            pack_data["display_name"],
            pack_data["description"],
        ]).lower()
        if lowered_current and lowered_current not in searchable_text:
            continue
        display_name = f'{pack_data["display_name"]} ({pack_key})'
        display_name = display_name if len(display_name) <= 100 else display_name[:97] + "..."
        choices.append(app_commands.Choice(name=display_name, value=pack_key))
    return choices[:25]


def parse_channel_mention(mention: str) -> str:
    match = re.match(r"<#(\d+)>", mention)
    if match:
        return match.group(1)
    return mention


AUTOREPLY_MODE_METADATA = {
    "contains": {
        "label": "包含",
        "description": "訊息包含其中一個觸發字就回覆",
    },
    "equals": {
        "label": "完全相同",
        "description": "訊息要和觸發字完全一樣",
    },
    "starts_with": {
        "label": "開頭符合",
        "description": "訊息開頭符合時觸發",
    },
    "ends_with": {
        "label": "結尾符合",
        "description": "訊息結尾符合時觸發",
    },
    "regex": {
        "label": "正規表達式",
        "description": "用 Python regex 比對訊息",
    },
}

AUTOREPLY_CHANNEL_MODE_METADATA = {
    "all": {
        "label": "全部頻道",
        "description": "任何文字頻道都能觸發",
    },
    "whitelist": {
        "label": "白名單",
        "description": "只有指定頻道會觸發",
    },
    "blacklist": {
        "label": "黑名單",
        "description": "除了指定頻道外都會觸發",
    },
}


class AutoReplyBuilderContentModal(discord.ui.Modal, title="AutoReply Builder"):
    def __init__(self, builder_view: "AutoReplyBuilderView"):
        super().__init__()
        self.builder_view = builder_view

        self.trigger_input = discord.ui.TextInput(
            label="觸發字",
            placeholder="一行一個 trigger；只有一行時也可用逗號分隔",
            required=True,
            max_length=1000,
            style=discord.TextStyle.paragraph,
            default=builder_view.state["trigger_text"],
        )
        self.response_input = discord.ui.TextInput(
            label="回覆內容",
            placeholder="一行一個 response；可直接使用 {user}、{contentsplit:1-} 等變數",
            required=True,
            max_length=2000,
            style=discord.TextStyle.paragraph,
            default=builder_view.state["response_text"],
        )
        self.random_chance_input = discord.ui.TextInput(
            label="觸發機率 (1-100)",
            placeholder="100",
            required=True,
            max_length=3,
            style=discord.TextStyle.short,
            default=str(builder_view.state["random_chance"]),
        )

        self.add_item(self.trigger_input)
        self.add_item(self.response_input)
        self.add_item(self.random_chance_input)

    async def on_submit(self, interaction: discord.Interaction):
        chance_raw = self.random_chance_input.value.strip()
        try:
            random_chance = int(chance_raw)
        except (TypeError, ValueError):
            await interaction.response.send_message("觸發機率必須是 1 到 100 的整數。", ephemeral=True)
            return

        if random_chance < 1 or random_chance > 100:
            await interaction.response.send_message("觸發機率必須是 1 到 100 的整數。", ephemeral=True)
            return

        self.builder_view.state["trigger_text"] = self.trigger_input.value.strip()
        self.builder_view.state["response_text"] = self.response_input.value.strip()
        self.builder_view.state["random_chance"] = random_chance

        await interaction.response.defer(ephemeral=True)
        await self.builder_view.refresh_message()
        await interaction.followup.send("Builder 內容已更新。", ephemeral=True)


class AutoReplyBuilderModeSelect(discord.ui.Select):
    def __init__(self, builder_view: "AutoReplyBuilderView"):
        self.builder_view = builder_view
        options = [
            discord.SelectOption(
                label=meta["label"],
                value=value,
                description=meta["description"],
                default=builder_view.state["mode"] == value,
            )
            for value, meta in AUTOREPLY_MODE_METADATA.items()
        ]
        super().__init__(
            placeholder="選擇觸發模式",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await self.builder_view.ensure_owner(interaction):
            return
        self.builder_view.state["mode"] = interaction.data["values"][0]
        await interaction.response.defer()
        await self.builder_view.refresh_message(interaction.message)


class AutoReplyBuilderChannelModeSelect(discord.ui.Select):
    def __init__(self, builder_view: "AutoReplyBuilderView"):
        self.builder_view = builder_view
        options = [
            discord.SelectOption(
                label=meta["label"],
                value=value,
                description=meta["description"],
                default=builder_view.state["channel_mode"] == value,
            )
            for value, meta in AUTOREPLY_CHANNEL_MODE_METADATA.items()
        ]
        super().__init__(
            placeholder="選擇頻道限制模式",
            min_values=1,
            max_values=1,
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await self.builder_view.ensure_owner(interaction):
            return
        self.builder_view.state["channel_mode"] = interaction.data["values"][0]
        await interaction.response.defer()
        await self.builder_view.refresh_message(interaction.message)


class AutoReplyBuilderChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, builder_view: "AutoReplyBuilderView"):
        self.builder_view = builder_view
        text_channel_count = len([
            channel for channel in builder_view.guild.channels
            if getattr(channel, "type", None) in (discord.ChannelType.text, discord.ChannelType.news)
        ])
        super().__init__(
            placeholder="選擇頻道限制（可多選，不選就是空清單）",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=0,
            max_values=max(1, min(25, text_channel_count or 1)),
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await self.builder_view.ensure_owner(interaction):
            return
        selected_values = interaction.data.get("values", [])
        self.builder_view.state["channels"] = [
            int(channel_id)
            for channel_id in selected_values
            if str(channel_id).isdigit()
        ]
        await interaction.response.defer()
        await self.builder_view.refresh_message(interaction.message)


class AutoReplyBuilderView(discord.ui.View):
    def __init__(self, cog: "AutoReply", interaction: discord.Interaction):
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = interaction.user.id
        self.guild = interaction.guild
        self.original_interaction = interaction
        self.message: discord.Message | None = None
        self.state = {
            "trigger_text": "",
            "response_text": "",
            "mode": "contains",
            "reply": False,
            "channel_mode": "all",
            "channels": [],
            "random_chance": 100,
        }
        self._rebuild_components()

    async def ensure_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("這個 builder 只給原本開啟的人操作。", ephemeral=True)
        return False

    def _rebuild_components(self):
        self.clear_items()
        self.add_item(AutoReplyBuilderModeSelect(self))
        self.add_item(AutoReplyBuilderChannelModeSelect(self))
        self.add_item(AutoReplyBuilderChannelSelect(self))

        edit_button = discord.ui.Button(label="編輯觸發與回覆", style=discord.ButtonStyle.primary, row=3)
        edit_button.callback = self.open_content_modal
        self.add_item(edit_button)

        reply_button = discord.ui.Button(
            label=f"回覆原訊息：{'開啟' if self.state['reply'] else '關閉'}",
            style=discord.ButtonStyle.success if self.state["reply"] else discord.ButtonStyle.secondary,
            row=3,
        )
        reply_button.callback = self.toggle_reply
        self.add_item(reply_button)

        clear_channels_button = discord.ui.Button(label="清空頻道限制", style=discord.ButtonStyle.secondary, row=3)
        clear_channels_button.callback = self.clear_channels
        self.add_item(clear_channels_button)

        save_button = discord.ui.Button(label="儲存規則", style=discord.ButtonStyle.success, row=4)
        save_button.callback = self.save_rule
        self.add_item(save_button)

        cancel_button = discord.ui.Button(label="取消", style=discord.ButtonStyle.danger, row=4)
        cancel_button.callback = self.cancel_builder
        self.add_item(cancel_button)

    def build_embed(self, *, title: str = "AutoReply Builder", description: str | None = None, color: int = 0x5865F2):
        trigger_preview = self.cog._preview_builder_items(self.state["trigger_text"], empty_text="還沒設定")
        response_preview = self.cog._preview_builder_items(self.state["response_text"], empty_text="還沒設定")
        channel_mentions = [
            f"<#{channel_id}>"
            for channel_id in self.state["channels"]
            if self.guild.get_channel(channel_id) is not None
        ]
        mode_label = AUTOREPLY_MODE_METADATA[self.state["mode"]]["label"]
        channel_mode_label = AUTOREPLY_CHANNEL_MODE_METADATA[self.state["channel_mode"]]["label"]
        channel_text = ", ".join(channel_mentions) if channel_mentions else "空清單"

        embed = discord.Embed(
            title=title,
            description=description or "用下方按鈕和下拉選單慢慢組這條規則，準備好之後按「儲存規則」。",
            color=color,
        )
        embed.add_field(name="觸發字", value=trigger_preview, inline=False)
        embed.add_field(name="回覆內容", value=response_preview, inline=False)
        embed.add_field(name="模式", value=f"{mode_label} (`{self.state['mode']}`)", inline=True)
        embed.add_field(name="回覆原訊息", value="開啟" if self.state["reply"] else "關閉", inline=True)
        embed.add_field(name="機率", value=f"{self.state['random_chance']}%", inline=True)
        embed.add_field(name="頻道模式", value=f"{channel_mode_label} (`{self.state['channel_mode']}`)", inline=True)
        embed.add_field(name="指定頻道", value=channel_text, inline=True)
        embed.add_field(name="目前條數", value=f"{len(get_server_config(self.guild.id, 'autoreplies', []))} / {self.cog._get_autoreply_limit(self.guild.id)}", inline=True)
        embed.add_field(
            name="小提示",
            value="觸發字 / 回覆可以一行一個；回覆可直接用 `{user}`、`{content}`、`{contentsplit:1-}`、`{if:...}`、`{math:(...)}`。",
            inline=False,
        )
        return embed

    async def refresh_message(self, message: discord.Message | None = None):
        if message is not None:
            self.message = message
        if self.message is None:
            return
        self._rebuild_components()
        await self.message.edit(embed=self.build_embed(), view=self)

    async def open_content_modal(self, interaction: discord.Interaction):
        if not await self.ensure_owner(interaction):
            return
        await interaction.response.send_modal(AutoReplyBuilderContentModal(self))

    async def toggle_reply(self, interaction: discord.Interaction):
        if not await self.ensure_owner(interaction):
            return
        self.state["reply"] = not self.state["reply"]
        await interaction.response.defer()
        await self.refresh_message(interaction.message)

    async def clear_channels(self, interaction: discord.Interaction):
        if not await self.ensure_owner(interaction):
            return
        self.state["channels"] = []
        await interaction.response.defer()
        await self.refresh_message(interaction.message)

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
            total_count, limit = self.cog._save_new_autoreply_rule(self.guild.id, rule)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        for child in self.children:
            child.disabled = True
        self.stop()

        success_embed = self.cog._build_autoreply_rule_embed(
            title="已儲存 AutoReply 規則",
            rule=rule,
            guild=self.guild,
            description=f"這條規則已經加入清單，目前共有 {total_count} / {limit} 條。",
        )
        if self.message is None:
            self.message = interaction.message
        if self.message is not None:
            await self.message.edit(embed=success_embed, view=self)

        trigger_text = ", ".join(rule["trigger"])
        log(
            f"自動回覆由 builder 新增：`{trigger_text[:10]}{'...' if len(trigger_text) > 10 else ''}`。",
            module_name="AutoReply",
            level=logging.INFO,
            user=interaction.user,
            guild=interaction.guild,
        )
        await interaction.followup.send("規則已加入 AutoReply 清單。", ephemeral=True)

    async def cancel_builder(self, interaction: discord.Interaction):
        if not await self.ensure_owner(interaction):
            return
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        self.stop()
        if self.message is None:
            self.message = interaction.message
        if self.message is not None:
            await self.message.edit(
                embed=self.build_embed(
                    title="AutoReply Builder 已取消",
                    description="這次沒有儲存任何規則。",
                    color=0x747F8D,
                ),
                view=self,
            )

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        self.stop()
        if self.message is not None:
            try:
                await self.message.edit(
                    embed=self.build_embed(
                        title="AutoReply Builder 已逾時",
                        description="Builder 已關閉，想繼續的話請重新執行指令。",
                        color=0xED4245,
                    ),
                    view=self,
                )
            except Exception:
                pass


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class AutoReply(commands.GroupCog, name="autoreply"):
    """自動回覆設定指令群組"""

    def __init__(self, bot):
        self.bot = bot
        self.autoreply_rate_limit = commands.CooldownMapping.from_cooldown(
            AUTOREPLY_RATE_LIMIT_COUNT,
            AUTOREPLY_RATE_LIMIT_WINDOW,
            commands.BucketType.guild
        )

    def _is_rate_limited(self, message: discord.Message) -> bool:
        bucket = self.autoreply_rate_limit.get_bucket(message)
        if bucket is None:
            return False
        return bucket.update_rate_limit() is not None

    def _get_autoreply_limit(self, guild_id: int) -> int:
        try:
            return int(get_server_config(guild_id, "autoreply_limit", DEFAULT_AUTOREPLY_CONFIG_LIMIT) or DEFAULT_AUTOREPLY_CONFIG_LIMIT)
        except (TypeError, ValueError):
            return DEFAULT_AUTOREPLY_CONFIG_LIMIT

    def _split_autoreply_items(self, raw_value: str) -> list[str]:
        if raw_value is None:
            return []

        normalized_value = str(raw_value).replace("\r\n", "\n").strip()
        if not normalized_value:
            return []

        if "\n" in normalized_value:
            return [item.strip() for item in normalized_value.split("\n") if item.strip()]

        return [item.strip() for item in normalized_value.split(",") if item.strip()]

    def _preview_builder_items(self, raw_value: str, *, empty_text: str = "未設定") -> str:
        items = self._split_autoreply_items(raw_value)
        if not items:
            return empty_text

        preview_lines = []
        for item in items[:5]:
            shortened = item if len(item) <= 250 else item[:247] + "..."
            preview_lines.append(f"• {shortened}")

        if len(items) > 5:
            preview_lines.append(f"… 另外還有 {len(items) - 5} 項")

        preview_text = "\n".join(preview_lines)
        return preview_text if len(preview_text) <= 1024 else preview_text[:1021] + "..."

    def _normalize_autoreply_channels(self, guild: discord.Guild, channels_input) -> list[int]:
        if not channels_input:
            return []

        if isinstance(channels_input, str):
            channel_candidates = [parse_channel_mention(item.strip()) for item in channels_input.split(",") if item.strip()]
        else:
            channel_candidates = list(channels_input)

        valid_channels = []
        seen_channels = set()

        for channel_candidate in channel_candidates:
            channel_id = None

            if isinstance(channel_candidate, int):
                channel_id = channel_candidate
            else:
                channel_text = str(channel_candidate).strip()
                if channel_text.isdigit():
                    channel_id = int(channel_text)

            if channel_id is None or channel_id in seen_channels:
                continue

            if guild.get_channel(channel_id) is None:
                continue

            seen_channels.add(channel_id)
            valid_channels.append(channel_id)

        return valid_channels

    def _build_autoreply_rule(
        self,
        guild: discord.Guild,
        mode: str,
        trigger_input: str,
        response_input: str,
        reply: bool = False,
        channel_mode: str = "all",
        channels_input=None,
        random_chance: int = 100,
    ) -> dict:
        if mode not in AUTOREPLY_MODE_METADATA:
            raise ValueError("未知的觸發模式。")
        if channel_mode not in AUTOREPLY_CHANNEL_MODE_METADATA:
            raise ValueError("未知的頻道限制模式。")

        try:
            random_chance = int(random_chance)
        except (TypeError, ValueError):
            raise ValueError("觸發機率必須是 1 到 100 的整數。")

        if random_chance < 1 or random_chance > 100:
            raise ValueError("觸發機率必須是 1 到 100 的整數。")

        trigger = self._split_autoreply_items(trigger_input)
        response = self._split_autoreply_items(response_input)

        if not trigger:
            raise ValueError("至少要設定一個觸發字。")
        if not response:
            raise ValueError("至少要設定一個回覆內容。")

        for template in response:
            try:
                self._validate_template_syntax(template)
            except TemplateSyntaxError as e:
                raise ValueError(f"回覆模板語法錯誤：{e}") from e

        valid_channels = self._normalize_autoreply_channels(guild, channels_input)

        return {
            "trigger": trigger,
            "response": response,
            "mode": mode,
            "reply": bool(reply),
            "channel_mode": channel_mode,
            "channels": valid_channels,
            "random_chance": random_chance,
        }

    def _normalize_autoreply_trigger(self, trigger: str) -> str:
        return re.sub(r"\s+", " ", str(trigger).strip()).casefold()

    def _find_duplicate_triggers_in_list(self, triggers: list[str]) -> list[str]:
        seen = {}
        duplicates = []

        for trigger in triggers or []:
            clean_trigger = str(trigger).strip()
            normalized_trigger = self._normalize_autoreply_trigger(clean_trigger)
            if not normalized_trigger:
                continue

            if normalized_trigger in seen:
                if seen[normalized_trigger] not in duplicates:
                    duplicates.append(seen[normalized_trigger])
                continue

            seen[normalized_trigger] = clean_trigger

        return duplicates

    def _find_conflicting_autoreply_triggers(self, autoreplies: list[dict], triggers: list[str], skip_rule: dict | None = None) -> list[str]:
        existing_triggers = {}

        for autoreply in autoreplies or []:
            if skip_rule is not None and autoreply is skip_rule:
                continue

            for existing_trigger in autoreply.get("trigger", []) or []:
                clean_trigger = str(existing_trigger).strip()
                normalized_trigger = self._normalize_autoreply_trigger(clean_trigger)
                if normalized_trigger and normalized_trigger not in existing_triggers:
                    existing_triggers[normalized_trigger] = clean_trigger

        conflicts = []
        seen_conflicts = set()

        for trigger in triggers or []:
            clean_trigger = str(trigger).strip()
            normalized_trigger = self._normalize_autoreply_trigger(clean_trigger)
            if not normalized_trigger or normalized_trigger not in existing_triggers or normalized_trigger in seen_conflicts:
                continue

            conflicts.append(clean_trigger)
            seen_conflicts.add(normalized_trigger)

        return conflicts

    def _format_autoreply_trigger_conflict_message(self, triggers: list[str], *, existing: bool) -> str:
        preview = ", ".join(f"`{trigger}`" for trigger in triggers[:5])
        if len(triggers) > 5:
            preview += f" ... (+{len(triggers) - 5})"

        if existing:
            return f"這些觸發器已經存在於其他自動回覆規則中：{preview}"
        return f"你這次新增的觸發器裡有重複項目：{preview}"

    def _save_new_autoreply_rule(self, guild_id: int, rule: dict) -> tuple[int, int]:
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        autoreply_limit = self._get_autoreply_limit(guild_id)
        if len(autoreplies) >= autoreply_limit:
            raise ValueError(f"自動回覆上限為 {autoreply_limit} 條。")

        duplicate_triggers = self._find_duplicate_triggers_in_list(rule.get("trigger", []))
        if duplicate_triggers:
            raise ValueError(self._format_autoreply_trigger_conflict_message(duplicate_triggers, existing=False))

        conflicting_triggers = self._find_conflicting_autoreply_triggers(autoreplies, rule.get("trigger", []))
        if conflicting_triggers:
            raise ValueError(self._format_autoreply_trigger_conflict_message(conflicting_triggers, existing=True))

        autoreplies.append(rule)
        set_server_config(guild_id, "autoreplies", autoreplies)
        return len(autoreplies), autoreply_limit

    def _build_autoreply_rule_embed(
        self,
        title: str,
        rule: dict,
        guild: discord.Guild | None = None,
        description: str | None = None,
        color: int = 0x00FF00,
    ) -> discord.Embed:
        trigger_text = ", ".join(rule["trigger"])
        response_text = ", ".join(rule["response"])
        trigger_preview = trigger_text if len(trigger_text) <= 1024 else trigger_text[:1021] + "..."
        response_preview = response_text if len(response_text) <= 1024 else response_text[:1021] + "..."

        if rule["channels"]:
            channel_mentions = []
            for channel_id in rule["channels"]:
                if guild is not None and guild.get_channel(channel_id) is not None:
                    channel_mentions.append(f"<#{channel_id}>")
                else:
                    channel_mentions.append(str(channel_id))
            channel_text = ", ".join(channel_mentions)
        else:
            channel_text = "無"

        mode_label = AUTOREPLY_MODE_METADATA.get(rule["mode"], {}).get("label", rule["mode"])
        channel_mode_label = AUTOREPLY_CHANNEL_MODE_METADATA.get(rule["channel_mode"], {}).get("label", rule["channel_mode"])

        embed = discord.Embed(title=title, description=description, color=color)
        embed.add_field(name="模式", value=f"{mode_label} (`{rule['mode']}`)")
        embed.add_field(name="觸發字", value=f"`{trigger_preview}`", inline=False)
        embed.add_field(name="回覆內容", value=f"`{response_preview}`", inline=False)
        embed.add_field(name="Reply", value="是" if rule["reply"] else "否")
        embed.add_field(name="頻道模式", value=f"{channel_mode_label} (`{rule['channel_mode']}`)")
        embed.add_field(name="指定頻道", value=channel_text, inline=False)
        embed.add_field(name="觸發機率", value=f"{rule['random_chance']}%")
        return embed

    def _parse_embed_color(self, value: str):
        raw_value = str(value).strip().lower()
        if not raw_value:
            return None
        if raw_value.startswith("#"):
            raw_value = raw_value[1:]
        elif raw_value.startswith("0x"):
            raw_value = raw_value[2:]
        try:
            color_value = int(raw_value, 16)
        except ValueError:
            return None
        if 0 <= color_value <= 0xFFFFFF:
            return color_value
        return None

    def _parse_bool(self, value: str) -> bool:
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _split_top_level(self, value: str, separator: str = ":"):
        depth = 0
        for index, char in enumerate(value):
            if char == "{":
                depth += 1
            elif char == "}":
                depth = max(0, depth - 1)
            elif char == separator and depth == 0:
                return value[:index], value[index + 1:]
        return value, None

    def _find_matching_brace(self, value: str, start_index: int) -> int:
        depth = 0
        for index in range(start_index, len(value)):
            char = value[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return index
                if depth < 0:
                    return -1
        return -1

    def _find_top_level_token(self, value: str, token: str):
        depth = 0
        for index, char in enumerate(value):
            if char == "{":
                depth += 1
            elif char == "}":
                depth = max(0, depth - 1)
            elif depth == 0 and value.startswith(token, index):
                return index
        return -1

    def _split_if_branches(self, branch_block: str):
        else_index = self._find_top_level_token(branch_block, ":else:")
        if else_index != -1:
            return branch_block[:else_index], branch_block[else_index + len(":else:"):]

        true_branch, false_branch = self._split_top_level(branch_block)
        if false_branch is None:
            return branch_block, ""
        return true_branch, false_branch

    def _split_top_level_all(self, value: str, token: str):
        parts = []
        depth = 0
        last_index = 0
        token_length = len(token)
        index = 0

        while index <= len(value) - token_length:
            char = value[index]
            if char == "{":
                depth += 1
                index += 1
                continue
            if char == "}":
                depth = max(0, depth - 1)
                index += 1
                continue
            if depth == 0 and value.startswith(token, index):
                parts.append(value[last_index:index])
                index += token_length
                last_index = index
                continue
            index += 1

        parts.append(value[last_index:])
        return parts

    def _parse_contentsplit_token(self, token: str):
        legacy_match = re.fullmatch(r"contentsplit\((-?\d+)\)", token, re.IGNORECASE)
        if legacy_match:
            return "index", int(legacy_match.group(1)), None

        if not token.lower().startswith("contentsplit:"):
            raise TemplateSyntaxError("Invalid contentsplit syntax")

        spec = token[len("contentsplit:"):].strip()
        if re.fullmatch(r"\d+", spec):
            return "index", int(spec), None

        range_match = re.fullmatch(r"(\d*)-(\d*)", spec)
        if range_match and (range_match.group(1) or range_match.group(2)):
            start = int(range_match.group(1)) if range_match.group(1) else None
            end = int(range_match.group(2)) if range_match.group(2) else None
            return "range", start, end

        raise TemplateSyntaxError("Invalid contentsplit syntax")

    def _resolve_contentsplit_token(self, token: str, content_parts: list[str]) -> str:
        try:
            split_type, start_value, end_value = self._parse_contentsplit_token(token)
        except TemplateSyntaxError:
            return ""

        if split_type == "index":
            try:
                return content_parts[start_value]
            except IndexError:
                return ""

        start_index = 0 if start_value is None else start_value
        if end_value is None:
            selected_parts = content_parts[start_index:]
        else:
            if start_index > end_value:
                return ""
            selected_parts = content_parts[start_index:end_value + 1]
        return " ".join(selected_parts)

    def _parse_delay_directive_token(self, token: str):
        lowered = token.lower()
        if lowered.startswith("newmsg:"):
            directive_name = "newmsg"
            raw_delay = token[len("newmsg:"):].strip()
        elif lowered.startswith("edit:"):
            directive_name = "edit"
            raw_delay = token[len("edit:"):].strip()
        else:
            raise TemplateSyntaxError("Invalid delay directive syntax")

        if not raw_delay.isdigit():
            raise TemplateSyntaxError(f"Invalid {directive_name} syntax")

        delay_seconds = int(raw_delay)
        if not AUTOREPLY_DELAY_MIN_SECONDS <= delay_seconds <= AUTOREPLY_DELAY_MAX_SECONDS:
            raise TemplateSyntaxError(f"{directive_name} delay must be between {AUTOREPLY_DELAY_MIN_SECONDS} and {AUTOREPLY_DELAY_MAX_SECONDS}")

        return directive_name, delay_seconds

    def _parse_state_var_token(self, token: str):
        lowered = token.lower()
        if lowered.startswith("uservar:"):
            scope = "user"
            payload = token[len("uservar:"):]
        elif lowered.startswith("guildvar:"):
            scope = "guild"
            payload = token[len("guildvar:"):]
        else:
            raise TemplateSyntaxError("Invalid state var syntax")

        key, value = self._split_top_level(payload)
        if not key or not key.strip():
            raise TemplateSyntaxError("Invalid state var syntax")

        return scope, key.strip(), value

    def _parse_math_token(self, token: str) -> str:
        if not token.lower().startswith("math:"):
            raise TemplateSyntaxError("Invalid math syntax")

        raw_expression = token[len("math:"):].strip()
        if len(raw_expression) < 2 or raw_expression[0] != "(" or raw_expression[-1] != ")":
            raise TemplateSyntaxError("Invalid math syntax")

        expression = raw_expression[1:-1].strip()
        if not expression:
            raise TemplateSyntaxError("Invalid math syntax")

        if len(expression) > AUTOREPLY_MATH_EXPRESSION_MAX_LENGTH:
            raise TemplateSyntaxError("Math expression too long")

        return expression

    def _prepare_math_expression_for_validation(self, expression: str) -> str:
        output = []
        index = 0

        while index < len(expression):
            if expression[index] != "{":
                output.append(expression[index])
                index += 1
                continue

            closing_index = self._find_matching_brace(expression, index)
            if closing_index == -1:
                raise TemplateSyntaxError("Invalid math syntax")

            nested_token = expression[index:closing_index + 1]
            self._validate_template_syntax(nested_token)
            output.append("0")
            index = closing_index + 1

        prepared_expression = "".join(output)
        if len(prepared_expression) > AUTOREPLY_MATH_EXPRESSION_MAX_LENGTH:
            raise TemplateSyntaxError("Math expression too long")

        return prepared_expression

    def _validate_math_ast_limits(self, parsed_expression):
        stack = [(parsed_expression, 1)]
        node_count = 0

        while stack:
            node, depth = stack.pop()
            node_count += 1

            if node_count > AUTOREPLY_MATH_AST_MAX_NODES:
                raise TemplateSyntaxError("Math expression too complex")
            if depth > AUTOREPLY_MATH_AST_MAX_DEPTH:
                raise TemplateSyntaxError("Math expression too complex")

            for child in ast.iter_child_nodes(node):
                stack.append((child, depth + 1))

    def _normalize_math_expression(self, expression: str) -> str:
        if not expression.strip():
            raise TemplateSyntaxError("Invalid math syntax")

        if len(expression) > AUTOREPLY_MATH_EXPRESSION_MAX_LENGTH:
            raise TemplateSyntaxError("Math expression too long")

        if not re.fullmatch(r"[0-9\.\+\-\*/\(\)\s]+", expression):
            raise TemplateSyntaxError("Invalid math syntax")

        number_pattern = re.compile(r"(?<![\w.])(?:\d+(?:\.\d*)?|\.\d+)")

        def normalize_number(match):
            literal = match.group(0)
            try:
                numeric_value = float(literal)
            except ValueError as e:
                raise TemplateSyntaxError("Invalid math syntax") from e

            if numeric_value < 0 or numeric_value > 1000:
                raise TemplateSyntaxError("Math number out of range")

            if "." in literal:
                normalized_value = format(numeric_value, ".15g")
                if normalized_value.startswith("."):
                    normalized_value = f"0{normalized_value}"
                return normalized_value

            return str(int(numeric_value))

        return number_pattern.sub(normalize_number, expression)

    def _evaluate_math_expression(self, expression: str, allow_template_placeholders: bool = False) -> str:
        if allow_template_placeholders:
            expression = self._prepare_math_expression_for_validation(expression)
        elif "{" in expression or "}" in expression:
            raise TemplateSyntaxError("Invalid math syntax")

        expression = self._normalize_math_expression(expression)

        try:
            parsed_expression = ast.parse(expression, mode="eval")
        except (SyntaxError, RecursionError, MemoryError) as e:
            raise TemplateSyntaxError("Invalid math syntax") from e

        try:
            self._validate_math_ast_limits(parsed_expression)
        except (RecursionError, MemoryError) as e:
            raise TemplateSyntaxError("Math expression too complex") from e

        def evaluate_node(node):
            if isinstance(node, ast.Expression):
                return evaluate_node(node.body)

            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                numeric_value = float(node.value)
                if numeric_value < -1000 or numeric_value > 1000:
                    raise TemplateSyntaxError("Math number out of range")
                return numeric_value

            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                operand_value = evaluate_node(node.operand)
                return operand_value if isinstance(node.op, ast.UAdd) else -operand_value

            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                left_value = evaluate_node(node.left)
                right_value = evaluate_node(node.right)

                if isinstance(node.op, ast.Add):
                    return left_value + right_value
                if isinstance(node.op, ast.Sub):
                    return left_value - right_value
                if isinstance(node.op, ast.Mult):
                    return left_value * right_value
                if right_value == 0:
                    raise TemplateSyntaxError("Division by zero")
                return left_value / right_value

            raise TemplateSyntaxError("Invalid math syntax")

        try:
            result = evaluate_node(parsed_expression)
        except (RecursionError, MemoryError) as e:
            raise TemplateSyntaxError("Math expression too complex") from e
        if not math.isfinite(result):
            raise TemplateSyntaxError("Invalid math result")

        if float(result).is_integer():
            return str(int(result))

        return format(result, ".10f").rstrip("0").rstrip(".")

    def _get_autoreply_var_storage_key(self, key: str) -> str:
        return f"{AUTOREPLY_VAR_KEY_PREFIX}{key}"

    def _count_autoreply_user_vars(self, guild_id: int, user_id: int) -> int:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM user_data WHERE guild_id = ? AND user_id = ? AND data_key LIKE ?",
                (guild_id or 0, user_id, f"{AUTOREPLY_VAR_KEY_PREFIX}%")
            )
            result = cursor.fetchone()
        return int(result[0]) if result and result[0] is not None else 0

    def _user_var_exists(self, guild_id: int, user_id: int, storage_key: str) -> bool:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM user_data WHERE guild_id = ? AND user_id = ? AND data_key = ? LIMIT 1",
                (guild_id or 0, user_id, storage_key)
            )
            return cursor.fetchone() is not None

    def _count_autoreply_guild_vars(self, guild_id: int) -> int:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM server_configs WHERE guild_id = ? AND config_key LIKE ?",
                (guild_id, f"{AUTOREPLY_VAR_KEY_PREFIX}%")
            )
            result = cursor.fetchone()
        return int(result[0]) if result and result[0] is not None else 0

    def _guild_var_exists(self, guild_id: int, storage_key: str) -> bool:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM server_configs WHERE guild_id = ? AND config_key = ? LIMIT 1",
                (guild_id, storage_key)
            )
            return cursor.fetchone() is not None

    def _set_autoreply_user_var(self, guild_id: int, user_id: int, key: str, value: str) -> bool:
        if not key or len(key) > AUTOREPLY_VAR_MAX_LENGTH or len(value) > AUTOREPLY_VAR_MAX_LENGTH:
            return False

        storage_key = self._get_autoreply_var_storage_key(key)
        if not self._user_var_exists(guild_id, user_id, storage_key):
            if self._count_autoreply_user_vars(guild_id, user_id) >= AUTOREPLY_USERVAR_LIMIT:
                return False

        return bool(set_user_data(guild_id, user_id, storage_key, value))

    def _set_autoreply_guild_var(self, guild_id: int, key: str, value: str) -> bool:
        if not key or len(key) > AUTOREPLY_VAR_MAX_LENGTH or len(value) > AUTOREPLY_VAR_MAX_LENGTH:
            return False

        storage_key = self._get_autoreply_var_storage_key(key)
        if not self._guild_var_exists(guild_id, storage_key):
            if self._count_autoreply_guild_vars(guild_id) >= AUTOREPLY_GUILDVAR_LIMIT:
                return False

        return bool(set_server_config(guild_id, storage_key, value))

    def _validate_condition_expression(self, expression: str):
        or_parts = self._split_top_level_all(expression, "||")
        if len(or_parts) > 1:
            for part in or_parts:
                if not part.strip():
                    raise TemplateSyntaxError("Invalid if condition")
                self._validate_condition_expression(part)
            return

        and_parts = self._split_top_level_all(expression, "&&")
        if len(and_parts) > 1:
            for part in and_parts:
                if not part.strip():
                    raise TemplateSyntaxError("Invalid if condition")
                self._validate_condition_expression(part)
            return

        left_text, operator, right_text = self._split_condition_expression(expression)
        if operator is None or not left_text.strip() or not right_text.strip():
            raise TemplateSyntaxError("Invalid if condition")

        self._validate_template_syntax(left_text)
        self._validate_template_syntax(right_text)

    async def _evaluate_condition_expression(self, expression: str, message: discord.Message, context: dict) -> bool:
        or_parts = self._split_top_level_all(expression, "||")
        if len(or_parts) > 1:
            for part in or_parts:
                if await self._evaluate_condition_expression(part, message, context):
                    return True
            return False

        and_parts = self._split_top_level_all(expression, "&&")
        if len(and_parts) > 1:
            for part in and_parts:
                if not await self._evaluate_condition_expression(part, message, context):
                    return False
            return True

        left_text, operator, right_text = self._split_condition_expression(expression)
        if operator is None:
            return False

        resolved_left = await self._resolve_response_variables(left_text.strip(), message, context)
        resolved_right = await self._resolve_response_variables(right_text.strip(), message, context)
        return self._compare_condition_values(resolved_left, operator, resolved_right)

    def _validate_if_payload(self, payload: str):
        condition_text, branch_block = self._split_top_level(payload)
        if branch_block is None:
            raise TemplateSyntaxError("Invalid if syntax")

        self._validate_condition_expression(condition_text)
        true_branch, false_branch = self._split_if_branches(branch_block)

        self._validate_template_syntax(true_branch)
        self._validate_template_syntax(false_branch)

    def _validate_template_syntax(self, response: str):
        if not response:
            return

        index = 0
        response_length = len(response)
        embed_prefixes = (
            "embedtitle:",
            "embeddescription:",
            "embedurl:",
            "embedimage:",
            "embedcolor:",
            "embedthumbnail:",
            "embedfooter:",
            "embedfooterimage:",
            "embedauthor:",
            "embedauthorurl:",
            "embedauthorimage:",
            "embedtime:",
            "embedfield:",
        )

        while index < response_length:
            if response[index] == "}":
                raise TemplateSyntaxError("Unexpected closing brace")

            if response[index] != "{":
                index += 1
                continue

            closing_index = self._find_matching_brace(response, index)
            if closing_index == -1:
                raise TemplateSyntaxError("Unclosed brace")

            token = response[index + 1:closing_index]
            lowered = token.lower()

            if lowered.startswith("if:"):
                self._validate_if_payload(token[3:])
            elif lowered.startswith("embedfield:"):
                field_name, field_value = self._split_top_level(token[len("embedfield:"):])
                if field_value is None:
                    raise TemplateSyntaxError("Invalid embed field syntax")
                self._validate_template_syntax(field_name)
                self._validate_template_syntax(field_value)
            elif lowered.startswith(embed_prefixes):
                prefix, payload = token.split(":", 1)
                if not payload:
                    raise TemplateSyntaxError(f"Empty {prefix} payload")
                self._validate_template_syntax(payload)
            elif lowered.startswith("contentsplit"):
                self._parse_contentsplit_token(token)
            elif lowered.startswith("newmsg:") or lowered.startswith("edit:"):
                self._parse_delay_directive_token(token)
            elif lowered.startswith("uservar:") or lowered.startswith("guildvar:"):
                _, key_text, value_text = self._parse_state_var_token(token)
                self._validate_template_syntax(key_text)
                if value_text is not None:
                    self._validate_template_syntax(value_text)
            elif lowered.startswith("math:"):
                expression = self._parse_math_token(token)
                self._evaluate_math_expression(expression, allow_template_placeholders=True)
            elif lowered.startswith("randint:") and not re.fullmatch(r"randint:(\d+)-(\d+)", token, re.IGNORECASE):
                raise TemplateSyntaxError("Invalid randint syntax")
            elif lowered.startswith("timemd:") and not re.fullmatch(r"timemd:[tTdDfFrR]", token, re.IGNORECASE):
                raise TemplateSyntaxError("Invalid timemd syntax")
            elif lowered.startswith("sticker:") and not re.fullmatch(r"sticker:\d+", token, re.IGNORECASE):
                raise TemplateSyntaxError("Invalid sticker syntax")
            elif lowered.startswith("react:") and not token[len("react:"):].strip():
                raise TemplateSyntaxError("Empty react payload")

            index = closing_index + 1

    def _split_condition_expression(self, expression: str):
        operators = ("==", "!=", "<=", ">=")
        depth = 0
        index = 0
        while index < len(expression) - 1:
            char = expression[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth = max(0, depth - 1)
            elif depth == 0:
                for operator in operators:
                    if expression.startswith(operator, index):
                        left = expression[:index]
                        right = expression[index + len(operator):]
                        return left, operator, right
            index += 1
        return None, None, None

    def _coerce_condition_value(self, value: str):
        raw_value = str(value).strip()
        lowered = raw_value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        try:
            return float(raw_value)
        except ValueError:
            return raw_value

    def _compare_condition_values(self, left: str, operator: str, right: str) -> bool:
        left_value = self._coerce_condition_value(left)
        right_value = self._coerce_condition_value(right)

        if operator == "==":
            return left_value == right_value
        if operator == "!=":
            return left_value != right_value

        if type(left_value) is not type(right_value):
            left_value = str(left).strip()
            right_value = str(right).strip()

        if operator == "<=":
            return left_value <= right_value
        if operator == ">=":
            return left_value >= right_value
        return False

    async def _resolve_if_expressions(self, response: str, message: discord.Message, context: dict) -> str:
        if not response or "{if:" not in response:
            return response

        output = []
        index = 0
        response_length = len(response)

        while index < response_length:
            if response[index] != "{" or not response.startswith("{if:", index):
                output.append(response[index])
                index += 1
                continue

            payload_start = index + 4
            cursor = payload_start
            depth = 1
            while cursor < response_length:
                if response[cursor] == "{":
                    depth += 1
                elif response[cursor] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                cursor += 1

            if cursor >= response_length or depth != 0:
                output.append(response[index])
                index += 1
                continue

            payload = response[payload_start:cursor]
            condition_text, branch_block = self._split_top_level(payload)
            if branch_block is None:
                output.append(response[index:cursor + 1])
                index = cursor + 1
                continue

            true_branch, false_branch = self._split_if_branches(branch_block)

            condition_result = await self._evaluate_condition_expression(condition_text, message, context)
            chosen_branch = true_branch if condition_result else false_branch
            chosen_branch = await self._resolve_if_expressions(chosen_branch, message, context)
            output.append(chosen_branch)
            index = cursor + 1

        return "".join(output)

    def _extract_embed_tokens(self, response: str):
        directives = {
            "embedtitle:": "title",
            "embeddescription:": "description",
            "embedurl:": "url",
            "embedimage:": "image",
            "embedcolor:": "color",
            "embedthumbnail:": "thumbnail",
            "embedfooter:": "footer",
            "embedfooterimage:": "footer_image",
            "embedauthor:": "author",
            "embedauthorurl:": "author_url",
            "embedauthorimage:": "author_image",
            "embedtime:": "time",
            "embedfield:": "field",
        }
        extracted = {
            "title": None,
            "description": None,
            "url": None,
            "image": None,
            "color": None,
            "thumbnail": None,
            "footer": None,
            "footer_image": None,
            "author": None,
            "author_url": None,
            "author_image": None,
            "time": None,
            "fields": [],
        }
        output = []
        index = 0
        response_length = len(response)

        while index < response_length:
            if response[index] != "{":
                output.append(response[index])
                index += 1
                continue

            matched_prefix = None
            matched_key = None
            remaining = response[index + 1:].lower()
            for prefix, key in directives.items():
                if remaining.startswith(prefix):
                    matched_prefix = prefix
                    matched_key = key
                    break

            if matched_prefix is None:
                output.append(response[index])
                index += 1
                continue

            value_start = index + 1 + len(matched_prefix)
            cursor = value_start
            depth = 1
            while cursor < response_length:
                if response[cursor] == "{":
                    depth += 1
                elif response[cursor] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                cursor += 1

            if cursor >= response_length or depth != 0:
                output.append(response[index])
                index += 1
                continue

            payload = response[value_start:cursor]
            if matched_key == "field":
                field_name, field_value = self._split_top_level(payload)
                if field_value is not None:
                    extracted["fields"].append((field_name, field_value))
            else:
                extracted[matched_key] = payload
            index = cursor + 1

        return "".join(output), extracted

    async def _resolve_response_variables(self, response: str, message: discord.Message, context: dict) -> str:
        if not response:
            return response

        response = await self._resolve_if_expressions(response, message, context)

        guild = message.guild
        author = message.author
        channel = message.channel
        now = context["now"]
        am_pm = "上午" if now.hour < 12 else "下午"
        hour_12 = now.hour % 12 or 12
        content_parts = message.content.split()
        role_name = getattr(getattr(author, "top_role", None), "name", "")
        channel_name = getattr(channel, "name", "")

        replacements = {
            "{user}": author.mention,
            "{content}": message.content,
            "{guild}": guild.name,
            "{server}": guild.name,
            "{channel}": channel_name,
            "{author}": author.name,
            "{member}": author.name,
            "{authorid}": str(author.id),
            "{authoravatar}": author.display_avatar.url if author.display_avatar else "",
            "{role}": role_name,
            "{id}": str(author.id),
            "{date}": now.strftime("%Y/%m/%d"),
            "{year}": now.strftime("%Y"),
            "{month}": now.strftime("%m"),
            "{day}": now.strftime("%d"),
            "{time}": f"{am_pm} {hour_12:02d}:{now.minute:02d}",
            "{time24}": now.strftime("%H:%M"),
            "{hour}": now.strftime("%H"),
            "{minute}": now.strftime("%M"),
            "{second}": now.strftime("%S"),
            "{null}": "",
            "\\n": "\n",
            "\\t": "\t"
        }

        for key, value in replacements.items():
            response = response.replace(key, value)

        def content_split_replacer(match):
            token = match.group(1)
            return self._resolve_contentsplit_token(token, content_parts)

        response = re.sub(r"\{(contentsplit:[^{}]+|contentsplit\(-?\d+\))\}", content_split_replacer, response)

        if "{random}" in response:
            response = response.replace("{random}", context["random"])

        randint_pattern = re.compile(r"\{randint:(\d+)-(\d+)\}")

        def randint_replacer(match):
            try:
                min_val = int(match.group(1))
                max_val = int(match.group(2))
                if min_val > max_val:
                    min_val, max_val = max_val, min_val
                return str(random.randint(min_val, max_val))
            except (ValueError, IndexError):
                return match.group(0)

        response = randint_pattern.sub(randint_replacer, response)

        if "{random_user}" in response:
            if context["random_user"] is None:
                try:
                    users = set()
                    async for history_message in channel.history(limit=50):
                        if not history_message.author.bot:
                            users.add(history_message.author)
                    if users:
                        context["random_user"] = random.choice(list(users)).display_name
                    else:
                        context["random_user"] = "查無使用者"
                except Exception as e:
                    log(f"?? {{random_user}} ??隤? {e}", module_name="AutoReply", level=logging.ERROR)
                    context["random_user"] = "無法取得使用者"
            response = response.replace("{random_user}", context["random_user"])

        current_timestamp = str(int(now.timestamp()))

        def timemd_replacer(match):
            style = match.group(1)
            if style == "r":
                style = "R"
            return f"<t:{current_timestamp}:{style}>"

        response = re.sub(r"\{timemd:([tTdDfFrR])\}", timemd_replacer, response)

        def state_var_replacer(match):
            token = match.group(1)
            try:
                scope, key_text, value_text = self._parse_state_var_token(token)
            except TemplateSyntaxError:
                return ""

            key_text = key_text.strip()
            if not key_text or len(key_text) > AUTOREPLY_VAR_MAX_LENGTH:
                return ""

            storage_key = self._get_autoreply_var_storage_key(key_text)
            if value_text is None:
                if scope == "user":
                    stored_value = get_user_data(guild.id, author.id, storage_key, "")
                else:
                    stored_value = get_server_config(guild.id, storage_key, "")
                stored_value = "" if stored_value is None else str(stored_value)
                return stored_value[:AUTOREPLY_VAR_MAX_LENGTH]

            raw_value = str(value_text)
            if len(raw_value) > AUTOREPLY_VAR_MAX_LENGTH:
                return ""

            if scope == "user":
                self._set_autoreply_user_var(guild.id, author.id, key_text, raw_value)
            else:
                self._set_autoreply_guild_var(guild.id, key_text, raw_value)
            return ""

        response = re.sub(r"\{((?:user|guild)var:[^{}]+)\}", state_var_replacer, response, flags=re.IGNORECASE)
        response = self._resolve_math_tokens(response)

        return response

    def _resolve_math_tokens(self, response: str) -> str:
        if not response or "{math:" not in response.lower():
            return response

        output = []
        index = 0
        response_length = len(response)

        while index < response_length:
            if response[index] != "{" or not response[index:index + 6].lower() == "{math:":
                output.append(response[index])
                index += 1
                continue

            closing_index = self._find_matching_brace(response, index)
            if closing_index == -1:
                output.append(response[index])
                index += 1
                continue

            token = response[index + 1:closing_index]
            try:
                expression = self._parse_math_token(token)
                expression = self._resolve_math_tokens(expression)
                output.append(self._evaluate_math_expression(expression))
            except TemplateSyntaxError:
                output.append("")

            index = closing_index + 1

        return "".join(output)

    async def _build_embed_from_tokens(self, extracted: dict, message: discord.Message, context: dict):
        embed_requested = any(
            extracted[key] is not None
            for key in (
                "title",
                "description",
                "url",
                "image",
                "color",
                "thumbnail",
                "footer",
                "footer_image",
                "author",
                "author_url",
                "author_image",
                "time",
            )
        ) or bool(extracted["fields"])
        if not embed_requested:
            return None

        embed = discord.Embed()

        if extracted["title"] is not None:
            title = (await self._resolve_response_variables(extracted["title"], message, context)).strip()
            if title:
                embed.title = title

        if extracted["description"] is not None:
            description = (await self._resolve_response_variables(extracted["description"], message, context)).strip()
            if description:
                embed.description = description

        if extracted["url"] is not None:
            embed_url = (await self._resolve_response_variables(extracted["url"], message, context)).strip()
            if embed_url:
                embed.url = embed_url

        if extracted["image"] is not None:
            image_url = (await self._resolve_response_variables(extracted["image"], message, context)).strip()
            if image_url:
                embed.set_image(url=image_url)

        if extracted["thumbnail"] is not None:
            thumbnail_url = (await self._resolve_response_variables(extracted["thumbnail"], message, context)).strip()
            if thumbnail_url:
                embed.set_thumbnail(url=thumbnail_url)

        if extracted["footer"] is not None:
            footer_text = (await self._resolve_response_variables(extracted["footer"], message, context)).strip()
        else:
            footer_text = ""

        if extracted["footer_image"] is not None:
            footer_image_url = (await self._resolve_response_variables(extracted["footer_image"], message, context)).strip()
        else:
            footer_image_url = ""

        if footer_text or footer_image_url:
            embed.set_footer(text=footer_text or "\u200b", icon_url=footer_image_url or None)

        if extracted["author"] is not None:
            author_name = (await self._resolve_response_variables(extracted["author"], message, context)).strip()
        else:
            author_name = ""

        if extracted["author_url"] is not None:
            author_url = (await self._resolve_response_variables(extracted["author_url"], message, context)).strip()
        else:
            author_url = ""

        if extracted["author_image"] is not None:
            author_icon_url = (await self._resolve_response_variables(extracted["author_image"], message, context)).strip()
        else:
            author_icon_url = ""

        if author_name or author_url or author_icon_url:
            embed.set_author(
                name=author_name or "\u200b",
                url=author_url or None,
                icon_url=author_icon_url or None,
            )

        if extracted["color"] is not None:
            color_value = (await self._resolve_response_variables(extracted["color"], message, context)).strip()
            parsed_color = self._parse_embed_color(color_value)
            if parsed_color is not None:
                embed.color = discord.Colour(parsed_color)

        if extracted["time"] is not None:
            time_value = (await self._resolve_response_variables(extracted["time"], message, context)).strip()
            if self._parse_bool(time_value):
                embed.timestamp = context["now"]

        for field_name, field_value in extracted["fields"][:25]:
            resolved_name = (await self._resolve_response_variables(field_name, message, context)).strip()
            resolved_value = (await self._resolve_response_variables(field_value, message, context)).strip()
            if resolved_name and resolved_value:
                embed.add_field(name=resolved_name, value=resolved_value, inline=False)

        return embed

    def _build_template_context(self) -> dict:
        return {
            "now": datetime.now().astimezone(),
            "random": str(random.randint(1, 100)),
            "random_user": None,
        }

    def _extract_timed_response_plan(self, response: str):
        stages = [{"send_delay": 0, "template": "", "edits": []}]
        current_stage = stages[0]
        current_target = "template"
        buffer = []
        newmsg_count = 0
        edit_count = 0
        index = 0

        while index < len(response):
            if response[index] != "{":
                buffer.append(response[index])
                index += 1
                continue

            closing_index = self._find_matching_brace(response, index)
            if closing_index == -1:
                buffer.append(response[index])
                index += 1
                continue

            token = response[index + 1:closing_index]
            lowered = token.lower()
            if lowered.startswith("newmsg:") or lowered.startswith("edit:"):
                directive_name, delay_seconds = self._parse_delay_directive_token(token)
                current_chunk = "".join(buffer)
                buffer = []

                if current_target == "template":
                    current_stage["template"] += current_chunk
                else:
                    current_stage["edits"][-1]["template"] += current_chunk

                if directive_name == "newmsg":
                    newmsg_count += 1
                    if newmsg_count > AUTOREPLY_NEWMESSAGE_LIMIT:
                        raise TemplateSyntaxError(f"newmsg limit exceeded ({AUTOREPLY_NEWMESSAGE_LIMIT})")
                    current_stage = {"send_delay": delay_seconds, "template": "", "edits": []}
                    stages.append(current_stage)
                    current_target = "template"
                else:
                    edit_count += 1
                    if edit_count > AUTOREPLY_EDIT_LIMIT:
                        raise TemplateSyntaxError(f"edit limit exceeded ({AUTOREPLY_EDIT_LIMIT})")
                    current_stage["edits"].append({"delay": delay_seconds, "template": ""})
                    current_target = "edit"

                index = closing_index + 1
                continue

            buffer.append(response[index:closing_index + 1])
            index = closing_index + 1

        remaining_chunk = "".join(buffer)
        if current_target == "template":
            current_stage["template"] += remaining_chunk
        else:
            current_stage["edits"][-1]["template"] += remaining_chunk

        return stages

    async def _render_response_segment(self, response: str, message: discord.Message, context: dict | None = None) -> tuple:
        if context is None:
            context = self._build_template_context()

        response = (await self._resolve_response_variables(response, message, context)).strip()

        react_pattern = re.compile(r"\{react:([^\}]+)\}")

        def react_replacer(match):
            emoji_str = match.group(1).strip()
            try:
                if emoji_str.isdigit():
                    emoji = discord.utils.get(message.guild.emojis, id=int(emoji_str))
                    if emoji:
                        asyncio.create_task(message.add_reaction(emoji))
                else:
                    asyncio.create_task(message.add_reaction(emoji_str))
                log(f"自動回覆觸發，對訊息添加反應：{emoji_str}", module_name="AutoReply", level=logging.INFO)
            except Exception as e:
                log(f"處理 {{react:{emoji_str}}} 時發生錯誤: {e}", module_name="AutoReply", level=logging.ERROR)
            return ""

        response = react_pattern.sub(react_replacer, response)

        sticker = None
        sticker_pattern = re.compile(r"\{sticker:(\d+)\}")

        def sticker_replacer(match):
            sticker_id = int(match.group(1))
            try:
                nonlocal sticker
                sticker = discord.utils.get(message.guild.stickers, id=sticker_id)
            except Exception as e:
                log(f"處理 {{sticker:{sticker_id}}} 時發生錯誤: {e}", module_name="AutoReply", level=logging.ERROR)
            return ""

        response = sticker_pattern.sub(sticker_replacer, response)
        response, extracted_embed = self._extract_embed_tokens(response)
        embed = await self._build_embed_from_tokens(extracted_embed, message, context)

        if not response and not sticker and embed is None:
            return "", None, None

        return response, sticker, embed

    async def _send_autoreply_message(self, trigger_message: discord.Message, reply_mode: bool, content: str, embed: discord.Embed | None, sticker):
        send_content = content or None
        if reply_mode:
            return await trigger_message.reply(
                send_content,
                embed=embed,
                stickers=[sticker] if sticker else [],
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        return await trigger_message.channel.send(
            send_content,
            embed=embed,
            stickers=[sticker] if sticker else [],
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )

    async def _execute_autoreply_edits(self, sent_message: discord.Message, trigger_message: discord.Message, edit_actions: list[dict]):
        for edit_action in edit_actions:
            try:
                await asyncio.sleep(edit_action["delay"])
                edit_content, _, edit_embed = await self._render_response_segment(edit_action["template"], trigger_message)
                if not edit_content and edit_embed is None:
                    continue
                await sent_message.edit(
                    content=edit_content or None,
                    embed=edit_embed,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
            except discord.HTTPException as e:
                log(f"自動回覆編輯失敗: {e}", module_name="AutoReply", level=logging.ERROR)
                return
            except Exception as e:
                log(f"自動回覆編輯發生錯誤: {e}", module_name="AutoReply", level=logging.ERROR)
                return

    async def _execute_autoreply_followup_stage(self, trigger_message: discord.Message, reply_mode: bool, stage: dict):
        try:
            await asyncio.sleep(stage["send_delay"])

            if self._is_rate_limited(trigger_message):
                return

            followup_content, followup_sticker, followup_embed = await self._render_response_segment(stage["template"], trigger_message)
            if not followup_content and not followup_sticker and followup_embed is None:
                return

            sent_message = await self._send_autoreply_message(
                trigger_message,
                reply_mode,
                followup_content,
                followup_embed,
                followup_sticker,
            )

            if stage["edits"]:
                asyncio.create_task(self._execute_autoreply_edits(sent_message, trigger_message, stage["edits"]))
        except discord.HTTPException as e:
            log(f"自動回覆延遲訊息發送失敗: {e}", module_name="AutoReply", level=logging.ERROR)
        except Exception as e:
            log(f"自動回覆延遲訊息發生錯誤: {e}", module_name="AutoReply", level=logging.ERROR)

    async def _process_response_v2(self, response: str, message: discord.Message) -> tuple:
        """Process autoreply response text and return the immediate result plus delayed actions."""

        try:
            self._validate_template_syntax(response)
        except TemplateSyntaxError as e:
            log(f"自動回覆模板語法錯誤: {e}", module_name="AutoReply", level=logging.WARNING)
            return "", None, None, {"initial_edits": [], "followups": []}

        planning_context = self._build_template_context()
        resolved_response = await self._resolve_if_expressions(response, message, planning_context)

        try:
            response_stages = self._extract_timed_response_plan(resolved_response)
        except TemplateSyntaxError as e:
            log(f"自動回覆模板語法錯誤: {e}", module_name="AutoReply", level=logging.WARNING)
            return "", None, None, {"initial_edits": [], "followups": []}

        initial_stage = response_stages[0] if response_stages else {"template": "", "edits": []}
        final_response, sticker, embed = await self._render_response_segment(initial_stage["template"], message)
        delayed_actions = {
            "initial_edits": initial_stage["edits"],
            "followups": response_stages[1:],
        }

        return final_response, sticker, embed, delayed_actions

    @app_commands.command(name="add", description="新增自動回覆")
    @app_commands.describe(
        mode="回覆模式",
        trigger="觸發字串 (使用 , 分隔多個觸發字串)",
        response="回覆內容 (使用 , 分隔多個回覆，隨機選擇一個回覆)",
        reply="回覆原訊息",
        channel_mode="指定頻道模式",
        channels="指定頻道 ID (使用 , 分隔多個頻道 ID)",
        random_chance="隨機回覆機率 (1-100)"
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="包含", value="contains"),
            app_commands.Choice(name="完全匹配", value="equals"),
            app_commands.Choice(name="開始於", value="starts_with"),
            app_commands.Choice(name="結束於", value="ends_with"),
            app_commands.Choice(name="正規表達式", value="regex"),
        ],
        reply=[
            app_commands.Choice(name="是", value="True"),
            app_commands.Choice(name="否", value="False"),
        ],
        channel_mode=[
            app_commands.Choice(name="所有頻道", value="all"),
            app_commands.Choice(name="白名單", value="whitelist"),
            app_commands.Choice(name="黑名單", value="blacklist"),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def add_autoreply(self, interaction: discord.Interaction, mode: str, trigger: str, response: str, reply: str = "False", channel_mode: str = "all", channels: str = "", random_chance: int = 100):
        guild_id = interaction.guild.id
        reply = (reply == "True")
        if random_chance < 1 or random_chance > 100:
            await interaction.response.send_message("隨機回覆機率必須在 1 到 100 之間。", ephemeral=True)
            return
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        autoreply_limit = self._get_autoreply_limit(guild_id)
        if len(autoreplies) >= autoreply_limit:
            await interaction.response.send_message(f"自動回覆設定最多只能有 {autoreply_limit} 筆。\n> 想要增加限制？\n> 前往支援伺服器開啟客服單取得支援！\n> {config('support_server_invite')}", ephemeral=True)
            return
        trigger = trigger.split(",")  # multiple triggers
        trigger = [t.strip() for t in trigger if t.strip()]  # remove empty triggers
        duplicate_triggers = self._find_duplicate_triggers_in_list(trigger)
        if duplicate_triggers:
            await interaction.response.send_message(
                self._format_autoreply_trigger_conflict_message(duplicate_triggers, existing=False),
                ephemeral=True
            )
            return
        conflicting_triggers = self._find_conflicting_autoreply_triggers(autoreplies, trigger)
        if conflicting_triggers:
            await interaction.response.send_message(
                self._format_autoreply_trigger_conflict_message(conflicting_triggers, existing=True),
                ephemeral=True
            )
            return
        response = response.split(",")  # random response
        response = [r.strip() for r in response if r.strip()]  # remove empty responses
        channels = channels.split(",") if channels else []
        channels = [int(c.strip()) for c in channels if c.strip().isdigit()]
        # verify channels exist in guild
        valid_channels = []
        for c in channels:
            if interaction.guild.get_channel(c):
                valid_channels.append(c)
        autoreplies.append({"trigger": trigger, "response": response, "mode": mode, "reply": reply, "channel_mode": channel_mode, "channels": valid_channels, "random_chance": random_chance})
        set_server_config(guild_id, "autoreplies", autoreplies)
        trigger_str = ", ".join(trigger)
        trigger_str = trigger_str if len(trigger_str) <= 100 else trigger_str[:97] + "..."
        response_str = ", ".join(response)
        response_str = response_str if len(response_str) <= 100 else response_str[:97] + "..."
        embed = discord.Embed(title="新增自動回覆成功", color=0x00ff00)
        embed.add_field(name="模式", value=mode)
        embed.add_field(name="觸發字串", value=f"`{trigger_str}`")
        embed.add_field(name="回覆內容", value=f"`{response_str}`")
        embed.add_field(name="回覆原訊息", value="是" if reply else "否")
        embed.add_field(name="指定頻道模式", value=channel_mode)
        embed.add_field(name="指定頻道", value=f"`{', '.join(map(str, valid_channels)) if valid_channels else '無'}`")
        embed.add_field(name="隨機回覆機率", value=f"{random_chance}%")
        await interaction.response.send_message(embed=embed)
        trigger_str = ", ".join(trigger)
        log(f"自動回覆被新增：`{trigger_str[:10]}{'...' if len(trigger_str) > 10 else ''}`。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)

    @app_commands.command(name="remove", description="移除自動回覆")
    @app_commands.describe(
        trigger="觸發字串"
    )
    @app_commands.autocomplete(trigger=list_autoreply_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def remove_autoreply(self, interaction: discord.Interaction, trigger: str):
        guild_id = interaction.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        for ar in autoreplies:
            det = ", ".join(ar["trigger"])
            if det == trigger:
                autoreplies.remove(ar)
                set_server_config(guild_id, "autoreplies", autoreplies)
                await interaction.response.send_message(f"已移除自動回覆：`{trigger}`。")
                log(f"自動回覆被移除：`{trigger[:10]}{'...' if len(trigger) > 10 else ''}`。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
                return
        await interaction.response.send_message(f"找不到觸發字串 `{trigger}` 的自動回覆。")
    
    @app_commands.command(name="list", description="列出所有自動回覆")
    @app_commands.default_permissions(manage_guild=True)
    async def list_autoreplies(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        if not autoreplies:
            await interaction.response.send_message("目前沒有設定任何自動回覆。")
            return
        description = ""
        for i, ar in enumerate(autoreplies, start=1):
            triggers = ", ".join(ar["trigger"])
            triggers = triggers if len(triggers) <= 100 else triggers[:97] + "..."
            responses = ", ".join(ar["response"])
            responses = responses if len(responses) <= 100 else responses[:97] + "..."
            # fix old data without reply and channel_mode and channels
            ar.setdefault("reply", False)
            ar.setdefault("channel_mode", "all")
            ar.setdefault("channels", [])
            ar.setdefault("random_chance", 100)
            triggers = triggers if len(triggers) <= 100 else triggers[:97] + "..."
            responses = responses if len(responses) <= 100 else responses[:97] + "..."
            description += f"**{i}.** 模式：{ar['mode']}，觸發字串：`{triggers}`，回覆內容：`{responses}`，回覆原訊息：{'是' if ar['reply'] else '否'}，指定頻道模式：{ar['channel_mode']}，指定頻道：`{', '.join(map(str, ar['channels'])) if ar['channels'] else '無'}`，隨機回覆機率：{ar['random_chance']}%\n"
        embed = discord.Embed(title="自動回覆列表", description=description, color=0x00ff00)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="clear", description="清除所有自動回覆")
    @app_commands.default_permissions(manage_guild=True)
    async def clear_autoreplies(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        user_id = interaction.user.id
        class Confirm(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=30)
            
            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(content="操作逾時，已取消清除自動回覆。", view=self)
                self.stop()

            @discord.ui.button(label="確認清除", style=discord.ButtonStyle.danger)
            async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != user_id:
                    await interaction.response.send_message("只有發起操作的使用者可以確認清除。", ephemeral=True)
                    return
                set_server_config(guild_id, "autoreplies", [])
                for child in self.children:
                    child.disabled = True
                await interaction.response.edit_message(content="已清除所有自動回覆。", view=self)
                log(f"所有自動回覆被清除。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
                self.stop()

            @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
            async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
                for child in self.children:
                    child.disabled = True
                await interaction.response.edit_message(content="已取消清除自動回覆。", view=self)
                self.stop()

        await interaction.response.send_message(f"您確定要清除所有自動回覆嗎？\n目前有 {len(autoreplies)} 筆自動回覆。", view=Confirm())

    @app_commands.command(name="edit", description="編輯自動回覆")
    @app_commands.describe(
        trigger="觸發字串",
        new_mode="新的回覆模式",
        new_trigger="新的觸發字串",
        new_response="回覆內容",
        reply="是否回覆原訊息",
        channel_mode="指定頻道模式",
        channels="指定頻道 ID (使用 , 分隔多個頻道 ID)",
        random_chance="隨機回覆機率 (1-100)"
    )
    @app_commands.choices(
        new_mode=[
            app_commands.Choice(name="包含", value="contains"),
            app_commands.Choice(name="完全匹配", value="equals"),
            app_commands.Choice(name="開始於", value="starts_with"),
            app_commands.Choice(name="結束於", value="ends_with"),
            app_commands.Choice(name="正規表達式", value="regex"),
        ],
        reply=[
            app_commands.Choice(name="是", value="True"),
            app_commands.Choice(name="否", value="False"),
        ],
        channel_mode=[
            app_commands.Choice(name="所有頻道", value="all"),
            app_commands.Choice(name="白名單", value="whitelist"),
            app_commands.Choice(name="黑名單", value="blacklist"),
        ]
    )
    @app_commands.autocomplete(trigger=list_autoreply_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def edit_autoreply(self, interaction: discord.Interaction, trigger: str, new_mode: str = None, new_trigger: str = None, new_response: str = None, reply: str = None, channel_mode: str = None, channels: str = None, random_chance: int = None):
        guild_id = interaction.guild.id
        reply = None if reply is None else (True if reply == "True" else False)
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        if random_chance is not None:
            if random_chance < 1 or random_chance > 100:
                await interaction.response.send_message("隨機回覆機率必須在 1 到 100 之間。", ephemeral=True)
                return
        for ar in autoreplies:
            det = ", ".join(ar["trigger"])
            det = det if len(det) <= 100 else det[:97] + "..."
            if det == trigger:
                if new_mode:
                    ar["mode"] = new_mode
                if new_trigger:
                    ar["trigger"] = [t.strip() for t in new_trigger.split(",") if t.strip()]
                if new_response:
                    ar["response"] = [r.strip() for r in new_response.split(",") if r.strip()]
                if reply is not None:
                    ar["reply"] = reply
                if channel_mode:
                    ar["channel_mode"] = channel_mode
                if channels:
                    ar["channels"] = [int(c.strip()) for c in channels.split(",") if c.strip().isdigit()]
                if random_chance is not None:
                    ar["random_chance"] = random_chance
                set_server_config(guild_id, "autoreplies", autoreplies)
                trigger_str = ", ".join(ar["trigger"])
                trigger_str = trigger_str if len(trigger_str) <= 100 else trigger_str[:97] + "..."
                response_str = ", ".join(ar["response"])
                response_str = response_str if len(response_str) <= 100 else response_str[:97] + "..."
                embed = discord.Embed(title="編輯自動回覆成功", color=0x00ff00)
                embed.add_field(name="模式", value=ar["mode"])
                embed.add_field(name="觸發字串", value=f"`{trigger_str}`")
                embed.add_field(name="回覆內容", value=f"`{response_str}`")
                embed.add_field(name="回覆原訊息", value="是" if ar["reply"] else "否")
                embed.add_field(name="指定頻道模式", value=ar["channel_mode"])
                embed.add_field(name="指定頻道", value=f"`{', '.join(map(str, ar['channels'])) if ar['channels'] else '無'}`")
                embed.add_field(name="隨機回覆機率", value=f"{ar['random_chance']}%")
                await interaction.response.send_message(embed=embed)
                log(f"自動回覆被編輯：`{det[:10]}{'...' if len(det) > 10 else ''}`。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
                return
        await interaction.response.send_message(f"找不到觸發字串 `{trigger}` 的自動回覆。")
    
    @app_commands.command(name="quickadd", description="快速新增自動回覆，合併現有的自動回覆")
    @app_commands.describe(
        trigger="觸發字串",
        new_trigger="新的觸發字串",
        new_response="新的回覆內容"
    )
    @app_commands.autocomplete(trigger=list_autoreply_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def quick_add_autoreply(self, interaction: discord.Interaction, trigger: str, new_trigger: str = "", new_response: str = ""):
        guild_id = interaction.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        for ar in autoreplies:
            det = ", ".join(ar["trigger"])
            det = det if len(det) <= 100 else det[:97] + "..."
            if det == trigger:
                if new_trigger:
                    new_triggers = [t.strip() for t in new_trigger.split(",") if t.strip()]
                    duplicate_triggers = self._find_duplicate_triggers_in_list(new_triggers)
                    if duplicate_triggers:
                        await interaction.response.send_message(
                            self._format_autoreply_trigger_conflict_message(duplicate_triggers, existing=False),
                            ephemeral=True
                        )
                        return
                    conflicting_triggers = self._find_conflicting_autoreply_triggers(autoreplies, new_triggers, skip_rule=ar)
                    if conflicting_triggers:
                        await interaction.response.send_message(
                            self._format_autoreply_trigger_conflict_message(conflicting_triggers, existing=True),
                            ephemeral=True
                        )
                        return
                    ar["trigger"].extend(new_triggers)
                    ar["trigger"] = list(set(ar["trigger"]))  # remove duplicates
                if new_response:
                    new_responses = [r.strip() for r in new_response.split(",") if r.strip()]
                    ar["response"].extend(new_responses)
                    ar["response"] = list(set(ar["response"]))  # remove duplicates
                set_server_config(guild_id, "autoreplies", autoreplies)
                trigger_str = ", ".join(ar["trigger"])
                trigger_str = trigger_str if len(trigger_str) <= 100 else trigger_str[:97] + "..."
                response_str = ", ".join(ar["response"])
                response_str = response_str if len(response_str) <= 100 else response_str[:97] + "..."
                embed = discord.Embed(title="快速新增自動回覆成功", color=0x00ff00)
                embed.add_field(name="模式", value=ar["mode"])
                embed.add_field(name="觸發字串", value=f"`{trigger_str}`")
                embed.add_field(name="回覆內容", value=f"`{response_str}`")
                embed.add_field(name="回覆原訊息", value="是" if ar["reply"] else "否")
                embed.add_field(name="指定頻道模式", value=ar["channel_mode"])
                embed.add_field(name="指定頻道", value=f"`{', '.join(map(str, ar['channels'])) if ar['channels'] else '無'}`")
                embed.add_field(name="隨機回覆機率", value=f"{ar['random_chance']}%")
                await interaction.response.send_message(embed=embed)
                log(f"自動回覆被快速新增：`{det}`。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
                return
        await interaction.response.send_message(f"找不到觸發字串 `{trigger}` 的自動回覆。")

    @app_commands.command(name="template", description="套用內建自動回覆範本包")
    @app_commands.describe(pack="要套用的範本包", merge="是否與現有規則合併")
    @app_commands.choices(
        merge=[
            app_commands.Choice(name="是", value="True"),
            app_commands.Choice(name="否（覆蓋現有規則）", value="False"),
        ]
    )
    @app_commands.autocomplete(pack=list_template_pack_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def apply_autoreply_template(self, interaction: discord.Interaction, pack: str, merge: str = "True"):
        pack_data = AUTOREPLY_TEMPLATE_PACKS.get(pack)
        if pack_data is None:
            await interaction.response.send_message("找不到這個範本包。", ephemeral=True)
            return

        guild_id = interaction.guild.id
        merge_enabled = (merge == "True")
        current_autoreplies = get_server_config(guild_id, "autoreplies", [])
        template_rules = copy.deepcopy(pack_data["rules"])
        skipped_duplicates = 0

        if merge_enabled:
            final_autoreplies = list(current_autoreplies)
            existing_rules = {
                json.dumps(rule, ensure_ascii=False, sort_keys=True)
                for rule in current_autoreplies
            }
            for rule in template_rules:
                serialized_rule = json.dumps(rule, ensure_ascii=False, sort_keys=True)
                if serialized_rule in existing_rules:
                    skipped_duplicates += 1
                    continue
                existing_rules.add(serialized_rule)
                final_autoreplies.append(rule)
            added_count = len(final_autoreplies) - len(current_autoreplies)
        else:
            final_autoreplies = template_rules
            added_count = len(template_rules)

        autoreply_limit = self._get_autoreply_limit(guild_id)
        if len(final_autoreplies) > autoreply_limit:
            await interaction.response.send_message(
                f"套用後會超過 {autoreply_limit} 筆自動回覆上限，這次未套用。",
                ephemeral=True
            )
            return

        set_server_config(guild_id, "autoreplies", final_autoreplies)

        preview_lines = []
        for index, rule in enumerate(pack_data["rules"][:5], start=1):
            trigger_preview = ", ".join(rule["trigger"])
            trigger_preview = trigger_preview if len(trigger_preview) <= 40 else trigger_preview[:37] + "..."
            preview_lines.append(f"{index}. {rule['mode']} / {trigger_preview}")
        preview_text = "\n".join(preview_lines) if preview_lines else "無"

        embed = discord.Embed(
            title="已套用自動回覆範本包",
            description=pack_data["description"],
            color=0x57F287 if added_count else 0xFEE75C,
        )
        embed.add_field(name="範本包", value=f"`{pack_data['display_name']}` (`{pack}`)", inline=False)
        embed.add_field(name="套用模式", value="合併現有規則" if merge_enabled else "覆蓋現有規則")
        embed.add_field(name="新增規則", value=str(added_count))
        if merge_enabled:
            embed.add_field(name="略過重複", value=str(skipped_duplicates))
        embed.add_field(name="目前總數", value=str(len(final_autoreplies)))
        embed.add_field(name="內含規則", value=preview_text, inline=False)
        await interaction.response.send_message(embed=embed)
        log(
            f"自動回覆範本包被套用：{pack}，模式：{'merge' if merge_enabled else 'replace'}，新增 {added_count} 筆，略過 {skipped_duplicates} 筆。",
            module_name="AutoReply",
            level=logging.INFO,
            user=interaction.user,
            guild=interaction.guild
        )
    
    @app_commands.command(name="export", description="匯出自動回覆設定為 JSON")
    @app_commands.default_permissions(administrator=True)
    async def export_autoreplies(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        if not autoreplies:
            await interaction.response.send_message("此伺服器尚未設定自動回覆。")
            return
        json_data = json.dumps(autoreplies, ensure_ascii=False, indent=4)
        file = discord.File(io.StringIO(json_data), filename="autoreplies.json")
        await interaction.response.send_message("以下是此伺服器的自動回覆設定 JSON 檔案：", file=file)
        log(f"自動回覆設定被匯出。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
    
    @app_commands.command(name="import", description="從 JSON 檔案匯入自動回覆設定")
    @app_commands.describe(file="要匯入的 JSON 檔案", merge="是否與現有設定合併")
    @app_commands.choices(
        merge=[
            app_commands.Choice(name="是", value="True"),
            app_commands.Choice(name="否", value="False")
        ]
    )
    @app_commands.default_permissions(administrator=True)
    async def import_autoreplies(self, interaction: discord.Interaction, file: discord.Attachment, merge: str = "False"):
        merge = (merge == "True")
        guild_id = interaction.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        # if not autoreplies:
        #     await interaction.response.send_message("此伺服器尚未設定自動回覆。")
        #     return
        await interaction.response.defer()
        # download file content
        async with aiohttp.ClientSession() as session:
            async with session.get(file.url) as resp:
                if resp.status != 200:
                    await interaction.followup.send("無法下載檔案。")
                    return
                json_data = await resp.text()
        try:
            new_autoreplies = json.loads(json_data)
        except json.JSONDecodeError:
            await interaction.followup.send("無法解析 JSON 檔案。")
            return
        if merge:
            autoreplies.extend(new_autoreplies)
        else:
            autoreplies = new_autoreplies
        autoreply_limit = self._get_autoreply_limit(guild_id)
        if len(autoreplies) > autoreply_limit:
            await interaction.followup.send(f"自動回覆設定最多只能有 {autoreply_limit} 筆，這次匯入未套用。\n> 想要增加限制？\n> 前往支援伺服器開啟客服單取得支援！\n> {config('support_server_invite')}")
            return
        set_server_config(guild_id, "autoreplies", autoreplies)
        await interaction.followup.send("已匯入自動回覆設定。")
        log(f"自動回覆設定被匯入。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
    
    @app_commands.command(name="ignore", description="設定忽略的頻道")
    @app_commands.describe(
        mode="忽略頻道模式",
        channels="頻道 ID (使用 , 分隔多個頻道 ID)"
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="忽略清單", value="blacklist"),
            app_commands.Choice(name="僅限清單", value="whitelist"),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def set_ignore_channels(self, interaction: discord.Interaction, mode: str, channels: str):
        guild_id = interaction.guild.id
        channels = channels.split(",") if channels else []
        channels = [int(parse_channel_mention(c.strip())) for c in channels if parse_channel_mention(c.strip()).isdigit()]
        # verify channels exist in guild
        valid_channels = []
        for c in channels:
            if interaction.guild.get_channel(c):
                valid_channels.append(c)
        set_server_config(guild_id, "autoreply_ignore_mode", mode)
        set_server_config(guild_id, "autoreply_ignore_channels", valid_channels)
        await interaction.response.send_message(f"已設定忽略頻道模式為 `{mode}`，頻道列表：`{', '.join(map(str, valid_channels)) if valid_channels else '無'}`。")
        log(f"忽略頻道設定被更新。模式：{mode}，頻道：{valid_channels}", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
    
    @app_commands.command(name="test", description="測試自動回覆內容的變數替換")
    @app_commands.describe(response="要測試的回覆內容")
    @app_commands.default_permissions(manage_guild=True)
    async def test_autoreply_response(self, interaction: discord.Interaction, response: str):
        guild = interaction.guild
        author = interaction.user
        channel = interaction.channel

        # 建立一個模擬的訊息物件
        class MockMessage:
            def __init__(self, guild, author, channel, content):
                self.guild = guild
                self.author = author
                self.channel = channel
                self.content = content

            async def add_reaction(self, emoji):
                return None

        mock_message = MockMessage(guild, author, channel, "這是一則測試訊息內容。")

        final_response, _, embed, delayed_actions = await self._process_response_v2(response, mock_message)
        preview_text = final_response or None
        delayed_lines = []
        for edit_action in delayed_actions["initial_edits"]:
            delayed_lines.append(f"[edit {edit_action['delay']}s] {edit_action['template']}")
        for followup_stage in delayed_actions["followups"]:
            delayed_lines.append(f"[newmsg {followup_stage['send_delay']}s] {followup_stage['template']}")
            for edit_action in followup_stage["edits"]:
                delayed_lines.append(f"[edit {edit_action['delay']}s] {edit_action['template']}")
        if delayed_lines:
            delayed_preview = "\n".join(delayed_lines)
            preview_text = f"{preview_text}\n\n{delayed_preview}" if preview_text else delayed_preview
        if preview_text is None and embed is None:
            preview_text = "沒有可輸出的內容"
        await interaction.response.send_message(preview_text, embed=embed)

    @app_commands.command(name="builder", description="用互動式介面建立自動回覆")
    @app_commands.default_permissions(manage_guild=True)
    async def autoreply_builder(self, interaction: discord.Interaction):
        view = AutoReplyBuilderView(self, interaction)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)
        view.message = await interaction.original_response()
    
    @app_commands.command(name="help", description="顯示自動回覆的使用說明")
    async def autoreply_help(self, interaction: discord.Interaction):
        # vibe coding is fun lol
        await interaction.response.defer()
        embed = discord.Embed(
            title="自動回覆使用說明",
            description="您可以使用以下設定，讓回覆更加靈活。",
            color=0x00FF00,
        )
        
        embed.add_field(
            name="指令說明",
            value=(
                f"使用 {await get_command_mention('autoreply', 'add')} 指令新增自動回覆。\n"
                f"使用 {await get_command_mention('autoreply', 'quickadd')} 指令可以快速新增自動回覆到一個現有的自動回覆裡。\n"
                f"使用 {await get_command_mention('autoreply', 'list')} 指令可以列出目前所有的自動回覆。\n"
                f"使用 {await get_command_mention('autoreply', 'remove')} 指令可以移除指定的自動回覆。\n"
                f"使用 {await get_command_mention('autoreply', 'edit')} 指令可以編輯指定的自動回覆。\n"
                f"使用 {await get_command_mention('autoreply', 'clear')} 指令可以清除所有自動回覆。\n"
                f"使用 {await get_command_mention('autoreply', 'export')} 指令可以匯出自動回覆設定為 JSON 檔案。\n"
                f"使用 {await get_command_mention('autoreply', 'import')} 指令可以從 JSON 檔案匯入自動回覆設定。\n"
                f"使用 {await get_command_mention('autoreply', 'test')} 指令可以測試自動回覆內容的變數替換效果。"
            ),
            inline=False,
        )

        embed.add_field(
            name="基本變數",
            value=(
                "您可以在自動回覆的回覆內容中使用以下變數，讓回覆更靈活。\n"
                "- `{user}`：提及觸發者\n"
                "- `{content}`：觸發訊息內容\n"
                "- `{guild}` / `{server}`：伺服器名稱\n"
                "- `{channel}`：頻道名稱\n"
                "- `{author}` / `{member}`：觸發者名稱\n"
                "- `{role}`：觸發者最高角色名稱\n"
                "- `{id}`：觸發者 ID\n"
                "- `\\n`：換行\n"
                "- `\\t`：制表符"
            ),
            inline=False,
        )

        embed.add_field(
            name="隨機 / 進階",
            value=(
                "- `{random}`：隨機產生 1 到 100 的整數\n"
                "- `{randint:min-max}`：隨機產生 min~max（例：`{randint:10-50}`）\n"
                "- `{random_user}`：從最近 50 則訊息中隨機選一位非機器人使用者顯示名稱\n"
                "- `{react:emoji}`：給予該訊息表情符號（例：`{react:↖️}`）\n"
                "- `{sticker:sticker_id}`：傳送貼圖（例：`{sticker:123456789012345678}`）\n"
                "  - 貼圖 ID 可用 `y!sticker` 指令取得"
            ),
            inline=False,
        )

        embed.add_field(
            name="快速範例",
            value=(
                "- `你好 {user}，你剛剛說：{content}`\n"
                "- `今天的幸運數字是 {randint:1-99}`"
            ),
            inline=False,
        )

        class HelpView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
            
            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(view=self)
                self.stop()

            @discord.ui.button(label="顯示更多範例", style=discord.ButtonStyle.primary)
            async def examples(self, i: discord.Interaction, _: discord.ui.Button):
                ex = discord.Embed(title="自動回覆範例", color=0x00FF00)
                ex.description = (
                    "1) `歡迎 {user} 來到 {guild}！`\n"
                    "2) `你在 #{channel} 發了：{content}`\n"
                    "3) `抽獎號碼：{randint:1000-9999}`\n"
                    "4) `剛剛聊天室隨機點名：{random_user}`"
                )
                await i.response.send_message(embed=ex, ephemeral=True)

            @discord.ui.button(label="提示：測試替換", style=discord.ButtonStyle.secondary)
            async def hint(self, i: discord.Interaction, _: discord.ui.Button):
                await i.response.send_message(f"可用 {await get_command_mention('autoreply', 'test')} 測試變數替換結果。", ephemeral=True)

        await interaction.followup.send(embed=embed, view=HelpView())

    @app_commands.command(name="help", description="顯示自動回覆功能說明")
    async def autoreply_help(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed = discord.Embed(
            title="自動回覆使用說明",
            description="支援變數替換、條件判斷、Embed 回覆、貼圖/反應。",
            color=0x00FF00,
        )

        embed.add_field(
            name="指令說明",
            value=(
                f"{await get_command_mention('autoreply', 'add')}：新增規則\n"
                f"{await get_command_mention('autoreply', 'edit')} / {await get_command_mention('autoreply', 'remove')}：修改或刪除規則\n"
                f"{await get_command_mention('autoreply', 'quickadd')}：快速補 trigger / response\n"
                f"{await get_command_mention('autoreply', 'template')}：套用內建範本包\n"
                f"{await get_command_mention('autoreply', 'list')} / {await get_command_mention('autoreply', 'clear')}：查看或清空規則\n"
                f"{await get_command_mention('autoreply', 'export')} / {await get_command_mention('autoreply', 'import')}：匯出或匯入 JSON\n"
                f"{await get_command_mention('autoreply', 'ignore')}：設定全域忽略/白名單頻道\n"
                f"{await get_command_mention('autoreply', 'test')}：預覽變數、條件與 embed 效果"
            ),
            inline=False,
        )

        embed.add_field(
            name="基本變數",
            value=(
                "- `{user}` / `{author}` / `{authorid}` / `{authoravatar}` / `{member}` / `{id}`\n"
                "- `{content}` / `{channel}` / `{guild}` / `{server}` / `{role}`\n"
                "- `{null}` 空字串，可拿來做 `if` 比較\n"
                "- `\\n` 換行、`\\t` Tab"
            ),
            inline=False,
        )

        embed.add_field(
            name="日期與條件",
            value=(
                "- `{date}` `{year}` `{month}` `{day}`\n"
                "- `{time}` `{time24}` `{hour}` `{minute}` `{second}`\n"
                "- `{timemd:t}` ~ `{timemd:R}` 產生 Discord 時間戳\n"
                "- `{contentsplit:0}`、`{contentsplit:1-}`、`{contentsplit:-4}`、`{contentsplit:1-2}`\n"
                "- `{math:(1+2*3)}`，只支援 `+ - * /`，數字限制 `-1000 ~ 1000`\n"
                "- `math` 內可用其他變數，例如 `{math:({contentsplit:1}+5)}`\n"
                "- `{if:{contentsplit:1}==true:Yes:else:No}`\n"
                "- `{if:{contentsplit:1}!={null}:有內容:else:空白}`\n"
                "- 也支援 `{if:{contentsplit:1}==true:Yes:No}` 與 `{if:條件:成立內容}`\n"
                "- 支援 `==` `!=` `<=` `>=` `&&` `||`"
            ),
            inline=False,
        )

        embed.add_field(
            name="Embed / 進階效果",
            value=(
                "- `{random}` / `{randint:min-max}` / `{random_user}`\n"
                "- `{react:emoji}`、`{sticker:sticker_id}`\n"
                "- `{embedtitle:標題}` `{embeddescription:內容}` `{embedurl:連結}`\n"
                "- `{embedimage:連結}` `{embedthumbnail:連結}`\n"
                "- `{embedcolor:HEX}` `{embedfooter:文字}` `{embedfooterimage:連結}`\n"
                "- `{embedauthor:名字}` `{embedauthorurl:連結}` `{embedauthorimage:連結}`\n"
                "- `{embedtime:true}` `{embedfield:欄位名:欄位值}`\n"
                "- Embed 內文也可繼續使用其他 `{}` 變數"
            ),
            inline=False,
        )

        embed.add_field(
            name="延遲 / 狀態變數",
            value=(
                "- `{newmsg:2}`：1~3 秒後再發一則新訊息，最多 2 個\n"
                "- `{edit:2}`：1~3 秒後編輯目前這則 autoreply，最多 4 個\n"
                "- `{uservar:key}` / `{uservar:key:value}`\n"
                "- `{guildvar:key}` / `{guildvar:key:value}`\n"
                "- uservar 最多 5 個、guildvar 最多 10 個，key/value 最長 100"
            ),
            inline=False,
        )

        embed.add_field(
            name="內建範本包",
            value=(
                "- `daily_greetings`：早安 / 午安 / 晚安 / 安安\n"
                "- `mini_commands`：!say / !time / !date / !roll\n"
                "- `chat_fun`：簽到 / 抽一個人 / 今日運勢 / 好耶\n"
                f"- 可用 {await get_command_mention('autoreply', 'template')} 直接套用"
            ),
            inline=False,
        )

        embed.add_field(
            name="注意事項",
            value=(
                "- 同一個 guild 每 1 秒最多發 3 條 autoreply\n"
                "- 模板語法錯誤時會直接輸出空字串\n"
                "- 日期 / 時間變數跟隨機器人主機本地時區\n"
            ),
            inline=False,
        )

        class HelpView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)

            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(view=self)
                self.stop()

            @discord.ui.button(label="顯示更多範例", style=discord.ButtonStyle.primary)
            async def examples(self, i: discord.Interaction, _: discord.ui.Button):
                ex = discord.Embed(title="自動回覆範例", color=0x00FF00)
                ex.description = (
                    "1) `歡迎 {user} 來到 {guild}！`\n"
                    "2) `{if:{contentsplit:1}==true&&{hour}>=12:你在下午輸入了 true:還沒達成條件}`\n"
                    "3) `{if:{contentsplit:1}!={null}:你有輸入參數:else:你沒輸入參數}`\n"
                    "4) `{embedtitle:簽到成功}{embedurl:https://example.com}{embeddescription:{user} 在 {date} {time24} 完成簽到}{embedauthor:系統}{embedauthorimage:{authoravatar}}{embedcolor:57F287}`\n"
                    "5) `從第 2 個單字開始：{contentsplit:1-}`\n"
                    "6) `剛剛聊天室隨機點名：{random_user}`"
                )
                await i.response.send_message(embed=ex, ephemeral=True)

            @discord.ui.button(label="提示：測試替換", style=discord.ButtonStyle.secondary)
            async def hint(self, i: discord.Interaction, _: discord.ui.Button):
                await i.response.send_message(
                    f"可用 {await get_command_mention('autoreply', 'test')} 測試變數、條件與 embed 結果。",
                    ephemeral=True
                )

        await interaction.followup.send(embed=embed, view=HelpView())

    async def _process_response(self, response: str, message: discord.Message) -> tuple:
        """處理回覆內容中的變數替換或檢測給予訊息反應"""
        
        # 訊息反應
        # response 可能包含多個反應，以空格分隔
        # {react:emoji} 格式 (unicode emoji 或自訂 emoji ID)
        react_pattern = re.compile(r"\{react:([^\}]+)\}")
        def react_replacer(match):
            emoji_str = match.group(1).strip()
            try:
                if emoji_str.isdigit():
                    # 自訂表情符號 ID
                    emoji = message.guild.emojis.get(int(emoji_str))
                    if emoji:
                        asyncio.create_task(message.add_reaction(emoji))
                else:
                    # Unicode 表情符號
                    asyncio.create_task(message.add_reaction(emoji_str))
                log(f"自動回覆觸發，對訊息添加反應：{emoji_str}", module_name="AutoReply", level=logging.INFO)
            except Exception as e:
                log(f"處理 {{react:{emoji_str}}} 時發生錯誤: {e}", module_name="AutoReply", level=logging.ERROR)
            return ""  # 移除反應標記
        response = react_pattern.sub(react_replacer, response)
        response = response.strip()
        if not response:
            return "", None  # 如果回覆內容在處理後為空，則不回覆
        
        # 貼圖傳送
        # {sticker:sticker_id} 格式
        sticker = None
        sticker_pattern = re.compile(r"\{sticker:(\d+)\}")
        def sticker_replacer(match):
            sticker_id = int(match.group(1))
            try:
                nonlocal sticker
                sticker = discord.utils.get(message.guild.stickers, id=sticker_id)
            except Exception as e:
                log(f"處理 {{sticker:{sticker_id}}} 時發生錯誤: {e}", module_name="AutoReply", level=logging.ERROR)
            return ""  # 移除貼圖標記
        response = sticker_pattern.sub(sticker_replacer, response)
        response = response.strip()
        if not response and not sticker:
            return "", None  # 如果回覆內容在處理後為空，且沒有貼圖，則不回覆
        elif not response and sticker:
            return "", sticker  # 如果回覆內容在處理後為空，但有貼圖，則只傳送貼圖

        # 取得基本資訊
        
        guild = message.guild
        author = message.author
        channel = message.channel

        # 基本變數替換
        replacements = {
            "{user}": author.mention,
            "{content}": message.content,
            "{guild}": guild.name,
            "{server}": guild.name,
            "{channel}": channel.name,
            "{author}": author.name,
            "{member}": author.name,
            "{role}": author.top_role.name,
            "{id}": str(author.id),
            "\\n": "\n",
            "\\t": "\t"
        }
        
        for key, value in replacements.items():
            response = response.replace(key, value)

        # {random}
        if "{random}" in response:
            response = response.replace("{random}", str(random.randint(1, 100)))

        # {randint:min-max}
        # 使用 regex 尋找所有 {randint:min-max} 格式
        # 非貪婪匹配，並捕捉 min 和 max
        randint_pattern = re.compile(r"\{randint:(\d+)-(\d+)\}")
        
        def randint_replacer(match):
            try:
                min_val = int(match.group(1))
                max_val = int(match.group(2))
                if min_val > max_val:
                    min_val, max_val = max_val, min_val
                return str(random.randint(min_val, max_val))
            except (ValueError, IndexError):
                return match.group(0) # 發生錯誤則不替換

        response = randint_pattern.sub(randint_replacer, response)

        # {random_user}
        if "{random_user}" in response:
            try:
                users = set()
                # 限制讀取歷史訊息數量以避免效能問題
                async for msg in channel.history(limit=50):
                     if not msg.author.bot:
                        users.add(msg.author)
                
                if users:
                    selected_user = random.choice(list(users))
                    response = response.replace("{random_user}", selected_user.display_name)
                else:
                    response = response.replace("{random_user}", "沒有人")
            except Exception as e:
                log(f"處理 {{random_user}} 時發生錯誤: {e}", module_name="AutoReply", level=logging.ERROR)
                response = response.replace("{random_user}", "未知使用者")

        return response, sticker

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        
        # check permissions
        if not message.channel.permissions_for(message.guild.me).send_messages:
            return
        
        ignore_mode = get_server_config(message.guild.id, "autoreply_ignore_mode", "blacklist")
        ignore_channels = get_server_config(message.guild.id, "autoreply_ignore_channels", [])
        if ignore_mode == "blacklist" and message.channel.id in ignore_channels:
            return
        elif ignore_mode == "whitelist" and message.channel.id not in ignore_channels:
            return

        guild_id = message.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        
        # 預先取得 channel_id 避免在迴圈中重複存取
        channel_id = message.channel.id
        content = message.content

        for ar in autoreplies:
            # check channel mode
            channel_mode = ar.get("channel_mode", "all")
            # 確保 ar['channels'] 存在，避免 KeyError
            channels = ar.get("channels", [])
            
            if channel_mode == "whitelist" and channel_id not in channels:
                continue
            elif channel_mode == "blacklist" and channel_id in channels:
                continue

            match_found = False
            mode = ar.get("mode")
            triggers = ar.get("trigger", [])
            
            # 優化：根據模式選擇匹配邏輯
            if mode == "regex":
                for trigger in triggers:
                    try:
                        if re.search(trigger, content):
                            match_found = True
                            break
                    except re.error:
                        continue
            else:
                 # 對於字串比對，可以使用 any 提早結束
                if mode == "contains":
                    match_found = any(trigger in content for trigger in triggers)
                elif mode == "equals":
                    match_found = any(trigger == content for trigger in triggers)
                elif mode == "starts_with":
                    match_found = any(content.startswith(trigger) for trigger in triggers)
                elif mode == "ends_with":
                    match_found = any(content.endswith(trigger) for trigger in triggers)
            
            if match_found:
                if not percent_random(ar.get("random_chance", 100)):
                    # 雖然匹配但隨機機率未中，繼續檢查下一個設定嗎？
                    # 原始邏輯是 return，表示同一個訊息只會有一次自動回覆機會(或該次判定結束)
                    # 依照原始邏輯保留 return
                    return

                responses = ar.get("response", [])
                if not responses:
                    return

                raw_response = random.choice(responses)
                
                # 使用新的處理方法
                final_response, sticker, embed, delayed_actions = await self._process_response_v2(raw_response, message)

                has_immediate_output = bool(final_response or sticker or embed is not None)
                has_followups = bool(delayed_actions["followups"])
                if not has_immediate_output and not has_followups:
                    return
                
                try:
                    sent_message = None
                    if has_immediate_output:
                        if self._is_rate_limited(message):
                            return
                        sent_message = await self._send_autoreply_message(
                            message,
                            ar.get("reply", False),
                            final_response,
                            embed,
                            sticker,
                        )

                    if sent_message and delayed_actions["initial_edits"]:
                        asyncio.create_task(self._execute_autoreply_edits(sent_message, message, delayed_actions["initial_edits"]))

                    for followup_stage in delayed_actions["followups"]:
                        asyncio.create_task(self._execute_autoreply_followup_stage(message, ar.get("reply", False), followup_stage))
                    
                    # 記錄日誌
                    # 避免 trigger 太長
                    trigger_used = triggers[0] if triggers else "unknown"
                    if final_response:
                        response_preview = final_response
                    elif embed and embed.title:
                        response_preview = embed.title
                    elif has_followups:
                        response_preview = "[delayed]"
                    else:
                        response_preview = "[embed]"
                    log(f"自動回覆觸發：`{trigger_used[:10]}...` 回覆內容：`{response_preview[:10]}...`。", 
                        module_name="AutoReply", level=logging.INFO, user=message.author, guild=message.guild)
                except discord.HTTPException as e:
                    log(f"自動回覆發送失敗: {e}", module_name="AutoReply", level=logging.ERROR)
                
                return


asyncio.run(bot.add_cog(AutoReply(bot)))

if __name__ == "__main__":
    start_bot()
