from globalenv import (
    bot, get_server_config, set_server_config, modules, config,
    panel_settings, register_panel_settings,
)
from logger import log

if "Website" not in modules:
    raise ImportError("Website module is required for GuildPanel")

from Website import app
from flask import request, redirect, session, jsonify, render_template, url_for
import requests as http_requests
import os
import json
import urllib.parse
import discord
from discord import app_commands
from discord.ext import commands
import asyncio

# ============= Settings alias =============
# The canonical dict lives in globalenv so any module can register before GuildPanel loads.
settings = panel_settings

# Re-export for convenience: modules loaded AFTER GuildPanel can do
#   from GuildPanel import register_settings
register_settings = register_panel_settings

# ============= Flask session secret =============
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    config("client_secret", "please-change-this-secret"),
)

# ============= Server-side guild cache =============
# Keyed by user ID -> list of {id, permissions}
_guild_cache: dict[str, list[dict]] = {}

# ============= Discord OAuth2 =============
DISCORD_API = "https://discord.com/api/v10"
OAUTH2_CLIENT_SECRET = config("client_secret", "")
OAUTH2_REDIRECT_URI = config("website_url", "http://localhost:8080").rstrip("/") + "/panel/callback"
OAUTH2_SCOPES = "identify guilds"
MANAGE_GUILD = 0x20


def _get_oauth2_url():
    params = urllib.parse.urlencode({
        "client_id": str(bot.user.id),
        "redirect_uri": OAUTH2_REDIRECT_URI,
        "response_type": "code",
        "scope": OAUTH2_SCOPES,
        "prompt": "none",
    })
    return f"{DISCORD_API}/oauth2/authorize?{params}"


def _exchange_code(code: str):
    return http_requests.post(
        f"{DISCORD_API}/oauth2/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": OAUTH2_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        auth=(str(bot.user.id), OAUTH2_CLIENT_SECRET),
    ).json()


def _get_user(token: str):
    return http_requests.get(
        f"{DISCORD_API}/users/@me",
        headers={"Authorization": f"Bearer {token}"},
    ).json()


def _get_guilds(token: str):
    return http_requests.get(
        f"{DISCORD_API}/users/@me/guilds",
        headers={"Authorization": f"Bearer {token}"},
    ).json()


def _has_manage(perms: int) -> bool:
    """MANAGE_GUILD or ADMINISTRATOR"""
    return bool(perms & MANAGE_GUILD) or bool(perms & 0x8)


def _current_user():
    return session.get("panel_user")


def _current_guilds():
    """Get guilds from server-side cache (not from cookie)."""
    user = _current_user()
    if not user:
        return []
    return _guild_cache.get(user["id"], [])


# ============= Auth decorators =============

def _require_auth(f):
    from functools import wraps

    @wraps(f)
    def wrapper(*args, **kwargs):
        if _current_user() is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("panel_login"))
        return f(*args, **kwargs)

    return wrapper


def _require_guild(f):
    from functools import wraps

    @wraps(f)
    def wrapper(*args, **kwargs):
        guild_id = kwargs.get("guild_id") or (args[0] if args else None)
        if _current_user() is None:
            return jsonify({"error": "Unauthorized"}), 401
        # permission check
        guilds = _current_guilds()
        guild = next((g for g in guilds if str(g["id"]) == str(guild_id)), None)
        if guild is None or not _has_manage(guild.get("permissions", 0)):
            return jsonify({"error": "Forbidden"}), 403
        # bot membership check
        if bot.get_guild(int(guild_id)) is None:
            return jsonify({"error": "Bot is not in this guild"}), 404
        return f(*args, **kwargs)

    return wrapper


# ============= Page routes =============

@app.route("/panel/login")
def panel_login():
    if _current_user():
        return redirect(url_for("panel_index"))
    return render_template("panel_login.html", bot=bot)


@app.route("/panel/auth")
def panel_auth():
    return redirect(_get_oauth2_url())


