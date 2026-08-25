import discord
from discord.ext import commands, tasks
from discord import app_commands
from globalenv import bot, start_bot, get_user_data, set_user_data, get_all_user_data, get_server_config, set_server_config, modules, config, get_command_mention
from datetime import datetime, timezone, timedelta
import asyncio
from typing import Optional
from difflib import SequenceMatcher
import re
import emoji
import sqlite3
import aiohttp
import io
import json
from logger import log
import logging
import sys
from contextlib import closing

import i18n
from i18n import t, t_enum


BLACKLIST_API_URL = "https://kurokusa.nkhost.dev/api/v1/blacklist?status=1"
BLACKLIST_SYNC_INTERVAL_MINUTES = 30
LOCAL_FLAG_LOOKBACK_MONTHS = 3
FLAGGED_USER_ACTION_SOURCES = {"local", "api", "both"}
FLAGGED_USER_LOCAL_MATCH_MODES = {"active", "history"}

if "Moderate" in modules:
    import Moderate
else:
    log("Moderate module not found", level=logging.ERROR, module_name="AutoModerate")


def _action_input_suggestions() -> list[tuple[str, str]]:
    """在地化的建議清單；沒有 Moderate 模組時退回精簡的內建清單（同樣在地化）。"""
    if "Moderate" in modules:
        return Moderate._action_input_suggestions()
    reason = t("moderate.suggest.sample_reason")
    return [
        (t("moderate.suggest.delete"), "delete"),
        (t("moderate.suggest.warn"), t("moderate.suggest.value.warn", reason=reason)),
        (t("moderate.suggest.mute_10m"), t("moderate.suggest.value.mute_10m", reason=reason)),
        (t("moderate.suggest.mute_1h"), t("moderate.suggest.value.mute_1h", reason=reason)),
        (t("moderate.suggest.kick"), t("moderate.suggest.value.kick", reason=reason)),
        (t("moderate.suggest.ban_perm"), t("moderate.suggest.value.ban_perm", reason=reason)),
    ]


def _flagged_user_action_input_suggestions() -> list[tuple[str, str]]:
    if "Moderate" not in modules:
        return []
    return [
        (label, value)
        for label, value in _action_input_suggestions()
        if Moderate.analyze_member_join_action(value)["valid"]
    ]


all_settings = [
    "escape_punish-punishment",
    "escape_punish-duration",
    "too_many_h1-max_length",
    "too_many_h1-action",
    "too_many_h1-ignore_channels",
    "too_many_emojis-max_emojis",
    "too_many_emojis-action",
    "too_many_emojis-ignore_channels",
    "scamtrap-channel_id",
    "scamtrap-action",
    "anti_invite_link-allow_current_server",
    "anti_invite_link-action",
    "anti_invite_link-ignore_channels",
    "anti_uispam-max_count",
    "anti_uispam-time_window",
    "anti_uispam-action",
    "anti_uispam-ignore_channels",
    "anti_raid-max_joins",
    "anti_raid-time_window",
    "anti_raid-action",
    "anti_spam-max_messages",
    "anti_spam-time_window",
    "anti_spam-similarity",
    "anti_spam-action",
    "anti_spam-ignore_channels",
    "automod_detect-log_channel",
    "automod_detect-action",
    "automod_detect-filter_rule",
    "automod_detect-filter_action_type",
    "flagged_user-log_channel",
    "flagged_user-action",
    "flagged_user-action_source",
    "flagged_user-local_match_mode",
]

# 用於追蹤 user install spam 的記憶體字典
# 結構: {guild_id: {user_id: [timestamp1, timestamp2, ...]}}
_uispam_tracker: dict[int, dict[int, list[datetime]]] = {}

# 用於追蹤 raid（大量用戶加入）的記憶體字典
# 結構: {guild_id: [(member, join_time), ...]}
_raid_tracker: dict[int, list[tuple[discord.Member, datetime]]] = {}

# 用於追蹤用戶刷頻的記憶體字典
# 結構: {guild_id: {user_id: [(content, timestamp), ...]}}
_spam_tracker: dict[int, dict[int, list[tuple[str, datetime]]]] = {}

INVITE_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/([A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_INVITE_GUILD_CACHE_TTL = timedelta(minutes=10)
_invite_guild_cache: dict[str, tuple[int | None, datetime]] = {}
AUTOMOD_IGNORE_CHANNEL_FEATURES = {
    "anti_invite_link",
    "too_many_h1",
    "too_many_emojis",
    "anti_uispam",
    "anti_spam",
}


def _subtract_calendar_months(value: datetime, months: int) -> datetime:
    target_month = value.month - months
    year = value.year + (target_month - 1) // 12
    month = (target_month - 1) % 12 + 1
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=value.tzinfo)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=value.tzinfo)
    last_day = (next_month - timedelta(days=1)).day
    return value.replace(year=year, month=month, day=min(value.day, last_day))


