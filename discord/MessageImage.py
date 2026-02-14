from PIL import Image, ImageDraw, ImageFont
import io
import os
import sys
import discord
import aiohttp
from discord.ext import commands
from discord import app_commands
from globalenv import bot, start_bot, on_ready_tasks, modules, get_command_mention, config, get_server_config, set_server_config
from playwright.async_api import async_playwright
import asyncio
import chat_exporter
from logger import log
import logging
import traceback
import random
import time
if "OwnerTools" in modules:
    import OwnerTools


if getattr(sys, 'frozen', False):
    fontdir = os.path.join(sys._MEIPASS, 'assets')
else:
    fontdir = os.path.join(os.path.dirname(__file__), 'assets')

import re
import emoji

def resolve_mentions(text, message):
    guild = message.guild
    if not guild:
         return text
    
    def replace_user(match):
        uid = int(match.group(1))
        member = guild.get_member(uid)
        return f"@{member.display_name}" if member else match.group(0)

    def replace_role(match):
        rid = int(match.group(1))
        role = guild.get_role(rid)
        return f"@{role.name}" if role else match.group(0)

    def replace_channel(match):
        cid = int(match.group(1))
        channel = guild.get_channel(cid)
        return f"#{channel.name}" if channel else match.group(0)

    text = re.sub(r'<@!?(\d+)>', replace_user, text)
    text = re.sub(r'<@&(\d+)>', replace_role, text)
    text = re.sub(r'<#(\d+)>', replace_channel, text)
    return text

class Segment:
    def __init__(self, type, content, url=None):
        self.type = type # 'text' or 'emoji'
        self.content = content
        self.url = url
        self.width = 0
        self.height = 0
        self.image = None

async def prepare_segments(text, font):
    segments = []
    # Split by custom emojis <a:name:id> or <:name:id>
    pattern = r'<(a?):(\w+):(\d+)>'
    last_pos = 0
    temp_segments = []
    
    for match in re.finditer(pattern, text):
        if match.start() > last_pos:
            temp_segments.append(Segment('text', text[last_pos:match.start()]))
        
        is_animated = match.group(1) == 'a'
        name = match.group(2)
        emoji_id = match.group(3)
        ext = 'gif' if is_animated else 'png'
        url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
        
        temp_segments.append(Segment('emoji', name, url))
        last_pos = match.end()
    
    if last_pos < len(text):
        temp_segments.append(Segment('text', text[last_pos:]))
    
    # Now process text segments for Unicode Emojis
    final_segments = []
    for seg in temp_segments:
        if seg.type == 'text':
             last_idx = 0
             # emoji.analyze yields Token objects
             for match in emoji.analyze(seg.content, non_emoji=False):
                 start = match.value.start
                 end = match.value.end
                 char = match.chars
                 
                 if start > last_idx:
                     final_segments.append(Segment('text', seg.content[last_idx:start]))
                 
                 final_segments.append(Segment('unicode_emoji', char))
                 last_idx = end
             
             if last_idx < len(seg.content):
                 final_segments.append(Segment('text', seg.content[last_idx:]))
        else:
             final_segments.append(seg)
    
    return final_segments

def get_text_size(text, font):
    # 使用 getlength 來獲取正確的寬度（包含空格）
    try:
        width = font.getlength(text)
    except AttributeError:
        # 舊版 Pillow 備用方案
        bbox = font.getmask(text).getbbox()
        width = bbox[2] - bbox[0] if bbox else 0
    
    # 高度使用 getbbox 或 getmetrics
    bbox = font.getmask(text).getbbox()
    if bbox:
        height = bbox[3] - bbox[1]
    else:
        # 對於純空格，使用字體的 metrics
        ascent, descent = font.getmetrics()
        height = ascent + descent
    
    return int(width), height

def get_twemoji_url(emoji_char):
    """將 Unicode emoji 轉換為 Twemoji CDN URL"""
    # 將 emoji 轉換為 codepoints
    codepoints = []
    for char in emoji_char:
        cp = ord(char)
        # 跳過變體選擇器 (FE0E, FE0F)
        if cp not in (0xFE0E, 0xFE0F):
            codepoints.append(f"{cp:x}")
    
    filename = "-".join(codepoints)
    return f"https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/{filename}.png"

