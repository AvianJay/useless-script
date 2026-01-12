from flask import Flask, send_from_directory, render_template
import os
import asyncio
from hypercorn.config import Config
from hypercorn.asyncio import serve
from globalenv import bot, modules, config, on_ready_tasks
from logger import log
from PIL import Image
import requests

if "UtilCommands" in modules:
    import UtilCommands
else:
    UtilCommands = None

app = Flask(__name__, template_folder='templates', static_folder='static')

@app.route('/api/status')
def api_status():
    try:
        bot_latency = round(bot.latency * 1000)  # Convert to milliseconds
    except OverflowError:
        bot_latency = "N/A"
    status = {
        "status": "online" if bot.is_ready() else "offline",
        "name": bot.user.name,
        "avatar_url": str(bot.user.avatar.url) if bot.user.avatar else None,
        "id": str(bot.user.id),
        "uptime": UtilCommands.get_uptime_seconds() if UtilCommands else None,
        "server_count": len(bot.guilds),
        "user_count": len(set(bot.get_all_members())),
        "user_install_count": bot.application.approximate_user_install_count if bot.application else None,
        "latency_ms": bot_latency,
        "version": UtilCommands.full_version if UtilCommands else "N/A"
    }
    return status

@app.route('/api/commit_logs')
def api_commit_logs():
    logs = UtilCommands.get_commit_logs(10) if UtilCommands else ["N/A"]
    return {"commit_logs": logs}

@app.route('/')
def index():
    return render_template('index.html', bot=bot, gtag=config("website_gtag", ""))

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('PrivacyPolicy.html', bot=bot, contact_email=config("support_email", "support@example.com"), support_server_invite=config("support_server_invite", ""), gtag=config("website_gtag", ""))

@app.route('/terms-of-service')
def terms_of_service():
    return render_template('TermsofService.html', bot=bot, gtag=config("website_gtag", ""))
AVATAR_ICO = None
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

    asyncio.create_task(serve(web_app, hypercorn_config))
    log(f"網站伺服器已啟動 (Hypercorn) - http{'s' if ssl else ''}://{host}:{port}", module_name="Website")

on_ready_tasks.append(start_webserver)