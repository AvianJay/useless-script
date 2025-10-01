import discord
from globalenv import bot, start_bot, get_user_data, set_user_data, config, get_server_config, set_server_config, _config
from discord.ext import commands
from typing import Callable

def is_owner() -> Callable:
    async def predicate(ctx):
        return ctx.author.id in config("owners", [])
    return commands.check(predicate)

@bot.command()
@is_owner()
async def settings(ctx, key: str=None, value: str=None):
    if key is None:
        await ctx.send("目前設定：\n" + "\n".join(f"- {k}: {v}" for k, v in _config.items()))
    elif value is None:
        await ctx.send(f"{key}: {config(key, '未設定')}")
    elif key in config().keys():
        config(key, value, mode="w")
        await ctx.send(f"已更新 {key} 為 {value}。")


@bot.command()
@is_owner()
async def shutdown(ctx):
    await ctx.send("機器人正在關閉...")
    await bot.close()


@bot.command()
@is_owner()
async def userdata(ctx, guild_id: int=None, user_id: int=None, key: str=None, value: str=None):
    if guild_id is None or user_id is None:
        await ctx.send("請提供 guild_id 和 user_id。")
        return
    if key is None:
        all_data = get_all_user_data(guild_id, "")
        user_data = all_data.get(str(user_id), {})
        if not user_data:
            await ctx.send("沒有找到該用戶的資料。")
            return
        await ctx.send(f"用戶 {user_id} 的資料：\n" + "\n".join(f"- {k}: {v}" for k, v in user_data.items()))
    elif value is None:
        data = get_user_data(guild_id, user_id, key)
        await ctx.send(f"用戶 {user_id} 的 {key}: {data if data is not None else '未設定'}")
    else:
        set_user_data(guild_id, user_id, key, value)
        await ctx.send(f"已更新用戶 {user_id} 的 {key} 為 {value}。")


@bot.command()
@is_owner()
async def serverconfig(ctx, guild_id: int=None, key: str=None, value: str=None):
    if guild_id is None:
        await ctx.send("請提供 guild_id。")
        return
    if key is None:
        # show all config
        config_data = db.get_all_server_config(guild_id)
        if not config_data:
            await ctx.send("沒有找到該伺服器的設定。")
            return
        await ctx.send(f"伺服器 {guild_id} 的設定：\n" + "\n".join(f"- {k}: {v}" for k, v in config_data.items()))
    elif value is None:
        data = get_server_config(guild_id, key)
        await ctx.send(f"伺服器 {guild_id} 的 {key}: {data if data is not None else '未設定'}")
    else:
        set_server_config(guild_id, key, value)
        await ctx.send(f"已更新伺服器 {guild_id} 的 {key} 為 {value}。")


@bot.command()
@is_owner()
async def leaveserver(ctx, guild_id: int):
    guild = bot.get_guild(guild_id)
    if guild is None:
        await ctx.send("找不到該伺服器。")
        return
    await guild.leave()
    await ctx.send(f"已離開伺服器 {guild.name} (ID: `{guild.id}`) 。")
    
    
