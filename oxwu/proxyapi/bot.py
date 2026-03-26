import discord
from discord import app_commands
import sqlite3
import asyncio
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

from auth_utils import generate_api_key

load_dotenv()

# 你的 Bot Token 與有權限管理點數的特定 User ID
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
ADMIN_IDS = [
    int(user_id)
    for user_id in os.getenv("DISCORD_ADMIN_IDS", "1048398804359061585,849221915856207895").split(",")
    if user_id.strip()
]
API_INFO_CHANNEL_ID = int(os.getenv("DISCORD_API_INFO_CHANNEL_ID", "1484035289998430261"))
DB_PATH = os.getenv("DB_PATH", "database.db")

WHITELIST_ON = os.getenv("WHITELIST", "false").lower() == "true"


def fmt_time(value):
    if not value:
        return "N/A"
    try:
        timestamp = int(datetime.fromisoformat(value).timestamp())
    except (TypeError, ValueError):
        return str(value)
    return f"<t:{timestamp}:F> (<t:{timestamp}:R>)"


def build_presence_text(api_info: dict) -> str:
    upstream = api_info.get("upstream", {})
    connections = api_info.get("connected_clients", 0)
    if not upstream.get("client_installed", True):
        return f"缺失上游客戶端 | WS {connections}"
    if upstream.get("connected"):
        return f"上游在線 | WS {connections}"
    return f"上游離線 | WS {connections}"


def build_api_info_embed(api_info: dict) -> discord.Embed:
    upstream = api_info.get("upstream", {})
    events = api_info.get("events", {})

    if not upstream.get("client_installed", True):
        color = 0x6B7280
        upstream_text = "未安裝 upstream Socket.IO client"
    elif upstream.get("connected"):
        color = 0x16A34A
        upstream_text = "已連結"
    else:
        color = 0xDC2626
        upstream_text = "未連結"

    embed = discord.Embed(title="API 使用資訊", color=color, timestamp=datetime.now(timezone.utc))
    embed.add_field(
        name="WebSocket 客戶端",
        value=str(api_info.get("connected_clients", "N/A")),
        inline=True,
    )
    embed.add_field(
        name="上游 WebSocket 狀態",
        value=upstream_text,
        inline=True,
    )
    embed.add_field(
        name="啟動時間",
        value=fmt_time(api_info.get("app_started_at")),
        inline=False,
    )
    embed.add_field(
        name="上游最後連結時間",
        value=fmt_time(upstream.get("last_connected_at")),
        inline=False,
    )
    embed.add_field(
        name="上游最後連線嘗試時間",
        value=fmt_time(upstream.get("last_connect_attempt_at")),
        inline=False,
    )

    if upstream.get("last_disconnected_at"):
        embed.add_field(
            name="上游最後中斷時間",
            value=fmt_time(upstream.get("last_disconnected_at")),
            inline=False,
        )

    if upstream.get("last_error"):
        embed.add_field(
            name="上游錯誤訊息",
            value=upstream["last_error"][:1024],
            inline=False,
        )

    for event_name in ("report", "warning"):
        event_info = events.get(event_name, {})
        value = "\n".join(
            [
                f"上次事件: {fmt_time(event_info.get('last_event_at'))}",
                f"快取更新: {fmt_time(event_info.get('last_cache_update_at'))}",
                f"截圖更新: {fmt_time(event_info.get('last_screenshot_update_at'))}",
                f"快取就緒: {event_info.get('cache_ready', False)}",
                f"截圖就緒: {event_info.get('screenshot_ready', False)}",
            ]
        )
        embed.add_field(name=f"{'報告' if event_name == 'report' else '速報'}狀態", value=value, inline=False)

    return embed

class ProxyBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # 允許讀取消息內容以處理管理員指令
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

client = ProxyBot()

def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id in ADMIN_IDS

def is_user_whitelisted(discord_id: str) -> bool:
    if not WHITELIST_ON:
        return True
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM whitelist WHERE discord_id = ?', (discord_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

# --- 一般用戶指令 ---

