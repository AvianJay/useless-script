import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, start_bot, get_user_data, set_user_data, get_all_user_data, get_server_config, set_server_config, modules, config, get_command_mention
from datetime import datetime, timezone, timedelta
import asyncio
from typing import Optional
import re
import emoji
import sqlite3
import io
from logger import log
import logging

if "Moderate" in modules:
    import Moderate
else:
    log("Moderate module not found", level=logging.ERROR, module_name="AutoModerate")


all_settings = [
    "escape_punish-punishment",
    "escape_punish-duration",
    "too_many_h1-max_length",
    "too_many_h1-action",
    "too_many_emojis-max_emojis",
    "too_many_emojis-action",
    "scamtrap-channel_id",
    "scamtrap-action",
    "anti_uispam-max_count",
    "anti_uispam-time_window",
    "anti_uispam-action",
]

# 用於追蹤 user install spam 的記憶體字典
# 結構: {guild_id: {user_id: [timestamp1, timestamp2, ...]}}
_uispam_tracker: dict[int, dict[int, list[datetime]]] = {}

async def settings_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=app_commands.locale_str(key), value=key)
        for key in all_settings if current.lower() in key.lower()
    ][:25]  # Discord 限制最多 25 個選項

async def do_action_str(action: str, guild: Optional[discord.Guild] = None, user: Optional[discord.Member] = None, message: Optional[discord.Message] = None):
    moderator = bot.user
    # if user is none just check if action is valid
    action_parts = action.split(",")
    action_parts = [a.strip() for a in action_parts]
    if len(action_parts) > 5:
        return ["錯誤：一次只能執行最多5個動作。"]
    logs = []
    last_reason = "自動管理執行"
    actions = []
    for a in action_parts:
        cmd = a.split(" ")
        if cmd[0] == "ban":
            # ban <reason> <delete_messages> <duration>
            if len(cmd) == 1:
                cmd.append("0s")
            if len(cmd) == 2:
                cmd.append("0s")
            if len(cmd) == 3:
                cmd.append(last_reason)

            duration_seconds = Moderate.timestr_to_seconds(cmd[1]) if cmd[1] != "0" else 0
            delete_messages = Moderate.timestr_to_seconds(cmd[2]) if cmd[2] != "0" else 0
            cmd.pop(0)  # remove "ban"
            cmd.pop(0)  # remove duration
            cmd.pop(0)  # remove delete_messages
            reason = " ".join(cmd)
            last_reason = reason
            logs.append(f"封禁用戶，原因: {reason}，持續秒數: {duration_seconds}秒，刪除訊息時間: {delete_messages}秒")
            if user:
                await Moderate.ban_user(guild, user, reason=reason, duration=duration_seconds, delete_message_seconds=delete_messages)
            actions.append({"action": "ban", "duration": duration_seconds, "reason": reason})
        elif cmd[0] == "kick":
            # kick <reason>
            if len(cmd) == 1:
                cmd.append(last_reason)
            cmd.pop(0)  # remove "kick"
            reason = " ".join(cmd)
            logs.append(f"踢出用戶，原因: {reason}")
            if user:
                await user.kick(reason=reason)
            actions.append({"action": "kick", "reason": reason})
        elif cmd[0] == "mute" or cmd[0] == "timeout":
            # mute <duration> <reason>
            if len(cmd) == 1:
                cmd.append("10m")
            if len(cmd) == 2:
                cmd.append(last_reason)
            duration_seconds = Moderate.timestr_to_seconds(cmd[1]) if cmd[1] != "0" else 0
            cmd.pop(0)  # remove "mute" or "timeout"
            cmd.pop(0)  # remove duration
            reason = " ".join(cmd) if cmd else last_reason
            logs.append(f"禁言用戶，原因: {reason}，持續秒數: {duration_seconds}秒")
            if user:
                await user.timeout(datetime.now(timezone.utc) + timedelta(seconds=duration_seconds), reason=reason)
            actions.append({"action": "mute", "duration": duration_seconds, "reason": reason})
        elif cmd[0] == "unban":
            # unban <reason>
            if len(cmd) == 1:
                cmd.append(last_reason)
            cmd.pop(0)  # remove "unban"
            reason = " ".join(cmd)
            last_reason = reason
            logs.append(f"解封用戶，原因: {reason}")
            if guild and user:
                try:
                    await guild.unban(user, reason=reason)
                    set_user_data(guild.id, user.id, "unban_time", None)
                except Exception as e:
                    log(f"解封用戶 {user} 時發生錯誤：{e}", level=logging.ERROR, module_name="Moderate", guild=guild)
            actions.append({"action": "unban", "reason": reason})
        elif cmd[0] == "unmute" or cmd[0] == "untimeout":
            # unmute <reason>
            if len(cmd) == 1:
                cmd.append(last_reason)
            cmd.pop(0)  # remove "unmute" or "untimeout"
            reason = " ".join(cmd)
            logs.append(f"解除禁言用戶，原因: {reason}")
            if user:
                await user.timeout(None, reason=reason)
            actions.append({"action": "unmute", "reason": reason})
        elif cmd[0] == "delete" or cmd[0] == "delete_dm":
            # delete <warn_message>
            logs.append("刪除訊息")
            if message:
                await message.delete()
            if len(cmd) > 1:
                msg = cmd.copy()
                msg.pop(0)
                warn_message = " ".join(msg)
                warn_message = warn_message.replace("{user}", user.mention if user else "用戶")
                logs.append(f"並警告: {warn_message}")
                if cmd[0] == "delete_dm" and user:
                    await user.send(warn_message)
                elif message:
                    await message.channel.send(warn_message)
        elif cmd[0] == "warn" or cmd[0] == "warn_dm":
            # warn <warn_message>
            if len(cmd) == 1:
                cmd.append(f"{user.mention if user else '用戶'}，請注意你的行為。")
            msg = cmd.copy()
            msg.pop(0)
            warn_message = " ".join(msg)
            warn_message = warn_message.replace("{user}", user.mention if user else "用戶")
            logs.append(f"傳送警告訊息: {warn_message}")
            if cmd[0] == "warn_dm" and user:
                await user.send(warn_message)
            elif message:
                await message.reply(warn_message)
        elif cmd[0] == "send_mod_message" or cmd[0] == "smm":
            # send_mod_message
            if len(cmd) == 1:
                cmd.append("用戶被系統處置。")
            logs.append("傳送管理訊息")
            if guild and user and moderator:
                await Moderate.moderation_message_settings(None, user, moderator, actions, direct=True)
        elif cmd[0] == "force_verify":
            # force_verify <duration>
            if "ServerWebVerify" in modules:
                from ServerWebVerify import force_verify_user
                if user:
                    success, message = await force_verify_user(guild, user)
                    logs.append(message)
                if len(cmd) > 1:
                    duration_seconds = Moderate.timestr_to_seconds(cmd[1]) if cmd[1] != "0" else 0
                    until_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
                    logs.append(f"強制驗證持續秒數: {duration_seconds}秒")
                    set_server_config(guild.id, "force_verify_until", until_time.timestamp())
            else:
                logs.append("無法執行 force_verify，因為 ServerWebVerify 模組未找到")
    return logs


