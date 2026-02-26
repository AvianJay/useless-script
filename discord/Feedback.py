import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, config
import asyncio
from datetime import datetime, timezone


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
                await feedback_channel.send(embed=embed)
                await modal_interaction.response.send_message("已成功送出，感謝您的回饋！", ephemeral=True)
            except Exception as e:
                await modal_interaction.response.send_message("無法送出回饋，請稍後再試。", ephemeral=True)
    modal = FeedbackModal()
    await interaction.response.send_modal(modal)