from globalenv import bot, get_global_config, set_global_config, add_app_command_error_handler, get_user_data
import discord
from discord.ext import commands
from discord import app_commands
from threading import Semaphore
import asyncio
import i18n
from i18n import t

semaphore = Semaphore()

class Statistics(commands.GroupCog, name=app_commands.locale_str("stats", i18n_key="cmd.statistics.stats.root.name")):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()
        add_app_command_error_handler(self.on_app_command_error)
    
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.command(name=app_commands.locale_str("command", i18n_key="cmd.statistics.stats.command.name"), description=app_commands.locale_str("View command usage statistics", i18n_key="cmd.statistics.stats.command.desc"))
    @app_commands.describe(full=app_commands.locale_str("Show full statistics", i18n_key="cmd.statistics.stats.command.param.full"))
    async def command_stats(self, interaction: discord.Interaction, full: bool = False):
        command_stats = get_global_config("command_usage_stats", {})
        command_error_stats = get_global_config("command_error_stats", {})
        app_command_stats = get_global_config("app_command_usage_stats", {})
        app_command_error_stats = get_global_config("app_command_error_stats", {})

        embed = discord.Embed(title=t("statistics.embed.command_stats_title"), color=discord.Color.blue())

        if full:
            sort_by_count = lambda items: sorted(items, key=lambda x: x[1], reverse=True)
            no_data = t("statistics.value.no_data")
            command_stats_str = "\n".join([f"{cmd}: {count}" for cmd, count in sort_by_count(command_stats.items())]) or no_data
            command_error_stats_str = "\n".join([f"{cmd}: {count}" for cmd, count in sort_by_count(command_error_stats.items())]) or no_data
            app_command_stats_str = "\n".join([f"{cmd}: {count}" for cmd, count in sort_by_count(app_command_stats.items())]) or no_data
            app_command_error_stats_str = "\n".join([f"{cmd}: {count}" for cmd, count in sort_by_count(app_command_error_stats.items())]) or no_data

            # anti 400
            command_stats_str = command_stats_str[:1021] + "..." if len(command_stats_str) > 1024 else command_stats_str
            command_error_stats_str = command_error_stats_str[:1021] + "..." if len(command_error_stats_str) > 1024 else command_error_stats_str
            app_command_stats_str = app_command_stats_str[:1021] + "..." if len(app_command_stats_str) > 1024 else app_command_stats_str
            app_command_error_stats_str = app_command_error_stats_str[:1021] + "..." if len(app_command_error_stats_str) > 1024 else app_command_error_stats_str
        else:
            command_stats_str = t("statistics.value.total_uses", count=sum(command_stats.values()))
            command_error_stats_str = t("statistics.value.total_errors", count=sum(command_error_stats.values()))
            app_command_stats_str = t("statistics.value.total_uses", count=sum(app_command_stats.values()))
            app_command_error_stats_str = t("statistics.value.total_errors", count=sum(app_command_error_stats.values()))

        embed.add_field(name=t("statistics.field.text_command_uses"), value=command_stats_str, inline=False)
        embed.add_field(name=t("statistics.field.text_command_errors"), value=command_error_stats_str, inline=False)
        embed.add_field(name=t("statistics.field.app_command_uses"), value=app_command_stats_str, inline=False)
        embed.add_field(name=t("statistics.field.app_command_errors"), value=app_command_error_stats_str, inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name=app_commands.locale_str("petpet-stats", i18n_key="cmd.statistics.stats.petpet_stats.name"), description=app_commands.locale_str("See how many times you've used petpet", i18n_key="cmd.statistics.stats.petpet_stats.desc"))
    async def petpet_stats(self, interaction: discord.Interaction):
        petpet_count = get_user_data(None, interaction.user.id, "petpet_count", 0)
        get_petpet_count = get_user_data(None, interaction.user.id, "get_petpet_count", 0)

        embed = discord.Embed(title=t("statistics.embed.petpet_stats_title"), color=0x00ff00)
        embed.add_field(name=t("statistics.field.petpet_given"), value=str(petpet_count), inline=False)
        embed.add_field(name=t("statistics.field.petpet_received"), value=str(get_petpet_count), inline=False)

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

    async def on_app_command_error(self, interaction: discord.Interaction, error):
        with semaphore:
            command_name = interaction.command.qualified_name if interaction.command else "unknown"
            stats = get_global_config("app_command_error_stats", {})
            stats[command_name] = stats.get(command_name, 0) + 1
            set_global_config("app_command_error_stats", stats)

asyncio.run(bot.add_cog(Statistics(bot)))
