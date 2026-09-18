from flask import (
    Flask, abort, g, redirect, render_template, request, send_file,
    send_from_directory, session,
)
import os
import asyncio
import logging
import secrets
import time
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit
from hypercorn.config import Config
from hypercorn.asyncio import serve
from globalenv import bot, modules, config, on_ready_tasks, get_global_config, on_close_tasks
from logger import log
from PIL import Image, ImageDraw
import aiohttp
import requests
from discord.ext import commands
from doc_markdown import load_docs_site
from web_preview import build_link_preview

# Shutdown event for graceful shutdown
_shutdown_event: asyncio.Event = None

fully_ready = False  # To track if the bot is fully ready (after on_ready tasks)

if "UtilCommands" in modules:
    import UtilCommands
else:
    UtilCommands = None

app = Flask(__name__, template_folder='templates', static_folder='static')


def _resolve_flask_secret_key() -> str:
    """取得 Flask session 的簽章金鑰，必要時自動產生並寫回設定檔。

    絕對不能退回硬編碼常數：那串常數就在公開的原始碼裡，任何人都能拿它簽出
    合法的 session cookie，偽造 GuildPanel 寫入的 session["panel_user"]，
    直接以任意伺服器管理員的身分登入控制面板。
    也不再沿用 Discord OAuth 的 client_secret — 一把鑰匙只該有一個用途，
    而且面板金鑰輪替時不應該連帶失效 OAuth。
    """
    secret = os.environ.get("FLASK_SECRET_KEY") or config("flask_secret_key", "")
    if secret:
        return secret

    secret = secrets.token_urlsafe(48)
    try:
        config("flask_secret_key", secret, mode="w")
        log("未設定 Flask session 金鑰，已自動產生一把並寫入設定檔。",
            module_name="Website", level=logging.WARNING)
    except Exception as exc:
        # 寫不進設定檔也不能退回固定值：這次啟動用隨機金鑰，代價只是重啟後
        # 所有面板 session 失效，總比金鑰可被預測好。
        log(f"無法將 Flask session 金鑰寫入設定檔（{exc}），本次啟動使用暫時金鑰，"
            "重啟後所有面板登入階段會失效。請設定環境變數 FLASK_SECRET_KEY。",
            module_name="Website", level=logging.ERROR)
    return secret


app.secret_key = _resolve_flask_secret_key()

# ============= i18n (choke point 4) =============
# 每個 request 依 ?lang= > session > 面板登入者的個人語言 > Accept-Language
# > zh-TW 解析 locale 並設進 ContextVar；template 可用 t()/html_lang。
import i18n as _i18n

app.jinja_env.globals.update(
    t=_i18n.t, tn=_i18n.tn, fmt_num=_i18n.fmt_num, fmt_dt=_i18n.fmt_dt,
)


@app.before_request
def _set_request_locale():
    lang_param = request.args.get("lang")
    if lang_param and lang_param in _i18n.available_locales():
        session["lang"] = lang_param
    locale = _i18n.resolve_web_locale(
        user_id=_panel_user_id(),
        lang_param=lang_param,
        session_locale=session.get("lang"),
        accept_language=request.headers.get("Accept-Language"),
    )
    g.i18n_token = _i18n.push_locale(locale)
    g.locale = locale


@app.teardown_request
def _reset_request_locale(_error=None):
    token = getattr(g, "i18n_token", None)
    if token is not None:
        g.i18n_token = None
        _i18n.reset_locale(token)


def _panel_user_id():
    panel_user = session.get("panel_user") or {}
    try:
        return int(panel_user.get("id")) if panel_user.get("id") else None
    except (TypeError, ValueError):
        return None


def _safe_internal_next(target: str | None, *, default: str | None = "/") -> str | None:
    if not target:
        return default
    try:
        parsed = urlsplit(target)
    except ValueError:
        return None
    if (not target.startswith("/") or target.startswith("//") or "\\" in target or
            parsed.scheme or parsed.netloc):
        return None
    return target


def _frontend_catalog(prefixes=()):
    if isinstance(prefixes, str):
        prefixes = (prefixes,)
    return _i18n.catalog_subset(prefixes, locale=getattr(g, "locale", None))


@app.context_processor
def _inject_i18n_context():
    locale = getattr(g, "locale", _i18n.DEFAULT_LOCALE)
    return_path = request.full_path
    if return_path.endswith("?"):
        return_path = return_path[:-1]
    return {
        "html_lang": "zh-Hant" if locale == "zh-TW" else locale,
        "locale": locale,
        "available_locales": _i18n.available_locales(),
        "locale_options": [
            {"code": code, "label": _i18n.locale_display_name(code)}
            for code in _i18n.available_locales()
        ],
        "language_return_path": return_path or "/",
        "frontend_catalog": _frontend_catalog,
    }


