from globalenv import (
    bot,
    get_all_user_data,
    get_command_mention,
    get_global_config,
    get_server_config,
    get_user_data,
    register_panel_settings,
    set_global_config,
    set_user_data,
    start_bot,
)
import discord
from discord.ext import commands, tasks
from discord import app_commands
from logger import log
import logging
import Moderate
from Moderate import check_member_hierarchy
from ModerationNotify import ignore_user
from datetime import datetime, timedelta, timezone
from pyfiglet import Figlet
import re
import random
import asyncio
import json
from OwnerTools import is_owner

import i18n
from i18n import t

fonts = [
    "6x9",
    "helvb",
    "5x8",
    "pagga",
    "xhelv",
    "keyboard",
    "future_5",
    "mono9",
    "ntgreek",
    "slant",
    "yie_ar_k",
    "chunky",
    "fuzzy",
    "xcouri",
    "script",
    "beer_pub",
    "tec_7000",
    "new_asci",
    "clr5x6",
    "utopiab",
    "xtty",
    "4max",
    "xsbooki",
    "tavl____",
    "calghpy2",
    "modern__"
]

sussy_thumbhashs = []

JOIN_DETECTION_ENABLED_KEY = "hacked_join_detection_enabled"
JOIN_DETECTION_ACTION_KEY = "hacked_join_detection_action"
CROSS_GUILD_DEFENSE_ENABLED_KEY = "hacked_cross_guild_defense_enabled"
# i18n: skip-start (the approved default action includes its persisted audit reason)
DEFAULT_JOIN_DETECTION_ACTION = "mute 28d 檢測到可疑帳號，預防性禁言。"
# i18n: skip-end


def _purge_disabled_cross_guild_cache(guild_id: int, enabled):
    """Drop cached evidence when a guild opts out through a settings surface."""
    if HackedDetector._coerce_bool(enabled, True):
        return
    cog = bot.get_cog("HackedDetector")
    if cog is not None:
        cog.purge_cross_guild_cache(int(guild_id))


register_panel_settings(
    "HackedDetector",
    "Account Safety Defense",
    [
        {
            "display": "Enable suspicious new-member detection",
            "description": "Detect suspicious new members using account age, avatar, and name characteristics",
            "database_key": JOIN_DETECTION_ENABLED_KEY,
            "type": "boolean",
            "default": True,
        },
        {
            "display": "New-member action",
            "description": "Supports custom Moderate actions; leave blank to restore the default 28-day timeout",
            "database_key": JOIN_DETECTION_ACTION_KEY,
            "type": "string",
            "default": DEFAULT_JOIN_DETECTION_ACTION,
            "action_context": "member_join",
        },
        {
            "display": "Enable global cross-channel defense",
            "description": "When disabled, this server contributes no evidence, deletes no matching messages, and receives no defense actions from other servers",
            "database_key": CROSS_GUILD_DEFENSE_ENABLED_KEY,
            "type": "boolean",
            "default": True,
            "trigger": _purge_disabled_cross_guild_cache,
        },
    ],
    description="Manage suspicious new-member detection and cross-server compromised-account defense",
    icon="🛡️",
)

