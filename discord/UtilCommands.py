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
version = "0.17.5"
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
    embed.add_field(name="相關連結", value=f"* [機器人網站]({config('website_url')})\n* [支援伺服器]({config('support_server_invite')})\n* [隱私政策]({config('website_url')}/privacy-policy)\n* [服務條款]({config('website_url')}/terms-of-service)\n* [邀請機器人](https://discord.com/oauth2/authorize?client_id={str(bot.user.id)})", inline=False)
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
        super().__init__(timeout=None)
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
        embed.set_footer(text=f"領取者: {interaction.user.display_name} ({interaction.user.name})", icon_url=interaction.user.display_avatar.url)
        
        await interaction.edit_original_response(embed=embed, view=self)
        
        # 私訊領取者連結
        await interaction.response.send_message(f"🎊 這是你的 Nitro 連結：\n{self.link}", ephemeral=True)
        self.stop()


@bot.tree.command(name=app_commands.locale_str("nitro"), description="我不想要被機器人幹走尼戳")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def nitro_command(interaction: discord.Interaction):
    await interaction.response.send_modal(NitroLinkModal())


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


@bot.tree.command(name=app_commands.locale_str("help"), description="顯示指令幫助與說明")
@app_commands.describe(command="要查詢的指令名稱")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.autocomplete(command=help_command_autocomplete)
async def help_slash_command(interaction: discord.Interaction, command: str = None):
    await interaction.response.defer()
    if command is None:
        # 顯示所有指令總覽
        embed = discord.Embed(
            title="📚 指令幫助",
            description=f"使用 {await get_command_mention('help')} <指令> 查看特定指令的詳細說明\n選擇下方的指令可以查看詳細資訊",
            color=0x5865F2
        )
        embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None)
        
        # 斜線指令
        app_cmds = []
        for cmd in bot.tree.get_commands():
            if isinstance(cmd, app_commands.Group):
                for subcmd in cmd.commands:
                    app_cmds.append(await get_command_mention(cmd.name, subcmd.name))
            elif isinstance(cmd, app_commands.Command):
                app_cmds.append(await get_command_mention(cmd.name))
        
        if app_cmds:
            # 分割成多個 field 避免超過字數限制
            chunk_size = 20
            for i in range(0, len(app_cmds), chunk_size):
                chunk = app_cmds[i:i + chunk_size]
                embed.add_field(
                    name=f"斜線指令 ({i + 1}-{min(i + chunk_size, len(app_cmds))})" if len(app_cmds) > chunk_size else f"斜線指令 ({len(app_cmds)})",
                    value=" ".join(chunk),
                    inline=False
                )
        
        # 文字指令
        text_cmds = []
        for cmd in bot.commands:
            if not cmd.hidden:
                if isinstance(cmd, commands.Group):
                    for subcmd in cmd.commands:
                        if await can_run_text_command(subcmd, interaction):
                            text_cmds.append(f"`{cmd.name} {subcmd.name}`")
                else:
                    if await can_run_text_command(cmd, interaction):
                        text_cmds.append(f"`{cmd.name}`")
        
        if text_cmds:
            chunk_size = 20
            for i in range(0, len(text_cmds), chunk_size):
                chunk = text_cmds[i:i + chunk_size]
                embed.add_field(
                    name=f"文字指令 ({i + 1}-{min(i + chunk_size, len(text_cmds))})" if len(text_cmds) > chunk_size else f"文字指令 ({len(text_cmds)})",
                    value=" ".join(chunk),
                    inline=False
                )
        
        embed.set_footer(text=f"共 {len(app_cmds)} 個斜線指令 | {len(text_cmds)} 個文字指令 | by AvianJay")
        await interaction.followup.send(embed=embed)
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


if __name__ == "__main__":
    start_bot()
