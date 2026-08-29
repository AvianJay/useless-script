import os
import sys
import re
import io
import random
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from globalenv import bot, start_bot, get_user_data, set_user_data, get_command_mention, modules, failed_modules, config, get_global_config, get_emoji_by_name, get_emoji_mention_by_name
from CustomPrefix import get_prefix
from typing import Union
from datetime import datetime, timezone
import psutil
import time
import aiohttp
from database import db
from CustomPrefix import get_prefix

import i18n
from i18n import t, t_enum

startup_time = datetime.now(timezone.utc)
version = "0.24.4"
try:
    git_commit_hash = os.popen("git rev-parse --short HEAD").read().strip()
except Exception as e:
    git_commit_hash = "unknown"
full_version = f"{version} ({git_commit_hash})"


UI_EMOJI_FALLBACKS: dict[str, str] = {
    "book": "📖",
    "books": "📚",
    "list": "📋",
    "tools": "🔧",
    "package": "📦",
    "folder": "📁",
    "success": "✅",
    "error": "❌",
    "warning": "⚠️",
    "nav_left": "⬅️",
    "nav_right": "➡️",
    "nav_first": "⏪",
    "nav_last": "⏩",
    "party": "🎉",
    "confetti": "🎊",
    "download": "💾",
    "wave": "👋",
    "chart_bar": "📊",
    "ping": "🏓",
    "info": "ℹ️",
    "note": "📝",
    "chart_line": "📈",
    "search": "🔍",
    "user": "👤",
    "server": "🏠",
    "image": "🖼️",
    "palette": "🎨",
    "dice": "🎲",
    "users": "👥",
    "ruler": "📏",
    "cat": "🐱",
    "shield": "🛡️",
    "hammer": "🔨",
    "unlock": "🔓",
    "boot": "👢",
    "mute": "🔇",
    "volume": "🔊",
    "bolt": "⚡",
    "robot": "🤖",
    "chat": "💬",
    "plus": "➕",
    "minus": "➖",
    "edit": "✏️",
    "upload": "📤",
    "import": "📥",
    "flask": "🧪",
    "coins": "💰",
    "wallet": "💵",
    "calendar": "📅",
    "clock": "⏰",
    "money_send": "💸",
    "refresh": "🔄",
    "cart": "🛒",
    "shopping_bag": "🛍️",
    "handshake": "🤝",
    "trophy": "🏆",
    "music": "🎵",
    "play": "▶️",
    "pause": "⏸️",
    "resume": "⏯️",
    "stop": "⏹️",
    "skip": "⏭️",
    "history": "📜",
    "music_note": "🎶",
    "shuffle": "🔀",
    "idea": "💡",
    "trash": "🗑️",
    "report": "🚨",
    "battle": "⚔️",
    "grass": "🌿",
    "gift": "🎁",
    "paw": "🐾",
    "globe": "🌐",
    "gamepad": "🎮",
}

BUTTON_UI_EMOJI_NAMES: dict[str, str] = {
    "nav_left": "btn_nav_left",
    "nav_right": "btn_nav_right",
    "nav_first": "btn_nav_first",
    "nav_last": "btn_nav_last",
    "party": "btn_party",
    "download": "btn_download",
}

NATIVE_UI_EMOJI_NAMES: dict[str, str] = {
    fallback: name
    for name, fallback in UI_EMOJI_FALLBACKS.items()
}


async def get_ui_emoji(name: str) -> str:
    emoji = await get_emoji_mention_by_name(name)
    if emoji == f":{name}:":
        return UI_EMOJI_FALLBACKS.get(name, "")
    return emoji


async def get_ui_button_emoji(name: str) -> discord.Emoji | str | None:
    button_emoji_name = BUTTON_UI_EMOJI_NAMES.get(name)
    if button_emoji_name is not None:
        emoji = await get_emoji_by_name(button_emoji_name)
        if emoji is not None:
            return emoji

    emoji = await get_emoji_by_name(name)
    if emoji is not None:
        return emoji
    return UI_EMOJI_FALLBACKS.get(name)


async def replace_native_ui_emojis(text: str | None) -> str | None:
    if text is None:
        return None

    rendered = str(text)
    cache: dict[str, str] = {}

    for native, name in sorted(NATIVE_UI_EMOJI_NAMES.items(), key=lambda item: len(item[0]), reverse=True):
        if native not in rendered:
            continue
        if name not in cache:
            cache[name] = await get_ui_emoji(name)
        rendered = rendered.replace(native, cache[name])

    return rendered


async def apply_ui_embed_emojis(embed: discord.Embed | None) -> discord.Embed | None:
    if embed is None:
        return None

    if embed.title:
        embed.title = await replace_native_ui_emojis(embed.title)
    if embed.description:
        embed.description = await replace_native_ui_emojis(embed.description)

    for index, field in enumerate(list(embed.fields)):
        embed.set_field_at(
            index,
            name=await replace_native_ui_emojis(field.name),
            value=await replace_native_ui_emojis(field.value),
            inline=field.inline,
        )

    if embed.footer and embed.footer.text:
        embed.set_footer(
            text=await replace_native_ui_emojis(embed.footer.text),
            icon_url=embed.footer.icon_url,
        )

    if embed.author and embed.author.name:
        embed.set_author(
            name=await replace_native_ui_emojis(embed.author.name),
            url=embed.author.url,
            icon_url=embed.author.icon_url,
        )

    return embed


def get_commit_logs(limit=10) -> str:
    try:
        logs = os.popen(f"git log -n {limit} \"--pretty=format:%an: %h - %s (%cr)\"").read().strip().split("\n")
        return logs
    except Exception as e:
        return [t("utilcommands.err.no_commit_log")]


def parse_changelog(locale: str | None = None) -> list[dict]:
    """解析 changelog.md 並返回版本列表；locale 指定時優先讀 changelog.<locale>.md，
    整檔不存在時 fallback 回 changelog.md（原文 zh-TW，逐版本翻譯不可行，僅支援整檔切換）。"""
    locale = locale or i18n.current_locale()
    changelog_path = os.path.join(os.path.dirname(__file__), "changelog.md")
    if locale and locale != i18n.SOURCE_LOCALE:
        localized_path = os.path.join(os.path.dirname(__file__), f"changelog.{locale}.md")
        if os.path.exists(localized_path):
            changelog_path = localized_path
    try:
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
        return i18n.tn("common.unit.seconds", 0)

    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []
    if days: parts.append(i18n.tn("common.unit.days", days))
    if hours: parts.append(i18n.tn("common.unit.hours", hours))
    if minutes: parts.append(i18n.tn("common.unit.minutes", minutes))
    if seconds: parts.append(i18n.tn("common.unit.seconds", seconds))

    return " ".join(parts)


def get_uptime_seconds() -> int:
    return int((datetime.now(timezone.utc) - startup_time).total_seconds())


DEFAULT_AVATAR_URL = "https://discord.com/assets/6debd47ed13483642cf09e832ed0bc1b.png"


def info_command_warning() -> str:
    return t("utilcommands.info.warning")


def server_verification_label(level_name: str) -> str:
    return t_enum("utilcommands.verification", level_name, default=t("common.state.none"))


def get_bot_latency_ms() -> str | float:
    try:
        return round(bot.latency * 1000, 2)
    except OverflowError:
        return "N/A"


def get_total_app_command_count() -> int:
    tree_commands = bot.tree.get_commands()
    return len(tree_commands) + sum(len(cmd.commands) for cmd in tree_commands if isinstance(cmd, app_commands.Group))


