import os
import random
import discord
from discord import app_commands
from discord.ext import commands
from globalenv import bot, start_bot, get_user_data, set_user_data, get_command_mention, modules, failed_modules, config
from typing import Union
from datetime import datetime, timezone
import psutil
import time
import aiohttp
from database import db

startup_time = datetime.now(timezone.utc)
version = "0.16.11"
try:
    git_commit_hash = os.popen("git rev-parse --short HEAD").read().strip()
except Exception as e:
    git_commit_hash = "unknown"
full_version = f"{version} ({git_commit_hash})"


def get_commit_logs(limit=10) -> str:
    try:
        logs = os.popen("git log -n 10 \"--pretty=format:%an: %h - %s (%cr)\"").read().strip().split("\n")
        return logs
    except Exception as e:
        return ["無法取得提交記錄。"]


def get_time_text(seconds: int) -> str:
    if seconds == 0:
        return "0 秒"
    
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    
    parts = []
    if days: parts.append(f"{days} 天")
    if hours: parts.append(f"{hours} 小時")
    if minutes: parts.append(f"{minutes} 分鐘")
    if seconds: parts.append(f"{seconds} 秒")
    
    return " ".join(parts)


def get_uptime_seconds() -> int:
    return int((datetime.now(timezone.utc) - startup_time).total_seconds())


@bot.tree.command(name=app_commands.locale_str("info"), description="顯示機器人資訊")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def info_command(interaction: discord.Interaction):
    await interaction.response.defer()
    server_count = len(bot.guilds)
    user_count = len(set(bot.get_all_members()))
    try:
        bot_latency = round(bot.latency * 1000, 2)  # Convert to milliseconds
    except OverflowError:
        bot_latency = "N/A"

    uptime = get_time_text(get_uptime_seconds())
    
    commands_count = len(bot.commands) + sum(len(c.commands) for c in bot.commands if isinstance(c, commands.Group))
    app_commands_count = len(bot.tree.get_commands()) + sum(len(c.commands) for c in bot.tree.get_commands() if isinstance(c, app_commands.Group))
    dbcount = db.get_database_count()

    embed = discord.Embed(title="機器人資訊", color=0x00ff00)
    embed.add_field(name="機器人名稱", value=bot.user.name)
    embed.add_field(name="版本", value=full_version)
    embed.add_field(name="指令數量", value=f"{commands_count + app_commands_count} ({commands_count} 文字指令, {app_commands_count} 應用程式指令)")
    embed.add_field(name="伺服器數量", value=server_count)
    embed.add_field(name="用戶總數量", value=user_count)
    embed.add_field(name="用戶安裝數量", value=bot.application.approximate_user_install_count or "N/A")
    embed.add_field(name="機器人延遲", value=f"{bot_latency}ms")
    embed.add_field(name="CPU 使用率", value=f"{psutil.cpu_percent()}%")
    embed.add_field(name="記憶體使用率", value=f"{psutil.virtual_memory().percent}%")
    embed.add_field(name="運行時間", value=uptime)
    embed.add_field(name="資料庫資訊", value=f"總筆數: {dbcount['total']}\n伺服器筆數: {dbcount['server_configs']}\n用戶資料筆數: {dbcount['user_data']}", inline=True)
    embed.add_field(name=f"已載入模組({len(modules)})", value="\n".join(modules) if modules else "無", inline=False)
    if config("disable_modules", []):
        embed.add_field(name=f"已禁用模組({len(config('disable_modules', []))})", value="\n".join(config("disable_modules", [])), inline=False)
    if failed_modules:
        embed.add_field(name=f"載入失敗的模組({len(failed_modules)})", value="\n".join(failed_modules), inline=False)
    embed.add_field(name="相關連結", value=f"* [機器人網站]({config('website_url')})\n* [支援伺服器]({config('support_server_invite')})\n* [隱私政策]({config('website_url')}/privacy-policy)\n* [服務條款]({config('website_url')}/terms-of-service)\n* [邀請機器人](https://discord.com/oauth2/authorize?client_id={str(bot.user.id)})", inline=False)
    embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None)
    embed.set_footer(text="by AvianJay")
    await interaction.followup.send(content="-# 提示：如果你指令用到一半停住或沒辦法用了那很有可能是那個傻逼開發者||尼摳||又再重開機器人了||不然就是機器人又當機了||", embed=embed)