@client.tree.command(name="register", description="註冊並獲取你的專屬 API Key")
async def register(interaction: discord.Interaction):
    discord_id = str(interaction.user.id)
    if WHITELIST_ON and not is_user_whitelisted(discord_id):
        await interaction.response.send_message("你不在白名單中，無法使用此服務。", ephemeral=True)
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT api_key FROM users WHERE discord_id = ?', (discord_id,))
    row = cursor.fetchone()
    
    if row:
        await interaction.response.send_message(f"你已經註冊過了！如果你忘記了請使用 `/reset_key`。", ephemeral=True)
    else:
        new_api_key = generate_api_key(discord_id)
        cursor.execute(
            'INSERT INTO users (discord_id, api_key, points, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)',
            (discord_id, new_api_key, 0.0)
        )
        conn.commit()
        await interaction.response.send_message(f"註冊成功！你的 API Key 是：`{new_api_key}`\n目前點數：0.0", ephemeral=True)
    conn.close()

@client.tree.command(name="reset_key", description="重置你的 API Key（舊的 Key 將失效）")
async def reset_key(interaction: discord.Interaction):
    discord_id = str(interaction.user.id)
    if WHITELIST_ON and not is_user_whitelisted(discord_id):
        await interaction.response.send_message("你不在白名單中，無法使用此服務。", ephemeral=True)
        return
    new_api_key = generate_api_key(discord_id)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET api_key = ?, updated_at = CURRENT_TIMESTAMP WHERE discord_id = ?', (new_api_key, discord_id))
    conn.commit()
    await interaction.response.send_message(f"API Key 已重置！新的 API Key 是：`{new_api_key}`", ephemeral=True)
    conn.close()


@client.tree.command(name="my_points", description="查看目前的剩餘點數")
async def my_points(interaction: discord.Interaction):
    discord_id = str(interaction.user.id)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT points FROM users WHERE discord_id = ?', (discord_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        await interaction.response.send_message(f"你目前擁有 **{row[0]:.2f}** 點。", ephemeral=True)
    else:
        await interaction.response.send_message("你尚未註冊，請先使用 `/register`。", ephemeral=True)

# --- 管理員專用指令 ---

@client.tree.command(name="add_points", description="[管理員] 新增指定用戶的點數")
@app_commands.check(is_admin)
async def add_points(interaction: discord.Interaction, member: discord.Member, amount: float):
    discord_id = str(member.id)
    if WHITELIST_ON and not is_user_whitelisted(discord_id):
        await interaction.response.send_message(f"{member.mention} 不在白名單中，無法修改點數。", ephemeral=True)
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('UPDATE users SET points = points + ? WHERE discord_id = ?', (amount, discord_id))
    if cursor.rowcount > 0:
        cursor.execute('SELECT points FROM users WHERE discord_id = ?', (discord_id,))
        new_points = cursor.fetchone()[0]
        conn.commit()
        await interaction.response.send_message(f"已為 {member.mention} 增加 {amount} 點。目前總計：{new_points:.2f} 點。")
    else:
        await interaction.response.send_message(f"{member.mention} 尚未註冊！")
    conn.close()

@client.tree.command(name="set_points", description="[管理員] 設定指定用戶的點數")
@app_commands.check(is_admin)
async def set_points(interaction: discord.Interaction, member: discord.Member, amount: float):
    discord_id = str(member.id)
    if WHITELIST_ON and not is_user_whitelisted(discord_id):
        await interaction.response.send_message(f"{member.mention} 不在白名單中，無法修改點數。", ephemeral=True)
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('UPDATE users SET points = ? WHERE discord_id = ?', (amount, discord_id))
    if cursor.rowcount > 0:
        conn.commit()
        await interaction.response.send_message(f"已將 {member.mention} 的點數設定為 {amount} 點。")
    else:
        await interaction.response.send_message(f"{member.mention} 尚未註冊！")
    conn.close()

