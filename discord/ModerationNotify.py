import time
import discord
import threading
from discord import app_commands
from datetime import datetime, timedelta, timezone
from globalenv import bot, start_bot, get_server_config, set_server_config, get_user_data, set_user_data


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

    # add server icon
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    # if present
    # print("Debug:", end_time)
    if end_time:
        embed.add_field(name="解禁時間", value=f"<t:{str(int(end_time.timestamp()))}:F>", inline=False)

    embed.set_footer(text=f"{guild.name}")

    try:
        await user.send(embed=embed)
    except discord.Forbidden:
        print(f"無法私訊 {user}")


@bot.event
async def on_member_remove(member):
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
        print(f"Error fetching audit logs: {e}")
        # await notify_user(member, guild, "移除", "無法取得")


# timeout
@bot.event
async def on_member_update(before, after):
    if not get_server_config(after.guild.id, "notify_user_on_mute", True):
        return
    if before.timed_out_until != after.timed_out_until and after.timed_out_until is not None:
        # 檢查database的值避免重複
        if get_user_data(after.guild.id, after.id, "muted_until") == after.timed_out_until.isoformat():
            return
        if after.communication_disabled_until <= datetime.now(timezone.utc):
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
            print(f"Error fetching audit logs: {e}")
            await notify_user(after, guild, "禁言", "無法取得", after.timed_out_until)


@bot.tree.command(name=app_commands.locale_str("settings-punishment-notify"), description="設定是否通知被懲罰的用戶")
@app_commands.describe(
    action="選擇要設定的懲罰類型",
    enable="是否啟用通知"
)
@app_commands.choices(action=[
    app_commands.Choice(name="踢出", value="kick"),
    app_commands.Choice(name="封禁", value="ban"),
    app_commands.Choice(name="禁言", value="mute"),
])
async def set_moderation_notification(interaction: discord.Interaction, action: str, enable: bool):
    guild = interaction.guild
    if action not in ["kick", "ban", "mute"]:
        await interaction.response.send_message("無效的懲罰類型。", ephemeral=True)
        return

    set_server_config(guild.id, f"notify_user_on_{action}", enable)
    await interaction.response.send_message(f"已將 {action} 通知設定為{'啟用' if enable else '禁用'}。", ephemeral=True)


if __name__ == "__main__":
    start_bot()