@app.post('/api/language')
def set_web_language():
    locale = request.form.get("lang", "")
    if locale not in _i18n.available_locales():
        abort(400)
    supplied_next = request.form.get("next")
    target = _safe_internal_next(
        supplied_next,
        default="/" if supplied_next is None else None,
    )
    if target is None:
        abort(400)

    session["lang"] = locale
    user_id = _panel_user_id()
    if user_id:
        try:
            if not _i18n.set_user_locale(user_id, locale):
                log(
                    f"無法儲存網站語言偏好 (user_id={user_id}, locale={locale})",
                    module_name="Website",
                )
        except Exception as error:
            log(
                f"儲存網站語言偏好時發生錯誤 (user_id={user_id}, locale={locale}): {error}",
                module_name="Website",
            )
    return redirect(target)

@app.route('/api/status')
def api_status():
    try:
        bot_latency = round(bot.latency * 1000)  # Convert to milliseconds
    except OverflowError:
        bot_latency = "N/A"
    if bot.is_ready():
        if fully_ready:
            status_text = "online"
        else:
            status_text = "starting"
    else:
        status_text = "offline"
    status = {
        "status": status_text,
        "name": bot.user.name,
        "avatar_url": str(bot.user.avatar.url) if bot.user.avatar else None,
        "id": str(bot.user.id),
        "uptime": UtilCommands.get_uptime_seconds() if UtilCommands else None,
        "server_count": len(bot.guilds),
        "user_count": len(set(bot.get_all_members())),
        "user_install_count": bot.application.approximate_user_install_count if bot.application else None,
        "command_stats": sum(get_global_config("command_usage_stats", {}).values()) + sum(get_global_config("app_command_usage_stats", {}).values()) + sum(get_global_config("command_error_stats", {}).values()) + sum(get_global_config("app_command_error_stats", {}).values()),
        "latency_ms": bot_latency,
        "version": UtilCommands.full_version if UtilCommands else "N/A"
    }
    return status

@app.route('/api/commit_logs')
def api_commit_logs():
    logs = UtilCommands.get_commit_logs(10) if UtilCommands else ["N/A"]
    return {"commit_logs": logs}

def _get_link_preview(page):
    return build_link_preview(
        page, website_url=config("website_url", ""), name=bot.user.name,
        bot_id=bot.user.id, locale=getattr(g, "locale", _i18n.DEFAULT_LOCALE),
        translate=_i18n.t,
    )

@app.route('/')
def index():
    return render_template('index.html', bot=bot, gtag=config("website_gtag", ""), module_count=len(modules), link_preview=_get_link_preview("index"))

@app.route('/docs')
def docs():
    base_dir = Path(__file__).resolve().parent
    docs_sidebar_groups, docs_sections = load_docs_site(
        base_dir / "docs", locale=getattr(g, "locale", None))
    return render_template(
        'docs.html',
        bot=bot,
        gtag=config("website_gtag", ""),
        docs_sidebar_groups=docs_sidebar_groups,
        docs_sections=docs_sections,
        link_preview=_get_link_preview("docs"),
    )

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('PrivacyPolicy.html', bot=bot, contact_email=config("support_email", "support@example.com"), support_server_invite=config("support_server_invite", ""), gtag=config("website_gtag", ""), link_preview=_get_link_preview("privacy"))

@app.route('/terms-of-service')
def terms_of_service():
    return render_template('TermsofService.html', bot=bot, gtag=config("website_gtag", ""), link_preview=_get_link_preview("terms"))
AVATAR_ICO = None
AVATAR_PNG = None
_avatar_png_url = None
_avatar_png_retry_at = 0.0
_avatar_png_lock = Lock()
_OG_IMAGE_TIMEOUT = aiohttp.ClientTimeout(total=4, connect=2, sock_read=3)


async def _download_og_avatar(avatar_url):
    # A total deadline also covers slow streaming bodies and redirects, leaving
    # room for the page fetch within Discord's 10-second unfurl budget.
    async with aiohttp.ClientSession(timeout=_OG_IMAGE_TIMEOUT) as client:
        async with client.get(avatar_url) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > 8 * 1024 * 1024:
                    raise ValueError("Preview avatar exceeds the image download limit")
            return bytes(data)


