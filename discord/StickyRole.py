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
class StickyRole(commands.GroupCog, group_name=app_commands.locale_str("stickyrole")):
    """當用戶離開伺服器後重新加入時，自動恢復先前擁有的身份組。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── 管理指令 ──────────────────────────────────────────────

    @app_commands.command(name=app_commands.locale_str("toggle"), description="啟用或停用 StickyRole 功能")
    @app_commands.describe(enable="是否啟用 StickyRole 功能")
    @app_commands.choices(enable=[
        app_commands.Choice(name="啟用", value="True"),
        app_commands.Choice(name="停用", value="False"),
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

    @app_commands.command(name=app_commands.locale_str("add"), description="新增允許記憶的身份組（留空代表記憶所有身份組）")
    @app_commands.describe(role="要加入允許清單的身份組")
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

    @app_commands.command(name=app_commands.locale_str("remove"), description="從允許清單中移除身份組")
    @app_commands.describe(role="要從允許清單移除的身份組")
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

    @app_commands.command(name=app_commands.locale_str("list"), description="查看目前允許清單與功能狀態")
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

    @app_commands.command(name=app_commands.locale_str("clear"), description="清空允許清單（恢復為記憶所有身份組）")
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_roles(self, interaction: discord.Interaction):
        set_server_config(interaction.guild.id, "stickyrole_allowed_roles", [])
        log("允許清單已清空", module_name="StickyRole", guild=interaction.guild, user=interaction.user)
        await interaction.response.send_message("✅ 已清空允許清單，StickyRole 將記憶所有可指派的身份組。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("ignore-bots"), description="設定是否忽略機器人帳號")
    @app_commands.describe(enable="是否忽略機器人帳號")
    @app_commands.choices(enable=[
        app_commands.Choice(name="是（忽略機器人）", value="True"),
        app_commands.Choice(name="否（包含機器人）", value="False"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def ignore_bots(self, interaction: discord.Interaction, enable: str):
        val = (enable == "True")
        set_server_config(interaction.guild.id, "stickyrole_ignore_bots", val)
        await interaction.response.send_message(f"✅ 已{'啟用' if val else '停用'}忽略機器人帳號。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("set-log-channel"), description="設定 StickyRole 日誌頻道")
    @app_commands.describe(channel="用於記錄 StickyRole 操作的頻道（留空則取消設定）")
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

    @app_commands.command(name=app_commands.locale_str("view"), description="查看指定用戶先前儲存的身份組")
    @app_commands.describe(user="要查看的用戶")
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

    @app_commands.command(name=app_commands.locale_str("clear-user"), description="清除指定用戶的 StickyRole 紀錄")
    @app_commands.describe(user="要清除紀錄的用戶")
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

