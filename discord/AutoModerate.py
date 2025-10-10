import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, start_bot, get_user_data, set_user_data, get_all_user_data, get_server_config, set_server_config


@app_commands.guild_only()
class AutoModerate(commands.GroupCog, name=app_commands.locale_str("automod")):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()