@bot.command()
@is_owner()
async def getinvite(ctx, guild_id: int, create_if_none: bool=False):
    guild = bot.get_guild(guild_id)
    if guild is None:
        await ctx.send("找不到該伺服器。")
        return
    # Try to fetch existing invites
    try:
        invites = await guild.invites()
    except discord.Forbidden:
        # Lacking permission to list invites
        if create_if_none:
            # Try to create an invite in a channel the bot can create invites in
            channel = None
            for ch in guild.text_channels:
                perms = ch.permissions_for(guild.me)
                if perms.create_instant_invite:
                    channel = ch
                    break
            if channel is None:
                await ctx.send("無法取得邀請清單，且找不到可用來建立邀請的頻道或機器人缺少權限。")
                return
            try:
                invite = await channel.create_invite(max_age=0, max_uses=0, unique=True)
                await ctx.send(f"已替伺服器 {guild.name} 建立邀請連結：{invite.url}")
            except discord.Forbidden:
                await ctx.send("無法創建邀請連結，機器人缺少在該頻道建立邀請的權限。")
            return
        else:
            await ctx.send("無法取得邀請清單，機器人缺少列出邀請的權限。若要自動建立邀請請將 create_if_none 設為 True。")
            return

    # If we got invites list successfully
    if not invites:
        # No existing invites
        if create_if_none:
            channel = None
            for ch in guild.text_channels:
                perms = ch.permissions_for(guild.me)
                if perms.create_instant_invite:
                    channel = ch
                    break
            if channel is None:
                await ctx.send("該伺服器沒有任何邀請連結，且找不到可用於建立邀請的頻道。")
                return
            try:
                invite = await channel.create_invite(max_age=0, max_uses=0, unique=True)
                await ctx.send(f"已替伺服器 {guild.name} 建立邀請連結：{invite.url}")
            except discord.Forbidden:
                await ctx.send("無法創建邀請連結，機器人缺少建立邀請的權限。")
            return
        else:
            await ctx.send("該伺服器沒有任何邀請連結。若要自動建立邀請請將 create_if_none 設為 True。")
            return

    # Return the first invite found
    invite = invites[0]
    await ctx.send(f"伺服器 {guild.name} 的邀請連結：{invite.url}")


@bot.command()
@is_owner()
async def listservers(ctx):
    guilds = bot.guilds
    if not guilds:
        await ctx.send("機器人目前沒有加入任何伺服器。")
        return
    description = "\n".join(f"- {g.name} (ID: `{g.id}`)" for g in guilds)
    await ctx.send(f"機器人目前加入的伺服器：\n{description}")


@bot.command()
@is_owner()
async def sendmessage(ctx, channel_id: int, *, message: str):
    channel = bot.get_channel(channel_id)
    if channel is None:
        await ctx.send("找不到該頻道。")
        return
    try:
        await channel.send(message)
        await ctx.send(f"已在頻道 {channel.name} 發送訊息。")
    except discord.Forbidden:
        await ctx.send("無法在該頻道發送訊息，機器人缺少權限。")
    except Exception as e:
        await ctx.send(f"發送訊息時發生錯誤：{e}")


@bot.event
async def on_guild_join(guild):
    print(f"Joined guild: {guild.name} (ID: {guild.id})")
    # send to owners
    for owner_id in config("owners", []):
        owner = bot.get_user(owner_id)
        if owner:
            try:
                # build embed with server icon and member count
                embed = discord.Embed(
                    title="已加入新伺服器",
                    description=f"{guild.name} (ID: `{guild.id}`)",
                    color=discord.Color.blurple()
                )
                embed.add_field(name="成員數", value=str(getattr(guild, "member_count", "未知")), inline=True)

                # try to get icon URL (works for discord.py v1.x and v2.x)
                icon_url = None
                if getattr(guild, "icon", None):
                    try:
                        icon_url = guild.icon.url  # v2.x
                    except Exception:
                        icon_url = getattr(guild, "icon_url", None)  # v1.x fallback
                if icon_url:
                    embed.set_thumbnail(url=icon_url)

                await owner.send(embed=embed)
            except discord.Forbidden:
                print(f"無法私訊擁有者 {owner_id}")
                

@bot.event
async def on_guild_remove(guild):
    print(f"Removed from guild: {guild.name} (ID: {guild.id})")
    # send to owners
    for owner_id in config("owners", []):
        owner = bot.get_user(owner_id)
        if owner:
            try:
                embed = discord.Embed(
                    title="已離開伺服器",
                    description=f"{guild.name} (ID: `{guild.id}`)",
                    color=discord.Color.red()
                )
                icon_url = None
                if getattr(guild, "icon", None):
                    try:
                        icon_url = guild.icon.url  # v2.x
                    except Exception:
                        icon_url = getattr(guild, "icon_url", None)  # v1.x fallback
                if icon_url:
                    embed.set_thumbnail(url=icon_url)
                await owner.send(embed=embed)
            except discord.Forbidden:
                print(f"無法私訊擁有者 {owner_id}")


if __name__ == "__main__":
    start_bot()