class HackedDetector(commands.Cog):
    HACKED_DATA_GUILD_ID = 0
    DETECTION_WINDOW_SECONDS = 10
    DETECTION_MIN_CHANNELS = 3
    RAW_DETECTION_MIN_CHANNELS = 2
    RAW_DETECTION_WINDOW_SECONDS = 180
    DEFAULT_UNLOCK_FONT = "slant"
    TIMEOUT_DURATION = timedelta(days=28)
    LEGACY_TIMEOUT_EXPIRY_KEY = "hacked_timeout_expires_at"
    TIMEOUT_EXPIRIES_KEY = "hacked_timeout_expires_by_guild"

    def __init__(self):
        super().__init__()
        # {user_id: [{"time": float, "channel_id": int, "message": discord.Message, "message_id": int}]}
        self.usercache = {}
        # {user_id: [{"time": float, "channel_id": int}]}
        self.raw_usercache = {}
        sussy_thumbhashs.extend(get_global_config("hacked_detector_sussy_thumbhashs", []))
        log(
            f"HackedDetector initialized with detection_window={self.DETECTION_WINDOW_SECONDS}s min_channels={self.DETECTION_MIN_CHANNELS}.",
            level=logging.DEBUG,
            module_name="HackedDetector",
        )

    def _get_hacked_user_data(self, user_id: int, key: str, default=None):
        return get_user_data(self.HACKED_DATA_GUILD_ID, user_id, key, default)

    def _set_hacked_user_data(self, user_id: int, key: str, value):
        return set_user_data(self.HACKED_DATA_GUILD_ID, user_id, key, value)

    def _normalize_guild_ids(self, guild_ids):
        normalized = []
        seen = set()
        for guild_id in guild_ids or []:
            try:
                guild_id = int(guild_id)
            except (TypeError, ValueError):
                continue
            if guild_id in seen:
                continue
            seen.add(guild_id)
            normalized.append(guild_id)
        return normalized

    def _normalize_admin_removed(self, admin_removed):
        if not isinstance(admin_removed, dict):
            return {}
        normalized = {}
        for guild_id, role_id in admin_removed.items():
            try:
                guild_id = int(guild_id)
                role_id = int(role_id)
            except (TypeError, ValueError):
                continue
            normalized[guild_id] = role_id
        return normalized

    def _parse_datetime(self, value):
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

    def _timeout_until(self):
        return datetime.now(timezone.utc) + self.TIMEOUT_DURATION

    @staticmethod
    def _coerce_bool(value, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on", "enabled"}:
                return True
            if normalized in {"false", "0", "no", "off", "disabled"}:
                return False
        return default

    def _is_join_detection_enabled(self, guild_id: int) -> bool:
        return self._coerce_bool(
            get_server_config(guild_id, JOIN_DETECTION_ENABLED_KEY, True),
            True,
        )

    def _is_cross_guild_defense_enabled(self, guild_id: int) -> bool:
        return self._coerce_bool(
            get_server_config(guild_id, CROSS_GUILD_DEFENSE_ENABLED_KEY, True),
            True,
        )

    def _get_join_detection_action(self, guild_id: int) -> str:
        action = get_server_config(
            guild_id,
            JOIN_DETECTION_ACTION_KEY,
            DEFAULT_JOIN_DETECTION_ACTION,
        )
        action = str(action or "").strip()
        return action or DEFAULT_JOIN_DETECTION_ACTION

    def _normalize_timeout_expiries(self, value) -> dict[int, datetime]:
        if not isinstance(value, dict):
            return {}
        normalized = {}
        for raw_guild_id, raw_expiry in value.items():
            try:
                guild_id = int(raw_guild_id)
            except (TypeError, ValueError):
                continue
            expires_at = self._parse_datetime(raw_expiry)
            if expires_at is not None:
                normalized[guild_id] = expires_at
        return normalized

    def _write_timeout_expiries(self, user_id: int, expiries: dict[int, datetime]):
        serialized = {
            str(guild_id): expires_at.astimezone(timezone.utc).isoformat()
            for guild_id, expires_at in expiries.items()
            if expires_at is not None
        }
        self._set_hacked_user_data(user_id, self.TIMEOUT_EXPIRIES_KEY, serialized)

    def _get_timeout_expiries(self, user_id: int, guild_ids=None) -> dict[int, datetime]:
        expiries = self._normalize_timeout_expiries(
            self._get_hacked_user_data(user_id, self.TIMEOUT_EXPIRIES_KEY, {})
        )
        legacy_expiry = self._parse_datetime(
            self._get_hacked_user_data(user_id, self.LEGACY_TIMEOUT_EXPIRY_KEY)
        )
        target_guild_ids = self._normalize_guild_ids(
            guild_ids
            if guild_ids is not None
            else self._get_hacked_user_data(user_id, "hacked_timed_out_channel", [])
        )
        if legacy_expiry is not None:
            for guild_id in target_guild_ids:
                if guild_id not in expiries:
                    expiries[guild_id] = legacy_expiry
            if target_guild_ids:
                self._write_timeout_expiries(user_id, expiries)
                self._set_hacked_user_data(user_id, self.LEGACY_TIMEOUT_EXPIRY_KEY, None)
        return expiries

    def _clear_hacked_user_records(self, user_id: int):
        self._set_hacked_user_data(user_id, "hacked_timed_out_channel", [])
        self._set_hacked_user_data(user_id, "hacked_admin_removed", {})
        self._set_hacked_user_data(user_id, self.TIMEOUT_EXPIRIES_KEY, {})
        self._set_hacked_user_data(user_id, self.LEGACY_TIMEOUT_EXPIRY_KEY, None)

    def _merge_hacked_user_records(self, user_id: int, guild_expiries, admin_ids=None):
        normalized_expiries = {}
        for raw_guild_id, raw_expiry in (guild_expiries or {}).items():
            try:
                guild_id = int(raw_guild_id)
            except (TypeError, ValueError):
                continue
            expires_at = self._parse_datetime(raw_expiry)
            if expires_at is not None:
                normalized_expiries[guild_id] = expires_at

        merged_guilds = self._normalize_guild_ids(
            self._get_hacked_user_data(user_id, "hacked_timed_out_channel", [])
        )
        seen_guilds = set(merged_guilds)
        added_guilds = []
        for guild_id in normalized_expiries:
            if guild_id in seen_guilds:
                continue
            seen_guilds.add(guild_id)
            merged_guilds.append(guild_id)
            added_guilds.append(guild_id)

        merged_admin_ids = self._normalize_admin_removed(
            self._get_hacked_user_data(user_id, "hacked_admin_removed", {}) or {}
        )
        merged_admin_ids.update(self._normalize_admin_removed(admin_ids or {}))

        self._set_hacked_user_data(user_id, "hacked_timed_out_channel", merged_guilds)
        self._set_hacked_user_data(user_id, "hacked_admin_removed", merged_admin_ids)
        merged_expiries = self._get_timeout_expiries(user_id, merged_guilds)
        merged_expiries.update(normalized_expiries)
        self._write_timeout_expiries(user_id, merged_expiries)
        self._set_hacked_user_data(user_id, self.LEGACY_TIMEOUT_EXPIRY_KEY, None)
        if normalized_expiries:
            set_user_data(0, user_id, "verified", False)
        return merged_guilds, merged_admin_ids, added_guilds, merged_expiries

    def purge_cross_guild_cache(self, guild_id: int):
        guild_id = int(guild_id)
        for cache in (self.usercache, self.raw_usercache):
            for user_id in list(cache):
                events = [
                    event for event in cache[user_id]
                    if event.get("guild_id") != guild_id
                ]
                if events:
                    cache[user_id] = events
                else:
                    cache.pop(user_id, None)

    def _filter_enabled_cross_guild_events(self, cache: dict, user_id: int, events: list[dict]):
        enabled_events = [
            event for event in events
            if event.get("guild_id") is not None
            and self._is_cross_guild_defense_enabled(event["guild_id"])
        ]
        if enabled_events:
            cache[user_id] = enabled_events
        else:
            cache.pop(user_id, None)
        return enabled_events

    def _prune_cached_events(self, cache: dict, user_id: int, now: float, window_seconds: int):
        window_start = now - window_seconds
        events = [
            event for event in cache.get(user_id, [])
            if event.get("time", 0) >= window_start
        ]
        if events:
            cache[user_id] = events
        else:
            cache.pop(user_id, None)
        return events

    def _render_unlock_code_art(self, code: str):
        font_name = random.choice(fonts or [self.DEFAULT_UNLOCK_FONT])
        try:
            return Figlet(font=font_name).renderText(code), font_name
        except Exception as e:
            log(
                f"Failed to render unlock code with font {font_name}: {e}",
                level=logging.WARNING,
                module_name="HackedDetector",
            )
            fallback_font = self.DEFAULT_UNLOCK_FONT
            return Figlet(font=fallback_font).renderText(code), fallback_font

    def _prune_suspicious_events(self, user_id: int, now: float):
        return self._prune_cached_events(self.usercache, user_id, now, self.DETECTION_WINDOW_SECONDS)

    def _record_suspicious_event(self, message: discord.Message):
        now = asyncio.get_running_loop().time()
        events = self._prune_suspicious_events(message.author.id, now)
        events.append({
            "time": now,
            "guild_id": message.guild.id,
            "channel_id": message.channel.id,
            "message": message,
            "message_id": message.id,
        })
        self.usercache[message.author.id] = events
        return self._filter_enabled_cross_guild_events(
            self.usercache,
            message.author.id,
            events,
        )

    def _record_raw_suspicious_event(self, user_id: int, guild_id: int, channel_id: int):
        now = asyncio.get_running_loop().time()
        events = self._prune_cached_events(self.raw_usercache, user_id, now, self.RAW_DETECTION_WINDOW_SECONDS)
        events.append({
            "time": now,
            "guild_id": guild_id,
            "channel_id": channel_id,
        })
        self.raw_usercache[user_id] = events
        return self._filter_enabled_cross_guild_events(
            self.raw_usercache,
            user_id,
            events,
        )

    async def _delete_detected_messages(self, events: list[dict]):
        deleted = 0
        failed = 0
        seen_message_ids = set()

        for event in events:
            guild_id = event.get("guild_id")
            if guild_id is not None and not self._is_cross_guild_defense_enabled(guild_id):
                continue
            message = event.get("message")
            message_id = event.get("message_id")
            if message is None or message_id in seen_message_ids:
                continue
            seen_message_ids.add(message_id)
            try:
                await message.delete()
                deleted += 1
            except discord.NotFound:
                continue
            except Exception as e:
                failed += 1
                log(
                    f"Failed to delete suspicious message {message_id} from user {message.author.id} in channel {message.channel.id}: {e}",
                    level=logging.ERROR,
                    module_name="HackedDetector",
                    user=message.author,
                    guild=message.guild,
                )

        return deleted, failed

    async def _notify_unlock_in_channel(self, user: discord.User, channel):
        if channel is None or not hasattr(channel, "send"):
            return False

        try:
            command_mention = await get_command_mention("imhacked") or "/imhacked"
        except Exception as e:
            command_mention = "/imhacked"
            log(
                f"Failed to resolve /imhacked mention for user {user.id}: {e}",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=user,
                guild=getattr(channel, "guild", None),
            )

        try:
            await channel.send(
                user.mention + " " + t("hackeddetector.msg.dm_failed_fallback",
                                       locale=i18n.resolve_locale(user_id=user.id),
                                       command=command_mention),
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
            log(
                f"Fallback unlock instructions sent in channel {channel.id} for user {user.id}.",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=user,
                guild=getattr(channel, "guild", None),
            )
            return True
        except Exception as e:
            log(
                f"Failed to send fallback unlock instructions in channel {getattr(channel, 'id', 'unknown')} for user {user.id}: {e}",
                level=logging.ERROR,
                module_name="HackedDetector",
                user=user,
                guild=getattr(channel, "guild", None),
            )
            return False

    async def unlock_user(self, user: discord.User):
        # untimeout the user in all mutual guilds
        guilds = self._normalize_guild_ids(self._get_hacked_user_data(user.id, "hacked_timed_out_channel", []))
        admin_removed = self._normalize_admin_removed(self._get_hacked_user_data(user.id, "hacked_admin_removed", {}) or {})
        log(f"Unlock flow started for user {user.id}. timed_out_guilds={len(guilds)}, admin_roles={len(admin_removed)}", level=logging.DEBUG, module_name="HackedDetector", user=user)
        if not guilds:
            log(f"Unlock flow aborted for user {user.id}: no timed out guild records.", level=logging.DEBUG, module_name="HackedDetector", user=user)
            return False
        for guild_id in guilds:
            guild = bot.get_guild(int(guild_id)) if guild_id is not None else None
            if not guild:
                log(f"Unlock skipped guild_id={guild_id}: guild not found in cache.", level=logging.DEBUG, module_name="HackedDetector", user=user)
                continue
            member = guild.get_member(user.id)
            if not member:
                log(f"Unlock skipped guild={guild.id}: member {user.id} not found.", level=logging.DEBUG, module_name="HackedDetector", user=user)
                continue
            try:
                # discord.Member.timeout is a coroutine in discord.py v2 → await it
                await member.timeout(None, reason=t("hackeddetector.audit.unlock",
                                                     locale=i18n.resolve_locale(guild_id=guild.id)))
                # log(f"User {user} has been unmuted in guild {guild.name} ({guild.id}).", level=logging.INFO, module_name="HackedDetector", user=user) ##anti 429##
            except Exception as e:
                log(f"Failed to untimeout user {user} in guild {guild.name} ({guild.id}): {e}", level=logging.ERROR, module_name="HackedDetector", user=user)
            # admin_removed may have string keys if persisted via JSON, be tolerant
            admin_role_id = admin_removed.get(guild_id) or admin_removed.get(str(guild_id))
            if admin_role_id:
                admin_role = guild.get_role(int(admin_role_id)) if admin_role_id is not None else None
                if admin_role:
                    try:
                        await member.add_roles(admin_role, reason=t("hackeddetector.audit.restore_admin",
                                                                    locale=i18n.resolve_locale(guild_id=guild.id)))
                        log(f"User {user} has been restored admin role in guild {guild.name} ({guild.id}).", level=logging.INFO, module_name="HackedDetector", user=user)
                    except Exception as e:
                        log(f"Failed to restore admin role to user {user} in guild {guild.name} ({guild.id}): {e}", level=logging.ERROR, module_name="HackedDetector", user=user)
                else:
                    log(f"Restore admin skipped for user {user.id} in guild={guild.id}: role_id={admin_role_id} not found.", level=logging.DEBUG, module_name="HackedDetector", user=user)
        # 清理資料
        self._clear_hacked_user_records(user.id)
        set_user_data(0, user.id, "verified", True)
        log(f"Unlock flow finished for user {user.id}. Records cleared.", level=logging.DEBUG, module_name="HackedDetector", user=user)
        return True

    def _active_timeout_until(self, member: discord.Member):
        expires_at = self._parse_datetime(getattr(member, "timed_out_until", None))
        if expires_at is None or expires_at <= datetime.now(timezone.utc):
            return None
        return expires_at

    def _expanded_action_commands(self, action: str, guild_id: int) -> set[str]:
        try:
            expanded = Moderate._expand_custom_action_aliases(
                action,
                Moderate._load_custom_action_strings(guild_id),
            )
        except ValueError:
            return set()
        return {
            chunk.split(maxsplit=1)[0].lower()
            for chunk in expanded
            if chunk.strip()
        }

    async def _send_recovery_notice(
        self,
        user: discord.User,
        all_muted: list[int],
        *,
        failed: int = 0,
        channel=None,
    ):
        recipient_loc = i18n.resolve_locale(user_id=user.id)
        embed = discord.Embed(
            title=t("hackeddetector.dm.title", locale=recipient_loc),
            description=t("hackeddetector.dm.desc", locale=recipient_loc),
            color=discord.Color.red()
        )
        embed.add_field(name=t("hackeddetector.dm.muted_guild_count", locale=recipient_loc), value=str(len(all_muted)), inline=False)
        if failed:
            embed.add_field(name=t("hackeddetector.dm.failed_guild_count", locale=recipient_loc), value=str(failed), inline=False)
        embed.timestamp = datetime.now()
        try:
            with i18n.use_locale(recipient_loc):
                await user.send(embed=embed, view=self.StartUnlockView(self))
            log(f"Warning DM sent to suspected hacked user {user.id}.", level=logging.DEBUG, module_name="HackedDetector", user=user)
        except Exception as e:
            log(f"Failed to send DM to user {user}: {e}", level=logging.ERROR, module_name="HackedDetector", user=user)
            if channel is not None:
                await self._notify_unlock_in_channel(user, channel)

    async def _apply_fixed_cross_guild_timeout(self, member: discord.Member):
        guild = member.guild
        if self._active_timeout_until(member) is not None:
            log(
                f"Skip cross-guild timeout for member {member.id} in guild {guild.id}: already timed out.",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=member,
                guild=guild,
            )
            return None, None

        bot_member = guild.me
        if bot_member is None:
            log(
                f"Skip cross-guild timeout for member {member.id} in guild {guild.id}: bot member unavailable.",
                level=logging.ERROR,
                module_name="HackedDetector",
                user=member,
                guild=guild,
            )
            return None, None
        ok, hierarchy_message = check_member_hierarchy(bot_member, member, bot_member)
        if not ok:
            log(
                f"Skip cross-guild timeout for member {member.id} in guild {guild.id}: hierarchy check failed ({hierarchy_message}).",
                level=logging.ERROR,
                module_name="HackedDetector",
                user=member,
                guild=guild,
            )
            return None, None

        removed_admin_role_id = None
        if member.guild_permissions.administrator:
            admin_roles = [
                role for role in getattr(member, "roles", [])
                if not role.is_default() and role.permissions.administrator
            ]
            admin_role = max(admin_roles, key=lambda role: role.position, default=None)
            if admin_role is None:
                log(
                    f"Failed to find an assigned administrator role for member {member.id} in guild {guild.id}.",
                    level=logging.ERROR,
                    module_name="HackedDetector",
                    user=member,
                    guild=guild,
                )
                return None, None
            try:
                await member.remove_roles(
                    admin_role,
                    reason=t(
                        "hackeddetector.audit.remove_admin",
                        locale=i18n.resolve_locale(guild_id=guild.id),
                    ),
                )
                removed_admin_role_id = admin_role.id
            except Exception as error:
                log(
                    f"Failed to remove admin role from member {member.id} in guild {guild.id}: {error}",
                    level=logging.ERROR,
                    module_name="HackedDetector",
                    user=member,
                    guild=guild,
                )
                return None, None

        until = self._timeout_until()
        try:
            await member.timeout(
                until,
                reason=t(
                    "hackeddetector.audit.hacked_timeout",
                    locale=i18n.resolve_locale(guild_id=guild.id),
                ),
            )
        except Exception as error:
            log(
                f"Failed to cross-guild timeout member {member.id} in guild {guild.id}: {error}",
                level=logging.ERROR,
                module_name="HackedDetector",
                user=member,
                guild=guild,
            )
            if removed_admin_role_id is not None:
                admin_role = guild.get_role(removed_admin_role_id)
                if admin_role is not None:
                    try:
                        await member.add_roles(
                            admin_role,
                            reason=t(
                                "hackeddetector.audit.restore_admin",
                                locale=i18n.resolve_locale(guild_id=guild.id),
                            ),
                        )
                    except Exception as restore_error:
                        log(
                            f"Failed to roll back admin role {removed_admin_role_id} for member {member.id} "
                            f"in guild {guild.id} after timeout failure: {restore_error}",
                            level=logging.ERROR,
                            module_name="HackedDetector",
                            user=member,
                            guild=guild,
                        )
            return None, None
        return until, removed_admin_role_id

    async def handle_hacked_user(self, user: discord.User, channel: discord.TextChannel = None):
        log(
            f"User {user} is suspected to be hacked. Sent messages in multiple channels within a short time frame.",
            level=logging.WARNING,
            module_name="HackedDetector",
            user=user,
        )
        guilds = [
            guild for guild in user.mutual_guilds
            if self._is_cross_guild_defense_enabled(guild.id)
        ]
        if not guilds:
            log(
                f"No enabled cross-guild defense targets for user {user.id}.",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=user,
            )
            return

        try:
            ignore_user(user.id)
        except Exception:
            log(f"ignore_user failed for {user.id}", level=logging.DEBUG, module_name="HackedDetector", user=user)

        muted_expiries = {}
        admin_ids = {}
        failed = 0
        for guild in guilds:
            if not self._is_cross_guild_defense_enabled(guild.id):
                continue
            member = guild.get_member(user.id)
            if member is None:
                continue
            expires_at, admin_role_id = await self._apply_fixed_cross_guild_timeout(member)
            if expires_at is None:
                failed += 1
                continue
            muted_expiries[guild.id] = expires_at
            if admin_role_id is not None:
                admin_ids[guild.id] = admin_role_id

        if not muted_expiries:
            log(
                f"No new cross-guild timeouts were applied for user {user.id}; failed_or_skipped={failed}.",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=user,
            )
            return

        all_muted, all_admin_ids, added_guilds, expiries = self._merge_hacked_user_records(
            user.id,
            muted_expiries,
            admin_ids,
        )
        log(
            f"Cross-guild timeout applied for user {user.id}: guilds={list(muted_expiries)}, "
            f"added_records={added_guilds}, total_records={all_muted}, "
            f"removed_admins={len(all_admin_ids)}, expiries={expiries}, failed_or_skipped={failed}.",
            level=logging.INFO,
            module_name="HackedDetector",
            user=user,
        )
        await self._send_recovery_notice(user, all_muted, failed=failed, channel=channel)

    async def handle_cross_guild_join(self, member: discord.Member):
        if not self._is_cross_guild_defense_enabled(member.guild.id):
            return False
        try:
            ignore_user(member.id)
        except Exception:
            log(
                f"ignore_user failed for {member.id}",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=member,
                guild=member.guild,
            )

        expires_at, admin_role_id = await self._apply_fixed_cross_guild_timeout(member)
        if expires_at is None:
            return False
        admin_ids = {member.guild.id: admin_role_id} if admin_role_id is not None else None
        all_muted, _, _, _ = self._merge_hacked_user_records(
            member.id,
            {member.guild.id: expires_at},
            admin_ids,
        )
        await self._send_recovery_notice(member, all_muted)
        return True

    async def handle_suspicious_join(self, member: discord.Member):
        if not self._is_join_detection_enabled(member.guild.id):
            return False

        action = self._get_join_detection_action(member.guild.id)
        analysis = Moderate.analyze_member_join_action(action, member.guild.id)
        if not analysis.get("valid") or analysis.get("requires_confirmation"):
            log(
                f"Invalid stored suspicious-join action for guild {member.guild.id}: "
                f"{analysis.get('error') or analysis.get('confirmation')}",
                level=logging.ERROR,
                module_name="HackedDetector",
                user=member,
                guild=member.guild,
            )
            return False

        normalized_action = str(analysis.get("normalized") or "").strip()
        action_commands = self._expanded_action_commands(normalized_action, member.guild.id)
        has_timeout_action = bool(action_commands & {"mute", "timeout", "to"})
        previous_timeout = self._active_timeout_until(member) if has_timeout_action else None

        try:
            ignore_user(member.id)
        except Exception:
            log(
                f"ignore_user failed for {member.id}",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=member,
                guild=member.guild,
            )

        try:
            action_logs, action_status = await Moderate.do_action_str(
                normalized_action,
                guild=member.guild,
                user=member,
                message=None,
                moderator=member.guild.me,
                return_status=True,
            )
        except Exception as error:
            log(
                f"Suspicious-join action execution failed for member {member.id} in guild {member.guild.id}: {error}",
                level=logging.ERROR,
                module_name="HackedDetector",
                user=member,
                guild=member.guild,
            )
            return False

        log(
            f"Suspicious-join action executed for member {member.id} in guild {member.guild.id}: "
            f"status={action_status}, logs={action_logs}",
            level=logging.INFO if action_status == "success" else logging.WARNING,
            module_name="HackedDetector",
            user=member,
            guild=member.guild,
        )
        if not has_timeout_action:
            return True

        try:
            refreshed_member = await member.guild.fetch_member(member.id)
        except discord.NotFound:
            log(
                f"No HackedDetector recovery record for member {member.id} in guild {member.guild.id}: member left after action.",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=member,
                guild=member.guild,
            )
            return True
        except Exception as error:
            log(
                f"Could not confirm suspicious-join timeout for member {member.id} in guild {member.guild.id}: {error}",
                level=logging.ERROR,
                module_name="HackedDetector",
                user=member,
                guild=member.guild,
            )
            return False

        actual_timeout = self._active_timeout_until(refreshed_member)
        if actual_timeout is None:
            log(
                f"No HackedDetector recovery record for member {member.id} in guild {member.guild.id}: no active timeout after action.",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=member,
                guild=member.guild,
            )
            return True
        if previous_timeout is not None and abs((actual_timeout - previous_timeout).total_seconds()) < 1:
            log(
                f"No HackedDetector recovery record for member {member.id} in guild {member.guild.id}: timeout was unchanged.",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=member,
                guild=member.guild,
            )
            return True

        all_muted, _, _, _ = self._merge_hacked_user_records(
            member.id,
            {member.guild.id: actual_timeout},
        )
        await self._send_recovery_notice(member, all_muted)
        return True

    class UnlockModal(i18n.I18nModal, title=i18n.K("hackeddetector.modal.unlock_title")):
        # enter code to unlock account
        code = discord.ui.TextInput(label=i18n.K("hackeddetector.modal.code_label"),
                                    placeholder=i18n.K("hackeddetector.modal.code_ph"), required=True)

        def __init__(self, user: discord.User, parent, code: str):
            super().__init__()
            self.user = user
            self.parent = parent
            self.original_code = code
    
        async def on_submit(self, interaction: discord.Interaction):
            log(f"Unlock modal submitted by user {interaction.user.id} for target {self.user.id}.", level=logging.DEBUG, module_name="HackedDetector", user=interaction.user, guild=interaction.guild)
            if interaction.user.id != self.user.id:
                await interaction.response.send_message(t("hackeddetector.err.not_your_button"), ephemeral=True)
                return
            if self.original_code != self.code.value:
                log(f"Unlock code mismatch for user {interaction.user.id}.", level=logging.DEBUG, module_name="HackedDetector", user=interaction.user, guild=interaction.guild)
                await interaction.response.send_message(t("hackeddetector.err.wrong_code"), ephemeral=True)
                return
            success = await self.parent.unlock_user(self.user)
            if success:
                await interaction.response.send_message(t("hackeddetector.msg.unlocked"), ephemeral=True)
                log(f"Unlock success for user {interaction.user.id}.", level=logging.DEBUG, module_name="HackedDetector", user=interaction.user, guild=interaction.guild)
            else:
                await interaction.response.send_message(t("hackeddetector.msg.unlock_failed"), ephemeral=True)
                log(f"Unlock failed for user {interaction.user.id} (no records or no guild actions).", level=logging.DEBUG, module_name="HackedDetector", user=interaction.user, guild=interaction.guild)

    class UnlockView(i18n.I18nView):
        def __init__(self, user: discord.User, parent, code: str):
            super().__init__()
            self.user = user
            self.parent = parent
            self.code = code

        @discord.ui.button(label=i18n.K("hackeddetector.btn.enter_code"), style=discord.ButtonStyle.green, custom_id="hacked:unlock_code_input")
        async def unlock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.user.id:
                await interaction.response.send_message(t("hackeddetector.err.not_your_button"), ephemeral=True)
                return
            # parent 為 cog instance
            modal = self.parent.UnlockModal(self.user, self.parent, self.code)
            await interaction.response.send_modal(modal)
            log(f"Unlock modal opened for user {interaction.user.id}.", level=logging.DEBUG, module_name="HackedDetector", user=interaction.user, guild=interaction.guild)

    class StartUnlockView(i18n.I18nView):
        def __init__(self, parent):
            super().__init__(timeout=None)
            # parent 必須是 cog instance
            self.parent = parent

        @discord.ui.button(label=i18n.K("hackeddetector.btn.start_unlock"), style=discord.ButtonStyle.green, custom_id="hacked:start_unlock")
        async def start_unlock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            user = interaction.user
            code = str(random.randint(1, 9999)).zfill(4)
            rendered_code, font_name = self.parent._render_unlock_code_art(code)
            embed = discord.Embed(
                title=t("hackeddetector.unlock.code_title"),
                description=f"```\n{rendered_code}\n```\n" + t("hackeddetector.unlock.code_prompt"),
                color=discord.Color.blue()
            )
            embed.timestamp = datetime.now()
            # 傳入正確的 parent (cog instance)
            view = self.parent.UnlockView(user, self.parent, code)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            log(f"Sent unlock challenge to user {user.id} with font={font_name}.", level=logging.DEBUG, module_name="HackedDetector", user=user)


    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        if not self._is_cross_guild_defense_enabled(message.guild.id):
            return

        # check message is matched some pattern that indicates the user might be hacked
        # discord invite links or 4 attachment images in a message
        has_invite = re.search(r"(https?://)?(www\.)?(discord\.gg|discordapp\.com/invite)/[a-zA-Z0-9]+", message.content)
        # 4 or 2 attachment images in a message
        if not (has_invite or (len(message.attachments) == 4 or len(message.attachments) == 2)):
            return
        log(
            f"Suspicious pattern matched from user {message.author.id} in channel {message.channel.id}: has_invite={bool(has_invite)} attachments={len(message.attachments)}",
            level=logging.DEBUG,
            module_name="HackedDetector",
            user=message.author,
            guild=message.guild,
        )

        events = self._record_suspicious_event(message)
        events = self._filter_enabled_cross_guild_events(
            self.usercache,
            message.author.id,
            events,
        )
        channel_ids = sorted({event["channel_id"] for event in events})
        log(
            f"User {message.author.id} suspicious window events={len(events)} unique_channels={channel_ids}",
            level=logging.DEBUG,
            module_name="HackedDetector",
            user=message.author,
            guild=message.guild,
        )

        if len(channel_ids) < self.DETECTION_MIN_CHANNELS:
            return

        detected_events = list(events)
        self.usercache.pop(message.author.id, None)

        deleted, delete_failed = await self._delete_detected_messages(detected_events)
        log(
            f"Suspicious message cleanup for user {message.author.id}: deleted={deleted}, failed={delete_failed}",
            level=logging.INFO,
            module_name="HackedDetector",
            user=message.author,
            guild=message.guild,
        )

        # check if user is already timed out
        timed_out = self._normalize_guild_ids(self._get_hacked_user_data(message.author.id, "hacked_timed_out_channel", []))
        if timed_out:
            log(f"Continue handling user {message.author.id}: existing timeout records will be preserved while checking for new guilds {timed_out}.", level=logging.DEBUG, module_name="HackedDetector", user=message.author, guild=message.guild)

        log(
            f"Trigger hacked handling for user {message.author.id} with channels={channel_ids} in {self.DETECTION_WINDOW_SECONDS}s window.",
            level=logging.DEBUG,
            module_name="HackedDetector",
            user=message.author,
            guild=message.guild,
        )
        await self.handle_hacked_user(message.author, channel=message.channel)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot:
            return

        verified = get_user_data(0, member.id, "verified", False)
        if verified:
            log(f"User {member.id} joined guild {member.guild.id} but is already verified. No action taken.", level=logging.DEBUG, module_name="HackedDetector", user=member, guild=member.guild)
            return

        existing_timed_out = self._normalize_guild_ids(self._get_hacked_user_data(member.id, "hacked_timed_out_channel", []))
        if member.guild.id in existing_timed_out and self._active_timeout_until(member) is not None:
            log(
                f"Previously handled user {member.id} rejoined guild {member.guild.id} with an active recorded timeout; no new action taken.",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=member,
                guild=member.guild,
            )
            return

        if existing_timed_out and member.guild.id not in existing_timed_out:
            if self._is_cross_guild_defense_enabled(member.guild.id):
                log(
                    f"Previously handled user {member.id} joined new guild {member.guild.id}; applying fixed cross-guild timeout and preserving existing records {existing_timed_out}.",
                    level=logging.WARNING,
                    module_name="HackedDetector",
                    user=member,
                    guild=member.guild,
                )
                try:
                    await self.handle_cross_guild_join(member)
                except Exception as e:
                    log(f"Failed to handle previously timed out member {member}: {e}", level=logging.ERROR, module_name="HackedDetector", user=member, guild=member.guild)
                return
            log(
                f"Previously handled user {member.id} joined guild {member.guild.id}, but cross-guild defense is disabled; evaluating the independent join heuristic.",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=member,
                guild=member.guild,
            )

        if not self._is_join_detection_enabled(member.guild.id):
            return

        score = 0
        global_name = member.global_name or ""
        # check if the member is sus
        # no avatar
        if member.avatar is None:
            score += 1
        else:
            score -= 3
        # account age < 30 days
        if (discord.utils.utcnow() - member.created_at).days < 30:
            score += 1
        # global display name starts with "!"
        if global_name.startswith("!"):
            score += 1
        # username "english+numbers" only
        if re.fullmatch(r"[A-Za-z0-9]+", member.name):
            score += 1
        # global name full english
        if global_name and re.fullmatch(r"[A-Za-z0-9]+", global_name):
            score += 1
        # how many spaces in global name
        # if global_name and global_name.count(" ") > 0:
        #     score += global_name.count(" ")
        # split words in global name and every word first is uppercase
        if global_name:
            words = global_name.split()
            for word in words:
                if word[0].isupper():
                    score += 1

        # final
        if score >= 5:
            log(
                f"Suspicious new member detected: {member} (id={member.id}) score={score}",
                level=logging.WARNING,
                module_name="HackedDetector",
                user=member,
                guild=member.guild,
            )
            try:
                await self.handle_suspicious_join(member)
            except Exception as e:
                log(f"Failed to handle suspicious member {member}: {e}", level=logging.ERROR, module_name="HackedDetector", user=member, guild=member.guild)

    @commands.Cog.listener()
    async def on_socket_raw_receive(self, payload):
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8")
            except Exception:
                return
        if not isinstance(payload, str):
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        if data.get("t") != "MESSAGE_CREATE":
            return

        message = data.get("d", {})
        author = message.get("author", {})
        if author.get("bot") or message.get("guild_id") is None:
            return

        attachments = message.get("attachments", [])
        if not attachments or (len(attachments) != 4 and len(attachments) != 2):
            return
        if not all(attachment.get("placeholder") in sussy_thumbhashs for attachment in attachments):
            return

        try:
            user_id = int(author.get("id"))
            channel_id = int(message.get("channel_id"))
            guild_id = int(message.get("guild_id"))
        except (TypeError, ValueError):
            return

        if not self._is_cross_guild_defense_enabled(guild_id):
            return

        guild = bot.get_guild(guild_id)
        events = self._record_raw_suspicious_event(user_id, guild_id, channel_id)
        events = self._filter_enabled_cross_guild_events(
            self.raw_usercache,
            user_id,
            events,
        )
        channel_ids = sorted({event["channel_id"] for event in events})
        log(
            f"Raw suspicious window events for user {user_id}: events={len(events)} unique_channels={channel_ids}",
            level=logging.DEBUG,
            module_name="HackedDetector",
            guild=guild,
        )

        if len(channel_ids) < self.RAW_DETECTION_MIN_CHANNELS:
            return

        self.raw_usercache.pop(user_id, None)

        timed_out = self._normalize_guild_ids(self._get_hacked_user_data(user_id, "hacked_timed_out_channel", []))
        if timed_out:
            log(
                f"Continue raw handling user {user_id}: existing timeout records will be preserved while checking for new guilds {timed_out}.",
                level=logging.DEBUG,
                module_name="HackedDetector",
                guild=guild,
            )

        user = bot.get_user(user_id)
        channel = bot.get_channel(channel_id)
        if user is None or channel is None:
            log(
                f"Skip raw handling user {user_id}: user or channel not found. channel_id={channel_id}",
                level=logging.DEBUG,
                module_name="HackedDetector",
                guild=guild,
            )
            return

        log(
            f"Trigger raw hacked handling for user {user_id} with channels={channel_ids} in {self.RAW_DETECTION_WINDOW_SECONDS}s window.",
            level=logging.DEBUG,
            module_name="HackedDetector",
            user=user,
            guild=guild,
        )
        await self.handle_hacked_user(user, channel=channel)

    async def _kick_expired_unverified_user(self, user_id: int, now: datetime | None = None):
        if get_user_data(0, user_id, "verified", False):
            self._clear_hacked_user_records(user_id)
            log(
                f"Skip expired hacked timeout kick for verified user {user_id}; records cleared.",
                level=logging.DEBUG,
                module_name="HackedDetector",
            )
            return

        recorded_guild_ids = self._normalize_guild_ids(
            self._get_hacked_user_data(user_id, "hacked_timed_out_channel", [])
        )
        expiries = self._get_timeout_expiries(user_id, recorded_guild_ids)
        guild_ids = self._normalize_guild_ids([*recorded_guild_ids, *expiries])
        if not guild_ids:
            self._clear_hacked_user_records(user_id)
            log(
                f"Expired hacked timeout for user {user_id} had no guild records; records cleared.",
                level=logging.DEBUG,
                module_name="HackedDetector",
            )
            return

        now = self._parse_datetime(now) or datetime.now(timezone.utc)
        due_guild_ids = {
            guild_id
            for guild_id, expires_at in expiries.items()
            if expires_at <= now
        }
        if not due_guild_ids:
            return

        admin_removed = self._normalize_admin_removed(self._get_hacked_user_data(user_id, "hacked_admin_removed", {}) or {})
        remaining_guilds = list(guild_ids)
        remaining_expiries = dict(expiries)
        remaining_admin_removed = dict(admin_removed)
        kicked_guilds = []
        skipped_guilds = []
        failed_guilds = []

        def remove_guild_record(guild_id: int):
            if guild_id in remaining_guilds:
                remaining_guilds.remove(guild_id)
            remaining_expiries.pop(guild_id, None)
            remaining_admin_removed.pop(guild_id, None)

        for guild_id in guild_ids:
            if guild_id not in due_guild_ids:
                continue
            guild = bot.get_guild(guild_id)
            if guild is None:
                skipped_guilds.append(guild_id)
                remove_guild_record(guild_id)
                log(f"Skip expired hacked timeout kick for user {user_id} in guild {guild_id}: guild not found.", level=logging.DEBUG, module_name="HackedDetector")
                continue

            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    member = None
                except Exception as error:
                    failed_guilds.append(guild_id)
                    log(
                        f"Failed to fetch expired unverified user {user_id} in guild {guild_id}: {error}",
                        level=logging.ERROR,
                        module_name="HackedDetector",
                        guild=guild,
                    )
                    continue
            if member is None:
                skipped_guilds.append(guild_id)
                remove_guild_record(guild_id)
                log(f"Skip expired hacked timeout kick for user {user_id} in guild {guild_id}: member not found.", level=logging.DEBUG, module_name="HackedDetector", guild=guild)
                continue

            bot_member = guild.me
            if bot_member is None:
                failed_guilds.append(guild_id)
                log(f"Failed to kick expired unverified user {user_id} in guild {guild_id}: bot member not found.", level=logging.ERROR, module_name="HackedDetector", user=member, guild=guild)
                continue

            ok, msg = check_member_hierarchy(bot_member, member, bot_member)
            if not ok:
                failed_guilds.append(guild_id)
                log(f"Failed to kick expired unverified user {user_id} in guild {guild_id}: hierarchy check failed ({msg}).", level=logging.ERROR, module_name="HackedDetector", user=member, guild=guild)
                continue

            try:
                await member.kick(reason=t("hackeddetector.audit.unverified_kick", locale=i18n.resolve_locale(guild_id=guild.id)))
                kicked_guilds.append(guild_id)
                remove_guild_record(guild_id)
            except Exception as e:
                failed_guilds.append(guild_id)
                log(f"Failed to kick expired unverified user {user_id} in guild {guild_id}: {e}", level=logging.ERROR, module_name="HackedDetector", user=member, guild=guild)

        if remaining_guilds:
            self._set_hacked_user_data(user_id, "hacked_timed_out_channel", remaining_guilds)
            self._set_hacked_user_data(user_id, "hacked_admin_removed", remaining_admin_removed)
            self._write_timeout_expiries(user_id, remaining_expiries)
            self._set_hacked_user_data(user_id, self.LEGACY_TIMEOUT_EXPIRY_KEY, None)
        else:
            self._clear_hacked_user_records(user_id)

        log(
            f"Expired hacked timeout kick processed for user {user_id}: due={sorted(due_guild_ids)}, "
            f"kicked={kicked_guilds}, skipped={skipped_guilds}, failed={failed_guilds}, "
            f"remaining={remaining_guilds}.",
            level=logging.INFO if kicked_guilds or skipped_guilds else logging.WARNING,
            module_name="HackedDetector",
        )

    @tasks.loop(minutes=5)
    async def expired_unverified_kick_task(self):
        expiry_rows = get_all_user_data(self.HACKED_DATA_GUILD_ID, self.TIMEOUT_EXPIRIES_KEY)
        legacy_rows = get_all_user_data(self.HACKED_DATA_GUILD_ID, self.LEGACY_TIMEOUT_EXPIRY_KEY)
        user_ids = {
            raw_user_id
            for raw_user_id, values in expiry_rows.items()
            if isinstance(values, dict)
            and self._normalize_timeout_expiries(values.get(self.TIMEOUT_EXPIRIES_KEY))
        }
        user_ids.update(
            raw_user_id
            for raw_user_id, values in legacy_rows.items()
            if isinstance(values, dict)
            and self._parse_datetime(values.get(self.LEGACY_TIMEOUT_EXPIRY_KEY)) is not None
        )
        if not user_ids:
            return

        now = datetime.now(timezone.utc)
        for raw_user_id in user_ids:
            try:
                user_id = int(raw_user_id)
            except (TypeError, ValueError):
                continue
            await self._kick_expired_unverified_user(user_id, now)

    @expired_unverified_kick_task.before_loop
    async def before_expired_unverified_kick_task(self):
        await bot.wait_until_ready()

    async def cog_load(self):
        # 註冊 persistent view：timeout=None + 穩定 custom_id
        bot.add_view(self.StartUnlockView(self))
        if not self.expired_unverified_kick_task.is_running():
            self.expired_unverified_kick_task.start()
        log("HackedDetector cog loaded and persistent view registered.", level=logging.DEBUG, module_name="HackedDetector")

    async def cog_unload(self):
        self.expired_unverified_kick_task.cancel()

    @app_commands.command(name=app_commands.locale_str("imhacked", i18n_key="cmd.hackeddetector.imhacked.name"), description=app_commands.locale_str("Start the compromised-account recovery flow", i18n_key="cmd.hackeddetector.imhacked.desc"))
    async def imhacked(self, interaction: discord.Interaction):
        user = interaction.user
        timed_out = self._get_hacked_user_data(user.id, "hacked_timed_out_channel", [])
        log(f"/imhacked invoked by user {user.id}, timeout_records={len(timed_out)}", level=logging.DEBUG, module_name="HackedDetector", user=user, guild=interaction.guild)
        if not timed_out:
            log(f"User {user} used /imhacked but has no active timeouts.", level=logging.DEBUG, module_name="HackedDetector", user=user)
            await interaction.response.send_message(t("hackeddetector.msg.no_timeout_records"), ephemeral=True)
            return
        await interaction.response.send_message(t("hackeddetector.msg.press_to_start"), view=self.StartUnlockView(self), ephemeral=True)

    @commands.command(name="hack-addhash", description="Add a suspicious thumbhash for hacked account detection (owner only)")
    @is_owner()
    async def hack_addhash(self, ctx: commands.Context, thumbhash: str):
        # i18n: skip-start (owner-facing)
        if thumbhash in sussy_thumbhashs:
            await ctx.send("這個 thumbhash 已經在列表中了。")
            return
        sussy_thumbhashs.append(thumbhash)
        set_global_config("hacked_detector_sussy_thumbhashs", sussy_thumbhashs)
        await ctx.send(f"已添加 suspicious thumbhash: {thumbhash}")
        # i18n: skip-end

    @commands.command(name="hack-clearhashes", description="Clear all suspicious thumbhashes (owner only)")
    @is_owner()
    async def hack_clearhashes(self, ctx: commands.Context):
        # i18n: skip-start (owner-facing)
        sussy_thumbhashs.clear()
        set_global_config("hacked_detector_sussy_thumbhashs", sussy_thumbhashs)
        await ctx.send("已清除所有 suspicious thumbhashes。")
        # i18n: skip-end

    @commands.command(name="hack-showhashes", description="Show all suspicious thumbhashes (owner only)")
    @is_owner()
    async def hack_showhashes(self, ctx: commands.Context):
        # i18n: skip-start (owner-facing)
        if not sussy_thumbhashs:
            await ctx.send("目前沒有 suspicious thumbhashes。")
            return
        await ctx.send("目前的 suspicious thumbhashes：\n" + "\n".join(sussy_thumbhashs))
        # i18n: skip-end

    @commands.command(name="hack-removehash", description="Remove a suspicious thumbhash by index (owner only)")
    @is_owner()
    async def hack_removehash(self, ctx: commands.Context, index: int):
        # i18n: skip-start (owner-facing)
        if index < 0 or index >= len(sussy_thumbhashs):
            await ctx.send("無效的索引。")
            return
        removed = sussy_thumbhashs.pop(index)
        set_global_config("hacked_detector_sussy_thumbhashs", sussy_thumbhashs)
        await ctx.send(f"已移除 suspicious thumbhash: {removed}")
        # i18n: skip-end


asyncio.run(bot.add_cog(HackedDetector()))

if __name__ == "__main__":
    start_bot()