def build_bot_info_embed(*, full: bool, user_count_value: str | int, include_timestamp: bool = False) -> discord.Embed:
    server_count = len(bot.guilds)
    bot_latency = get_bot_latency_ms()
    uptime = get_time_text(get_uptime_seconds())
    commands_count = len(bot.commands) + sum(len(c.commands) for c in bot.commands if isinstance(c, commands.Group))
    app_commands_count = get_total_app_command_count()
    dbcount = db.get_database_count()
    application = bot.application
    install_count = getattr(application, "approximate_user_install_count", None) if application else None

    embed = discord.Embed(title=t("utilcommands.botinfo.title"), color=0x00ff00)
    embed.add_field(name=t("utilcommands.botinfo.field.bot_name"), value=bot.user.name)
    embed.add_field(name=t("utilcommands.botinfo.field.version"), value=full_version)
    embed.add_field(name=t("utilcommands.botinfo.field.command_count"),
                    value=t("utilcommands.botinfo.command_count_value", total=commands_count + app_commands_count,
                           text=commands_count, app=app_commands_count))
    embed.add_field(name=t("utilcommands.botinfo.field.server_count"), value=server_count)
    embed.add_field(name=t("utilcommands.botinfo.field.user_count"), value=user_count_value)
    embed.add_field(name=t("utilcommands.botinfo.field.install_count"), value=install_count or "N/A")
    embed.add_field(name=t("utilcommands.botinfo.field.latency"), value=f"{bot_latency}ms")
    embed.add_field(name=t("utilcommands.botinfo.field.cpu_usage"), value=f"{psutil.cpu_percent()}%")
    embed.add_field(name=t("utilcommands.botinfo.field.memory_usage"), value=f"{psutil.virtual_memory().percent}%")
    embed.add_field(name="Discord.py " + t("utilcommands.botinfo.field.version_suffix"), value=discord.__version__)
    embed.add_field(name="Python " + t("utilcommands.botinfo.field.version_suffix"), value=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    embed.add_field(name=t("utilcommands.botinfo.field.command_usage_count"), value=f"{sum(get_global_config('command_usage_stats', {}).values()) + sum(get_global_config('app_command_usage_stats', {}).values()) + sum(get_global_config('command_error_stats', {}).values()) + sum(get_global_config('app_command_error_stats', {}).values())}", inline=False)
    embed.add_field(name=t("utilcommands.botinfo.field.uptime"), value=uptime)
    embed.add_field(name=t("utilcommands.botinfo.field.database_info"),
                    value=t("utilcommands.botinfo.database_info_value", total=dbcount["total"],
                           server=dbcount["server_configs"], user=dbcount["user_data"]), inline=True)
    if full:
        embed.add_field(name=t("utilcommands.botinfo.field.modules_loaded_full", count=len(modules)),
                        value="\n".join(modules) if modules else t("common.state.none"), inline=False)
        if config("disable_modules", []):
            embed.add_field(name=t("utilcommands.botinfo.field.modules_disabled_full", count=len(config("disable_modules", []))),
                            value="\n".join(config("disable_modules", [])), inline=False)
        if failed_modules:
            embed.add_field(name=t("utilcommands.botinfo.field.modules_failed_full", count=len(failed_modules)),
                            value="\n".join(failed_modules), inline=False)
    else:
        embed.add_field(name=t("utilcommands.botinfo.field.modules_loaded"), value=str(len(modules)), inline=False)
        if config("disable_modules", []):
            embed.add_field(name=t("utilcommands.botinfo.field.modules_disabled"), value=str(len(config("disable_modules", []))), inline=False)
        if failed_modules:
            embed.add_field(name=t("utilcommands.botinfo.field.modules_failed"), value=str(len(failed_modules)), inline=False)
    embed.add_field(name=t("utilcommands.botinfo.field.links"),
                    value=t("utilcommands.botinfo.links_value",
                           website=config("website_url"),
                           docs=f"{config('website_url')}/docs",
                           support=config("support_server_invite"),
                           privacy=f"{config('website_url')}/privacy-policy",
                           terms=f"{config('website_url')}/terms-of-service",
                           invite=f"https://discord.com/oauth2/authorize?client_id={str(bot.user.id)}"), inline=False)
    embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None)
    if include_timestamp:
        embed.timestamp = datetime.now(timezone.utc)
    embed.set_footer(text="by AvianJay")
    return embed


def build_user_info_message(user: Union[discord.User, discord.Member]) -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(title=t("utilcommands.userinfo.title", name=user.display_name), color=0x00ff00)
    embed.set_thumbnail(url=user.avatar.url if user.avatar else None)
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=t("utilcommands.btn.avatar_link"), url=user.avatar.url if user.avatar else DEFAULT_AVATAR_URL))
    embed.add_field(name=t("utilcommands.userinfo.field.user_id"), value=str(user.id), inline=True)
    embed.add_field(name=t("utilcommands.userinfo.field.created_at"), value=user.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    if isinstance(user, discord.Member):
        embed.add_field(name=t("utilcommands.userinfo.field.nickname"), value=user.nick or t("common.state.none"), inline=True)
        embed.add_field(name=t("utilcommands.userinfo.field.joined_at"), value=user.joined_at.strftime("%Y-%m-%d %H:%M:%S") if user.joined_at else t("utilcommands.state.unknown"), inline=True)
        if user.display_avatar and user.display_avatar.url != (user.avatar.url if user.avatar else None):
            embed.set_image(url=user.display_avatar.url)
            view.add_item(discord.ui.Button(label=t("utilcommands.btn.server_avatar_link"), url=user.display_avatar.url))
    return embed, view


def build_server_info_message(guild: discord.Guild) -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(title=t("utilcommands.serverinfo.title", name=guild.name), color=0x00ff00)
    view = discord.ui.View()
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
        view.add_item(discord.ui.Button(label=t("utilcommands.btn.server_icon_link"), url=guild.icon.url))
    if guild.banner:
        embed.set_image(url=guild.banner.url)
        view.add_item(discord.ui.Button(label=t("utilcommands.btn.server_banner_link"), url=guild.banner.url))
    embed.add_field(name=t("utilcommands.serverinfo.field.server_id"), value=str(guild.id), inline=True)
    embed.add_field(name=t("utilcommands.serverinfo.field.created_at"), value=f"<t:{int(guild.created_at.timestamp())}:F>", inline=True)
    embed.add_field(name=t("utilcommands.serverinfo.field.owner"), value=guild.owner.mention if guild.owner else t("utilcommands.state.unknown"), inline=True)
    embed.add_field(name=t("utilcommands.serverinfo.field.boosts"), value=t("utilcommands.serverinfo.boosts_value", count=guild.premium_subscription_count, tier=guild.premium_tier), inline=True)
    embed.add_field(name=t("utilcommands.serverinfo.field.verification_level"), value=server_verification_label(guild.verification_level.name.lower()), inline=True)
    embed.add_field(name=t("utilcommands.serverinfo.field.locale"), value=str(guild.preferred_locale), inline=True)
    embed.add_field(name=t("utilcommands.serverinfo.field.member_count"), value=str(guild.member_count), inline=True)
    embed.add_field(name=t("utilcommands.serverinfo.field.channel_count"), value=str(len(guild.channels)), inline=True)
    embed.add_field(name=t("utilcommands.serverinfo.field.role_count"), value=str(len(guild.roles)), inline=True)
    return embed, view


def build_avatar_message(user: Union[discord.User, discord.Member]) -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(title=t("utilcommands.avatar.title", name=user.display_name), color=0x00ff00)
    view = discord.ui.View()
    avatar_url = user.avatar.url if user.avatar else DEFAULT_AVATAR_URL
    if user.display_avatar and user.display_avatar.url != avatar_url:
        embed.set_image(url=user.display_avatar.url)
        embed.set_thumbnail(url=avatar_url)
        view.add_item(discord.ui.Button(label=t("utilcommands.btn.server_avatar_link"), url=user.display_avatar.url))
    else:
        embed.set_image(url=avatar_url)
    view.add_item(discord.ui.Button(label=t("utilcommands.btn.avatar_link"), url=avatar_url))
    return embed, view


def build_banner_message(user: discord.User) -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(title=t("utilcommands.banner.title", name=user.display_name), color=0x00ff00)
    embed.set_image(url=user.banner.url)
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=t("utilcommands.btn.banner_link"), url=user.banner.url))
    return embed, view


