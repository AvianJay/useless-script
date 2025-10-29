import discord
from discord.ext import commands
from globalenv import config, bot, start_bot, modules
import asyncio
import logging

async def _log(*messages, level = logging.INFO, module_name: str = "General", user: discord.User = None, guild: discord.Guild = None):
    logger = logging.getLogger(module_name)
    if not logger.hasHandlers():
        logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler('bot.log', encoding='utf-8')
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    message = ' '.join(str(m) for m in messages)
    if level == logging.INFO:
        logger.info(message)
    elif level == logging.WARNING:
        logger.warning(message)
    elif level == logging.ERROR:
        logger.error(message)
    elif level == logging.DEBUG:
        logger.debug(message)
    else:
        logger.log(level, message)

    # Also print to console
    print(f"[{module_name}] {message}")
    
    # try to send to a specific discord channel if configured
    try:
        await bot.wait_until_ready()
    except Exception:
        return
    log_channel_id = config("log_channel_id", None)
    if log_channel_id:
        channel = bot.get_channel(log_channel_id)
        if channel:
            try:
                # embed message
                color = 0x00ff00 if level == logging.INFO else 0xffff00 if level == logging.WARNING else 0xff0000 if level == logging.ERROR else 0x0000ff
                embed = discord.Embed(title=module_name, description=message, color=color)
                if user:
                    embed.add_field(name="使用者ID", value=f"`{user.id}`", inline=False)  # easy to copy user id
                    to_show_name = f"{user.display_name} ({user.name})" if user.display_name != user.name else user.name
                    embed.set_author(name=to_show_name, icon_url=user.display_avatar.url if user.display_avatar else None)
                if guild:
                    embed.add_field(name="伺服器ID", value=f"`{guild.id}`", inline=False)  # easy to copy guild id
                    embed.set_footer(text=guild.name if guild.name else guild.id, icon_url=guild.icon.url if guild.icon else None)
                await channel.send(embed=embed)
            except Exception as e:
                print(f"[!] Error sending log message to Discord channel: {e}")

def log(*messages, level = logging.INFO, module_name: str = "General", user: discord.User = None, guild: discord.Guild = None):
    if "logger" not in modules:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_log(*messages, level=level, module_name=module_name, user=user, guild=guild))
    except RuntimeError:
        asyncio.run(_log(*messages, level=level, module_name=module_name, user=user, guild=guild))

class LoggerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author == self.bot.user:
            return
        # only log dm messages
        if isinstance(message.channel, discord.DMChannel):
            await log(f"收到了私訊 {message.author}: {message.content}", module_name="Logger", level=logging.INFO, user=message.author)
        # else:
        #     await log(f"收到了訊息 {message.author}: {message.content}", module_name="Logger", level=logging.INFO, user=message.author, guild=message.guild)

    @commands.Cog.listener()
    async def on_command(self, ctx):
        log(f"指令被觸發: {ctx.command} 由 {ctx.author}", module_name="Logger", level=logging.INFO, user=ctx.author, guild=ctx.guild)

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        log(f"指令 {ctx.command} 由 {ctx.author} 觸發時發生錯誤: {error}", module_name="Logger", level=logging.ERROR, user=ctx.author, guild=ctx.guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        log(f"加入了新的伺服器: {guild.name} (ID: {guild.id})", module_name="Logger", level=logging.INFO)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        log(f"離開了伺服器: {guild.name} (ID: {guild.id})", module_name="Logger", level=logging.INFO)

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, application_command: discord.app_commands.Command):
        # maybe it is command or context menu
        if isinstance(application_command, discord.app_commands.ContextMenu):
            log(f"應用程式選單被觸發: {application_command.name}", module_name="Logger", level=logging.INFO, user=interaction.user, guild=interaction.guild)
        else:
            log(f"應用程式指令被觸發: {application_command.parent.name + ' ' + application_command.name if application_command.parent else application_command.name}", module_name="Logger", level=logging.INFO, user=interaction.user, guild=interaction.guild)

    # @commands.Cog.listener()
    # async def on_ready(self):
    #     log("機器人已準備就緒。", module_name="Logger", level=logging.INFO)
        
    @commands.Cog.listener()
    async def on_error(self, event_method, *args, **kwargs):
        log(f"事件 {event_method} 發生錯誤。", module_name="Logger", level=logging.ERROR)

if "logger" in modules:
    asyncio.run(bot.add_cog(LoggerCog(bot)))

if __name__ == "__main__":
    start_bot()
