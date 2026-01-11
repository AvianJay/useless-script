import discord
import asyncio
from discord import app_commands
from discord.ext import commands
from globalenv import bot, start_bot, get_server_config, set_server_config
from logger import log
import logging


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class AutoPublish(commands.GroupCog, name=app_commands.locale_str("autopublish")):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    @app_commands.command(name=app_commands.locale_str("view"), description="查看公告自動發布設定")
    async def view_autopublish_settings(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        autopublish_settings = get_server_config(guild_id, "autopublish", {})

        await interaction.response.send_message(f"自動發布{'已啟用' if autopublish_settings.get('enabled', False) else '未啟用'}。", ephemeral=True)
        return

    @app_commands.command(name=app_commands.locale_str("settings"), description="設定自動發布")
    @app_commands.describe(enable="是否啟用自動發布")
    @app_commands.choices(enable=[
        app_commands.Choice(name="啟用", value="True"),
        app_commands.Choice(name="停用", value="False"),
    ])
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def set_autopublish(self, interaction: discord.Interaction, enable: str):
        guild_id = interaction.guild.id if interaction.guild else None
        # check bot permissions
        if not interaction.guild.me.guild_permissions.manage_messages:
            await interaction.response.send_message("機器人需要管理訊息權限才能設定自動發布。", ephemeral=True)
            return
        set_server_config(guild_id, "autopublish", {"enabled": (enable == "True")})
        await interaction.response.send_message(f"自動發布已{'啟用' if enable == 'True' else '停用'}。", ephemeral=True)
        log(f"自動發布已{'啟用' if enable == 'True' else '停用'}。", module_name="AutoPublish", guild=interaction.guild)
        return
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        guild = message.guild
        if guild is None:
            return
        
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
        
        if message.channel.type == discord.ChannelType.news:
            if message.reference:
                return  # Ignore replies
            try:
                await message.publish()
                log(f"Auto-published message ID {message.id} in guild {guild.id}", module_name="AutoPublish", guild=guild)
            except Exception as e:
                log(f"Error auto-publishing message: {e}", level=logging.ERROR, module_name="AutoPublish", guild=guild)

asyncio.run(bot.add_cog(AutoPublish(bot)))

if __name__ == "__main__":
    start_bot()
