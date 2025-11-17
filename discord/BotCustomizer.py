import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, config
import base64
import mimetypes
import requests
import g4f
import json
from logger import log
import asyncio
import traceback
import io


prompt = """
你是一個 Discord 頭像、橫幅審核員，你的工作是審核用戶提交的頭像和橫幅圖片是否符合 Discord 的社群規範。當用戶提交圖片後，你需要根據以下標準進行審核：
不得包含任何色情、暴力、仇恨言論或其他不當內容。
如果你無法確定圖片是否符合規範，請選擇需要人工審核。

請返回 JSON 格式的回應，包含以下欄位：
{
    "approved": true 或 false，表示圖片是否通過審核，
    "reason": "如果未通過審核，請提供拒絕的原因說明",
    "human_review": true 或 false，表示是否需要人工審核
}
"""

bio_prompt = """
你是一個 Discord 關於我（Bio）審核員，你的工作是審核用戶提交的關於我內容是否符合 Discord 的社群規範。當用戶提交關於我內容後，你需要根據以下標準進行審核：
1. 不得包含任何色情、暴力、仇恨言論或其他不當內容
2. 內容應該尊重他人，不得包含人身攻擊或歧視性言論
3. 不得違反 Discord 的使用條款和社群規範
4. 不得廣告或推銷產品
如果你無法確定內容是否符合規範，請選擇需要人工審核。
請返回 JSON 格式的回應，包含以下欄位：
{
    "approved": true 或 false，表示內容是否通過審核，
    "reason": "如果未通過審核，請提供拒絕的原因說明",
    "human_review": true 或 false，表示是否需要人工審核
}
"""


async def review_image(image_data: bytes) -> dict:
    try:
        response = await asyncio.to_thread(
            g4f.ChatCompletion.create,
            model="openai",
            provider=g4f.Provider.PollinationsAI,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "請審核這張圖片，並以 JSON 格式回應。"},
            ],
            stream=False,
            image=image_data
        )
        # try get json
        try:
            response = "{" + response.split("}{")[1]
        except Exception:
            pass
        log(f"收到圖片審核回應：{response}", module_name="BotCustomizer")
        # 嘗試解析回應為 JSON
        result = json.loads(response)
        return result
    except Exception as e:
        log(f"審核過程中發生錯誤: {e}", module_name="BotCustomizer")
        return {"approved": False, "reason": f"審核過程中發生錯誤: {e}", "human_review": True}

async def review_bio(bio_text: str) -> dict:
    try:
        response = await asyncio.to_thread(
            g4f.ChatCompletion.create,
            model="openai",
            provider=g4f.Provider.PollinationsAI,
            messages=[
                {"role": "system", "content": bio_prompt},
                {"role": "user", "content": f"請審核這段關於我內容：{bio_text}，並以 JSON 格式回應。"},
            ],
            stream=False
        )
        try:
            response = "{" + response.split("}{")[1]
        except Exception:
            pass
        log(f"收到關於我審核回應：{response}", module_name="BotCustomizer")
        # 嘗試解析回應為 JSON
        result = json.loads(response)
        return result
    except Exception as e:
        log(f"關於我審核過程中發生錯誤: {e}", module_name="BotCustomizer")
        return {"approved": False, "reason": f"審核過程中發生錯誤: {e}", "human_review": True}


