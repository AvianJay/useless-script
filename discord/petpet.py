from petpetgif import petpet
import io
import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, set_user_data, get_user_data, start_bot
from typing import Union
from logger import log
import asyncio
import logging
import traceback
import aiohttp

@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class PetPetCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="petpet", description="生成 PetPet GIF")
    @app_commands.describe(user="要撫摸的使用者 (預設為自己)")
    async def petpet(self, interaction: discord.Interaction, user: Union[discord.Member, discord.User] = None):
        try:
            if user is None:
                user = interaction.user

            log(f"生成 petpet GIF 給 {user}", module_name="petpet", user=interaction.user, guild=interaction.guild)
            avatar_url = user.display_avatar.with_size(128).with_static_format("png").url
            async with aiohttp.ClientSession() as session:
                async with session.get(avatar_url) as resp:
                    avatar = io.BytesIO(await resp.read())
            gif_bytes = io.BytesIO()
            petpet.make(avatar, gif_bytes)
            gif_bytes.seek(0)

            file = discord.File(fp=gif_bytes, filename="petpet.gif")
            await interaction.response.send_message(file=file)
            t = get_user_data(0, interaction.user.id, "petpet_count", 0)
            set_user_data(0, interaction.user.id, "petpet_count", t + 1)
            ut = get_user_data(0, user.id, "get_petpet_count", 0)
            set_user_data(0, user.id, "get_petpet_count", ut + 1)
        except Exception as e:
            await interaction.response.send_message(f"生成 PetPet GIF 時發生錯誤：{e}")
            log(f"生成 PetPet GIF 時發生錯誤：{e}", module_name="petpet", level=logging.ERROR, user=interaction.user, guild=interaction.guild)
            traceback.print_exc()
    
    @commands.command(name="petpet", help="生成 PetPet GIF", aliases=["撫摸", "pet", "p"])
    async def petpet_command(self, ctx: commands.Context, user: Union[discord.Member, discord.User] = None):
        try:
            if user is None:
                if ctx.message.reference and ctx.message.reference.resolved:
                    user = ctx.message.reference.resolved.author
                else:
                    user = ctx.author

            log(f"生成 petpet GIF 給 {user}", module_name="petpet", user=ctx.author, guild=ctx.guild)
            avatar_url = user.display_avatar.with_size(128).with_static_format("png").url
            async with aiohttp.ClientSession() as session:
                async with session.get(avatar_url) as resp:
                    avatar = io.BytesIO(await resp.read())

            gif_bytes = io.BytesIO()
            petpet.make(avatar, gif_bytes)
            gif_bytes.seek(0)

            file = discord.File(fp=gif_bytes, filename="petpet.gif")
            await ctx.reply(file=file)
            t = get_user_data(0, ctx.author.id, "petpet_count", 0)
            set_user_data(0, ctx.author.id, "petpet_count", t + 1)
            ut = get_user_data(0, user.id, "get_petpet_count", 0)
            set_user_data(0, user.id, "get_petpet_count", ut + 1)
        except Exception as e:
            await ctx.reply(f"生成 PetPet GIF 時發生錯誤：{e}")
            log(f"生成 PetPet GIF 時發生錯誤：{e}", module_name="petpet", level=logging.ERROR, user=ctx.author, guild=ctx.guild)
            traceback.print_exc()
    
    @app_commands.command(name="petpet-stats", description="查看你使用 petpet 指令的次數")
    async def petpet_stats(self, interaction: discord.Interaction):
        petpet_count = get_user_data(None, interaction.user.id, "petpet_count", 0)
        get_petpet_count = get_user_data(None, interaction.user.id, "get_petpet_count", 0)

        embed = discord.Embed(title="PetPet 統計", color=0x00ff00)
        embed.add_field(name="你 PetPet 了多少次？", value=str(petpet_count), inline=False)
        embed.add_field(name="被別人 PetPet 了多少次？", value=str(get_petpet_count), inline=False)

        await interaction.response.send_message(embed=embed)

asyncio.run(bot.add_cog(PetPetCommand(bot)))

if __name__ == "__main__":
    start_bot()