from PIL import Image, ImageDraw, ImageFont
import io
import os
import sys
import discord
import aiohttp
from discord.ext import commands
from discord import app_commands
from globalenv import bot, start_bot, on_ready_tasks
from playwright.async_api import async_playwright
import asyncio
import chat_exporter
from logger import log
import logging
import traceback
import random


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
    """
    糟糕的 Make it a Quote。
    回覆一則訊息以生成糟糕的 Make it a Quote 圖片。
    """
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


async def screenshot(message: discord.Message):
    # check browser alive
    global browser
    if browser is None:
        raise Exception("瀏覽器尚未啟動，請稍後再試。")
    if not browser.is_connected():
        raise Exception("瀏覽器已關閉，請稍後再試。")

    # try to get previous message (group consecutive messages from same author)
    messages = [message]
    try:
        # Logic to find previous messages from the same author within reason
        # This logic was slightly different in the two commands, unifying to the more robust history check
        # However, the provided snippet for `screenshot_cmd` had a weird while loop with `current_msg.next()` which isn't standard discord.py async iterator usage.
        # The `screenshot_generator` logic using `async for` is cleaner. Let's use that logic but adapted for a helper.
        
        # Note: The original context menu code appended to `messages` then seemingly relied on `chat_exporter` handling order or `messages` being in reverse order?
        # `chat_exporter` usually expects messages in chronological order.
        # The context menu code: `messages.append(msg)` inside `history(oldest_first=False)` means `messages` is [target, target-1, target-2].
        # Then it passes this list to chat_exporter.
        
        if not message.reference and not message.interaction:
            async for msg in message.channel.history(limit=10, before=message.created_at, oldest_first=False):
                if msg.author.id == message.author.id:
                    messages.append(msg)
                    if msg.reference or msg.interaction:
                        break
                else:
                    break
    except Exception:
        traceback.print_exc()
    
    # chat_exporter expects chronological order usually, so reverse to [target-2, target-1, target]
    messages.reverse()

    try:
        html_content = await chat_exporter.raw_export(
            message.channel,
            messages=messages,
            tz_info="Asia/Taipei",
            guild=message.channel.guild,
            bot=bot,
            raise_exceptions=True
        )
    except Exception as e:
        log(f"生成 HTML 失敗: {e}", module_name="MessageImage", level=logging.ERROR)
        traceback.print_exc()
        raise Exception(f"生成 HTML 失敗: {e}")

    try:
        page = await browser.new_page()
        await page.set_content(html_content, wait_until="networkidle")
        # Force width to fit content so the screenshot isn't full width
        await page.add_style_tag(content=".chatlog__message-group { width: fit-content; }")
        # Locate the message group container
        image_bytes = await page.locator('.chatlog__message-group').screenshot(type="png")
        await page.close()
    except Exception as e:
        log(f"截圖失敗: {e}", module_name="MessageImage", level=logging.ERROR)
        traceback.print_exc()
        # If screenshot fails, maybe we want to return the HTML for debugging? 
        # The original code did this in one place. For simplicity in a shared function, let's just raise.
        raise Exception(f"截圖失敗: {e}")

    return io.BytesIO(image_bytes)


@bot.tree.context_menu(name="截圖生成器")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def screenshot_generator(interaction: discord.Interaction, message: discord.Message):
    await interaction.response.defer()
    try:
        buffer = await screenshot(message)
        await interaction.followup.send(file=discord.File(buffer, filename="screenshot.png"))
        log("截圖生成完成", module_name="MessageImage", user=interaction.user, guild=interaction.guild)
    except Exception as e:
        await interaction.followup.send(f"截圖失敗: {e}", ephemeral=True)


@bot.command(name="screenshot", aliases=["ss", "sgen", "screenshotgen"])
async def screenshot_cmd(ctx: commands.Context):
    """
    截圖生成器。
    回覆一則訊息以截圖該訊息。
    """
    # try to get replied message
    message = None
    if ctx.message.reference:
        ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        message = ref_msg
    
    if not message:
        await ctx.reply("錯誤：沒有回覆的訊息。")
        return

    try:
        buffer = await screenshot(message)
        await ctx.reply(file=discord.File(buffer, filename="screenshot.png"))
        log("截圖生成完成", module_name="MessageImage", user=ctx.author, guild=ctx.guild)
    except Exception as e:
        await ctx.reply(f"截圖失敗: {e}")

whatisthisguytalking_images = []

async def generate_whatisthisguytalking(message: discord.Message):
    file_path = random.choice(whatisthisguytalking_images)
    screenshot_buffer = await screenshot(message)

    template_image = Image.open(file_path)
    screenshot_pil_image = Image.open(screenshot_buffer)

    # Determine target width (use the width of the template image)
    target_width = template_image.width

    # Resize screenshot image to match the target width, maintaining aspect ratio
    screenshot_aspect_ratio = screenshot_pil_image.width / screenshot_pil_image.height
    new_screenshot_height = int(target_width / screenshot_aspect_ratio)
    resized_screenshot = screenshot_pil_image.resize((target_width, new_screenshot_height), Image.LANCZOS)

    # Create a new blank image with the combined height and target width
    combined_height = resized_screenshot.height + template_image.height
    combined_image = Image.new("RGB", (target_width, combined_height))

    # Paste the resized screenshot image at the top
    combined_image.paste(resized_screenshot, (0, 0))
    # Paste the template image below the screenshot
    combined_image.paste(template_image, (0, resized_screenshot.height))

    image_bytes = io.BytesIO()
    combined_image.save(image_bytes, "PNG")
    image_bytes.seek(0)
    return image_bytes


@bot.tree.context_menu(name="這傢伙在說什麼呢")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def whatisthisguytalking(interaction: discord.Interaction, message: discord.Message):
    await interaction.response.defer()
    try:
        buffer = await generate_whatisthisguytalking(message)
        await interaction.followup.send(file=discord.File(buffer, filename="whatisthisguytalking.png"))
        log("引用生成完成", module_name="MessageImage", user=interaction.user, guild=interaction.guild)
    except Exception as e:
        await interaction.followup.send(f"引用失敗: {e}", ephemeral=True)

browser = None

async def setup_browser():
    global browser
    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.launch()
    except Exception as e:
        log(f"啟動瀏覽器失敗: {e}", module_name="MessageImage", level=logging.ERROR)
    log("Playwright 瀏覽器已啟動", module_name="MessageImage")
    while True:
        await asyncio.sleep(60)
        if not browser.is_connected():
            log("Playwright 瀏覽器已關閉，重新啟動中...", module_name="MessageImage", level=logging.WARNING)
            browser = await playwright.chromium.launch()
            log("Playwright 瀏覽器已重新啟動", module_name="MessageImage")

on_ready_tasks.append(setup_browser)

async def load_whatisthisguytalking_images():
    global whatisthisguytalking_images
    try:
        dir = "./whatisthisguytalking-images"
        for file in os.listdir(dir):
            if file.endswith(".png") or file.endswith(".jpg") or file.endswith(".jpeg") or file.endswith(".gif") or file.endswith(".webp"):
                whatisthisguytalking_images.append(os.path.join(dir, file))
    except Exception as e:
        log(f"載入引用圖片失敗: {e}", module_name="MessageImage", level=logging.ERROR)

on_ready_tasks.append(load_whatisthisguytalking_images)


if __name__ == "__main__":
    start_bot()