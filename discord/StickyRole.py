from globalenv import bot, get_server_config, set_server_config, get_user_data, set_user_data
import discord
from discord.ext import commands
from discord import app_commands
from logger import log
import logging
import asyncio

import i18n
from i18n import t


@app_commands.guild_only()
@app_commands.default_permissions(manage_roles=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class StickyRole(commands.GroupCog,
                 group_name=app_commands.locale_str("stickyrole", i18n_key="cmd.stickyrole.stickyrole.root.name"),
                 group_description=app_commands.locale_str("Restore previously held roles when users rejoin", i18n_key="cmd.stickyrole.stickyrole.root.desc")):
    """當用戶離開伺服器後重新加入時，自動恢復先前擁有的身份組。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── 管理指令 ──────────────────────────────────────────────

    @app_commands.command(name=app_commands.locale_str("toggle", i18n_key="cmd.stickyrole.stickyrole.toggle.name"), description=app_commands.locale_str("Enable or disable StickyRole", i18n_key="cmd.stickyrole.stickyrole.toggle.desc"))
    @app_commands.describe(enable=app_commands.locale_str("Whether to enable StickyRole", i18n_key="cmd.stickyrole.stickyrole.toggle.param.enable"))
    @app_commands.choices(enable=[
        app_commands.Choice(name=app_commands.locale_str("Enable", i18n_key="cmd.stickyrole.stickyrole.toggle.choice.true"), value="True"),
        app_commands.Choice(name=app_commands.locale_str("Disable", i18n_key="cmd.stickyrole.stickyrole.toggle.choice.false"), value="False"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def toggle(self, interaction: discord.Interaction, enable: str):
        enabled = (enable == "True")
        if enabled and not interaction.guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message(t("stickyrole.err.missing_manage_roles"), ephemeral=True)
            return
        set_server_config(interaction.guild.id, "stickyrole_enabled", enabled)
        log(f"StickyRole {'enabled' if enabled else 'disabled'}", module_name="StickyRole", guild=interaction.guild, user=interaction.user)
        await interaction.response.send_message(
            t("stickyrole.msg.enabled" if enabled else "stickyrole.msg.disabled"), ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("add", i18n_key="cmd.stickyrole.stickyrole.add.name"), description=app_commands.locale_str("Add a role to the remember list (empty list = remember all roles)", i18n_key="cmd.stickyrole.stickyrole.add.desc"))
    @app_commands.describe(role=app_commands.locale_str("The role to add to the allow list", i18n_key="cmd.stickyrole.stickyrole.add.param.role"))
    @app_commands.checks.has_permissions(administrator=True)
    async def add_role(self, interaction: discord.Interaction, role: discord.Role):
        guild_id = interaction.guild.id
        allowed: list = get_server_config(guild_id, "stickyrole_allowed_roles", [])
        if role.id in allowed:
            await interaction.response.send_message(t("stickyrole.err.already_allowed", role=role.mention), ephemeral=True)
            return
        if role.is_default():
            await interaction.response.send_message(t("stickyrole.err.cannot_add_everyone"), ephemeral=True)
            return
        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(t("stickyrole.err.role_too_high", role=role.mention), ephemeral=True)
            return
        allowed.append(role.id)
        set_server_config(guild_id, "stickyrole_allowed_roles", allowed)
        log(f"Added {role.name} ({role.id}) to the allow list", module_name="StickyRole", guild=interaction.guild, user=interaction.user)
        await interaction.response.send_message(t("stickyrole.msg.role_added", role=role.mention), ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("remove", i18n_key="cmd.stickyrole.stickyrole.remove.name"), description=app_commands.locale_str("Remove a role from the allow list", i18n_key="cmd.stickyrole.stickyrole.remove.desc"))
    @app_commands.describe(role=app_commands.locale_str("The role to remove from the allow list", i18n_key="cmd.stickyrole.stickyrole.remove.param.role"))
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_role(self, interaction: discord.Interaction, role: discord.Role):
        guild_id = interaction.guild.id
        allowed: list = get_server_config(guild_id, "stickyrole_allowed_roles", [])
        if role.id not in allowed:
            await interaction.response.send_message(t("stickyrole.err.not_allowed", role=role.mention), ephemeral=True)
            return
        allowed.remove(role.id)
        set_server_config(guild_id, "stickyrole_allowed_roles", allowed)
        log(f"Removed {role.name} ({role.id}) from the allow list", module_name="StickyRole", guild=interaction.guild, user=interaction.user)
        await interaction.response.send_message(t("stickyrole.msg.role_removed", role=role.mention), ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("list", i18n_key="cmd.stickyrole.stickyrole.list.name"), description=app_commands.locale_str("View the current allow list and feature status", i18n_key="cmd.stickyrole.stickyrole.list.desc"))
    @app_commands.checks.has_permissions(administrator=True)
    async def list_config(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        enabled = get_server_config(guild_id, "stickyrole_enabled", False)
        allowed: list = get_server_config(guild_id, "stickyrole_allowed_roles", [])
        ignore_bots = get_server_config(guild_id, "stickyrole_ignore_bots", True)
        log_channel_id = get_server_config(guild_id, "stickyrole_log_channel")

        embed = discord.Embed(
            title=t("stickyrole.config.title"),
            color=0x5865F2 if enabled else 0x99AAB5,
        )
        embed.add_field(name=t("stickyrole.field.status"),
                        value=t("stickyrole.state.enabled") if enabled else t("stickyrole.state.disabled"),
                        inline=True)
        embed.add_field(name=t("stickyrole.field.ignore_bots"),
                        value=t("common.state.yes") if ignore_bots else t("common.state.no"), inline=True)

        if log_channel_id:
            channel = interaction.guild.get_channel(log_channel_id)
            embed.add_field(name=t("stickyrole.field.log_channel"),
                            value=channel.mention if channel else t("stickyrole.config.channel_missing", channel_id=log_channel_id),
                            inline=True)
        else:
            embed.add_field(name=t("stickyrole.field.log_channel"), value=t("common.state.unset"), inline=True)

        if allowed:
            role_mentions = []
            for rid in allowed:
                r = interaction.guild.get_role(rid)
                role_mentions.append(r.mention if r else t("stickyrole.config.deleted_role", role_id=rid))
            embed.add_field(name=t("stickyrole.field.allowed_roles"), value="\n".join(role_mentions), inline=False)
        else:
            embed.add_field(name=t("stickyrole.field.allowed_roles"), value=t("stickyrole.config.no_restriction"), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("clear", i18n_key="cmd.stickyrole.stickyrole.clear.name"), description=app_commands.locale_str("Clear the allow list (back to remembering all roles)", i18n_key="cmd.stickyrole.stickyrole.clear.desc"))
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_roles(self, interaction: discord.Interaction):
        set_server_config(interaction.guild.id, "stickyrole_allowed_roles", [])
        log("Allow list cleared", module_name="StickyRole", guild=interaction.guild, user=interaction.user)
        await interaction.response.send_message(t("stickyrole.msg.list_cleared"), ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("ignore-bots", i18n_key="cmd.stickyrole.stickyrole.ignore_bots.name"), description=app_commands.locale_str("Configure whether bot accounts are ignored", i18n_key="cmd.stickyrole.stickyrole.ignore_bots.desc"))
    @app_commands.describe(enable=app_commands.locale_str("Whether to ignore bot accounts", i18n_key="cmd.stickyrole.stickyrole.ignore_bots.param.enable"))
    @app_commands.choices(enable=[
        app_commands.Choice(name=app_commands.locale_str("Yes (ignore bots)", i18n_key="cmd.stickyrole.stickyrole.ignore_bots.choice.true"), value="True"),
        app_commands.Choice(name=app_commands.locale_str("No (include bots)", i18n_key="cmd.stickyrole.stickyrole.ignore_bots.choice.false"), value="False"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def ignore_bots(self, interaction: discord.Interaction, enable: str):
        val = (enable == "True")
        set_server_config(interaction.guild.id, "stickyrole_ignore_bots", val)
        await interaction.response.send_message(
            t("stickyrole.msg.ignore_bots_on" if val else "stickyrole.msg.ignore_bots_off"), ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("set-log-channel", i18n_key="cmd.stickyrole.stickyrole.set_log_channel.name"), description=app_commands.locale_str("Set the StickyRole log channel", i18n_key="cmd.stickyrole.stickyrole.set_log_channel.desc"))
    @app_commands.describe(channel=app_commands.locale_str("Channel for StickyRole logs (leave empty to unset)", i18n_key="cmd.stickyrole.stickyrole.set_log_channel.param.channel"))
    @app_commands.checks.has_permissions(administrator=True)
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        guild_id = interaction.guild.id
        if channel:
            perms = channel.permissions_for(interaction.guild.me)
            if not (perms.view_channel and perms.send_messages):
                await interaction.response.send_message(t("stickyrole.err.log_channel_perms", channel=channel.mention), ephemeral=True)
                return
            set_server_config(guild_id, "stickyrole_log_channel", channel.id)
            await interaction.response.send_message(t("stickyrole.msg.log_channel_set", channel=channel.mention), ephemeral=True)
        else:
            set_server_config(guild_id, "stickyrole_log_channel", None)
            await interaction.response.send_message(t("stickyrole.msg.log_channel_unset"), ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("view", i18n_key="cmd.stickyrole.stickyrole.view.name"), description=app_commands.locale_str("View a user's previously saved roles", i18n_key="cmd.stickyrole.stickyrole.view.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to view", i18n_key="cmd.stickyrole.stickyrole.view.param.user"))
    @app_commands.checks.has_permissions(administrator=True)
    async def view_user(self, interaction: discord.Interaction, user: discord.User):
        guild_id = interaction.guild.id
        saved: list = get_user_data(guild_id, user.id, "stickyrole_roles", [])
        if not saved:
            await interaction.response.send_message(t("stickyrole.msg.no_records", user=user.mention), ephemeral=True)
            return
        role_mentions = []
        for rid in saved:
            r = interaction.guild.get_role(rid)
            role_mentions.append(r.mention if r else t("stickyrole.config.deleted_role", role_id=rid))
        embed = discord.Embed(title=t("stickyrole.view.title", user=user), color=0x5865F2)
        embed.add_field(name=t("stickyrole.field.saved_roles"), value="\n".join(role_mentions), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("clear-user", i18n_key="cmd.stickyrole.stickyrole.clear_user.name"), description=app_commands.locale_str("Clear a user's StickyRole records", i18n_key="cmd.stickyrole.stickyrole.clear_user.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user whose records to clear", i18n_key="cmd.stickyrole.stickyrole.clear_user.param.user"))
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_user(self, interaction: discord.Interaction, user: discord.User):
        guild_id = interaction.guild.id
        set_user_data(guild_id, user.id, "stickyrole_roles", None)
        log(f"Cleared the StickyRole records for {user}", module_name="StickyRole", guild=interaction.guild, user=interaction.user)
        await interaction.response.send_message(t("stickyrole.msg.records_cleared", user=user.mention), ephemeral=True)

    # ── 事件監聽 ──────────────────────────────────────────────

    def _filter_roles(self, member: discord.Member) -> list[int]:
        """根據伺服器設定過濾出需要記憶的身份組 ID 列表。"""
        guild_id = member.guild.id
        allowed: list = get_server_config(guild_id, "stickyrole_allowed_roles", [])
        bot_top_role = member.guild.me.top_role

        role_ids = []
        for role in member.roles:
            if role.is_default():
                continue  # 跳過 @everyone
            if role.managed:
                continue  # 跳過由整合服務管理的身份組（如 Boost）
            if role >= bot_top_role:
                continue  # 機器人無法指派高於自己的身份組
            if allowed and role.id not in allowed:
                continue  # 不在允許清單中
            role_ids.append(role.id)
        return role_ids

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild_id = member.guild.id

        # 是否啟用
        if not get_server_config(guild_id, "stickyrole_enabled", False):
            return

        # 是否忽略機器人
        if member.bot and get_server_config(guild_id, "stickyrole_ignore_bots", True):
            return

        role_ids = self._filter_roles(member)
        if not role_ids:
            return

        set_user_data(guild_id, member.id, "stickyrole_roles", role_ids)
        log(f"Saved {len(role_ids)} role(s) held by {member} on leave", module_name="StickyRole", guild=member.guild, user=member)

        # 發送日誌
        await self._send_log(member.guild, member, role_ids, action="save")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild_id = member.guild.id

        # 是否啟用
        if not get_server_config(guild_id, "stickyrole_enabled", False):
            return

        # 是否忽略機器人
        if member.bot and get_server_config(guild_id, "stickyrole_ignore_bots", True):
            return

        saved: list = get_user_data(guild_id, member.id, "stickyrole_roles", [])
        if not saved:
            return

        restored = []
        failed = []
        for rid in saved:
            role = member.guild.get_role(rid)
            if role is None:
                failed.append(rid)
                continue
            if role >= member.guild.me.top_role:
                failed.append(rid)
                continue
            if role.managed:
                failed.append(rid)
                continue
            try:
                await member.add_roles(role, reason=t("stickyrole.audit.restore",
                                                      locale=i18n.resolve_locale(guild_id=guild_id)))
                restored.append(rid)
            except discord.Forbidden:
                failed.append(rid)
                log(f"Couldn't restore role {role.name} for {member} (insufficient permissions)", level=logging.WARNING, module_name="StickyRole", guild=member.guild, user=member)
            except discord.HTTPException as e:
                failed.append(rid)
                log(f"Error restoring role {role.name} for {member}: {e}", level=logging.ERROR, module_name="StickyRole", guild=member.guild, user=member)

        if restored:
            log(f"Restored {len(restored)} role(s) for {member}", module_name="StickyRole", guild=member.guild, user=member)
        if failed:
            log(f"Failed to restore {len(failed)} role(s) for {member}", level=logging.WARNING, module_name="StickyRole", guild=member.guild, user=member)

        # 清除已使用的紀錄
        set_user_data(guild_id, member.id, "stickyrole_roles", None)

        # 發送日誌
        await self._send_log(member.guild, member, restored, failed=failed, action="restore")

    async def _send_log(self, guild: discord.Guild, user: discord.User, role_ids: list[int], failed: list[int] = None, action: str = "save"):
        """發送日誌到設定的頻道。"""
        channel_id = get_server_config(guild.id, "stickyrole_log_channel")
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not channel:
            return

        # 日誌頻道是 guild 共享的，用伺服器語言而不是觸發者的個人語言
        locale = i18n.resolve_locale(guild_id=guild.id)
        role_mentions = []
        for rid in role_ids:
            r = guild.get_role(rid)
            role_mentions.append(r.mention if r else f"`{rid}`")
        none_text = t("common.state.none", locale=locale)

        if action == "save":
            embed = discord.Embed(
                title=t("stickyrole.log.saved_title", locale=locale),
                description=t("stickyrole.log.left_guild", locale=locale, user=user.mention),
                color=0xFFA500,
            )
            embed.add_field(name=t("stickyrole.field.recorded_roles", locale=locale),
                            value=i18n.join_list(role_mentions, locale=locale) if role_mentions else none_text,
                            inline=False)
        else:
            embed = discord.Embed(
                title=t("stickyrole.log.restored_title", locale=locale),
                description=t("stickyrole.log.rejoined_guild", locale=locale, user=user.mention),
                color=0x57F287,
            )
            embed.add_field(name=t("stickyrole.field.restored", locale=locale),
                            value=i18n.join_list(role_mentions, locale=locale) if role_mentions else none_text,
                            inline=False)
            if failed:
                failed_mentions = []
                for rid in failed:
                    r = guild.get_role(rid)
                    failed_mentions.append(r.mention if r else f"`{rid}`")
                embed.add_field(name=t("stickyrole.field.restore_failed", locale=locale),
                                value=i18n.join_list(failed_mentions, locale=locale), inline=False)

        embed.set_thumbnail(url=user.display_avatar.url if user.display_avatar else None)
        embed.set_footer(text=t("stickyrole.log.footer", locale=locale, user_id=user.id))

        try:
            await channel.send(embed=embed)
        except Exception as e:
            log(f"Failed to send the StickyRole log: {e}", level=logging.ERROR, module_name="StickyRole", guild=guild)


asyncio.run(bot.add_cog(StickyRole(bot)))

if __name__ == "__main__":
    from globalenv import start_bot
    start_bot()

