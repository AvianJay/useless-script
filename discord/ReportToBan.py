import asyncio
import g4f
import json
from datetime import datetime, timedelta
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import os

# 你要放檢舉紀錄的頻道 ID
config_version = 1
config_path = 'config.reporttoban.json'

default_config = {
    "config_version": config_version,
    "REPORT_CHANNEL_ID": 123456789012345678,  # 檢舉紀錄頻道 ID
    "MODERATION_MESSAGE_CHANNEL_ID": 123456789012345678,  # 管理員通知頻道 ID
    "TOKEN": "YOUR_BOT_TOKEN_HERE",  # 機器人 Token
    "REPORTED_MESSAGE": "感謝您的檢舉，我們會盡快處理您的檢舉。",  # 用戶檢舉後的回覆訊息
    "REPORT_BLACKLIST": [],  # 無法使用檢舉功能的角色 ID 陣列
    "REPORT_RATE_LIMIT": 300  # 用戶檢舉頻率限制，單位為秒
}
_config = None

try:
    if os.path.exists(config_path):
        _config = json.load(open(config_path, "r"))
        # Todo: verify
        if not isinstance(_config, dict):
            print("[!] Config file is not a valid JSON object, \
                resetting to default config.")
            _config = default_config.copy()
        for key in _config.keys():
            if not isinstance(_config[key], type(default_config[key])):
                print(f"[!] Config key '{key}' has an invalid type, \
                      resetting to default value.")
                _config[key] = default_config[key]
        if "config_version" not in _config:
            print("[!] Config file does not have 'config_version', \
                resetting to default config.")
            _config = default_config.copy()
    else:
        _config = default_config.copy()
        json.dump(_config, open(config_path, "w"), indent=4)
except ValueError:
    _config = default_config.copy()
    json.dump(_config, open(config_path, "w"), indent=4)

if _config.get("config_version", 0) < config_version:
    print("[+] Updating config file from version",
          _config.get("config_version", 0),
          "to version",
          config_version
          )
    for k in default_config.keys():
        if _config.get(k) is None:
            _config[k] = default_config[k]
    _config["config_version"] = config_version
    print("[+] Saving...")
    json.dump(_config, open(config_path, "w"), indent=4)
    print("[+] Done.")

def config(key, value=None, mode="r"):
    if mode == "r":
        return _config.get(key)
    elif mode == "w":
        _config[key] = value
        json.dump(_config, open(config_path, "w"), indent=4)
        return True
    else:
        raise ValueError(f"Invalid mode: {mode}")

REPORT_CHANNEL_ID = config("REPORT_CHANNEL_ID")
MODERATION_MESSAGE_CHANNEL_ID = config("MODERATION_MESSAGE_CHANNEL_ID")
TOKEN = config("TOKEN")
REPORTED_MESSAGE = config("REPORTED_MESSAGE")
REPORT_BLACKLIST = config("REPORT_BLACKLIST")
REPORT_RATE_LIMIT = config("REPORT_RATE_LIMIT")
last_report_times = {}  # 用戶 ID -> 上次檢舉時間
reported_messages = []

SERVER_RULES = """
# 地球Online台服玩家交流區新版規則 (摘要)

1. 新進成員請領取身分組 (不重要)
2. 遵守 Discord 規範
3. 禁止騷擾、仇恨、不實言論
4. 禁止粗俗或髒字貶損他人
5. 禁止色情、血腥或暴力內容
6. 禁止有害連結、檔案
7. 禁止散布非本人私人資料
8. 請依照頻道用途使用
9. 禁止規避懲處
10. 禁止惡意誣告檢舉
11. 其他違反以上規則之行為
"""

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


