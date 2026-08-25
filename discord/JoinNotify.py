from globalenv import bot, get_command_mention, config, set_user_data, get_user_data
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from logger import log
import logging

import i18n
from i18n import t


async def find_bot_inviter(guild: discord.Guild, bot_user_id: int | None = None):
    """Return the user who added the bot, when the audit log is available."""
    target_bot_id = bot_user_id or (bot.user.id if bot.user else None)
    if target_bot_id is None:
        return None

    try:
        me = getattr(guild, "me", None)
        permissions = getattr(me, "guild_permissions", None)
        if permissions is not None and not permissions.view_audit_log:
            return None
        async for entry in guild.audit_logs(limit=10, action=discord.AuditLogAction.bot_add):
            target = getattr(entry, "target", None)
            if target is not None and target.id == target_bot_id:
                return entry.user
    except (discord.Forbidden, discord.HTTPException):
        pass
    return None


async def get_join_prompt_recipient(guild: discord.Guild, bot_user_id: int | None = None):
    return await find_bot_inviter(guild, bot_user_id) or guild.owner


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
            placeholder=i18n.K("joinnotify.select.other_channel_ph"),
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


class UpdateSubscriptionView(i18n.I18nView):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(UpdateSubscriptionChannelSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        permissions = getattr(interaction.user, "guild_permissions", None)
        if interaction.guild is None or permissions is None or not permissions.manage_guild:
            await interaction.response.send_message(t("joinnotify.err.need_manage_guild"), ephemeral=True)
            return False
        return True

    async def subscribe(self, interaction: discord.Interaction, destination):
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None or not isinstance(destination, discord.TextChannel):
            await interaction.followup.send(t("joinnotify.err.pick_channel_in_guild"), ephemeral=True)
            return

        bot_member = interaction.guild.me
        if bot_member is None:
            await interaction.followup.send(t("joinnotify.err.cannot_check_perms"), ephemeral=True)
            return

        permissions = destination.permissions_for(bot_member)
        if not (permissions.view_channel and permissions.manage_webhooks):
            await interaction.followup.send(
                t("joinnotify.err.missing_channel_perms"),
                ephemeral=True,
            )
            return

        update_channel = await get_update_channel()
        if update_channel is None:
            await interaction.followup.send(t("joinnotify.err.updates_unavailable"), ephemeral=True)
            return

        try:
            await update_channel.follow(
                destination=destination,
                reason=t("joinnotify.audit.subscribed",
                         locale=i18n.resolve_locale(guild_id=interaction.guild.id),
                         user=f"{interaction.user} ({interaction.user.id})"),
            )
        except discord.Forbidden:
            await interaction.followup.send(t("joinnotify.err.follow_forbidden"), ephemeral=True)
            return
        except (discord.ClientException, discord.HTTPException) as error:
            log(
                f"Failed to subscribe to update notifications: {error}",
                level=logging.ERROR,
                module_name="JoinNotify",
                user=interaction.user,
                guild=interaction.guild,
            )
            await interaction.followup.send(t("joinnotify.err.subscribe_failed"), ephemeral=True)
            return

        await interaction.followup.send(t("joinnotify.msg.subscribed", channel=destination.mention), ephemeral=True)
        if interaction.message:
            try:
                await interaction.message.delete()
            except discord.HTTPException:
                pass
        log(
            f"Subscribed to update notifications in {destination.name} ({destination.id})",
            module_name="JoinNotify",
            user=interaction.user,
            guild=interaction.guild,
        )

    @discord.ui.button(
        label=i18n.K("joinnotify.btn.subscribe_here"),
        style=discord.ButtonStyle.success,
        custom_id="join_notify_subscribe_updates_here",
        row=0,
    )
    async def subscribe_here(self, interaction: discord.Interaction, button: discord.ui.Button):
        destination = interaction.guild.get_channel(interaction.channel_id) if interaction.guild else None
        await self.subscribe(interaction, destination)

    @discord.ui.button(
        label=i18n.K("joinnotify.btn.dismiss"),
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


class JoinNotifyView(i18n.I18nView):
    def __init__(self):
        super().__init__(timeout=None)
        # 這三個是 instance 建立時求值，語言已經正確，不需要 K()
        self.add_item(discord.ui.Button(label=t("joinnotify.btn.website"), style=discord.ButtonStyle.link, url=config('website_url')))
        self.add_item(discord.ui.Button(label=t("joinnotify.btn.docs"), style=discord.ButtonStyle.link, url=f"{config('website_url')}/docs"))
        self.add_item(discord.ui.Button(label=t("joinnotify.btn.support"), style=discord.ButtonStyle.link, url=config('support_server_invite')))

    @discord.ui.button(label=i18n.K("joinnotify.btn.disable_join_notify"), style=discord.ButtonStyle.secondary, custom_id="dont_notify_join")
    async def dont_notify_join(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title=t("joinnotify.notify_off.title"),
            description=t("joinnotify.notify_off.desc", command=await get_command_mention("joinnotify") or "`/joinnotify`"),
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
            async with i18n.guild_scope(guild.id):
                await channel.send(
                    recipient.mention + " " + t("joinnotify.msg.update_prompt"),
                    view=UpdateSubscriptionView(),
                    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=[recipient]),
                )
        except discord.Forbidden:
            return
        except discord.HTTPException as error:
            log(
                f"Failed to send the update subscription prompt: {error}",
                level=logging.ERROR,
                module_name="JoinNotify",
                user=recipient,
                guild=guild,
            )

    @staticmethod
    async def _quick_start_body() -> str:
        """兩段歡迎私訊共用的快速開始說明。"""
        return t("joinnotify.dm.body",
                 help_cmd=await get_command_mention("info", "help"),
                 tutorial_cmd=await get_command_mention("info", "tutorial"),
                 panel_cmd=await get_command_mention("panel"),
                 support_url=config("support_server_invite"),
                 joinnotify_cmd=await get_command_mention("joinnotify"))

    @app_commands.command(name=app_commands.locale_str("joinnotify", i18n_key="cmd.joinnotify.joinnotify.name"), description=app_commands.locale_str("Choose whether I DM you when you invite me to a server", i18n_key="cmd.joinnotify.joinnotify.desc"))
    @app_commands.choices(option=[
        app_commands.Choice(name=app_commands.locale_str("Sure", i18n_key="cmd.joinnotify.joinnotify.choice.enable"), value="enable"),
        app_commands.Choice(name=app_commands.locale_str("No thanks", i18n_key="cmd.joinnotify.joinnotify.choice.disable"), value="disable")
    ])
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def joinnotify(self, interaction: discord.Interaction, option: str):
        if option == "enable":
            set_user_data(0, interaction.user.id, "join_notify", True)
            await interaction.response.send_message(t("joinnotify.msg.enabled"), ephemeral=True)
        else:
            set_user_data(0, interaction.user.id, "join_notify", False)
            await interaction.response.send_message(t("joinnotify.msg.disabled"), ephemeral=True)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        inviter = await find_bot_inviter(guild, self.bot.user.id)

        prompt_recipient = inviter or guild.owner
        if prompt_recipient:
            await self.send_update_subscription_prompt(guild, prompt_recipient)

        if inviter:
            if not get_user_data(0, inviter.id, "join_notify", True):
                return
            try:
                with i18n.use_locale(i18n.resolve_locale(user_id=inviter.id)):
                    embed = discord.Embed(
                        title=t("joinnotify.dm.title_inviter"),
                        description=t("joinnotify.dm.greeting_inviter", user=inviter.mention, guild=guild.name)
                        + "\n" + await self._quick_start_body(),
                        color=discord.Color.green()
                    )
                    embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)
                    view = JoinNotifyView()
                    await inviter.send(embed=embed, view=view)
                log("Found the inviter and DMed them successfully", module_name="JoinNotify", user=inviter, guild=guild)
            except discord.Forbidden:
                log("Couldn't DM the inviter; they may have DMs closed or have blocked me", level=logging.WARNING, module_name="JoinNotify", user=inviter, guild=guild)
        else:
            # dm the owner of the guild if we can't find the inviter
            owner = guild.owner
            if not get_user_data(0, owner.id, "join_notify", True):
                return
            try:
                with i18n.use_locale(i18n.resolve_locale(user_id=owner.id)):
                    embed = discord.Embed(
                        title=t("joinnotify.dm.title_unknown"),
                        description=t("joinnotify.dm.greeting_unknown", user=owner.mention, guild=guild.name)
                        + "\n" + await self._quick_start_body(),
                        color=discord.Color.green()
                    )
                    embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)
                    view = JoinNotifyView()
                    await owner.send(embed=embed, view=view)
                log("Couldn't find the inviter but DMed the guild owner successfully", module_name="JoinNotify", user=owner, guild=guild)
            except discord.Forbidden:
                log("Couldn't DM the guild owner; they may have DMs closed or have blocked me", level=logging.WARNING, module_name="JoinNotify", user=owner, guild=guild)

asyncio.run(bot.add_cog(JoinNotify(bot)))
