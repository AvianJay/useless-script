from flask import (
    Flask, abort, g, redirect, render_template, request, send_file,
    send_from_directory, session,
)
import os
import asyncio
from pathlib import Path
from urllib.parse import urlsplit
from hypercorn.config import Config
from hypercorn.asyncio import serve
from globalenv import bot, modules, config, on_ready_tasks, get_global_config, on_close_tasks
from logger import log
from PIL import Image
import requests
from discord.ext import commands
from doc_markdown import load_docs_site

# Shutdown event for graceful shutdown
_shutdown_event: asyncio.Event = None

fully_ready = False  # To track if the bot is fully ready (after on_ready tasks)

if "UtilCommands" in modules:
    import UtilCommands
else:
    UtilCommands = None

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = (
    os.environ.get("FLASK_SECRET_KEY")
    or config("client_secret", "")
    or "please-change-this-secret"
)

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

def _get_bot_og_data():
    """Get common Open Graph data for templates"""
    website_url = config("website_url", "")
    # Use our own server to serve the avatar for OG image (Discord can't crawl its own CDN)
    avatar_url = f"{website_url}/og-image.png" if website_url else ""
    return {"avatar_url": avatar_url, "website_url": website_url}

@app.route('/')
def index():
    og = _get_bot_og_data()
    return render_template('index.html', bot=bot, gtag=config("website_gtag", ""), module_count=len(modules), **og)

@app.route('/docs')
def docs():
    from flask import g
    og = _get_bot_og_data()
    base_dir = Path(__file__).resolve().parent
    docs_sidebar_groups, docs_sections = load_docs_site(
        base_dir / "docs", locale=getattr(g, "locale", None))
    return render_template(
        'docs.html',
        bot=bot,
        gtag=config("website_gtag", ""),
        docs_sidebar_groups=docs_sidebar_groups,
        docs_sections=docs_sections,
        **og,
    )

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('PrivacyPolicy.html', bot=bot, contact_email=config("support_email", "support@example.com"), support_server_invite=config("support_server_invite", ""), gtag=config("website_gtag", ""))

@app.route('/terms-of-service')
def terms_of_service():
    return render_template('TermsofService.html', bot=bot, gtag=config("website_gtag", ""))
AVATAR_ICO = None
AVATAR_PNG = None

@app.route('/og-image.png')
def og_image():
    """Serve bot avatar as PNG for Open Graph / Discord embed previews"""
    global AVATAR_PNG
    if AVATAR_PNG is None:
        avatar_url = str(bot.user.avatar.url) if bot.user.avatar else None
        if avatar_url:
            avatar_path = os.path.join('static', 'og_avatar.png')
            try:
                avatar_image = Image.open(requests.get(avatar_url, stream=True).raw)
                avatar_image = avatar_image.convert('RGBA')
                avatar_image = avatar_image.resize((512, 512), Image.LANCZOS)
                avatar_image.save(avatar_path, format='PNG')
                AVATAR_PNG = avatar_path
            except Exception as e:
                log(f"無法下載或轉換機器人頭像為 OG 圖片: {e}", module_name="Website")
                AVATAR_PNG = None
                return '', 404
        else:
            return '', 404
    return send_file(AVATAR_PNG, mimetype='image/png')

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
