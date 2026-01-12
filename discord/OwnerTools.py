import io
import json
import discord
from globalenv import bot, start_bot, get_user_data, set_user_data, config, get_server_config, set_server_config, _config, default_config, get_all_user_data, db, on_close_tasks, reload_config
from discord.ext import commands
from typing import Callable
import chat_exporter
from logger import log
import logging
from typing import Union

def is_owner() -> Callable:
    async def predicate(ctx):
        return ctx.author.id in config("owners", [])
    return commands.check(predicate)

@bot.command(aliases=["set", "cfg"])
@is_owner()
async def settings(ctx, key: str=None, value: str=None):
    if key is None:
        safe_config = _config.copy()
        safe_config["TOKEN"] = "<token>"
        await ctx.send("目前設定：\n" + "\n".join(f"- {k}: {v}" for k, v in safe_config.items()))
    elif value is None:
        await ctx.send(f"{key}: {config(key, '未設定')}")
    elif key in config().keys():
        # check original type
        original_type = type(default_config.get(key))
        try:
            if original_type is bool:
                if value.lower() in ['true', '1', 'yes', 'on']:
                    value = True
                elif value.lower() in ['false', '0', 'no', 'off']:
                    value = False
                else:
                    await ctx.send(f"無法將 {value} 轉換為布林值。請使用 true/false。")
                    return
            elif original_type is int:
                value = int(value)
            elif original_type is float:
                value = float(value)
            elif original_type is list:
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:  # fallback to comma split
                    value = [v.strip() for v in value.split(',')]
            elif original_type is dict:
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    await ctx.send(f"無法將 {value} 轉換為字典：請使用有效的 JSON 格式。")
                    return
            else:
                pass
        except Exception as e:
            await ctx.send(f"無法將 {value} 轉換為 {original_type.__name__}：{e}")
            return
        config(key=key, value=value, mode="w")
        await ctx.send(f"已更新 {key} 為 {str(value)}。")


@bot.command(aliases=["off", "exit", "q", "bye"])
@is_owner()
async def shutdown(ctx):
    await ctx.send("機器人正在關閉...")
    print("Shutting down...")
    if on_close_tasks:
        print("Running on_close tasks...")
        await ctx.send(f"正在執行關閉前任務...共 {len(on_close_tasks)} 項。")
        for task in on_close_tasks:
            print(f"Running on_close task: {task.__name__}...")
            await ctx.send(f"正在執行關閉前任務：{task.__name__}...")
            try:
                await task()
            except Exception as e:
                print(f"Error running on_close task: {e}")
                await ctx.send(f"關閉前任務發生錯誤：{e}")
    await bot.close()


@bot.command(aliases=["user", "u"])
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


@bot.command(aliases=["server", "sc"])
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


@bot.command(aliases=["leave", "l"])
@is_owner()
async def leaveserver(ctx, guild_id: int):
    guild = bot.get_guild(guild_id)
    if guild is None:
        await ctx.send("找不到該伺服器。")
        return
    await guild.leave()
    await ctx.send(f"已離開伺服器 {guild.name} (ID: `{guild.id}`) 。")


@bot.command(aliases=["invite", "inv"])
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


@bot.command(aliases=["servers", "ls"])
@is_owner()
async def listservers(ctx, query: str = None):
    guilds = bot.guilds
    if not guilds:
        await ctx.send("機器人目前沒有加入任何伺服器。")
        return
    servers_info = []
    for guild in guilds:
        if query and query.lower() not in guild.name.lower() and query not in str(guild.id):
            continue
        servers_info.append(f"- {guild.name} ({guild.member_count} 人) (ID: `{guild.id}`)")
    await ctx.send(f"機器人目前加入的伺服器： 共 {len(servers_info)} 個。")
    for i in range(0, len(servers_info), 30):
        await ctx.send("\n".join(servers_info[i:i+30]))