async def load_emojis(segments, size):
    async with aiohttp.ClientSession() as session:
        for seg in segments:
            if seg.type == 'emoji' and seg.url:
                try:
                    async with session.get(seg.url) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            img = Image.open(io.BytesIO(data)).convert("RGBA")
                            img = img.resize((size, size), Image.Resampling.LANCZOS)
                            seg.image = img
                            seg.width = size
                            seg.height = size
                except Exception as e:
                    print(f"Failed to load emoji {seg.url}: {e}")
                    # Fallback to text representation
                    seg.type = 'text'
                    seg.content = f":{seg.content}:"
            
            # 處理 Unicode emoji - 從 Twemoji 下載圖片
            elif seg.type == 'unicode_emoji':
                url = get_twemoji_url(seg.content)
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            img = Image.open(io.BytesIO(data)).convert("RGBA")
                            img = img.resize((size, size), Image.Resampling.LANCZOS)
                            seg.image = img
                            seg.width = size
                            seg.height = size
                        else:
                            # 如果下載失敗，保留為文字
                            print(f"Failed to load unicode emoji {seg.content}, status: {resp.status}")
                except Exception as e:
                    print(f"Failed to load unicode emoji {seg.content}: {e}")

def layout_segments(segments, font, max_width, emoji_size, emoji_font=None):
    lines = []
    current_line = []
    current_width = 0
    
    space_width, _ = get_text_size(" ", font) # Measure space once

    for seg in segments:
        if seg.type == 'emoji':
             seg.width = emoji_size
             seg.height = emoji_size
        elif seg.type == 'unicode_emoji':
             # Unicode emoji 將會被下載為圖片，使用與自定義 emoji 相同的尺寸
             seg.width = emoji_size
             seg.height = emoji_size
        else: # text
             pass # width calculated below

        if seg.type == 'text':
            # Split text logic
            words = re.split(r'(\s+)', seg.content) 
            for word in words:
                word_w, word_h = get_text_size(word, font)
                if current_width + word_w > max_width and current_line:
                     lines.append(current_line)
                     current_line = []
                     current_width = 0
                
                word_seg = Segment('text', word)
                word_seg.width = word_w
                word_seg.height = word_h
                current_line.append(word_seg)
                current_width += word_w
        else:
             # Emoji or Unicode Emoji
             if current_width + seg.width > max_width and current_line:
                 lines.append(current_line)
                 current_line = []
                 current_width = 0
             current_line.append(seg)
             current_width += seg.width
                
    if current_line:
        lines.append(current_line)
    return lines

