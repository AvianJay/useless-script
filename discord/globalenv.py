import os
import json
import discord
import asyncio
import threading
from discord.ext import commands
from discord import app_commands
from database import db
import traceback
import logging


# Global configuration for backward compatibility
config_version = 24
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
    "flagged_database_path": "flagged_data.db",
    "default_favorite_stops_limit": 2,
    "default_favorite_youbike_limit": 2,
    "log_channel_id": 123456789012345678,
    "feedback_message_channel_id": 123456789012345678,
    "botcustomizer_log_channel_id": 123456789012345678,
    "webserver_host": "0.0.0.0",
    "webserver_port": 8080,
    "webserver_ssl": False,
    "webverify_recaptcha_key": "",
    "webverify_recaptcha_secret": "",
    "webverify_turnstile_key": "",
    "webverify_turnstile_secret": "",
    "webverify_url": "http://localhost:8080/server-verify",
    "client_secret": "",
    "process_monitor_channel_id": 0,
    "process_monitor_alert_channel_id": 0,
    "cpu_usage_threshold": 80,
    "memory_usage_threshold": 80,
    "support_server_invite": "https://discord.gg/your-invite-link",
    "support_email": "support@example.com",
    "website_url": "http://localhost:8080",
    "website_gtag": "",
    "contribute_channel_id": 0,
    "disable_modules": [],
    "join_leave_log_channel_id": 0,
    # "lavalink_host": "localhost",  # decprecated, use lavalink_nodes instead
    # "lavalink_port": 2333,
    # "lavalink_password": "youshallnotpass",
    "oxwu_api": "http://localhost:10281",
    "temp_channel_id": 123456789012345678,
    "lavalink_nodes": [
        {
            "id": "MAIN",
            "host": "localhost",
            "port": 2333,
            "password": "youshallnotpass",
            "name": "Default Node"
        }
    ],
    "upvote_board_channel_id": 0,
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


def reload_config():
    global _config
    try:
        if os.path.exists(config_path):
            _config = json.load(open(config_path, "r", encoding="utf-8"))
            log("設定檔已重新載入。", module_name="Main")
            return True
        else:
            log("設定檔不存在。", module_name="Main", level=logging.WARNING)
            return False
    except Exception as e:
        log(f"重新載入設定檔時發生錯誤: {e}", module_name="Main", level=logging.ERROR)
        return False

modules = []
failed_modules = []

# ============= Panel Settings Registry =============
# Allows any module to register its server settings for the web panel.
# Modules can call register_panel_settings() at import time.
panel_settings = {}

def register_panel_settings(module_name: str, display_name: str, module_settings: list, description: str = "", icon: str = "⚙️"):
    """
    Register settings for the web panel.

    Args:
        module_name: Internal module name (e.g. "ReportSystem")
        display_name: Display name (e.g. "檢舉系統")
        module_settings: List of setting dicts with keys:
            - display (str): Setting display name
            - description (str, optional): Help text
            - database_key (str): Key used in get/set_server_config
            - type (str): channel | voice_channel | category | role | role_list | boolean | string | number | float | text | select
            - default: Default value
            - options (list, optional): For 'select' type, [{"label": str, "value": any}, ...]
            - min / max (number, optional): For number/float type
        description: Module description
        icon: Emoji icon
    """
    panel_settings[module_name] = {
        "display_name": display_name,
        "description": description,
        "icon": icon,
        "settings": module_settings,
    }


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix=config("prefix", "!"), intents=intents, chunk_guilds_at_startup=False)


# Helper functions for per-server configuration
def get_server_config(guild_id: int, key: str, default=None):
    """Get server-specific configuration"""
    return db.get_server_config(guild_id, key, default)

def set_server_config(guild_id: int, key: str, value):
    """Set server-specific configuration"""
    return db.set_server_config(guild_id, key, value)

def get_all_server_config_key(key: str):
    """Get all server-specific configuration for a specific key"""
    return db.get_all_server_config_key(key)


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

def get_global_config(key: str, default=None):
    return db.get_global_config(key, default)

def set_global_config(key: str, value):
    return db.set_global_config(key, value)

def get_db_connection():
    """Get a new database connection"""
    return db.get_connection()

fetched_commands_cache = None

async def get_command_mention(command_name: str, subcommand_name: str = None):
    global fetched_commands_cache
    if fetched_commands_cache is None:
        fetched_commands_cache = await bot.tree.fetch_commands()
    for command in fetched_commands_cache:
        if command.name == command_name:
            if subcommand_name:
                return command.mention.replace(f"/{command_name}", f"/{command_name} {subcommand_name}")
            return command.mention
    return None