@client.tree.command(name="remove_points", description="[管理員] 扣除指定用戶的點數")
@app_commands.check(is_admin)
async def remove_points(interaction: discord.Interaction, member: discord.Member, amount: float):
    # 邏輯與 add_points 類似，只是變成扣除
    await add_points.callback(interaction, member, -amount)

@client.tree.command(name="whitelist", description="[管理員] 將指定用戶加入白名單")
@app_commands.describe(mode="選擇加入或移除或列出白名單", member="要加入或移除白名單的用戶（列出模式可不填）")
async def whitelist(interaction: discord.Interaction, mode: str, member: discord.Member = None):
    if not WHITELIST_ON:
        await interaction.response.send_message("白名單功能未啟用。", ephemeral=True)
        return
    discord_id = str(member.id) if member else None
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if mode == "加入":
        cursor.execute('INSERT OR IGNORE INTO whitelist (discord_id) VALUES (?)', (discord_id,))
        await interaction.response.send_message(f"已將 {member.mention} 加入白名單。")
    elif mode == "移除":
        cursor.execute('DELETE FROM whitelist WHERE discord_id = ?', (discord_id,))
        await interaction.response.send_message(f"已將 {member.mention} 移除白名單。")
    elif mode == "列出":
        cursor.execute('SELECT discord_id FROM whitelist')
        whitelisted_users = cursor.fetchall()
        if whitelisted_users:
            user_list = "\n".join([f"<@{user[0]}> ({user[0]})" for user in whitelisted_users])
            await interaction.response.send_message(f"白名單用戶：\n{user_list}")
        else:
            await interaction.response.send_message("白名單為空。")
    else:
        await interaction.response.send_message("無效的模式。請選擇 '加入'、'移除' 或 '列出'。")

    conn.commit()
    conn.close()

async def send_api_info_periodically():
    await client.wait_until_ready()
    channel = client.get_channel(API_INFO_CHANNEL_ID)
    if not channel:
        print(f"無法找到頻道 ID {API_INFO_CHANNEL_ID}，請確認設定正確。")
        return
    
    initial_info = get_api_info() if get_api_info else {}
    await client.change_presence(
        activity=discord.Game(name=build_presence_text(initial_info))
    )
    info_message = await channel.send(embed=build_api_info_embed(initial_info))
    
    while not client.is_closed():
        api_info = get_api_info() if get_api_info else {}
        await client.change_presence(
            activity=discord.Game(name=build_presence_text(api_info))
        )
        await info_message.edit(embed=build_api_info_embed(api_info))
        await asyncio.sleep(60)  # 每 60 秒更新一次

@client.event
async def on_ready():
    print(f'Bot 已登入為 {client.user}')
    asyncio.create_task(send_api_info_periodically())

class FakeInteraction:
    def __init__(self, message):
        self.user = message.author
        self.message = message
        self.response = self.FakeResponse(message)

    class FakeResponse:
        def __init__(self, message):
            self.message = message

        async def send_message(self, content, ephemeral=False):
            await self.message.reply(content)

@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return
    if message.author.id not in ADMIN_IDS:
        return
    if not message.content:
        return
    cmd = message.content.strip().split()
    if not cmd:
        return
    # %add_points [point] <@user>
    if cmd[0] == '%add_points':
        if len(cmd) > 3 or len(cmd) < 2:
            await message.channel.send("指令格式錯誤！正確用法：`%add_points [point] <@user>`")
            return
        if len(cmd) == 2:
            target = None
            if message.reference and message.reference.resolved:
                target = message.reference.resolved.author
            if message.interaction_metadata:
                target = message.interaction_metadata.user
            if not target:
                target = message.author
            try:
                amount = float(cmd[1])
            except ValueError:
                await message.channel.send("點數必須是數字！")
                return
            await add_points.callback(FakeInteraction(message), target, amount)

get_api_info = None

def run_bot(get_api_info_func):
    global get_api_info
    get_api_info = get_api_info_func
    if not TOKEN:
        print("未設定 DISCORD_BOT_TOKEN，跳過啟動 Discord Bot。")
        return
    client.run(TOKEN)
