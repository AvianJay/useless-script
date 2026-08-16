from globalenv import bot, get_server_config, set_server_config, get_user_data, set_user_data
import discord
from discord.ext import commands
from discord import app_commands
from logger import log
import logging
import asyncio


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
            await interaction.response.send_message("⚠️ 機器人缺少「管理身份組」權限，無法啟用 StickyRole 功能。", ephemeral=True)
            return
        set_server_config(interaction.guild.id, "stickyrole_enabled", enabled)
        status = "啟用" if enabled else "停用"
        log(f"StickyRole 已{status}", module_name="StickyRole", guild=interaction.guild, user=interaction.user)
        await interaction.response.send_message(f"✅ StickyRole 功能已 **{status}**。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("add", i18n_key="cmd.stickyrole.stickyrole.add.name"), description=app_commands.locale_str("Add a role to the remember list (empty list = remember all roles)", i18n_key="cmd.stickyrole.stickyrole.add.desc"))
    @app_commands.describe(role=app_commands.locale_str("The role to add to the allow list", i18n_key="cmd.stickyrole.stickyrole.add.param.role"))
    @app_commands.checks.has_permissions(administrator=True)
    async def add_role(self, interaction: discord.Interaction, role: discord.Role):
        guild_id = interaction.guild.id
        allowed: list = get_server_config(guild_id, "stickyrole_allowed_roles", [])
        if role.id in allowed:
            await interaction.response.send_message(f"⚠️ {role.mention} 已在允許清單中。", ephemeral=True)
            return
        if role.is_default():
            await interaction.response.send_message("⚠️ 無法將 @everyone 加入允許清單。", ephemeral=True)
            return
        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(f"⚠️ {role.mention} 的順位高於或等於機器人最高身份組，無法指派。", ephemeral=True)
            return
        allowed.append(role.id)
        set_server_config(guild_id, "stickyrole_allowed_roles", allowed)
        log(f"允許清單新增 {role.name} ({role.id})", module_name="StickyRole", guild=interaction.guild, user=interaction.user)
        await interaction.response.send_message(f"✅ 已將 {role.mention} 加入 StickyRole 允許清單。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("remove", i18n_key="cmd.stickyrole.stickyrole.remove.name"), description=app_commands.locale_str("Remove a role from the allow list", i18n_key="cmd.stickyrole.stickyrole.remove.desc"))
    @app_commands.describe(role=app_commands.locale_str("The role to remove from the allow list", i18n_key="cmd.stickyrole.stickyrole.remove.param.role"))
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_role(self, interaction: discord.Interaction, role: discord.Role):
        guild_id = interaction.guild.id
        allowed: list = get_server_config(guild_id, "stickyrole_allowed_roles", [])
        if role.id not in allowed:
            await interaction.response.send_message(f"⚠️ {role.mention} 不在允許清單中。", ephemeral=True)
            return
        allowed.remove(role.id)
        set_server_config(guild_id, "stickyrole_allowed_roles", allowed)
        log(f"允許清單移除 {role.name} ({role.id})", module_name="StickyRole", guild=interaction.guild, user=interaction.user)
        await interaction.response.send_message(f"✅ 已將 {role.mention} 從 StickyRole 允許清單中移除。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("list", i18n_key="cmd.stickyrole.stickyrole.list.name"), description=app_commands.locale_str("View the current allow list and feature status", i18n_key="cmd.stickyrole.stickyrole.list.desc"))
    @app_commands.checks.has_permissions(administrator=True)
    async def list_config(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        enabled = get_server_config(guild_id, "stickyrole_enabled", False)
        allowed: list = get_server_config(guild_id, "stickyrole_allowed_roles", [])
        ignore_bots = get_server_config(guild_id, "stickyrole_ignore_bots", True)
        log_channel_id = get_server_config(guild_id, "stickyrole_log_channel")

        embed = discord.Embed(
            title="📌 StickyRole 設定",
            color=0x5865F2 if enabled else 0x99AAB5,
        )
        embed.add_field(name="功能狀態", value="✅ 啟用" if enabled else "❌ 停用", inline=True)
        embed.add_field(name="忽略機器人", value="是" if ignore_bots else "否", inline=True)

        if log_channel_id:
            channel = interaction.guild.get_channel(log_channel_id)
            embed.add_field(name="日誌頻道", value=channel.mention if channel else f"找不到 (ID: {log_channel_id})", inline=True)
        else:
            embed.add_field(name="日誌頻道", value="未設定", inline=True)

        if allowed:
            role_mentions = []
            for rid in allowed:
                r = interaction.guild.get_role(rid)
                role_mentions.append(r.mention if r else f"已刪除 (ID: `{rid}`)")
            embed.add_field(name="允許記憶的身份組", value="\n".join(role_mentions), inline=False)
        else:
            embed.add_field(name="允許記憶的身份組", value="（未限定，將記憶所有可指派的身份組）", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("clear", i18n_key="cmd.stickyrole.stickyrole.clear.name"), description=app_commands.locale_str("Clear the allow list (back to remembering all roles)", i18n_key="cmd.stickyrole.stickyrole.clear.desc"))
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_roles(self, interaction: discord.Interaction):
        set_server_config(interaction.guild.id, "stickyrole_allowed_roles", [])
        log("允許清單已清空", module_name="StickyRole", guild=interaction.guild, user=interaction.user)
        await interaction.response.send_message("✅ 已清空允許清單，StickyRole 將記憶所有可指派的身份組。", ephemeral=True)

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
        await interaction.response.send_message(f"✅ 已{'啟用' if val else '停用'}忽略機器人帳號。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("set-log-channel", i18n_key="cmd.stickyrole.stickyrole.set_log_channel.name"), description=app_commands.locale_str("Set the StickyRole log channel", i18n_key="cmd.stickyrole.stickyrole.set_log_channel.desc"))
    @app_commands.describe(channel=app_commands.locale_str("Channel for StickyRole logs (leave empty to unset)", i18n_key="cmd.stickyrole.stickyrole.set_log_channel.param.channel"))
    @app_commands.checks.has_permissions(administrator=True)
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        guild_id = interaction.guild.id
        if channel:
            perms = channel.permissions_for(interaction.guild.me)
            if not (perms.view_channel and perms.send_messages):
                await interaction.response.send_message(f"⚠️ 機器人在 {channel.mention} 沒有檢視頻道或發送訊息的權限，請先調整後再設定。", ephemeral=True)
                return
            set_server_config(guild_id, "stickyrole_log_channel", channel.id)
            await interaction.response.send_message(f"✅ StickyRole 日誌頻道已設定為 {channel.mention}。", ephemeral=True)
        else:
            set_server_config(guild_id, "stickyrole_log_channel", None)
            await interaction.response.send_message("✅ 已取消 StickyRole 日誌頻道設定。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("view", i18n_key="cmd.stickyrole.stickyrole.view.name"), description=app_commands.locale_str("View a user's previously saved roles", i18n_key="cmd.stickyrole.stickyrole.view.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to view", i18n_key="cmd.stickyrole.stickyrole.view.param.user"))
    @app_commands.checks.has_permissions(administrator=True)
    async def view_user(self, interaction: discord.Interaction, user: discord.User):
        guild_id = interaction.guild.id
        saved: list = get_user_data(guild_id, user.id, "stickyrole_roles", [])
        if not saved:
            await interaction.response.send_message(f"ℹ️ {user.mention} 沒有已儲存的身份組紀錄。", ephemeral=True)
            return
        role_mentions = []
        for rid in saved:
            r = interaction.guild.get_role(rid)
            role_mentions.append(r.mention if r else f"已刪除 (ID: `{rid}`)")
        embed = discord.Embed(title=f"📋 {user} 的 StickyRole 紀錄", color=0x5865F2)
        embed.add_field(name="儲存的身份組", value="\n".join(role_mentions), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("clear-user", i18n_key="cmd.stickyrole.stickyrole.clear_user.name"), description=app_commands.locale_str("Clear a user's StickyRole records", i18n_key="cmd.stickyrole.stickyrole.clear_user.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user whose records to clear", i18n_key="cmd.stickyrole.stickyrole.clear_user.param.user"))
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_user(self, interaction: discord.Interaction, user: discord.User):
        guild_id = interaction.guild.id
        set_user_data(guild_id, user.id, "stickyrole_roles", None)
        log(f"已清除 {user} 的 StickyRole 紀錄", module_name="StickyRole", guild=interaction.guild, user=interaction.user)
        await interaction.response.send_message(f"✅ 已清除 {user.mention} 的 StickyRole 紀錄。", ephemeral=True)

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
        log(f"已記錄 {member} 離開時的 {len(role_ids)} 個身份組", module_name="StickyRole", guild=member.guild, user=member)

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
                await member.add_roles(role, reason="StickyRole 自動恢復身份組")
                restored.append(rid)
            except discord.Forbidden:
                failed.append(rid)
                log(f"無法恢復身份組 {role.name} 給 {member}（權限不足）", level=logging.WARNING, module_name="StickyRole", guild=member.guild, user=member)
            except discord.HTTPException as e:
                failed.append(rid)
                log(f"恢復身份組 {role.name} 給 {member} 時發生錯誤：{e}", level=logging.ERROR, module_name="StickyRole", guild=member.guild, user=member)

        if restored:
            log(f"已恢復 {member} 的 {len(restored)} 個身份組", module_name="StickyRole", guild=member.guild, user=member)
        if failed:
            log(f"無法恢復 {member} 的 {len(failed)} 個身份組", level=logging.WARNING, module_name="StickyRole", guild=member.guild, user=member)

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

        role_mentions = []
        for rid in role_ids:
            r = guild.get_role(rid)
            role_mentions.append(r.mention if r else f"`{rid}`")

        if action == "save":
            embed = discord.Embed(
                title="📤 StickyRole — 身份組已記錄",
                description=f"{user.mention} 離開了伺服器",
                color=0xFFA500,
            )
            embed.add_field(name="記錄的身份組", value=", ".join(role_mentions) if role_mentions else "無", inline=False)
        else:
            embed = discord.Embed(
                title="📥 StickyRole — 身份組已恢復",
                description=f"{user.mention} 重新加入了伺服器",
                color=0x57F287,
            )
            embed.add_field(name="已恢復", value=", ".join(role_mentions) if role_mentions else "無", inline=False)
            if failed:
                failed_mentions = []
                for rid in failed:
                    r = guild.get_role(rid)
                    failed_mentions.append(r.mention if r else f"`{rid}`")
                embed.add_field(name="恢復失敗", value=", ".join(failed_mentions), inline=False)

        embed.set_thumbnail(url=user.display_avatar.url if user.display_avatar else None)
        embed.set_footer(text=f"用戶 ID: {user.id}")

        try:
            await channel.send(embed=embed)
        except Exception as e:
            log(f"無法發送 StickyRole 日誌：{e}", level=logging.ERROR, module_name="StickyRole", guild=guild)


asyncio.run(bot.add_cog(StickyRole(bot)))

if __name__ == "__main__":
    from globalenv import start_bot
    start_bot()