async def check_message_with_ai(text: str, history_messages: str="", reason: str="") -> dict:
    """
    使用 g4f + Pollinations 判斷訊息是否違反群規
    回傳格式 JSON: {"level": 違規等級，0到5, "reason": "簡短說明，若違規需指出違反哪一條規則", "suggestion_actions": [{"action": "ban" | "kick" | "mute", "duration": 若禁言，請提供禁言時間，格式如秒數，若非封鎖則為 0 (只能為秒數)}]}, "target": "reporter" | "reported_user" (若是封鎖檢舉人，請填 reporter，若是封鎖被檢舉人，請填 reported_user)
    """

    if history_messages:
        history_messages = "\n用戶歷史訊息：\n" + history_messages + "\n"
    
    safe_text = json.dumps(text, ensure_ascii=False)
    safe_history = json.dumps(history_messages, ensure_ascii=False) if history_messages else '""'
    safe_reason = json.dumps(reason, ensure_ascii=False)

    prompt = f"""
你是 Discord 伺服器的審核助手。
以下是伺服器規則：
{SERVER_RULES}

請根據規則判斷這則訊息是否違規。
檢舉人的檢舉原因也有可能是錯的，請以規則為準。
如果檢舉人的原因違反規則，請一併指出。

被檢舉的原始資料（已 escape 為 JSON 字串）：
REPORTED_MESSAGE: {safe_text}
HISTORY_MESSAGES: {safe_history}
REPORT_REASON: {safe_reason}

請輸出 JSON，格式如下：
{{
  "level": 違規等級，0到5,
  "reason": "簡短說明，若違規需指出違反哪一條規則",
  "suggestion_actions": [
      {{
        "action": "ban" | "kick" | "mute",
        "duration": 若禁言，請提供禁言時間，格式如秒數，若非封鎖則為 0 (只能為秒數)
      }}
  ]
}}
"""

    response = await asyncio.to_thread(
        g4f.ChatCompletion.create,
        model="openai-fast",
        provider=g4f.Provider.PollinationsAI,
        messages=[{"role": "system", "content": "你是一個公正嚴謹的Discord審核助手。"},
                  {"role": "user", "content": prompt}]
    )

    try:
        return json.loads(response)
    except Exception:
        return {"level": 0, "reason": "無法解析回應", "suggestion_actions": []}


def get_time_text(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} 秒"
    elif seconds < 3600:
        return f"{seconds // 60} 分鐘"
    elif seconds < 86400:
        return f"{seconds // 3600} 小時"
    else:
        return f"{seconds // 86400} 天"


def send_moderation_message(user: discord.Member, moderator: discord.Member, actions: dict, reason: str, message_content: str, is_ai: bool=False) -> str:
    action_texts = []
    print("[DEBUG] Actions:", actions)
    for action in actions:
        if action["action"] == "ban":
            action_texts.append("驅逐出境至柬服KK副本||永久停權||")
        elif action["action"] == "kick":
            action_texts.append("踢出")
        elif action["action"] == "mute":
            time_text = action.get("duration", 0)
            action_texts.append(f"羈押禁見||禁言||{get_time_text(time_text)}")
    action_text = "+".join(action_texts)
    print("[DEBUG] Action Text:", action_text)
    text = f"""
### ⛔ 違規處分
> - 被處分者： {user.mention}
> - 訊息內容： {message_content}
> - 處分原因：{reason}
> - 處分結果：{action_text}
> - 處分執行： {moderator.mention}
"""
    if is_ai:
        text += "\n-# 此處分由 AI 建議的處分"
    mod_channel = bot.get_channel(MODERATION_MESSAGE_CHANNEL_ID)
    if mod_channel:
        asyncio.create_task(mod_channel.send(text))


async def timeout_user(*, user_id: int, guild_id: int, until, reason: str="") -> bool:
    headers = {"Authorization": f"Bot {bot.http.token}"}
    url = f"https://discord.com/api/v9/guilds/{guild_id}/members/{user_id}"
    timeout_dt = discord.utils.utcnow() + timedelta(seconds=until)
    timeout = timeout_dt.replace(microsecond=0).isoformat()
    payload = {'communication_disabled_until': timeout, 'reason': reason}
    async with aiohttp.ClientSession() as session:
        async with session.patch(url, json=payload, headers=headers) as resp:
            if resp.status in range(200, 299):
                return True
            return False