def parse_mention_to_id(mention: str) -> str:
    # 解析用戶、頻道或角色的提及格式，返回ID
    match = re.match(r"<@!?(\d+)>", mention)  # 用戶提及
    if match:
        return match.group(1)
    match = re.match(r"<#(\d+)>", mention)  # 頻道提及
    if match:
        return match.group(1)
    match = re.match(r"<@&(\d+)>", mention)  # 角色提及
    if match:
        return match.group(1)
    return mention  # 如果不是提及格式，直接返回原字符串
    


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class AutoModerate(commands.GroupCog, name=app_commands.locale_str("automod")):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()
        
    @app_commands.command(name=app_commands.locale_str("view"), description="查看自動管理設定")
    async def view_automod_settings(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        automod_settings = get_server_config(guild_id, "automod", {})
        if not automod_settings:
            await interaction.response.send_message("自動管理尚未啟用。", ephemeral=True)
            return

        embed = discord.Embed(title="自動管理設定", color=0x00ff00)
        desc = ""
        for key, value in automod_settings.items():
            desc += f"**{key}**:"
            for subkey, subvalue in value.items():
                desc += f"\n - {subkey}: {subvalue}"
            desc += "\n"
        embed.description = desc
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name=app_commands.locale_str("toggle"), description="啟用或停用自動管理設定")
    @app_commands.describe(setting="要啟用或停用的自動管理設定名稱", enable="是否啟用該設定")
    @app_commands.choices(
        setting=[
            app_commands.Choice(name="詐騙陷阱", value="scamtrap"),
            app_commands.Choice(name="逃避責任懲處", value="escape_punish"),
            app_commands.Choice(name="標題過多", value="too_many_h1"),
            app_commands.Choice(name="表情符號過多", value="too_many_emojis"),
            app_commands.Choice(name="用戶安裝應用程式濫用", value="anti_uispam"),
        ],
        enable=[
            app_commands.Choice(name="啟用", value="True"),
            app_commands.Choice(name="停用", value="False"),
        ]
    )
    async def toggle_automod_setting(self, interaction: discord.Interaction, setting: str, enable: str):
        guild_id = interaction.guild.id if interaction.guild else None
        automod_settings = get_server_config(guild_id, "automod", {})
        automod_settings.setdefault(setting, {})["enabled"] = (enable == "True")
        set_server_config(guild_id, "automod", automod_settings)
        await interaction.response.send_message(f"已將自動管理設定 '{setting}' 設為 {'啟用' if enable == 'True' else '停用'}。")
        
        if setting == "scamtrap" and enable == "True":
            # settings
            if "channel_id" not in automod_settings.get("scamtrap", {}):
                await interaction.followup.send(f"請注意，詐騙陷阱已啟用，但尚未設定頻道ID。請使用 {await get_command_mention('automod', 'settings')} 來設定頻道ID。", ephemeral=True)
            if "action" not in automod_settings.get("scamtrap", {}):
                await interaction.followup.send(f"請注意，詐騙陷阱已啟用，但尚未設定動作指令。請使用 {await get_command_mention('automod', 'settings')} 來設定動作指令。", ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("settings"), description="設定自動管理選項")
    @app_commands.describe(
        setting="要設定的自動管理選項",
        value="選項的值"
    )
    @app_commands.autocomplete(setting=settings_autocomplete)
    async def set_automod_setting(self, interaction: discord.Interaction, setting: str, value: str):
        guild_id = interaction.guild.id if interaction.guild else None
        automod_settings = get_server_config(guild_id, "automod", {})
        setting_base = setting.split("-")[0]
        setting_key = setting.split("-")[1] if len(setting.split("-")) > 1 else None
        if setting_base not in automod_settings:
            automod_settings[setting_base] = {}
        value = parse_mention_to_id(value) if setting_key in ["channel_id"] else value
        automod_settings[setting_base][setting_key] = value
        set_server_config(guild_id, "automod", automod_settings)
        await interaction.response.send_message(f"已將自動管理設定 '{setting}' 設為 {value}。")
    
    @app_commands.command(name=app_commands.locale_str("check-action"), description="檢查自動管理動作指令是否有效")
    @app_commands.describe(action="要檢查的動作指令")
    async def check_automod_action(self, interaction: discord.Interaction, action: str):
        try:
            actions = await do_action_str(action)
        except Exception as e:
            await interaction.response.send_message(f"無法解析動作指令: {e}", ephemeral=True)
            return
        actions = [f"- {a}" for a in actions]
        actions_str = "\n".join(actions) if actions else "無動作"
        msg = f"指令有效，解析出的動作:\n{actions_str}"
        await interaction.response.send_message(content=msg)

    @app_commands.command(name=app_commands.locale_str("scan-flagged-users"), description="掃描並更新伺服器中的標記用戶")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(user="要掃描的用戶，若不指定則掃描所有用戶")
    async def scan_flagged_users(self, interaction: discord.Interaction, user: discord.User = None):
        await interaction.response.send_message("開始掃描標記用戶...")
        database_file = config("flagged_database_path", "flagged_data.db")
        conn = sqlite3.connect(database_file)
        cursor = conn.cursor()
        flagged_user = {}
        if user:
            cursor.execute('SELECT user_id, guild_id, flagged_at, flagged_role FROM flagged_users WHERE user_id = ?', (user.id,))
            results = cursor.fetchall()
            results = [dict(zip([column[0] for column in cursor.description], row)) for row in results]
            if results:
                for result in results:
                    cursor.execute('SELECT name FROM guilds WHERE id = ?', (result['guild_id'],))
                    guild_info = cursor.fetchone()
                    result['guild_name'] = guild_info[0] if guild_info else "未知伺服器"
                flagged_user[result['user_id']] = results
        else:
            for member in interaction.guild.members:
                cursor.execute('SELECT user_id, guild_id, flagged_at, flagged_role FROM flagged_users WHERE user_id = ?', (member.id,))
                results = cursor.fetchall()
                # convert to dict
                results = [dict(zip([column[0] for column in cursor.description], row)) for row in results]
                # get server info
                if results:
                    for result in results:
                        cursor.execute('SELECT name FROM guilds WHERE id = ?', (result['guild_id'],))
                        guild_info = cursor.fetchone()
                        result["user_name"] = member.name
                        result['guild_name'] = guild_info[0] if guild_info else "未知伺服器"
                    flagged_user[member.id] = results
        conn.close()
        if flagged_user:
            msg_lines = []
            for user_id, fu in flagged_user.items():
                member = interaction.guild.get_member(user_id)
                user_name = member.name if member else "未知用戶"
                msg_lines.append(f"用戶: {user_name} (ID: {user_id})")
                for entry in fu:
                    guild_name = entry.get('guild_name', '未知伺服器')
                    flagged_at = entry.get('flagged_at', '未知時間')
                    flagged_role = entry.get('flagged_role', 0)
                    msg_lines.append(f" - 伺服器: {guild_name}, 標記時間: {flagged_at}{', 擁有被標記的身份組' if flagged_role else ''}")
                msg_lines.append("")  # 空行分隔不同用戶
            msg = "\n".join(msg_lines)
            file = discord.File(io.StringIO(msg), filename="flagged_users.txt")
            await interaction.followup.send(file=file)
        else:
            await interaction.followup.send("掃描完成！未找到任何標記用戶。")

    @app_commands.command(name=app_commands.locale_str("flagged-user-alert-channel"), description="設置用戶加入伺服器時的通知頻道")
    @app_commands.describe(channel="用於接收用戶加入通知的頻道")
    async def set_flagged_user_onjoin_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        set_server_config(interaction.guild.id, "flagged_user_onjoin_channel", channel.id)
        await interaction.response.send_message(f"已將用戶加入通知頻道設置為 {channel.mention}。")
    
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild_id = member.guild.id
        channel_id = get_server_config(guild_id, "flagged_user_onjoin_channel")
        if not channel_id:
            return
        database_file = config("flagged_database_path", "flagged_data.db")
        conn = sqlite3.connect(database_file)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, guild_id, flagged_at, flagged_role FROM flagged_users WHERE user_id = ?', (member.id,))
        results = cursor.fetchall()
        results = [dict(zip([column[0] for column in cursor.description], row)) for row in results]
        if results:
            log(f"被標記的用戶 {member} ({len(results)}) 加入伺服器，發送通知。", module_name="AutoModerate", user=member, guild=member.guild)
            channel = member.guild.get_channel(channel_id)
            if channel:
                embed = discord.Embed(title="標記用戶加入伺服器", color=0xff0000)
                embed.add_field(name="用戶", value=f"{member.mention} (ID: {member.id})", inline=False)
                for result in results:
                    cursor.execute('SELECT name FROM guilds WHERE id = ?', (result['guild_id'],))
                    guild_info = cursor.fetchone()
                    guild_name = guild_info[0] if guild_info else "未知伺服器"
                    flagged_at = result.get('flagged_at', '未知時間')
                    embed.add_field(name=guild_name, value=f"標記時間: {flagged_at}{', 擁有被標記的身份組' if result.get('flagged_role', 0) else ''}", inline=False)
                await channel.send(embed=embed)
        conn.close()
                

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not after.guild:
            return
        if not after.timed_out_until:
            return
        set_user_data(guild_id=after.guild.id, user_id=after.id, key="communication_disabled_until", value=after.timed_out_until.isoformat() if after.timed_out_until else None)
        
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not member.guild:
            return
        server_config = get_server_config(member.guild.id, "automod", {})
        if not server_config.get("escape_punish", {}).get("enabled", False):
            return
        communication_disabled_until = get_user_data(guild_id=member.guild.id, user_id=member.id, key="communication_disabled_until")
        if communication_disabled_until:
            communication_disabled_until = datetime.fromisoformat(communication_disabled_until)
            if communication_disabled_until > datetime.now(timezone.utc):
                # 用戶在禁言期間離開，進行懲處
                punishment = server_config["escape_punish"].get("punishment", "ban")
                duration = server_config["escape_punish"].get("duration", "0")
                duration_seconds = Moderate.timestr_to_seconds(duration) if Moderate else 0
                try:
                    if punishment == "ban":
                        if Moderate:
                            await Moderate.ban_user(member.guild, member, reason="逃避禁言", duration=duration_seconds if duration_seconds > 0 else 0)
                        else:
                            print("[!] Moderate module not loaded, cannot ban user.")
                            raise Exception("Moderate module not loaded")
                    # 好像也就只有 ban 可以用了，我在做什麼呀
                    print(f"[+] 用戶 {member} 因逃避禁言被 {punishment}")
                except Exception as e:
                    print(f"[!] 無法對用戶 {member} 執行懲處: {e}")
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        guild_id = message.guild.id
        automod_settings = get_server_config(guild_id, "automod", {})
        
        # 用戶安裝應用程式濫用檢查（需在 bot 訊息過濾之前，因為 user install 的訊息作者是 bot）
        is_user_install_message = (
            message.interaction_metadata is not None 
            and message.interaction_metadata.is_user_integration()
            and not message.interaction_metadata.is_guild_integration()
        )
        
        if is_user_install_message and automod_settings.get("anti_uispam", {}).get("enabled", False):
            triggering_user = message.interaction_metadata.user
            member = message.guild.get_member(triggering_user.id)
            if member and not member.guild_permissions.administrator:
                max_count = int(automod_settings["anti_uispam"].get("max_count", 5))
                time_window = int(automod_settings["anti_uispam"].get("time_window", 60))
                action = automod_settings["anti_uispam"].get("action", "delete {user}，請勿濫用用戶安裝的應用程式指令。")
                
                now = datetime.now(timezone.utc)
                guild_tracker = _uispam_tracker.setdefault(guild_id, {})
                user_timestamps = guild_tracker.setdefault(triggering_user.id, [])
                
                # 清除過期的時間戳
                user_timestamps[:] = [ts for ts in user_timestamps if (now - ts).total_seconds() < time_window]
                
                # 記錄本次觸發
                user_timestamps.append(now)
                
                if len(user_timestamps) > max_count:
                    try:
                        target_member = member or triggering_user
                        await do_action_str(action, guild=message.guild, user=target_member, message=message)
                        log(f"用戶 {triggering_user} 因濫用用戶安裝應用程式被處理 (在 {time_window}秒內觸發 {len(user_timestamps)} 次): {action}", module_name="AutoModerate", user=triggering_user, guild=message.guild)
                        # 重置計數器避免重複處罰
                        user_timestamps.clear()
                    except Exception as e:
                        log(f"無法對用戶 {triggering_user} 執行用戶安裝應用程式濫用的處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=triggering_user, guild=message.guild)
        
        if message.author.bot:
            return
        if message.author.guild_permissions.administrator:
            return
        
        # 詐騙陷阱檢查
        if automod_settings.get("scamtrap", {}).get("enabled", False):
            scamtrap_channel_id = int(automod_settings["scamtrap"].get("channel_id", 0))
            action = automod_settings["scamtrap"].get("action", "delete 請不要在此頻道發送訊息。")
            if scamtrap_channel_id != 0 and message.channel.id == scamtrap_channel_id:
                try:
                    await do_action_str(action, guild=message.guild, user=message.author, message=message)
                    # print(f"[+] 用戶 {message.author} 因進入詐騙陷阱頻道被處理: {action}")
                    log(f"用戶 {message.author} 因進入詐騙陷阱頻道被處理: {action}", module_name="AutoModerate", user=message.author, guild=message.guild)
                except Exception as e:
                    # print(f"[!] 無法對用戶 {message.author} 執行詐騙陷阱的處理: {e}")
                    log(f"無法對用戶 {message.author} 執行詐騙陷阱的處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=message.author, guild=message.guild)
        
        # 標題過多檢查
        if automod_settings.get("too_many_h1", {}).get("enabled", False):
            max_length = int(automod_settings["too_many_h1"].get("max_length", 20))
            action = automod_settings["too_many_h1"].get("action", "warn")
            h1_count = 0
            split_lines = message.content.split("\n")
            for line in split_lines:
                line = line.lstrip()
                if line.startswith("# "):
                    # find custom emoji and replace with single character
                    while re.search(r'<a?:\w+:\d+>', line):
                        line = re.sub(r'<a?:\w+:\d+>', 'E', line, count=1)
                    line = line[2:]
                    h1_count += len(line)
            if h1_count > max_length:
                try:
                    await do_action_str(action, guild=message.guild, user=message.author, message=message)
                    # print(f"[+] 用戶 {message.author} 因標題長度過長被處理: {action}")
                    log(f"用戶 {message.author} 因標題長度過長被處理: {action}", module_name="AutoModerate", user=message.author, guild=message.guild)
                except Exception as e:
                    # print(f"[!] 無法對用戶 {message.author} 執行標題過多的處理: {e}")
                    log(f"無法對用戶 {message.author} 執行標題過多的處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=message.author, guild=message.guild)
        
        # 表情符號過多檢查
        if automod_settings.get("too_many_emojis", {}).get("enabled", False):
            max_emojis = int(automod_settings["too_many_emojis"].get("max_emojis", 10))
            action = automod_settings["too_many_emojis"].get("action", "warn")
            emoji_count = len(re.findall(r'<a?:\w+:\d+>', message.content))
            emoji_count += len([c for c in message.content if emoji.is_emoji(c)])
            if emoji_count > max_emojis:
                try:
                    await do_action_str(action, guild=message.guild, user=message.author, message=message)
                    # print(f"[+] 用戶 {message.author} 因表情符號過多被處理: {action}")
                    log(f"用戶 {message.author} 因表情符號過多被處理: {action}", module_name="AutoModerate", user=message.author, guild=message.guild)
                except Exception as e:
                    # print(f"[!] 無法對用戶 {message.author} 執行表情符號過多的處理: {e}")
                    log(f"無法對用戶 {message.author} 執行表情符號過多的處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=message.author, guild=message.guild)

asyncio.run(bot.add_cog(AutoModerate(bot)))

if __name__ == "__main__":
    start_bot()
