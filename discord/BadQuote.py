from PIL import Image, ImageDraw, ImageFont
import io
import os
import sys
import discord
import aiohttp
from discord.ext import commands
from discord import app_commands
from globalenv import bot, start_bot


if getattr(sys, 'frozen', False):
    fontdir = sys._MEIPASS
else:
    fontdir = "."

async def create(message: discord.Message):
    name = message.author.display_name
    avatar_url = message.author.display_avatar.url if message.author.display_avatar else None
    message = message.content.strip()
    # load avatar
    async with aiohttp.ClientSession() as session:
        async with session.get(avatar_url) as resp:
            avatar = Image.open(io.BytesIO(await resp.read())).convert("RGBA").resize((630, 630))

    # new image
    width, height = 1200, 630
    img = Image.new("RGBA", (width, height), (0, 0, 0, 255))

    # draw
    draw = ImageDraw.Draw(img)
    img.paste(avatar, (0, 0), avatar)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    drawol = ImageDraw.Draw(overlay)

    for i in range(51):
        alpha = min((i+1) * 5, 255)  # 保險起見
        drawol.arc([-300 + i, -200, 630 + i, 1030], 270, 90, fill=(0, 0, 0, alpha), width=150)

    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    draw.rectangle([540, 0, 700, 300], fill="black")

    # font
    font_msg = ImageFont.truetype(os.path.join(fontdir, "notobold.ttf"), 55)
    font_name = ImageFont.truetype(os.path.join(fontdir, "notolight.ttf"), 25)

    # msg
    # message = "好。"
    display_name = f" - {name}"

    # area
    x_min, x_max = 560, 1150
    y_min, y_max = 50, 580

    # get size
    def get_text_size(text, font):
        bbox = font.getmask(text).getbbox()
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        return width, height

    msg_w, msg_h = get_text_size(message, font_msg)
    name_w, name_h = get_text_size(display_name, font_name)

    # center
    center_x = (x_min + x_max) // 2
    msg_x = center_x - msg_w // 2
    name_x = center_x - name_w // 2

    total_text_height = msg_h + 20 + name_h
    start_y = y_min + (y_max - y_min - total_text_height) // 2
    msg_y = start_y
    name_y = start_y + msg_h + 40

    # 繪製文字
    draw.text((msg_x, msg_y), message, font=font_msg, fill="white")
    # draw.text((name_x, name_y), display_name, font=font_name, fill="white")

    # 建立透明文字圖層
    text_img = Image.new("RGBA", (400, 100), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_img)
    text_draw.text((0, 0), display_name, font=font_name, fill="white")

    # 仿斜體：X 軸傾斜（負值向左斜，正值向右斜）
    sheared = text_img.transform(
        text_img.size,
        Image.AFFINE,
        (1, 0.2, 0, 0, 1, 0),  # X 傾斜 0.3 的效果
        resample=Image.BICUBIC,
    )
    img.paste(sheared, (name_x, name_y), sheared)

    # save to bytes
    output_buffer = io.BytesIO()
    img.save(output_buffer, format="PNG")
    output_buffer.seek(0)
    return output_buffer


@bot.tree.context_menu(name="糟糕的Make it a Quote")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def make_it_a_quote(interaction: discord.Interaction, message: discord.Message):
    if not message.content or message.content.strip() == "":
        await interaction.response.send_message("錯誤：訊息沒有內容。", ephemeral=True)
        return
    await interaction.response.defer()
    output_buffer = await create(message)
    await interaction.followup.send(file=discord.File(output_buffer, filename="messenger_quote.png"))

@bot.command(name="badquote", aliases=["bquote", "bq", "makeitaquote", "miq"])
async def badquote(ctx: commands.Context):
    # try to get replied message
    if ctx.message.reference:
        ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        message = ref_msg
    if not message:
        await ctx.send("錯誤：沒有回覆的訊息。")
        return
    if not message.content or message.content.strip() == "":
        await ctx.send("錯誤：訊息沒有內容。")
        return
    output_buffer = await create(message)
    await ctx.reply(file=discord.File(output_buffer, filename="messenger_quote.png"))

if __name__ == "__main__":
    start_bot()