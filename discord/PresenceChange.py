import time
import asyncio
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
    print("[+] 狀態更新任務已啟動")
    # 若你有可能在 reconnect 時重複啟動，請在 on_ready 那裡用 flag 防止重複 create_task
    while not bot.is_closed():
        try:
            loop_time = float(config("presence_loop_time"))
        except Exception:
            loop_time = 60.0  # 預設

        if not activities:
            try:
                await bot.change_presence(status=status, activity=None)
                # print("[+] Status set to", status)
            except Exception as e:
                print(f"[!] 無法改變狀態: {e}")
            await asyncio.sleep(loop_time)
            continue

        for activity in activities:
            try:
                await bot.change_presence(status=status, activity=activity)
                # print(f"[+] Status set to {status}, activity: {activity.type.name} {activity.name}")
            except Exception as e:
                print(f"[!] 無法改變狀態: {e}")
            await asyncio.sleep(loop_time)

        # 如果 load_config() 是同步且輕量（只讀記憶體），可直接呼叫
        # 若會做檔案/網路 I/O，改用 executor：
        try:
            load_config()
        except Exception as e:
            print(f"[!] 重新載入設定時發生錯誤: {e}")

load_config()
on_ready_tasks.append(set_presence)


if __name__ == "__main__":
    start_bot()
