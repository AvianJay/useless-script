import time
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta, timezone
from globalenv import bot, start_bot, get_server_config, set_server_config, get_user_data, set_user_data
from logger import log
import logging
import asyncio

import i18n
from i18n import t


ignore = {}
IGNORE_WINDOW_SECONDS = 30

def ignore_user(user_id: int):
    ignore[user_id] = time.time()

def is_ignored(user_id: int) -> bool:
    # 避免重複觸發通知
    ts = ignore.get(user_id)
    if ts is None:
        return False
    if time.time() - ts > IGNORE_WINDOW_SECONDS:
        ignore.pop(user_id, None)
        return False
    return True



# notify_user 的 action 參數輸入語法：其他模組（Moderate 等）以中文動作詞
# 呼叫，這裡正規化成英文 key 後再依收件人語言渲染。中文詞永久接受。
ch2en_map = {  # i18n: skip
    "踢出": "kick",
    "封禁": "ban",
    "禁言": "mute",
    "黑名單": "blacklist",
}

_KNOWN_ACTIONS = ("kick", "ban", "mute", "blacklist")


class ResponseAppealView(i18n.I18nView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label=i18n.K("moderationnotify.btn.respond_appeal"), style=discord.ButtonStyle.primary, emoji="⚖️", custom_id="response_appeal_button")
    async def response_appeal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        origself = self
        class ResponseAppealModal(discord.ui.Modal, title=t("moderationnotify.modal.respond_title")):
            response = discord.ui.TextInput(label=t("moderationnotify.modal.respond_label"), style=discord.TextStyle.paragraph, required=True, max_length=1000)
            can_appeal = discord.ui.TextInput(label=t("moderationnotify.modal.can_appeal_label"), style=discord.TextStyle.short, required=True, max_length=3)

            async def on_submit(self, modal_interaction: discord.Interaction):
                # 輸入語法：是 / y / yes 皆代表允許（中英文永久接受）
                raw_can_appeal = self.can_appeal.value.strip().lower()
                can_appeal = raw_can_appeal == "是" or raw_can_appeal.startswith("y")  # i18n: skip
                message = interaction.message  # 直接使用 interaction.message
                user_id = int(message.embeds[0].fields[0].value)  # 機器約定：fields[0] 是用戶 ID
                user = await bot.fetch_user(user_id)  # 獲取用戶對象
                # 申訴回覆發到用戶私訊，以收件人語言渲染
                loc = i18n.resolve_locale(user_id=user_id, guild_id=modal_interaction.guild.id if modal_interaction.guild else None)
                yes_no = t("common.state.yes", locale=loc) if can_appeal else t("common.state.no", locale=loc)
                embed = discord.Embed(
                    title=t("moderationnotify.embed.appeal_reply_title", locale=loc),
                    description=t("moderationnotify.embed.appeal_reply_desc", locale=loc, guild=modal_interaction.guild.name),
                    color=discord.Color.green(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.add_field(name=t("moderationnotify.field.guild_id", locale=loc), value=modal_interaction.guild.id if modal_interaction.guild else "?", inline=True)
                embed.add_field(name=t("moderationnotify.field.reply_content", locale=loc), value=self.response.value, inline=False)
                embed.add_field(name=t("moderationnotify.field.can_appeal_again", locale=loc), value=yes_no, inline=False)
                embed.set_footer(text=f"{modal_interaction.guild.name}", icon_url=modal_interaction.guild.icon.url if modal_interaction.guild.icon else None)
                if can_appeal:
                    embed.add_field(name=t("moderationnotify.field.appeal_method", locale=loc), value=t("moderationnotify.msg.appeal_again_hint", locale=loc), inline=False)
                    with i18n.use_locale(loc):
                        view = AppealView()
                else:
                    view = None
                try:
                    await user.send(embed=embed, view=view)
                    await modal_interaction.response.send_message(t("moderationnotify.msg.reply_sent"), ephemeral=True)
                except discord.Forbidden:
                    await modal_interaction.response.send_message(t("moderationnotify.msg.reply_dm_blocked"), ephemeral=True)
                    return
                for child in origself.children:
                    child.disabled = True
                # 原始 embed 在申訴頻道（guild 共享），後綴以伺服器語言渲染
                guild_loc = i18n.resolve_locale(guild_id=modal_interaction.guild.id) if modal_interaction.guild else i18n.SOURCE_LOCALE
                admin_yes_no = t("common.state.yes", locale=guild_loc) if can_appeal else t("common.state.no", locale=guild_loc)
                origembed = message.embeds[0]
                origembed.title += t("moderationnotify.suffix.replied", locale=guild_loc)
                origembed.color = discord.Color.green()
                origembed.add_field(name=t("moderationnotify.field.admin_reply", locale=guild_loc), value=self.response.value, inline=False)
                origembed.add_field(name=t("moderationnotify.field.can_appeal_again", locale=guild_loc), value=admin_yes_no, inline=False)
                origembed.set_footer(text=t("moderationnotify.footer.replied_by", locale=guild_loc, user=modal_interaction.user.name), icon_url=modal_interaction.user.display_avatar.url if modal_interaction.user and modal_interaction.user.display_avatar else None)
                await interaction.edit_original_response(embed=origembed, view=origself)
                origself.stop()
        await interaction.response.send_modal(ResponseAppealModal())

    @discord.ui.button(label=i18n.K("moderationnotify.btn.blacklist_appeal"), style=discord.ButtonStyle.danger, emoji="⛔", custom_id="blacklist_appeal_button")
    async def blacklist_appeal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        message = interaction.message  # 直接使用 interaction.message
        user_id = int(message.embeds[0].fields[0].value)  # 機器約定：fields[0] 是用戶 ID
        guild_id = int(message.embeds[0].fields[1].value)  # 機器約定：fields[1] 是伺服器 ID
        blacklist = get_server_config(guild_id, "appeal_blacklist", [])
        if user_id in blacklist:
            await interaction.response.send_message(t("moderationnotify.msg.already_blacklisted"), ephemeral=True)
            return
        blacklist.append(user_id)
        set_server_config(guild_id, "appeal_blacklist", blacklist)
        await interaction.response.send_message(t("moderationnotify.msg.blacklisted"), ephemeral=True)
        for child in self.children:
            child.disabled = True
        guild_loc = i18n.resolve_locale(guild_id=guild_id)
        origembed = message.embeds[0]
        origembed.title += t("moderationnotify.suffix.blacklisted", locale=guild_loc)
        origembed.color = discord.Color.red()
        origembed.add_field(name=t("moderationnotify.field.admin_action", locale=guild_loc), value=t("moderationnotify.msg.action_blacklist", locale=guild_loc), inline=False)
        await interaction.edit_original_response(embed=origembed, view=self)
        self.stop()


class AppealView(i18n.I18nView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label=i18n.K("moderationnotify.btn.appeal"), style=discord.ButtonStyle.primary, emoji="📩", custom_id="appeal_button")
    async def appeal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        origself = self
        class AppealModal(discord.ui.Modal, title=t("moderationnotify.modal.appeal_title")):
            reason = discord.ui.TextInput(label=t("moderationnotify.modal.appeal_label"), style=discord.TextStyle.paragraph, required=True, max_length=1000)

            async def on_submit(self, modal_interaction: discord.Interaction):
                message = interaction.message  # 直接使用 interaction.message (DM 中也能用)
                guild_id = int(message.embeds[0].fields[0].value)  # 機器約定：DM 通知的 fields[0] 是伺服器 ID
                appeal_channel_id = get_server_config(guild_id, "user_appeal_channel")
                appeal_channel = bot.get_channel(appeal_channel_id) if appeal_channel_id else None
                if not appeal_channel:
                    await modal_interaction.response.send_message(t("moderationnotify.msg.appeal_channel_missing"), ephemeral=True)
                    return
                # 申訴 embed 發到伺服器的申訴頻道（guild 共享），以伺服器語言渲染。
                # 機器約定：fields[0] 是用戶 ID、fields[1] 是伺服器 ID。
                guild_loc = i18n.resolve_locale(guild_id=guild_id)
                embed = discord.Embed(
                    title=t("moderationnotify.embed.new_appeal_title", locale=guild_loc),
                    description=t("moderationnotify.embed.new_appeal_desc", locale=guild_loc,
                                  user=modal_interaction.user.mention, user_id=modal_interaction.user.id),
                    color=discord.Color.blue(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.add_field(name=t("moderationnotify.field.user_id", locale=guild_loc), value=str(modal_interaction.user.id), inline=False)
                embed.add_field(name=t("moderationnotify.field.guild_id", locale=guild_loc), value=str(guild_id), inline=False)
                embed.add_field(name=t("moderationnotify.field.appeal_reason", locale=guild_loc), value=self.reason.value, inline=False)
                embed.set_author(name=modal_interaction.user.display_name, icon_url=modal_interaction.user.display_avatar.url)
                with i18n.use_locale(guild_loc):
                    response_view = ResponseAppealView()
                await appeal_channel.send(embed=embed, view=response_view)
                await modal_interaction.response.send_message(t("moderationnotify.msg.appeal_submitted"), ephemeral=True)
                for child in origself.children:
                    child.disabled = True
                await interaction.edit_original_response(view=origself)
                origself.stop()
        # check if banned or muted
        message = interaction.message  # 直接使用 interaction.message (DM 中也能用)
        guild_id = int(message.embeds[0].fields[0].value)  # 機器約定：DM 通知的 fields[0] 是伺服器 ID
        guild = bot.get_guild(guild_id)
        if not guild:
            await interaction.response.send_message(t("moderationnotify.msg.guild_not_found"), ephemeral=True)
            return
        if guild.get_member(interaction.user.id) is None:
            if not guild.fetch_ban(interaction.user.id):
                await interaction.response.send_message(t("moderationnotify.msg.not_banned"), ephemeral=True)
                return
        if guild.get_member(interaction.user.id):
            if not guild.get_member(interaction.user.id).is_timed_out():
                await interaction.response.send_message(t("moderationnotify.msg.not_muted"), ephemeral=True)
                return
        await interaction.response.send_modal(AppealModal())

async def notify_user(user: discord.User, guild: discord.Guild, action: str, reason: str = None, end_time=None, moderator: discord.Member = None):
    en_action = ch2en_map.get(action, action.lower())
    if not get_server_config(guild.id, f"notify_user_on_{en_action}", True):
        return
    if guild.get_member(user.id) is None:
        # user is not in the guild, check if they are banned
        try:
            ban_entry = await guild.fetch_ban(user)
            if ban_entry:
                # already banned
                return
        except discord.NotFound:
            return
    # 私訊通知以收件人語言渲染（listener 呼叫時沒有 interaction scope）
    loc = i18n.resolve_locale(user_id=user.id, guild_id=guild.id)
    if en_action in _KNOWN_ACTIONS:
        title = t(f"moderationnotify.dm.title_{en_action}", locale=loc, guild=guild.name)
    else:
        title = t("moderationnotify.dm.title_generic", locale=loc, guild=guild.name, action=action)
    embed = discord.Embed(
        title=title,
        description=t("moderationnotify.dm.reason", locale=loc,
                      reason=reason if reason else t("moderationnotify.msg.no_reason", locale=loc)),
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc)  # 訊息時間
    )

    # 機器約定：DM 通知的 fields[0] 是伺服器 ID（AppealView 解析用），必須維持在第一個
    embed.add_field(name=t("moderationnotify.field.guild_id", locale=loc), value=guild.id, inline=True)

    # add server icon
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    if end_time:
        embed.add_field(name=t("moderationnotify.field.until", locale=loc), value=f"<t:{str(int(end_time.timestamp()))}:R> (<t:{str(int(end_time.timestamp()))}:F>)", inline=False)

    if moderator:
        embed.set_author(name=f"{moderator.display_name}({moderator.name})", icon_url=moderator.display_avatar.url if moderator.display_avatar else None)

    embed.set_footer(text=f"{guild.name}")
    if get_server_config(guild.id, "user_appeal_channel"):
        # check if user is in blacklist
        blacklist = get_server_config(guild.id, "appeal_blacklist", [])
        if user.id in blacklist:
            embed.add_field(name=t("moderationnotify.field.appeal_method", locale=loc), value=t("moderationnotify.msg.appeal_blacklisted_hint", locale=loc), inline=False)
            view = None
        else:
            embed.add_field(name=t("moderationnotify.field.appeal_method", locale=loc), value=t("moderationnotify.msg.appeal_hint", locale=loc), inline=False)
            with i18n.use_locale(loc):
                view = AppealView()
    else:
        view = None

    try:
        msg = await user.send(embed=embed, view=view)
        log(f"Sent DM notification to {user}\n- {embed.title}\n- {embed.description}", module_name="ModerationNotify", guild=guild)
        return msg
    except discord.Forbidden:
        log(f"Could not DM {user}", level=logging.ERROR, module_name="ModerationNotify", guild=guild)


@bot.event
async def on_member_remove(member: discord.Member):
    if member.bot:
        return
    if is_ignored(member.id):
        return
    guild = member.guild
    # check bot permissions
    if not guild.me.guild_permissions.view_audit_log:
        return
    try:
        async for entry in guild.audit_logs(limit=1):
            if entry.target.id != member.id:
                continue

            if entry.action == discord.AuditLogAction.kick:  # kick
                if not get_server_config(guild.id, "notify_user_on_kick", True):
                    return
                await notify_user(member, guild, "kick", entry.reason, moderator=entry.user)
            elif entry.action == discord.AuditLogAction.ban:  # ban
                if not get_server_config(guild.id, "notify_user_on_ban", True):
                    return
                await notify_user(member, guild, "ban", entry.reason, moderator=entry.user)
            else:
                pass
    except Exception as e:
        log(f"Error fetching audit logs: {e}", level=logging.ERROR, module_name="ModerationNotify", guild=guild)


# timeout
@bot.event
async def on_member_update(before, after):
    if after.bot:
        return
    if not get_server_config(after.guild.id, "notify_user_on_mute", True):
        return
    if is_ignored(after.id):
        return
    if before.timed_out_until != after.timed_out_until and after.timed_out_until is not None:
        # 檢查database的值避免重複
        if get_user_data(after.guild.id, after.id, "muted_until") == after.timed_out_until.isoformat():
            return
        if after.timed_out_until <= datetime.now(timezone.utc):
            return
        set_user_data(after.guild.id, after.id, "muted_until", after.timed_out_until.isoformat())
        guild = after.guild
        try:
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.member_update):
                if entry.target.id == after.id:
                    end_time = after.timed_out_until.astimezone(timezone(timedelta(hours=8)))  # 台灣時間
                    await notify_user(after, guild, "mute", entry.reason, end_time, moderator=entry.user)
        except Exception as e:
            log(f"Error fetching audit logs: {e}", level=logging.ERROR, module_name="ModerationNotify", guild=guild)
            await notify_user(after, guild, "mute", None, after.timed_out_until)


class ModerationNotify(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name=app_commands.locale_str("settings-punishment-notify", i18n_key="cmd.moderationnotify.settings_punishment_notify.name"), description=app_commands.locale_str("Configure whether punished users are notified", i18n_key="cmd.moderationnotify.settings_punishment_notify.desc"))
    @app_commands.describe(
        action=app_commands.locale_str("The punishment type to configure", i18n_key="cmd.moderationnotify.settings_punishment_notify.param.action"),
        enable=app_commands.locale_str("Whether to enable notifications", i18n_key="cmd.moderationnotify.settings_punishment_notify.param.enable")
    )
    @app_commands.choices(action=[
        app_commands.Choice(name=app_commands.locale_str("Kick", i18n_key="cmd.moderationnotify.settings_punishment_notify.choice.kick"), value="kick"),
        app_commands.Choice(name=app_commands.locale_str("Ban", i18n_key="cmd.moderationnotify.settings_punishment_notify.choice.ban"), value="ban"),
        app_commands.Choice(name=app_commands.locale_str("Mute", i18n_key="cmd.moderationnotify.settings_punishment_notify.choice.mute"), value="mute"),
    ])
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def set_moderation_notification(self, interaction: discord.Interaction, action: str, enable: bool):
        guild = interaction.guild
        if action not in ["kick", "ban", "mute"]:
            await interaction.response.send_message(t("moderationnotify.msg.invalid_action"), ephemeral=True)
            return

        set_server_config(guild.id, f"notify_user_on_{action}", enable)
        state = t("common.state.enabled") if enable else t("common.state.disabled")
        await interaction.response.send_message(t("moderationnotify.msg.notify_set", action=action, state=state), ephemeral=True)
        log(f"Notification for {action} set to {'enabled' if enable else 'disabled'}.", module_name="ModerationNotify", guild=guild)

    @app_commands.command(name=app_commands.locale_str("user-appeal-channel", i18n_key="cmd.moderationnotify.user_appeal_channel.name"), description=app_commands.locale_str("Set the appeal channel; appeals are disabled if unset.", i18n_key="cmd.moderationnotify.user_appeal_channel.desc"))
    @app_commands.describe(channel=app_commands.locale_str("The appeal channel; leave empty to disable appeals.", i18n_key="cmd.moderationnotify.user_appeal_channel.param.channel"))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def set_user_appeal_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        guild = interaction.guild
        if channel:
            if channel.permissions_for(interaction.guild.me).send_messages is False:
                await interaction.response.send_message(t("moderationnotify.msg.channel_no_permission"), ephemeral=True)
                return
            set_server_config(guild.id, "user_appeal_channel", channel.id)
            await interaction.response.send_message(t("moderationnotify.msg.appeal_channel_set", channel=channel.mention), ephemeral=True)
            log(f"Appeal channel set to {channel} ({channel.id})", module_name="ModerationNotify", guild=guild)
        else:
            # remove the appeal channel
            set_server_config(guild.id, "user_appeal_channel", None)
            await interaction.response.send_message(t("moderationnotify.msg.appeal_disabled"), ephemeral=True)
            log("Appeal feature disabled", module_name="ModerationNotify", guild=guild)

    @app_commands.command(name=app_commands.locale_str("user-appeal-blacklist", i18n_key="cmd.moderationnotify.user_appeal_blacklist.name"), description=app_commands.locale_str("Manage the appeal blacklist", i18n_key="cmd.moderationnotify.user_appeal_blacklist.desc"))
    @app_commands.describe(
        user=app_commands.locale_str("User to add to or remove from the blacklist", i18n_key="cmd.moderationnotify.user_appeal_blacklist.param.user"),
        reason=app_commands.locale_str("Reason for blacklisting (optional)", i18n_key="cmd.moderationnotify.user_appeal_blacklist.param.reason")
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def blacklist_appeal_user(self, interaction: discord.Interaction, user: discord.User, reason: str = None):
        guild = interaction.guild

        # 防止將機器人加入黑名單
        if user.bot:
            await interaction.response.send_message(t("moderationnotify.msg.cannot_blacklist_bot"), ephemeral=True)
            return

        # 防止將自己加入黑名單
        if user.id == interaction.user.id:
            await interaction.response.send_message(t("moderationnotify.msg.cannot_blacklist_self"), ephemeral=True)
            return

        blacklist = get_server_config(guild.id, "appeal_blacklist", [])
        if user.id in blacklist:
            class UnblacklistConfirm(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=60)

                async def on_timeout(self):
                    for child in self.children:
                        child.disabled = True
                    try:
                        await interaction.edit_original_response(view=self)
                    except:
                        pass

                @discord.ui.button(label=t("moderationnotify.btn.confirm_unblacklist"), style=discord.ButtonStyle.danger, emoji="✅")
                async def confirm_unblacklist(self, inter: discord.Interaction, button: discord.ui.Button):
                    blacklist.remove(user.id)
                    set_server_config(guild.id, "appeal_blacklist", blacklist)
                    await inter.response.edit_message(content=t("moderationnotify.msg.unblacklisted", user=user.mention), view=None)
                    log(f"Removed {user} ({user.id}) from the appeal blacklist", module_name="ModerationNotify", guild=guild)
                    self.stop()

                @discord.ui.button(label=t("common.btn.cancel"), style=discord.ButtonStyle.secondary, emoji="❌")
                async def cancel_unblacklist(self, inter: discord.Interaction, button: discord.ui.Button):
                    await inter.response.edit_message(content=t("moderationnotify.msg.cancelled"), view=None)
                    self.stop()

            await interaction.response.send_message(
                t("moderationnotify.msg.unblacklist_confirm", user=user.mention),
                view=UnblacklistConfirm(),
                ephemeral=True
            )
            return
        blacklist.append(user.id)
        set_server_config(guild.id, "appeal_blacklist", blacklist)
        await interaction.response.send_message(t("moderationnotify.msg.blacklist_added", user=user.mention), ephemeral=True)

        try:
            # 通知內容以收件人語言渲染（notify_user 內部處理）
            recipient_loc = i18n.resolve_locale(user_id=user.id, guild_id=guild.id)
            if reason:
                blacklist_reason = t("moderationnotify.msg.blacklist_notify_reason", locale=recipient_loc, reason=reason)
            else:
                blacklist_reason = t("moderationnotify.msg.blacklist_notify", locale=recipient_loc)
            await notify_user(user, guild, "blacklist", blacklist_reason, moderator=interaction.user)
        except Exception as e:
            log(f"Could not notify {user} ({user.id}) about blacklist status: {e}", level=logging.WARNING, module_name="ModerationNotify", guild=guild)

        log(f"Added {user} ({user.id}) to the appeal blacklist, reason: {reason}", module_name="ModerationNotify", guild=guild)

    @app_commands.command(name=app_commands.locale_str("view-appeal-blacklist", i18n_key="cmd.moderationnotify.view_appeal_blacklist.name"), description=app_commands.locale_str("View the appeal blacklist", i18n_key="cmd.moderationnotify.view_appeal_blacklist.desc"))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def view_appeal_blacklist(self, interaction: discord.Interaction):
        guild = interaction.guild
        blacklist = get_server_config(guild.id, "appeal_blacklist", [])

        if not blacklist:
            await interaction.response.send_message(t("moderationnotify.msg.blacklist_empty"), ephemeral=True)
            return

        embed = discord.Embed(
            title=t("moderationnotify.embed.blacklist_title"),
            description=t("moderationnotify.embed.blacklist_desc", count=len(blacklist)),
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )

        # 分批顯示用戶,避免超過 embed 限制
        user_list = []
        for user_id in blacklist[:25]:  # 最多顯示 25 個
            try:
                user = await bot.fetch_user(user_id)
                user_list.append(f"• {user.mention} (`{user.id}`)")
            except:
                user_list.append(f"• {t('moderationnotify.msg.unknown_user')} (`{user_id}`)")

        embed.add_field(name=t("moderationnotify.field.blacklist_users"), value="\n".join(user_list) if user_list else t("common.state.none"), inline=False)

        if len(blacklist) > 25:
            embed.set_footer(text=t("moderationnotify.footer.blacklist_truncated", count=len(blacklist)))

        await interaction.response.send_message(embed=embed, ephemeral=True)
        log(f"Viewed appeal blacklist ({len(blacklist)} users)", module_name="ModerationNotify", guild=guild)

    @commands.Cog.listener()
    async def on_ready(self):
        # 註冊持久化 View，讓機器人重啟後按鈕仍可用
        bot.add_view(AppealView())
        bot.add_view(ResponseAppealView())

asyncio.run(bot.add_cog(ModerationNotify(bot)))



if __name__ == "__main__":
    start_bot()
