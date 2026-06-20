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


def _build_explore_server_payload(guild: discord.Guild, server: dict, viewer_guild_ids: set[str]) -> dict:
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


def get_user_skin(user_id: int):
    return get_user_data(0, user_id, "explore_skin_data", "1")

def set_user_skin(user_id: int, skin_data: str):
    set_user_data(0, user_id, "explore_skin_data", skin_data)


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


def get_available_skins() -> list[dict]:
    return [{'id': '1', 'name': 'Skin 1', 'icon_url': None}]  # TODO: implement skin storage


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
        result.append(_build_explore_server_payload(guild, server, guild_ids))

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

    return jsonify(_build_explore_server_payload(guild, server, guild_ids))


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
    return jsonify(get_available_skins())


@app.route('/api/explore/me/skin', methods=['POST'])
@_require_explore_auth
def explore_set_my_skin():
    data = request.get_json(silent=True) or {}
    skin_id = data.get('skin_id')
    if skin_id is None:
        return jsonify({'error': 'Missing skin_id'}), 400

    available = {s['id'] for s in get_available_skins()}
    if str(skin_id) not in available:
        return jsonify({'error': 'Invalid skin_id'}), 400

    user_id = int(g.explore_user['user_id'])
    set_user_skin(user_id, str(skin_id))
    return jsonify({'success': True, 'skin_id': str(skin_id)})

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
        skin_id = get_user_data(0, int(uid), "explore_skin_data", "1")
        
    set_user_data(0, int(uid), "explore_skin_data", skin_id)
    return jsonify({"success": True})

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
        with _presence_lock:
            s = space_presence.get(gid)
            if s and uid in s:
                s.remove(uid)
                if not s:
                    space_presence.pop(gid, None)

            players = space_players.get(gid)
            if players and uid in players:
                del players[uid]
                if not players:
                    space_players.pop(gid, None)

        await sio.emit("user_left", {"guild_id": str(gid), "user_id": uid}, room=str(gid), skip_sid=sid)
        log(f"User {sess.get('username','Unknown')} ({uid}) disconnected (sid={sid})", module_name="Explore")


@sio.on("join")
async def on_join(sid, data=None):
    sess = _get_socket_session(sid)
    if not sess:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    guild_id = str((data or {}).get("guild_id") or "world")

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

    with _session_lock:
        if sid in socket_sessions:
            socket_sessions[sid]["guild_id"] = guild_id

    await sio.enter_room(sid, guild_id)

    with _presence_lock:
        space_presence.setdefault(guild_id, set()).add(uid)
        players = space_players.setdefault(guild_id, {})
        players[uid] = {
            "user_id": uid,
            "name": username,
            "skin_id": skin_id,
            "x": players.get(uid, {}).get("x", 0),
            "y": players.get(uid, {}).get("y", 0),
            "direction": players.get(uid, {}).get("direction", 2),
        }
        snapshot_players = list(players.values())

    await sio.emit("joined", {"guild_id": guild_id, "user_id": uid, "name": username, "skin_id": skin_id}, to=sid)
    await sio.emit("room_state", {"guild_id": guild_id, "players": snapshot_players}, to=sid)
    await sio.emit(
        "user_joined",
        {"guild_id": guild_id, "user_id": uid, "name": username, "skin_id": skin_id},
        room=guild_id,
        skip_sid=sid,
    )


@sio.on("leave")
async def on_leave(sid, data=None):
    sess = _get_socket_session(sid)
    if not sess:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    guild_id = str((data or {}).get("guild_id") or sess.get("guild_id") or "world")
    uid = sess.get("user_id")

    await sio.leave_room(sid, guild_id)

    with _presence_lock:
        s = space_presence.get(guild_id)
        if s and uid in s:
            s.remove(uid)
            if not s:
                space_presence.pop(guild_id, None)

        players = space_players.get(guild_id)
        if players and uid in players:
            del players[uid]
            if not players:
                space_players.pop(guild_id, None)

    await sio.emit("left", {"guild_id": guild_id, "user_id": uid}, to=sid)
    await sio.emit("user_left", {"guild_id": guild_id, "user_id": uid}, room=guild_id, skip_sid=sid)


@sio.on("move")
async def on_move(sid, data=None):
    sess = _get_socket_session(sid)
    if not sess:
        await sio.emit("error", {"message": "Unauthorized"}, to=sid)
        return

    payload = data or {}
    guild_id = str(payload.get("guild_id") or sess.get("guild_id") or "world")
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
        if idir is not None:
            existing["direction"] = idir
        if moveSpeed is not None:
            existing["moveSpeed"] = moveSpeed
        if moveFrequency is not None:
            existing["moveFrequency"] = moveFrequency
        players[sess["user_id"]] = existing

    out = {"guild_id": guild_id, "user_id": sess["user_id"], "x": ix, "y": iy, "moveSpeed": moveSpeed, "moveFrequency": moveFrequency}
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
    skin_id = payload.get("skin_id")
    if skin_id is None:
        return

    uid = int(sess["user_id"])
    set_user_skin(uid, str(skin_id))
    guild_id = str(payload.get("guild_id") or sess.get("guild_id") or "world")

    with _presence_lock:
        players = space_players.setdefault(guild_id, {})
        existing = players.get(str(uid)) or {
            "user_id": str(uid),
            "name": sess.get("username", "Unknown"),
            "x": 0,
            "y": 0,
        }
        existing["skin_id"] = str(skin_id)
        players[str(uid)] = existing

    await sio.emit(
        "skin_changed",
        {"guild_id": guild_id, "user_id": str(uid), "skin_id": str(skin_id)},
        room=guild_id,
        skip_sid=sid,
    )


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

@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@bot.tree.command(name="explore", description="啟動探索空間")
async def explore_command(interaction: discord.Interaction):
    await interaction.response.launch_activity()

@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(manage_guild=True)
class ExplorerCommands(commands.GroupCog, name="explore-settings"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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

init_db()

asyncio.run(bot.add_cog(ExplorerCommands(bot)))
