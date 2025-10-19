import os
import json
import discord
import asyncio
import threading
from discord.ext import commands
from discord import app_commands
from database import db
import traceback


# Global configuration for backward compatibility (mainly for TOKEN)
config_version = 6
config_path = 'config.json'

default_config = {
    "config_version": config_version,
    "TOKEN": "YOUR_BOT_TOKEN_HERE",  # 機器人 Token
    "presence_loop_time": 10, # second
    "bot_status": "online", # online, idle, dnd, invisible
    "bot_activities": [
        {"type": "playing", "name": "Robot"}
    ],
    "owners": [123456789012345678],  # 機器人擁有者 ID 列表
    "prefix": "!",  # 指令前綴
    "r34_user_id": "",
    "r34_api_key": "",
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
    

modules = []


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix=config("prefix", "!"), intents=intents)


# Helper functions for per-server configuration
def get_server_config(guild_id: int, key: str, default=None):
    """Get server-specific configuration"""
    return db.get_server_config(guild_id, key, default)

def set_server_config(guild_id: int, key: str, value):
    """Set server-specific configuration"""
    return db.set_server_config(guild_id, key, value)

# User data functions
def get_user_data(guild_id: int, user_id: int, key: str, default=None):
    """Get user-specific data in a server"""
    return db.get_user_data(user_id, guild_id, key, default)

def set_user_data(guild_id: int, user_id: int, key: str, value):
    """Set user-specific data in a server"""
    return db.set_user_data(user_id, guild_id, key, value)

def get_all_user_data(guild_id: int, key: str):
    """Get all user-specific data for a specific key in a server"""
    return db.get_all_user_data(guild_id, key)

async def get_command_mention(command_name: str, subcommand_name: str = None):
    commands = await bot.tree.fetch_commands()
    for command in commands:
        if command.name == command_name:
            if subcommand_name:
                return command.mention.replace(f"/{command_name}", f"/{command_name} {subcommand_name}")
            return command.mention
    return None

translations = {
    "test": "測試",
    "admin": "管理",
    "multi-moderate": "多重操作",
    "ban": "封禁",
    "unban": "解封",
    "timeout": "禁言",
    "untimeout": "解除禁言",
    "kick": "踢出",
    "send-moderation-message": "發送懲處公告",
    "moderation-message-channel": "懲處公告頻道",
    "settings-punishment-notify": "設定-懲罰通知",
    "dsize-leaderboard": "dsize-排行榜",
    "dsize-battle": "dsize-對決",
    "dsize-settings": "dsize-設定",
    "dsize-feedgrass": "dsize-草飼",
    "item": "物品",
    "list": "列出",
    "use": "使用",
    "drop": "丟棄",
    "give": "給予",
    "remove": "移除",
    "listuser": "列出用戶",
    "view": "查看",
    "toggle": "切換",
    "automod": "自動管理",
    "settings": "設定",
    "escape_punish": "逃避責任懲處",
    "escape_punish-punishment": "逃避責任懲處-懲處",
    "escape_punish-duration": "逃避責任懲處-持續時間",
    "itemmod": "物品管理",
    "autopublish": "公告自動發布",
    "view": "查看",
    "info": "資訊",
    "randomnumber": "隨機數字",
    "randomuser": "隨機用戶",
    "report": "檢舉系統",
    "blacklist-role": "檢舉黑名單",
    "too_many_h1-max_length": "過多標題-最大允許長度",
    "too_many_h1-action": "過多標題-執行動作",
    "too_many_emojis-max_emojis": "過多表情符號-偵測數量",
    "too_many_emojis-action": "過多表情符號-執行動作",
    "check-action": "檢查動作",
    "dynamic-voice": "動態語音",
    "setup": "設置",
    "disable": "禁用",
    "play-audio": "播放音效",
    "bus": "公車",
    "getroute": "查詢路線",
    "True": "是",
    "False": "否",
    "item_id": "物品",
    "amount": "數量",
    "target_user": "目標用戶",
    "reason": "原因",
    "duration": "持續時間",
    "can_pickup": "可撿起",
    "pickup_only_once": "僅能撿起一次",
    "pickup_duration": "撿起持續時間",
    "setting": "設定項",
    "value": "值",
    "action": "動作",
    "enable": "啟用",
    "min": "最小值",
    "max": "最大值",
    "channel": "頻道",
    "channel_category": "頻道類別",
    "channel_name": "頻道名稱",
    "global": "全域",
    "global_dsize": "全域",
    "global_leaderboard": "全域排行榜",
    "opponent": "對手",
    "user": "用戶",
    "route_key": "路線代碼",
    "limit": "限制",
    "tag": "標籤",
    "tags": "標籤",
    "pid": "頁數",
    "userinfo": "用戶資訊",
    "get-command-mention": "取得指令提及",
    "command": "指令",
    "subcommand": "子指令",
    "textlength": "文字長度",
}
class CommandNameTranslator(app_commands.Translator):
    async def translate(
        self,
        string: app_commands.locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContext
    ):
        if locale == discord.Locale.taiwan_chinese:
            # print("DEBUG: Translate", type(context.data))
            # print("[DEBUG] Translating command/group:", context.data.name)
            # print("[DEBUG] Translated to:", translations.get(context.data.name, None))
            allowed_locations = [
                app_commands.TranslationContextLocation.command_name,
                app_commands.TranslationContextLocation.group_name,
                app_commands.TranslationContextLocation.choice_name,
                app_commands.TranslationContextLocation.parameter_name,
            ]
            if context.location not in allowed_locations:
                return None
            try:
                return translations.get(context.data.name, None)
            except Exception as e:
                pass
        return None


async def setup_hook():
    await bot.tree.set_translator(CommandNameTranslator())
    print("[+] Command translator set up.")


bot.setup_hook = setup_hook
on_ready_tasks = []
on_close_tasks = set()  # only works on !shutdown


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    try:
        synced = await bot.tree.sync()  # 同步指令
        print(f"Synced {len(synced)} command(s)")

        # 防止重複建立相同的 background task（例如 reconnect）
        if not getattr(bot, "_on_ready_tasks_started", False):
            for task_coro_func in on_ready_tasks:
                # task_coro_func 應該是 coroutine function，不是 coroutine object
                bot.loop.create_task(task_coro_func())
            bot._on_ready_tasks_started = True

    except Exception as e:
        print(f"Error syncing commands: {e}")
        traceback.print_exc()


def start_bot():
    bot.run(config("TOKEN"))