async def create(message: discord.Message):
    name = message.author.display_name
    avatar_url = message.author.display_avatar.url if message.author.display_avatar else None
    content = message.content.strip()
    
    # Resolve mentions
    content = resolve_mentions(content, message)

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

    # Area constraints
    x_min, x_max = 560, 1150
    y_min, y_max = 50, 580
    max_w = x_max - x_min
    max_h = y_max - y_min

    # Font handling with auto-shrink
    font_size = 55
    min_font_size = 20
    final_lines = []
    final_font = None
    final_emoji_font = None

    base_segments = await prepare_segments(content, None) 
    
    while font_size >= min_font_size:
        font_msg = ImageFont.truetype(os.path.join(fontdir, "notobold.ttf"), font_size)
        try:
            # Try to load Twemoji font at the same size
            # Note: Some color fonts require specific sizes or standard sizes.
            # We'll assume it scales like a TTF.
            emoji_font_obj = ImageFont.truetype(os.path.join(fontdir, "twemoji.ttf"), font_size)
        except Exception:
            # Fallback if not found or failed, use main font (might show boxes)
            emoji_font_obj = font_msg

        ascent, descent = font_msg.getmetrics()
        line_height = ascent + descent + 10 # spacing
        emoji_size = ascent + descent
        
        lines = layout_segments(base_segments, font_msg, max_w, emoji_size, emoji_font=emoji_font_obj)
        total_height = len(lines) * line_height
        
        if total_height <= max_h or font_size == min_font_size:
            final_lines = lines
            final_font = font_msg
            final_emoji_font = emoji_font_obj
            break
        
        font_size -= 2 # Decrease font size

    # Load custom emojis
    ascent, descent = final_font.getmetrics()
    emoji_size = ascent + descent
    await load_emojis(base_segments, emoji_size)

    # Calculate vertical position
    total_text_height = len(final_lines) * (emoji_size + 10) 
    
    font_name = ImageFont.truetype(os.path.join(fontdir, "notolight.ttf"), 25)
    name_w, name_h = get_text_size(f" - {name}", font_name)
    
    total_content_height = total_text_height + 20 + name_h
    start_y = y_min + (max_h - total_content_height) // 2
    
    current_y = start_y
    center_x = (x_min + x_max) // 2

    # Draw Text and Emojis
    for line in final_lines:
        line_width = 0
        for seg in line:
            line_width += seg.width
        
        start_x = center_x - line_width // 2
        cursor_x = start_x
        
        for seg in line:
            if seg.type == 'emoji' and seg.image:
                img.paste(seg.image, (int(cursor_x), int(current_y)), seg.image)
            elif seg.type == 'unicode_emoji':
                # 使用下載的圖片繪製 Unicode emoji
                if seg.image:
                    img.paste(seg.image, (int(cursor_x), int(current_y)), seg.image)
                else:
                    # 如果圖片載入失敗，fallback 用文字繪製
                    draw.text((cursor_x, current_y), seg.content, font=final_font, fill="white")
            else:
                draw.text((cursor_x, current_y), seg.content, font=final_font, fill="white")
            
            cursor_x += seg.width
        
        current_y += emoji_size + 10

    # Draw Name
    name_y = current_y + 20
    display_name = f" - {name}"
    name_x = center_x - name_w // 2

    # draw.text((name_x, name_y), display_name, font=font_name, fill="white")

    # 建立透明文字圖層 (Reflect effect)
    text_img = Image.new("RGBA", (int(name_w) + 20, int(name_h) + 20), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_img)
    text_draw.text((0, 0), display_name, font=font_name, fill="white")

    # 仿斜體：X 軸傾斜（負值向左斜，正值向右斜）
    sheared = text_img.transform(
        text_img.size,
        Image.AFFINE,
        (1, 0.2, 0, 0, 1, 0),  # X 傾斜 0.3 的效果
        resample=Image.BICUBIC,
    )
    img.paste(sheared, (name_x, int(name_y)), sheared)

    # save to bytes
    output_buffer = io.BytesIO()
    img.save(output_buffer, format="PNG")
    output_buffer.seek(0)
    return output_buffer


