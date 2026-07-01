import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from globalenv import bot, get_server_config, set_server_config, start_bot
from logger import log


RULE_NAME = "AntiBeast - block everyone/here and roles"
LEGACY_RULE_NAMES = {"AntiBeast - block everyone/here"}
BASE_KEYWORD_FILTER = ["@everyone", "@here"]
BLOCK_MESSAGE = "AntiBeast 已阻擋 everyone/here 或受保護身分組提及。"


class AntiBeastPermissionError(RuntimeError):
    """Raised when AntiBeast cannot apply because the bot lacks permissions."""


@app_commands.guild_only()
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(manage_guild=True, manage_roles=True)
class AntiBeast(commands.GroupCog, name="antibeast"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _default_config() -> dict:
        return {
            "enabled": False,
            "bypass_roles": [],
            "rule_id": None,
            "everyone_mention_before": None,
        }

    def _get_config(self, guild_id: int) -> dict:
        config = get_server_config(guild_id, "antibeast", self._default_config())
        if not isinstance(config, dict):
            config = {}

        merged = self._default_config()
        merged.update(config)
        merged["enabled"] = bool(merged.get("enabled", False))
        merged["bypass_roles"] = self._normalize_role_ids(merged.get("bypass_roles", []))
        return merged

    @staticmethod
    def _normalize_role_ids(role_ids) -> list[int]:
        normalized = []
        seen = set()
        for role_id in role_ids or []:
            try:
                role_id = int(role_id)
            except (TypeError, ValueError):
                continue
            if role_id in seen:
                continue
            seen.add(role_id)
            normalized.append(role_id)
        return normalized

    @staticmethod
    def _required_bot_permissions(guild: discord.Guild) -> list[str]:
        missing = []
        bot_member = guild.me
        if bot_member is None:
            return ["管理伺服器", "管理身分組"]

        permissions = bot_member.guild_permissions
        if not permissions.manage_guild:
            missing.append("管理伺服器")
        if not permissions.manage_roles:
            missing.append("管理身分組")
        return missing

    def _resolve_bypass_roles(self, guild: discord.Guild, config: dict) -> list[discord.Role]:
        roles = []
        role_ids = []
        for role_id in self._normalize_role_ids(config.get("bypass_roles", [])):
            role = guild.get_role(role_id)
            if role is None or role.is_default():
                continue
            roles.append(role)
            role_ids.append(role.id)
        config["bypass_roles"] = role_ids
        return roles

    def _build_keyword_filter(self, guild: discord.Guild, config: dict) -> list[str]:
        bypass_role_ids = set(config.get("bypass_roles", []))
        role_keywords = [
            role.mention
            for role in guild.roles
            if not role.is_default() and role.id not in bypass_role_ids
        ]
        return [*BASE_KEYWORD_FILTER, *role_keywords]

    async def _find_rule(self, guild: discord.Guild, config: dict) -> discord.AutoModRule | None:
        rule_id = config.get("rule_id")
        if rule_id:
            try:
                return await guild.fetch_automod_rule(int(rule_id))
            except (TypeError, ValueError):
                config["rule_id"] = None
            except discord.NotFound:
                config["rule_id"] = None

        for rule in await guild.fetch_automod_rules():
            if rule.name == RULE_NAME or rule.name in LEGACY_RULE_NAMES:
                config["rule_id"] = rule.id
                return rule
        return None

    def _build_trigger(self, guild: discord.Guild, config: dict) -> discord.AutoModTrigger:
        return discord.AutoModTrigger(
            type=discord.AutoModRuleTriggerType.keyword,
            keyword_filter=self._build_keyword_filter(guild, config),
        )

    def _build_actions(self) -> list[discord.AutoModRuleAction]:
        return [
            discord.AutoModRuleAction(
                type=discord.AutoModRuleActionType.block_message,
                custom_message=BLOCK_MESSAGE,
            )
        ]

    async def _sync_rule(
        self,
        guild: discord.Guild,
        config: dict,
        *,
        enabled: bool,
        create_if_missing: bool,
        reason: str,
    ) -> discord.AutoModRule | None:
        self._resolve_bypass_roles(guild, config)
        rule = await self._find_rule(guild, config)
        rule_kwargs = {
            "name": RULE_NAME,
            "event_type": discord.AutoModRuleEventType.message_send,
            "trigger": self._build_trigger(guild, config),
            "actions": self._build_actions(),
            "enabled": enabled,
            "exempt_roles": [],
            "exempt_channels": [],
            "reason": reason,
        }

        if rule is None:
            if not create_if_missing:
                return None
            rule = await guild.create_automod_rule(**rule_kwargs)
        else:
            rule = await rule.edit(**rule_kwargs)

        config["rule_id"] = rule.id
        return rule

    @staticmethod
    async def _set_everyone_mention(
        guild: discord.Guild,
        enabled: bool,
        *,
        reason: str,
    ) -> bool:
        default_role = guild.default_role
        if default_role.permissions.mention_everyone == enabled:
            return False

        permissions = discord.Permissions(default_role.permissions.value)
        permissions.update(mention_everyone=enabled)
        await default_role.edit(permissions=permissions, reason=reason)
        return True

    async def _apply_state(
        self,
        guild: discord.Guild,
        config: dict,
        *,
        enabled: bool,
        reason: str,
    ) -> tuple[discord.AutoModRule | None, bool]:
        missing = self._required_bot_permissions(guild)
        if missing:
            raise AntiBeastPermissionError(f"機器人缺少權限：{'、'.join(missing)}")

        if enabled:
            if config.get("everyone_mention_before") is None:
                config["everyone_mention_before"] = guild.default_role.permissions.mention_everyone
            rule = await self._sync_rule(
                guild,
                config,
                enabled=True,
                create_if_missing=True,
                reason=reason,
            )
            everyone_changed = await self._set_everyone_mention(guild, True, reason=reason)
        else:
            restore = config.get("everyone_mention_before")
            restore_enabled = bool(restore) if restore is not None else False
            everyone_changed = await self._set_everyone_mention(guild, restore_enabled, reason=reason)
            rule = await self._sync_rule(
                guild,
                config,
                enabled=False,
                create_if_missing=False,
                reason=reason,
            )
            config["everyone_mention_before"] = None

        config["enabled"] = enabled
        return rule, everyone_changed

    async def _send_sync_error(self, interaction: discord.Interaction, error: Exception):
        if isinstance(error, AntiBeastPermissionError):
            message = f"⚠️ AntiBeast 同步失敗：{error}"
        elif isinstance(error, discord.Forbidden):
            message = f"⚠️ AntiBeast 同步失敗：{error.text or error}"
        elif isinstance(error, discord.HTTPException):
            message = f"⚠️ AntiBeast 同步失敗：Discord API 回應錯誤 ({error.status})。"
        else:
            message = "⚠️ AntiBeast 同步失敗，請稍後再試。"

        log(
            f"AntiBeast 同步失敗: {error}",
            level=logging.ERROR,
            module_name="AntiBeast",
            guild=interaction.guild,
            user=interaction.user,
        )
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(name="about", description="關於 AntiBeast")
    async def about(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="AntiBeast",
            description=(
                "眾所周知，現在 Discord 有非常多的 Mr. Beast 圖片詐騙。\n"
                "AntiBeast 會用 Discord 原生 AutoMod 阻擋 everyone/here 與身分組提及，"
                "同時讓詐騙機器人以為伺服器允許大量提及。"
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="AutoMod",
            value=(
                "啟用時會建立/更新 AntiBeast 專用 AutoMod 規則，封鎖 everyone/here 與所有非繞過身分組提及。\n"
                "同時會暫時開啟 @everyone 的提及 everyone/here/所有身分組權限；停用時會還原原本設定。"
            ),
            inline=False,
        )
        embed.add_field(
            name="繞過",
            value=(
                "可以把需要正常被提及的身分組加入繞過清單；"
                "這些身分組不會被放進 AutoMod keyword filter。"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="toggle", description="啟用/停用 AntiBeast")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(enable="留空則切換目前狀態")
    async def toggle(self, interaction: discord.Interaction, enable: bool = None):
        await interaction.response.defer(ephemeral=True)

        config = self._get_config(interaction.guild.id)
        enabled = not config["enabled"] if enable is None else enable
        reason = f"AntiBeast toggled by {interaction.user} ({interaction.user.id})"

        try:
            rule, everyone_changed = await self._apply_state(
                interaction.guild,
                config,
                enabled=enabled,
                reason=reason,
            )
        except Exception as error:
            await self._send_sync_error(interaction, error)
            return

        set_server_config(interaction.guild.id, "antibeast", config)
        status = "啟用" if enabled else "停用"
        rule_text = f"AutoMod 規則 ID：`{rule.id}`" if rule else "沒有找到既有 AutoMod 規則。"
        everyone_text = "已更新 @everyone 權限" if everyone_changed else "@everyone 權限已是目標狀態"
        log(
            f"AntiBeast 已{status}",
            module_name="AntiBeast",
            guild=interaction.guild,
            user=interaction.user,
        )
        await interaction.followup.send(
            f"✅ AntiBeast 已 **{status}**。\n{rule_text}\n{everyone_text}。",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="bypass", description="新增/移除想要被繞過的身分組")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(role="要切換繞過狀態的身分組")
    async def bypass(self, interaction: discord.Interaction, role: discord.Role):
        if role.is_default():
            await interaction.response.send_message("⚠️ 不能把 @everyone 加入繞過清單。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        config = self._get_config(interaction.guild.id)
        bypass_roles = config["bypass_roles"]
        if role.id in bypass_roles:
            bypass_roles.remove(role.id)
            action = "移除"
        else:
            bypass_roles.append(role.id)
            action = "新增"

        if config["enabled"]:
            reason = f"AntiBeast bypass updated by {interaction.user} ({interaction.user.id})"
            try:
                await self._sync_rule(
                    interaction.guild,
                    config,
                    enabled=True,
                    create_if_missing=True,
                    reason=reason,
                )
            except Exception as error:
                await self._send_sync_error(interaction, error)
                return

        set_server_config(interaction.guild.id, "antibeast", config)
        log(
            f"AntiBeast 繞過清單{action} {role.name} ({role.id})",
            module_name="AntiBeast",
            guild=interaction.guild,
            user=interaction.user,
        )
        suffix = "並已同步 AutoMod 關鍵字規則" if config["enabled"] else "啟用時會套用到 AutoMod 關鍵字規則"
        await interaction.followup.send(
            f"✅ 已{action} **{role.name}**，{suffix}。",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="list", description="列出 AntiBeast 設定")
    @app_commands.default_permissions(administrator=True)
    async def list_config(self, interaction: discord.Interaction):
        config = self._get_config(interaction.guild.id)
        roles = self._resolve_bypass_roles(interaction.guild, config)
        protected_role_count = max(len(interaction.guild.roles) - 1 - len(roles), 0)
        set_server_config(interaction.guild.id, "antibeast", config)

        embed = discord.Embed(
            title="AntiBeast 設定",
            color=discord.Color.green() if config["enabled"] else discord.Color.light_grey(),
        )
        embed.add_field(name="狀態", value="✅ 啟用" if config["enabled"] else "❌ 停用", inline=True)
        embed.add_field(
            name="@everyone 權限",
            value="可提及 everyone/here" if interaction.guild.default_role.permissions.mention_everyone else "不可提及 everyone/here",
            inline=True,
        )
        embed.add_field(
            name="AutoMod 規則",
            value=f"`{config['rule_id']}`" if config.get("rule_id") else "尚未建立",
            inline=True,
        )
        embed.add_field(
            name="受保護身分組",
            value=f"{protected_role_count} 個身分組會被放進 keyword filter",
            inline=False,
        )
        embed.add_field(
            name="繞過身分組",
            value="\n".join(role.mention for role in roles) if roles else "目前沒有任何想要被繞過的身分組。",
            inline=False,
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _sync_enabled_guild_state(self, guild: discord.Guild, *, reason: str) -> bool:
        config = self._get_config(guild.id)
        if not config["enabled"]:
            return False

        try:
            await self._apply_state(
                guild,
                config,
                enabled=True,
                reason=reason,
            )
        except Exception as error:
            log(
                f"AntiBeast 身分組規則同步失敗: {error}",
                level=logging.ERROR,
                module_name="AntiBeast",
                guild=guild,
            )
            return False

        set_server_config(guild.id, "antibeast", config)
        return True

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self._sync_enabled_guild_state(
                guild,
                reason="AntiBeast startup role reconciliation",
            )

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        await self._sync_enabled_guild_state(
            role.guild,
            reason=f"AntiBeast role created: {role.id}",
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await self._sync_enabled_guild_state(
            role.guild,
            reason=f"AntiBeast role deleted: {role.id}",
        )


asyncio.run(bot.add_cog(AntiBeast(bot)))


if __name__ == "__main__":
    start_bot()