class doModerationActions(discord.ui.View):
    def __init__(self, user: discord.Member, interaction: discord.Interaction, ai_suggestions: list, ai_reason: str="", message: discord.Message=None):
        super().__init__(timeout=None)
        self.user = user
        self.interaction = interaction
        self.ai_suggestions = ai_suggestions
        self.ai_reason = ai_reason
        self.message = message
        self.message_content = message.content if message else "(無內容)"

        # 如果 AI 建議為空，不顯示按鈕
        if not self.ai_suggestions:
            self.remove_item(self.ai_suggestion_button)

    # AI 建議的處置按鈕
    @discord.ui.button(label="執行 AI 建議處置", style=discord.ButtonStyle.danger, custom_id="ai_suggestion_button")
    async def ai_suggestion_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"已執行 AI 建議處置 {self.user.mention}", ephemeral=True)
        for action in self.ai_suggestions:
            if action.get("action") == "ban":
                await interaction.guild.ban(self.user, reason=self.ai_reason)
            elif action.get("action") == "kick":
                await interaction.guild.kick(self.user, reason=self.ai_reason)
            elif action.get("action") == "mute":
                duration = action.get("duration", 0)
                if duration > 0:
                    await timeout_user(user_id=self.user.id, guild_id=interaction.guild.id, until=duration, reason=self.ai_reason)
        send_moderation_message(self.user, interaction.user, self.ai_suggestions, self.ai_reason, self.message_content, is_ai=True)

    @discord.ui.button(label="封鎖", style=discord.ButtonStyle.danger, custom_id="ban_button")
    async def ban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        class BanReasonModal(discord.ui.Modal, title="封鎖原因"):
            reason = discord.ui.TextInput(label="封鎖原因", placeholder="請輸入封鎖原因", required=True, max_length=100)

            async def on_submit(self, modal_interaction: discord.Interaction):
                await modal_interaction.response.send_message(f"已封鎖 {self.user.mention}", ephemeral=True)
                await interaction.guild.ban(self.user, reason=self.reason.value or "違反規則")
                send_moderation_message(self.user, interaction.user, [{"action": "ban"}], self.reason.value or "違反規則", self.message_content)
        await interaction.response.send_modal(BanReasonModal())

    @discord.ui.button(label="踢出", style=discord.ButtonStyle.primary, custom_id="kick_button")
    async def kick_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        class KickReasonModal(discord.ui.Modal, title="踢出原因"):
            reason = discord.ui.TextInput(label="踢出原因", placeholder="請輸入踢出原因", required=True, max_length=100)

            async def on_submit(self, modal_interaction: discord.Interaction):
                await modal_interaction.response.send_message(f"已踢出 {self.user.mention}", ephemeral=True)
                await interaction.guild.kick(self.user, reason=self.reason.value or "違反規則")
                send_moderation_message(self.user, interaction.user, [{"action": "kick"}], self.reason.value or "違反規則", self.message_content)
        await interaction.response.send_modal(KickReasonModal())

    @discord.ui.button(label="禁言", style=discord.ButtonStyle.secondary, custom_id="mute_button")
    async def mute_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        parent_user = self.user  # 先存外部 self.user

        class MuteModal(discord.ui.Modal, title="禁言時間設定"):
            minutes = discord.ui.TextInput(label="禁言分鐘數", placeholder="請輸入禁言時間（分鐘）", required=True)
            reason = discord.ui.TextInput(label="禁言原因", placeholder="請輸入禁言原因", required=True, max_length=100)

            async def on_submit(self, modal_interaction: discord.Interaction):
                try:
                    mins = int(self.minutes.value)
                    if mins <= 0:
                        await modal_interaction.response.send_message("請輸入正整數分鐘。", ephemeral=True)
                        return
                    await timeout_user(user_id=parent_user.id, guild_id=interaction.guild.id, until=mins * 60, reason=self.reason.value or "違反規則")
                    await modal_interaction.response.send_message(f"已禁言 {parent_user.mention} {mins} 分鐘", ephemeral=True)
                    send_moderation_message(parent_user, interaction.user, [{"action": "mute", "duration": mins * 60}], self.reason.value or "違反規則", self.message_content)
                except Exception as e:
                    print(f"Error occurred: {str(e)}")
                    await modal_interaction.response.send_message(f"發生錯誤，請稍後再試。\n{str(e)}", ephemeral=True)

        await interaction.response.send_modal(MuteModal())
    
    @discord.ui.button(label="查看前10則訊息", style=discord.ButtonStyle.secondary, custom_id="view_messages_button")
    async def view_messages_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        messages = []
        async for msg in self.interaction.channel.history(limit=100, before=self.message):
            if msg.author == self.message.author:
                messages.append(f"{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')} - {msg.content}")
            if len(messages) >= 10:
                break
        if messages:
            await interaction.response.send_message("前10則訊息：\n" + "\n".join(messages), ephemeral=True)
        else:
            await interaction.response.send_message("找不到該用戶的訊息。", ephemeral=True)