@bot.command(aliases=["botinfo", "bi"])
async def info(ctx: commands.Context):
    """顯示機器人資訊
    
    用法： info
    """
    server_count = len(bot.guilds)
    user_count = len(set(bot.get_all_members()))
    try:
        bot_latency = round(bot.latency * 1000, 2)  # Convert to milliseconds
    except OverflowError:
        bot_latency = "N/A"
    
    uptime = get_time_text(get_uptime_seconds())
    
    commands_count = len(bot.commands) + sum(len(c.commands) for c in bot.commands if isinstance(c, commands.Group))
    app_commands_count = len(bot.tree.get_commands()) + sum(len(c.commands) for c in bot.tree.get_commands() if isinstance(c, app_commands.Group))
    dbcount = db.get_database_count()

    embed = discord.Embed(title="機器人資訊", color=0x00ff00)
    embed.add_field(name="機器人名稱", value=bot.user.name)
    embed.add_field(name="版本", value=full_version)
    embed.add_field(name="指令數量", value=f"{commands_count + app_commands_count} ({commands_count} 文字指令, {app_commands_count} 應用程式指令)")
    embed.add_field(name="伺服器數量", value=server_count)
    embed.add_field(name="用戶總數量", value=user_count)
    embed.add_field(name="用戶安裝數量", value=bot.application.approximate_user_install_count or "N/A")
    embed.add_field(name="機器人延遲", value=f"{bot_latency}ms")
    embed.add_field(name="CPU 使用率", value=f"{psutil.cpu_percent()}%")
    embed.add_field(name="記憶體使用率", value=f"{psutil.virtual_memory().percent}%")
    embed.add_field(name="運行時間", value=uptime)
    embed.add_field(name="資料庫資訊", value=f"總筆數: {dbcount['total']}\n伺服器筆數: {dbcount['server_configs']}\n用戶資料筆數: {dbcount['user_data']}", inline=True)
    embed.add_field(name=f"已載入模組({len(modules)})", value="\n".join(modules) if modules else "無", inline=False)
    if config("disable_modules", []):
        embed.add_field(name=f"已禁用模組({len(config('disable_modules', []))})", value="\n".join(config("disable_modules", [])), inline=False)
    if failed_modules:
        embed.add_field(name=f"載入失敗的模組({len(failed_modules)})", value="\n".join(failed_modules), inline=False)
    embed.add_field(name="相關連結", value=f"* [機器人網站]({config('website_url')})\n* [支援伺服器]({config('support_server_invite')})\n* [隱私政策]({config('website_url')}/privacy-policy)\n* [服務條款]({config('website_url')}/terms-of-service)\n* [邀請機器人](https://discord.com/oauth2/authorize?client_id={str(bot.user.id)})", inline=False)
    embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None)
    embed.timestamp = datetime.now(timezone.utc)
    embed.set_footer(text="by AvianJay")
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
    """生成一個隨機數字"""
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
    app_commands.Choice(name="是", value="True"),
    app_commands.Choice(name="否", value="False"),
])
async def randomuser_command(interaction: discord.Interaction, mention: str = "False"):
    mention = mention == "True"
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
    view = discord.ui.View()
    # avatar url button
    button = discord.ui.Button(label="頭像連結", url=user.avatar.url if user.avatar else "https://discord.com/assets/6debd47ed13483642cf09e832ed0bc1b.png")
    view.add_item(button)
    embed.add_field(name="用戶 ID", value=str(user.id), inline=True)
    embed.add_field(name="帳號創建時間", value=user.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    if isinstance(user, discord.Member):
        embed.add_field(name="伺服器暱稱", value=user.nick or "無", inline=True)
        embed.add_field(name="加入伺服器時間", value=user.joined_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
        # pfp
        if user.display_avatar and user.display_avatar.url != user.avatar.url:
            embed.set_image(url=user.display_avatar.url if user.display_avatar.url != user.avatar.url else None)
            button_serverpfp = discord.ui.Button(label="伺服器頭像連結", url=user.display_avatar.url)
            view.add_item(button_serverpfp)
    await interaction.response.send_message(embed=embed, view=view)


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
    # avatar url button
    button = discord.ui.Button(label="頭像連結", url=user.avatar.url if user.avatar else "https://discord.com/assets/6debd47ed13483642cf09e832ed0bc1b.png")
    view = discord.ui.View()
    view.add_item(button)
    embed.add_field(name="用戶 ID", value=str(user.id), inline=True)
    embed.add_field(name="帳號創建時間", value=user.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    if isinstance(user, discord.Member):
        embed.add_field(name="伺服器暱稱", value=user.nick or "無", inline=True)
        embed.add_field(name="加入伺服器時間", value=user.joined_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
        # pfp
        if user.display_avatar and user.display_avatar.url != user.avatar.url:
            embed.set_image(url=user.display_avatar.url if user.display_avatar.url != user.avatar.url else None)
            button_serverpfp = discord.ui.Button(label="伺服器頭像連結", url=user.display_avatar.url)
            view.add_item(button_serverpfp)
    await ctx.send(embed=embed, view=view)


@bot.tree.command(name=app_commands.locale_str("serverinfo"), description="顯示目前所在伺服器資訊")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def serverinfo_command(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("此指令只能在伺服器中使用。", ephemeral=True)
        return

    embed = discord.Embed(title=f"{guild.name} 的資訊", color=0x00ff00)
    view = discord.ui.View()
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        iconbutton = discord.ui.Button(label="伺服器圖標連結", url=guild.icon.url)
        view.add_item(iconbutton)
    if guild.banner:
        embed.set_image(url=guild.banner.url if guild.banner else None)
        bannerbutton = discord.ui.Button(label="伺服器橫幅連結", url=guild.banner.url)
        view.add_item(bannerbutton)
    embed.add_field(name="伺服器 ID", value=str(guild.id), inline=True)
    embed.add_field(name="創建時間", value=f"<t:{int(guild.created_at.timestamp())}:F>", inline=True)
    embed.add_field(name="擁有者", value=guild.owner.mention if guild.owner else "未知", inline=True)
    embed.add_field(name="加成", value=f"{guild.premium_subscription_count} (等級{guild.premium_tier})", inline=True)
    embed.add_field(
        name="驗證等級",
        value={
            "none": "無",
            "low": "低",
            "medium": "中等",
            "high": "高",
            "highest": "最高"
        }
        .get(
                guild.verification_level.name.lower(), "none"
            ),
        inline=True
    )
    embed.add_field(name="地區", value=str(guild.preferred_locale), inline=True)
    embed.add_field(name="成員數量", value=str(guild.member_count), inline=True)
    embed.add_field(name="頻道數量", value=str(len(guild.channels)), inline=True)
    embed.add_field(name="身分組數量", value=str(len(guild.roles)), inline=True)
    await interaction.response.send_message(embed=embed, view=view)

@bot.command(aliases=["si"])
async def serverinfo(ctx: commands.Context):
    """顯示目前所在伺服器資訊
    
    用法： serverinfo
    """
    guild = ctx.guild
    if guild is None:
        await ctx.send("此指令只能在伺服器中使用。")
        return

    embed = discord.Embed(title=f"{guild.name} 的資訊", color=0x00ff00)
    view = discord.ui.View()
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        iconbutton = discord.ui.Button(label="伺服器圖標連結", url=guild.icon.url)
        view.add_item(iconbutton)
    if guild.banner:
        embed.set_image(url=guild.banner.url if guild.banner else None)
        bannerbutton = discord.ui.Button(label="伺服器橫幅連結", url=guild.banner.url)
        view.add_item(bannerbutton)
    embed.add_field(name="伺服器 ID", value=str(guild.id), inline=True)
    embed.add_field(name="創建時間", value=f"<t:{int(guild.created_at.timestamp())}:F>", inline=True)
    embed.add_field(name="擁有者", value=guild.owner.mention if guild.owner else "未知", inline=True)
    embed.add_field(name="加成", value=f"{guild.premium_subscription_count} (等級{guild.premium_tier})", inline=True)
    embed.add_field(
        name="驗證等級",
        value={
            "none": "無",
            "low": "低",
            "medium": "中等",
            "high": "高",
            "highest": "最高"
        }
        .get(
                guild.verification_level.name.lower(), "none"
            ),
        inline=True
    )
    embed.add_field(name="地區", value=str(guild.preferred_locale), inline=True)
    embed.add_field(name="成員數量", value=str(guild.member_count), inline=True)
    embed.add_field(name="頻道數量", value=str(len(guild.channels)), inline=True)
    embed.add_field(name="身分組數量", value=str(len(guild.roles)), inline=True)
    await ctx.send(embed=embed, view=view)

@bot.tree.command(name=app_commands.locale_str("avatar"), description="取得用戶頭像")
@app_commands.describe(user="要查詢的用戶")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def avatar_command(interaction: discord.Interaction, user: Union[discord.User, discord.Member] = None):
    if user is None:
        user = interaction.user
    embed = discord.Embed(title=f"{user.display_name} 的頭像", color=0x00ff00)
    view = discord.ui.View()
    if user.display_avatar and user.display_avatar.url != user.avatar.url:
        embed.set_image(url=user.display_avatar.url)
        embed.set_thumbnail(url=user.avatar.url if user.avatar else None)
        serverpfp_button = discord.ui.Button(label="伺服器頭像連結", url=user.display_avatar.url)
        view.add_item(serverpfp_button)
    else:
        embed.set_image(url=user.avatar.url if user.avatar else None)
    button = discord.ui.Button(label="頭像連結", url=user.avatar.url if user.avatar else "https://discord.com/assets/6debd47ed13483642cf09e832ed0bc1b.png")
    view.add_item(button)
    await interaction.response.send_message(embed=embed, view=view)


@bot.command(aliases=["pfp"])
async def avatar(ctx: commands.Context, user: Union[discord.User, discord.Member] = None):
    """取得用戶頭像
    
    用法： avatar [用戶]
    如果不指定用戶，則顯示自己的頭像。
    """
    if user is None:
        user = ctx.author
    embed = discord.Embed(title=f"{user.display_name} 的頭像", color=0x00ff00)
    view = discord.ui.View()
    if user.display_avatar and user.display_avatar.url != user.avatar.url:
        embed.set_image(url=user.display_avatar.url)
        embed.set_thumbnail(url=user.avatar.url if user.avatar else None)
        serverpfp_button = discord.ui.Button(label="伺服器頭像連結", url=user.display_avatar.url)
        view.add_item(serverpfp_button)
    else:
        embed.set_image(url=user.avatar.url if user.avatar else None)
    button = discord.ui.Button(label="頭像連結", url=user.avatar.url if user.avatar else "https://discord.com/assets/6debd47ed13483642cf09e832ed0bc1b.png")
    view.add_item(button)
    await ctx.send(embed=embed, view=view)


@bot.tree.command(name=app_commands.locale_str("banner"), description="取得用戶橫幅")
@app_commands.describe(user="要查詢的用戶")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def banner_command(interaction: discord.Interaction, user: Union[discord.User, discord.Member] = None):
    if user is None:
        user = interaction.user
    user = await bot.fetch_user(user.id)  # Fetch to get banner
    if user.banner is None:
        await interaction.response.send_message("該用戶沒有設定橫幅。", ephemeral=True)
        return
    embed = discord.Embed(title=f"{user.display_name} 的橫幅", color=0x00ff00)
    embed.set_image(url=user.banner.url)
    view = discord.ui.View()
    button = discord.ui.Button(label="橫幅連結", url=user.banner.url)
    view.add_item(button)
    await interaction.response.send_message(embed=embed, view=view)


@bot.command(aliases=["bnr"])
async def banner(ctx: commands.Context, user: Union[discord.User, discord.Member] = None):
    """取得用戶橫幅
    
    用法： banner [用戶]
    如果不指定用戶，則顯示自己的橫幅。
    """
    if user is None:
        user = ctx.author
    user = await bot.fetch_user(user.id)  # Fetch to get banner
    if user.banner is None:
        await ctx.send("該用戶沒有設定橫幅。")
        return
    embed = discord.Embed(title=f"{user.display_name} 的橫幅", color=0x00ff00)
    embed.set_image(url=user.banner.url)
    view = discord.ui.View()
    button = discord.ui.Button(label="橫幅連結", url=user.banner.url)
    view.add_item(button)
    await ctx.send(embed=embed, view=view)


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


@bot.tree.command(name=app_commands.locale_str("changelogs"), description="顯示機器人更新日誌")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def changelogs_command(interaction: discord.Interaction):
    # get 10 commit logs
    commit_logs = get_commit_logs(10)
    embed = discord.Embed(title="機器人更新日誌", description="\n".join(commit_logs), color=0x00ff00)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name=app_commands.locale_str("ping"), description="檢查機器人延遲")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def ping_command(interaction: discord.Interaction):
    try:
        bot_latency = round(bot.latency * 1000, 2)  # Convert to milliseconds
    except OverflowError:
        bot_latency = "N/A"
    s = time.perf_counter()
    await interaction.response.defer()
    e = time.perf_counter()
    rest_latency = round((e - s) * 1000, 2)  # in milliseconds
    embed = discord.Embed(title="機器人延遲", color=0x00ff00)
    embed.add_field(name="Websocket 延遲", value=f"{bot_latency}ms")
    embed.add_field(name="REST API 延遲", value=f"{rest_latency}ms")
    await interaction.followup.send(embed=embed)


@bot.command(aliases=["pg"])
async def ping(ctx: commands.Context):
    """檢查機器人延遲
    
    用法： ping
    """
    try:
        bot_latency = round(bot.latency * 1000, 2)  # Convert to milliseconds
    except OverflowError:
        bot_latency = "N/A"
    s = time.perf_counter()
    await ctx.trigger_typing()
    e = time.perf_counter()
    rest_latency = round((e - s) * 1000, 2)  # in milliseconds
    embed = discord.Embed(title="機器人延遲", color=0x00ff00)
    embed.add_field(name="Websocket 延遲", value=f"{bot_latency}ms")
    embed.add_field(name="REST API 延遲", value=f"{rest_latency}ms")
    await ctx.send(embed=embed)


class NitroLinkModal(discord.ui.Modal, title="發送 Nitro 禮物"):
    nitro_link = discord.ui.TextInput(
        label="Nitro 連結", 
        placeholder="https://discord.gift/...", 
        style=discord.TextStyle.short, 
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        link = self.nitro_link.value.strip()
        
        if not link.startswith("https://discord.gift/"):
            await interaction.response.send_message("❌ 錯誤：這不是有效的 Nitro 連結格式。", ephemeral=True)
            return

        # 延遲回應，避免 API 請求超時
        await interaction.response.defer()

        code = link.split('/')[-1]
        api_url = f"https://discord.com/api/v9/entitlements/gift-codes/{code}?with_application=false&with_subscription_plan=true"

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # 檢查是否已被領取
                    is_redeemed = data.get("uses", 0) >= data.get("max_uses", 1)
                    if is_redeemed:
                        await interaction.followup.send("⚠️ 此連結已被使用過。", ephemeral=True)
                        return

                    # 準備顯示用的資訊
                    gift_name = data.get("subscription_plan", {}).get("name", "Discord Nitro")
                    expires_raw = data.get("expires_at")
                    gifter = bot.get_user(int(data.get("user", {}).get("id", 0)))
                    
                    embed = discord.Embed(title=f"{gift_name}", color=0xFF73FA)
                    embed.description = "有人送出了一份禮物！點擊下方按鈕領取。"
                    embed.set_author(name=gifter.display_name if gifter else "未知用戶", icon_url=gifter.display_avatar.url if gifter else None)
                    embed.set_footer(text="啊我就不想要被Selfbot幹走尼戳")
                    
                    if expires_raw:
                        expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
                        embed.add_field(name="到期時間", value=f"<t:{int(expires_at.timestamp())}:R>")

                    # 建立按鈕 View 並把連結傳進去
                    view = NitroClaimView(link, gift_name)
                    
                    # 在頻道發送公開訊息（非 ephemeral），讓大家搶
                    await interaction.followup.send(embed=embed, view=view)
                    await interaction.followup.send("✅ 禮物已成功發送至頻道！", ephemeral=True)
                else:
                    await interaction.followup.send("❌ 無法驗證此連結，請檢查是否輸入正確。", ephemeral=True)

class NitroClaimView(discord.ui.View):
    def __init__(self, link: str, gift_name: str):
        super().__init__(timeout=None) # 永不到期或自訂時間
        self.link = link
        self.gift_name = gift_name
        self.claimed = False

    @discord.ui.button(label="領取", style=discord.ButtonStyle.primary, emoji="🎉")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.claimed:  # avoid edit message delay
            await interaction.response.send_message("⚠️ 此禮物已被領取。", ephemeral=True)
            return
        self.claimed = True
        # 禁用所有按鈕防止重複點擊
        for child in self.children:
            child.disabled = True
        
        # 更新原訊息
        embed = interaction.message.embeds[0]
        embed.title = f"{self.gift_name} [已領取]"
        embed.color = discord.Color.light_grey()
        embed.set_footer(text=f"領取者: {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        
        await interaction.message.edit(embed=embed, view=self)
        
        # 私訊領取者連結
        await interaction.response.send_message(f"🎊 這是你的 Nitro 連結：\n{self.link}", ephemeral=True)
        self.stop()


@bot.tree.command(name=app_commands.locale_str("nitro"), description="我不想要被機器人幹走尼戳")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def nitro_command(interaction: discord.Interaction):
    await interaction.response.send_modal(NitroLinkModal())


if __name__ == "__main__":
    start_bot()
