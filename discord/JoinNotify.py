from globalenv import bot, get_command_mention, config, set_user_data, get_user_data
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from logger import log
import logging


async def get_update_channel() -> discord.TextChannel | None:
    try:
        channel_id = int(config("update_channel_id", 0))
    except (TypeError, ValueError):
        return None

    if channel_id <= 0:
        return None

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return None

    if not isinstance(channel, discord.TextChannel) or not channel.is_news():
        return None
    return channel


class UpdateSubscriptionChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            custom_id="join_notify_update_channel_select",
            placeholder="還是在其他頻道接收...",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        selected_channel = self.values[0] if self.values else None
        if selected_channel is not None and hasattr(selected_channel, "resolve"):
            selected_channel = selected_channel.resolve()
        if selected_channel is None and interaction.guild and self.values:
            selected_channel = interaction.guild.get_channel(self.values[0].id)

        await self.view.subscribe(interaction, selected_channel)


class UpdateSubscriptionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(UpdateSubscriptionChannelSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        permissions = getattr(interaction.user, "guild_permissions", None)
        if interaction.guild is None or permissions is None or not permissions.manage_guild:
            await interaction.response.send_message("只有具備管理伺服器權限的成員可以設定更新通知。", ephemeral=True)
            return False
        return True

    async def subscribe(self, interaction: discord.Interaction, destination):
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None or not isinstance(destination, discord.TextChannel):
            await interaction.followup.send("請選擇這個伺服器中的文字或公告頻道。", ephemeral=True)
            return

        bot_member = interaction.guild.me
        if bot_member is None:
            await interaction.followup.send("目前無法確認機器人在該伺服器的權限。", ephemeral=True)
            return

        permissions = destination.permissions_for(bot_member)
        if not (permissions.view_channel and permissions.manage_webhooks):
            await interaction.followup.send(
                "我需要在該頻道的查看頻道和管理 Webhook 權限。",
                ephemeral=True,
            )
            return

        update_channel = await get_update_channel()
        if update_channel is None:
            await interaction.followup.send("目前無法訂閱更新通知，請稍後再試。", ephemeral=True)
            return

        try:
            await update_channel.follow(
                destination=destination,
                reason=f"由 {interaction.user} ({interaction.user.id}) 訂閱機器人更新通知",
            )
        except discord.Forbidden:
            await interaction.followup.send("我沒有足夠權限在該頻道建立更新通知訂閱。", ephemeral=True)
            return
        except (discord.ClientException, discord.HTTPException) as error:
            log(
                f"訂閱更新通知失敗：{error}",
                level=logging.ERROR,
                module_name="JoinNotify",
                user=interaction.user,
                guild=interaction.guild,
            )
            await interaction.followup.send("訂閱更新通知時發生錯誤，請稍後再試。", ephemeral=True)
            return

        await interaction.followup.send(f"已在 {destination.mention} 接收機器人更新通知。", ephemeral=True)
        if interaction.message:
            try:
                await interaction.message.delete()
            except discord.HTTPException:
                pass
        log(
            f"已訂閱更新通知到 {destination.name} ({destination.id})",
            module_name="JoinNotify",
            user=interaction.user,
            guild=interaction.guild,
        )

    @discord.ui.button(
        label="好啊",
        style=discord.ButtonStyle.success,
        custom_id="join_notify_subscribe_updates_here",
        row=0,
    )
    async def subscribe_here(self, interaction: discord.Interaction, button: discord.ui.Button):
        destination = interaction.guild.get_channel(interaction.channel_id) if interaction.guild else None
        await self.subscribe(interaction, destination)

    @discord.ui.button(
        label="算了",
        style=discord.ButtonStyle.secondary,
        custom_id="join_notify_dismiss_update_subscription",
        row=0,
    )
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if interaction.message:
            try:
                await interaction.message.delete()
            except discord.HTTPException:
                pass


class JoinNotifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="官方網站", style=discord.ButtonStyle.link, url=config('website_url')))
        self.add_item(discord.ui.Button(label="使用文檔", style=discord.ButtonStyle.link, url=f"{config('website_url')}/docs"))
        self.add_item(discord.ui.Button(label="支援伺服器", style=discord.ButtonStyle.link, url=config('support_server_invite')))

    @discord.ui.button(label="停用加入通知", style=discord.ButtonStyle.secondary, custom_id="dont_notify_join")
    async def dont_notify_join(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="好吧",
            description="我不會再通知你了！如果你改變主意了，可以使用 `/joinnotify` 指令來重新啟用加入通知！",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        set_user_data(0, interaction.user.id, "join_notify", False)

class JoinNotify(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.persistent_views_registered = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self.persistent_views_registered:
            return
        bot.add_view(JoinNotifyView())
        bot.add_view(UpdateSubscriptionView())
        self.persistent_views_registered = True

    async def send_update_subscription_prompt(self, guild: discord.Guild, recipient: discord.abc.User):
        if "COMMUNITY" not in guild.features:
            return

        channel = guild.public_updates_channel
        bot_member = guild.me
        if channel is None or bot_member is None:
            return

        permissions = channel.permissions_for(bot_member)
        if not (permissions.view_channel and permissions.send_messages):
            return

        if await get_update_channel() is None:
            return

        try:
            await channel.send(
                f"{recipient.mention} 請問要在這裡接收機器人更新通知嗎？",
                view=UpdateSubscriptionView(),
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=[recipient]),
            )
        except discord.Forbidden:
            return
        except discord.HTTPException as error:
            log(
                f"發送更新通知訂閱詢問失敗：{error}",
                level=logging.ERROR,
                module_name="JoinNotify",
                user=recipient,
                guild=guild,
            )

    @app_commands.command(name="joinnotify", description="設定是否在你邀請我加入伺服器時私訊")
    @app_commands.choices(option=[
        app_commands.Choice(name="好啊", value="enable"),
        app_commands.Choice(name="不要", value="disable")
    ])
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def joinnotify(self, interaction: discord.Interaction, option: str):
        if option == "enable":
            set_user_data(0, interaction.user.id, "join_notify", True)
            await interaction.response.send_message("已啟用加入通知！當你邀請我加入伺服器時，我會私訊你一個歡迎訊息！", ephemeral=True)
        else:
            set_user_data(0, interaction.user.id, "join_notify", False)
            await interaction.response.send_message("已停用加入通知！當你邀請我加入伺服器時，我將不會私訊你任何訊息！", ephemeral=True)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        # try to find who invited the bot using the audit logs
        inviter = None
        try:
            async for entry in guild.audit_logs(limit=10, action=discord.AuditLogAction.bot_add):
                if entry.target.id == self.bot.user.id:
                    inviter = entry.user
                    break
        except (discord.Forbidden, discord.HTTPException):
            pass

        prompt_recipient = inviter or guild.owner
        if prompt_recipient:
            await self.send_update_subscription_prompt(guild, prompt_recipient)

        if inviter:
            if not get_user_data(0, inviter.id, "join_notify", True):
                return
            try:
                embed = discord.Embed(
                    title="欸？你好像邀請了我？",
                    description=f"你好啊，{inviter.mention}！感謝你邀請我加入 {guild.name}！\n快速開始：\n- {await get_command_mention('info', 'help')} 查看指令列表\n- {await get_command_mention('info', 'tutorial')} 查看使用教學\n- {await get_command_mention('panel')} 開啟網頁面板\n如果有任何問題，歡迎加入[支援伺服器]({config('support_server_invite')})尋求幫助！\n\n-# 不想收到這些訊息？你可以使用 {await get_command_mention('joinnotify')} 指令來關閉加入通知！",
                    color=discord.Color.green()
                )
                embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)
                view = JoinNotifyView()
                await inviter.send(embed=embed, view=view)
                log(f"找到了邀請者並私訊成功", module_name="JoinNotify", user=inviter, guild=guild)
            except discord.Forbidden:
                log("無法私訊邀請者，可能是因為他關閉了私訊或封鎖了我", level=logging.WARNING, module_name="JoinNotify", user=inviter, guild=guild)
        else:
            # dm the owner of the guild if we can't find the inviter
            owner = guild.owner
            if not get_user_data(0, owner.id, "join_notify", True):
                return
            try:
                embed = discord.Embed(
                    title="欸？好像有人邀請了我？",
                    description=f"你好啊，{owner.mention}！好像有人把我邀請進入你的伺服器 {guild.name} 了？但是我沒有權限可以知道他是誰 :/\n快速開始：\n- {await get_command_mention('info', 'help')} 查看指令列表\n- {await get_command_mention('info', 'tutorial')} 查看使用教學\n- {await get_command_mention('panel')} 開啟網頁面板\n如果有任何問題，歡迎加入[支援伺服器]({config('support_server_invite')})尋求幫助！\n\n-# 不想收到這些訊息？你可以使用 {await get_command_mention('joinnotify')} 指令來關閉加入通知！",
                    color=discord.Color.green()
                )
                embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)
                view = JoinNotifyView()
                await owner.send(embed=embed, view=view)
                log(f"找不到邀請者但私訊了伺服器擁有者成功", module_name="JoinNotify", user=owner, guild=guild)
            except discord.Forbidden:
                log("無法私訊伺服器擁有者，可能是因為他關閉了私訊或封鎖了我", level=logging.WARNING, module_name="JoinNotify", user=owner, guild=guild)

asyncio.run(bot.add_cog(JoinNotify(bot)))