@bot.command(aliases=["send", "s", "msg"])
@is_owner()
async def sendmessage(ctx, channel_id: int, *, message: str):
    channel = bot.get_channel(channel_id)
    if channel is None:
        # try get user DM
        user = bot.get_user(channel_id)
        if user is not None:
            try:
                await user.send(message)
                await ctx.send(f"已在用戶 {user.name} 的私訊中發送訊息。")
            except discord.Forbidden:
                await ctx.send("無法在該用戶的私訊中發送訊息，機器人缺少權限。")
            return
        await ctx.send("找不到該頻道。")
        return
    try:
        await channel.send(message)
        await ctx.send(f"已在頻道 {channel.name} 發送訊息。")
    except discord.Forbidden:
        await ctx.send("無法在該頻道發送訊息，機器人缺少權限。")
    except Exception as e:
        await ctx.send(f"發送訊息時發生錯誤：{e}")


@bot.command(aliases=["transcript", "ct"])
@is_owner()
async def createtranscript(ctx, channel_id: int, after_message_id: int=None, before_message_id: int=None, limit: int=500):
    channel = bot.get_channel(channel_id)
    if channel is None or not isinstance(channel, discord.TextChannel):
        await ctx.send("找不到該文字頻道。")
        return
    try:
        messages = []
        async for msg in channel.history(limit=limit, after=discord.Object(id=after_message_id) if after_message_id else None, before=discord.Object(id=before_message_id) if before_message_id else None, oldest_first=True):
            messages.append(msg)
        messages.reverse()  # chat_exporter needs oldest first
        
        transcript = await chat_exporter.raw_export(
            channel,
            messages=messages,
            tz_info="Asia/Taipei",
            guild=channel.guild,
            bot=bot
        )

        # send as file
        transcript_file = io.BytesIO(transcript.encode('utf-8'))
        transcript_file.name = f"transcript_{channel.id}.html"
        await ctx.send("以下是頻道的對話紀錄：", file=discord.File(fp=transcript_file))
    except discord.Forbidden:
        await ctx.send("無法讀取該頻道的歷史訊息，機器人缺少權限。")
    except Exception as e:
        await ctx.send(f"創建對話紀錄時發生錯誤：{e}")


@bot.command(aliases=["dsi"])
@is_owner()
async def devserverinfo(ctx: commands.Context, guild_id: int=None):
    """顯示指定伺服器資訊
    
    用法： devserverinfo [伺服器ID]
    """
    guild = bot.get_guild(guild_id) if guild_id else ctx.guild
    if guild is None:
        await ctx.send("找不到該伺服器。")
        return

    embed = discord.Embed(title=f"{guild.name} 的資訊", color=0x00ff00)
    view = discord.ui.View()
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        iconbutton = discord.ui.Button(label="伺服器圖標連結", url=guild.icon.url)
        view.add_item(iconbutton)
    if guild.banner:
        embed.set_image(url=guild.banner.url if guild.banner else None)
        bannerbutton = discord.ui.Button(label="伺服器橫幅連結", url=guild.banner.url)
        view.add_item(bannerbutton)
    embed.add_field(name="伺服器 ID", value=str(guild.id), inline=True)
    embed.add_field(name="創建時間", value=f"<t:{int(guild.created_at.timestamp())}:F>", inline=True)
    embed.add_field(name="擁有者", value=guild.owner.mention if guild.owner else "未知", inline=True)
    embed.add_field(name="加成", value=f"{guild.premium_subscription_count} (等級{guild.premium_tier})", inline=True)
    embed.add_field(
        name="驗證等級",
        value={
            "none": "無",
            "low": "低",
            "medium": "中等",
            "high": "高",
            "highest": "最高"
        }
        .get(
                guild.verification_level.name.lower(), "none"
            ),
        inline=True
    )
    embed.add_field(name="地區", value=str(guild.preferred_locale), inline=True)
    embed.add_field(name="成員數量", value=str(guild.member_count), inline=True)
    embed.add_field(name="頻道數量", value=str(len(guild.channels)), inline=True)
    embed.add_field(name="身分組數量", value=str(len(guild.roles)), inline=True)
    
    # database info
    server_config = db.get_all_server_config(guild.id)
    embed.add_field(name="資料庫設定項目數量", value=str(len(server_config)), inline=True)
    user_data = db.get_all_user_data(guild.id)
    embed.add_field(name="資料庫用戶資料數量", value=str(len(user_data)), inline=True)
    
    await ctx.send(embed=embed, view=view)


@bot.command(aliases=["rc"])
@is_owner()
async def reloadconfig(ctx):
    if reload_config():
        await ctx.send("配置已重新加載。")
    else:
        await ctx.send("重新加載配置時發生錯誤。")


