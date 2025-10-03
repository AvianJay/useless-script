from globalenv import bot, start_bot, get_server_config, set_server_config, get_user_data, set_user_data
import discord
from discord import app_commands
from discord.ext import commands
import json
import aiohttp
from datetime import datetime, timedelta, timezone


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


async def timeout_user(*, user_id: int, guild_id: int, until, reason: str="") -> bool:
    headers = {"Authorization": f"Bot {bot.http.token}"}
    url = f"https://discord.com/api/v9/guilds/{guild_id}/members/{user_id}"
    timeout_dt = discord.utils.utcnow() + timedelta(seconds=until)
    timeout = timeout_dt.replace(microsecond=0).isoformat()
    payload = {'communication_disabled_until': timeout, 'reason': reason}
    async with aiohttp.ClientSession() as session:
        async with session.patch(url, json=payload, headers=headers) as resp:
            if resp.status in range(200, 299):
                return True
            return False


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
            


@bot.tree.command(name="管理-多重操作", description="對用戶進行多重操作")
@app_commands.describe(user="選擇用戶")
@app_commands.default_permissions(administrator=True)
@app_commands.allowed_installs(guilds=True, users=False)
async def multi_moderate(interaction: discord.Interaction, user: discord.Member):
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
                        await timeout_user(user_id=user.id, guild_id=interaction.guild.id, until=duration*60, reason=action.get("reason", "無"))
                        results.append(f"已對 {user.mention} 禁言 {get_time_text(duration)}。")
                    elif action["action"] == "kick":
                        await user.kick(reason=action.get("reason", "無"))
                        results.append(f"已將 {user.mention} 踢出伺服器。")
                    elif action["action"] == "ban":
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