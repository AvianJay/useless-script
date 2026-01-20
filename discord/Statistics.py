from globalenv import bot, get_global_config, set_global_config
import discord
from discord.ext import commands
from discord import app_commands
from threading import Semaphore
import asyncio

semaphore = Semaphore()

class Statistics(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()
    
    @app_commands.command(name="stats", description="查看指令使用統計")
    @app_commands.describe(full="是否顯示完整統計數據")
    async def stats(self, interaction: discord.Interaction, full: bool = False):
        command_stats = get_global_config("command_usage_stats", {})
        command_error_stats = get_global_config("command_error_stats", {})
        app_command_stats = get_global_config("app_command_usage_stats", {})

        embed = discord.Embed(title="指令使用統計", color=discord.Color.blue())
        
        if full:
            command_stats_str = "\n".join([f"{cmd}: {count}" for cmd, count in command_stats.items()]) or "無數據"
            command_error_stats_str = "\n".join([f"{cmd}: {count}" for cmd, count in command_error_stats.items()]) or "無數據"
            app_command_stats_str = "\n".join([f"{cmd}: {count}" for cmd, count in app_command_stats.items()]) or "無數據"
        else:
            command_stats_str = f"總計 {sum(command_stats.values())} 次使用"
            command_error_stats_str = f"總計 {sum(command_error_stats.values())} 次錯誤"
            app_command_stats_str = f"總計 {sum(app_command_stats.values())} 次使用"

        embed.add_field(name="文字指令使用次數", value=command_stats_str, inline=False)
        embed.add_field(name="文字指令錯誤次數", value=command_error_stats_str, inline=False)
        embed.add_field(name="應用程式指令使用次數", value=app_command_stats_str, inline=False)

        await interaction.response.send_message(embed=embed)
    
    @commands.Cog.listener()
    async def on_command(self, ctx):
        with semaphore:
            command_name = ctx.command.qualified_name if ctx.command else "unknown"
            stats = get_global_config("command_usage_stats", {})
            stats[command_name] = stats.get(command_name, 0) + 1
            set_global_config("command_usage_stats", stats)
    
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        with semaphore:
            command_name = ctx.command.qualified_name if ctx.command else "unknown"
            stats = get_global_config("command_error_stats", {})
            stats[command_name] = stats.get(command_name, 0) + 1
            set_global_config("command_error_stats", stats)
    
    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, application_command: discord.app_commands.Command):
        with semaphore:
            command_name = application_command.qualified_name if application_command else "unknown"
            stats = get_global_config("app_command_usage_stats", {})
            stats[command_name] = stats.get(command_name, 0) + 1
            set_global_config("app_command_usage_stats", stats)

asyncio.run(bot.add_cog(Statistics(bot)))