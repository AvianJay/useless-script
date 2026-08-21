import discord
from discord import app_commands
from globalenv import bot, start_bot, set_server_config, get_server_config, get_server_config_i18n, on_ready_tasks
import i18n
from i18n import t
from discord.ext import commands
import asyncio
import random
from logger import log
import logging


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.bot_has_permissions(manage_channels=True, move_members=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class DynamicVoice(commands.GroupCog, name=app_commands.locale_str("dynamic-voice", i18n_key="cmd.dynamicvoice.dynamic_voice.root.name")):
    def __init__(self, bot):
        self.bot = bot
        self.playing_voice_guilds = set()
    @app_commands.command(name=app_commands.locale_str("setup", i18n_key="cmd.dynamicvoice.dynamic_voice.setup.name"), description=app_commands.locale_str("Set up dynamic voice channels", i18n_key="cmd.dynamicvoice.dynamic_voice.setup.desc"))
    @app_commands.describe(channel=app_commands.locale_str("Choose a channel", i18n_key="cmd.dynamicvoice.dynamic_voice.setup.param.channel"), channel_category=app_commands.locale_str("Choose a channel category", i18n_key="cmd.dynamicvoice.dynamic_voice.setup.param.channel_category"), channel_name=app_commands.locale_str("Channel name template ({user} inserts the user's name)", i18n_key="cmd.dynamicvoice.dynamic_voice.setup.param.channel_name"))
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction, channel: discord.VoiceChannel, channel_category: discord.CategoryChannel, channel_name: str | None = None):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        # set channel limit
        play_audio_enabled = get_server_config(guild_id, "dynamic_voice_play_audio", False)
        warn = ""
        try:
            await channel.edit(user_limit=2 if play_audio_enabled else 1)
        except Exception as e:
            log(f"Failed to set the user limit of channel '{channel.name}' to 1: {e}", level=logging.WARNING, module_name="DynamicVoice", guild=interaction.guild)
            warn = t("dynamicvoice.warn.user_limit_failed", channel=channel.name) + "\n"
        # check bot permissions
        if not channel.guild.me.guild_permissions.manage_channels or not channel.guild.me.guild_permissions.move_members:
            await interaction.followup.send(t("dynamicvoice.err.missing_perms") + f"\n- {warn}", ephemeral=True)
            return
        # Save configuration to database
        set_server_config(guild_id, "dynamic_voice_channel", channel.id)
        set_server_config(guild_id, "dynamic_voice_channel_category", channel_category.id)
        # 沒指定就存 None，讓讀取端每次用當下的伺服器語言解析預設值；
        # 存渲染後的字串會把語言凍結在設定當下。
        set_server_config(guild_id, "dynamic_voice_channel_name", channel_name)
        # print(f"[+] Set up dynamic voice channel in guild {guild_id}, channel {channel.id}, category {channel_category.id}, name {channel_name}")
        log(f"Dynamic voice set up in guild {guild_id}: channel {channel.id}, category {channel_category.id}, name {channel_name}", module_name="DynamicVoice", guild=interaction.guild)
        await interaction.followup.send(
            t("dynamicvoice.msg.setup_done", channel=channel.mention, category=channel_category.name)
            + f"\n- {warn}", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("disable", i18n_key="cmd.dynamicvoice.dynamic_voice.disable.name"), description=app_commands.locale_str("Disable dynamic voice channels", i18n_key="cmd.dynamicvoice.dynamic_voice.disable.desc"))
    @app_commands.checks.has_permissions(administrator=True)
    async def disable(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        # Remove configuration from database
        set_server_config(guild_id, "dynamic_voice_channel", None)
        set_server_config(guild_id, "dynamic_voice_channel_category", None)
        set_server_config(guild_id, "dynamic_voice_channel_name", None)
        log("Dynamic voice disabled", module_name="DynamicVoice", guild=interaction.guild)
        await interaction.followup.send(t("dynamicvoice.msg.disabled"), ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("play-audio", i18n_key="cmd.dynamicvoice.dynamic_voice.play_audio.name"), description=app_commands.locale_str("Play a sound before switching dynamic voice channels", i18n_key="cmd.dynamicvoice.dynamic_voice.play_audio.desc"))
    @app_commands.describe(enable=app_commands.locale_str("Whether to play a sound before entering the channel", i18n_key="cmd.dynamicvoice.dynamic_voice.play_audio.param.enable"))
    @app_commands.choices(enable=[
        app_commands.Choice(name=app_commands.locale_str("Enable", i18n_key="cmd.dynamicvoice.dynamic_voice.play_audio.choice.true"), value="True"),
        app_commands.Choice(name=app_commands.locale_str("Disable", i18n_key="cmd.dynamicvoice.dynamic_voice.play_audio.choice.false"), value="False")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def play_audio(self, interaction: discord.Interaction, enable: str):
        enable = (enable == "True")
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        # connect to voice channel and standby to play audio when user joins the dynamic voice channel
        if enable:
            channel_id = get_server_config(guild_id, "dynamic_voice_channel")
            if not channel_id:
                await interaction.followup.send(t("dynamicvoice.err.not_set_up"), ephemeral=True)
                return
            channel = interaction.guild.get_channel(channel_id)
            if not channel:
                await interaction.followup.send(t("dynamicvoice.err.channel_missing"), ephemeral=True)
                return
            # check bot permissions
            if not channel.guild.me.guild_permissions.connect or not channel.guild.me.guild_permissions.speak:
                await interaction.followup.send(t("dynamicvoice.err.missing_voice_perms"), ephemeral=True)
                return
            # try:
            #     await channel.connect()
            # except Exception as e:
            #     log(f"無法連接到頻道 '{channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=interaction.guild)
            #     await interaction.followup.send(f"錯誤：無法連接到語音頻道 '{channel.name}'。", ephemeral=True)
            #     return
            # set voice channel limit members to 1
            try:
                await channel.edit(user_limit=1)
            except Exception as e:
                log(f"Failed to set the user limit for channel '{channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=interaction.guild)
        else:
            channel_id = get_server_config(guild_id, "dynamic_voice_channel")
            if not channel_id:
                await interaction.followup.send(t("dynamicvoice.err.not_set_up"), ephemeral=True)
                return
            channel = interaction.guild.get_channel(channel_id)
            voice_client = discord.utils.get(self.bot.voice_clients, guild=channel.guild)
            if voice_client and voice_client.is_connected():
                await voice_client.disconnect()
                log(f"Disconnected from channel '{channel.name}'", module_name="DynamicVoice", guild=interaction.guild)
            try:
                await channel.edit(user_limit=1)
            except Exception as e:
                log(f"Failed to set user limit for channel '{channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=interaction.guild)
        set_server_config(guild_id, "dynamic_voice_play_audio", enable)
        log(f"Dynamic voice join sound {'enabled' if enable else 'disabled'}.", module_name="DynamicVoice", guild=interaction.guild)
        await interaction.followup.send(
            t("dynamicvoice.msg.play_audio_enabled" if enable else "dynamicvoice.msg.play_audio_disabled"),
            ephemeral=True)
    
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
    
    @app_commands.command(name=app_commands.locale_str("blacklist-role", i18n_key="cmd.dynamicvoice.dynamic_voice.blacklist_role.name"), description=app_commands.locale_str("Set a dynamic voice channel blacklist role", i18n_key="cmd.dynamicvoice.dynamic_voice.blacklist_role.desc"))
    async def blacklist_role(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        blacklisted_roles = get_server_config(guild_id, "dynamic_voice_blacklist_roles", [])
        if role.id in blacklisted_roles:
            await interaction.followup.send(t("dynamicvoice.err.role_already_blacklisted"), ephemeral=True)
            return
        blacklisted_roles.append(role.id)
        set_server_config(guild_id, "dynamic_voice_blacklist_roles", blacklisted_roles)
        await interaction.followup.send(t("dynamicvoice.msg.role_blacklisted"), ephemeral=True)
        log(f"Role {role.name} added to the blacklist", module_name="DynamicVoice", user=interaction.user, guild=interaction.guild)
    
    @app_commands.command(name=app_commands.locale_str("unblacklist-role", i18n_key="cmd.dynamicvoice.dynamic_voice.unblacklist_role.name"), description=app_commands.locale_str("Remove a dynamic voice channel blacklist role", i18n_key="cmd.dynamicvoice.dynamic_voice.unblacklist_role.desc"))
    async def unblacklist_role(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        blacklisted_roles = get_server_config(guild_id, "dynamic_voice_blacklist_roles", [])
        if role.id not in blacklisted_roles:
            await interaction.followup.send(t("dynamicvoice.err.role_not_blacklisted"), ephemeral=True)
            return
        blacklisted_roles.remove(role.id)
        set_server_config(guild_id, "dynamic_voice_blacklist_roles", blacklisted_roles)
        await interaction.followup.send(t("dynamicvoice.msg.role_unblacklisted"), ephemeral=True)
        log(f"Role {role.name} removed from the blacklist", module_name="DynamicVoice", user=interaction.user, guild=interaction.guild)
    
    @app_commands.command(name=app_commands.locale_str("view-blacklist-roles", i18n_key="cmd.dynamicvoice.dynamic_voice.view_blacklist_roles.name"), description=app_commands.locale_str("View dynamic voice channel blacklist roles", i18n_key="cmd.dynamicvoice.dynamic_voice.view_blacklist_roles.desc"))
    async def view_blacklist_roles(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        blacklisted_roles = get_server_config(guild_id, "dynamic_voice_blacklist_roles", [])
        if not blacklisted_roles:
            await interaction.followup.send(t("dynamicvoice.msg.blacklist_empty"), ephemeral=True)
            return
        role_mentions = []
        for role_id in blacklisted_roles:
            role = interaction.guild.get_role(role_id)
            if role:
                role_mentions.append(role.mention)
            else:
                role_mentions.append(t("dynamicvoice.msg.deleted_role", role_id=role_id))
        await interaction.followup.send(
            t("dynamicvoice.msg.blacklist_header") + "\n" + "\n".join(role_mentions), ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        # if member.bot and member != bot.user:
        #     return  # Ignore bot users
        guild_id = member.guild.id
        channel_id = get_server_config(guild_id, "dynamic_voice_channel")
        channel_category_id = get_server_config(guild_id, "dynamic_voice_channel_category")
        # 頻道名稱是 guild 共享的產物，以伺服器語言解析預設值（非觸發者個人語言）
        channel_name_template = get_server_config_i18n(
            guild_id, "dynamic_voice_channel_name",
            "panel.dynamicvoice.dynamic_voice_channel_name.default",
            locale=i18n.resolve_locale(guild_id=guild_id))
        play_audio_enabled = get_server_config(guild_id, "dynamic_voice_play_audio", False)
        created_channels = get_server_config(guild_id, "created_dynamic_channels", [])
        if not channel_id:
            return  # Dynamic voice feature not set up for this guild
        channel_category = member.guild.get_channel(channel_category_id) if channel_category_id else None

        # if member.bot:
        #     return
        
        if guild_id in self.playing_voice_guilds:
            return  # Already playing audio in this guild

        # User joins the dynamic voice channel
        if after.channel and after.channel.id == channel_id and (not before.channel or before.channel.id != channel_id):
            # Check if user is blacklisted
            blacklisted_roles = get_server_config(guild_id, "dynamic_voice_blacklist_roles", [])
            if any(role.id in blacklisted_roles for role in member.roles):
                log("User is blacklisted; not creating a dynamic voice channel.", module_name="DynamicVoice", user=member, guild=member.guild)
                # disconnect user from the dynamic voice channel
                try:
                    await member.move_to(None, reason=t("dynamicvoice.audit.blacklisted",
                                                        locale=i18n.resolve_locale(guild_id=guild_id)))
                except Exception as e:
                    log(f"Failed to remove user {member} from the dynamic voice channel: {e}", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)
                return
            # Create a new voice channel for the user
            new_channel = await member.guild.create_voice_channel(
                name=channel_name_template.format(user=member.name),
                category=channel_category,
                bitrate=member.guild.bitrate_limit,  # maximum bitrate
                overwrites=channel_category.overwrites if channel_category else None
            )
            # give user permission to manage the channel
            await new_channel.set_permissions(member, manage_channels=True, create_events=True, use_embedded_activities=True)
            # disable blacklisted roles from joining the channel
            for role_id in blacklisted_roles:
                role = member.guild.get_role(role_id)
                if role:
                    await new_channel.set_permissions(role, connect=False, send_messages=False, create_private_threads=False, create_public_threads=False)
            # disable everyone role from managing the channel
            everyone_role = member.guild.default_role
            await new_channel.set_permissions(everyone_role, manage_channels=False, create_events=False, manage_webhooks=False, mention_everyone=False, use_external_apps=False)
            
            # Move the user to the new channel
            if play_audio_enabled:
                existing_voice_client = member.guild.voice_client
                if existing_voice_client:
                    # 已有語音連線（例如音樂播放器），跳過播放進入音效
                    log("A voice client already exists; skipping the join sound", module_name="DynamicVoice", guild=member.guild)
                else:
                    try:
                        self.playing_voice_guilds.add(guild_id)
                        vp = await after.channel.connect()
                        await asyncio.sleep(1)  # wait for a moment to ensure the user has joined the new channel
                        voice_client = discord.utils.get(self.bot.voice_clients, guild=member.guild)
                        if voice_client and voice_client.is_connected():
                            voice_client.stop()  # Stop any existing audio
                            import os
                            audio_folder = os.path.join(os.path.dirname(__file__), "assets", "dynamic_voice_audio")
                            audio_files = [f for f in os.listdir(audio_folder) if f.endswith(('.mp3', '.wav', '.ogg'))] if os.path.exists(audio_folder) else []
                            if audio_files:
                                selected_audio = random.choice(audio_files)
                                audio_path = os.path.join(audio_folder, selected_audio)
                            else:
                                # fallback to old location if no files in new folder
                                audio_path = f"assets/dynamic_voice_join_{random.randint(1, 7)}.mp3"
                            audio_source = discord.FFmpegPCMAudio(audio_path)
                            if not voice_client.is_playing():
                                log(f"Playing the join sound for {member}", module_name="DynamicVoice", user=member, guild=member.guild)
                                voice_client.play(audio_source)
                                while voice_client.is_playing():
                                    if after.channel.members == 1:
                                        voice_client.stop()
                                        break
                                    await asyncio.sleep(0.1)
                    except Exception as e:
                        log(f"Failed to play the join sound: {e}", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)
                    finally:
                        try:
                            await vp.disconnect()
                        except Exception as e:
                            log(f"Failed to disconnect from the voice channel: {e}", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)
                        finally:
                            self.playing_voice_guilds.discard(guild_id)
            try:
                if after.channel.members != 1:
                    await member.move_to(new_channel)
            except Exception as e:
                log(f"Failed to move user {member} into channel '{new_channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)
            await asyncio.sleep(0.5)
            if new_channel and len(new_channel.members) == 0:
                try:
                    await new_channel.delete()
                    log(f"Deleted the empty dynamic voice channel '{new_channel.name}' for user {member} in guild {guild_id}", module_name="DynamicVoice", guild=member.guild)
                    return
                except Exception as e:
                    log(f"Failed to delete the empty channel '{new_channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)
            else:
                created_channels = get_server_config(guild_id, "created_dynamic_channels", [])  # refresh list
                created_channels.append(new_channel.id)
                set_server_config(guild_id, "created_dynamic_channels", created_channels)
                log(f"Created the dynamic voice channel '{new_channel.name}' for user {member} in guild {guild_id}", module_name="DynamicVoice", guild=member.guild)
        for user_channel_id in created_channels:
            # created_channels = get_server_config(guild_id, "created_dynamic_channels", [])
            try:
                channel = member.guild.get_channel(user_channel_id)
            except Exception:
                created_channels.remove(user_channel_id)
                log(f"Couldn't fetch channel ID {user_channel_id}; removing it from the tracking list.", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)
                set_server_config(guild_id, "created_dynamic_channels", created_channels)
                continue
            if not channel:
                created_channels.remove(user_channel_id)
                log(f"Channel ID {user_channel_id} not found; removing it from the tracking list.", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)
                set_server_config(guild_id, "created_dynamic_channels", created_channels)
                continue
            if len(channel.members) == 0:
                created_channels.remove(channel.id)
                set_server_config(guild_id, "created_dynamic_channels", created_channels)
                try:
                    await channel.delete()
                    log(f"Deleted the empty dynamic voice channel '{channel.name}'", module_name="DynamicVoice", guild=member.guild)
                except Exception as e:
                    log(f"Failed to delete the empty channel '{channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=member.guild)
    
    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            guild_id = guild.id
            play_audio_enabled = get_server_config(guild_id, "dynamic_voice_play_audio", False)
            channel_id = get_server_config(guild_id, "dynamic_voice_channel")
            if play_audio_enabled and channel_id:
                channel = guild.get_channel(channel_id)
                if channel:
                    if channel.user_limit != 1:
                        await channel.edit(user_limit=1)
                # if channel:
                #     try:
                #         await channel.connect()
                #         log(f"已連接到 '{channel.name}'", module_name="DynamicVoice", guild=guild)
                #     except Exception as e:
                #         log(f"無法連接到頻道 '{channel.name}': {e}", level=logging.ERROR, module_name="DynamicVoice", guild=guild)

asyncio.run(bot.add_cog(DynamicVoice(bot)))
                    

if __name__ == "__main__":
    start_bot()
