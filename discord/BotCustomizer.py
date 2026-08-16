import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, config
import base64
import mimetypes
import requests
import json
from logger import log
import asyncio
import traceback
import io
from typing import Optional
import re
from ai_provider import create_ai_chat_completion, get_ai_review_model

import i18n
from i18n import t


def S(key, **params):
    """營運者審核頻道的文字：一律以原文語言渲染。"""
    return t(key, locale=i18n.SOURCE_LOCALE, **params)


class HumanReviewView(discord.ui.View):
    """人工審核按鈕視圖"""
    def __init__(
        self,
        review_type: str,  # "avatar", "banner", or "bio"
        guild_id: int,
        user_id: int,
        data: Optional[bytes] = None,  # 圖片資料（用於 avatar/banner）
        bio_text: Optional[str] = None,  # bio 內容（用於 bio）
        mime_type: Optional[str] = None,  # 圖片的 MIME 類型
        reason: str = None
    ):
        super().__init__(timeout=None)  # 不設置超時，讓按鈕持久
        self.review_type = review_type
        self.guild_id = guild_id
        self.user_id = user_id
        self.data = data
        self.bio_text = bio_text
        self.mime_type = mime_type
        self.reason = reason
        # 機器狀態：狀態欄位的位置由 review_type 決定（bio 的 embed 多一個內容欄位）
        self.status_field_index = 2 if review_type == "bio" else 1

    @discord.ui.button(label=i18n.K("botcustomizer.btn.approve"), style=discord.ButtonStyle.success)
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 檢查權限 - 只有擁有者可以審核
        if interaction.user.id not in config("owners", []):
            await interaction.response.send_message(t("common.err.no_permission"), ephemeral=True)
            return
        
        await interaction.response.defer()
        
        try:
            # 根據類型執行對應的更新
            if self.review_type == "avatar":
                if self.data and self.mime_type:
                    b64_data = base64.b64encode(self.data).decode('utf-8')
                    avatar_data = f"data:{self.mime_type};base64,{b64_data}"
                    url = f"https://discord.com/api/v10/guilds/{self.guild_id}/members/@me"
                    headers = {"Authorization": f"Bot {bot.http.token}"}
                    payload = {"avatar": avatar_data}
                    response = requests.patch(url, json=payload, headers=headers)
                    response.raise_for_status()
                    log("Human review approved; avatar updated", module_name="BotCustomizer")
            elif self.review_type == "banner":
                if self.data and self.mime_type:
                    b64_data = base64.b64encode(self.data).decode('utf-8')
                    banner_data = f"data:{self.mime_type};base64,{b64_data}"
                    url = f"https://discord.com/api/v10/guilds/{self.guild_id}/members/@me"
                    headers = {"Authorization": f"Bot {bot.http.token}"}
                    payload = {"banner": banner_data}
                    response = requests.patch(url, json=payload, headers=headers)
                    response.raise_for_status()
                    log("Human review approved; banner updated", module_name="BotCustomizer")
            elif self.review_type == "bio":
                self.bio_text += f"\n\n----------\n{bot.user.name}#{bot.user.discriminator} - {config('website_url', 'https://example.com')}"
                url = f"https://discord.com/api/v10/guilds/{self.guild_id}/members/@me"
                headers = {"Authorization": f"Bot {bot.http.token}"}
                payload = {"bio": self.bio_text}
                response = requests.patch(url, json=payload, headers=headers)
                response.raise_for_status()
                log(f"Human review approved; bio updated to: {self.bio_text}", module_name="BotCustomizer")
            
            # 更新 embed 狀態
            embed = interaction.message.embeds[0] if interaction.message.embeds else None
            if embed:
                embed.color = discord.Color.green()
                if len(embed.fields) > self.status_field_index:
                    embed.set_field_at(self.status_field_index, name=S("botcustomizer.field.status"),
                                       value=S("botcustomizer.status.human_approved", user=interaction.user.mention), inline=False)
                await interaction.message.edit(embed=embed, view=None)
            
            # 通知提交者
            try:
                submitter = await bot.fetch_user(self.user_id)
                loc = i18n.resolve_locale(user_id=self.user_id, guild_id=self.guild_id)
                type_name = t(f"botcustomizer.type.{self.review_type}", locale=loc,
                              default=t("botcustomizer.type.generic", locale=loc))
                await submitter.send(t("botcustomizer.msg.approved_dm", locale=loc, type=type_name))
            except discord.Forbidden:
                log(f"Could not DM user {self.user_id}: DMs closed", module_name="BotCustomizer")
            except discord.NotFound:
                log(f"Could not DM user {self.user_id}: user not found", module_name="BotCustomizer")
            except Exception as e:
                log(f"Error sending DM to user {self.user_id}: {e}", module_name="BotCustomizer")
            
            await interaction.followup.send(t("botcustomizer.msg.review_approved"), ephemeral=True)
        except Exception as e:
            log(f"Error applying approved review: {e}", module_name="BotCustomizer")
            traceback.print_exc()
            await interaction.followup.send(t("botcustomizer.msg.update_error", error=e), ephemeral=True)

    @discord.ui.button(label=i18n.K("botcustomizer.btn.reject"), style=discord.ButtonStyle.danger)
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 檢查權限 - 只有擁有者可以審核
        if interaction.user.id not in config("owners", []):
            await interaction.response.send_message(t("common.err.no_permission"), ephemeral=True)
            return
        
        # 更新 embed 狀態
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed:
            embed.color = discord.Color.red()
            if len(embed.fields) > self.status_field_index:
                embed.set_field_at(self.status_field_index, name=S("botcustomizer.field.status"),
                                   value=S("botcustomizer.status.human_rejected", user=interaction.user.mention), inline=False)
            await interaction.message.edit(embed=embed, view=None)
        
        log("Human review rejected", module_name="BotCustomizer")
        
        # 通知提交者
        try:
            submitter = await bot.fetch_user(self.user_id)
            loc = i18n.resolve_locale(user_id=self.user_id, guild_id=self.guild_id)
            type_name = t(f"botcustomizer.type.{self.review_type}", locale=loc,
                          default=t("botcustomizer.type.generic", locale=loc))
            reason_text = self.reason if self.reason else t("botcustomizer.msg.unknown_reason", locale=loc)
            await submitter.send(t("botcustomizer.msg.rejected_dm", locale=loc, type=type_name, reason=reason_text))
        except discord.Forbidden:
            log(f"Could not DM user {self.user_id}: DMs closed", module_name="BotCustomizer")
        except discord.NotFound:
            log(f"Could not DM user {self.user_id}: user not found", module_name="BotCustomizer")
        except Exception as e:
            log(f"Error sending DM to user {self.user_id}: {e}", module_name="BotCustomizer")
        
        await interaction.response.send_message(t("botcustomizer.msg.review_rejected"), ephemeral=True)


