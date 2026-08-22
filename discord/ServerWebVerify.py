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
import sys
from typing import Union
from datetime import datetime, timezone
from urllib.parse import urlencode
import i18n
from i18n import t
if "Website" in modules:
    from Website import app
else:
    raise ModuleNotFoundError("Website module not found")
if "Moderate" in modules:
    from Moderate import timestr_to_seconds
else:
    raise ModuleNotFoundError("Moderate module not found")
if "UtilCommands" in modules:
    from UtilCommands import get_time_text
else:
    raise ModuleNotFoundError("UtilCommands module not found")

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
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.missing_auth_token"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        auth_token = request.args.get('auth_token')
        if auth_token not in auth_tokens:
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.invalid_auth_token"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        user_id = auth_tokens[auth_token]['user_id']
        guild_id = auth_tokens[auth_token]['guild_id']
        guild_config = get_server_config(guild_id, "webverify_config")
        return render_template('ServerVerify.html', bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), captcha_type=guild_config.get('captcha_type'), guild_name=bot.get_guild(guild_id).name, gtag=config("website_gtag", ""))
    elif request.method == 'GET' and 'error' in request.args:
        error_message = request.args.get('error')
        if error_message == "access_denied":
            error_message = t("serverwebverify.web.err.access_denied")
        return render_template('ServerVerify.html', error=error_message, bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
    elif request.method == 'GET':
        guild_id = request.args.get('state')
        code = request.args.get('code')
        if not guild_id or not code:
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.missing_params"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.guild_not_found"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        
        guild_config = get_server_config(guild.id, "webverify_config")
        if not guild_config:
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.not_configured"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        if not guild_config.get('enabled', False):
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.disabled"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        
        user_id = oauth_code_to_id(code)
        if not user_id:
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.no_user_info"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        
        member = guild.get_member(int(user_id))
        if not member:
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.not_a_member"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        if not member.get_role(guild_config.get('unverified_role_id')):
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.already_verified"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        
        auth_token = secrets.token_urlsafe(32)
        auth_tokens[auth_token] = {'user_id': user_id, 'guild_id': guild.id, 'timestamp': time.time()}
        return redirect(f"/server-verify?auth_token={auth_token}")
    elif request.method == 'POST':
        token = request.form.get('token')
        method = request.form.get('method')  # 'turnstile' or 'recaptcha'
        auth_token = request.form.get('auth_token')
        if not auth_token or auth_token not in auth_tokens:
            return t("serverwebverify.web.err.invalid_auth_token_api"), 400
        fingerprint = request.form.get('fingerprint')
        user_id = auth_tokens[auth_token]['user_id']
        guild_id = auth_tokens[auth_token]['guild_id']
        guild_config = get_server_config(guild_id, "webverify_config", {})
        guild_country_config = guild_config.get('webverify_country_alert', {}) if guild_config else {}
        if not guild_config:
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.not_configured"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        if not guild_config.get('enabled', False):
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.disabled"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        remoteip = request.headers.get('CF-Connecting-IP') or \
               request.headers.get('X-Forwarded-For') or \
               request.remote_addr
        guild = bot.get_guild(guild_id)
        if not guild:
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.guild_not_found"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        member = guild.get_member(int(user_id))

        if method != guild_config.get('captcha_type'):
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.method_mismatch"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        
        if not is_valid_md5(fingerprint):
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.bad_fingerprint"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))

        if method == 'turnstile':
            result = validate_turnstile(token, remoteip)
        elif method == 'recaptcha':
            result = validate_recaptcha(token, remoteip)
        else:
            if guild_config.get('captcha_type') != 'none':
                return render_template('ServerVerify.html', error=t("serverwebverify.web.err.invalid_method"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
            result = {'success': True}

        if result.get('success'):
            add_webverify_history(user_id, guild_id, remoteip, fingerprint)
            if member.get_role(guild_config.get('unverified_role_id')):
                asyncio.run_coroutine_threadsafe(member.remove_roles(discord.Object(id=guild_config.get('unverified_role_id')), reason=t("serverwebverify.audit.passed_web_verify")), bot.loop)
            log("User passed web verification", module_name="ServerWebVerify", user=member, guild=guild)
            try:
                if guild_country_config.get('enabled', False):
                    mode = guild_country_config.get('mode', 'blacklist')
                    location = get_ip_location(remoteip)
                    country = location.get('country', 'Unknown') if isinstance(location, dict) else 'Unknown'
                    alert_countries = guild_country_config.get('countries', [])
                    if (mode == 'blacklist' and country in alert_countries) or (mode == 'whitelist' and country not in alert_countries):
                        alert_channel_id = guild_country_config.get('alert_channel_id') or guild_country_config.get('channel_id')
                        alert_channel = guild.get_channel(alert_channel_id) if alert_channel_id else None
                        if alert_channel:
                            embed = discord.Embed(title=t("serverwebverify.embed.geo_alert_title"), color=0xFF0000)
                            embed.set_author(name=str(member), icon_url=member.display_avatar.url if member.display_avatar else None)
                            embed.add_field(name=t("serverwebverify.field.user_id"), value=str(member.id), inline=False)
                            embed.add_field(name=t("serverwebverify.field.region"), value=location.get('region', 'Unknown'))
                            embed.add_field(name=t("serverwebverify.field.city"), value=location.get('city', 'Unknown'))
                            embed.add_field(name=t("serverwebverify.field.country_code"), value=country)
                            embed.timestamp = datetime.now(timezone.utc)
                            asyncio.run_coroutine_threadsafe(alert_channel.send(embed=embed), bot.loop)
            except Exception as e:
                log(f"Failed to send geo-location alert: {e}", level=logging.ERROR, module_name="ServerWebVerify", guild=guild)
            # try to dm user
            try:
                asyncio.run_coroutine_threadsafe(member.send(t("serverwebverify.dm.verified_success", guild=guild.name)), bot.loop)
            except Exception as e:
                log(f"Failed to DM user {member} about their successful verification: {e}", level=logging.ERROR, module_name="ServerWebVerify", user=member, guild=guild)
            return render_template('ServerVerify.html', error=t("serverwebverify.web.msg.verify_success"), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))
        else:
            error_codes = result.get('error-codes', [])
            log(f"User failed web verification, error codes: {error_codes}", module_name="ServerWebVerify", user=member, guild=guild)
            return render_template('ServerVerify.html', error=t("serverwebverify.web.err.verify_failed", codes=", ".join(error_codes)), bot=bot, site_key_turnstile=config("webverify_turnstile_key"), site_key_recaptcha=config("webverify_recaptcha_key"), gtag=config("website_gtag", ""))

async def force_verify_user(guild: discord.Guild, user: Union[discord.Member, discord.User]):
    if not isinstance(user, discord.Member):
        return False, t("serverwebverify.err.members_only")
    guild_config = get_server_config(guild.id, "webverify_config")
    if not guild_config:
        return False, t("serverwebverify.err.not_configured")
    unverified_role_id = guild_config.get('unverified_role_id')
    if not unverified_role_id:
        return False, t("serverwebverify.err.no_unverified_role")
    if user.get_role(unverified_role_id):
        return False, t("serverwebverify.err.already_unverified")
    await user.add_roles(discord.Object(id=unverified_role_id), reason=t("serverwebverify.audit.force_assign_unverified"))
    return True, t("serverwebverify.msg.force_assign_success")

async def check_unlock_force_verify():
    await bot.wait_until_ready()
    try:
        while not bot.is_closed():
            for guild in bot.guilds:
                if bot.is_closed():
                    return
                until_timestamp = get_server_config(guild.id, "force_verify_until")
                if not until_timestamp:
                    continue
                if datetime.now(timezone.utc).timestamp() > until_timestamp:
                    set_server_config(guild.id, "force_verify_until", None)
                    log(f"Forced verification lifted for server {guild.name}", module_name="ServerWebVerify")
            await asyncio.sleep(60) # Check every minute
    except Exception as e:
        log(f"Error checking forced-verification unlock status: {e}", level=logging.ERROR, module_name="ServerWebVerify")
                

@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class ServerWebVerify(commands.GroupCog, name=app_commands.locale_str("webverify", i18n_key="cmd.serverwebverify.webverify.root.name"), description=app_commands.locale_str("Server web verification commands", i18n_key="cmd.serverwebverify.webverify.root.desc")):
    def __init__(self, bot):
        self.bot = bot
        self.force_ctx_menu = app_commands.ContextMenu(name=app_commands.locale_str("Force user verification", i18n_key="cmd.serverwebverify.ctx.force_verify"), callback=self.force_user_verify_context_menu)
        bot.tree.add_command(self.force_ctx_menu)
        self.manual_ctx_menu = app_commands.ContextMenu(name=app_commands.locale_str("Manually verify user", i18n_key="cmd.serverwebverify.ctx.manual_verify"), callback=self.manual_verify_user_context_menu)
        bot.tree.add_command(self.manual_ctx_menu)
    
    @app_commands.command(name=app_commands.locale_str("setup", i18n_key="cmd.serverwebverify.webverify.setup.name"), description=app_commands.locale_str("Set up web verification for this server", i18n_key="cmd.serverwebverify.webverify.setup.desc"))
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
                'title': t("serverwebverify.value.default_notify_title"),
                'message': t("serverwebverify.value.default_notify_message")
            }
        }
        set_server_config(guild_id, "webverify_config", default_config)
        await interaction.response.send_message(t("serverwebverify.msg.setup_done", command=get_command_mention('webverify', 'set_unverified_role')))

    @app_commands.command(name=app_commands.locale_str("quick_setup", i18n_key="cmd.serverwebverify.webverify.quick_setup.name"), description=app_commands.locale_str("Quickly set up web verification with an interactive wizard", i18n_key="cmd.serverwebverify.webverify.quick_setup.desc"))
    @app_commands.default_permissions(administrator=True)
    async def quick_setup(self, interaction: discord.Interaction):
        getting_started_module = sys.modules.get("gettingstarted")
        if getting_started_module is not None:
            await getting_started_module.start_webverify_quick_setup(interaction)
            return
        view = WebVerifySetupWizard(interaction, self.bot)
        await interaction.response.send_message(embed=await view.get_embed(), view=view)

    
    @app_commands.command(name=app_commands.locale_str("disable", i18n_key="cmd.serverwebverify.webverify.disable.name"), description=app_commands.locale_str("Disable web verification for this server", i18n_key="cmd.serverwebverify.webverify.disable.desc"))
    @app_commands.default_permissions(administrator=True)
    async def disable(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        guild_config = get_server_config(guild_id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message(t("serverwebverify.err.not_configured"))
            return
        guild_config['enabled'] = False
        set_server_config(guild_id, "webverify_config", guild_config)
        await interaction.response.send_message(t("serverwebverify.msg.disabled"))
    
    @app_commands.command(name=app_commands.locale_str("enable", i18n_key="cmd.serverwebverify.webverify.enable.name"), description=app_commands.locale_str("Enable web verification for this server", i18n_key="cmd.serverwebverify.webverify.enable.desc"))
    @app_commands.default_permissions(administrator=True)
    async def enable(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        guild_config = get_server_config(guild_id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message(t("serverwebverify.err.not_configured"))
            return
        guild_config['enabled'] = True
        set_server_config(guild_id, "webverify_config", guild_config)
        await interaction.response.send_message(t("serverwebverify.msg.enabled"))
    
    @app_commands.command(name=app_commands.locale_str("set_captcha", i18n_key="cmd.serverwebverify.webverify.set_captcha.name"), description=app_commands.locale_str("Set the CAPTCHA provider used for web verification", i18n_key="cmd.serverwebverify.webverify.set_captcha.desc"))
    @app_commands.describe(captcha_provider=app_commands.locale_str("Choose a CAPTCHA provider", i18n_key="cmd.serverwebverify.webverify.set_captcha.param.captcha_provider"))
    @app_commands.choices(captcha_provider=[
        app_commands.Choice(name=app_commands.locale_str("None", i18n_key="cmd.serverwebverify.webverify.set_captcha.choice.none"), value="none"),
        app_commands.Choice(name=app_commands.locale_str("Cloudflare Turnstile", i18n_key="cmd.serverwebverify.webverify.set_captcha.choice.turnstile"), value="turnstile"),
        app_commands.Choice(name=app_commands.locale_str("Google reCAPTCHA", i18n_key="cmd.serverwebverify.webverify.set_captcha.choice.recaptcha"), value="recaptcha")
    ])
    @app_commands.default_permissions(administrator=True)
    async def set_captcha(self, interaction: discord.Interaction, captcha_provider: str):
        guild_id = interaction.guild.id
        guild_config = get_server_config(guild_id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message(t("serverwebverify.err.not_configured"))
            return
        if captcha_provider not in ['none', 'turnstile', 'recaptcha']:
            await interaction.response.send_message(t("serverwebverify.err.invalid_captcha_provider"))
            return
        guild_config['captcha_type'] = captcha_provider
        set_server_config(guild_id, "webverify_config", guild_config)
        await interaction.response.send_message(t("serverwebverify.msg.captcha_set", provider=captcha_provider) if captcha_provider != 'none' else t("serverwebverify.msg.captcha_disabled"))
    
    @app_commands.command(name=app_commands.locale_str("set_unverified_role", i18n_key="cmd.serverwebverify.webverify.set_unverified_role.name"), description=app_commands.locale_str("Set the unverified-member role", i18n_key="cmd.serverwebverify.webverify.set_unverified_role.desc"))
    @app_commands.describe(role=app_commands.locale_str("The role for unverified members", i18n_key="cmd.serverwebverify.webverify.set_unverified_role.param.role"))
    @app_commands.default_permissions(administrator=True)
    async def set_unverified_role(self, interaction: discord.Interaction, role: discord.Role):
        guild_id = interaction.guild.id
        guild_config = get_server_config(guild_id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message(t("serverwebverify.err.not_configured"))
            return
        guild_config['unverified_role_id'] = role.id
        set_server_config(guild_id, "webverify_config", guild_config)
        await interaction.response.send_message(t("serverwebverify.msg.unverified_role_set", role=role.name))
    
    @app_commands.command(name=app_commands.locale_str("status", i18n_key="cmd.serverwebverify.webverify.status.name"), description=app_commands.locale_str("View this server's web verification status", i18n_key="cmd.serverwebverify.webverify.status.desc"))
    async def status(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        guild_config = get_server_config(guild_id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message(t("serverwebverify.err.not_configured"))
            return
        status_msg = t(
            "serverwebverify.msg.status",
            enabled=t("serverwebverify.value.enabled") if guild_config.get('enabled', False) else t("serverwebverify.value.disabled"),
            captcha_type=guild_config.get('captcha_type') or t("serverwebverify.value.unset"),
            role_id=guild_config.get('unverified_role_id') or t("serverwebverify.value.unset"),
        )
        await interaction.response.send_message(status_msg)
    
    @app_commands.command(name=app_commands.locale_str("verify_notify", i18n_key="cmd.serverwebverify.webverify.verify_notify.name"), description=app_commands.locale_str("Configure how verification notices are sent", i18n_key="cmd.serverwebverify.webverify.verify_notify.desc"))
    @app_commands.describe(type=app_commands.locale_str("How to notify", i18n_key="cmd.serverwebverify.webverify.verify_notify.param.type"), channel=app_commands.locale_str("The channel for verification messages", i18n_key="cmd.serverwebverify.webverify.verify_notify.param.channel"), title=app_commands.locale_str("Custom embed title", i18n_key="cmd.serverwebverify.webverify.verify_notify.param.title"), message=app_commands.locale_str("Custom verification message", i18n_key="cmd.serverwebverify.webverify.verify_notify.param.message"))
    @app_commands.choices(type=[
        app_commands.Choice(name=app_commands.locale_str("In the channel", i18n_key="cmd.serverwebverify.webverify.verify_notify.choice.channel"), value="channel"),
        app_commands.Choice(name=app_commands.locale_str("Direct message", i18n_key="cmd.serverwebverify.webverify.verify_notify.choice.dm"), value="dm"),
        app_commands.Choice(name=app_commands.locale_str("Both", i18n_key="cmd.serverwebverify.webverify.verify_notify.choice.both"), value="both")
    ])
    @app_commands.default_permissions(administrator=True)
    async def verify_notify(self, interaction: discord.Interaction, type: str = "channel", channel: discord.TextChannel = None, title: str = None, message: str = None):
        guild_id = interaction.guild.id
        guild_config = get_server_config(guild_id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message(t("serverwebverify.err.not_configured"))
            return
        if title is None:
            title = t("serverwebverify.value.default_notify_title")
        if message is None:
            message = t("serverwebverify.value.default_notify_message")
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
            verify_button = discord.ui.Button(label=t("serverwebverify.value.go_verify_btn"), url=verify_url)
            view = discord.ui.View()
            view.add_item(verify_button)
            embed = discord.Embed(title=title, description=message, color=0x00ff00)
            await channel.send(embed=embed, view=view)
            await interaction.response.send_message(t("serverwebverify.msg.notify_sent", channel=channel.mention))
        elif type == "dm":
            await interaction.response.send_message(t("serverwebverify.msg.notify_dm_set"))
    
    @app_commands.command(name=app_commands.locale_str("check_relation", i18n_key="cmd.serverwebverify.webverify.check_relation.name"), description=app_commands.locale_str("Check a user's related accounts", i18n_key="cmd.serverwebverify.webverify.check_relation.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to check", i18n_key="cmd.serverwebverify.webverify.check_relation.param.user"))
    @app_commands.default_permissions(administrator=True)
    async def check_relation(self, interaction: discord.Interaction, user: discord.User):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Get relation_id for the user
            cursor.execute('SELECT relation_id FROM webverify_user_relation WHERE user_id = ?', (user.id,))
            result = cursor.fetchone()
            
            if not result:
                await interaction.response.send_message(t("serverwebverify.err.no_relation_data", user=user.mention), ephemeral=True)
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
            
            embed = discord.Embed(title=t("serverwebverify.embed.relation_title", user=user.name), color=0xff0000)
            embed.add_field(name=t("serverwebverify.field.relation_id"), value=f"`{relation_id}`", inline=False)
            embed.add_field(name=t("serverwebverify.field.related_accounts", count=len(related_users_mentions)), value="\n".join(related_users_mentions), inline=False)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("relation_action", i18n_key="cmd.serverwebverify.webverify.relation_action.name"), description=app_commands.locale_str("Act on a user and their related accounts", i18n_key="cmd.serverwebverify.webverify.relation_action.desc"))
    @app_commands.describe(user=app_commands.locale_str("Choose a user", i18n_key="cmd.serverwebverify.webverify.relation_action.param.user"), action=app_commands.locale_str("The action to run (same format as !moderate)", i18n_key="cmd.serverwebverify.webverify.relation_action.param.action"))
    @app_commands.default_permissions(administrator=True)
    async def relation_action(self, interaction: discord.Interaction, user: discord.Member, action: str):
        if "Moderate" not in modules:
            await interaction.response.send_message(t("serverwebverify.err.moderate_not_enabled"), ephemeral=True)
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
                await interaction.followup.send(t("serverwebverify.err.no_relation_data", user=user.mention))
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
                        results.append(t("serverwebverify.value.member_not_found_skip", uid=uid))
                        continue

                # 檢查身份組階層，避免對比執行者或機器人身份組更高的人執行操作
                ok, msg = Moderate.check_member_hierarchy(interaction.user, member, interaction.guild.me)
                if not ok:
                    results.append(t("serverwebverify.value.hierarchy_skip", member=member.mention, reason=msg))
                    continue

                logs = await Moderate.do_action_str(action, interaction.guild, member, None, moderator=interaction.user)
                if len(logs) == 0:
                    results.append(t("serverwebverify.value.no_action", member=member.mention))
                else:
                    results.append(f"{member.mention} - {'; '.join(logs)}")

            except Exception as e:
                results.append(t("serverwebverify.value.action_error", uid=uid, error=str(e)))
        
        # Split results into chunks to avoid message length limits
        output = t("serverwebverify.msg.relation_action_header", user=user.mention)
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
    
    @app_commands.command(name=app_commands.locale_str("autorole", i18n_key="cmd.serverwebverify.webverify.autorole.name"), description=app_commands.locale_str("Automatically assign the unverified role to new members", i18n_key="cmd.serverwebverify.webverify.autorole.desc"))
    @app_commands.describe(enable=app_commands.locale_str("Enable or disable automatic role assignment", i18n_key="cmd.serverwebverify.webverify.autorole.param.enable"), trigger=app_commands.locale_str("Condition for assigning the role", i18n_key="cmd.serverwebverify.webverify.autorole.param.trigger"))
    @app_commands.choices(trigger=[
        app_commands.Choice(name=app_commands.locale_str("Always assign", i18n_key="cmd.serverwebverify.webverify.autorole.choice.always"), value="always"),
        app_commands.Choice(name=app_commands.locale_str("Account too new", i18n_key="cmd.serverwebverify.webverify.autorole.choice.age_check"), value="age_check"),
        app_commands.Choice(name=app_commands.locale_str("No verification history", i18n_key="cmd.serverwebverify.webverify.autorole.choice.no_history"), value="no_history"),
        app_commands.Choice(name=app_commands.locale_str("Account was flagged before", i18n_key="cmd.serverwebverify.webverify.autorole.choice.has_flagged_history"), value="has_flagged_history"),
        app_commands.Choice(name=app_commands.locale_str("Left the server before", i18n_key="cmd.serverwebverify.webverify.autorole.choice.left_guild_before"), value="left_guild_before")
    ])
    @app_commands.default_permissions(administrator=True)
    async def autorole(self, interaction: discord.Interaction, enable: bool, trigger: str):
        guild_id = interaction.guild.id
        guild_config = get_server_config(guild_id, "webverify_config")
        if not guild_config:
            await interaction.response.send_message(t("serverwebverify.err.not_configured"))
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
        status = t("serverwebverify.value.enabled_suffix") if enable else t("serverwebverify.value.disabled_suffix")
        await interaction.response.send_message(t("serverwebverify.msg.autorole_set", status=status, trigger=guild_config['autorole_trigger']))
    
    @app_commands.command(name=app_commands.locale_str("create_unverified_role", i18n_key="cmd.serverwebverify.webverify.create_unverified_role.name"), description=app_commands.locale_str("Automatically create and configure the unverified-member role", i18n_key="cmd.serverwebverify.webverify.create_unverified_role.desc"))
    @app_commands.describe(name=app_commands.locale_str("Name for the unverified-member role", i18n_key="cmd.serverwebverify.webverify.create_unverified_role.param.name"))
    @app_commands.default_permissions(administrator=True)
    async def create_unverified_role(self, interaction: discord.Interaction, name: str = None):
        if name is None:
            name = t("serverwebverify.value.default_unverified_role_name")
        guild = interaction.guild
        existing_role = discord.utils.get(guild.roles, name=name)
        if existing_role:
            await interaction.response.send_message(t("serverwebverify.err.role_already_exists", name=name))
            return
        await interaction.response.defer()
        unverified_role = await guild.create_role(name=name, reason=t("serverwebverify.audit.create_unverified_role"))
        # try to set role permissions to deny send messages in all text channels
        for channel in guild.text_channels:
            await channel.set_permissions(unverified_role, send_messages=False, connect=False, create_public_threads=False, create_private_threads=False, reason=t("serverwebverify.audit.set_unverified_role_permissions"))
        guild_config = get_server_config(guild.id, "webverify_config")
        if not guild_config:
            guild_config = {}
        guild_config['unverified_role_id'] = unverified_role.id
        set_server_config(guild.id, "webverify_config", guild_config)
        await interaction.followup.send(t("serverwebverify.msg.unverified_role_created", name=name))
    
    @app_commands.command(name=app_commands.locale_str("minage", i18n_key="cmd.serverwebverify.webverify.minage.name"), description=app_commands.locale_str("Define the minimum account age", i18n_key="cmd.serverwebverify.webverify.minage.desc"))
    @app_commands.describe(min_age=app_commands.locale_str("Minimum account age (days)", i18n_key="cmd.serverwebverify.webverify.minage.param.min_age"))
    @app_commands.default_permissions(administrator=True)
    async def minage(self, interaction: discord.Interaction, min_age: int):
        guild_config = get_server_config(interaction.guild.id, "webverify_config")
        if not guild_config:
            guild_config = {}
        guild_config['min_age'] = min_age
        set_server_config(interaction.guild.id, "webverify_config", guild_config)
        await interaction.response.send_message(t("serverwebverify.msg.min_age_set", count=min_age, days=min_age))
    
    @app_commands.command(name=app_commands.locale_str("country-alert", i18n_key="cmd.serverwebverify.webverify.country_alert.name"), description=app_commands.locale_str("Configure verification region alerts", i18n_key="cmd.serverwebverify.webverify.country_alert.desc"))
    @app_commands.describe(
        enable=app_commands.locale_str("Enable or disable region alerts", i18n_key="cmd.serverwebverify.webverify.country_alert.param.enable"),
        mode=app_commands.locale_str("Alert mode", i18n_key="cmd.serverwebverify.webverify.country_alert.param.mode"),
        countries=app_commands.locale_str("Country codes, comma separated (e.g. US,CN,RU)", i18n_key="cmd.serverwebverify.webverify.country_alert.param.countries"),
        channel=app_commands.locale_str("The channel that receives alerts", i18n_key="cmd.serverwebverify.webverify.country_alert.param.channel")
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name=app_commands.locale_str("Blocklist mode", i18n_key="cmd.serverwebverify.webverify.country_alert.choice.blocklist"), value="blocklist"),
        app_commands.Choice(name=app_commands.locale_str("Allowlist mode", i18n_key="cmd.serverwebverify.webverify.country_alert.choice.allowlist"), value="allowlist")
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
        status = t("serverwebverify.value.enabled_suffix") if enable else t("serverwebverify.value.disabled_suffix")
        await interaction.response.send_message(t("serverwebverify.msg.country_alert_set", status=status, mode=mode, countries=", ".join(country_list), channel=channel.mention))
    
    @app_commands.command(name=app_commands.locale_str("manual-check-country", i18n_key="cmd.serverwebverify.webverify.manual_check_country.name"), description=app_commands.locale_str("Manually check a user's region", i18n_key="cmd.serverwebverify.webverify.manual_check_country.desc"))
    @app_commands.describe(user=app_commands.locale_str("Choose a user", i18n_key="cmd.serverwebverify.webverify.manual_check_country.param.user"))
    @app_commands.default_permissions(administrator=True)
    async def manual_check_country(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer()
        guild_config = get_server_config(interaction.guild.id, "webverify_config")
        guild_config = guild_config.get('webverify_country_alert') if guild_config else None
        if not guild_config or not guild_config.get('enabled', False):
            await interaction.followup.send(t("serverwebverify.err.geo_alert_disabled"))
            return
        await interaction.followup.send(t("serverwebverify.value.please_wait"))
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
            await interaction.followup.send(t("serverwebverify.err.no_verify_history"))
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
                report_lines.append(t("serverwebverify.value.geo_report_line", user=user_mention, country=country, timestamp=timestamp))
        if not report_lines:
            await interaction.followup.send(t("serverwebverify.msg.no_geo_anomalies"))
            return
        for i in range(0, len(report_lines), 20):
            chunk = report_lines[i:i+20]
            await interaction.followup.send("```" + "\n".join(chunk) + "```")
    
    @app_commands.command(name=app_commands.locale_str("force-verify", i18n_key="cmd.serverwebverify.webverify.force_verify.name"), description=app_commands.locale_str("Force a user to verify", i18n_key="cmd.serverwebverify.webverify.force_verify.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to force-verify", i18n_key="cmd.serverwebverify.webverify.force_verify.param.user"))
    @app_commands.default_permissions(administrator=True)
    async def force_verify(self, interaction: discord.Interaction, user: discord.Member):
        success, message = await force_verify_user(interaction.guild, user)
        if success:
            await interaction.response.send_message(t("serverwebverify.msg.force_verify_success", user=user.mention))
        else:
            await interaction.response.send_message(t("serverwebverify.err.force_verify_failed", user=user.mention, message=message))
    
    @app_commands.command(name=app_commands.locale_str("start-force-verify", i18n_key="cmd.serverwebverify.webverify.start_force_verify.name"), description=app_commands.locale_str("Start forcing verification for all newly joined members", i18n_key="cmd.serverwebverify.webverify.start_force_verify.desc"))
    @app_commands.describe(duration=app_commands.locale_str("How long forced verification lasts (?h?m?s...)", i18n_key="cmd.serverwebverify.webverify.start_force_verify.param.duration"))
    @app_commands.default_permissions(administrator=True)
    async def start_force_verify(self, interaction: discord.Interaction, duration: str):
        try:
            seconds = timestr_to_seconds(duration)
            until_timestamp = datetime.now(timezone.utc).timestamp() + seconds
            set_server_config(interaction.guild.id, "force_verify_until", until_timestamp)
            await interaction.response.send_message(t("serverwebverify.msg.force_verify_started", duration=get_time_text(seconds)))
        except ValueError:
            await interaction.response.send_message(t("serverwebverify.err.invalid_duration_format"))
    
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        async with i18n.guild_scope(member.guild.id, user_id=member.id):
            await self._on_member_join_impl(member)

    async def _on_member_join_impl(self, member: discord.Member):
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
            until_timestamp = get_server_config(member.guild.id, "force_verify_until")
            if until_timestamp and datetime.now(timezone.utc).timestamp() < until_timestamp:
                assign_role = True
                break
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
                is_left_before = get_user_data(member.guild.id, member.id, "left_guild", False)
                if is_left_before:
                    assign_role = True

        if assign_role:
            await asyncio.sleep(.5) # wait for discord to update member's roles if they have any join roles, to avoid conflicts
            await member.add_roles(discord.Object(id=unverified_role_id), reason=t("serverwebverify.audit.autorole_assign"))
            notify_type = guild_config.get('notify', {}).get('type', 'dm')
            log(f"Automatically assigned the unverified role to new member {member}", module_name="ServerWebVerify", guild=member.guild, user=member)
            # if notify_type in ['dm', 'both']:
            #     notify_title = guild_config.get('notify', {}).get('title')
            #     notify_message = guild_config.get('notify', {}).get('message')
            #     embed = discord.Embed(title=notify_title, description=notify_message, color=0x00ff00)
            #     embed.set_footer(text=member.guild.name, icon_url=member.guild.icon.url if member.guild.icon else None)
            #     verify_url = f"https://discord.com/oauth2/authorize?client_id={bot.application.id}&response_type=code&scope=identify&prompt=none&{urlencode({'redirect_uri': config('webverify_url')})}&state={member.guild.id}"
            #     verify_button = discord.ui.Button(label="前往驗證", url=verify_url)
            #     view = discord.ui.View()
            #     view.add_item(verify_button)
            #     await member.send(embed=embed, view=view)
    
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        set_user_data(member.guild.id, member.id, "left_guild", "True")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        async with i18n.guild_scope(after.guild.id, user_id=after.id):
            await self._on_member_update_impl(before, after)

    async def _on_member_update_impl(self, before: discord.Member, after: discord.Member):
        if before.roles != after.roles:
            guild_config = get_server_config(before.guild.id, "webverify_config")
            if not guild_config:
                return
            unverified_role_id = guild_config.get('unverified_role_id')
            if not unverified_role_id:
                return
            before_role_ids = {role.id for role in before.roles}
            after_role_ids = {role.id for role in after.roles}
            added_role_ids = after_role_ids - before_role_ids
            if unverified_role_id not in added_role_ids:
                return
            # if dm enabled, send dm
            notify_type = guild_config.get('notify', {}).get('type', 'dm')
            if notify_type in ['dm', 'both']:
                notify_title = guild_config.get('notify', {}).get('title')
                notify_message = guild_config.get('notify', {}).get('message')
                embed = discord.Embed(title=notify_title, description=notify_message, color=0x00ff00)
                embed.set_footer(text=after.guild.name, icon_url=after.guild.icon.url if after.guild.icon else None)
                verify_url = f"https://discord.com/oauth2/authorize?client_id={bot.application.id}&response_type=code&scope=identify&prompt=none&{urlencode({'redirect_uri': config('webverify_url')})}&state={after.guild.id}"
                verify_button = discord.ui.Button(label=t("serverwebverify.value.go_verify_btn"), url=verify_url)
                view = discord.ui.View()
                view.add_item(verify_button)
                try:
                    await after.send(embed=embed, view=view)
                except Exception as e:
                    log(f"Failed to DM user {after} about their verification-status change: {e}", level=logging.ERROR, module_name="ServerWebVerify", user=after, guild=after.guild)
    
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.default_permissions(administrator=True)
    async def force_user_verify_context_menu(self, interaction: discord.Interaction, user: Union[discord.Member, discord.User]):
        await interaction.response.defer(ephemeral=True)
        success, message = await force_verify_user(interaction.guild, user)
        await interaction.followup.send(message, ephemeral=True)
    
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.default_permissions(administrator=True)
    async def manual_verify_user_context_menu(self, interaction: discord.Interaction, user: Union[discord.Member, discord.User]):
        await interaction.response.defer(ephemeral=True)
        if not isinstance(user, discord.Member):
            await interaction.followup.send(t("serverwebverify.err.members_only"), ephemeral=True)
            return
        guild_config = get_server_config(interaction.guild.id, "webverify_config")
        if not guild_config:
            await interaction.followup.send(t("serverwebverify.err.not_configured"), ephemeral=True)
            return
        unverified_role_id = guild_config.get('unverified_role_id')
        if not unverified_role_id:
            await interaction.followup.send(t("serverwebverify.err.no_unverified_role"), ephemeral=True)
            return
        if not user.get_role(unverified_role_id):
            await interaction.followup.send(t("serverwebverify.err.not_unverified"), ephemeral=True)
            return
        await user.remove_roles(discord.Object(id=unverified_role_id), reason=t("serverwebverify.audit.manual_remove_unverified"))
        await interaction.followup.send(t("serverwebverify.msg.manually_verified", user=user.mention), ephemeral=True)
        # try to send
        try:
            embed = discord.Embed(title=t("serverwebverify.embed.status_update_title"), description=t("serverwebverify.embed.manually_verified_desc"), color=0x00ff00)
            embed.set_footer(text=user.guild.name, icon_url=user.guild.icon.url if user.guild.icon else None)
            embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
            await user.send(embed=embed)
        except Exception as e:
            log(f"Failed to DM user {user} about their verification-status change: {e}", level=logging.ERROR, module_name="ServerWebVerify", user=user, guild=user.guild)

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
            'notify': {'type': 'dm', 'channel_id': None, 'title': t("serverwebverify.value.default_notify_title"), 'message': t("serverwebverify.value.default_notify_message")}
        }
        self.step = 1
        self.select = None
        self.update_components()
    
    async def on_timeout(self):
        with i18n.use_locale(i18n.resolve_locale(user_id=self.interaction.user.id, guild_id=self.guild.id)):
            embed = discord.Embed(
                title=t("serverwebverify.wizard.title"),
                description=t("serverwebverify.wizard.timed_out_desc"),
                color=0xff0000,
            )
        await self.interaction.edit_original_response(embed=embed, view=None)
        self.stop()

    def update_components(self):
        self.clear_items()
        if self.step == 1:
            # Step 1: Captcha
            select = discord.ui.Select(placeholder=t("serverwebverify.wizard.select_captcha_ph"), options=[
                discord.SelectOption(label=t("serverwebverify.wizard.captcha_none_label"), value="none", description=t("serverwebverify.wizard.captcha_none_desc")),
                discord.SelectOption(label="Cloudflare Turnstile", value="turnstile", description=t("serverwebverify.wizard.captcha_turnstile_desc")),
                discord.SelectOption(label="Google reCAPTCHA", value="recaptcha", description=t("serverwebverify.wizard.captcha_recaptcha_desc"))
            ])
            select.callback = self.on_captcha_select
            self.select = select
            self.add_item(select)
        
        elif self.step == 2:
            # Step 2: Role
            btn_create = discord.ui.Button(label=t("serverwebverify.wizard.auto_create_role_btn"), style=discord.ButtonStyle.green, custom_id="create_role")
            btn_create.callback = self.on_create_role
            self.add_item(btn_create)

            select_role = discord.ui.RoleSelect(placeholder=t("serverwebverify.wizard.select_existing_role_ph"), min_values=1, max_values=1)
            select_role.callback = self.on_select_role
            self.select = select_role
            self.add_item(select_role)

        elif self.step == 3:
            # Step 3: Autorole
            btn_toggle = discord.ui.Button(
                label=t("serverwebverify.wizard.autorole_toggle_btn", state=t("serverwebverify.value.enabled") if self.config.get('autorole_enabled') else t("serverwebverify.value.disabled")),
                style=discord.ButtonStyle.success if self.config.get('autorole_enabled') else discord.ButtonStyle.danger
            )
            btn_toggle.callback = self.on_toggle_autorole
            self.add_item(btn_toggle)

            if self.config.get('autorole_enabled'):
                trigger_options = [
                    discord.SelectOption(label=t("serverwebverify.wizard.trigger.always"), value="always"),
                    discord.SelectOption(label=t("serverwebverify.wizard.trigger.age_check"), value="age_check"),
                    discord.SelectOption(label=t("serverwebverify.wizard.trigger.no_history"), value="no_history"),
                    discord.SelectOption(label=t("serverwebverify.wizard.trigger.has_flagged_history"), value="has_flagged_history"),
                    discord.SelectOption(label=t("serverwebverify.wizard.trigger.left_guild_before"), value="left_guild_before")
                ]
                # Pre-select current triggers
                current_triggers = self.config.get('autorole_trigger', 'always').split('+')
                for opt in trigger_options:
                    if opt.value in current_triggers:
                        opt.default = True
                
                select_trigger = discord.ui.Select(placeholder=t("serverwebverify.wizard.select_trigger_ph"), min_values=1, max_values=len(trigger_options), options=trigger_options)
                select_trigger.callback = self.on_select_trigger
                self.select = select_trigger
                self.add_item(select_trigger)

            btn_next = discord.ui.Button(label=t("common.btn.next_step"), style=discord.ButtonStyle.primary)
            btn_next.callback = self.on_next_step
            self.add_item(btn_next)

        elif self.step == 4:
            # Step 4: Notify
            select_type = discord.ui.Select(placeholder=t("serverwebverify.wizard.select_notify_type_ph"), options=[
                discord.SelectOption(label=t("serverwebverify.wizard.notify_dm_label"), value="dm"),
                discord.SelectOption(label=t("serverwebverify.wizard.notify_channel_label"), value="channel"),
                discord.SelectOption(label=t("serverwebverify.wizard.notify_both_label"), value="both")
            ])
            # Set default
            if self.config.get('notify', {}).get('type') == 'dm':
                select_type.options[0].default = True
            elif self.config.get('notify', {}).get('type') == 'channel':
                select_type.options[1].default = True
            elif self.config.get('notify', {}).get('type') == 'both':
                select_type.options[2].default = True
            else:
                select_type.options[0].default = True
            
            select_type.callback = self.on_notify_type_select
            self.add_item(select_type)

            if self.config.get('notify', {}).get('type') == 'channel':
                select_channel = discord.ui.ChannelSelect(
                    placeholder=t("serverwebverify.wizard.select_notify_channel_ph"), 
                    channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                    min_values=1, max_values=1
                )
                select_channel.callback = self.on_channel_select
                self.select = select_channel
                self.add_item(select_channel)

            btn_finish = discord.ui.Button(label=t("serverwebverify.wizard.finish_btn"), style=discord.ButtonStyle.success)
            btn_finish.callback = self.on_finish
            self.add_item(btn_finish)

    async def get_embed(self):
        embed = discord.Embed(title=t("serverwebverify.wizard.step_title", step=self.step), color=0x00ff00)
        if self.step == 1:
            embed.description = t("serverwebverify.wizard.step1_desc")
            embed.add_field(name=t("serverwebverify.wizard.current_setting"), value=self.config.get('captcha_type') or t("serverwebverify.value.unset"))
        elif self.step == 2:
            embed.description = t("serverwebverify.wizard.step2_desc")
            role_id = self.config.get('unverified_role_id')
            role = self.guild.get_role(role_id) if role_id else None
            embed.add_field(name=t("serverwebverify.wizard.current_setting"), value=role.mention if role else t("serverwebverify.value.unset"))
        elif self.step == 3:
            embed.description = t("serverwebverify.wizard.step3_desc")
            embed.add_field(name=t("serverwebverify.wizard.feature_status"), value=t("serverwebverify.value.enabled") if self.config.get('autorole_enabled') else t("serverwebverify.value.disabled"))
            embed.add_field(name=t("serverwebverify.wizard.trigger_condition"), value=self.config.get('autorole_trigger', 'always'))
        elif self.step == 4:
            embed.description = t("serverwebverify.wizard.step4_desc")
            notify = self.config.get('notify', {})
            embed.add_field(name=t("serverwebverify.wizard.notify_type"), value=notify.get('type', 'dm'))
            if notify.get('type') == 'channel':
                chan = self.guild.get_channel(notify.get('channel_id'))
                embed.add_field(name=t("serverwebverify.wizard.notify_channel"), value=chan.mention if chan else t("serverwebverify.value.not_selected"))
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
        selected_type = interaction.data.get('values', [None])[0]
        if not selected_type:
            await interaction.response.defer()
            return
        self.config['notify']['type'] = selected_type
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
                verify_button = discord.ui.Button(label=t("serverwebverify.value.go_verify_btn"), url=verify_url)
                view = discord.ui.View()
                view.add_item(verify_button)
                embed = discord.Embed(title=notify.get('title') or t("serverwebverify.value.default_notify_title"), description=notify.get('message') or t("serverwebverify.value.default_notify_message"), color=0x00ff00)
                try:
                    await channel.send(embed=embed, view=view)
                    msg_extras = t("serverwebverify.wizard.notify_sent_suffix", channel=channel.mention)
                except Exception as e:
                    msg_extras = t("serverwebverify.wizard.notify_send_failed_suffix", channel=channel.mention, error=str(e))

        embed = discord.Embed(title=t("serverwebverify.wizard.setup_complete_title"), color=0x00ff00)
        embed.description = t("serverwebverify.wizard.setup_complete_desc") + msg_extras
        embed.add_field(name="CAPTCHA", value=self.config.get('captcha_type'))
        
        role = self.guild.get_role(self.config.get('unverified_role_id'))
        embed.add_field(name=t("serverwebverify.wizard.unverified_role_field"), value=role.mention if role else "None")
        
        embed.add_field(name=t("serverwebverify.wizard.autorole_field"), value=f"{t('serverwebverify.value.enabled') if self.config.get('autorole_enabled') else t('serverwebverify.value.disabled')} ({self.config.get('autorole_trigger')})")
        embed.add_field(name=t("serverwebverify.wizard.notify_method_field"), value=notify.get('type'))
        
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()


class RoleCreationModal(i18n.I18nModal, title=i18n.K("serverwebverify.modal.create_role_title")):
    def __init__(self, wizard_view: WebVerifySetupWizard):
        super().__init__()
        self.wizard_view = wizard_view
        self.role_name = discord.ui.TextInput(
            label=t("serverwebverify.field.role_name_label"),
            default=t("serverwebverify.value.default_unverified_role_name"),
            required=True,
        )
        self.add_item(self.role_name)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild = interaction.guild
        name = self.role_name.value

        # Logic from create_unverified_role
        unverified_role = await guild.create_role(name=name, reason=t("serverwebverify.audit.wizard_create_unverified_role"))
        for channel in guild.text_channels:
            try:
                await channel.set_permissions(unverified_role, send_messages=False, connect=False, create_public_threads=False, create_private_threads=False, reason=t("serverwebverify.audit.set_unverified_role_permissions"))
            except:
                pass # Ignore errors if bot lacks permission
        
        self.wizard_view.config['unverified_role_id'] = unverified_role.id
        self.wizard_view.step = 3
        self.wizard_view.update_components()
        await interaction.followup.edit_message(message_id=interaction.message.id, embed=await self.wizard_view.get_embed(), view=self.wizard_view)


init_db()

asyncio.run(bot.add_cog(ServerWebVerify(bot)))
