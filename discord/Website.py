from flask import Flask, send_from_directory, render_template
import os
import threading
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
    return render_template('index.html')

def run_webserver():
    host = config("webserver_host")
    port = config("webserver_port")
    ssl = config("webserver_ssl")
    if ssl:
        ssl_path = 'sslkey'
        ssl_crt = os.path.join(ssl_path, 'server.crt')
        ssl_key = os.path.join(ssl_path, 'server.key')
        app.run(host=host, port=port, ssl_context=(ssl_crt, ssl_key))
    else:
        app.run(host=host, port=port)

async def start_webserver():
    thread = threading.Thread(target=run_webserver)
    thread.start()
    log("網站伺服器已啟動。", module_name="Website")

on_ready_tasks.append(start_webserver)