@app_commands.default_permissions(administrator=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class BotCustomizer(commands.GroupCog, name="change"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name=app_commands.locale_str("avatar"), description="更換機器人的頭像（不指定則恢復預設頭像）")
    @app_commands.describe(image="新的頭像圖片")
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def changeavatar_command(self, interaction: discord.Interaction, image: discord.Attachment = None):
        guild_id = interaction.guild.id if interaction.guild else None
        await interaction.response.defer()
        try:
            if image:
                img_data = await image.read()
                # limit 10mb
                if len(img_data) > 10 * 1024 * 1024:
                    await interaction.followup.send("圖片大小超過 10MB，請選擇較小的圖片。")
                    return
                try:
                    embed = discord.Embed(title="頭像圖片審核請求", description=f"用戶 {interaction.user} 正在審核頭像圖片。")
                    embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
                    embed.set_footer(text=interaction.guild.name if interaction.guild else "DM", icon_url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None)
                    embed.set_image(url="attachment://avatar_image.png")
                    embed.add_field(name="伺服器 ID", value=str(guild_id))
                    embed.add_field(name="目前狀態", value="等待審核中...")
                    msg = await bot.get_channel(config("botcustomizer_log_channel_id")).send(embed=embed, file=discord.File(fp=io.BytesIO(img_data), filename="avatar_image.png"))
                except Exception:
                    msg = None
                    embed = None
                if interaction.user.id not in config("owners", []):
                    await interaction.followup.send("圖片正在審核中，請稍候...")
                    review_result = await review_image(img_data)
                    if not review_result.get("approved", False):
                        if review_result.get("human_review", False):
                            await interaction.followup.send(f"圖片需要人工審核，原因：{review_result.get('reason', '未知原因')}\n-# 但現在還沒做完 哈哈")
                            # Here you might want to implement a queue or notification for manual review
                            if msg:
                                embed.color = discord.Color.orange()
                                embed.set_field_at(1, name="目前狀態", value="需要人工審核", inline=False)
                                embed.add_field(name="原因", value=review_result.get("reason", "未知原因"), inline=False)
                                await msg.edit(embed=embed)
                                log("頭像圖片需要人工審核", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
                            return
                        await interaction.followup.send(f"圖片未通過審核：{review_result.get('reason', '未知原因')}")
                        log("頭像圖片未通過審核", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
                        if msg:
                            embed.color = discord.Color.red()
                            embed.set_field_at(1, name="目前狀態", value="審核未通過", inline=False)
                            embed.add_field(name="拒絕原因", value=review_result.get("reason", "未知原因"), inline=False)
                            await msg.edit(embed=embed)
                        return
                    if msg:
                        embed.color = discord.Color.green()
                        embed.set_field_at(1, name="目前狀態", value="審核通過並已更新頭像", inline=False)
                        embed.add_field(name="原因", value=review_result.get("reason", "未知原因"), inline=False)
                        await msg.edit(embed=embed)
                else:
                    embed.color = discord.Color.green()
                    embed.set_field_at(1, name="目前狀態", value="擁有者提交，直接更新頭像", inline=False)
                    await msg.edit(embed=embed)
                mine = mimetypes.guess_type(image.filename)[0] or "application/octet-stream"
                b64_data = base64.b64encode(img_data).decode('utf-8')
                avatar_data = f"data:{mine};base64,{b64_data}"
            else:
                avatar_data = None  # reset to default
            url = f"https://discord.com/api/v10/guilds/{guild_id}/members/@me"
            headers = {"Authorization": f"Bot {bot.http.token}"}
            payload = {"avatar": avatar_data}
            response = requests.patch(url, json=payload, headers=headers)
            response.raise_for_status()
            await interaction.followup.send("頭像更新成功！")
        except Exception as e:
            await interaction.followup.send(f"更新頭像時發生錯誤：{e}")
            log(f"更新頭像時發生錯誤：{e}", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
            traceback.print_exc()


    @app_commands.command(name=app_commands.locale_str("banner"), description="更換機器人的橫幅（不指定則恢復預設橫幅）")
    @app_commands.describe(image="新的橫幅圖片")
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def changebanner_command(self, interaction: discord.Interaction, image: discord.Attachment = None):
        guild_id = interaction.guild.id if interaction.guild else None
        await interaction.response.defer()
        try:
            if image:
                img_data = await image.read()
                # limit 10mb
                if len(img_data) > 10 * 1024 * 1024:
                    await interaction.followup.send("圖片大小超過 10MB，請選擇較小的圖片。")
                    return
                try:
                    embed = discord.Embed(title="橫幅圖片審核請求", description=f"用戶 {interaction.user} 正在審核橫幅圖片。")
                    embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
                    embed.set_footer(text=interaction.guild.name if interaction.guild else "DM", icon_url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None)
                    embed.set_image(url="attachment://banner_image.png")
                    embed.add_field(name="伺服器 ID", value=str(guild_id))
                    embed.add_field(name="目前狀態", value="等待審核中...")
                    msg = await bot.get_channel(config("botcustomizer_log_channel_id")).send(embed=embed, file=discord.File(fp=io.BytesIO(img_data), filename="banner_image.png"))
                except Exception:
                    msg = None
                    embed = None
                if interaction.user.id not in config("owners", []):
                    await interaction.followup.send("圖片正在審核中，請稍候...")
                    review_result = await review_image(img_data)
                    if not review_result.get("approved", False):
                        if review_result.get("human_review", False):
                            await interaction.followup.send(f"圖片需要人工審核，原因：{review_result.get('reason', '未知原因')}\n-# 但現在還沒做完 哈哈")
                            # Here you might want to implement a queue or notification for manual review
                            if msg:
                                embed.color = discord.Color.orange()
                                embed.set_field_at(1, name="目前狀態", value="需要人工審核", inline=False)
                                embed.add_field(name="原因", value=review_result.get("reason", "未知原因"), inline=False)
                                await msg.edit(embed=embed)
                                log("橫幅圖片需要人工審核", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
                            return
                        await interaction.followup.send(f"圖片未通過審核：{review_result.get('reason', '未知原因')}")
                        log("橫幅圖片未通過審核", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
                        if msg:
                            embed.color = discord.Color.red()
                            embed.set_field_at(1, name="目前狀態", value="審核未通過", inline=False)
                            embed.add_field(name="拒絕原因", value=review_result.get("reason", "未知原因"), inline=False)
                            await msg.edit(embed=embed)
                        return
                    if msg:
                        embed.color = discord.Color.green()
                        embed.set_field_at(1, name="目前狀態", value="審核通過並已更新橫幅", inline=False)
                        embed.add_field(name="原因", value=review_result.get("reason", "未知原因"), inline=False)
                        await msg.edit(embed=embed)
                else:
                    embed.color = discord.Color.green()
                    embed.set_field_at(1, name="目前狀態", value="擁有者提交，直接更新橫幅", inline=False)
                    await msg.edit(embed=embed)
                mine = mimetypes.guess_type(image.filename)[0] or "application/octet-stream"
                b64_data = base64.b64encode(img_data).decode('utf-8')
                banner_data = f"data:{mine};base64,{b64_data}"
            else:
                banner_data = None  # reset to default
            url = f"https://discord.com/api/v10/guilds/{guild_id}/members/@me"
            headers = {"Authorization": f"Bot {bot.http.token}"}
            payload = {"banner": banner_data}
            response = requests.patch(url, json=payload, headers=headers)
            response.raise_for_status()
            await interaction.followup.send("橫幅更新成功！")
        except Exception as e:
            await interaction.followup.send(f"更新橫幅時發生錯誤：{e}")
            log(f"更新橫幅時發生錯誤：{e}", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
            traceback.print_exc()


    @app_commands.command(name=app_commands.locale_str("bio"), description="更改機器人的關於我（不指定則恢復預設）")
    @app_commands.describe(bio="新的介紹（最多 100 字）")
    @app_commands.default_permissions(administrator=True)
    async def changebio_command(self, interaction: discord.Interaction, bio: str = None):
        guild_id = interaction.guild.id if interaction.guild else None
        await interaction.response.defer()
        try:
            if bio:
                bio = bio if len(bio) <= 100 else bio[:97] + "..."
                try:
                    embed = discord.Embed(title="關於我審核請求", description=f"用戶 {interaction.user} 正在審核關於我內容。")
                    embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
                    embed.set_footer(text=interaction.guild.name if interaction.guild else "DM", icon_url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None)
                    embed.add_field(name="關於我內容", value=bio or "無內容")
                    embed.add_field(name="伺服器 ID", value=str(guild_id))
                    embed.add_field(name="目前狀態", value="等待審核中...")
                    msg = await bot.get_channel(config("botcustomizer_log_channel_id")).send(embed=embed)
                except Exception:
                    msg = None
                    embed = None
                if interaction.user.id not in config("owners", []):
                    await interaction.followup.send("關於我內容正在審核中，請稍候...")
                    review_result = await review_bio(bio)
                    if not review_result.get("approved", False):
                        if review_result.get("human_review", False):
                            await interaction.followup.send(f"關於我內容需要人工審核，原因：{review_result.get('reason', '未知原因')}\n-# 但現在還沒做完 哈哈")
                            # Here you might want to implement a queue or notification for manual review
                            if msg:
                                embed.color = discord.Color.orange()
                                embed.set_field_at(2, name="目前狀態", value="需要人工審核", inline=False)
                                embed.add_field(name="原因", value=review_result.get("reason", "未知原因"), inline=False)
                                await msg.edit(embed=embed)
                                log("關於我內容需要人工審核", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
                            return
                        await interaction.followup.send(f"關於我內容未通過審核：{review_result.get('reason', '未知原因')}")
                        log("關於我內容未通過審核", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
                        if msg:
                            embed.color = discord.Color.red()
                            embed.set_field_at(2, name="目前狀態", value="審核未通過", inline=False)
                            embed.add_field(name="拒絕原因", value=review_result.get("reason", "未知原因"), inline=False)
                            await msg.edit(embed=embed)
                        return
                    if msg:
                        embed.color = discord.Color.green()
                        embed.set_field_at(2, name="目前狀態", value="審核通過並已更新關於我", inline=False)
                        embed.add_field(name="原因", value=review_result.get("reason", "未知原因"), inline=False)
                        await msg.edit(embed=embed)
                else:
                    embed.color = discord.Color.green()
                    embed.set_field_at(2, name="目前狀態", value="擁有者提交，直接更新關於我", inline=False)
                    await msg.edit(embed=embed)
            url = f"https://discord.com/api/v10/guilds/{guild_id}/members/@me"
            headers = {"Authorization": f"Bot {bot.http.token}"}
            payload = {"bio": bio}
            response = requests.patch(url, json=payload, headers=headers)
            response.raise_for_status()
            log(f"關於我更新為：{bio}", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
            await interaction.followup.send("介紹更新成功！")
        except Exception as e:
            await interaction.followup.send(f"更新介紹時發生錯誤：{e}")
            log(f"更新介紹時發生錯誤：{e}", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
            traceback.print_exc()

asyncio.run(bot.add_cog(BotCustomizer(bot)))