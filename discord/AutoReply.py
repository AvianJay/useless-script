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
import sys
from datetime import datetime
from embed_template import (
    build_embed_from_tokens as build_shared_embed_from_tokens,
    extract_embed_tokens as extract_shared_embed_tokens,
    parse_embed_color,
)

import i18n
from i18n import t, t_enum

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

# i18n: skip-start
# 內建範本包的 trigger/response 內容不翻譯：
# - trigger 是要比對「中文」使用者訊息的字面詞（"早安"、"簽到"…），換成英文字面值
#   等於改變功能，不是翻譯；一個英文伺服器需要的是完全不同的觸發詞與笑話文案。
# - response 混雜著本模組自己的樣板 DSL（{if:...}、{embedtitle:...}…，與
#   embed_template.py 的 EMBED_DIRECTIVES 同一套語法，受 dsl-frozen 保護）與需要
#   道地雙關/哏才有笑點的中文文案，機械翻譯只會產生語意通但不好笑的英文。
# - 使用者安裝範本包後，內容會原樣複製進該 guild 的 server_configs，屬於
#   「guild 自選內容」的既有排除規則（見遷移計畫排除清單第 7 項），guild 已存的
#   資料一律不翻譯。
# display_name / description 是選擇範本包時看到的 UI chrome，會在下方另外
# 用 t_enum("autoreply.pack", f"{key}.display_name"/".description") 覆蓋。
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
                    "{if:{hour}>=5:{if:{hour}<=11:早安 {user}，地球 Online 台服剛重啟，今天也要努力當 NPC。:現在都 {time24} 了，這句早安我幫你存檔到明天早上。}:凌晨 {time24} 說早安，你的肝還在載入中嗎？}",
                    "{if:{hour}>=5:{if:{hour}<=11:早安！願你今天的腦袋比 Discord 載入還快。:這個時間點的早安，時區是 UTC-25 嗎？}:現在 {time24}，這不是早安，是投胎前安。}",
                    "{if:{hour}>=5:{if:{hour}<=11:早安 {user}，大腦還在 Booting，請稍後再開始做人。:現在 {time24}，太陽都到头顶了還在早，時差自由。}:凌晨 {time24} 說早安，我幫你報名地球 Online 夜貓子成就。}",
                    "{if:{hour}>=5:{if:{hour}<=11:早安！今天也要像機器人一樣穩定運作。:已經 {time} 了，這早安送得比我的人生規劃還遲。}:現在 {time24}，建議直接快轉到明天的早安。}",
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
                    "{if:{hour}>=12:{if:{hour}<=17:午安 {user}，午餐吃了嗎？還是跟睡意一起吃掉了？:現在都 {time24} 了，這個午安發得比我的人生還亂。}:現在才 {time24}，太陽表示收到了但未讀。}",
                    "{if:{hour}>=12:{if:{hour}<=17:午安！下午的能量條還撐得住嗎？:這個時間講午安，時間線裂開了。}:太早啦，現在還不到午安時段，先等等系統更新。}",
                    "{if:{hour}>=12:{if:{hour}<=17:午安！願你下午的進度比訊息通知還少。:現在是 {time}，這句午安屬於時空旅人專屬。}:還沒中午就在午安，太陽還沒點頭。}",
                    "{if:{hour}>=12:{if:{hour}<=17:午安 {user}，吃完飯記得讓肝臟休息，雖然它從來沒有。:現在 {time24}，午安已過期，請重新發送晚安。}:現在 {time24}，建議先存檔到中午再讀取。}",
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
                    "{if:{hour}>=18:晚安 {user}，今天辛苦了，該休息就休息。:{if:{hour}<=5:雖然已經凌晨了，但還是跟你說聲晚安吧。:現在才 {time24}，這麼早就在晚安嗎？}}",
                    "{if:{hour}>=18:晚安！希望你今晚的夢裡沒有 bug。:{if:{hour}<=5:晚安夜貓子！:還沒到晚上耶，現在是 {time24}。}}",
                    "{if:{hour}>=18:晚安安，記得把今天的煩惱留給昨天。:{if:{hour}<=5:你的睡眠時間是有點晚了。:白天就晚安，這睡意來得有點快。}}",
                    "{if:{hour}>=18:晚安 {user}，願你的睡眠品質比這伺服器的延遲還穩定。:{if:{hour}<=5:凌晨 {time24} 說晚安，這是熬夜安還是投胎安？:現在才 {time24}，這麼早晚安，作息是歐服嗎？}}",
                    "{if:{hour}>=18:晚安！希望你今晚的夢裡沒有未讀訊息。:{if:{hour}<=5:晚安夜貓子！你的太陽從來沒升起過。:還沒到晚上耶，現在是 {time24}，建議改說午安。}}",
                    "{if:{hour}>=18:晚安安，記得把今天的煩惱留給明天，反正明天也不會解決。:{if:{hour}<=5:你的睡眠時間是有點晚了，但沒關係，明天你也會這樣說。:白天就晚安，這睡意來得有點像系統當機。}}",
                    "{if:{hour}>=18:晚安 {user}，大腦.exe 即將關閉，明天見。:{if:{hour}<=5:現在 {time24}，建議直接說早安比較省時間。:現在 {time24}，這個晚安發得比我的智商還早。}}",
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
                    "{if:{hour}<=11:安安，這邊自動幫你翻譯成「還沒睡夠」。:else:{if:{hour}<=17:安安，午安版本已送達，附贈一杯虛擬咖啡。:else:{if:{hour}>=22:安安，夜深了，你的腦袋還在線嗎？:安安，晚餐時間過得還順利嗎？沒有的話也沒關係。}}}",
                    "{if:{hour}<=11:安安！系統偵測到野生 {user} 正在載入中。:else:{if:{hour}<=17:安安！下午場繼續加油，離下班還有一段漫長的距離。:else:{if:{hour}>=22:安安，睡眠模式建議啟動，但你應該不會聽。:安安，今晚也辛苦了，雖然什麼都沒做。}}}",
                    "{if:{hour}<=11:安安，早晨模式啟動…失敗，大腦尚未連線。:else:{if:{hour}<=17:安安，午后模式啟動…成功，但能量只剩 3%。:else:{if:{hour}>=22:安安，睡前模式啟動…等等你根本不想睡吧。:安安，晚上模式啟動，建議開啟省電模式做人。}}}",
                    "{if:{hour}<=11:安安 {user}，這句安安我收下了，順便幫你預約明天的睏。:else:{if:{hour}<=17:安安，現在是下午的戰場，撐住。:else:{if:{hour}>=22:安安，凌晨三點的你是不是又在思考人生了？:安安，今天的你已經盡力了，雖然看起來沒有。}}}",
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
        "description": "幾個常用小指令，像是 !say、!time、!date、!roll、!weather。",
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
            {
                "trigger": ["!weather"],
                "response": ["用法：!weather 城市名稱"],
                "mode": "equals",
                "reply": False,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
            {
                "trigger": ["!weather "],
                "response": [
                    "{embedtitle:天氣預報}{embedurl:https://discord.com/vanityurl/dotcom/steakpants/flour/flower/index11.html}{embeddescription:現在時間 : {date} {time24}}{embedauthor:Yee}{embedauthorurl:{null}}{embedfield:小提示 : !weather 城市名稱}{embedauthorimage:https://cdn.discordapp.com/guilds/1417448842223161446/users/1048398804359061585/avatars/7f50aa14697c57c7b4fedbc20580247d.webp?size=1024}{embedcolor:39C5BB}{embedimage:https://wttr.in/{contentsplit:1-}.png?m&lang=zh-tw}{embedfooter:Credit to always_tried.exe}{embedfooterimage:{authoravatar}}{embedtime:true}"
                ],
                "mode": "starts_with",
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
    "welcome": {
        "display_name": "歡迎語包",
        "description": "新成員加入歡迎語，適合放在歡迎頻道或一般聊天頻道。",
        "rules": [
            {
                "trigger": ["type:join"],
                "response": [
                    "{embedtitle:歡迎新成員！}{embeddescription:歡迎 {user} 加入 {guild}！}{embedcolor:5865F2}{embedfooter:{guildmembers}}{embedtime:true}"
                ],
                "mode": "equals",
                "reply": True,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
        ],
    },
    "booster": {
        "display_name": "加成者回覆包",
        "description": "專為加成者設計的回覆規則，適合放在公告頻道或一般聊天頻道。",
        "rules": [
            {
                "trigger": ["type:boost"],
                "response": [
                    "{user}{embedtitle:感謝加成！}{embeddescription:{user} 剛剛加成了伺服器！}{embedcolor:F04747}{embedfooter:享受專屬特權吧！}{embedtime:true}"
                ],
                "mode": "equals",
                "reply": True,
                "channel_mode": "all",
                "channels": [],
                "random_chance": 100,
            },
        ],
    }
}
# i18n: skip-end


def autoreply_pack_display_name(pack_key: str, *, locale: str | None = None) -> str:
    """範本包的顯示名稱；語言檔缺這個 key 才會退回內嵌原文（不應該發生，
    每個 AUTOREPLY_TEMPLATE_PACKS 的 key 都在語言檔裡有對應條目）。"""
    return t_enum("autoreply.pack", f"{pack_key}.display_name", locale=locale,
                  default=AUTOREPLY_TEMPLATE_PACKS.get(pack_key, {}).get("display_name", pack_key))


def autoreply_pack_description(pack_key: str, *, locale: str | None = None) -> str:
    return t_enum("autoreply.pack", f"{pack_key}.description", locale=locale,
                  default=AUTOREPLY_TEMPLATE_PACKS.get(pack_key, {}).get("description", ""))


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
    for pack_key in AUTOREPLY_TEMPLATE_PACKS:
        pack_display_name = autoreply_pack_display_name(pack_key)
        pack_description = autoreply_pack_description(pack_key)
        searchable_text = " ".join([pack_key, pack_display_name, pack_description]).lower()
        if lowered_current and lowered_current not in searchable_text:
            continue
        display_name = f"{pack_display_name} ({pack_key})"
        display_name = display_name if len(display_name) <= 100 else display_name[:97] + "..."
        choices.append(app_commands.Choice(name=display_name, value=pack_key))
    return choices[:25]


def parse_channel_mention(mention: str) -> str:
    match = re.match(r"<#(\d+)>", mention)
    if match:
        return match.group(1)
    return mention


# mode/channel_mode 的 key 本身是存進 server_configs 的機器值，永不翻譯；
# label/description 一律透過 t_enum() 動態查表（見下方函式）。
AUTOREPLY_MODES = ("contains", "equals", "starts_with", "ends_with", "regex")
AUTOREPLY_CHANNEL_MODES = ("all", "whitelist", "blacklist")


def autoreply_mode_label(mode: str, *, locale: str | None = None) -> str:
    if mode not in AUTOREPLY_MODES:
        return mode
    return t_enum("autoreply.mode", f"{mode}.label", locale=locale)


def autoreply_mode_description(mode: str, *, locale: str | None = None) -> str:
    return t_enum("autoreply.mode", f"{mode}.desc", locale=locale)


def autoreply_channel_mode_label(mode: str, *, locale: str | None = None) -> str:
    if mode not in AUTOREPLY_CHANNEL_MODES:
        return mode
    return t_enum("autoreply.channel_mode", f"{mode}.label", locale=locale)


def autoreply_channel_mode_description(mode: str, *, locale: str | None = None) -> str:
    return t_enum("autoreply.channel_mode", f"{mode}.desc", locale=locale)

AUTOREPLY_MESSAGE_TYPE_TRIGGER_ALIASES = {
    "boost": "premium_guild_subscription",
    "booster": "premium_guild_subscription",
    "join": "new_member",
}


def build_autoreply_message_type_lookup() -> dict[str, discord.MessageType]:
    lookup = {
        name.casefold(): message_type
        for name, message_type in discord.MessageType.__members__.items()
    }

    for alias, target_name in AUTOREPLY_MESSAGE_TYPE_TRIGGER_ALIASES.items():
        target_message_type = discord.MessageType.__members__.get(target_name)
        if target_message_type is not None:
            lookup[alias.casefold()] = target_message_type

    return lookup


AUTOREPLY_MESSAGE_TYPE_TRIGGER_LOOKUP = build_autoreply_message_type_lookup()


class AutoReplyBuilderContentModal(i18n.I18nModal, title=i18n.K("autoreply.modal.builder_title")):
    def __init__(self, builder_view: "AutoReplyBuilderView"):
        super().__init__()
        self.builder_view = builder_view

        self.trigger_input = discord.ui.TextInput(
            label=t("autoreply.field.trigger"),
            placeholder=t("autoreply.modal.trigger_ph"),
            required=True,
            max_length=1000,
            style=discord.TextStyle.paragraph,
            default=builder_view.state["trigger_text"],
        )
        self.response_input = discord.ui.TextInput(
            label=t("autoreply.field.response"),
            placeholder=t("autoreply.modal.response_ph"),
            required=True,
            max_length=2000,
            style=discord.TextStyle.paragraph,
            default=builder_view.state["response_text"],
        )
        self.random_chance_input = discord.ui.TextInput(
            label=t("autoreply.modal.random_chance_label"),
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
            await interaction.response.send_message(t("autoreply.err.random_chance_range"), ephemeral=True)
            return

        if random_chance < 1 or random_chance > 100:
            await interaction.response.send_message(t("autoreply.err.random_chance_range"), ephemeral=True)
            return

        self.builder_view.state["trigger_text"] = self.trigger_input.value.strip()
        self.builder_view.state["response_text"] = self.response_input.value.strip()
        self.builder_view.state["random_chance"] = random_chance

        await interaction.response.defer(ephemeral=True)
        await self.builder_view.refresh_message()
        await interaction.followup.send(t("autoreply.msg.builder_content_updated"), ephemeral=True)


class AutoReplyBuilderModeSelect(discord.ui.Select):
    def __init__(self, builder_view: "AutoReplyBuilderView"):
        self.builder_view = builder_view
        options = [
            discord.SelectOption(
                label=autoreply_mode_label(value),
                value=value,
                description=autoreply_mode_description(value),
                default=builder_view.state["mode"] == value,
            )
            for value in AUTOREPLY_MODES
        ]
        super().__init__(
            placeholder=t("autoreply.select.mode_ph"),
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
                label=autoreply_channel_mode_label(value),
                value=value,
                description=autoreply_channel_mode_description(value),
                default=builder_view.state["channel_mode"] == value,
            )
            for value in AUTOREPLY_CHANNEL_MODES
        ]
        super().__init__(
            placeholder=t("autoreply.select.channel_mode_ph"),
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
            placeholder=t("autoreply.select.channel_limit_ph"),
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
        await interaction.response.send_message(t("autoreply.err.not_builder_owner"), ephemeral=True)
        return False

    def _rebuild_components(self):
        self.clear_items()
        self.add_item(AutoReplyBuilderModeSelect(self))
        self.add_item(AutoReplyBuilderChannelModeSelect(self))
        self.add_item(AutoReplyBuilderChannelSelect(self))

        edit_button = discord.ui.Button(label=t("autoreply.btn.edit_trigger_response"), style=discord.ButtonStyle.primary, row=3)
        edit_button.callback = self.open_content_modal
        self.add_item(edit_button)

        reply_button = discord.ui.Button(
            label=t("autoreply.btn.reply_original", state=t("autoreply.state.on") if self.state["reply"] else t("autoreply.state.off")),
            style=discord.ButtonStyle.success if self.state["reply"] else discord.ButtonStyle.secondary,
            row=3,
        )
        reply_button.callback = self.toggle_reply
        self.add_item(reply_button)

        clear_channels_button = discord.ui.Button(label=t("autoreply.btn.clear_channels"), style=discord.ButtonStyle.secondary, row=3)
        clear_channels_button.callback = self.clear_channels
        self.add_item(clear_channels_button)

        save_button = discord.ui.Button(label=t("autoreply.btn.save_rule"), style=discord.ButtonStyle.success, row=4)
        save_button.callback = self.save_rule
        self.add_item(save_button)

        cancel_button = discord.ui.Button(label=t("common.btn.cancel"), style=discord.ButtonStyle.danger, row=4)
        cancel_button.callback = self.cancel_builder
        self.add_item(cancel_button)

    def build_embed(self, *, title: str | None = None, description: str | None = None, color: int = 0x5865F2):
        title = title or t("autoreply.modal.builder_title")
        trigger_preview = self.cog._preview_builder_items(self.state["trigger_text"])
        response_preview = self.cog._preview_builder_items(self.state["response_text"])
        channel_mentions = [
            f"<#{channel_id}>"
            for channel_id in self.state["channels"]
            if self.guild.get_channel(channel_id) is not None
        ]
        mode_label = autoreply_mode_label(self.state["mode"])
        channel_mode_label = autoreply_channel_mode_label(self.state["channel_mode"])
        channel_text = ", ".join(channel_mentions) if channel_mentions else t("autoreply.state.empty_list")

        embed = discord.Embed(
            title=title,
            description=description or t("autoreply.builder.instructions"),
            color=color,
        )
        embed.add_field(name=t("autoreply.field.trigger"), value=trigger_preview, inline=False)
        embed.add_field(name=t("autoreply.field.response"), value=response_preview, inline=False)
        embed.add_field(name=t("autoreply.field.mode"), value=f"{mode_label} (`{self.state['mode']}`)", inline=True)
        embed.add_field(name=t("autoreply.field.reply_original"), value=t("autoreply.state.on") if self.state["reply"] else t("autoreply.state.off"), inline=True)
        embed.add_field(name=t("autoreply.field.chance"), value=f"{self.state['random_chance']}%", inline=True)
        embed.add_field(name=t("autoreply.field.channel_mode"), value=f"{channel_mode_label} (`{self.state['channel_mode']}`)", inline=True)
        embed.add_field(name=t("autoreply.field.specified_channels"), value=channel_text, inline=True)
        embed.add_field(name=t("autoreply.field.current_count"), value=f"{len(get_server_config(self.guild.id, 'autoreplies', []))} / {self.cog._get_autoreply_limit(self.guild.id)}", inline=True)
        embed.add_field(
            name=t("autoreply.field.tip"),
            value=t("autoreply.builder.tip_body"),
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
            title=t("autoreply.msg.rule_saved_title"),
            rule=rule,
            guild=self.guild,
            description=t("autoreply.builder.rule_added_desc", total=total_count, limit=limit),
        )
        if self.message is None:
            self.message = interaction.message
        if self.message is not None:
            await self.message.edit(embed=success_embed, view=self)

        trigger_text = ", ".join(rule["trigger"])
        log(
            f"AutoReply rule added via builder: `{trigger_text[:10]}{'...' if len(trigger_text) > 10 else ''}`.",
            module_name="AutoReply",
            level=logging.INFO,
            user=interaction.user,
            guild=interaction.guild,
        )
        await interaction.followup.send(t("autoreply.builder.rule_added_confirm"), ephemeral=True)

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
                    title=t("autoreply.builder.cancelled_title"),
                    description=t("autoreply.builder.cancelled_desc"),
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
                        title=t("autoreply.builder.timed_out_title"),
                        description=t("autoreply.builder.timed_out_desc"),
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
class AutoReply(commands.GroupCog, name=app_commands.locale_str("autoreply", i18n_key="cmd.autoreply.autoreply.root.name"), description=app_commands.locale_str("Auto-reply configuration commands", i18n_key="cmd.autoreply.autoreply.root.desc")):
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

    def _preview_builder_items(self, raw_value: str, *, empty_text: str | None = None) -> str:
        empty_text = empty_text if empty_text is not None else t("autoreply.state.not_set")
        items = self._split_autoreply_items(raw_value)
        if not items:
            return empty_text

        preview_lines = []
        for item in items[:5]:
            shortened = item if len(item) <= 250 else item[:247] + "..."
            preview_lines.append(f"• {shortened}")

        if len(items) > 5:
            preview_lines.append(t("autoreply.builder.more_items", count=len(items) - 5))

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
        if mode not in AUTOREPLY_MODES:
            raise ValueError(t("autoreply.err.unknown_mode"))
        if channel_mode not in AUTOREPLY_CHANNEL_MODES:
            raise ValueError(t("autoreply.err.unknown_channel_mode"))

        try:
            random_chance = int(random_chance)
        except (TypeError, ValueError):
            raise ValueError(t("autoreply.err.random_chance_range"))

        if random_chance < 1 or random_chance > 100:
            raise ValueError(t("autoreply.err.random_chance_range"))

        trigger = self._split_autoreply_items(trigger_input)
        response = self._split_autoreply_items(response_input)

        if not trigger:
            raise ValueError(t("autoreply.err.need_trigger"))
        self._validate_message_type_triggers(trigger)
        if not response:
            raise ValueError(t("autoreply.err.need_response"))

        for template in response:
            try:
                self._validate_template_syntax(template)
            except TemplateSyntaxError as e:
                raise ValueError(t("autoreply.err.template_syntax", error=e)) from e

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

    def _parse_message_type_trigger(self, trigger: str) -> tuple[bool, discord.MessageType | None, str | None]:
        raw_trigger = str(trigger).strip()
        prefix, separator, payload = raw_trigger.partition(":")
        if separator != ":" or prefix.casefold() != "type":
            return False, None, None

        normalized_payload = re.sub(r"[\s\-]+", "_", payload.strip()).casefold()
        if not normalized_payload:
            return True, None, ""

        return True, AUTOREPLY_MESSAGE_TYPE_TRIGGER_LOOKUP.get(normalized_payload), normalized_payload

    def _format_invalid_message_type_trigger_message(self, triggers: list[str]) -> str:
        preview = ", ".join(f"`{trigger}`" for trigger in triggers[:5])
        if len(triggers) > 5:
            preview += f" ... (+{len(triggers) - 5})"

        return t("autoreply.err.unknown_type_trigger", preview=preview)

    def _validate_message_type_triggers(self, triggers: list[str]):
        invalid_triggers = []

        for trigger in triggers or []:
            is_type_trigger, message_type, _ = self._parse_message_type_trigger(trigger)
            if is_type_trigger and message_type is None:
                invalid_triggers.append(str(trigger).strip())

        if invalid_triggers:
            raise ValueError(self._format_invalid_message_type_trigger_message(invalid_triggers))

    def _normalize_autoreply_trigger(self, trigger: str) -> str:
        is_type_trigger, message_type, normalized_payload = self._parse_message_type_trigger(trigger)
        if is_type_trigger:
            if message_type is not None:
                return f"type:{message_type.name.casefold()}"
            return f"type:{normalized_payload or ''}"

        return re.sub(r"\s+", " ", str(trigger).strip()).casefold()

    def _message_matches_autoreply_triggers(self, message: discord.Message, mode: str, triggers: list[str]) -> bool:
        text_triggers = []

        for trigger in triggers or []:
            is_type_trigger, message_type, _ = self._parse_message_type_trigger(trigger)
            if is_type_trigger:
                if message_type is not None and message.type == message_type:
                    return True
                continue

            text_triggers.append(str(trigger))

        if not text_triggers:
            return False

        content = message.content
        if mode == "regex":
            for trigger in text_triggers:
                try:
                    if re.search(trigger, content):
                        return True
                except re.error:
                    continue
            return False

        if mode == "contains":
            return any(trigger in content for trigger in text_triggers)
        if mode == "equals":
            return any(trigger == content for trigger in text_triggers)
        if mode == "starts_with":
            return any(content.startswith(trigger) for trigger in text_triggers)
        if mode == "ends_with":
            return any(content.endswith(trigger) for trigger in text_triggers)

        return False

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
            return t("autoreply.err.trigger_conflict_existing", preview=preview)
        return t("autoreply.err.trigger_conflict_new", preview=preview)

    def _save_new_autoreply_rule(self, guild_id: int, rule: dict) -> tuple[int, int]:
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        autoreply_limit = self._get_autoreply_limit(guild_id)
        if len(autoreplies) >= autoreply_limit:
            raise ValueError(t("autoreply.err.limit_reached", count=autoreply_limit))

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
            channel_text = t("common.state.none")

        mode_label = autoreply_mode_label(rule["mode"])
        channel_mode_label = autoreply_channel_mode_label(rule["channel_mode"])

        embed = discord.Embed(title=title, description=description, color=color)
        embed.add_field(name=t("autoreply.field.mode"), value=f"{mode_label} (`{rule['mode']}`)")
        embed.add_field(name=t("autoreply.field.trigger"), value=f"`{trigger_preview}`", inline=False)
        embed.add_field(name=t("autoreply.field.response"), value=f"`{response_preview}`", inline=False)
        embed.add_field(name="Reply", value=t("common.state.yes") if rule["reply"] else t("common.state.no"))
        embed.add_field(name=t("autoreply.field.channel_mode"), value=f"{channel_mode_label} (`{rule['channel_mode']}`)")
        embed.add_field(name=t("autoreply.field.specified_channels"), value=channel_text, inline=False)
        embed.add_field(name=t("autoreply.field.chance"), value=f"{rule['random_chance']}%")
        return embed

    def _parse_embed_color(self, value: str):
        return parse_embed_color(value)

    def _parse_bool(self, value: str) -> bool:
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _build_allowed_mentions(self, allow_everyone_and_roles: bool = False) -> discord.AllowedMentions:
        return discord.AllowedMentions(
            users=True,
            roles=allow_everyone_and_roles,
            everyone=allow_everyone_and_roles,
            replied_user=True,
        )

    def _extract_mention_directive(self, response: str) -> tuple[str, discord.AllowedMentions]:
        allow_everyone_and_roles = False

        def mention_replacer(match):
            nonlocal allow_everyone_and_roles
            allow_everyone_and_roles = match.group(1).strip().lower() == "true"
            return ""

        cleaned_response = re.sub(r"\{mention:(true|false)\}", mention_replacer, response, flags=re.IGNORECASE)
        return cleaned_response, self._build_allowed_mentions(allow_everyone_and_roles)

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
            elif lowered.startswith("mention:") and not re.fullmatch(r"mention:(true|false)", token, re.IGNORECASE):
                raise TemplateSyntaxError("Invalid mention syntax")
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
        return extract_shared_embed_tokens(response)

    async def _resolve_response_variables(self, response: str, message: discord.Message, context: dict) -> str:
        if not response:
            return response

        response = await self._resolve_if_expressions(response, message, context)

        guild = message.guild
        author = message.author
        channel = message.channel
        now = context["now"]
        am_pm = t("autoreply.render.am") if now.hour < 12 else t("autoreply.render.pm")
        hour_12 = now.hour % 12 or 12
        content_parts = message.content.split()
        role_name = getattr(getattr(author, "top_role", None), "name", "")
        channel_name = getattr(channel, "name", "")

        replacements = {
            "{user}": author.mention,
            "{content}": message.content,
            "{guild}": guild.name,
            "{server}": guild.name,
            "{guildid}": str(guild.id),
            "{guildicon}": guild.icon.url if guild.icon else "",
            "{guildowner}": guild.owner.name if guild.owner else "",
            "{guildownerid}": str(guild.owner.id) if guild.owner else "",
            "{guildmembers}": str(guild.member_count),
            "{guildroles}": str(len(guild.roles)),
            "{guildbanner}": guild.banner.url if guild.banner else "",
            "{guildboosts}": str(guild.premium_subscription_count) if guild.premium_subscription_count is not None else "0",
            "{channel}": channel_name,
            "{author}": author.name,
            "{member}": author.name,
            "{authorid}": str(author.id),
            "{authoravatar}": author.display_avatar.url if author.display_avatar else "",
            "{authorbanner}": author.banner.url if getattr(author, "banner", None) else "",
            "{authorcreated}": author.created_at.strftime("%Y/%m/%d %H:%M:%S"),
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
                        context["random_user"] = t("autoreply.render.no_user_found")
                except Exception as e:
                    log(f"Error handling {{random_user}}: {e}", module_name="AutoReply", level=logging.ERROR)
                    context["random_user"] = t("autoreply.render.user_unavailable")
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
        async def resolver(value: str) -> str:
            return await self._resolve_response_variables(value, message, context)

        return await build_shared_embed_from_tokens(
            extracted,
            resolver,
            now=context["now"],
        )

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
        response, allowed_mentions = self._extract_mention_directive(response)

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
                log(f"AutoReply triggered, added reaction to message: {emoji_str}", module_name="AutoReply", level=logging.INFO)
            except Exception as e:
                log(f"Error handling {{react:{emoji_str}}}: {e}", module_name="AutoReply", level=logging.ERROR)
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
                log(f"Error handling {{sticker:{sticker_id}}}: {e}", module_name="AutoReply", level=logging.ERROR)
            return ""

        response = sticker_pattern.sub(sticker_replacer, response)
        response, extracted_embed = self._extract_embed_tokens(response)
        embed = await self._build_embed_from_tokens(extracted_embed, message, context)

        if not response and not sticker and embed is None:
            return "", None, None, allowed_mentions

        return response, sticker, embed, allowed_mentions

    async def _send_autoreply_message(self, trigger_message: discord.Message, reply_mode: bool, content: str, embed: discord.Embed | None, sticker, allowed_mentions: discord.AllowedMentions | None = None):
        send_content = content or None
        send_allowed_mentions = allowed_mentions or self._build_allowed_mentions()
        if reply_mode:
            return await trigger_message.reply(
                send_content,
                embed=embed,
                stickers=[sticker] if sticker else [],
                allowed_mentions=send_allowed_mentions,
            )
        return await trigger_message.channel.send(
            send_content,
            embed=embed,
            stickers=[sticker] if sticker else [],
            allowed_mentions=send_allowed_mentions,
        )

    async def _execute_autoreply_edits(self, sent_message: discord.Message, trigger_message: discord.Message, edit_actions: list[dict]):
        for edit_action in edit_actions:
            try:
                await asyncio.sleep(edit_action["delay"])
                edit_content, _, edit_embed, edit_allowed_mentions = await self._render_response_segment(edit_action["template"], trigger_message)
                if not edit_content and edit_embed is None:
                    continue
                await sent_message.edit(
                    content=edit_content or None,
                    embed=edit_embed,
                    allowed_mentions=edit_allowed_mentions,
                )
            except discord.HTTPException as e:
                log(f"AutoReply edit failed: {e}", module_name="AutoReply", level=logging.ERROR)
                return
            except Exception as e:
                log(f"AutoReply edit error: {e}", module_name="AutoReply", level=logging.ERROR)
                return

    async def _execute_autoreply_followup_stage(self, trigger_message: discord.Message, reply_mode: bool, stage: dict):
        try:
            await asyncio.sleep(stage["send_delay"])

            if self._is_rate_limited(trigger_message):
                return

            followup_content, followup_sticker, followup_embed, followup_allowed_mentions = await self._render_response_segment(stage["template"], trigger_message)
            if not followup_content and not followup_sticker and followup_embed is None:
                return

            sent_message = await self._send_autoreply_message(
                trigger_message,
                reply_mode,
                followup_content,
                followup_embed,
                followup_sticker,
                followup_allowed_mentions,
            )

            if stage["edits"]:
                asyncio.create_task(self._execute_autoreply_edits(sent_message, trigger_message, stage["edits"]))
        except discord.HTTPException as e:
            log(f"AutoReply delayed message failed to send: {e}", module_name="AutoReply", level=logging.ERROR)
        except Exception as e:
            log(f"AutoReply delayed message error: {e}", module_name="AutoReply", level=logging.ERROR)

    async def _process_response_v2(self, response: str, message: discord.Message) -> tuple:
        """Process autoreply response text and return the immediate result plus delayed actions."""

        try:
            self._validate_template_syntax(response)
        except TemplateSyntaxError as e:
            log(f"AutoReply template syntax error: {e}", module_name="AutoReply", level=logging.WARNING)
            return "", None, None, self._build_allowed_mentions(), {"initial_edits": [], "followups": []}

        planning_context = self._build_template_context()
        resolved_response = await self._resolve_if_expressions(response, message, planning_context)

        try:
            response_stages = self._extract_timed_response_plan(resolved_response)
        except TemplateSyntaxError as e:
            log(f"AutoReply template syntax error: {e}", module_name="AutoReply", level=logging.WARNING)
            return "", None, None, self._build_allowed_mentions(), {"initial_edits": [], "followups": []}

        initial_stage = response_stages[0] if response_stages else {"template": "", "edits": []}
        final_response, sticker, embed, allowed_mentions = await self._render_response_segment(initial_stage["template"], message)
        delayed_actions = {
            "initial_edits": initial_stage["edits"],
            "followups": response_stages[1:],
        }

        return final_response, sticker, embed, allowed_mentions, delayed_actions

    @app_commands.command(name=app_commands.locale_str("add", i18n_key="cmd.autoreply.autoreply.add.name"), description=app_commands.locale_str("Add an auto-reply", i18n_key="cmd.autoreply.autoreply.add.desc"))
    @app_commands.describe(
        mode=app_commands.locale_str("Reply mode", i18n_key="cmd.autoreply.autoreply.add.param.mode"),
        trigger=app_commands.locale_str("Trigger string (separate multiple with ,)", i18n_key="cmd.autoreply.autoreply.add.param.trigger"),
        response=app_commands.locale_str("Reply content (separate multiple with , to pick one at random)", i18n_key="cmd.autoreply.autoreply.add.param.response"),
        reply=app_commands.locale_str("Reply to the original message", i18n_key="cmd.autoreply.autoreply.add.param.reply"),
        channel_mode=app_commands.locale_str("Channel filter mode", i18n_key="cmd.autoreply.autoreply.add.param.channel_mode"),
        channels=app_commands.locale_str("Channel IDs (separate multiple IDs with ,)", i18n_key="cmd.autoreply.autoreply.add.param.channels"),
        random_chance=app_commands.locale_str("Random reply chance (1-100)", i18n_key="cmd.autoreply.autoreply.add.param.random_chance")
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name=app_commands.locale_str("Contains", i18n_key="cmd.autoreply.autoreply.add.choice.contains"), value="contains"),
            app_commands.Choice(name=app_commands.locale_str("Exact match", i18n_key="cmd.autoreply.autoreply.add.choice.equals"), value="equals"),
            app_commands.Choice(name=app_commands.locale_str("Starts with", i18n_key="cmd.autoreply.autoreply.add.choice.starts_with"), value="starts_with"),
            app_commands.Choice(name=app_commands.locale_str("Ends with", i18n_key="cmd.autoreply.autoreply.add.choice.ends_with"), value="ends_with"),
            app_commands.Choice(name=app_commands.locale_str("Regular expression", i18n_key="cmd.autoreply.autoreply.add.choice.regex"), value="regex"),
        ],
        reply=[
            app_commands.Choice(name=app_commands.locale_str("Yes", i18n_key="cmd.autoreply.autoreply.add.choice.true"), value="True"),
            app_commands.Choice(name=app_commands.locale_str("No", i18n_key="cmd.autoreply.autoreply.add.choice.false"), value="False"),
        ],
        channel_mode=[
            app_commands.Choice(name=app_commands.locale_str("All channels", i18n_key="cmd.autoreply.autoreply.add.choice.all"), value="all"),
            app_commands.Choice(name=app_commands.locale_str("Whitelist", i18n_key="cmd.autoreply.autoreply.add.choice.whitelist"), value="whitelist"),
            app_commands.Choice(name=app_commands.locale_str("Blacklist", i18n_key="cmd.autoreply.autoreply.add.choice.blacklist"), value="blacklist"),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def add_autoreply(self, interaction: discord.Interaction, mode: str, trigger: str, response: str, reply: str = "False", channel_mode: str = "all", channels: str = "", random_chance: int = 100):
        guild_id = interaction.guild.id
        reply = (reply == "True")
        if random_chance < 1 or random_chance > 100:
            await interaction.response.send_message(t("autoreply.err.random_chance_between"), ephemeral=True)
            return
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        autoreply_limit = self._get_autoreply_limit(guild_id)
        if len(autoreplies) >= autoreply_limit:
            await interaction.response.send_message(
                t("autoreply.err.config_limit", count=autoreply_limit, support_url=config("support_server_invite")),
                ephemeral=True)
            return
        trigger = trigger.split(",")  # multiple triggers
        trigger = [t.strip() for t in trigger if t.strip()]  # remove empty triggers
        try:
            self._validate_message_type_triggers(trigger)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
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
        embed = discord.Embed(title=t("autoreply.msg.add_success"), color=0x00ff00)
        embed.add_field(name=t("autoreply.field.mode"), value=mode)
        embed.add_field(name=t("autoreply.field.trigger_string"), value=f"`{trigger_str}`")
        embed.add_field(name=t("autoreply.field.response"), value=f"`{response_str}`")
        embed.add_field(name=t("autoreply.field.reply_original"), value=t("common.state.yes") if reply else t("common.state.no"))
        embed.add_field(name=t("autoreply.field.channel_mode"), value=channel_mode)
        embed.add_field(name=t("autoreply.field.specified_channels"), value=f"`{', '.join(map(str, valid_channels)) if valid_channels else t('common.state.none')}`")
        embed.add_field(name=t("autoreply.field.random_chance"), value=f"{random_chance}%")
        await interaction.response.send_message(embed=embed)
        trigger_str = ", ".join(trigger)
        log(f"AutoReply added: `{trigger_str[:10]}{'...' if len(trigger_str) > 10 else ''}`.", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)

    @app_commands.command(name=app_commands.locale_str("remove", i18n_key="cmd.autoreply.autoreply.remove.name"), description=app_commands.locale_str("Remove an auto-reply", i18n_key="cmd.autoreply.autoreply.remove.desc"))
    @app_commands.describe(
        trigger=app_commands.locale_str("Trigger string", i18n_key="cmd.autoreply.autoreply.remove.param.trigger")
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
                await interaction.response.send_message(t("autoreply.msg.removed", trigger=trigger))
                log(f"AutoReply removed: `{trigger[:10]}{'...' if len(trigger) > 10 else ''}`.", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
                return
        await interaction.response.send_message(t("autoreply.err.trigger_not_found", trigger=trigger))
    
    @app_commands.command(name=app_commands.locale_str("list", i18n_key="cmd.autoreply.autoreply.list.name"), description=app_commands.locale_str("List all auto-replies", i18n_key="cmd.autoreply.autoreply.list.desc"))
    @app_commands.default_permissions(manage_guild=True)
    async def list_autoreplies(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        if not autoreplies:
            await interaction.response.send_message(t("autoreply.msg.no_autoreplies"))
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
            description += t("autoreply.list.row",
                             index=i, mode=ar["mode"], triggers=triggers, responses=responses,
                             reply=t("common.state.yes") if ar["reply"] else t("common.state.no"),
                             channel_mode=ar["channel_mode"],
                             channels=", ".join(map(str, ar["channels"])) if ar["channels"] else t("common.state.none"),
                             chance=ar["random_chance"]) + "\n"
        embed = discord.Embed(title=t("autoreply.list.title"), description=description, color=0x00ff00)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name=app_commands.locale_str("clear", i18n_key="cmd.autoreply.autoreply.clear.name"), description=app_commands.locale_str("Clear all auto-replies", i18n_key="cmd.autoreply.autoreply.clear.desc"))
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
                await interaction.edit_original_response(content=t("autoreply.clear.timed_out"), view=self)
                self.stop()

            @discord.ui.button(label=t("autoreply.btn.confirm_clear"), style=discord.ButtonStyle.danger)
            async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != user_id:
                    await interaction.response.send_message(t("autoreply.err.not_clear_initiator"), ephemeral=True)
                    return
                set_server_config(guild_id, "autoreplies", [])
                for child in self.children:
                    child.disabled = True
                await interaction.response.edit_message(content=t("autoreply.clear.done"), view=self)
                log("All AutoReply rules cleared.", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
                self.stop()

            @discord.ui.button(label=t("common.btn.cancel"), style=discord.ButtonStyle.secondary)
            async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
                for child in self.children:
                    child.disabled = True
                await interaction.response.edit_message(content=t("autoreply.clear.cancelled"), view=self)
                self.stop()

        await interaction.response.send_message(t("autoreply.clear.confirm_prompt", count=len(autoreplies)), view=Confirm())

    @app_commands.command(name=app_commands.locale_str("edit", i18n_key="cmd.autoreply.autoreply.edit.name"), description=app_commands.locale_str("Edit an auto-reply", i18n_key="cmd.autoreply.autoreply.edit.desc"))
    @app_commands.describe(
        trigger=app_commands.locale_str("Trigger string", i18n_key="cmd.autoreply.autoreply.edit.param.trigger"),
        new_mode=app_commands.locale_str("New reply mode", i18n_key="cmd.autoreply.autoreply.edit.param.new_mode"),
        new_trigger=app_commands.locale_str("New trigger string", i18n_key="cmd.autoreply.autoreply.edit.param.new_trigger"),
        new_response=app_commands.locale_str("Reply content", i18n_key="cmd.autoreply.autoreply.edit.param.new_response"),
        reply=app_commands.locale_str("Whether to reply to the original message", i18n_key="cmd.autoreply.autoreply.edit.param.reply"),
        channel_mode=app_commands.locale_str("Channel filter mode", i18n_key="cmd.autoreply.autoreply.edit.param.channel_mode"),
        channels=app_commands.locale_str("Channel IDs (separate multiple IDs with ,)", i18n_key="cmd.autoreply.autoreply.edit.param.channels"),
        random_chance=app_commands.locale_str("Random reply chance (1-100)", i18n_key="cmd.autoreply.autoreply.edit.param.random_chance")
    )
    @app_commands.choices(
        new_mode=[
            app_commands.Choice(name=app_commands.locale_str("Contains", i18n_key="cmd.autoreply.autoreply.edit.choice.contains"), value="contains"),
            app_commands.Choice(name=app_commands.locale_str("Exact match", i18n_key="cmd.autoreply.autoreply.edit.choice.equals"), value="equals"),
            app_commands.Choice(name=app_commands.locale_str("Starts with", i18n_key="cmd.autoreply.autoreply.edit.choice.starts_with"), value="starts_with"),
            app_commands.Choice(name=app_commands.locale_str("Ends with", i18n_key="cmd.autoreply.autoreply.edit.choice.ends_with"), value="ends_with"),
            app_commands.Choice(name=app_commands.locale_str("Regular expression", i18n_key="cmd.autoreply.autoreply.edit.choice.regex"), value="regex"),
        ],
        reply=[
            app_commands.Choice(name=app_commands.locale_str("Yes", i18n_key="cmd.autoreply.autoreply.edit.choice.true"), value="True"),
            app_commands.Choice(name=app_commands.locale_str("No", i18n_key="cmd.autoreply.autoreply.edit.choice.false"), value="False"),
        ],
        channel_mode=[
            app_commands.Choice(name=app_commands.locale_str("All channels", i18n_key="cmd.autoreply.autoreply.edit.choice.all"), value="all"),
            app_commands.Choice(name=app_commands.locale_str("Whitelist", i18n_key="cmd.autoreply.autoreply.edit.choice.whitelist"), value="whitelist"),
            app_commands.Choice(name=app_commands.locale_str("Blacklist", i18n_key="cmd.autoreply.autoreply.edit.choice.blacklist"), value="blacklist"),
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
                await interaction.response.send_message(t("autoreply.err.random_chance_between"), ephemeral=True)
                return
        for ar in autoreplies:
            det = ", ".join(ar["trigger"])
            det = det if len(det) <= 100 else det[:97] + "..."
            if det == trigger:
                if new_mode:
                    ar["mode"] = new_mode
                if new_trigger:
                    parsed_triggers = [t.strip() for t in new_trigger.split(",") if t.strip()]
                    try:
                        self._validate_message_type_triggers(parsed_triggers)
                    except ValueError as e:
                        await interaction.response.send_message(str(e), ephemeral=True)
                        return
                    ar["trigger"] = parsed_triggers
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
                embed = discord.Embed(title=t("autoreply.msg.edit_success"), color=0x00ff00)
                embed.add_field(name=t("autoreply.field.mode"), value=ar["mode"])
                embed.add_field(name=t("autoreply.field.trigger_string"), value=f"`{trigger_str}`")
                embed.add_field(name=t("autoreply.field.response"), value=f"`{response_str}`")
                embed.add_field(name=t("autoreply.field.reply_original"), value=t("common.state.yes") if ar["reply"] else t("common.state.no"))
                embed.add_field(name=t("autoreply.field.channel_mode"), value=ar["channel_mode"])
                embed.add_field(name=t("autoreply.field.specified_channels"), value=f"`{', '.join(map(str, ar['channels'])) if ar['channels'] else t('common.state.none')}`")
                embed.add_field(name=t("autoreply.field.random_chance"), value=f"{ar['random_chance']}%")
                await interaction.response.send_message(embed=embed)
                log(f"AutoReply edited: `{det[:10]}{'...' if len(det) > 10 else ''}`.", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
                return
        await interaction.response.send_message(t("autoreply.err.trigger_not_found", trigger=trigger))
    
    @app_commands.command(name=app_commands.locale_str("quickadd", i18n_key="cmd.autoreply.autoreply.quickadd.name"), description=app_commands.locale_str("Quickly add an auto-reply, merging with existing ones", i18n_key="cmd.autoreply.autoreply.quickadd.desc"))
    @app_commands.describe(
        trigger=app_commands.locale_str("Trigger string", i18n_key="cmd.autoreply.autoreply.quickadd.param.trigger"),
        new_trigger=app_commands.locale_str("New trigger string", i18n_key="cmd.autoreply.autoreply.quickadd.param.new_trigger"),
        new_response=app_commands.locale_str("New reply content", i18n_key="cmd.autoreply.autoreply.quickadd.param.new_response")
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
                    try:
                        self._validate_message_type_triggers(new_triggers)
                    except ValueError as e:
                        await interaction.response.send_message(str(e), ephemeral=True)
                        return
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
                embed = discord.Embed(title=t("autoreply.msg.quickadd_success"), color=0x00ff00)
                embed.add_field(name=t("autoreply.field.mode"), value=ar["mode"])
                embed.add_field(name=t("autoreply.field.trigger_string"), value=f"`{trigger_str}`")
                embed.add_field(name=t("autoreply.field.response"), value=f"`{response_str}`")
                embed.add_field(name=t("autoreply.field.reply_original"), value=t("common.state.yes") if ar["reply"] else t("common.state.no"))
                embed.add_field(name=t("autoreply.field.channel_mode"), value=ar["channel_mode"])
                embed.add_field(name=t("autoreply.field.specified_channels"), value=f"`{', '.join(map(str, ar['channels'])) if ar['channels'] else t('common.state.none')}`")
                embed.add_field(name=t("autoreply.field.random_chance"), value=f"{ar['random_chance']}%")
                await interaction.response.send_message(embed=embed)
                log(f"AutoReply quick-added: `{det}`.", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
                return
        await interaction.response.send_message(t("autoreply.err.trigger_not_found", trigger=trigger))

    @app_commands.command(name=app_commands.locale_str("template", i18n_key="cmd.autoreply.autoreply.template.name"), description=app_commands.locale_str("Apply a built-in auto-reply template pack", i18n_key="cmd.autoreply.autoreply.template.desc"))
    @app_commands.describe(pack=app_commands.locale_str("The template pack to apply", i18n_key="cmd.autoreply.autoreply.template.param.pack"), merge=app_commands.locale_str("Whether to merge with existing rules", i18n_key="cmd.autoreply.autoreply.template.param.merge"))
    @app_commands.choices(
        merge=[
            app_commands.Choice(name=app_commands.locale_str("Yes", i18n_key="cmd.autoreply.autoreply.template.choice.true"), value="True"),
            app_commands.Choice(name=app_commands.locale_str("No (overwrite existing rules)", i18n_key="cmd.autoreply.autoreply.template.choice.false"), value="False"),
        ]
    )
    @app_commands.autocomplete(pack=list_template_pack_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def apply_autoreply_template(self, interaction: discord.Interaction, pack: str, merge: str = "True"):
        pack_data = AUTOREPLY_TEMPLATE_PACKS.get(pack)
        if pack_data is None:
            await interaction.response.send_message(t("autoreply.err.pack_not_found"), ephemeral=True)
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
                t("autoreply.err.template_would_exceed_limit", count=autoreply_limit),
                ephemeral=True
            )
            return

        set_server_config(guild_id, "autoreplies", final_autoreplies)

        preview_lines = []
        for index, rule in enumerate(pack_data["rules"][:5], start=1):
            trigger_preview = ", ".join(rule["trigger"])
            trigger_preview = trigger_preview if len(trigger_preview) <= 40 else trigger_preview[:37] + "..."
            preview_lines.append(f"{index}. {rule['mode']} / {trigger_preview}")
        preview_text = "\n".join(preview_lines) if preview_lines else t("common.state.none")

        embed = discord.Embed(
            title=t("autoreply.msg.template_applied"),
            description=autoreply_pack_description(pack),
            color=0x57F287 if added_count else 0xFEE75C,
        )
        embed.add_field(name=t("autoreply.field.template_pack"), value=f"`{autoreply_pack_display_name(pack)}` (`{pack}`)", inline=False)
        embed.add_field(name=t("autoreply.field.apply_mode"), value=t("autoreply.state.merge_existing") if merge_enabled else t("autoreply.state.overwrite_existing"))
        embed.add_field(name=t("autoreply.field.rules_added"), value=str(added_count))
        if merge_enabled:
            embed.add_field(name=t("autoreply.field.duplicates_skipped"), value=str(skipped_duplicates))
        embed.add_field(name=t("autoreply.field.current_total"), value=str(len(final_autoreplies)))
        embed.add_field(name=t("autoreply.field.included_rules"), value=preview_text, inline=False)
        await interaction.response.send_message(embed=embed)
        log(
            f"AutoReply template pack applied: {pack}, mode={'merge' if merge_enabled else 'replace'}, "
            f"added={added_count}, skipped={skipped_duplicates}.",
            module_name="AutoReply",
            level=logging.INFO,
            user=interaction.user,
            guild=interaction.guild
        )
    
    @app_commands.command(name=app_commands.locale_str("export", i18n_key="cmd.autoreply.autoreply.export.name"), description=app_commands.locale_str("Export auto-reply settings as JSON", i18n_key="cmd.autoreply.autoreply.export.desc"))
    @app_commands.default_permissions(administrator=True)
    async def export_autoreplies(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        if not autoreplies:
            await interaction.response.send_message(t("autoreply.export.no_config"))
            return
        json_data = json.dumps(autoreplies, ensure_ascii=False, indent=4)
        file = discord.File(io.StringIO(json_data), filename="autoreplies.json")
        await interaction.response.send_message(t("autoreply.export.file_message"), file=file)
        log("AutoReply config exported.", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
    
    @app_commands.command(name=app_commands.locale_str("import", i18n_key="cmd.autoreply.autoreply.import.name"), description=app_commands.locale_str("Import auto-reply settings from a JSON file", i18n_key="cmd.autoreply.autoreply.import.desc"))
    @app_commands.describe(file=app_commands.locale_str("The JSON file to import", i18n_key="cmd.autoreply.autoreply.import.param.file"), merge=app_commands.locale_str("Whether to merge with existing settings", i18n_key="cmd.autoreply.autoreply.import.param.merge"))
    @app_commands.choices(
        merge=[
            app_commands.Choice(name=app_commands.locale_str("Yes", i18n_key="cmd.autoreply.autoreply.import.choice.true"), value="True"),
            app_commands.Choice(name=app_commands.locale_str("No", i18n_key="cmd.autoreply.autoreply.import.choice.false"), value="False")
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
                    await interaction.followup.send(t("autoreply.import.download_failed"))
                    return
                json_data = await resp.text()
        try:
            new_autoreplies = json.loads(json_data)
        except json.JSONDecodeError:
            await interaction.followup.send(t("autoreply.import.parse_failed"))
            return
        if merge:
            autoreplies.extend(new_autoreplies)
        else:
            autoreplies = new_autoreplies
        autoreply_limit = self._get_autoreply_limit(guild_id)
        if len(autoreplies) > autoreply_limit:
            await interaction.followup.send(
                t("autoreply.import.would_exceed_limit", count=autoreply_limit, support_url=config("support_server_invite")))
            return
        set_server_config(guild_id, "autoreplies", autoreplies)
        await interaction.followup.send(t("autoreply.import.done"))
        log("AutoReply config imported.", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
    
    @app_commands.command(name=app_commands.locale_str("ignore", i18n_key="cmd.autoreply.autoreply.ignore.name"), description=app_commands.locale_str("Configure ignored channels", i18n_key="cmd.autoreply.autoreply.ignore.desc"))
    @app_commands.describe(
        mode=app_commands.locale_str("Ignore channel mode", i18n_key="cmd.autoreply.autoreply.ignore.param.mode"),
        channels=app_commands.locale_str("Channel IDs (separate multiple IDs with ,)", i18n_key="cmd.autoreply.autoreply.ignore.param.channels")
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name=app_commands.locale_str("Ignore list", i18n_key="cmd.autoreply.autoreply.ignore.choice.blacklist"), value="blacklist"),
            app_commands.Choice(name=app_commands.locale_str("Allow list only", i18n_key="cmd.autoreply.autoreply.ignore.choice.whitelist"), value="whitelist"),
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
        await interaction.response.send_message(
            t("autoreply.ignore.updated", mode=mode,
              channels=", ".join(map(str, valid_channels)) if valid_channels else t("common.state.none")))
        log(f"AutoReply ignore settings updated. mode={mode}, channels={valid_channels}", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
    
    @app_commands.command(name=app_commands.locale_str("test", i18n_key="cmd.autoreply.autoreply.test.name"), description=app_commands.locale_str("Test variable substitution in auto-reply content", i18n_key="cmd.autoreply.autoreply.test.desc"))
    @app_commands.describe(response=app_commands.locale_str("The reply content to test", i18n_key="cmd.autoreply.autoreply.test.param.response"))
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

        mock_message = MockMessage(guild, author, channel, t("autoreply.test.mock_content"))

        final_response, _, embed, _, delayed_actions = await self._process_response_v2(response, mock_message)
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
            preview_text = t("autoreply.test.no_output")
        await interaction.response.send_message(
            preview_text,
            embed=embed,
            allowed_mentions=self._build_allowed_mentions(),
        )

    @app_commands.command(name=app_commands.locale_str("builder", i18n_key="cmd.autoreply.autoreply.builder.name"), description=app_commands.locale_str("Build auto-replies with an interactive interface", i18n_key="cmd.autoreply.autoreply.builder.desc"))
    @app_commands.default_permissions(manage_guild=True)
    async def autoreply_builder(self, interaction: discord.Interaction):
        getting_started_module = sys.modules.get("gettingstarted")
        if getting_started_module is not None:
            await getting_started_module.start_autoreply_builder(interaction)
            return
        view = AutoReplyBuilderView(self, interaction)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)
        view.message = await interaction.original_response()
    
    @app_commands.command(name=app_commands.locale_str("help", i18n_key="cmd.autoreply.autoreply.help.name"), description=app_commands.locale_str("Show auto-reply usage instructions", i18n_key="cmd.autoreply.autoreply.help.desc"))
    async def autoreply_help(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed = discord.Embed(
            title=t("autoreply.help.title"),
            description=t("autoreply.help.subtitle"),
            color=0x00FF00,
        )

        embed.add_field(
            name=t("autoreply.help.section.commands"),
            value=t("autoreply.help.body.commands",
                   add=await get_command_mention("autoreply", "add"),
                   edit=await get_command_mention("autoreply", "edit"),
                   remove=await get_command_mention("autoreply", "remove"),
                   quickadd=await get_command_mention("autoreply", "quickadd"),
                   template=await get_command_mention("autoreply", "template"),
                   list=await get_command_mention("autoreply", "list"),
                   clear=await get_command_mention("autoreply", "clear"),
                   export=await get_command_mention("autoreply", "export"),
                   import_=await get_command_mention("autoreply", "import"),
                   ignore=await get_command_mention("autoreply", "ignore"),
                   test=await get_command_mention("autoreply", "test")),
            inline=False,
        )

        embed.add_field(
            name=t("autoreply.help.section.basic_vars"),
            value=t("autoreply.help.body.basic_vars"),
            inline=False,
        )

        embed.add_field(
            name=t("autoreply.help.section.date_condition"),
            value=t("autoreply.help.body.date_condition"),
            inline=False,
        )

        embed.add_field(
            name=t("autoreply.help.section.embed_advanced"),
            value=t("autoreply.help.body.embed_advanced"),
            inline=False,
        )

        embed.add_field(
            name=t("autoreply.help.section.special_trigger"),
            value=t("autoreply.help.body.special_trigger"),
            inline=False,
        )

        embed.add_field(
            name=t("autoreply.help.section.delay_state_vars"),
            value=t("autoreply.help.body.delay_state_vars"),
            inline=False,
        )

        embed.add_field(
            name=t("autoreply.help.section.builtin_packs"),
            value=t("autoreply.help.body.builtin_packs",
                   template=await get_command_mention("autoreply", "template")),
            inline=False,
        )

        embed.add_field(
            name=t("autoreply.help.section.notes"),
            value=t("autoreply.help.body.notes"),
            inline=False,
        )

        class HelpView(i18n.I18nView):
            def __init__(self):
                super().__init__(timeout=60)

            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(view=self)
                self.stop()

            @discord.ui.button(label=i18n.K("autoreply.help.btn.more_examples"), style=discord.ButtonStyle.primary)
            async def examples(self, i: discord.Interaction, _: discord.ui.Button):
                ex = discord.Embed(title=t("autoreply.help.examples_title"), color=0x00FF00)
                ex.description = t("autoreply.help.examples_body")
                await i.response.send_message(embed=ex, ephemeral=True)

            @discord.ui.button(label=i18n.K("autoreply.help.btn.test_hint"), style=discord.ButtonStyle.secondary)
            async def hint(self, i: discord.Interaction, _: discord.ui.Button):
                await i.response.send_message(
                    t("autoreply.help.test_hint_body", test=await get_command_mention("autoreply", "test")),
                    ephemeral=True
                )

        await interaction.followup.send(embed=embed, view=HelpView())


    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        async with i18n.guild_scope(message.guild.id, user_id=message.author.id):
            await self._on_message_impl(message)

    async def _on_message_impl(self, message: discord.Message):
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

            match_found = self._message_matches_autoreply_triggers(message, mode, triggers)
            
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
                final_response, sticker, embed, allowed_mentions, delayed_actions = await self._process_response_v2(raw_response, message)

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
                            allowed_mentions,
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
                    log(f"AutoReply triggered: `{trigger_used[:10]}...` response: `{response_preview[:10]}...`.",
                        module_name="AutoReply", level=logging.INFO, user=message.author, guild=message.guild)
                except discord.HTTPException as e:
                    log(f"AutoReply failed to send: {e}", module_name="AutoReply", level=logging.ERROR)
                
                return


asyncio.run(bot.add_cog(AutoReply(bot)))

if __name__ == "__main__":
    start_bot()
