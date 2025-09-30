# Powered by ChatGPT lol
import discord
import random
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta, timezone
from globalenv import bot, start_bot

# 記錄使用者的上次使用時間
last_used = {}


@bot.tree.command(name="dsize", description="量屌長")
async def dsize(interaction: discord.Interaction):
    user_id = interaction.user.id
    now = datetime.utcnow().astimezone(timezone(timedelta(hours=8))).date()  # 台灣時間
    last = last_used.get(user_id, datetime(1970, 1, 1).date())

    # 檢查是否已經使用過指令，並且是否已超過一天
    if user_id in last_used and now == last:
        # calculate time left
        # Convert last (date) to datetime at midnight in Taiwan timezone
        next_day = datetime.combine(last + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone(timedelta(hours=8)))
        timestamp_next = next_day.astimezone(timezone.utc)  # Convert to UTC for Discord timestamp
        await interaction.response.send_message(f"一天只能量一次屌長。<t:{int(timestamp_next.timestamp())}:R> 才能再次使用。", ephemeral=True)
        return

    # 更新使用時間
    last_used[user_id] = datetime.utcnow().date()

    # 隨機產生長度 (2-30)
    size = random.randint(2, 30)
    d_string = "=" * (size - 2)

    # 建立 Embed 訊息
    embed = discord.Embed(title=f"{interaction.user.name} 的長度：", color=0x00ff00)
    embed.add_field(name=f"{size} cm", value=f"8{d_string}D", inline=False)

    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    start_bot()
