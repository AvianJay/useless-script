import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, config
import asyncio
from datetime import datetime, timezone

import i18n
from i18n import t

# 回饋頻道（開發者的頻道）內的 embed 欄位順序是機器約定：
# fields[0] = 使用者 ID、fields[1] = 回饋內容。回覆流程靠位置＋數字檢查
# 解析，欄位「名稱」只是顯示文字。


class ReplyFeedbackView(i18n.I18nView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label=i18n.K("feedback.btn.reply"), style=discord.ButtonStyle.primary, custom_id="reply_feedback")
    async def provide_feedback(self, interaction: discord.Interaction, button: discord.ui.Button):
        class FeedbackReplyModal(discord.ui.Modal, title=t("feedback.modal.reply_title")):
            reply_input = discord.ui.TextInput(
                label=t("feedback.modal.reply_label"),
                style=discord.TextStyle.paragraph,
                placeholder=t("feedback.modal.reply_placeholder"),
                required=True,
                max_length=2000
            )

            async def on_submit(self, modal_interaction: discord.Interaction):
                try:
                    # find user id from original embed（機器約定：fields[0] 是使用者 ID）
                    original_embed = interaction.message.embeds[0] if interaction.message.embeds else None
                    if not (original_embed and len(original_embed.fields) > 0
                            and str(original_embed.fields[0].value).isdigit()):
                        await modal_interaction.response.send_message(
                            t("feedback.msg.user_not_found"), ephemeral=True)
                        return
                    user_id = int(original_embed.fields[0].value)
                    user = await bot.fetch_user(user_id)
                    if not user:
                        await modal_interaction.response.send_message(
                            t("feedback.msg.user_not_found"), ephemeral=True)
                        return

                    # 回覆內容以「收件人」的語言渲染，不是操作者的語言
                    recipient_locale = i18n.resolve_locale(user_id=user_id)
                    embed = discord.Embed(
                        title=t("feedback.embed.reply_title", locale=recipient_locale),
                        color=discord.Color.green())
                    embed.add_field(
                        name=t("feedback.embed.reply_content", locale=recipient_locale),
                        value=self.reply_input.value, inline=False)
                    embed.timestamp = datetime.now(timezone.utc)
                    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
                    embed.set_footer(text=t("feedback.embed.reply_footer", locale=recipient_locale))
                    await user.send(embed=embed)
                    await modal_interaction.response.send_message(
                        t("feedback.msg.reply_sent"), ephemeral=True)
                except Exception as e:
                    await modal_interaction.response.send_message(
                        t("feedback.msg.reply_failed", error=e), ephemeral=True)
        modal = FeedbackReplyModal()
        await interaction.response.send_modal(modal)

class FeedbackCog(commands.Cog):
    @app_commands.command(name=app_commands.locale_str("feedback", i18n_key="cmd.feedback.feedback.name"), description=app_commands.locale_str("Send feedback to the bot developers", i18n_key="cmd.feedback.feedback.desc"))
    @app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
    async def feedback_command(self, interaction: discord.Interaction):
        class FeedbackModal(discord.ui.Modal, title=t("feedback.modal.title")):
            feedback_input = discord.ui.TextInput(
                label=t("feedback.modal.label"),
                style=discord.TextStyle.paragraph,
                placeholder=t("feedback.modal.placeholder"),
                required=True,
                max_length=2000
            )

            async def on_submit(self, modal_interaction: discord.Interaction):
                feedback_message_channel_id = config("feedback_message_channel_id", None)
                if feedback_message_channel_id is None:
                    await modal_interaction.response.send_message(
                        t("feedback.msg.channel_missing"), ephemeral=True)
                    return

                feedback_channel = bot.get_channel(int(feedback_message_channel_id))
                if feedback_channel is None:
                    await modal_interaction.response.send_message(
                        t("feedback.msg.channel_missing"), ephemeral=True)
                    return

                # 回饋頻道是開發者的頻道，embed 以原文語言渲染；
                # fields[0] 必須是使用者 ID（回覆流程的機器約定）
                embed = discord.Embed(
                    title=t("feedback.embed.title", locale=i18n.SOURCE_LOCALE),
                    color=discord.Color.blue())
                embed.add_field(
                    name=t("feedback.embed.user_id", locale=i18n.SOURCE_LOCALE),
                    value=interaction.user.id, inline=False)
                embed.add_field(
                    name=t("feedback.embed.content", locale=i18n.SOURCE_LOCALE),
                    value=self.feedback_input.value, inline=False)
                embed.timestamp = datetime.now(timezone.utc)
                to_show_name = f"{interaction.user.display_name} ({interaction.user.name})" if interaction.user.display_name != interaction.user.name else interaction.user.name
                embed.set_author(name=to_show_name, icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)

                try:
                    # 回覆按鈕與 embed 同屬開發者頻道，以原文語言建構
                    with i18n.use_locale(i18n.SOURCE_LOCALE):
                        reply_view = ReplyFeedbackView()
                    await feedback_channel.send(embed=embed, view=reply_view)
                    await modal_interaction.response.send_message(
                        t("feedback.msg.sent",
                          invite=config('support_server_invite', 'https://discord.gg/your-support-server')),
                        ephemeral=True)
                except Exception as e:
                    await modal_interaction.response.send_message(
                        t("feedback.msg.send_failed"), ephemeral=True)
        modal = FeedbackModal()
        await interaction.response.send_modal(modal)

    @commands.Cog.listener()
    async def on_ready(self):
        bot.add_view(ReplyFeedbackView())

asyncio.run(bot.add_cog(FeedbackCog()))
