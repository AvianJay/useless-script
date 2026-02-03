from globalenv import bot, start_bot, get_server_config, set_server_config, get_user_data, set_user_data, on_ready_tasks, config
import discord
from discord import app_commands
from discord.ext import commands
import json
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone
from typing import Union, Optional
import ModerationNotify
from logger import log
import logging
import re


def timestr_to_seconds(timestr: str) -> int:
    """將時間字串轉換為秒數"""
    units = {
        's': 1,
        'm': 60,
        'h': 3600,
        'd': 86400,
        'w': 604800,
        'M': 2592000,  # 假設一個月30天
        'y': 31536000, # 假設一年365天
    }
    total_seconds = 0
    num = ''
    for char in timestr:
        if not char.strip():
            continue
        if char.isdigit():
            num += char
        elif char in units and num:
            total_seconds += int(num) * units[char]
            num = ''
    if num:  # 如果字串以數字結尾，則算為秒數
        total_seconds += int(num)
    return total_seconds


def get_time_text(seconds: int) -> str:
    final = ""
    while seconds != 0:
        if seconds < 60:
            final += f" {seconds} 秒"
            seconds = 0
        elif seconds < 3600:
            final += f" {seconds // 60} 分鐘"
            seconds = seconds % 60
        elif seconds < 86400:
            final += f" {seconds // 3600} 小時"
            seconds = seconds % 3600
        else:
            final += f" {seconds // 86400} 天"
            seconds = seconds % 86400
    return final.strip()


def guess_role(guild: discord.Guild, role_name: str):
    # try to find role by id
    try:
        role_id = int(role_name)
        role = guild.get_role(role_id)
        if role is not None:
            return role.id
    except ValueError:
        pass
    # try to find role by mention
    if role_name.startswith("<@&") and role_name.endswith(">"):
        try:
            role_id = int(role_name[3:-1])
            role = guild.get_role(role_id)
            if role is not None:
                return role.id
        except ValueError:
            pass
    # try to find role by name
    for role in guild.roles:
        if role.name == role_name:
            return role.id
    # try to find role by case insensitive name
    for role in guild.roles:
        if role.name.lower() == role_name.lower():
            return role.id
    # try to find role by partial name
    for role in guild.roles:
        if role_name in role.name:
            return role.id
    return None


async def get_case_id(guild: discord.Guild) -> int:
    channel_id = get_server_config(guild.id, "MODERATION_MESSAGE_CHANNEL_ID")
    channel = guild.get_channel(channel_id)
    if channel is None:
        return int(f"{current_roc_year()}0001")

    current_year = current_roc_year()

    async for message in channel.history(limit=25):
        m = re.search(r"裁判字號：\s*(\d{7})", message.content)
        if not m:
            continue

        case_id = m.group(1)
        case_year = int(case_id[:3])
        case_no = int(case_id[3:])

        if case_year != current_year:
            return int(f"{current_year}0001")

        return int(f"{current_year}{case_no + 1:04d}")

    # 完全沒找到任何裁判字號
    return int(f"{current_year}0001")


def current_roc_year() -> int:
    return datetime.now().year - 1911


async def ban_user(guild: discord.Guild, user: Union[discord.Member, discord.User], reason: str, duration: int = 0, delete_message_seconds: int = 0):
    notifymsg = None
    try:
        if duration > 0:
            unban_time = datetime.now(timezone.utc) + timedelta(seconds=duration)
            set_user_data(guild.id, user.id, "unban_time", unban_time.isoformat())
        ModerationNotify.ignore_user(user.id)  # 避免重複通知
        try:
            notifymsg = await ModerationNotify.notify_user(user, guild, "封禁", reason, end_time=unban_time if duration > 0 else None)
        except Exception:
            pass
        if isinstance(user, discord.Member):
            await user.ban(reason=reason, delete_message_seconds=delete_message_seconds)
        else:
            await guild.ban(user, reason=reason, delete_message_seconds=delete_message_seconds)
        # print(f"[+] 已封禁用戶 {user}，原因：{reason}，解封時間：{'無' if duration == 0 else unban_time.isoformat()}")
        log(f"已封禁用戶 {user}，原因：{reason}，解封時間：{'無' if duration == 0 else unban_time.isoformat()}", module_name="Moderate", guild=guild)
        return True
    except Exception as e:
        # print(f"[!] 無法封禁用戶 {user}：{e}")
        log(f"無法封禁用戶 {user}：{e}", level=logging.ERROR, module_name="Moderate", guild=guild)
        if notifymsg:
            await notifymsg.delete()
        return False


