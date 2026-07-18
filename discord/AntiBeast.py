import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

import Moderate
from globalenv import bot, get_server_config, set_server_config, start_bot
from logger import log


RULE_NAME = "AntiBeast - block everyone/here and roles"
LEGACY_RULE_NAMES = {"AntiBeast - block everyone/here"}
BASE_KEYWORD_FILTER = ["@everyone", "@here"]
BLOCK_MESSAGE = "AntiBeast 已阻擋 everyone/here 或受保護身分組提及。"
DEFAULT_TRIGGER_ACTION = "kick AntiBeast: {time_window} 秒內觸發 {trigger_count} 次"
AUTOMOD_RULE_LIMIT_ERROR_CODES = {30034}
SUPPORTED_ACTION_PREFIXES = {
    "ban",
    "kick",
    "mute",
    "timeout",
    "to",
    "unban",
    "unmute",
    "untimeout",
    "delete",
    "warn",
    "send_mod_message",
    "smm",
    "force_verify",
}


class AntiBeastPermissionError(RuntimeError):
    """Raised when AntiBeast cannot apply because the bot lacks permissions."""


@app_commands.guild_only()
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(manage_guild=True, manage_roles=True)
class AntiBeast(commands.GroupCog, name="antibeast"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._trigger_history: dict[tuple[int, int], list[float]] = {}

    @staticmethod
    def _default_config() -> dict:
        return {
            "enabled": False,
            "bypass_roles": [],
            "rule_id": None,
            "everyone_mention_before": None,
            "kick": {
                "enabled": False,
                "threshold": 2,
                "time_window": 10,
                "action": DEFAULT_TRIGGER_ACTION,
            },
        }

    def _get_config(self, guild_id: int) -> dict:
        config = get_server_config(guild_id, "antibeast", self._default_config())
        if not isinstance(config, dict):
            config = {}

        merged = self._default_config()
        merged.update(config)
        merged["enabled"] = bool(merged.get("enabled", False))
        merged["bypass_roles"] = self._normalize_role_ids(merged.get("bypass_roles", []))
        merged["kick"] = self._normalize_kick_config(merged.get("kick", {}))
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
    def _normalize_kick_config(kick_config) -> dict:
        if not isinstance(kick_config, dict):
            kick_config = {}

        try:
            threshold = int(kick_config.get("threshold", 2))
        except (TypeError, ValueError):
            threshold = 2

        try:
            time_window = int(kick_config.get("time_window", 10))
        except (TypeError, ValueError):
            time_window = 10

        return {
            "enabled": bool(kick_config.get("enabled", False)),
            "threshold": min(max(threshold, 1), 20),
            "time_window": min(max(time_window, 5), 3600),
            "action": str(kick_config.get("action") or DEFAULT_TRIGGER_ACTION).strip()[:500],
        }

    @staticmethod
    def _expand_action_string(action: str, guild_id: int | None) -> tuple[list[str], str | None]:
        action = (action or "").strip()
        if not action:
            return [], "action 不能是空的。"
        if len(action) > 500:
            return [], "action 最多 500 個字。"

        try:
            custom_actions = Moderate._load_custom_action_strings(guild_id)
            actions = Moderate._expand_custom_action_aliases(action, custom_actions)
        except ValueError as error:
            return [], str(error)

        if len(actions) > 5:
            return [], "一次只能執行最多 5 個動作。"

        for expanded_action in actions:
            prefix = expanded_action.strip().split(" ", 1)[0]
            if prefix not in SUPPORTED_ACTION_PREFIXES:
                return [], f"不支援的 Moderate 動作：{prefix}"
        return actions, None

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

    @staticmethod
    def _is_antibeast_rule_object(rule: discord.AutoModRule) -> bool:
        if rule.name != RULE_NAME and rule.name not in LEGACY_RULE_NAMES:
            return False
        if rule.event_type != discord.AutoModRuleEventType.message_send:
            return False

        trigger_type = getattr(rule, "trigger_type", None)
        if trigger_type is None and getattr(rule, "trigger", None) is not None:
            trigger_type = getattr(rule.trigger, "type", None)
        if trigger_type != discord.AutoModRuleTriggerType.keyword:
            return False
        return True

    async def _find_rule(self, guild: discord.Guild, config: dict) -> discord.AutoModRule | None:
        rule_id = config.get("rule_id")
        if rule_id:
            try:
                rule = await guild.fetch_automod_rule(int(rule_id))
            except (TypeError, ValueError):
                config["rule_id"] = None
            except discord.NotFound:
                config["rule_id"] = None
            else:
                if self._is_antibeast_rule_object(rule):
                    return rule
                config["rule_id"] = None

        for rule in await guild.fetch_automod_rules():
            if self._is_antibeast_rule_object(rule):
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
            if self._is_automod_rule_limit_error(error):
                message = (
                    "⚠️ AntiBeast 無法建立 AutoMod 規則：這個伺服器的 Discord AutoMod 規則數量已達上限。"
                    "請先刪除不需要的 AutoMod 規則，或移除舊的 AntiBeast 規則後再試。"
                )
            else:
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

    @staticmethod
    def _is_automod_rule_limit_error(error: discord.HTTPException) -> bool:
        if getattr(error, "code", None) in AUTOMOD_RULE_LIMIT_ERROR_CODES:
            return True

        error_text = " ".join(
            str(part)
            for part in (
                getattr(error, "text", ""),
                getattr(error, "response", ""),
                error,
            )
        ).casefold()
        mentions_automod = "automod" in error_text or "auto moderation" in error_text
        mentions_limit = any(
            keyword in error_text
            for keyword in ("maximum", "limit", "too many", "reached", "已達", "上限")
        )
        return mentions_automod and mentions_limit

    async def _is_antibeast_execution(self, execution: discord.AutoModAction, config: dict) -> bool:
        try:
            execution_rule_id = int(execution.rule_id)
        except (TypeError, ValueError):
            return False

        configured_rule_id = None
        if config.get("rule_id"):
            try:
                configured_rule_id = int(config["rule_id"])
            except (TypeError, ValueError):
                config["rule_id"] = None

        if configured_rule_id is not None and configured_rule_id != execution_rule_id:
            guild = execution.guild
            if guild is None:
                return False

            try:
                configured_rule = await guild.fetch_automod_rule(configured_rule_id)
            except discord.NotFound:
                config["rule_id"] = None
            except discord.HTTPException:
                return False
            else:
                if self._is_antibeast_rule_object(configured_rule):
                    return False
                config["rule_id"] = None

        try:
            rule = await execution.fetch_rule()
        except discord.HTTPException:
            return False

        if self._is_antibeast_rule_object(rule):
            config["rule_id"] = rule.id
            return True
        return False

    async def _get_execution_member(
        self,
        guild: discord.Guild,
        execution: discord.AutoModAction,
    ) -> discord.Member | None:
        member = getattr(execution, "member", None)
        if isinstance(member, discord.Member):
            return member

        member = guild.get_member(execution.user_id)
        if member is not None:
            return member

        try:
            return await guild.fetch_member(execution.user_id)
        except (discord.HTTPException, discord.NotFound):
            return None

    def _record_trigger(self, guild_id: int, user_id: int, kick_config: dict) -> int:
        now = time.monotonic()
        time_window = kick_config["time_window"]
        key = (guild_id, user_id)
        history = [
            timestamp
            for timestamp in self._trigger_history.get(key, [])
            if now - timestamp <= time_window
        ]
        history.append(now)

        if len(history) >= kick_config["threshold"]:
            self._trigger_history.pop(key, None)
        else:
            self._trigger_history[key] = history
        return len(history)

    def _format_trigger_action(
        self,
        action: str,
        *,
        trigger_count: int,
        time_window: int,
    ) -> str:
        return (
            action.replace("{trigger_count}", str(trigger_count))
            .replace("{time_window}", str(time_window))
        )

    async def _run_trigger_action(
        self,
        guild: discord.Guild,
        member: discord.Member,
        *,
        trigger_count: int,
        time_window: int,
        action: str,
    ) -> bool:
        formatted_action = self._format_trigger_action(
            action,
            trigger_count=trigger_count,
            time_window=time_window,
        )
        _, error = self._expand_action_string(formatted_action, guild.id)
        if error:
            log(
                f"AntiBeast 觸發動作無效: {error}",
                level=logging.ERROR,
                module_name="AntiBeast",
                guild=guild,
                user=member,
            )
            return False

        try:
            result = await Moderate.do_action_str(
                formatted_action,
                guild=guild,
                user=member,
                message=None,
                moderator=guild.me,
            )
        except Exception as error:
            log(
                f"AntiBeast 執行觸發動作失敗: {error}",
                level=logging.ERROR,
                module_name="AntiBeast",
                guild=guild,
                user=member,
            )
            return False

        log(
            f"AntiBeast 已對 {member} 執行觸發動作: {formatted_action} / {result}",
            module_name="AntiBeast",
            guild=guild,
            user=member,
        )
        return True

    def _build_about_embed(self) -> discord.Embed:
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
        embed.add_field(
            name="連續觸發處置",
            value="可以設定在指定秒數內觸發 AntiBeast 幾次後，執行 Moderate 動作指令；預設是踢出。",
            inline=False,
        )
        return embed

    def _build_config_embed(self, guild: discord.Guild, config: dict) -> discord.Embed:
        roles = self._resolve_bypass_roles(guild, config)
        protected_role_count = max(len(guild.roles) - 1 - len(roles), 0)
        embed = discord.Embed(
            title="AntiBeast 設定",
            color=discord.Color.green() if config["enabled"] else discord.Color.light_grey(),
        )
        embed.add_field(name="狀態", value="✅ 啟用" if config["enabled"] else "❌ 停用", inline=True)
        embed.add_field(
            name="@everyone 權限",
            value="可提及 everyone/here" if guild.default_role.permissions.mention_everyone else "不可提及 everyone/here",
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
        kick_config = config["kick"]
        kick_text = (
            f"✅ 啟用，{kick_config['time_window']} 秒內觸發 {kick_config['threshold']} 次後執行："
            f"`{kick_config['action']}`"
            if kick_config["enabled"]
            else "❌ 停用"
        )
        embed.add_field(name="連續觸發處置", value=kick_text, inline=False)
        embed.add_field(
            name="繞過身分組",
            value="\n".join(role.mention for role in roles) if roles else "目前沒有任何想要被繞過的身分組。",
            inline=False,
        )
        return embed

    @app_commands.command(name="about", description="關於 AntiBeast")
    async def about(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_about_embed(), ephemeral=True)

    @app_commands.command(name="setup", description="互動式設定並啟用 AntiBeast")
    @app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        config = self._get_config(interaction.guild.id)
        view = AntiBeastSetupView(self, interaction.user, interaction.guild, config)
        await view.send_about(interaction)

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
            f"✅ AntiBeast 已**{status}**。\n{rule_text}\n{everyone_text}。",
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

    @app_commands.command(name="settings", description="設定 AntiBeast 短時間多次觸發時的處置動作")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        enable="是否啟用自動處置",
        threshold="時間窗口內觸發幾次後處置（1-20）",
        time_window="時間窗口秒數（5-3600）",
        action="Moderate 動作指令，留空則保留目前設定",
    )
    @app_commands.autocomplete(action=Moderate.action_input_autocomplete)
    async def settings(
        self,
        interaction: discord.Interaction,
        enable: bool = None,
        threshold: int = None,
        time_window: int = None,
        action: str = None,
    ):
        config = self._get_config(interaction.guild.id)
        kick_config = dict(config["kick"])
        changed = False
        action_analysis = None

        if enable is not None:
            kick_config["enabled"] = enable
            changed = True

        if threshold is not None:
            if threshold < 1 or threshold > 20:
                await interaction.response.send_message("⚠️ threshold 必須介於 1 到 20。", ephemeral=True)
                return
            kick_config["threshold"] = threshold
            changed = True

        if time_window is not None:
            if time_window < 5 or time_window > 3600:
                await interaction.response.send_message("⚠️ time_window 必須介於 5 到 3600 秒。", ephemeral=True)
                return
            kick_config["time_window"] = time_window
            changed = True

        if action is not None:
            action = action.strip()
            action_analysis = Moderate.analyze_action_string(action, interaction.guild.id)
            if not action_analysis["valid"]:
                await interaction.response.send_message(
                    embed=Moderate.build_action_preview_embed(action_analysis),
                    ephemeral=True,
                )
                return
            kick_config["action"] = action_analysis["normalized"]
            changed = True

        kick_config = self._normalize_kick_config(kick_config)
        config["kick"] = kick_config

        if not kick_config["enabled"]:
            status = "❌ 停用"
        else:
            status = (
                f"✅ 啟用，{kick_config['time_window']} 秒內觸發 {kick_config['threshold']} 次後執行："
                f"`{kick_config['action']}`"
            )

        prefix = "已更新設定。" if changed else "目前設定："

        def persist(actor):
            set_server_config(interaction.guild.id, "antibeast", config)
            log(
                f"AntiBeast 自動處置設定更新: {kick_config}",
                module_name="AntiBeast",
                guild=interaction.guild,
                user=actor,
            )

        if action_analysis is not None and action_analysis["requires_confirmation"]:
            async def confirm_action(confirm_interaction: discord.Interaction, confirmed: dict):
                persist(confirm_interaction.user)
                await confirm_interaction.response.edit_message(
                    content=f"{prefix}\n自動處置：{status}",
                    embed=Moderate.build_action_preview_embed(
                        confirmed,
                        title="AntiBeast 動作設定完成",
                        saved=True,
                    ),
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            await interaction.response.send_message(
                embed=Moderate.build_action_preview_embed(action_analysis, title="確認你的意思"),
                view=Moderate.ActionConfirmationView(
                    interaction.user.id,
                    action_analysis,
                    confirm_action,
                ),
                ephemeral=True,
            )
            return

        persist(interaction.user)
        response_kwargs = {
            "content": f"{prefix}\n自動處置：{status}",
            "ephemeral": True,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if action_analysis is not None:
            response_kwargs["embed"] = Moderate.build_action_preview_embed(
                action_analysis,
                title="AntiBeast 動作設定完成",
                saved=True,
            )
        await interaction.response.send_message(**response_kwargs)

    @app_commands.command(name="list", description="列出 AntiBeast 設定")
    @app_commands.default_permissions(administrator=True)
    async def list_config(self, interaction: discord.Interaction):
        config = self._get_config(interaction.guild.id)
        embed = self._build_config_embed(interaction.guild, config)
        set_server_config(interaction.guild.id, "antibeast", config)
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

    @commands.Cog.listener()
    async def on_automod_action(self, execution: discord.AutoModAction):
        guild = execution.guild
        if guild is None:
            return

        config = self._get_config(guild.id)
        kick_config = config["kick"]
        if not config["enabled"] or not kick_config["enabled"]:
            return

        previous_rule_id = config.get("rule_id")
        is_antibeast_execution = await self._is_antibeast_execution(execution, config)
        if config.get("rule_id") != previous_rule_id:
            set_server_config(guild.id, "antibeast", config)
        if not is_antibeast_execution:
            return

        set_server_config(guild.id, "antibeast", config)
        trigger_count = self._record_trigger(guild.id, execution.user_id, kick_config)
        if trigger_count < kick_config["threshold"]:
            return

        member = await self._get_execution_member(guild, execution)
        if member is None:
            log(
                f"AntiBeast 達到處置門檻，但找不到用戶 {execution.user_id}。",
                level=logging.WARNING,
                module_name="AntiBeast",
                guild=guild,
            )
            return

        await self._run_trigger_action(
            guild,
            member,
            trigger_count=trigger_count,
            time_window=kick_config["time_window"],
            action=kick_config["action"],
        )


class AntiBeastSetupView(discord.ui.View):
    def __init__(
        self,
        cog: AntiBeast,
        owner: discord.abc.User,
        guild: discord.Guild,
        config: dict,
    ):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner = owner
        self.guild = guild
        self.config = config

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("這個設定流程不是你的。", ephemeral=True)
            return False
        return True

    async def send_about(self, interaction: discord.Interaction):
        self.clear_items()
        self.add_item(AntiBeastSetupContinueButton())
        embed = self.cog._build_about_embed()
        embed.set_footer(text="AntiBeast setup: 1/4")
        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)

    async def show_bypass(self, interaction: discord.Interaction):
        self.clear_items()
        self.add_item(AntiBeastBypassRoleSelect())
        self.add_item(AntiBeastKeepBypassButton())
        self.add_item(AntiBeastClearBypassButton())
        embed = discord.Embed(
            title="AntiBeast Setup: 繞過身分組",
            description=(
                "選擇需要正常被提及的身分組。\n"
                "被選到的身分組不會被放進 AntiBeast 的 AutoMod keyword filter。"
            ),
            color=discord.Color.blurple(),
        )
        roles = self.cog._resolve_bypass_roles(self.guild, self.config)
        embed.add_field(
            name="目前繞過",
            value="\n".join(role.mention for role in roles) if roles else "尚未設定",
            inline=False,
        )
        embed.set_footer(text="AntiBeast setup: 2/4")
        await interaction.response.edit_message(embed=embed, view=self, allowed_mentions=discord.AllowedMentions.none())

    async def show_action(self, interaction: discord.Interaction):
        self.clear_items()
        self.add_item(AntiBeastDefaultActionButton())
        self.add_item(AntiBeastCustomActionButton())
        self.add_item(AntiBeastDisableActionButton())
        self.add_item(AntiBeastBackToBypassButton())
        embed = discord.Embed(
            title="AntiBeast Setup: 連續觸發處置",
            description=(
                "設定同一個使用者在短時間內連續觸發 AntiBeast 後要執行的 Moderate 指令。\n"
                "預設會在門檻達成後踢出。"
            ),
            color=discord.Color.blurple(),
        )
        kick_config = self.config["kick"]
        status = (
            f"{kick_config['time_window']} 秒內 {kick_config['threshold']} 次，執行 `{kick_config['action']}`"
            if kick_config["enabled"]
            else "目前停用連續觸發處置"
        )
        embed.add_field(name="目前處置", value=status, inline=False)
        embed.add_field(
            name="可用變數",
            value="`{time_window}`、`{trigger_count}` 會在執行前替換成實際數值。",
            inline=False,
        )
        embed.set_footer(text="AntiBeast setup: 3/4")
        await interaction.response.edit_message(embed=embed, view=self, allowed_mentions=discord.AllowedMentions.none())

    async def show_confirm(self, interaction: discord.Interaction):
        self.clear_items()
        self.add_item(AntiBeastEnableButton())
        self.add_item(AntiBeastBackToActionButton())
        embed = discord.Embed(
            title="AntiBeast Setup: 確認啟用",
            description="確認設定後按下啟用，AntiBeast 會建立/更新 AutoMod 規則並套用 @everyone 權限。",
            color=discord.Color.green(),
        )
        roles = self.cog._resolve_bypass_roles(self.guild, self.config)
        kick_config = self.config["kick"]
        embed.add_field(
            name="繞過身分組",
            value="\n".join(role.mention for role in roles) if roles else "尚未設定",
            inline=False,
        )
        embed.add_field(
            name="連續觸發處置",
            value=(
                f"{kick_config['time_window']} 秒內 {kick_config['threshold']} 次，執行 `{kick_config['action']}`"
                if kick_config["enabled"]
                else "停用"
            ),
            inline=False,
        )
        embed.set_footer(text="AntiBeast setup: 4/4")
        await interaction.response.edit_message(embed=embed, view=self, allowed_mentions=discord.AllowedMentions.none())

    async def finish_enable(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        reason = f"AntiBeast setup enabled by {interaction.user} ({interaction.user.id})"

        try:
            await self.cog._apply_state(
                self.guild,
                self.config,
                enabled=True,
                reason=reason,
            )
        except Exception as error:
            await self.cog._send_sync_error(interaction, error)
            return

        embed = self.cog._build_config_embed(self.guild, self.config)
        embed.title = "AntiBeast 已啟用"
        kick_config = self.config["kick"]
        if kick_config["enabled"]:
            analysis = Moderate.analyze_action_string(kick_config["action"], self.guild.id)
            if analysis["valid"]:
                embed.add_field(
                    name="動作執行預覽",
                    value="\n".join(
                        f"{index}. {line}"
                        for index, line in enumerate(analysis.get("preview", []), 1)
                    ),
                    inline=False,
                )
        set_server_config(self.guild.id, "antibeast", self.config)
        log(
            "AntiBeast 已透過 setup 啟用",
            module_name="AntiBeast",
            guild=self.guild,
            user=interaction.user,
        )
        self.clear_items()
        await interaction.edit_original_response(embed=embed, view=self, allowed_mentions=discord.AllowedMentions.none())

    async def on_timeout(self):
        self.clear_items()


class AntiBeastSetupContinueButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="繼續", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_bypass(interaction)


class AntiBeastBypassRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder="選擇要繞過的身分組", min_values=1, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        selected_roles = [role for role in self.values if not role.is_default()]
        self.view.config["bypass_roles"] = [role.id for role in selected_roles]
        await self.view.show_action(interaction)


class AntiBeastKeepBypassButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="保留目前繞過", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_action(interaction)


class AntiBeastClearBypassButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="清空並繼續", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        self.view.config["bypass_roles"] = []
        await self.view.show_action(interaction)


class AntiBeastDefaultActionButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="使用預設踢出", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        self.view.config["kick"] = self.view.cog._normalize_kick_config(
            {
                "enabled": True,
                "threshold": 2,
                "time_window": 10,
                "action": DEFAULT_TRIGGER_ACTION,
            }
        )
        await self.view.show_confirm(interaction)


class AntiBeastCustomActionButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="自訂處置", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AntiBeastActionModal(self.view))


class AntiBeastDisableActionButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="不啟用處置", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        kick_config = dict(self.view.config["kick"])
        kick_config["enabled"] = False
        self.view.config["kick"] = self.view.cog._normalize_kick_config(kick_config)
        await self.view.show_confirm(interaction)


class AntiBeastBackToBypassButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="返回繞過", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_bypass(interaction)


class AntiBeastBackToActionButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="返回處置", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_action(interaction)


class AntiBeastEnableButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="啟用 AntiBeast", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        await self.view.finish_enable(interaction)


class AntiBeastActionModal(discord.ui.Modal, title="AntiBeast 處置設定"):
    def __init__(self, setup_view: AntiBeastSetupView):
        super().__init__()
        self.setup_view = setup_view
        kick_config = setup_view.config["kick"]
        self.threshold = discord.ui.TextInput(
            label="觸發次數",
            default=str(kick_config["threshold"]),
            placeholder="2",
            max_length=2,
        )
        self.time_window = discord.ui.TextInput(
            label="時間窗口秒數",
            default=str(kick_config["time_window"]),
            placeholder="10",
            max_length=4,
        )
        self.action = discord.ui.TextInput(
            label="Moderate 動作指令",
            default=kick_config["action"],
            placeholder=DEFAULT_TRIGGER_ACTION,
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.add_item(self.threshold)
        self.add_item(self.time_window)
        self.add_item(self.action)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            threshold = int(str(self.threshold.value).strip())
            time_window = int(str(self.time_window.value).strip())
        except ValueError:
            await interaction.response.send_message("⚠️ 觸發次數與時間窗口都必須是整數。", ephemeral=True)
            return

        if threshold < 1 or threshold > 20:
            await interaction.response.send_message("⚠️ 觸發次數必須介於 1 到 20。", ephemeral=True)
            return
        if time_window < 5 or time_window > 3600:
            await interaction.response.send_message("⚠️ 時間窗口必須介於 5 到 3600 秒。", ephemeral=True)
            return

        action = str(self.action.value).strip()
        analysis = Moderate.analyze_action_string(action, self.setup_view.guild.id)
        if not analysis["valid"]:
            await interaction.response.send_message(
                embed=Moderate.build_action_preview_embed(analysis),
                ephemeral=True,
            )
            return

        def apply_action(normalized_action: str):
            self.setup_view.config["kick"] = self.setup_view.cog._normalize_kick_config(
                {
                    "enabled": True,
                    "threshold": threshold,
                    "time_window": time_window,
                    "action": normalized_action,
                }
            )

        if analysis["requires_confirmation"]:
            async def confirm_action(confirm_interaction: discord.Interaction, confirmed: dict):
                apply_action(confirmed["normalized"])
                await self.setup_view.show_confirm(confirm_interaction)

            async def cancel_action(cancel_interaction: discord.Interaction):
                await self.setup_view.show_action(cancel_interaction)

            await interaction.response.edit_message(
                embed=Moderate.build_action_preview_embed(analysis, title="確認你的意思"),
                view=Moderate.ActionConfirmationView(
                    interaction.user.id,
                    analysis,
                    confirm_action,
                    cancel_callback=cancel_action,
                ),
            )
            return

        apply_action(analysis["normalized"])
        await self.setup_view.show_confirm(interaction)


asyncio.run(bot.add_cog(AntiBeast(bot)))


if __name__ == "__main__":
    start_bot()
