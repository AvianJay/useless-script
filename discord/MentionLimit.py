import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from globalenv import (
    bot,
    get_all_server_config_key,
    get_server_config,
    set_server_config,
    start_bot,
)
from logger import log

import i18n
from i18n import t


CONFIG_KEY = "mentionlimit"
ANTIBEAST_CONFIG_KEY = "antibeast"
# 用來在 Discord 端找回既有規則的識別字串；翻譯會讓機器人找不到自己建的規則。
RULE_NAME = "MentionLimit - role mention cooldown"
MIN_COOLDOWN = 10
MAX_COOLDOWN = 86400
DEFAULT_COOLDOWN = 600
MAX_MANAGED_ROLES = 25
TRIGGER_DEBOUNCE_SECONDS = 2.0
RESTORE_MAX_FAILURES = 6
GUILD_ABSENT_GRACE_SECONDS = 600
RULE_LIMIT_DEGRADE_SECONDS = 600
AUTOMOD_RULE_LIMIT_ERROR_CODES = {30034}


@app_commands.guild_only()
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(manage_guild=True, manage_roles=True)
class MentionLimit(commands.GroupCog, name=app_commands.locale_str("mentionlimit", i18n_key="cmd.mentionlimit.mentionlimit.root.name")):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_trigger: dict[tuple[int, int], float] = {}
        self._restore_failures: dict[tuple[int, int], int] = {}
        self._guild_locks: dict[int, asyncio.Lock] = {}
        # AutoMod 規則數達上限而降級改用關閉可提及的伺服器 -> 降級時間，逾時後重試 AutoMod。
        self._rule_limit_degraded: dict[int, float] = {}

    # ---------- config ----------

    @staticmethod
    def _default_config() -> dict:
        return {
            "enabled": False,
            "automod_mode": False,
            "count_admins": False,
            "announce": False,
            "rule_id": None,
            "roles": {},
        }

    @staticmethod
    def _default_role_entry(cooldown: int = DEFAULT_COOLDOWN) -> dict:
        return {
            "cooldown": cooldown,
            "cooldown_until": None,
            "mentionable_before": None,
        }

    @staticmethod
    def _parse_datetime(value):
        if isinstance(value, datetime):
            parsed = value
        elif not value or str(value).lower() == "none":
            return None
        else:
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _normalize_roles(cls, roles) -> dict:
        if not isinstance(roles, dict):
            return {}
        normalized = {}
        for raw_role_id, raw_entry in roles.items():
            try:
                role_id = str(int(raw_role_id))
            except (TypeError, ValueError):
                continue
            if not isinstance(raw_entry, dict):
                raw_entry = {}
            try:
                cooldown = int(raw_entry.get("cooldown", DEFAULT_COOLDOWN))
            except (TypeError, ValueError):
                cooldown = DEFAULT_COOLDOWN
            until = cls._parse_datetime(raw_entry.get("cooldown_until"))
            before = raw_entry.get("mentionable_before")
            normalized[role_id] = {
                "cooldown": min(max(cooldown, MIN_COOLDOWN), MAX_COOLDOWN),
                "cooldown_until": until.isoformat() if until else None,
                "mentionable_before": bool(before) if before is not None else None,
            }
            if len(normalized) >= MAX_MANAGED_ROLES:
                break
        return normalized

    def _get_config(self, guild_id: int) -> dict:
        config = get_server_config(guild_id, CONFIG_KEY, self._default_config())
        if not isinstance(config, dict):
            config = {}

        merged = self._default_config()
        merged.update(config)
        merged["enabled"] = bool(merged.get("enabled", False))
        merged["automod_mode"] = bool(merged.get("automod_mode", False))
        merged["count_admins"] = bool(merged.get("count_admins", False))
        merged["announce"] = bool(merged.get("announce", False))
        merged["roles"] = self._normalize_roles(merged.get("roles", {}))
        return merged

    def _active_cooldowns(self, config: dict) -> dict[str, datetime]:
        now = discord.utils.utcnow()
        active = {}
        for role_id, entry in config.get("roles", {}).items():
            until = self._parse_datetime(entry.get("cooldown_until"))
            if until is not None and until > now:
                active[role_id] = until
        return active

    @staticmethod
    def _entry_has_state(entry: dict) -> bool:
        return bool(entry.get("cooldown_until")) or entry.get("mentionable_before") is not None

    def _guild_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._guild_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._guild_locks[guild_id] = lock
        return lock

    # ---------- AntiBeast 相容 ----------

    @staticmethod
    def _antibeast_enabled(guild_id: int) -> bool:
        config = get_server_config(guild_id, ANTIBEAST_CONFIG_KEY, {})
        if not isinstance(config, dict):
            return False
        return bool(config.get("enabled", False))

    def _rule_limit_degraded_active(self, guild_id: int) -> bool:
        degraded_at = self._rule_limit_degraded.get(guild_id)
        if degraded_at is None:
            return False
        if time.monotonic() - degraded_at >= RULE_LIMIT_DEGRADE_SECONDS:
            self._rule_limit_degraded.pop(guild_id, None)
            return False
        return True

    def _effective_automod(self, guild_id: int, config: dict) -> bool:
        # AntiBeast 啟用時 @everyone 擁有 mention_everyone，關閉可提及會失效，強制走 AutoMod。
        if self._rule_limit_degraded_active(guild_id):
            return False
        return bool(config.get("automod_mode", False)) or self._antibeast_enabled(guild_id)

    async def _ensure_antibeast_bypass(self, guild: discord.Guild, role: discord.Role) -> tuple[bool, str | None]:
        antibeast_config = get_server_config(guild.id, ANTIBEAST_CONFIG_KEY, None)
        if not isinstance(antibeast_config, dict) or not antibeast_config.get("enabled"):
            return False, None

        bypass_ids = set()
        for raw_role_id in antibeast_config.get("bypass_roles") or []:
            try:
                bypass_ids.add(int(raw_role_id))
            except (TypeError, ValueError):
                continue
        if role.id in bypass_ids:
            return False, None

        cog = self.bot.get_cog("antibeast")
        if cog is not None:
            try:
                cog_config = cog._get_config(guild.id)
                if role.id not in cog_config["bypass_roles"]:
                    cog_config["bypass_roles"].append(role.id)
                await cog._sync_rule(
                    guild,
                    cog_config,
                    enabled=True,
                    create_if_missing=True,
                    reason=f"MentionLimit auto-added an AntiBeast bypass: {role.id}",
                )
                set_server_config(guild.id, ANTIBEAST_CONFIG_KEY, cog_config)
            except Exception as error:
                log(
                    f"MentionLimit failed to add the AntiBeast bypass: {error}",
                    level=logging.ERROR,
                    module_name="MentionLimit",
                    guild=guild,
                )
                return False, t("mentionlimit.antibeast.bypass_failed", role=role.name)
            return True, t("mentionlimit.antibeast.bypass_added_synced", role=role.name)

        bypass_ids.add(role.id)
        antibeast_config["bypass_roles"] = list(bypass_ids)
        set_server_config(guild.id, ANTIBEAST_CONFIG_KEY, antibeast_config)
        return True, t("mentionlimit.antibeast.bypass_added_pending", role=role.name)

    # ---------- 權限 / AutoMod 規則 ----------

    @staticmethod
    def _required_bot_permissions(guild: discord.Guild, *, automod: bool) -> list[str]:
        manage_roles = t("mentionlimit.perm.manage_roles")
        manage_guild = t("mentionlimit.perm.manage_guild")
        bot_member = guild.me
        if bot_member is None:
            return [manage_roles, manage_guild] if automod else [manage_roles]

        missing = []
        permissions = bot_member.guild_permissions
        if not permissions.manage_roles:
            missing.append(manage_roles)
        if automod and not permissions.manage_guild:
            missing.append(manage_guild)
        return missing

    @staticmethod
    def _is_mentionlimit_rule_object(rule: discord.AutoModRule) -> bool:
        if rule.name != RULE_NAME:
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
                if self._is_mentionlimit_rule_object(rule):
                    return rule
                config["rule_id"] = None

        for rule in await guild.fetch_automod_rules():
            if self._is_mentionlimit_rule_object(rule):
                config["rule_id"] = rule.id
                return rule
        return None

    def _build_keyword_filter(self, guild_id: int, config: dict) -> list[str]:
        if not self._effective_automod(guild_id, config):
            return []
        return [f"<@&{role_id}>" for role_id in self._active_cooldowns(config)]

    async def _sync_rule(self, guild: discord.Guild, config: dict, *, reason: str) -> discord.AutoModRule | None:
        keywords = self._build_keyword_filter(guild.id, config)
        if not keywords:
            # keyword_filter 不能是空的，冷卻全數結束時只停用既有規則。
            if not config.get("rule_id"):
                return None
            rule = await self._find_rule(guild, config)
            if rule is not None and rule.enabled:
                rule = await rule.edit(enabled=False, reason=reason)
            return rule

        rule = await self._find_rule(guild, config)
        rule_kwargs = {
            "name": RULE_NAME,
            "event_type": discord.AutoModRuleEventType.message_send,
            "trigger": discord.AutoModTrigger(
                type=discord.AutoModRuleTriggerType.keyword,
                keyword_filter=keywords,
            ),
            "actions": [
                discord.AutoModRuleAction(
                    type=discord.AutoModRuleActionType.block_message,
                    custom_message=t("mentionlimit.block_message",
                                     locale=i18n.resolve_locale(guild_id=guild.id)),
                )
            ],
            "enabled": True,
            "exempt_roles": [],
            "exempt_channels": [],
            "reason": reason,
        }

        if rule is None:
            rule = await guild.create_automod_rule(**rule_kwargs)
        else:
            rule = await rule.edit(**rule_kwargs)

        config["rule_id"] = rule.id
        self._rule_limit_degraded.pop(guild.id, None)
        return rule

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

    def _rule_sync_error_message(self, error: Exception) -> str:
        if isinstance(error, discord.Forbidden):
            return t("mentionlimit.err.rule_sync_forbidden", error=error.text or error)
        if isinstance(error, discord.HTTPException):
            if self._is_automod_rule_limit_error(error):
                return t("mentionlimit.err.rule_limit_reached")
            return t("mentionlimit.err.rule_sync_http", status=error.status)
        return t("mentionlimit.err.rule_sync_generic")

    def _log_rule_sync_failure(self, guild: discord.Guild, error: Exception):
        log(
            f"MentionLimit AutoMod rule sync failed: {error}",
            level=logging.ERROR,
            module_name="MentionLimit",
            guild=guild,
        )

    # ---------- 冷卻執法 ----------

    async def _end_cooldown(self, guild: discord.Guild, role_id: str, config: dict, *, reason: str) -> bool:
        entry = config["roles"].get(role_id)
        if entry is None or not self._entry_has_state(entry):
            return False

        role = guild.get_role(int(role_id))
        key = (guild.id, int(role_id))
        before = entry.get("mentionable_before")
        if role is not None and before is not None and bool(role.mentionable) != bool(before):
            try:
                await role.edit(mentionable=bool(before), reason=reason)
            except (discord.Forbidden, discord.HTTPException) as error:
                failures = self._restore_failures.get(key, 0) + 1
                self._restore_failures[key] = failures
                if failures == 1:
                    log(
                        f"MentionLimit failed to restore the mentionable state of {role.name} ({role.id}): {error}",
                        level=logging.ERROR,
                        module_name="MentionLimit",
                        guild=guild,
                    )
                if failures < RESTORE_MAX_FAILURES:
                    return False
                log(
                    f"MentionLimit failed to restore {role.name} ({role.id}) {failures} times in a row; "
                    "giving up on automatic restore, please adjust the role's mentionable setting manually.",
                    level=logging.ERROR,
                    module_name="MentionLimit",
                    guild=guild,
                )

        self._restore_failures.pop(key, None)
        entry["cooldown_until"] = None
        entry["mentionable_before"] = None
        return True

    async def _reconcile_enforcement(self, guild: discord.Guild, config: dict, *, reason: str) -> tuple[bool, bool]:
        """讓進行中冷卻的執法方式跟上 _effective_automod（AntiBeast 或 automod_mode 中途切換）。"""
        changed = False
        need_sync = False
        effective = self._effective_automod(guild.id, config)

        for role_id in self._active_cooldowns(config):
            entry = config["roles"][role_id]
            role = guild.get_role(int(role_id))
            if role is None:
                continue
            key = (guild.id, int(role_id))

            if effective and entry.get("mentionable_before") is not None:
                try:
                    if bool(role.mentionable) != bool(entry["mentionable_before"]):
                        await role.edit(mentionable=bool(entry["mentionable_before"]), reason=reason)
                except (discord.Forbidden, discord.HTTPException) as error:
                    failures = self._restore_failures.get(key, 0) + 1
                    self._restore_failures[key] = failures
                    if failures == 1:
                        log(
                            f"MentionLimit failed to restore {role.name} ({role.id}) while switching enforcement mode: {error}",
                            level=logging.ERROR,
                            module_name="MentionLimit",
                            guild=guild,
                        )
                    continue
                self._restore_failures.pop(key, None)
                entry["mentionable_before"] = None
                changed = True
                need_sync = True
                # AntiBeast 中途啟用會攔下所有非繞過身分組的提及，補上繞過讓冷卻結束後仍可被提及。
                await self._ensure_antibeast_bypass(guild, role)
            elif not effective and entry.get("mentionable_before") is None:
                before = bool(role.mentionable)
                try:
                    if role.mentionable:
                        await role.edit(mentionable=False, reason=reason)
                except (discord.Forbidden, discord.HTTPException) as error:
                    failures = self._restore_failures.get(key, 0) + 1
                    self._restore_failures[key] = failures
                    if failures == 1:
                        log(
                            f"MentionLimit failed to disable mentionable on {role.name} ({role.id}) while switching enforcement mode: {error}",
                            level=logging.ERROR,
                            module_name="MentionLimit",
                            guild=guild,
                        )
                    continue
                self._restore_failures.pop(key, None)
                entry["mentionable_before"] = before
                changed = True
                need_sync = True

        return changed, need_sync

    async def _restore_all(self, guild: discord.Guild, config: dict, *, reason: str) -> list[str]:
        """結束所有冷卻並停用規則，回傳還原失敗的 role id。"""
        need_sync = False
        for role_id in list(config["roles"].keys()):
            ended = await self._end_cooldown(guild, role_id, config, reason=reason)
            need_sync = need_sync or ended

        if need_sync or config.get("rule_id"):
            try:
                await self._sync_rule(guild, config, reason=reason)
            except Exception as error:
                self._log_rule_sync_failure(guild, error)

        return [
            role_id
            for role_id, entry in config["roles"].items()
            if self._entry_has_state(entry)
        ]

    # ---------- 觸發 ----------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or not message.role_mentions:
            return
        if self.bot.user and message.author.id == self.bot.user.id:
            return
        # listener 不在 choke point 內；冷卻公告是發到 guild 頻道的共享文字。
        async with i18n.guild_scope(message.guild.id):
            await self._on_message_impl(message)

    async def _on_message_impl(self, message: discord.Message):
        guild = message.guild
        config = self._get_config(guild.id)
        if not config["enabled"] or not config["roles"]:
            return

        matched = [role for role in message.role_mentions if str(role.id) in config["roles"]]
        if not matched:
            return

        if not config["count_admins"]:
            permissions = getattr(message.author, "guild_permissions", None)
            if permissions is not None and (permissions.administrator or permissions.manage_guild or permissions.manage_roles):
                return

        started: list[tuple[discord.Role, datetime]] = []
        announce = False

        async with self._guild_lock(guild.id):
            config = self._get_config(guild.id)
            if not config["enabled"]:
                return
            announce = config["announce"]
            effective_automod = self._effective_automod(guild.id, config)
            need_rule_sync = False
            now_monotonic = time.monotonic()

            for role in matched:
                entry = config["roles"].get(str(role.id))
                if entry is None:
                    continue

                key = (guild.id, role.id)
                last = self._last_trigger.get(key)
                if last is not None and now_monotonic - last < TRIGGER_DEBOUNCE_SECONDS:
                    continue
                self._last_trigger[key] = now_monotonic

                # 先持久化冷卻意圖，執法失敗由排程 loop 收斂；冷卻中再被提及則重置冷卻。
                until = discord.utils.utcnow() + timedelta(seconds=entry["cooldown"])
                entry["cooldown_until"] = until.isoformat()

                if effective_automod:
                    need_rule_sync = True
                else:
                    if entry.get("mentionable_before") is None:
                        entry["mentionable_before"] = bool(role.mentionable)
                    if role.mentionable:
                        try:
                            await role.edit(
                                mentionable=False,
                                reason=t("mentionlimit.audit.cooldown_start", seconds=entry["cooldown"]),
                            )
                        except (discord.Forbidden, discord.HTTPException) as error:
                            log(
                                f"MentionLimit failed to disable mentionable on {role.name} ({role.id}): {error}",
                                level=logging.ERROR,
                                module_name="MentionLimit",
                                guild=guild,
                            )
                started.append((role, until))

            if not started:
                return

            if need_rule_sync:
                try:
                    await self._sync_rule(guild, config, reason=t("mentionlimit.audit.cooldown_started"))
                except discord.HTTPException as error:
                    if self._is_automod_rule_limit_error(error) and not self._antibeast_enabled(guild.id):
                        # AutoMod 規則已達上限且 AntiBeast 未啟用：降級改用關閉可提及。
                        self._rule_limit_degraded[guild.id] = time.monotonic()
                        log(
                            "MentionLimit hit the AutoMod rule limit; falling back to disabling mentionable.",
                            level=logging.WARNING,
                            module_name="MentionLimit",
                            guild=guild,
                        )
                        for role, _until in started:
                            entry = config["roles"][str(role.id)]
                            if entry.get("mentionable_before") is None:
                                entry["mentionable_before"] = bool(role.mentionable)
                            if role.mentionable:
                                try:
                                    await role.edit(mentionable=False, reason=t("mentionlimit.audit.cooldown_started_degraded"))
                                except (discord.Forbidden, discord.HTTPException) as edit_error:
                                    log(
                                        f"MentionLimit degraded enforcement failed: {edit_error}",
                                        level=logging.ERROR,
                                        module_name="MentionLimit",
                                        guild=guild,
                                    )
                    else:
                        self._log_rule_sync_failure(guild, error)
                except Exception as error:
                    self._log_rule_sync_failure(guild, error)

            set_server_config(guild.id, CONFIG_KEY, config)

        log(
            "MentionLimit cooldown started: "
            + ", ".join(f"{role.name} ({role.id})" for role, _ in started),
            module_name="MentionLimit",
            guild=guild,
            user=message.author,
        )

        if announce:
            for role, until in started:
                try:
                    await message.channel.send(
                        t("mentionlimit.msg.cooldown_announce", role=role.mention, when=i18n.fmt_ts(until, "R")),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except (discord.Forbidden, discord.HTTPException):
                    break

    # ---------- 排程恢復 ----------

    def _raw_config_has_state(self, raw_config) -> bool:
        if not isinstance(raw_config, dict):
            return False
        roles = raw_config.get("roles")
        if not isinstance(roles, dict):
            return False
        for entry in roles.values():
            if isinstance(entry, dict) and self._entry_has_state(entry):
                return True
        return False

    def _cleanup_missing_guild(self, guild_id: int):
        config = self._get_config(guild_id)
        now = discord.utils.utcnow()
        for entry in config["roles"].values():
            until = self._parse_datetime(entry.get("cooldown_until"))
            if until is not None and (now - until).total_seconds() < GUILD_ABSENT_GRACE_SECONDS:
                # 冷卻還在跑或剛過期：可能只是伺服器暫時不可用，先不清。
                return

        changed = False
        for entry in config["roles"].values():
            if self._entry_has_state(entry):
                entry["cooldown_until"] = None
                entry["mentionable_before"] = None
                changed = True
        if changed:
            set_server_config(guild_id, CONFIG_KEY, config)
            log(
                f"MentionLimit cleared leftover cooldown state for absent guild {guild_id}.",
                module_name="MentionLimit",
            )

    async def _process_guild_cooldowns(self, guild: discord.Guild, *, force_rule_sync: bool = False):
        async with self._guild_lock(guild.id):
            config = self._get_config(guild.id)
            changed = False
            need_sync = force_rule_sync
            now = discord.utils.utcnow()

            for role_id in list(config["roles"].keys()):
                entry = config["roles"][role_id]
                role = guild.get_role(int(role_id))
                if role is None:
                    if self._entry_has_state(entry):
                        need_sync = True
                    del config["roles"][role_id]
                    changed = True
                    continue

                until = self._parse_datetime(entry.get("cooldown_until"))
                expired = until is not None and until <= now
                leftover = until is None and entry.get("mentionable_before") is not None
                if expired or leftover:
                    ended = await self._end_cooldown(guild, role_id, config, reason=t("mentionlimit.audit.cooldown_ended", locale=i18n.resolve_locale(guild_id=guild.id)))
                    if ended:
                        changed = True
                        need_sync = True

            drift_changed, drift_sync = await self._reconcile_enforcement(
                guild,
                config,
                reason=t("mentionlimit.audit.enforcement_reconcile", locale=i18n.resolve_locale(guild_id=guild.id)),
            )
            changed = changed or drift_changed
            need_sync = need_sync or drift_sync

            if need_sync:
                try:
                    await self._sync_rule(guild, config, reason=t("mentionlimit.audit.cooldown_sync", locale=i18n.resolve_locale(guild_id=guild.id)))
                except Exception as error:
                    self._log_rule_sync_failure(guild, error)
                changed = True

            if changed:
                set_server_config(guild.id, CONFIG_KEY, config)

    @tasks.loop(seconds=10)
    async def cooldown_expiry_task(self):
        try:
            rows = get_all_server_config_key(CONFIG_KEY)
        except Exception as error:
            log(
                f"MentionLimit failed to read cooldown state: {error}",
                level=logging.ERROR,
                module_name="MentionLimit",
            )
            return
        if not rows:
            return

        for raw_guild_id, raw_config in rows.items():
            try:
                guild_id = int(raw_guild_id)
            except (TypeError, ValueError):
                continue
            if not self._raw_config_has_state(raw_config):
                continue

            guild = self.bot.get_guild(guild_id)
            if guild is None:
                self._cleanup_missing_guild(guild_id)
                continue

            try:
                await self._process_guild_cooldowns(guild)
            except Exception as error:
                log(
                    f"MentionLimit cooldown recovery failed: {error}",
                    level=logging.ERROR,
                    module_name="MentionLimit",
                    guild=guild,
                )

    @cooldown_expiry_task.before_loop
    async def _before_cooldown_expiry_task(self):
        await self.bot.wait_until_ready()

    async def cog_load(self):
        # 模組由 asyncio.run(add_cog) 載入；只有 bot 已在正式 loop ready 時才在這裡啟動。
        if self.bot.is_ready() and not self.cooldown_expiry_task.is_running():
            self.cooldown_expiry_task.start()

    async def cog_unload(self):
        if self.cooldown_expiry_task.is_running():
            self.cooldown_expiry_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.cooldown_expiry_task.is_running():
            self.cooldown_expiry_task.start()
        await self._startup_reconcile()

    async def _startup_reconcile(self):
        try:
            rows = get_all_server_config_key(CONFIG_KEY)
        except Exception as error:
            log(
                f"MentionLimit startup reconcile read failed: {error}",
                level=logging.ERROR,
                module_name="MentionLimit",
            )
            return

        for raw_guild_id, raw_config in (rows or {}).items():
            if not isinstance(raw_config, dict):
                continue
            has_state = self._raw_config_has_state(raw_config)
            if not has_state and not raw_config.get("rule_id"):
                continue

            try:
                guild_id = int(raw_guild_id)
            except (TypeError, ValueError):
                continue
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue

            try:
                await self._process_guild_cooldowns(guild, force_rule_sync=True)
            except Exception as error:
                log(
                    f"MentionLimit startup reconcile failed: {error}",
                    level=logging.ERROR,
                    module_name="MentionLimit",
                    guild=guild,
                )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        config = self._get_config(role.guild.id)
        if str(role.id) not in config["roles"]:
            return

        async with self._guild_lock(role.guild.id):
            config = self._get_config(role.guild.id)
            entry = config["roles"].pop(str(role.id), None)
            if entry is None:
                return
            if self._entry_has_state(entry):
                try:
                    await self._sync_rule(role.guild, config, reason=f"MentionLimit role deleted: {role.id}")
                except Exception as error:
                    self._log_rule_sync_failure(role.guild, error)
            set_server_config(role.guild.id, CONFIG_KEY, config)

        log(
            f"MentionLimit removed the deleted role {role.name} ({role.id})",
            module_name="MentionLimit",
            guild=role.guild,
        )

    # ---------- embeds ----------

    def _build_about_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="MentionLimit",
            description=t("mentionlimit.about.desc"),
            color=discord.Color.blue(),
        )
        for section in ("default_mode", "automod_mode", "antibeast", "other"):
            embed.add_field(
                name=t(f"mentionlimit.about.{section}_title"),
                value=t(f"mentionlimit.about.{section}_body"),
                inline=False,
            )
        return embed

    def _build_config_embed(self, guild: discord.Guild, config: dict) -> discord.Embed:
        effective_automod = self._effective_automod(guild.id, config)
        antibeast_forced = effective_automod and not config["automod_mode"]
        embed = discord.Embed(
            title=t("mentionlimit.config.title"),
            color=discord.Color.green() if config["enabled"] else discord.Color.light_grey(),
        )
        embed.add_field(name=t("mentionlimit.field.status"),
                        value=t("mentionlimit.state.enabled") if config["enabled"] else t("mentionlimit.state.disabled"),
                        inline=True)

        mode_text = t("mentionlimit.mode.automod") if config["automod_mode"] else t("mentionlimit.mode.unmentionable")
        if antibeast_forced:
            mode_text += "\n" + t("mentionlimit.mode.antibeast_forced")
        embed.add_field(name=t("mentionlimit.field.mode"), value=mode_text, inline=True)
        embed.add_field(
            name=t("mentionlimit.field.automod_rule"),
            value=f"`{config['rule_id']}`" if config.get("rule_id") else t("mentionlimit.config.rule_not_created"),
            inline=True,
        )
        embed.add_field(name=t("mentionlimit.field.count_admins"),
                        value=t("mentionlimit.state.yes") if config["count_admins"] else t("mentionlimit.state.no"),
                        inline=True)
        embed.add_field(name=t("mentionlimit.field.announce"),
                        value=t("mentionlimit.state.on") if config["announce"] else t("mentionlimit.state.off"),
                        inline=True)

        antibeast_bypass_ids: set[int] | None = None
        if self._antibeast_enabled(guild.id):
            antibeast_config = get_server_config(guild.id, ANTIBEAST_CONFIG_KEY, {})
            antibeast_bypass_ids = set()
            for raw_role_id in (antibeast_config or {}).get("bypass_roles") or []:
                try:
                    antibeast_bypass_ids.add(int(raw_role_id))
                except (TypeError, ValueError):
                    continue

        now = discord.utils.utcnow()
        lines = []
        for role_id, entry in config["roles"].items():
            role = guild.get_role(int(role_id))
            name = role.mention if role else t("mentionlimit.config.deleted_role", role_id=role_id)
            until = self._parse_datetime(entry.get("cooldown_until"))
            if until is not None and until > now:
                state = t("mentionlimit.config.state_cooling", when=i18n.fmt_ts(until, "R"))
            else:
                state = t("mentionlimit.config.state_idle")
            line = t("mentionlimit.config.role_line", role=name, seconds=entry["cooldown"], state=state)
            if (
                role is not None
                and antibeast_bypass_ids is not None
                and role.id not in antibeast_bypass_ids
            ):
                line += "\n" + t("mentionlimit.config.not_in_bypass")
            lines.append(line)
        roles_text = "\n".join(lines) if lines else t("mentionlimit.config.no_roles")
        if len(roles_text) > 1024:
            # embed field 上限 1024 字元，超過就截斷。
            truncated = []
            length = 0
            for line in lines:
                if length + len(line) + 1 > 990:
                    truncated.append(t("mentionlimit.config.more_roles", count=len(lines) - len(truncated)))
                    break
                truncated.append(line)
                length += len(line) + 1
            roles_text = "\n".join(truncated)
        embed.add_field(
            name=t("mentionlimit.field.managed_roles"),
            value=roles_text,
            inline=False,
        )
        if effective_automod:
            embed.set_footer(text=t("mentionlimit.config.automod_admin_note"))
        return embed

    # ---------- 指令 ----------

    @app_commands.command(name=app_commands.locale_str("about", i18n_key="cmd.mentionlimit.mentionlimit.about.name"), description=app_commands.locale_str("About MentionLimit", i18n_key="cmd.mentionlimit.mentionlimit.about.desc"))
    async def about(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_about_embed(), ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("setup", i18n_key="cmd.mentionlimit.mentionlimit.setup.name"), description=app_commands.locale_str("Interactively configure and enable MentionLimit", i18n_key="cmd.mentionlimit.mentionlimit.setup.desc"))
    @app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        config = self._get_config(interaction.guild.id)
        view = MentionLimitSetupView(self, interaction.user, interaction.guild, config)
        await view.send_about(interaction)

    @app_commands.command(name=app_commands.locale_str("toggle", i18n_key="cmd.mentionlimit.mentionlimit.toggle.name"), description=app_commands.locale_str("Enable/disable MentionLimit", i18n_key="cmd.mentionlimit.mentionlimit.toggle.desc"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(enable=app_commands.locale_str("Leave empty to toggle the current state", i18n_key="cmd.mentionlimit.mentionlimit.toggle.param.enable"))
    async def toggle(self, interaction: discord.Interaction, enable: bool = None):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        async with self._guild_lock(guild.id):
            config = self._get_config(guild.id)
            enabled = not config["enabled"] if enable is None else enable

            if enabled:
                missing = self._required_bot_permissions(
                    guild,
                    automod=self._effective_automod(guild.id, config),
                )
                if missing:
                    await interaction.followup.send(
                        t("mentionlimit.err.bot_missing_perms", perms=i18n.join_list(missing)),
                        ephemeral=True,
                    )
                    return
                config["enabled"] = True
                set_server_config(guild.id, CONFIG_KEY, config)
                message = t("mentionlimit.msg.enabled", count=len(config["roles"]))
                if not config["roles"]:
                    message += "\n" + t("mentionlimit.msg.enabled_no_roles")
            else:
                config["enabled"] = False
                failed = await self._restore_all(
                    guild,
                    config,
                    reason=f"MentionLimit disabled by {interaction.user} ({interaction.user.id})",
                )
                set_server_config(guild.id, CONFIG_KEY, config)
                message = t("mentionlimit.msg.disabled")
                if failed:
                    message += "\n" + t("mentionlimit.msg.disabled_partial_failure")

        log(
            f"MentionLimit {'enabled' if enabled else 'disabled'}",
            module_name="MentionLimit",
            guild=guild,
            user=interaction.user,
        )
        await interaction.followup.send(
            message,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name=app_commands.locale_str("add", i18n_key="cmd.mentionlimit.mentionlimit.add.name"), description=app_commands.locale_str("Add or update mention cooldown for a managed role", i18n_key="cmd.mentionlimit.mentionlimit.add.desc"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(role=app_commands.locale_str("The role to manage", i18n_key="cmd.mentionlimit.mentionlimit.add.param.role"), cooldown=app_commands.locale_str("Cooldown seconds (10-86400, default 600)", i18n_key="cmd.mentionlimit.mentionlimit.add.param.cooldown"))
    async def add_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        cooldown: app_commands.Range[int, MIN_COOLDOWN, MAX_COOLDOWN] = DEFAULT_COOLDOWN,
    ):
        guild = interaction.guild
        if role.is_default():
            await interaction.response.send_message(t("mentionlimit.err.cannot_manage_everyone"), ephemeral=True)
            return
        if role.managed:
            await interaction.response.send_message(
                t("mentionlimit.err.cannot_manage_integration_role"),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        async with self._guild_lock(guild.id):
            config = self._get_config(guild.id)
            missing = self._required_bot_permissions(
                guild,
                automod=self._effective_automod(guild.id, config),
            )
            if missing:
                await interaction.followup.send(
                    t("mentionlimit.err.bot_missing_perms", perms=i18n.join_list(missing)),
                    ephemeral=True,
                )
                return
            bot_member = guild.me
            if bot_member is not None and bot_member.top_role <= role:
                await interaction.followup.send(
                    t("mentionlimit.err.bot_hierarchy_too_low", role=role.name),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            is_update = str(role.id) in config["roles"]
            if not is_update and len(config["roles"]) >= MAX_MANAGED_ROLES:
                await interaction.followup.send(
                    t("mentionlimit.err.max_roles", max=MAX_MANAGED_ROLES),
                    ephemeral=True,
                )
                return

            entry = config["roles"].get(str(role.id)) or self._default_role_entry()
            entry["cooldown"] = int(cooldown)
            config["roles"][str(role.id)] = entry

            _, bypass_note = await self._ensure_antibeast_bypass(guild, role)
            set_server_config(guild.id, CONFIG_KEY, config)

        message = t("mentionlimit.msg.role_updated" if is_update else "mentionlimit.msg.role_added",
                    role=role.name, seconds=int(cooldown))
        if bypass_note:
            message += f"\n{bypass_note}"
        if not config["enabled"]:
            message += "\n" + t("mentionlimit.msg.currently_disabled")

        log(
            f"MentionLimit {'updated' if is_update else 'added'} role {role.name} ({role.id}), cooldown {int(cooldown)}s",
            module_name="MentionLimit",
            guild=guild,
            user=interaction.user,
        )
        await interaction.followup.send(
            message,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name=app_commands.locale_str("remove", i18n_key="cmd.mentionlimit.mentionlimit.remove.name"), description=app_commands.locale_str("Remove a managed role", i18n_key="cmd.mentionlimit.mentionlimit.remove.desc"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(role=app_commands.locale_str("The role to stop managing", i18n_key="cmd.mentionlimit.mentionlimit.remove.param.role"))
    async def remove(self, interaction: discord.Interaction, role: discord.Role):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)

        async with self._guild_lock(guild.id):
            config = self._get_config(guild.id)
            entry = config["roles"].get(str(role.id))
            if entry is None:
                await interaction.followup.send(
                    t("mentionlimit.err.role_not_managed", role=role.name),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            was_cooling = self._entry_has_state(entry)
            restored = True
            if was_cooling:
                restored = await self._end_cooldown(
                    guild,
                    str(role.id),
                    config,
                    reason=f"MentionLimit role removed by {interaction.user} ({interaction.user.id})",
                )
            config["roles"].pop(str(role.id), None)
            if was_cooling:
                try:
                    await self._sync_rule(guild, config, reason=f"MentionLimit removed role {role.id}")
                except Exception as error:
                    self._log_rule_sync_failure(guild, error)
            set_server_config(guild.id, CONFIG_KEY, config)

        message = t("mentionlimit.msg.role_removed", role=role.name)
        if was_cooling and restored:
            message += t("mentionlimit.msg.role_removed_restored")
        elif was_cooling:
            message += "\n" + t("mentionlimit.msg.role_removed_restore_failed")

        log(
            f"MentionLimit removed role {role.name} ({role.id})",
            module_name="MentionLimit",
            guild=guild,
            user=interaction.user,
        )
        await interaction.followup.send(
            message,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name=app_commands.locale_str("settings", i18n_key="cmd.mentionlimit.mentionlimit.settings.name"), description=app_commands.locale_str("Configure MentionLimit mode and options", i18n_key="cmd.mentionlimit.mentionlimit.settings.desc"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        automod_mode=app_commands.locale_str("AutoMod mode: block with an AutoMod rule during cooldown, role stays mentionable", i18n_key="cmd.mentionlimit.mentionlimit.settings.param.automod_mode"),
        count_admins=app_commands.locale_str("Whether admin mentions also trigger the cooldown", i18n_key="cmd.mentionlimit.mentionlimit.settings.param.count_admins"),
        announce=app_commands.locale_str("Announce in the channel when a cooldown starts", i18n_key="cmd.mentionlimit.mentionlimit.settings.param.announce"),
    )
    async def settings(
        self,
        interaction: discord.Interaction,
        automod_mode: bool = None,
        count_admins: bool = None,
        announce: bool = None,
    ):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)
        notes = []

        async with self._guild_lock(guild.id):
            config = self._get_config(guild.id)
            changed = False

            if count_admins is not None and count_admins != config["count_admins"]:
                config["count_admins"] = count_admins
                changed = True

            if announce is not None and announce != config["announce"]:
                config["announce"] = announce
                changed = True

            if automod_mode is not None and automod_mode != config["automod_mode"]:
                if automod_mode:
                    missing = self._required_bot_permissions(guild, automod=True)
                    if missing:
                        await interaction.followup.send(
                            t("mentionlimit.err.bot_missing_perms", perms=i18n.join_list(missing)),
                            ephemeral=True,
                        )
                        return
                config["automod_mode"] = automod_mode
                changed = True

                # 進行中的冷卻換軌到新的執法方式。
                _, need_sync = await self._reconcile_enforcement(
                    guild,
                    config,
                    reason=f"MentionLimit mode switch by {interaction.user} ({interaction.user.id})",
                )
                if need_sync or config.get("rule_id"):
                    try:
                        await self._sync_rule(guild, config, reason="MentionLimit mode switch")
                    except Exception as error:
                        self._log_rule_sync_failure(guild, error)
                        notes.append(self._rule_sync_error_message(error))

            if changed:
                set_server_config(guild.id, CONFIG_KEY, config)
                log(
                    f"MentionLimit settings updated: automod_mode={config['automod_mode']}, "
                    f"count_admins={config['count_admins']}, announce={config['announce']}",
                    module_name="MentionLimit",
                    guild=guild,
                    user=interaction.user,
                )

        if self._antibeast_enabled(guild.id) and not config["automod_mode"]:
            notes.append(t("mentionlimit.note.antibeast_forces_automod"))
        if self._effective_automod(guild.id, config):
            notes.append(t("mentionlimit.note.automod_skips_admins"))

        status_lines = [
            t("mentionlimit.settings.updated") if changed else t("mentionlimit.settings.current"),
            t("mentionlimit.settings.automod_line",
              state=t("mentionlimit.state.on") if config["automod_mode"] else t("mentionlimit.state.off")),
            t("mentionlimit.settings.count_admins_line",
              state=t("mentionlimit.state.yes") if config["count_admins"] else t("mentionlimit.state.no")),
            t("mentionlimit.settings.announce_line",
              state=t("mentionlimit.state.on") if config["announce"] else t("mentionlimit.state.off")),
        ]
        status_lines.extend(notes)
        await interaction.followup.send(
            "\n".join(status_lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name=app_commands.locale_str("list", i18n_key="cmd.mentionlimit.mentionlimit.list.name"), description=app_commands.locale_str("List MentionLimit settings", i18n_key="cmd.mentionlimit.mentionlimit.list.desc"))
    @app_commands.default_permissions(administrator=True)
    async def list_config(self, interaction: discord.Interaction):
        config = self._get_config(interaction.guild.id)
        embed = self._build_config_embed(interaction.guild, config)
        set_server_config(interaction.guild.id, CONFIG_KEY, config)
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class MentionLimitSetupView(i18n.I18nView):
    def __init__(
        self,
        cog: MentionLimit,
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
        self.add_item(MentionLimitSetupContinueButton())
        embed = self.cog._build_about_embed()
        embed.set_footer(text="MentionLimit setup: 1/4")
        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)

    async def show_roles(self, interaction: discord.Interaction):
        self.clear_items()
        self.add_item(MentionLimitRoleSelect())
        self.add_item(MentionLimitKeepRolesButton())
        self.add_item(MentionLimitClearRolesButton())
        embed = discord.Embed(
            title=t("mentionlimit.setup.roles_title"),
            description=t("mentionlimit.setup.roles_desc"),
            color=discord.Color.blurple(),
        )
        lines = []
        for role_id, entry in self.config["roles"].items():
            role = self.guild.get_role(int(role_id))
            if role is not None:
                lines.append(t("mentionlimit.setup.role_line", role=role.mention, seconds=entry["cooldown"]))
        embed.add_field(
            name=t("mentionlimit.setup.current_selection"),
            value="\n".join(lines) if lines else t("mentionlimit.setup.nothing_selected"),
            inline=False,
        )
        embed.set_footer(text="MentionLimit setup: 2/4")
        await interaction.response.edit_message(
            embed=embed,
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def show_options(self, interaction: discord.Interaction):
        self.clear_items()
        self.add_item(MentionLimitCooldownButton())
        self.add_item(MentionLimitContinueToConfirmButton())
        self.add_item(MentionLimitAutomodToggleButton(self.config["automod_mode"]))
        self.add_item(MentionLimitCountAdminsToggleButton(self.config["count_admins"]))
        self.add_item(MentionLimitAnnounceToggleButton(self.config["announce"]))
        self.add_item(MentionLimitBackToRolesButton())

        embed = discord.Embed(
            title=t("mentionlimit.setup.options_title"),
            description=t("mentionlimit.setup.options_desc"),
            color=discord.Color.blurple(),
        )
        lines = []
        for role_id, entry in self.config["roles"].items():
            role = self.guild.get_role(int(role_id))
            if role is not None:
                lines.append(t("mentionlimit.setup.role_line", role=role.mention, seconds=entry["cooldown"]))
        embed.add_field(
            name=t("mentionlimit.field.managed_roles"),
            value="\n".join(lines) if lines else t("mentionlimit.setup.nothing_selected_hint"),
            inline=False,
        )
        embed.add_field(
            name=t("mentionlimit.field.mode"),
            value=t("mentionlimit.setup.mode_explainer"),
            inline=False,
        )
        if self.cog._antibeast_enabled(self.guild.id):
            embed.add_field(
                name="AntiBeast",
                value=t("mentionlimit.setup.antibeast_note"),
                inline=False,
            )
        embed.set_footer(text="MentionLimit setup: 3/4")
        await interaction.response.edit_message(
            embed=embed,
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def show_confirm(self, interaction: discord.Interaction):
        self.clear_items()
        self.add_item(MentionLimitEnableButton())
        self.add_item(MentionLimitBackToOptionsButton())
        embed = discord.Embed(
            title=t("mentionlimit.setup.confirm_title"),
            description=t("mentionlimit.setup.confirm_desc"),
            color=discord.Color.green(),
        )
        lines = []
        for role_id, entry in self.config["roles"].items():
            role = self.guild.get_role(int(role_id))
            if role is not None:
                lines.append(t("mentionlimit.setup.role_line", role=role.mention, seconds=entry["cooldown"]))
        embed.add_field(
            name=t("mentionlimit.field.managed_roles"),
            value="\n".join(lines) if lines else t("mentionlimit.setup.nothing_selected"),
            inline=False,
        )
        embed.add_field(
            name=t("mentionlimit.field.mode"),
            value=t("mentionlimit.mode.automod") if self.config["automod_mode"] else t("mentionlimit.mode.unmentionable"),
            inline=True,
        )
        embed.add_field(
            name=t("mentionlimit.field.count_admins"),
            value=t("mentionlimit.state.yes") if self.config["count_admins"] else t("mentionlimit.state.no"),
            inline=True,
        )
        embed.add_field(
            name=t("mentionlimit.field.announce"),
            value=t("mentionlimit.state.on") if self.config["announce"] else t("mentionlimit.state.off"),
            inline=True,
        )
        embed.set_footer(text="MentionLimit setup: 4/4")
        await interaction.response.edit_message(
            embed=embed,
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def finish_enable(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        cog = self.cog
        guild = self.guild
        reason = f"MentionLimit setup enabled by {interaction.user} ({interaction.user.id})"
        bypass_notes = []
        hierarchy_warnings = []

        async with cog._guild_lock(guild.id):
            config = cog._get_config(guild.id)
            config["automod_mode"] = self.config["automod_mode"]
            config["count_admins"] = self.config["count_admins"]
            config["announce"] = self.config["announce"]

            missing = cog._required_bot_permissions(
                guild,
                automod=cog._effective_automod(guild.id, config),
            )
            if missing:
                await interaction.followup.send(
                    t("mentionlimit.err.bot_missing_perms", perms=i18n.join_list(missing)),
                    ephemeral=True,
                )
                return

            selected = self.config["roles"]
            for role_id in list(config["roles"].keys()):
                if role_id not in selected:
                    await cog._end_cooldown(guild, role_id, config, reason=reason)
                    config["roles"].pop(role_id, None)

            for role_id, entry in selected.items():
                current = config["roles"].get(role_id)
                if current is not None:
                    current["cooldown"] = entry["cooldown"]
                else:
                    config["roles"][role_id] = cog._default_role_entry(entry["cooldown"])

                role = guild.get_role(int(role_id))
                if role is None:
                    continue
                bot_member = guild.me
                if bot_member is not None and bot_member.top_role <= role:
                    hierarchy_warnings.append(role.name)
                added, note = await cog._ensure_antibeast_bypass(guild, role)
                if added and note:
                    bypass_notes.append(note)

            config["enabled"] = True
            _, need_sync = await cog._reconcile_enforcement(guild, config, reason=reason)
            if need_sync or config.get("rule_id"):
                try:
                    await cog._sync_rule(guild, config, reason=reason)
                except Exception as error:
                    cog._log_rule_sync_failure(guild, error)
                    await interaction.followup.send(
                        cog._rule_sync_error_message(error),
                        ephemeral=True,
                    )
                    return
            set_server_config(guild.id, CONFIG_KEY, config)

        embed = cog._build_config_embed(guild, config)
        embed.title = t("mentionlimit.setup.enabled_title")
        if bypass_notes:
            embed.add_field(name="AntiBeast", value="\n".join(bypass_notes), inline=False)
        if hierarchy_warnings:
            embed.add_field(
                name=t("mentionlimit.setup.hierarchy_title"),
                value=t("mentionlimit.setup.hierarchy_body",
                        roles=i18n.join_list(f"**{name}**" for name in hierarchy_warnings)),
                inline=False,
            )
        log(
            "MentionLimit enabled via setup",
            module_name="MentionLimit",
            guild=guild,
            user=interaction.user,
        )
        self.clear_items()
        await interaction.edit_original_response(
            embed=embed,
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def on_timeout(self):
        self.clear_items()


class MentionLimitSetupContinueButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("common.btn.next"), style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_roles(interaction)


class MentionLimitRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder=t("mentionlimit.setup.role_select_ph"), min_values=1, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        selected = [
            role
            for role in self.values
            if not role.is_default() and not role.managed
        ][:MAX_MANAGED_ROLES]
        new_roles = {}
        for role in selected:
            role_id = str(role.id)
            previous = self.view.config["roles"].get(role_id)
            new_roles[role_id] = previous if previous else self.view.cog._default_role_entry()
        self.view.config["roles"] = new_roles
        await self.view.show_options(interaction)


class MentionLimitKeepRolesButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("mentionlimit.btn.keep_current"), style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_options(interaction)


class MentionLimitClearRolesButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("mentionlimit.btn.clear_and_continue"), style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        self.view.config["roles"] = {}
        await self.view.show_options(interaction)


class MentionLimitCooldownButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("mentionlimit.btn.set_cooldown"), style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(MentionLimitCooldownModal(self.view))


class MentionLimitContinueToConfirmButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("common.btn.next"), style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_confirm(interaction)


class MentionLimitAutomodToggleButton(discord.ui.Button):
    def __init__(self, enabled: bool):
        label = t("mentionlimit.btn.automod_toggle",
                  state=t("mentionlimit.state.on_plain") if enabled else t("mentionlimit.state.off_plain"))
        style = discord.ButtonStyle.primary if enabled else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.view.config["automod_mode"] = not self.view.config["automod_mode"]
        await self.view.show_options(interaction)


class MentionLimitCountAdminsToggleButton(discord.ui.Button):
    def __init__(self, enabled: bool):
        label = t("mentionlimit.btn.count_admins_toggle",
                  state=t("mentionlimit.state.yes_plain") if enabled else t("mentionlimit.state.no_plain"))
        style = discord.ButtonStyle.primary if enabled else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.view.config["count_admins"] = not self.view.config["count_admins"]
        await self.view.show_options(interaction)


class MentionLimitAnnounceToggleButton(discord.ui.Button):
    def __init__(self, enabled: bool):
        label = t("mentionlimit.btn.announce_toggle",
                  state=t("mentionlimit.state.on_plain") if enabled else t("mentionlimit.state.off_plain"))
        style = discord.ButtonStyle.primary if enabled else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.view.config["announce"] = not self.view.config["announce"]
        await self.view.show_options(interaction)


class MentionLimitBackToRolesButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("mentionlimit.btn.back_to_roles"), style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_roles(interaction)


class MentionLimitEnableButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("mentionlimit.btn.enable"), style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        await self.view.finish_enable(interaction)


class MentionLimitBackToOptionsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=t("common.btn.back"), style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_options(interaction)


class MentionLimitCooldownModal(i18n.I18nModal, title=i18n.K("mentionlimit.modal.cooldown_title")):
    def __init__(self, setup_view: MentionLimitSetupView):
        super().__init__()
        self.setup_view = setup_view
        cooldowns = {entry["cooldown"] for entry in setup_view.config["roles"].values()}
        default = str(cooldowns.pop()) if len(cooldowns) == 1 else str(DEFAULT_COOLDOWN)
        self.cooldown = discord.ui.TextInput(
            label=t("mentionlimit.modal.cooldown_label", min=MIN_COOLDOWN, max=MAX_COOLDOWN),
            default=default,
            placeholder=str(DEFAULT_COOLDOWN),
            max_length=5,
        )
        self.add_item(self.cooldown)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cooldown = int(str(self.cooldown.value).strip())
        except ValueError:
            await interaction.response.send_message(t("mentionlimit.err.cooldown_not_int"), ephemeral=True)
            return
        if cooldown < MIN_COOLDOWN or cooldown > MAX_COOLDOWN:
            await interaction.response.send_message(
                t("mentionlimit.err.cooldown_range", min=MIN_COOLDOWN, max=MAX_COOLDOWN),
                ephemeral=True,
            )
            return

        for entry in self.setup_view.config["roles"].values():
            entry["cooldown"] = cooldown
        await self.setup_view.show_options(interaction)


asyncio.run(bot.add_cog(MentionLimit(bot)))


if __name__ == "__main__":
    start_bot()
