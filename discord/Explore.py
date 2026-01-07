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
from functools import wraps
import threading
import urllib.parse
from logger import log

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

activity_entry = ActivityEntry(name=app_commands.locale_str("explore space"), description="開啟探索空間")


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


def _issue_explore_auth_token(user_id: int, username: str, guild_ids: set[str]) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _auth_lock:
        auth_tokens[token] = {
            'user_id': int(user_id),
            'username': username,
            'guild_ids': set(guild_ids),
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

# sid -> {'user_id': str, 'guild_id': str|None}
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

# --- Helper Functions ---

def get_explore_server(guild_id: int):
    return get_server_config(
        guild_id,
        "explore_config",
        {
            "enabled": False,
            "is_public": False,
            "map_type": 1,
            "require_join": False,
        }
    )

def toggle_explore_server(guild_id: int, enabled: bool):
    guild_config = get_server_config(
        guild_id,
        "explore_config",
        {
            "enabled": False,
            "is_public": False,
            "map_type": 1,
            "require_join": False,
        }
    )
    guild_config['enabled'] = enabled
    set_server_config(guild_id, "explore_config", guild_config)

def set_explore_privacy(guild_id: int, is_public: bool):
    guild_config = get_server_config(
        guild_id,
        "explore_config",
        {
            "enabled": False,
            "is_public": False,
            "map_type": 1,
            "require_join": False,
        }
    )
    guild_config['is_public'] = is_public
    set_server_config(guild_id, "explore_config", guild_config)


def get_user_skin(user_id: int):
    return get_user_data(0, user_id, "explore_skin_data", "1")

def set_user_skin(user_id: int, skin_data: str):
    set_user_data(0, user_id, "explore_skin_data", skin_data)

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

    auth_token = _issue_explore_auth_token(user_id=user_id, username=username, guild_ids=guild_ids)
    return jsonify({
        'auth_token': auth_token,
        'expires_in': AUTH_TOKEN_TTL_SECONDS,
    })


@app.route('/api/explore/servers', methods=['GET'])
@_require_explore_auth
def explore_get_accessible_servers():
    """List accessible servers with icon url + name + member count + in-space count."""
    guild_ids: set[str] = set(g.explore_user.get('guild_ids') or [])

    # Get all enabled explore servers
    enabled: set[str] = set()
    for gid in get_all_server_config_key("explore_config"):
        server = get_explore_server(int(gid))
        if server and server.get('enabled'):
            enabled.add(str(gid))


    # If we have no guild list (missing scope), fall back to mutual guilds via cache.
    if not guild_ids:
        uid = int(g.explore_user['user_id'])
        for guild in bot.guilds:
            try:
                if guild.get_member(uid):
                    guild_ids.add(str(guild.id))
            except Exception:
                pass

    result = []
    for gid in sorted(guild_ids):
        if gid not in enabled:
            continue
        guild = bot.get_guild(int(gid))
        if not guild:
            continue
        with _presence_lock:
            in_space = len(space_presence.get(gid, set()))
        result.append({
            'id': gid,
            'name': guild.name,
            'icon_url': "/api/explore/icon/guild/" + gid if guild.icon else None,
            'member_count': int(getattr(guild, 'member_count', 0) or 0),
            'in_space_count': in_space,
        })

    return jsonify(result)


@app.route('/api/explore/server/<guild_id>', methods=['GET'])
@_require_explore_auth
def explore_get_server_info(guild_id: str):
    """Return server info: icon url + name + member count + in-space count."""
    gid = str(guild_id)
    server = get_explore_server(int(gid))
    if not server or not server.get('enabled'):
        return jsonify({'error': 'Server not enabled'}), 404

    # Access control: must be in the verified guild list if provided
    allowed: set[str] = set(g.explore_user.get('guild_ids') or [])
    if allowed and gid not in allowed:
        return jsonify({'error': 'Forbidden'}), 403

    guild = bot.get_guild(int(gid))
    if not guild:
        return jsonify({'error': 'Guild not found'}), 404

    with _presence_lock:
        in_space = len(space_presence.get(gid, set()))

    return jsonify({
        'id': gid,
        'name': guild.name,
        'icon_url': "/api/explore/icon/guild/" + gid if guild.icon else None,
        'member_count': int(getattr(guild, 'member_count', 0) or 0),
        'in_space_count': in_space,
    })


@app.route('/api/explore/space/<guild_id>', methods=['GET'])
@_require_explore_auth
def explore_get_space_data(guild_id: str):
    """Return server space tile data as list[{x,y,z,tile_id}]."""
    gid = str(guild_id)
    if gid != 'world':
        server = get_explore_server(int(gid))
        if not server or not server.get('enabled'):
            return jsonify({'error': 'Server not enabled'}), 404

    # Access control: must be in the verified guild list if provided
    allowed: set[str] = set(g.explore_user.get('guild_ids') or [])
    if allowed and gid not in allowed:
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
@_require_explore_auth
def explore_guild_icon(guild_id):
    # Proxy guild icon
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return jsonify({"error": "Guild not found"}), 404
    if not guild.icon:
        return jsonify({"error": "Guild has no icon"}), 404
    return requests.get(guild.icon.url).content, 200, {'Content-Type': 'image/png'}

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
async def on_join(sid, data):
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

        allowed = {str(x) for x in (sess.get("guild_ids") or [])}
        if str(guild_id) not in allowed:
            await sio.emit("error", {"message": "Membership required"}, to=sid)
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
async def on_leave(sid, data):
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
async def on_move(sid, data):
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
async def on_edit_map(sid, data):
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
async def on_skin_change(sid, data):
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

init_db()

asyncio.run(bot.add_cog(ExplorerCommands(bot)))