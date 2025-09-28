import os
import json
import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

config_version = 1
config_path = 'config.moderationnotify.json'

default_config = {
    "config_version": config_version,
    "token": "YOUR_BOT_TOKEN_HERE"
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


async def notify_user(user: discord.User, guild: discord.Guild, action: str, reason: str = "未提供", end_time=None):
    embed = discord.Embed(
        title=f"你在 {guild.name} 被{action}。",
        description=f"原因：{reason}",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc)  # 訊息時間
    )

    # add server icon
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    # if mute
    if end_time:
        embed.add_field(name="解禁時間", value=end_time.strftime("%Y-%m-%d %H:%M:%S"), inline=False)

    embed.set_footer(text=f"{guild.name}")

    try:
        await user.send(embed=embed)
    except discord.Forbidden:
        print(f"無法私訊 {user}")


@bot.event
async def on_member_remove(member):
    guild = member.guild
    async for entry in guild.audit_logs(limit=1):
        if entry.target.id != member.id:
            continue

        if entry.action == discord.AuditLogAction.kick:
            await notify_user(member, guild, "踢出", entry.reason or "未提供")
        elif entry.action == discord.AuditLogAction.ban:
            # ban
            await notify_user(member, guild, "封禁", entry.reason or "未提供")
        else:
            pass


# timeout
@bot.event
async def on_member_update(before, after):
    if before.timed_out_until != after.timed_out_until and after.timed_out_until is not None:
        guild = after.guild
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.member_update):
            if entry.target.id == after.id:
                reason = entry.reason or "未提供"
                end_time = after.timed_out_until.astimezone(timezone(timedelta(hours=8)))  # 台灣時間
                await notify_user(after, guild, "禁言", reason, end_time)


@bot.event
async def on_ready():
    print(f"已登入：{bot.user}")

bot.run(config("token"))