def build_git_commits_embed() -> discord.Embed:
    commit_logs = get_commit_logs(10)
    return discord.Embed(title=t("utilcommands.commits.title"), description="\n".join(commit_logs), color=0x00ff00)


def build_ping_embed(*, bot_latency: str | float, defer_latency: float = None, typing_latency: float = None) -> discord.Embed:
    embed = discord.Embed(title=t("utilcommands.ping.title"), color=0x00ff00)
    embed.add_field(name=t("utilcommands.ping.field.websocket"), value=f"{bot_latency}ms")
    if defer_latency:
        embed.add_field(name=t("utilcommands.ping.field.rest_defer"), value=f"{defer_latency}ms")
    if typing_latency:
        embed.add_field(name=t("utilcommands.ping.field.rest_typing"), value=f"{defer_latency}ms")
    return embed


async def get_command_display(command_name: str, subcommand_name: str = None) -> str:
    mention = await get_command_mention(command_name, subcommand_name)
    if mention is not None:
        return mention
    if subcommand_name:
        return f"`/{command_name} {subcommand_name}`"
    return f"`/{command_name}`"


async def info_command(interaction: discord.Interaction, full: bool = False):
    await interaction.response.defer()
    user_count = f"{len(bot.users)}/{sum(guild.member_count for guild in bot.guilds)}"
    embed = build_bot_info_embed(full=full, user_count_value=user_count)
    await interaction.followup.send(content=info_command_warning(), embed=embed)


@bot.command(aliases=["botinfo", "bi"])
async def info(ctx: commands.Context, full: bool = False):
    """顯示機器人資訊

    用法： info [full]

    如果指定 full 參數為 True，則顯示完整模組列表與載入失敗模組。
    """
    user_count = len(set(bot.get_all_members()))
    embed = build_bot_info_embed(full=full, user_count_value=user_count, include_timestamp=True)
    await ctx.send(content=info_command_warning(), embed=embed)


@bot.tree.command(name=app_commands.locale_str("randomnumber", i18n_key="cmd.utilcommands.randomnumber.name"), description=app_commands.locale_str("Generate a random number", i18n_key="cmd.utilcommands.randomnumber.desc"))
@app_commands.describe(min=app_commands.locale_str("Minimum value", i18n_key="cmd.utilcommands.randomnumber.param.min"), max=app_commands.locale_str("Maximum value", i18n_key="cmd.utilcommands.randomnumber.param.max"))
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def randomnumber_command(interaction: discord.Interaction, min: int = 1, max: int = 100):
    if min >= max:
        await interaction.response.send_message(t("utilcommands.randomnumber.err_min_max"), ephemeral=True)
        return
    number = random.randint(min, max)
    await interaction.response.send_message(t("utilcommands.randomnumber.result", number=number, min=min, max=max))


@bot.command(aliases=["rn"])
async def randomnumber(ctx: commands.Context, min: int = 1, max: int = 100):
    """生成一個隨機數字"""
    if min >= max:
        await ctx.send(t("utilcommands.randomnumber.err_min_max"))
        return
    number = random.randint(min, max)
    await ctx.send(t("utilcommands.randomnumber.result", number=number, min=min, max=max))


@bot.tree.command(name=app_commands.locale_str("randomuser", i18n_key="cmd.utilcommands.randomuser.name"), description=app_commands.locale_str("Pick a random recent speaker in this channel", i18n_key="cmd.utilcommands.randomuser.desc"))
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.describe(mention=app_commands.locale_str("Whether to mention the chosen user", i18n_key="cmd.utilcommands.randomuser.param.mention"))
@app_commands.choices(mention=[
    app_commands.Choice(name=app_commands.locale_str("Yes", i18n_key="cmd.utilcommands.randomuser.choice.true"), value="True"),
    app_commands.Choice(name=app_commands.locale_str("No", i18n_key="cmd.utilcommands.randomuser.choice.false"), value="False"),
])
async def randomuser_command(interaction: discord.Interaction, mention: str = "False"):
    mention = mention == "True"
    if interaction.guild is None or interaction.channel is None:
        await interaction.response.send_message(t("utilcommands.randomuser.err_guild_only"), ephemeral=True)
        return

    channel = interaction.channel
    messages = [msg async for msg in channel.history(limit=50)]
    users = list(set(msg.author for msg in messages if not msg.author.bot))

    if not users:
        await interaction.response.send_message(t("utilcommands.randomuser.err_no_users"), ephemeral=True)
        return

    selected_user = random.choice(users)
    await interaction.response.send_message(
        t("utilcommands.randomuser.result", user=selected_user.mention, count=len(users)),
        allowed_mentions=discord.AllowedMentions(users=mention, roles=False, everyone=False))


@bot.command(aliases=["ui"])
async def userinfo(ctx: commands.Context, user: Union[discord.User, discord.Member] = None):
    """顯示用戶資訊

    用法： userinfo [用戶]
    如果不指定用戶，則顯示自己的資訊。
    """
    if user is None:
        user = ctx.author
    embed, view = build_user_info_message(user)
    await ctx.send(embed=embed, view=view)


async def userinfo_command(interaction: discord.Interaction, user: Union[discord.User, discord.Member]):
    embed, view = build_user_info_message(user)
    await interaction.response.send_message(embed=embed, view=view)


