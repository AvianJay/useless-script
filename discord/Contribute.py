import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, get_user_data, set_user_data, config, modules
from datetime import datetime, timezone
if "Website" not in modules:
    raise Exception("依賴模組 Website 未加載，無法加載 Contribute 模組。")
from Website import app
from flask import request, redirect, render_template
import requests
from logger import log
import logging
import time
import base64
import uuid
import json
import os
import io
from io import BytesIO
from urllib.parse import urlencode
import asyncio
import traceback
from Economy import log_transaction, GLOBAL_CURRENCY_NAME
from PIL import Image, ImageDraw, ImageFont

def oauth_code_to_id(code, redirect_uri=None):
    url = 'https://discord.com/api/oauth2/token'
    data = {
        'client_id': bot.application.id,
        'client_secret': config("client_secret"),
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri or config("webverify_url"),  # Replace with your redirect URI
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    try:
        response = requests.post(url, data=data, headers=headers, timeout=10)
        response.raise_for_status()
        token_info = response.json()
        access_token = token_info.get('access_token')

        if not access_token:
            return None

        user_response = requests.get(
            'https://discord.com/api/users/@me',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )
        user_response.raise_for_status()
        user_info = user_response.json()
        return user_info.get('id')
    except requests.RequestException as e:
        log(f"OAuth code exchange error: {e}", module_name="Contribute", level=logging.ERROR)
        return None

auth_tokens = {}
contribution_cooldowns = {} # user_id: timestamp
GLOBAL_GUILD_ID = 0
APPROVAL_REWARD_GLOBAL = 200

def cleanup_tokens():
    current_time = time.time()
    expired_tokens = [token for token, data in auth_tokens.items() if current_time - data['timestamp'] > 600] # 10 minutes
    for token in expired_tokens:
        del auth_tokens[token]

def grant_approval_global_reward_once(user_id: int, message_id: int, ctype: str):
    reward_records = get_user_data(GLOBAL_GUILD_ID, user_id, "contribution_approval_rewards", {})
    record_key = str(message_id)
    if record_key in reward_records:
        return False, get_user_data(GLOBAL_GUILD_ID, user_id, "economy_balance", 0.0)

    current_balance = float(get_user_data(GLOBAL_GUILD_ID, user_id, "economy_balance", 0.0) or 0.0)
    new_balance = round(current_balance + APPROVAL_REWARD_GLOBAL, 2)
    set_user_data(GLOBAL_GUILD_ID, user_id, "economy_balance", new_balance)

    reward_records[record_key] = {
        "reward": APPROVAL_REWARD_GLOBAL,
        "type": ctype,
        "time": datetime.now(timezone.utc).isoformat()
    }
    set_user_data(GLOBAL_GUILD_ID, user_id, "contribution_approval_rewards", reward_records)

    log_transaction(
        GLOBAL_GUILD_ID,
        user_id,
        "投稿審核獎勵",
        APPROVAL_REWARD_GLOBAL,
        GLOBAL_CURRENCY_NAME,
        f"投稿類型：{ctype}"
    )
    return True, new_balance

def generate_feedgrass_preview(image_bytes: bytes, json_data: dict) -> BytesIO:
    """在背景圖上繪製標示圓圈來預覽 target/feeder/extras 的位置"""
    image = Image.open(BytesIO(image_bytes)).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 嘗試載入字型
    try:
        font = ImageFont.truetype("arial.ttf", 16)
        font_small = ImageFont.truetype("arial.ttf", 12)
    except (IOError, OSError):
        font = ImageFont.load_default()
        font_small = font

    def draw_circle_label(pos, size, color, label):
        x, y = pos
        w, h = size
        cx, cy = x + w // 2, y + h // 2
        r = min(w, h) // 2
        # 半透明填充圓
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*color, 80), outline=(*color, 220), width=3)
        # 標籤文字
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        # 文字背景
        draw.rectangle((cx - tw // 2 - 4, cy - th // 2 - 2, cx + tw // 2 + 4, cy + th // 2 + 2), fill=(0, 0, 0, 160))
        draw.text((cx - tw // 2, cy - th // 2), label, fill=(255, 255, 255, 255), font=font)

    # 繪製 Target (被草飼人) - 綠色
    if "target" in json_data:
        t = json_data["target"]
        draw_circle_label(t["position"], t["size"], (0, 200, 0), "TARGET")

    # 繪製 Feeder (草飼人) - 藍色
    if "feeder" in json_data and not json_data.get("self", False):
        f = json_data["feeder"]
        draw_circle_label(f["position"], f["size"], (0, 100, 255), "FEEDER")

    # 繪製 Extras (旁觀者) - 黃色
    if "extras" in json_data:
        for i, extra in enumerate(json_data["extras"]):
            draw_circle_label(extra["position"], extra["size"], (255, 200, 0), f"EXTRA #{i+1}")

    # 如果是自己草飼自己，標示 self
    if json_data.get("self", False):
        draw.text((10, 10), "[SELF MODE]", fill=(255, 100, 100, 255), font=font)

    image = Image.alpha_composite(image, overlay)
    byte_io = BytesIO()
    image.save(byte_io, "PNG")
    byte_io.seek(0)
    return byte_io


class EditJsonModal(discord.ui.Modal, title="編輯 JSON 設定"):
    json_content = discord.ui.TextInput(
        label="JSON 內容",
        style=discord.TextStyle.paragraph,
        placeholder='{"target": {"position": [x, y], "size": [w, h]}, ...}',
        required=True,
        max_length=4000
    )

    def __init__(self, message: discord.Message):
        super().__init__()
        self.target_message = message
        # 從附件取得目前的 JSON 內容
        # 這會在按鈕回調中被設定
        self.original_json = ""

    async def on_submit(self, interaction: discord.Interaction):
        try:
            new_json = json.loads(self.json_content.value)
        except json.JSONDecodeError as e:
            await interaction.response.send_message(f"JSON 格式錯誤：{e}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 驗證必要欄位
        if "target" not in new_json or "position" not in new_json["target"] or "size" not in new_json["target"]:
            await interaction.followup.send("JSON 必須包含 target.position 和 target.size", ephemeral=True)
            return

        if not new_json.get("self", False):
            if "feeder" not in new_json or "position" not in new_json["feeder"] or "size" not in new_json["feeder"]:
                await interaction.followup.send("非 self 模式下 JSON 必須包含 feeder.position 和 feeder.size", ephemeral=True)
                return

        try:
            # 找到 JSON 附件並取得檔名
            json_att = None
            img_att = None
            for att in self.target_message.attachments:
                if att.filename.endswith(".json"):
                    json_att = att
                elif att.filename.endswith(".png") and att.filename != "preview.png":
                    img_att = att

            if not json_att:
                await interaction.followup.send("找不到 JSON 附件", ephemeral=True)
                return

            json_filename = json_att.filename

            # 保留原始的 file 欄位
            new_json["file"] = json_filename.replace(".json", ".png")

            # 建立新的 JSON File
            json_bytes = json.dumps(new_json, indent=4, ensure_ascii=False).encode("utf-8")
            new_json_file = discord.File(BytesIO(json_bytes), filename=json_filename)

            # 重新生成預覽圖
            files_to_send = [new_json_file]
            if img_att:
                img_data = await img_att.read()
                preview_bytes = generate_feedgrass_preview(img_data, new_json)
                preview_file = discord.File(preview_bytes, filename="preview.png")
                files_to_send.append(preview_file)
                # 同時重新附加原圖
                files_to_send.insert(0, discord.File(BytesIO(img_data), filename=img_att.filename))

            # 更新 embed 預覽圖
            embed = self.target_message.embeds[0]
            embed.set_image(url="attachment://preview.png")

            # 更新 NSFW 欄位
            for i, field in enumerate(embed.fields):
                if field.name == "NSFW":
                    embed.set_field_at(i, name="NSFW", value=str(new_json.get("nsfw", False)))
                    break

            await self.target_message.edit(embed=embed, attachments=files_to_send)
            await interaction.followup.send("JSON 已更新！預覽已重新生成。", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"更新失敗：{e}", ephemeral=True)
            traceback.print_exc()


class ContributionView(discord.ui.View):
    def __init__(self, ctype, audio_filename=None):
        super().__init__(timeout=None)
        self.ctype = ctype
        self.audio_filename = audio_filename  # 用於 dynamic_voice_audio
        # 只有 feedgrass 類型才加上編輯 JSON 按鈕
        if ctype == "feedgrass":
            edit_btn = discord.ui.Button(
                label="編輯 JSON",
                style=discord.ButtonStyle.grey,
                custom_id="contribution_edit_json",
                emoji="📝"
            )
            edit_btn.callback = self.edit_json
            self.add_item(edit_btn)

    @discord.ui.button(label="同意", style=discord.ButtonStyle.green, custom_id="contribution_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            if self.ctype == "feedgrass":
                # Find image and JSON attachments by extension
                img_att = None
                json_att = None
                for att in interaction.message.attachments:
                    if att.filename.endswith(".json"):
                        json_att = att
                    elif att.filename.endswith(".png") and att.filename != "preview.png":
                        img_att = att
                
                if not img_att or not json_att:
                    await interaction.followup.send("錯誤：找不到附件。", ephemeral=True)
                    return
                
                # Verify dirs
                if not os.path.exists("dsize-feedgrass-images"):
                    os.makedirs("dsize-feedgrass-images")
                
                # Get filenames from attachments
                img_filename = img_att.filename
                json_filename = json_att.filename
                
                # Download
                await img_att.save(os.path.join("dsize-feedgrass-images", img_filename))
                # Download JSON and update file path inside? 
                # actually the JSON we constructed has "file": "UUID.png" which matches the img_filename we set.
                # So we just save it.
                await json_att.save(os.path.join("dsize-feedgrass-images", json_filename))
                
                await interaction.followup.send("已保存並批准投稿！", ephemeral=True)
                
                # Update message
                embed = interaction.message.embeds[0]
                embed.color = discord.Color.green()
                embed.title += " [已批准]"
                # disable buttons
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(embed=embed, view=self)

                # Try reload
                try:
                    from dsize import load_feedgrass_images
                    count = load_feedgrass_images()
                    await interaction.followup.send(f"已重新載入 {count} 張草飼圖片。", ephemeral=True)
                except ImportError:
                    pass

                # try to send dm
                user_id = interaction.message.embeds[0].fields[0].value
                user_id_int = int(user_id)
                rewarded, new_global_balance = grant_approval_global_reward_once(
                    user_id=user_id_int,
                    message_id=interaction.message.id,
                    ctype=self.ctype
                )

                user = await bot.fetch_user(user_id_int)
                if rewarded:
                    await user.send(
                        f"你的投稿已被批准！你獲得了 **{APPROVAL_REWARD_GLOBAL}** 全域幣獎勵。\n"
                        f"目前全域幣餘額：**{new_global_balance:,.2f}**"
                    )
                else:
                    await user.send("你的投稿已被批准！")

            elif self.ctype == "whatisthisguytalking":
                # Attachment 0: Image
                embed = interaction.message.embeds[0]
                if not embed.image:
                    await interaction.followup.send("錯誤：找不到附件。", ephemeral=True)
                    return
                
                img_att = embed.image.url
                
                if not os.path.exists("whatisthisguytalking-images"):
                    os.makedirs("whatisthisguytalking-images")
                
                path = os.path.join("whatisthisguytalking-images", uuid.uuid4().hex + ".png")
                # download
                response = requests.get(img_att)
                with open(path, "wb") as f:
                    f.write(response.content)
                await interaction.followup.send("已保存並批准投稿！", ephemeral=True)
                
                # Update message
                embed = interaction.message.embeds[0]
                embed.color = discord.Color.green()
                embed.title += " [已批准]"
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(embed=embed, view=self, attachments=[])

                try:
                    from MessageImage import load_whatisthisguytalking_images
                    count = await load_whatisthisguytalking_images()
                    await interaction.followup.send(f"已重新載入 {count} 張圖片。", ephemeral=True)
                except Exception as e:
                    await interaction.followup.send(f"重新載入失敗: {e}", ephemeral=True)
                
                # try to send dm
                user_id = interaction.message.embeds[0].fields[0].value
                user_id_int = int(user_id)
                rewarded, new_global_balance = grant_approval_global_reward_once(
                    user_id=user_id_int,
                    message_id=interaction.message.id,
                    ctype=self.ctype
                )

                user = await bot.fetch_user(user_id_int)
                if rewarded:
                    await user.send(
                        f"你的投稿已被批准！你獲得了 **{APPROVAL_REWARD_GLOBAL}** 全域幣獎勵。\n"
                        f"目前全域幣餘額：**{new_global_balance:,.2f}**"
                    )
                else:
                    await user.send("你的投稿已被批准！")

            elif self.ctype == "dynamic_voice_audio":
                # Attachment 0: Audio file
                if len(interaction.message.attachments) < 1:
                    await interaction.followup.send("錯誤：找不到音檔附件。", ephemeral=True)
                    return
                
                audio_att = interaction.message.attachments[0]
                audio_data = await audio_att.read()
                
                # 保存音檔到 assets/dynamic_voice_audio 資料夾
                audio_folder = os.path.join(os.path.dirname(__file__), "assets", "dynamic_voice_audio")
                os.makedirs(audio_folder, exist_ok=True)
                audio_path = os.path.join(audio_folder, self.audio_filename)
                with open(audio_path, "wb") as f:
                    f.write(audio_data)
                
                await interaction.followup.send(f"已保存並批准音檔投稿！檔名：{self.audio_filename}", ephemeral=True)
                
                # Update message
                embed = interaction.message.embeds[0]
                embed.color = discord.Color.green()
                embed.title += " [已批准]"
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(embed=embed, view=self)
                
                log(f"音檔已保存為：{self.audio_filename}", module_name="Contribute")
                
                # try to send dm
                user_id = interaction.message.embeds[0].fields[0].value
                user_id_int = int(user_id)
                rewarded, new_global_balance = grant_approval_global_reward_once(
                    user_id=user_id_int,
                    message_id=interaction.message.id,
                    ctype=self.ctype
                )

                user = await bot.fetch_user(user_id_int)
                if rewarded:
                    await user.send(
                        f"你投稿的動態語音音效已被批准！你獲得了 **{APPROVAL_REWARD_GLOBAL}** 全域幣獎勵。\n"
                        f"目前全域幣餘額：**{new_global_balance:,.2f}**"
                    )
                else:
                    await user.send("你投稿的動態語音音效已被批准！")

        except Exception as e:
            await interaction.followup.send(f"批准失敗: {e}", ephemeral=True)
            traceback.print_exc()
            log(f"Contribution Approve Error: {e}", module_name="Contribute", level=logging.ERROR)

    async def edit_json(self, interaction: discord.Interaction):
        """開啟編輯 JSON 的 Modal"""
        try:
            # 找到 JSON 附件
            json_att = None
            for att in interaction.message.attachments:
                if att.filename.endswith(".json"):
                    json_att = att
                    break

            if not json_att:
                await interaction.response.send_message("找不到 JSON 附件", ephemeral=True)
                return

            # 讀取目前 JSON 內容
            json_data = await json_att.read()
            json_str = json_data.decode("utf-8")

            # 建立 Modal
            modal = EditJsonModal(interaction.message)
            modal.json_content.default = json_str
            await interaction.response.send_modal(modal)

        except Exception as e:
            await interaction.response.send_message(f"開啟編輯器失敗：{e}", ephemeral=True)
            traceback.print_exc()

    @discord.ui.button(label="拒絕", style=discord.ButtonStyle.red, custom_id="contribution_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.title += " [已拒絕]"
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self, attachments=[])

@app.route("/contribute-feed-grass", methods=["GET", "POST"])
def contribute_feed_grass():
    cleanup_tokens()
    if request.method == "GET":
        if request.args.get("code"):
            code = request.args.get("code")
            redirect_uri = config('website_url') + "/contribute-feed-grass"
            user_id = oauth_code_to_id(code, redirect_uri)
            if not user_id:
                return "驗證失敗，請重試。"
            # generate a temporary token
            temp_token = f"token_{int(time.time())}_{user_id}"
            auth_tokens[temp_token] = {"user_id": user_id, "timestamp": time.time()}
            return redirect(f"/contribute-feed-grass?token={temp_token}")
        elif request.args.get("token"):
            token = request.args.get("token")
            if token in auth_tokens:
                user_id = auth_tokens[token]["user_id"]
                return render_template("contribute_feed_grass.html", bot=bot, user_id=user_id, gtag=config("website_gtag"))
            else:
                return "無效或過期的驗證令牌，請重試。"
        else:
             # Redirect to OAuth
             redirect_uri = config('website_url') + "/contribute-feed-grass"
             oauth_url = f"https://discord.com/oauth2/authorize?client_id={bot.application.id}&response_type=code&scope=identify&prompt=none&{urlencode({'redirect_uri': redirect_uri})}"
             return redirect(oauth_url)
    elif request.method == "POST":
        data = request.json
        token = data.get("token")
        if not token or token not in auth_tokens:
             return "無效的令牌", 401
        
        user_id = auth_tokens[token]["user_id"]

        # Rate Limit Check
        current_time = time.time()
        if user_id in contribution_cooldowns:
            last_time = contribution_cooldowns[user_id]
            if current_time - last_time < 300: # 5 minutes
                remaining = int(300 - (current_time - last_time))
                return f"投稿過於頻繁，請等待 {remaining} 秒後再試。", 429
        
        # Prepare Data
        try:
            file_data = base64.b64decode(data["file"])
            # Generate UUID
            file_uuid = str(uuid.uuid4())
            img_filename = f"{file_uuid}.png"
            json_filename = f"{file_uuid}.json"
            
            # Update JSON data structure
            # The 'file' field in json should point to the png filename
            json_payload = data.copy()
            json_payload["file"] = img_filename
            del json_payload["token"] # remove token
            
            # Send to Discord
            contribute_channel_id = config("contribute_channel_id", None)
            if not contribute_channel_id:
                 return "投稿頻道未設置", 500
            
            channel = bot.get_channel(int(contribute_channel_id))
            if not channel:
                return "無法找到投稿頻道", 500

            async def send_contribution():
                user = await bot.fetch_user(user_id)
                embed = discord.Embed(title="新的「草飼圖」投稿", color=discord.Color.blue())
                embed.set_author(name=f"{user.name} ({user.id})", icon_url=user.display_avatar.url)
                embed.add_field(name="使用者 ID", value=user.id)
                embed.add_field(name="NSFW", value=str(json_payload.get("nsfw", False)))
                embed.timestamp = datetime.now(timezone.utc)
                
                # Generate preview image with position markers
                preview_bytes = generate_feedgrass_preview(file_data, json_payload)
                preview_file = discord.File(preview_bytes, filename="preview.png")
                embed.set_image(url="attachment://preview.png")
                
                # Create files
                img_file = discord.File(io.BytesIO(file_data), filename=img_filename)
                
                json_bytes = json.dumps(json_payload, indent=4, ensure_ascii=False).encode('utf-8')
                json_file = discord.File(io.BytesIO(json_bytes), filename=json_filename)
                
                view = ContributionView("feedgrass")
                await channel.send(embed=embed, files=[img_file, json_file, preview_file], view=view)

            bot.loop.create_task(send_contribution())
            contribution_cooldowns[user_id] = time.time()
            return "投稿已送出！"

        except Exception as e:
            log(f"Contribute Error: {e}", module_name="Contribute", level=logging.ERROR)
            return f"發生錯誤: {e}", 500
        

@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class Contribute(commands.GroupCog, description="投稿圖片"):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="feedgrass", description="投稿 dsize 的草飼圖")
    async def contribute_feed_grass(self, interaction: discord.Interaction):
        redirect_uri = config('website_url') + "/contribute-feed-grass"
        url = f"https://discord.com/oauth2/authorize?client_id={self.bot.application.id}&response_type=code&scope=identify&prompt=none&{urlencode({'redirect_uri': redirect_uri})}"
        embed = discord.Embed(title="草飼圖投稿", description="請點擊以下連結進行投稿", color=discord.Color.blue())
        link_btn = discord.ui.Button(label="前往投稿", url=url, emoji="🔗")
        view = discord.ui.View()
        view.add_item(link_btn)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @app_commands.command(name="what-is-this-guy-talking-about", description="投稿「這傢伙在說什麼呢」圖片")
    async def what_is_this_guy_talking_about(self, interaction: discord.Interaction, image: discord.Attachment):
        # Rate Limit Check
        current_time = time.time()
        user_id = interaction.user.id
        if user_id in contribution_cooldowns:
            last_time = contribution_cooldowns[user_id]
            if current_time - last_time < 300:
                remaining = int(300 - (current_time - last_time))
                await interaction.response.send_message(f"投稿過於頻繁，請等待 {remaining} 秒後再試。", ephemeral=True)
                return

        if not image.content_type or not image.content_type.startswith("image/"):
            await interaction.response.send_message("請上傳一個圖片檔案。", ephemeral=True)
            return
        contribute_channel_id = config("contribute_channel_id", None)
        if contribute_channel_id is None:
            await interaction.response.send_message("投稿頻道未設置，請聯繫開發者。", ephemeral=True)
            return
        contribute_channel = self.bot.get_channel(int(contribute_channel_id))
        if contribute_channel is None:
            await interaction.response.send_message("無法找到投稿頻道，請聯繫開發者。", ephemeral=True)
            return
        embed = discord.Embed(title="新的「這傢伙在說什麼呢」圖片投稿", color=discord.Color.green())
        embed.set_author(name=f"{interaction.user.name} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
        embed.timestamp = datetime.now(timezone.utc)
        embed.add_field(name="使用者 ID", value=interaction.user.id)
        
        # We need to re-upload the file to the channel? Or just use the url?
        # Using url is fine for display, but for "Approval" we need to download it.
        # It's better to re-upload it as a file so it persists in the channel even if original is deleted, 
        # and so we can easily grab it in the View.
        
        # Download first
        img_data = await image.read()
        file_ext = image.filename.split('.')[-1]
        file_uuid = str(uuid.uuid4())
        new_filename = f"{file_uuid}.{file_ext}"
        
        file = discord.File(io.BytesIO(img_data), filename=new_filename)
        embed.set_image(url=f"attachment://{new_filename}")
        
        view = ContributionView("whatisthisguytalking")
        await contribute_channel.send(embed=embed, file=file, view=view)
        contribution_cooldowns[user_id] = time.time()
        await interaction.response.send_message("感謝您的投稿！我們會盡快審核您的圖片。", ephemeral=True)
    
    @app_commands.command(name="dynamic-voice-audio", description="投稿動態語音頻道的進入音效")
    @app_commands.describe(audio="音檔（MP3、WAV、OGG 格式，最大 5MB，建議 3-10 秒）")
    async def dynamic_voice_audio(self, interaction: discord.Interaction, audio: discord.Attachment):
        # Rate Limit Check
        current_time = time.time()
        user_id = interaction.user.id
        if user_id in contribution_cooldowns:
            last_time = contribution_cooldowns[user_id]
            if current_time - last_time < 300:
                remaining = int(300 - (current_time - last_time))
                await interaction.response.send_message(f"投稿過於頻繁，請等待 {remaining} 秒後再試。", ephemeral=True)
                return
        
        # 檢查檔案類型
        if not audio.filename.lower().endswith(('.mp3', '.wav', '.ogg')):
            await interaction.response.send_message("錯誤：只支援 MP3、WAV、OGG 格式的音檔。", ephemeral=True)
            return
        
        # 檢查檔案大小（限制 5MB）
        if audio.size > 5 * 1024 * 1024:
            await interaction.response.send_message("錯誤：音檔大小超過 5MB，請選擇較小的音檔。", ephemeral=True)
            return
        
        contribute_channel_id = config("contribute_channel_id", None)
        if contribute_channel_id is None:
            await interaction.response.send_message("投稿頻道未設置，請聯繫開發者。", ephemeral=True)
            return
        contribute_channel = self.bot.get_channel(int(contribute_channel_id))
        if contribute_channel is None:
            await interaction.response.send_message("無法找到投稿頻道，請聯繫開發者。", ephemeral=True)
            return
        
        audio_data = await audio.read()
        
        # 使用 UUID 作為檔名
        file_ext = os.path.splitext(audio.filename)[1].lower() or ".mp3"
        audio_filename = f"{uuid.uuid4()}{file_ext}"
        
        embed = discord.Embed(title="新的「動態語音音效」投稿", color=discord.Color.orange())
        embed.set_author(name=f"{interaction.user.name} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
        embed.timestamp = datetime.now(timezone.utc)
        embed.add_field(name="使用者 ID", value=str(interaction.user.id))
        embed.add_field(name="原始檔名", value=audio.filename)
        embed.add_field(name="檔案大小", value=f"{audio.size / 1024:.2f} KB")
        embed.add_field(name="預計儲存為", value=audio_filename)
        
        file = discord.File(io.BytesIO(audio_data), filename=audio.filename)
        
        view = ContributionView("dynamic_voice_audio", audio_filename=audio_filename)
        await contribute_channel.send(embed=embed, file=file, view=view)
        contribution_cooldowns[user_id] = time.time()
        await interaction.response.send_message("感謝您的投稿！我們會盡快審核您的音檔。\n-# 審核通過後會通知你。", ephemeral=True)


asyncio.run(bot.add_cog(Contribute(bot)))