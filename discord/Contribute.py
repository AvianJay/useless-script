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
from urllib.parse import urlencode
import asyncio

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

def cleanup_tokens():
    current_time = time.time()
    expired_tokens = [token for token, data in auth_tokens.items() if current_time - data['timestamp'] > 600] # 10 minutes
    for token in expired_tokens:
        del auth_tokens[token]

class ContributionView(discord.ui.View):
    def __init__(self, ctype):
        super().__init__(timeout=None)
        self.ctype = ctype

    @discord.ui.button(label="同意", style=discord.ButtonStyle.green, custom_id="contribution_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            if self.ctype == "feedgrass":
                # Attachment 0: Image, Attachment 1: JSON
                if len(interaction.message.attachments) < 2:
                    await interaction.followup.send("錯誤：找不到附件。", ephemeral=True)
                    return
                
                img_att = interaction.message.attachments[0]
                json_att = interaction.message.attachments[1]
                
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

            elif self.ctype == "whatisthisguytalking":
                # Attachment 0: Image
                if len(interaction.message.attachments) < 1:
                    await interaction.followup.send("錯誤：找不到附件。", ephemeral=True)
                    return
                
                img_att = interaction.message.attachments[0]
                
                if not os.path.exists("whatisthisguytalking-images"):
                    os.makedirs("whatisthisguytalking-images")
                
                await img_att.save(os.path.join("whatisthisguytalking-images", img_att.filename))
                await interaction.followup.send("已保存並批准投稿！", ephemeral=True)
                
                 # Update message
                embed = interaction.message.embeds[0]
                embed.color = discord.Color.green()
                embed.title += " [已批准]"
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(embed=embed, view=self)

                try:
                    from MessageImage import load_whatisthisguytalking_images
                    count = await load_whatisthisguytalking_images()
                    await interaction.followup.send(f"已重新載入 {count} 張圖片。", ephemeral=True)
                except Exception as e:
                    await interaction.followup.send(f"重新載入失敗: {e}", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"批准失敗: {e}", ephemeral=True)
            log(f"Contribution Approve Error: {e}", module_name="Contribute", level=logging.ERROR)

    @discord.ui.button(label="拒絕", style=discord.ButtonStyle.red, custom_id="contribution_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.title += " [已拒絕]"
        for child in self.children:
            child.disabled = True
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
                embed.add_field(name="NSFW", value=str(json_payload.get("nsfw", False)))
                embed.timestamp = datetime.now(timezone.utc)
                
                # Create files
                img_file = discord.File(io.BytesIO(file_data), filename=img_filename)
                
                json_bytes = json.dumps(json_payload, indent=4, ensure_ascii=False).encode('utf-8')
                json_file = discord.File(io.BytesIO(json_bytes), filename=json_filename)
                
                view = ContributionView("feedgrass")
                await channel.send(embed=embed, files=[img_file, json_file], view=view)

            bot.loop.create_task(send_contribution())
            return "投稿已送出！"

        except Exception as e:
            log(f"Contribute Error: {e}", module_name="Contribute", level=logging.ERROR)
            return f"發生錯誤: {e}", 500
        


class Contribute(commands.GroupCog, description="投稿圖片"):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="feedgrass", description="投稿 dsize 的草飼圖")
    async def contribute_feed_grass(self, interaction: discord.Interaction):
        redirect_uri = config('website_url') + "/contribute-feed-grass"
        url = f"https://discord.com/oauth2/authorize?client_id={self.bot.application.id}&response_type=code&scope=identify&prompt=none&{urlencode({'redirect_uri': redirect_uri})}"
        await interaction.response.send_message(f"請點擊以下連結進行投稿：\n[點我投稿]({url})", ephemeral=True)
    
    @app_commands.command(name="what-is-this-guy-talking-about", description="投稿「這傢伙在說什麼呢」圖片")
    async def what_is_this_guy_talking_about(self, interaction: discord.interactions, image: discord.Attachment):
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
        await interaction.response.send_message("感謝您的投稿！我們會盡快審核您的圖片。", ephemeral=True)


asyncio.run(bot.add_cog(Contribute(bot)))