@app.route("/panel/callback")
def panel_callback():
    code = request.args.get("code")
    if not code:
        return redirect(url_for("panel_login"))
    try:
        token_data = _exchange_code(code)
        if "access_token" not in token_data:
            log(f"OAuth2 失敗: {token_data}", module_name="GuildPanel")
            return redirect(url_for("panel_login"))
        token = token_data["access_token"]
        user_data = _get_user(token)
        user_id = user_data.get("id")
        session["panel_user"] = {
            "id": user_id,
            "username": user_data.get("username"),
            "global_name": user_data.get("global_name"),
            "avatar": user_data.get("avatar"),
        }
        # Store guilds server-side to avoid cookie size limit
        _guild_cache[user_id] = [
            {"id": g["id"], "permissions": int(g.get("permissions", 0))}
            for g in _get_guilds(token)
        ]
        return redirect(url_for("panel_index"))
    except Exception as e:
        log(f"OAuth2 callback 錯誤: {e}", module_name="GuildPanel")
        return redirect(url_for("panel_login"))


@app.route("/panel/logout")
def panel_logout():
    user = _current_user()
    if user:
        _guild_cache.pop(user["id"], None)
    session.pop("panel_user", None)
    return redirect(url_for("panel_login"))


@app.route("/panel/")
@_require_auth
def panel_index():
    user = _current_user()
    guilds = _current_guilds()
    manageable = []
    for g in guilds:
        if _has_manage(g.get("permissions", 0)):
            bg = bot.get_guild(int(g["id"]))
            if bg:
                manageable.append({
                    "id": g["id"],
                    "name": bg.name,
                    "member_count": bg.member_count,
                    "icon_url": str(bg.icon.url) if bg.icon else None,
                })
    return render_template("panel.html", bot=bot, user=user, guilds=manageable)


@app.route("/panel/guild/<guild_id>")
@_require_auth
def panel_guild_page(guild_id):
    user = _current_user()
    guilds = _current_guilds()
    guild = next((g for g in guilds if str(g["id"]) == str(guild_id)), None)
    if guild is None or not _has_manage(guild.get("permissions", 0)):
        return redirect(url_for("panel_index"))
    bg = bot.get_guild(int(guild_id))
    if bg is None:
        return redirect(url_for("panel_index"))

    # Build a JSON-safe copy of the registry (strip callables)
    safe_settings = {}
    for mod, data in settings.items():
        safe_settings[mod] = {
            "display_name": data["display_name"],
            "description": data.get("description", ""),
            "icon": data.get("icon", "⚙️"),
            "settings": [
                {k: v for k, v in s.items() if k != "trigger"}
                for s in data["settings"]
            ],
        }

    return render_template(
        "panel_guild.html",
        bot=bot,
        user=user,
        guild=bg,
        settings_json=json.dumps(safe_settings, ensure_ascii=False),
    )


# ============= API routes =============

@app.route("/api/panel/guild/<guild_id>/settings")
@_require_auth
@_require_guild
def api_get_settings(guild_id):
    gid = int(guild_id)
    result = {}
    for mod, data in settings.items():
        ms = {}
        for s in data["settings"]:
            val = get_server_config(gid, s["database_key"], s.get("default"))
            ms[s["database_key"]] = _serialize(val, s.get("type", "string"))
        result[mod] = ms
    return jsonify(result)


