import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

import Moderate
from globalenv import bot, get_server_config, set_server_config, start_bot
from logger import log

import i18n
from i18n import t


# RULE_NAME / LEGACY_RULE_NAMES 是找回既有 AutoMod 規則的識別字串，不可翻譯。
RULE_NAME = "AntiBeast - block everyone/here and roles"
LEGACY_RULE_NAMES = {"AntiBeast - block everyone/here"}
BASE_KEYWORD_FILTER = ["@everyone", "@here"]
EVERYONE_HERE_KEYWORDS = {"@everyone", "@here"}
AUTOMOD_RULE_LIMIT_ERROR_CODES = {30034}


def default_trigger_action(*, locale: str | None = None) -> str:
    """預設連續觸發處置；`kick` 是 DSL 動作詞，{time_window}/{trigger_count}
    由 _format_trigger_action 在執行前替換，t() 不帶參數所以會原樣保留。"""
    return t("antibeast.default_trigger_action", locale=locale)
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
class AntiBeast(commands.GroupCog, name=app_commands.locale_str("antibeast", i18n_key="cmd.antibeast.antibeast.root.name")):
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
                "action": default_trigger_action(),
                "only_everyone_here": False,
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
            "action": str(kick_config.get("action") or default_trigger_action()).strip()[:500],
            "only_everyone_here": bool(kick_config.get("only_everyone_here", False)),
        }

    @staticmethod
    def _format_action_scope(kick_config: dict) -> str:
        if kick_config.get("only_everyone_here", False):
            return t("antibeast.scope.everyone_here_only")
        return t("antibeast.scope.everyone_here_and_roles")

    @staticmethod
    def _expand_action_string(action: str, guild_id: int | None) -> tuple[list[str], str | None]:
        action = (action or "").strip()
        if not action:
            return [], t("antibeast.err.action_empty")
        if len(action) > 500:
            return [], t("antibeast.err.action_too_long")

        try:
            custom_actions = Moderate._load_custom_action_strings(guild_id)
            actions = Moderate._expand_custom_action_aliases(action, custom_actions)
        except ValueError as error:
            return [], str(error)

        if len(actions) > 5:
            return [], t("moderate.err.too_many_actions")

        for expanded_action in actions:
            prefix = expanded_action.strip().split(" ", 1)[0]
            if prefix not in SUPPORTED_ACTION_PREFIXES:
                return [], t("antibeast.err.unsupported_action", action=prefix)
        return actions, None

    @staticmethod
    def _required_bot_permissions(guild: discord.Guild) -> list[str]:
        manage_guild = t("mentionlimit.perm.manage_guild")
        manage_roles = t("mentionlimit.perm.manage_roles")
        missing = []
        bot_member = guild.me
        if bot_member is None:
            return [manage_guild, manage_roles]

        permissions = bot_member.guild_permissions
        if not permissions.manage_guild:
            missing.append(manage_guild)
        if not permissions.manage_roles:
            missing.append(manage_roles)
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

    def _build_actions(self, guild: discord.Guild) -> list[discord.AutoModRuleAction]:
        return [
            discord.AutoModRuleAction(
                type=discord.AutoModRuleActionType.block_message,
                custom_message=t("antibeast.block_message",
                                 locale=i18n.resolve_locale(guild_id=guild.id)),
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
            "actions": self._build_actions(guild),
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
            raise AntiBeastPermissionError(t("antibeast.err.bot_missing_perms", perms=i18n.join_list(missing)))

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
            message = t("antibeast.err.sync_failed", error=error)
        elif isinstance(error, discord.Forbidden):
            message = t("antibeast.err.sync_failed", error=error.text or error)
        elif isinstance(error, discord.HTTPException):
            if self._is_automod_rule_limit_error(error):
                message = t("antibeast.err.rule_limit_reached")
            else:
                message = t("antibeast.err.sync_http", status=error.status)
        else:
            message = t("antibeast.err.sync_generic")

        log(
            f"AntiBeast sync failed: {error}",
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
            for keyword in ("maximum", "limit", "too many", "reached", "已達", "上限")  # i18n: skip (API error matching)
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

    @staticmethod
    def _execution_mentions_everyone_or_here(execution: discord.AutoModAction) -> bool:
        for value in (
            getattr(execution, "matched_keyword", None),
            getattr(execution, "matched_content", None),
            getattr(execution, "content", None),
        ):
            if not isinstance(value, str):
                continue
            lowered = value.casefold()
            if any(keyword in lowered for keyword in EVERYONE_HERE_KEYWORDS):
                return True
        return False

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
                f"AntiBeast trigger action is invalid: {error}",
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
                f"AntiBeast failed to run the trigger action: {error}",
                level=logging.ERROR,
                module_name="AntiBeast",
                guild=guild,
                user=member,
            )
            return False

        log(
            f"AntiBeast ran the trigger action on {member}: {formatted_action} / {result}",
            module_name="AntiBeast",
            guild=guild,
            user=member,
        )
        return True

    def _build_about_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="AntiBeast",
            description=t("antibeast.about.desc"),
            color=discord.Color.blue(),
        )
        embed.add_field(name="AutoMod", value=t("antibeast.about.automod_body"), inline=False)
        embed.add_field(name=t("antibeast.about.bypass_title"), value=t("antibeast.about.bypass_body"), inline=False)
        embed.add_field(name=t("antibeast.field.trigger_action"), value=t("antibeast.about.trigger_action_body"), inline=False)
        return embed

    def _build_config_embed(self, guild: discord.Guild, config: dict) -> discord.Embed:
        roles = self._resolve_bypass_roles(guild, config)
        protected_role_count = max(len(guild.roles) - 1 - len(roles), 0)
        embed = discord.Embed(
            title=t("antibeast.config.title"),
            color=discord.Color.green() if config["enabled"] else discord.Color.light_grey(),
        )
        embed.add_field(name=t("antibeast.field.status"),
                        value=t("antibeast.state.enabled") if config["enabled"] else t("antibeast.state.disabled"),
                        inline=True)
        embed.add_field(
            name=t("antibeast.field.everyone_permission"),
            value=t("antibeast.config.everyone_can_mention") if guild.default_role.permissions.mention_everyone
            else t("antibeast.config.everyone_cannot_mention"),
            inline=True,
        )
        embed.add_field(
            name=t("antibeast.field.automod_rule"),
            value=f"`{config['rule_id']}`" if config.get("rule_id") else t("antibeast.config.rule_not_created"),
            inline=True,
        )
        embed.add_field(
            name=t("antibeast.field.protected_roles"),
            value=t("antibeast.config.protected_role_count", count=protected_role_count),
            inline=False,
        )
        kick_config = config["kick"]
        kick_text = (
            t("antibeast.config.trigger_enabled",
              seconds=kick_config["time_window"], count=kick_config["threshold"],
              action=kick_config["action"], scope=self._format_action_scope(kick_config))
            if kick_config["enabled"]
            else t("antibeast.config.trigger_disabled", scope=self._format_action_scope(kick_config))
        )
        embed.add_field(name=t("antibeast.field.trigger_action"), value=kick_text, inline=False)
        embed.add_field(
            name=t("antibeast.field.bypass_roles"),
            value="\n".join(role.mention for role in roles) if roles else t("antibeast.config.no_bypass_roles"),
            inline=False,
        )
        return embed

    @app_commands.command(name=app_commands.locale_str("about", i18n_key="cmd.antibeast.antibeast.about.name"), description=app_commands.locale_str("About AntiBeast", i18n_key="cmd.antibeast.antibeast.about.desc"))
    async def about(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_about_embed(), ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("setup", i18n_key="cmd.antibeast.antibeast.setup.name"), description=app_commands.locale_str("Interactively configure and enable AntiBeast", i18n_key="cmd.antibeast.antibeast.setup.desc"))
    @app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        config = self._get_config(interaction.guild.id)
        view = AntiBeastSetupView(self, interaction.user, interaction.guild, config)
        await view.send_about(interaction)

    @app_commands.command(name=app_commands.locale_str("toggle", i18n_key="cmd.antibeast.antibeast.toggle.name"), description=app_commands.locale_str("Enable/disable AntiBeast", i18n_key="cmd.antibeast.antibeast.toggle.desc"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(enable=app_commands.locale_str("Leave empty to toggle the current state", i18n_key="cmd.antibeast.antibeast.toggle.param.enable"))
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
        rule_text = t("antibeast.toggle.rule_id", rule_id=rule.id) if rule else t("antibeast.toggle.no_rule")
        everyone_text = t("antibeast.toggle.everyone_updated") if everyone_changed else t("antibeast.toggle.everyone_unchanged")
        log(
            f"AntiBeast {'enabled' if enabled else 'disabled'}",
            module_name="AntiBeast",
            guild=interaction.guild,
            user=interaction.user,
        )
        await interaction.followup.send(
            t("antibeast.toggle.enabled" if enabled else "antibeast.toggle.disabled")
            + f"\n{rule_text}\n{everyone_text}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name=app_commands.locale_str("bypass", i18n_key="cmd.antibeast.antibeast.bypass.name"), description=app_commands.locale_str("Add/remove roles that bypass AntiBeast", i18n_key="cmd.antibeast.antibeast.bypass.desc"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(role=app_commands.locale_str("The role to toggle bypass for", i18n_key="cmd.antibeast.antibeast.bypass.param.role"))
    async def bypass(self, interaction: discord.Interaction, role: discord.Role):
        if role.is_default():
            await interaction.response.send_message(t("antibeast.err.cannot_bypass_everyone"), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        config = self._get_config(interaction.guild.id)
        bypass_roles = config["bypass_roles"]
        if role.id in bypass_roles:
            bypass_roles.remove(role.id)
            removed = True
        else:
            bypass_roles.append(role.id)
            removed = False

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
            f"AntiBeast bypass list {'removed' if removed else 'added'} {role.name} ({role.id})",
            module_name="AntiBeast",
            guild=interaction.guild,
            user=interaction.user,
        )
        suffix = (t("antibeast.bypass.synced") if config["enabled"]
                  else t("antibeast.bypass.applies_when_enabled"))
        await interaction.followup.send(
            t("antibeast.bypass.removed" if removed else "antibeast.bypass.added",
              role=role.name, suffix=suffix),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name=app_commands.locale_str("settings", i18n_key="cmd.antibeast.antibeast.settings.name"), description=app_commands.locale_str("Configure what AntiBeast does after repeated triggers in a short time", i18n_key="cmd.antibeast.antibeast.settings.desc"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        enable=app_commands.locale_str("Enable automatic action", i18n_key="cmd.antibeast.antibeast.settings.param.enable"),
        threshold=app_commands.locale_str("Act after this many triggers within the window (1-20)", i18n_key="cmd.antibeast.antibeast.settings.param.threshold"),
        time_window=app_commands.locale_str("Time window in seconds (5-3600)", i18n_key="cmd.antibeast.antibeast.settings.param.time_window"),
        action=app_commands.locale_str("Moderate action command; leave empty to keep the current setting", i18n_key="cmd.antibeast.antibeast.settings.param.action"),
        only_everyone_here=app_commands.locale_str("Only act on members who mention @everyone or @here", i18n_key="cmd.antibeast.antibeast.settings.param.only_everyone_here"),
    )
    @app_commands.autocomplete(action=Moderate.action_input_autocomplete)
    async def settings(
        self,
        interaction: discord.Interaction,
        enable: bool = None,
        threshold: int = None,
        time_window: int = None,
        action: str = None,
        only_everyone_here: bool = None,
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
                await interaction.response.send_message(t("antibeast.err.threshold_range_param"), ephemeral=True)
                return
            kick_config["threshold"] = threshold
            changed = True

        if time_window is not None:
            if time_window < 5 or time_window > 3600:
                await interaction.response.send_message(t("antibeast.err.time_window_range_param"), ephemeral=True)
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

        if only_everyone_here is not None:
            kick_config["only_everyone_here"] = only_everyone_here
            changed = True

        kick_config = self._normalize_kick_config(kick_config)
        config["kick"] = kick_config

        if not kick_config["enabled"]:
            status = t("antibeast.config.trigger_disabled", scope=self._format_action_scope(kick_config))
        else:
            status = t("antibeast.config.trigger_enabled",
                       seconds=kick_config["time_window"], count=kick_config["threshold"],
                       action=kick_config["action"], scope=self._format_action_scope(kick_config))

        prefix = t("mentionlimit.settings.updated") if changed else t("mentionlimit.settings.current")

        def persist(actor):
            set_server_config(interaction.guild.id, "antibeast", config)
            log(
                f"AntiBeast trigger action settings updated: {kick_config}",
                module_name="AntiBeast",
                guild=interaction.guild,
                user=actor,
            )

        if action_analysis is not None and action_analysis["requires_confirmation"]:
            async def confirm_action(confirm_interaction: discord.Interaction, confirmed: dict):
                persist(confirm_interaction.user)
                await confirm_interaction.response.edit_message(
                    content=prefix + "\n" + t("antibeast.settings.trigger_line", status=status),
                    embed=Moderate.build_action_preview_embed(
                        confirmed,
                        title=t("antibeast.action_setup_done_title"),
                        saved=True,
                    ),
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            await interaction.response.send_message(
                embed=Moderate.build_action_preview_embed(action_analysis, title=t("moderate.confirm_your_intent_title")),
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
            "content": prefix + "\n" + t("antibeast.settings.trigger_line", status=status),
            "ephemeral": True,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if action_analysis is not None:
            response_kwargs["embed"] = Moderate.build_action_preview_embed(
                action_analysis,
                title=t("antibeast.action_setup_done_title"),
                saved=True,
            )
        await interaction.response.send_message(**response_kwargs)

    @app_commands.command(name=app_commands.locale_str("list", i18n_key="cmd.antibeast.antibeast.list.name"), description=app_commands.locale_str("List AntiBeast settings", i18n_key="cmd.antibeast.antibeast.list.desc"))
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
                f"AntiBeast role rule sync failed: {error}",
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
        # listener 不在 choke point 內；觸發處置可能發出公開警告。
        async with i18n.guild_scope(guild.id):
            await self._on_automod_action_impl(execution)

    async def _on_automod_action_impl(self, execution: discord.AutoModAction):
        guild = execution.guild
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

        if kick_config["only_everyone_here"] and not self._execution_mentions_everyone_or_here(execution):
            return

        set_server_config(guild.id, "antibeast", config)
        trigger_count = self._record_trigger(guild.id, execution.user_id, kick_config)
        if trigger_count < kick_config["threshold"]:
            return

        member = await self._get_execution_member(guild, execution)
        if member is None:
            log(
                f"AntiBeast hit the action threshold but couldn't find user {execution.user_id}.",
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


class AntiBeastSetupView(i18n.I18nView):
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
            await interaction.response.send_message(t("mentionlimit.err.not_your_setup"), ephemeral=True)
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
            title=t("antibeast.setup.bypass_title"),
            description=t("antibeast.setup.bypass_desc"),
            color=discord.Color.blurple(),
        )
        roles = self.cog._resolve_bypass_roles(self.guild, self.config)
        embed.add_field(
            name=t("antibeast.setup.current_bypass"),
            value="\n".join(role.mention for role in roles) if roles else t("antibeast.setup.not_configured"),
            inline=False,
        )
        embed.set_footer(text="AntiBeast setup: 2/4")
        await interaction.response.edit_message(embed=embed, view=self, allowed_mentions=discord.AllowedMentions.none())

    async def show_action(self, interaction: discord.Interaction):
        kick_config = self.config["kick"]
        self.clear_items()
        self.add_item(AntiBeastDefaultActionButton())
        self.add_item(AntiBeastCustomActionButton())
        self.add_item(AntiBeastDisableActionButton())
        self.add_item(AntiBeastToggleEveryoneHereOnlyButton(kick_config["only_everyone_here"]))
        self.add_item(AntiBeastBackToBypassButton())
        embed = discord.Embed(
            title=t("antibeast.setup.action_title"),
            description=t("antibeast.setup.action_desc"),
            color=discord.Color.blurple(),
        )
        status = (
            t("antibeast.setup.action_summary",
              seconds=kick_config["time_window"], count=kick_config["threshold"], action=kick_config["action"])
            if kick_config["enabled"]
            else t("antibeast.setup.action_disabled")
        )
        embed.add_field(name=t("antibeast.setup.current_action"), value=status, inline=False)
        embed.add_field(
            name=t("antibeast.setup.action_scope"),
            value=self.cog._format_action_scope(kick_config),
            inline=False,
        )
        embed.add_field(
            name=t("antibeast.setup.variables"),
            value=t("antibeast.setup.variables_body"),
            inline=False,
        )
        embed.set_footer(text="AntiBeast setup: 3/4")
        await interaction.response.edit_message(embed=embed, view=self, allowed_mentions=discord.AllowedMentions.none())

    async def show_confirm(self, interaction: discord.Interaction):
        self.clear_items()
        self.add_item(AntiBeastEnableButton())
        self.add_item(AntiBeastBackToActionButton())
        embed = discord.Embed(
            title=t("antibeast.setup.confirm_title"),
            description=t("antibeast.setup.confirm_desc"),
            color=discord.Color.green(),
        )
        roles = self.cog._resolve_bypass_roles(self.guild, self.config)
        kick_config = self.config["kick"]
        embed.add_field(
            name=t("antibeast.field.bypass_roles"),
            value="\n".join(role.mention for role in roles) if roles else t("antibeast.setup.not_configured"),
            inline=False,
        )
        embed.add_field(
            name=t("antibeast.field.trigger_action"),
            value=(
                t("antibeast.setup.confirm_action_enabled",
                  seconds=kick_config["time_window"], count=kick_config["threshold"],
                  action=kick_config["action"], scope=self.cog._format_action_scope(kick_config))
                if kick_config["enabled"]
                else t("antibeast.setup.confirm_action_disabled",
                       scope=self.cog._format_action_scope(kick_config))
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
        embed.title = t("antibeast.setup.enabled_title")
        kick_config = self.config["kick"]
        if kick_config["enabled"]:
            analysis = Moderate.analyze_action_string(kick_config["action"], self.guild.id)
            if analysis["valid"]:
                embed.add_field(
                    name=t("antibeast.setup.action_preview"),
                    value="\n".join(
                        f"{index}. {line}"
                        for index, line in enumerate(analysis.get("preview", []), 1)
                    ),
                    inline=False,
                )
        set_server_config(self.guild.id, "antibeast", self.config)
        log(
            "AntiBeast enabled via setup",
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
        super().__init__(label=t("common.btn.next"), style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_bypass(interaction)


class AntiBeastBypassRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder=t("antibeast.setup.bypass_select_ph"), min_values=1, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        selected_roles = [role for role in self.values if not role.is_default()]
        self.view.config["bypass_roles"] = [role.id for role in selected_roles]
        await self.view.show_action(interaction)


class AntiBeastKeepBypassButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("antibeast.btn.keep_bypass"), style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_action(interaction)


class AntiBeastClearBypassButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("mentionlimit.btn.clear_and_continue"), style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        self.view.config["bypass_roles"] = []
        await self.view.show_action(interaction)


class AntiBeastDefaultActionButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("antibeast.btn.use_default_kick"), style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        self.view.config["kick"] = self.view.cog._normalize_kick_config(
            {
                "enabled": True,
                "threshold": 2,
                "time_window": 10,
                "action": default_trigger_action(),
                "only_everyone_here": self.view.config["kick"]["only_everyone_here"],
            }
        )
        await self.view.show_confirm(interaction)


class AntiBeastCustomActionButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("antibeast.btn.custom_action"), style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AntiBeastActionModal(self.view))


class AntiBeastDisableActionButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("antibeast.btn.no_action"), style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        kick_config = dict(self.view.config["kick"])
        kick_config["enabled"] = False
        self.view.config["kick"] = self.view.cog._normalize_kick_config(kick_config)
        await self.view.show_confirm(interaction)


class AntiBeastToggleEveryoneHereOnlyButton(discord.ui.Button):
    def __init__(self, enabled: bool):
        label = t("antibeast.btn.everyone_here_only_toggle",
                  state=t("mentionlimit.state.on_plain") if enabled else t("mentionlimit.state.off_plain"))
        style = discord.ButtonStyle.primary if enabled else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, row=1)

    async def callback(self, interaction: discord.Interaction):
        kick_config = dict(self.view.config["kick"])
        kick_config["only_everyone_here"] = not kick_config["only_everyone_here"]
        self.view.config["kick"] = self.view.cog._normalize_kick_config(kick_config)
        await self.view.show_action(interaction)


class AntiBeastBackToBypassButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("antibeast.btn.back_to_bypass"), style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_bypass(interaction)


class AntiBeastBackToActionButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("antibeast.btn.back_to_action"), style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_action(interaction)


class AntiBeastEnableButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("antibeast.btn.enable"), style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        await self.view.finish_enable(interaction)


class AntiBeastActionModal(i18n.I18nModal, title=i18n.K("antibeast.modal.action_title")):
    def __init__(self, setup_view: AntiBeastSetupView):
        super().__init__()
        self.setup_view = setup_view
        kick_config = setup_view.config["kick"]
        self.threshold = discord.ui.TextInput(
            label=t("antibeast.modal.threshold_label"),
            default=str(kick_config["threshold"]),
            placeholder="2",
            max_length=2,
        )
        self.time_window = discord.ui.TextInput(
            label=t("antibeast.modal.time_window_label"),
            default=str(kick_config["time_window"]),
            placeholder="10",
            max_length=4,
        )
        self.action = discord.ui.TextInput(
            label=t("antibeast.modal.action_label"),
            default=kick_config["action"],
            placeholder=default_trigger_action(),
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
            await interaction.response.send_message(t("antibeast.err.modal_not_int"), ephemeral=True)
            return

        if threshold < 1 or threshold > 20:
            await interaction.response.send_message(t("antibeast.err.threshold_range"), ephemeral=True)
            return
        if time_window < 5 or time_window > 3600:
            await interaction.response.send_message(t("antibeast.err.time_window_range"), ephemeral=True)
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
                    "only_everyone_here": self.setup_view.config["kick"]["only_everyone_here"],
                }
            )

        if analysis["requires_confirmation"]:
            async def confirm_action(confirm_interaction: discord.Interaction, confirmed: dict):
                apply_action(confirmed["normalized"])
                await self.setup_view.show_confirm(confirm_interaction)

            async def cancel_action(cancel_interaction: discord.Interaction):
                await self.setup_view.show_action(cancel_interaction)

            await interaction.response.edit_message(
                embed=Moderate.build_action_preview_embed(analysis, title=t("moderate.confirm_your_intent_title")),
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
