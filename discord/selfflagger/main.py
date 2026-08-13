import selfcord
from selfcord.ext import commands
import database
from channel_selection import member_is_admin, select_most_visible_channel
from config_manager import (
    CONFIG_VERSION,
    ConfigError,
    command_access_allowed,
    load_config_file,
    normalize_config,
    save_config_file,
)
import asyncio
import random
from datetime import datetime, timezone

config_version = CONFIG_VERSION
config_path = 'config.json'
try:
    _config, config_needs_save = load_config_file(config_path)
except ConfigError as error:
    raise SystemExit(f"[!] Invalid config: {error}") from error
if config_needs_save:
    save_config_file(config_path, _config)

config_last_loaded_at = datetime.now(timezone.utc)
config_last_error = None
token_change_requires_restart = False
startup_token = _config.get("token", "")

def config(key, value=None, mode="r"):
    if mode == "r":
        return _config.get(key, value)
    elif mode == "w":
        updated_config = dict(_config)
        updated_config[key] = value
        updated_config = normalize_config(updated_config)
        save_config_file(config_path, updated_config)
        _config.clear()
        _config.update(updated_config)
        return True
    else:
        raise ValueError(f"Invalid mode: {mode}")

bot = commands.Bot(
    command_prefix=config("prefix", "!"),
    self_bot=True,
    owner_id=config("owner_id", 0) or None,
    help_command=None,
)
database.init_db()
conn = database.get_db_connection()

scan_lock = asyncio.Lock()
scan_task = None
scan_started_at = None
scan_completed_at = None
scan_last_error = None
scan_last_result = None
selected_channels = {}

async def update_flagged_users():
    scan_guilds = config("scan_guilds", [])
    scan_guilds = random.sample(scan_guilds, len(scan_guilds))
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
        try:
            selection = select_most_visible_channel(guild)
            if selection is None:
                print(
                    f"[!] No text channel visible to this account was found "
                    f"for guild {guild.name} (ID: {guild_id})."
                )
                continue
            viewable_channel, visible_count, cached_member_count = selection
            selected_channels[guild_id] = {
                "channel_id": viewable_channel.id,
                "channel_name": viewable_channel.name,
                "visible_count": visible_count,
                "cached_member_count": cached_member_count,
            }
            print(
                f"[+] Selected #{viewable_channel.name} (ID: {viewable_channel.id}); "
                f"visible to {visible_count}/{cached_member_count} cached members."
            )
            members = await guild.fetch_members(channels=[viewable_channel])
            for member in members:
                if member.bot:
                    # check bot verified
                    if member.public_flags.verified_bot:
                        continue
                if member.id == bot.user.id:
                    continue
                if member.id in config("ignored_users", []):
                    continue
                is_flagged = any(role.id in flagged_roles for role in member.roles)
                dt = database.get_flagged_user(conn, member.id, guild_id)
                if not dt:
                    this_time_added_count += 1
                    if is_flagged:
                        this_time_added_and_flagged_count += 1
                database.add_flagged_user(
                    conn,
                    member.id,
                    guild_id,
                    is_flagged,
                    member_is_admin(member),
                )
                count += 1
                if is_flagged:
                    count_flagged += 1
        except Exception as e:
            print(f"[!] Error fetching members for guild {guild.name} (ID: {guild_id}): {e}")
            continue
        print(f"[+] Updated flagged users for guild {guild_id}: {count_flagged}/{count} users flagged.")
        print("[!] Cooldown for 10 seconds to avoid rate limits...")
        await asyncio.sleep(10)
    return this_time_added_count, this_time_added_and_flagged_count


async def run_flagged_users_update():
    global scan_started_at, scan_completed_at, scan_last_error, scan_last_result
    async with scan_lock:
        scan_started_at = datetime.now(timezone.utc)
        scan_last_error = None
        try:
            scan_last_result = await update_flagged_users()
            return scan_last_result
        except Exception as error:
            scan_last_error = f"{type(error).__name__}: {error}"
            raise
        finally:
            scan_completed_at = datetime.now(timezone.utc)


