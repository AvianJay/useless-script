import discord
from datetime import datetime, timedelta, timezone
from globalenv import bot, start_bot


async def notify_user(user: discord.User, guild: discord.Guild, action: str, reason: str = "未提供", end_time=None):
    embed = discord.Embed(
        title=f"你在 {guild.name} 被{action}。",
        description=f"原因：{reason}",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc)  # 訊息時間
    )

    # add server icon
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    # if mute
    if end_time:
        embed.add_field(name="解禁時間", value=end_time.strftime("%Y-%m-%d %H:%M:%S"), inline=False)

    embed.set_footer(text=f"{guild.name}")

    try:
        await user.send(embed=embed)
    except discord.Forbidden:
        print(f"無法私訊 {user}")


@bot.event
async def on_member_remove(member):
    guild = member.guild
    try:
        async for entry in guild.audit_logs(limit=1):
            if entry.target.id != member.id:
                continue

            if entry.action == discord.AuditLogAction.kick:
                await notify_user(member, guild, "踢出", entry.reason or "未提供")
            elif entry.action == discord.AuditLogAction.ban:
                # ban
                await notify_user(member, guild, "封禁", entry.reason or "未提供")
            else:
                pass
    except Exception as e:
        print(f"Error fetching audit logs: {e}")
        await notify_user(member, guild, "移除", "無法取得")


# timeout
@bot.event
async def on_member_update(before, after):
    if before.timed_out_until != after.timed_out_until and after.timed_out_until is not None:
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


if __name__ == "__main__":
    start_bot()