async def serverinfo_command(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(t("common.err.guild_only"), ephemeral=True)
        return

    embed, view = build_server_info_message(guild)
    await interaction.response.send_message(embed=embed, view=view)


async def avatar_command(interaction: discord.Interaction, user: Union[discord.User, discord.Member] = None):
    if user is None:
        user = interaction.user
    embed, view = build_avatar_message(user)
    await interaction.response.send_message(embed=embed, view=view)


async def banner_command(interaction: discord.Interaction, user: Union[discord.User, discord.Member] = None):
    if user is None:
        user = interaction.user
    user = await bot.fetch_user(user.id)
    if user.banner is None:
        await interaction.response.send_message(t("utilcommands.banner.err_no_banner"), ephemeral=True)
        return

    embed, view = build_banner_message(user)
    await interaction.response.send_message(embed=embed, view=view)

@bot.command(aliases=["si"])
async def serverinfo(ctx: commands.Context):
    """顯示目前所在伺服器資訊

    用法： serverinfo
    """
    guild = ctx.guild
    if guild is None:
        await ctx.send(t("common.err.guild_only"))
        return

    embed, view = build_server_info_message(guild)
    await ctx.send(embed=embed, view=view)

@bot.command(aliases=["pfp"])
async def avatar(ctx: commands.Context, user: Union[discord.User, discord.Member] = None):
    """取得用戶頭像

    用法： avatar [用戶]
    如果不指定用戶，則顯示自己的頭像。
    """
    if user is None:
        user = ctx.author
    embed, view = build_avatar_message(user)
    await ctx.send(embed=embed, view=view)

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
        await ctx.send(t("utilcommands.banner.err_no_banner"))
        return
    embed, view = build_banner_message(user)
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


async def get_cmd_mention(interaction: discord.Interaction, command: str, subcommand: str = None):
    mention = await get_command_mention(command, subcommand)
    if mention is None:
        await interaction.response.send_message(t("utilcommands.err.command_not_found"), ephemeral=True)
        return
    await interaction.response.send_message(f"{mention}", allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name=app_commands.locale_str("textlength", i18n_key="cmd.utilcommands.textlength.name"), description=app_commands.locale_str("Count the length of the given text", i18n_key="cmd.utilcommands.textlength.desc"))
@app_commands.describe(text=app_commands.locale_str("The text to measure", i18n_key="cmd.utilcommands.textlength.param.text"))
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def textlength_command(interaction: discord.Interaction, text: str):
    length = len(text)
    await interaction.response.send_message(i18n.tn("utilcommands.textlength.result", length))


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
    await ctx.send(i18n.tn("utilcommands.textlength.result", length))


@bot.tree.command(name=app_commands.locale_str("httpcat", i18n_key="cmd.utilcommands.httpcat.name"), description=app_commands.locale_str("Cats are adorable", i18n_key="cmd.utilcommands.httpcat.desc"))
@app_commands.describe(status_code=app_commands.locale_str("HTTP status code (e.g. 404)", i18n_key="cmd.utilcommands.httpcat.param.status_code"))
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


async def changelogs_command(interaction: discord.Interaction):
    embed = build_git_commits_embed()
    await interaction.response.send_message(embed=embed)


class ChangeLogView(discord.ui.View):
    def __init__(self, versions: list[dict], current_page: int = 0, interaction: discord.Interaction = None):
        super().__init__(timeout=300)  # 5 minutes timeout
        self.versions = versions
        self.current_page = current_page
        self.interaction = interaction
        self.time = datetime.now(timezone.utc)
        self.update_buttons()

    async def apply_ui_emojis(self):
        self.prev_button.emoji = await get_ui_button_emoji("nav_left")
        self.next_button.emoji = await get_ui_button_emoji("nav_right")

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
            return discord.Embed(title=t("utilcommands.changelog.title"), description=t("utilcommands.changelog.unavailable"), color=0xff0000)

        version_data = self.versions[self.current_page]
        embed = discord.Embed(
            title=t("utilcommands.changelog.title_versioned", version=version_data["version"]),
            description=version_data["content"][:4096] if version_data["content"] else t("utilcommands.changelog.no_content"),
            color=0x00ff00
        )
        embed.set_footer(text=t("utilcommands.changelog.page_footer", page=self.current_page + 1, total=len(self.versions)))
        embed.timestamp = self.time
        return embed

    @discord.ui.button(style=discord.ButtonStyle.primary, custom_id="changelog_prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.primary, custom_id="changelog_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)


async def changelog_command(interaction: discord.Interaction):
    versions = parse_changelog()
    if not versions:
        await interaction.response.send_message(t("utilcommands.changelog.unavailable"), ephemeral=True)
        return

    view = ChangeLogView(versions, interaction=interaction)
    await view.apply_ui_emojis()
    await interaction.response.send_message(embed=await apply_ui_embed_emojis(view.get_embed()), view=view)


async def ping_command(interaction: discord.Interaction):
    bot_latency = get_bot_latency_ms()
    s = time.perf_counter()
    await interaction.response.defer()
    e = time.perf_counter()
    rest_latency = round((e - s) * 1000, 2)
    embed = build_ping_embed(bot_latency=bot_latency, defer_latency=rest_latency)
    await interaction.followup.send(embed=embed)


@bot.command(aliases=["pg"])
async def ping(ctx: commands.Context):
    """檢查機器人延遲

    用法： ping
    """
    bot_latency = get_bot_latency_ms()
    s = time.perf_counter()
    await ctx.typing()
    e = time.perf_counter()
    rest_latency = round((e - s) * 1000, 2)
    embed = build_ping_embed(bot_latency=bot_latency, typing_latency=rest_latency)
    await ctx.send(embed=embed)


class NitroLinkModal(i18n.I18nModal, title=i18n.K("utilcommands.nitro.modal_title")):
    def __init__(self, need_message: bool = False):
        super().__init__()
        self.need_message = need_message
        self.author_ids = None  # 用於存儲有發過訊息的用戶 ID

    nitro_link = discord.ui.TextInput(
        label=i18n.K("utilcommands.nitro.link_label"),
        placeholder="https://discord.gift/...",
        style=discord.TextStyle.short,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        link = self.nitro_link.value.strip()

        if not link.startswith("https://discord.gift/"):
            await interaction.response.send_message(await replace_native_ui_emojis("❌ " + t("utilcommands.nitro.err_invalid_link")), ephemeral=True)
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
                        await interaction.followup.send(await replace_native_ui_emojis("⚠️ " + t("utilcommands.nitro.err_already_used")), ephemeral=True)
                        return

                    # 準備顯示用的資訊
                    gift_name = data.get("subscription_plan", {}).get("name", "Discord Nitro")
                    expires_raw = data.get("expires_at")
                    gifter = bot.get_user(int(data.get("user", {}).get("id", 0)))

                    embed = discord.Embed(title=f"{gift_name}", color=0xFF73FA)
                    embed.description = t("utilcommands.nitro.gift_desc")
                    embed.set_author(name=f"{gifter.display_name} ({gifter.name})" if gifter else t("utilcommands.state.unknown_user"), icon_url=gifter.display_avatar.url if gifter else None)
                    embed.set_footer(text=t("utilcommands.nitro.not_claimed_yet"))

                    if expires_raw:
                        expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
                        embed.add_field(name=t("utilcommands.nitro.field.expires_at"), value=f"<t:{int(expires_at.timestamp())}:R>")

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
                                embed.add_field(name=t("utilcommands.nitro.field.claim_restriction"), value=t("utilcommands.nitro.claim_restriction_value"))
                            else:
                                warn_message = "\n⚠️ " + t("utilcommands.nitro.warn_no_history_perm")
                        else:
                            warn_message = "\n⚠️ " + t("utilcommands.nitro.warn_user_install_unsupported")

                    # 建立按鈕 View 並把連結傳進去
                    view = NitroClaimView(link, gift_name, need_message=self.need_message, author_ids=self.author_ids)
                    await view.apply_ui_emojis()

                    # 在頻道發送公開訊息（非 ephemeral），讓大家搶
                    await interaction.followup.send(embed=embed, view=view)
                    await interaction.followup.send(await replace_native_ui_emojis("✅ " + t("utilcommands.nitro.sent_to_channel", warning=warn_message)), ephemeral=True)
                else:
                    await interaction.followup.send(await replace_native_ui_emojis("❌ " + t("utilcommands.nitro.err_verify_failed")), ephemeral=True)

class NitroClaimView(i18n.I18nView):
    def __init__(self, link: str, gift_name: str, need_message: bool = False, author_ids: set[int] = None):
        super().__init__(timeout=None) # 永不到期或自訂時間
        self.link = link
        self.gift_name = gift_name
        self.need_message = need_message
        self.author_ids = author_ids
        self.claimed = False

    async def apply_ui_emojis(self):
        self.claim_button.emoji = await get_ui_button_emoji("party")

    @discord.ui.button(label=i18n.K("utilcommands.nitro.btn.claim"), style=discord.ButtonStyle.primary)
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.claimed:  # avoid edit message delay
            await interaction.response.send_message(await replace_native_ui_emojis("⚠️ " + t("utilcommands.nitro.err_already_claimed")), ephemeral=True)
            return
        if self.need_message and self.author_ids is not None:
            if interaction.user.id not in self.author_ids:
                await interaction.response.send_message(await replace_native_ui_emojis("❌ " + t("utilcommands.nitro.err_need_message")), ephemeral=True)
                return
        self.claimed = True
        # 禁用所有按鈕防止重複點擊
        for child in self.children:
            child.disabled = True

        # 更新原訊息
        embed = interaction.message.embeds[0]
        embed.title = t("utilcommands.nitro.claimed_title", gift_name=self.gift_name)
        embed.color = discord.Color.light_grey()
        embed.set_footer(text=t("utilcommands.nitro.claimed_by", user=f"{interaction.user.display_name} ({interaction.user.name})"), icon_url=interaction.user.display_avatar.url)

        await interaction.response.edit_message(embed=embed, view=self)

        # 私訊領取者連結
        await interaction.followup.send(await replace_native_ui_emojis("🎊 " + t("utilcommands.nitro.your_link", link=self.link)), ephemeral=True)
        self.stop()


@bot.tree.command(name=app_commands.locale_str("nitro", i18n_key="cmd.utilcommands.nitro.name"), description=app_commands.locale_str("I don't want bots stealing my Nitro", i18n_key="cmd.utilcommands.nitro.desc"))
@app_commands.describe(
    need_message=app_commands.locale_str("Only recent speakers can claim (authors of the last 50 messages)", i18n_key="cmd.utilcommands.nitro.param.need_message")
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
            await ctx.send(t("utilcommands.err.no_sticker"))
            return
        sticker = replied_message.stickers[0]
    elif not ctx.message.stickers:
        await ctx.send(t("utilcommands.err.no_sticker"))
        return
    else:
        sticker = ctx.message.stickers[0]
    embed = discord.Embed(title=t("utilcommands.stickerinfo.title", name=sticker.name), color=0x00ff00)
    embed.set_image(url=sticker.url)
    embed.add_field(name=t("utilcommands.stickerinfo.field.sticker_id"), value=str(sticker.id), inline=True)
    embed.add_field(name=t("utilcommands.stickerinfo.field.format"), value=sticker.format.name, inline=True)
    btn = discord.ui.Button(label=t("utilcommands.btn.sticker_link"), url=sticker.url)
    view = discord.ui.View()
    view.add_item(btn)
    await ctx.reply(embed=embed, view=view)


def _embed_author_matches(embed, key: str) -> bool:
    """embed.author.name 用來分辨這張卡片是表情符號還是貼圖資訊。卡片建立
    當下的語言（建立者）跟按下按鈕當下的語言（choke point 2 解析出的點擊者）
    可能不同，所以比對「所有語言」的翻譯結果，而不是只比對目前 locale。"""
    if not embed or not embed.author:
        return False
    return embed.author.name in {t(key, locale=loc) for loc in i18n.available_locales()}


class StealView(i18n.I18nView):
    def __init__(self, *, timeout: float | None = None):
        super().__init__(timeout=timeout)

    async def apply_ui_emojis(self):
        self.download_button.emoji = await get_ui_button_emoji("download")

    @discord.ui.button(label=i18n.K("utilcommands.btn.steal"), style=discord.ButtonStyle.primary, custom_id="steal")
    async def download_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if not interaction.guild:
            await interaction.followup.send(t("common.err.guild_only"), ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_emojis_and_stickers:
            await interaction.followup.send(t("utilcommands.err.need_manage_emojis_stickers"), ephemeral=True)
            return
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if _embed_author_matches(embed, "utilcommands.emoji_info.author"):
            emoji = True
            target = t("utilcommands.target.emoji")
        elif _embed_author_matches(embed, "utilcommands.sticker_info.author"):
            emoji = False
            target = t("utilcommands.target.sticker")
        if not embed or not embed.image:
            await interaction.followup.send(t("utilcommands.err.image_not_found", target=target), ephemeral=True)
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
            await interaction.followup.send(await replace_native_ui_emojis("✅ " + t("utilcommands.msg.steal_success", target=target, name=embed.title)), ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(await replace_native_ui_emojis("❌ " + t("utilcommands.err.steal_failed", target=target, error=e)), ephemeral=True)

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
            label=t("utilcommands.btn.emoji_link"),
            url=str(self.emojis[0].url),
            row=0,
        )
        self.add_item(self.link_button)

        if not allow_steal:
            self.remove_item(self.download_button)

        self.update_buttons()

    async def apply_ui_emojis(self):
        await super().apply_ui_emojis()
        self.prev_button.emoji = await get_ui_button_emoji("nav_left")
        self.next_button.emoji = await get_ui_button_emoji("nav_right")

    @property
    def current_emoji(self) -> discord.PartialEmoji:
        return self.emojis[self.current_page]

    def get_embed(self) -> discord.Embed:
        emoji = self.current_emoji
        embed = discord.Embed(title=f"{emoji.name}", color=0x00ff00)
        embed.set_author(name=t("utilcommands.emoji_info.author"))
        embed.set_image(url=str(emoji.url))
        embed.add_field(name=t("utilcommands.emoji_info.field.emoji_id"), value=str(emoji.id), inline=True)
        embed.add_field(name=t("utilcommands.emoji_info.field.is_animated"), value=str(emoji.animated), inline=True)
        embed.set_footer(text=t("utilcommands.emoji_info.footer", page=self.current_page + 1, total=len(self.emojis)))
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

    @discord.ui.button(style=discord.ButtonStyle.primary, row=0)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.primary, row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.emojis) - 1, self.current_page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@bot.tree.context_menu(name=app_commands.locale_str("Emoji Info", i18n_key="cmd.utilcommands.ctx.emoji_info_context.name"))
async def emoji_info_context(interaction: discord.Interaction, message: discord.Message):
    emojis = _collect_custom_emojis_from_message(message)
    if not emojis:
        await interaction.response.send_message(t("utilcommands.err.no_emoji"), ephemeral=True)
        return
    view = EmojiInfoView(
        emojis,
        interaction=interaction,
        allow_steal=interaction.is_guild_integration(),
    )
    await view.apply_ui_emojis()
    await interaction.response.send_message(embed=view.get_embed(), view=view)

# context menu for sticker info
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@bot.tree.context_menu(name=app_commands.locale_str("Sticker Info", i18n_key="cmd.utilcommands.ctx.sticker_info_context.name"))
async def sticker_info_context(interaction: discord.Interaction, message: discord.Message):
    if message.stickers:
        sticker = message.stickers[0]
    elif message.message_snapshots and message.message_snapshots[0].stickers:
        sticker = message.message_snapshots[0].stickers[0]
    else:
        await interaction.response.send_message(t("utilcommands.err.no_sticker"), ephemeral=True)
        return
    embed = discord.Embed(title=f"{sticker.name}", color=0x00ff00)
    embed.set_author(name=t("utilcommands.sticker_info.author"))
    embed.set_image(url=sticker.url)
    embed.add_field(name=t("utilcommands.stickerinfo.field.sticker_id"), value=str(sticker.id), inline=True)
    embed.add_field(name=t("utilcommands.stickerinfo.field.format"), value=sticker.format.name, inline=True)
    if interaction.is_guild_integration():
        view = StealView()
        await view.apply_ui_emojis()
    else:
        view = discord.ui.View()
    btn = discord.ui.Button(label=t("utilcommands.btn.sticker_link"), url=sticker.url)
    view.add_item(btn)
    await interaction.response.send_message(embed=embed, view=view)


class PrettyHelpCommand(commands.HelpCommand):
    """美化版的 Help Command"""

    def __init__(self):
        super().__init__(
            command_attrs={
                'help': '顯示所有指令或特定指令的幫助訊息',  # i18n: skip（等同 command.help，跟其他指令的 docstring 同一類）
                'aliases': ['h', '?', 'commands']
            }
        )

    def get_command_signature(self, command: commands.Command) -> str:
        """取得指令的使用格式"""
        return f"{self.context.clean_prefix}{command.qualified_name} {command.signature}"

    async def send_bot_help(self, mapping):
        """顯示所有指令的總覽"""
        embed = discord.Embed(
            title=t("utilcommands.help.title"),
            description=t("utilcommands.help.overview_desc", prefix=self.context.clean_prefix),
            color=0x5865F2
        )
        embed.set_thumbnail(url=self.context.bot.user.avatar.url if self.context.bot.user.avatar else None)

        for cog, cmds in mapping.items():
            filtered = await self.filter_commands(cmds, sort=True)
            if filtered:
                cog_name = cog.qualified_name if cog else t("utilcommands.help.other_commands")
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

        embed.set_footer(text=t("utilcommands.help.bot_help_footer", count=len(self.context.bot.commands)))

        channel = self.get_destination()
        await channel.send(embed=await apply_ui_embed_emojis(embed))

    async def send_cog_help(self, cog: commands.Cog):
        """顯示特定 Cog 的指令"""
        embed = discord.Embed(
            title=f"📦 {cog.qualified_name}",
            description=cog.description or t("utilcommands.help.no_description"),
            color=0x5865F2
        )

        filtered = await self.filter_commands(cog.get_commands(), sort=True)
        for command in filtered:
            embed.add_field(
                name=f"`{self.get_command_signature(command)}`",
                value=command.short_doc or t("utilcommands.help.no_description"),
                inline=False
            )

        embed.set_footer(text=t("utilcommands.help.cog_help_footer", prefix=self.context.clean_prefix))

        channel = self.get_destination()
        await channel.send(embed=await apply_ui_embed_emojis(embed))

    async def send_group_help(self, group: commands.Group):
        """顯示群組指令的幫助"""
        embed = discord.Embed(
            title=f"📁 {group.qualified_name}",
            description=group.help or t("utilcommands.help.no_description"),
            color=0x5865F2
        )

        embed.add_field(
            name=t("utilcommands.help.field.usage"),
            value=f"`{self.get_command_signature(group)}`",
            inline=False
        )

        if group.aliases:
            embed.add_field(
                name=t("utilcommands.help.field.aliases"),
                value=" ".join([f"`{alias}`" for alias in group.aliases]),
                inline=False
            )

        filtered = await self.filter_commands(group.commands, sort=True)
        if filtered:
            subcommands = "\n".join([
                f"`{self.context.clean_prefix}{cmd.qualified_name}` - {cmd.short_doc or t('utilcommands.help.no_description')}"
                for cmd in filtered
            ])
            embed.add_field(
                name=t("utilcommands.help.field.subcommands"),
                value=subcommands,
                inline=False
            )

        channel = self.get_destination()
        await channel.send(embed=await apply_ui_embed_emojis(embed))

    async def send_command_help(self, command: commands.Command):
        """顯示單一指令的幫助"""
        embed = discord.Embed(
            title=f"📝 {command.qualified_name}",
            description=command.help or t("utilcommands.help.no_description"),
            color=0x5865F2
        )

        embed.add_field(
            name=t("utilcommands.help.field.usage"),
            value=f"`{self.get_command_signature(command)}`",
            inline=False
        )

        if command.aliases:
            embed.add_field(
                name=t("utilcommands.help.field.aliases"),
                value=" ".join([f"`{alias}`" for alias in command.aliases]),
                inline=True
            )

        # 顯示冷卻時間（如果有）
        if command._buckets and command._buckets._cooldown:
            cooldown = command._buckets._cooldown
            embed.add_field(
                name=t("utilcommands.help.field.cooldown"),
                value=t("utilcommands.help.cooldown_value", rate=cooldown.rate, per=f"{cooldown.per:.0f}"),
                inline=True
            )

        embed.set_footer(text=t("utilcommands.help.command_help_footer"))

        channel = self.get_destination()
        await channel.send(embed=await apply_ui_embed_emojis(embed))

    async def send_error_message(self, error: str):
        """顯示錯誤訊息"""
        embed = discord.Embed(
            title=t("utilcommands.help.command_not_found_title"),
            description=error,
            color=0xFF0000
        )
        embed.set_footer(text=t("utilcommands.help.error_footer", prefix=self.context.clean_prefix))

        channel = self.get_destination()
        await channel.send(embed=await apply_ui_embed_emojis(embed))


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


@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
class InfoCommands(commands.GroupCog, name=app_commands.locale_str("info", i18n_key="cmd.utilcommands.info.root.name")):
    def __init__(self, bot_: commands.Bot) -> None:
        self.bot = bot_
        super().__init__()

    @app_commands.command(name=app_commands.locale_str("bot", i18n_key="cmd.utilcommands.info.bot.name"), description=app_commands.locale_str("Show bot information", i18n_key="cmd.utilcommands.info.bot.desc"))
    @app_commands.describe(full=app_commands.locale_str("Show the full module list including failed modules", i18n_key="cmd.utilcommands.info.bot.param.full"))
    async def show_bot_info(self, interaction: discord.Interaction, full: bool = False):
        await info_command(interaction, full)

    @app_commands.command(name=app_commands.locale_str("user", i18n_key="cmd.utilcommands.info.user.name"), description=app_commands.locale_str("Show user information", i18n_key="cmd.utilcommands.info.user.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to look up", i18n_key="cmd.utilcommands.info.user.param.user"))
    async def user_info(self, interaction: discord.Interaction, user: Union[discord.User, discord.Member]):
        await userinfo_command(interaction, user)

    @app_commands.command(name=app_commands.locale_str("server", i18n_key="cmd.utilcommands.info.server.name"), description=app_commands.locale_str("Show information about this server", i18n_key="cmd.utilcommands.info.server.desc"))
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def server_info(self, interaction: discord.Interaction):
        await serverinfo_command(interaction)

    @app_commands.command(name=app_commands.locale_str("avatar", i18n_key="cmd.utilcommands.info.avatar.name"), description=app_commands.locale_str("Get a user's avatar", i18n_key="cmd.utilcommands.info.avatar.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to look up", i18n_key="cmd.utilcommands.info.avatar.param.user"))
    async def avatar_info(self, interaction: discord.Interaction, user: Union[discord.User, discord.Member] = None):
        await avatar_command(interaction, user)

    @app_commands.command(name=app_commands.locale_str("banner", i18n_key="cmd.utilcommands.info.banner.name"), description=app_commands.locale_str("Get a user's banner", i18n_key="cmd.utilcommands.info.banner.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to look up", i18n_key="cmd.utilcommands.info.banner.param.user"))
    async def banner_info(self, interaction: discord.Interaction, user: Union[discord.User, discord.Member] = None):
        await banner_command(interaction, user)

    @app_commands.command(name=app_commands.locale_str("mention", i18n_key="cmd.utilcommands.info.mention.name"), description=app_commands.locale_str("Get a command's mention format", i18n_key="cmd.utilcommands.info.mention.desc"))
    @app_commands.describe(command=app_commands.locale_str("Command name", i18n_key="cmd.utilcommands.info.mention.param.command"), subcommand=app_commands.locale_str("Subcommand name (optional)", i18n_key="cmd.utilcommands.info.mention.param.subcommand"))
    @app_commands.autocomplete(command=command_autocomplete, subcommand=subcommand_autocomplete)
    async def mention_info(self, interaction: discord.Interaction, command: str, subcommand: str = None):
        await get_cmd_mention(interaction, command, subcommand)

    @app_commands.command(name=app_commands.locale_str("commits", i18n_key="cmd.utilcommands.info.commits.name"), description=app_commands.locale_str("Show the bot's git commit history", i18n_key="cmd.utilcommands.info.commits.desc"))
    async def commits_info(self, interaction: discord.Interaction):
        await changelogs_command(interaction)

    @app_commands.command(name=app_commands.locale_str("changelog", i18n_key="cmd.utilcommands.info.changelog.name"), description=app_commands.locale_str("Show the bot changelog", i18n_key="cmd.utilcommands.info.changelog.desc"))
    async def changelog_info(self, interaction: discord.Interaction):
        await changelog_command(interaction)

    @app_commands.command(name=app_commands.locale_str("ping", i18n_key="cmd.utilcommands.info.ping.name"), description=app_commands.locale_str("Check the bot's latency", i18n_key="cmd.utilcommands.info.ping.desc"))
    async def ping_info(self, interaction: discord.Interaction):
        await ping_command(interaction)

    @app_commands.command(name=app_commands.locale_str("help", i18n_key="cmd.utilcommands.info.help.name"), description=app_commands.locale_str("Show command help and usage", i18n_key="cmd.utilcommands.info.help.desc"))
    @app_commands.describe(command=app_commands.locale_str("The command to look up", i18n_key="cmd.utilcommands.info.help.param.command"))
    @app_commands.autocomplete(command=help_command_autocomplete)
    async def help_info(self, interaction: discord.Interaction, command: str = None):
        await help_slash_command(interaction, command)

    @app_commands.command(name=app_commands.locale_str("tutorial", i18n_key="cmd.utilcommands.info.tutorial.name"), description=app_commands.locale_str("Bot usage tutorial", i18n_key="cmd.utilcommands.info.tutorial.desc"))
    async def tutorial_info(self, interaction: discord.Interaction):
        await tutorial_command(interaction)


class HelpPageView(discord.ui.View):
    def __init__(self, pages: list[discord.Embed], interaction: discord.Interaction):
        super().__init__(timeout=120)
        self.pages = pages
        self.current_page = 0
        self.interaction = interaction
        self.update_buttons()

    async def apply_ui_emojis(self):
        self.prev_button.emoji = await get_ui_button_emoji("nav_left")
        self.next_button.emoji = await get_ui_button_emoji("nav_right")

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

    @discord.ui.button(style=discord.ButtonStyle.primary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)


async def help_slash_command(interaction: discord.Interaction, command: str = None):
    await interaction.response.defer()
    if command is None:
        help_mention = await get_command_display("info", "help")

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
                title=t("utilcommands.help.title"),
                description=t("utilcommands.help.overview_desc_mention", mention=help_mention),
                color=0x5865F2
            )
            embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None)
            if chunk:
                embed.add_field(
                    name=t("utilcommands.help.slash_commands_field", start=i + 1, end=min(i + chunk_size, len(app_cmds)), total=len(app_cmds)),
                    value=" ".join(chunk),
                    inline=False
                )
            await apply_ui_embed_emojis(embed)
            pages.append(embed)

        # 文字指令分頁
        for i in range(0, max(len(text_cmds), 1), chunk_size):
            chunk = text_cmds[i:i + chunk_size]
            if not chunk:
                continue
            embed = discord.Embed(
                title=t("utilcommands.help.title"),
                description=t("utilcommands.help.overview_desc_mention", mention=help_mention),
                color=0x5865F2
            )
            embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None)
            embed.add_field(
                name=t("utilcommands.help.text_commands_field", start=i + 1, end=min(i + chunk_size, len(text_cmds)), total=len(text_cmds)),
                value=" ".join(chunk),
                inline=False
            )
            await apply_ui_embed_emojis(embed)
            pages.append(embed)

        # 加上頁碼
        total_app = len(app_cmds)
        total_text = len(text_cmds)
        for idx, page in enumerate(pages):
            page.set_footer(text=t("utilcommands.help.page_footer", page=idx + 1, total=len(pages), app_count=total_app, text_count=total_text))

        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0])
        else:
            view = HelpPageView(pages, interaction)
            await view.apply_ui_emojis()
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
            await interaction.followup.send(await replace_native_ui_emojis("❌ " + t("utilcommands.help.command_not_found")), ephemeral=True)
            return

        if subcmd_name and isinstance(target_cmd, app_commands.Group):
            # 查找子指令
            for subcmd in target_cmd.commands:
                if subcmd.name == subcmd_name:
                    target_cmd = subcmd
                    break
            else:
                await interaction.followup.send(await replace_native_ui_emojis("❌ " + t("utilcommands.help.subcommand_not_found")), ephemeral=True)
                return

        embed = discord.Embed(
            title=f"/{target_cmd.qualified_name}",
            description=target_cmd.description or t("utilcommands.help.no_description"),
            color=0x5865F2
        )

        # 顯示參數
        if hasattr(target_cmd, 'parameters') and target_cmd.parameters:
            params_text = []
            for param in target_cmd.parameters:
                required = t("utilcommands.help.required") if param.required else t("utilcommands.help.optional")
                param_desc = param.description or t("utilcommands.help.no_description")
                params_text.append(f"• `{param.name}` ({required}): {param_desc}")

            if params_text:
                embed.add_field(
                    name=t("utilcommands.help.field.parameters"),
                    value="\n".join(params_text),
                    inline=False
                )

        # 如果是群組指令，顯示子指令
        if isinstance(target_cmd, app_commands.Group):
            subcmds = [f"`{subcmd.name}` - {subcmd.description or t('utilcommands.help.no_description')}" for subcmd in target_cmd.commands]
            if subcmds:
                embed.add_field(
                    name=t("utilcommands.help.field.subcommands"),
                    value="\n".join(subcmds),
                    inline=False
                )

        await interaction.followup.send(embed=await apply_ui_embed_emojis(embed))

    elif command.startswith("text:"):
        # 文字指令
        cmd_parts = command[5:].split(" ", 1)
        cmd_name = cmd_parts[0]
        subcmd_name = cmd_parts[1] if len(cmd_parts) > 1 else None

        target_cmd = bot.get_command(cmd_name)
        if target_cmd is None:
            await interaction.followup.send(await replace_native_ui_emojis("❌ " + t("utilcommands.help.command_not_found")), ephemeral=True)
            return

        if subcmd_name and isinstance(target_cmd, commands.Group):
            target_cmd = target_cmd.get_command(subcmd_name)
            if target_cmd is None:
                await interaction.followup.send(await replace_native_ui_emojis("❌ " + t("utilcommands.help.subcommand_not_found")), ephemeral=True)
                return

        embed = discord.Embed(
            title=f"{target_cmd.qualified_name}",
            description=target_cmd.help or t("utilcommands.help.no_description"),
            color=0x5865F2
        )

        # 使用方法
        embed.add_field(
            name=t("utilcommands.help.field.usage"),
            value=f"`{target_cmd.qualified_name} {target_cmd.signature}`",
            inline=False
        )

        # 別名
        if target_cmd.aliases:
            embed.add_field(
                name=t("utilcommands.help.field.aliases"),
                value=" ".join([f"`{alias}`" for alias in target_cmd.aliases]),
                inline=True
            )

        # 如果是群組指令，顯示子指令
        if isinstance(target_cmd, commands.Group):
            subcmds = [f"`{subcmd.name}` - {subcmd.short_doc or t('utilcommands.help.no_description')}" for subcmd in target_cmd.commands]
            if subcmds:
                embed.add_field(
                    name=t("utilcommands.help.field.subcommands"),
                    value="\n".join(subcmds),
                    inline=False
                )

        await interaction.followup.send(embed=await apply_ui_embed_emojis(embed))

    else:
        # 嘗試搜尋指令
        # 先搜尋斜線指令
        target_cmd = bot.tree.get_command(command)
        if target_cmd:
            embed = discord.Embed(
                title=f"/{target_cmd.qualified_name}",
                description=target_cmd.description or t("utilcommands.help.no_description"),
                color=0x5865F2
            )

            if hasattr(target_cmd, 'parameters') and target_cmd.parameters:
                params_text = []
                for param in target_cmd.parameters:
                    required = t("utilcommands.help.required") if param.required else t("utilcommands.help.optional")
                    param_desc = param.description or t("utilcommands.help.no_description")
                    params_text.append(f"• `{param.name}` ({required}): {param_desc}")

                if params_text:
                    embed.add_field(
                        name=t("utilcommands.help.field.parameters"),
                        value="\n".join(params_text),
                        inline=False
                    )

            if isinstance(target_cmd, app_commands.Group):
                subcmds = [f"`{subcmd.name}` - {subcmd.description or t('utilcommands.help.no_description')}" for subcmd in target_cmd.commands]
                if subcmds:
                    embed.add_field(
                        name=t("utilcommands.help.field.subcommands"),
                        value="\n".join(subcmds),
                        inline=False
                    )

            await interaction.followup.send(embed=await apply_ui_embed_emojis(embed))
            return

        # 搜尋文字指令
        target_cmd = bot.get_command(command)
        if target_cmd:
            embed = discord.Embed(
                title=f"{target_cmd.qualified_name}",
                description=target_cmd.help or t("utilcommands.help.no_description"),
                color=0x5865F2
            )

            embed.add_field(
                name=t("utilcommands.help.field.usage"),
                value=f"`{target_cmd.qualified_name} {target_cmd.signature}`",
                inline=False
            )

            if target_cmd.aliases:
                embed.add_field(
                    name=t("utilcommands.help.field.aliases"),
                    value=" ".join([f"`{alias}`" for alias in target_cmd.aliases]),
                    inline=True
                )

            if isinstance(target_cmd, commands.Group):
                subcmds = [f"`{subcmd.name}` - {subcmd.short_doc or t('utilcommands.help.no_description')}" for subcmd in target_cmd.commands]
                if subcmds:
                    embed.add_field(
                        name=t("utilcommands.help.field.subcommands"),
                        value="\n".join(subcmds),
                        inline=False
                    )

            await interaction.followup.send(embed=await apply_ui_embed_emojis(embed))
            return

        await interaction.followup.send(await replace_native_ui_emojis("❌ " + t("utilcommands.help.command_not_found_autocomplete")), ephemeral=True)


