import time
import discord
import threading
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta, timezone
from globalenv import bot, start_bot, get_server_config, set_server_config, get_user_data, set_user_data
from logger import log
import logging
import asyncio


ignore = []

def _ingore_user(user_id: int):
    if user_id not in ignore:
        ignore.append(user_id)
        time.sleep(5)  # 避免重複觸發
        ignore.remove(user_id)

def ignore_user(user_id: int):
    threading.Thread(target=_ingore_user, args=(user_id,)).start()
    

ch2en_map = {
    "踢出": "kick",
    "封禁": "ban",
    "禁言": "mute",
}

class ResponseAppealView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="回覆申訴", style=discord.ButtonStyle.primary, emoji="⚖️", custom_id="response_appeal_button")
    async def response_appeal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        origself = self
        class ResponseAppealModal(discord.ui.Modal, title="回覆用戶申訴"):
            response = discord.ui.TextInput(label="請輸入你的回覆內容", style=discord.TextStyle.paragraph, required=True, max_length=1000)

            async def on_submit(self, modal_interaction: discord.Interaction):
                message = interaction.message  # 直接使用 interaction.message
                user_id = int(message.embeds[0].fields[0].value)  # 從嵌入訊息中取得用戶 ID (fields[0] 是用戶 ID)
                user = await bot.fetch_user(user_id)  # 獲取用戶對象
                embed = discord.Embed(
                    title="申訴回覆",
                    description=f"你收到了來自伺服器 {modal_interaction.guild.name} 管理員的申訴回覆。",
                    color=discord.Color.green(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.add_field(name="回覆內容", value=self.response.value, inline=False)
                try:
                    await user.send(embed=embed)
                    await modal_interaction.response.send_message("你的回覆已發送給用戶。", ephemeral=True)
                except discord.Forbidden:
                    await modal_interaction.response.send_message("無法發送訊息給該用戶，用戶可能已關閉私訊。", ephemeral=True)
                    return
                for child in origself.children:
                    child.disabled = True
                await interaction.edit_original_response(view=origself)
                origself.stop()
        await interaction.response.send_modal(ResponseAppealModal())
        

class AppealView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="提出申訴", style=discord.ButtonStyle.primary, emoji="📩", custom_id="appeal_button")
    async def appeal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        origself = self
        class AppealModal(discord.ui.Modal, title="用戶申訴"):
            reason = discord.ui.TextInput(label="請描述你的申訴理由", style=discord.TextStyle.paragraph, required=True, max_length=1000)

            async def on_submit(self, modal_interaction: discord.Interaction):
                message = interaction.message  # 直接使用 interaction.message (DM 中也能用)
                guild_id = int(message.embeds[0].fields[0].value)  # 從嵌入訊息中取得伺服器 ID
                appeal_channel_id = get_server_config(guild_id, "user_appeal_channel")
                appeal_channel = bot.get_channel(appeal_channel_id) if appeal_channel_id else None  # 移除 modal_interaction.guild 條件
                if not appeal_channel:
                    await modal_interaction.response.send_message("申訴頻道未設置或無法訪問，請聯繫管理員。", ephemeral=True)
                    return
                embed = discord.Embed(
                    title="新的用戶申訴",
                    description=f"來自用戶 {modal_interaction.user.mention} (`{modal_interaction.user.id}`) 的申訴。",
                    color=discord.Color.blue(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.add_field(name="用戶 ID", value=str(modal_interaction.user.id), inline=False)
                embed.add_field(name="申訴理由", value=self.reason.value, inline=False)
                embed.set_author(name=modal_interaction.user.display_name, icon_url=modal_interaction.user.display_avatar.url)
                await appeal_channel.send(embed=embed, view=ResponseAppealView())  # 添加回覆按鈕
                await modal_interaction.response.send_message("你的申訴已提交，管理員將會審核你的申訴。", ephemeral=True)
                for child in origself.children:
                    child.disabled = True
                await interaction.edit_original_response(view=origself)
                origself.stop()
        await interaction.response.send_modal(AppealModal())

async def notify_user(user: discord.User, guild: discord.Guild, action: str, reason: str = "未提供", end_time=None):
    en_action = ch2en_map.get(action, action.lower())
    if not get_server_config(guild.id, f"notify_user_on_{en_action}", True):
        return
    embed = discord.Embed(
        title=f"你在 {guild.name} 被{action}。",
        description=f"原因：{reason}",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc)  # 訊息時間
    )
    
    embed.add_field(name="伺服器 ID", value=guild.id, inline=True)

    # add server icon
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    # if present
    # print("Debug:", end_time)
    if end_time:
        embed.add_field(name="解禁時間", value=f"<t:{str(int(end_time.timestamp()))}:F>", inline=False)

    embed.set_footer(text=f"{guild.name}")
    if get_server_config(guild.id, "user_appeal_channel"):
        embed.add_field(name="申訴方式", value="你可以點擊下方按鈕提出申訴。", inline=False)
        view = AppealView()
    else:
        view = None

    try:
        msg = await user.send(embed=embed, view=view)
        log(f"已發送私訊給 {user}\n- {embed.title}\n- {embed.description}", module_name="ModerationNotify", guild=guild)
        return msg
    except discord.Forbidden:
        log(f"無法私訊 {user}", level=logging.ERROR, module_name="ModerationNotify", guild=guild)


@bot.event
async def on_member_remove(member):
    if member.bot:
        return
    if member.id in ignore:
        return
    guild = member.guild
    try:
        async for entry in guild.audit_logs(limit=1):
            if entry.target.id != member.id:
                continue

            if entry.action == discord.AuditLogAction.kick:  # kick
                if not get_server_config(guild.id, "notify_user_on_kick", True):
                    return
                await notify_user(member, guild, "踢出", entry.reason or "未提供")
            elif entry.action == discord.AuditLogAction.ban:  # ban
                if not get_server_config(guild.id, "notify_user_on_ban", True):
                    return
                await notify_user(member, guild, "封禁", entry.reason or "未提供")
            else:
                pass
    except Exception as e:
        # print(f"Error fetching audit logs: {e}")
        log(f"Error fetching audit logs: {e}", level=logging.ERROR, module_name="ModerationNotify", guild=guild)
        # await notify_user(member, guild, "移除", "無法取得")


# timeout
@bot.event
async def on_member_update(before, after):
    if after.bot:
        return
    if not get_server_config(after.guild.id, "notify_user_on_mute", True):
        return
    if before.timed_out_until != after.timed_out_until and after.timed_out_until is not None:
        # 檢查database的值避免重複
        if get_user_data(after.guild.id, after.id, "muted_until") == after.timed_out_until.isoformat():
            return
        if after.timed_out_until <= datetime.now(timezone.utc):
            return
        set_user_data(after.guild.id, after.id, "muted_until", after.timed_out_until.isoformat())
        guild = after.guild
        try:
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.member_update):
                if entry.target.id == after.id:
                    reason = entry.reason or "未提供"
                    end_time = after.timed_out_until.astimezone(timezone(timedelta(hours=8)))  # 台灣時間
                    await notify_user(after, guild, "禁言", reason, end_time)
        except Exception as e:
            log(f"Error fetching audit logs: {e}", level=logging.ERROR, module_name="ModerationNotify", guild=guild)
            await notify_user(after, guild, "禁言", "無法取得", after.timed_out_until)


class ModerationNotify(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name=app_commands.locale_str("settings-punishment-notify"), description="設定是否通知被懲罰的用戶")
    @app_commands.describe(
        action="選擇要設定的懲罰類型",
        enable="是否啟用通知"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="踢出", value="kick"),
        app_commands.Choice(name="封禁", value="ban"),
        app_commands.Choice(name="禁言", value="mute"),
    ])
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def set_moderation_notification(self, interaction: discord.Interaction, action: str, enable: bool):
        guild = interaction.guild
        if action not in ["kick", "ban", "mute"]:
            await interaction.response.send_message("無效的懲罰類型。", ephemeral=True)
            return

        set_server_config(guild.id, f"notify_user_on_{action}", enable)
        await interaction.response.send_message(f"已將 {action} 通知設定為{'啟用' if enable else '禁用'}。", ephemeral=True)
        log(f"已將 {action} 通知設定為{'啟用' if enable else '禁用'}。", module_name="ModerationNotify", guild=guild)
    
    @app_commands.command(name=app_commands.locale_str("user-appeal-channel"), description="設置用戶申訴頻道，若未設置則關閉。")
    @app_commands.describe(channel="要設置的用戶申訴頻道，留空則關閉申訴功能。")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def set_user_appeal_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        guild = interaction.guild
        if channel:
            if channel.permissions_for(interaction.guild.me).send_messages is False:
                await interaction.response.send_message("機器人沒有在該頻道發送訊息的權限，請選擇其他頻道。", ephemeral=True)
                return
            set_server_config(guild.id, "user_appeal_channel", channel.id)
            await interaction.response.send_message(f"用戶申訴頻道已設置為：{channel.mention}", ephemeral=True)
            log(f"設置用戶申訴頻道為 {channel} ({channel.id})", module_name="ModerationNotify", guild=guild)
        else:
            # remove the appeal channel
            set_server_config(guild.id, "user_appeal_channel", None)
            await interaction.response.send_message("用戶申訴功能已被禁用。", ephemeral=True)
            log("禁用用戶申訴功能", module_name="ModerationNotify", guild=guild)
    
asyncio.run(bot.add_cog(ModerationNotify(bot)))

# 註冊持久化 View，讓機器人重啟後按鈕仍可用
bot.add_view(AppealView())
bot.add_view(ResponseAppealView())

if __name__ == "__main__":
    start_bot()
