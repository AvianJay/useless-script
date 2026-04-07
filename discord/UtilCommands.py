import os
import sys
import re
import io
import random
import discord
from discord import app_commands
from discord.ext import commands
from globalenv import bot, start_bot, get_user_data, set_user_data, get_command_mention, modules, failed_modules, config, get_global_config
from CustomPrefix import get_prefix
from typing import Union
from datetime import datetime, timezone
import psutil
import time
import aiohttp
from database import db
from CustomPrefix import get_prefix

startup_time = datetime.now(timezone.utc)
version = "0.20.0"
try:
    git_commit_hash = os.popen("git rev-parse --short HEAD").read().strip()
except Exception as e:
    git_commit_hash = "unknown"
full_version = f"{version} ({git_commit_hash})"


def get_commit_logs(limit=10) -> str:
    try:
        logs = os.popen(f"git log -n {limit} \"--pretty=format:%an: %h - %s (%cr)\"").read().strip().split("\n")
        return logs
    except Exception as e:
        return ["無法取得提交記錄。"]


def parse_changelog() -> list[dict]:
    """解析 changelog.md 並返回版本列表"""
    try:
        changelog_path = os.path.join(os.path.dirname(__file__), "changelog.md")
        with open(changelog_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return []
    
    versions = []
    current_version = None
    current_content = []
    
    for line in content.split("\n"):
        if line.startswith("## "):
            # 新版本開始
            if current_version:
                versions.append({
                    "version": current_version,
                    "content": "\n".join(current_content).strip()
                })
            current_version = line[3:].strip()
            current_content = []
        elif current_version:
            current_content.append(line)
    
    # 添加最後一個版本
    if current_version:
        versions.append({
            "version": current_version,
            "content": "\n".join(current_content).strip()
        })
    
    return versions


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
@app_commands.describe(
    full="是否顯示完整模組列表與載入失敗模組"
)
async def info_command(interaction: discord.Interaction, full: bool = False):
    await interaction.response.defer()
    server_count = len(bot.guilds)
    user_count = f"{len(bot.users)}/{sum(guild.member_count for guild in bot.guilds)}"
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
    embed.add_field(name="指令數量", value=f"{commands_count + app_commands_count} ({commands_count} 文字, {app_commands_count} 應用)")
    embed.add_field(name="伺服器數量", value=server_count)
    embed.add_field(name="用戶總數量", value=user_count)
    embed.add_field(name="用戶安裝數量", value=bot.application.approximate_user_install_count or "N/A")
    embed.add_field(name="機器人延遲", value=f"{bot_latency}ms")
    embed.add_field(name="CPU 使用率", value=f"{psutil.cpu_percent()}%")
    embed.add_field(name="記憶體使用率", value=f"{psutil.virtual_memory().percent}%")
    embed.add_field(name="Discord.py 版本", value=discord.__version__)
    embed.add_field(name="Python 版本", value=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    embed.add_field(name="指令使用次數", value=f"{sum(get_global_config('command_usage_stats', {}).values()) + sum(get_global_config('app_command_usage_stats', {}).values()) + sum(get_global_config('command_error_stats', {}).values()) + sum(get_global_config('app_command_error_stats', {}).values())}", inline=False)
    embed.add_field(name="運行時間", value=uptime)
    embed.add_field(name="資料庫資訊", value=f"總筆數: {dbcount['total']}\n伺服器筆數: {dbcount['server_configs']}\n用戶資料筆數: {dbcount['user_data']}", inline=True)
    if full:
        embed.add_field(name=f"已載入模組({len(modules)})", value="\n".join(modules) if modules else "無", inline=False)
        if config("disable_modules", []):
            embed.add_field(name=f"已禁用模組({len(config('disable_modules', []))})", value="\n".join(config("disable_modules", [])), inline=False)
        if failed_modules:
            embed.add_field(name=f"載入失敗的模組({len(failed_modules)})", value="\n".join(failed_modules), inline=False)
    else:
        embed.add_field(name=f"已載入模組數量", value=str(len(modules)), inline=False)
        if config("disable_modules", []):
            embed.add_field(name=f"已禁用模組數量", value=str(len(config("disable_modules", []))), inline=False)
        if failed_modules:
            embed.add_field(name=f"載入失敗的模組數量", value=str(len(failed_modules)), inline=False)
    embed.add_field(name="相關連結", value=f"* [機器人網站]({config('website_url')})\n* [使用文檔]({config('website_url')}/docs)\n* [支援伺服器]({config('support_server_invite')})\n* [隱私政策]({config('website_url')}/privacy-policy)\n* [服務條款]({config('website_url')}/terms-of-service)\n* [邀請機器人](https://discord.com/oauth2/authorize?client_id={str(bot.user.id)})", inline=False)
    embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None)
    embed.set_footer(text="by AvianJay")
    await interaction.followup.send(content="-# 提示：如果你指令用到一半停住或沒辦法用了那很有可能是那個傻逼開發者||尼摳||又再重開機器人了||不然就是機器人又當機了||", embed=embed)


@bot.command(aliases=["botinfo", "bi"])
async def info(ctx: commands.Context, full: bool = False):
    """顯示機器人資訊
    
    用法： info [full]

    如果指定 full 參數為 True，則顯示完整模組列表與載入失敗模組。
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
    embed.add_field(name="指令數量", value=f"{commands_count + app_commands_count} ({commands_count} 文字, {app_commands_count} 應用)")
    embed.add_field(name="伺服器數量", value=server_count)
    embed.add_field(name="用戶總數量", value=user_count)
    embed.add_field(name="用戶安裝數量", value=bot.application.approximate_user_install_count or "N/A")
    embed.add_field(name="機器人延遲", value=f"{bot_latency}ms")
    embed.add_field(name="CPU 使用率", value=f"{psutil.cpu_percent()}%")
    embed.add_field(name="記憶體使用率", value=f"{psutil.virtual_memory().percent}%")
    embed.add_field(name="Discord.py 版本", value=discord.__version__)
    embed.add_field(name="Python 版本", value=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    embed.add_field(name="指令使用次數", value=f"{sum(get_global_config('command_usage_stats', {}).values()) + sum(get_global_config('app_command_usage_stats', {}).values()) + sum(get_global_config('command_error_stats', {}).values()) + sum(get_global_config('app_command_error_stats', {}).values())}", inline=False)
    embed.add_field(name="運行時間", value=uptime)
    embed.add_field(name="資料庫資訊", value=f"總筆數: {dbcount['total']}\n伺服器筆數: {dbcount['server_configs']}\n用戶資料筆數: {dbcount['user_data']}", inline=True)
    if full:
        embed.add_field(name=f"已載入模組({len(modules)})", value="\n".join(modules) if modules else "無", inline=False)
        if config("disable_modules", []):
            embed.add_field(name=f"已禁用模組({len(config('disable_modules', []))})", value="\n".join(config("disable_modules", [])), inline=False)
        if failed_modules:
            embed.add_field(name=f"載入失敗的模組({len(failed_modules)})", value="\n".join(failed_modules), inline=False)
    else:
        embed.add_field(name=f"已載入模組數量", value=str(len(modules)), inline=False)
        if config("disable_modules", []):
            embed.add_field(name=f"已禁用模組數量", value=str(len(config("disable_modules", []))), inline=False)
        if failed_modules:
            embed.add_field(name=f"載入失敗的模組數量", value=str(len(failed_modules)), inline=False)
    embed.add_field(name="相關連結", value=f"* [機器人網站]({config('website_url')})\n* [使用文檔]({config('website_url')}/docs)\n* [支援伺服器]({config('support_server_invite')})\n* [隱私政策]({config('website_url')}/privacy-policy)\n* [服務條款]({config('website_url')}/terms-of-service)\n* [邀請機器人](https://discord.com/oauth2/authorize?client_id={str(bot.user.id)})", inline=False)
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
    await interaction.response.send_message(f"隨機選擇的用戶是：{selected_user.mention}！\n-# 抽取用戶總數：{len(users)}", allowed_mentions=discord.AllowedMentions(users=mention, roles=False, everyone=False))


@bot.tree.command(name=app_commands.locale_str("userinfo"), description="顯示用戶資訊")
@app_commands.describe(user="要查詢的用戶")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def userinfo_command(interaction: discord.Interaction, user: Union[discord.User, discord.Member]):
    embed = discord.Embed(title=f"{user.display_name} 的資訊", color=0x00ff00)
    embed.set_thumbnail(url=user.avatar.url if user.avatar else None)
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
    embed.set_thumbnail(url=user.avatar.url if user.avatar else None)
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


async def command_autocomplete(interaction: discord.Interaction, current: str):
    commands_list = []
    for cmd in bot.tree.get_commands():
        commands_list.append(cmd.name)
    return [
        app_commands.Choice(name=cmd, value=cmd)
        for cmd in commands_list if current.lower() in cmd.lower()
    ][:25]


async def subcommand_autocomplete(interaction: discord.Interaction, current: str):
    command_name = interaction.namespace.command
    command = bot.tree.get_command(command_name)
    subcommands_list = []
    if command and isinstance(command, app_commands.Group):
        for subcmd in command.commands:
            if isinstance(subcmd, app_commands.Command):
                subcommands_list.append(subcmd.name)
    return [
        app_commands.Choice(name=subcmd, value=subcmd)
        for subcmd in subcommands_list if current.lower() in subcmd.lower()
    ][:25]


@bot.tree.command(name=app_commands.locale_str("get-command-mention"), description="取得指令的提及格式")
@app_commands.describe(command="指令名稱", subcommand="子指令名稱（可選）")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.autocomplete(command=command_autocomplete, subcommand=subcommand_autocomplete)
async def get_cmd_mention(interaction: discord.Interaction, command: str, subcommand: str = None):
    mention = await get_command_mention(command, subcommand)
    if mention is None:
        await interaction.response.send_message("找不到指定的指令。", ephemeral=True)
        return
    await interaction.response.send_message(f"{mention}", allowed_mentions=discord.AllowedMentions.none())


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


@bot.tree.command(name=app_commands.locale_str("git-commits"), description="顯示機器人的 git 提交記錄")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def changelogs_command(interaction: discord.Interaction):
    # get 10 commit logs
    commit_logs = get_commit_logs(10)
    embed = discord.Embed(title="機器人 git 提交記錄", description="\n".join(commit_logs), color=0x00ff00)
    await interaction.response.send_message(embed=embed)


class ChangeLogView(discord.ui.View):
    def __init__(self, versions: list[dict], current_page: int = 0, interaction: discord.Interaction = None):
        super().__init__(timeout=300)  # 5 minutes timeout
        self.versions = versions
        self.current_page = current_page
        self.interaction = interaction
        self.time = datetime.now(timezone.utc)
        self.update_buttons()
    
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        # Disable buttons when timeout
        await self.interaction.edit_original_response(view=self)
    
    def update_buttons(self):
        self.prev_button.disabled = self.current_page <= 0
        self.next_button.disabled = self.current_page >= len(self.versions) - 1
    
    def get_embed(self) -> discord.Embed:
        if not self.versions:
            return discord.Embed(title="更新日誌", description="無法取得更新日誌。", color=0xff0000)
        
        version_data = self.versions[self.current_page]
        embed = discord.Embed(
            title=f"更新日誌 - {version_data['version']}",
            description=version_data['content'][:4096] if version_data['content'] else "無更新內容。",
            color=0x00ff00
        )
        embed.set_footer(text=f"頁數：{self.current_page + 1}/{len(self.versions)}")
        embed.timestamp = self.time
        return embed

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.primary, custom_id="changelog_prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)
    
    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.primary, custom_id="changelog_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)


@bot.tree.command(name=app_commands.locale_str("changelog"), description="顯示機器人更新日誌")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def changelog_command(interaction: discord.Interaction):
    versions = parse_changelog()
    if not versions:
        await interaction.response.send_message("無法取得更新日誌。", ephemeral=True)
        return
    
    view = ChangeLogView(versions, interaction=interaction)
    await interaction.response.send_message(embed=view.get_embed(), view=view)


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
    await ctx.typing()
    e = time.perf_counter()
    rest_latency = round((e - s) * 1000, 2)  # in milliseconds
    embed = discord.Embed(title="機器人延遲", color=0x00ff00)
    embed.add_field(name="Websocket 延遲", value=f"{bot_latency}ms")
    embed.add_field(name="REST API 延遲", value=f"{rest_latency}ms")
    await ctx.send(embed=embed)


class NitroLinkModal(discord.ui.Modal, title="發送 Nitro 禮物"):
    def __init__(self, need_message: bool = False):
        super().__init__()
        self.need_message = need_message
        self.author_ids = None  # 用於存儲有發過訊息的用戶 ID

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
                    embed.set_author(name=f"{gifter.display_name} ({gifter.name})" if gifter else "未知用戶", icon_url=gifter.display_avatar.url if gifter else None)
                    embed.set_footer(text="尚未被領取。")
                    
                    if expires_raw:
                        expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
                        embed.add_field(name="到期時間", value=f"<t:{int(expires_at.timestamp())}:R>")

                    warn_message = ""
                    if self.need_message:
                        if interaction.is_guild_integration():
                            # try to read recent 50 messages and check if interaction user is in them
                            channel = interaction.channel
                            # check bot has permission to read message history
                            if channel.permissions_for(interaction.guild.me).read_message_history:
                                messages = [msg async for msg in channel.history(limit=50)]
                                authors = set(msg.author.id for msg in messages)
                                self.author_ids = authors
                                embed.add_field(name="領取限制", value="只有近期在此頻道發過訊息的用戶才能領取。")
                            else:
                                warn_message = "\n⚠️ 請注意：機器人沒有讀取訊息歷史的權限，無法驗證用戶是否發過訊息。所有人都可以領取此禮物。"
                        else:
                            warn_message = "\n⚠️ 請注意：使用者安裝不支援驗證用戶是否發過訊息。所有人都可以領取此禮物。"

                    # 建立按鈕 View 並把連結傳進去
                    view = NitroClaimView(link, gift_name, need_message=self.need_message, author_ids=self.author_ids)

                    # 在頻道發送公開訊息（非 ephemeral），讓大家搶
                    await interaction.followup.send(embed=embed, view=view)
                    await interaction.followup.send(f"✅ 禮物已成功發送至頻道！{warn_message}", ephemeral=True)
                else:
                    await interaction.followup.send("❌ 無法驗證此連結，請檢查是否輸入正確。", ephemeral=True)

class NitroClaimView(discord.ui.View):
    def __init__(self, link: str, gift_name: str, need_message: bool = False, author_ids: set[int] = None):
        super().__init__(timeout=None) # 永不到期或自訂時間
        self.link = link
        self.gift_name = gift_name
        self.need_message = need_message
        self.author_ids = author_ids
        self.claimed = False

    @discord.ui.button(label="領取", style=discord.ButtonStyle.primary, emoji="🎉")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.claimed:  # avoid edit message delay
            await interaction.response.send_message("⚠️ 此禮物已被領取。", ephemeral=True)
            return
        if self.need_message and self.author_ids is not None:
            if interaction.user.id not in self.author_ids:
                await interaction.response.send_message("❌ 你需要在這個頻道發過訊息才能使用這個禮物。", ephemeral=True)
                return
        self.claimed = True
        # 禁用所有按鈕防止重複點擊
        for child in self.children:
            child.disabled = True
        
        # 更新原訊息
        embed = interaction.message.embeds[0]
        embed.title = f"{self.gift_name} [已領取]"
        embed.color = discord.Color.light_grey()
        embed.set_footer(text=f"領取者: {interaction.user.display_name} ({interaction.user.name})", icon_url=interaction.user.display_avatar.url)
        
        await interaction.response.edit_message(embed=embed, view=self)
        
        # 私訊領取者連結
        await interaction.followup.send(f"🎊 這是你的 Nitro 連結：\n{self.link}", ephemeral=True)
        self.stop()


@bot.tree.command(name=app_commands.locale_str("nitro"), description="我不想要被機器人幹走尼戳")
@app_commands.describe(
    need_message="是否只有近期發過訊息的用戶才能領取（最近 50 條訊息的作者）"
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def nitro_command(interaction: discord.Interaction, need_message: bool = False):
    await interaction.response.send_modal(NitroLinkModal(need_message=need_message))


# get sticker context command
@bot.command(aliases=["stickerinfo", "sticker", "sti"])
async def sticker_info(ctx: commands.Context):
    """顯示貼圖資訊
    用法： sticker_info/<回覆貼圖訊息>
    """
    if ctx.message.reference:
        replied_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        if not replied_message.stickers:
            await ctx.send("此訊息沒有貼圖。")
            return
        sticker = replied_message.stickers[0]
    elif not ctx.message.stickers:
        await ctx.send("此訊息沒有貼圖。")
        return
    else:
        sticker = ctx.message.stickers[0]
    embed = discord.Embed(title=f"貼圖資訊 - {sticker.name}", color=0x00ff00)
    embed.set_image(url=sticker.url)
    embed.add_field(name="貼圖 ID", value=str(sticker.id), inline=True)
    embed.add_field(name="貼圖格式", value=sticker.format.name, inline=True)
    btn = discord.ui.Button(label="貼圖連結", url=sticker.url)
    view = discord.ui.View()
    view.add_item(btn)
    await ctx.reply(embed=embed, view=view)

class StealView(discord.ui.View):
    def __init__(self, *, timeout: float | None = None):
        super().__init__(timeout=timeout)

    @discord.ui.button(label="偷", style=discord.ButtonStyle.primary, emoji="💾", custom_id="steal")
    async def download_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if not interaction.guild:
            await interaction.followup.send("此操作只能在伺服器中使用。", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_emojis_and_stickers:
            await interaction.followup.send("你需要管理表情和貼圖的權限才能使用此功能。", ephemeral=True)
            return
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed.author.name == "表情符號資訊":
            emoji = True
            target = "表情符號"
        elif embed.author.name == "貼圖資訊":
            emoji = False
            target = "貼圖"
        if not embed or not embed.image:
            await interaction.followup.send(f"無法找到{target}圖片。", ephemeral=True)
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(embed.image.url) as resp:
                    image_bytes = await resp.read()
            if emoji:
                await interaction.guild.create_custom_emoji(
                    name=embed.title,
                    image=image_bytes,
                )
            else:
                await interaction.guild.create_sticker(
                    name=embed.title,
                    emoji=embed.title,
                    file=discord.File(fp=io.BytesIO(image_bytes), filename=f"{embed.title}.{embed.fields[1].value.lower()}"),
                )
            await interaction.followup.send(f"✅ 已成功將{target} '{embed.title}' 加入到伺服器！", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 無法加入{target}：{e}", ephemeral=True)

_CUSTOM_EMOJI_RE = re.compile(r'<(a?):(\w+):(\d+)>')
_MAX_EMOJI_INFO_RESULTS = 10

def _append_custom_emojis(
    content: str,
    emojis: list[discord.PartialEmoji],
    seen_ids: set[int],
    limit: int = _MAX_EMOJI_INFO_RESULTS,
) -> None:
    for match in _CUSTOM_EMOJI_RE.finditer(content or ""):
        emoji_id = int(match.group(3))
        if emoji_id in seen_ids:
            continue

        emojis.append(
            discord.PartialEmoji(
                animated=match.group(1) == "a",
                name=match.group(2),
                id=emoji_id,
            )
        )
        seen_ids.add(emoji_id)

        if len(emojis) >= limit:
            return


def _collect_custom_emojis_from_message(
    message: discord.Message,
    limit: int = _MAX_EMOJI_INFO_RESULTS,
) -> list[discord.PartialEmoji]:
    emojis: list[discord.PartialEmoji] = []
    seen_ids: set[int] = set()

    _append_custom_emojis(message.content, emojis, seen_ids, limit=limit)
    if len(emojis) >= limit:
        return emojis

    for snapshot in message.message_snapshots or []:
        _append_custom_emojis(snapshot.content, emojis, seen_ids, limit=limit)
        if len(emojis) >= limit:
            break

    return emojis


class EmojiInfoView(StealView):
    def __init__(
        self,
        emojis: list[discord.PartialEmoji],
        interaction: discord.Interaction,
        *,
        allow_steal: bool,
    ):
        super().__init__(timeout=300)
        self.emojis = emojis[:_MAX_EMOJI_INFO_RESULTS]
        self.current_page = 0
        self.original_interaction = interaction
        self.link_button = discord.ui.Button(
            label="表情符號連結",
            url=str(self.emojis[0].url),
            row=0,
        )
        self.add_item(self.link_button)

        if not allow_steal:
            self.remove_item(self.download_button)

        self.update_buttons()

    @property
    def current_emoji(self) -> discord.PartialEmoji:
        return self.emojis[self.current_page]

    def get_embed(self) -> discord.Embed:
        emoji = self.current_emoji
        embed = discord.Embed(title=f"{emoji.name}", color=0x00ff00)
        embed.set_author(name="表情符號資訊")
        embed.set_image(url=str(emoji.url))
        embed.add_field(name="表情符號 ID", value=str(emoji.id), inline=True)
        embed.add_field(name="是否為動畫", value=str(emoji.animated), inline=True)
        embed.set_footer(text=f"表情符號 {self.current_page + 1} / {len(self.emojis)}")
        return embed

    def update_buttons(self):
        self.prev_button.disabled = self.current_page <= 0
        self.next_button.disabled = self.current_page >= len(self.emojis) - 1
        self.link_button.url = str(self.current_emoji.url)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.original_interaction.edit_original_response(view=self)
        except Exception:
            pass

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.primary, row=0)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.primary, row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.emojis) - 1, self.current_page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@bot.tree.context_menu(name="表情符號資訊")
async def emoji_info_context(interaction: discord.Interaction, message: discord.Message):
    emojis = _collect_custom_emojis_from_message(message)
    if not emojis:
        await interaction.response.send_message("此訊息沒有表情符號。", ephemeral=True)
        return
    view = EmojiInfoView(
        emojis,
        interaction=interaction,
        allow_steal=interaction.is_guild_integration(),
    )
    await interaction.response.send_message(embed=view.get_embed(), view=view)

# context menu for sticker info
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@bot.tree.context_menu(name="貼圖資訊")
async def sticker_info_context(interaction: discord.Interaction, message: discord.Message):
    if message.stickers:
        sticker = message.stickers[0]
    elif message.message_snapshots and message.message_snapshots[0].stickers:
        sticker = message.message_snapshots[0].stickers[0]
    else:
        await interaction.response.send_message("此訊息沒有貼圖。", ephemeral=True)
        return
    embed = discord.Embed(title=f"{sticker.name}", color=0x00ff00)
    embed.set_author(name=f"貼圖資訊")
    embed.set_image(url=sticker.url)
    embed.add_field(name="貼圖 ID", value=str(sticker.id), inline=True)
    embed.add_field(name="貼圖格式", value=sticker.format.name, inline=True)
    if interaction.is_guild_integration():
        view = StealView()
    else:
        view = discord.ui.View()
    btn = discord.ui.Button(label="貼圖連結", url=sticker.url)
    view.add_item(btn)
    await interaction.response.send_message(embed=embed, view=view)


class PrettyHelpCommand(commands.HelpCommand):
    """美化版的 Help Command"""
    
    def __init__(self):
        super().__init__(
            command_attrs={
                'help': '顯示所有指令或特定指令的幫助訊息',
                'aliases': ['h', '?', 'commands']
            }
        )
    
    def get_command_signature(self, command: commands.Command) -> str:
        """取得指令的使用格式"""
        return f"{self.context.clean_prefix}{command.qualified_name} {command.signature}"
    
    async def send_bot_help(self, mapping):
        """顯示所有指令的總覽"""
        embed = discord.Embed(
            title="📚 指令幫助",
            description=f"使用 `{self.context.clean_prefix}help <指令>` 查看特定指令的詳細說明",
            color=0x5865F2
        )
        embed.set_thumbnail(url=self.context.bot.user.avatar.url if self.context.bot.user.avatar else None)
        
        for cog, cmds in mapping.items():
            filtered = await self.filter_commands(cmds, sort=True)
            if filtered:
                cog_name = cog.qualified_name if cog else "🔧 其他指令"
                # 加上 emoji
                if cog:
                    cog_name = f"📦 {cog_name}"
                
                command_list = " ".join([f"`{cmd.name}`" for cmd in filtered])
                if command_list:
                    embed.add_field(
                        name=cog_name,
                        value=command_list,
                        inline=False
                    )
        
        embed.set_footer(text=f"共 {len(self.context.bot.commands)} 個文字指令 | by AvianJay")
        
        channel = self.get_destination()
        await channel.send(embed=embed)
    
    async def send_cog_help(self, cog: commands.Cog):
        """顯示特定 Cog 的指令"""
        embed = discord.Embed(
            title=f"📦 {cog.qualified_name}",
            description=cog.description or "無描述",
            color=0x5865F2
        )
        
        filtered = await self.filter_commands(cog.get_commands(), sort=True)
        for command in filtered:
            embed.add_field(
                name=f"`{self.get_command_signature(command)}`",
                value=command.short_doc or "無描述",
                inline=False
            )
        
        embed.set_footer(text=f"使用 {self.context.clean_prefix}help <指令> 查看詳細說明")
        
        channel = self.get_destination()
        await channel.send(embed=embed)
    
    async def send_group_help(self, group: commands.Group):
        """顯示群組指令的幫助"""
        embed = discord.Embed(
            title=f"📁 {group.qualified_name}",
            description=group.help or "無描述",
            color=0x5865F2
        )
        
        embed.add_field(
            name="使用方法",
            value=f"`{self.get_command_signature(group)}`",
            inline=False
        )
        
        if group.aliases:
            embed.add_field(
                name="別名",
                value=" ".join([f"`{alias}`" for alias in group.aliases]),
                inline=False
            )
        
        filtered = await self.filter_commands(group.commands, sort=True)
        if filtered:
            subcommands = "\n".join([
                f"`{self.context.clean_prefix}{cmd.qualified_name}` - {cmd.short_doc or '無描述'}"
                for cmd in filtered
            ])
            embed.add_field(
                name="子指令",
                value=subcommands,
                inline=False
            )
        
        channel = self.get_destination()
        await channel.send(embed=embed)
    
    async def send_command_help(self, command: commands.Command):
        """顯示單一指令的幫助"""
        embed = discord.Embed(
            title=f"📝 {command.qualified_name}",
            description=command.help or "無描述",
            color=0x5865F2
        )
        
        embed.add_field(
            name="使用方法",
            value=f"`{self.get_command_signature(command)}`",
            inline=False
        )
        
        if command.aliases:
            embed.add_field(
                name="別名",
                value=" ".join([f"`{alias}`" for alias in command.aliases]),
                inline=True
            )
        
        # 顯示冷卻時間（如果有）
        if command._buckets and command._buckets._cooldown:
            cooldown = command._buckets._cooldown
            embed.add_field(
                name="冷卻時間",
                value=f"{cooldown.rate} 次 / {cooldown.per:.0f} 秒",
                inline=True
            )
        
        embed.set_footer(text=f"<> = 必填參數 | [] = 選填參數")
        
        channel = self.get_destination()
        await channel.send(embed=embed)
    
    async def send_error_message(self, error: str):
        """顯示錯誤訊息"""
        embed = discord.Embed(
            title="❌ 找不到指令",
            description=error,
            color=0xFF0000
        )
        embed.set_footer(text=f"使用 {self.context.clean_prefix}help 查看所有指令")
        
        channel = self.get_destination()
        await channel.send(embed=embed)


bot.help_command = PrettyHelpCommand()


async def can_run_text_command(command: commands.Command, interaction: discord.Interaction) -> bool:
    """檢查用戶是否可以執行文字指令"""
    if command.hidden:
        return False
    
    # 如果沒有檢查，直接返回 True
    if not command.checks:
        return True
    
    # 創建一個模擬的 Context 來檢查權限
    class FakeMessage:
        def __init__(self):
            self.author = interaction.user
            self.guild = interaction.guild
            self.channel = interaction.channel
            self.content = ""
            self.id = 0
    
    class FakeContext:
        def __init__(self):
            self.author = interaction.user
            self.guild = interaction.guild
            self.channel = interaction.channel
            self.bot = bot
            self.message = FakeMessage()
            self.command = command
    
    fake_ctx = FakeContext()
    
    try:
        # 嘗試運行所有檢查
        for check in command.checks:
            result = await discord.utils.maybe_coroutine(check, fake_ctx)
            if not result:
                return False
        return True
    except Exception:
        # 如果檢查失敗（例如權限不足），返回 False
        return False


async def help_command_autocomplete(interaction: discord.Interaction, current: str):
    """自動完成：列出所有可用指令"""
    commands_list = []
    
    # 斜線指令
    for cmd in bot.tree.get_commands():
        if isinstance(cmd, app_commands.Group):
            # 群組指令，加入子指令
            for subcmd in cmd.commands:
                commands_list.append({
                    "name": f"/{cmd.name} {subcmd.name}",
                    "value": f"app:{cmd.name} {subcmd.name}"
                })
        else:
            commands_list.append({
                "name": f"/{cmd.name}",
                "value": f"app:{cmd.name}"
            })
    
    # 文字指令
    for cmd in bot.commands:
        if isinstance(cmd, commands.Group):
            for subcmd in cmd.commands:
                # 檢查權限
                if await can_run_text_command(subcmd, interaction):
                    commands_list.append({
                        "name": f"!{cmd.name} {subcmd.name}",
                        "value": f"text:{cmd.name} {subcmd.name}"
                    })
        else:
            # 檢查權限
            if await can_run_text_command(cmd, interaction):
                commands_list.append({
                    "name": f"!{cmd.name}",
                    "value": f"text:{cmd.name}"
                })
    
    # 過濾並返回結果
    return [
        app_commands.Choice(name=cmd["name"], value=cmd["value"])
        for cmd in commands_list if current.lower() in cmd["name"].lower()
    ][:25]


class HelpPageView(discord.ui.View):
    def __init__(self, pages: list[discord.Embed], interaction: discord.Interaction):
        super().__init__(timeout=120)
        self.pages = pages
        self.current_page = 0
        self.interaction = interaction
        self.update_buttons()

    def update_buttons(self):
        self.prev_button.disabled = self.current_page <= 0
        self.next_button.disabled = self.current_page >= len(self.pages) - 1

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except Exception:
            pass

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.primary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)


@bot.tree.command(name=app_commands.locale_str("help"), description="顯示指令幫助與說明")
@app_commands.describe(command="要查詢的指令名稱")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.autocomplete(command=help_command_autocomplete)
async def help_slash_command(interaction: discord.Interaction, command: str = None):
    await interaction.response.defer()
    if command is None:
        help_mention = await get_command_mention('help')
        
        # 收集斜線指令
        app_cmds = []
        for cmd in bot.tree.get_commands():
            if isinstance(cmd, app_commands.Group):
                for subcmd in cmd.commands:
                    mention = await get_command_mention(cmd.name, subcmd.name)
                    app_cmds.append(mention or f"`/{cmd.name} {subcmd.name}`")
            elif isinstance(cmd, app_commands.Command):
                mention = await get_command_mention(cmd.name)
                app_cmds.append(mention or f"`/{cmd.name}`")
        
        # 收集文字指令
        text_cmds = []
        if interaction.is_guild_integration():
            for cmd in bot.commands:
                if not cmd.hidden:
                    if isinstance(cmd, commands.Group):
                        for subcmd in cmd.commands:
                            if await can_run_text_command(subcmd, interaction):
                                text_cmds.append(f"`{cmd.name} {subcmd.name}`")
                    else:
                        if await can_run_text_command(cmd, interaction):
                            text_cmds.append(f"`{cmd.name}`")
        
        # 建立分頁
        pages = []
        chunk_size = 15
        
        # 斜線指令分頁
        for i in range(0, max(len(app_cmds), 1), chunk_size):
            chunk = app_cmds[i:i + chunk_size]
            embed = discord.Embed(
                title="📚 指令幫助",
                description=f"使用 {help_mention} `<指令>` 查看特定指令的詳細說明",
                color=0x5865F2
            )
            embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None)
            if chunk:
                embed.add_field(
                    name=f"⚡ 斜線指令 ({i + 1}-{min(i + chunk_size, len(app_cmds))}/{len(app_cmds)})",
                    value=" ".join(chunk),
                    inline=False
                )
            pages.append(embed)
        
        # 文字指令分頁
        for i in range(0, max(len(text_cmds), 1), chunk_size):
            chunk = text_cmds[i:i + chunk_size]
            if not chunk:
                continue
            embed = discord.Embed(
                title="📚 指令幫助",
                description=f"使用 {help_mention} `<指令>` 查看特定指令的詳細說明",
                color=0x5865F2
            )
            embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None)
            embed.add_field(
                name=f"📝 文字指令 ({i + 1}-{min(i + chunk_size, len(text_cmds))}/{len(text_cmds)})",
                value=" ".join(chunk),
                inline=False
            )
            pages.append(embed)
        
        # 加上頁碼
        total_app = len(app_cmds)
        total_text = len(text_cmds)
        for idx, page in enumerate(pages):
            page.set_footer(text=f"頁數：{idx + 1}/{len(pages)} | 共 {total_app} 個斜線指令 | {total_text} 個文字指令 | by AvianJay")
        
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0])
        else:
            view = HelpPageView(pages, interaction)
            await interaction.followup.send(embed=pages[0], view=view)
        return
    
    # 解析指令類型
    if command.startswith("app:"):
        # 斜線指令
        cmd_parts = command[4:].split(" ", 1)
        cmd_name = cmd_parts[0]
        subcmd_name = cmd_parts[1] if len(cmd_parts) > 1 else None
        
        target_cmd = bot.tree.get_command(cmd_name)
        if target_cmd is None:
            await interaction.followup.send("❌ 找不到此指令。", ephemeral=True)
            return
        
        if subcmd_name and isinstance(target_cmd, app_commands.Group):
            # 查找子指令
            for subcmd in target_cmd.commands:
                if subcmd.name == subcmd_name:
                    target_cmd = subcmd
                    break
            else:
                await interaction.followup.send("❌ 找不到此子指令。", ephemeral=True)
                return
        
        embed = discord.Embed(
            title=f"/{target_cmd.qualified_name}",
            description=target_cmd.description or "無描述",
            color=0x5865F2
        )
        
        # 顯示參數
        if hasattr(target_cmd, 'parameters') and target_cmd.parameters:
            params_text = []
            for param in target_cmd.parameters:
                required = "必填" if param.required else "選填"
                param_desc = param.description or "無描述"
                params_text.append(f"• `{param.name}` ({required}): {param_desc}")
            
            if params_text:
                embed.add_field(
                    name="參數",
                    value="\n".join(params_text),
                    inline=False
                )
        
        # 如果是群組指令，顯示子指令
        if isinstance(target_cmd, app_commands.Group):
            subcmds = [f"`{subcmd.name}` - {subcmd.description or '無描述'}" for subcmd in target_cmd.commands]
            if subcmds:
                embed.add_field(
                    name="子指令",
                    value="\n".join(subcmds),
                    inline=False
                )
        
        await interaction.followup.send(embed=embed)
    
    elif command.startswith("text:"):
        # 文字指令
        cmd_parts = command[5:].split(" ", 1)
        cmd_name = cmd_parts[0]
        subcmd_name = cmd_parts[1] if len(cmd_parts) > 1 else None
        
        target_cmd = bot.get_command(cmd_name)
        if target_cmd is None:
            await interaction.followup.send("❌ 找不到此指令。", ephemeral=True)
            return
        
        if subcmd_name and isinstance(target_cmd, commands.Group):
            target_cmd = target_cmd.get_command(subcmd_name)
            if target_cmd is None:
                await interaction.followup.send("❌ 找不到此子指令。", ephemeral=True)
                return
        
        embed = discord.Embed(
            title=f"{target_cmd.qualified_name}",
            description=target_cmd.help or "無描述",
            color=0x5865F2
        )
        
        # 使用方法
        embed.add_field(
            name="使用方法",
            value=f"`{target_cmd.qualified_name} {target_cmd.signature}`",
            inline=False
        )
        
        # 別名
        if target_cmd.aliases:
            embed.add_field(
                name="別名",
                value=" ".join([f"`{alias}`" for alias in target_cmd.aliases]),
                inline=True
            )
        
        # 如果是群組指令，顯示子指令
        if isinstance(target_cmd, commands.Group):
            subcmds = [f"`{subcmd.name}` - {subcmd.short_doc or '無描述'}" for subcmd in target_cmd.commands]
            if subcmds:
                embed.add_field(
                    name="子指令",
                    value="\n".join(subcmds),
                    inline=False
                )
        
        await interaction.followup.send(embed=embed)
    
    else:
        # 嘗試搜尋指令
        # 先搜尋斜線指令
        target_cmd = bot.tree.get_command(command)
        if target_cmd:
            embed = discord.Embed(
                title=f"/{target_cmd.qualified_name}",
                description=target_cmd.description or "無描述",
                color=0x5865F2
            )
            
            if hasattr(target_cmd, 'parameters') and target_cmd.parameters:
                params_text = []
                for param in target_cmd.parameters:
                    required = "必填" if param.required else "選填"
                    param_desc = param.description or "無描述"
                    params_text.append(f"• `{param.name}` ({required}): {param_desc}")
                
                if params_text:
                    embed.add_field(
                        name="參數",
                        value="\n".join(params_text),
                        inline=False
                    )
            
            if isinstance(target_cmd, app_commands.Group):
                subcmds = [f"`{subcmd.name}` - {subcmd.description or '無描述'}" for subcmd in target_cmd.commands]
                if subcmds:
                    embed.add_field(
                        name="子指令",
                        value="\n".join(subcmds),
                        inline=False
                    )
            
            await interaction.followup.send(embed=embed)
            return
        
        # 搜尋文字指令
        target_cmd = bot.get_command(command)
        if target_cmd:
            embed = discord.Embed(
                title=f"{target_cmd.qualified_name}",
                description=target_cmd.help or "無描述",
                color=0x5865F2
            )
            
            embed.add_field(
                name="使用方法",
                value=f"`{target_cmd.qualified_name} {target_cmd.signature}`",
                inline=False
            )
            
            if target_cmd.aliases:
                embed.add_field(
                    name="別名",
                    value=" ".join([f"`{alias}`" for alias in target_cmd.aliases]),
                    inline=True
                )
            
            if isinstance(target_cmd, commands.Group):
                subcmds = [f"`{subcmd.name}` - {subcmd.short_doc or '無描述'}" for subcmd in target_cmd.commands]
                if subcmds:
                    embed.add_field(
                        name="子指令",
                        value="\n".join(subcmds),
                        inline=False
                    )
            
            await interaction.followup.send(embed=embed)
            return
        
        await interaction.followup.send("❌ 找不到此指令。請使用自動完成選擇指令。", ephemeral=True)


# ===== 使用教學指令 =====

async def build_tutorial_pages(guild: discord.Guild = None) -> list[dict]:
    """動態生成教學頁面，使用 get_command_mention 取得指令提及格式，get_prefix 取得伺服器前綴"""
    prefix = get_prefix(guild)
    bot_name = bot.user.name if bot.user else "機器人"

    # 批次取得所有需要的指令提及
    cmd = {}
    cmd_names = [
        "ping", "info", "changelog", "git-commits", "stats",
        "userinfo", "serverinfo", "avatar", "banner",
        "randomnumber", "randomuser", "textlength", "httpcat",
        "nitro", "petpet", "explore", "feedback", "help", "tutorial",
        "dsize", "dsize-leaderboard", "dsize-battle", "dsize-feedgrass", "dsize-stats",
        "ai", "ai-clear", "ai-history", "ban", "unban", "kick", "timeout", "untimeout", "multi-moderate",
    ]
    # 群組指令：(group_name, subcommand_name)
    subcmd_names = [
        ("automod", "view"), ("automod", "toggle"), ("automod", "settings"),
        ("autopublish", "settings"),
        ("autoreply", "add"), ("autoreply", "remove"), ("autoreply", "list"),
        ("autoreply", "edit"), ("autoreply", "quickadd"),
        ("autoreply", "export"), ("autoreply", "import"), ("autoreply", "test"),
        ("economy", "balance"), ("economy", "daily"), ("economy", "hourly"),
        ("economy", "pay"), ("economy", "exchange"), ("economy", "shop"),
        ("economy", "buy"), ("economy", "sell"), ("economy", "trade"),
        ("economy", "leaderboard"),
        ("music", "play"), ("music", "pause"), ("music", "resume"),
        ("music", "stop"), ("music", "skip"), ("music", "queue"),
        ("music", "now-playing"), ("music", "shuffle"), ("music", "volume"),
        ("music", "recommend"),
        ("report", None),
        ("dynamic-voice", "setup"),
        ("change", "avatar"), ("change", "banner"), ("change", "bio"),
    ]

    for name in cmd_names:
        mention = await get_command_mention(name)
        cmd[name] = mention or f"`/{name}`"

    for group, sub in subcmd_names:
        key = f"{group} {sub}" if sub else group
        mention = await get_command_mention(group, sub)
        cmd[key] = mention or f"`/{key}`"

    return [
        {
            "title": f"👋 歡迎使用 {bot_name} 機器人！",
            "description": (
                "這是一份使用教學，幫助你快速上手本機器人的所有功能。\n\n"
                "**如何操作：**\n"
                "使用下方的 ⬅️ ➡️ 按鈕翻頁瀏覽各項功能介紹。\n\n"
                "**指令類型：**\n"
                "• **斜線指令** — 輸入 `/` 後從選單選取\n"
                f"• **文字指令** — 在聊天中輸入前綴（目前為 `{prefix}`）加上指令名稱\n\n"
                f"**小提示：** 使用 {cmd['help']} 或 `{prefix}help` 可以隨時查看所有指令清單。"
            ),
            "color": 0x5865F2,
        },
        {
            "title": "📊 基本資訊指令",
            "description": (
                "這些指令讓你快速取得機器人與伺服器的相關資訊。\n\n"
                f"🏓 {cmd['ping']} — 檢查機器人延遲（Websocket & REST API）\n"
                f"ℹ️ {cmd['info']} — 顯示機器人版本、伺服器數量、運行時間等詳細資訊\n"
                f"📋 {cmd['changelog']} — 查看機器人的更新日誌\n"
                f"📝 {cmd['git-commits']} — 顯示最近的 Git 提交記錄\n"
                f"📈 {cmd['stats']} — 查看指令使用統計\n"
            ),
            "color": 0x3498DB,
        },
        {
            "title": "🔍 查詢指令",
            "description": (
                "查詢用戶、伺服器與其他實用資訊。\n\n"
                f"👤 {cmd['userinfo']} `<用戶>` — 查詢用戶的 ID、創建時間、加入時間等\n"
                f"🏠 {cmd['serverinfo']} — 查詢目前伺服器的詳細資訊\n"
                f"🖼️ {cmd['avatar']} `[用戶]` — 取得用戶的頭像圖片\n"
                f"🎨 {cmd['banner']} `[用戶]` — 取得用戶的橫幅圖片\n"
                f"🎲 {cmd['randomnumber']} `[min] [max]` — 生成一個隨機數字\n"
                f"👥 {cmd['randomuser']} — 從頻道的發言者中隨機選一人\n"
                f"📏 {cmd['textlength']} `<文字>` — 計算文字長度\n"
                f"🐱 {cmd['httpcat']} `<狀態碼>` — 用 HTTP 狀態碼產生貓咪圖片\n"
            ),
            "color": 0x2ECC71,
        },
        {
            "title": "🛡️ 管理工具",
            "description": (
                "伺服器管理員專用的懲處與管理功能。\n\n"
                f"🔨 {cmd['ban']} `<用戶> [原因]` — 封禁用戶\n"
                f"🔓 {cmd['unban']} `<用戶>` — 解除封禁\n"
                f"👢 {cmd['kick']} `<用戶> [原因]` — 踢出用戶\n"
                f"🔇 {cmd['timeout']} `<用戶> <時間>` — 禁言用戶\n"
                f"🔊 {cmd['untimeout']} `<用戶>` — 解除禁言\n"
                f"⚡ {cmd['multi-moderate']} — 對多名用戶同時執行懲處\n\n"
                "-# 需要對應的伺服器管理權限才能使用"
            ),
            "color": 0xE74C3C,
        },
        {
            "title": "🤖 自動管理 & 自動發布",
            "description": (
                "讓機器人自動幫你管理伺服器。\n\n"
                "**自動管理 (AutoMod)**\n"
                f"• {cmd['automod view']} — 查看目前的自動管理設定\n"
                f"• {cmd['automod toggle']} — 開啟或關閉自動管理功能\n"
                f"• {cmd['automod settings']} — 調整自動管理的偵測項目\n"
                "• 可自動偵測：逃避處罰、過多表情、詐騙連結等\n\n"
                "**自動發布 (AutoPublish)**\n"
                f"• {cmd['autopublish settings']} — 設定自動發布的頻道\n"
                "• 機器人會自動將公告頻道的訊息發布給所有追蹤的伺服器\n"
            ),
            "color": 0x9B59B6,
        },
        {
            "title": "💬 自動回覆",
            "description": (
                "設定關鍵字觸發的自動回覆訊息。\n\n"
                f"➕ {cmd['autoreply add']} `<關鍵字> <回覆>` — 新增自動回覆\n"
                f"➖ {cmd['autoreply remove']} `<關鍵字>` — 刪除自動回覆\n"
                f"📋 {cmd['autoreply list']} — 列出所有自動回覆\n"
                f"✏️ {cmd['autoreply edit']} — 編輯現有的自動回覆\n"
                f"⚡ {cmd['autoreply quickadd']} — 快速新增多個回覆\n"
                f"📤 {cmd['autoreply export']} — 匯出回覆設定為 JSON\n"
                f"📥 {cmd['autoreply import']} — 從 JSON 匯入回覆設定\n"
                f"🧪 {cmd['autoreply test']} — 測試自動回覆觸發\n\n"
                "-# 支援機率觸發與變數替換"
            ),
            "color": 0xF39C12,
        },
        {
            "title": "💰 經濟系統",
            "description": (
                "完整的虛擬經濟系統，含貨幣、商店與交易。\n\n"
                f"💵 {cmd['economy balance']} — 查看你的餘額\n"
                f"📅 {cmd['economy daily']} — 領取每日獎勵\n"
                f"⏰ {cmd['economy hourly']} — 領取每小時獎勵\n"
                f"💸 {cmd['economy pay']} `<用戶> <金額>` — 轉帳給其他用戶\n"
                f"🔄 {cmd['economy exchange']} — 伺服幣與全域幣互換\n"
                f"🛒 {cmd['economy shop']} — 瀏覽商店\n"
                f"🛍️ {cmd['economy buy']} / {cmd['economy sell']} — 購買或出售物品\n"
                f"🤝 {cmd['economy trade']} — 與其他用戶交易\n"
                f"🏆 {cmd['economy leaderboard']} — 查看財富排行榜\n"
            ),
            "color": 0xF1C40F,
        },
        {
            "title": "🎵 音樂播放",
            "description": (
                "在語音頻道中播放音樂。\n\n"
                f"▶️ {cmd['music play']} `<歌曲>` — 播放歌曲或將歌曲加入隊列\n"
                f"⏸️ {cmd['music pause']} — 暫停播放\n"
                f"⏯️ {cmd['music resume']} — 繼續播放\n"
                f"⏹️ {cmd['music stop']} — 停止播放並離開語音頻道\n"
                f"⏭️ {cmd['music skip']} — 跳過目前歌曲\n"
                f"📜 {cmd['music queue']} — 查看播放隊列\n"
                f"🎶 {cmd['music now-playing']} — 顯示正在播放的歌曲\n"
                f"🔀 {cmd['music shuffle']} — 隨機播放隊列\n"
                f"🔊 {cmd['music volume']} `<音量>` — 調整音量\n"
                f"💡 {cmd['music recommend']} — 根據目前歌曲推薦\n"
            ),
            "color": 0x1DB954,
        },
        {
            "title": "🤖 AI 聊天 & 其他功能",
            "description": (
                "**AI 聊天助手**\n"
                f"💬 {cmd['ai']} `<訊息>` — 與 AI 對話\n"
                f"🗑️ {cmd['ai-clear']} — 清除對話歷史\n"
                f"📜 {cmd['ai-history']} — 查看對話記錄\n\n"
                "**檢舉系統**\n"
                f"🚨 {cmd['report']} — 檢舉違規訊息（支援 AI 判定）\n\n"
                "**動態語音頻道**\n"
                f"🔊 {cmd['dynamic-voice setup']} — 設定動態語音頻道，加入即自動建立專屬房間\n\n"
                "**回饋建議**\n"
                f"📝 {cmd['feedback']} — 向開發者提交回饋\n\n"
                "**機器人自訂**\n"
                f"🖼️ {cmd['change avatar']} / {cmd['change banner']} / {cmd['change bio']} — 自訂機器人外觀（需授權）\n"
            ),
            "color": 0xE91E63,
        },
        {
            "title": "🎮 娛樂功能",
            "description": (
                "各種有趣的娛樂指令。\n\n"
                f"📏 {cmd['dsize']} — 隨機量測...嗯...你懂的 😏\n"
                f"🏆 {cmd['dsize-leaderboard']} — 查看排行榜\n"
                f"⚔️ {cmd['dsize-battle']} — 與其他用戶對戰\n"
                f"🌿 {cmd['dsize-feedgrass']} — 餵草功能\n"
                f"📊 {cmd['dsize-stats']} — 查看你的統計數據\n\n"
                f"🎁 {cmd['nitro']} — Nitro 禮物分享工具\n"
                f"🐾 {cmd['petpet']} — 生成 petpet GIF\n"
                f"🌐 {cmd['explore']} — 探索其他伺服器\n"
            ),
            "color": 0xFF6B6B,
        },
        {
            "title": "✅ 教學完成！",
            "description": (
                "恭喜你完成了機器人的使用教學！🎉\n\n"
                "**快速回顧：**\n"
                f"• 使用 {cmd['help']} 查看所有指令\n"
                f"• 使用 {cmd['help']} `<指令>` 查看特定指令的詳細說明\n"
                f"• 使用 {cmd['info']} 查看機器人資訊\n"
                f"• 使用 {cmd['feedback']} 向開發者回饋意見\n\n"
                "**相關連結：**\n"
                f"如有任何問題，歡迎加入[支援伺服器]({config('support_server_invite')})尋求協助！\n\n"
                "-# 祝你使用愉快！— by AvianJay"
            ),
            "color": 0x2ECC71,
        },
    ]


class TutorialView(discord.ui.View):
    def __init__(self, pages: list[dict], interaction: discord.Interaction):
        super().__init__(timeout=300)
        self.pages = pages
        self.current_page = 0
        self.original_interaction = interaction
        self.update_buttons()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(view=self)
        except Exception:
            pass

    def update_buttons(self):
        self.first_button.disabled = self.current_page == 0
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= len(self.pages) - 1
        self.last_button.disabled = self.current_page >= len(self.pages) - 1

    def get_embed(self) -> discord.Embed:
        page = self.pages[self.current_page]
        embed = discord.Embed(
            title=page["title"],
            description=page["description"],
            color=page.get("color", 0x5865F2),
        )
        embed.set_footer(text=f"頁面 {self.current_page + 1} / {len(self.pages)} • 使用教學")
        return embed

    @discord.ui.button(emoji="⏪", style=discord.ButtonStyle.secondary, custom_id="tutorial_first")
    async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.primary, custom_id="tutorial_prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.primary, custom_id="tutorial_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(emoji="⏩", style=discord.ButtonStyle.secondary, custom_id="tutorial_last")
    async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = len(self.pages) - 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)


@bot.tree.command(name=app_commands.locale_str("tutorial"), description="機器人使用教學")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def tutorial_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    pages = await build_tutorial_pages(guild=interaction.guild)
    view = TutorialView(pages, interaction=interaction)
    await interaction.followup.send(embed=view.get_embed(), view=view, ephemeral=True)


@bot.command(aliases=["tut", "guide"])
async def tutorial(ctx: commands.Context):
    """機器人使用教學

    用法： tutorial
    顯示一份教學，幫助你了解機器人的所有功能。
    """
    prefix = get_prefix(ctx.guild)

    # 取得常用指令提及
    cmd_help = await get_command_mention("help") or "`/help`"
    cmd_info = await get_command_mention("info") or "`/info`"
    cmd_ping = await get_command_mention("ping") or "`/ping`"
    cmd_changelog = await get_command_mention("changelog") or "`/changelog`"
    cmd_stats = await get_command_mention("stats") or "`/stats`"
    cmd_feedback = await get_command_mention("feedback") or "`/feedback`"
    cmd_tutorial = await get_command_mention("tutorial") or "`/tutorial`"

    embed = discord.Embed(
        title="📖 機器人使用教學",
        description=(
            "歡迎使用本機器人！以下是主要功能分類：\n\n"
            f"📊 **基本資訊** — {cmd_ping}, {cmd_info}, {cmd_changelog}, {cmd_stats}\n"
            "🔍 **查詢功能** — `/userinfo`, `/serverinfo`, `/avatar`, `/banner`\n"
            "🛡️ **管理工具** — `/ban/kick/timeout` 等\n"
            "🤖 **自動管理** — `/automod`, `/autopublish`\n"
            "💬 **自動回覆** — `/autoreply add/remove/list`\n"
            "💰 **經濟系統** — `/economy balance/daily/shop` 等\n"
            "🎵 **音樂播放** — `/music play/pause/skip` 等\n"
            "🤖 **AI 聊天** — `/ai`, `/ai-clear`\n"
            "🎮 **娛樂功能** — `/dsize`, `/petpet`, `/explore`\n"
            f"📝 **回饋建議** — {cmd_feedback}\n\n"
            f"使用 `{prefix}help <指令>` 查看特定指令說明\n"
            f"使用斜線指令 {cmd_tutorial} 可以獲得互動式翻頁教學！"
        ),
        color=0x5865F2,
    )
    embed.set_thumbnail(url=ctx.bot.user.avatar.url if ctx.bot.user.avatar else None)
    embed.set_footer(text="by AvianJay")
    await ctx.send(embed=embed)


if __name__ == "__main__":
    start_bot()