async def check_unban():
    await bot.wait_until_ready()
    # print("[+] 自動解封任務已啟動")
    log("自動解封任務已啟動", module_name="Moderate")
    try:
        while not bot.is_closed():
            for guild in bot.guilds:
                if bot.is_closed():
                    return
                guild_id = guild.id
                to_unban = []

                try:
                    # 使用 async for 逐項讀取封鎖列表（memory-friendly）
                    async for entry in guild.bans():
                        user = entry.user
                        unban_time_str = get_user_data(guild_id, user.id, "unban_time")
                        if unban_time_str is None:
                            continue
                        try:
                            unban_time = datetime.fromisoformat(unban_time_str)
                        except Exception:
                            continue
                        if unban_time.tzinfo is None:
                            unban_time = unban_time.replace(tzinfo=timezone.utc)
                        if unban_time <= datetime.now(timezone.utc):
                            to_unban.append(user)
                except Exception as e:
                    # print(f"[!] 讀取 {guild.name} 的封鎖列表發生錯誤：{e}")
                    continue

                for user in to_unban:
                    try:
                        await guild.unban(user, reason="自動解封")
                        set_user_data(guild_id, user.id, "unban_time", None)
                        log(f"已自動解封 {user} 在 {guild.name} 的封禁。", module_name="Moderate", guild=guild)
                    except Exception as e:
                        log(f"解封 {user} 時發生錯誤：{e}", level=logging.ERROR, module_name="Moderate", guild=guild)

            await asyncio.sleep(60)  # 每分鐘檢查一次
    except asyncio.CancelledError:
        log("自動解封任務已取消", module_name="Moderate")
on_ready_tasks.append(check_unban)


async def do_action_str(action: str, guild: Optional[discord.Guild] = None, user: Optional[discord.Member] = None, message: Optional[discord.Message] = None, moderator: Optional[discord.Member] = None):
    # if user is none just check if action is valid
    actions = action.split(",")
    actions = [a.strip() for a in actions]
    if len(actions) > 5:
        return ["錯誤：一次只能執行最多5個動作。"]
    logs = []
    last_reason = "管理執行"
    actions_json = []
    for a in actions:
        cmd = a.split(" ")
        if cmd[0] == "ban":
            # ban <reason> <delete_messages> <duration>
            if len(cmd) == 1:
                cmd.append("0s")
            if len(cmd) == 2:
                cmd.append("0s")
            if len(cmd) == 3:
                cmd.append(last_reason)

            duration_seconds = timestr_to_seconds(cmd[1]) if cmd[1] != "0" else 0
            delete_messages = timestr_to_seconds(cmd[2]) if cmd[2] != "0" else 0
            cmd.pop(0)  # remove "ban"
            cmd.pop(0)  # remove duration
            cmd.pop(0)  # remove delete_messages
            reason = " ".join(cmd)
            last_reason = reason
            success = True
            if user:
                success = await ban_user(guild, user, reason=reason, duration=duration_seconds, delete_message_seconds=delete_messages)
            if success:
                logs.append(f"封禁用戶，原因: {reason}，持續秒數: {duration_seconds}秒，刪除訊息時間: {delete_messages}秒")
            else:
                logs.append(f"封禁用戶失敗。")
            actions_json.append({"action": "ban", "duration": duration_seconds, "reason": reason})
        elif cmd[0] == "kick":
            # kick <reason>
            if len(cmd) == 1:
                cmd.append(last_reason)
            cmd.pop(0)  # remove "kick"
            reason = " ".join(cmd)
            logs.append(f"踢出用戶，原因: {reason}")
            if user:
                await user.kick(reason=reason)
            actions_json.append({"action": "kick", "reason": reason})
        elif cmd[0] == "mute" or cmd[0] == "timeout":
            # mute <duration> <reason>
            if len(cmd) == 1:
                cmd.append("10m")
            if len(cmd) == 2:
                cmd.append(last_reason)
            duration_seconds = timestr_to_seconds(cmd[1]) if cmd[1] != "0" else 0
            cmd.pop(0)  # remove "mute" or "timeout"
            cmd.pop(0)  # remove duration
            reason = " ".join(cmd) if cmd else last_reason
            logs.append(f"禁言用戶，原因: {reason}，持續秒數: {duration_seconds}秒")
            if user:
                await user.timeout(datetime.now(timezone.utc) + timedelta(seconds=duration_seconds), reason=reason)
            actions_json.append({"action": "mute", "duration": duration_seconds, "reason": reason})
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
            actions_json.append({"action": "unban", "reason": reason})
        elif cmd[0] == "unmute" or cmd[0] == "untimeout":
            # unmute <reason>
            if len(cmd) == 1:
                cmd.append(last_reason)
            cmd.pop(0)  # remove "unmute" or "untimeout"
            reason = " ".join(cmd)
            logs.append(f"解除禁言用戶，原因: {reason}")
            if user:
                await user.timeout(None, reason=reason)
            actions_json.append({"action": "unmute", "reason": reason})
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
                await moderation_message_settings(None, user, moderator, actions_json, direct=True, guild=guild)
    return logs


