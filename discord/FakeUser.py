import discord
from discord import app_commands
from discord.ext import commands
from globalenv import bot, get_server_config, set_server_config, get_user_data, set_user_data
from typing import Union
from datetime import datetime, timezone
from logger import log
import asyncio


class FakeUser(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.command(name="fake-log-channel", description="設置假冒用戶紀錄的頻道")
    @app_commands.describe(channel="要設置的假冒用戶紀錄頻道，留空以查看當前頻道")
    async def fakeuser(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        guild_id = str(interaction.guild.id) if interaction.guild else None
        if channel:
            if channel.permissions_for(interaction.guild.me).send_messages is False:
                await interaction.response.send_message("機器人沒有在該頻道發送訊息的權限，請選擇其他頻道。", ephemeral=True)
                return
            set_server_config(guild_id, "fake_user_log_channel", channel.id)
            await interaction.response.send_message(f"假冒用戶紀錄頻道已設置為：{channel.mention}", ephemeral=True)
            log(f"設置假冒用戶紀錄頻道為 {channel} ({channel.id})", module_name="FakeUser", user=interaction.user, guild=interaction.guild)
        else:
            # remove the log channel
            set_server_config(guild_id, "fake_user_log_channel", None)
            await interaction.response.send_message("假冒用戶功能已被禁用。", ephemeral=True)
            log("禁用假冒用戶功能", module_name="FakeUser", user=interaction.user, guild=interaction.guild)
    
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="fake", description="假冒用戶說話")
    @app_commands.describe(user="要假冒的用戶", message="要發送的訊息內容")
    async def fake(self, interaction: discord.Interaction, user: Union[discord.User, discord.Member], message: str):
        await interaction.response.defer(ephemeral=True)
        if interaction.channel.permissions_for(interaction.guild.me).manage_webhooks is False:
            await interaction.followup.send("機器人沒有管理 Webhook 的權限，無法使用假冒用戶功能。", ephemeral=True)
            return
        user_last_used = get_user_data(0, str(interaction.user.id), "fake_rate_limit_last", None)
        if user_last_used:
            last_time = datetime.fromisoformat(user_last_used)
            if (datetime.now(timezone.utc) - last_time).total_seconds() < 30:
                log("假冒用戶速率限制觸發", module_name="FakeUser", user=interaction.user, guild=interaction.guild)
                await interaction.followup.send("你正在頻繁使用假冒用戶功能，請稍後再試。", ephemeral=True)
                return
        set_user_data(0, str(interaction.user.id), "fake_rate_limit_last", datetime.now(timezone.utc).isoformat())
        guild_id = str(interaction.guild.id) if interaction.guild else None
        log_channel_id = get_server_config(guild_id, "fake_user_log_channel")
        log_channel = interaction.guild.get_channel(log_channel_id) if interaction.guild and log_channel_id else None
        if not log_channel:
            await interaction.followup.send("假冒用戶功能未啟用，請聯繫管理員設置假冒用戶功能。", ephemeral=True)
            return
        
        user_blacklist = get_user_data(interaction.guild.id if interaction.guild else 0, user.id, "fake_user_blacklist", [])
        if str(interaction.user.id) in user_blacklist:
            await interaction.followup.send(f"看起來 {user} 不想要被你假冒，換一個人試試吧。", ephemeral=True)
            log(f"嘗試假冒被黑名單的用戶 {user}", module_name="FakeUser", user=interaction.user, guild=interaction.guild)
            return
        elif self.bot.user.id in user_blacklist:
            await interaction.followup.send(f"看起來 {user} 不想要被你假冒，換一個人試試吧。", ephemeral=True)
            log(f"嘗試假冒被黑名單的用戶 {user}", module_name="FakeUser", user=interaction.user, guild=interaction.guild)
            return

        webhook = await interaction.channel.create_webhook(name=user.name, reason=f"用戶 {interaction.user} 假冒 {user} 發送訊息")
        try:
            avatar_url = user.display_avatar or user.avatar or user.default_avatar
            await webhook.send(content=message, username=user.display_name, avatar_url=avatar_url.url, allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=False))
            await interaction.followup.send("訊息已發送。", ephemeral=True)
            log(f"假冒了用戶 {user} 發送訊息：{message}", module_name="FakeUser", user=interaction.user, guild=interaction.guild)
            if log_channel:
                embed = discord.Embed(title="假冒用戶操作紀錄", description=f"用戶 {interaction.user.mention} 假冒 {user.mention} 發送了訊息：{message}", color=discord.Color.red())
                embed.timestamp = datetime.now(timezone.utc)
                await log_channel.send(embed=embed)
        finally:
            await webhook.delete()
    
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="fake-blacklist", description="假冒用戶黑名單管理")
    @app_commands.describe(user="要加入或移除黑名單的用戶 (若指定本機器人代表所有人)")
    async def fake_blacklist(self, interaction: discord.Interaction, user: Union[discord.User, discord.Member]):
        guild_id = interaction.guild.id if interaction.guild else None
        blacklist = get_user_data(guild_id, interaction.user.id, "fake_user_blacklist", [])
        if str(user.id) in blacklist:
            blacklist.remove(str(user.id))
            action = "移除"
        else:
            blacklist.append(str(user.id))
            action = "加入"
        set_user_data(guild_id, interaction.user.id, "fake_user_blacklist", blacklist)
        if user.id == self.bot.user.id:
            await interaction.response.send_message(f"所有人已被{action}你的假冒用戶黑名單。", ephemeral=True)
        else:
            await interaction.response.send_message(f"用戶 {user.mention} 已被{action}你的假冒用戶黑名單。", ephemeral=True)
        log(f"{action}用戶 {user} ({user.id}) 至假冒用戶黑名單", module_name="FakeUser", user=interaction.user, guild=interaction.guild)


asyncio.run(bot.add_cog(FakeUser(bot)))