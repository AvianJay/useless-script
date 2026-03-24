import discord
from discord import app_commands
from discord.ext import commands
from globalenv import bot, get_server_config, set_server_config, get_user_data, set_user_data
from typing import Union
from datetime import datetime, timezone
from logger import log
import asyncio
import re


def filter_checker(content: str, guild: discord.Guild) -> bool:
    filters = get_server_config(str(guild.id), "fake_user_filters", [])
    for f in filters:
        try:
            if re.search(f, content):
                return True
        except re.error:
            continue
    return False


def check_mentions(content: str) -> bool:
    # 簡單檢查是否有 @everyone、@here 或 <@&role_id> 這類的提及
    if "@everyone" in content or "@here" in content:
        return True
    if re.search(r"<@&\d+>", content):
        return True
    return False


class ConfirmMentionsView(discord.ui.View):
    def __init__(self, user: Union[discord.User, discord.Member], message: str, interaction: discord.Interaction):
        super().__init__(timeout=30)
        self.user = user
        self.message = message
        self.result = None
        self.interaction = interaction

    async def on_timeout(self):
        if self.result is None:
            await self.interaction.message.edit(content="你沒有在時間內確認，訊息發送已取消。", view=None)
            self.result = False

    @discord.ui.button(label="是的，我確定要發送這條訊息", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.user:
            await interaction.response.send_message("這不是你的確認按鈕！", ephemeral=True)
            return
        self.result = True
        await interaction.edit_original_response(content="你已確認要發送包含提及的訊息。", view=None)
        self.stop()

    @discord.ui.button(label="不，我想修改一下訊息", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.user:
            await interaction.response.send_message("這不是你的確認按鈕！", ephemeral=True)
            return
        self.result = False
        await interaction.edit_original_response(content="訊息發送已取消。", view=None)
        self.stop()


class FakeUser(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
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
        elif str(self.bot.user.id) in user_blacklist:
            await interaction.followup.send(f"看起來 {user} 不想要被你假冒，換一個人試試吧。", ephemeral=True)
            log(f"嘗試假冒被黑名單的用戶 {user}", module_name="FakeUser", user=interaction.user, guild=interaction.guild)
            return

        if filter_checker(message, interaction.guild):
            await interaction.followup.send("你的訊息內容觸發了過濾器，請修改後再試。", ephemeral=True)
            if log_channel:
                embed = discord.Embed(title="假冒用戶操作紀錄", description=f"用戶 {interaction.user.mention} 假冒 {user.mention} 發送了訊息被過濾：{message}", color=discord.Color.red())
                embed.timestamp = datetime.now(timezone.utc)
                await log_channel.send(embed=embed)

            log(f"訊息觸發過濾器，拒絕假冒用戶 {user} 發送訊息：{message}", module_name="FakeUser", user=interaction.user, guild=interaction.guild)
            return

        mention = False

        if check_mentions(message):
            if interaction.channel.permissions_for(interaction.guild.me).mention_everyone:
                # ask user if they really want to send a message with mentions
                view = ConfirmMentionsView(interaction.user, message, interaction)
                await interaction.followup.send("你的訊息中包含了提及，這可能會通知到很多人。你確定要發送這條訊息嗎？", view=view, ephemeral=True)
                await view.wait()
                if not view.result:
                    # await interaction.followup.send("訊息發送已取消。", ephemeral=True)
                    return
                mention = True

        webhook = await interaction.channel.create_webhook(name=user.name, reason=f"用戶 {interaction.user} 假冒 {user} 發送訊息")
        try:
            avatar_url = user.display_avatar or user.avatar or user.default_avatar
            await webhook.send(content=message, username=user.display_name, avatar_url=avatar_url.url, allowed_mentions=discord.AllowedMentions(everyone=mention, users=True, roles=mention))
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

@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.default_permissions(manage_guild=True)
class FakeAdmin(commands.GroupCog, name="fake-admin", description="假冒用戶管理指令"):
    def __init__(self, bot):
        self.bot = bot

    async def filter_autocomplete(self, interaction: discord.Interaction, current: str):
        guild_id = str(interaction.guild.id) if interaction.guild else None
        filters = get_server_config(guild_id, "fake_user_filters", [])
        return [app_commands.Choice(name=f, value=f) for f in filters if current.lower() in f.lower()]

    @app_commands.command(name="filter", description="設定假冒用戶功能的過濾器")
    @app_commands.describe(mode="要做什麼？", regex="要過濾的正則表達式，僅在選擇添加或移除模式時需要")
    @app_commands.choices(mode=[
        app_commands.Choice(name="添加過濾器", value="add"),
        app_commands.Choice(name="移除過濾器", value="remove"),
        app_commands.Choice(name="查看過濾器", value="view")
    ])
    @app_commands.autocomplete(regex=filter_autocomplete)
    async def filter(self, interaction: discord.Interaction, mode: str, regex: str = None):
        guild_id = str(interaction.guild.id) if interaction.guild else None
        filters = get_server_config(guild_id, "fake_user_filters", [])
        if mode == "add":
            if not regex:
                await interaction.response.send_message("請提供要添加的正則表達式。", ephemeral=True)
                return
            if regex in filters:
                await interaction.response.send_message("該過濾器已存在。", ephemeral=True)
                return
            # 簡單驗證正則表達式是否有效
            try:
                re.compile(regex)
            except re.error:
                await interaction.response.send_message("提供的正則表達式無效，請檢查後再試。", ephemeral=True)
                return
            filters.append(regex)
            set_server_config(guild_id, "fake_user_filters", filters)
            await interaction.response.send_message(f"已添加過濾器：`{regex}`", ephemeral=True)
            log(f"添加假冒用戶過濾器 `{regex}`", module_name="FakeAdmin", user=interaction.user, guild=interaction.guild)
        elif mode == "remove":
            if not regex:
                await interaction.response.send_message("請提供要移除的正則表達式。", ephemeral=True)
                return
            if regex not in filters:
                await interaction.response.send_message("該過濾器不存在。", ephemeral=True)
                return
            filters.remove(regex)
            set_server_config(guild_id, "fake_user_filters", filters)
            await interaction.response.send_message(f"已移除過濾器：`{regex}`", ephemeral=True)
            log(f"移除假冒用戶過濾器 `{regex}`", module_name="FakeAdmin", user=interaction.user, guild=interaction.guild)
        else:
            if not filters:
                await interaction.response.send_message("目前沒有設置任何過濾器。", ephemeral=True)
                return
            filter_list = "\n".join(f"- `{f}`" for f in filters)
            await interaction.response.send_message(f"目前的假冒用戶過濾器有：\n{filter_list}", ephemeral=True)

    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.command(name="log-channel", description="設置假冒用戶紀錄的頻道")
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

asyncio.run(bot.add_cog(FakeAdmin(bot)))
