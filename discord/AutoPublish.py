import discord
import asyncio
from discord import app_commands
from discord.ext import commands
from globalenv import bot, start_bot, get_server_config, set_server_config
from logger import log
import logging

from i18n import t
from datetime import datetime, timezone


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class AutoPublish(commands.GroupCog, name=app_commands.locale_str("autopublish", i18n_key="cmd.autopublish.autopublish.root.name")):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    @app_commands.command(name=app_commands.locale_str("view", i18n_key="cmd.autopublish.autopublish.view.name"), description=app_commands.locale_str("View announcement auto-publish settings", i18n_key="cmd.autopublish.autopublish.view.desc"))
    async def view_autopublish_settings(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        autopublish_settings = get_server_config(guild_id, "autopublish", {})

        await interaction.response.send_message(
            t("autopublish.msg.status_enabled" if autopublish_settings.get("enabled", False)
              else "autopublish.msg.status_disabled"), ephemeral=True)
        return

    @app_commands.command(name=app_commands.locale_str("settings", i18n_key="cmd.autopublish.autopublish.settings.name"), description=app_commands.locale_str("Configure auto-publishing", i18n_key="cmd.autopublish.autopublish.settings.desc"))
    @app_commands.describe(enable=app_commands.locale_str("Whether to enable auto-publishing", i18n_key="cmd.autopublish.autopublish.settings.param.enable"))
    @app_commands.choices(enable=[
        app_commands.Choice(name=app_commands.locale_str("Enable", i18n_key="cmd.autopublish.autopublish.settings.choice.true"), value="True"),
        app_commands.Choice(name=app_commands.locale_str("Disable", i18n_key="cmd.autopublish.autopublish.settings.choice.false"), value="False"),
    ])
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def set_autopublish(self, interaction: discord.Interaction, enable: str):
        guild_id = interaction.guild.id if interaction.guild else None
        # check bot permissions
        if not interaction.guild.me.guild_permissions.manage_messages:
            await interaction.response.send_message(t("autopublish.err.missing_manage_messages"), ephemeral=True)
            return
        set_server_config(guild_id, "autopublish", {"enabled": (enable == "True")})
        await interaction.response.send_message(
            t("autopublish.msg.enabled" if enable == "True" else "autopublish.msg.disabled"), ephemeral=True)
        log(f"Auto-publish {'enabled' if enable == 'True' else 'disabled'}.", module_name="AutoPublish", guild=interaction.guild)
        return
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        guild = message.guild
        if guild is None:
            return
        
        if message.channel.type == discord.ChannelType.news:
            if message.reference:
                return  # Ignore replies
            if message.interaction_metadata:
                return  # Ignore interaction messages
            autopublish_settings = get_server_config(guild.id, "autopublish", {})
            if not autopublish_settings:
                return
            if not autopublish_settings.get("enabled", False):
                return
            
            # check permissions
            if not guild.me.guild_permissions.manage_messages:
                return
            if not message.channel.permissions_for(guild.me).send_messages:
                return
            
            channel_data = autopublish_settings.get("channels", {})
            channel_settings = channel_data.get(str(message.channel.id), {})
            
            # check time
            # date + hour
            now = datetime.now(timezone.utc)
            outstr = now.strftime("%Y-%m-%d %H")
            last_published = channel_settings.get("last_publish", "")
            if outstr != last_published:
                # New hour: reset counter and update last_publish within autopublish settings
                channel_settings["hour_published"] = 0
                channel_settings["last_publish"] = outstr
                channel_data[str(message.channel.id)] = channel_settings
                autopublish_settings["channels"] = channel_data
                set_server_config(guild.id, "autopublish", autopublish_settings)
            hour_published = channel_settings.get("hour_published", 0)
            if hour_published >= 10:
                return  # limit to 10 publishes per hour
            hour_published += 1
            channel_settings["hour_published"] = hour_published
            channel_data[str(message.channel.id)] = channel_settings
            autopublish_settings["channels"] = channel_data
            set_server_config(guild.id, "autopublish", autopublish_settings)
            try:
                await message.publish()
                log(f"Auto-published message {message.id} in guild {guild.id}", module_name="AutoPublish", guild=guild)
            except Exception as e:
                log(f"Failed to publish message: {e}", level=logging.ERROR, module_name="AutoPublish", guild=guild)

asyncio.run(bot.add_cog(AutoPublish(bot)))

if __name__ == "__main__":
    start_bot()