def _normalize_blacklist_payload(payload: object) -> dict[int, list[dict]]:
    if not isinstance(payload, dict):
        raise ValueError("API response is not a JSON object")
    if payload.get("code") != 200:
        raise ValueError("API response code is not 200")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("API response is missing data.items")

    normalized: dict[int, list[dict]] = {}
    for item in data["items"]:
        if not isinstance(item, dict):
            raise ValueError("Malformed blacklist item in API response")
        raw_user_id = str(item.get("userid", "")).strip()
        if not raw_user_id.isdigit() or int(raw_user_id) <= 0:
            raise ValueError("API blacklist contains an invalid userid")
        try:
            status = int(item.get("status", 1))
        except (TypeError, ValueError) as error:
            raise ValueError("API blacklist contains an invalid status") from error
        if status != 1:
            raise ValueError("status=1 query returned a non-active record")
        reason = item.get("reason")
        reported_at = item.get("reported_at")
        reporter = item.get("reporter")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("API blacklist contains a blank reason")
        if not isinstance(reported_at, str) or not reported_at.strip():
            raise ValueError("API blacklist contains an invalid reported_at")
        try:
            datetime.fromisoformat(reported_at.strip().replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("API blacklist contains an invalid reported_at") from error
        if not isinstance(reporter, dict):
            raise ValueError("API blacklist contains an invalid reporter")
        reporter_id = str(reporter.get("id", "")).strip()
        reporter_name = reporter.get("name")
        if not reporter_id.isdigit() or not isinstance(reporter_name, str) or not reporter_name.strip():
            raise ValueError("API blacklist contains invalid reporter data")

        user_id = int(raw_user_id)
        normalized.setdefault(user_id, []).append({
            "record_id": item.get("id"),
            "reason": reason.strip(),
            "reported_at": reported_at.strip(),
            "reporter_id": reporter_id,
            "reporter_name": reporter_name.strip(),
        })
    return normalized


def _normalize_flagged_user_config(guild_id: int) -> dict:
    automod = get_server_config(guild_id, "automod", {})
    if isinstance(automod, dict) and "flagged_user" in automod:
        raw = automod.get("flagged_user")
        raw = raw if isinstance(raw, dict) else {}
        action_source = str(raw.get("action_source", "both")).strip().lower()
        local_match_mode = str(raw.get("local_match_mode", "active")).strip().lower()
        return {
            "enabled": _is_truthy(raw.get("enabled", False)),
            "log_channel": raw.get("log_channel"),
            "action": str(raw.get("action", "") or "").strip(),
            "action_source": action_source if action_source in FLAGGED_USER_ACTION_SOURCES else "both",
            "local_match_mode": local_match_mode if local_match_mode in FLAGGED_USER_LOCAL_MATCH_MODES else "active",
            "legacy": False,
        }

    legacy_channel = get_server_config(guild_id, "flagged_user_onjoin_channel")
    return {
        "enabled": bool(legacy_channel),
        "log_channel": legacy_channel,
        "action": "",
        "action_source": "both",
        "local_match_mode": "active",
        "legacy": bool(legacy_channel),
    }


def _ensure_flagged_user_settings(automod: dict, guild_id: int) -> dict:
    if "flagged_user" in automod:
        current = automod.get("flagged_user")
        if isinstance(current, dict):
            return current
        legacy_channel = None
    else:
        legacy_channel = get_server_config(guild_id, "flagged_user_onjoin_channel")
    current = {
        "enabled": bool(legacy_channel),
        "log_channel": str(legacy_channel) if legacy_channel else "",
        "action": "",
        "action_source": "both",
        "local_match_mode": "active",
    }
    automod["flagged_user"] = current
    return current


def _load_local_flagged_records(
    user_ids: set[int] | None = None,
    *,
    match_mode: str = "active",
    now: datetime | None = None,
) -> dict[int, list[dict]]:
    if match_mode not in FLAGGED_USER_LOCAL_MATCH_MODES:
        match_mode = "active"
    cutoff = _subtract_calendar_months(now or datetime.now(timezone.utc), LOCAL_FLAG_LOOKBACK_MONTHS)
    database_file = config("flagged_database_path", "flagged_data.db")
    query = (
        "SELECT f.user_id, f.guild_id, f.flagged_at, f.flagged_role, g.name "
        "FROM flagged_users AS f LEFT JOIN guilds AS g ON g.id = f.guild_id "
        "WHERE datetime(f.flagged_at) >= datetime(?)"
    )
    params: list[object] = [cutoff.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")]
    if match_mode == "active":
        query += " AND f.flagged_role = 1"

    records: dict[int, list[dict]] = {}
    with closing(sqlite3.connect(database_file)) as conn:
        conn.row_factory = sqlite3.Row
        with closing(conn.execute(query, params)) as cursor:
            for row in cursor:
                user_id = int(row["user_id"])
                if user_ids is not None and user_id not in user_ids:
                    continue
                records.setdefault(user_id, []).append({
                    "guild_id": row["guild_id"],
                    "guild_name": row["name"] or t("automoderate.unknown_guild"),
                    "flagged_at": str(row["flagged_at"]),
                    "flagged_role": bool(row["flagged_role"]),
                })
    return records


def _action_matches_sources(action_source: str, *, has_local: bool, has_api: bool) -> bool:
    return (
        (action_source == "local" and has_local)
        or (action_source == "api" and has_api)
        or (action_source == "both" and (has_local or has_api))
    )


def _format_embed_record_lines(lines: list[str], *, limit: int = 1024) -> str:
    output: list[str] = []
    used = 0
    omitted = 0
    for index, raw_line in enumerate(lines):
        line = str(raw_line).replace("\x00", "").strip()
        remaining = limit - used - (1 if output else 0)
        if remaining <= 0:
            omitted = len(lines) - index
            break
        if len(line) > remaining:
            line = line[: max(0, remaining - 1)].rstrip() + "…"
            omitted = len(lines) - index - 1
        output.append(line)
        used += len(line) + (1 if len(output) > 1 else 0)
        if index < len(lines) - 1 and used >= limit:
            omitted = len(lines) - index - 1
            break
    if omitted > 0:
        suffix = "\n" + t("automoderate.records_omitted", count=omitted)
        joined = "\n".join(output)
        return joined[: max(0, limit - len(suffix))].rstrip() + suffix
    return "\n".join(output) or t("moderate.fallback_none")


def _is_truthy(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def _normalize_channel_id_list(value) -> list[int]:
    if value is None:
        return []

    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    elif isinstance(value, int):
        raw_items = [value]
    elif isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"none", "null", "[]"}:
            return []
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            raw_items = parsed
        else:
            raw_items = re.findall(r"\d+", text)
    else:
        return []

    channel_ids = []
    seen = set()
    for item in raw_items:
        normalized = parse_mention_to_id(str(item).strip())
        if not normalized.isdigit():
            continue
        channel_id = int(normalized)
        if channel_id in seen:
            continue
        seen.add(channel_id)
        channel_ids.append(channel_id)
    return channel_ids


def _is_ignored_channel(feature_config: dict, channel_id: int) -> bool:
    return channel_id in _normalize_channel_id_list(feature_config.get("ignore_channels"))


def _extract_invite_codes(content: str) -> list[str]:
    seen = set()
    codes = []
    for code in INVITE_LINK_RE.findall(content or ""):
        normalized = code.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        codes.append(normalized)
    return codes


async def _resolve_invite_guild_id(invite_code: str) -> int | None:
    now = datetime.now(timezone.utc)
    cached = _invite_guild_cache.get(invite_code)
    if cached and (now - cached[1]) < _INVITE_GUILD_CACHE_TTL:
        return cached[0]

    guild_id = None
    try:
        invite = await bot.fetch_invite(invite_code)
        invite_guild = getattr(invite, "guild", None)
        if invite_guild:
            guild_id = invite_guild.id
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        guild_id = None

    _invite_guild_cache[invite_code] = (guild_id, now)
    return guild_id


async def _get_external_invite_codes(message: discord.Message, guild_id: int, allow_current_server: bool) -> list[str]:
    invite_codes = _extract_invite_codes(message.content)
    if not invite_codes:
        return []
    if not allow_current_server:
        return invite_codes

    external_codes = []
    for invite_code in invite_codes:
        invite_guild_id = await _resolve_invite_guild_id(invite_code)
        if invite_guild_id != guild_id:
            external_codes.append(invite_code)
    return external_codes

def _text_similarity(a: str, b: str) -> float:
    """計算兩個字串的相似度 (0.0 ~ 1.0)"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()

async def settings_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(
            name=app_commands.locale_str(key, i18n_key=f"cmd.automoderate.setting.{key}"),
            value=key,
        )
        for key in all_settings if current.lower() in key.lower()
    ][:25]  # Discord 限制最多 25 個選項


async def action_value_autocomplete(interaction: discord.Interaction, current: str):
    setting = str(getattr(interaction.namespace, "setting", "") or "")
    if not setting.endswith("-action"):
        return []

    choices = []
    current_text = current.strip()
    if current_text.isdigit():
        minutes = int(current_text)
        if 0 < minutes <= 40320:
            choices.append(
                app_commands.Choice(
                    name=t("moderate.autocomplete.mute_minutes", minutes=minutes),
                    value=f"mute {minutes}m",
                )
            )

    lowered = current_text.casefold()
    suggestions = (
        [(t("automoderate.suggest.clear"), "clear"), *_flagged_user_action_input_suggestions()]
        if setting == "flagged_user-action"
        else _action_input_suggestions()
    )
    for label, value in suggestions:
        if lowered and lowered not in label.casefold() and lowered not in value.casefold():
            continue
        if any(choice.value == value for choice in choices):
            continue
        choices.append(app_commands.Choice(name=label, value=value))
    return choices[:25]


async def action_autocomplete(interaction: discord.Interaction, current: str):
    choices = []
    current_text = current.strip()
    if current_text.isdigit():
        minutes = int(current_text)
        if 0 < minutes <= 40320:
            choices.append(app_commands.Choice(name=t("moderate.autocomplete.mute_minutes", minutes=minutes), value=f"mute {minutes}m"))
    lowered = current_text.casefold()
    for label, value in _action_input_suggestions():
        if lowered and lowered not in label.casefold() and lowered not in value.casefold():
            continue
        if any(choice.value == value for choice in choices):
            continue
        choices.append(app_commands.Choice(name=label, value=value))
    return choices[:25]


def build_action_preview_embed(analysis: dict, *, title: str | None = None, saved: bool = False) -> discord.Embed:
    """與 Moderate.build_action_preview_embed 同構，共用 moderate.* 語言檔鍵。"""
    if not analysis.get("valid"):
        return discord.Embed(
            title=t("moderate.action_invalid_title"),
            description=analysis.get("error") or t("moderate.err.cannot_parse_action"),
            color=discord.Color.red(),
        )

    embed = discord.Embed(
        title=title if title is not None else t("moderate.action_preview_title"),
        description=t("moderate.settings_saved") if saved else (analysis.get("confirmation") or t("moderate.please_confirm")),
        color=discord.Color.green() if saved else discord.Color.orange(),
    )
    embed.add_field(name=t("moderate.field.stored_command"), value=f"```text\n{analysis['normalized']}\n```", inline=False)
    preview = analysis.get("preview") or []
    embed.add_field(
        name=t("moderate.field.execution_preview"),
        value="\n".join(f"{index}. {line}" for index, line in enumerate(preview, 1)) or t("moderate.no_executable_actions"),
        inline=False,
    )
    return embed

async def do_action_str(action: str, guild: Optional[discord.Guild] = None, user: Optional[discord.Member] = None, message: Optional[discord.Message] = None):
    """AutoModerate wrapper：以機器人身份執行動作，委派給 Moderate.do_action_str。"""
    # 以 bot 本身作為 moderator，讓 send_mod_message 能在自動處置中正常運作
    moderator = guild.me if guild else None
    return await Moderate.do_action_str(action, guild=guild, user=user, message=message, moderator=moderator)


def _action_presets() -> list[tuple[str, str]]:
    """快速設定的處置預設選項（value 為 __custom__ 時會跳出 Modal 讓使用者輸入）。"""
    return [*_action_input_suggestions(), (t("automoderate.suggest.custom"), "__custom__")]


class QuickSetupActionConfirmationView(discord.ui.View):
    def __init__(self, quick_setup_view: "QuickSetupView", analysis: dict, owner_id: int):
        super().__init__(timeout=120)
        self.quick_setup_view = quick_setup_view
        self.analysis = analysis
        self.owner_id = owner_id

        confirm = discord.ui.Button(label=t("moderate.btn.confirm_action"), style=discord.ButtonStyle.success)
        confirm.callback = self.confirm
        self.add_item(confirm)
        retry = discord.ui.Button(label=t("automoderate.btn.retry_action"), style=discord.ButtonStyle.secondary)
        retry.callback = self.retry
        self.add_item(retry)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(t("moderate.err.not_your_confirmation"), ephemeral=True)
        return False

    async def confirm(self, interaction: discord.Interaction):
        self.quick_setup_view.config["action"] = self.analysis["normalized"]
        self.stop()
        await interaction.response.edit_message(
            embed=self.quick_setup_view._get_embed(interaction.guild),
            view=self.quick_setup_view,
        )

    async def retry(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CustomActionModal(self.quick_setup_view))


class CustomActionModal(i18n.I18nModal, title=i18n.K("automoderate.modal.custom_action_title")):
    action_input = discord.ui.TextInput(
        label=t("automoderate.modal.custom_action_label"),
        placeholder=t("automoderate.modal.custom_action_ph"),
        required=True,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, view: "QuickSetupView"):
        super().__init__()
        self.quick_setup_view = view

    async def on_submit(self, interaction: discord.Interaction):
        analyzer = (
            Moderate.analyze_member_join_action
            if self.quick_setup_view.feature == "flagged_user"
            else Moderate.analyze_action_string
        )
        analysis = analyzer(self.action_input.value, interaction.guild_id)
        if not analysis["valid"]:
            await interaction.response.send_message(analysis["error"], ephemeral=True)
            return
        if analysis["requires_confirmation"]:
            view = QuickSetupActionConfirmationView(self.quick_setup_view, analysis, interaction.user.id)
            await interaction.response.edit_message(
                embed=build_action_preview_embed(analysis, title=t("moderate.confirm_your_intent_title")),
                view=view,
            )
            return
        self.quick_setup_view.config["action"] = analysis["normalized"]
        await interaction.response.edit_message(embed=build_action_preview_embed(analysis), view=self.quick_setup_view)


class QuickSetupView(discord.ui.View):
    """互動式快速設定精靈"""
    def __init__(self, guild_id: int, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.step = 1
        self.feature = None
        self.config = {}

    def _format_channel_list(self, guild: discord.Guild, channel_ids) -> str:
        normalized_ids = _normalize_channel_id_list(channel_ids)
        if not normalized_ids:
            return t("moderate.fallback_none")

        mentions = []
        for channel_id in normalized_ids[:10]:
            ch = guild.get_channel(channel_id)
            mentions.append(ch.mention if ch else f"<#{channel_id}>")
        if len(normalized_ids) > 10:
            mentions.append(t("automoderate.quick_setup.more_channels", count=len(normalized_ids)))
        return i18n.join_list(mentions)

    def _get_embed(self, guild: discord.Guild):
        embed = discord.Embed(title=t("automoderate.quick_setup.title"), color=0x5865F2)
        if self.step == 1:
            embed.description = t("automoderate.quick_setup.step1_desc")
        elif self.step == 2 and self.feature:
            feature_name = t_enum("automoderate.feature_name_emoji", self.feature, default=self.feature)
            embed.description = t("automoderate.quick_setup.step2_desc", feature=feature_name)
            if self.config:
                for k, v in self.config.items():
                    if k == "log_channel" and v:
                        ch = guild.get_channel(int(v))
                        embed.add_field(name=t("automoderate.field.log_channel"), value=ch.mention if ch else v, inline=False)
                    elif k == "channel_id" and v:
                        ch = guild.get_channel(int(v))
                        embed.add_field(name=t("automoderate.field.channel"), value=ch.mention if ch else v, inline=False)
                    elif k == "ignore_channels":
                        embed.add_field(name=t("automoderate.field.ignore_channels"), value=self._format_channel_list(guild, v), inline=False)
                    elif k == "allow_current_server":
                        embed.add_field(name=t("automoderate.field.allow_current_server"), value=t("common.state.yes") if _is_truthy(v) else t("common.state.no"), inline=False)
                    elif k == "action":
                        embed.add_field(name=t("automoderate.field.action"), value=f"`{str(v)[:50]}{'...' if len(str(v)) > 50 else ''}`", inline=False)
                    else:
                        embed.add_field(name=k, value=str(v), inline=True)
        return embed

    def _update_components_step1(self):
        self.clear_items()
        opts = [
            discord.SelectOption(label=t("automoderate.feature_name.scamtrap"), value="scamtrap", description=t("automoderate.feature_desc.scamtrap")),
            discord.SelectOption(label=t("automoderate.feature_name.escape_punish"), value="escape_punish", description=t("automoderate.feature_desc.escape_punish")),
            discord.SelectOption(label=t("automoderate.feature_name.too_many_h1"), value="too_many_h1", description=t("automoderate.feature_desc.too_many_h1")),
            discord.SelectOption(label=t("automoderate.feature_name.too_many_emojis"), value="too_many_emojis", description=t("automoderate.feature_desc.too_many_emojis")),
            discord.SelectOption(label=t("automoderate.feature_name.anti_invite_link"), value="anti_invite_link", description=t("automoderate.feature_desc.anti_invite_link")),
            discord.SelectOption(label=t("automoderate.feature_name.anti_uispam"), value="anti_uispam", description=t("automoderate.feature_desc.anti_uispam")),
            discord.SelectOption(label=t("automoderate.feature_name.anti_raid"), value="anti_raid", description=t("automoderate.feature_desc.anti_raid")),
            discord.SelectOption(label=t("automoderate.feature_name.anti_spam"), value="anti_spam", description=t("automoderate.feature_desc.anti_spam")),
            discord.SelectOption(label=t("automoderate.feature_name.automod_detect"), value="automod_detect", description=t("automoderate.feature_desc.automod_detect")),
            discord.SelectOption(label=t("automoderate.feature_name.flagged_user"), value="flagged_user", description=t("automoderate.feature_desc.flagged_user")),
        ]
        sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.feature_ph"), options=opts)
        sel.callback = self._on_feature_select
        self.add_item(sel)

    def _update_components_step2(self, guild: discord.Guild):
        self.clear_items()
        automod_settings = get_server_config(self.guild_id, "automod", {}).get(self.feature, {})
        defaults = automod_settings.copy()

        if self.feature == "scamtrap":
            ch_sel = discord.ui.ChannelSelect(
                placeholder=t("automoderate.quick_setup.trap_channel_ph"),
                channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                min_values=1, max_values=1,
            )
            ch_sel.callback = self._on_scamtrap_channel
            self.add_item(ch_sel)
        elif self.feature == "anti_invite_link":
            allow_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.allow_invite_ph"), options=[
                discord.SelectOption(label=t("common.state.yes"), value="True", description=t("automoderate.quick_setup.allow_invite_yes_desc")),
                discord.SelectOption(label=t("common.state.no"), value="False", description=t("automoderate.quick_setup.allow_invite_no_desc")),
            ])
            allow_sel.callback = self._on_invite_allow_current_server_select
            self.add_item(allow_sel)
        elif self.feature == "escape_punish":
            punish_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.punishment_ph"), options=[
                discord.SelectOption(label=t("moderate.action_name.ban"), value="ban", description=t("moderate.duration.permanent")),
            ])
            punish_sel.callback = self._on_escape_punish_select
            self.add_item(punish_sel)
            dur_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.ban_duration_ph"), options=[
                discord.SelectOption(label=t("moderate.duration.permanent"), value="0"),
                discord.SelectOption(label=t("automoderate.quick_setup.days_7"), value="7d"),
                discord.SelectOption(label=t("automoderate.quick_setup.days_30"), value="30d"),
            ])
            dur_sel.callback = self._on_escape_duration_select
            self.add_item(dur_sel)
            # escape_punish 不需 action
            btn = discord.ui.Button(label=t("automoderate.quick_setup.finish_btn"), style=discord.ButtonStyle.success)
            btn.callback = self._on_finish
            self.add_item(btn)
            return
        elif self.feature == "too_many_h1":
            len_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.max_h1_length_ph"), options=[
                discord.SelectOption(label="15", value="15"),
                discord.SelectOption(label="20", value="20"),
                discord.SelectOption(label="30", value="30"),
                discord.SelectOption(label="50", value="50"),
            ])
            len_sel.callback = self._on_h1_length_select
            self.add_item(len_sel)
        elif self.feature == "too_many_emojis":
            emoji_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.max_emojis_ph"), options=[
                discord.SelectOption(label="5", value="5"),
                discord.SelectOption(label="10", value="10"),
                discord.SelectOption(label="15", value="15"),
                discord.SelectOption(label="20", value="20"),
            ])
            emoji_sel.callback = self._on_emojis_select
            self.add_item(emoji_sel)
        elif self.feature == "anti_uispam":
            cnt_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.max_trigger_count_ph"), options=[
                discord.SelectOption(label="3", value="3"),
                discord.SelectOption(label="5", value="5"),
                discord.SelectOption(label="10", value="10"),
            ])
            cnt_sel.callback = self._on_uispam_count_select
            self.add_item(cnt_sel)
            win_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.time_window_ph"), options=[
                discord.SelectOption(label=t("automoderate.quick_setup.seconds_n", count=30), value="30"),
                discord.SelectOption(label=t("automoderate.quick_setup.seconds_n", count=60), value="60"),
                discord.SelectOption(label=t("automoderate.quick_setup.seconds_n", count=120), value="120"),
            ])
            win_sel.callback = self._on_uispam_window_select
            self.add_item(win_sel)
        elif self.feature == "anti_raid":
            joins_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.max_joins_ph"), options=[
                discord.SelectOption(label="3", value="3"),
                discord.SelectOption(label="5", value="5"),
                discord.SelectOption(label="10", value="10"),
            ])
            joins_sel.callback = self._on_raid_joins_select
            self.add_item(joins_sel)
            win_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.time_window_ph"), options=[
                discord.SelectOption(label=t("automoderate.quick_setup.seconds_n", count=30), value="30"),
                discord.SelectOption(label=t("automoderate.quick_setup.seconds_n", count=60), value="60"),
                discord.SelectOption(label=t("automoderate.quick_setup.seconds_n", count=120), value="120"),
            ])
            win_sel.callback = self._on_raid_window_select
            self.add_item(win_sel)
        elif self.feature == "anti_spam":
            msg_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.max_similar_messages_ph"), options=[
                discord.SelectOption(label="3", value="3"),
                discord.SelectOption(label="5", value="5"),
                discord.SelectOption(label="10", value="10"),
            ])
            msg_sel.callback = self._on_spam_messages_select
            self.add_item(msg_sel)
            win_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.time_window_ph"), options=[
                discord.SelectOption(label=t("automoderate.quick_setup.seconds_n", count=30), value="30"),
                discord.SelectOption(label=t("automoderate.quick_setup.seconds_n", count=60), value="60"),
            ])
            win_sel.callback = self._on_spam_window_select
            self.add_item(win_sel)
            sim_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.similarity_ph"), options=[
                discord.SelectOption(label="50%", value="50"),
                discord.SelectOption(label="75%", value="75"),
                discord.SelectOption(label="90%", value="90"),
            ])
            sim_sel.callback = self._on_spam_similarity_select
            self.add_item(sim_sel)
        elif self.feature == "automod_detect":
            ch_sel = discord.ui.ChannelSelect(
                placeholder=t("automoderate.quick_setup.log_channel_ph"),
                channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                min_values=1, max_values=1,
            )
            ch_sel.callback = self._on_automod_detect_channel
            self.add_item(ch_sel)
        elif self.feature == "flagged_user":
            ch_sel = discord.ui.ChannelSelect(
                placeholder=t("automoderate.quick_setup.log_channel_ph"),
                channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                min_values=1,
                max_values=1,
            )
            ch_sel.callback = self._on_automod_detect_channel
            self.add_item(ch_sel)
            source_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.action_source_ph"), options=[
                discord.SelectOption(label=t("automoderate.quick_setup.source_both"), value="both"),
                discord.SelectOption(label=t("automoderate.quick_setup.source_local"), value="local"),
                discord.SelectOption(label=t("automoderate.quick_setup.source_api"), value="api"),
            ])
            source_sel.callback = self._on_flagged_action_source_select
            self.add_item(source_sel)
            mode_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.match_mode_ph"), options=[
                discord.SelectOption(label=t("automoderate.quick_setup.match_mode_active"), value="active"),
                discord.SelectOption(label=t("automoderate.quick_setup.match_mode_history"), value="history"),
            ])
            mode_sel.callback = self._on_flagged_local_match_mode_select
            self.add_item(mode_sel)

        if self.feature in AUTOMOD_IGNORE_CHANNEL_FEATURES:
            ignore_sel = discord.ui.ChannelSelect(
                placeholder=t("automoderate.quick_setup.ignore_channels_ph"),
                channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                min_values=0, max_values=25,
            )
            ignore_sel.callback = self._on_ignore_channels_select
            self.add_item(ignore_sel)

        action_presets = (
            [*_flagged_user_action_input_suggestions(), (t("automoderate.suggest.custom"), "__custom__")]
            if self.feature == "flagged_user"
            else _action_presets()
        )
        action_opts = [discord.SelectOption(label=l, value=v) for l, v in action_presets]
        action_sel = discord.ui.Select(placeholder=t("automoderate.quick_setup.action_ph"), options=action_opts)
        action_sel.callback = self._on_action_select
        self.add_item(action_sel)

        btn = discord.ui.Button(label=t("automoderate.quick_setup.finish_btn"), style=discord.ButtonStyle.success)
        btn.callback = self._on_finish
        self.add_item(btn)

    async def _on_feature_select(self, interaction: discord.Interaction):
        self.feature = interaction.data["values"][0]
        self.step = 2
        self.config = {}
        feat_defaults = {
            "too_many_h1": {"max_length": "20"},
            "too_many_emojis": {"max_emojis": "10"},
            "anti_invite_link": {"allow_current_server": "False"},
            "anti_uispam": {"max_count": "5", "time_window": "60"},
            "anti_raid": {"max_joins": "5", "time_window": "60"},
            "anti_spam": {"max_messages": "5", "time_window": "30", "similarity": "75"},
            "escape_punish": {"punishment": "ban", "duration": "0"},
            "automod_detect": {},
            "flagged_user": {"action_source": "both", "local_match_mode": "active"},
        }
        self.config = feat_defaults.get(self.feature, {}).copy()
        self._update_components_step2(interaction.guild)
        await interaction.response.edit_message(embed=self._get_embed(interaction.guild), view=self)

    async def _on_scamtrap_channel(self, interaction: discord.Interaction):
        self.config["channel_id"] = str(interaction.data["values"][0])
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_escape_punish_select(self, interaction: discord.Interaction):
        self.config["punishment"] = interaction.data["values"][0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_escape_duration_select(self, interaction: discord.Interaction):
        self.config["duration"] = interaction.data["values"][0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_h1_length_select(self, interaction: discord.Interaction):
        self.config["max_length"] = interaction.data["values"][0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_emojis_select(self, interaction: discord.Interaction):
        self.config["max_emojis"] = interaction.data["values"][0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_invite_allow_current_server_select(self, interaction: discord.Interaction):
        self.config["allow_current_server"] = interaction.data["values"][0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_uispam_count_select(self, interaction: discord.Interaction):
        self.config["max_count"] = interaction.data["values"][0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_uispam_window_select(self, interaction: discord.Interaction):
        self.config["time_window"] = interaction.data["values"][0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_raid_joins_select(self, interaction: discord.Interaction):
        self.config["max_joins"] = interaction.data["values"][0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_raid_window_select(self, interaction: discord.Interaction):
        self.config["time_window"] = interaction.data["values"][0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_spam_messages_select(self, interaction: discord.Interaction):
        self.config["max_messages"] = interaction.data["values"][0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_spam_window_select(self, interaction: discord.Interaction):
        self.config["time_window"] = interaction.data["values"][0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_spam_similarity_select(self, interaction: discord.Interaction):
        self.config["similarity"] = interaction.data["values"][0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_automod_detect_channel(self, interaction: discord.Interaction):
        self.config["log_channel"] = str(interaction.data["values"][0])
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_flagged_action_source_select(self, interaction: discord.Interaction):
        self.config["action_source"] = interaction.data["values"][0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_flagged_local_match_mode_select(self, interaction: discord.Interaction):
        self.config["local_match_mode"] = interaction.data["values"][0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_ignore_channels_select(self, interaction: discord.Interaction):
        self.config["ignore_channels"] = _normalize_channel_id_list(interaction.data.get("values", []))
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_action_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        if value == "__custom__":
            modal = CustomActionModal(self)
            await interaction.response.send_modal(modal)
            return
        self.config["action"] = value
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(interaction.guild), view=self)

    async def _on_finish(self, interaction: discord.Interaction):
        if self.feature not in ("scamtrap", "escape_punish", "too_many_h1", "too_many_emojis", "anti_invite_link", "anti_uispam", "anti_raid", "anti_spam", "automod_detect", "flagged_user"):
            await interaction.response.send_message(t("automoderate.err.invalid_feature"), ephemeral=True)
            return
        if self.feature == "scamtrap" and "channel_id" not in self.config:
            await interaction.response.send_message(t("automoderate.err.scamtrap_needs_channel"), ephemeral=True)
            return
        if self.feature == "automod_detect" and "log_channel" not in self.config:
            await interaction.response.send_message(t("automoderate.err.automod_detect_needs_channel"), ephemeral=True)
            return
        if self.feature == "flagged_user" and "log_channel" not in self.config:
            await interaction.response.send_message(t("automoderate.err.flagged_user_needs_channel"), ephemeral=True)
            return
        if "action" not in self.config and self.feature in ("scamtrap", "too_many_h1", "too_many_emojis", "anti_invite_link", "anti_uispam", "anti_raid", "anti_spam"):
            await interaction.response.send_message(t("automoderate.err.pick_action"), ephemeral=True)
            return

        automod_settings = get_server_config(self.guild_id, "automod", {})
        automod_settings.setdefault(self.feature, {})
        automod_settings[self.feature]["enabled"] = True
        for k, v in self.config.items():
            if k and v is not None:
                if k == "ignore_channels":
                    automod_settings[self.feature][k] = _normalize_channel_id_list(v)
                elif k == "allow_current_server":
                    automod_settings[self.feature][k] = _is_truthy(v)
                else:
                    automod_settings[self.feature][k] = str(v)
        set_server_config(self.guild_id, "automod", automod_settings)

        feature_name = t_enum("automoderate.feature_name", self.feature, default=self.feature)
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(title=t("automoderate.quick_setup.done_title"), color=0x00ff00,
                description=t("automoderate.quick_setup.done_desc", feature=feature_name)),
            view=None,
        )

    async def on_timeout(self):
        self.stop()


def parse_mention_to_id(mention: str) -> str:
    # 解析用戶、頻道或角色的提及格式，返回ID
    match = re.match(r"<@!?(\d+)>", mention)  # 用戶提及
    if match:
        return match.group(1)
    match = re.match(r"<#(\d+)>", mention)  # 頻道提及
    if match:
        return match.group(1)
    match = re.match(r"<@&(\d+)>", mention)  # 角色提及
    if match:
        return match.group(1)
    return mention  # 如果不是提及格式，直接返回原字符串


class SaveActionConfirmationView(i18n.I18nView):
    def __init__(self, guild_id: int, feature: str, analysis: dict, owner_id: int):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.feature = feature
        self.analysis = analysis
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(t("moderate.err.not_your_confirmation"), ephemeral=True)
        return False

    @discord.ui.button(label=i18n.K("automoderate.btn.save_action"), style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        automod_settings = get_server_config(self.guild_id, "automod", {})
        if not isinstance(automod_settings, dict):
            automod_settings = {}
        if self.feature == "flagged_user":
            feature_settings = _ensure_flagged_user_settings(automod_settings, self.guild_id)
        else:
            feature_settings = automod_settings.setdefault(self.feature, {})
        feature_settings["action"] = self.analysis["normalized"]
        set_server_config(self.guild_id, "automod", automod_settings)
        self.stop()
        await interaction.response.edit_message(
            embed=build_action_preview_embed(self.analysis, title=t("automoderate.action_setup_done_title"), saved=True),
            view=None,
        )

    @discord.ui.button(label=i18n.K("common.btn.cancel"), style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content=t("moderate.action_settings_unchanged"), embed=None, view=None)
    


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class AutoModerate(commands.GroupCog, name=app_commands.locale_str("automod", i18n_key="cmd.automoderate.automod.root.name")):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._blacklist_cache: dict[int, list[dict]] = {}
        self._blacklist_last_success: datetime | None = None
        self._blacklist_last_error: str | None = None
        self._blacklist_session: aiohttp.ClientSession | None = None
        self._missing_blacklist_key_logged = False
        super().__init__()

    async def _get_blacklist_session(self) -> aiohttp.ClientSession:
        if self._blacklist_session is None or self._blacklist_session.closed:
            self._blacklist_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self._blacklist_session

    async def _fetch_blacklist_snapshot(self) -> dict[int, list[dict]]:
        api_key = str(config("blacklist_api_key", "") or "").strip()
        if not api_key:
            raise RuntimeError("blacklist_api_key is not configured")

        session = await self._get_blacklist_session()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        for attempt in range(2):
            async with session.get(BLACKLIST_API_URL, headers=headers) as response:
                if response.status == 429 and attempt == 0:
                    try:
                        retry_after = max(0.0, min(float(response.headers.get("Retry-After", "2")), 60.0))
                    except (TypeError, ValueError):
                        retry_after = 2.0
                    await asyncio.sleep(retry_after)
                    continue
                if response.status >= 400:
                    raise RuntimeError(f"Blacklist API returned HTTP {response.status}")
                try:
                    payload = await response.json()
                except (aiohttp.ContentTypeError, json.JSONDecodeError) as error:
                    raise ValueError("Blacklist API response is not valid JSON") from error
                return _normalize_blacklist_payload(payload)
        raise RuntimeError("Blacklist API rate limit exceeded")

    async def sync_blacklist_cache(self) -> bool:
        if not str(config("blacklist_api_key", "") or "").strip():
            self._blacklist_last_error = "API key is not configured"
            if not self._missing_blacklist_key_logged:
                log(
                    "Blacklist API sync disabled: blacklist_api_key is not set in config.json.",
                    level=logging.WARNING,
                    module_name="AutoModerate",
                )
                self._missing_blacklist_key_logged = True
            return False
        self._missing_blacklist_key_logged = False
        try:
            snapshot = await self._fetch_blacklist_snapshot()
        except Exception as error:
            self._blacklist_last_error = f"{type(error).__name__}: {error}"
            log(
                f"Blacklist API sync failed; keeping the last successful cache: {error}",
                level=logging.ERROR,
                module_name="AutoModerate",
            )
            return False

        self._blacklist_cache = snapshot
        self._blacklist_last_success = datetime.now(timezone.utc)
        self._blacklist_last_error = None
        record_count = sum(len(records) for records in snapshot.values())
        log(
            f"Blacklist API sync complete: {len(snapshot)} users, {record_count} active records.",
            module_name="AutoModerate",
        )
        return True

    @tasks.loop(minutes=BLACKLIST_SYNC_INTERVAL_MINUTES)
    async def blacklist_sync_task(self):
        await self.sync_blacklist_cache()

    @blacklist_sync_task.before_loop
    async def before_blacklist_sync_task(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.blacklist_sync_task.is_running():
            self.blacklist_sync_task.start()

    async def cog_unload(self):
        self.blacklist_sync_task.cancel()
        if self._blacklist_session is not None and not self._blacklist_session.closed:
            await self._blacklist_session.close()
        
    @app_commands.command(name=app_commands.locale_str("view", i18n_key="cmd.automoderate.automod.view.name"), description=app_commands.locale_str("View auto-moderation settings", i18n_key="cmd.automoderate.automod.view.desc"))
    async def view_automod_settings(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        automod_settings = get_server_config(guild_id, "automod", {})
        if not isinstance(automod_settings, dict):
            automod_settings = {}
        if "flagged_user" not in automod_settings:
            flagged_config = _normalize_flagged_user_config(guild_id)
            if flagged_config["legacy"]:
                automod_settings = dict(automod_settings)
                automod_settings["flagged_user"] = {
                    key: value for key, value in flagged_config.items() if key != "legacy"
                }
        if not automod_settings:
            await interaction.response.send_message(t("automoderate.err.not_enabled"), ephemeral=True)
            return

        embed = discord.Embed(title=t("automoderate.settings_title"), color=0x00ff00)
        desc = ""
        for key, value in automod_settings.items():
            desc += f"**{key}**:"
            for subkey, subvalue in value.items():
                desc += f"\n - {subkey}: {subvalue}"
            desc += "\n"
        embed.description = desc
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name=app_commands.locale_str("toggle", i18n_key="cmd.automoderate.automod.toggle.name"), description=app_commands.locale_str("Enable or disable an auto-moderation setting", i18n_key="cmd.automoderate.automod.toggle.desc"))
    @app_commands.describe(setting=app_commands.locale_str("Name of the auto-moderation setting to toggle", i18n_key="cmd.automoderate.automod.toggle.param.setting"), enable=app_commands.locale_str("Whether to enable the setting", i18n_key="cmd.automoderate.automod.toggle.param.enable"))
    @app_commands.choices(
        setting=[
            app_commands.Choice(name=app_commands.locale_str("Scam trap", i18n_key="cmd.automoderate.automod.toggle.choice.scamtrap"), value="scamtrap"),
            app_commands.Choice(name=app_commands.locale_str("Punishment evasion", i18n_key="cmd.automoderate.automod.toggle.choice.escape_punish"), value="escape_punish"),
            app_commands.Choice(name=app_commands.locale_str("Too many headings", i18n_key="cmd.automoderate.automod.toggle.choice.too_many_h1"), value="too_many_h1"),
            app_commands.Choice(name=app_commands.locale_str("Too many emojis", i18n_key="cmd.automoderate.automod.toggle.choice.too_many_emojis"), value="too_many_emojis"),
            app_commands.Choice(name=app_commands.locale_str("Invite links", i18n_key="cmd.automoderate.automod.toggle.choice.anti_invite_link"), value="anti_invite_link"),
            app_commands.Choice(name=app_commands.locale_str("User-installed app abuse", i18n_key="cmd.automoderate.automod.toggle.choice.anti_uispam"), value="anti_uispam"),
            app_commands.Choice(name=app_commands.locale_str("Anti-raid (mass-join detection)", i18n_key="cmd.automoderate.automod.toggle.choice.anti_raid"), value="anti_raid"),
            app_commands.Choice(name=app_commands.locale_str("Anti-spam", i18n_key="cmd.automoderate.automod.toggle.choice.anti_spam"), value="anti_spam"),
            app_commands.Choice(name=app_commands.locale_str("AutoMod detection (native AutoMod triggers)", i18n_key="cmd.automoderate.automod.toggle.choice.automod_detect"), value="automod_detect"),
            app_commands.Choice(name=app_commands.locale_str("Flagged user joins", i18n_key="cmd.automoderate.automod.toggle.choice.flagged_user"), value="flagged_user"),
        ],
        enable=[
            app_commands.Choice(name=app_commands.locale_str("Enable", i18n_key="cmd.automoderate.automod.toggle.choice.true"), value="True"),
            app_commands.Choice(name=app_commands.locale_str("Disable", i18n_key="cmd.automoderate.automod.toggle.choice.false"), value="False"),
        ]
    )
    async def toggle_automod_setting(self, interaction: discord.Interaction, setting: str, enable: str):
        guild_id = interaction.guild.id if interaction.guild else None
        automod_settings = get_server_config(guild_id, "automod", {})
        if not isinstance(automod_settings, dict):
            automod_settings = {}
        if setting == "flagged_user":
            feature_settings = _ensure_flagged_user_settings(automod_settings, guild_id)
        else:
            feature_settings = automod_settings.setdefault(setting, {})
        feature_settings["enabled"] = (enable == "True")
        set_server_config(guild_id, "automod", automod_settings)
        await interaction.response.send_message(
            t("automoderate.msg.toggled", setting=setting,
              status=t("common.state.enabled" if enable == "True" else "common.state.disabled")))
        
        if setting == "scamtrap" and enable == "True":
            # settings
            if "channel_id" not in automod_settings.get("scamtrap", {}):
                await interaction.followup.send(t("automoderate.warn.scamtrap_no_channel", command=await get_command_mention('automod', 'settings')), ephemeral=True)
            if "action" not in automod_settings.get("scamtrap", {}):
                await interaction.followup.send(t("automoderate.warn.scamtrap_no_action", command=await get_command_mention('automod', 'settings')), ephemeral=True)

        if setting == "automod_detect" and enable == "True":
            if "log_channel" not in automod_settings.get("automod_detect", {}):
                await interaction.followup.send(t("automoderate.warn.automod_detect_no_channel", command=await get_command_mention('automod', 'settings')), ephemeral=True)
        if setting == "flagged_user" and enable == "True":
            if not automod_settings.get("flagged_user", {}).get("log_channel"):
                legacy_channel = get_server_config(guild_id, "flagged_user_onjoin_channel")
                if not legacy_channel:
                    await interaction.followup.send(
                        t("automoderate.warn.flagged_user_no_channel", command=await get_command_mention('automod', 'settings')),
                        ephemeral=True,
                    )
        if setting == "anti_invite_link" and enable == "True":
            if "action" not in automod_settings.get("anti_invite_link", {}):
                await interaction.followup.send(t("automoderate.warn.anti_invite_no_action", command=await get_command_mention('automod', 'settings')), ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("quick-setup", i18n_key="cmd.automoderate.automod.quick_setup.name"), description=app_commands.locale_str("Interactive quick-setup wizard (menu guided)", i18n_key="cmd.automoderate.automod.quick_setup.desc"))
    async def quick_setup_automod(self, interaction: discord.Interaction):
        getting_started_module = sys.modules.get("gettingstarted")
        if getting_started_module is not None:
            await getting_started_module.start_automod_quick_setup(interaction)
            return
        guild_id = interaction.guild.id if interaction.guild else 0
        view = QuickSetupView(guild_id)
        view._update_components_step1()
        await interaction.response.send_message(
            embed=view._get_embed(interaction.guild),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name=app_commands.locale_str("settings", i18n_key="cmd.automoderate.automod.settings.name"), description=app_commands.locale_str("Configure auto-moderation options", i18n_key="cmd.automoderate.automod.settings.desc"))
    @app_commands.describe(
        setting=app_commands.locale_str("The auto-moderation option to configure", i18n_key="cmd.automoderate.automod.settings.param.setting"),
        value=app_commands.locale_str("The option's value", i18n_key="cmd.automoderate.automod.settings.param.value")
    )
    @app_commands.autocomplete(setting=settings_autocomplete, value=action_value_autocomplete)
    async def set_automod_setting(self, interaction: discord.Interaction, setting: str, value: str):
        guild_id = interaction.guild.id if interaction.guild else None
        automod_settings = get_server_config(guild_id, "automod", {})
        if not isinstance(automod_settings, dict):
            automod_settings = {}
        setting_base = setting.split("-")[0]
        setting_key = setting.split("-")[1] if len(setting.split("-")) > 1 else None
        if setting_base not in automod_settings:
            if setting_base == "flagged_user":
                _ensure_flagged_user_settings(automod_settings, guild_id)
            else:
                automod_settings[setting_base] = {}
        action_analysis = None
        if setting_key == "action":
            clear_action = (
                setting_base == "flagged_user"
                and str(value).strip().lower() in {"none", "null", "clear", "無", "清空"}  # i18n: skip (input syntax)
            )
            if clear_action:
                value = ""
            else:
                analyzer = Moderate.analyze_member_join_action if setting_base == "flagged_user" else Moderate.analyze_action_string
                action_analysis = analyzer(value, guild_id)
                if not action_analysis["valid"]:
                    await interaction.response.send_message(
                        embed=build_action_preview_embed(action_analysis),
                        ephemeral=True,
                    )
                    return
                if action_analysis["requires_confirmation"]:
                    await interaction.response.send_message(
                        embed=build_action_preview_embed(action_analysis, title=t("moderate.confirm_your_intent_title")),
                        view=SaveActionConfirmationView(
                            guild_id,
                            setting_base,
                            action_analysis,
                            interaction.user.id,
                        ),
                        ephemeral=True,
                    )
                    return
                value = action_analysis["normalized"]
        value = parse_mention_to_id(value) if setting_key in ["channel_id", "log_channel"] else value
        if setting_key == "allow_current_server":
            normalized_value = str(value).strip().lower()
            if normalized_value not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                await interaction.response.send_message(t("automoderate.err.allow_current_server_bool"), ephemeral=True)
                return
            value = _is_truthy(normalized_value)
        if setting_base == "flagged_user" and setting_key == "action_source":
            value = str(value).strip().lower()
            if value not in FLAGGED_USER_ACTION_SOURCES:
                await interaction.response.send_message(t("automoderate.err.action_source_invalid"), ephemeral=True)
                return
        if setting_base == "flagged_user" and setting_key == "local_match_mode":
            value = str(value).strip().lower()
            if value not in FLAGGED_USER_LOCAL_MATCH_MODES:
                await interaction.response.send_message(t("automoderate.err.local_match_mode_invalid"), ephemeral=True)
                return
        if setting_key == "ignore_channels":
            raw_text = str(value or "").strip()
            if raw_text.lower() in {"none", "null", "clear", "[]"} or raw_text in {"無", "清空"}:  # i18n: skip (input syntax)
                value = []
            else:
                tokens = [token.strip() for token in re.split(r"[,，\n]+", raw_text) if token.strip()]
                if not tokens:
                    value = []
                else:
                    parsed_channels = []
                    invalid_tokens = []
                    seen_channels = set()
                    for token in tokens:
                        parsed = parse_mention_to_id(token)
                        if not parsed.isdigit():
                            invalid_tokens.append(token)
                            continue
                        channel_id = int(parsed)
                        if channel_id in seen_channels:
                            continue
                        seen_channels.add(channel_id)
                        parsed_channels.append(channel_id)
                    if invalid_tokens:
                        await interaction.response.send_message(
                            t("automoderate.err.unparseable_channels", channels=', '.join(invalid_tokens)),
                            ephemeral=True,
                        )
                        return
                    for channel_id in parsed_channels:
                        if interaction.guild.get_channel(channel_id) is None:
                            await interaction.response.send_message(
                                t("automoderate.err.channel_not_in_guild", channel_id=channel_id),
                                ephemeral=True,
                            )
                            return
                    value = parsed_channels
        # 若為頻道設定，驗證頻道存在且機器人有發言權限
        if setting_key in ["channel_id", "log_channel"] and value:
            try:
                channel_obj = interaction.guild.get_channel(int(value))
            except (ValueError, TypeError):
                channel_obj = None
            if channel_obj is None:
                await interaction.response.send_message(t("automoderate.err.channel_not_found", channel_id=value), ephemeral=True)
                return
            perms = channel_obj.permissions_for(interaction.guild.me)
            if not (perms.view_channel and perms.send_messages):
                await interaction.response.send_message(t("automoderate.err.channel_no_perms", channel=channel_obj.mention), ephemeral=True)
                return
        automod_settings[setting_base][setting_key] = value
        set_server_config(guild_id, "automod", automod_settings)
        if action_analysis is not None:
            await interaction.response.send_message(
                embed=build_action_preview_embed(action_analysis, title=t("automoderate.action_setup_done_title"), saved=True),
            )
        else:
            await interaction.response.send_message(t("automoderate.msg.setting_set", setting=setting, value=value))
    
    @app_commands.command(name=app_commands.locale_str("check-action", i18n_key="cmd.automoderate.automod.check_action.name"), description=app_commands.locale_str("Check whether an automod action command is valid", i18n_key="cmd.automoderate.automod.check_action.desc"))
    @app_commands.describe(action=app_commands.locale_str("The action command to check", i18n_key="cmd.automoderate.automod.check_action.param.action"))
    @app_commands.autocomplete(action=action_autocomplete)
    async def check_automod_action(self, interaction: discord.Interaction, action: str):
        analysis = Moderate.analyze_action_string(action, interaction.guild_id)
        await interaction.response.send_message(
            embed=build_action_preview_embed(
                analysis,
                title=t("moderate.confirm_your_intent_title") if analysis["requires_confirmation"] else t("automoderate.action_check_title"),
            ),
            ephemeral=not analysis["valid"],
        )

    @app_commands.command(name=app_commands.locale_str("action-builder", i18n_key="cmd.automoderate.automod.action_builder.name"), description=app_commands.locale_str("Build an action command string", i18n_key="cmd.automoderate.automod.action_builder.desc"))
    @app_commands.describe(
        action_type=app_commands.locale_str("Action type", i18n_key="cmd.automoderate.automod.action_builder.param.action_type"),
        duration=app_commands.locale_str("Duration (for mute/ban/force_verify), e.g. 10m, 7d; 0 = permanent", i18n_key="cmd.automoderate.automod.action_builder.param.duration"),
        delete_message_duration=app_commands.locale_str("Ban only: delete the user's messages from this period, e.g. 1d; 0 = none", i18n_key="cmd.automoderate.automod.action_builder.param.delete_message_duration"),
        reason=app_commands.locale_str("Reason (for mute/kick/ban)", i18n_key="cmd.automoderate.automod.action_builder.param.reason"),
        message=app_commands.locale_str("Warning message (for delete/warn); {user} inserts the user", i18n_key="cmd.automoderate.automod.action_builder.param.message"),
        prepend=app_commands.locale_str("Existing command to prepend before this action (comma-separated actions)", i18n_key="cmd.automoderate.automod.action_builder.param.prepend"),
    )
    @app_commands.choices(
        action_type=[
            app_commands.Choice(name=app_commands.locale_str("Delete message", i18n_key="cmd.automoderate.automod.action_builder.choice.delete"), value="delete"),
            app_commands.Choice(name=app_commands.locale_str("Delete message + DM warning", i18n_key="cmd.automoderate.automod.action_builder.choice.delete_dm"), value="delete_dm"),
            app_commands.Choice(name=app_commands.locale_str("Public warning", i18n_key="cmd.automoderate.automod.action_builder.choice.warn"), value="warn"),
            app_commands.Choice(name=app_commands.locale_str("DM warning", i18n_key="cmd.automoderate.automod.action_builder.choice.warn_dm"), value="warn_dm"),
            app_commands.Choice(name=app_commands.locale_str("Mute", i18n_key="cmd.automoderate.automod.action_builder.choice.mute"), value="mute"),
            app_commands.Choice(name=app_commands.locale_str("Kick", i18n_key="cmd.automoderate.automod.action_builder.choice.kick"), value="kick"),
            app_commands.Choice(name=app_commands.locale_str("Ban", i18n_key="cmd.automoderate.automod.action_builder.choice.ban"), value="ban"),
            app_commands.Choice(name=app_commands.locale_str("Send moderation notice", i18n_key="cmd.automoderate.automod.action_builder.choice.send_mod_message"), value="send_mod_message"),
            app_commands.Choice(name=app_commands.locale_str("Force verification", i18n_key="cmd.automoderate.automod.action_builder.choice.force_verify"), value="force_verify"),
        ],
    )
    async def action_builder(
        self,
        interaction: discord.Interaction,
        action_type: str,
        duration: Optional[str] = None,
        delete_message_duration: Optional[str] = None,
        reason: Optional[str] = None,
        message: Optional[str] = None,
        prepend: Optional[str] = None,
    ):
        parts = []
        if action_type == "delete":
            parts = ["delete"]
            if message:
                parts.append(message)
        elif action_type == "delete_dm":
            parts = ["delete_dm"]
            if message:
                parts.append(message)
        elif action_type == "warn":
            parts = ["warn"]
            parts.append(message or t("moderate.default_warn_message"))
        elif action_type == "warn_dm":
            parts = ["warn_dm"]
            parts.append(message or t("moderate.default_warn_message"))
        elif action_type == "force_verify":
            parts = ["force_verify"]
            if duration:
                parts.append(duration)
        elif action_type == "mute":
            parts = ["mute", duration or "10m"]
            if reason:
                parts.append(reason)
        elif action_type == "kick":
            parts = ["kick"]
            if reason:
                parts.append(reason)
        elif action_type == "ban":
            parts = ["ban", duration or "0", delete_message_duration or "0"]
            if reason:
                parts.append(reason)
        elif action_type == "send_mod_message":
            parts = ["send_mod_message"]

        generated = " ".join(parts)
        if prepend and prepend.strip():
            generated = f"{prepend.strip()}, {generated}"
        if len([a for a in generated.split(",")]) > 5:
            await interaction.response.send_message(t("moderate.log.error", error=t("moderate.err.too_many_actions")), ephemeral=True)
            return

        embed = discord.Embed(title=t("moderate.action_builder.result_title"), color=0x00ff00)
        embed.description = f"```\n{generated}\n```"
        embed.add_field(name=t("moderate.action_builder.usage_field"),
                        value=t("automoderate.action_builder.usage_value",
                                settings_command=await get_command_mention('automod', 'settings'),
                                setup_command=await get_command_mention('automod', 'setup')),
                        inline=False)
        try:
            preview = await do_action_str(generated)
            embed.add_field(name=t("moderate.action_builder.preview_field"), value="\n".join(f"• {a}" for a in preview), inline=False)
        except Exception:
            pass
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("scan-flagged-users", i18n_key="cmd.automoderate.automod.scan_flagged_users.name"), description=app_commands.locale_str("Scan the server for flagged users", i18n_key="cmd.automoderate.automod.scan_flagged_users.desc"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(user=app_commands.locale_str("The user to scan; scans everyone if omitted", i18n_key="cmd.automoderate.automod.scan_flagged_users.param.user"))
    async def scan_flagged_users(self, interaction: discord.Interaction, user: discord.User = None):
        await interaction.response.defer(ephemeral=True)
        feature_config = _normalize_flagged_user_config(interaction.guild.id)
        member_ids = {user.id} if user else {member.id for member in interaction.guild.members}
        try:
            local_records = _load_local_flagged_records(
                member_ids,
                match_mode=feature_config["local_match_mode"],
            )
            local_error = None
        except (sqlite3.Error, OSError) as error:
            local_records = {}
            local_error = type(error).__name__

        matched_ids = member_ids & (set(local_records) | set(self._blacklist_cache))
        status_lines = [
            t("automoderate.scan.local_data",
              status=t("automoderate.scan.local_read_failed", error=local_error) if local_error
              else t("automoderate.scan.local_ok", mode=feature_config["local_match_mode"])),
            t("automoderate.scan.api_cache",
              status=t("automoderate.scan.api_last_sync", at=self._blacklist_last_success.astimezone(timezone.utc).isoformat())
              if self._blacklist_last_success else t("automoderate.scan.api_never_synced")),
            t("automoderate.scan.api_status",
              status=self._blacklist_last_error
              or (t("automoderate.scan.api_ok") if self._blacklist_last_success else t("automoderate.scan.api_no_result"))),
            "",
        ]
        for user_id in sorted(matched_ids):
            member = interaction.guild.get_member(user_id)
            status_lines.append(t("automoderate.scan.user_line",
                                  user=member.name if member else t("automoderate.unknown_user"),
                                  user_id=user_id))
            for entry in local_records.get(user_id, []):
                active_text = t("automoderate.scan.flag_active") if entry["flagged_role"] else t("automoderate.scan.flag_history")
                status_lines.append(
                    t("automoderate.scan.local_line", kind=active_text,
                      guild=entry["guild_name"], at=entry["flagged_at"])
                )
            for entry in self._blacklist_cache.get(user_id, []):
                status_lines.append(
                    t("automoderate.scan.api_line", reason=entry["reason"],
                      reporter=entry["reporter_name"], reporter_id=entry["reporter_id"],
                      at=entry["reported_at"])
                )
            status_lines.append("")

        if not matched_ids:
            await interaction.followup.send("\n".join(status_lines) + t("automoderate.scan.no_matches"), ephemeral=True)
            return
        file = discord.File(io.StringIO("\n".join(status_lines)), filename="flagged_users.txt")
        await interaction.followup.send(content=t("automoderate.scan.matches_found", count=len(matched_ids)), file=file, ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("flagged-user-alert-channel", i18n_key="cmd.automoderate.automod.flagged_user_alert_channel.name"), description=app_commands.locale_str("Set the notification channel for when users join the server", i18n_key="cmd.automoderate.automod.flagged_user_alert_channel.desc"))
    @app_commands.describe(channel=app_commands.locale_str("Channel that receives user-join notifications", i18n_key="cmd.automoderate.automod.flagged_user_alert_channel.param.channel"))
    async def set_flagged_user_onjoin_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages):
            await interaction.response.send_message(t("automoderate.err.channel_no_perms", channel=channel.mention), ephemeral=True)
            return
        automod_settings = get_server_config(interaction.guild.id, "automod", {})
        if not isinstance(automod_settings, dict):
            automod_settings = {}
        flagged_user = _ensure_flagged_user_settings(automod_settings, interaction.guild.id)
        flagged_user.update({
            "enabled": True,
            "log_channel": str(channel.id),
            "action": str(flagged_user.get("action", "") or ""),
            "action_source": str(flagged_user.get("action_source", "both") or "both"),
            "local_match_mode": str(flagged_user.get("local_match_mode", "active") or "active"),
        })
        set_server_config(interaction.guild.id, "automod", automod_settings)
        await interaction.response.send_message(t("automoderate.msg.alert_channel_set", channel=channel.mention))
    
    @app_commands.command(name=app_commands.locale_str("info", i18n_key="cmd.automoderate.automod.info.name"), description=app_commands.locale_str("View an introduction to auto-moderation features", i18n_key="cmd.automoderate.automod.info.desc"))
    async def automod_info(self, interaction: discord.Interaction):
        embed = discord.Embed(title=t("automoderate.info.title"), color=0x5865F2)
        embed.description = t(
            "automoderate.info.desc",
            quick_setup=await get_command_mention('automod', 'quick-setup'),
            setup=await get_command_mention('automod', 'setup'),
            toggle=await get_command_mention('automod', 'toggle'),
            settings=await get_command_mention('automod', 'settings'),
            view=await get_command_mention('automod', 'view'),
        )
        for feature in ("scamtrap", "escape_punish", "too_many_h1", "too_many_emojis",
                        "anti_invite_link", "anti_uispam", "anti_raid", "anti_spam",
                        "automod_detect", "flagged_user"):
            embed.add_field(
                name=t("automoderate.info.feature_heading",
                       name=t_enum("automoderate.feature_name_emoji", feature), key=feature),
                value=t_enum("automoderate.info.feature_body", feature),
                inline=False,
            )
        embed.add_field(
            name=t("automoderate.info.action_syntax_title"),
            value=t("automoderate.info.action_syntax_body",
                    builder=await get_command_mention('automod', 'action-builder'),
                    check=await get_command_mention('automod', 'check-action')),
            inline=False
        )
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_automod_action(self, execution: discord.AutoModAction):
        """偵測 Discord 原生 AutoMod 規則被觸發（listener 需顯式開 i18n scope）"""
        guild = execution.guild
        if not guild:
            return
        async with i18n.guild_scope(guild.id):
            await self._on_automod_action_impl(execution)

    async def _on_automod_action_impl(self, execution: discord.AutoModAction):
        guild = execution.guild
        guild_id = guild.id
        automod_settings = get_server_config(guild_id, "automod", {})
        if not automod_settings.get("automod_detect", {}).get("enabled", False):
            return

        log_channel_id = automod_settings["automod_detect"].get("log_channel")
        action = automod_settings["automod_detect"].get("action")

        # 取得觸發規則的用戶
        member = guild.get_member(execution.user_id)
        user_mention = member.mention if member else f"<@{execution.user_id}>"

        # 取得規則資訊
        rule_name = t("automoderate.detect.unknown_rule")
        try:
            rule = await guild.fetch_automod_rule(execution.rule_id)
            rule_name = rule.name
        except Exception:
            pass

        # 觸發類型對應名稱
        trigger_type_names = {
            discord.AutoModRuleTriggerType.keyword: "keyword",
            discord.AutoModRuleTriggerType.harmful_link: "harmful_link",
            discord.AutoModRuleTriggerType.spam: "spam",
            discord.AutoModRuleTriggerType.keyword_preset: "keyword_preset",
            discord.AutoModRuleTriggerType.mention_spam: "mention_spam",
            discord.AutoModRuleTriggerType.member_profile: "member_profile",
        }
        trigger_key = trigger_type_names.get(execution.rule_trigger_type)
        trigger_type_str = t_enum("automoderate.trigger_type", trigger_key) if trigger_key else str(execution.rule_trigger_type)

        # 執行動作類型對應名稱
        action_type_names = {
            discord.AutoModRuleActionType.block_message: "block_message",
            discord.AutoModRuleActionType.send_alert_message: "send_alert_message",
            discord.AutoModRuleActionType.timeout: "timeout",
            discord.AutoModRuleActionType.block_member_interactions: "block_member_interactions",
        }
        action_key = action_type_names.get(execution.action.type)
        executed_action_str = t_enum("automoderate.automod_action_type", action_key) if action_key else str(execution.action.type)

        # 頻道資訊
        channel_mention = f"<#{execution.channel_id}>" if execution.channel_id else t("automoderate.detect.unknown_channel")

        # 建立通知 embed
        embed = discord.Embed(
            title=t("automoderate.detect.title"),
            color=0xED4245,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name=t("automoderate.field.user"), value=f"{user_mention} (ID: {execution.user_id})", inline=True)
        embed.add_field(name=t("automoderate.field.rule_name"), value=rule_name, inline=True)
        embed.add_field(name=t("automoderate.field.trigger_type"), value=trigger_type_str, inline=True)
        embed.add_field(name=t("automoderate.field.executed_action"), value=executed_action_str, inline=True)
        embed.add_field(name=t("automoderate.field.channel"), value=channel_mention, inline=True)
        if execution.matched_keyword:
            embed.add_field(name=t("automoderate.field.matched_keyword"), value=f"`{execution.matched_keyword}`", inline=True)
        if execution.matched_content:
            embed.add_field(name=t("automoderate.field.matched_content"), value=execution.matched_content[:200], inline=False)
        if execution.content:
            embed.add_field(name=t("automoderate.field.message_content"), value=execution.content[:500], inline=False)

        # 傳送通知到指定頻道
        if log_channel_id:
            log_channel = guild.get_channel(int(log_channel_id))
            if log_channel:
                try:
                    await log_channel.send(embed=embed)
                except Exception as e:
                    log(f"Failed to send the AutoMod detection notice to channel {log_channel_id}: {e}", level=logging.ERROR, module_name="AutoModerate", guild=guild)

        log(f"AutoMod rule '{rule_name}' triggered by user {execution.user_id} (trigger: {trigger_type_str}, action: {executed_action_str})", module_name="AutoModerate", guild=guild)

        # 如果有設定額外處置動作，先檢查過濾條件是否符合
        if action and member:
            # 規則名稱過濾
            filter_rule = automod_settings["automod_detect"].get("filter_rule", "")
            if filter_rule:
                allowed_rules = [r.strip() for r in filter_rule.split("|") if r.strip()]
                if allowed_rules and rule_name not in allowed_rules:
                    log(f"AutoMod detection: rule '{rule_name}' is not in the filter list {allowed_rules}; skipping the extra action.", module_name="AutoModerate", guild=guild)
                    return

            # 動作類型過濾
            filter_action_type = automod_settings["automod_detect"].get("filter_action_type", "")
            if filter_action_type:
                action_type_map = {
                    "block": discord.AutoModRuleActionType.block_message,
                    "alert": discord.AutoModRuleActionType.send_alert_message,
                    "timeout": discord.AutoModRuleActionType.timeout,
                    "block_interactions": discord.AutoModRuleActionType.block_member_interactions,
                }
                allowed_types = [t.strip() for t in filter_action_type.split("|") if t.strip()]
                matched = any(action_type_map.get(t) == execution.action.type for t in allowed_types)
                if allowed_types and not matched:
                    log(f"AutoMod detection: action type '{executed_action_str}' is not in the filter list {allowed_types}; skipping the extra action.", module_name="AutoModerate", guild=guild)
                    return

            try:
                result = await do_action_str(action, guild=guild, user=member)
                res = '\n'.join(result)
                log(f"AutoMod detection extra action: {action}\nResult: {res}", module_name="AutoModerate", guild=guild)
            except Exception as e:
                log(f"Failed to run the AutoMod detection extra action on {member}: {e}", level=logging.ERROR, module_name="AutoModerate", guild=guild)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # listener 不在 choke point 內，顯式開伺服器語言 scope
        async with i18n.guild_scope(member.guild.id):
            await self._on_member_join_impl(member)

    async def _on_member_join_impl(self, member: discord.Member):
        guild_id = member.guild.id

        # 防突襲檢查
        automod_settings = get_server_config(guild_id, "automod", {})
        if automod_settings.get("anti_raid", {}).get("enabled", False):
            max_joins = int(automod_settings["anti_raid"].get("max_joins", 5))
            time_window = int(automod_settings["anti_raid"].get("time_window", 60))
            action = automod_settings["anti_raid"].get("action") or ("kick " + t("automoderate.default_reason.raid"))
            
            now = datetime.now(timezone.utc)
            join_list = _raid_tracker.setdefault(guild_id, [])
            join_list.append((member, now))
            
            # 清除過期的記錄
            join_list[:] = [(m, t) for m, t in join_list if (now - t).total_seconds() < time_window]
            
            if len(join_list) >= max_joins:
                # 觸發 raid 偵測，對所有在時間窗口內加入的用戶執行動作
                raid_members = [m for m, t in join_list]
                log(f"Raid detected! {len(raid_members)} users joined within {time_window}s; starting to handle them.", module_name="AutoModerate", guild=member.guild)
                for raid_member in raid_members:
                    try:
                        await do_action_str(action, guild=member.guild, user=raid_member)
                        log(f"Raid user {raid_member} handled: {action}", module_name="AutoModerate", user=raid_member, guild=member.guild)
                    except Exception as e:
                        log(f"Failed to handle raid user {raid_member}: {e}", level=logging.ERROR, module_name="AutoModerate", user=raid_member, guild=member.guild)
                # 重置追蹤器避免重複處罰
                join_list.clear()

        flagged_config = _normalize_flagged_user_config(guild_id)
        if not flagged_config["enabled"]:
            return
        try:
            local_results = _load_local_flagged_records(
                {member.id},
                match_mode=flagged_config["local_match_mode"],
            ).get(member.id, [])
        except (sqlite3.Error, OSError) as error:
            local_results = []
            log(
                f"Failed to read local flagged records: {type(error).__name__}: {error}",
                level=logging.ERROR,
                module_name="AutoModerate",
                user=member,
                guild=member.guild,
            )
        api_results = list(self._blacklist_cache.get(member.id, []))
        if not local_results and not api_results:
            return

        action_result = t("automoderate.flagged.no_action_configured")
        action = flagged_config["action"]
        should_act = bool(action) and _action_matches_sources(
            flagged_config["action_source"],
            has_local=bool(local_results),
            has_api=bool(api_results),
        )
        if should_act:
            analysis = Moderate.analyze_member_join_action(action, guild_id)
            if not analysis["valid"] or analysis["requires_confirmation"]:
                action_result = t("automoderate.flagged.action_not_applicable")
                log(
                    f"Flagged-user join action config is invalid: {analysis.get('error') or analysis.get('confirmation')}",
                    level=logging.ERROR,
                    module_name="AutoModerate",
                    user=member,
                    guild=member.guild,
                )
            else:
                try:
                    result_lines = await do_action_str(action, guild=member.guild, user=member)
                    action_result = "\n".join(result_lines) or t("automoderate.flagged.action_done")
                    log(
                        f"Flagged-user join action complete: {action_result}",
                        module_name="AutoModerate",
                        user=member,
                        guild=member.guild,
                    )
                except Exception as error:
                    action_result = t("automoderate.flagged.action_failed", error=type(error).__name__)
                    log(
                        f"Flagged-user join action failed: {error}",
                        level=logging.ERROR,
                        module_name="AutoModerate",
                        user=member,
                        guild=member.guild,
                    )
        elif action:
            action_result = t("automoderate.flagged.source_mismatch", source=flagged_config["action_source"])

        embed = discord.Embed(title=t("automoderate.flagged.title"), color=0xff0000)
        embed.add_field(name=t("automoderate.field.user"), value=f"{member.mention} (ID: {member.id})", inline=False)
        if local_results:
            embed.add_field(
                name=t("automoderate.flagged.local_records", count=len(local_results)),
                value=_format_embed_record_lines([
                    t("automoderate.flagged.local_entry",
                      guild=result["guild_name"], at=result["flagged_at"],
                      kind=t("automoderate.scan.flag_active") if result["flagged_role"] else t("automoderate.scan.flag_history"))
                    for result in local_results
                ]),
                inline=False,
            )
        if api_results:
            embed.add_field(
                name=t("automoderate.flagged.api_records", count=len(api_results)),
                value=_format_embed_record_lines([
                    t("automoderate.flagged.api_entry", reason=result["reason"],
                      reporter=result["reporter_name"], reporter_id=result["reporter_id"],
                      at=result["reported_at"])
                    for result in api_results
                ]),
                inline=False,
            )
        embed.add_field(name=t("automoderate.flagged.action_result"), value=action_result[:1024], inline=False)

        channel_id = flagged_config["log_channel"]
        try:
            channel = member.guild.get_channel(int(channel_id)) if channel_id else None
        except (TypeError, ValueError):
            channel = None
        if channel is None:
            log(
                f"Flagged-user notification channel is invalid: {channel_id}",
                level=logging.ERROR,
                module_name="AutoModerate",
                user=member,
                guild=member.guild,
            )
            return
        try:
            await channel.send(embed=embed)
        except Exception as error:
            log(
                f"Failed to send the flagged-user join notice to channel {channel_id}: {error}",
                level=logging.ERROR,
                module_name="AutoModerate",
                user=member,
                guild=member.guild,
            )
                

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not after.guild:
            return
        if not after.timed_out_until:
            return
        set_user_data(guild_id=after.guild.id, user_id=after.id, key="communication_disabled_until", value=after.timed_out_until.isoformat() if after.timed_out_until else None)
        
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not member.guild:
            return
        async with i18n.guild_scope(member.guild.id):
            await self._on_member_remove_impl(member)

    async def _on_member_remove_impl(self, member: discord.Member):
        server_config = get_server_config(member.guild.id, "automod", {})
        if not server_config.get("escape_punish", {}).get("enabled", False):
            return
        communication_disabled_until = get_user_data(guild_id=member.guild.id, user_id=member.id, key="communication_disabled_until")
        if communication_disabled_until:
            communication_disabled_until = datetime.fromisoformat(communication_disabled_until)
            if communication_disabled_until > datetime.now(timezone.utc):
                # 用戶在禁言期間離開，進行懲處
                punishment = server_config["escape_punish"].get("punishment", "ban")
                duration = server_config["escape_punish"].get("duration", "0")
                duration_seconds = Moderate.timestr_to_seconds(duration) if Moderate else 0
                try:
                    if punishment == "ban":
                        if Moderate:
                            await Moderate.ban_user(member.guild, member, reason=t("automoderate.default_reason.escape_punish"), duration=duration_seconds if duration_seconds > 0 else 0)
                        else:
                            print("[!] Moderate module not loaded, cannot ban user.")
                            raise Exception("Moderate module not loaded")
                    # 好像也就只有 ban 可以用了，我在做什麼呀
                    print(f"[+] {member} was {punishment}ed for evading a timeout")
                except Exception as e:
                    print(f"[!] Failed to punish {member}: {e}")
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        async with i18n.guild_scope(message.guild.id, user_id=message.author.id):
            await self._on_message_impl(message)

    async def _on_message_impl(self, message: discord.Message):
        guild_id = message.guild.id
        message_channel_id = message.channel.id
        automod_settings = get_server_config(guild_id, "automod", {})
        
        # 用戶安裝應用程式濫用檢查（需在 bot 訊息過濾之前，因為 user install 的訊息作者是 bot）
        is_user_install_message = (
            message.interaction_metadata is not None 
            and message.interaction_metadata.is_user_integration()
            and not message.interaction_metadata.is_guild_integration()
        )
        
        anti_uispam_settings = automod_settings.get("anti_uispam", {})
        if is_user_install_message and anti_uispam_settings.get("enabled", False):
            triggering_user = message.interaction_metadata.user
            member = message.guild.get_member(triggering_user.id)
            if member and not member.guild_permissions.administrator and not _is_ignored_channel(anti_uispam_settings, message_channel_id):
                max_count = int(anti_uispam_settings.get("max_count", 5))
                time_window = int(anti_uispam_settings.get("time_window", 60))
                action = anti_uispam_settings.get("action") or t("automoderate.default_action.uispam")
                
                now = datetime.now(timezone.utc)
                guild_tracker = _uispam_tracker.setdefault(guild_id, {})
                user_timestamps = guild_tracker.setdefault(triggering_user.id, [])
                
                # 清除過期的時間戳
                user_timestamps[:] = [ts for ts in user_timestamps if (now - ts).total_seconds() < time_window]
                
                # 記錄本次觸發
                user_timestamps.append(now)
                
                if len(user_timestamps) > max_count:
                    try:
                        target_member = member or triggering_user
                        await do_action_str(action, guild=message.guild, user=target_member, message=message)
                        log(f"{triggering_user} was handled for user-install app abuse ({len(user_timestamps)} triggers within {time_window}s): {action}", module_name="AutoModerate", user=triggering_user, guild=message.guild)
                        # 重置計數器避免重複處罰
                        user_timestamps.clear()
                    except Exception as e:
                        log(f"Failed to handle user-install app abuse for {triggering_user}: {e}", level=logging.ERROR, module_name="AutoModerate", user=triggering_user, guild=message.guild)
        
        
        # 詐騙陷阱檢查
        if automod_settings.get("scamtrap", {}).get("enabled", False):
            scamtrap_channel_id = int(automod_settings["scamtrap"].get("channel_id", 0))
            action = automod_settings["scamtrap"].get("action") or t("automoderate.default_action.scamtrap")
            if scamtrap_channel_id != 0 and message.channel.id == scamtrap_channel_id:
                target = message.author
                if message.author.bot:
                    if message.interaction_metadata:
                        target = message.interaction_metadata.user
                    else:
                        await asyncio.sleep(.5)  # 等待一秒以防訊息作者資訊尚未更新
                        if message.id not in Moderate.ignore_message_ids:
                            await message.delete()
                        return
                ok, msg = Moderate.check_member_hierarchy(message.guild.me, target, message.guild.me)
                if not ok:
                    log(f"Scam trap: can't act on {target} due to insufficient hierarchy: {msg}", level=logging.ERROR, module_name="AutoModerate", user=target, guild=message.guild)
                    return
                try:
                    result = await do_action_str(action, guild=message.guild, user=target, message=message)
                    res = '\n'.join(result)
                    # print(f"[+] 用戶 {message.author} 因進入詐騙陷阱頻道被處理: {action}")
                    log(f"{target} was handled for entering the scam trap channel: {action}\nResult: {res}", module_name="AutoModerate", user=target, guild=message.guild)
                except Exception as e:
                    log(f"Failed to run the scam trap action on {target}: {e}", level=logging.ERROR, module_name="AutoModerate", user=target, guild=message.guild)

        if message.author.bot:
            return
        if message.author.guild_permissions.administrator:
            return

        # 邀請連結檢查
        anti_invite_settings = automod_settings.get("anti_invite_link", {})
        if anti_invite_settings.get("enabled", False) and not _is_ignored_channel(anti_invite_settings, message_channel_id):
            allow_current_server = _is_truthy(anti_invite_settings.get("allow_current_server", False))
            action = anti_invite_settings.get("action") or t("automoderate.default_action.invite_link")
            external_invite_codes = await _get_external_invite_codes(message, guild_id, allow_current_server)
            if external_invite_codes:
                try:
                    await do_action_str(action, guild=message.guild, user=message.author, message=message)
                    log(
                        f"{message.author} was handled for posting an invite link (allow own server: {allow_current_server}, codes: {', '.join(external_invite_codes)}): {action}",
                        module_name="AutoModerate",
                        user=message.author,
                        guild=message.guild,
                    )
                except Exception as e:
                    log(
                        f"Failed to run the invite-link action on {message.author}: {e}",
                        level=logging.ERROR,
                        module_name="AutoModerate",
                        user=message.author,
                        guild=message.guild,
                    )

        # 標題過多檢查
        too_many_h1_settings = automod_settings.get("too_many_h1", {})
        if too_many_h1_settings.get("enabled", False) and not _is_ignored_channel(too_many_h1_settings, message_channel_id):
            max_length = int(too_many_h1_settings.get("max_length", 20))
            action = too_many_h1_settings.get("action", "warn")
            h1_count = 0
            split_lines = message.content.split("\n")
            for line in split_lines:
                line = line.lstrip()
                if line.startswith("# "):
                    # find custom emoji and replace with single character
                    while re.search(r'<a?:\w+:\d+>', line):
                        line = re.sub(r'<a?:\w+:\d+>', 'E', line, count=1)
                    line = line[2:]
                    h1_count += len(line)
            if h1_count > max_length:
                try:
                    await do_action_str(action, guild=message.guild, user=message.author, message=message)
                    # print(f"[+] 用戶 {message.author} 因標題長度過長被處理: {action}")
                    log(f"{message.author} was handled for overly long headings: {action}", module_name="AutoModerate", user=message.author, guild=message.guild)
                except Exception as e:
                    log(f"Failed to run the too-many-headings action on {message.author}: {e}", level=logging.ERROR, module_name="AutoModerate", user=message.author, guild=message.guild)
        
        # 表情符號過多檢查
        too_many_emojis_settings = automod_settings.get("too_many_emojis", {})
        if too_many_emojis_settings.get("enabled", False) and not _is_ignored_channel(too_many_emojis_settings, message_channel_id):
            max_emojis = int(too_many_emojis_settings.get("max_emojis", 10))
            action = too_many_emojis_settings.get("action", "warn")
            emoji_count = len(re.findall(r'<a?:\w+:\d+>', message.content))
            emoji_count += len([c for c in message.content if emoji.is_emoji(c)])
            if emoji_count > max_emojis:
                try:
                    await do_action_str(action, guild=message.guild, user=message.author, message=message)
                    log(f"{message.author} was handled for too many emojis: {action}", module_name="AutoModerate", user=message.author, guild=message.guild)
                except Exception as e:
                    log(f"Failed to run the too-many-emojis action on {message.author}: {e}", level=logging.ERROR, module_name="AutoModerate", user=message.author, guild=message.guild)
        
        # 刷頻偵測檢查
        anti_spam_settings = automod_settings.get("anti_spam", {})
        if anti_spam_settings.get("enabled", False) and not _is_ignored_channel(anti_spam_settings, message_channel_id):
            max_messages = int(anti_spam_settings.get("max_messages", 5))
            time_window = int(anti_spam_settings.get("time_window", 30))
            similarity_threshold = int(anti_spam_settings.get("similarity", 75)) / 100.0
            action = anti_spam_settings.get("action") or t("automoderate.default_action.spam")
            
            now = datetime.now(timezone.utc)
            content = message.content.strip()
            guild_spam = _spam_tracker.setdefault(guild_id, {})
            user_history = guild_spam.setdefault(message.author.id, [])
            
            # 清除過期的記錄
            user_history[:] = [(c, t) for c, t in user_history if (now - t).total_seconds() < time_window]
            
            # 記錄本次訊息
            user_history.append((content, now))
            
            # 檢查是否有足夠多的相似訊息
            if len(user_history) >= max_messages:
                # 計算相似訊息數量：與最新訊息比較
                similar_count = 0
                for old_content, _ in user_history[:-1]:
                    if content == old_content or _text_similarity(content, old_content) >= similarity_threshold:
                        similar_count += 1
                
                # 如果相似訊息數 >= max_messages - 1（加上自身就是 >= max_messages）
                if similar_count >= max_messages - 1:
                    try:
                        await do_action_str(action, guild=message.guild, user=message.author, message=message)
                        log(f"{message.author} was handled for spamming ({similar_count + 1} similar messages within {time_window}s): {action}", module_name="AutoModerate", user=message.author, guild=message.guild)
                        # 重置計數器避免重複處罰
                        user_history.clear()
                    except Exception as e:
                        log(f"Failed to run the anti-spam action on {message.author}: {e}", level=logging.ERROR, module_name="AutoModerate", user=message.author, guild=message.guild)

asyncio.run(bot.add_cog(AutoModerate(bot)))

if __name__ == "__main__":
    start_bot()