async def scan_loop():
    while not bot.is_closed():
        try:
            print('[+] Starting flagged users update...')
            tta, ttaf = await run_flagged_users_update()
            print(f'[+] Flagged users update complete. Added: {tta}, +Flagged: {ttaf}')
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"[!] Flagged users update failed: {type(error).__name__}: {error}")
        await asyncio.sleep(300)


async def subscribe_configured_guilds():
    subscribed = 0
    missing = []
    for guild_info in config("scan_guilds", []):
        guild_id = guild_info.get("id")
        guild = bot.get_guild(guild_id)
        if guild is None:
            missing.append(guild_id)
            continue
        await guild.subscribe()
        subscribed += 1
        print(f'[+] Subscribed to guild: {guild.name} (ID: {guild.id})')
    return subscribed, missing

# other event to add flagged users
@bot.event
async def on_message(message: selfcord.Message):
    await bot.process_commands(message)
    if message.author.id != bot.user.id:
        return
    if message.author.bot:
        # check bot verified
        if message.author.public_flags.verified_bot:
            return
    if message.guild is None:
        return
    if message.author.id in config("ignored_users", []):
        return
    scan_guilds = config("scan_guilds", [])
    guild_info = next((g for g in scan_guilds if g.get("id") == message.guild.id), None)
    if guild_info is None:
        return
    if database.get_flagged_user(conn, message.author.id, message.guild.id):
        return
    flagged_roles = guild_info.get("flagged_roles", [])
    is_flagged = any(role.id in flagged_roles for role in message.author.roles)
    database.add_flagged_user(
        conn,
        message.author.id,
        message.guild.id,
        is_flagged,
        member_is_admin(message.author),
    )
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
    if member.id in config("ignored_users", []):
        return
    if member.bot:
        # check bot verified
        if member.public_flags.verified_bot:
            return
    if database.get_flagged_user(conn, member.id, member.guild.id):
        return
    flagged_roles = guild_info.get("flagged_roles", [])
    is_flagged = any(role.id in flagged_roles for role in member.roles)
    database.add_flagged_user(
        conn,
        member.id,
        member.guild.id,
        is_flagged,
        member_is_admin(member),
    )
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
    if after.id in config("ignored_users", []):
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
    before_admin = member_is_admin(before)
    after_admin = member_is_admin(after)
    if before_flagged != after_flagged or before_admin != after_admin:
        database.add_flagged_user(
            conn,
            after.id,
            after.guild.id,
            after_flagged,
            after_admin,
        )
        print(
            f"[+] Updated status for user {after} (ID: {after.id}) in guild "
            f"{after.guild.name} (ID: {after.guild.id}): "
            f"{'Flagged' if after_flagged else 'Not Flagged'}, "
            f"{'Admin' if after_admin else 'Not Admin'}"
        )

last_update_users = {}

def clean_up_last_update_users():
    for user_id in list(last_update_users.keys()):
        if (datetime.now(timezone.utc) - last_update_users[user_id]).total_seconds() > 60:
            del last_update_users[user_id]

@bot.event
async def on_presence_update(before: selfcord.Relationship, after: selfcord.Member):
    # get same guilds
    user = after if isinstance(after, selfcord.Member) else after.user
    clean_up_last_update_users()
    if user.id in last_update_users:
        if (datetime.now(timezone.utc) - last_update_users[user.id]).total_seconds() < 60:
            return
    last_update_users[user.id] = datetime.now(timezone.utc)
    profile = await user.profile()
    mutual_guilds = profile.mutual_guilds
    if not mutual_guilds:
        return
    scan_guilds = config("scan_guilds", [])
    for mutual_guild in mutual_guilds:
        guild_info = next((g for g in scan_guilds if g.get("id") == mutual_guild.id), None)
        if guild_info is None:
            return
        if user.id == bot.user.id:
            return
        if user.id in config("ignored_users", []):
            return
        if user.bot:
            # check bot verified
            if user.public_flags.verified_bot:
                return
        # if database.get_flagged_user(conn, after.id, after.guild.id):
        #     return
        flagged_roles = guild_info.get("flagged_roles", [])
        member = mutual_guild.guild.get_member(user.id)
        if member is None:
            return
        flagged = any(role.id in flagged_roles for role in member.roles)
        database.add_flagged_user(
            conn,
            member.id,
            mutual_guild.id,
            flagged,
            member_is_admin(member),
        )
        print(f"[+] Updated flagged status for user {member} (ID: {member.id}) in guild {mutual_guild.guild.name} (ID: {mutual_guild.id}): {'Flagged' if flagged else 'Not Flagged'}")

