import selfcord
from selfcord.ext import commands
import os
import json
import database
import asyncio

config_version = 3
config_path = 'config.json'
default_config = {
    "prefix": ">",
    "token": "",
    "owner_id": 0,
    "scan_guilds": [
        {
            "id": 0,
            "flagged_roles": [0],
            "viewable_channels": [0],
            "check_channels": [0]
        }
    ]
}
_config = None

try:
    if os.path.exists(config_path):
        _config = json.load(open(config_path, "r", encoding="utf-8"))
        if not isinstance(_config, dict):
            print("[!] Config file is not a valid JSON object, resetting to default config.")
            _config = default_config.copy()
        for key in _config.keys():
            if key in default_config and not isinstance(_config[key], type(default_config[key])):
                print(f"[!] Config key '{key}' has an invalid type, resetting to default value.")
                _config[key] = default_config[key]
        if "config_version" not in _config:
            print("[!] Config file does not have 'config_version', resetting to default config.")
            _config = default_config.copy()
    else:
        _config = default_config.copy()
        json.dump(_config, open(config_path, "w", encoding="utf-8"), indent=4)
except ValueError:
    _config = default_config.copy()
    json.dump(_config, open(config_path, "w", encoding="utf-8"), indent=4)

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
    json.dump(_config, open(config_path, "w", encoding="utf-8"), indent=4)
    print("[+] Done.")

def config(key, value=None, mode="r"):
    if mode == "r":
        return _config.get(key, value)
    elif mode == "w":
        _config[key] = value
        json.dump(_config, open(config_path, "w", encoding="utf-8"), indent=4)
        return True
    else:
        raise ValueError(f"Invalid mode: {mode}")

bot = commands.Bot(command_prefix=config("prefix", "!"), self_bot=True, owner_id=config("owner_id", 0))
database.init_db()
conn = database.get_db_connection()

async def update_flagged_users():
    scan_guilds = config("scan_guilds", [])
    this_time_added_count = 0
    this_time_added_and_flagged_count = 0
    for guild_info in scan_guilds:
        guild_id = guild_info.get("id")
        flagged_roles = guild_info.get("flagged_roles", [])
        guild = bot.get_guild(guild_id)
        if guild is None:
            continue
        print(f"[+] Updating flagged users for guild {guild.name} (ID: {guild_id})...")
        database.update_guild(conn, guild)
        count = 0
        count_flagged = 0
        print(f"[+] Fetching members for guild {guild.name}...")
        viewable_channels_id = guild_info.get("viewable_channels", [])
        viewable_channels = [guild.get_channel(cid) for cid in viewable_channels_id if guild.get_channel(cid) is not None]
        members = await guild.fetch_members(channels=viewable_channels)
        for member in members:
            if member.bot:
                # check bot verified
                if member.public_flags.verified_bot:
                    continue
            if member.id == bot.user.id:
                continue
            is_flagged = any(role.id in flagged_roles for role in member.roles)
            dt = database.get_flagged_user(conn, member.id, guild_id)
            if not dt:
                this_time_added_count += 1
                if is_flagged:
                    this_time_added_and_flagged_count += 1
            database.add_flagged_user(conn, member.id, guild_id, is_flagged)
            count += 1
            if is_flagged:
                count_flagged += 1
        print(f"[+] Updated flagged users for guild {guild_id}: {count_flagged}/{count} users flagged.")
    return this_time_added_count, this_time_added_and_flagged_count

# other event to add flagged users
@bot.event
async def on_message(message: selfcord.Message):
    if message.author.id != bot.user.id:
        return
    if message.author.bot:
        # check bot verified
        if message.author.public_flags.verified_bot:
            return
    if message.guild is None:
        return
    scan_guilds = config("scan_guilds", [])
    guild_info = next((g for g in scan_guilds if g.get("id") == message.guild.id), None)
    if guild_info is None:
        return
    if database.get_flagged_user(conn, message.author.id, message.guild.id):
        return
    flagged_roles = guild_info.get("flagged_roles", [])
    is_flagged = any(role.id in flagged_roles for role in message.author.roles)
    database.add_flagged_user(conn, message.author.id, message.guild.id, is_flagged)
    print(f"[+] Updated flagged status for user {message.author} (ID: {message.author.id}) in guild {message.guild.name} (ID: {message.guild.id}): {'Flagged' if is_flagged else 'Not Flagged'}")

