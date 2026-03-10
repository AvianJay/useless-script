# credits to https://github.com/camprevail/pet-pet-gif
import io
import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, set_user_data, get_user_data, start_bot
from logger import log
import asyncio
import logging
import traceback
import aiohttp
import os
from typing import Tuple, List, Union
from collections import defaultdict
from random import randrange
from itertools import chain

from PIL import Image
from PIL.Image import Image as PILImage

frames = 10
resolution = (128, 128)
delay = 20

class TransparentAnimatedGifConverter(object):
    _PALETTE_SLOTSET = set(range(256))

    def __init__(self, img_rgba: PILImage, alpha_threshold: int = 0):
        self._img_rgba = img_rgba
        self._alpha_threshold = alpha_threshold

    def _process_pixels(self):
        """Set the transparent pixels to the color 0."""
        self._transparent_pixels = set(
            idx for idx, alpha in enumerate(
                self._img_rgba.getchannel(channel='A').getdata())
            if alpha <= self._alpha_threshold)

    def _set_parsed_palette(self):
        """Parse the RGB palette color `tuple`s from the palette."""
        palette = self._img_p.getpalette()
        self._img_p_used_palette_idxs = set(
            idx for pal_idx, idx in enumerate(self._img_p_data)
            if pal_idx not in self._transparent_pixels)
        self._img_p_parsedpalette = dict(
            (idx, tuple(palette[idx * 3:idx * 3 + 3]))
            for idx in self._img_p_used_palette_idxs)

    def _get_similar_color_idx(self):
        """Return a palette index with the closest similar color."""
        old_color = self._img_p_parsedpalette[0]
        dict_distance = defaultdict(list)
        for idx in range(1, 256):
            color_item = self._img_p_parsedpalette[idx]
            if color_item == old_color:
                return idx
            distance = sum((
                abs(old_color[0] - color_item[0]),  # Red
                abs(old_color[1] - color_item[1]),  # Green
                abs(old_color[2] - color_item[2])))  # Blue
            dict_distance[distance].append(idx)
        return dict_distance[sorted(dict_distance)[0]][0]

    def _remap_palette_idx_zero(self):
        """Since the first color is used in the palette, remap it."""
        free_slots = self._PALETTE_SLOTSET - self._img_p_used_palette_idxs
        new_idx = free_slots.pop() if free_slots else \
            self._get_similar_color_idx()
        self._img_p_used_palette_idxs.add(new_idx)
        self._palette_replaces['idx_from'].append(0)
        self._palette_replaces['idx_to'].append(new_idx)
        self._img_p_parsedpalette[new_idx] = self._img_p_parsedpalette[0]
        del(self._img_p_parsedpalette[0])

    def _get_unused_color(self) -> tuple:
        """ Return a color for the palette that does not collide with any other already in the palette."""
        used_colors = set(self._img_p_parsedpalette.values())
        while True:
            new_color = (randrange(256), randrange(256), randrange(256))
            if new_color not in used_colors:
                return new_color

    def _process_palette(self):
        """Adjust palette to have the zeroth color set as transparent. Basically, get another palette
        index for the zeroth color."""
        self._set_parsed_palette()
        if 0 in self._img_p_used_palette_idxs:
            self._remap_palette_idx_zero()
        self._img_p_parsedpalette[0] = self._get_unused_color()

    def _adjust_pixels(self):
        """Convert the pixels into their new values."""
        if self._palette_replaces['idx_from']:
            trans_table = bytearray.maketrans(
                bytes(self._palette_replaces['idx_from']),
                bytes(self._palette_replaces['idx_to']))
            self._img_p_data = self._img_p_data.translate(trans_table)
        for idx_pixel in self._transparent_pixels:
            self._img_p_data[idx_pixel] = 0
        self._img_p.frombytes(data=bytes(self._img_p_data))

    def _adjust_palette(self):
        """Modify the palette in the new `Image`."""
        unused_color = self._get_unused_color()
        final_palette = chain.from_iterable(
            self._img_p_parsedpalette.get(x, unused_color) for x in range(256))
        self._img_p.putpalette(data=final_palette)

    def process(self) -> Image:
        """Return the processed mode `P` `Image`."""
        self._img_p = self._img_rgba.convert(mode='P')
        self._img_p_data = bytearray(self._img_p.tobytes())
        self._palette_replaces = dict(idx_from=list(), idx_to=list())
        self._process_pixels()
        self._process_palette()
        self._adjust_pixels()
        self._adjust_palette()
        self._img_p.info['transparency'] = 0
        self._img_p.info['background'] = 0
        return self._img_p


def _create_animated_gif(images: List[PILImage], durations: Union[int, List[int]]) -> Tuple[PILImage, dict]:
    """If the image is a GIF, create an its thumbnail here."""
    save_kwargs = dict()
    new_images: List[PILImage] = []

    for frame in images:
        thumbnail = frame.copy()  # type: Image
        thumbnail_rgba = thumbnail.convert(mode='RGBA')
        thumbnail_rgba.thumbnail(size=frame.size, reducing_gap=3.0)
        converter = TransparentAnimatedGifConverter(img_rgba=thumbnail_rgba)
        thumbnail_p = converter.process()  # type: Image
        new_images.append(thumbnail_p)

    output_image = new_images[0]
    save_kwargs.update(
        format='GIF',
        save_all=True,
        optimize=False,
        append_images=new_images[1:],
        duration=durations,
        disposal=2,  # Other disposals don't work
        loop=0)
    return output_image, save_kwargs


