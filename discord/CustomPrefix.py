from globalenv import bot, get_server_config, set_server_config, config
from typing import Optional
from logger import log
from discord import app_commands
from discord.ext import commands
import asyncio
from expiring_dict import ExpiringDict

usercache = ExpiringDict(150)

async def determine_prefix(bot, message):
    guild = message.guild
    if guild:
        guild_id = str(guild.id)
        prefix = get_server_config(guild_id, "custom_prefix", config("prefix", "!"))
        return prefix
    return config("prefix", "!")


class CustomPrefix(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.command_prefix = determine_prefix
        log("CustomPrefix cog loaded.", module_name="CustomPrefix")
    

    @commands.command(name="setprefix", help="設置自定義前綴", usage="<prefix>")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
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
    
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if message.content == bot.user.mention:
            try:
                pingcount = usercache[message.author.id]
            except KeyError:
                pingcount = 0
            if pingcount < 3:
                prefix = await determine_prefix(self.bot, message)
                await message.channel.send(f"你在找我嗎 :O\n我的前綴是：`{prefix}`！")
            elif pingcount == 3:
                await message.channel.send("好啦好啦，我知道你在找我 XD")
            elif pingcount == 4:
                await message.channel.send("欸欸欸，冷靜點啦！")
            elif pingcount == 5:
                await message.channel.send("再 ping 我我就不理你了喔！")
            elif pingcount == 6:
                await message.channel.send("...")
            elif pingcount == 7:
                await message.channel.send("好吧，我不理你了。")
            elif pingcount == 100:
                await message.channel.send("你還在 ping 我？真是執著啊...")
            else:
                return
            usercache[message.author.id] = pingcount + 1

asyncio.run(bot.add_cog(CustomPrefix(bot)))