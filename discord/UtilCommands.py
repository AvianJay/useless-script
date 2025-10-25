import os
import random
import discord
from discord import app_commands
from discord.ext import commands
from globalenv import bot, start_bot, get_user_data, set_user_data, get_command_mention, modules
from typing import Union

version = "0.3.1"
try:
    git_commit_hash = os.popen("git rev-parse --short HEAD").read().strip()
except Exception as e:
    git_commit_hash = "unknown"
full_version = f"{version} ({git_commit_hash})"

@bot.tree.command(name=app_commands.locale_str("info"), description="顯示機器人資訊")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def info_command(interaction: discord.Interaction):
    await interaction.response.defer()
    server_count = len(bot.guilds)
    user_count = len(set(bot.get_all_members()))
    try:
        bot_latency = round(bot.latency * 1000)  # Convert to milliseconds
    except OverflowError:
        bot_latency = "N/A"

    embed = discord.Embed(title="機器人資訊", color=0x00ff00)
    embed.add_field(name="機器人名稱", value=bot.user.name)
    embed.add_field(name="版本", value=full_version)
    embed.add_field(name="伺服器數量", value=server_count)
    embed.add_field(name="用戶總數量", value=user_count)
    embed.add_field(name="機器人延遲", value=f"{bot_latency}ms")
    embed.add_field(name=f"已載入模組({len(modules)})", value="\n".join(modules) if modules else "無", inline=False)
    embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.followup.send(content="-# 提示：如果你指令用到一半停住或沒辦法用了那很有可能是那個傻逼開發者||尼摳||又再重開機器人了||不然就是機器人又當機了||", embed=embed)


@bot.command(aliases=["botinfo", "bi"])
async def info(ctx: commands.Context):
    """顯示機器人資訊
    
    用法： info
    """
    server_count = len(bot.guilds)
    user_count = len(set(bot.get_all_members()))
    try:
        bot_latency = round(bot.latency * 1000)  # Convert to milliseconds
    except OverflowError:
        bot_latency = "N/A"

    embed = discord.Embed(title="機器人資訊", color=0x00ff00)
    embed.add_field(name="機器人名稱", value=bot.user.name)
    embed.add_field(name="版本", value=full_version)
    embed.add_field(name="伺服器數量", value=server_count)
    embed.add_field(name="用戶總數量", value=user_count)
    embed.add_field(name="機器人延遲", value=f"{bot_latency}ms")
    embed.add_field(name=f"已載入模組({len(modules)})", value="\n".join(modules) if modules else "無", inline=False)
    embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None)
    await ctx.send(content="-# 提示：如果你指令用到一半停住或沒辦法用了那很有可能是那個傻逼開發者||尼摳||又再重開機器人了||不然就是機器人又當機了||", embed=embed)


@bot.tree.command(name=app_commands.locale_str("randomnumber"), description="生成一個隨機數字")
@app_commands.describe(min="最小值", max="最大值")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def randomnumber_command(interaction: discord.Interaction, min: int = 1, max: int = 100):
    if min >= max:
        await interaction.response.send_message("錯誤：最小值必須小於最大值。", ephemeral=True)
        return
    number = random.randint(min, max)
    await interaction.response.send_message(f"隨機數字：{number}\n-# 範圍：{min} - {max}")


@bot.command(aliases=["rn"])
async def randomnumber(ctx: commands.Context, min: int = 1, max: int = 100):
    if min >= max:
        await ctx.send("錯誤：最小值必須小於最大值。")
        return
    number = random.randint(min, max)
    await ctx.send(f"隨機數字：{number}\n-# 範圍：{min} - {max}")


@bot.tree.command(name=app_commands.locale_str("randomuser"), description="從在目前頻道的發言者中隨機選擇一人")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.describe(mention="是否提及該用戶")
@app_commands.choices(mention=[
    app_commands.Choice(name="是", value=1),
    app_commands.Choice(name="否", value=0),
])
async def randomuser_command(interaction: discord.Interaction, mention: int = 0):
    mention = bool(mention)
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
    await interaction.response.send_message(f"隨機選擇的用戶是：{selected_user.mention if mention else selected_user.display_name}！\n-# 抽取用戶總數：{len(users)}")