def save_transparent_gif(images: List[PILImage], durations: Union[int, List[int]], save_file):
    """Creates a transparent GIF, adjusting to avoid transparency issues that are present in the PIL library

    Note that this does NOT work for partial alpha. The partial alpha gets discarded and replaced by solid colors.

    Parameters:
        images: a list of PIL Image objects that compose the GIF frames
        durations: an int or List[int] that describes the animation durations for the frames of this GIF
        save_file: A filename (string), pathlib.Path object or file object. (This parameter corresponds
                   and is passed to the PIL.Image.save() method.)
    Returns:
        PILImage - The PIL Image object (after first saving the image to the specified target)
    """
    root_frame, save_args = _create_animated_gif(images, durations)
    root_frame.save(save_file, **save_args)

def make(source, dest):
    """

    :param source: A filename (string), pathlib.Path object or a file object. (This parameter corresponds
                   and is passed to the PIL.Image.open() method.)
    :param dest: A filename (string), pathlib.Path object or a file object. (This parameter corresponds
                   and is passed to the PIL.Image.save() method.)
    :return: None
    """
    images = []
    base = Image.open(source).convert('RGBA').resize(resolution)

    for i in range(frames):
        squeeze = i if i < frames/2 else frames - i
        width = 0.8 + squeeze * 0.02
        height = 0.8 - squeeze * 0.05
        offsetX = (1 - width) * 0.5 + 0.1
        offsetY = (1 - height) - 0.08

        canvas = Image.new('RGBA', size=resolution, color=(0, 0, 0, 0))
        canvas.paste(base.resize((round(width * resolution[0]), round(height * resolution[1]))), (round(offsetX * resolution[0]), round(offsetY * resolution[1])))
        pet = Image.open(os.path.join("assets", "petpet", f"pet{i}.gif")).convert('RGBA').resize(resolution)
        canvas.paste(pet, mask=pet)
        images.append(canvas)

    save_transparent_gif(images, durations=20, save_file=dest)

@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class PetPetCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ctx_menu = app_commands.ContextMenu(
            name="PetPet",
            callback=self.petpet_context_menu
        )
        bot.tree.add_command(self.ctx_menu)

    @app_commands.command(name="petpet", description="生成 PetPet GIF")
    @app_commands.describe(user="要撫摸的使用者 (預設為自己)")
    async def petpet(self, interaction: discord.Interaction, user: Union[discord.Member, discord.User] = None):
        try:
            await interaction.response.defer()
            if user is None:
                user = interaction.user

            log(f"生成 petpet GIF 給 {user}", module_name="petpet", user=interaction.user, guild=interaction.guild)
            avatar_url = user.display_avatar.with_size(128).with_static_format("png").url
            async with aiohttp.ClientSession() as session:
                async with session.get(avatar_url) as resp:
                    avatar = io.BytesIO(await resp.read())
            gif_bytes = io.BytesIO()
            make(avatar, gif_bytes)
            gif_bytes.seek(0)

            file = discord.File(fp=gif_bytes, filename="petpet.gif")
            await interaction.followup.send(file=file)
            t = get_user_data(0, interaction.user.id, "petpet_count", 0)
            set_user_data(0, interaction.user.id, "petpet_count", t + 1)
            ut = get_user_data(0, user.id, "get_petpet_count", 0)
            set_user_data(0, user.id, "get_petpet_count", ut + 1)
        except Exception as e:
            await interaction.followup.send(f"生成 PetPet GIF 時發生錯誤：{e}")
            log(f"生成 PetPet GIF 時發生錯誤：{e}", module_name="petpet", level=logging.ERROR, user=interaction.user, guild=interaction.guild)
            traceback.print_exc()
    
    @commands.command(name="petpet", help="生成 PetPet GIF", aliases=["撫摸", "pet", "pp"])
    async def petpet_command(self, ctx: commands.Context, user: Union[discord.Member, discord.User] = None):
        async with ctx.typing():
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
                make(avatar, gif_bytes)
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
    
    async def petpet_context_menu(self, interaction: discord.Interaction, user: Union[discord.User, discord.Member]):
        try:
            await interaction.response.defer()
            if user is None:
                user = interaction.user

            log(f"生成 petpet GIF 給 {user}", module_name="petpet", user=interaction.user, guild=interaction.guild)
            avatar_url = user.display_avatar.with_size(128).with_static_format("png").url
            async with aiohttp.ClientSession() as session:
                async with session.get(avatar_url) as resp:
                    avatar = io.BytesIO(await resp.read())
            gif_bytes = io.BytesIO()
            make(avatar, gif_bytes)
            gif_bytes.seek(0)

            file = discord.File(fp=gif_bytes, filename="petpet.gif")
            await interaction.followup.send(file=file)
            t = get_user_data(0, interaction.user.id, "petpet_count", 0)
            set_user_data(0, interaction.user.id, "petpet_count", t + 1)
            ut = get_user_data(0, user.id, "get_petpet_count", 0)
            set_user_data(0, user.id, "get_petpet_count", ut + 1)
        except Exception as e:
            await interaction.followup.send(f"生成 PetPet GIF 時發生錯誤：{e}")
            log(f"生成 PetPet GIF 時發生錯誤：{e}", module_name="petpet", level=logging.ERROR, user=interaction.user, guild=interaction.guild)
            traceback.print_exc()

asyncio.run(bot.add_cog(PetPetCommand(bot)))

if __name__ == "__main__":
    start_bot()