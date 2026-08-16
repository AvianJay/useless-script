import os
import json
import discord
import asyncio
import threading
import sys
from discord.ext import commands
from discord import app_commands
from database import db
import i18n
import traceback
import logging
from datetime import datetime
from pathlib import Path


# Global configuration for backward compatibility
config_version = 34
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
    "blacklist_api_key": "",
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
    "update_channel_id": 0,
    "disable_modules": [],
    "join_leave_log_channel_id": 0,
    # "lavalink_host": "localhost",  # decprecated, use lavalink_nodes instead
    # "lavalink_port": 2333,
    # "lavalink_password": "youshallnotpass",
    "oxwu_api": "http://localhost:5000",
    "oxwu_api_key": "",
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
    "pollinations_api_key": "",
    "economy_log_channel_id": 0,
    "backend_guild_id": 0,
    "dctw_api_key": "",
    "poe_api_key": "",
    "restart_command": "python all.py",
    "support_guild_id": 0,
}
_config = None
_runtime_logging_configured = False
_app_command_error_handlers = []
_app_command_error_dispatcher_installed = False
DEBUG_MODE = "--debug" in sys.argv

try:
    if os.path.exists(config_path):
        _config = json.load(open(config_path, "r", encoding="utf-8"))
        if not isinstance(_config, dict):
            print("[!] Config file is not a valid JSON object, resetting to default config.")
            _config = default_config.copy()
            os.rename(config_path, config_path + ".backup")
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
    os.rename(config_path, config_path + ".backup")
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

def configure_runtime_logging():
    """Configure stdlib logging so discord.py errors are visible."""
    global _runtime_logging_configured
    if _runtime_logging_configured:
        return

    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    runtime_log_path = logs_dir / f"runtime-{datetime.now().strftime('%Y-%m-%d')}.log"

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)

    has_stream_handler = any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        for handler in root_logger.handlers
    )
    has_runtime_file_handler = any(
        isinstance(handler, logging.FileHandler)
        and Path(getattr(handler, "baseFilename", "")).name == runtime_log_path.name
        for handler in root_logger.handlers
    )

    if not has_stream_handler:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)

    if not has_runtime_file_handler:
        file_handler = logging.FileHandler(runtime_log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    logging.getLogger("discord").setLevel(logging.INFO)
    logging.getLogger("discord.http").setLevel(logging.INFO)
    logging.getLogger("discord.gateway").setLevel(logging.INFO)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.captureWarnings(True)

    def _log_uncaught_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.getLogger("runtime").critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    def _log_thread_exception(args):
        logging.getLogger("runtime").critical(
            "Unhandled thread exception in %s",
            args.thread.name if args.thread else "unknown",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _log_uncaught_exception
    threading.excepthook = _log_thread_exception
    _runtime_logging_configured = True


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
bot = commands.Bot(command_prefix=config("prefix", "!"), intents=intents, chunk_guilds_at_startup=False, enable_debug_events=True, tree_cls=i18n.I18nCommandTree)
configure_runtime_logging()


def add_app_command_error_handler(handler):
    """Register an app command error handler without overwriting others."""
    global _app_command_error_dispatcher_installed

    if handler in _app_command_error_handlers:
        return

    _app_command_error_handlers.append(handler)

    if _app_command_error_dispatcher_installed:
        return

    async def _dispatch_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        for registered_handler in tuple(_app_command_error_handlers):
            try:
                await registered_handler(interaction, error)
            except Exception:
                command_name = interaction.command.qualified_name if getattr(interaction, "command", None) else "unknown"
                logging.getLogger("runtime").exception(
                    "App command error handler failed while handling %s",
                    command_name,
                )

    bot.tree.error(_dispatch_app_command_error)
    _app_command_error_dispatcher_installed = True


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


ECONOMY_GLOBAL_MODE_CONFIG_KEY = "economy_global_mode"


def is_forced_global_mode(guild_id: int | None) -> bool:
    if not guild_id:
        return False
    return bool(get_server_config(guild_id, ECONOMY_GLOBAL_MODE_CONFIG_KEY, False))


def interaction_uses_guild_scope(interaction: discord.Interaction) -> bool:
    guild = getattr(interaction, "guild", None)
    if not guild:
        return False
    if not interaction.is_guild_integration():
        return False
    return not is_forced_global_mode(guild.id)


def get_interaction_scope_guild_id(interaction: discord.Interaction) -> int:
    guild = getattr(interaction, "guild", None)
    if guild and interaction_uses_guild_scope(interaction):
        return guild.id
    return 0


# User data functions
def get_user_data(guild_id: int, user_id: int, key: str, default=None):
    """Get user-specific data in a server"""
    return db.get_user_data(user_id, guild_id, key, default)

def set_user_data(guild_id: int, user_id: int, key: str, value):
    """Set user-specific data in a server"""
    return db.set_user_data(user_id, guild_id, key, value)

def get_all_user_data(guild_id: int, key: str, value=None):
    """Get all user-specific data for a specific key in a server"""
    return db.get_all_user_data(guild_id, key, value)

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

fetched_emojis_cache = None

async def get_emoji_by_name(emoji_name: str) -> discord.Emoji | None:
    global fetched_emojis_cache
    if fetched_emojis_cache is None:
        fetched_emojis_cache = await bot.fetch_application_emojis()
    for emoji in fetched_emojis_cache:
        if emoji.name == emoji_name:
            return emoji
    return None

async def get_emoji_mention_by_name(emoji_name: str) -> str:
    emoji = await get_emoji_by_name(emoji_name)
    if emoji:
        return str(emoji)
    return f":{emoji_name}:"

async def reload_emojis_cache():
    global fetched_emojis_cache
    fetched_emojis_cache = await bot.fetch_application_emojis()

# 指令 metadata 在地化：locale_str(i18n_key=...) + locales/_commands.json
async def setup_hook():
    await bot.tree.set_translator(i18n.CatalogTranslator())
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


async def _main_with_runtime_logging():
    loop = asyncio.get_running_loop()

    def _asyncio_exception_handler(loop, context):
        exc = context.get("exception")
        logger = logging.getLogger("asyncio")
        if exc is not None:
            logger.error(context.get("message", "Unhandled asyncio exception"), exc_info=exc)
        else:
            logger.error("Unhandled asyncio exception: %s", context.get("message", context))

    loop.set_exception_handler(_asyncio_exception_handler)
    await _main()


def start_bot():
    log("正在啟動機器人...", module_name="Main")
    try:
        asyncio.run(_main_with_runtime_logging())
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