@bot.tree.command(name=app_commands.locale_str("userinfo"), description="顯示用戶資訊")
@app_commands.describe(user="要查詢的用戶")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def userinfo_command(interaction: discord.Interaction, user: Union[discord.User, discord.Member]):
    embed = discord.Embed(title=f"{user.display_name} 的資訊", color=0x00ff00)
    embed.set_thumbnail(url=user.avatar.url if user.avatar else discord.Embed.Empty)
    embed.add_field(name="用戶 ID", value=str(user.id), inline=True)
    embed.add_field(name="帳號創建時間", value=user.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    if isinstance(user, discord.Member):
        embed.add_field(name="伺服器暱稱", value=user.nick or "無", inline=True)
        embed.add_field(name="加入伺服器時間", value=user.joined_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
        # pfp
        if user.display_avatar:
            embed.set_image(url=user.display_avatar.url if user.display_avatar.url != user.avatar.url else None)
    await interaction.response.send_message(embed=embed)


@bot.command(aliases=["ui"])
async def userinfo(ctx: commands.Context, user: Union[discord.User, discord.Member] = None):
    """顯示用戶資訊
    
    用法： userinfo [用戶]
    如果不指定用戶，則顯示自己的資訊。
    """
    if user is None:
        user = ctx.author
    embed = discord.Embed(title=f"{user.display_name} 的資訊", color=0x00ff00)
    embed.set_thumbnail(url=user.avatar.url if user.avatar else discord.Embed.Empty)
    embed.add_field(name="用戶 ID", value=str(user.id), inline=True)
    embed.add_field(name="帳號創建時間", value=user.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    if isinstance(user, discord.Member):
        embed.add_field(name="伺服器暱稱", value=user.nick or "無", inline=True)
        embed.add_field(name="加入伺服器時間", value=user.joined_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
        # pfp
        if user.display_avatar:
            embed.set_image(url=user.display_avatar.url if user.display_avatar.url != user.avatar.url else None)
    await ctx.send(embed=embed)


@bot.tree.command(name=app_commands.locale_str("get-command-mention"), description="取得指令的提及格式")
@app_commands.describe(command="指令名稱", subcommand="子指令名稱（可選）")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def get_cmd_mention(interaction: discord.Interaction, command: str, subcommand: str = None):
    mention = await get_command_mention(command, subcommand)
    if mention is None:
        await interaction.response.send_message("找不到指定的指令。", ephemeral=True)
        return
    await interaction.response.send_message(f"{mention}")


@bot.tree.command(name=app_commands.locale_str("textlength"), description="計算輸入文字的長度")
@app_commands.describe(text="要計算長度的文字")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def textlength_command(interaction: discord.Interaction, text: str):
    length = len(text)
    await interaction.response.send_message(f"{length} 個字。")


@bot.command(aliases=["len"])
async def length(ctx: commands.Context, *, text: str = ""):
    """計算輸入文字的長度
    
    用法： length <文字>/<回覆訊息>
    """
    # if not text use reply message content
    if not text and ctx.message.reference:
        replied_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        text = replied_message.content
    length = len(text)
    await ctx.send(f"{length} 個字。")


@bot.tree.command(name=app_commands.locale_str("httpcat"), description="貓咪好可愛")
@app_commands.describe(status_code="HTTP 狀態碼（例如 404）")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def httpcat_command(interaction: discord.Interaction, status_code: int):
    # check status code is valid
    if status_code < 100 or status_code > 599:
        status_code = 404
    url = f"https://http.cat/{status_code}"
    embed = discord.Embed(title=f"HTTP Cat {status_code}", color=0x00ff00)
    embed.set_image(url=url)
    await interaction.response.send_message(embed=embed)
    

@bot.command(aliases=["hc"])
async def httpcat(ctx: commands.Context, status_code: int):
    """貓咪好可愛
    
    用法： httpcat <HTTP 狀態碼>
    """
    # check status code is valid
    if status_code < 100 or status_code > 599:
        status_code = 404
    url = f"https://http.cat/{status_code}"
    embed = discord.Embed(title=f"HTTP Cat {status_code}", color=0x00ff00)
    embed.set_image(url=url)
    await ctx.send(embed=embed)


if __name__ == "__main__":
    start_bot()
