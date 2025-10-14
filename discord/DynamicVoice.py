import discord
from discord import app_commands
from globalenv import bot, start_bot, set_server_config, get_server_config, on_ready_tasks
from discord.ext import commands
import asyncio


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
        # Save configuration to database
        set_server_config(guild_id, "dynamic_voice_channel", channel.id)
        set_server_config(guild_id, "dynamic_voice_channel_category", channel_category.id)
        set_server_config(guild_id, "dynamic_voice_channel_name", channel_name)
        print(f"[+] Set up dynamic voice channel in guild {guild_id}, channel {channel.id}, category {channel_category.id}, name {channel_name}")
        await interaction.followup.send(f"動態語音頻道已設置在 '{channel.mention}' 下，將自動創建頻道於 '{channel_category.name}' 中。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("disable"), description="禁用動態語音頻道")
    @app_commands.checks.has_permissions(administrator=True)
    async def disable(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        # Remove configuration from database
        set_server_config(guild_id, "dynamic_voice_channel", None)
        set_server_config(guild_id, "dynamic_voice_channel_category", None)
        set_server_config(guild_id, "dynamic_voice_channel_name", None)
        print(f"[+] Disabled dynamic voice channel in guild {guild_id}")
        await interaction.followup.send("動態語音頻道已被禁用。", ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("play-audio"), description="動態語音頻道切換前先播放音效")
    @app_commands.describe(enable="是否啟用進入頻道前播放音效")
    @app_commands.checks.has_permissions(administrator=True)
    async def play_audio(self, interaction: discord.Interaction, enable: bool):
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
            try:
                await channel.connect()
            except Exception as e:
                print(f"[-] Failed to connect to channel '{channel.name}': {e}")
            # set voice channel limit members to 2
            try:
                await channel.edit(user_limit=2)
            except Exception as e:
                print(f"[-] Failed to set user limit for channel '{channel.name}': {e}")
        set_server_config(guild_id, "dynamic_voice_play_audio", enable)
        print(f"[+] Set dynamic voice play audio to {enable} in guild {guild_id}")
        await interaction.followup.send(f"動態語音頻道進入前播放音效已{'啟用' if enable else '禁用'}。", ephemeral=True)
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
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

        # User joins the dynamic voice channel
        if after.channel and after.channel.id == channel_id and (not before.channel or before.channel.id != channel_id):
            # Create a new voice channel for the user
            new_channel = await member.guild.create_voice_channel(
                name=channel_name_template.format(user=member.name),
                category=channel_category,
                bitrate=member.guild.bitrate_limit  # maximum bitrate
            )
            # give user permission to manage the channel
            await new_channel.set_permissions(member, manage_channels=True, manage_permissions=True, manage_roles=True)
            # Move the user to the new channel
            if play_audio_enabled:
                await asyncio.sleep(1)  # wait for a moment to ensure the user has joined the new channel
                try:
                    voice_client = discord.utils.get(self.bot.voice_clients, guild=member.guild)
                    if voice_client and voice_client.is_connected():
                        voice_client.stop()  # Stop any existing audio
                        audio_source = discord.FFmpegPCMAudio("assets/dynamic_voice_join.mp3")
                        if not voice_client.is_playing():
                            print(f"[+] Playing join audio for user {member} in guild {guild_id}")
                            voice_client.play(audio_source)
                            while voice_client.is_playing():
                                if after.channel.members == 1:
                                    voice_client.stop()
                                    break
                                await asyncio.sleep(0.1)
                except Exception as e:
                    print(f"[-] Failed to play join audio: {e}")
            await member.move_to(new_channel)
            created_channels = get_server_config(guild_id, "created_dynamic_channels", [])  # refresh list
            created_channels.append(new_channel.id)
            set_server_config(guild_id, "created_dynamic_channels", created_channels)
            print(f"[+] Created dynamic voice channel '{new_channel.name}' for user {member} in guild {guild_id}")
        for user_channel_id in created_channels:
            # created_channels = get_server_config(guild_id, "created_dynamic_channels", [])
            try:
                channel = member.guild.get_channel(user_channel_id)
            except Exception:
                created_channels.remove(user_channel_id)
                print(f"[-] Failed to get channel with ID {user_channel_id}, removing from tracking list.")
                set_server_config(guild_id, "created_dynamic_channels", created_channels)
                continue
            if not channel:
                created_channels.remove(user_channel_id)
                print(f"[-] Channel with ID {user_channel_id} not found, removing from tracking list.")
                set_server_config(guild_id, "created_dynamic_channels", created_channels)
                continue
            if len(channel.members) == 0:
                created_channels.remove(channel.id)
                set_server_config(guild_id, "created_dynamic_channels", created_channels)
                try:
                    await channel.delete()
                    print(f"[+] Deleted empty dynamic voice channel '{channel.name}' in guild {guild_id}")
                except Exception as e:
                    print(f"[-] Failed to delete channel '{channel.name}': {e}")

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
                    print(f"[+] Connected to dynamic voice channel '{channel.name}' in guild {guild_id} on ready.")
                except Exception as e:
                    print(f"[-] Failed to connect to channel '{channel.name}' on ready: {e}")
on_ready_tasks.append(on_ready)
                    

if __name__ == "__main__":
    start_bot()
