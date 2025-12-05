from flask import Flask, send_from_directory, render_template
import os
import asyncio
from hypercorn.config import Config
from hypercorn.asyncio import serve
from globalenv import bot, modules, config, on_ready_tasks
from logger import log

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
        "id": bot.user.id,
        "uptime": UtilCommands.get_uptime_seconds() if UtilCommands else None,
        "server_count": len(bot.guilds),
        "user_count": sum(guild.member_count for guild in bot.guilds),
        "user_install_count": bot.application.approximate_user_install_count if bot.application else None,
        "latency_ms": bot_latency,
        "version": UtilCommands.full_version if UtilCommands else "N/A"
    }
    return status

@app.route('/')
def index():
    return render_template('index.html', bot=bot)

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('PrivacyPolicy.html', bot=bot)

@app.route('/terms-of-service')
def terms_of_service():
    return render_template('TermsofService.html', bot=bot)

async def start_webserver():
    host = config("webserver_host")
    port = config("webserver_port")
    ssl = config("webserver_ssl")
    
    hypercorn_config = Config()
    hypercorn_config.bind = [f"{host}:{port}"]
    
    if ssl:
        ssl_path = 'sslkey'
        hypercorn_config.certfile = os.path.join(ssl_path, 'server.crt')
        hypercorn_config.keyfile = os.path.join(ssl_path, 'server.key')
    
    # Run Hypercorn in the background
    asyncio.create_task(serve(app, hypercorn_config))
    log(f"網站伺服器已啟動 (Hypercorn) - http{'s' if ssl else ''}://{host}:{port}", module_name="Website")

on_ready_tasks.append(start_webserver)