@lru_cache(maxsize=1)
def _fallback_og_image():
    """A self-contained PNG, also available when the bot has no custom avatar."""
    image = Image.new("RGB", (512, 512), "#5865F2")
    draw = ImageDraw.Draw(image)
    draw.line((256, 96, 256, 160), fill="white", width=20)
    draw.ellipse((232, 72, 280, 120), fill="white")
    draw.rounded_rectangle((96, 152, 416, 408), radius=64, fill="white")
    for x in (184, 328):
        draw.ellipse((x - 24, 232, x + 24, 280), fill="#5865F2")
    draw.rounded_rectangle((200, 328, 312, 344), radius=8, fill="#5865F2")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _get_og_image():
    global AVATAR_PNG, _avatar_png_url, _avatar_png_retry_at
    avatar = getattr(getattr(bot, "user", None), "avatar", None)
    avatar_url = str(avatar.url) if avatar else None
    if not avatar_url:
        return _fallback_og_image(), 300
    if avatar_url == _avatar_png_url:
        if AVATAR_PNG is not None:
            return AVATAR_PNG, 3600
        if time.monotonic() < _avatar_png_retry_at:
            return _fallback_og_image(), 60
    # Concurrent crawlers should get a usable image instead of waiting in a queue.
    if not _avatar_png_lock.acquire(blocking=False):
        return _fallback_og_image(), 60
    try:
        _avatar_png_url = avatar_url
        AVATAR_PNG = None
        try:
            data = asyncio.run(_download_og_avatar(avatar_url))
            with Image.open(BytesIO(data)) as source:
                image = source.convert("RGBA").resize((512, 512), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="PNG")
            AVATAR_PNG = output.getvalue()
        except (aiohttp.ClientError, OSError, ValueError, Image.DecompressionBombError):
            _avatar_png_retry_at = time.monotonic() + 60
            log("OG image unavailable; serving the built-in preview image.", module_name="Website")
            return _fallback_og_image(), 60
        return AVATAR_PNG, 3600
    finally:
        _avatar_png_lock.release()

@app.route('/og-image.png')
def og_image():
    """Serve a cached avatar or fallback without requiring a session or login."""
    data, max_age = _get_og_image()
    return send_file(BytesIO(data), mimetype='image/png', max_age=max_age)

@app.route('/favicon.ico')
def favicon():
    global AVATAR_ICO
    if AVATAR_ICO is None:
        avatar_url = str(bot.user.avatar.url) if bot.user.avatar else None
        if avatar_url:
            avatar_path = os.path.join('static', 'avatar_temp.ico')
            try:
                avatar_image = Image.open(requests.get(avatar_url, stream=True).raw)
                avatar_image.save(avatar_path, format='ICO', sizes=[(32, 32)])
                AVATAR_ICO = avatar_path
            except Exception as e:
                log(f"無法下載或轉換機器人頭像為 favicon: {e}", module_name="Website")
                AVATAR_ICO = os.path.join('static', 'favicon.ico')
        else:
            AVATAR_ICO = os.path.join('static', 'favicon.ico')
    return send_from_directory('static', os.path.basename(AVATAR_ICO))

async def start_webserver():
    global _shutdown_event
    host = config("webserver_host")
    port = config("webserver_port")
    ssl = config("webserver_ssl")
    
    hypercorn_config = Config()
    hypercorn_config.bind = [f"{host}:{port}"]
    # verbose request
    # hypercorn_config.loglevel = "debug"
    # hypercorn_config.accesslog = "-"
    # hypercorn_config.errorlog = "-"
    
    if ssl:
        ssl_path = 'sslkey'
        hypercorn_config.certfile = os.path.join(ssl_path, 'server.crt')
        hypercorn_config.keyfile = os.path.join(ssl_path, 'server.key')
    
    # Run Hypercorn in the background
    # Prefer ASGI app (real WebSocket Socket.IO) if Explore provides it.
    web_app = app
    try:
        from Explore import asgi_app as web_app  # type: ignore
        log("使用 Explore.asgi_app (ASGIApp) 啟動網站伺服器", module_name="Website")
    except Exception as e:
        log(f"Explore.asgi_app 未啟用，改用 Flask WSGI：{e}", module_name="Website")

    # Create shutdown event for graceful shutdown
    _shutdown_event = asyncio.Event()
    asyncio.create_task(serve(web_app, hypercorn_config, shutdown_trigger=_shutdown_event.wait))
    log(f"網站伺服器已啟動 (Hypercorn) - http{'s' if ssl else ''}://{host}:{port}", module_name="Website")


async def stop_webserver():
    """Stop the webserver gracefully"""
    global _shutdown_event
    if _shutdown_event is not None:
        log("正在關閉網站伺服器...", module_name="Website")
        _shutdown_event.set()


# Register close handler
on_close_tasks.add(stop_webserver)


class Website(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.Cog.listener()
    async def on_ready(self):
        log("正在啟動網站伺服器...", module_name="Website")
        await start_webserver()

asyncio.run(bot.add_cog(Website(bot)))
        
async def is_fully_ready():
    global fully_ready
    # Wait a short time to ensure all on_ready tasks have completed
    await asyncio.sleep(5)
    fully_ready = True
    log("機器人已完全啟動，網站狀態為 online。", module_name="Website")

on_ready_tasks.append(is_fully_ready)