# ===== 使用教學指令 =====

async def build_tutorial_pages(guild: discord.Guild = None) -> list[dict]:
    """動態生成教學頁面，使用 get_command_mention 取得指令提及格式，get_prefix 取得伺服器前綴"""
    prefix = get_prefix(guild)
    bot_name = bot.user.name if bot.user else t("utilcommands.state.bot_name_fallback")

    # 批次取得所有需要的指令提及
    cmd = {}
    cmd_names = [
        "stats",
        "randomnumber", "randomuser", "textlength", "httpcat",
        "nitro", "petpet", "explore", "feedback",
        "dsize", "dsize-leaderboard", "dsize-battle", "dsize-feedgrass", "dsize-stats",
        "ai", "ai-clear", "ai-history", "ban", "unban", "kick", "timeout", "untimeout", "multi-moderate",
    ]
    # 群組指令：(group_name, subcommand_name)
    subcmd_names = [
        ("info", "bot"), ("info", "help"), ("info", "tutorial"), ("info", "ping"), ("info", "changelog"),
        ("info", "commits"), ("info", "user"), ("info", "server"), ("info", "avatar"), ("info", "banner"),
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

    # cmd 的 key 含空格/連字號，不是合法識別字，轉成 sanitized 版本供 t() 的
    # **kwargs 展開；catalog 的 placeholder 用同一套命名。
    cmd_p = {key.replace(" ", "_").replace("-", "_"): value for key, value in cmd.items()}
    tp = dict(cmd_p, prefix=prefix, bot_name=bot_name, support_url=config("support_server_invite"))

    pages = [
        {"title": t("utilcommands.tutorial.welcome.title", **tp), "description": t("utilcommands.tutorial.welcome.desc", **tp), "color": 0x5865F2},
        {"title": t("utilcommands.tutorial.basic_info.title"), "description": t("utilcommands.tutorial.basic_info.desc", **tp), "color": 0x3498DB},
        {"title": t("utilcommands.tutorial.lookup.title"), "description": t("utilcommands.tutorial.lookup.desc", **tp), "color": 0x2ECC71},
        {"title": t("utilcommands.tutorial.moderation.title"), "description": t("utilcommands.tutorial.moderation.desc", **tp), "color": 0xE74C3C},
        {"title": t("utilcommands.tutorial.automod_autopublish.title"), "description": t("utilcommands.tutorial.automod_autopublish.desc", **tp), "color": 0x9B59B6},
        {"title": t("utilcommands.tutorial.autoreply.title"), "description": t("utilcommands.tutorial.autoreply.desc", **tp), "color": 0xF39C12},
        {"title": t("utilcommands.tutorial.economy.title"), "description": t("utilcommands.tutorial.economy.desc", **tp), "color": 0xF1C40F},
        {"title": t("utilcommands.tutorial.music.title"), "description": t("utilcommands.tutorial.music.desc", **tp), "color": 0x1DB954},
        {"title": t("utilcommands.tutorial.ai_misc.title"), "description": t("utilcommands.tutorial.ai_misc.desc", **tp), "color": 0xE91E63},
        {"title": t("utilcommands.tutorial.fun.title"), "description": t("utilcommands.tutorial.fun.desc", **tp), "color": 0xFF6B6B},
        {"title": t("utilcommands.tutorial.completed.title"), "description": t("utilcommands.tutorial.completed.desc", **tp), "color": 0x2ECC71},
    ]

    for page in pages:
        page["title"] = await replace_native_ui_emojis(page["title"])
        page["description"] = await replace_native_ui_emojis(page["description"])

    return pages


class TutorialView(discord.ui.View):
    def __init__(self, pages: list[dict], interaction: discord.Interaction):
        super().__init__(timeout=300)
        self.pages = pages
        self.current_page = 0
        self.original_interaction = interaction
        self.update_buttons()

    async def apply_ui_emojis(self):
        self.first_button.emoji = await get_ui_button_emoji("nav_first")
        self.prev_button.emoji = await get_ui_button_emoji("nav_left")
        self.next_button.emoji = await get_ui_button_emoji("nav_right")
        self.last_button.emoji = await get_ui_button_emoji("nav_last")

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
        embed.set_footer(text=t("utilcommands.tutorial.page_footer", page=self.current_page + 1, total=len(self.pages)))
        return embed

    @discord.ui.button(style=discord.ButtonStyle.secondary, custom_id="tutorial_first")
    async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.primary, custom_id="tutorial_prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.primary, custom_id="tutorial_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.secondary, custom_id="tutorial_last")
    async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = len(self.pages) - 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)


