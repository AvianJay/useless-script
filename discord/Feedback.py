import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, config
import asyncio
from datetime import datetime, timezone


class ReplyFeedbackView(discord.ui.View):
    @discord.ui.button(label="回覆", style=discord.ButtonStyle.primary, custom_id="reply_feedback")
    async def provide_feedback(self, interaction: discord.Interaction, button: discord.ui.Button):
        class FeedbackReplyModal(discord.ui.Modal, title="回覆使用者回饋"):
            reply_input = discord.ui.TextInput(
                label="請輸入您的回覆內容",
                style=discord.TextStyle.paragraph,
                placeholder="在此輸入您的回覆...",
                required=True,
                max_length=2000
            )

            async def on_submit(self, modal_interaction: discord.Interaction):
                try:
                    embed = discord.Embed(title="來自開發者的回覆", color=discord.Color.green())
                    embed.add_field(name="回覆內容", value=self.reply_input.value, inline=False)
                    embed.timestamp = datetime.now(timezone.utc)
                    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
                    embed.set_footer(text="由於你先前提供了回饋而收到這則訊息。")
                    # find user id from original embed
                    original_embed = interaction.message.embeds[0] if interaction.message.embeds else None
                    if original_embed and len(original_embed.fields) > 0 and original_embed.fields[0].name == "使用者ID":
                        user_id = int(original_embed.fields[0].value)
                        user = await bot.fetch_user(user_id)
                        if user:
                            await user.send(embed=embed)
                            await modal_interaction.response.send_message("已成功發送回覆給使用者！", ephemeral=True)
                        else:
                            await modal_interaction.response.send_message("無法找到使用者，可能是使用者已刪除帳號或更改了隱私設定。", ephemeral=True)
                except Exception as e:
                    await modal_interaction.response.send_message(f"無法發送回覆，可能是使用者的隱私設定阻止了私訊：{e}", ephemeral=True)
        modal = FeedbackReplyModal()
        await interaction.response.send_modal(modal)

bot.add_view(ReplyFeedbackView())

@bot.tree.command(name="feedback", description="提供回饋給機器人開發者")
@app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
async def feedback_command(interaction: discord.Interaction):
    class FeedbackModal(discord.ui.Modal, title="提供回饋給機器人開發者"):
        feedback_input = discord.ui.TextInput(
            label="請輸入您的回饋內容",
            style=discord.TextStyle.paragraph,
            placeholder="在此輸入您的建議或回饋...",
            required=True,
            max_length=2000
        )

        async def on_submit(self, modal_interaction: discord.Interaction):
            feedback_message_channel_id = config("feedback_message_channel_id", None)
            if feedback_message_channel_id is None:
                await modal_interaction.response.send_message("無法找到頻道，請稍後再試。", ephemeral=True)
                return

            feedback_channel = bot.get_channel(int(feedback_message_channel_id))
            if feedback_channel is None:
                await modal_interaction.response.send_message("無法找到頻道，請稍後再試。", ephemeral=True)
                return

            embed = discord.Embed(title="新的使用者回饋", color=discord.Color.blue())
            embed.add_field(name="使用者ID", value=interaction.user.id, inline=False)
            embed.add_field(name="回饋內容", value=self.feedback_input.value, inline=False)
            embed.timestamp = datetime.now(timezone.utc)
            to_show_name = f"{interaction.user.display_name} ({interaction.user.name})" if interaction.user.display_name != interaction.user.name else interaction.user.name
            embed.set_author(name=to_show_name, icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
            # embed.set_footer(text=f"{interaction.user.display_name} ({interaction.user.name})", icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)

            try:
                await feedback_channel.send(embed=embed, view=ReplyFeedbackView())
                await modal_interaction.response.send_message(f"已成功送出，感謝您的回饋！\n建議加入官方支援群來回饋或是回報問題！\n{config('support_server_invite', 'https://discord.gg/your-support-server')}", ephemeral=True)
            except Exception as e:
                await modal_interaction.response.send_message("無法送出回饋，請稍後再試。", ephemeral=True)
    modal = FeedbackModal()
    await interaction.response.send_modal(modal)