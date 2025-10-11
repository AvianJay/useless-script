import discord
import asyncio
from discord import app_commands
from discord.ext import commands
from globalenv import bot, start_bot, get_server_config, set_server_config


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
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
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def set_autopublish(self, interaction: discord.Interaction, enable: bool):
        guild_id = interaction.guild.id if interaction.guild else None
        set_server_config(guild_id, "autopublish", {"enabled": enable})
        await interaction.response.send_message(f"自動發布已{'啟用' if enable else '停用'}。", ephemeral=True)
        return
    
    @commands.Cog.listener()
    async def on_message(message: discord.Message):
        guild = message.guild
        if guild is None:
            return
        
        autopublish_settings = get_server_config(guild.id, "autopublish", {})
        if not autopublish_settings:
            return
        if not autopublish_settings.get("enabled", False):
            return
        
        if message.channel.type == discord.ChannelType.news:
            try:
                await message.publish()
            except Exception as e:
                print(f"Error auto-publishing message: {e}")

asyncio.run(bot.add_cog(AutoPublish(bot)))

if __name__ == "__main__":
    start_bot()
