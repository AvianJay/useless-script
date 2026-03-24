import os
import json
import math
import threading
import time
import sqlite3
from functools import wraps
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
from flask import (
    Flask,
    Response,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from dotenv import load_dotenv
load_dotenv()

from auth_utils import generate_api_key, validate_api_key_structure
from db_init import init_db

try:
    from flask_socketio import SocketIO
except ImportError:
    SocketIO = None

try:
    import socketio as client_socketio
except ImportError:
    client_socketio = None

UPSTREAM_URL = os.getenv("UPSTREAM_URL", "http://127.0.0.1:10281")
DB_PATH = os.getenv("DB_PATH", "database.db")
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "5000"))

COST_PER_API = float(os.getenv("COST_PER_API", "1.0"))
COST_PER_SCREENSHOT = float(os.getenv("COST_PER_SCREENSHOT", "5.0"))
COST_PER_SEC_WS = float(os.getenv("COST_PER_SEC_WS", "0.01"))
WARNING_S_WAVE_SPEED_KMPS = float(os.getenv("WARNING_S_WAVE_SPEED_KMPS", "4.0"))
TOWN_ID_PATH = Path(__file__).resolve().parent.parent / "town_id.json"
TAIWAN_TZ = timezone(timedelta(hours=8))

UPSTREAM_INFO_ENDPOINTS = {
    "report": "/getReportInfo",
    "warning": "/getWarningInfo",
}

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_OAUTH_SCOPE = os.getenv("DISCORD_OAUTH_SCOPE", "identify")
DISCORD_API_BASE = "https://discord.com/api/v10"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-in-production")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

if SocketIO:
    socketio = SocketIO(
        app,
        async_mode="threading",
        cors_allowed_origins="*",
        manage_session=False,
    )
else:
    class _SocketIOServerShim:
        @staticmethod
        def disconnect(_sid):
            return None

    class _SocketIOShim:
        def __init__(self, flask_app):
            self.app = flask_app
            self.server = _SocketIOServerShim()

        def on(self, _event_name):
            def decorator(func):
                return func

            return decorator

        def emit(self, *_args, **_kwargs):
            return None

        def run(self, flask_app, host="127.0.0.1", port=5000):
            flask_app.run(host=host, port=port)

    socketio = _SocketIOShim(app)

CACHE = {"report": None, "warning": None}
CACHE_SCREENSHOT = {"report": None, "warning": None}


def load_town_locations():
    try:
        with TOWN_ID_PATH.open("r", encoding="utf-8") as fp:
            raw = json.load(fp)
    except Exception as exc:
        print(f"Failed to load town locations: {exc}")
        return {}

    locations = {}
    for town_id, info in raw.items():
        if isinstance(info, str):
            locations[town_id] = {"name": info}
            continue

        locations[town_id] = {
            "name": info.get("name", town_id),
            "latitude": info.get("latitude"),
            "longitude": info.get("longitude"),
        }
    return locations


TOWN_LOCATIONS = load_town_locations()

connected_clients = {}
connected_clients_lock = threading.Lock()
status_lock = threading.Lock()

system_status = {
    "app_started_at": datetime.now(timezone.utc),
    "upstream": {
        "client_installed": client_socketio is not None,
        "connected": False,
        "last_connect_attempt_at": None,
        "last_connected_at": None,
        "last_disconnected_at": None,
        "last_error": None,
    },
    "events": {
        "report": {"last_event_at": None, "last_cache_update_at": None, "last_screenshot_update_at": None},
        "warning": {"last_event_at": None, "last_cache_update_at": None, "last_screenshot_update_at": None},
    },
}

upstream_sio = client_socketio.Client() if client_socketio else None


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


@app.teardown_appcontext
def teardown_db(_error):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def fetch_user_by_discord_id(discord_id: str):
    row = get_db().execute(
        "SELECT discord_id, api_key, points, updated_at FROM users WHERE discord_id = ?",
        (discord_id,),
    ).fetchone()
    return dict(row) if row else None


def fetch_user_by_key(api_key: str):
    discord_id = validate_api_key_structure(api_key)
    if not discord_id:
        return None

    row = get_db().execute(
        "SELECT discord_id, api_key, points, updated_at FROM users WHERE api_key = ?",
        (api_key,),
    ).fetchone()
    if not row or row["discord_id"] != discord_id:
        return None
    return dict(row)


