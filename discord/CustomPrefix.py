from globalenv import bot, get_server_config, set_server_config, config, get_user_data, set_user_data
from typing import Optional
from logger import log
import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from expiring_dict import ExpiringDict
import random

usercache = ExpiringDict(180)

def get_prefix(guild: Optional[discord.Guild]) -> str:
    if guild is None:
        return config("prefix", "!")
    guild_id = str(guild.id)
    return get_server_config(guild_id, "custom_prefix", config("prefix", "!"))

async def determine_prefix(bot, message):
    guild = message.guild
    if guild:
        guild_id = str(guild.id)
        prefix = get_server_config(guild_id, "custom_prefix", config("prefix", "!"))
        return str(prefix)
    return str(config("prefix", "!"))

class DontRemindMeProfixView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="不要再提醒了", style=discord.ButtonStyle.secondary, custom_id="dont_remind_prefix")
    async def dont_remind_prefix(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="好吧",
            description="我不會再提醒你了！如果你忘記前綴，可以嘗試提及我來查看目前伺服器的前綴！",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        set_user_data(interaction.guild.id if interaction.guild else 0, interaction.user.id, "dont_remind_prefix", True)


class CustomPrefix(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.command_prefix = determine_prefix
        # log("CustomPrefix cog loaded.", module_name="CustomPrefix")
    
    @commands.Cog.listener()
    async def on_ready(self):
        bot.add_view(DontRemindMeProfixView())
    

    @commands.command(name="setprefix", help="設置自定義前綴", usage="<prefix>")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def setprefix(self, ctx, prefix: Optional[str] = None):
        """
        設置伺服器的自定義前綴。如果不提供前綴，則重置為預設值。
        
        用法: `setprefix <prefix>` 或 `setprefix` 來重置前綴。
        
        :param prefix: 自定義前綴字串，若為 None 則重置為預設前綴。
        """
        await asyncio.sleep(.5)  # wait for on_message to finish
        guild_id = str(ctx.guild.id)
        if prefix is None:
            set_server_config(guild_id, "custom_prefix", config("prefix", "!"))
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
        if message.guild is None:
            return
        prefix = get_server_config(str(message.guild.id), "custom_prefix", config("prefix", "!"))
        curr_prefix = config("prefix", "!")
        if message.content.startswith(curr_prefix) and prefix != curr_prefix and prefix:
            # tip user about custom prefix, but rate-limit per user to avoid spam
            if get_user_data(message.guild.id, message.author.id, "dont_remind_prefix", False):
                return
            cache_key = ("prefix_tip", message.author.id)
            try:
                _ = usercache[cache_key]
            except KeyError:
                await message.channel.send(f"提醒：本伺服器的自定義前綴為：`{prefix}`！", view=DontRemindMeProfixView())
                usercache[cache_key] = 0
        if message.content == bot.user.mention:
            try:
                pingcount = usercache[message.author.id]
            except KeyError:
                pingcount = 0
            if pingcount < 3:
                prefix = await determine_prefix(self.bot, message)
                await message.channel.send(f"你在找我嗎 :O\n我的前綴是：`{prefix}`！")
            elif pingcount == 3:
                msgs = [
                    "是有什麼事嗎？",
                    "需要幫忙嗎？",
                    "好啦好啦，我知道你在找我 XD",
                    "有事請說，不要一直 ping 我啦！",
                ]
                await message.channel.send(random.choice(msgs))
            elif pingcount == 4:
                msgs = [
                    "冷靜一點啦！",
                    "別這樣一直 ping 我嘛～",
                    "我會累的欸...",
                    "欸欸欸，冷靜點啦！"
                ]
                await message.channel.send(random.choice(msgs))
            elif pingcount == 5:
                msgs = [
                    "你真的很執著耶...",
                    "再這樣我就要生氣了喔！",
                    "欸，你這樣不好喔！",
                    "再 ping 我我就不理你了喔！"
                ]
                await message.channel.send(random.choice(msgs))
            elif pingcount == 6:
                msgs = [
                    "我不想在這裡跟你耗時間。",
                    "你還在 ping 我？",
                    "...",
                    f"{message.author.mention} {message.author.mention} {message.author.mention}",
                ]
                await message.channel.send(random.choice(msgs))
            elif pingcount == 7:
                msgs = [
                    "好吧，我不理你了。",
                    "你這樣一直 ping 我真的很煩耶。",
                    "我累了，我要休息了。",
                    "再見。"
                ]
                await message.channel.send(random.choice(msgs))
            elif pingcount == 100:
                msgs = [
                    "你還在 ping 我？真是執著啊...",
                    "恭喜你獲得了 3 分鐘內 ping 我 100 次的成就💀",
                ]
                await message.channel.send(random.choice(msgs))
            else:
                return
            usercache[message.author.id] = pingcount + 1

asyncio.run(bot.add_cog(CustomPrefix(bot)))