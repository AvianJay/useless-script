import discord
from discord import app_commands
from discord.ext import commands
from globalenv import bot, get_server_config, set_server_config, get_user_data, set_user_data
from typing import Union
from datetime import datetime, timezone
from logger import log
import asyncio
import re
import i18n
from i18n import t


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


def is_antibeast_enabled(guild: discord.Guild | None) -> bool:
    if guild is None:
        return False

    config = get_server_config(guild.id, "antibeast", {})
    return isinstance(config, dict) and bool(config.get("enabled", False))


def can_send_mass_mentions(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not interaction.channel:
        return False

    channel = interaction.channel
    if not channel.permissions_for(interaction.user).mention_everyone:
        return False
    if not channel.permissions_for(interaction.guild.me).mention_everyone:
        return False

    if is_antibeast_enabled(interaction.guild):
        return interaction.user.guild_permissions.manage_guild
    return True


class ConfirmMentionsView(i18n.I18nView):
    def __init__(self, user: Union[discord.User, discord.Member], message: str, interaction: discord.Interaction):
        super().__init__(timeout=30)
        self.user = user
        self.message = message
        self.result = None
        self.interaction = interaction

    async def on_timeout(self):
        if self.result is None:
            with i18n.use_locale(i18n.resolve_locale(user_id=self.user.id)):
                content = t("fakeuser.msg.confirm_timed_out")
            await self.interaction.message.edit(content=content, view=None)
            self.result = False

    @discord.ui.button(label=i18n.K("fakeuser.btn.confirm_send"), style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.user:
            await interaction.response.send_message(t("fakeuser.err.not_your_button"), ephemeral=True)
            return
        self.result = True
        await interaction.response.edit_message(content=t("fakeuser.msg.confirmed_with_mentions"), view=None)
        self.stop()

    @discord.ui.button(label=i18n.K("fakeuser.btn.send_without_mentions"), style=discord.ButtonStyle.secondary)
    async def send_without_mentions(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.user:
            await interaction.response.send_message(t("fakeuser.err.not_your_button"), ephemeral=True)
            return
        self.result = False
        await interaction.response.edit_message(content=t("fakeuser.msg.confirmed_without_mentions"), view=None)
        self.stop()

    @discord.ui.button(label=i18n.K("fakeuser.btn.cancel_edit"), style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.user:
            await interaction.response.send_message(t("fakeuser.err.not_your_button"), ephemeral=True)
            return
        self.result = None
        await interaction.response.edit_message(content=t("fakeuser.msg.send_cancelled"), view=None)
        self.stop()


class FakeUser(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name=app_commands.locale_str("fake", i18n_key="cmd.fakeuser.fake.name"), description=app_commands.locale_str("Speak as another user", i18n_key="cmd.fakeuser.fake.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to impersonate", i18n_key="cmd.fakeuser.fake.param.user"), message=app_commands.locale_str("The message to send", i18n_key="cmd.fakeuser.fake.param.message"))
    async def fake(self, interaction: discord.Interaction, user: Union[discord.User, discord.Member], message: str):
        await interaction.response.defer(ephemeral=True)
        if interaction.channel.permissions_for(interaction.guild.me).manage_webhooks is False:
            await interaction.followup.send(t("fakeuser.err.no_webhook_permission"), ephemeral=True)
            return
        user_last_used = get_user_data(0, str(interaction.user.id), "fake_rate_limit_last", None)
        if user_last_used:
            last_time = datetime.fromisoformat(user_last_used)
            if (datetime.now(timezone.utc) - last_time).total_seconds() < 30:
                log("Fake-user rate limit triggered", module_name="FakeUser", user=interaction.user, guild=interaction.guild)
                await interaction.followup.send(t("fakeuser.err.rate_limited"), ephemeral=True)
                return
        set_user_data(0, str(interaction.user.id), "fake_rate_limit_last", datetime.now(timezone.utc).isoformat())
        guild_id = str(interaction.guild.id) if interaction.guild else None
        log_channel_id = get_server_config(guild_id, "fake_user_log_channel")
        log_channel = interaction.guild.get_channel(log_channel_id) if interaction.guild and log_channel_id else None
        if not log_channel:
            await interaction.followup.send(t("fakeuser.err.not_enabled"), ephemeral=True)
            return
        
        user_blacklist = get_user_data(interaction.guild.id if interaction.guild else 0, user.id, "fake_user_blacklist", [])
        if str(interaction.user.id) in user_blacklist:
            await interaction.followup.send(t("fakeuser.err.target_blacklisted", user=user), ephemeral=True)
            log(f"Attempted to impersonate blacklisted user {user}", module_name="FakeUser", user=interaction.user, guild=interaction.guild)
            return
        elif str(self.bot.user.id) in user_blacklist:
            await interaction.followup.send(t("fakeuser.err.target_blacklisted", user=user), ephemeral=True)
            log(f"Attempted to impersonate blacklisted user {user}", module_name="FakeUser", user=interaction.user, guild=interaction.guild)
            return

        message = message.replace("\\n", "\n")  # 允許使用 \n 來換行

        if filter_checker(message, interaction.guild):
            await interaction.followup.send(t("fakeuser.err.filter_triggered"), ephemeral=True)
            if log_channel:
                embed = discord.Embed(title=t("fakeuser.embed.log_title"), description=t("fakeuser.value.filtered_log_desc", moderator=interaction.user.mention, target=user.mention, message=message), color=discord.Color.red())
                embed.timestamp = datetime.now(timezone.utc)
                await log_channel.send(embed=embed)

            log(f"Message triggered filter; refused to send fake-user message as {user}: {message}", module_name="FakeUser", user=interaction.user, guild=interaction.guild)
            return

        # {t:n} allow multiple messages
        # n: delay next message in seconds (>=0 <=2, supports float)
        # if only {t} then delay is 0
        # example: hello {t:1}world{t:2}!!! will send "hello " then after 1 second "world" then after 2 seconds "!!!"
        
        # limit 3 messages (2 delays) to prevent abuse
        delay_pattern = r"\{t(?::((?:\d+(?:\.\d+)?)|(?:\.\d+)))?\}"
        delays = re.findall(delay_pattern, message)
        if len(delays) > 2:
            await interaction.followup.send(t("fakeuser.err.too_many_delays"), ephemeral=True)
            return

        if re.search(r"\{t(?:(?:\d+(?:\.\d+)?)|(?:\.\d+))?\}\s*$", message):
            await interaction.followup.send(t("fakeuser.err.delay_needs_content"), ephemeral=True)
            return

        x = re.split(delay_pattern, message, maxsplit=2)
        message_chunks = [x[0]]
        chunk_delays = []
        for i in range(1, len(x), 2):
            delay = float(x[i]) if x[i] else 0.0
            if delay < 0 or delay > 2:
                await interaction.followup.send(t("fakeuser.err.delay_out_of_range"), ephemeral=True)
                return
            chunk_delays.append(delay)
            message_chunks.append(x[i + 1])

        send_plan = []
        for i, chunk in enumerate(message_chunks):
            if not chunk.strip():
                continue
            delay_before_send = 0 if i == 0 else chunk_delays[i - 1]
            send_plan.append((delay_before_send, chunk))

        if not send_plan:
            await interaction.followup.send(t("fakeuser.err.empty_content"), ephemeral=True)
            return
        

        mention = False

        if check_mentions(message):
            if can_send_mass_mentions(interaction):
                # ask user if they really want to send a message with mentions
                view = ConfirmMentionsView(interaction.user, message, interaction)
                await interaction.followup.send(t("fakeuser.msg.confirm_mentions_prompt"), view=view, ephemeral=True)
                await view.wait()
                if view.result is None:
                    # await interaction.followup.send("訊息發送已取消。", ephemeral=True)
                    return
                mention = view.result

        webhook = await interaction.channel.create_webhook(name=user.name, reason=t("fakeuser.audit.impersonate_reason", moderator=interaction.user, target=user))
        try:
            avatar_url = user.display_avatar or user.avatar or user.default_avatar
            for delay_before_send, chunk in send_plan:
                if delay_before_send > 0:
                    await asyncio.sleep(delay_before_send)
                await webhook.send(content=chunk, username=user.display_name, avatar_url=avatar_url.url, allowed_mentions=discord.AllowedMentions(everyone=mention, users=True, roles=mention))

            history_scope_id = interaction.guild.id if interaction.guild else 0
            fake_history = get_user_data(history_scope_id, user.id, "fakeuser_history", [])
            if not isinstance(fake_history, list):
                fake_history = []
            fake_history.append({
                "user": interaction.user.id,
                "content": "\n".join(chunk for _, chunk in send_plan),
            })
            fake_history = fake_history[-10:]
            set_user_data(history_scope_id, user.id, "fakeuser_history", fake_history)

            await interaction.followup.send(t("fakeuser.msg.sent", count=len(send_plan)), ephemeral=True)
            log(f"Impersonated user {user} to send a message: {message}", module_name="FakeUser", user=interaction.user, guild=interaction.guild)
            if log_channel:
                embed = discord.Embed(title=t("fakeuser.embed.log_title"), description=t("fakeuser.value.sent_log_desc", moderator=interaction.user.mention, target=user.mention, message=message), color=discord.Color.red())
                embed.timestamp = datetime.now(timezone.utc)
                await log_channel.send(embed=embed)
        finally:
            await webhook.delete()
    
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name=app_commands.locale_str("fake-blacklist", i18n_key="cmd.fakeuser.fake_blacklist.name"), description=app_commands.locale_str("Manage the fake-user blacklist", i18n_key="cmd.fakeuser.fake_blacklist.desc"))
    @app_commands.describe(user=app_commands.locale_str("User to add/remove from the blacklist (pick this bot to mean everyone)", i18n_key="cmd.fakeuser.fake_blacklist.param.user"))
    async def fake_blacklist(self, interaction: discord.Interaction, user: Union[discord.User, discord.Member]):
        guild_id = interaction.guild.id if interaction.guild else None
        blacklist = get_user_data(guild_id, interaction.user.id, "fake_user_blacklist", [])
        if str(user.id) in blacklist:
            blacklist.remove(str(user.id))
            added = False
        else:
            blacklist.append(str(user.id))
            added = True
        set_user_data(guild_id, interaction.user.id, "fake_user_blacklist", blacklist)
        if user.id == self.bot.user.id:
            await interaction.response.send_message(t("fakeuser.msg.blacklist_everyone_toggled", action=t("fakeuser.value.blacklist_added") if added else t("fakeuser.value.blacklist_removed")), ephemeral=True)
        else:
            await interaction.response.send_message(t("fakeuser.msg.blacklist_toggled", user=user.mention, action=t("fakeuser.value.blacklist_added") if added else t("fakeuser.value.blacklist_removed")), ephemeral=True)
        log(f"{'Added' if added else 'Removed'} user {user} ({user.id}) {'to' if added else 'from'} the fake-user blacklist", module_name="FakeUser", user=interaction.user, guild=interaction.guild)


asyncio.run(bot.add_cog(FakeUser(bot)))

@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.default_permissions(manage_guild=True)
class FakeAdmin(commands.GroupCog, name=app_commands.locale_str("fake-admin", i18n_key="cmd.fakeuser.fake_admin.root.name"), description=app_commands.locale_str("Fake-user admin commands", i18n_key="cmd.fakeuser.fake_admin.root.desc")):
    def __init__(self, bot):
        self.bot = bot

    async def filter_autocomplete(self, interaction: discord.Interaction, current: str):
        guild_id = str(interaction.guild.id) if interaction.guild else None
        filters = get_server_config(guild_id, "fake_user_filters", [])
        return [app_commands.Choice(name=f, value=f) for f in filters if current.lower() in f.lower()]

    @app_commands.command(name=app_commands.locale_str("filter", i18n_key="cmd.fakeuser.fake_admin.filter.name"), description=app_commands.locale_str("Configure filters for the fake-user feature", i18n_key="cmd.fakeuser.fake_admin.filter.desc"))
    @app_commands.describe(mode=app_commands.locale_str("What do you want to do?", i18n_key="cmd.fakeuser.fake_admin.filter.param.mode"), regex=app_commands.locale_str("Regex to filter; only needed for add or remove", i18n_key="cmd.fakeuser.fake_admin.filter.param.regex"))
    @app_commands.choices(mode=[
        app_commands.Choice(name=app_commands.locale_str("Add filter", i18n_key="cmd.fakeuser.fake_admin.filter.choice.add"), value="add"),
        app_commands.Choice(name=app_commands.locale_str("Remove filter", i18n_key="cmd.fakeuser.fake_admin.filter.choice.remove"), value="remove"),
        app_commands.Choice(name=app_commands.locale_str("View filters", i18n_key="cmd.fakeuser.fake_admin.filter.choice.view"), value="view")
    ])
    @app_commands.autocomplete(regex=filter_autocomplete)
    async def filter(self, interaction: discord.Interaction, mode: str, regex: str = None):
        guild_id = str(interaction.guild.id) if interaction.guild else None
        filters = get_server_config(guild_id, "fake_user_filters", [])
        if mode == "add":
            if not regex:
                await interaction.response.send_message(t("fakeuser.err.regex_required_add"), ephemeral=True)
                return
            if regex in filters:
                await interaction.response.send_message(t("fakeuser.err.filter_already_exists"), ephemeral=True)
                return
            # 簡單驗證正則表達式是否有效
            try:
                re.compile(regex)
            except re.error:
                await interaction.response.send_message(t("fakeuser.err.invalid_regex"), ephemeral=True)
                return
            filters.append(regex)
            set_server_config(guild_id, "fake_user_filters", filters)
            await interaction.response.send_message(t("fakeuser.msg.filter_added", regex=regex), ephemeral=True)
            log(f"Added fake-user filter `{regex}`", module_name="FakeAdmin", user=interaction.user, guild=interaction.guild)
        elif mode == "remove":
            if not regex:
                await interaction.response.send_message(t("fakeuser.err.regex_required_remove"), ephemeral=True)
                return
            if regex not in filters:
                await interaction.response.send_message(t("fakeuser.err.filter_not_found"), ephemeral=True)
                return
            filters.remove(regex)
            set_server_config(guild_id, "fake_user_filters", filters)
            await interaction.response.send_message(t("fakeuser.msg.filter_removed", regex=regex), ephemeral=True)
            log(f"Removed fake-user filter `{regex}`", module_name="FakeAdmin", user=interaction.user, guild=interaction.guild)
        else:
            if not filters:
                await interaction.response.send_message(t("fakeuser.value.no_filters"), ephemeral=True)
                return
            filter_list = "\n".join(f"- `{f}`" for f in filters)
            await interaction.response.send_message(t("fakeuser.value.current_filters", filter_list=filter_list), ephemeral=True)

    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.command(name=app_commands.locale_str("log-channel", i18n_key="cmd.fakeuser.fake_admin.log_channel.name"), description=app_commands.locale_str("Set the fake-user log channel", i18n_key="cmd.fakeuser.fake_admin.log_channel.desc"))
    @app_commands.describe(channel=app_commands.locale_str("The log channel to set; leave empty to view the current one", i18n_key="cmd.fakeuser.fake_admin.log_channel.param.channel"))
    async def fakeuser(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        guild_id = str(interaction.guild.id) if interaction.guild else None
        if channel:
            if channel.permissions_for(interaction.guild.me).send_messages is False:
                await interaction.response.send_message(t("fakeuser.err.no_send_permission"), ephemeral=True)
                return
            set_server_config(guild_id, "fake_user_log_channel", channel.id)
            await interaction.response.send_message(t("fakeuser.msg.log_channel_set", channel=channel.mention), ephemeral=True)
            log(f"Set fake-user log channel to {channel} ({channel.id})", module_name="FakeUser", user=interaction.user, guild=interaction.guild)
        else:
            # remove the log channel
            set_server_config(guild_id, "fake_user_log_channel", None)
            await interaction.response.send_message(t("fakeuser.msg.disabled"), ephemeral=True)
            log("Disabled the fake-user feature", module_name="FakeUser", user=interaction.user, guild=interaction.guild)

asyncio.run(bot.add_cog(FakeAdmin(bot)))