@bot.tree.context_menu(name="檢舉訊息")
async def report_message(interaction: discord.Interaction, message: discord.Message):
    global last_report_times
    global reported_messages
    # check if the user's role is in the blacklist
    for role in interaction.user.roles:
        if role.id in REPORT_BLACKLIST:
            await interaction.response.send_message("您無法檢舉此訊息。", ephemeral=True)
            return
    
    # rate limit: check if the user has reported in the last REPORT_RATE_LIMIT seconds
    # if the user is admin, skip rate limit
    if not (interaction.user.guild_permissions.administrator):
        now = datetime.utcnow()
        last_report_time = last_report_times.get(interaction.user.id)
        if last_report_time and (now - last_report_time).total_seconds() < REPORT_RATE_LIMIT:
            can_report_time = last_report_time + timedelta(seconds=REPORT_RATE_LIMIT)
            await interaction.response.send_message(f"您檢舉的頻率過快，請在 {can_report_time.strftime('%Y-%m-%d %H:%M:%S')} 後再試。", ephemeral=True)
            return
        
    if message.id in reported_messages:
        await interaction.response.send_message("此訊息已被檢舉過，請勿重複檢舉。", ephemeral=True)
        return

    async def handle_report(interaction: discord.Interaction, message: discord.Message, reason: str):
        global last_report_times
        global reported_messages
        # check again
        if message.id in reported_messages:
            await interaction.response.send_message("此訊息已被檢舉過，請勿重複檢舉。", ephemeral=True)
            return
        last_report_times[interaction.user.id] = datetime.utcnow()
        reported_messages.append(message.id)
        # clean old message ids (limit 100)
        if len(reported_messages) > 100:
            reported_messages = reported_messages[-100:]
            print("[!] 清理舊的檢舉訊息ID")
        print(f"[+] {interaction.user} 檢舉訊息 {message.id}，原因：{reason}")
        # 發送到檢舉紀錄頻道
        report_channel = bot.get_channel(REPORT_CHANNEL_ID)
        if report_channel:
            embed = discord.Embed(
                title="📣 新檢舉紀錄",
                color=discord.Color.red()
            )
            embed.add_field(name="被檢舉訊息", value=message.content or "(無內容)", inline=False)
            embed.add_field(name="檢舉人", value=interaction.user.mention, inline=False)
            embed.add_field(name="訊息作者", value=message.author.mention, inline=False)
            embed.add_field(name="檢舉原因", value=reason, inline=False)
            embed.add_field(name="AI 判斷", value="正在載入中...", inline=False)
            embed.add_field(name="訊息連結", value=f"[跳轉]({message.jump_url})", inline=False)

            sent_msg = await report_channel.send(embed=embed, view=doModerationActions(message.author, interaction, [], message=message))

            # 呼叫 AI 判斷訊息是否正當
            try:
                messages = []
                async for msg in interaction.channel.history(limit=100, before=message):
                    # print("[DEBUG]", msg.created_at, msg.author, msg.content)
                    if msg.author == message.author:
                        messages.append(f"{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')} - {msg.content}")
                    if len(messages) >= 10:
                        break
                history_messages = "\n".join(messages[:10])
                # print("[DEBUG] History Messages:", history_messages)
                verdict = await check_message_with_ai(message.content, history_messages=history_messages, reason=reason)

                verdict_text = f"違規等級: {verdict.get('level', 0)}\n原因: {verdict.get('reason', '無')}\n建議處置: "
                actions = verdict.get('suggestion_actions', [])
                if actions:
                    action_texts = []
                    for action in actions:
                        action_desc = f"{action.get('action', 'N/A')}"
                        if action.get('action') == 'mute':
                            action_desc += f" ({get_time_text(action.get('duration', 0))})"
                        action_texts.append(action_desc)
                    verdict_text += ", ".join(action_texts)

                # 更新嵌入訊息
                embed.set_field_at(4, name="AI 判斷", value=verdict_text, inline=False)
                await sent_msg.edit(embed=embed, view=doModerationActions(message.author, interaction, actions, message=message, ai_reason=verdict.get('reason', '')))
            except Exception as e:
                embed.set_field_at(4, name="AI 判斷", value=f"錯誤：\n{str(e)}", inline=False)
                await sent_msg.edit(embed=embed, view=doModerationActions(message.author, interaction, [], message=message))
                return
    class ReasonModal(discord.ui.Modal, title="檢舉原因"):
        reason = discord.ui.TextInput(label="檢舉原因", placeholder="請輸入檢舉原因", required=True, max_length=100)

        async def on_submit(self, modal_interaction: discord.Interaction):
            await modal_interaction.response.send_message(REPORTED_MESSAGE, ephemeral=True)
            await handle_report(modal_interaction, message, self.reason.value)

    await interaction.response.send_modal(ReasonModal())


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'Logged in as {bot.user}')

bot.run(TOKEN)