@bot.command(aliases=["ou"])
@is_owner()
async def owner_userinfo(ctx, user: Union[discord.User, discord.Member] = None):
    """顯示用戶資訊，比起基本的可以顯示更多資訊。
    
    用法： owner_userinfo [用戶]
    如果不指定用戶，則顯示自己的資訊。
    """
    if user is None:
        user = ctx.author
    embed = discord.Embed(title=f"{user.display_name} 的資訊", color=0x00ff00)
    embed.set_thumbnail(url=user.avatar.url if user.avatar else discord.Embed.Empty)
    # avatar url button
    button = discord.ui.Button(label="頭像連結", url=user.avatar.url if user.avatar else "https://discord.com/assets/6debd47ed13483642cf09e832ed0bc1b.png")
    view = discord.ui.View()
    view.add_item(button)
    embed.add_field(name="用戶 ID", value=str(user.id), inline=True)
    embed.add_field(name="帳號創建時間", value=user.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    if isinstance(user, discord.Member):
        embed.add_field(name="伺服器暱稱", value=user.nick or "無", inline=True)
        embed.add_field(name="加入伺服器時間", value=user.joined_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
        # pfp
        if user.display_avatar and user.display_avatar.url != user.avatar.url:
            embed.set_image(url=user.display_avatar.url if user.display_avatar.url != user.avatar.url else None)
            button_serverpfp = discord.ui.Button(label="伺服器頭像連結", url=user.display_avatar.url)
            view.add_item(button_serverpfp)
    # get mutual guilds
    mutual_guilds = [g for g in bot.guilds if g.get_member(user.id)]
    embed.add_field(name="共同伺服器", value="\n".join(g.name for g in mutual_guilds) or "無", inline=False)
    await ctx.send(embed=embed, view=view)


@bot.event
async def on_guild_join(guild: discord.Guild):
    # print(f"Joined guild: {guild.name} (ID: {guild.id})")
    log(f"加入了伺服器: {guild.name}，正在快取伺服器資料", module_name="OwnerTools", guild=guild)
    # try to chunk guild data
    try:
        await guild.chunk(cache=True)
        log(f"已快取伺服器資料: {guild.name}", module_name="OwnerTools", guild=guild)
    except Exception as e:
        log(f"無法快取伺服器資料: {e}", module_name="OwnerTools", guild=guild)
    # send to channel
    channel = bot.get_channel(config("join_leave_log_channel_id"))
    try:
        # build embed with server icon and member count
        embed = discord.Embed(
            title="已加入新伺服器",
            description=f"{guild.name} (ID: `{guild.id}`)",
            color=discord.Color.blurple()
        )
        embed.add_field(name="擁有者", value=f"{guild.owner} (ID: {guild.owner_id})", inline=True)
        embed.add_field(name="伺服器 ID", value=str(guild.id), inline=True)
        embed.add_field(name="頻道數", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="成員數", value=str(getattr(guild, "member_count", "未知")), inline=True)
        embed.add_field(name="身分組數", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="加成等級", value=f"等級 {guild.premium_tier} ({guild.premium_subscription_count} 個加成)", inline=True)
        embed.add_field(name="建立時間", value=guild.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)

        # try to get icon URL (works for discord.py v1.x and v2.x)
        icon_url = None
        if getattr(guild, "icon", None):
            try:
                icon_url = guild.icon.url  # v2.x
            except Exception:
                icon_url = getattr(guild, "icon_url", None)  # v1.x fallback
        if icon_url:
            embed.set_thumbnail(url=icon_url)

        await channel.send(embed=embed)
    except discord.Forbidden:
        log(f"無法在設定的頻道發送加入伺服器訊息", level=logging.ERROR, module_name="OwnerTools")
                

@bot.event
async def on_guild_remove(guild):
    log(f"離開了伺服器: {guild.name}", module_name="OwnerTools", guild=guild)
    # send to channel
    channel = bot.get_channel(config("join_leave_log_channel_id"))
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
        await channel.send(embed=embed)
    except discord.Forbidden:
        log(f"無法在設定的頻道發送離開伺服器訊息", level=logging.ERROR, module_name="OwnerTools")


if __name__ == "__main__":
    start_bot()
