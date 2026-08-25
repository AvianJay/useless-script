from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from globalenv import (
    bot,
    get_server_config,
    register_panel_settings,
    set_server_config,
)
from logger import log

import i18n
from i18n import t


CONFIG_KEY = "stickymessage"
STATE_KEY = "stickymessage_state"
LIMIT_KEY = "stickymessage_limit"
DEFAULT_LIMIT = 5
MIN_LIMIT = 1
MAX_LIMIT = 25
DEFAULT_QUIET_SECONDS = 10
DEFAULT_MIN_INTERVAL_SECONDS = 30
MIN_QUIET_SECONDS = 0
MAX_QUIET_SECONDS = 300
MIN_INTERVAL_SECONDS = 5
MAX_INTERVAL_SECONDS = 3600
MAX_CONTENT_LENGTH = 2000
MAX_HTTP_RETRIES = 4
API_REQUEST_INTERVAL_SECONDS = 0.25

DEFAULT_CONFIG = {
    "quiet_seconds": DEFAULT_QUIET_SECONDS,
    "min_interval_seconds": DEFAULT_MIN_INTERVAL_SECONDS,
    "entries": [],
}

NO_MENTIONS = discord.AllowedMentions(
    everyone=False,
    users=False,
    roles=False,
    replied_user=False,
)
ALL_MENTIONS = discord.AllowedMentions(
    everyone=True,
    users=True,
    roles=True,
    replied_user=False,
)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def get_stickymessage_limit(guild_id: int) -> int:
    return _bounded_int(
        get_server_config(guild_id, LIMIT_KEY, DEFAULT_LIMIT),
        DEFAULT_LIMIT,
        MIN_LIMIT,
        MAX_LIMIT,
    )


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def normalize_entry(raw: Any, *, strict: bool = False) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        if strict:
            raise ValueError(t("stickymessage.err.entry_not_object"))
        return None

    raw_channel_id = raw.get("channel_id")
    try:
        channel_id = int(raw_channel_id)
    except (TypeError, ValueError):
        if strict:
            raise ValueError(t("stickymessage.err.bad_channel_id"))
        return None
    if channel_id <= 0:
        if strict:
            raise ValueError(t("stickymessage.err.bad_channel_id"))
        return None

    content = str(raw.get("content") or "").strip()
    if not content:
        if strict:
            raise ValueError(t("stickymessage.err.content_empty"))
        return None
    if len(content) > MAX_CONTENT_LENGTH:
        if strict:
            raise ValueError(t("stickymessage.err.content_too_long", max=MAX_CONTENT_LENGTH))
        content = content[:MAX_CONTENT_LENGTH]

    return {
        "channel_id": channel_id,
        "content": content,
        "allow_mentions": bool(raw.get("allow_mentions", False)),
    }


def normalize_config(raw: Any, *, strict: bool = False) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        if strict:
            raise ValueError(t("stickymessage.err.config_not_object"))
        raw = {}

    quiet_seconds = _bounded_int(
        raw.get("quiet_seconds", DEFAULT_QUIET_SECONDS),
        DEFAULT_QUIET_SECONDS,
        MIN_QUIET_SECONDS,
        MAX_QUIET_SECONDS,
    )
    min_interval_seconds = _bounded_int(
        raw.get("min_interval_seconds", DEFAULT_MIN_INTERVAL_SECONDS),
        DEFAULT_MIN_INTERVAL_SECONDS,
        MIN_INTERVAL_SECONDS,
        MAX_INTERVAL_SECONDS,
    )
    if strict:
        for key, value, minimum, maximum in (
            ("quiet_seconds", raw.get("quiet_seconds", DEFAULT_QUIET_SECONDS), MIN_QUIET_SECONDS, MAX_QUIET_SECONDS),
            (
                "min_interval_seconds",
                raw.get("min_interval_seconds", DEFAULT_MIN_INTERVAL_SECONDS),
                MIN_INTERVAL_SECONDS,
                MAX_INTERVAL_SECONDS,
            ),
        ):
            try:
                parsed = int(value)
            except (TypeError, ValueError) as error:
                raise ValueError(t("stickymessage.err.field_not_int", field=key)) from error
            if parsed < minimum or parsed > maximum:
                raise ValueError(t("stickymessage.err.field_out_of_range",
                                   field=key, min=minimum, max=maximum))

    raw_entries = raw.get("entries", [])
    if not isinstance(raw_entries, list):
        if strict:
            raise ValueError(t("stickymessage.err.entries_not_list"))
        raw_entries = []

    entries: list[dict[str, Any]] = []
    seen_channels: set[int] = set()
    for raw_entry in raw_entries:
        entry = normalize_entry(raw_entry, strict=strict)
        if entry is None:
            continue
        channel_id = entry["channel_id"]
        if channel_id in seen_channels:
            if strict:
                raise ValueError(t("stickymessage.err.duplicate_channel"))
            continue
        seen_channels.add(channel_id)
        entries.append(entry)

    if len(entries) > MAX_LIMIT:
        if strict:
            raise ValueError(t("stickymessage.err.too_many_entries", count=MAX_LIMIT))
        entries = entries[:MAX_LIMIT]

    return {
        "quiet_seconds": quiet_seconds,
        "min_interval_seconds": min_interval_seconds,
        "entries": entries,
    }