def command_access():
    async def predicate(ctx):
        return command_access_allowed(
            author_id=ctx.author.id,
            self_user_id=getattr(bot.user, "id", 0),
            owner_id=config("owner_id", 0),
            guild_id=getattr(ctx.guild, "id", None),
            command_guild_id=config("command_guild_id", 0),
        )
    return commands.check(predicate)


def format_timestamp(value):
    if value is None:
        return "尚未"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def database_status():
    row = conn.execute('''
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN flagged_role = 1 THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN is_admin = 1 THEN 1 ELSE 0 END), 0),
            COUNT(DISTINCT guild_id)
        FROM flagged_users
    ''').fetchone()
    return {
        "total": row[0],
        "flagged": row[1],
        "admins": row[2],
        "guilds": row[3],
    }


def reload_runtime_config():
    global config_last_loaded_at, config_last_error, token_change_requires_restart
    try:
        updated_config, needs_save = load_config_file(
            config_path,
            allow_missing=False,
        )
        if needs_save:
            save_config_file(config_path, updated_config)
    except (ConfigError, OSError) as error:
        config_last_error = f"{type(error).__name__}: {error}"
        return False, config_last_error

    token_changed = updated_config.get("token") != startup_token
    _config.clear()
    _config.update(updated_config)
    bot.command_prefix = updated_config["prefix"]
    bot.owner_id = updated_config["owner_id"] or None
    config_last_loaded_at = datetime.now(timezone.utc)
    config_last_error = None
    token_change_requires_restart = token_changed
    configured_guild_ids = {
        guild_info["id"] for guild_info in updated_config["scan_guilds"]
    }
    for guild_id in list(selected_channels):
        if guild_id not in configured_guild_ids:
            del selected_channels[guild_id]
    return True, token_changed


# commands
@bot.command(aliases=["指令"])
@command_access()
async def help(ctx):
    prefix = config("prefix", ">")
    await ctx.send(
        "Selfflagger 指令：\n"
        f"`{prefix}status` - 顯示掃描、資料庫與設定狀態\n"
        f"`{prefix}reloadconfig` - 不重啟重新載入 config.json\n"
        f"`{prefix}updateflags` - 立即執行一次掃描\n"
        f"`{prefix}ping` - 顯示延遲\n"
        f"`{prefix}shutdown` - 關閉程式"
    )


@bot.command(aliases=["狀態"])
@command_access()
async def status(ctx):
    try:
        db_status = database_status()
        database_line = (
            f"{db_status['total']} 筆 / {db_status['flagged']} 標記 / "
            f"{db_status['admins']} 管理員 / {db_status['guilds']} 個伺服器"
        )
    except Exception as error:
        database_line = f"讀取失敗：{type(error).__name__}"

    command_guild_id = config("command_guild_id", 0)
    allowed_guild = bot.get_guild(command_guild_id) if command_guild_id else None
    allowed_guild_line = (
        f"{allowed_guild.name} ({command_guild_id})"
        if allowed_guild
        else str(command_guild_id or "未限制")
    )
    if scan_lock.locked():
        scan_state = f"掃描中（開始：{format_timestamp(scan_started_at)}）"
    elif scan_last_error:
        scan_state = f"上次失敗：{scan_last_error[:200]}"
    elif scan_last_result is None:
        scan_state = "尚未完成掃描"
    else:
        scan_state = (
            f"閒置；上次新增 {scan_last_result[0]}，"
            f"其中標記 {scan_last_result[1]}"
        )

    channel_lines = []
    for guild_id, selected in sorted(selected_channels.items()):
        channel_lines.append(
            f"- {guild_id}: #{selected['channel_name'][:50]} ({selected['channel_id']}), "
            f"可見 {selected['visible_count']}/{selected['cached_member_count']}"
        )
    channel_text = "\n".join(channel_lines[:5]) or "- 尚未選擇"
    if len(channel_lines) > 5:
        channel_text += f"\n- 另有 {len(channel_lines) - 5} 個伺服器"

    await ctx.send(
        "**Selfflagger 狀態**\n"
        f"掃描：{scan_state}\n"
        f"上次完成：{format_timestamp(scan_completed_at)}\n"
        f"資料庫：{database_line}\n"
        f"掃描伺服器：{len(config('scan_guilds', []))}\n"
        f"指令伺服器：{allowed_guild_line}\n"
        f"Prefix：`{config('prefix', '>')}`\n"
        f"設定載入：{format_timestamp(config_last_loaded_at)}\n"
        f"設定錯誤：{config_last_error[:200] if config_last_error else '無'}\n"
        f"Token 狀態：{'已變更，需重啟' if token_change_requires_restart else '目前連線使用啟動時設定'}\n"
        "自動選擇頻道：\n"
        f"{channel_text}"
    )


