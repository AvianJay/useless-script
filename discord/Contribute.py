import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, get_user_data, set_user_data, config, modules
from datetime import datetime, timezone
if "Website" not in modules:
    raise Exception("Dependency module Website is not loaded; Contribute cannot be loaded.")
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
from urllib.parse import urlencode, urlparse
import asyncio
import traceback
from Economy import log_transaction, GLOBAL_CURRENCY_NAME
from PIL import Image, ImageDraw, ImageFont

import i18n
from i18n import t

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
WHATTISTHISGUYTALKING_STATIC_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
WHATTISTHISGUYTALKING_GIF_EXTENSIONS = {".gif"}
WHATTISTHISGUYTALKING_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv"}


def classify_whatisthisguytalking_attachment(attachment: discord.Attachment) -> str | None:
    content_type = (attachment.content_type or "").lower()
    ext = os.path.splitext(attachment.filename or "")[1].lower()

    if content_type == "image/gif" or ext in WHATTISTHISGUYTALKING_GIF_EXTENSIONS:
        return "gif"
    if content_type.startswith("video/") or ext in WHATTISTHISGUYTALKING_VIDEO_EXTENSIONS:
        return "video"
    if content_type.startswith("image/") or ext in WHATTISTHISGUYTALKING_STATIC_EXTENSIONS:
        return "static"
    return None


def get_whatisthisguytalking_extension(attachment: discord.Attachment, media_type: str) -> str:
    ext = os.path.splitext(attachment.filename or "")[1].lower()
    if ext:
        return ext

    if media_type == "gif":
        return ".gif"
    if media_type == "video":
        return ".mp4"
    return ".png"

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
        "投稿審核獎勵",  # i18n: skip (stored tx data)
        APPROVAL_REWARD_GLOBAL,
        GLOBAL_CURRENCY_NAME,
        f"投稿類型：{ctype}"  # i18n: skip (stored tx data)
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