async def tutorial_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    pages = await build_tutorial_pages(guild=interaction.guild)
    view = TutorialView(pages, interaction=interaction)
    await view.apply_ui_emojis()
    await interaction.followup.send(embed=await apply_ui_embed_emojis(view.get_embed()), view=view, ephemeral=True)


@bot.command(aliases=["tut", "guide"])
async def tutorial(ctx: commands.Context):
    """機器人使用教學

    用法： tutorial
    顯示一份教學，幫助你了解機器人的所有功能。
    """
    prefix = get_prefix(ctx.guild)

    # 取得常用指令提及
    cmd_help = await get_command_display("info", "help")
    cmd_info = await get_command_display("info", "bot")
    cmd_ping = await get_command_display("info", "ping")
    cmd_changelog = await get_command_display("info", "changelog")
    cmd_stats = await get_command_mention("stats") or "`/stats`"
    cmd_feedback = await get_command_mention("feedback") or "`/feedback`"
    cmd_tutorial = await get_command_display("info", "tutorial")

    embed = discord.Embed(
        title=t("utilcommands.tutorial.text_cmd.title"),
        description=t("utilcommands.tutorial.text_cmd.desc",
                      cmd_ping=cmd_ping, cmd_info=cmd_info, cmd_changelog=cmd_changelog,
                      cmd_stats=cmd_stats, cmd_feedback=cmd_feedback, prefix=prefix,
                      cmd_tutorial=cmd_tutorial),
        color=0x5865F2,
    )
    embed.set_thumbnail(url=ctx.bot.user.avatar.url if ctx.bot.user.avatar else None)
    embed.set_footer(text="by AvianJay")
    await ctx.send(embed=await apply_ui_embed_emojis(embed))


asyncio.run(bot.add_cog(InfoCommands(bot)))


if __name__ == "__main__":
    start_bot()
