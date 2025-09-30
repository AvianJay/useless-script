import time
import discord
from globalenv import bot, start_bot, config, on_ready_tasks

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
        name = act.get("name")
        type_str = act.get("type", "playing").lower()
        type_enum = activity_type_map.get(type_str, discord.ActivityType.playing)
        if name:
            activities.append(discord.Activity(type=type_enum, name=name))

async def set_presence():
    await bot.wait_until_ready()
    while not bot.is_closed():
        if not activities:
            await bot.change_presence(status=status, activity=None)
            time.sleep(config("presence_loop_time"))
        for activity in activities:
            await bot.change_presence(status=status, activity=activity)
            time.sleep(config("presence_loop_time"))
        load_config()
        
on_ready_tasks.append(set_presence)


if __name__ == "__main__":
    start_bot()