class UpvoteView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.upvotes = 0
        self.on_board_message = None  # 用於追蹤已經被放上看板的訊息
        self.on_guild_board_message = None  # 用於追蹤已經被放上公會看板的訊息
        self.upvoted_users = set()  # 用於追蹤已經點過讚的用戶
        self._lock = asyncio.Lock()  # 序列化按鈕點擊以避免競態條件

    @discord.ui.button(emoji="⬆️", style=discord.ButtonStyle.green)
    async def upvote(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self._lock:
            if interaction.user.id in self.upvoted_users:
                await interaction.response.send_message("你已經點過了！", ephemeral=True)
                return
            self.upvotes += 1
            self.upvoted_users.add(interaction.user.id)
            button.label = f" | {self.upvotes} 人"
            if self.upvotes >= 5:
                channel = bot.get_channel(config("upvote_board_channel_id"))
                message = interaction.message
                image = message.attachments[0] if message.attachments else None
                if channel and image:
                    if self.on_board_message is None:  # 確保同一則訊息不會被重複放上看板
                        sent_message = await channel.send(content=f"⬆️ | {self.upvotes} 人", file=await image.to_file())
                        self.on_board_message = sent_message
                    else:
                        # 如果已經在看板上了，更新看板訊息的內容
                        try:
                            await self.on_board_message.edit(content=f"⬆️ | {self.upvotes} 人")
                        except Exception as e:
                            log(f"更新看板訊息失敗: {e}", module_name="MessageImage", level=logging.ERROR)
                guild_channel_id = get_server_config(interaction.guild.id, "upvote_board_channel_id")
                if guild_channel_id:
                    guild_channel = bot.get_channel(guild_channel_id)
                    if guild_channel and image:
                        if self.on_guild_board_message is None:  # 確保同一則訊息不會被重複放上公會看板
                            sent_message = await guild_channel.send(content=f"⬆️ | {self.upvotes} 人", file=await image.to_file())
                            self.on_guild_board_message = sent_message
                        else:
                            # 如果已經在公會看板上了，更新公會看板訊息的內容
                            try:
                                await self.on_guild_board_message.edit(content=f"⬆️ | {self.upvotes} 人")
                            except Exception as e:
                                log(f"更新公會看板訊息失敗: {e}", module_name="MessageImage", level=logging.ERROR)
            await interaction.response.edit_message(view=self)


@bot.tree.command(name="upvoteboard", description="設定有料板子，當超過 5 個人點擊將會傳送在該頻道。")
@app_commands.describe(channel="要設置的頻道（若未設置則清除設定）")
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.default_permissions(manage_guild=True)
async def set_upvoteboard(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if channel:
        set_server_config(interaction.guild.id, "upvote_board_channel_id", channel.id)
        await interaction.response.send_message(f"已設定有料板子頻道為 {channel.mention}")
    else:
        set_server_config(interaction.guild.id, "upvote_board_channel_id", None)
        await interaction.response.send_message("已清除有料板子頻道設定")


@bot.tree.context_menu(name="糟糕的Make it a Quote")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def make_it_a_quote(interaction: discord.Interaction, message: discord.Message):
    if not message.content or message.content.strip() == "":
        await interaction.response.send_message("錯誤：訊息沒有內容。", ephemeral=True)
        return
    await interaction.response.defer()
    output_buffer = await create(message)
    await interaction.followup.send(file=discord.File(output_buffer, filename="messenger_quote.png"), view=UpvoteView())

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
    await ctx.reply(file=discord.File(output_buffer, filename="messenger_quote.png"), view=UpvoteView())


async def screenshot(message: discord.Message):
    # check browser alive
    global browser
    if browser is None:
        raise Exception("瀏覽器尚未啟動，請稍後再試。")
    if not browser.is_connected():
        raise Exception("瀏覽器已關閉，請稍後再試。")
    
    # make a stopwatch for debugging
    times = {"getting_messages": 0, "generating_html": 0, "taking_screenshot": 0}
    start_time = time.perf_counter()

    # try to get previous message (group consecutive messages from same author)
    messages = [message]
    message_time = message.created_at
    try:
        # Logic to find previous messages from the same author within reason
        # This logic was slightly different in the two commands, unifying to the more robust history check
        # However, the provided snippet for `screenshot_cmd` had a weird while loop with `current_msg.next()` which isn't standard discord.py async iterator usage.
        # The `screenshot_generator` logic using `async for` is cleaner. Let's use that logic but adapted for a helper.
        
        # Note: The original context menu code appended to `messages` then seemingly relied on `chat_exporter` handling order or `messages` being in reverse order?
        # `chat_exporter` usually expects messages in chronological order.
        # The context menu code: `messages.append(msg)` inside `history(oldest_first=False)` means `messages` is [target, target-1, target-2].
        # Then it passes this list to chat_exporter.

        async for msg in message.channel.history(limit=10, before=message.created_at, oldest_first=False):
            if msg.author.id == message.author.id and (message_time - msg.created_at).total_seconds() < 300:  # 5 minutes threshold
                messages.append(msg)
            else:
                break
    except Exception:
        # traceback.print_exc()
        pass
    times["getting_messages"] = time.perf_counter() - start_time
    start_time = time.perf_counter()
    
    # chat_exporter expects chronological order usually, so reverse to [target-2, target-1, target]
    # messages.reverse()

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
    times["generating_html"] = time.perf_counter() - start_time
    start_time = time.perf_counter()

    try:
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        await page.set_content(html_content, wait_until="load")
        # Force width to fit content so the screenshot isn't full width
        await page.add_style_tag(content=".chatlog__message-group { width: fit-content; } .chatlog { padding: 0px 1rem 0px 0px !important; border-top: unset !important; width: fit-content; }")
        # Remove spoiler hidden class
        await page.evaluate("() => { document.querySelectorAll('.spoiler--hidden').forEach(el => el.classList.remove('spoiler--hidden')); }")
        # Resize viewport to fit the actual content size so nothing gets clipped
        chatlog = page.locator('.chatlog')
        bounding_box = await chatlog.bounding_box()
        if bounding_box:
            new_width = max(int(bounding_box['width'] + bounding_box['x']) + 50, 800)
            new_height = max(int(bounding_box['height'] + bounding_box['y']) + 50, 600)
            await page.set_viewport_size({"width": new_width, "height": new_height})
        # Locate the message group container
        image_bytes = await chatlog.screenshot(type="png")
        await page.close()
    except Exception as e:
        log(f"截圖失敗: {e}", module_name="MessageImage", level=logging.ERROR)
        # traceback.print_exc()
        # If screenshot fails, maybe we want to return the HTML for debugging? 
        # The original code did this in one place. For simplicity in a shared function, let's just raise.
        raise Exception(f"截圖失敗: {e}")
    times["taking_screenshot"] = time.perf_counter() - start_time
    # log the times for debugging
    log(f"截圖生成成功: 取得訊息時間={times['getting_messages']*1000:.2f}ms, 生成HTML時間={times['generating_html']*1000:.2f}ms, 截圖時間={times['taking_screenshot']*1000:.2f}ms", module_name="MessageImage")

    return io.BytesIO(image_bytes)


@bot.tree.context_menu(name="截圖生成器")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def screenshot_generator(interaction: discord.Interaction, message: discord.Message):
    await interaction.response.defer()
    try:
        buffer = await screenshot(message)
        await interaction.followup.send(file=discord.File(buffer, filename="screenshot.png"), view=UpvoteView())
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
        await ctx.reply(file=discord.File(buffer, filename="screenshot.png"), view=UpvoteView())
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
        # msg = f"現正開放投稿！\n-# {await get_command_mention('contribute', 'what-is-this-guy-talking-about')}"
        await interaction.followup.send(file=discord.File(buffer, filename="whatisthisguytalking.png"), view=UpvoteView())
        log("引用圖片生成完成", module_name="MessageImage", user=interaction.user, guild=interaction.guild)
    except Exception as e:
        await interaction.followup.send(f"引用圖片生成失敗: {e}", ephemeral=True)

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
        count    = 0
        for file in os.listdir(dir):
            if file.endswith(".png") or file.endswith(".jpg") or file.endswith(".jpeg") or file.endswith(".gif") or file.endswith(".webp"):
                whatisthisguytalking_images.append(os.path.join(dir, file))
                count += 1
        log(f"載入了 {count} 張「這傢伙在說什麼呢？」的圖片", module_name="MessageImage")
        return count
    except Exception as e:
        log(f"載入「這傢伙在說什麼呢？」的圖片失敗: {e}", module_name="MessageImage", level=logging.ERROR)
        return 0

on_ready_tasks.append(load_whatisthisguytalking_images)

@bot.command()
@OwnerTools.is_owner()
async def reload_whatisthisguytalking_images(ctx: commands.Context):
    count = await load_whatisthisguytalking_images()
    if count == 0:
        await ctx.reply("重新載入「這傢伙在說什麼呢？」的圖片失敗")
    else:
        await ctx.reply(f"重新載入「這傢伙在說什麼呢？」的圖片完成，載入了 {count} 張圖片")


if __name__ == "__main__":
    start_bot()