@bot.command(aliases=["reload", "重載設定"])
@command_access()
async def reloadconfig(ctx):
    success, result = reload_runtime_config()
    if not success:
        await ctx.send(f"設定重新載入失敗，已保留原設定：`{result}`")
        return
    try:
        subscribed, missing = await subscribe_configured_guilds()
        subscription_note = f"；已訂閱 {subscribed} 個掃描伺服器"
        if missing:
            subscription_note += f"，找不到 {len(missing)} 個"
    except Exception as error:
        subscription_note = f"；訂閱更新失敗：{type(error).__name__}"
    token_note = "；token 有變更，需重啟才會生效" if result else ""
    await ctx.send(
        f"設定已重新載入（版本 {config_version}、prefix `{config('prefix')}`）"
        f"{subscription_note}{token_note}。"
    )


@bot.command()
@command_access()
async def ping(ctx):
    try:
        latency = bot.latency * 1000  # Convert to milliseconds
    except OverflowError:
        latency = float('NaN')
    await ctx.send('Pong! Latency: {:.2f} ms'.format(latency))

@bot.command(aliases=["關機"])
@command_access()
async def shutdown(ctx):
    await ctx.send('Shutting down...')
    if scan_task and not scan_task.done():
        scan_task.cancel()
    await bot.close()

@bot.command(aliases=["scan", "更新標記"])
@command_access()
async def updateflags(ctx):
    if scan_lock.locked():
        await ctx.send('目前已有掃描正在執行，請用 status 查看進度。')
        return
    await ctx.send('Updating flagged users...')
    try:
        tta, ttaf = await run_flagged_users_update()
    except Exception as error:
        await ctx.send(f'Flagged users update failed: `{type(error).__name__}: {error}`')
        return
    await ctx.send(f'Flagged users update complete. Added: {tta}, +Flagged: {ttaf}')


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        allowed_authors = {
            user_id
            for user_id in (config("owner_id", 0), getattr(bot.user, "id", 0))
            if user_id
        }
        if ctx.author.id not in allowed_authors:
            return
        if ctx.guild is None:
            await ctx.send("Selfflagger 指令不能在私訊使用。")
            return
        allowed_guild_id = config("command_guild_id", 0)
        if allowed_guild_id and ctx.guild.id != allowed_guild_id:
            await ctx.send(f"Selfflagger 指令只能在伺服器 `{allowed_guild_id}` 使用。")
        return
    await ctx.send(f"指令執行失敗：`{type(error).__name__}: {error}`")

@bot.event
async def on_ready():
    global scan_task
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    _, missing_guild_ids = await subscribe_configured_guilds()
    for guild_id in missing_guild_ids:
        print(f'[!] Could not find guild with ID: {guild_id}')
    if scan_task is None or scan_task.done():
        scan_task = asyncio.create_task(scan_loop(), name="selfflagger-scan-loop")

bot.run(config("token"))