def create_or_get_user(discord_id: str):
    user = fetch_user_by_discord_id(discord_id)
    if user:
        return user

    api_key = generate_api_key(discord_id)
    db = get_db()
    db.execute(
        """
        INSERT INTO users (discord_id, api_key, points, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (discord_id, api_key, 0.0),
    )
    db.commit()
    return fetch_user_by_discord_id(discord_id)


def reset_user_api_key(discord_id: str):
    new_api_key = generate_api_key(discord_id)
    db = get_db()
    db.execute(
        "UPDATE users SET api_key = ?, updated_at = CURRENT_TIMESTAMP WHERE discord_id = ?",
        (new_api_key, discord_id),
    )
    db.commit()
    return fetch_user_by_discord_id(discord_id)


def deduct_points_by_key(api_key: str, amount: float) -> bool:
    db = get_db()
    cursor = db.execute(
        """
        UPDATE users
        SET points = points - ?, updated_at = CURRENT_TIMESTAMP
        WHERE api_key = ? AND points >= ?
        """,
        (amount, api_key, amount),
    )
    db.commit()
    return cursor.rowcount > 0


def require_api_key(cost: float = COST_PER_API):
    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            api_key = request.headers.get("X-API-Key")
            if not api_key:
                return jsonify({"error": "Missing API Key"}), 401

            user = fetch_user_by_key(api_key)
            if not user:
                return jsonify({"error": "Invalid API Key"}), 401
            if user["points"] < cost:
                return jsonify({"error": "Insufficient Points"}), 402
            if not deduct_points_by_key(api_key, cost):
                return jsonify({"error": "Transaction Failed"}), 500

            g.api_user = user
            g.api_key = api_key
            return func(*args, **kwargs)

        return wrapped

    return decorator


def require_login(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        discord_id = session.get("discord_id")
        if not discord_id:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))

        user = create_or_get_user(discord_id)
        g.session_user = user
        return func(*args, **kwargs)

    return wrapped


def discord_oauth_enabled() -> bool:
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET)


def build_discord_redirect_uri() -> str:
    return os.getenv("DISCORD_REDIRECT_URI") or url_for("discord_callback", _external=True)


def utc_now():
    return datetime.now(timezone.utc)


def format_dt(dt):
    return dt.isoformat() if dt else None


def mark_upstream_status(**updates):
    with status_lock:
        system_status["upstream"].update(updates)


def mark_event_status(type_: str, field: str, value):
    with status_lock:
        system_status["events"][type_][field] = value


def parse_warning_origin(time_text: str):
    if not time_text:
        return None

    try:
        return datetime.strptime(time_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIWAN_TZ)
    except ValueError:
        return None


def haversine_km(lat1, lon1, lat2, lon2):
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


INTENSITY_LABELS = [
    "0\u7d1a",
    "1\u7d1a",
    "2\u7d1a",
    "3\u7d1a",
    "4\u7d1a",
    "5\u5f31",
    "5\u5f37",
    "6\u5f31",
    "6\u5f37",
    "7\u7d1a",
]
INTENSITY_TO_RANK = {label: rank for rank, label in enumerate(INTENSITY_LABELS)}
INTENSITY_ALIASES = {
    "0": "0\u7d1a",
    "1": "1\u7d1a",
    "2": "2\u7d1a",
    "3": "3\u7d1a",
    "4": "4\u7d1a",
    "5-": "5\u5f31",
    "5+": "5\u5f37",
    "6-": "6\u5f31",
    "6+": "6\u5f37",
    "7": "7\u7d1a",
}


def normalize_intensity_label(label):
    if label is None:
        return None

    text = str(label).strip()
    if not text:
        return None
    if text in INTENSITY_TO_RANK:
        return text
    if text in INTENSITY_ALIASES:
        return INTENSITY_ALIASES[text]

    compact = text.replace(" ", "")
    if compact in INTENSITY_TO_RANK:
        return compact
    if compact in INTENSITY_ALIASES:
        return INTENSITY_ALIASES[compact]
    if compact.endswith("\u7d1a") and compact[:-1].isdigit():
        return f"{int(compact[:-1])}\u7d1a"
    if compact.isdigit():
        return f"{int(compact)}\u7d1a"
    return None


def intensity_rank_to_label(rank: int) -> str:
    rank = max(0, min(rank, len(INTENSITY_LABELS) - 1))
    return INTENSITY_LABELS[rank]


def estimate_intensity_label(magnitude: float, depth_km: float, hypocenter_distance_km: float) -> str:
    # Preview estimate only. The old version was too conservative for towns near
    # the epicenter, so this uses a stronger source term before we anchor to the
    # upstream max intensity.
    source_term = 0.58 * magnitude + 0.0038 * depth_km - 0.05
    attenuation = math.log10(
        hypocenter_distance_km + 0.0028 * (10 ** (0.5 * magnitude))
    ) + 0.002 * hypocenter_distance_km
    pga = 10 ** (source_term - attenuation)

    if pga < 0.8:
        return "0\u7d1a"
    if pga < 2.5:
        return "1\u7d1a"
    if pga < 8.0:
        return "2\u7d1a"
    if pga < 25.0:
        return "3\u7d1a"
    if pga < 80.0:
        return "4\u7d1a"
    if pga < 140.0:
        return "5\u5f31"
    if pga < 250.0:
        return "5\u5f37"
    if pga < 440.0:
        return "6\u5f31"
    if pga < 800.0:
        return "6\u5f37"
    return "7\u7d1a"


def calibrate_estimated_intensities(estimated_intensities: dict, warning_data: dict) -> dict:
    official_max_label = normalize_intensity_label(warning_data.get("maxIntensity"))
    if not official_max_label or not estimated_intensities:
        return estimated_intensities

    official_max_rank = INTENSITY_TO_RANK[official_max_label]
    current_max_rank = max(
        INTENSITY_TO_RANK.get(normalize_intensity_label(label) or "0\u7d1a", 0)
        for label in estimated_intensities.values()
    )
    delta = official_max_rank - current_max_rank
    if delta <= 0:
        return estimated_intensities

    adjusted = {}
    for town_id, label in estimated_intensities.items():
        normalized = normalize_intensity_label(label) or "0\u7d1a"
        adjusted_rank = INTENSITY_TO_RANK[normalized] + delta
        adjusted[town_id] = intensity_rank_to_label(adjusted_rank)
    return adjusted


def build_warning_arrival_times(warning_data: dict):
    if not warning_data:
        return {}, {}

    try:
        epicenter_lat = float(warning_data["location"]["latitude"])
        epicenter_lon = float(warning_data["location"]["longitude"])
        depth_km = float(warning_data["depth"])
        magnitude = float(warning_data["magnitude"])
    except (KeyError, TypeError, ValueError):
        return {}, {}

    origin = parse_warning_origin(warning_data.get("time"))
    now = datetime.now(TAIWAN_TZ)
    elapsed_seconds = max(0.0, (now - origin).total_seconds()) if origin else 0.0

    arrival_times = {}
    raw_estimated_intensities = {}
    for town_id, info in TOWN_LOCATIONS.items():
        latitude = info.get("latitude")
        longitude = info.get("longitude")
        if latitude is None or longitude is None:
            continue

        horizontal_distance_km = haversine_km(epicenter_lat, epicenter_lon, latitude, longitude)
        hypocenter_distance_km = math.sqrt(horizontal_distance_km ** 2 + depth_km ** 2)
        travel_seconds = hypocenter_distance_km / WARNING_S_WAVE_SPEED_KMPS
        remaining_seconds = max(0, math.ceil(travel_seconds - elapsed_seconds))
        raw_estimated_intensities[town_id] = estimate_intensity_label(
            magnitude,
            depth_km,
            hypocenter_distance_km,
        )

        if remaining_seconds > 0:
            arrival_times[town_id] = remaining_seconds

    calibrated_estimated_intensities = calibrate_estimated_intensities(
        raw_estimated_intensities,
        warning_data,
    )
    estimated_intensities = {
        town_id: level
        for town_id, level in calibrated_estimated_intensities.items()
        if level != "0\u7d1a"
    }
    return arrival_times, estimated_intensities


def enrich_warning_payload(payload: dict):
    if not payload or not payload.get("ok", False):
        return payload

    enriched = dict(payload)
    arrival_times, estimated_intensities = build_warning_arrival_times(enriched)
    enriched["arrival_times"] = arrival_times
    enriched["estimated_intensities"] = estimated_intensities
    enriched["arrival_count"] = len(arrival_times)
    enriched["arrival_generated_at"] = datetime.now(TAIWAN_TZ).isoformat()
    return enriched


def update_cache(type_: str):
    try:
        endpoint = UPSTREAM_INFO_ENDPOINTS.get(type_)
        if not endpoint:
            print(f"未知快取類型 ({type_})")
            return

        response = requests.get(f"{UPSTREAM_URL}{endpoint}", timeout=5)
        if response.ok:
            payload = response.json()
            if not payload.get("ok", False):
                print(f"更新快取失敗 ({type_}): 上游回傳 ok=false")
                return

            if type_ == "warning":
                payload = enrich_warning_payload(payload)

            CACHE[type_] = payload
            mark_event_status(type_, "last_cache_update_at", utc_now())
            print(f"[{type_}] 資料快取已更新")
        else:
            print(f"更新快取失敗 ({type_}): HTTP {response.status_code}")
    except Exception as exc:
        print(f"更新快取失敗 ({type_}): {exc}")


def update_screenshot_cache(type_: str):
    try:
        if type_ == "report":
            requests.get(f"{UPSTREAM_URL}/gotoReport", timeout=2)
        elif type_ == "warning":
            requests.get(f"{UPSTREAM_URL}/gotoWarning", timeout=2)

        time.sleep(0.2)
        response = requests.get(f"{UPSTREAM_URL}/screenshot", timeout=5)
        if response.ok:
            CACHE_SCREENSHOT[type_] = response.content
            mark_event_status(type_, "last_screenshot_update_at", utc_now())
            print(f"[{type_}] 截圖快取已更新")
    except Exception as exc:
        print(f"更新截圖快取失敗 ({type_}): {exc}")


if upstream_sio:
    @upstream_sio.event
    def connect():
        mark_upstream_status(
            connected=True,
            last_connected_at=utc_now(),
            last_error=None,
        )
        print("上游 Socket.IO 已連線")


    @upstream_sio.event
    def disconnect():
        mark_upstream_status(
            connected=False,
            last_disconnected_at=utc_now(),
        )
        print("上游 Socket.IO 已中斷")


    @upstream_sio.on("reportTimeChanged")
    def on_report_changed(data):
        print("[Upstream] reportTimeChanged 事件觸發")
        mark_event_status("report", "last_event_at", utc_now())
        update_cache("report")
        update_screenshot_cache("report")
        payload = dict(data or {})
        payload["data"] = CACHE["report"]
        socketio.emit("proxy_report_update", payload)


    @upstream_sio.on("warningTimeChanged")
    def on_warning_changed(data):
        print("[Upstream] warningTimeChanged 事件觸發")
        mark_event_status("warning", "last_event_at", utc_now())
        update_cache("warning")
        update_screenshot_cache("warning")
        payload = dict(data or {})
        payload["data"] = CACHE["warning"]
        if CACHE["warning"]:
            payload["arrival_times"] = CACHE["warning"].get("arrival_times", {})
            payload["estimated_intensities"] = CACHE["warning"].get("estimated_intensities", {})
            payload["arrival_count"] = CACHE["warning"].get("arrival_count", 0)
            payload["arrival_generated_at"] = CACHE["warning"].get("arrival_generated_at")
        socketio.emit("proxy_warning_update", payload)


    @upstream_sio.on("warningUpdated")
    def on_warning_updated(data):
        print("[Upstream] warningUpdated 事件觸發")
        mark_event_status("warning", "last_event_at", utc_now())
        update_cache("warning")
        update_screenshot_cache("warning")
        payload = dict(data or {})
        payload["data"] = CACHE["warning"]
        if CACHE["warning"]:
            payload["arrival_times"] = CACHE["warning"].get("arrival_times", {})
            payload["estimated_intensities"] = CACHE["warning"].get("estimated_intensities", {})
            payload["arrival_count"] = CACHE["warning"].get("arrival_count", 0)
            payload["arrival_generated_at"] = CACHE["warning"].get("arrival_generated_at")
        socketio.emit("proxy_warning_updated", payload)


def start_upstream_sync():
    if not upstream_sio:
        mark_upstream_status(
            client_installed=False,
            connected=False,
            last_error="python-socketio client not installed",
        )
        print("未安裝 python-socketio client，略過上游 Socket.IO 同步。")
        return

    print("初始化上游快取...")
    for cache_type in ("report", "warning"):
        update_cache(cache_type)
        update_screenshot_cache(cache_type)
    print("開始連接上游 Socket.IO...")
    try:
        mark_upstream_status(last_connect_attempt_at=utc_now(), last_error=None)
        upstream_sio.connect(UPSTREAM_URL)
        upstream_sio.wait()
    except Exception as exc:
        mark_upstream_status(connected=False, last_error=str(exc), last_disconnected_at=utc_now())
        print(f"無法連接上游 Socket.IO: {exc}")


def get_discord_access_token(code: str) -> dict:
    response = requests.post(
        f"{DISCORD_API_BASE}/oauth2/token",
        data={
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": build_discord_redirect_uri(),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_discord_user(access_token: str) -> dict:
    response = requests.get(
        f"{DISCORD_API_BASE}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def mask_api_key(api_key: str) -> str:
    if not api_key or len(api_key) < 12:
        return api_key
    return f"{api_key[:8]}...{api_key[-6:]}"


@app.route("/")
def index():
    if session.get("discord_id"):
        return redirect(url_for("dashboard"))
    return render_template("landing.html", oauth_enabled=discord_oauth_enabled())


@app.route("/dashboard")
@require_login
def dashboard():
    profile = session.get("discord_profile", {})
    return render_template(
        "dashboard.html",
        user=g.session_user,
        profile=profile,
        masked_api_key=mask_api_key(g.session_user["api_key"]),
        costs={
            "api": COST_PER_API,
            "screenshot": COST_PER_SCREENSHOT,
            "ws_per_sec": COST_PER_SEC_WS,
        },
    )


@app.route("/login")
def login():
    if not discord_oauth_enabled():
        return (
            "Discord OAuth2 尚未設定。請先設定 DISCORD_CLIENT_ID 與 DISCORD_CLIENT_SECRET。",
            503,
        )

    params = {
        "client_id": DISCORD_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": build_discord_redirect_uri(),
        "scope": DISCORD_OAUTH_SCOPE,
    }
    query = "&".join(f"{key}={requests.utils.quote(str(value), safe='')}" for key, value in params.items())
    return redirect(f"https://discord.com/oauth2/authorize?{query}")


@app.route("/oauth/discord/callback")
def discord_callback():
    if "error" in request.args:
        return f"Discord OAuth2 驗證失敗：{request.args.get('error')}", 400

    code = request.args.get("code")
    if not code:
        return "缺少 OAuth2 code。", 400

    if not discord_oauth_enabled():
        return "Discord OAuth2 尚未設定。", 503

    try:
        token_data = get_discord_access_token(code)
        discord_user = get_discord_user(token_data["access_token"])
    except requests.RequestException as exc:
        return f"Discord OAuth2 交換 token 失敗：{exc}", 502

    discord_id = str(discord_user["id"])
    user = create_or_get_user(discord_id)
    session["discord_id"] = discord_id
    session["discord_profile"] = {
        "username": discord_user.get("username"),
        "global_name": discord_user.get("global_name"),
        "avatar_url": (
            f"https://cdn.discordapp.com/avatars/{discord_id}/{discord_user['avatar']}.png?size=256"
            if discord_user.get("avatar")
            else None
        ),
    }
    session["api_key_preview"] = mask_api_key(user["api_key"])
    return redirect(url_for("dashboard"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/api/me", methods=["GET"])
@require_login
def api_me():
    profile = session.get("discord_profile", {})
    return jsonify(
        {
            "discord_id": g.session_user["discord_id"],
            "points": g.session_user["points"],
            "api_key": g.session_user["api_key"],
            "api_key_masked": mask_api_key(g.session_user["api_key"]),
            "updated_at": g.session_user["updated_at"],
            "profile": profile,
        }
    )


@app.route("/api/me/reset-key", methods=["POST"])
@require_login
def api_reset_key():
    user = reset_user_api_key(g.session_user["discord_id"])
    return jsonify(
        {
            "message": "API Key 已重設",
            "api_key": user["api_key"],
            "api_key_masked": mask_api_key(user["api_key"]),
            "updated_at": user["updated_at"],
        }
    )


@app.route("/api/data/<type_>", methods=["GET"])
@require_api_key(COST_PER_API)
def get_data(type_):
    if type_ not in {"report", "warning"}:
        return jsonify({"error": "Invalid type"}), 400

    data = CACHE.get(type_)
    if data is None:
        return jsonify({"error": "Cache not ready"}), 503
    return jsonify(data)


@app.route("/api/screenshot/<type_>", methods=["GET"])
@require_api_key(COST_PER_SCREENSHOT)
def get_screenshot(type_):
    if type_ not in {"report", "warning"}:
        return jsonify({"error": "Invalid type"}), 400

    image_data = CACHE_SCREENSHOT.get(type_)
    if image_data is None:
        return jsonify({"error": "Cache not ready"}), 503
    return Response(image_data, mimetype="image/png")


@app.route("/api/town-map", methods=["GET"])
def get_town_map():
    return jsonify(TOWN_LOCATIONS)


@app.route("/api/docs", methods=["GET"])
def api_docs():
    return render_template(
        "docs.html",
        openapi_url=url_for("openapi_spec"),
        costs={
            "api": COST_PER_API,
            "screenshot": COST_PER_SCREENSHOT,
            "ws_per_sec": COST_PER_SEC_WS,
        },
    )


@app.route("/openapi.json", methods=["GET"])
def openapi_spec():
    server_url = request.host_url.rstrip("/")
    return jsonify(
        {
            "openapi": "3.1.0",
            "info": {
                "title": "OXWU API",
                "version": "1.0.0",
                "description": "提供 report / warning 資料、截圖與 WebSocket 事件代理。",
            },
            "servers": [{"url": server_url}],
            "components": {
                "securitySchemes": {
                    "ApiKeyAuth": {
                        "type": "apiKey",
                        "in": "header",
                        "name": "X-API-Key",
                    },
                    "SessionAuth": {"type": "apiKey", "in": "cookie", "name": "session"},
                }
            },
            "paths": {
                "/api/data/{type}": {
                    "get": {
                        "summary": "取得快取資料",
                        "security": [{"ApiKeyAuth": []}],
                        "parameters": [
                            {
                                "name": "type",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string", "enum": ["report", "warning"]},
                            }
                        ],
                        "responses": {
                            "200": {"description": "成功"},
                            "401": {"description": "API Key 無效"},
                            "402": {"description": "點數不足"},
                            "503": {"description": "快取尚未準備完成"},
                        },
                    }
                },
                "/api/screenshot/{type}": {
                    "get": {
                        "summary": "取得截圖 PNG",
                        "security": [{"ApiKeyAuth": []}],
                        "parameters": [
                            {
                                "name": "type",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string", "enum": ["report", "warning"]},
                            }
                        ],
                        "responses": {
                            "200": {"description": "成功"},
                            "401": {"description": "API Key 無效"},
                            "402": {"description": "點數不足"},
                            "503": {"description": "快取尚未準備完成"},
                        },
                    }
                },
                "/api/town-map": {
                    "get": {
                        "summary": "?? town_id 對照表與座標",
                        "responses": {"200": {"description": "??"}},
                    }
                },
                "/api/me": {
                    "get": {
                        "summary": "取得目前登入帳號資訊",
                        "security": [{"SessionAuth": []}],
                        "responses": {"200": {"description": "成功"}, "401": {"description": "未登入"}},
                    }
                },
                "/api/me/reset-key": {
                    "post": {
                        "summary": "重設目前登入帳號的 API Key",
                        "security": [{"SessionAuth": []}],
                        "responses": {"200": {"description": "成功"}, "401": {"description": "未登入"}},
                    }
                },
                "/api/docs": {
                    "get": {
                        "summary": "取得 API 文件頁（HTML）",
                        "responses": {"200": {"description": "成功"}},
                    }
                },
                "/openapi.json": {
                    "get": {
                        "summary": "取得 OpenAPI JSON",
                        "responses": {"200": {"description": "成功"}},
                    }
                },
            },
            "x-websocket": {
                "endpoint": f"{server_url.replace('http', 'ws', 1)}/socket.io/",
                "auth": "連線時帶入 X-API-Key header 或 api_key query string。",
                "billing": f"每秒 {COST_PER_SEC_WS} 點，每 5 秒結算一次。",
                "events": ["proxy_report_update", "proxy_warning_update", "proxy_warning_updated"],
                "warning_arrival_times": "{ town_id: eta_seconds }",
            },
            "x-upstream": {
                "base_url": UPSTREAM_URL,
                "info_endpoints": {
                    "report": f"{UPSTREAM_URL}{UPSTREAM_INFO_ENDPOINTS['report']}",
                    "warning": f"{UPSTREAM_URL}{UPSTREAM_INFO_ENDPOINTS['warning']}",
                },
                "navigation_endpoints": {
                    "report": f"{UPSTREAM_URL}/gotoReport",
                    "warning": f"{UPSTREAM_URL}/gotoWarning",
                },
                "screenshot_endpoint": f"{UPSTREAM_URL}/screenshot",
                "upstream_events": ["reportTimeChanged", "warningTimeChanged", "warningUpdated"],
            },
        }
    )


@socketio.on("connect")
def handle_connect():
    api_key = request.headers.get("X-API-Key") or request.args.get("api_key")

    with app.app_context():
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        try:
            discord_id = validate_api_key_structure(api_key)
            if not discord_id:
                return False

            row = db.execute(
                "SELECT discord_id, api_key, points FROM users WHERE api_key = ?",
                (api_key,),
            ).fetchone()
            if not row or row["discord_id"] != discord_id or row["points"] <= 0:
                return False
        finally:
            db.close()

    with connected_clients_lock:
        connected_clients[request.sid] = {
            "api_key": api_key,
            "discord_id": row["discord_id"],
        }
    print(f"Client connected: {request.sid}")


@socketio.on("disconnect")
def handle_disconnect():
    with connected_clients_lock:
        connected_clients.pop(request.sid, None)
    print(f"Client disconnected: {request.sid}")


def ws_billing_task():
    while True:
        time.sleep(5)
        with connected_clients_lock:
            items = list(connected_clients.items())
        if not items:
            continue

        db = sqlite3.connect(DB_PATH)
        try:
            cost = COST_PER_SEC_WS * 5
            to_disconnect = []
            for sid, info in items:
                api_key = info["api_key"]
                cursor = db.execute(
                    """
                    UPDATE users
                    SET points = points - ?, updated_at = CURRENT_TIMESTAMP
                    WHERE api_key = ? AND points >= ?
                    """,
                    (cost, api_key, cost),
                )
                if cursor.rowcount == 0:
                    to_disconnect.append(sid)
            db.commit()
        finally:
            db.close()

        for sid in to_disconnect:
            socketio.server.disconnect(sid)


def get_api_info() -> dict:
    with connected_clients_lock:
        count = len(connected_clients)
    with status_lock:
        upstream = dict(system_status["upstream"])
        events = {
            name: dict(values)
            for name, values in system_status["events"].items()
        }
        app_started_at = system_status["app_started_at"]

    return {
        "connected_clients": count,
        "app_started_at": format_dt(app_started_at),
        "upstream_url": UPSTREAM_URL,
        "upstream": {
            "client_installed": upstream["client_installed"],
            "connected": upstream["connected"],
            "last_connect_attempt_at": format_dt(upstream["last_connect_attempt_at"]),
            "last_connected_at": format_dt(upstream["last_connected_at"]),
            "last_disconnected_at": format_dt(upstream["last_disconnected_at"]),
            "last_error": upstream["last_error"],
        },
        "events": {
            name: {
                "last_event_at": format_dt(values["last_event_at"]),
                "last_cache_update_at": format_dt(values["last_cache_update_at"]),
                "last_screenshot_update_at": format_dt(values["last_screenshot_update_at"]),
                "cache_ready": CACHE.get(name) is not None,
                "screenshot_ready": CACHE_SCREENSHOT.get(name) is not None,
            }
            for name, values in events.items()
        },
    }


if __name__ == "__main__":
    init_db(DB_PATH)
    threading.Thread(target=start_upstream_sync, daemon=True).start()
    threading.Thread(target=ws_billing_task, daemon=True).start()
    try:
        from bot import run_bot
    except ImportError:
        run_bot = None
        print("未安裝 discord.py，略過 Discord Bot 啟動。")

    if run_bot:
        threading.Thread(target=run_bot, args=(get_api_info,), daemon=True).start()
    socketio.run(app, host=APP_HOST, port=APP_PORT)
