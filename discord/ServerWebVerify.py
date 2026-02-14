from globalenv import bot, config, modules, get_server_config, set_server_config, get_db_connection, get_command_mention, get_user_data, set_user_data
import discord
from discord.ext import commands
from discord import app_commands
from logger import log
import logging
import requests
from flask import request, render_template, redirect
import random
import string
import secrets
import time
import uuid
import asyncio
import sqlite3
import re
import json
from typing import Union
from datetime import datetime, timezone
from urllib.parse import urlencode
if "Website" in modules:
    from Website import app
else:
    raise ModuleNotFoundError("Website module not found")

def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS webverify_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                ip_address TEXT,
                fingerprint TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS webverify_user_relation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                relation_id TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS webverify_ip_location (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                location TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

def is_valid_md5(s: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{32}", s))

def add_webverify_history(user_id, guild_id, ip_address, fingerprint):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # check theres existing record for this user in this guild with same ip and fingerprint
        cursor.execute('''
            SELECT id FROM webverify_history
            WHERE user_id = ? AND guild_id = ? AND ip_address = ? AND fingerprint = ?
        ''', (user_id, guild_id, ip_address, fingerprint))
        existing_record = cursor.fetchone()
        if not existing_record:
            cursor.execute('''
                INSERT INTO webverify_history (user_id, guild_id, ip_address, fingerprint)
                VALUES (?, ?, ?, ?)
            ''', (user_id, guild_id, ip_address, fingerprint))
            conn.commit()

        # Find all users that share the same IP or fingerprint
        cursor.execute('''
            SELECT DISTINCT user_id FROM webverify_history 
            WHERE (ip_address = ? AND ip_address IS NOT NULL) 
               OR (fingerprint = ? AND fingerprint IS NOT NULL)
        ''', (ip_address, fingerprint))
        related_users = {row[0] for row in cursor.fetchall()}
        related_users.add(user_id) # Ensure current user is included

        if not related_users:
            return

        # Find existing relation IDs for these users
        placeholders = ','.join('?' for _ in related_users)
        cursor.execute(f'''
            SELECT DISTINCT relation_id FROM webverify_user_relation
            WHERE user_id IN ({placeholders})
        ''', list(related_users))
        existing_relations = [row[0] for row in cursor.fetchall()]

        if existing_relations:
            # Merge: Use the first existing relation ID
            target_relation_id = existing_relations[0]
            
            # If there are multiple different relation IDs, we need to merge them all into one
            if len(existing_relations) > 1:
                 # Update all users with any of the found relation IDs to the target ID
                 placeholders_rel = ','.join('?' for _ in existing_relations)
                 cursor.execute(f'''
                    UPDATE webverify_user_relation
                    SET relation_id = ?
                    WHERE relation_id IN ({placeholders_rel})
                 ''', [target_relation_id] + existing_relations)
        else:
            # Create new relation ID
            target_relation_id = str(uuid.uuid4())

        # Ensure all related users have this relation ID
        for r_user_id in related_users:
            cursor.execute('SELECT id FROM webverify_user_relation WHERE user_id = ?', (r_user_id,))
            if cursor.fetchone():
                cursor.execute('''
                    UPDATE webverify_user_relation SET relation_id = ? WHERE user_id = ?
                ''', (target_relation_id, r_user_id))
            else:
                cursor.execute('''
                    INSERT INTO webverify_user_relation (user_id, relation_id) VALUES (?, ?)
                ''', (r_user_id, target_relation_id))
        
        conn.commit()

def validate_turnstile(token, remoteip=None):
    url = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'

    data = {
        'secret': config("webverify_turnstile_secret"),
        'response': token
    }

    if remoteip:
        data['remoteip'] = remoteip

    try:
        response = requests.post(url, data=data, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        log(f"Turnstile validation error: {e}", module_name="ServerWebVerify", level=logging.ERROR)
        return {'success': False, 'error-codes': ['internal-error']}

def validate_recaptcha(token, remoteip=None):
    url = 'https://www.google.com/recaptcha/api/siteverify'

    data = {
        'secret': config("webverify_recaptcha_secret"),
        'response': token
    }

    if remoteip:
        data['remoteip'] = remoteip

    try:
        response = requests.post(url, data=data, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        log(f"ReCaptcha validation error: {e}", module_name="ServerWebVerify", level=logging.ERROR)
        return {'success': False, 'error-codes': ['internal-error']}

def oauth_code_to_id(code):
    url = 'https://discord.com/api/oauth2/token'
    data = {
        'client_id': bot.application.id,
        'client_secret': config("client_secret"),
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': config("webverify_url"),  # Replace with your redirect URI
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    try:
        response = requests.post(url, data=data, headers=headers, timeout=10)
        response.raise_for_status()
        token_info = response.json()
        access_token = token_info.get('access_token')

        if not access_token:
            return None

        user_response = requests.get(
            'https://discord.com/api/users/@me',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )
        user_response.raise_for_status()
        user_info = user_response.json()
        return user_info.get('id')
    except requests.RequestException as e:
        log(f"OAuth code exchange error: {e}", module_name="ServerWebVerify", level=logging.ERROR)
        return None

def get_ip_location(ip_address):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT location FROM webverify_ip_location
            WHERE ip_address = ?
            ORDER BY timestamp DESC
            LIMIT 1
        ''', (ip_address,))
        row = cursor.fetchone()
        if row:
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return row[0]

    try:
        response = requests.get(f'https://ipinfo.io/{ip_address}/json', timeout=10)
        response.raise_for_status()
        data = response.json()
        location = {
            'city': data.get('city', ''),
            'region': data.get('region', ''),
            'country': data.get('country', '')
        }
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO webverify_ip_location (ip_address, location)
                VALUES (?, ?)
            ''', (ip_address, json.dumps(location)))
            conn.commit()
        
        return location
    except requests.RequestException as e:
        log(f"IP location fetch error: {e}", module_name="ServerWebVerify", level=logging.ERROR)
        return "Unknown"

auth_tokens = {}

def cleanup_tokens():
    current_time = time.time()
    expired_tokens = [token for token, data in auth_tokens.items() if current_time - data['timestamp'] > 600] # 10 minutes
    for token in expired_tokens:
        del auth_tokens[token]

@app.route('/server-verify', methods=['GET', 'POST'])
def server_verify():
    cleanup_tokens()
    if request.method == 'GET' and 'auth_token' in request.args:
        if 'auth_token' not in request.args:
            return render_template('ServerVerify.html', error="缺少驗證令牌。請重新從伺服器中的驗證按鈕進入此頁面。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        auth_token = request.args.get('auth_token')
        if auth_token not in auth_tokens:
            return render_template('ServerVerify.html', error="無效的驗證令牌。請重新從伺服器中的驗證按鈕進入此頁面。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        user_id = auth_tokens[auth_token]['user_id']
        guild_id = auth_tokens[auth_token]['guild_id']
        guild_config = get_server_config(guild_id, "webverify_config")
        return render_template('ServerVerify.html', bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), captcha_type=guild_config.get('captcha_type'), guild_name=bot.get_guild(guild_id).name, gtag=config("website_gtag", ""))
    elif request.method == 'GET' and 'error' in request.args:
        error_message = request.args.get('error')
        if error_message == "access_denied":
            error_message = "您拒絕了應用程式存取您的 Discord 帳號資訊的授權。請重新從伺服器中的驗證按鈕進入此頁面並授權應用程式。"
        return render_template('ServerVerify.html', error=error_message, bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
    elif request.method == 'GET':
        guild_id = request.args.get('state')
        code = request.args.get('code')
        if not guild_id or not code:
            return render_template('ServerVerify.html', error="缺少參數。請從伺服器中的驗證按鈕進入此頁面。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return render_template('ServerVerify.html', error="找不到指定的伺服器。請確認您是從正確的驗證按鈕進入此頁面。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        
        guild_config = get_server_config(guild.id, "webverify_config")
        if not guild_config:
            return render_template('ServerVerify.html', error="此伺服器未設定網頁驗證。請聯絡伺服器管理員。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        if not guild_config.get('enabled', False):
            return render_template('ServerVerify.html', error="此伺服器的網頁驗證功能已停用。請聯絡伺服器管理員。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        
        user_id = oauth_code_to_id(code)
        if not user_id:
            return render_template('ServerVerify.html', error="無法取得您的 Discord 帳號資訊。請確保您已授權應用程式存取您的帳號資訊。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        
        member = guild.get_member(int(user_id))
        if not member:
            return render_template('ServerVerify.html', error="您不是此伺服器的成員。請先加入伺服器後再進行驗證。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        if not member.get_role(guild_config.get('unverified_role_id')):
            return render_template('ServerVerify.html', error="您已經通過了此伺服器的網頁驗證，無需再次驗證。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        
        auth_token = secrets.token_urlsafe(32)
        auth_tokens[auth_token] = {'user_id': user_id, 'guild_id': guild.id, 'timestamp': time.time()}
        return redirect(f"/server-verify?auth_token={auth_token}")
    elif request.method == 'POST':
        token = request.form.get('token')
        method = request.form.get('method')  # 'turnstile' or 'recaptcha'
        auth_token = request.form.get('auth_token')
        if not auth_token or auth_token not in auth_tokens:
            return "Invalid or missing auth token.", 400
        fingerprint = request.form.get('fingerprint')
        user_id = auth_tokens[auth_token]['user_id']
        guild_id = auth_tokens[auth_token]['guild_id']
        guild_config = get_server_config(guild_id, "webverify_config", {})
        guild_country_config = guild_config.get('webverify_country_alert', {}) if guild_config else {}
        if not guild_config:
            return render_template('ServerVerify.html', error="此伺服器未設定網頁驗證。請聯絡伺服器管理員。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        if not guild_config.get('enabled', False):
            return render_template('ServerVerify.html', error="此伺服器的網頁驗證功能已停用。請聯絡伺服器管理員。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        remoteip = request.headers.get('CF-Connecting-IP') or \
               request.headers.get('X-Forwarded-For') or \
               request.remote_addr
        guild = bot.get_guild(guild_id)
        if not guild:
            return render_template('ServerVerify.html', error="找不到指定的伺服器。請確認您是從正確的驗證按鈕進入此頁面。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        member = guild.get_member(int(user_id))

        if method != guild_config.get('captcha_type'):
            return render_template('ServerVerify.html', error="驗證方法與伺服器設定不符。請重新嘗試。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        
        if not is_valid_md5(fingerprint):
            return render_template('ServerVerify.html', error="錯誤的瀏覽器指紋。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))

        if method == 'turnstile':
            result = validate_turnstile(token, remoteip)
        elif method == 'recaptcha':
            result = validate_recaptcha(token, remoteip)
        else:
            if guild_config.get('captcha_type') != 'none':
                return render_template('ServerVerify.html', error="無效的驗證方法。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
            result = {'success': True}

        if result.get('success'):
            add_webverify_history(user_id, guild_id, remoteip, fingerprint)
            if member.get_role(guild_config.get('unverified_role_id')):
                asyncio.run_coroutine_threadsafe(member.remove_roles(discord.Object(id=guild_config.get('unverified_role_id')), reason="通過網頁驗證"), bot.loop)
            log("用戶通過了網頁驗證", module_name="ServerWebVerify", user=member, guild=guild)
            try:
                if guild_country_config.get('enabled', False):
                    mode = guild_country_config.get('mode', 'blacklist')
                    location = get_ip_location(remoteip)
                    country = location.get('country', 'Unknown') if isinstance(location, dict) else 'Unknown'
                    alert_countries = guild_country_config.get('countries', [])
                    if (mode == 'blacklist' and country in alert_countries) or (mode == 'whitelist' and country not in alert_countries):
                        alert_channel_id = guild_country_config.get('alert_channel_id')
                        alert_channel = guild.get_channel(alert_channel_id) if alert_channel_id else None
                        if alert_channel:
                            embed = discord.Embed(title="網頁驗證異常地理位置警報", color=0xFF0000)
                            embed.set_author(name=str(member), icon_url=member.display_avatar.url if member.display_avatar else None)
                            embed.add_field(name="用戶 ID", value=str(member.id), inline=False)
                            embed.add_field(name="地區", value=location.get('region', 'Unknown'))
                            embed.add_field(name="城市", value=location.get('city', 'Unknown'))
                            embed.add_field(name="國家代碼", value=country)
                            embed.timestamp = datetime.now(timezone.utc)
                            asyncio.run_coroutine_threadsafe(alert_channel.send(embed=embed), bot.loop)
            except Exception as e:
                log(f"發送地理位置警報失敗：{e}", level=logging.ERROR, module_name="ServerWebVerify", guild=guild)
            # try to dm user
            try:
                asyncio.run_coroutine_threadsafe(member.send(f"您已成功通過 {guild.name} 的網頁驗證，現在可以訪問伺服器了！"), bot.loop)
            except Exception as e:
                log(f"無法私訊用戶 {member} 通知其驗證成功：{e}", level=logging.ERROR, module_name="ServerWebVerify", user=member, guild=guild)
            return render_template('ServerVerify.html', error="驗證成功！您現在可以返回伺服器。", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        else:
            error_codes = result.get('error-codes', [])
            log(f"用戶未能通過網頁驗證，錯誤代碼：{error_codes}", module_name="ServerWebVerify", user=member, guild=guild)
            return render_template('ServerVerify.html', error=f"驗證失敗。錯誤代碼：{', '.join(error_codes)}", bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class ServerWebVerify(commands.GroupCog, name="webverify", description="伺服器網頁驗證設定指令"):
    def __init__(self, bot):
        self.bot = bot
        self.force_ctx_menu = app_commands.ContextMenu(name="強制用戶驗證", callback=self.force_user_verify_context_menu)
        bot.tree.add_command(self.force_ctx_menu)
        self.manual_ctx_menu = app_commands.ContextMenu(name="手動驗證用戶", callback=self.manual_verify_user_context_menu)
        bot.tree.add_command(self.manual_ctx_menu)
    
    @app_commands.command(name="setup", description="設定伺服器的網頁驗證功能")
    @app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        default_config = {
            'enabled': True,
            'captcha_type': 'turnstile',
            'unverified_role_id': None,
            'autorole_enabled': False,
            'autorole_trigger': 'always',
            'min_age': 7,
            'notify': {
                'type': 'dm',
                'channel_id': None,
                'title': '伺服器網頁驗證',
                'message': '請點擊下方按鈕進行網頁驗證：'
            }
        }
        set_server_config(guild_id, "webverify_config", default_config)
        await interaction.response.send_message(f"伺服器的網頁驗證功能已設定完成。請記得設定未驗證成員的角色({get_command_mention('webverify', 'set_unverified_role')})。")

    @app_commands.command(name="quick_setup", description="使用互動式精靈快速設定網頁驗證")
    @app_commands.default_permissions(administrator=True)
    async def quick_setup(self, interaction: discord.Interaction):
        view = WebVerifySetupWizard(interaction, self.bot)
        await interaction.response.send_message(embed=await view.get_embed(), view=view)

    
    @app_commands.command(name="disable", description="停用伺服器的網頁驗證功能")
    @app_commands.default_permissions(administrator=True)
    async def disable(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        guild_config = get_server_config(guild_id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message("伺服器尚未設定網頁驗證功能。")
            return
        guild_config['enabled'] = False
        set_server_config(guild_id, "webverify_config", guild_config)
        await interaction.response.send_message("伺服器的網頁驗證功能已停用。")
    
    @app_commands.command(name="enable", description="啟用伺服器的網頁驗證功能")
    @app_commands.default_permissions(administrator=True)
    async def enable(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        guild_config = get_server_config(guild_id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message("伺服器尚未設定網頁驗證功能。")
            return
        guild_config['enabled'] = True
        set_server_config(guild_id, "webverify_config", guild_config)
        await interaction.response.send_message("伺服器的網頁驗證功能已啟用。")
    
    @app_commands.command(name="set_captcha", description="設定網頁驗證使用的 CAPTCHA 提供者")
    @app_commands.describe(captcha_provider="選擇 CAPTCHA 提供者")
    @app_commands.choices(captcha_provider=[
        app_commands.Choice(name="無", value="none"),
        app_commands.Choice(name="Cloudflare Turnstile", value="turnstile"),
        app_commands.Choice(name="Google reCAPTCHA", value="recaptcha")
    ])
    @app_commands.default_permissions(administrator=True)
    async def set_captcha(self, interaction: discord.Interaction, captcha_provider: str):
        guild_id = interaction.guild.id
        guild_config = get_server_config(guild_id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message("伺服器尚未設定網頁驗證功能。")
            return
        if captcha_provider not in ['none', 'turnstile', 'recaptcha']:
            await interaction.response.send_message("無效的 CAPTCHA 提供者。請選擇 'none'、'turnstile' 或 'recaptcha'。")
            return
        guild_config['captcha_type'] = captcha_provider
        set_server_config(guild_id, "webverify_config", guild_config)
        await interaction.response.send_message(f"網頁驗證的 CAPTCHA 類型已設定為 {captcha_provider}。" if captcha_provider != 'none' else "已關閉 CAPTCHA 驗證。")
    
    @app_commands.command(name="set_unverified_role", description="設定未驗證成員的角色")
    @app_commands.describe(role="選擇未驗證成員的角色")
    @app_commands.default_permissions(administrator=True)
    async def set_unverified_role(self, interaction: discord.Interaction, role: discord.Role):
        guild_id = interaction.guild.id
        guild_config = get_server_config(guild_id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message("伺服器尚未設定網頁驗證功能。")
            return
        guild_config['unverified_role_id'] = role.id
        set_server_config(guild_id, "webverify_config", guild_config)
        await interaction.response.send_message(f"未驗證成員的角色已設定為 {role.name}。")
    
    @app_commands.command(name="status", description="查看伺服器的網頁驗證設定狀態")
    async def status(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        guild_config = get_server_config(guild_id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message("伺服器尚未設定網頁驗證功能。")
            return
        status_msg = (
            f"網頁驗證功能狀態：{'啟用' if guild_config.get('enabled', False) else '停用'}\n"
            f"CAPTCHA 類型：{guild_config.get('captcha_type', '未設定')}\n"
            f"未驗證成員角色 ID：{guild_config.get('unverified_role_id', '未設定')}"
        )
        await interaction.response.send_message(status_msg)
    
    @app_commands.command(name="verify_notify", description="設定驗證通知的方式")
    @app_commands.describe(type="選擇要如何提示", channel="選擇要發送驗證訊息的頻道", title="自訂 Embed 標題", message="自訂驗證訊息內容")
    @app_commands.choices(type=[
        app_commands.Choice(name="在頻道內", value="channel"),
        app_commands.Choice(name="私訊", value="dm"),
        app_commands.Choice(name="都要", value="both")
    ])
    @app_commands.default_permissions(administrator=True)
    async def verify_notify(self, interaction: discord.Interaction, type: str = "channel", channel: discord.TextChannel = None, title: str = "伺服器網頁驗證", message: str = "請點擊下方按鈕進行網頁驗證："):
        guild_id = interaction.guild.id
        guild_config = get_server_config(guild_id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message("伺服器尚未設定網頁驗證功能。")
            return
        if channel is None:
            channel = interaction.channel
        guild_config['notify'] = {
            'type': type,
            'channel_id': channel.id if type in ["channel", "both"] else None,
            'title': title,
            'message': message
        }
        set_server_config(guild_id, "webverify_config", guild_config)
        if type in ["channel", "both"]:
            verify_url = f"https://discord.com/oauth2/authorize?client_id={bot.application.id}&response_type=code&scope=identify&prompt=none&{urlencode({'redirect_uri': config('webverify_url')})}&state={guild_id}"
            verify_button = discord.ui.Button(label="前往驗證", url=verify_url)
            view = discord.ui.View()
            view.add_item(verify_button)
            embed = discord.Embed(title=title, description=message, color=0x00ff00)
            await channel.send(embed=embed, view=view)
            await interaction.response.send_message(f"已在 {channel.mention} 發送網頁驗證訊息。")
        elif type == "dm":
            await interaction.response.send_message("已設定驗證通知方式為私訊。")
    
    @app_commands.command(name="check_relation", description="檢查用戶的關聯帳號")
    @app_commands.describe(user="要檢查的用戶")
    @app_commands.default_permissions(administrator=True)
    async def check_relation(self, interaction: discord.Interaction, user: discord.User):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Get relation_id for the user
            cursor.execute('SELECT relation_id FROM webverify_user_relation WHERE user_id = ?', (user.id,))
            result = cursor.fetchone()
            
            if not result:
                await interaction.response.send_message(f"找不到用戶 {user.mention} 的關聯資料。", ephemeral=True)
                return
            
            relation_id = result[0]
            
            # Get all users with this relation_id
            cursor.execute('SELECT user_id FROM webverify_user_relation WHERE relation_id = ?', (relation_id,))
            related_user_ids = [row[0] for row in cursor.fetchall()]
            
            related_users_mentions = []
            for uid in related_user_ids:
                try:
                    u = await self.bot.fetch_user(uid)
                    related_users_mentions.append(f"{u.name} ({u.mention}) [`{uid}`]")
                except:
                    related_users_mentions.append(f"Unknown User [`{uid}`]")
            
            embed = discord.Embed(title=f"用戶 {user.name} 的關聯帳號", color=0xff0000)
            embed.add_field(name="關聯 ID", value=f"`{relation_id}`", inline=False)
            embed.add_field(name=f"關聯帳號 ({len(related_users_mentions)})", value="\n".join(related_users_mentions), inline=False)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(name="relation_action", description="對用戶及其關聯帳號進行操作")
    @app_commands.describe(user="選擇用戶", action="要執行的操作 (格式與 !moderate 相同)")
    @app_commands.default_permissions(administrator=True)
    async def relation_action(self, interaction: discord.Interaction, user: discord.Member, action: str):
        if "Moderate" not in modules:
            await interaction.response.send_message("Moderate 模組未啟用，無法執行此操作。", ephemeral=True)
            log("Moderate module not found", level=logging.ERROR, module_name="ServerWebVerify")
            return
        
        import Moderate # checking modules above ensures this is safe-ish, but ideally we rely on the check

        await interaction.response.defer()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Get relation_id for the user
            cursor.execute('SELECT relation_id FROM webverify_user_relation WHERE user_id = ?', (user.id,))
            result = cursor.fetchone()
            
            if not result:
                await interaction.followup.send(f"找不到用戶 {user.mention} 的關聯資料。")
                return
            
            relation_id = result[0]
            
            # Get all users with this relation_id
            cursor.execute('SELECT user_id FROM webverify_user_relation WHERE relation_id = ?', (relation_id,))
            related_user_ids = [row[0] for row in cursor.fetchall()]
        
        results = []
        for uid in related_user_ids:
            try:
                member = interaction.guild.get_member(uid)
                if not member:
                    # try to fetch if not in cache (though get_member usually checks cache)
                    try:
                        member = await interaction.guild.fetch_member(uid)
                    except discord.NotFound:
                        results.append(f"用戶 ID: `{uid}` - 未在伺服器中，跳過。")
                        continue
                
                logs = await Moderate.do_action_str(action, interaction.guild, member, None, moderator=interaction.user)
                if len(logs) == 0:
                    results.append(f"{member.mention} - 無操作。")
                else:
                    results.append(f"{member.mention} - {'; '.join(logs)}")

            except Exception as e:
                results.append(f"用戶 ID: `{uid}` - 執行錯誤: {e}")
        
        # Split results into chunks to avoid message length limits
        output = f"**對 {user.mention} 及其關聯帳號的操作結果：**\n"
        chunks = []
        current_chunk = ""
        for line in results:
            if len(current_chunk) + len(line) + 1 > 1900:
                chunks.append(current_chunk)
                current_chunk = ""
            current_chunk += line + "\n"
        chunks.append(current_chunk)

        for i, chunk in enumerate(chunks):
            if i == 0:
                await interaction.followup.send(output + chunk)
            else:
                await interaction.followup.send(chunk)
    
    @app_commands.command(name="autorole", description="設定自動為新成員分配未驗證角色")
    @app_commands.describe(enable="啟用或停用自動分配未驗證角色", trigger="選擇給予身分組條件")
    @app_commands.choices(trigger=[
        app_commands.Choice(name="總是給予", value="always"),
        app_commands.Choice(name="帳號年齡過小", value="age_check"),
        app_commands.Choice(name="無驗證紀錄", value="no_history"),
        app_commands.Choice(name="帳號曾經被標記過", value="has_flagged_history"),
        app_commands.Choice(name="曾經退出過伺服器", value="left_guild_before")
    ])
    @app_commands.default_permissions(administrator=True)
    async def autorole(self, interaction: discord.Interaction, enable: bool, trigger: str):
        guild_id = interaction.guild.id
        guild_config = get_server_config(guild_id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message("伺服器尚未設定網頁驗證功能。")
            return
        guild_config['autorole_enabled'] = enable
        current_trigger = guild_config.get('autorole_trigger', 'always')
        if current_trigger == "always":
            guild_config['autorole_trigger'] = trigger
        else:
            if trigger == "always":
                guild_config['autorole_trigger'] = "always"
            else:
                triggers = current_trigger.split('+')
                if trigger in triggers:
                    triggers.remove(trigger)
                else:
                    triggers.append(trigger)
                guild_config['autorole_trigger'] = '+'.join(triggers)
        set_server_config(guild_id, "webverify_config", guild_config)
        status = "已啟用" if enable else "已停用"
        await interaction.response.send_message(f"自動分配未驗證角色功能{status}，觸發條件：{guild_config['autorole_trigger']}。")
    
    @app_commands.command(name="create_unverified_role", description="自動建立並設定未驗證成員的身分組")
    @app_commands.describe(name="未驗證成員身分組名稱")
    @app_commands.default_permissions(administrator=True)
    async def create_unverified_role(self, interaction: discord.Interaction, name: str = "未驗證成員"):
        guild = interaction.guild
        existing_role = discord.utils.get(guild.roles, name=name)
        if existing_role:
            await interaction.response.send_message(f"角色 '{name}' 已存在。請使用其他名稱或直接設定此角色為未驗證成員角色。")
            return
        await interaction.response.defer()
        unverified_role = await guild.create_role(name=name, reason="建立未驗證成員身分組")
        # try to set role permissions to deny send messages in all text channels
        for channel in guild.text_channels:
            await channel.set_permissions(unverified_role, send_messages=False, connect=False, create_public_threads=False, create_private_threads=False, reason="設定未驗證成員身分組權限")
        guild_config = get_server_config(guild.id, "webverify_config")
        if not guild_config:
            guild_config = {}
        guild_config['unverified_role_id'] = unverified_role.id
        set_server_config(guild.id, "webverify_config", guild_config)
        await interaction.followup.send(f"已建立角色 '{name}' 並將所有文字頻道權限關閉且設定為未驗證成員角色。")
    
    @app_commands.command(name="minage", description="定義最小帳號年齡")
    @app_commands.describe(min_age="最小帳號年齡（天）")
    @app_commands.default_permissions(administrator=True)
    async def minage(self, interaction: discord.Interaction, min_age: int):
        guild_config = get_server_config(interaction.guild.id, "webverify_config")
        if not guild_config:
            guild_config = {}
        guild_config['min_age'] = min_age
        set_server_config(interaction.guild.id, "webverify_config", guild_config)
        await interaction.response.send_message(f"最小帳號年齡已設定為 {min_age} 天。")
    
    @app_commands.command(name="country-alert", description="設定驗證地區警示")
    @app_commands.describe(
        enable="啟用或停用地區警示功能",
        mode="選擇警示模式",
        countries="輸入國家代碼，使用逗號分隔 (例如: US,CN,RU)",
        channel="選擇接收警示的頻道"
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="封鎖清單模式", value="blocklist"),
        app_commands.Choice(name="允許清單模式", value="allowlist")
    ])
    @app_commands.default_permissions(administrator=True)
    async def country_alert(self, interaction: discord.Interaction, enable: bool, mode: str, countries: str, channel: discord.TextChannel):
        guild_config = get_server_config(interaction.guild.id, "webverify_config")
        if not guild_config:
            guild_config = {}
        country_list = [code.strip().upper() for code in countries.split(',') if code.strip()]
        guild_config['webverify_country_alert'] = {
            'enabled': enable,
            'mode': mode,
            'countries': country_list,
            'channel_id': channel.id
        }
        set_server_config(interaction.guild.id, "webverify_config", guild_config)
        status = "已啟用" if enable else "已停用"
        await interaction.response.send_message(f"地區警示功能{status}。模式：{mode}，國家代碼：{', '.join(country_list)}，警示頻道：{channel.mention}。")
    
    @app_commands.command(name="manual-check-country", description="手動檢查用戶的地理位置")
    @app_commands.describe(user="選擇用戶")
    @app_commands.default_permissions(administrator=True)
    async def manual_check_country(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer()
        guild_config = get_server_config(interaction.guild.id, "webverify_config")
        guild_config = guild_config.get('webverify_country_alert') if guild_config else None
        if not guild_config or not guild_config.get('enabled', False):
            await interaction.followup.send("此伺服器未啟用地理位置警示功能。")
            return
        await interaction.followup.send("請稍候...")
        user_ips = []
        if user:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT ip_address, timestamp FROM webverify_history WHERE user_id = ? AND guild_id = ? ORDER BY timestamp DESC LIMIT 1', (user.id, interaction.guild.id))
                row = cursor.fetchone()
                if row:
                    user_ips.append({'user_id': user.id, 'ip': row[0], 'timestamp': row[1]})
        else:
            got_users = set()
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT user_id, ip_address, timestamp FROM webverify_history WHERE guild_id = ? ORDER BY timestamp DESC', (interaction.guild.id,))
                rows = cursor.fetchall()
                for row in rows:
                    if row[0] not in got_users:
                        user_ips.append({'user_id': row[0], 'ip': row[1], 'timestamp': row[2]})
                        got_users.add(row[0])
        if not user_ips:
            await interaction.followup.send("找不到用戶的驗證紀錄。")
            return
        report_lines = []
        for entry in user_ips:
            location = get_ip_location(entry['ip'])
            country = location.get('country', 'Unknown') if isinstance(location, dict) else 'Unknown'
            country_list = guild_config.get('countries', [])
            mode = guild_config.get('mode', 'blacklist')
            if (mode == 'blacklist' and country in country_list) or (mode == 'whitelist' and country not in country_list):
                try:
                    u = await bot.fetch_user(entry['user_id'])
                    user_mention = f"{u.name} ({u.id})"
                except:
                    user_mention = f"Unknown User (`{entry['user_id']}`)"
                timestamp = datetime.fromtimestamp(entry['timestamp'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
                report_lines.append(f"用戶: {user_mention} | 國家代碼: {country} | 時間: {timestamp}")
        if not report_lines:
            await interaction.followup.send("所有用戶的地理位置均符合設定的清單條件，無異常紀錄。")
            return
        for i in range(0, len(report_lines), 20):
            chunk = report_lines[i:i+20]
            await interaction.followup.send("```" + "\n".join(chunk) + "```")
    
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild_config = get_server_config(member.guild.id, "webverify_config")
        if not guild_config:
            return
        if not guild_config.get('autorole_enabled', False):
            return
        unverified_role_id = guild_config.get('unverified_role_id')
        if not unverified_role_id:
            return
        cfg_trigger = guild_config.get('autorole_trigger', 'always')
        assign_role = False
        triggers = cfg_trigger.split('+')
        if member.bot:
            return

        for trigger in triggers:
            if trigger == 'always':
                assign_role = True
            elif trigger == 'age_check':
                account_age = (discord.utils.utcnow() - member.created_at).total_seconds()
                if account_age < guild_config.get('min_age', 7) * 86400:
                    assign_role = True
            elif trigger == 'no_history':
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT COUNT(*) FROM webverify_history WHERE user_id = ?', (member.id,))
                    count = cursor.fetchone()[0]
                    if count == 0:
                        assign_role = True
            elif trigger == 'has_flagged_history':
                database_file = config("flagged_database_path", "flagged_data.db")
                conn = sqlite3.connect(database_file)
                cursor = conn.cursor()
                cursor.execute('SELECT user_id, guild_id, flagged_at, flagged_role FROM flagged_users WHERE user_id = ?', (member.id,))
                results = cursor.fetchall()
                results = [dict(zip([column[0] for column in cursor.description], row)) for row in results]
                if results:
                    assign_role = True
                conn.close()
            elif trigger == 'left_guild_before':
                is_left_before = get_user_data(member.guild.id, member.id, "left_guild") == "True"
                if is_left_before:
                    assign_role = True

        if assign_role:
            await member.add_roles(discord.Object(id=unverified_role_id), reason="自動分配未驗證角色")
            notify_type = guild_config.get('notify', {}).get('type', 'dm')
            log(f"自動為新成員 {member} 分配未驗證角色", module_name="ServerWebVerify", guild=member.guild, user=member)
            if notify_type in ['dm', 'both']:
                notify_title = guild_config.get('notify', {}).get('title')
                notify_message = guild_config.get('notify', {}).get('message')
                embed = discord.Embed(title=notify_title, description=notify_message, color=0x00ff00)
                embed.set_footer(text=member.guild.name, icon_url=member.guild.icon.url if member.guild.icon else None)
                verify_url = f"https://discord.com/oauth2/authorize?client_id={bot.application.id}&response_type=code&scope=identify&prompt=none&{urlencode({'redirect_uri': config('webverify_url')})}&state={member.guild.id}"
                verify_button = discord.ui.Button(label="前往驗證", url=verify_url)
                view = discord.ui.View()
                view.add_item(verify_button)
                await member.send(embed=embed, view=view)
    
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        set_user_data(member.guild.id, member.id, "left_guild", "True")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles != after.roles:
            guild_config = get_server_config(before.guild.id, "webverify_config")
            if not guild_config:
                return
            unverified_role_id = guild_config.get('unverified_role_id')
            if not unverified_role_id:
                return
            # had_unverified = any(role.id == unverified_role_id for role in before.roles)
            has_unverified = any(role.id == unverified_role_id for role in after.roles)
            if has_unverified:
                # if dm enabled, send dm
                notify_type = guild_config.get('notify', {}).get('type', 'dm')
                if notify_type in ['dm', 'both']:
                    notify_title = guild_config.get('notify', {}).get('title')
                    notify_message = guild_config.get('notify', {}).get('message')
                    embed = discord.Embed(title=notify_title, description=notify_message, color=0x00ff00)
                    embed.set_footer(text=after.guild.name, icon_url=after.guild.icon.url if after.guild.icon else None)
                    verify_url = f"https://discord.com/oauth2/authorize?client_id={bot.application.id}&response_type=code&scope=identify&prompt=none&{urlencode({'redirect_uri': config('webverify_url')})}&state={after.guild.id}"
                    verify_button = discord.ui.Button(label="前往驗證", url=verify_url)
                    view = discord.ui.View()
                    view.add_item(verify_button)
                    try:
                        await after.send(embed=embed, view=view)
                    except Exception as e:
                        log(f"無法私訊用戶 {after} 通知其驗證狀態變更：{e}", level=logging.ERROR, module_name="ServerWebVerify", user=after, guild=after.guild)
    
    async def force_user_verify_context_menu(self, interaction: discord.Interaction, user: Union[discord.Member, discord.User]):
        if not isinstance(user, discord.Member):
            await interaction.response.send_message("只能對伺服器成員使用此操作。", ephemeral=True)
            return
        guild_config = get_server_config(interaction.guild.id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message("伺服器尚未設定網頁驗證功能。", ephemeral=True)
            return
        unverified_role_id = guild_config.get('unverified_role_id')
        if not unverified_role_id:
            await interaction.response.send_message("伺服器尚未設定未驗證成員的角色。", ephemeral=True)
            return
        if user.get_role(unverified_role_id):
            await interaction.response.send_message("該用戶已經是未驗證狀態了。", ephemeral=True)
            return
        await user.add_roles(discord.Object(id=unverified_role_id), reason="強制分配未驗證角色")
        await interaction.response.send_message(f"已強制將 {user.mention} 設為未驗證狀態。", ephemeral=True)
    
    async def manual_verify_user_context_menu(self, interaction: discord.Interaction, user: Union[discord.Member, discord.User]):
        if not isinstance(user, discord.Member):
            await interaction.response.send_message("只能對伺服器成員使用此操作。", ephemeral=True)
            return
        guild_config = get_server_config(interaction.guild.id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message("伺服器尚未設定網頁驗證功能。", ephemeral=True)
            return
        unverified_role_id = guild_config.get('unverified_role_id')
        if not unverified_role_id:
            await interaction.response.send_message("伺服器尚未設定未驗證成員的角色。", ephemeral=True)
            return
        if not user.get_role(unverified_role_id):
            await interaction.response.send_message("該用戶目前不是未驗證狀態。", ephemeral=True)
            return
        await user.remove_roles(discord.Object(id=unverified_role_id), reason="手動移除未驗證角色")
        await interaction.response.send_message(f"已將 {user.mention} 設為已驗證狀態。", ephemeral=True)
        # try to send
        try:
            await user.send(f"你的驗證狀態已被管理員手動設為已驗證。\n-# 伺服器: {user.guild.name}")
        except Exception as e:
            log(f"無法私訊用戶 {user} 通知其驗證狀態變更：{e}", level=logging.ERROR, module_name="ServerWebVerify", user=user, guild=user.guild)

class WebVerifySetupWizard(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, bot: commands.Bot):
        super().__init__(timeout=300)
        self.interaction = interaction
        self.bot = bot
        self.guild = interaction.guild
        self.config = get_server_config(self.guild.id, "webverify_config") or {
            'enabled': True,
            'captcha_type': 'turnstile',
            'unverified_role_id': None,
            'autorole_enabled': False,
            'autorole_trigger': 'always',
            'min_age': 7,
            'notify': {'type': 'dm', 'channel_id': None, 'title': '伺服器網頁驗證', 'message': '請點擊下方按鈕進行網頁驗證：'}
        }
        self.step = 1
        self.select = None
        self.update_components()
    
    async def on_timeout(self):
        await self.interaction.edit_original_response(embed=discord.Embed(title="網頁驗證設定精靈", description="精靈已超時，請重新執行命令。", color=0xff0000), view=None)
        self.stop()

    def update_components(self):
        self.clear_items()
        if self.step == 1:
            # Step 1: Captcha
            select = discord.ui.Select(placeholder="選擇 CAPTCHA 驗證方式", options=[
                discord.SelectOption(label="無 (None)", value="none", description="不使用 CAPTCHA"),
                discord.SelectOption(label="Cloudflare Turnstile", value="turnstile", description="推薦使用"),
                discord.SelectOption(label="Google reCAPTCHA", value="recaptcha", description="Google 的驗證服務")
            ])
            select.callback = self.on_captcha_select
            self.select = select
            self.add_item(select)
        
        elif self.step == 2:
            # Step 2: Role
            btn_create = discord.ui.Button(label="自動建立未驗證身分組", style=discord.ButtonStyle.green, custom_id="create_role")
            btn_create.callback = self.on_create_role
            self.add_item(btn_create)

            select_role = discord.ui.RoleSelect(placeholder="選擇現有的未驗證身分組", min_values=1, max_values=1)
            select_role.callback = self.on_select_role
            self.select = select_role
            self.add_item(select_role)

        elif self.step == 3:
            # Step 3: Autorole
            btn_toggle = discord.ui.Button(
                label=f"自動分配功能: {'已啟用' if self.config.get('autorole_enabled') else '已停用'}",
                style=discord.ButtonStyle.success if self.config.get('autorole_enabled') else discord.ButtonStyle.danger
            )
            btn_toggle.callback = self.on_toggle_autorole
            self.add_item(btn_toggle)

            if self.config.get('autorole_enabled'):
                trigger_options = [
                    discord.SelectOption(label="總是給予 (Always)", value="always"),
                    discord.SelectOption(label="帳號年齡過小 (Age Check)", value="age_check"),
                    discord.SelectOption(label="無驗證紀錄 (No History)", value="no_history"),
                    discord.SelectOption(label="帳號曾經被標記過 (Flagged History)", value="has_flagged_history")
                ]
                # Pre-select current triggers
                current_triggers = self.config.get('autorole_trigger', 'always').split('+')
                for opt in trigger_options:
                    if opt.value in current_triggers:
                        opt.default = True
                
                select_trigger = discord.ui.Select(placeholder="選擇自動分配觸發條件 (可多選)", min_values=1, max_values=len(trigger_options), options=trigger_options)
                select_trigger.callback = self.on_select_trigger
                self.select = select_trigger
                self.add_item(select_trigger)

            btn_next = discord.ui.Button(label="下一步", style=discord.ButtonStyle.primary)
            btn_next.callback = self.on_next_step
            self.add_item(btn_next)

        elif self.step == 4:
            # Step 4: Notify
            select_type = discord.ui.Select(placeholder="選擇通知方式", options=[
                discord.SelectOption(label="私訊通知 (DM)", value="dm"),
                discord.SelectOption(label="頻道通知 (Channel)", value="channel")
            ])
            # Set default
            if self.config.get('notify', {}).get('type') == 'dm':
                select_type.options[0].default = True
            else:
                select_type.options[1].default = True
            
            select_type.callback = self.on_notify_type_select
            self.add_item(select_type)

            if self.config.get('notify', {}).get('type') == 'channel':
                select_channel = discord.ui.ChannelSelect(
                    placeholder="選擇通知頻道", 
                    channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                    min_values=1, max_values=1
                )
                select_channel.callback = self.on_channel_select
                self.select = select_channel
                self.add_item(select_channel)

            btn_finish = discord.ui.Button(label="完成設定", style=discord.ButtonStyle.success)
            btn_finish.callback = self.on_finish
            self.add_item(btn_finish)

    async def get_embed(self):
        embed = discord.Embed(title=f"網頁驗證設定精靈 (步驟 {self.step}/4)", color=0x00ff00)
        if self.step == 1:
            embed.description = "首先，請選擇要使用的 CAPTCHA 驗證機制。"
            embed.add_field(name="目前設定", value=self.config.get('captcha_type', '尚未設定'))
        elif self.step == 2:
            embed.description = "接著，設定或建立「未驗證成員」的身分組。\n擁有此身分組的成員通常會被限制權限，直到通過驗證。"
            role_id = self.config.get('unverified_role_id')
            role = self.guild.get_role(role_id) if role_id else None
            embed.add_field(name="目前設定", value=role.mention if role else "尚未設定")
        elif self.step == 3:
            embed.description = "設定是否在成員加入時自動給予未驗證身分組，以及觸發的條件。"
            embed.add_field(name="功能狀態", value="啟用" if self.config.get('autorole_enabled') else "停用")
            embed.add_field(name="觸發條件", value=self.config.get('autorole_trigger', 'always'))
        elif self.step == 4:
            embed.description = "最後，設定驗證提示的通知方式。\n如果是頻道通知，將會發送一個永久性的驗證 Embed 到該頻道。"
            notify = self.config.get('notify', {})
            embed.add_field(name="通知類型", value=notify.get('type', 'dm'))
            if notify.get('type') == 'channel':
                chan = self.guild.get_channel(notify.get('channel_id'))
                embed.add_field(name="通知頻道", value=chan.mention if chan else "尚未選擇")
        return embed

    async def on_captcha_select(self, interaction: discord.Interaction):
        self.config['captcha_type'] = self.select.values[0]
        self.step = 2
        self.update_components()
        await interaction.response.edit_message(embed=await self.get_embed(), view=self)

    async def on_create_role(self, interaction: discord.Interaction):
        # Use a modal to get role name
        modal = RoleCreationModal(self)
        await interaction.response.send_modal(modal)

    async def on_select_role(self, interaction: discord.Interaction):
        role = self.select.values[0]
        self.config['unverified_role_id'] = role.id
        self.step = 3
        self.update_components()
        await interaction.response.edit_message(embed=await self.get_embed(), view=self)

    async def on_toggle_autorole(self, interaction: discord.Interaction):
        self.config['autorole_enabled'] = not self.config.get('autorole_enabled', False)
        self.update_components()
        await interaction.response.edit_message(embed=await self.get_embed(), view=self)

    async def on_select_trigger(self, interaction: discord.Interaction):
        self.config['autorole_trigger'] = "+".join(self.select.values)
        self.update_components()
        await interaction.response.edit_message(embed=await self.get_embed(), view=self)

    async def on_next_step(self, interaction: discord.Interaction):
        self.step = 4
        self.update_components()
        await interaction.response.edit_message(embed=await self.get_embed(), view=self)

    async def on_notify_type_select(self, interaction: discord.Interaction):
        if 'notify' not in self.config: self.config['notify'] = {}
        self.config['notify']['type'] = self.select.values[0]
        self.update_components()
        await interaction.response.edit_message(embed=await self.get_embed(), view=self)

    async def on_channel_select(self, interaction: discord.Interaction):
        if 'notify' not in self.config: self.config['notify'] = {}
        self.config['notify']['channel_id'] = self.select.values[0].id
        self.update_components()
        await interaction.response.edit_message(embed=await self.get_embed(), view=self)

    async def on_finish(self, interaction: discord.Interaction):
        # Save config
        set_server_config(self.guild.id, "webverify_config", self.config)
        
        # Send message if channel notify is selected and channel is set
        msg_extras = ""
        notify = self.config.get('notify', {})
        if notify.get('type') == 'channel' and notify.get('channel_id'):
            channel = self.guild.get_channel(notify.get('channel_id'))
            if channel:
                verify_url = f"https://discord.com/oauth2/authorize?client_id={self.bot.application.id}&response_type=code&scope=identify&prompt=none&{urlencode({'redirect_uri': config('webverify_url')})}&state={self.guild.id}"
                verify_button = discord.ui.Button(label="前往驗證", url=verify_url)
                view = discord.ui.View()
                view.add_item(verify_button)
                embed = discord.Embed(title=notify.get('title', '伺服器網頁驗證'), description=notify.get('message', '請點擊下方按鈕進行網頁驗證：'), color=0x00ff00)
                try:
                    await channel.send(embed=embed, view=view)
                    msg_extras = f"\n驗證訊息已發送至 {channel.mention}。"
                except Exception as e:
                    msg_extras = f"\n無法發送驗證訊息至 {channel.mention}: {e}"

        embed = discord.Embed(title="網頁驗證設定完成", color=0x00ff00)
        embed.description = f"所有設定已儲存。{msg_extras}"
        embed.add_field(name="CAPTCHA", value=self.config.get('captcha_type'))
        
        role = self.guild.get_role(self.config.get('unverified_role_id'))
        embed.add_field(name="未驗證身分組", value=role.mention if role else "None")
        
        embed.add_field(name="自動分配", value=f"{'啟用' if self.config.get('autorole_enabled') else '停用'} ({self.config.get('autorole_trigger')})")
        embed.add_field(name="通知方式", value=notify.get('type'))
        
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()


class RoleCreationModal(discord.ui.Modal, title="建立未驗證身分組"):
    role_name = discord.ui.TextInput(label="身分組名稱", default="未驗證成員", required=True)

    def __init__(self, wizard_view: WebVerifySetupWizard):
        super().__init__()
        self.wizard_view = wizard_view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild = interaction.guild
        name = self.role_name.value
        
        # Logic from create_unverified_role
        unverified_role = await guild.create_role(name=name, reason="網頁驗證設定精靈：建立未驗證身分組")
        for channel in guild.text_channels:
            try:
                await channel.set_permissions(unverified_role, send_messages=False, connect=False, create_public_threads=False, create_private_threads=False, reason="設定未驗證成員身分組權限")
            except:
                pass # Ignore errors if bot lacks permission
        
        self.wizard_view.config['unverified_role_id'] = unverified_role.id
        self.wizard_view.step = 3
        self.wizard_view.update_components()
        await interaction.followup.edit_message(message_id=interaction.message.id, embed=await self.wizard_view.get_embed(), view=self.wizard_view)


init_db()

asyncio.run(bot.add_cog(ServerWebVerify(bot)))
