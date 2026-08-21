from globalenv import bot, get_server_config, set_server_config, config, get_user_data, set_user_data
from typing import Optional
from logger import log
import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from expiring_dict import ExpiringDict
import random

import i18n
from i18n import t

# 每個階段有幾句罐頭回覆；key 是 customprefix.ping.stage<N>.<i>。
PING_FLAVOR_COUNTS = {3: 4, 4: 4, 5: 4, 6: 2, 7: 4, 100: 2}


def _ping_flavor(stage: int) -> list[str]:
    return [t(f"customprefix.ping.stage{stage}.{i}")
            for i in range(1, PING_FLAVOR_COUNTS[stage] + 1)]

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

class DontRemindMeProfixView(i18n.I18nView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label=i18n.K("customprefix.btn.dont_remind"), style=discord.ButtonStyle.secondary, custom_id="dont_remind_prefix")
    async def dont_remind_prefix(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title=t("customprefix.remind_off.title"),
            description=t("customprefix.remind_off.desc"),
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
    

    @commands.command(name="setprefix", help="設置自定義前綴", usage="<prefix>")  # i18n: skip (help= 在 import 期求值，待 PrettyHelpCommand 在地化)
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
            await ctx.send(t("customprefix.msg.reset", prefix=config("prefix", "!")))
            log(f"Reset prefix for guild {ctx.guild} ({guild_id}) to the default", module_name="CustomPrefix", user=ctx.author, guild=ctx.guild)
        else:
            set_server_config(guild_id, "custom_prefix", prefix)
            await ctx.send(t("customprefix.msg.set", prefix=prefix))
            log(f"Set prefix for guild {ctx.guild} ({guild_id}) to `{prefix}`", module_name="CustomPrefix", user=ctx.author, guild=ctx.guild)
    
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if message.guild is None:
            return
        async with i18n.guild_scope(message.guild.id, user_id=message.author.id):
            await self._on_message_impl(message)

    async def _on_message_impl(self, message):
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
                await message.channel.send(t("customprefix.msg.tip", prefix=prefix), view=DontRemindMeProfixView())
                usercache[cache_key] = 0
        if message.content == bot.user.mention:
            try:
                pingcount = usercache[message.author.id]
            except KeyError:
                pingcount = 0
            if pingcount < 3:
                prefix = await determine_prefix(self.bot, message)
                await message.channel.send(t("customprefix.msg.mention_reply", prefix=prefix))
            elif pingcount in PING_FLAVOR_COUNTS:
                msgs = _ping_flavor(pingcount)
                if pingcount == 6:
                    # 這兩句沒有可翻譯的部分
                    msgs = msgs + ["...", f"{message.author.mention} {message.author.mention} {message.author.mention}"]
                await message.channel.send(random.choice(msgs))
            else:
                return
            usercache[message.author.id] = pingcount + 1

asyncio.run(bot.add_cog(CustomPrefix(bot)))