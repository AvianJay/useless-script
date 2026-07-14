from globalenv import bot, start_bot, set_server_config, get_server_config, get_all_server_config_key, set_user_data, get_user_data, config, modules
import json
import discord
from discord.ext import commands
from discord import app_commands
from database import db
from flask import jsonify, render_template, request, send_from_directory, abort, g
import time
import asyncio
import os
import base64
from Activity import ActivityEntry
import requests
import secrets
import uuid as _uuid_mod
from functools import wraps
import threading
import urllib.parse
from logger import log
import logging
import re
import math
from collections import deque

import socketio as socketio_asgi

from asgiref.wsgi import WsgiToAsgi

if "Website" in modules:
    from Website import app
else:
    raise Exception("Website module not found")

def init_db():
    with db.get_connection() as conn:
        cursor = conn.cursor()

        # Create explore_space_tiles table
        # Stores space map as sparse tiles: (x, y, z) -> tile_id
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS explore_space_tiles (
                guild_id INTEGER NOT NULL,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                z INTEGER NOT NULL,
                tile_id TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, x, y, z)
            )
        ''')
        
        conn.commit()

# --- ASGI Socket.IO (real WebSocket under Hypercorn) ---

sio = socketio_asgi.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
)

# Compose ASGI app: Socket.IO first, then fall through to Flask (WSGI wrapped into ASGI)
flask_asgi = WsgiToAsgi(app)

# socketio_path must NOT start with '/'
asgi_app = socketio_asgi.ASGIApp(sio, other_asgi_app=flask_asgi, socketio_path="explore/socket.io")

# Music module integration (optional — graceful degradation when Music is absent)
try:
    import Music as _music_mod
    _music_available = True
except Exception:
    _music_mod = None
    _music_available = False

# Economy module integration (optional — skin shop & quest rewards use global currency)
try:
    import Economy as _economy_mod
    _economy_available = True
except Exception:
    _economy_mod = None
    _economy_available = False

activity_entry = ActivityEntry(name=app_commands.locale_str("explore space"), description="開啟探索空間")

EXPLORE_SAVE_DATA_KEY = "explore_save_data"
EXPLORE_SAVE_ALLOWED_SWITCH_IDS = (
    3, 4
)
EXPLORE_SAVE_ALLOWED_VARIABLE_IDS = (
    # Add variable IDs here, for example: 1, 2, 3
)


# --- Auth (Explore API) ---

# auth_token -> {user_id:int, username:str, guild_ids:set[str], issued_at:float, expires_at:float}
auth_tokens = {}
AUTH_TOKEN_TTL_SECONDS = 60 * 60 * 24  # 24h
_auth_lock = threading.Lock()


def _cleanup_auth_tokens() -> None:
    now = time.time()
    with _auth_lock:
        expired = [t for t, v in auth_tokens.items() if v.get('expires_at', 0) <= now]
        for t in expired:
            del auth_tokens[t]


def _parse_bearer_token(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.lower().startswith('bearer '):
        return value.split(' ', 1)[1].strip() or None
    return value


def _get_explore_auth_token_from_request() -> str | None:
    # Prefer Authorization: Bearer <auth_token>
    token = _parse_bearer_token(request.headers.get('Authorization'))
    if token:
        return token
    # Fallback header
    token = request.headers.get('X-Auth-Token')
    return token.strip() if token else None


def _require_explore_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        _cleanup_auth_tokens()
        token = _get_explore_auth_token_from_request()
        if not token:
            return jsonify({'error': 'Unauthorized'}), 401
        with _auth_lock:
            entry = auth_tokens.get(token)
        if not entry:
            return jsonify({'error': 'Unauthorized'}), 401
        g.explore_user = entry
        g.explore_auth_token = token
        return fn(*args, **kwargs)
    return wrapper


def _verify_discord_user_token(discord_token: str) -> dict | None:
    # Accept raw token, or a full Authorization header value.
    header_value = discord_token.strip()
    if not (header_value.lower().startswith('bearer ') or header_value.lower().startswith('bot ')):
        header_value = f"Bearer {header_value}"
    try:
        r = requests.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": header_value},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _fetch_discord_user_guild_ids(discord_token: str) -> set[str]:
    header_value = discord_token.strip()
    if not (header_value.lower().startswith('bearer ') or header_value.lower().startswith('bot ')):
        header_value = f"Bearer {header_value}"
    try:
        r = requests.get(
            "https://discord.com/api/users/@me/guilds",
            headers={"Authorization": header_value},
            timeout=10,
        )
        if r.status_code != 200:
            return set()
        return {str(g.get('id')) for g in r.json() if g.get('id') is not None}
    except Exception:
        return set()


def _fetch_discord_user_guild_ids_result(discord_token: str) -> tuple[bool, set[str]]:
    header_value = discord_token.strip()
    if not (header_value.lower().startswith('bearer ') or header_value.lower().startswith('bot ')):
        header_value = f"Bearer {header_value}"
    try:
        r = requests.get(
            "https://discord.com/api/users/@me/guilds",
            headers={"Authorization": header_value},
            timeout=10,
        )
        if r.status_code != 200:
            return False, set()
        return True, {str(g.get('id')) for g in r.json() if g.get('id') is not None}
    except Exception:
        return False, set()


def _issue_explore_auth_token(user_id: int, username: str, guild_ids: set[str], discord_token: str | None = None) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _auth_lock:
        auth_tokens[token] = {
            'user_id': int(user_id),
            'username': username,
            'guild_ids': set(guild_ids),
            'discord_token': str(discord_token).strip() if discord_token else None,
            'issued_at': now,
            'expires_at': now + AUTH_TOKEN_TTL_SECONDS,
        }
    return token


# --- Presence (in-space counts) ---

# guild_id(str) -> set[user_id(str)]
space_presence: dict[str, set[str]] = {}
_presence_lock = threading.Lock()

# guild_id(str) -> {user_id(str): {user_id,name,skin_id,x,y}}
space_players: dict[str, dict[str, dict]] = {}

# sid -> {'user_id': str, 'guild_id': str|None, 'music_guild_id': str|None}
socket_sessions: dict[str, dict] = {}
_session_lock = threading.Lock()


def _parse_token_from_query(environ: dict) -> str | None:
    qs = environ.get("QUERY_STRING") or ""
    if not qs:
        return None
    parsed = urllib.parse.parse_qs(qs)
    token_values = parsed.get("token") or []
    if not token_values:
        return None
    t = str(token_values[0]).strip()
    return t or None


def _get_socket_session(sid: str) -> dict | None:
    with _session_lock:
        return socket_sessions.get(sid)


def _music_room_name(guild_id: int | str) -> str:
    return f"music:{guild_id}"


async def _set_socket_music_room(sid: str, guild_id: int | str | None) -> None:
    previous_guild_id = None
    next_guild_id = str(guild_id) if guild_id is not None else None

    with _session_lock:
        sess = socket_sessions.get(sid)
        if not sess:
            return
        previous_guild_id = sess.get("music_guild_id")
        sess["music_guild_id"] = next_guild_id

    if previous_guild_id and previous_guild_id != next_guild_id:
        await sio.leave_room(sid, _music_room_name(previous_guild_id))
    if next_guild_id and previous_guild_id != next_guild_id:
        await sio.enter_room(sid, _music_room_name(next_guild_id))

# --- Helper Functions ---

_DISCORD_INVITE_CODE_RE = re.compile(r"^[A-Za-z0-9-]+$")


def _default_explore_server_config() -> dict:
    return {
        "enabled": True,
        "is_public": False,
        "map_type": 1,
        "require_join": False,
        "invite_link": None,
    }


def _normalize_explore_server_config(value) -> dict:
    config_value = _default_explore_server_config()
    if isinstance(value, dict):
        config_value.update(value)
    config_value["enabled"] = bool(config_value.get("enabled"))
    config_value["is_public"] = bool(config_value.get("is_public"))
    config_value["require_join"] = bool(config_value.get("require_join"))
    invite_link = str(config_value.get("invite_link") or "").strip()
    config_value["invite_link"] = invite_link or None
    return config_value


def _save_explore_server_config(guild_id: int, guild_config: dict) -> dict:
    normalized = _normalize_explore_server_config(guild_config)
    set_server_config(guild_id, "explore_config", normalized)
    return normalized


def _update_explore_server_config(guild_id: int, **changes) -> dict:
    guild_config = get_explore_server(guild_id)
    guild_config.update(changes)
    return _save_explore_server_config(guild_id, guild_config)


def _normalize_discord_invite_url(value: str | None) -> str | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    if "://" not in raw_value:
        raw_value = f"https://{raw_value.lstrip('/')}"
    try:
        parsed = urllib.parse.urlparse(raw_value)
    except Exception:
        return None

    host = (parsed.netloc or "").lower()
    path_parts = [segment for segment in (parsed.path or "").split("/") if segment]
    invite_code = None

    if host in {"discord.gg", "www.discord.gg"} and path_parts:
        invite_code = path_parts[0]
    elif host in {"discord.com", "www.discord.com", "discordapp.com", "www.discordapp.com"}:
        if len(path_parts) >= 2 and path_parts[0].lower() == "invite":
            invite_code = path_parts[1]

    if not invite_code or not _DISCORD_INVITE_CODE_RE.fullmatch(invite_code):
        return None
    return f"https://discord.gg/{invite_code}"


def _get_explore_bot_member(guild: discord.Guild) -> discord.Member | None:
    member = getattr(guild, "me", None)
    if member is not None:
        return member
    if bot.user is None:
        return None
    return guild.get_member(bot.user.id)


def _find_explore_invite_channel(guild: discord.Guild) -> discord.abc.GuildChannel | None:
    bot_member = _get_explore_bot_member(guild)
    if bot_member is None:
        return None
    for channel in guild.text_channels:
        try:
            permissions = channel.permissions_for(bot_member)
        except Exception:
            continue
        if permissions.view_channel and permissions.create_instant_invite:
            return channel
    return None


async def _create_explore_invite_link(guild: discord.Guild) -> str | None:
    channel = _find_explore_invite_channel(guild)
    if channel is None:
        return None
    try:
        invite = await channel.create_invite(
            max_age=0,
            max_uses=0,
            unique=False,
            reason="Enable Explore require-join",
        )
    except (discord.Forbidden, discord.HTTPException):
        return None
    return getattr(invite, "url", None)


async def _validate_explore_invite_link(
    guild: discord.Guild,
    invite_link: str | None,
) -> tuple[str | None, str | None]:
    normalized = _normalize_discord_invite_url(invite_link)
    if not normalized:
        return None, "邀請連結格式無效，請提供 Discord 邀請連結。"
    try:
        invite = await bot.fetch_invite(normalized)
    except discord.NotFound:
        return None, "找不到這個邀請連結，請確認它仍然有效。"
    except discord.HTTPException:
        return None, "目前無法驗證這個邀請連結，請稍後再試。"

    invite_guild = getattr(invite, "guild", None)
    if invite_guild is None or invite_guild.id != guild.id:
        return None, "這個邀請連結不屬於目前的伺服器。"

    resolved_url = getattr(invite, "url", None) or normalized
    return resolved_url, None


async def _resolve_explore_require_join_invite_link(
    guild: discord.Guild,
    invite_link: str | None,
    fallback_invite_link: str | None = None,
) -> tuple[str | None, str | None, bool]:
    candidate_link = str(invite_link or "").strip() or fallback_invite_link
    if candidate_link:
        resolved_link, error_message = await _validate_explore_invite_link(guild, candidate_link)
        if resolved_link:
            return resolved_link, None, False
        if invite_link:
            return None, error_message, False

    created_link = await _create_explore_invite_link(guild)
    if created_link:
        return created_link, None, True
    return None, "我沒有權限自動建立永久邀請連結，請先提供 `invite_link` 或給我建立邀請的權限。", False


def _is_explore_server_member(guild_id: int | str, guild_ids: set[str]) -> bool:
    return str(guild_id) in {str(x) for x in (guild_ids or set())}


def _is_bot_owner(user_id: int | str) -> bool:
    try:
        owners = config("owners") or []
        return int(user_id) in {int(x) for x in owners}
    except Exception:
        return False


def _is_explore_guild_admin(guild_id: int | str, user_id: int | str) -> bool:
    """Server-side admin check via the bot's member cache. 'world' is owner-only."""
    if str(guild_id) == "world":
        return _is_bot_owner(user_id)
    try:
        guild = bot.get_guild(int(guild_id))
    except (TypeError, ValueError):
        return False
    if not guild:
        return False
    try:
        member = guild.get_member(int(user_id))
    except (TypeError, ValueError):
        return False
    if not member:
        return False
    perms = member.guild_permissions
    return bool(perms.administrator or perms.manage_guild)


def _can_access_explore_server(server: dict, guild_id: int | str, guild_ids: set[str]) -> bool:
    if not server or not server.get("enabled"):
        return False
    if _is_explore_server_member(guild_id, guild_ids):
        return True
    if not server.get("is_public"):
        return False
    return not server.get("require_join")


def _refresh_explore_auth_guild_ids(auth_token: str | None) -> set[str] | None:
    if not auth_token:
        return None
    with _auth_lock:
        entry = auth_tokens.get(auth_token)
        if not entry:
            return None
        existing_guild_ids = set(entry.get("guild_ids") or [])
        discord_token = entry.get("discord_token")

    if not discord_token:
        return existing_guild_ids

    ok, guild_ids = _fetch_discord_user_guild_ids_result(str(discord_token))
    if not ok:
        return existing_guild_ids

    with _auth_lock:
        current_entry = auth_tokens.get(auth_token)
        if current_entry:
            current_entry["guild_ids"] = set(guild_ids)
    return set(guild_ids)


def _get_request_explore_guild_ids(refresh: bool = False) -> set[str]:
    guild_ids = set(g.explore_user.get("guild_ids") or [])
    if refresh:
        refreshed = _refresh_explore_auth_guild_ids(getattr(g, "explore_auth_token", None))
        if refreshed is not None:
            guild_ids = set(refreshed)
            g.explore_user["guild_ids"] = set(guild_ids)

    if not guild_ids:
        uid = int(g.explore_user["user_id"])
        for guild in bot.guilds:
            try:
                if guild.get_member(uid):
                    guild_ids.add(str(guild.id))
            except Exception:
                pass
        g.explore_user["guild_ids"] = set(guild_ids)
    return guild_ids


def _refresh_socket_session_guild_ids(sid: str) -> set[str]:
    with _session_lock:
        sess = socket_sessions.get(sid)
        if not sess:
            return set()
        auth_token = sess.get("auth_token")
        existing = {str(x) for x in (sess.get("guild_ids") or [])}

    refreshed = _refresh_explore_auth_guild_ids(auth_token)
    if refreshed is None:
        return existing

    refreshed_list = sorted(str(x) for x in refreshed)
    with _session_lock:
        sess = socket_sessions.get(sid)
        if sess is not None:
            sess["guild_ids"] = refreshed_list
    return set(refreshed_list)


def _build_explore_server_payload(guild: discord.Guild, server: dict, viewer_guild_ids: set[str], viewer_user_id: int | str | None = None) -> dict:
    gid = str(guild.id)
    is_member = _is_explore_server_member(gid, viewer_guild_ids)
    with _presence_lock:
        in_space = len(space_presence.get(gid, set()))
    return {
        "id": gid,
        "name": guild.name,
        "icon_url": "/api/explore/icon/guild/" + gid if guild.icon else None,
        "member_count": int(getattr(guild, "member_count", 0) or 0),
        "in_space_count": in_space,
        "is_public": bool(server.get("is_public")),
        "require_join": bool(server.get("require_join")),
        "invite_link": server.get("invite_link"),
        "is_member": is_member,
        "can_enter": _can_access_explore_server(server, gid, viewer_guild_ids),
        "can_edit": _is_explore_guild_admin(gid, viewer_user_id) if viewer_user_id is not None else False,
    }


def get_explore_server(guild_id: int):
    return _normalize_explore_server_config(
        get_server_config(
            guild_id,
            "explore_config",
            _default_explore_server_config(),
        )
    )


def toggle_explore_server(guild_id: int, enabled: bool):
    _update_explore_server_config(guild_id, enabled=enabled)


def set_explore_privacy(guild_id: int, is_public: bool):
    _update_explore_server_config(guild_id, is_public=is_public)


# --- Skins ---
# Skin id format: "<CharacterSheet>:<index>" (index 0-7 within the sheet).
# Sheets must exist in Explore/img/characters/. price 0 = free.
EXPLORE_OWNED_SKINS_KEY = "explore_owned_skins"
DEFAULT_SKIN_ID = "Actor1:0"

SKIN_CATALOG: list[dict] = [
    # 免費基本款
    {"id": "Actor1:0", "name": "勇者", "price": 0},
    {"id": "Actor1:1", "name": "戰士", "price": 0},
    {"id": "Actor1:2", "name": "武鬥家", "price": 0},
    {"id": "Actor1:3", "name": "盜賊", "price": 0},
    {"id": "Actor1:4", "name": "女勇者", "price": 0},
    {"id": "Actor1:5", "name": "女戰士", "price": 0},
    {"id": "Actor1:6", "name": "女武鬥家", "price": 0},
    {"id": "Actor1:7", "name": "女盜賊", "price": 0},
    {"id": "Actor2:0", "name": "僧侶", "price": 0},
    {"id": "Actor2:1", "name": "魔法師", "price": 0},
    {"id": "Actor2:2", "name": "賢者", "price": 0},
    {"id": "Actor2:3", "name": "商人", "price": 0},
    # 村民系列(便宜)
    {"id": "People1:0", "name": "村民大叔", "price": 50},
    {"id": "People1:1", "name": "村民大嬸", "price": 50},
    {"id": "People1:6", "name": "老爺爺", "price": 50},
    {"id": "People1:7", "name": "老奶奶", "price": 50},
    {"id": "People2:2", "name": "貴族", "price": 120},
    {"id": "People2:3", "name": "貴婦", "price": 120},
    {"id": "People4:4", "name": "國王", "price": 300},
    {"id": "People4:5", "name": "皇后", "price": 300},
    # 科幻系列
    {"id": "SF_Actor1:0", "name": "太空人A", "price": 200},
    {"id": "SF_Actor1:1", "name": "太空人B", "price": 200},
    {"id": "SF_Actor2:0", "name": "機器人", "price": 250},
    {"id": "SF_Actor3:0", "name": "異星人", "price": 250},
    {"id": "SF_People1:0", "name": "太空市民", "price": 150},
    # 怪物/特殊系列(貴)
    {"id": "Evil:0", "name": "魔王手下", "price": 500},
    {"id": "Evil:4", "name": "死神", "price": 800},
    {"id": "Monster:0", "name": "史萊姆", "price": 400},
    {"id": "Monster:2", "name": "蝙蝠", "price": 400},
    {"id": "Nature:0", "name": "精靈", "price": 600},
    {"id": "Meme:0", "name": "迷因人", "price": 1000},
]

_SKIN_BY_ID: dict[str, dict] = {s["id"]: s for s in SKIN_CATALOG}

# 舊版皮膚 id(純數字)→ 新格式的對照
_LEGACY_SKIN_MAP = {"1": DEFAULT_SKIN_ID}


def _normalize_skin_id(skin_id) -> str:
    sid = str(skin_id or "").strip()
    sid = _LEGACY_SKIN_MAP.get(sid, sid)
    if sid not in _SKIN_BY_ID:
        return DEFAULT_SKIN_ID
    return sid


def get_user_skin(user_id: int):
    return _normalize_skin_id(get_user_data(0, user_id, "explore_skin_data", DEFAULT_SKIN_ID))

def set_user_skin(user_id: int, skin_data: str):
    set_user_data(0, user_id, "explore_skin_data", _normalize_skin_id(skin_data))


def get_user_owned_skins(user_id: int) -> set[str]:
    raw = get_user_data(0, user_id, EXPLORE_OWNED_SKINS_KEY, [])
    owned = {str(x) for x in raw} if isinstance(raw, list) else set()
    # 免費皮膚人人都有
    owned.update(s["id"] for s in SKIN_CATALOG if not s.get("price"))
    return owned


def add_user_owned_skin(user_id: int, skin_id: str) -> None:
    raw = get_user_data(0, user_id, EXPLORE_OWNED_SKINS_KEY, [])
    owned = [str(x) for x in raw] if isinstance(raw, list) else []
    if str(skin_id) not in owned:
        owned.append(str(skin_id))
        set_user_data(0, user_id, EXPLORE_OWNED_SKINS_KEY, owned)


def user_owns_skin(user_id: int, skin_id: str) -> bool:
    sid = str(skin_id or "").strip()
    sid = _LEGACY_SKIN_MAP.get(sid, sid)
    skin = _SKIN_BY_ID.get(sid)
    if not skin:
        return False
    if not skin.get("price"):
        return True
    return sid in get_user_owned_skins(user_id)


# --- XP / Level ---

EXPLORE_XP_KEY = "explore_xp"
CHAT_XP_AMOUNT = 2
CHAT_XP_COOLDOWN_SECONDS = 30
EMOTE_XP_AMOUNT = 1
EMOTE_XP_COOLDOWN_SECONDS = 60

# user_id(str) -> {"chat": last_ts, "emote": last_ts}
_xp_cooldowns: dict[str, dict[str, float]] = {}
_xp_lock = threading.Lock()


def get_user_xp(user_id: int) -> int:
    try:
        return max(0, int(get_user_data(0, user_id, EXPLORE_XP_KEY, 0) or 0))
    except (TypeError, ValueError):
        return 0


def xp_to_level(xp: int) -> int:
    """Lv.1 起跳;每級所需 XP 平方成長 (Lv n 需要 25*(n-1)^2)。"""
    return int(math.sqrt(max(0, int(xp)) / 25)) + 1


def get_user_level(user_id: int) -> int:
    return xp_to_level(get_user_xp(user_id))


def add_user_xp(user_id: int, amount: int, *, source: str | None = None, cooldown: float = 0.0) -> tuple[int, int, bool]:
    """
    Add XP with an optional per-source cooldown.
    Returns (xp, level, leveled_up). When on cooldown, returns current values unchanged.
    """
    amount = int(amount)
    uid = str(user_id)
    if amount <= 0:
        xp = get_user_xp(user_id)
        return xp, xp_to_level(xp), False

    if source and cooldown > 0:
        now = time.time()
        with _xp_lock:
            per_user = _xp_cooldowns.setdefault(uid, {})
            if now - per_user.get(source, 0.0) < cooldown:
                xp = get_user_xp(user_id)
                return xp, xp_to_level(xp), False
            per_user[source] = now

    old_xp = get_user_xp(user_id)
    new_xp = old_xp + amount
    set_user_data(0, user_id, EXPLORE_XP_KEY, new_xp)
    old_level = xp_to_level(old_xp)
    new_level = xp_to_level(new_xp)
    return new_xp, new_level, new_level > old_level


async def _broadcast_level_up(guild_id: str, user_id: str, level: int) -> None:
    await sio.emit(
        "level_up",
        {"guild_id": str(guild_id), "user_id": str(user_id), "level": int(level)},
        room=str(guild_id),
    )


# --- Chat (in-space chat room with filter + Discord bridge) ---

CHAT_MAX_LENGTH = 200
CHAT_HISTORY_SIZE = 50
CHAT_RATE_LIMIT_SECONDS = 1.0
CHAT_BLOCKLIST_CONFIG_KEY = "explore_chat_blocklist"
CHAT_CHANNEL_CONFIG_KEY = "explore_chat_channel"

# 內建禁字詞庫(中英俄羅斯基本款,管理員可用指令補充)
BUILTIN_BANNED_WORDS = [
    # 英文
    "fuck", "shit", "bitch", "asshole", "cunt", "faggot", "nigger", "nigga",
    "retard", "dickhead", "motherfucker",
    # 中文
    "幹你娘", "幹您娘", "操你媽", "肏你媽", "操你妈", "干你娘", "他媽的", "他妈的",
    "媽的", "妈的", "靠北", "靠杯", "雞掰", "機掰", "北七", "白癡", "白痴",
    "智障", "腦殘", "脑残", "垃圾人", "去死", "婊子", "賤人", "贱人", "傻逼",
    "沙比", "煞筆", "傻屄", "尼瑪", "你妈死了", "妳媽死了",
]

# guild_id(str) -> deque of chat message dicts
_chat_history: dict[str, deque] = {}
_chat_lock = threading.Lock()

# user_id(str) -> last chat timestamp (rate limit)
_chat_last_sent: dict[str, float] = {}


def _get_guild_banned_words(guild_id: str) -> list[str]:
    if guild_id == "world":
        return []
    try:
        raw = get_server_config(int(guild_id), CHAT_BLOCKLIST_CONFIG_KEY, [])
    except (TypeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [str(w).strip() for w in raw if str(w).strip()]


def filter_chat_text(text: str, guild_id: str = "world") -> tuple[str, bool]:
    """
    Mask banned words (builtin + per-guild custom) with '*'.
    Returns (filtered_text, was_filtered).
    """
    filtered = str(text)
    was_filtered = False
    words = BUILTIN_BANNED_WORDS + _get_guild_banned_words(guild_id)
    # 長詞優先,避免短詞先遮蔽壞掉長詞比對
    for word in sorted(set(words), key=len, reverse=True):
        if not word:
            continue
        pattern = re.compile(re.escape(word), re.IGNORECASE)
        if pattern.search(filtered):
            filtered = pattern.sub("*" * len(word), filtered)
            was_filtered = True
    return filtered, was_filtered


def _get_chat_history(guild_id: str) -> list[dict]:
    with _chat_lock:
        dq = _chat_history.get(str(guild_id))
        return list(dq) if dq else []


def _append_chat_history(guild_id: str, message: dict) -> None:
    with _chat_lock:
        dq = _chat_history.setdefault(str(guild_id), deque(maxlen=CHAT_HISTORY_SIZE))
        dq.append(message)


def _get_chat_bridge_channel_id(guild_id: str) -> int | None:
    if guild_id == "world":
        return None
    try:
        raw = get_server_config(int(guild_id), CHAT_CHANNEL_CONFIG_KEY, None)
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _make_chat_message(guild_id: str, user_id: str, name: str, text: str, *, source: str = "game", level: int | None = None) -> dict:
    return {
        "guild_id": str(guild_id),
        "user_id": str(user_id),
        "name": str(name),
        "text": str(text),
        "level": level,
        "source": source,  # "game" | "discord"
        "ts": int(time.time()),
    }


async def _forward_chat_to_discord(guild_id: str, name: str, text: str) -> None:
    """遊戲內訊息 → Discord 橋接頻道(過濾後的內容,防 mention)。"""
    channel_id = _get_chat_bridge_channel_id(guild_id)
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        return
    try:
        await channel.send(
            f"🎮 **{name}**: {text}",
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except (discord.Forbidden, discord.HTTPException) as e:
        log(f"Chat bridge send failed for guild {guild_id}: {e}", level=logging.WARNING, module_name="Explore")


# --- Quests (one-time, server-side verified) ---

EXPLORE_QUESTS_KEY = "explore_quests"

# 一次性任務目錄。reward: 全域幣 / xp。requires: 前置任務 id 列表。
QUEST_CATALOG: dict[str, dict] = {
    "vill_rat": {
        "name": "村長家的哲學鼠患",
        "description": "幫村長趕走他家裡那隻會背莎士比亞的老鼠。",
        "reward_coins": 30,
        "reward_xp": 50,
        "requires": [],
    },
    "vill_delivery": {
        "name": "極速快遞(步行)",
        "description": "把一封「超急件」從村口走路送到隔壁,距離大概十步。",
        "reward_coins": 30,
        "reward_xp": 50,
        "requires": [],
    },
    "vill_cabbage": {
        "name": "高麗菜失蹤事件",
        "description": "調查菜園裡高麗菜連環失蹤案。兇手可能就是委託人。",
        "reward_coins": 30,
        "reward_xp": 50,
        "requires": [],
    },
    "vill_boss": {
        "name": "迷因大魔王",
        "description": "村莊三大蠢事解決後,迷因大魔王被吵醒了。把祂打回去睡覺。",
        "reward_coins": 200,
        "reward_xp": 300,
        "requires": ["vill_rat", "vill_delivery", "vill_cabbage"],
    },
}


def get_user_completed_quests(user_id: int) -> set[str]:
    raw = get_user_data(0, user_id, EXPLORE_QUESTS_KEY, [])
    if not isinstance(raw, list):
        return set()
    return {str(q) for q in raw if str(q) in QUEST_CATALOG}


def _quest_prerequisites_met(quest_id: str, completed: set[str]) -> bool:
    quest = QUEST_CATALOG.get(quest_id)
    if not quest:
        return False
    return all(req in completed for req in quest.get("requires", []))


def complete_user_quest(user_id: int, quest_id: str) -> tuple[bool, str | dict]:
    """
    Server-side one-time quest completion.
    Returns (True, result_dict) or (False, error_message).
    """
    quest = QUEST_CATALOG.get(str(quest_id))
    if not quest:
        return False, "未知的任務"

    completed = get_user_completed_quests(user_id)
    if quest_id in completed:
        return False, "這個任務你已經完成過了"
    if not _quest_prerequisites_met(quest_id, completed):
        return False, "前置任務尚未完成"

    completed.add(str(quest_id))
    set_user_data(0, user_id, EXPLORE_QUESTS_KEY, sorted(completed))

    reward_coins = int(quest.get("reward_coins") or 0)
    reward_xp = int(quest.get("reward_xp") or 0)

    coins_awarded = 0
    if reward_coins > 0 and _economy_available:
        try:
            balance = float(_economy_mod.get_global_balance(user_id))
            _economy_mod.set_global_balance(user_id, balance + reward_coins)
            _economy_mod.log_transaction(
                _economy_mod.GLOBAL_GUILD_ID, user_id, 'explore_quest',
                reward_coins, getattr(_economy_mod, 'GLOBAL_CURRENCY_NAME', '全域幣'),
                f"完成 Explore 任務 {quest['name']} ({quest_id})",
            )
            coins_awarded = reward_coins
        except Exception as e:
            log(f"Quest coin reward failed for user {user_id}: {e}", level=logging.WARNING, module_name="Explore")

    xp, level, leveled_up = add_user_xp(user_id, reward_xp)

    return True, {
        "quest_id": str(quest_id),
        "name": quest["name"],
        "coins": coins_awarded,
        "xp_gained": reward_xp,
        "xp": xp,
        "level": level,
        "leveled_up": leveled_up,
        "completed_quests": sorted(completed),
    }


def build_quest_state_payload(user_id: int) -> dict:
    completed = get_user_completed_quests(user_id)
    quests = []
    for qid, quest in QUEST_CATALOG.items():
        quests.append({
            "id": qid,
            "name": quest["name"],
            "description": quest["description"],
            "reward_coins": int(quest.get("reward_coins") or 0),
            "reward_xp": int(quest.get("reward_xp") or 0),
            "requires": list(quest.get("requires", [])),
            "completed": qid in completed,
            "available": _quest_prerequisites_met(qid, completed) and qid not in completed,
        })
    return {
        "quests": quests,
        "completed": sorted(completed),
        # 村莊 Boss 出現條件:三個小任務全完成
        "vill_boss_ready": _quest_prerequisites_met("vill_boss", completed),
    }


def _normalize_save_id_allowlist(values) -> set[str]:
    allowed: set[str] = set()
    for value in values or ():
        try:
            value_int = int(value)
        except (TypeError, ValueError):
            continue
        if value_int > 0:
            allowed.add(str(value_int))
    return allowed


def _parse_optional_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _remove_user_from_space(guild_id: str | None, user_id: str | None) -> bool:
    if not guild_id or not user_id:
        return False

    removed = False
    with _presence_lock:
        presence = space_presence.get(guild_id)
        if presence and user_id in presence:
            presence.remove(user_id)
            removed = True
            if not presence:
                space_presence.pop(guild_id, None)

        players = space_players.get(guild_id)
        if players and user_id in players:
            del players[user_id]
            removed = True
            if not players:
                space_players.pop(guild_id, None)

    return removed


def _empty_explore_save_data() -> dict:
    return {
        "map_id": None,
        "x": None,
        "y": None,
        "guild_id": None,
        "is_world_map": None,
        "switches": {},
        "variables": {},
    }


def _sanitize_explore_save_entries(values, allowed_ids: set[str]) -> dict:
    if not isinstance(values, dict):
        return {}

    sanitized: dict = {}
    for raw_id, value in values.items():
        try:
            save_id = str(int(raw_id))
        except (TypeError, ValueError):
            continue
        if save_id not in allowed_ids:
            continue
        sanitized[save_id] = value
    return sanitized


def _sanitize_explore_save_data(payload, *, for_storage: bool) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    sanitized = _empty_explore_save_data()

    allowed_switch_ids = _normalize_save_id_allowlist(EXPLORE_SAVE_ALLOWED_SWITCH_IDS)
    allowed_variable_ids = _normalize_save_id_allowlist(EXPLORE_SAVE_ALLOWED_VARIABLE_IDS)

    guild_id = payload.get("guild_id")
    if guild_id is not None:
        guild_id = str(guild_id).strip() or None

    raw_is_world_map = payload.get("is_world_map")
    if raw_is_world_map is None and guild_id is not None:
        is_world_map = guild_id == "world"
    elif raw_is_world_map is None:
        is_world_map = None
    else:
        is_world_map = bool(raw_is_world_map)

    sanitized.update({
        "map_id": _parse_optional_int(payload.get("map_id")),
        "x": _parse_optional_int(payload.get("x")),
        "y": _parse_optional_int(payload.get("y")),
        "guild_id": guild_id,
        "is_world_map": is_world_map,
        "switches": _sanitize_explore_save_entries(payload.get("switches"), allowed_switch_ids),
        "variables": _sanitize_explore_save_entries(payload.get("variables"), allowed_variable_ids),
    })

    updated_at = int(time.time()) if for_storage else _parse_optional_int(payload.get("updated_at"))
    if updated_at is not None:
        sanitized["updated_at"] = updated_at

    return sanitized


def get_explore_save_data(user_id: int) -> tuple[bool, dict]:
    stored = get_user_data(0, user_id, EXPLORE_SAVE_DATA_KEY, None)
    if stored is None:
        return False, _empty_explore_save_data()
    return True, _sanitize_explore_save_data(stored, for_storage=False)


def set_explore_save_data(user_id: int, payload) -> dict:
    sanitized = _sanitize_explore_save_data(payload, for_storage=True)
    set_user_data(0, user_id, EXPLORE_SAVE_DATA_KEY, sanitized)
    return sanitized

def get_space_tiles(guild_id: int) -> list[dict]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT x, y, z, tile_id FROM explore_space_tiles WHERE guild_id = ?",
            (guild_id,),
        )
        rows = cursor.fetchall()
        return [
            {'x': int(r[0]), 'y': int(r[1]), 'z': int(r[2]), 'tile_id': r[3]}
            for r in rows
        ]


def set_space_tile(guild_id: int, x: int, y: int, z: int, tile_id: str):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO explore_space_tiles (guild_id, x, y, z, tile_id) VALUES (?, ?, ?, ?, ?)",
            (guild_id, x, y, z, tile_id),
        )
        conn.commit()


def get_available_skins(user_id: int | None = None) -> list[dict]:
    """Full skin catalog; when user_id is given, mark ownership."""
    owned = get_user_owned_skins(int(user_id)) if user_id is not None else set()
    result = []
    for skin in SKIN_CATALOG:
        entry = {
            "id": skin["id"],
            "name": skin["name"],
            "price": int(skin.get("price") or 0),
            "icon_url": None,
        }
        if user_id is not None:
            entry["owned"] = skin["id"] in owned
        result.append(entry)
    return result


# --- Music Integration ---

# ── Thumbnail proxy cache ──────────────────────────────────────────────────────
# Maps  UUID  →  {url: str, expires_at: float}
# Maps  URL   →  UUID  (reverse; for deduplication)
_thumb_by_uuid: dict[str, dict] = {}
_thumb_by_url:  dict[str, str]  = {}
_thumb_lock     = threading.Lock()
_THUMB_TTL      = 30 * 60   # seconds — long enough to outlive any single track
_THUMB_MAX      = 300       # hard cap; oldest entries evicted when exceeded


def _thumb_cleanup_locked() -> None:
    """Evict all expired entries. Must be called while holding _thumb_lock."""
    now = time.time()
    expired = [uid for uid, v in _thumb_by_uuid.items() if v['expires_at'] <= now]
    for uid in expired:
        url = _thumb_by_uuid.pop(uid, {}).get('url')
        if url:
            _thumb_by_url.pop(url, None)


def _get_thumbnail_proxy_url(raw_url: str | None) -> str | None:
    """
    Register *raw_url* in the proxy cache and return its proxy path.
    Returns None when raw_url is falsy.
    """
    if not raw_url:
        return None
    with _thumb_lock:
        _thumb_cleanup_locked()

        uid = _thumb_by_url.get(raw_url)
        if uid and uid in _thumb_by_uuid:
            # Refresh TTL so actively-played thumbnails stay alive
            _thumb_by_uuid[uid]['expires_at'] = time.time() + _THUMB_TTL
            return f'/api/explore/music/thumbnail/{uid}'

        # Enforce hard cap — evict the entry that expires soonest
        if len(_thumb_by_uuid) >= _THUMB_MAX:
            oldest = min(_thumb_by_uuid, key=lambda k: _thumb_by_uuid[k]['expires_at'])
            old_url = _thumb_by_uuid.pop(oldest, {}).get('url')
            if old_url:
                _thumb_by_url.pop(old_url, None)

        uid = str(_uuid_mod.uuid4())
        _thumb_by_uuid[uid] = {'url': raw_url, 'expires_at': time.time() + _THUMB_TTL}
        _thumb_by_url[raw_url] = uid
    return f'/api/explore/music/thumbnail/{uid}'


def _run_async(coro, timeout: float = 10.0):
    """Run an async coroutine from a synchronous (Flask) handler thread."""
    loop = bot.loop
    if loop is None or not loop.is_running():
        raise RuntimeError("Bot event loop is not running")
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


def _get_music_state(guild_id: int) -> dict | None:
    """Return current music info dict for a guild, or None if nothing is playing."""
    if not _music_available:
        return None
    guild = bot.get_guild(guild_id)
    if not guild:
        return None
    player = guild.voice_client
    if not player:
        return None

    # Radio mode — read from Music cog's cached info
    station_key = _music_mod.radio_modes.get(guild_id)
    if station_key:
        music_cog = bot.get_cog("Music")
        radio_info: dict = (music_cog._get_radio_info(station_key) if music_cog else {}) or {}
        return {
            "title": radio_info.get("title") or f"📻 {station_key}",
            "author": radio_info.get("artist"),
            "thumbnail": _get_thumbnail_proxy_url(radio_info.get("thumbnail")),
            "url": radio_info.get("url"),
            "current": None,
            "is_paused": False,
            "is_radio": True,
        }

    current = getattr(player, 'current', None)
    if not current:
        return None
    return {
        "title": getattr(current, 'title', None),
        "author": getattr(current, 'author', None),
        "thumbnail": _get_thumbnail_proxy_url(getattr(current, 'thumbnail', None)),
        "url": getattr(current, 'uri', None),
        "current": int(getattr(player, 'position', 0) // 1000),
        "is_paused": bool(getattr(player, 'is_paused', False)),
        "is_radio": False,
    }


def _is_music_context_available(guild_id: int) -> bool:
    guild = bot.get_guild(guild_id)
    if not guild:
        return False
    player = guild.voice_client
    return bool(player and player.channel)


async def _emit_music_update(guild_id: int) -> None:
    """Broadcast music_update to all Socket.IO clients in the guild room."""
    gid_str = str(guild_id)
    state = _get_music_state(guild_id)
    available = _is_music_context_available(guild_id)
    if state:
        await sio.emit("music_update", {"guild_id": gid_str, "playing": True, "available": available, **state}, room=_music_room_name(gid_str))
    else:
        await sio.emit("music_update", {"guild_id": gid_str, "playing": False, "available": available}, room=_music_room_name(gid_str))


def _find_user_music_guild(user_id: int):
    """
    Find the first guild where this user AND the bot are in the same voice channel.
    Returns (guild, guild_id) on success, or (None, None).
    """
    for guild in bot.guilds:
        player = guild.voice_client
        if not player or not player.channel:
            continue
        member = guild.get_member(user_id)
        if not member or not member.voice or not member.voice.channel:
            continue
        if member.voice.channel.id == player.channel.id:
            return guild, guild.id
    return None, None


def _verify_user_in_voice_channel(guild_id: int, user_id: int) -> bool:
    """Return True only if the user is in the same voice channel as the bot right now."""
    guild = bot.get_guild(guild_id)
    if not guild:
        return False
    player = guild.voice_client
    if not player or not player.channel:
        return False
    member = guild.get_member(user_id)
    if not member or not member.voice or not member.voice.channel:
        return False
    return member.voice.channel.id == player.channel.id


# --- Routes ---

@app.route("/api/explore/authenticate", methods=["POST"])
def explore_authenticate():
    code = request.json.get("code")
    if not code:
        return jsonify({"error": "Missing code"}), 400
    
    # Exchange code for token
    data = {
        "client_id": bot.application_id,
        "client_secret": config("client_secret"),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "https://url.truc/"
    }
    response = requests.post("https://discord.com/api/oauth2/token", data=data)
    if response.status_code == 200:
        token_data = response.json()
        access_token = token_data.get("access_token")
            
        return jsonify({"token": access_token})
    else:
        return jsonify({"error": "Failed to exchange code for token"}), 500


@app.route("/api/explore/me", methods=["GET"])
@_require_explore_auth
def explore_me():
    user_id = int(g.explore_user['user_id'])
    username = g.explore_user.get('username', 'Unknown')
    skin_id = get_user_skin(user_id)
    return jsonify({
        'name': username,
        'skin_id': skin_id,
    })


@app.route('/api/explore/me/save-data', methods=['GET', 'POST'])
@_require_explore_auth
def explore_my_save_data():
    user_id = int(g.explore_user['user_id'])

    if request.method == 'GET':
        has_save, save_data = get_explore_save_data(user_id)
        return jsonify({
            'has_save': has_save,
            **save_data,
        })

    payload = request.get_json(silent=True) or {}
    save_data = set_explore_save_data(user_id, payload)
    return jsonify({
        'success': True,
        **save_data,
    })


@app.route('/api/explore/auth/discord-token', methods=['POST'])
def explore_auth_discord_token():
    """Verify a Discord user token and exchange it for an Explore auth_token."""
    _cleanup_auth_tokens()
    data = request.get_json(silent=True) or {}
    discord_token = data.get('discord_token') or data.get('token')
    if not discord_token:
        return jsonify({'error': 'Missing discord_token'}), 400

    d_user = _verify_discord_user_token(str(discord_token))
    if not d_user:
        return jsonify({'error': 'Invalid Discord token'}), 401

    user_id = int(d_user['id'])
    username = d_user.get('username') or d_user.get('global_name') or str(user_id)
    guild_ids = _fetch_discord_user_guild_ids(str(discord_token))

    auth_token = _issue_explore_auth_token(
        user_id=user_id,
        username=username,
        guild_ids=guild_ids,
        discord_token=str(discord_token),
    )
    return jsonify({
        'auth_token': auth_token,
        'expires_in': AUTH_TOKEN_TTL_SECONDS,
    })


@app.route('/api/explore/servers', methods=['GET'])
@_require_explore_auth
def explore_get_accessible_servers():
    """List accessible servers with icon url + name + member count + in-space count."""
    guild_ids = _get_request_explore_guild_ids(refresh=True)
    result = []
    for gid in get_all_server_config_key("explore_config"):
        server = get_explore_server(int(gid))
        if not server or not server.get("enabled"):
            continue
        guild = bot.get_guild(int(gid))
        if not guild:
            continue
        gid_str = str(gid)
        is_member = _is_explore_server_member(gid_str, guild_ids)
        if not is_member and not server.get("is_public"):
            continue
        result.append(_build_explore_server_payload(guild, server, guild_ids, int(g.explore_user["user_id"])))

    result.sort(key=lambda entry: (not entry.get("is_member", False), entry.get("name", "").lower(), entry["id"]))
    return jsonify(result)


@app.route('/api/explore/server/<guild_id>', methods=['GET'])
@_require_explore_auth
def explore_get_server_info(guild_id: str):
    """Return server info: icon url + name + member count + in-space count."""
    gid = str(guild_id)
    server = get_explore_server(int(gid))
    if not server or not server.get('enabled'):
        return jsonify({'error': 'Server not enabled'}), 404

    guild_ids = _get_request_explore_guild_ids(refresh=True)
    if not _can_access_explore_server(server, gid, guild_ids):
        return jsonify({'error': 'Forbidden'}), 403

    guild = bot.get_guild(int(gid))
    if not guild:
        return jsonify({'error': 'Guild not found'}), 404

    return jsonify(_build_explore_server_payload(guild, server, guild_ids, int(g.explore_user["user_id"])))


@app.route('/api/explore/space/<guild_id>', methods=['GET'])
@_require_explore_auth
def explore_get_space_data(guild_id: str):
    """Return server space tile data as list[{x,y,z,tile_id}]."""
    gid = str(guild_id)
    if gid != 'world':
        server = get_explore_server(int(gid))
        if not server or not server.get('enabled'):
            return jsonify({'error': 'Server not enabled'}), 404

    guild_ids = _get_request_explore_guild_ids(refresh=True)
    if gid != 'world' and not _can_access_explore_server(server, gid, guild_ids):
        return jsonify({'error': 'Forbidden'}), 403

    tiles = get_space_tiles(0 if gid == 'world' else int(gid))
    return jsonify({'tiles': tiles})


@app.route('/api/explore/skins', methods=['GET'])
@_require_explore_auth
def explore_get_skins():
    user_id = int(g.explore_user['user_id'])
    balance = None
    if _economy_available:
        try:
            balance = float(_economy_mod.get_global_balance(user_id))
        except Exception:
            balance = None
    return jsonify({
        'skins': get_available_skins(user_id),
        'current_skin_id': get_user_skin(user_id),
        'balance': balance,
        'currency_name': getattr(_economy_mod, 'GLOBAL_CURRENCY_NAME', '全域幣') if _economy_available else None,
    })


@app.route('/api/explore/skins/buy', methods=['POST'])
@_require_explore_auth
def explore_buy_skin():
    data = request.get_json(silent=True) or {}
    skin_id = _normalize_skin_id_for_lookup(data.get('skin_id'))
    if not skin_id:
        return jsonify({'error': 'Invalid skin_id'}), 400

    skin = _SKIN_BY_ID[skin_id]
    price = int(skin.get('price') or 0)
    user_id = int(g.explore_user['user_id'])

    if user_owns_skin(user_id, skin_id):
        return jsonify({'error': '你已經擁有這個皮膚了'}), 400
    if price <= 0:
        return jsonify({'error': '這個皮膚是免費的,不用買'}), 400
    if not _economy_available:
        return jsonify({'error': '經濟系統未啟用,無法購買'}), 503

    # Server-side balance check & deduction (never trust client)
    balance = float(_economy_mod.get_global_balance(user_id))
    if balance < price:
        return jsonify({'error': f'全域幣不足(需要 {price},你有 {balance:.0f})', 'balance': balance}), 400

    _economy_mod.set_global_balance(user_id, balance - price)
    try:
        _economy_mod.log_transaction(
            _economy_mod.GLOBAL_GUILD_ID, user_id, 'explore_skin',
            -price, getattr(_economy_mod, 'GLOBAL_CURRENCY_NAME', '全域幣'),
            f"購買 Explore 皮膚 {skin['name']} ({skin_id})",
        )
    except Exception:
        pass
    add_user_owned_skin(user_id, skin_id)
    return jsonify({
        'success': True,
        'skin_id': skin_id,
        'balance': float(_economy_mod.get_global_balance(user_id)),
    })


def _normalize_skin_id_for_lookup(skin_id) -> str | None:
    sid = str(skin_id or '').strip()
    sid = _LEGACY_SKIN_MAP.get(sid, sid)
    return sid if sid in _SKIN_BY_ID else None


@app.route('/api/explore/quests', methods=['GET'])
@_require_explore_auth
def explore_get_quests():
    user_id = int(g.explore_user['user_id'])
    payload = build_quest_state_payload(user_id)
    payload['xp'] = get_user_xp(user_id)
    payload['level'] = get_user_level(user_id)
    return jsonify(payload)


@app.route('/api/explore/quests/complete', methods=['POST'])
@_require_explore_auth
def explore_complete_quest():
    data = request.get_json(silent=True) or {}
    quest_id = str(data.get('quest_id') or '').strip()
    if not quest_id:
        return jsonify({'error': 'Missing quest_id'}), 400

    user_id = int(g.explore_user['user_id'])
    ok, result = complete_user_quest(user_id, quest_id)
    if not ok:
        return jsonify({'error': result}), 400

    # 廣播升級事件到玩家目前所在房間
    if isinstance(result, dict) and result.get('leveled_up'):
        gid = None
        with _session_lock:
            for sess in socket_sessions.values():
                if str(sess.get('user_id')) == str(user_id) and sess.get('guild_id'):
                    gid = str(sess['guild_id'])
                    break
        if gid:
            try:
                _run_async(_broadcast_level_up(gid, str(user_id), int(result['level'])))
            except Exception:
                pass

    return jsonify({'success': True, **result})


@app.route('/api/explore/me/skin', methods=['POST'])
@_require_explore_auth
def explore_set_my_skin():
    data = request.get_json(silent=True) or {}
    skin_id = _normalize_skin_id_for_lookup(data.get('skin_id'))
    if skin_id is None:
        return jsonify({'error': 'Invalid skin_id'}), 400

    user_id = int(g.explore_user['user_id'])
    if not user_owns_skin(user_id, skin_id):
        return jsonify({'error': '你還沒擁有這個皮膚'}), 403

    set_user_skin(user_id, skin_id)
    return jsonify({'success': True, 'skin_id': skin_id})

@app.route("/api/explore/skin", methods=["GET", "POST"])
def explore_save_skin():
    # Similar auth check needed
    auth_header = request.headers.get('Authorization')
    uid = None

    if not auth_header:
        # Mock
        # data = request.json
        # if data.get('dev_id'):
        #     uid = int(data.get('dev_id'))
        return jsonify({"error": "Unauthorized"}), 401
    else:
        # Verify
        try:
            r = requests.get("https://discord.com/api/users/@me", headers={"Authorization": auth_header})
            if r.status_code == 200:
                uid = int(r.json()['id'])
        except:
            pass

    if not uid:
        return jsonify({"error": "Unauthorized"}), 401

    skin_id = request.json.get('skin_id')
    if not skin_id:
        return jsonify({"success": True, "skin_id": get_user_skin(int(uid))})

    normalized = _normalize_skin_id_for_lookup(skin_id)
    if normalized is None or not user_owns_skin(int(uid), normalized):
        return jsonify({"error": "Invalid or unowned skin"}), 403

    set_user_skin(int(uid), normalized)
    return jsonify({"success": True, "skin_id": normalized})

@app.route("/api/explore/icon/guild/<guild_id>", methods=["GET"])
def explore_guild_icon(guild_id):
    # Proxy guild icon
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return jsonify({"error": "Guild not found"}), 404
    if not guild.icon:
        return jsonify({"error": "Guild has no icon"}), 404
    return requests.get(guild.icon.url).content, 200, {'Content-Type': 'image/png'}


# --- Music API ---
# All three endpoints identify the target guild via the caller's voice channel (user_id from auth
# token). The user MUST be in the same voice channel as the bot; otherwise 403 is returned.

@app.route('/api/explore/music/thumbnail/<thumb_id>')
def explore_music_thumbnail(thumb_id: str):
    """
    Proxy a music thumbnail by its UUID key.
    No auth required — the opaque UUID is sufficient access control.
    Needed because Discord Activities' CSP blocks direct external image URLs.
    """
    with _thumb_lock:
        _thumb_cleanup_locked()
        entry = _thumb_by_uuid.get(str(thumb_id))

    if not entry:
        return jsonify({'error': 'Not found or expired'}), 404

    raw_url = entry.get('url')
    if not raw_url:
        return jsonify({'error': 'Not found'}), 404

    try:
        resp = requests.get(raw_url, timeout=10)
        if resp.status_code != 200:
            return jsonify({'error': 'Upstream error'}), 502

        content_type = resp.headers.get('Content-Type', 'image/jpeg')
        if not content_type.startswith('image/'):
            content_type = 'image/jpeg'

        return resp.content, 200, {
            'Content-Type': content_type,
            'Cache-Control': f'public, max-age={_THUMB_TTL}',
        }
    except Exception as e:
        log(f"Thumbnail proxy fetch failed: {e}", level=logging.WARNING, module_name="Explore")
        return jsonify({'error': 'Failed to fetch thumbnail'}), 502

def _music_resolve_caller(user_id: int):
    """
    Find the guild & player for a REST music request.
    Returns (guild, player) when the user is co-located with the bot, else (None, error_response).
    The "error_response" is a Flask response tuple ready to be returned.
    """
    guild, guild_id = _find_user_music_guild(user_id)
    if not guild:
        return None, (jsonify({'error': 'No Music Found'}), 404)
    player = guild.voice_client
    if not player:
        return None, (jsonify({'error': 'No Music Found'}), 404)
    return (guild, player), None


@app.route('/api/explore/music', methods=['GET'])
@_require_explore_auth
def explore_music_get():
    """
    GET /api/explore/music
    Looks up the caller's current voice channel (user_id from auth token).
    Returns music state, or 404 {"error": "No Music Found"} when not co-located with bot.
    """
    if not _music_available:
        return jsonify({'error': 'Music module not available'}), 503

    user_id = int(g.explore_user['user_id'])
    result, err_resp = _music_resolve_caller(user_id)
    if err_resp:
        return err_resp

    guild, _ = result
    state = _get_music_state(guild.id)
    if not state:
        return jsonify({'guild_id': str(guild.id), 'playing': False, 'available': True})
    return jsonify({**state, 'guild_id': str(guild.id), 'playing': True, 'available': True})


@app.route('/api/explore/music', methods=['PATCH'])
@_require_explore_auth
def explore_music_patch():
    """
    PATCH /api/explore/music
    Body: {"action": "next|pause|play|seek|recommend", "data": <optional>}
      - seek:      data = seconds (int)
      - recommend: data = count   (int, 1-10, default 5)
    Caller must be in the same voice channel as the bot.
    """
    if not _music_available:
        return jsonify({'error': 'Music module not available'}), 503

    user_id = int(g.explore_user['user_id'])
    result, err_resp = _music_resolve_caller(user_id)
    if err_resp:
        return err_resp

    guild, player = result

    payload = request.get_json(silent=True) or {}
    action = str(payload.get('action') or '').lower().strip()
    action_data = payload.get('data')

    VALID_ACTIONS = {'next', 'pause', 'play', 'seek', 'recommend'}
    if action not in VALID_ACTIONS:
        return jsonify({'error': f'Invalid action. Valid: {", ".join(sorted(VALID_ACTIONS))}'}), 400

    try:
        if action == 'pause':
            _run_async(player.set_pause(True))

        elif action == 'play':
            _run_async(player.set_pause(False))

        elif action == 'next':
            if _music_mod.radio_modes.get(guild.id):
                return jsonify({'error': 'Cannot skip in radio mode'}), 400
            queue = _music_mod.get_queue(guild.id)
            next_track = queue.get()

            async def _skip():
                await player.stop()
                if next_track:
                    await player.play(next_track)

            _run_async(_skip())

        elif action == 'seek':
            try:
                seek_ms = int(action_data) * 1000
            except (TypeError, ValueError):
                return jsonify({'error': 'seek requires data as integer seconds'}), 400
            _run_async(player.seek(seek_ms))

        elif action == 'recommend':
            if _music_mod.radio_modes.get(guild.id):
                return jsonify({'error': 'Cannot recommend in radio mode'}), 400
            current = getattr(player, 'current', None)
            if not current:
                return jsonify({'error': 'No current track to base recommendations on'}), 404
            try:
                count = max(1, min(int(action_data or 5), 10))
            except (TypeError, ValueError):
                count = 5

            async def _recommend():
                results = await player.get_recommendations(track=current)
                if not results:
                    return 0
                tracks = getattr(results, 'tracks', results)
                q = _music_mod.get_queue(guild.id)
                for t in tracks[:count]:
                    q.add(t)
                if not player.is_playing:
                    nt = q.get()
                    if nt:
                        await player.play(nt)
                return min(len(tracks), count)

            added = _run_async(_recommend())
            try:
                _run_async(_emit_music_update(guild.id))
            except Exception:
                pass
            return jsonify({'success': True, 'action': action, 'added': added})

    except Exception as e:
        log(f"Music PATCH action='{action}' failed: {e}", level=logging.WARNING, module_name="Explore")
        return jsonify({'error': str(e)}), 500

    try:
        _run_async(_emit_music_update(guild.id))
    except Exception:
        pass
    return jsonify({'success': True, 'action': action})


@app.route('/api/explore/music', methods=['DELETE'])
@_require_explore_auth
def explore_music_delete():
    """
    DELETE /api/explore/music
    Stop music and disconnect the player. Caller must be in the same voice channel as the bot.
    """
    if not _music_available:
        return jsonify({'error': 'Music module not available'}), 503

    user_id = int(g.explore_user['user_id'])
    result, err_resp = _music_resolve_caller(user_id)
    if err_resp:
        return err_resp

    guild, player = result
    music_cog = bot.get_cog("Music")

    try:
        async def _stop():
            await player.stop()
            await player.destroy()
            if music_cog:
                await music_cog._cleanup_player(guild.id)

        _run_async(_stop())
        _run_async(_emit_music_update(guild.id))
    except Exception as e:
        log(f"Music DELETE failed: {e}", level=logging.WARNING, module_name="Explore")
        return jsonify({'error': str(e)}), 500

    return jsonify({'success': True})


@app.route("/explore/<path:path>")
def serve_game_files(path):
    return send_from_directory(os.path.join(os.getcwd(), 'Explore'), path)

@app.route("/explore/")
def serve_game_index():
    return send_from_directory(os.path.join(os.getcwd(), 'Explore'), 'index.html')

# --- Socket.IO Events (ASGI) ---


@sio.event
async def connect(sid, environ, auth):
    _cleanup_auth_tokens()

    token = None
    if isinstance(auth, dict):
        token = auth.get("token") or auth.get("auth_token")
    if not token:
        token = _parse_token_from_query(environ)
    if not token:
        return False

    token = str(token).strip()
    with _auth_lock:
        entry = auth_tokens.get(token)
    if not entry:
        return False

    with _session_lock:
        socket_sessions[sid] = {
            "user_id": str(entry["user_id"]),
            "username": entry.get("username", "Unknown"),
            "guild_id": None,
            "music_guild_id": None,
            "auth_token": token,
            "guild_ids": [str(x) for x in (entry.get("guild_ids") or [])],
        }
    log(f"User {entry.get('username','Unknown')} ({entry['user_id']}) connected (sid={sid})", module_name="Explore")


@sio.event
async def disconnect(sid):
    with _session_lock:
        sess = socket_sessions.pop(sid, None)

    if not sess:
        return

    gid = sess.get("guild_id")
    uid = sess.get("user_id")
    if gid and uid:
        removed = _remove_user_from_space(str(gid), str(uid))
        if removed:
            await sio.emit("user_left", {"guild_id": str(gid), "user_id": str(uid)}, room=str(gid), skip_sid=sid)
        log(f"User {sess.get('username','Unknown')} ({uid}) disconnected (sid={sid})", module_name="Explore")


@sio.on("join")
async def on_join(sid, data=None):
    sess = _get_socket_session(sid)
    if not sess:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    payload = data or {}
    guild_id = str(payload.get("guild_id") or "world")
    map_id = _parse_optional_int(payload.get("map_id"))
    x = _parse_optional_int(payload.get("x"))
    y = _parse_optional_int(payload.get("y"))
    direction = _parse_optional_int(payload.get("direction"))
    move_speed = payload.get("moveSpeed")
    move_frequency = payload.get("moveFrequency")

    if guild_id != "world":
        try:
            server = get_explore_server(int(guild_id))
        except Exception:
            server = None
        if not server or not server.get("enabled"):
            await sio.emit("error", {"message": "Server not enabled"}, to=sid)
            return

        allowed = _refresh_socket_session_guild_ids(sid)
        if str(guild_id) not in allowed and server.get("require_join"):
            await sio.emit(
                "error",
                {
                    "message": "Membership required",
                    "guild_id": guild_id,
                    "invite_link": server.get("invite_link"),
                },
                to=sid,
            )
            return
        if str(guild_id) not in allowed and not server.get("is_public"):
            await sio.emit("error", {"message": "Forbidden", "guild_id": guild_id}, to=sid)
            return

    uid = sess["user_id"]
    username = sess.get("username", "Unknown")
    skin_id = get_user_skin(int(uid))
    level = get_user_level(int(uid))
    previous_guild_id = str(sess.get("guild_id")) if sess.get("guild_id") else None

    if previous_guild_id and previous_guild_id != guild_id:
        await sio.leave_room(sid, previous_guild_id)
        if _remove_user_from_space(previous_guild_id, uid):
            await sio.emit("user_left", {"guild_id": previous_guild_id, "user_id": uid}, room=previous_guild_id, skip_sid=sid)

    with _session_lock:
        if sid in socket_sessions:
            socket_sessions[sid]["guild_id"] = guild_id

    await sio.enter_room(sid, guild_id)

    with _presence_lock:
        space_presence.setdefault(guild_id, set()).add(uid)
        players = space_players.setdefault(guild_id, {})
        previous_state = players.get(uid, {})
        players[uid] = {
            "user_id": uid,
            "name": username,
            "skin_id": skin_id,
            "level": level,
            "map_id": map_id,
            "x": x if x is not None else previous_state.get("x", 0),
            "y": y if y is not None else previous_state.get("y", 0),
            "direction": direction if direction is not None else previous_state.get("direction", 2),
            "moveSpeed": move_speed if move_speed is not None else previous_state.get("moveSpeed"),
            "moveFrequency": move_frequency if move_frequency is not None else previous_state.get("moveFrequency"),
        }
        snapshot_players = list(players.values())

    await sio.emit("joined", {"guild_id": guild_id, "user_id": uid, "name": username, "skin_id": skin_id, "level": level, "map_id": map_id}, to=sid)
    await sio.emit("room_state", {"guild_id": guild_id, "players": snapshot_players}, to=sid)
    await sio.emit(
        "user_joined",
        {
            "guild_id": guild_id,
            "user_id": uid,
            "name": username,
            "skin_id": skin_id,
            "level": level,
            "map_id": map_id,
            "x": players[uid].get("x", 0),
            "y": players[uid].get("y", 0),
            "direction": players[uid].get("direction", 2),
            "moveSpeed": players[uid].get("moveSpeed"),
            "moveFrequency": players[uid].get("moveFrequency"),
        },
        room=guild_id,
        skip_sid=sid,
    )

    # 加入房間後補發聊天歷史(只給自己)
    history = _get_chat_history(guild_id)
    if history:
        await sio.emit("chat_history", {"guild_id": guild_id, "messages": history}, to=sid)


@sio.on("leave")
async def on_leave(sid, data=None):
    sess = _get_socket_session(sid)
    if not sess:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    guild_id = str((data or {}).get("guild_id") or sess.get("guild_id") or "world")
    uid = sess.get("user_id")

    await sio.leave_room(sid, guild_id)
    removed = _remove_user_from_space(guild_id, str(uid))
    with _session_lock:
        if sid in socket_sessions and str(socket_sessions[sid].get("guild_id") or "") == guild_id:
            socket_sessions[sid]["guild_id"] = None

    await sio.emit("left", {"guild_id": guild_id, "user_id": uid}, to=sid)
    if removed:
        await sio.emit("user_left", {"guild_id": guild_id, "user_id": uid}, room=guild_id, skip_sid=sid)


@sio.on("move")
async def on_move(sid, data=None):
    sess = _get_socket_session(sid)
    if not sess:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    payload = data or {}
    guild_id = str(payload.get("guild_id") or sess.get("guild_id") or "world")
    map_id = _parse_optional_int(payload.get("map_id"))
    x = payload.get("x")
    y = payload.get("y")
    direction = payload.get("direction")
    moveSpeed = payload.get("moveSpeed")
    moveFrequency = payload.get("moveFrequency")
    if x is None or y is None:
        return

    try:
        ix = int(x)
        iy = int(y)
        idir = int(direction) if direction is not None else None
    except Exception:
        return

    with _presence_lock:
        players = space_players.setdefault(guild_id, {})
        existing = players.get(sess["user_id"]) or {
            "user_id": sess["user_id"],
            "name": sess.get("username", "Unknown"),
            "skin_id": get_user_skin(int(sess["user_id"])),
        }
        existing["x"] = ix
        existing["y"] = iy
        existing["map_id"] = map_id
        if idir is not None:
            existing["direction"] = idir
        if moveSpeed is not None:
            existing["moveSpeed"] = moveSpeed
        if moveFrequency is not None:
            existing["moveFrequency"] = moveFrequency
        players[sess["user_id"]] = existing

    out = {"guild_id": guild_id, "user_id": sess["user_id"], "map_id": map_id, "x": ix, "y": iy, "moveSpeed": moveSpeed, "moveFrequency": moveFrequency}
    if idir is not None:
        out["direction"] = idir

    await sio.emit("user_moved", out, room=guild_id, skip_sid=sid)


@sio.on("edit_map")
async def on_edit_map(sid, data=None):
    sess = _get_socket_session(sid)
    if not sess:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    payload = data or {}
    guild_id = str(payload.get("guild_id") or sess.get("guild_id") or "world")
    x = payload.get("x")
    y = payload.get("y")
    z = payload.get("z", 0)
    tile_id = payload.get("tile_id")
    if x is None or y is None or tile_id is None:
        return

    # Only guild admins (manage_guild/administrator) may edit; world map is bot-owner only.
    if not _is_explore_guild_admin(guild_id, sess["user_id"]):
        await sio.emit("error", {"message": "Edit permission denied", "guild_id": guild_id}, to=sid)
        return

    set_space_tile(0 if guild_id == "world" else int(guild_id), int(x), int(y), int(z), str(tile_id))

    await sio.emit(
        "map_edited",
        {"guild_id": guild_id, "x": int(x), "y": int(y), "z": int(z), "tile_id": str(tile_id)},
        room=guild_id,
        skip_sid=sid,
    )


@sio.on("skin_change")
async def on_skin_change(sid, data=None):
    sess = _get_socket_session(sid)
    if not sess:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    payload = data or {}
    skin_id = _normalize_skin_id_for_lookup(payload.get("skin_id"))
    if skin_id is None:
        return

    uid = int(sess["user_id"])
    if not user_owns_skin(uid, skin_id):
        await sio.emit("error", {"message": "你還沒擁有這個皮膚"}, to=sid)
        return
    set_user_skin(uid, str(skin_id))
    guild_id = str(payload.get("guild_id") or sess.get("guild_id") or "world")
    map_id = _parse_optional_int(payload.get("map_id"))

    with _presence_lock:
        players = space_players.setdefault(guild_id, {})
        existing = players.get(str(uid)) or {
            "user_id": str(uid),
            "name": sess.get("username", "Unknown"),
            "x": 0,
            "y": 0,
        }
        existing["skin_id"] = str(skin_id)
        existing["map_id"] = map_id
        players[str(uid)] = existing

    await sio.emit(
        "skin_changed",
        {"guild_id": guild_id, "user_id": str(uid), "skin_id": str(skin_id), "map_id": map_id},
        room=guild_id,
        skip_sid=sid,
    )


@sio.on("emote")
async def on_emote(sid, data=None):
    """表情氣泡:balloon_id 1-15(RMMZ 內建),廣播到同房間。"""
    sess = _get_socket_session(sid)
    if not sess:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    payload = data or {}
    guild_id = str(payload.get("guild_id") or sess.get("guild_id") or "world")
    map_id = _parse_optional_int(payload.get("map_id"))
    try:
        balloon_id = int(payload.get("balloon_id"))
    except (TypeError, ValueError):
        return
    if not (1 <= balloon_id <= 15):
        return

    uid = str(sess["user_id"])

    # 表情限速(共用 XP 冷卻表,同時當 rate limit 用)
    now = time.time()
    with _xp_lock:
        per_user = _xp_cooldowns.setdefault(uid, {})
        if now - per_user.get("emote_rate", 0.0) < 1.5:
            return
        per_user["emote_rate"] = now

    add_user_xp(int(uid), EMOTE_XP_AMOUNT, source="emote", cooldown=EMOTE_XP_COOLDOWN_SECONDS)

    await sio.emit(
        "user_emote",
        {"guild_id": guild_id, "user_id": uid, "balloon_id": balloon_id, "map_id": map_id},
        room=guild_id,
    )


@sio.on("chat_send")
async def on_chat_send(sid, data=None):
    """伺服內聊天:過濾禁字後廣播,並橋接到 Discord 頻道。"""
    sess = _get_socket_session(sid)
    if not sess:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    payload = data or {}
    guild_id = str(payload.get("guild_id") or sess.get("guild_id") or "world")
    text = str(payload.get("text") or "").strip()
    if not text:
        return
    if len(text) > CHAT_MAX_LENGTH:
        text = text[:CHAT_MAX_LENGTH]

    uid = str(sess["user_id"])
    username = sess.get("username", "Unknown")

    # Rate limit
    now = time.time()
    with _chat_lock:
        if now - _chat_last_sent.get(uid, 0.0) < CHAT_RATE_LIMIT_SECONDS:
            await sio.emit("chat_error", {"message": "你講話太快了,慢一點"}, to=sid)
            return
        _chat_last_sent[uid] = now

    filtered_text, was_filtered = filter_chat_text(text, guild_id)

    _xp, level, leveled_up = add_user_xp(int(uid), CHAT_XP_AMOUNT, source="chat", cooldown=CHAT_XP_COOLDOWN_SECONDS)

    message = _make_chat_message(guild_id, uid, username, filtered_text, source="game", level=level)
    message["filtered"] = was_filtered
    _append_chat_history(guild_id, message)

    await sio.emit("chat_message", message, room=guild_id)
    if leveled_up:
        await _broadcast_level_up(guild_id, uid, level)

    await _forward_chat_to_discord(guild_id, username, filtered_text)


# --- Music Socket.IO Events ---

@sio.on("music_get")
async def on_music_get(sid, data=None):
    """
    Request current music state. The guild is auto-detected from the caller's voice channel.
    Responds with music_update only to the requesting sid.
    """
    sess = _get_socket_session(sid)
    if not sess:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    if not _music_available:
        await _set_socket_music_room(sid, None)
        await sio.emit("music_update", {"playing": False, "available": False}, to=sid)
        return

    try:
        user_id = int(sess["user_id"])
    except (ValueError, TypeError):
        await _set_socket_music_room(sid, None)
        await sio.emit("music_update", {"playing": False, "available": False}, to=sid)
        return

    guild, guild_id = _find_user_music_guild(user_id)
    if not guild:
        await _set_socket_music_room(sid, None)
        await sio.emit("music_update", {"playing": False, "available": False}, to=sid)
        return

    await _set_socket_music_room(sid, guild_id)

    gid = str(guild_id)
    state = _get_music_state(guild_id)
    if state:
        await sio.emit("music_update", {"guild_id": gid, "playing": True, "available": True, **state}, to=sid)
    else:
        await sio.emit("music_update", {"guild_id": gid, "playing": False, "available": True}, to=sid)


@sio.on("music_action")
async def on_music_action(sid, data=None):
    """
    Control music via Socket.IO. Guild is auto-detected from the caller's voice channel;
    the caller must be co-located with the bot. Broadcasts music_update to the guild room.
    Payload: {action: "next|pause|play|seek|recommend", data: <optional>}
    """
    sess = _get_socket_session(sid)
    if not sess:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    if not _music_available:
        await sio.emit("music_error", {"message": "Music module not available"}, to=sid)
        return

    payload = data or {}
    action = str(payload.get("action") or "").lower().strip()
    action_data = payload.get("data")

    VALID_ACTIONS = {'next', 'pause', 'play', 'seek', 'recommend'}
    if action not in VALID_ACTIONS:
        await sio.emit("music_error", {"message": f"Invalid action. Valid: {', '.join(sorted(VALID_ACTIONS))}"}, to=sid)
        return

    try:
        user_id = int(sess["user_id"])
    except (ValueError, TypeError):
        await sio.emit("music_error", {"message": "Invalid session"}, to=sid)
        return

    guild, guild_id = _find_user_music_guild(user_id)
    if not guild:
        await _set_socket_music_room(sid, None)
        await sio.emit("music_error", {"message": "You are not in any voice channel with the bot"}, to=sid)
        return

    await _set_socket_music_room(sid, guild_id)

    player = guild.voice_client
    if not player:
        await sio.emit("music_error", {"message": "No active player"}, to=sid)
        return

    try:
        if action == 'pause':
            await player.set_pause(True)

        elif action == 'play':
            await player.set_pause(False)

        elif action == 'next':
            if _music_mod.radio_modes.get(guild.id):
                await sio.emit("music_error", {"message": "Cannot skip in radio mode"}, to=sid)
                return
            queue = _music_mod.get_queue(guild.id)
            next_track = queue.get()
            await player.stop()
            if next_track:
                await player.play(next_track)

        elif action == 'seek':
            try:
                seek_ms = int(action_data) * 1000
            except (TypeError, ValueError):
                await sio.emit("music_error", {"message": "seek requires integer seconds"}, to=sid)
                return
            await player.seek(seek_ms)

        elif action == 'recommend':
            if _music_mod.radio_modes.get(guild.id):
                await sio.emit("music_error", {"message": "Cannot recommend in radio mode"}, to=sid)
                return
            current = getattr(player, 'current', None)
            if not current:
                await sio.emit("music_error", {"message": "No current track"}, to=sid)
                return
            try:
                count = max(1, min(int(action_data or 5), 10))
            except (TypeError, ValueError):
                count = 5
            results = await player.get_recommendations(track=current)
            if results:
                tracks = getattr(results, 'tracks', results)
                q = _music_mod.get_queue(guild.id)
                for t in tracks[:count]:
                    q.add(t)
                if not player.is_playing:
                    nt = q.get()
                    if nt:
                        await player.play(nt)

    except Exception as e:
        log(f"Socket music_action '{action}' failed: {e}", level=logging.WARNING, module_name="Explore")
        await sio.emit("music_error", {"message": str(e)}, to=sid)
        return

    await _emit_music_update(guild.id)


# --- Discord Commands ---

class PlayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="玩", style=discord.ButtonStyle.primary, custom_id="explore_play_button")
    async def play_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.launch_activity()

bot.add_view(PlayView())

@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@bot.tree.command(name="explore", description="啟動探索空間")
async def explore_command(interaction: discord.Interaction):
    await interaction.response.launch_activity()
    embed = discord.Embed(
        title="探索空間",
        description=f"{interaction.user.display_name} 正在遊玩",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    await interaction.followup.send(embed=embed, view=PlayView())

@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(manage_guild=True)
class ExplorerCommands(commands.GroupCog, name="explore-settings"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Discord 頻道 → 遊戲內聊天橋接。"""
        if message.author.bot or not message.guild:
            return
        gid = str(message.guild.id)
        channel_id = _get_chat_bridge_channel_id(gid)
        if not channel_id or message.channel.id != channel_id:
            return
        text = str(message.content or "").strip()
        if not text:
            return
        if len(text) > CHAT_MAX_LENGTH:
            text = text[:CHAT_MAX_LENGTH]

        filtered_text, _was_filtered = filter_chat_text(text, gid)
        display_name = message.author.display_name or message.author.name
        chat_message = _make_chat_message(gid, message.author.id, display_name, filtered_text, source="discord")
        _append_chat_history(gid, chat_message)
        await sio.emit("chat_message", chat_message, room=gid)

    @app_commands.command(name="setup", description="啟用或設定探索空間")
    @app_commands.choices(enabled=[
        app_commands.Choice(name="啟用", value=1),
        app_commands.Choice(name="停用", value=0),
    ])
    async def setup(self, interaction: discord.Interaction, enabled: int):
        toggle_explore_server(interaction.guild.id, bool(enabled))
        status = "啟用" if enabled else "停用"
        await interaction.response.send_message(f"已{status}本伺服器的探索空間。")

    @app_commands.command(name="privacy", description="設定伺服器是否在探索大廳公開")
    @app_commands.choices(public=[
        app_commands.Choice(name="公開", value=1),
        app_commands.Choice(name="私人", value=0),
    ])
    async def privacy(self, interaction: discord.Interaction, public: int):
        set_explore_privacy(interaction.guild.id, bool(public))
        status = "公開" if public else "私人"
        await interaction.response.send_message(f"已將本伺服器設定為{status}（在探索大廳{'可見' if public else '不可見'}）。")

    @app_commands.command(name="require-join", description="設定是否必須先加入伺服器才能進入 Explore")
    async def require_join(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        invite_link: str | None = None,
    ):
        if interaction.guild is None:
            await interaction.response.send_message("這個指令只能在伺服器內使用。", ephemeral=True)
            return

        guild = interaction.guild
        current_config = get_explore_server(guild.id)

        if not enabled:
            _update_explore_server_config(guild.id, require_join=False)
            await interaction.response.send_message("已關閉必須先加入伺服器才能進入 Explore 的限制。")
            return

        resolved_link, error_message, created_link = await _resolve_explore_require_join_invite_link(
            guild,
            invite_link,
            fallback_invite_link=current_config.get("invite_link"),
        )
        if not resolved_link:
            await interaction.response.send_message(error_message or "無法設定加入限制。", ephemeral=True)
            return

        _update_explore_server_config(
            guild.id,
            require_join=True,
            invite_link=resolved_link,
        )
        source_text = "已自動建立永久邀請連結" if created_link else "已使用提供的邀請連結"
        await interaction.response.send_message(
            f"已啟用先加入伺服器才能進入 Explore。{source_text}：{resolved_link}"
        )

    @app_commands.command(name="chat-channel", description="設定 Explore 聊天室橋接的 Discord 文字頻道")
    async def chat_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        if interaction.guild is None:
            await interaction.response.send_message("這個指令只能在伺服器內使用。", ephemeral=True)
            return

        if channel is None:
            set_server_config(interaction.guild.id, CHAT_CHANNEL_CONFIG_KEY, None)
            await interaction.response.send_message("已關閉 Explore 聊天室與 Discord 頻道的橋接。")
            return

        bot_member = _get_explore_bot_member(interaction.guild)
        if bot_member is not None:
            perms = channel.permissions_for(bot_member)
            if not (perms.view_channel and perms.send_messages):
                await interaction.response.send_message(
                    f"我沒有在 {channel.mention} 檢視或發言的權限,請先調整頻道權限。",
                    ephemeral=True,
                )
                return

        set_server_config(interaction.guild.id, CHAT_CHANNEL_CONFIG_KEY, str(channel.id))
        await interaction.response.send_message(
            f"已將 Explore 聊天室橋接到 {channel.mention}。遊戲內訊息會轉發到這裡,頻道訊息也會出現在遊戲內。"
        )

    banned_words = app_commands.Group(name="banned-words", description="管理 Explore 聊天室的自訂禁字")

    @banned_words.command(name="add", description="新增一個自訂禁字")
    async def banned_words_add(self, interaction: discord.Interaction, word: str):
        if interaction.guild is None:
            await interaction.response.send_message("這個指令只能在伺服器內使用。", ephemeral=True)
            return
        word = word.strip()
        if not word or len(word) > 50:
            await interaction.response.send_message("禁字長度需為 1-50 字元。", ephemeral=True)
            return
        words = _get_guild_banned_words(str(interaction.guild.id))
        if word.lower() in {w.lower() for w in words}:
            await interaction.response.send_message(f"「{word}」已在禁字清單中。", ephemeral=True)
            return
        if len(words) >= 100:
            await interaction.response.send_message("自訂禁字已達上限(100 個)。", ephemeral=True)
            return
        words.append(word)
        set_server_config(interaction.guild.id, CHAT_BLOCKLIST_CONFIG_KEY, words)
        await interaction.response.send_message(f"已新增禁字「{word}」。目前共 {len(words)} 個自訂禁字。", ephemeral=True)

    @banned_words.command(name="remove", description="移除一個自訂禁字")
    async def banned_words_remove(self, interaction: discord.Interaction, word: str):
        if interaction.guild is None:
            await interaction.response.send_message("這個指令只能在伺服器內使用。", ephemeral=True)
            return
        word = word.strip()
        words = _get_guild_banned_words(str(interaction.guild.id))
        remaining = [w for w in words if w.lower() != word.lower()]
        if len(remaining) == len(words):
            await interaction.response.send_message(f"清單中找不到「{word}」。", ephemeral=True)
            return
        set_server_config(interaction.guild.id, CHAT_BLOCKLIST_CONFIG_KEY, remaining)
        await interaction.response.send_message(f"已移除禁字「{word}」。目前共 {len(remaining)} 個自訂禁字。", ephemeral=True)

    @banned_words.command(name="list", description="列出所有自訂禁字")
    async def banned_words_list(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("這個指令只能在伺服器內使用。", ephemeral=True)
            return
        words = _get_guild_banned_words(str(interaction.guild.id))
        if not words:
            await interaction.response.send_message("目前沒有自訂禁字(內建詞庫仍然生效)。", ephemeral=True)
            return
        joined = "、".join(f"`{w}`" for w in words[:100])
        await interaction.response.send_message(f"自訂禁字({len(words)} 個):{joined}", ephemeral=True)

init_db()

asyncio.run(bot.add_cog(ExplorerCommands(bot)))
