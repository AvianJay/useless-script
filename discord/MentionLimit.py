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


CONFIG_KEY = "mentionlimit"
ANTIBEAST_CONFIG_KEY = "antibeast"
RULE_NAME = "MentionLimit - role mention cooldown"
BLOCK_MESSAGE = "MentionLimit 已阻擋提及：此身分組正在冷卻中，請稍後再試。"
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
class MentionLimit(commands.GroupCog, name="mentionlimit"):
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
                    reason=f"MentionLimit 自動加入 AntiBeast 繞過: {role.id}",
                )
                set_server_config(guild.id, ANTIBEAST_CONFIG_KEY, cog_config)
            except Exception as error:
                log(
                    f"MentionLimit 加入 AntiBeast 繞過失敗: {error}",
                    level=logging.ERROR,
                    module_name="MentionLimit",
                    guild=guild,
                )
                return False, f"⚠️ 將 **{role.name}** 加入 AntiBeast 繞過清單失敗，請手動執行 `/antibeast bypass`。"
            return True, (
                f"已偵測到 AntiBeast 啟用中，已自動將 **{role.name}** 加入 AntiBeast 繞過清單並同步其 AutoMod 規則。"
            )

        bypass_ids.add(role.id)
        antibeast_config["bypass_roles"] = list(bypass_ids)
        set_server_config(guild.id, ANTIBEAST_CONFIG_KEY, antibeast_config)
        return True, (
            f"已偵測到 AntiBeast 啟用中，已自動將 **{role.name}** 加入 AntiBeast 繞過清單，"
            "將於 AntiBeast 下次同步時生效。"
        )

    # ---------- 權限 / AutoMod 規則 ----------

    @staticmethod
    def _required_bot_permissions(guild: discord.Guild, *, automod: bool) -> list[str]:
        bot_member = guild.me
        if bot_member is None:
            return ["管理身分組", "管理伺服器"] if automod else ["管理身分組"]

        missing = []
        permissions = bot_member.guild_permissions
        if not permissions.manage_roles:
            missing.append("管理身分組")
        if automod and not permissions.manage_guild:
            missing.append("管理伺服器")
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
                    custom_message=BLOCK_MESSAGE,
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
            for keyword in ("maximum", "limit", "too many", "reached", "已達", "上限")
        )
        return mentions_automod and mentions_limit

    def _rule_sync_error_message(self, error: Exception) -> str:
        if isinstance(error, discord.Forbidden):
            return f"⚠️ MentionLimit 同步 AutoMod 規則失敗：{error.text or error}"
        if isinstance(error, discord.HTTPException):
            if self._is_automod_rule_limit_error(error):
                return (
                    "⚠️ MentionLimit 無法建立 AutoMod 規則：這個伺服器的 Discord AutoMod 規則數量已達上限。"
                    "請先刪除不需要的 AutoMod 規則後再試。"
                )
            return f"⚠️ MentionLimit 同步 AutoMod 規則失敗：Discord API 回應錯誤 ({error.status})。"
        return "⚠️ MentionLimit 同步 AutoMod 規則失敗，請稍後再試。"

    def _log_rule_sync_failure(self, guild: discord.Guild, error: Exception):
        log(
            f"MentionLimit AutoMod 規則同步失敗: {error}",
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
                        f"MentionLimit 還原 {role.name} ({role.id}) 可提及狀態失敗: {error}",
                        level=logging.ERROR,
                        module_name="MentionLimit",
                        guild=guild,
                    )
                if failures < RESTORE_MAX_FAILURES:
                    return False
                log(
                    f"MentionLimit 還原 {role.name} ({role.id}) 連續失敗 {failures} 次，"
                    "已放棄自動還原，請手動調整身分組的可提及設定。",
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
                            f"MentionLimit 切換執法模式時還原 {role.name} ({role.id}) 失敗: {error}",
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
                            f"MentionLimit 切換執法模式時關閉 {role.name} ({role.id}) 可提及失敗: {error}",
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

        guild = message.guild
        config = self._get_config(guild.id)
        if not config["enabled"] or not config["roles"]:
            return

        matched = [role for role in message.role_mentions if str(role.id) in config["roles"]]
        if not matched:
            return

        if not config["count_admins"]:
            permissions = getattr(message.author, "guild_permissions", None)
            if permissions is not None and permissions.administrator:
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
                                reason=f"MentionLimit 冷卻開始，{entry['cooldown']} 秒後恢復",
                            )
                        except (discord.Forbidden, discord.HTTPException) as error:
                            log(
                                f"MentionLimit 關閉 {role.name} ({role.id}) 可提及失敗: {error}",
                                level=logging.ERROR,
                                module_name="MentionLimit",
                                guild=guild,
                            )
                started.append((role, until))

            if not started:
                return

            if need_rule_sync:
                try:
                    await self._sync_rule(guild, config, reason="MentionLimit 冷卻開始")
                except discord.HTTPException as error:
                    if self._is_automod_rule_limit_error(error) and not self._antibeast_enabled(guild.id):
                        # AutoMod 規則已達上限且 AntiBeast 未啟用：降級改用關閉可提及。
                        self._rule_limit_degraded[guild.id] = time.monotonic()
                        log(
                            "MentionLimit AutoMod 規則數量已達上限，改用關閉可提及執法。",
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
                                    await role.edit(mentionable=False, reason="MentionLimit 冷卻開始（降級執法）")
                                except (discord.Forbidden, discord.HTTPException) as edit_error:
                                    log(
                                        f"MentionLimit 降級執法失敗: {edit_error}",
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
            "MentionLimit 冷卻開始: "
            + "、".join(f"{role.name} ({role.id})" for role, _ in started),
            module_name="MentionLimit",
            guild=guild,
            user=message.author,
        )

        if announce:
            for role, until in started:
                try:
                    await message.channel.send(
                        f"⏳ {role.mention} 已進入提及冷卻，<t:{int(until.timestamp())}:R> 後恢復。",
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
                f"MentionLimit 已清除不在伺服器 {guild_id} 的殘留冷卻狀態。",
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
                    ended = await self._end_cooldown(guild, role_id, config, reason="MentionLimit 冷卻結束")
                    if ended:
                        changed = True
                        need_sync = True

            drift_changed, drift_sync = await self._reconcile_enforcement(
                guild,
                config,
                reason="MentionLimit 執法模式對帳",
            )
            changed = changed or drift_changed
            need_sync = need_sync or drift_sync

            if need_sync:
                try:
                    await self._sync_rule(guild, config, reason="MentionLimit 冷卻狀態同步")
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
                f"MentionLimit 讀取冷卻狀態失敗: {error}",
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
                    f"MentionLimit 冷卻恢復處理失敗: {error}",
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
                f"MentionLimit 啟動對帳讀取失敗: {error}",
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
                    f"MentionLimit 啟動對帳失敗: {error}",
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
                    await self._sync_rule(role.guild, config, reason=f"MentionLimit 身分組已刪除: {role.id}")
                except Exception as error:
                    self._log_rule_sync_failure(role.guild, error)
            set_server_config(role.guild.id, CONFIG_KEY, config)

        log(
            f"MentionLimit 已移除被刪除的身分組 {role.name} ({role.id})",
            module_name="MentionLimit",
            guild=role.guild,
        )

    # ---------- embeds ----------

    def _build_about_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="MentionLimit",
            description=(
                "為「我想被tag」這類開放提及的身分組加上冷卻：\n"
                "身分組被提及後，會暫時擋住再次提及，冷卻結束自動恢復。"
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="預設模式",
            value="被提及後暫時關閉身分組的「允許任何人提及」，冷卻結束還原原本設定。",
            inline=False,
        )
        embed.add_field(
            name="AutoMod 模式",
            value=(
                "身分組保持永遠可提及，冷卻期間改用 Discord 原生 AutoMod 規則封鎖該身分組的提及。\n"
                "注意：AutoMod 不會作用於管理員與伺服器擁有者，他們在冷卻期間仍可提及。"
            ),
            inline=False,
        )
        embed.add_field(
            name="AntiBeast 相容",
            value=(
                "AntiBeast 啟用時會讓 @everyone 擁有提及權限，關閉可提及會失效；"
                "此時 MentionLimit 會自動改用 AutoMod 規則強制執行。\n"
                "加入身分組時會自動加入 AntiBeast 繞過清單（移除時不會自動移出）。"
            ),
            inline=False,
        )
        embed.add_field(
            name="其他設定",
            value=(
                "「計入管理員」可讓管理員的提及也觸發冷卻（預設不觸發）。\n"
                "「冷卻公告」會在進入冷卻時於該頻道發出提示（預設關閉）。"
            ),
            inline=False,
        )
        return embed

    def _build_config_embed(self, guild: discord.Guild, config: dict) -> discord.Embed:
        effective_automod = self._effective_automod(guild.id, config)
        antibeast_forced = effective_automod and not config["automod_mode"]
        embed = discord.Embed(
            title="MentionLimit 設定",
            color=discord.Color.green() if config["enabled"] else discord.Color.light_grey(),
        )
        embed.add_field(name="狀態", value="✅ 啟用" if config["enabled"] else "❌ 停用", inline=True)

        mode_text = "AutoMod 規則封鎖" if config["automod_mode"] else "關閉可提及"
        if antibeast_forced:
            mode_text += "\n（AntiBeast 啟用中，強制使用 AutoMod 模式）"
        embed.add_field(name="模式", value=mode_text, inline=True)
        embed.add_field(
            name="AutoMod 規則",
            value=f"`{config['rule_id']}`" if config.get("rule_id") else "尚未建立",
            inline=True,
        )
        embed.add_field(name="計入管理員", value="✅ 是" if config["count_admins"] else "❌ 否", inline=True)
        embed.add_field(name="冷卻公告", value="✅ 開" if config["announce"] else "❌ 關", inline=True)

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
            name = role.mention if role else f"已刪除的身分組 ({role_id})"
            until = self._parse_datetime(entry.get("cooldown_until"))
            if until is not None and until > now:
                state = f"冷卻中，<t:{int(until.timestamp())}:R> 結束"
            else:
                state = "未在冷卻"
            line = f"{name}｜冷卻 {entry['cooldown']} 秒｜{state}"
            if (
                role is not None
                and antibeast_bypass_ids is not None
                and role.id not in antibeast_bypass_ids
            ):
                line += "\n　⚠️ 不在 AntiBeast 繞過清單，提及會被 AntiBeast 阻擋。"
            lines.append(line)
        roles_text = "\n".join(lines) if lines else "尚未加入任何身分組，請用 `/mentionlimit add` 加入。"
        if len(roles_text) > 1024:
            # embed field 上限 1024 字元，超過就截斷。
            truncated = []
            length = 0
            for line in lines:
                if length + len(line) + 1 > 990:
                    truncated.append(f"…以及其他 {len(lines) - len(truncated)} 個身分組")
                    break
                truncated.append(line)
                length += len(line) + 1
            roles_text = "\n".join(truncated)
        embed.add_field(
            name="受管理身分組",
            value=roles_text,
            inline=False,
        )
        if effective_automod:
            embed.set_footer(text="AutoMod 不會作用於管理員與伺服器擁有者。")
        return embed

    # ---------- 指令 ----------

    @app_commands.command(name="about", description="關於 MentionLimit")
    async def about(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_about_embed(), ephemeral=True)

    @app_commands.command(name="setup", description="互動式設定並啟用 MentionLimit")
    @app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        config = self._get_config(interaction.guild.id)
        view = MentionLimitSetupView(self, interaction.user, interaction.guild, config)
        await view.send_about(interaction)

    @app_commands.command(name="toggle", description="啟用/停用 MentionLimit")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(enable="留空則切換目前狀態")
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
                        f"⚠️ 機器人缺少權限：{'、'.join(missing)}",
                        ephemeral=True,
                    )
                    return
                config["enabled"] = True
                set_server_config(guild.id, CONFIG_KEY, config)
                message = f"✅ MentionLimit 已**啟用**，目前管理 {len(config['roles'])} 個身分組。"
                if not config["roles"]:
                    message += "\n請用 `/mentionlimit add` 加入要管理的身分組。"
            else:
                config["enabled"] = False
                failed = await self._restore_all(
                    guild,
                    config,
                    reason=f"MentionLimit disabled by {interaction.user} ({interaction.user.id})",
                )
                set_server_config(guild.id, CONFIG_KEY, config)
                message = "✅ MentionLimit 已**停用**，冷卻中的身分組已全部還原。"
                if failed:
                    message += "\n⚠️ 部分身分組還原失敗，將自動重試；請檢查機器人權限。"

        log(
            f"MentionLimit 已{'啟用' if enabled else '停用'}",
            module_name="MentionLimit",
            guild=guild,
            user=interaction.user,
        )
        await interaction.followup.send(
            message,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="add", description="新增或更新受管理身分組的提及冷卻")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(role="要管理的身分組", cooldown="冷卻秒數（10-86400，預設 600）")
    async def add_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        cooldown: app_commands.Range[int, MIN_COOLDOWN, MAX_COOLDOWN] = DEFAULT_COOLDOWN,
    ):
        guild = interaction.guild
        if role.is_default():
            await interaction.response.send_message("⚠️ 不能管理 @everyone。", ephemeral=True)
            return
        if role.managed:
            await interaction.response.send_message(
                "⚠️ 不能管理由機器人或整合服務管理的身分組。",
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
                    f"⚠️ 機器人缺少權限：{'、'.join(missing)}",
                    ephemeral=True,
                )
                return
            bot_member = guild.me
            if bot_member is not None and bot_member.top_role <= role:
                await interaction.followup.send(
                    f"⚠️ 機器人的最高身分組必須高於 **{role.name}** 才能切換它的可提及狀態。",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            is_update = str(role.id) in config["roles"]
            if not is_update and len(config["roles"]) >= MAX_MANAGED_ROLES:
                await interaction.followup.send(
                    f"⚠️ 最多只能管理 {MAX_MANAGED_ROLES} 個身分組。",
                    ephemeral=True,
                )
                return

            entry = config["roles"].get(str(role.id)) or self._default_role_entry()
            entry["cooldown"] = int(cooldown)
            config["roles"][str(role.id)] = entry

            _, bypass_note = await self._ensure_antibeast_bypass(guild, role)
            set_server_config(guild.id, CONFIG_KEY, config)

        action = "更新" if is_update else "加入"
        message = f"✅ 已將 **{role.name}** {action}提及冷卻管理，冷卻時間 {int(cooldown)} 秒。"
        if bypass_note:
            message += f"\n{bypass_note}"
        if not config["enabled"]:
            message += "\nℹ️ MentionLimit 目前是停用狀態，請用 `/mentionlimit toggle` 啟用。"

        log(
            f"MentionLimit {action}身分組 {role.name} ({role.id})，冷卻 {int(cooldown)} 秒",
            module_name="MentionLimit",
            guild=guild,
            user=interaction.user,
        )
        await interaction.followup.send(
            message,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="remove", description="移除受管理的身分組")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(role="要移除管理的身分組")
    async def remove(self, interaction: discord.Interaction, role: discord.Role):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)

        async with self._guild_lock(guild.id):
            config = self._get_config(guild.id)
            entry = config["roles"].get(str(role.id))
            if entry is None:
                await interaction.followup.send(
                    f"⚠️ **{role.name}** 不在管理清單中。",
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
                    reason=f"MentionLimit 移除身分組 by {interaction.user} ({interaction.user.id})",
                )
            config["roles"].pop(str(role.id), None)
            if was_cooling:
                try:
                    await self._sync_rule(guild, config, reason="MentionLimit 移除身分組")
                except Exception as error:
                    self._log_rule_sync_failure(guild, error)
            set_server_config(guild.id, CONFIG_KEY, config)

        message = f"✅ 已移除 **{role.name}** 的提及冷卻管理。"
        if was_cooling and restored:
            message += "（原本正在冷卻，已立即還原）"
        elif was_cooling:
            message += "\n⚠️ 還原可提及狀態失敗，請手動檢查身分組設定。"

        log(
            f"MentionLimit 移除身分組 {role.name} ({role.id})",
            module_name="MentionLimit",
            guild=guild,
            user=interaction.user,
        )
        await interaction.followup.send(
            message,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="settings", description="設定 MentionLimit 模式與選項")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        automod_mode="AutoMod 模式：冷卻期間用 AutoMod 規則封鎖，身分組保持可提及",
        count_admins="管理員的提及是否也觸發冷卻",
        announce="進入冷卻時是否在該頻道公告",
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
                            f"⚠️ 機器人缺少權限：{'、'.join(missing)}",
                            ephemeral=True,
                        )
                        return
                config["automod_mode"] = automod_mode
                changed = True

                # 進行中的冷卻換軌到新的執法方式。
                _, need_sync = await self._reconcile_enforcement(
                    guild,
                    config,
                    reason=f"MentionLimit 模式切換 by {interaction.user} ({interaction.user.id})",
                )
                if need_sync or config.get("rule_id"):
                    try:
                        await self._sync_rule(guild, config, reason="MentionLimit 模式切換")
                    except Exception as error:
                        self._log_rule_sync_failure(guild, error)
                        notes.append(self._rule_sync_error_message(error))

            if changed:
                set_server_config(guild.id, CONFIG_KEY, config)
                log(
                    f"MentionLimit 設定更新: automod_mode={config['automod_mode']}, "
                    f"count_admins={config['count_admins']}, announce={config['announce']}",
                    module_name="MentionLimit",
                    guild=guild,
                    user=interaction.user,
                )

        if self._antibeast_enabled(guild.id) and not config["automod_mode"]:
            notes.append("⚠️ AntiBeast 啟用中：冷卻期間仍會以 AutoMod 規則強制執行，直到 AntiBeast 停用。")
        if self._effective_automod(guild.id, config):
            notes.append("ℹ️ AutoMod 不會作用於管理員與伺服器擁有者，他們在冷卻期間仍可提及。")

        status_lines = [
            "已更新設定。" if changed else "目前設定：",
            f"AutoMod 模式：{'✅ 開' if config['automod_mode'] else '❌ 關'}",
            f"計入管理員：{'✅ 是' if config['count_admins'] else '❌ 否'}",
            f"冷卻公告：{'✅ 開' if config['announce'] else '❌ 關'}",
        ]
        status_lines.extend(notes)
        await interaction.followup.send(
            "\n".join(status_lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="list", description="列出 MentionLimit 設定")
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


class MentionLimitSetupView(discord.ui.View):
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
            await interaction.response.send_message("這個設定流程不是你的。", ephemeral=True)
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
            title="MentionLimit Setup: 受管理身分組",
            description=(
                "選擇要加上提及冷卻的身分組（例如「我想被tag」）。\n"
                "@everyone 與整合服務管理的身分組會自動排除。"
            ),
            color=discord.Color.blurple(),
        )
        lines = []
        for role_id, entry in self.config["roles"].items():
            role = self.guild.get_role(int(role_id))
            if role is not None:
                lines.append(f"{role.mention}｜冷卻 {entry['cooldown']} 秒")
        embed.add_field(
            name="目前選擇",
            value="\n".join(lines) if lines else "尚未選擇",
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
            title="MentionLimit Setup: 冷卻與模式",
            description="設定冷卻秒數與執法模式。",
            color=discord.Color.blurple(),
        )
        lines = []
        for role_id, entry in self.config["roles"].items():
            role = self.guild.get_role(int(role_id))
            if role is not None:
                lines.append(f"{role.mention}｜冷卻 {entry['cooldown']} 秒")
        embed.add_field(
            name="受管理身分組",
            value="\n".join(lines) if lines else "尚未選擇（也可以之後用 `/mentionlimit add` 加入）",
            inline=False,
        )
        embed.add_field(
            name="模式",
            value=(
                "AutoMod 模式：冷卻期間用 AutoMod 規則封鎖，身分組保持可提及。\n"
                "關閉時：冷卻期間暫時關閉身分組的「允許任何人提及」。"
            ),
            inline=False,
        )
        if self.cog._antibeast_enabled(self.guild.id):
            embed.add_field(
                name="AntiBeast",
                value="⚠️ AntiBeast 啟用中：冷卻期間會強制以 AutoMod 規則執行，並自動將身分組加入 AntiBeast 繞過清單。",
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
            title="MentionLimit Setup: 確認啟用",
            description="確認設定後按下啟用。",
            color=discord.Color.green(),
        )
        lines = []
        for role_id, entry in self.config["roles"].items():
            role = self.guild.get_role(int(role_id))
            if role is not None:
                lines.append(f"{role.mention}｜冷卻 {entry['cooldown']} 秒")
        embed.add_field(
            name="受管理身分組",
            value="\n".join(lines) if lines else "尚未選擇",
            inline=False,
        )
        embed.add_field(
            name="模式",
            value="AutoMod 規則封鎖" if self.config["automod_mode"] else "關閉可提及",
            inline=True,
        )
        embed.add_field(
            name="計入管理員",
            value="✅ 是" if self.config["count_admins"] else "❌ 否",
            inline=True,
        )
        embed.add_field(
            name="冷卻公告",
            value="✅ 開" if self.config["announce"] else "❌ 關",
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
                    f"⚠️ 機器人缺少權限：{'、'.join(missing)}",
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
        embed.title = "MentionLimit 已啟用"
        if bypass_notes:
            embed.add_field(name="AntiBeast", value="\n".join(bypass_notes), inline=False)
        if hierarchy_warnings:
            embed.add_field(
                name="⚠️ 身分組層級",
                value=(
                    "機器人的最高身分組低於："
                    + "、".join(f"**{name}**" for name in hierarchy_warnings)
                    + "，將無法切換這些身分組的可提及狀態。"
                ),
                inline=False,
            )
        log(
            "MentionLimit 已透過 setup 啟用",
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
        super().__init__(label="繼續", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_roles(interaction)


class MentionLimitRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder="選擇要管理的身分組", min_values=1, max_values=25)

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
        super().__init__(label="保留目前設定", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_options(interaction)


class MentionLimitClearRolesButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="清空並繼續", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        self.view.config["roles"] = {}
        await self.view.show_options(interaction)


class MentionLimitCooldownButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="設定冷卻秒數", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(MentionLimitCooldownModal(self.view))


class MentionLimitContinueToConfirmButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="繼續", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_confirm(interaction)


class MentionLimitAutomodToggleButton(discord.ui.Button):
    def __init__(self, enabled: bool):
        label = "AutoMod 模式：開" if enabled else "AutoMod 模式：關"
        style = discord.ButtonStyle.primary if enabled else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.view.config["automod_mode"] = not self.view.config["automod_mode"]
        await self.view.show_options(interaction)


class MentionLimitCountAdminsToggleButton(discord.ui.Button):
    def __init__(self, enabled: bool):
        label = "計入管理員：是" if enabled else "計入管理員：否"
        style = discord.ButtonStyle.primary if enabled else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.view.config["count_admins"] = not self.view.config["count_admins"]
        await self.view.show_options(interaction)


class MentionLimitAnnounceToggleButton(discord.ui.Button):
    def __init__(self, enabled: bool):
        label = "冷卻公告：開" if enabled else "冷卻公告：關"
        style = discord.ButtonStyle.primary if enabled else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.view.config["announce"] = not self.view.config["announce"]
        await self.view.show_options(interaction)


class MentionLimitBackToRolesButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="返回選擇身分組", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_roles(interaction)


class MentionLimitEnableButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="啟用 MentionLimit", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        await self.view.finish_enable(interaction)


class MentionLimitBackToOptionsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="返回", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_options(interaction)


class MentionLimitCooldownModal(discord.ui.Modal, title="MentionLimit 冷卻設定"):
    def __init__(self, setup_view: MentionLimitSetupView):
        super().__init__()
        self.setup_view = setup_view
        cooldowns = {entry["cooldown"] for entry in setup_view.config["roles"].values()}
        default = str(cooldowns.pop()) if len(cooldowns) == 1 else str(DEFAULT_COOLDOWN)
        self.cooldown = discord.ui.TextInput(
            label=f"冷卻秒數（{MIN_COOLDOWN}-{MAX_COOLDOWN}），套用到所有已選身分組",
            default=default,
            placeholder=str(DEFAULT_COOLDOWN),
            max_length=5,
        )
        self.add_item(self.cooldown)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cooldown = int(str(self.cooldown.value).strip())
        except ValueError:
            await interaction.response.send_message("⚠️ 冷卻秒數必須是整數。", ephemeral=True)
            return
        if cooldown < MIN_COOLDOWN or cooldown > MAX_COOLDOWN:
            await interaction.response.send_message(
                f"⚠️ 冷卻時間必須介於 {MIN_COOLDOWN} 到 {MAX_COOLDOWN} 秒。",
                ephemeral=True,
            )
            return

        for entry in self.setup_view.config["roles"].values():
            entry["cooldown"] = cooldown
        await self.setup_view.show_options(interaction)


asyncio.run(bot.add_cog(MentionLimit(bot)))


if __name__ == "__main__":
    start_bot()