class EditJsonModal(i18n.I18nModal, title=i18n.K("contribute.editjson.title")):
    json_content = discord.ui.TextInput(
        label=i18n.K("contribute.editjson.label"),
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
            await interaction.response.send_message(t("contribute.err.json_format", error=e), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 驗證必要欄位
        if "target" not in new_json or "position" not in new_json["target"] or "size" not in new_json["target"]:
            await interaction.followup.send(t("contribute.err.missing_target_fields"), ephemeral=True)
            return

        if not new_json.get("self", False):
            if "feeder" not in new_json or "position" not in new_json["feeder"] or "size" not in new_json["feeder"]:
                await interaction.followup.send(t("contribute.err.missing_feeder_fields"), ephemeral=True)
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
                await interaction.followup.send(t("contribute.err.json_attachment_not_found"), ephemeral=True)
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
            await interaction.followup.send(t("contribute.msg.json_updated"), ephemeral=True)

        except Exception as e:
            await interaction.followup.send(t("contribute.err.update_failed", error=e), ephemeral=True)
            traceback.print_exc()


class ContributionReviewView(discord.ui.View):
    def __init__(self, ctype, audio_filename=None):
        super().__init__(timeout=None)
        self.ctype = ctype
        self.audio_filename = audio_filename

        if ctype == "feedgrass":
            edit_btn = discord.ui.Button(
                label=t("contribute.review.btn.edit_json"),
                style=discord.ButtonStyle.grey,
                custom_id="contribution_edit_json_v2",
                emoji="🛠️",
            )
            edit_btn.callback = self.edit_json
            self.add_item(edit_btn)

        approve_btn = discord.ui.Button(
            label=t("contribute.review.btn.approve"),
            style=discord.ButtonStyle.green,
            custom_id=f"contribution_approve_v2_{ctype}",
        )
        approve_btn.callback = self.approve
        self.add_item(approve_btn)

        reject_btn = discord.ui.Button(
            label=t("contribute.review.btn.reject"),
            style=discord.ButtonStyle.red,
            custom_id=f"contribution_reject_v2_{ctype}",
        )
        reject_btn.callback = self.reject
        self.add_item(reject_btn)

    def _disable_all_buttons(self):
        for child in self.children:
            child.disabled = True

    async def _reward_submitter(self, interaction: discord.Interaction, approved_key: str):
        user_id_int = int(interaction.message.embeds[0].fields[0].value)
        rewarded, new_global_balance = grant_approval_global_reward_once(
            user_id=user_id_int,
            message_id=interaction.message.id,
            ctype=self.ctype,
        )
        user = await bot.fetch_user(user_id_int)
        recipient_loc = i18n.resolve_locale(user_id=user_id_int)
        approved_text = t(approved_key, locale=recipient_loc)
        with i18n.use_locale(recipient_loc):
            if rewarded:
                await user.send(t("contribute.dm.approved_with_reward",
                                  approved_text=approved_text,
                                  reward=APPROVAL_REWARD_GLOBAL,
                                  currency=GLOBAL_CURRENCY_NAME,
                                  balance=f"{new_global_balance:,.2f}"))
            else:
                await user.send(t("contribute.dm.approved_plain", approved_text=approved_text))

    async def _get_source_message(self, interaction: discord.Interaction) -> discord.Message:
        message = interaction.message
        channel = interaction.channel
        if channel and hasattr(channel, "fetch_message"):
            try:
                fetched = await channel.fetch_message(interaction.message.id)
                if fetched is not None:
                    message = fetched
            except Exception:
                pass
        return message

    def _download_embed_media(self, embed: discord.Embed) -> tuple[bytes, str] | None:
        if not embed.image or not embed.image.url:
            return None

        response = requests.get(embed.image.url, timeout=15)
        response.raise_for_status()
        path = urlparse(embed.image.url).path
        ext = os.path.splitext(path)[1].lower() or ".png"
        return response.content, ext

    async def approve(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            source_message = await self._get_source_message(interaction)

            if self.ctype == "feedgrass":
                img_att = None
                json_att = None
                for att in source_message.attachments:
                    if att.filename.endswith(".json"):
                        json_att = att
                    elif att.filename.endswith(".png") and att.filename != "preview.png":
                        img_att = att

                if not img_att or not json_att:
                    await interaction.followup.send(t("contribute.review.err.missing_image_or_json"), ephemeral=True)
                    return

                os.makedirs("dsize-feedgrass-images", exist_ok=True)
                await img_att.save(os.path.join("dsize-feedgrass-images", img_att.filename))
                await json_att.save(os.path.join("dsize-feedgrass-images", json_att.filename))
                await interaction.followup.send(t("contribute.review.msg.saved"), ephemeral=True)

                embed = source_message.embeds[0]
                embed.color = discord.Color.green()
                embed.title += t("contribute.suffix.approved")
                self._disable_all_buttons()
                await interaction.edit_original_response(embed=embed, view=self)

                try:
                    from dsize import load_feedgrass_images
                    count = load_feedgrass_images()
                    await interaction.followup.send(t("contribute.review.msg.reloaded_assets", count=count), ephemeral=True)
                except ImportError:
                    pass

                await self._reward_submitter(interaction, "contribute.dm.text.feedgrass_approved")
                return

            if self.ctype == "whatisthisguytalking":
                os.makedirs("whatisthisguytalking-images", exist_ok=True)
                media_att = source_message.attachments[0] if source_message.attachments else None

                if media_att is not None:
                    media_type = classify_whatisthisguytalking_attachment(media_att)
                    if media_type is None:
                        await interaction.followup.send(t("contribute.review.err.unsupported_format"), ephemeral=True)
                        return

                    file_ext = get_whatisthisguytalking_extension(media_att, media_type)
                    saved_path = os.path.join("whatisthisguytalking-images", uuid.uuid4().hex + file_ext)
                    await media_att.save(saved_path)
                else:
                    embed_media = self._download_embed_media(source_message.embeds[0])
                    if embed_media is None:
                        await interaction.followup.send(t("contribute.review.err.no_attachment_or_preview"), ephemeral=True)
                        return

                    media_bytes, file_ext = embed_media
                    saved_path = os.path.join("whatisthisguytalking-images", uuid.uuid4().hex + file_ext)
                    with open(saved_path, "wb") as f:
                        f.write(media_bytes)

                await interaction.followup.send(t("contribute.review.msg.asset_saved"), ephemeral=True)

                embed = source_message.embeds[0]
                embed.color = discord.Color.green()
                embed.title += t("contribute.suffix.approved")
                self._disable_all_buttons()
                await interaction.edit_original_response(embed=embed, view=self)

                try:
                    from MessageImage import load_whatisthisguytalking_images
                    count = await load_whatisthisguytalking_images()
                    await interaction.followup.send(t("contribute.review.msg.reloaded_assets", count=count), ephemeral=True)
                except Exception as e:
                    await interaction.followup.send(t("contribute.review.err.reload_failed", error=e), ephemeral=True)

                await self._reward_submitter(interaction, "contribute.dm.text.whatisthisguytalking_approved")
                return

            if self.ctype == "dynamic_voice_audio":
                if len(source_message.attachments) < 1:
                    await interaction.followup.send(t("contribute.review.err.no_audio_attachment"), ephemeral=True)
                    return

                audio_att = source_message.attachments[0]
                audio_data = await audio_att.read()
                audio_folder = os.path.join(os.path.dirname(__file__), "assets", "dynamic_voice_audio")
                os.makedirs(audio_folder, exist_ok=True)
                audio_path = os.path.join(audio_folder, self.audio_filename)
                with open(audio_path, "wb") as f:
                    f.write(audio_data)

                await interaction.followup.send(t("contribute.review.msg.audio_saved_as", filename=self.audio_filename), ephemeral=True)

                embed = source_message.embeds[0]
                embed.color = discord.Color.green()
                embed.title += t("contribute.suffix.approved")
                self._disable_all_buttons()
                await interaction.edit_original_response(embed=embed, view=self)

                log(f"Audio file saved as: {self.audio_filename}", module_name="Contribute")
                await self._reward_submitter(interaction, "contribute.dm.text.audio_approved")
                return

        except Exception as e:
            await interaction.followup.send(t("contribute.review.err.review_failed", error=e), ephemeral=True)
            traceback.print_exc()
            log(f"Contribution Approve Error: {e}", module_name="Contribute", level=logging.ERROR)

    async def edit_json(self, interaction: discord.Interaction):
        try:
            json_att = None
            for att in interaction.message.attachments:
                if att.filename.endswith(".json"):
                    json_att = att
                    break

            if not json_att:
                await interaction.response.send_message(t("contribute.err.json_attachment_not_found_period"), ephemeral=True)
                return

            json_data = await json_att.read()
            json_str = json_data.decode("utf-8")
            modal = EditJsonModal(interaction.message)
            modal.json_content.default = json_str
            await interaction.response.send_modal(modal)
        except Exception as e:
            await interaction.response.send_message(t("contribute.err.open_editor_failed", error=e), ephemeral=True)
            traceback.print_exc()

    async def reject(self, interaction: discord.Interaction):
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.title += t("contribute.suffix.rejected")
        self._disable_all_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

@app.route("/contribute-feed-grass", methods=["GET", "POST"])
def contribute_feed_grass():
    cleanup_tokens()
    if request.method == "GET":
        if request.args.get("code"):
            code = request.args.get("code")
            redirect_uri = config('website_url') + "/contribute-feed-grass"
            user_id = oauth_code_to_id(code, redirect_uri)
            if not user_id:
                return t("contribute.web.verify_failed")
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
                return t("contribute.web.invalid_or_expired_token")
        else:
             # Redirect to OAuth
             redirect_uri = config('website_url') + "/contribute-feed-grass"
             oauth_url = f"https://discord.com/oauth2/authorize?client_id={bot.application.id}&response_type=code&scope=identify&prompt=none&{urlencode({'redirect_uri': redirect_uri})}"
             return redirect(oauth_url)
    elif request.method == "POST":
        data = request.json
        token = data.get("token")
        if not token or token not in auth_tokens:
             return t("contribute.web.invalid_token"), 401
        
        user_id = auth_tokens[token]["user_id"]

        # Rate Limit Check
        current_time = time.time()
        if user_id in contribution_cooldowns:
            last_time = contribution_cooldowns[user_id]
            if current_time - last_time < 300: # 5 minutes
                remaining = int(300 - (current_time - last_time))
                return t("contribute.web.rate_limited", seconds=remaining), 429
        
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
                 return t("contribute.web.channel_not_set"), 500

            channel = bot.get_channel(int(contribute_channel_id))
            if not channel:
                return t("contribute.web.channel_not_found"), 500

            async def send_contribution():
                # bot.loop.create_task 排程到事件迴圈執行緒，不會繼承 Flask
                # 執行緒的 ContextVar，所以顯式解析審核頻道所在 guild 的語言。
                with i18n.use_locale(i18n.resolve_locale(guild_id=channel.guild.id)):
                    user = await bot.fetch_user(user_id)
                    embed = discord.Embed(title=t("contribute.embed.new_feedgrass"), color=discord.Color.blue())
                    embed.set_author(name=f"{user.name} ({user.id})", icon_url=user.display_avatar.url)
                    embed.add_field(name=t("contribute.field.user_id"), value=user.id)
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

                    view = ContributionReviewView("feedgrass")
                    await channel.send(embed=embed, files=[img_file, json_file, preview_file], view=view)

            bot.loop.create_task(send_contribution())
            contribution_cooldowns[user_id] = time.time()
            return t("contribute.web.submitted")

        except Exception as e:
            log(f"Contribute Error: {e}", module_name="Contribute", level=logging.ERROR)
            return t("contribute.web.error_occurred", error=e), 500
        

@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class Contribute(commands.GroupCog, description=app_commands.locale_str("Submit images", i18n_key="cmd.contribute.contribute.root.desc")):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name=app_commands.locale_str("feedgrass", i18n_key="cmd.contribute.contribute.feedgrass.name"), description=app_commands.locale_str("Submit a feedgrass image for dsize", i18n_key="cmd.contribute.contribute.feedgrass.desc"))
    async def contribute_feed_grass(self, interaction: discord.Interaction):
        redirect_uri = config('website_url') + "/contribute-feed-grass"
        url = f"https://discord.com/oauth2/authorize?client_id={self.bot.application.id}&response_type=code&scope=identify&prompt=none&{urlencode({'redirect_uri': redirect_uri})}"
        embed = discord.Embed(title=t("contribute.embed.feedgrass_cmd_title"), description=t("contribute.embed.feedgrass_cmd_desc"), color=discord.Color.blue())
        link_btn = discord.ui.Button(label=t("contribute.btn.go_submit"), url=url, emoji="🔗")
        view = discord.ui.View()
        view.add_item(link_btn)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("what-is-this-guy-talking-about", i18n_key="cmd.contribute.contribute.what_is_this_guy_talking_about.name"), description=app_commands.locale_str("Submit a \"What is this guy talking about\" image", i18n_key="cmd.contribute.contribute.what_is_this_guy_talking_about.desc"))
    async def what_is_this_guy_talking_about(self, interaction: discord.Interaction, image: discord.Attachment):
        # Rate Limit Check
        current_time = time.time()
        user_id = interaction.user.id
        if user_id in contribution_cooldowns:
            last_time = contribution_cooldowns[user_id]
            if current_time - last_time < 300:
                remaining = int(300 - (current_time - last_time))
                await interaction.response.send_message(t("contribute.cmd.rate_limited", seconds=remaining), ephemeral=True)
                return

        media_type = classify_whatisthisguytalking_attachment(image)
        if media_type is None:
            await interaction.response.send_message(t("contribute.cmd.upload_image_gif_video"), ephemeral=True)
            return
        contribute_channel_id = config("contribute_channel_id", None)
        if contribute_channel_id is None:
            await interaction.response.send_message(t("contribute.cmd.channel_not_set"), ephemeral=True)
            return
        contribute_channel = self.bot.get_channel(int(contribute_channel_id))
        if contribute_channel is None:
            await interaction.response.send_message(t("contribute.cmd.channel_not_found"), ephemeral=True)
            return
        review_locale = i18n.resolve_locale(guild_id=contribute_channel.guild.id)
        embed = discord.Embed(title=t("contribute.embed.new_whatisthisguytalking", locale=review_locale), color=discord.Color.green())
        embed.set_author(name=f"{interaction.user.name} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
        embed.timestamp = datetime.now(timezone.utc)
        embed.add_field(name=t("contribute.field.user_id", locale=review_locale), value=interaction.user.id)
        
        # We need to re-upload the file to the channel? Or just use the url?
        # Using url is fine for display, but for "Approval" we need to download it.
        # It's better to re-upload it as a file so it persists in the channel even if original is deleted, 
        # and so we can easily grab it in the View.
        
        # Download first
        img_data = await image.read()
        file_ext = get_whatisthisguytalking_extension(image, media_type).lstrip(".")
        file_uuid = str(uuid.uuid4())
        new_filename = f"{file_uuid}.{file_ext}"
        
        file = discord.File(io.BytesIO(img_data), filename=new_filename)
        media_type_labels = {
            "static": t("contribute.media_type.static", locale=review_locale),
            "gif": "GIF",
            "video": t("contribute.media_type.video", locale=review_locale),
        }
        embed.add_field(name=t("contribute.field.media_type", locale=review_locale), value=media_type_labels[media_type])
        if media_type != "video":
            embed.set_image(url=f"attachment://{new_filename}")
        else:
            embed.description = t("contribute.embed.video_attachment_desc", locale=review_locale, filename=new_filename)
        
        with i18n.use_locale(review_locale):
            view = ContributionReviewView("whatisthisguytalking")
        await contribute_channel.send(embed=embed, file=file, view=view)
        contribution_cooldowns[user_id] = time.time()
        await interaction.response.send_message(t("contribute.cmd.thanks_image"), ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("dynamic-voice-audio", i18n_key="cmd.contribute.contribute.dynamic_voice_audio.name"), description=app_commands.locale_str("Submit a join sound for dynamic voice channels", i18n_key="cmd.contribute.contribute.dynamic_voice_audio.desc"))
    @app_commands.describe(audio=app_commands.locale_str("Audio file (MP3/WAV/OGG, max 5MB, 3-10 seconds recommended)", i18n_key="cmd.contribute.contribute.dynamic_voice_audio.param.audio"))
    async def dynamic_voice_audio(self, interaction: discord.Interaction, audio: discord.Attachment):
        # Rate Limit Check
        current_time = time.time()
        user_id = interaction.user.id
        if user_id in contribution_cooldowns:
            last_time = contribution_cooldowns[user_id]
            if current_time - last_time < 300:
                remaining = int(300 - (current_time - last_time))
                await interaction.response.send_message(t("contribute.cmd.rate_limited", seconds=remaining), ephemeral=True)
                return

        # 檢查檔案類型
        if not audio.filename.lower().endswith(('.mp3', '.wav', '.ogg')):
            await interaction.response.send_message(t("contribute.cmd.audio_format_error"), ephemeral=True)
            return

        # 檢查檔案大小（限制 5MB）
        if audio.size > 5 * 1024 * 1024:
            await interaction.response.send_message(t("contribute.cmd.audio_too_large"), ephemeral=True)
            return

        contribute_channel_id = config("contribute_channel_id", None)
        if contribute_channel_id is None:
            await interaction.response.send_message(t("contribute.cmd.channel_not_set"), ephemeral=True)
            return
        contribute_channel = self.bot.get_channel(int(contribute_channel_id))
        if contribute_channel is None:
            await interaction.response.send_message(t("contribute.cmd.channel_not_found"), ephemeral=True)
            return

        audio_data = await audio.read()

        # 使用 UUID 作為檔名
        file_ext = os.path.splitext(audio.filename)[1].lower() or ".mp3"
        audio_filename = f"{uuid.uuid4()}{file_ext}"

        review_locale = i18n.resolve_locale(guild_id=contribute_channel.guild.id)
        embed = discord.Embed(title=t("contribute.embed.new_dynamic_voice_audio", locale=review_locale), color=discord.Color.orange())
        embed.set_author(name=f"{interaction.user.name} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
        embed.timestamp = datetime.now(timezone.utc)
        embed.add_field(name=t("contribute.field.user_id", locale=review_locale), value=str(interaction.user.id))
        embed.add_field(name=t("contribute.field.original_filename", locale=review_locale), value=audio.filename)
        embed.add_field(name=t("contribute.field.file_size", locale=review_locale), value=f"{audio.size / 1024:.2f} KB")
        embed.add_field(name=t("contribute.field.will_save_as", locale=review_locale), value=audio_filename)

        file = discord.File(io.BytesIO(audio_data), filename=audio.filename)

        with i18n.use_locale(review_locale):
            view = ContributionReviewView("dynamic_voice_audio", audio_filename=audio_filename)
        await contribute_channel.send(embed=embed, file=file, view=view)
        contribution_cooldowns[user_id] = time.time()
        await interaction.response.send_message(t("contribute.cmd.thanks_audio"), ephemeral=True)


asyncio.run(bot.add_cog(Contribute(bot)))