@app.route("/api/panel/guild/<guild_id>/settings", methods=["POST"])
@_require_auth
@_require_guild
def api_set_settings(guild_id):
    gid = int(guild_id)
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "No data"}), 400

    mod_name = payload.get("module")
    key = payload.get("key")
    value = payload.get("value")

    if not mod_name or not key:
        return jsonify({"error": "Missing module or key"}), 400
    if mod_name not in settings:
        return jsonify({"error": "Unknown module"}), 400

    setting = next((s for s in settings[mod_name]["settings"] if s["database_key"] == key), None)
    if setting is None:
        return jsonify({"error": "Unknown setting"}), 400

    try:
        value = _coerce(value, setting.get("type", "string"))
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400

    set_server_config(gid, key, value)

    trigger = setting.get("trigger")
    if callable(trigger):
        try:
            import asyncio, inspect
            if inspect.iscoroutinefunction(trigger):
                asyncio.run_coroutine_threadsafe(trigger(gid, value), bot.loop)
            else:
                trigger(gid, value)
        except Exception as e:
            log(f"Trigger error {mod_name}.{key}: {e}", module_name="GuildPanel")

    user = _current_user()
    log(
        f"面板設定變更: {mod_name}.{key} = {value} "
        f"(Guild: {gid}, User: {user.get('username', '?') if user else '?'})",
        module_name="GuildPanel",
    )
    return jsonify({"success": True, "value": value})


@app.route("/api/panel/guild/<guild_id>/channels")
@_require_auth
@_require_guild
def api_channels(guild_id):
    bg = bot.get_guild(int(guild_id))
    channels = []
    for ch in sorted(bg.channels, key=lambda c: c.position):
        d = {
            "id": str(ch.id),
            "name": ch.name,
            "type": str(ch.type).split(".")[-1],
            "position": ch.position,
        }
        if hasattr(ch, "category") and ch.category:
            d["category"] = ch.category.name
            d["category_id"] = str(ch.category.id)
        channels.append(d)
    return jsonify(channels)


@app.route("/api/panel/guild/<guild_id>/roles")
@_require_auth
@_require_guild
def api_roles(guild_id):
    bg = bot.get_guild(int(guild_id))
    roles = []
    for r in sorted(bg.roles, key=lambda r: r.position, reverse=True):
        if r.is_default():
            continue
        roles.append({
            "id": str(r.id),
            "name": r.name,
            "color": str(r.color),
            "position": r.position,
        })
    return jsonify(roles)


# ============= Value serialization =============

def _serialize(value, stype):
    """Convert values to JSON-safe types. IDs -> strings to avoid JS precision loss."""
    if value is None:
        return None
    if stype in ("channel", "voice_channel", "category", "role"):
        return str(value)
    if stype == "role_list":
        if isinstance(value, list):
            return [str(v) for v in value]
        return []
    return value


# ============= Value coercion =============

def _coerce(value, stype):
    if value is None or value == "" or value == "none":
        return None

    if stype in ("channel", "voice_channel", "category", "role"):
        return int(value)

    if stype == "role_list":
        if isinstance(value, list):
            return [int(v) for v in value if v]
        return []

    if stype == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "yes")

    if stype == "number":
        return int(value)

    if stype == "float":
        return float(value)

    if stype in ("string", "text", "select"):
        return str(value) if value is not None else None

    return value


# ==================================================================
#  Pre-register settings for all known modules
# ==================================================================

