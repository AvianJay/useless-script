from globalenv import bot, get_server_config, set_server_config, config
from typing import Optional
from logger import log
from discord import app_commands, commands
from discord.ext import commands
import asyncio

async def determine_prefix(bot, message):
    guild = message.guild
    if guild:
        guild_id = str(guild.id)
        prefix = get_server_config(guild_id, "custom_prefix", config("prefix", "!"))
        return prefix


class CustomPrefix(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.command_prefix = determine_prefix
        log("CustomPrefix cog loaded.", module_name="CustomPrefix")
    

    @commands.command(name="setprefix", help="設置自定義前綴", usage="<prefix>")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def setprefix(self, ctx, prefix: Optional[str] = None):
        """
        設置伺服器的自定義前綴。如果不提供前綴，則重置為預設值。
        
        用法: `setprefix <prefix>` 或 `setprefix` 來重置前綴。
        
        :param prefix: 自定義前綴字串，若為 None 則重置為預設前綴。
        """
        guild_id = str(ctx.guild.id)
        if prefix is None:
            set_server_config(guild_id, "custom_prefix", None)
            await ctx.send(f"已重置前綴為預設值：`{config('prefix', '!')}`")
            log(f"重置伺服器 {ctx.guild} ({guild_id}) 的前綴為預設值", module_name="CustomPrefix", user=ctx.author, guild=ctx.guild)
        else:
            set_server_config(guild_id, "custom_prefix", prefix)
            await ctx.send(f"已將前綴設置為：`{prefix}`")
            log(f"設置伺服器 {ctx.guild} ({guild_id}) 的前綴為 `{prefix}`", module_name="CustomPrefix", user=ctx.author, guild=ctx.guild)

asyncio.run_coroutine_threadsafe(bot.add_cog(CustomPrefix(bot)), bot.loop)