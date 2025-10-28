import time
import asyncio
import discord
import random
from globalenv import bot, start_bot, config, on_ready_tasks, modules
from logger import log
import logging
if "UtilCommands" in modules:
    import UtilCommands
else:
    log("UtilCommands module not found, some features may be missing.", level=logging.WARNING, module_name="PresenceChange")
    UtilCommands = None

status_map = {
    "online": discord.Status.online,
    "idle": discord.Status.idle,
    "dnd": discord.Status.dnd,
    "invisible": discord.Status.invisible
}
status = None
activity_type_map = {
    "playing": discord.ActivityType.playing,
    "streaming": discord.ActivityType.streaming,
    "listening": discord.ActivityType.listening,
    "watching": discord.ActivityType.watching,
    "competing": discord.ActivityType.competing,
}
activities = []

def load_config():
    global status, activities
    status = status_map.get(config("bot_status", "online"), discord.Status.online)
    activities = []
    for act in config("bot_activities", []):
        if not isinstance(act, dict):
            continue
        if bot.latency:
            try:
                bot_latency = round(bot.latency * 1000)  # Convert to milliseconds
                bot_latency = str(bot_latency) + "ms"
            except OverflowError:
                bot_latency = "N/A"
        else:
            bot_latency = "N/A"
        uptime_str = "N/A"
        if UtilCommands:
            try:
                uptime_str = UtilCommands.get_time_text(UtilCommands.get_uptime_seconds())
            except Exception:
                uptime_str = "N/A"
        name = act.get("name")
        name = name.replace("{prefix}", config("prefix", "!"))
        name = name.replace("{bot_name}", bot.user.name if bot.user else "Bot")
        name = name.replace("{version}", UtilCommands.version if UtilCommands else "unknown")
        name = name.replace("{user_count}", str(len(bot.users)))
        name = name.replace("{guild_count}", str(len(bot.guilds)))
        name = name.replace("{latency_ms}", bot_latency)
        name = name.replace("{random_number_1_100}", str(random.randint(1, 100)))
        name = name.replace("{full_version}", UtilCommands.full_version if UtilCommands else "unknown")
        name = name.replace("{uptime}", uptime_str)
        type_str = act.get("type", "playing").lower()
        type_enum = activity_type_map.get(type_str, discord.ActivityType.playing)
        if name:
            activities.append(discord.Activity(type=type_enum, name=name))

async def set_presence():
    await bot.wait_until_ready()
    log("狀態更新任務已啟動", module_name="PresenceChange")
    # 若你有可能在 reconnect 時重複啟動，請在 on_ready 那裡用 flag 防止重複 create_task
    while not bot.is_closed():
        try:
            load_config()
        except Exception as e:
            log(f"重新載入設定時發生錯誤: {e}", level=logging.ERROR, module_name="PresenceChange")

        try:
            loop_time = float(config("presence_loop_time"))
        except Exception:
            loop_time = 60.0  # 預設

        if not activities:
            try:
                await bot.change_presence(status=status, activity=None)
                # print("[+] Status set to", status)
            except Exception as e:
                log(f"無法改變狀態: {e}", level=logging.ERROR, module_name="PresenceChange")
            await asyncio.sleep(loop_time)
            continue

        for activity in activities:
            try:
                await bot.change_presence(status=status, activity=activity)
                # print(f"[+] Status set to {status}, activity: {activity.type.name} {activity.name}")
            except Exception as e:
                log(f"無法改變狀態: {e}", level=logging.ERROR, module_name="PresenceChange")
            await asyncio.sleep(loop_time)


load_config()
on_ready_tasks.append(set_presence)


if __name__ == "__main__":
    start_bot()