def normalize_state(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for raw_channel_id, raw_state in raw.items():
        if not str(raw_channel_id).isdigit() or not isinstance(raw_state, dict):
            continue
        state: dict[str, Any] = {}
        message_id = raw_state.get("message_id")
        if message_id is not None and str(message_id).isdigit():
            state["message_id"] = int(message_id)
        try:
            state["last_sent_at"] = float(raw_state.get("last_sent_at", 0) or 0)
        except (TypeError, ValueError):
            state["last_sent_at"] = 0.0
        digest = str(raw_state.get("content_digest") or "")
        if digest:
            state["content_digest"] = digest
        normalized[str(int(raw_channel_id))] = state
    return normalized


def active_entries(config: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    return list(config.get("entries", []))[:limit]


def find_entry(config: dict[str, Any], channel_id: int) -> tuple[int, dict[str, Any] | None]:
    for index, entry in enumerate(config.get("entries", [])):
        if entry["channel_id"] == channel_id:
            return index, entry
    return -1, None


async def apply_stickymessage_config(
    guild_id: int,
    value: Any,
    previous_value: Any = None,
) -> None:
    cog = bot.get_cog("StickyMessage")
    if cog is not None:
        await cog.apply_config(
            guild_id,
            normalize_config(value),
            normalize_config(previous_value),
        )


register_panel_settings(
    "StickyMessage",
    t("panel.stickymessage._display", locale=i18n.SOURCE_LOCALE),
    [
        {
            "display": t("panel.stickymessage.stickymessage.display", locale=i18n.SOURCE_LOCALE),
            "description": t("panel.stickymessage.stickymessage.desc", locale=i18n.SOURCE_LOCALE),
            "database_key": CONFIG_KEY,
            "type": "stickymessage_config",
            "default": DEFAULT_CONFIG,
            "trigger": apply_stickymessage_config,
            "trigger_with_previous": True,
        },
    ],
    description=t("panel.stickymessage._desc", locale=i18n.SOURCE_LOCALE),
    icon="📌",
)


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class StickyMessage(commands.GroupCog,
                    group_name=app_commands.locale_str("stickymessage", i18n_key="cmd.stickymessage.stickymessage.root.name"),
                    group_description=app_commands.locale_str("Manage this server's sticky messages", i18n_key="cmd.stickymessage.stickymessage.root.desc")):
    """管理伺服器的置底訊息。"""

    def __init__(self, client: commands.Bot):
        self.bot = client
        self._channel_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self._wake_events: dict[tuple[int, int], asyncio.Event] = {}
        self._last_activity: dict[tuple[int, int], float] = {}
        self._api_lock = asyncio.Lock()
        self._next_api_request_at = 0.0

    def cog_unload(self) -> None:
        for task in list(self._channel_tasks.values()):
            task.cancel()
        self._channel_tasks.clear()
        self._wake_events.clear()

    def get_config(self, guild_id: int) -> dict[str, Any]:
        return normalize_config(get_server_config(guild_id, CONFIG_KEY, DEFAULT_CONFIG))

    def get_state(self, guild_id: int) -> dict[str, dict[str, Any]]:
        return normalize_state(get_server_config(guild_id, STATE_KEY, {}))

    def save_config(self, guild_id: int, config: dict[str, Any]) -> bool:
        return set_server_config(guild_id, CONFIG_KEY, normalize_config(config, strict=True))

    def save_state(self, guild_id: int, state: dict[str, dict[str, Any]]) -> bool:
        return set_server_config(guild_id, STATE_KEY, normalize_state(state))

    def _active_channel_ids(self, guild_id: int, config: dict[str, Any] | None = None) -> set[int]:
        config = config or self.get_config(guild_id)
        return {
            entry["channel_id"]
            for entry in active_entries(config, get_stickymessage_limit(guild_id))
        }

    def _cancel_channel_task(self, guild_id: int, channel_id: int) -> asyncio.Task | None:
        key = (guild_id, channel_id)
        task = self._channel_tasks.pop(key, None)
        self._wake_events.pop(key, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        return task

    def schedule_refresh(self, guild_id: int, channel_id: int, *, activity_at: float | None = None) -> None:
        if channel_id not in self._active_channel_ids(guild_id):
            self._cancel_channel_task(guild_id, channel_id)
            return
        key = (guild_id, channel_id)
        self._last_activity[key] = activity_at or time.time()
        existing = self._channel_tasks.get(key)
        if existing is not None and not existing.done():
            self._wake_events.setdefault(key, asyncio.Event()).set()
            return
        self._wake_events[key] = asyncio.Event()
        self._channel_tasks[key] = asyncio.create_task(
            self._wait_and_refresh(guild_id, channel_id),
            name=f"stickymessage:{guild_id}:{channel_id}",
        )

    async def _wait_and_refresh(self, guild_id: int, channel_id: int) -> None:
        key = (guild_id, channel_id)
        try:
            while True:
                config = self.get_config(guild_id)
                if channel_id not in self._active_channel_ids(guild_id, config):
                    return
                state = self.get_state(guild_id).get(str(channel_id), {})
                quiet_due = self._last_activity.get(key, time.time()) + config["quiet_seconds"]
                interval_due = float(state.get("last_sent_at", 0) or 0) + config["min_interval_seconds"]
                delay = max(quiet_due, interval_due) - time.time()
                if delay > 0:
                    wake_event = self._wake_events.setdefault(key, asyncio.Event())
                    wake_event.clear()
                    try:
                        await asyncio.wait_for(wake_event.wait(), timeout=delay)
                        continue
                    except asyncio.TimeoutError:
                        pass
                publish_started = time.time()
                publish_task = asyncio.create_task(
                    self.publish_entry(guild_id, channel_id, notify_mentions=False)
                )
                try:
                    await asyncio.shield(publish_task)
                except asyncio.CancelledError:
                    try:
                        await publish_task
                    except Exception:
                        pass
                    raise
                if self._last_activity.get(key, 0) > publish_started:
                    continue
                return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log(
                f"Sticky refresh schedule failed ({channel_id}): {error}",
                level=logging.ERROR,
                module_name="StickyMessage",
            )
        finally:
            if self._channel_tasks.get(key) is asyncio.current_task():
                self._channel_tasks.pop(key, None)
                self._wake_events.pop(key, None)

    @staticmethod
    def _retry_after(error: discord.HTTPException, attempt: int) -> float:
        retry_after = getattr(error, "retry_after", None)
        if retry_after is None:
            response = getattr(error, "response", None)
            headers = getattr(response, "headers", {}) if response is not None else {}
            retry_after = headers.get("Retry-After") if headers else None
        try:
            return max(0.25, float(retry_after))
        except (TypeError, ValueError):
            return min(30.0, (2 ** attempt) + random.random())

    async def _wait_for_api_slot(self) -> None:
        delay = self._next_api_request_at - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        self._next_api_request_at = time.monotonic() + API_REQUEST_INTERVAL_SECONDS

    def _defer_api_after_http_error(self, error: discord.HTTPException, attempt: int) -> float:
        delay = self._retry_after(error, attempt)
        self._next_api_request_at = max(
            self._next_api_request_at,
            time.monotonic() + delay,
        )
        return delay

    async def _wait_after_http_error(self, error: discord.HTTPException, attempt: int) -> None:
        delay = self._defer_api_after_http_error(error, attempt)
        await asyncio.sleep(delay)

    async def _delete_previous(self, channel: discord.abc.Messageable, message_id: int | None) -> None:
        if not message_id:
            return
        for attempt in range(MAX_HTTP_RETRIES):
            try:
                await self._wait_for_api_slot()
                await channel.get_partial_message(message_id).delete()
                return
            except discord.NotFound:
                return
            except discord.Forbidden:
                raise
            except discord.HTTPException as error:
                if error.status != 429 and error.status < 500:
                    raise
                if attempt >= MAX_HTTP_RETRIES - 1:
                    self._defer_api_after_http_error(error, attempt)
                    raise
                await self._wait_after_http_error(error, attempt)

    async def _send_with_retry(
        self,
        channel: discord.TextChannel,
        content: str,
        allowed_mentions: discord.AllowedMentions,
    ) -> discord.Message:
        last_error: discord.HTTPException | None = None
        for attempt in range(MAX_HTTP_RETRIES):
            try:
                await self._wait_for_api_slot()
                return await channel.send(content, allowed_mentions=allowed_mentions)
            except (discord.Forbidden, discord.NotFound):
                raise
            except discord.HTTPException as error:
                last_error = error
                if error.status != 429 and error.status < 500:
                    raise
                if attempt >= MAX_HTTP_RETRIES - 1:
                    self._defer_api_after_http_error(error, attempt)
                    raise
                await self._wait_after_http_error(error, attempt)
        raise last_error or RuntimeError(t("stickymessage.err.send_failed"))

    async def publish_entry(
        self,
        guild_id: int,
        channel_id: int,
        *,
        notify_mentions: bool,
    ) -> discord.Message:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise ValueError(t("stickymessage.err.guild_not_found"))
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.type not in (
            discord.ChannelType.text,
            discord.ChannelType.news,
        ):
            raise ValueError(t("stickymessage.err.channel_not_found"))

        config = self.get_config(guild_id)
        index, entry = find_entry(config, channel_id)
        if entry is None:
            raise ValueError(t("stickymessage.err.not_configured"))
        if index >= get_stickymessage_limit(guild_id):
            raise ValueError(t("stickymessage.err.over_quota"))

        bot_member = guild.me
        permissions = channel.permissions_for(bot_member) if bot_member else None
        if permissions is None or not permissions.view_channel or not permissions.send_messages:
            raise ValueError(t("stickymessage.err.missing_perms"))

        async with self._api_lock:
            state = self.get_state(guild_id)
            channel_state = state.get(str(channel_id), {})
            await self._delete_previous(channel, channel_state.get("message_id"))
            channel_state.pop("message_id", None)
            state[str(channel_id)] = channel_state
            self.save_state(guild_id, state)

            allowed_mentions = (
                ALL_MENTIONS
                if notify_mentions and entry["allow_mentions"]
                else NO_MENTIONS
            )
            sent = await self._send_with_retry(channel, entry["content"], allowed_mentions)
            state = self.get_state(guild_id)
            state[str(channel_id)] = {
                "message_id": sent.id,
                "last_sent_at": time.time(),
                "content_digest": content_digest(entry["content"]),
            }
            self.save_state(guild_id, state)
            return sent

    async def remove_published_message(self, guild_id: int, channel_id: int) -> None:
        task = self._cancel_channel_task(guild_id, channel_id)
        if task is not None and task is not asyncio.current_task():
            try:
                await task
            except asyncio.CancelledError:
                pass
        guild = self.bot.get_guild(guild_id)
        state = self.get_state(guild_id)
        channel_state = state.pop(str(channel_id), None)
        self.save_state(guild_id, state)
        if guild is None or not channel_state:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            async with self._api_lock:
                await self._delete_previous(channel, channel_state.get("message_id"))
        except (discord.Forbidden, discord.HTTPException) as error:
            log(
                f"Failed to remove the previous sticky message ({channel_id}): {error}",
                level=logging.WARNING,
                module_name="StickyMessage",
            )

    async def apply_config(
        self,
        guild_id: int,
        config: dict[str, Any],
        previous_config: dict[str, Any],
    ) -> None:
        config = normalize_config(config)
        previous_config = normalize_config(previous_config)
        old_by_channel = {entry["channel_id"]: entry for entry in previous_config["entries"]}
        new_by_channel = {entry["channel_id"]: entry for entry in config["entries"]}
        old_active_ids = {
            entry["channel_id"]
            for entry in active_entries(previous_config, get_stickymessage_limit(guild_id))
        }

        active_ids = self._active_channel_ids(guild_id, config)
        for channel_id in (
            (old_by_channel.keys() - new_by_channel.keys())
            | (old_active_ids - active_ids)
        ):
            await self.remove_published_message(guild_id, channel_id)

        for channel_id in old_by_channel.keys() | new_by_channel.keys():
            if channel_id not in active_ids:
                self._cancel_channel_task(guild_id, channel_id)

        timing_changed = (
            config["quiet_seconds"] != previous_config["quiet_seconds"]
            or config["min_interval_seconds"] != previous_config["min_interval_seconds"]
        )
        if timing_changed:
            for guild_channel, activity_at in list(self._last_activity.items()):
                if guild_channel[0] == guild_id and guild_channel[1] in active_ids:
                    self.schedule_refresh(guild_id, guild_channel[1], activity_at=activity_at)

        publish_errors = []
        for channel_id, entry in new_by_channel.items():
            old_entry = old_by_channel.get(channel_id)
            if channel_id not in active_ids:
                continue
            if (
                old_entry is None
                or old_entry["content"] != entry["content"]
                or channel_id not in old_active_ids
            ):
                try:
                    await self.publish_entry(guild_id, channel_id, notify_mentions=True)
                except (ValueError, discord.Forbidden, discord.HTTPException) as error:
                    publish_errors.append(f"<#{channel_id}>: {error}")
                    log(
                        f"Config saved but the immediate publish failed ({channel_id}): {error}",
                        level=logging.WARNING,
                        module_name="StickyMessage",
                    )
        if publish_errors:
            raise RuntimeError(
                t("stickymessage.err.saved_but_publish_failed",
                  errors=i18n.join_list(publish_errors[:3]))
            )

    async def reconcile_limit(self, guild_id: int, previous_limit: int) -> None:
        config = self.get_config(guild_id)
        entries = config["entries"]
        old_active_ids = {entry["channel_id"] for entry in entries[:previous_limit]}
        new_active_ids = {
            entry["channel_id"]
            for entry in entries[:get_stickymessage_limit(guild_id)]
        }
        for channel_id in old_active_ids - new_active_ids:
            await self.remove_published_message(guild_id, channel_id)
        for channel_id in new_active_ids - old_active_ids:
            try:
                await self.publish_entry(guild_id, channel_id, notify_mentions=False)
            except (ValueError, discord.Forbidden, discord.HTTPException) as error:
                log(
                    f"Failed to restore the sticky message after a quota change ({channel_id}): {error}",
                    level=logging.WARNING,
                    module_name="StickyMessage",
                )

    async def _save_and_apply(self, guild_id: int, config: dict[str, Any]) -> None:
        previous = self.get_config(guild_id)
        normalized = normalize_config(config, strict=True)
        if not set_server_config(guild_id, CONFIG_KEY, normalized):
            raise RuntimeError(t("stickymessage.err.save_failed"))
        await self.apply_config(guild_id, normalized, previous)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if (
            message.guild is None
            or message.author.bot
            or message.webhook_id is not None
        ):
            return
        if message.channel.id not in self._active_channel_ids(message.guild.id):
            return
        self.schedule_refresh(message.guild.id, message.channel.id)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id is None:
            return
        state = self.get_state(payload.guild_id)
        channel_state = state.get(str(payload.channel_id))
        if not channel_state or channel_state.get("message_id") != payload.message_id:
            return
        channel_state.pop("message_id", None)
        state[str(payload.channel_id)] = channel_state
        self.save_state(payload.guild_id, state)

    async def _respond_error(self, interaction: discord.Interaction, error: Exception) -> None:
        message = str(error) or t("stickymessage.err.generic")
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("add", i18n_key="cmd.stickymessage.stickymessage.add.name"), description=app_commands.locale_str("Add a sticky message to a channel", i18n_key="cmd.stickymessage.stickymessage.add.desc"))
    @app_commands.describe(channel=app_commands.locale_str("The text or announcement channel to configure", i18n_key="cmd.stickymessage.stickymessage.add.param.channel"), content=app_commands.locale_str("Sticky message content", i18n_key="cmd.stickymessage.stickymessage.add.param.content"), allow_mentions=app_commands.locale_str("Allow mentions on first and manual posts", i18n_key="cmd.stickymessage.stickymessage.add.param.allow_mentions"))
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        content: app_commands.Range[str, 1, MAX_CONTENT_LENGTH],
        allow_mentions: bool = False,
    ) -> None:
        try:
            if channel.type not in (discord.ChannelType.text, discord.ChannelType.news):
                raise ValueError(t("stickymessage.err.text_or_news_only"))
            config = self.get_config(interaction.guild_id)
            _, existing = find_entry(config, channel.id)
            if existing is not None:
                raise ValueError(t("stickymessage.err.already_configured"))
            limit = get_stickymessage_limit(interaction.guild_id)
            if len(config["entries"]) >= limit:
                raise ValueError(t("stickymessage.err.quota_reached", count=limit))
            config["entries"].append({
                "channel_id": channel.id,
                "content": content,
                "allow_mentions": allow_mentions,
            })
            await interaction.response.defer(ephemeral=True)
            await self._save_and_apply(interaction.guild_id, config)
            await interaction.followup.send(t("stickymessage.msg.added", channel=channel.mention), ephemeral=True)
        except Exception as error:
            await self._respond_error(interaction, error)

    @app_commands.command(name=app_commands.locale_str("edit", i18n_key="cmd.stickymessage.stickymessage.edit.name"), description=app_commands.locale_str("Edit a channel's sticky message", i18n_key="cmd.stickymessage.stickymessage.edit.desc"))
    @app_commands.describe(channel=app_commands.locale_str("The configured text or announcement channel", i18n_key="cmd.stickymessage.stickymessage.edit.param.channel"), content=app_commands.locale_str("New sticky message content", i18n_key="cmd.stickymessage.stickymessage.edit.param.content"), allow_mentions=app_commands.locale_str("Allow mentions on first and manual posts", i18n_key="cmd.stickymessage.stickymessage.edit.param.allow_mentions"))
    @app_commands.checks.has_permissions(manage_guild=True)
    async def edit(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        content: app_commands.Range[str, 1, MAX_CONTENT_LENGTH],
        allow_mentions: bool = False,
    ) -> None:
        try:
            config = self.get_config(interaction.guild_id)
            index, entry = find_entry(config, channel.id)
            if entry is None:
                raise ValueError(t("stickymessage.err.not_configured"))
            config["entries"][index] = {
                "channel_id": channel.id,
                "content": content,
                "allow_mentions": allow_mentions,
            }
            await interaction.response.defer(ephemeral=True)
            await self._save_and_apply(interaction.guild_id, config)
            await interaction.followup.send(t("stickymessage.msg.updated", channel=channel.mention), ephemeral=True)
        except Exception as error:
            await self._respond_error(interaction, error)

    @app_commands.command(name=app_commands.locale_str("remove", i18n_key="cmd.stickymessage.stickymessage.remove.name"), description=app_commands.locale_str("Remove a channel's sticky message", i18n_key="cmd.stickymessage.stickymessage.remove.desc"))
    @app_commands.describe(channel=app_commands.locale_str("The channel whose sticky message to remove", i18n_key="cmd.stickymessage.stickymessage.remove.param.channel"))
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        try:
            config = self.get_config(interaction.guild_id)
            index, entry = find_entry(config, channel.id)
            if entry is None:
                raise ValueError(t("stickymessage.err.not_configured"))
            config["entries"].pop(index)
            await interaction.response.defer(ephemeral=True)
            await self._save_and_apply(interaction.guild_id, config)
            await interaction.followup.send(t("stickymessage.msg.removed", channel=channel.mention), ephemeral=True)
        except Exception as error:
            await self._respond_error(interaction, error)

    @app_commands.command(name=app_commands.locale_str("list", i18n_key="cmd.stickymessage.stickymessage.list.name"), description=app_commands.locale_str("List current sticky message settings", i18n_key="cmd.stickymessage.stickymessage.list.desc"))
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list_entries(self, interaction: discord.Interaction) -> None:
        config = self.get_config(interaction.guild_id)
        limit = get_stickymessage_limit(interaction.guild_id)
        lines = []
        for index, entry in enumerate(config["entries"]):
            content = entry["content"].replace("\n", " ")
            if len(content) > 80:
                content = content[:77] + "..."
            status = t("stickymessage.state.active" if index < limit else "stickymessage.state.over_quota")
            mentions = t("stickymessage.state.mentions_on" if entry["allow_mentions"]
                         else "stickymessage.state.mentions_off")
            lines.append(
                f"{index + 1}. <#{entry['channel_id']}> · {status} · {mentions}\n{content}"
            )
        embed = discord.Embed(
            title=t("stickymessage.list.title"),
            description="\n\n".join(lines) if lines else t("stickymessage.list.empty"),
            color=discord.Color.blurple(),
        )
        embed.add_field(name=t("stickymessage.field.quota"), value=f"{len(config['entries'])} / {limit}", inline=True)
        embed.add_field(name=t("stickymessage.field.quiet_seconds"),
                        value=i18n.tn("common.unit.seconds", config["quiet_seconds"]), inline=True)
        embed.add_field(name=t("stickymessage.field.min_interval"),
                        value=i18n.tn("common.unit.seconds", config["min_interval_seconds"]), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_MENTIONS)

    @app_commands.command(name=app_commands.locale_str("publish", i18n_key="cmd.stickymessage.stickymessage.publish.name"), description=app_commands.locale_str("Repost a sticky message immediately", i18n_key="cmd.stickymessage.stickymessage.publish.desc"))
    @app_commands.describe(channel=app_commands.locale_str("The channel whose sticky message to repost now", i18n_key="cmd.stickymessage.stickymessage.publish.param.channel"))
    @app_commands.checks.has_permissions(manage_guild=True)
    async def publish(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        try:
            await interaction.response.defer(ephemeral=True)
            await self.publish_entry(interaction.guild_id, channel.id, notify_mentions=True)
            await interaction.followup.send(t("stickymessage.msg.republished", channel=channel.mention), ephemeral=True)
        except Exception as error:
            await self._respond_error(interaction, error)

    @app_commands.command(name=app_commands.locale_str("move", i18n_key="cmd.stickymessage.stickymessage.move.name"), description=app_commands.locale_str("Reorder sticky messages; order also sets quota priority", i18n_key="cmd.stickymessage.stickymessage.move.desc"))
    @app_commands.describe(channel=app_commands.locale_str("The sticky message channel to move", i18n_key="cmd.stickymessage.stickymessage.move.param.channel"), position=app_commands.locale_str("New position", i18n_key="cmd.stickymessage.stickymessage.move.param.position"))
    @app_commands.checks.has_permissions(manage_guild=True)
    async def move(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        position: app_commands.Range[int, 1, MAX_LIMIT],
    ) -> None:
        try:
            config = self.get_config(interaction.guild_id)
            index, entry = find_entry(config, channel.id)
            if entry is None:
                raise ValueError(t("stickymessage.err.not_configured"))
            if position > len(config["entries"]):
                raise ValueError(t("stickymessage.err.position_out_of_range", count=len(config["entries"])))
            config["entries"].pop(index)
            config["entries"].insert(position - 1, entry)
            await interaction.response.defer(ephemeral=True)
            await self._save_and_apply(interaction.guild_id, config)
            await interaction.followup.send(t("stickymessage.msg.moved", channel=channel.mention, position=position), ephemeral=True)
        except Exception as error:
            await self._respond_error(interaction, error)

    @app_commands.command(name=app_commands.locale_str("timing", i18n_key="cmd.stickymessage.stickymessage.timing.name"), description=app_commands.locale_str("Configure quiet time and minimum repost interval", i18n_key="cmd.stickymessage.stickymessage.timing.desc"))
    @app_commands.describe(quiet_seconds=app_commands.locale_str("Seconds to wait after the last human message", i18n_key="cmd.stickymessage.stickymessage.timing.param.quiet_seconds"), min_interval_seconds=app_commands.locale_str("Minimum interval between two automatic reposts in a channel", i18n_key="cmd.stickymessage.stickymessage.timing.param.min_interval_seconds"))
    @app_commands.checks.has_permissions(manage_guild=True)
    async def timing(
        self,
        interaction: discord.Interaction,
        quiet_seconds: app_commands.Range[int, MIN_QUIET_SECONDS, MAX_QUIET_SECONDS],
        min_interval_seconds: app_commands.Range[int, MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS],
    ) -> None:
        try:
            config = self.get_config(interaction.guild_id)
            config["quiet_seconds"] = quiet_seconds
            config["min_interval_seconds"] = min_interval_seconds
            await interaction.response.defer(ephemeral=True)
            await self._save_and_apply(interaction.guild_id, config)
            await interaction.followup.send(
                t("stickymessage.msg.timing_set", quiet=quiet_seconds, interval=min_interval_seconds),
                ephemeral=True,
            )
        except Exception as error:
            await self._respond_error(interaction, error)


asyncio.run(bot.add_cog(StickyMessage(bot)))
