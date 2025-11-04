import discord
from discord import app_commands
from globalenv import bot, start_bot, set_server_config, get_server_config, on_ready_tasks
from discord.ext import commands
import asyncio
import random
from logger import log
import logging


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.checks.bot_has_permissions(manage_channels=True, move_members=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class DynamicVoice(commands.GroupCog, name=app_commands.locale_str("dynamic-voice")):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name=app_commands.locale_str("setup"), description="設置動態語音頻道")
    @app_commands.describe(channel="選擇頻道", channel_category="選擇頻道類別", channel_name="選擇頻道名稱模板 (使用 {user} 代表用戶名稱)")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction, channel: discord.VoiceChannel, channel_category: discord.CategoryChannel, channel_name: str = "{user} 的頻道"):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        # set channel limit to 1
        warn = ""
        try:
            await channel.edit(user_limit=1)
        except Exception as e:
            log(f"無法將頻道 '{channel.name}' 的使用者上限設置為 1: {e}", level=logging.WARNING, module_name="DynamicVoice", guild=interaction.guild)
            warn = f"警告：無法將頻道 '{channel.name}' 的使用者上限設置為 1，請確保機器人有管理頻道的權限。\n"
        # check bot permissions
        if not channel.guild.me.guild_permissions.manage_channels or not channel.guild.me.guild_permissions.move_members:
            await interaction.followup.send(f"錯誤：機器人需要管理頻道和移動成員的權限才能設置動態語音頻道。\n- {warn}", ephemeral=True)
            return
        # Save configuration to database
        set_server_config(guild_id, "dynamic_voice_channel", channel.id)
        set_server_config(guild_id, "dynamic_voice_channel_category", channel_category.id)
        set_server_config(guild_id, "dynamic_voice_channel_name", channel_name)
        # print(f"[+] Set up dynamic voice channel in guild {guild_id}, channel {channel.id}, category {channel_category.id}, name {channel_name}")
        log(f"Set up dynamic voice channel in guild {guild_id}, channel {channel.id}, category {channel_category.id}, name {channel_name}", module_name="DynamicVoice", guild=interaction.guild)
        await interaction.followup.send(f"動態語音頻道已設置在 '{channel.mention}' 下，將自動創建頻道於 '{channel_category.name}' 中。\n- {warn}", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("disable"), description="禁用動態語音頻道")
    @app_commands.checks.has_permissions(administrator=True)
    async def disable(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        # Remove configuration from database
        set_server_config(guild_id, "dynamic_voice_channel", None)
        set_server_config(guild_id, "dynamic_voice_channel_category", None)
        set_server_config(guild_id, "dynamic_voice_channel_name", None)
        log(f"動態語音頻道被禁用", module_name="DynamicVoice", guild=interaction.guild)
        await interaction.followup.send("動態語音頻道已被禁用。", ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("play-audio"), description="動態語音頻道切換前先播放音效")
    @app_commands.describe(enable="是否啟用進入頻道前播放音效")
    @app_commands.choices(enable=[
        app_commands.Choice(name="啟用", value=1),
        app_commands.Choice(name="禁用", value=0)
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def play_audio(self, interaction: discord.Interaction, enable: int):
        enable = bool(enable)
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        # connect to voice channel and standby to play audio when user joins the dynamic voice channel
        if enable:
            channel_id = get_server_config(guild_id, "dynamic_voice_channel")
            if not channel_id:
                await interaction.followup.send("錯誤：請先設置動態語音頻道。", ephemeral=True)
                return
            channel = interaction.guild.get_channel(channel_id)
            if not channel:
                await interaction.followup.send("錯誤：找不到設置的動態語音頻道。", ephemeral=True)
                return
            # check bot permissions
            if not channel.guild.me.guild_permissions.connect or not channel.guild.me.guild_permissions.speak:
                await interaction.followup.send("錯誤：機器人需要連接和說話權限才能播放音效。", ephemeral=True)
                return
            try:
                await channel.connect()
            except Exception as e:
                log(f"Failed to connect to channel '{channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=interaction.guild)
                await interaction.followup.send(f"錯誤：無法連接到語音頻道 '{channel.name}'。", ephemeral=True)
                return
            # set voice channel limit members to 2
            try:
                await channel.edit(user_limit=2)
            except Exception as e:
                log(f"Failed to set user limit for channel '{channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=interaction.guild)
        else:
            channel_id = get_server_config(guild_id, "dynamic_voice_channel")
            if not channel_id:
                await interaction.followup.send("錯誤：請先設置動態語音頻道。", ephemeral=True)
                return
            channel = interaction.guild.get_channel(channel_id)
            voice_client = discord.utils.get(self.bot.voice_clients, guild=channel.guild)
            if voice_client and voice_client.is_connected():
                await voice_client.disconnect()
                log(f"已斷開與頻道 '{channel.name}' 的連接", module_name="DynamicVoice", guild=interaction.guild)
            try:
                await channel.edit(user_limit=1)
            except Exception as e:
                log(f"Failed to set user limit for channel '{channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=interaction.guild)
        set_server_config(guild_id, "dynamic_voice_play_audio", enable)
        log(f"動態語音頻道進入前播放音效已{'啟用' if enable else '禁用'}。", module_name="DynamicVoice", guild=interaction.guild)
        await interaction.followup.send(f"動態語音頻道進入前播放音效已{'啟用' if enable else '禁用'}。", ephemeral=True)
    
    # @app_commands.command(name=app_commands.locale_str("blacklist"), description="設定動態語音頻道黑名單")
    # async def blacklist(self, interaction: discord.Interaction, user: discord.User):
    #     guild_id = interaction.guild.id
    #     blacklisted_users = get_server_config(guild_id, "dynamic_voice_blacklist", [])
    #     if user.id in blacklisted_users:
    #         await interaction.followup.send("該用戶已在黑名單中。", ephemeral=True)
    #         return
    #     blacklisted_users.append(user.id)
    #     set_server_config(guild_id, "dynamic_voice_blacklist", blacklisted_users)
    #     await interaction.followup.send("已將該用戶加入黑名單。", ephemeral=True)

    # @app_commands.command(name=app_commands.locale_str("unblacklist"), description="移除動態語音頻道黑名單")
    # async def unblacklist(self, interaction: discord.Interaction, user: discord.User):
    #     guild_id = interaction.guild.id
    #     blacklisted_users = get_server_config(guild_id, "dynamic_voice_blacklist", [])
    #     if user.id not in blacklisted_users:
    #         await interaction.followup.send("該用戶不在黑名單中。", ephemeral=True)
    #         return
    #     blacklisted_users.remove(user.id)
    #     set_server_config(guild_id, "dynamic_voice_blacklist", blacklisted_users)
    #     await interaction.followup.send("已將該用戶移除黑名單。", ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("blacklist-role"), description="設定動態語音頻道黑名單身分組")
    async def blacklist_role(self, interaction: discord.Interaction, role: discord.Role):
        guild_id = interaction.guild.id
        blacklisted_roles = get_server_config(guild_id, "dynamic_voice_blacklist_roles", [])
        if role.id in blacklisted_roles:
            await interaction.followup.send("該身分組已在黑名單中。", ephemeral=True)
            return
        blacklisted_roles.append(role.id)
        set_server_config(guild_id, "dynamic_voice_blacklist_roles", blacklisted_roles)
        await interaction.followup.send("已將該身分組加入黑名單。", ephemeral=True)
        log(f"身分組 {role.name} 被加入黑名單", module_name="DynamicVoice", user=interaction.user, guild=interaction.guild)
    
    @app_commands.command(name=app_commands.locale_str("unblacklist-role"), description="移除動態語音頻道黑名單身分組")
    async def unblacklist_role(self, interaction: discord.Interaction, role: discord.Role):
        guild_id = interaction.guild.id
        blacklisted_roles = get_server_config(guild_id, "dynamic_voice_blacklist_roles", [])
        if role.id not in blacklisted_roles:
            await interaction.followup.send("該身分組不在黑名單中。", ephemeral=True)
            return
        blacklisted_roles.remove(role.id)
        set_server_config(guild_id, "dynamic_voice_blacklist_roles", blacklisted_roles)
        await interaction.followup.send("已將該身分組移除黑名單。", ephemeral=True)
        log(f"身分組 {role.name} 被移除黑名單", module_name="DynamicVoice", user=interaction.user, guild=interaction.guild)
    
    @app_commands.command(name=app_commands.locale_str("view-blacklist-roles"), description="查看動態語音頻道黑名單身分組")
    async def view_blacklist_roles(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        blacklisted_roles = get_server_config(guild_id, "dynamic_voice_blacklist_roles", [])
        if not blacklisted_roles:
            await interaction.response.send_message("黑名單身分組為空。", ephemeral=True)
            return
        role_mentions = []
        for role_id in blacklisted_roles:
            role = interaction.guild.get_role(role_id)
            if role:
                role_mentions.append(role.mention)
            else:
                role_mentions.append(f"已刪除的身分組 (ID: `{role_id}`)")
        await interaction.response.send_message("黑名單身分組：\n" + "\n".join(role_mentions), ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot and member != bot.user:
            return  # Ignore bot users
        guild_id = member.guild.id
        channel_id = get_server_config(guild_id, "dynamic_voice_channel")
        channel_category_id = get_server_config(guild_id, "dynamic_voice_channel_category")
        channel_name_template = get_server_config(guild_id, "dynamic_voice_channel_name", "{user} 的頻道")
        play_audio_enabled = get_server_config(guild_id, "dynamic_voice_play_audio", False)
        created_channels = get_server_config(guild_id, "created_dynamic_channels", [])
        if not channel_id:
            return  # Dynamic voice feature not set up for this guild
        channel_category = member.guild.get_channel(channel_category_id) if channel_category_id else None
        
        # Bot is not connected to the voice channel, try to connect
        if before.channel:
            if play_audio_enabled and channel_id == before.channel.id and member == bot.user:
                if after.channel:
                    if bot.user in after.channel.members:
                        return
                voice_client = discord.utils.get(self.bot.voice_clients, guild=member.guild)
                if not voice_client or not voice_client.is_connected():
                    channel = member.guild.get_channel(channel_id)
                    if channel:
                        try:
                            await channel.connect()
                            # print(f"[+] Connected to dynamic voice channel '{channel.name}' in guild {guild_id}")
                            log(f"已連接到動態語音頻道 '{channel.name}'", module_name="DynamicVoice", guild=member.guild)
                        except Exception as e:
                            log(f"無法連接到頻道 '{channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)
                        return
        if member.bot:
            return

        # User joins the dynamic voice channel
        if after.channel and after.channel.id == channel_id and (not before.channel or before.channel.id != channel_id):
            # Check if user is blacklisted
            blacklisted_roles = get_server_config(guild_id, "dynamic_voice_blacklist_roles", [])
            if any(role.id in blacklisted_roles for role in member.roles):
                log(f"用戶被黑名單限制，無法創建動態語音頻道。", module_name="DynamicVoice", user=member, guild=member.guild)
                return
            # Create a new voice channel for the user
            new_channel = await member.guild.create_voice_channel(
                name=channel_name_template.format(user=member.name),
                category=channel_category,
                bitrate=member.guild.bitrate_limit  # maximum bitrate
            )
            # give user permission to manage the channel
            await new_channel.set_permissions(member, manage_channels=True, create_events=True)
            # disable blacklisted roles from joining the channel
            for role_id in blacklisted_roles:
                role = member.guild.get_role(role_id)
                if role:
                    await new_channel.set_permissions(role, connect=False, send_messages=False, create_private_threads=False, create_public_threads=False)
            # Move the user to the new channel
            if play_audio_enabled:
                await asyncio.sleep(1)  # wait for a moment to ensure the user has joined the new channel
                try:
                    voice_client = discord.utils.get(self.bot.voice_clients, guild=member.guild)
                    if voice_client and voice_client.is_connected():
                        voice_client.stop()  # Stop any existing audio
                        id = random.randint(1, 7)
                        audio_source = discord.FFmpegPCMAudio(f"assets/dynamic_voice_join_{id}.mp3")
                        if not voice_client.is_playing():
                            log(f"正在播放 {member} 的進入音效", module_name="DynamicVoice", user=member, guild=member.guild)
                            voice_client.play(audio_source)
                            while voice_client.is_playing():
                                if after.channel.members == 1:
                                    voice_client.stop()
                                    break
                                await asyncio.sleep(0.1)
                except Exception as e:
                    log(f"無法播放進入音效: {e}", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)
            try:
                await member.move_to(new_channel)
            except Exception as e:
                log(f"無法將用戶 {member} 移動到頻道 '{new_channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)
            await asyncio.sleep(0.5)
            if new_channel and len(new_channel.members) == 0:
                try:
                    await new_channel.delete()
                    log(f"已刪除用戶 {member} 在伺服器 {guild_id} 中的空動態語音頻道 '{new_channel.name}'", module_name="DynamicVoice", guild=member.guild)
                    return
                except Exception as e:
                    log(f"無法刪除空頻道 '{new_channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)
            else:
                created_channels = get_server_config(guild_id, "created_dynamic_channels", [])  # refresh list
                created_channels.append(new_channel.id)
                set_server_config(guild_id, "created_dynamic_channels", created_channels)
                log(f"已為用戶 {member} 在伺服器 {guild_id} 中創建動態語音頻道 '{new_channel.name}'", module_name="DynamicVoice", guild=member.guild)
        for user_channel_id in created_channels:
            # created_channels = get_server_config(guild_id, "created_dynamic_channels", [])
            try:
                channel = member.guild.get_channel(user_channel_id)
            except Exception:
                created_channels.remove(user_channel_id)
                log(f"無法獲取頻道 ID {user_channel_id}，從追蹤列表中移除。", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)
                set_server_config(guild_id, "created_dynamic_channels", created_channels)
                continue
            if not channel:
                created_channels.remove(user_channel_id)
                log(f"頻道 ID {user_channel_id} 未找到，從追蹤列表中移除。", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)
                set_server_config(guild_id, "created_dynamic_channels", created_channels)
                continue
            if len(channel.members) == 0:
                created_channels.remove(channel.id)
                set_server_config(guild_id, "created_dynamic_channels", created_channels)
                try:
                    await channel.delete()
                    log(f"已刪除空動態語音頻道 '{channel.name}'", module_name="DynamicVoice", guild=member.guild)
                except Exception as e:
                    log(f"無法刪除空頻道 '{channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)

asyncio.run(bot.add_cog(DynamicVoice(bot)))

async def on_ready():
    for guild in bot.guilds:
        guild_id = guild.id
        play_audio_enabled = get_server_config(guild_id, "dynamic_voice_play_audio", False)
        channel_id = get_server_config(guild_id, "dynamic_voice_channel")
        if play_audio_enabled and channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.connect()
                    log(f"已連接到 '{channel.name}'", module_name="DynamicVoice", guild=guild)
                except Exception as e:
                    log(f"無法連接到頻道 '{channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=guild)
on_ready_tasks.append(on_ready)
                    

if __name__ == "__main__":
    start_bot()