async def moderation_message_settings(interaction: Optional[discord.Interaction], user: discord.Member, moderator: discord.Member, actions: list, direct: bool = False, guild: Optional[discord.Guild] = None):
    # generate message
    action_texts = []
    for action in actions:
        if action["action"] == "ban":
            duration_seconds = action.get("duration", 0)
            if duration_seconds > 0:
                action_texts.append(f"暫時驅逐出境至柬服KK副本||停權||{get_time_text(duration_seconds)}")
            else:
                action_texts.append("驅逐出境至柬服KK副本||永久停權||")
        elif action["action"] == "kick":
            action_texts.append("踢出")
        elif action["action"] == "mute":
            time_text = action.get("duration", 0)
            action_texts.append(f"羈押禁見||禁言||{get_time_text(time_text)}")
        elif action["action"] == "add_role":
            action_texts.append(f"給予身分組 {action['role']}")
        elif action["action"] == "remove_role":
            action_texts.append(f"移除身分組 {action['role']}")
        elif action["action"] == "custom":
            action_texts.append(action.get("custom_action", "無"))
    action_text = "+".join(action_texts) if action_texts else "無"
    # get reason
    reason = None
    for action in actions:
        if 'reason' in action:
            reason = action['reason']
            break
    async def generate_message():
        return f"""
### ⛔ 違規處分
> - 被處分者： {user.mention}
> - 處分原因：{reason}
> - 處分結果：{action_text}
> - 裁判字號： {await get_case_id(guild if guild else interaction.guild)}
> - 處分執行： {moderator.mention}
"""

    async def send_message():
        nonlocal guild
        if interaction:
            guild = interaction.guild
        channel_id = get_server_config(guild.id, "MODERATION_MESSAGE_CHANNEL_ID")
        if channel_id is None:
            if interaction:
                await interaction.response.send_message("伺服器未設定公告頻道，請先設定後再嘗試。", ephemeral=True)
            return
        channel = guild.get_channel(channel_id)
        if channel is None:
            if interaction:
                await interaction.response.send_message("找不到公告頻道，請確認頻道是否存在。", ephemeral=True)
            return
        try:
            await channel.send(generate_message())
            if interaction:
                await interaction.followup.send("已發送公告到公告頻道。", ephemeral=True)
            log(f"已發送公告到 {channel.name} 頻道。", module_name="Moderate", guild=guild)
        except discord.Forbidden:
            if interaction:
                await interaction.response.send_message("無法在公告頻道發送訊息，機器人缺少權限。", ephemeral=True)
            log(f"無法在公告頻道發送訊息，機器人缺少權限。", level=logging.ERROR, module_name="Moderate", guild=guild)
        except Exception as e:
            if interaction:
                await interaction.response.send_message(f"發送公告時發生錯誤：{e}", ephemeral=True)
            log(f"發送公告時發生錯誤：{e}", level=logging.ERROR, module_name="Moderate", guild=guild)
    embed = discord.Embed(title="公告設定", color=0xff0000)
    embed.add_field(name="公告內容", value="```\n" + generate_message() + "\n```", inline=False)
    class MessageButtons(discord.ui.View):
        def __init__(self):
            super().__init__()
        
        @discord.ui.button(label="更改原因", style=discord.ButtonStyle.primary, row=0)
        async def change_reason_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            class ReasonModal(discord.ui.Modal, title="更改原因"):
                nonlocal reason
                reason = discord.ui.TextInput(label="處分原因", placeholder="請輸入處分原因", required=True, max_length=100)
                async def on_submit(self, interaction: discord.Interaction):
                    nonlocal reason
                    if not interaction.user.guild_permissions.administrator:
                        await interaction.response.send_message("你沒有權限執行此操作。", ephemeral=True)
                        return
                    reason = self.reason.value
                    for action in actions:
                        if 'reason' in action:
                            action['reason'] = reason
                    embed.set_field_at(0, name="公告內容", value="```\n" + generate_message() + "\n```", inline=False)
                    await interaction.response.edit_message(embed=embed, view=self.view)
            await interaction.response.send_modal(ReasonModal())
                    
        @discord.ui.button(label="更改結果", style=discord.ButtonStyle.primary, row=0)
        async def change_actions_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            class ActionModal(discord.ui.Modal, title="更改結果"):
                nonlocal action_text
                new_actions = discord.ui.TextInput(label="處分結果", placeholder="請輸入處分結果", required=True, max_length=200)
                async def on_submit(self, interaction: discord.Interaction):
                    nonlocal action_text
                    if not interaction.user.guild_permissions.administrator:
                        await interaction.response.send_message("你沒有權限執行此操作。", ephemeral=True)
                        return
                    action_text = self.new_actions.value
                    embed.set_field_at(0, name="公告內容", value="```\n" + generate_message() + "\n```", inline=False)
                    await interaction.response.edit_message(embed=embed, view=self.view)
            await interaction.response.send_modal(ActionModal())
        
        @discord.ui.button(label="確認並發送", style=discord.ButtonStyle.success, row=1)
        async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("你沒有權限執行此操作。", ephemeral=True)
                return
            self.stop()
            # send message to channel
            await send_message()
    
    if direct:
        await send_message()
    else:
        if interaction:
            await interaction.response.send_message(embed=embed, view=MessageButtons())
            