@bot.event
async def on_member_join(member):
    if member.guild is None:
        return
    scan_guilds = config("scan_guilds", [])
    guild_info = next((g for g in scan_guilds if g.get("id") == member.guild.id), None)
    if guild_info is None:
        return
    if member.id == bot.user.id:
        return
    if member.bot:
        # check bot verified
        if member.public_flags.verified_bot:
            return
    if database.get_flagged_user(conn, member.id, member.guild.id):
        return
    flagged_roles = guild_info.get("flagged_roles", [])
    is_flagged = any(role.id in flagged_roles for role in member.roles)
    database.add_flagged_user(conn, member.id, member.guild.id, is_flagged)
    print(f"[+] Updated flagged status for user {member} (ID: {member.id}) in guild {member.guild.name} (ID: {member.guild.id}): {'Flagged' if is_flagged else 'Not Flagged'}")

@bot.event
async def on_member_update(before, after):
    if before.guild is None:
        return
    scan_guilds = config("scan_guilds", [])
    guild_info = next((g for g in scan_guilds if g.get("id") == before.guild.id), None)
    if guild_info is None:
        return
    if after.id == bot.user.id:
        return
    if after.bot:
        # check bot verified
        if after.public_flags.verified_bot:
            return
    # if database.get_flagged_user(conn, after.id, after.guild.id):
    #     return
    flagged_roles = guild_info.get("flagged_roles", [])
    before_flagged = any(role.id in flagged_roles for role in before.roles)
    after_flagged = any(role.id in flagged_roles for role in after.roles)
    if before_flagged != after_flagged:
        database.add_flagged_user(conn, after.id, after.guild.id, after_flagged)
        print(f"[+] Updated flagged status for user {after} (ID: {after.id}) in guild {after.guild.name} (ID: {after.guild.id}): {'Flagged' if after_flagged else 'Not Flagged'}")

@bot.event
async def on_presence_update(before, after):
    if before.guild is None:
        return
    scan_guilds = config("scan_guilds", [])
    guild_info = next((g for g in scan_guilds if g.get("id") == before.guild.id), None)
    if guild_info is None:
        return
    if after.id == bot.user.id:
        return
    if after.bot:
        # check bot verified
        if after.public_flags.verified_bot:
            return
    # if database.get_flagged_user(conn, after.id, after.guild.id):
    #     return
    flagged_roles = guild_info.get("flagged_roles", [])
    before_flagged = any(role.id in flagged_roles for role in before.roles)
    after_flagged = any(role.id in flagged_roles for role in after.roles)
    if before_flagged != after_flagged:
        database.add_flagged_user(conn, after.id, after.guild.id, after_flagged)
        print(f"[+] Updated flagged status for user {after} (ID: {after.id}) in guild {after.guild.name} (ID: {after.guild.id}): {'Flagged' if after_flagged else 'Not Flagged'}")

# commands
@bot.command()
@commands.is_owner()
async def ping(ctx):
    try:
        latency = bot.latency * 1000  # Convert to milliseconds
    except OverflowError:
        latency = float('NaN')
    await ctx.send('Pong! Latency: {:.2f} ms'.format(latency))

@bot.command()
@commands.is_owner()
async def shutdown(ctx):
    await ctx.send('Shutting down...')
    await bot.close()

@bot.command()
@commands.is_owner()
async def updateflags(ctx):
    await ctx.send('Updating flagged users...')
    tta, ttaf = await update_flagged_users()
    await ctx.send(f'Flagged users update complete. Added: {tta}, +Flagged: {ttaf}')

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    # subscribe to guilds
    scan_guilds = config("scan_guilds", [])
    for guild_info in scan_guilds:
        guild_id = guild_info.get("id")
        guild = bot.get_guild(guild_id)
        if guild is None:
            print(f'[!] Could not find guild with ID: {guild_id}')
            continue
        await guild.subscribe()
        print(f'[+] Subscribed to guild: {guild.name} (ID: {guild.id})')
    while True:
        print('[+] Starting flagged users update...')
        tta, ttaf = await update_flagged_users()
        print(f'[+] Flagged users update complete. Added: {tta}, +Flagged: {ttaf}')
        asyncio.wait(300)

bot.run(config("token"))