def _register_all():
    """Register settings for every loaded module that uses server config."""

    if "ReportSystem" in modules:
        register_settings("ReportSystem", "檢舉系統", [
            {"display": "檢舉通知頻道", "description": "檢舉訊息將發送到此頻道", "database_key": "REPORT_CHANNEL_ID", "type": "channel", "default": None},
            {"display": "檢舉頻率限制 (秒)", "description": "同一用戶連續檢舉的冷卻時間", "database_key": "REPORT_RATE_LIMIT", "type": "number", "default": 300, "min": 0},
            {"display": "檢舉成功回覆訊息", "description": "檢舉成功後回覆給檢舉者的訊息", "database_key": "REPORTED_MESSAGE", "type": "string", "default": "感謝您的檢舉，我們會盡快處理您的檢舉。"},
            {"display": "檢舉頻道提及文字", "description": "發送到檢舉頻道的提及/通知文字", "database_key": "REPORT_MESSAGE", "type": "string", "default": "@Admin"},
            {"display": "檢舉黑名單身分組", "description": "擁有這些身分組的用戶無法檢舉", "database_key": "REPORT_BLACKLIST", "type": "role_list", "default": []},
            {"display": "伺服器規則", "description": "AI 審核使用的伺服器規則 (每行一條)", "database_key": "SERVER_RULES", "type": "text", "default": None},
        ], description="管理檢舉相關設定", icon="📋")

    if "Moderate" in modules:
        register_settings("Moderate", "管理系統", [
            {"display": "懲處公告頻道", "description": "自動發送懲處公告的頻道", "database_key": "MODERATION_MESSAGE_CHANNEL_ID", "type": "channel", "default": None},
        ], description="管理懲處相關設定", icon="🔨")

    if "ModerationNotify" in modules:
        register_settings("ModerationNotify", "懲處通知", [
            {"display": "踢出時通知用戶", "database_key": "notify_user_on_kick", "type": "boolean", "default": True},
            {"display": "封鎖時通知用戶", "database_key": "notify_user_on_ban", "type": "boolean", "default": True},
            {"display": "禁言時通知用戶", "database_key": "notify_user_on_mute", "type": "boolean", "default": True},
            {"display": "用戶申訴頻道", "description": "設定後懲處通知會包含申訴連結", "database_key": "user_appeal_channel", "type": "channel", "default": None},
        ], description="控制懲處時是否通知被處分的用戶", icon="🔔")

    if "DynamicVoice" in modules:
        async def _dv_channel_trigger(gid, value):
            """Set user_limit on the dynamic voice channel when changed via panel."""
            if value is None:
                return
            guild = bot.get_guild(gid)
            if not guild:
                return
            ch = guild.get_channel(int(value))
            if ch:
                play_audio = get_server_config(gid, "dynamic_voice_play_audio", False)
                try:
                    await ch.edit(user_limit=2 if play_audio else 1)
                except Exception as e:
                    log(f"面板觸發: 無法設定頻道 user_limit: {e}", module_name="GuildPanel")

        register_settings("DynamicVoice", "動態語音", [
            {"display": "入口語音頻道", "description": "用戶加入此頻道時自動建立動態頻道", "database_key": "dynamic_voice_channel", "type": "voice_channel", "default": None, "trigger": _dv_channel_trigger},
            {"display": "動態頻道分類", "description": "動態語音頻道所屬的分類", "database_key": "dynamic_voice_channel_category", "type": "category", "default": None},
            {"display": "頻道名稱範本", "description": "使用 {user} 作為用戶名稱佔位符", "database_key": "dynamic_voice_channel_name", "type": "string", "default": "{user} 的頻道"},
            {"display": "加入時播放音效", "database_key": "dynamic_voice_play_audio", "type": "boolean", "default": False},
            {"display": "黑名單身分組", "description": "擁有這些身分組的用戶無法使用動態語音", "database_key": "dynamic_voice_blacklist_roles", "type": "role_list", "default": []},
        ], description="自動建立/刪除語音頻道", icon="🔊")

    if "dsize" in modules:
        register_settings("dsize", "dsize", [
            {"display": "最大尺寸上限", "database_key": "dsize_max", "type": "number", "default": 30, "min": 1},
            {"display": "手術成功率 (%)", "database_key": "dsize_surgery_percent", "type": "number", "default": 10, "min": 0, "max": 100},
            {"display": "手術結果最大值", "database_key": "dsize_surgery_max", "type": "number", "default": 10, "min": 1},
            {"display": "掉落物品機率 (%)", "database_key": "dsize_drop_item_chance", "type": "number", "default": 5, "min": 0, "max": 100},
        ], description="dsize 數值設定", icon="📏")

    if "Economy" in modules:
        register_settings("Economy", "經濟系統", [
            {"display": "貨幣名稱", "database_key": "economy_currency_name", "type": "string", "default": "伺服幣"},
        ], description="管理伺服器經濟參數", icon="💰")

    if "AutoReply" in modules:
        register_settings("AutoReply", "自動回覆", [
            {"display": "忽略模式", "description": "黑名單: 忽略指定頻道 / 白名單: 只在指定頻道生效", "database_key": "autoreply_ignore_mode", "type": "select", "default": "blacklist",
             "options": [{"label": "黑名單", "value": "blacklist"}, {"label": "白名單", "value": "whitelist"}]},
        ], description="自動回覆基本設定", icon="💬")

    if "CustomPrefix" in modules:
        register_settings("CustomPrefix", "自訂前綴", [
            {"display": "指令前綴", "description": "此伺服器專用的指令前綴，留空使用預設", "database_key": "custom_prefix", "type": "string", "default": config("prefix", "!")},
        ], description="自訂此伺服器的指令前綴", icon="⌨️")

    if "dgpa" in modules:
        register_settings("dgpa", "NDS 追蹤", [
            {"display": "NDS 通知頻道", "description": "NDS 追蹤結果發布的頻道", "database_key": "nds_follow_channel_id", "type": "channel", "default": None},
        ], description="NDS 追蹤通知設定", icon="📡")

    if "FakeUser" in modules:
        register_settings("FakeUser", "仿冒用戶", [
            {"display": "仿冒日誌頻道", "description": "仿冒用戶結果發送到此頻道", "database_key": "fake_user_log_channel", "type": "channel", "default": None},
        ], description="仿冒用戶通知", icon="🕵️")

    if "logger" in modules:
        register_settings("logger", "日誌系統", [
            {"display": "日誌頻道", "description": "Bot 日誌輸出的文字頻道", "database_key": "log_channel_id", "type": "channel", "default": None},
        ], description="機器人日誌設定", icon="📝")

    if "MessageImage" in modules:
        register_settings("MessageImage", "留言板", [
            {"display": "留言板頻道", "description": "伺服器留言板功能使用的頻道", "database_key": "guild_board_channel_id", "type": "channel", "default": None},
        ], description="伺服器留言板設定", icon="🖼️")

    if "OXWU" in modules:
        register_settings("OXWU", "地震警報", [
            {"display": "警報通知頻道", "database_key": "oxwu_warning_channel", "type": "channel", "default": None},
            {"display": "警報附加文字", "description": "例如 @everyone", "database_key": "oxwu_warning_channel_text", "type": "string", "default": None},
            {"display": "報告通知頻道", "database_key": "oxwu_report_channel", "type": "channel", "default": None},
            {"display": "報告附加文字", "database_key": "oxwu_report_channel_text", "type": "string", "default": None},
        ], description="地震警報/報告推送設定", icon="🌦️")

    if "StickyRole" in modules:
        register_settings("StickyRole", "StickyRole", [
            {"display": "啟用角色記憶", "description": "用戶離開後重新加入時自動還原角色", "database_key": "stickyrole_enabled", "type": "boolean", "default": False},
            {"display": "允許記憶的身分組", "description": "只有這些身分組會被記憶並還原", "database_key": "stickyrole_allowed_roles", "type": "role_list", "default": []},
            {"display": "忽略機器人", "database_key": "stickyrole_ignore_bots", "type": "boolean", "default": True},
            {"display": "日誌頻道", "description": "角色還原時發送通知的頻道", "database_key": "stickyrole_log_channel", "type": "channel", "default": None},
        ], description="角色記憶功能設定", icon="📌")


_register_all()

class GuildPanel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="panel", description="打開伺服器面板")
    async def panel(self, interaction: discord.Interaction):
        button = discord.ui.Button(label="打開面板", style=discord.ButtonStyle.link, url=_get_oauth2_url())
        view = discord.ui.View()
        view.add_item(button)
        await interaction.response.send_message("請前往網站面板進行設定：", view=view, ephemeral=True)
    
    @commands.Cog.listener()
    async def on_ready(self):
        log(f"已載入 {len(settings)} 個模組的面板設定 ({sum(len(d['settings']) for d in settings.values())} 項)", module_name="GuildPanel")

asyncio.run(bot.add_cog(GuildPanel(bot)))