REVIEW_PROMPT = """
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

BIO_REVIEW_PROMPT = """
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
        chat = await asyncio.to_thread(
            create_ai_chat_completion,
            model=get_ai_review_model(),
            messages=[
                {"role": "system", "content": REVIEW_PROMPT},
                {"role": "user", "content": "請審核這張圖片，並以 JSON 格式回應。"},  # i18n: skip (model prompt)
            ],
            stream=False,
            image=image_data
        )
        response = chat.choices[0].message.content.strip()
        # try get json
        try:
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                response = json_match.group(0)
        except Exception:
            pass
        log(f"Image review response: {response}", module_name="BotCustomizer")
        # 嘗試解析回應為 JSON
        result = json.loads(response)
        return result
    except Exception as e:
        log(f"Image review failed: {e}", module_name="BotCustomizer")
        return {"approved": False, "reason": S("botcustomizer.msg.review_error", error=e), "human_review": True}

async def review_bio(bio_text: str) -> dict:
    try:
        chat = await asyncio.to_thread(
            create_ai_chat_completion,
            model=get_ai_review_model(),
            messages=[
                {"role": "system", "content": BIO_REVIEW_PROMPT},
                {"role": "user", "content": f"請審核這段關於我內容：{bio_text}，並以 JSON 格式回應。"},  # i18n: skip (model prompt)
            ],
            stream=False
        )
        response = chat.choices[0].message.content.strip()
        try:
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                response = json_match.group(0)
        except Exception:
            pass
        log(f"Bio review response: {response}", module_name="BotCustomizer")
        # 嘗試解析回應為 JSON
        result = json.loads(response)
        return result
    except Exception as e:
        log(f"Bio review failed: {e}", module_name="BotCustomizer")
        return {"approved": False, "reason": S("botcustomizer.msg.review_error", error=e), "human_review": True}


