from globalenv import bot, start_bot, get_server_config, set_server_config, get_user_data, set_user_data, on_ready_tasks
import discord
from discord import app_commands
from discord.ext import commands
import json
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone
from typing import Union
import ModerationNotify


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
    if num:  # 如果字串以數字結尾，則忽略這些數字
        pass
    return total_seconds


def get_time_text(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} 秒"
    elif seconds < 3600:
        return f"{seconds // 60} 分鐘"
    elif seconds < 86400:
        return f"{seconds // 3600} 小時"
    else:
        return f"{seconds // 86400} 天"


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


async def check_unban():
    await bot.wait_until_ready()
    print("[+] 自動解封任務已啟動")
    while not bot.is_closed():
        for guild in bot.guilds:
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
                    print(f"[+] 已自動解封 {user} 在 {guild.name} 的封禁。")
                except Exception as e:
                    print(f"[!] 解封 {user} 時發生錯誤：{e}")

        await asyncio.sleep(60)  # 每分鐘檢查一次
on_ready_tasks.append(check_unban)


async def moderation_message_settings(interaction: discord.Interaction, user: discord.Member, moderator: discord.Member, actions: list):
    # generate message
    action_texts = []
    for action in actions:
        if action["action"] == "ban":
            action_texts.append("驅逐出境至柬服KK副本||永久停權||")
        elif action["action"] == "kick":
            action_texts.append("踢出")
        elif action["action"] == "mute":
            time_text = action.get("duration", 0)
            action_texts.append(f"羈押禁見||禁言||{get_time_text(time_text * 60)}")
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
    def generate_message():
        return f"""
### ⛔ 違規處分
> - 被處分者： {user.mention}
> - 處分原因：{reason}
> - 處分結果：{action_text}
> - 處分執行： {moderator.mention}
"""
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
            channel_id = get_server_config(interaction.guild.id, "MODERATION_MESSAGE_CHANNEL_ID")
            if channel_id is None:
                await interaction.response.send_message("伺服器未設定公告頻道，請先設定後再嘗試。", ephemeral=True)
                return
            channel = interaction.guild.get_channel(channel_id)
            if channel is None:
                await interaction.response.send_message("找不到公告頻道，請確認頻道是否存在。", ephemeral=True)
                return
            try:
                await channel.send(generate_message())
                await interaction.response.edit_message(content="公告已發送。", view=None)
            except discord.Forbidden:
                await interaction.response.send_message("無法在公告頻道發送訊息，機器人缺少權限。", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message(f"發送公告時發生錯誤：{e}", ephemeral=True)
    await interaction.response.send_message(embed=embed, view=MessageButtons())
            

@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
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
                    role_name = discord.ui.TextInput(label="身分組名稱", placeholder="請輸入身分組名稱/ID/關鍵字", required=True, max_length=100)
                    async def on_submit(self, interaction: discord.Interaction):
                        if not interaction.user.guild_permissions.administrator:
                            await interaction.response.send_message("你沒有權限執行此操作。", ephemeral=True)
                            return
                        role_id = guess_role(interaction.guild, self.role_name.value)
                        if role_id is None:
                            await interaction.response.send_message("找不到指定的身分組，請確認名稱或 ID 是否正確。", ephemeral=True)
                            return
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
    @app_commands.describe(user="選擇用戶（@或ID）", reason="封禁原因（可選）", duration="封禁時間（可選，預設永久）", delete_message="刪除訊息時間（可選，預設不刪除）")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.default_permissions(ban_members=True)
    async def ban_user(self, interaction: discord.Interaction, user: str, reason: str = "無", duration: str = "", delete_message: str = ""):
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("此指令只能在伺服器中使用。")
            return
        
        if user.startswith("<@") and user.endswith(">"):
            user = user[2:-1]
            if user.startswith("!"):
                user = user[1:]

        # 解析目標 user id / 取得 User/Member 物件（若在伺服器內會是 Member）
        if isinstance(user, discord.Member):
            user_id = user.id
            user_obj = user
        else:
            try:
                user_id = int(user)
            except Exception:
                await interaction.followup.send("無效的使用者或 ID。")
                return
            user_obj = None
            try:
                user_obj = await bot.fetch_user(user_id)
            except Exception:
                user_obj = None  # 仍可以 id 封禁，但無法直接私訊

        # 解析封禁時間（可選，若提供則記錄 unban_time）
        unban_time = None
        if duration:
            duration_seconds = timestr_to_seconds(duration)
            if duration_seconds <= 0:
                await interaction.followup.send("無效的封禁時間，請使用類似 10m、2h、3d 的格式。")
                return
            unban_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

        # 記錄解封時間（若為永久則存 None）
        set_user_data(guild.id, user_id, "unban_time", unban_time.isoformat() if unban_time else None)

        # 通知與忽略（使用 id 與可用的 user 物件）
        ModerationNotify.ignore_user(user_id)
        try:
            await ModerationNotify.notify_user(user_obj if user_obj else user_id, guild, "封禁", reason, end_time=unban_time)
        except Exception:
            # 如果 notify_user 期待 Member/User 物件但我們只有 id，忽略錯誤
            pass

        # 解析要刪除訊息的秒數
        delete_message_seconds = timestr_to_seconds(delete_message)

        # 執行封禁：若有 Member 直接用 member.ban，否則用 guild.ban 與 discord.Object(id=...)
        try:
            if isinstance(user, discord.Member):
                await user.ban(reason=reason, delete_message_seconds=delete_message_seconds)
            else:
                await guild.ban(discord.Object(id=user_id), reason=reason, delete_message_seconds=delete_message_seconds)
        except Exception as e:
            await interaction.followup.send(f"封禁時發生錯誤：{e}")
            return

        mention = user_obj.mention if user_obj else f"<@{user_id}>"
        await interaction.followup.send(f"已將 {mention} 封禁。")


    @app_commands.command(name=app_commands.locale_str("unban"), description="解封用戶")
    @app_commands.describe(user="選擇用戶（@或ID）")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def unban_user(self, interaction: discord.Interaction, user: str):
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("此指令只能在伺服器中使用。")
            return
        
        if user.startswith("<@") and user.endswith(">"):
            user = user[2:-1]
            if user.startswith("!"):
                user = user[1:]

        # 解析目標 user id
        try:
            user_id = int(user)
        except Exception:
            await interaction.followup.send("無效的使用者或 ID。")
            return

        # 執行解封
        try:
            await guild.unban(discord.Object(id=user_id), reason="手動解封")
            set_user_data(guild.id, user_id, "unban_time", None)
        except Exception as e:
            await interaction.followup.send(f"解封時發生錯誤：{e}")
            return

        await interaction.followup.send(f"已將 <@{user_id}> 解封。")


    @app_commands.command(name=app_commands.locale_str("kick"), description="踢出用戶")
    @app_commands.describe(user="選擇用戶（@或ID）", reason="踢出原因（可選）")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def kick_user(self, interaction: discord.Interaction, user: str, reason: str = "無"):
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("此指令只能在伺服器中使用。")
            return
        
        if user.startswith("<@") and user.endswith(">"):
            user = user[2:-1]
            if user.startswith("!"):
                user = user[1:]

        # 解析目標 user id / 取得 Member 物件
        if isinstance(user, discord.Member):
            user_id = user.id
            member = user
        else:
            try:
                user_id = int(user)
            except Exception:
                await interaction.followup.send("無效的使用者或 ID。")
                return
            member = guild.get_member(user_id)
            if member is None:
                await interaction.followup.send("該用戶不在伺服器中，無法踢出。")
                return

        # 通知與忽略
        ModerationNotify.ignore_user(user_id)
        try:
            await ModerationNotify.notify_user(member, guild, "踢出", reason)
        except Exception:
            pass

        # 執行踢出
        try:
            await member.kick(reason=reason)
        except Exception as e:
            await interaction.followup.send(f"踢出時發生錯誤：{e}")
            return

        await interaction.followup.send(f"已將 {member.mention} 踢出伺服器。")


    @app_commands.command(name=app_commands.locale_str("timeout"), description="禁言用戶")
    @app_commands.describe(user="選擇用戶（@或ID）", reason="禁言原因（可選）", duration="禁言時間（可選，預設10分鐘）")
    @app_commands.default_permissions(mute_members=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def timeout_user(self, interaction: discord.Interaction, user: str, reason: str = "無", duration: str = "10m"):
        # 先 defer，避免耗時操作導致 interaction 過期
        await interaction.response.defer()

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("此指令只能在伺服器中使用。")
            return

        if user.startswith("<@") and user.endswith(">"):
            user = user[2:-1]
            if user.startswith("!"):
                user = user[1:]

        # 解析 target
        if isinstance(user, discord.Member):
            user_id = user.id
            member = user
        else:
            try:
                user_id = int(user)
            except Exception:
                await interaction.followup.send("無效的使用者或 ID。")
                return
            member = guild.get_member(user_id)
            if member is None:
                await interaction.followup.send("該用戶不在伺服器中，無法禁言。")
                return

        duration_seconds = timestr_to_seconds(duration)
        if duration_seconds <= 0:
            await interaction.followup.send("無效的禁言時間，請使用類似 10m、2h、3d 的格式。")
            return

        # 執行禁言（可能耗時）
        try:
            await member.timeout(timedelta(seconds=duration_seconds), reason=reason)
        except Exception as e:
            print(f"[!] 禁言 {member} 時發生錯誤：{e}")
            await interaction.followup.send(f"禁言時發生錯誤：{e}")
            return

        # 使用 followup 送出最終訊息
        await interaction.followup.send(f"已對 {member.mention} 禁言 {get_time_text(duration_seconds)}。")


asyncio.run(bot.add_cog(Moderate(bot)))


if __name__ == "__main__":
    start_bot()
