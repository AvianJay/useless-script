# Powered by ChatGPT lol
import os
import sys
import json
import discord
import random
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta

if os.path.exists("config.dsize.json"):
    config = json.load(open("config.dsize.json", "r"))
    TOKEN = config["token"]
else:
    config = {"token": "YOUR_TOKEN_HERE"}
    json.dump(config, open("config.dsize.json", "w"))
    print("No config! Saved default config, please edit!")
    sys.exit(1)

# 設定指令前綴
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# 記錄使用者的上次使用時間
last_used = {}

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()  # 同步 Slash 指令
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Error syncing commands: {e}")

@bot.tree.command(name="dsize", description="量屌長")
async def dsize(interaction: discord.Interaction):
    user_id = interaction.user.id
    now = datetime.utcnow()

    # 檢查是否已經使用過指令，並且是否已超過一天
    if user_id in last_used and now - last_used[user_id] < timedelta(days=1):
        await interaction.response.send_message("一天只能量一次屌長。", ephemeral=True)
        return

    # 更新使用時間
    last_used[user_id] = now

    # 隨機產生長度 (2-30)
    size = random.randint(2, 30)
    d_string = "=" * (size - 2)

    # 建立 Embed 訊息
    embed = discord.Embed(title=f"{interaction.user.name} 的長度：", color=0x00ff00)
    embed.add_field(name=f"{size} cm", value=f"8{d_string}D", inline=False)

    await interaction.response.send_message(embed=embed)

# 啟動機器人
bot.run(TOKEN)