@app_commands.default_permissions(manage_guild=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class BotCustomizer(commands.GroupCog, name=app_commands.locale_str("change", i18n_key="cmd.botcustomizer.change.root.name")):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name=app_commands.locale_str("avatar", i18n_key="cmd.botcustomizer.change.avatar.name"), description=app_commands.locale_str("Change the bot's avatar (omit to restore the default)", i18n_key="cmd.botcustomizer.change.avatar.desc"))
    @app_commands.describe(image=app_commands.locale_str("The new avatar image", i18n_key="cmd.botcustomizer.change.avatar.param.image"))
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
                    await interaction.followup.send(t("botcustomizer.msg.too_large"))
                    return
                try:
                    embed = discord.Embed(title=S("botcustomizer.embed.review_title_avatar"), description=S("botcustomizer.embed.review_desc_avatar", user=interaction.user))
                    embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
                    embed.set_footer(text=interaction.guild.name if interaction.guild else "DM", icon_url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None)
                    embed.set_image(url="attachment://avatar_image.png")
                    embed.add_field(name=S("botcustomizer.field.guild_id"), value=str(guild_id))
                    embed.add_field(name=S("botcustomizer.field.status"), value=S("botcustomizer.status.pending"))
                    msg = await bot.get_channel(config("botcustomizer_log_channel_id")).send(embed=embed, file=discord.File(fp=io.BytesIO(img_data), filename="avatar_image.png"))
                except Exception:
                    msg = None
                    embed = None
                if interaction.user.id not in config("owners", []):
                    await interaction.followup.send(t("botcustomizer.msg.reviewing_image"))
                    review_result = await review_image(img_data)
                    if not review_result.get("approved", False):
                        if review_result.get("human_review", False):
                            await interaction.followup.send(t("botcustomizer.msg.human_review_needed", reason=review_result.get("reason") or t("botcustomizer.msg.unknown_reason")))
                            # 創建人工審核視圖並更新訊息
                            if msg:
                                embed.color = discord.Color.orange()
                                embed.set_field_at(1, name=S("botcustomizer.field.status"), value=S("botcustomizer.status.human_review"), inline=False)
                                embed.add_field(name=S("botcustomizer.field.reason"), value=review_result.get("reason") or S("botcustomizer.msg.unknown_reason"), inline=False)
                                mime_type = mimetypes.guess_type(image.filename)[0] or "application/octet-stream"
                                review_view = HumanReviewView(
                                    review_type="avatar",
                                    guild_id=guild_id,
                                    user_id=interaction.user.id,
                                    data=img_data,
                                    mime_type=mime_type,
                                    reason=review_result.get("reason")
                                )
                                await msg.edit(embed=embed, view=review_view)
                                log("Avatar image needs human review", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
                            return
                        await interaction.followup.send(t("botcustomizer.msg.rejected_image", reason=review_result.get("reason") or t("botcustomizer.msg.unknown_reason")))
                        log("Avatar image rejected", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
                        if msg:
                            embed.color = discord.Color.red()
                            embed.set_field_at(1, name=S("botcustomizer.field.status"), value=S("botcustomizer.status.rejected"), inline=False)
                            embed.add_field(name=S("botcustomizer.field.reject_reason"), value=review_result.get("reason") or S("botcustomizer.msg.unknown_reason"), inline=False)
                            await msg.edit(embed=embed)
                        return
                    if msg:
                        embed.color = discord.Color.green()
                        embed.set_field_at(1, name=S("botcustomizer.field.status"), value=S("botcustomizer.status.approved_avatar"), inline=False)
                        embed.add_field(name=S("botcustomizer.field.reason"), value=review_result.get("reason") or S("botcustomizer.msg.unknown_reason"), inline=False)
                        await msg.edit(embed=embed)
                else:
                    embed.color = discord.Color.green()
                    embed.set_field_at(1, name=S("botcustomizer.field.status"), value=S("botcustomizer.status.owner_avatar"), inline=False)
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
            await interaction.followup.send(t("botcustomizer.msg.avatar_updated"))
        except Exception as e:
            await interaction.followup.send(t("botcustomizer.msg.update_error_avatar", error=e))
            log(f"Error updating avatar: {e}", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
            traceback.print_exc()


    @app_commands.command(name=app_commands.locale_str("banner", i18n_key="cmd.botcustomizer.change.banner.name"), description=app_commands.locale_str("Change the bot's banner (omit to restore the default)", i18n_key="cmd.botcustomizer.change.banner.desc"))
    @app_commands.describe(image=app_commands.locale_str("The new banner image", i18n_key="cmd.botcustomizer.change.banner.param.image"))
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
                    await interaction.followup.send(t("botcustomizer.msg.too_large"))
                    return
                try:
                    embed = discord.Embed(title=S("botcustomizer.embed.review_title_banner"), description=S("botcustomizer.embed.review_desc_banner", user=interaction.user))
                    embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
                    embed.set_footer(text=interaction.guild.name if interaction.guild else "DM", icon_url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None)
                    embed.set_image(url="attachment://banner_image.png")
                    embed.add_field(name=S("botcustomizer.field.guild_id"), value=str(guild_id))
                    embed.add_field(name=S("botcustomizer.field.status"), value=S("botcustomizer.status.pending"))
                    msg = await bot.get_channel(config("botcustomizer_log_channel_id")).send(embed=embed, file=discord.File(fp=io.BytesIO(img_data), filename="banner_image.png"))
                except Exception:
                    msg = None
                    embed = None
                if interaction.user.id not in config("owners", []):
                    await interaction.followup.send(t("botcustomizer.msg.reviewing_image"))
                    review_result = await review_image(img_data)
                    if not review_result.get("approved", False):
                        if review_result.get("human_review", False):
                            await interaction.followup.send(t("botcustomizer.msg.human_review_needed", reason=review_result.get("reason") or t("botcustomizer.msg.unknown_reason")))
                            # 創建人工審核視圖並更新訊息
                            if msg:
                                embed.color = discord.Color.orange()
                                embed.set_field_at(1, name=S("botcustomizer.field.status"), value=S("botcustomizer.status.human_review"), inline=False)
                                embed.add_field(name=S("botcustomizer.field.reason"), value=review_result.get("reason") or S("botcustomizer.msg.unknown_reason"), inline=False)
                                mime_type = mimetypes.guess_type(image.filename)[0] or "application/octet-stream"
                                review_view = HumanReviewView(
                                    review_type="banner",
                                    guild_id=guild_id,
                                    user_id=interaction.user.id,
                                    data=img_data,
                                    mime_type=mime_type,
                                    reason=review_result.get("reason")
                                )
                                await msg.edit(embed=embed, view=review_view)
                                log("Banner image needs human review", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
                            return
                        await interaction.followup.send(t("botcustomizer.msg.rejected_image", reason=review_result.get("reason") or t("botcustomizer.msg.unknown_reason")))
                        log("Banner image rejected", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
                        if msg:
                            embed.color = discord.Color.red()
                            embed.set_field_at(1, name=S("botcustomizer.field.status"), value=S("botcustomizer.status.rejected"), inline=False)
                            embed.add_field(name=S("botcustomizer.field.reject_reason"), value=review_result.get("reason") or S("botcustomizer.msg.unknown_reason"), inline=False)
                            await msg.edit(embed=embed)
                        return
                    if msg:
                        embed.color = discord.Color.green()
                        embed.set_field_at(1, name=S("botcustomizer.field.status"), value=S("botcustomizer.status.approved_banner"), inline=False)
                        embed.add_field(name=S("botcustomizer.field.reason"), value=review_result.get("reason") or S("botcustomizer.msg.unknown_reason"), inline=False)
                        await msg.edit(embed=embed)
                else:
                    embed.color = discord.Color.green()
                    embed.set_field_at(1, name=S("botcustomizer.field.status"), value=S("botcustomizer.status.owner_banner"), inline=False)
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
            await interaction.followup.send(t("botcustomizer.msg.banner_updated"))
        except Exception as e:
            await interaction.followup.send(t("botcustomizer.msg.update_error_banner", error=e))
            log(f"Error updating banner: {e}", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
            traceback.print_exc()


    @app_commands.command(name=app_commands.locale_str("bio", i18n_key="cmd.botcustomizer.change.bio.name"), description=app_commands.locale_str("Change the bot's About Me (omit to restore the default)", i18n_key="cmd.botcustomizer.change.bio.desc"))
    @app_commands.describe(bio=app_commands.locale_str("The new bio (\\n for line breaks, max 100 characters)", i18n_key="cmd.botcustomizer.change.bio.param.bio"))
    @app_commands.default_permissions(administrator=True)
    async def changebio_command(self, interaction: discord.Interaction, bio: str = None):
        guild_id = interaction.guild.id if interaction.guild else None
        await interaction.response.defer()
        try:
            if bio:
                bio = bio if len(bio) <= 100 else bio[:97] + "..."
                bio = bio.replace("\\n", "\n")
                try:
                    embed = discord.Embed(title=S("botcustomizer.embed.review_title_bio"), description=S("botcustomizer.embed.review_desc_bio", user=interaction.user))
                    embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
                    embed.set_footer(text=interaction.guild.name if interaction.guild else "DM", icon_url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None)
                    embed.add_field(name=S("botcustomizer.field.bio_content"), value=bio or S("botcustomizer.msg.no_content"))
                    embed.add_field(name=S("botcustomizer.field.guild_id"), value=str(guild_id))
                    embed.add_field(name=S("botcustomizer.field.status"), value=S("botcustomizer.status.pending"))
                    msg = await bot.get_channel(config("botcustomizer_log_channel_id")).send(embed=embed)
                except Exception:
                    msg = None
                    embed = None
                if interaction.user.id not in config("owners", []):
                    await interaction.followup.send(t("botcustomizer.msg.reviewing_bio"))
                    review_result = await review_bio(bio)
                    if not review_result.get("approved", False):
                        if review_result.get("human_review", False):
                            await interaction.followup.send(t("botcustomizer.msg.human_review_needed_bio", reason=review_result.get("reason") or t("botcustomizer.msg.unknown_reason")))
                            # 創建人工審核視圖並更新訊息
                            if msg:
                                embed.color = discord.Color.orange()
                                embed.set_field_at(2, name=S("botcustomizer.field.status"), value=S("botcustomizer.status.human_review"), inline=False)
                                embed.add_field(name=S("botcustomizer.field.reason"), value=review_result.get("reason") or S("botcustomizer.msg.unknown_reason"), inline=False)
                                review_view = HumanReviewView(
                                    review_type="bio",
                                    guild_id=guild_id,
                                    user_id=interaction.user.id,
                                    bio_text=bio,
                                    reason=review_result.get("reason")
                                )
                                await msg.edit(embed=embed, view=review_view)
                                log("Bio needs human review", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
                            return
                        await interaction.followup.send(t("botcustomizer.msg.rejected_bio", reason=review_result.get("reason") or t("botcustomizer.msg.unknown_reason")))
                        log("Bio rejected", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
                        if msg:
                            embed.color = discord.Color.red()
                            embed.set_field_at(2, name=S("botcustomizer.field.status"), value=S("botcustomizer.status.rejected"), inline=False)
                            embed.add_field(name=S("botcustomizer.field.reject_reason"), value=review_result.get("reason") or S("botcustomizer.msg.unknown_reason"), inline=False)
                            await msg.edit(embed=embed)
                        return
                    if msg:
                        embed.color = discord.Color.green()
                        embed.set_field_at(2, name=S("botcustomizer.field.status"), value=S("botcustomizer.status.approved_bio"), inline=False)
                        embed.add_field(name=S("botcustomizer.field.reason"), value=review_result.get("reason") or S("botcustomizer.msg.unknown_reason"), inline=False)
                        await msg.edit(embed=embed)
                else:
                    embed.color = discord.Color.green()
                    embed.set_field_at(2, name=S("botcustomizer.field.status"), value=S("botcustomizer.status.owner_bio"), inline=False)
                    await msg.edit(embed=embed)
            bio += f"\n\n----------\n{bot.user.name}#{bot.user.discriminator} - {config('website_url', 'https://example.com')}"
            url = f"https://discord.com/api/v10/guilds/{guild_id}/members/@me"
            headers = {"Authorization": f"Bot {bot.http.token}"}
            payload = {"bio": bio}
            response = requests.patch(url, json=payload, headers=headers)
            response.raise_for_status()
            log(f"Bio updated to: {bio}", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
            await interaction.followup.send(t("botcustomizer.msg.bio_updated"))
        except Exception as e:
            await interaction.followup.send(t("botcustomizer.msg.update_error_bio", error=e))
            log(f"Error updating bio: {e}", module_name="BotCustomizer", user=interaction.user, guild=interaction.guild)
            traceback.print_exc()

asyncio.run(bot.add_cog(BotCustomizer(bot)))