translations = {
    "test": "測試",
    "admin": "管理",
    "multi-moderate": "多使用者懲處",
    "multi-moderate-action": "多重操作",
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
    "dsize-history": "dsize-歷史紀錄",
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
    "scamtrap": "詐騙陷阱",
    "scamtrap-channel_id": "詐騙陷阱-頻道ID",
    "scamtrap-action": "詐騙陷阱-執行動作",
    "itemmod": "物品管理",
    "addcustom": "新增自定義物品",
    "editcustom": "編輯自定義物品",
    "removecustom": "移除自定義物品",
    "listcustom": "列出自定義物品",
    "list_in_shop": "上架商店",
    "price": "定價",
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
    "text": "文字",
    # "httpcat": "網路貓咪",
    "status_code": "狀態碼",
    # "youbike": "YouBike",
    "getstop": "查詢站點",
    "stop_id": "站點代碼",
    "station_name": "站點名稱",
    "favorites": "最愛",
    "autoreply": "自動回覆",
    "add": "新增",
    "contains": "包含",
    "equals": "完全匹配",
    "starts_with": "開始於",
    "ends_with": "結束於",
    "trigger": "觸發字串",
    "response": "回覆內容",
    "mode": "模式",
    "edit": "編輯",
    "new_mode": "新模式",
    "new_trigger": "新觸發字串",
    "new_response": "新回覆內容",
    "clear": "清除",
    "scan-flagged-users": "掃描標記用戶",
    "reply": "回覆",
    "channel_mode": "頻道模式",
    "channels": "頻道",
    "blacklist": "黑名單",
    "whitelist": "白名單",
    "all": "全部",
    "flagged-user-alert-channel": "標記用戶警報頻道",
    "mention": "提及",
    "query": "關鍵字",
    "dsize-stats": "dsize-統計資料",
    "set-server-rules": "設定伺服器規則",
    "rules": "規則",
    "quickadd": "快速新增",
    "import": "匯入",
    "export": "匯出",
    "file": "檔案",
    "merge": "合併",
    "avatar": "頭像",
    "image": "圖片",
    "random_chance": "回覆機率",
    "banner": "橫幅",
    "bio": "介紹",
    "feedback": "回饋箱",
    "change": "更改",
    "blacklist-role": "黑名單身分組",
    "unblacklist-role": "解除黑名單身分組",
    "set-log-channel": "設置日誌頻道",
    "view-blacklist-roles": "查看黑名單身分組",
    "nds": "停班停課",
    "follow": "追蹤",
    "reverse": "反轉",
    "spoilers": "暴雷",
    "changelog": "更新日誌",
    "global_history": "全域歷史紀錄",
    "webverify": "網頁驗證",
    "status": "狀態",
    "set_captcha": "設定人機驗證",
    "captcha_provider": "人機驗證提供者",
    "send_verify_message": "發送驗證提示",
    "title": "標題",
    "message": "訊息",
    "set_unverified_role": "設定未驗證身分組",
    "role": "身分組",
    "check_relation": "檢查關聯帳號",
    "autorole": "自動身分組",
    "create_unverified_role": "建立未驗證身分組",
    "name": "名稱",
    "quick_setup": "快速設定",
    "verify_notify": "驗證通知",
    "type": "類型",
    "relation_action": "關聯用戶動作",
    "delete_message": "刪除訊息",
    "fake": "假冒",
    "fake-log-channel": "假冒日誌頻道",
    "contribute": "投稿",
    "feedgrass": "草飼",
    "what-is-this-guy-talking-about": "這傢伙在說什麼呢",
    "minage": "最小帳號年齡",
    "country-alert": "地區警示設定",
    "manual-check-country": "手動檢查地區",
    "countries": "國家列表",
    "fake-blacklist": "假冒黑名單",
    "ping": "延遲",
    "explore space": "探索空間",
    "explore": "探索",
    "explore-settings": "探索設定",
    "privacy": "隱私",
    "enabled": "啟用",
    "public": "公開",
    "serverinfo": "伺服器資訊",
    "help": "幫助",
    "stats": "統計資料",
    "user-appeal-channel": "用戶申訴頻道",
    "full": "完整",
    "music": "音樂",
    "play": "播放",
    "pause": "暫停",
    "resume": "繼續",
    "skip": "跳過",
    "stop": "停止",
    "queue": "佇列",
    "now-playing": "正在播放",
    "volume": "音量",
    "ai-clear": "ai-清除",
    "ai-history": "ai-歷史紀錄",
    "git-commits": "git-提交紀錄",
    "new_conversation": "新對話",
    "set-alert-channel": "設定速報頻道",
    "set-report-channel": "設定報告頻道",
    "query-report": "查詢報告",
    "query-warning": "查詢速報",
    "screenshot": "截圖",
    "earthquake": "地震",
    "dynamic-voice-audio": "動態語音音效",
    "stickyrole": "黏黏的身份組",
    "ignore-bots": "忽略機器人",
    "set-log-channel": "設定日誌頻道",
    "clear-user": "清除用戶紀錄",
    # Economy system
    "economy": "經濟",
    "balance": "餘額",
    "daily": "每日獎勵",
    "pay": "轉帳",
    "exchange": "兌換",
    "buy": "購買",
    "sell": "賣出",
    "shop": "商店",
    "trade": "交易",
    "leaderboard": "排行榜",
    "economymod": "經濟管理",
    "setrate": "設定匯率",
    "setname": "設定名稱",
    "setdaily": "設定每日獎勵",
    "setsellratio": "設定賣出比率",
    "reset": "重置",
    "currency": "貨幣類型",
    "direction": "方向",
    "offer_item": "提供物品",
    "offer_item_amount": "提供物品數量",
    "offer_money": "提供金額",
    "request_item": "要求物品",
    "request_item_amount": "要求物品數量",
    "request_money": "要求金額",
    "scope": "範圍",
    "to-global": "轉到全域",
    "to-server": "轉到伺服器",
    "rate": "匯率",
    "ratio": "比率",
    "nodes": "節點",
    "global_feedgrass": "全域草飼",
    "shuffle": "隨機",
    "hourly": "每小時獎勵",
    "global_daily": "全域每日獎勵",
    "global_hourly": "全域每小時獎勵",
    "upvoteboard": "有料板子",
    "recommend": "推薦",
    "anti_uispam-max_count": "反用戶安裝垃圾訊息-最大數量",
    "anti_uispam-time_window": "反用戶安裝垃圾訊息-時間窗口",
    "anti_uispam-action": "反用戶安裝垃圾訊息-執行動作",
    "tutorial": "使用教學",
    "panel": "面板",
    "joinnotify": "邀請機器人通知",
    "option": "選項",
    "force-verify": "強制驗證",
    "start-force-verify": "開始強制驗證",
    "anti_raid-max_joins": "防突襲（大量加入偵測）-最大加入數量",
    "anti_raid-time_window": "防突襲（大量加入偵測）-時間窗口",
    "anti_raid-action": "防突襲（大量加入偵測）-執行動作",
    "anti_spam-max_messages": "防刷頻-最大訊息數量",
    "anti_spam-time_window": "防刷頻-時間窗口",
    "anti_spam-similarity": "防刷頻-相似度",
    "anti_spam-action": "防刷頻-執行動作",
    "games": "遊戲",
    "big2": "大老二",
    "tower": "爬塔",
    "bet": "賭注",
    # action-builder, automod setup, Moderate 指令與參數
    "action-builder": "動作產生器",
    "users": "目標用戶",
    "action_type": "動作類型",
    "delete_message_duration": "刪除訊息時長",
    "prepend": "前置指令",
    "feature": "功能",
    "max_length": "最大標題字數",
    "max_emojis": "最大表情符號數",
    "max_count": "最大觸發次數",
    "time_window": "偵測時間窗口",
    "max_joins": "最大加入數",
    "max_messages": "最大訊息數",
    "similarity": "相似度閾值",
    "punishment": "懲處方式",
    "moderator": "執行管理員",
    "quick-setup": "快速設定",
    "toggle-flow": "切換流通",
    "count": "數量",
    "restore-queue": "回復播放隊列",
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
    log("指令翻譯器已設定。", module_name="Main")


bot.setup_hook = setup_hook
on_ready_tasks = []
on_close_tasks = set()  # only works on !shutdown


@bot.event
async def on_ready():
    log(f'已登入為 {bot.user}', module_name="Main")
    try:
        if "Explore" in modules:
            from Explore import activity_entry
            bot.tree._global_commands["launch"] = activity_entry
        synced = await bot.tree.sync()  # 同步指令
        log(f"已同步 {len(synced)} 個指令", module_name="Main")
        if "Explore" in modules:
            del bot.tree._global_commands["launch"]

        # 快取所有伺服器的成員資料
        for guild in bot.guilds:
            if not guild.chunked:
                await guild.chunk()
        log(f"成功快取 {len(bot.guilds)} 個伺服器的成員資料。", module_name="Main")

        # 防止重複建立相同的 background task（例如 reconnect）
        if not getattr(bot, "_on_ready_tasks_started", False):
            for task_coro_func in on_ready_tasks:
                # task_coro_func 應該是 coroutine function，不是 coroutine object，啥ai東西啊
                bot.loop.create_task(task_coro_func())
            bot._on_ready_tasks_started = True

    except Exception as e:
        log("同步指令時發生錯誤:", str(e), module_name="Main")
        traceback.print_exc()

def log(*messages, level = logging.INFO, module_name: str = "General", user: discord.User = None, guild: discord.Guild = None):
    if "logger" in modules:
        import logger
        logger.log(*messages, level=level, module_name=module_name, user=user, guild=guild)


async def _run_close_tasks():
    """執行所有關閉任務"""
    # 先 flush logs
    try:
        from logger import flush_logs
        await flush_logs()
    except Exception:
        pass
    
    if on_close_tasks:
        print("[Main] 正在執行關閉前任務...")
        for task in on_close_tasks:
            try:
                print(f"[Main] 正在執行關閉前任務：{task.__name__}...")
                await task()
            except Exception as e:
                print(f"[Main] 關閉前任務發生錯誤：{e}")


async def _main():
    """主程式進入點，處理 bot 生命週期"""
    async with bot:
        await bot.start(config("TOKEN"))


def start_bot():
    log("正在啟動機器人...", module_name="Main")
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("[Main] 收到 Ctrl+C，正在關閉機器人...")
    finally:
        # 確保關閉任務被執行
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_run_close_tasks())
            if not bot.is_closed():
                loop.run_until_complete(bot.close())
            loop.close()
        except Exception as e:
            print(f"[Main] 關閉時發生錯誤：{e}")