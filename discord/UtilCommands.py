import os
import random
import discord
from discord import app_commands
from discord.ext import commands
from globalenv import bot, start_bot, get_user_data, set_user_data

version = "0.0.1"
try:
    git_commit_hash = os.popen("git rev-parse --short HEAD").read().strip()
except Exception as e:
    git_commit_hash = "unknown"
full_version = f"{version} ({git_commit_hash})"

@bot.tree.command(name=app_commands.locale_str("info"), description="顯示機器人資訊")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def info_command(interaction: discord.Interaction):
    server_count = len(bot.guilds)
    user_count = len(set(bot.get_all_members()))
    bot_latency = round(bot.latency * 1000)  # Convert to milliseconds

    embed = discord.Embed(title="機器人資訊", color=0x00ff00)
    embed.add_field(name="版本", value=full_version)
    embed.add_field(name="伺服器數量", value=server_count)
    embed.add_field(name="用戶總數量", value=user_count)
    embed.add_field(name="機器人延遲", value=f"{bot_latency}ms")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name=app_commands.locale_str("randomnumber"), description="生成一個隨機數字")
@app_commands.describe(min="最小值", max="最大值")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def randomnumber_command(interaction: discord.Interaction, min: int = 1, max: int = 100):
    if min >= max:
        await interaction.response.send_message("錯誤：最小值必須小於最大值。", ephemeral=True)
        return
    number = random.randint(min, max)
    await interaction.response.send_message(f"隨機數字：{number} (範圍：{min} - {max})")


@bot.tree.command(name=app_commands.locale_str("randomuser"), description="從在目前頻道的發言者中隨機選擇一人")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def randomuser_command(interaction: discord.Interaction):
    if interaction.guild is None or interaction.channel is None:
        await interaction.response.send_message("此指令只能在伺服器頻道中使用。", ephemeral=True)
        return

    channel = interaction.channel
    messages = [msg async for msg in channel.history(limit=50)]
    users = list(set(msg.author for msg in messages if not msg.author.bot))

    if not users:
        await interaction.response.send_message("找不到任何用戶。", ephemeral=True)
        return

    selected_user = random.choice(users)
    await interaction.response.send_message(f"隨機選擇的用戶是：{selected_user.mention}！\n-# 抽取用戶總數：{len(users)}")