@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class Moderate(commands.GroupCog, group_name=app_commands.locale_str("admin")):
    def __init__(self, bot):
        self.bot = bot
    
    
    @app_commands.command(name=app_commands.locale_str("multi-moderate"), description="對用戶進行多重操作")
    @app_commands.describe(user="選擇用戶")
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def multi_moderate(self, interaction: discord.Interaction, user: discord.Member):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("此指令只能在伺服器中使用。", ephemeral=True)
            return
        
        # check bot permissions
        if not guild.me.guild_permissions.administrator:
            await interaction.response.send_message("機器人需要管理員權限才能執行此操作。", ephemeral=True)
            return
        
        actions = []  # {"action": "mute/kick/ban/add_role/remove_role", "reason": "reason", "duration": minutes, "role": role_id}
        def actions_to_str(actions):
            if not actions:
                return "無"
            return "\n".join(f"- {a['action']}" + (f" ({a['duration']} 分鐘)" if a['action'] == 'mute' and 'duration' in a else '') + (f" (角色 ID: {a['role']} / 名稱: {interaction.guild.get_role(a['role']).name})" if a['action'] in ['add_role', 'remove_role'] and 'role' in a else '') + (f": {a['reason']}" if 'reason' in a else '') for a in actions)
        class ActionButtons(discord.ui.View):
            def __init__(self):
                super().__init__()
            
            @discord.ui.button(label="禁言", style=discord.ButtonStyle.primary, row=0)
            async def mute_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                class MuteModal(discord.ui.Modal, title="禁言時間設定"):
                    minutes = discord.ui.TextInput(label="禁言分鐘數", placeholder="請輸入禁言時間（分鐘）", required=True)
                    reason = discord.ui.TextInput(label="禁言原因", placeholder="請輸入禁言原因", required=True, max_length=100)
                    async def on_submit(self, interaction: discord.Interaction):
                        if not interaction.user.guild_permissions.administrator:
                            await interaction.response.send_message("你沒有權限執行此操作。", ephemeral=True)
                            return
                        try:
                            duration = int(self.minutes.value)
                            if duration <= 0:
                                raise ValueError
                        except ValueError:
                            await interaction.response.send_message("無效的禁言時間，請輸入正整數。", ephemeral=True)
                            return
                        actions.append({"action": "mute", "duration": duration, "reason": self.reason.value})
                        embed.set_field_at(0, name="目前操作", value=actions_to_str(actions), inline=False)
                        await interaction.response.edit_message(embed=embed, view=view)
                await interaction.response.send_modal(MuteModal())

            @discord.ui.button(label="踢出", style=discord.ButtonStyle.danger, row=0)
            async def kick_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                class KickModal(discord.ui.Modal, title="踢出原因設定"):
                    reason = discord.ui.TextInput(label="踢出原因", placeholder="請輸入踢出原因", required=True, max_length=100)
                    async def on_submit(self, interaction: discord.Interaction):
                        if not interaction.user.guild_permissions.administrator:
                            await interaction.response.send_message("你沒有權限執行此操作。", ephemeral=True)
                            return
                        actions.append({"action": "kick", "reason": self.reason.value})
                        embed.set_field_at(0, name="目前操作", value=actions_to_str(actions), inline=False)
                        await interaction.response.edit_message(embed=embed, view=view)
                await interaction.response.send_modal(KickModal())

            @discord.ui.button(label="封禁", style=discord.ButtonStyle.danger, row=0)
            async def ban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                class BanModal(discord.ui.Modal, title="封禁原因設定"):
                    reason = discord.ui.TextInput(label="封禁原因", placeholder="請輸入封禁原因", required=True, max_length=100)
                    async def on_submit(self, interaction: discord.Interaction):
                        if not interaction.user.guild_permissions.administrator:
                            await interaction.response.send_message("你沒有權限執行此操作。", ephemeral=True)
                            return
                        actions.append({"action": "ban", "reason": self.reason.value})
                        embed.set_field_at(0, name="目前操作", value=actions_to_str(actions), inline=False)
                        await interaction.response.edit_message(embed=embed, view=view)
                await interaction.response.send_modal(BanModal())
                
            @discord.ui.button(label="新增身分組", style=discord.ButtonStyle.secondary, row=1)
            async def add_role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                class AddRoleModal(discord.ui.Modal, title="新增身分組設定"):
                    role = discord.ui.Label(text="選擇身分組", component=discord.ui.RoleSelect(placeholder="選擇身分組", min_values=1, max_values=1))
                    async def on_submit(self, interaction: discord.Interaction):
                        if not interaction.user.guild_permissions.administrator:
                            await interaction.response.send_message("你沒有權限執行此操作。", ephemeral=True)
                            return
                        role_id = self.role.component.values[0].id
                        actions.append({"action": "add_role", "role": role_id})
                        embed.set_field_at(0, name="目前操作", value=actions_to_str(actions), inline=False)
                        await interaction.response.edit_message(embed=embed, view=view)
                await interaction.response.send_modal(AddRoleModal())

            @discord.ui.button(label="移除身分組", style=discord.ButtonStyle.secondary, row=1)
            async def remove_role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                class RemoveRoleModal(discord.ui.Modal, title="移除身分組設定"):
                    role_name = discord.ui.TextInput(label="身分組名稱", placeholder="請輸入身分組名稱/ID/關鍵字", required=True, max_length=100)
                    async def on_submit(self, interaction: discord.Interaction):
                        if not interaction.user.guild_permissions.administrator:
                            await interaction.response.send_message("你沒有權限執行此操作。", ephemeral=True)
                            return
                        role_id = guess_role(interaction.guild, self.role_name.value)
                        if role_id is None:
                            await interaction.response.send_message("找不到指定的身分組，請確認名稱或 ID 是否正確。", ephemeral=True)
                            return
                        actions.append({"action": "remove_role", "role": role_id})
                        embed.set_field_at(0, name="目前操作", value=actions_to_str(actions), inline=False)
                        await interaction.response.edit_message(embed=embed, view=view)
                await interaction.response.send_modal(RemoveRoleModal())
            
            
            @discord.ui.button(label="執行公告設定", style=discord.ButtonStyle.success, row=1)
            async def moderation_message(self, interaction: discord.Interaction, button: discord.ui.Button):
                if not interaction.user.guild_permissions.administrator:
                    await interaction.response.send_message("你沒有權限執行此操作。", ephemeral=True)
                    return
                if not actions:
                    await interaction.response.send_message("請先選擇至少一個操作。", ephemeral=True)
                    return
                actions_with_mention = actions.copy()
                for action in actions_with_mention:
                    if action["action"] in ["add_role", "remove_role"]:
                        role = interaction.guild.get_role(action["role"])
                        if role:
                            action["role"] = role.mention
                        else:
                            action["role"] = str(action["role"])
                await moderation_message_settings(interaction, user, interaction.user, actions_with_mention)
            
            @discord.ui.button(label="執行操作", style=discord.ButtonStyle.success, row=2)
            async def execute_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if not interaction.user.guild_permissions.administrator:
                    await interaction.response.send_message("你沒有權限執行此操作。", ephemeral=True)
                    return
                if not actions:
                    await interaction.response.send_message("請先選擇至少一個操作。", ephemeral=True)
                    return
                if len(actions) > 5:
                    await interaction.response.send_message("一次只能執行最多5個動作。", ephemeral=True)
                    return
                self.stop()
                # execute actions
                results = []
                for action in actions:
                    try:
                        if action["action"] == "mute":
                            duration = action.get("duration", 0)
                            await user.timeout(timedelta(minutes=duration), reason=action.get("reason", "無"))
                            results.append(f"已對 {user.mention} 禁言 {get_time_text(duration)}。")
                        elif action["action"] == "kick":
                            ModerationNotify.ignore_user(user.id)  # 避免重複通知
                            try:
                                await ModerationNotify.notify_user(user, interaction.guild, "踢出", action.get("reason", "無"))
                            except Exception as e:
                                print(f"[!] 無法私訊 {user}：{e}")
                            await user.kick(reason=action.get("reason", "無"))
                            results.append(f"已將 {user.mention} 踢出伺服器。")
                        elif action["action"] == "ban":
                            ModerationNotify.ignore_user(user.id)  # 避免重複通知
                            try:
                                await ModerationNotify.notify_user(user, interaction.guild, "封禁", action.get("reason", "無"))
                            except Exception as e:
                                print(f"[!] 無法私訊 {user}：{e}")
                            await user.ban(reason=action.get("reason", "無"))
                            results.append(f"已將 {user.mention} 封禁。")
                        elif action["action"] == "add_role":
                            role = interaction.guild.get_role(action["role"])
                            if role:
                                await user.add_roles(role, reason="多重操作")
                                results.append(f"已給予 {user.mention} 身分組 {role.name}。")
                            else:
                                results.append(f"找不到身分組 ID {action['role']}，無法新增身分組。")
                        elif action["action"] == "remove_role":
                            role = interaction.guild.get_role(action["role"])
                            if role:
                                await user.remove_roles(role, reason="多重操作")
                                results.append(f"已移除 {user.mention} 身分組 {role.name}。")
                            else:
                                results.append(f"找不到身分組 ID {action['role']}，無法移除身分組。")
                    except Exception as e:
                        results.append(f"執行 {action['action']} 時發生錯誤：{e}")
                await interaction.response.edit_message(content="\n".join(results), embed=None, view=None)

            @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary, row=2)
            async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if not interaction.user.guild_permissions.administrator:
                    await interaction.response.send_message("你沒有權限執行此操作。", ephemeral=True)
                    return
                actions.append({"action": "cancel", "user": user.id})
                self.stop()
                await interaction.response.edit_message(content="操作已取消。", view=None)
        
        embed = discord.Embed(title="多重操作", description=f"請選擇對 {user.name} 執行的操作：", color=0xff0000)
        embed.add_field(name="目前操作", value="無", inline=False)
        view = ActionButtons()
        message = await interaction.response.send_message(embed=embed, view=view)


    @app_commands.command(name=app_commands.locale_str("send-moderation-message"), description="手動發送懲處公告")
    @app_commands.describe(user="選擇用戶", reason="處分原因", action="處分結果", moderator="執行管理員（可選）")
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def send_moderation_message(self, interaction: discord.Interaction, user: discord.Member, reason: str, action: str, moderator: discord.Member=None):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("此指令只能在伺服器中使用。", ephemeral=True)
            return
        if moderator is None:
            moderator = interaction.user
        actions = [{"action": "custom", "custom_action": action, "reason": reason}]
        await moderation_message_settings(interaction, user, moderator, actions)


    @app_commands.command(name=app_commands.locale_str("ban"), description="封禁用戶")
    @app_commands.describe(user="選擇用戶", reason="封禁原因（可選）", duration="封禁時間（可選，預設永久）", delete_message="刪除訊息時間（可選，預設不刪除）")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.default_permissions(ban_members=True)
    async def ban_user(self, interaction: discord.Interaction, user: discord.Member, reason: str = "無", duration: str = "", delete_message: str = ""):
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("此指令只能在伺服器中使用。")
            return
        
        # check bot permissions
        if not guild.me.guild_permissions.ban_members:
            await interaction.followup.send("機器人沒有封鎖成員的權限，請確認機器人擁有「封鎖成員」的權限。")
            return

        user_id = user.id
        user_obj = user

        # 解析封禁時間（可選，若提供則記錄 unban_time）
        unban_time = None
        if duration:
            duration_seconds = timestr_to_seconds(duration)
            if duration_seconds <= 0:
                await interaction.followup.send("無效的封禁時間，請使用類似 10m、2h、3d 的格式。")
                return
            unban_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

        # 解析要刪除訊息的秒數
        delete_message_seconds = timestr_to_seconds(delete_message)

        # 執行封禁：若有 Member 直接用 member.ban，否則用 guild.ban 與 discord.Object(id=...)
        success = await ban_user(guild, user_obj if user_obj else discord.Object(id=user_id), reason, duration=duration_seconds if unban_time else 0, delete_message_seconds=delete_message_seconds)
        if not success:
            await interaction.followup.send("封禁時發生錯誤，請確認機器人是否有足夠的權限。")
            return

        mention = user_obj.mention if user_obj else f"<@{user_id}>"
        parts = [f"已將 {mention} 封禁。"]
        if reason != "無":
            parts.append(f"- 原因：{reason}")
        if unban_time:
            parts.append(f"- 封禁時間：{get_time_text(duration_seconds)}")
        if delete_message_seconds > 0:
            parts.append(f"- 刪除訊息時間：{get_time_text(delete_message_seconds)}")
        await interaction.followup.send("\n".join(parts))


    @app_commands.command(name=app_commands.locale_str("unban"), description="解封用戶")
    @app_commands.describe(user="選擇用戶")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def unban_user(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("此指令只能在伺服器中使用。")
            return
        
        # check bot permissions
        if not guild.me.guild_permissions.ban_members:
            await interaction.followup.send("機器人沒有解封成員的權限，請確認機器人擁有「解除封鎖成員」的權限。")
            return

        user_id = user.id

        # 執行解封
        try:
            await guild.unban(user, reason="手動解封")
            set_user_data(guild.id, user_id, "unban_time", None)
        except Exception as e:
            await interaction.followup.send(f"解封時發生錯誤：{e}")
            return

        await interaction.followup.send(f"已將 <@{user_id}> 解封。")


    @app_commands.command(name=app_commands.locale_str("kick"), description="踢出用戶")
    @app_commands.describe(user="選擇用戶（@或ID）", reason="踢出原因（可選）")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def kick_user(self, interaction: discord.Interaction, user: discord.Member, reason: str = "無"):
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("此指令只能在伺服器中使用。")
            return

        # check bot permissions
        if not guild.me.guild_permissions.kick_members:
            await interaction.followup.send("機器人沒有踢出成員的權限，請確認機器人擁有「踢出成員」的權限。")
            return

        # 解析目標 user id / 取得 Member 物件
        user_id = user.id

        # 通知與忽略
        ModerationNotify.ignore_user(user_id)
        try:
            await ModerationNotify.notify_user(user, guild, "踢出", reason)
        except Exception:
            pass

        # 執行踢出
        try:
            await user.kick(reason=reason)
        except Exception as e:
            await interaction.followup.send(f"踢出時發生錯誤：{e}")
            return

        suffix = f"\n- 原因：{reason}" if reason != "無" else ""
        await interaction.followup.send(f"已將 {user.mention} 踢出伺服器。{suffix}")


    @app_commands.command(name=app_commands.locale_str("timeout"), description="禁言用戶")
    @app_commands.describe(user="選擇用戶", reason="禁言原因（可選）", duration="禁言時間（可選，預設10分鐘）")
    @app_commands.default_permissions(mute_members=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def timeout_user(self, interaction: discord.Interaction, user: discord.Member, reason: str = "無", duration: str = "10m"):
        # 先 defer，避免耗時操作導致 interaction 過期
        await interaction.response.defer()

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("此指令只能在伺服器中使用。")
            return
        
        # check bot permissions
        if not guild.me.guild_permissions.moderate_members:
            await interaction.followup.send("機器人沒有禁言的權限，請確認機器人擁有「管理成員」的權限。")
            return
        # 解析 target
        user_id = user.id

        duration_seconds = timestr_to_seconds(duration)
        if duration_seconds <= 0:
            await interaction.followup.send("無效的禁言時間，請使用類似 10m、2h、3d 的格式。")
            return

        # 執行禁言（可能耗時）
        try:
            await user.timeout(timedelta(seconds=duration_seconds), reason=reason)
        except Exception as e:
            print(f"[!] 禁言 {user} 時發生錯誤：{e}")
            await interaction.followup.send(f"禁言時發生錯誤：{e}")
            return

        # 使用 followup 送出最終訊息
        suffix = f"\n- 原因：{reason}" if reason != "無" else ""
        await interaction.followup.send(f"已對 {user.mention} 禁言 {get_time_text(duration_seconds)}。{suffix}")
        
    @app_commands.command(name=app_commands.locale_str("untimeout"), description="解除用戶禁言")
    @app_commands.describe(user="選擇用戶")
    @app_commands.default_permissions(mute_members=True)
    async def untimeout_user(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer()

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("此指令只能在伺服器中使用。")
            return
        
        # check bot permissions
        if not guild.me.guild_permissions.moderate_members:
            await interaction.followup.send("機器人沒有解除禁言的權限，請確認機器人擁有「管理成員」的權限。")
            return
        # 解析 target
        user_id = user.id

        # 執行解除禁言
        try:
            await user.timeout(None, reason="解除禁言")
        except Exception as e:
            print(f"[!] 解除禁言 {user} 時發生錯誤：{e}")
            await interaction.followup.send(f"解除禁言時發生錯誤：{e}")
            return

        await interaction.followup.send(f"已對 {user.mention} 解除禁言。")
        
    @app_commands.command(name=app_commands.locale_str("moderation-message-channel"), description="設定懲處公告頻道")
    @app_commands.describe(channel="選擇頻道")
    @app_commands.default_permissions(manage_channels=True)
    async def set_moderation_message_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer()
        permissions = channel.permissions_for(interaction.guild.me)
        if not (permissions.send_messages and permissions.view_channel):
            await interaction.followup.send("機器人在該頻道沒有發送訊息的權限，請先調整權限後再嘗試。")
            return
        set_server_config(interaction.guild.id, "MODERATION_MESSAGE_CHANNEL_ID", channel.id)
        await interaction.followup.send(f"已設定懲處公告頻道為 {channel.mention}。")
    
    @commands.command(aliases=["mod", "m"])
    async def moderate(self, ctx: commands.Context, user: Union[discord.Member, discord.User, None] = None, *, commands_str: str = ""):
        """對用戶進行多重管理操作。
        
        用法：!moderate <用戶> <指令1> , <指令2> , ...
        
        指令格式：
        - ban <duration> <delete_messages> <reason>
        - kick <reason>
        - timeout|mute <duration> <reason>
        - delete <warn_message>
        - delete_dm <warn_message>
        - warn <warn_message>
        - warn_dm <warn_message>
        - send_mod_message|smm
        
        範例：
        !moderate @User ban 違規 1d 3600 , mute 30m 注意行為 , delete 請注意你的言論
        """
        # check bot permissions
        if not ctx.guild.me.guild_permissions.ban_members or not ctx.guild.me.guild_permissions.kick_members or not ctx.guild.me.guild_permissions.manage_messages or not ctx.guild.me.guild_permissions.moderate_members:
            await ctx.send("機器人缺少必要的權限，請確認機器人擁有封禁、踢出、管理訊息及禁言權限。")
            return
        if user is None:
            await ctx.send("請指定要管理的用戶。")
            return
        if ctx.author.guild_permissions.ban_members is False and ctx.author.guild_permissions.kick_members is False and ctx.author.guild_permissions.moderate_members is False and ctx.author.guild_permissions.manage_messages is False:
            await ctx.send("你沒有權限執行此操作。" + ('\n-# 你傻逼吧你以為你是開發者你就可以濫權？' if ctx.author.id in config('owners') else ''))
            return
        logs = await do_action_str(commands_str, ctx.guild, user, message=None, moderator=ctx.author)
        if len(logs) == 0:
            msg = "無任何操作被執行。"
        elif len(logs) == 1:
            msg = user.name + " 操作完成：" + logs[0]
        else:
            msg = user.name + " 操作完成：\n- " + "\n- ".join(logs)
        await ctx.send(msg)
        log(msg, module_name="Moderate", guild=ctx.guild)
    
    @commands.command(aliases=["mr", "mod_reply"])
    async def moderate_reply(self, ctx: commands.Context, *, commands_str: str = ""):
        """對訊息發送者進行多重管理操作。
        
        用法：!moderate_reply <指令1> , <指令2> , ...
        
        指令格式：
        - ban <duration> <delete_messages> <reason>
        - kick <reason>
        - timeout|mute <duration> <reason>
        - delete <warn_message>
        - delete_dm <warn_message>
        - warn <warn_message>
        - warn_dm <warn_message>
        - send_mod_message|smm
        
        範例：
        !moderate_reply ban 違規 1d 3600 , mute 30m 注意行為 , delete 請注意你的言論
        """
        # check bot permissions
        if not ctx.guild.me.guild_permissions.ban_members or not ctx.guild.me.guild_permissions.kick_members or not ctx.guild.me.guild_permissions.manage_messages or not ctx.guild.me.guild_permissions.moderate_members:
            await ctx.send("機器人缺少必要的權限，請確認機器人擁有封禁、踢出、管理訊息及禁言權限。")
            return
        if ctx.author.guild_permissions.ban_members is False and ctx.author.guild_permissions.kick_members is False and ctx.author.guild_permissions.moderate_members is False and ctx.author.guild_permissions.manage_messages is False:
            await ctx.send("你沒有權限執行此操作。" + ('\n-# 你傻逼吧你以為你是開發者你就可以濫權？' if ctx.author.id in config('owners') else ''))
            return
        if ctx.message.reference is None:
            await ctx.send("請在回覆的訊息中使用此指令。")
            return
        try:
            referenced_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        except Exception:
            await ctx.send("無法取得被回覆的訊息。")
            return
        user = referenced_message.author if isinstance(referenced_message.author, discord.Member) else None
        logs = await do_action_str(commands_str, ctx.guild, user, message=referenced_message, moderator=ctx.author)
        if len(logs) == 0:
            msg = "無任何操作被執行。"
        elif len(logs) == 1:
            msg = user.name + " 操作完成：" + logs[0]
        else:
            msg = user.name + " 操作完成：\n- " + "\n- ".join(logs)
        await ctx.send(msg)
        log(msg, module_name="Moderate", user=user, guild=ctx.guild)


asyncio.run(bot.add_cog(Moderate(bot)))


if __name__ == "__main__":
    start_bot()
