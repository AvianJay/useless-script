from globalenv import bot, start_bot, get_server_config, set_server_config, get_user_data, set_user_data, on_ready_tasks, config, modules, get_command_mention
import discord
from discord import app_commands
from discord.ext import commands
import json
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone
from typing import Union, Optional
import ModerationNotify
from logger import log
import logging
import re
import shlex
import string
import time
from collections import defaultdict
from embed_template import (
    EmbedTemplateSyntaxError,
    build_embed_from_tokens,
    extract_embed_tokens,
    parse_embed_color,
    validate_embed_output,
    validate_embed_template,
)

import i18n
from i18n import t, t_enum

ignore_message_ids = set()  # 用於暫時忽略特定訊息的處理（例如剛剛被刪除的訊息）
BUILTIN_ACTIONS = {
    "ban", "kick", "mute", "timeout", "to", "unban", "unmute", "untimeout",
    "delete", "warn", "send_mod_message", "smm", "force_verify"
}
REPLY_ACTION_ALIASES = {f"{action}r": action for action in BUILTIN_ACTIONS}

# 動作指令輸入建議：label 與範例原因是顯示用（在地化），DSL 動作詞（delete/mute/...）
# 與 {user} 佔位符是永久語法，值模板存在語言檔中、由 t() 的 _SafeDict 保留不變。
ACTION_INPUT_SUGGESTION_KEYS = [
    ("moderate.suggest.delete", "moderate.suggest.value.delete"),
    ("moderate.suggest.delete_warn", "moderate.suggest.value.delete_warn"),
    ("moderate.suggest.warn", "moderate.suggest.value.warn"),
    ("moderate.suggest.mute_10m", "moderate.suggest.value.mute_10m"),
    ("moderate.suggest.to_10m", "moderate.suggest.value.to_10m"),
    ("moderate.suggest.mute_1h", "moderate.suggest.value.mute_1h"),
    ("moderate.suggest.kick", "moderate.suggest.value.kick"),
    ("moderate.suggest.ban_perm", "moderate.suggest.value.ban_perm"),
    ("moderate.suggest.ban_1d", "moderate.suggest.value.ban_1d"),
    ("moderate.suggest.force_verify_1d", "moderate.suggest.value.force_verify_1d"),
    ("moderate.suggest.smm", "moderate.suggest.value.smm"),
]


def _action_input_suggestions() -> list[tuple[str, str]]:
    """回傳 (顯示 label, 範例動作字串) 清單；DSL 動作詞與 {user} 佔位符不變，其餘在地化。"""
    reason = t("moderate.suggest.sample_reason")
    return [(t(label_key), t(value_key, reason=reason)) for label_key, value_key in ACTION_INPUT_SUGGESTION_KEYS]

_DURATION_TOKEN_RE = re.compile(
    r"^(?:0|(?:\d+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?|s|m|h|d|w|M|y|秒|分|分鐘|小時|天|週|月|個月|年))+)$"
)
MAX_TIMEOUT_SECONDS = 28 * 24 * 60 * 60
MODERATION_ANNOUNCEMENT_CONFIG_KEY = "moderation_announcement_config"
MODERATION_CASE_STATE_KEY = "moderation_case_state"
DEFAULT_MODERATION_CASE_ID_FORMAT = "{roc_year}{sequence:04d}"
MODERATION_TEMPLATE_VARIABLES = {
    "user",
    "user_name",
    "user_id",
    "user_avatar",
    "moderator",
    "moderator_name",
    "moderator_id",
    "moderator_avatar",
    "reason",
    "action",
    "case_id",
    "guild",
    "guild_id",
    "guild_icon",
    "reported_message",
    "report_context",
    "ai_note",
}
TAIPEI_TIMEZONE = timezone(timedelta(hours=8))
_case_id_locks = defaultdict(asyncio.Lock)
_LEGACY_CASE_ID_RE = re.compile(r"裁判字號\s*[：:]\s*(?P<roc_year>\d{3})(?P<sequence>\d{4,9})(?!\d)")


def timestr_to_seconds(timestr: str) -> int:
    """將時間字串轉換為秒數"""
    units = {
        's': 1,
        'second': 1,
        'seconds': 1,
        '秒': 1,
        'm': 60,
        'minute': 60,
        'minutes': 60,
        '分': 60,
        '分鐘': 60,
        'h': 3600,
        'hour': 3600,
        'hours': 3600,
        '小時': 3600,
        'd': 86400,
        'day': 86400,
        'days': 86400,
        '天': 86400,
        'w': 604800,
        'week': 604800,
        'weeks': 604800,
        '週': 604800,
        'M': 2592000,  # 假設一個月30天
        'month': 2592000,
        'months': 2592000,
        '月': 2592000,
        '個月': 2592000,
        'y': 31536000, # 假設一年365天
        'year': 31536000,
        'years': 31536000,
        '年': 31536000,
    }
    text = str(timestr or '').strip()
    if not text:
        return 0

    unit_pattern = '|'.join(sorted((re.escape(unit) for unit in units), key=len, reverse=True))
    part_pattern = re.compile(rf'(\d+)\s*({unit_pattern})?')
    total_seconds = 0
    position = 0
    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        match = part_pattern.match(text, position)
        if match is None:
            return 0
        amount = int(match.group(1))
        unit = match.group(2)
        if unit is None and match.end() != len(text):
            return 0
        total_seconds += amount * (units[unit] if unit else 1)
        position = match.end()
    return total_seconds


def get_time_text(seconds: int, *, locale: str | None = None) -> str:
    parts = []
    while seconds != 0:
        if seconds < 60:
            parts.append(i18n.tn("common.unit.seconds", seconds, locale=locale))
            seconds = 0
        elif seconds < 3600:
            parts.append(i18n.tn("common.unit.minutes", seconds // 60, locale=locale))
            seconds = seconds % 60
        elif seconds < 86400:
            parts.append(i18n.tn("common.unit.hours", seconds // 3600, locale=locale))
            seconds = seconds % 3600
        else:
            parts.append(i18n.tn("common.unit.days", seconds // 86400, locale=locale))
            seconds = seconds % 86400
    return " ".join(parts)


def guess_role(guild: discord.Guild, role_name: str):
    # try to find role by id
    try:
        role_id = int(role_name)
        role = guild.get_role(role_id)
        if role is not None:
            return role.id
    except ValueError:
        pass
    # try to find role by mention
    if role_name.startswith("<@&") and role_name.endswith(">"):
        try:
            role_id = int(role_name[3:-1])
            role = guild.get_role(role_id)
            if role is not None:
                return role.id
        except ValueError:
            pass
    # try to find role by name
    for role in guild.roles:
        if role.name == role_name:
            return role.id
    # try to find role by case insensitive name
    for role in guild.roles:
        if role.name.lower() == role_name.lower():
            return role.id
    # try to find role by partial name
    for role in guild.roles:
        if role_name in role.name:
            return role.id
    return None


def _taipei_now() -> datetime:
    return datetime.now(TAIPEI_TIMEZONE)


def current_roc_year() -> int:
    return _taipei_now().year - 1911


def default_moderation_announcement_config(*, locale: str | None = None) -> dict[str, str]:
    return {
        "template": t("moderate.default_announcement_template", locale=locale),
        "case_id_format": DEFAULT_MODERATION_CASE_ID_FORMAT,
    }


def _parse_case_id_format(case_id_format: str) -> list[tuple[str, str | None, str, str | None]]:
    if not isinstance(case_id_format, str):
        raise ValueError(t("moderate.err.case_id_format_not_string"))
    case_id_format = case_id_format.strip()
    if not case_id_format:
        raise ValueError(t("moderate.err.case_id_format_empty"))
    if len(case_id_format) > 100:
        raise ValueError(t("moderate.err.case_id_format_too_long"))

    try:
        parsed = list(string.Formatter().parse(case_id_format))
    except ValueError as error:
        raise ValueError(t("moderate.err.case_id_format_invalid", error=error)) from error

    fields = []
    for _, field_name, format_spec, conversion in parsed:
        if field_name is None:
            continue
        if field_name not in {"year", "roc_year", "sequence"}:
            raise ValueError(t("moderate.err.case_id_format_unsupported_var", field=field_name))
        if conversion is not None:
            raise ValueError(t("moderate.err.case_id_format_no_conversion"))
        if field_name == "sequence":
            if format_spec not in {"", "d"} and not re.fullmatch(r"0[1-9]d", format_spec):
                raise ValueError(t("moderate.err.case_id_format_sequence_spec"))
        elif format_spec not in {"", "d"}:
            raise ValueError(t("moderate.err.case_id_format_field_spec", field=field_name))
        fields.append(field_name)
    if "sequence" not in fields:
        raise ValueError(t("moderate.err.case_id_format_missing_sequence"))
    return parsed


def format_case_id(case_id_format: str, year: int, sequence: int) -> str:
    _parse_case_id_format(case_id_format)
    try:
        return case_id_format.format(
            year=int(year),
            roc_year=int(year) - 1911,
            sequence=int(sequence),
        )
    except (KeyError, ValueError) as error:
        raise ValueError(t("moderate.err.case_id_generate_failed", error=error)) from error


def _compile_case_id_regex(case_id_format: str) -> re.Pattern:
    parsed = _parse_case_id_format(case_id_format)
    pattern_parts = []
    seen_fields = set()
    for literal, field_name, format_spec, _ in parsed:
        pattern_parts.append(re.escape(literal))
        if field_name is None:
            continue
        if field_name in seen_fields:
            pattern_parts.append(f"(?P={field_name})")
            continue
        seen_fields.add(field_name)
        if field_name == "year":
            field_pattern = r"\d{4}"
        elif field_name == "roc_year":
            field_pattern = r"\d{3,4}"
        else:
            width_match = re.fullmatch(r"0([1-9])d", format_spec or "")
            minimum_width = int(width_match.group(1)) if width_match else 1
            field_pattern = rf"\d{{{minimum_width},9}}"
        pattern_parts.append(f"(?P<{field_name}>{field_pattern})")
    return re.compile(r"(?<!\d)" + "".join(pattern_parts) + r"(?!\d)")


def normalize_moderation_announcement_config(value, *, locale: str | None = None) -> dict[str, str]:
    defaults = default_moderation_announcement_config(locale=locale)
    raw = value if isinstance(value, dict) else {}
    template = raw.get("template", defaults["template"])
    case_id_format = raw.get("case_id_format", defaults["case_id_format"])
    template = str(template if template is not None else defaults["template"])
    case_id_format = str(case_id_format if case_id_format is not None else defaults["case_id_format"]).strip()
    if not template.strip():
        raise ValueError(t("moderate.err.announcement_template_empty", locale=locale))
    if len(template) > 4000:
        raise ValueError(t("moderate.err.announcement_template_too_long", locale=locale))
    try:
        validate_embed_template(template, MODERATION_TEMPLATE_VARIABLES)
    except EmbedTemplateSyntaxError as error:
        raise ValueError(t("moderate.err.announcement_template_invalid", locale=locale, error=error)) from error
    _parse_case_id_format(case_id_format)
    _, extracted = extract_embed_tokens(template)
    if len(extracted["fields"]) > 25:
        raise ValueError(t("moderate.err.announcement_template_too_many_fields", locale=locale))
    return {"template": template, "case_id_format": case_id_format}


def get_moderation_announcement_config(guild_id: int) -> dict[str, str]:
    guild_locale = i18n.resolve_locale(guild_id=guild_id)
    raw = get_server_config(guild_id, MODERATION_ANNOUNCEMENT_CONFIG_KEY, {})
    try:
        return normalize_moderation_announcement_config(raw, locale=guild_locale)
    except ValueError:
        return default_moderation_announcement_config(locale=guild_locale)


def _message_case_texts(message: discord.Message) -> list[str]:
    texts = [str(getattr(message, "content", "") or "")]
    for embed in getattr(message, "embeds", []) or []:
        data = embed.to_dict()
        texts.extend(
            str(value)
            for value in (
                data.get("title"),
                data.get("description"),
                data.get("author", {}).get("name"),
                data.get("footer", {}).get("text"),
            )
            if value
        )
        for field in data.get("fields", []):
            if field.get("name"):
                texts.append(str(field["name"]))
            if field.get("value"):
                texts.append(str(field["value"]))
    return texts


def _case_candidates_from_message(message: discord.Message, case_id_format: str) -> set[tuple[int, int]]:
    format_regex = _compile_case_id_regex(case_id_format)
    created_at = getattr(message, "created_at", _taipei_now())
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    message_year = created_at.astimezone(TAIPEI_TIMEZONE).year
    candidates = set()

    for text in _message_case_texts(message):
        legacy_matches = list(_LEGACY_CASE_ID_RE.finditer(text))
        if case_id_format == DEFAULT_MODERATION_CASE_ID_FORMAT and legacy_matches:
            matches = legacy_matches
            legacy_only = True
        else:
            matches = list(format_regex.finditer(text))
            legacy_only = False
        for match in matches:
            groups = match.groupdict()
            if legacy_only:
                year = int(groups["roc_year"]) + 1911
            elif groups.get("year"):
                year = int(groups["year"])
            elif groups.get("roc_year"):
                year = int(groups["roc_year"]) + 1911
            else:
                year = message_year
            candidates.add((year, int(groups["sequence"])))
        if not legacy_only:
            for match in legacy_matches:
                candidates.add((int(match.group("roc_year")) + 1911, int(match.group("sequence"))))
    return candidates


async def _find_previous_case(channel: discord.TextChannel, guild: discord.Guild, case_id_format: str):
    try:
        permissions = channel.permissions_for(guild.me)
        if not permissions.read_message_history:
            return None
        async for message in channel.history(limit=1000):
            candidates = _case_candidates_from_message(message, case_id_format)
            if len(candidates) == 1:
                return next(iter(candidates))
            if len(candidates) > 1:
                continue
    except (discord.Forbidden, discord.HTTPException, AttributeError):
        return None
    return None


def _fallback_case_state(guild_id: int, current_year: int) -> tuple[int, int]:
    raw = get_server_config(guild_id, MODERATION_CASE_STATE_KEY, {})
    if not isinstance(raw, dict):
        return current_year, 0
    try:
        year = int(raw.get("year", current_year))
        sequence = max(0, int(raw.get("sequence", 0)))
    except (TypeError, ValueError):
        return current_year, 0
    return (year, sequence) if year == current_year else (current_year, 0)


async def _next_case_components(
    guild: discord.Guild,
    channel: Optional[discord.TextChannel],
    *,
    case_id_format: Optional[str] = None,
) -> tuple[int, int]:
    current_year = _taipei_now().year
    if case_id_format is None:
        case_id_format = get_moderation_announcement_config(guild.id)["case_id_format"]
    previous = await _find_previous_case(channel, guild, case_id_format) if channel else None
    if previous is not None:
        previous_year, previous_sequence = previous
        return (current_year, previous_sequence + 1) if previous_year == current_year else (current_year, 1)
    _, fallback_sequence = _fallback_case_state(guild.id, current_year)
    return current_year, fallback_sequence + 1


async def get_case_id(guild: discord.Guild) -> str:
    channel_id = get_server_config(guild.id, "MODERATION_MESSAGE_CHANNEL_ID")
    channel = guild.get_channel(channel_id) if channel_id else None
    config_value = get_moderation_announcement_config(guild.id)
    year, sequence = await _next_case_components(
        guild,
        channel,
        case_id_format=config_value["case_id_format"],
    )
    return format_case_id(config_value["case_id_format"], year, sequence)


def check_member_hierarchy(
    executor: discord.Member,
    target: Union[discord.Member, discord.User],
    bot_member: discord.Member,
) -> tuple[bool, str]:
    """檢查執行者是否有權限對目標成員操作（身份組階層檢查）。

    回傳 (True, "") 表示可以執行；(False, 錯誤訊息) 表示無權限。
    若目標不是 discord.Member（即已被封禁、不在伺服器的 User），跳過階層檢查。
    """
    if not isinstance(target, discord.Member):
        return True, ""
    if target == target.guild.owner:
        return False, t("moderate.err.cannot_target_owner")
    if target.top_role >= bot_member.top_role:
        return False, t("moderate.err.bot_hierarchy_too_low", target=target.mention)
    if executor != executor.guild.owner and target.top_role >= executor.top_role:
        return False, t("moderate.err.executor_hierarchy_too_low", target=target.mention)
    return True, ""


async def ban_user(guild: discord.Guild, user: Union[discord.Member, discord.User], reason: str, duration: int = 0, delete_message_seconds: int = 0, moderator: Optional[discord.Member] = None) -> bool:
    notifymsg = None
    try:
        if duration > 0:
            unban_time = datetime.now(timezone.utc) + timedelta(seconds=duration)
            set_user_data(guild.id, user.id, "unban_time", unban_time.isoformat())
        ModerationNotify.ignore_user(user.id)  # 避免重複通知
        try:
            notifymsg = await ModerationNotify.notify_user(user, guild, "封禁", reason, end_time=unban_time if duration > 0 else None, moderator=moderator)  # i18n: skip (ModerationNotify action token)
        except Exception:
            pass
        if isinstance(user, discord.Member):
            await user.ban(reason=reason, delete_message_seconds=delete_message_seconds)
        else:
            await guild.ban(user, reason=reason, delete_message_seconds=delete_message_seconds)
        log(f"Banned {user}, reason: {reason}, unban time: {'none' if duration == 0 else unban_time.isoformat()}", module_name="Moderate", guild=guild)
        return True
    except Exception as e:
        log(f"Failed to ban {user}: {e}", level=logging.ERROR, module_name="Moderate", guild=guild)
        if notifymsg:
            await notifymsg.delete()
        return False


async def check_unban():
    await bot.wait_until_ready()
    log("Auto-unban task started", module_name="Moderate")
    try:
        while not bot.is_closed():
            for guild in bot.guilds:
                if bot.is_closed():
                    return
                guild_id = guild.id
                to_unban = []

                try:
                    # 使用 async for 逐項讀取封鎖列表（memory-friendly）
                    async for entry in guild.bans():
                        user = entry.user
                        unban_time_str = get_user_data(guild_id, user.id, "unban_time")
                        if unban_time_str is None:
                            continue
                        try:
                            unban_time = datetime.fromisoformat(unban_time_str)
                        except Exception:
                            continue
                        if unban_time.tzinfo is None:
                            unban_time = unban_time.replace(tzinfo=timezone.utc)
                        if unban_time <= datetime.now(timezone.utc):
                            to_unban.append(user)
                except Exception as e:
                    continue

                for user in to_unban:
                    try:
                        await guild.unban(user, reason=t("moderate.audit.auto_unban", locale=i18n.resolve_locale(guild_id=guild_id)))
                        set_user_data(guild_id, user.id, "unban_time", None)
                        log(f"Auto-unbanned {user} in {guild.name}.", module_name="Moderate", guild=guild)
                    except Exception as e:
                        log(f"Error unbanning {user}: {e}", level=logging.ERROR, module_name="Moderate", guild=guild)

            await asyncio.sleep(60)  # 每分鐘檢查一次
    except asyncio.CancelledError:
        log("Auto-unban task cancelled", module_name="Moderate")
on_ready_tasks.append(check_unban)


def _bot_action_check(
    guild: Optional[discord.Guild],
    user: Optional[Union[discord.Member, discord.User]],
    perm_name: str,
) -> tuple[bool, str]:
    """檢查機器人是否有執行指定動作的伺服器權限，以及身份組是否夠高。
    只在 guild 與 user 都存在時進行完整檢查，否則視為 dry-run 直接放行。"""
    if not guild:
        return True, ""
    bot_member = guild.me
    if not getattr(bot_member.guild_permissions, perm_name, False):
        return False, t("moderate.err.bot_missing_perm", perm=perm_name)
    if user and isinstance(user, discord.Member):
        if user == guild.owner:
            return False, t("moderate.err.cannot_target_owner_action")
        if user.top_role >= bot_member.top_role:
            if getattr(user.guild_permissions, perm_name, False):
                return False, t("moderate.err.target_is_admin_hierarchy", user=str(user))
    return True, ""


def _load_custom_action_strings(guild_id: Optional[int]) -> dict[str, str]:
    if not guild_id:
        return {}
    raw = get_server_config(guild_id, "custom_action_strings")
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        cleaned[key] = value
    return cleaned


def _find_custom_action_key(custom_actions: dict[str, str], name: str) -> Optional[str]:
    lowered = name.strip().lower()
    for key in custom_actions.keys():
        if key.lower() == lowered:
            return key
    return None


def _split_action_chunks(action: str) -> list[str]:
    return [a.strip() for a in action.split(",") if a.strip()]


_CUSTOM_ACTION_ARGUMENT_RE = re.compile(r"\{([1-9])(?::([^{}]*))?\}")


def _split_custom_action_tokens(value: str) -> list[str]:
    lexer = shlex.shlex(value, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.quotes = '"'
    return list(lexer)


def _quote_custom_action_argument(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _custom_action_argument_count(template: str) -> int:
    matches = list(_CUSTOM_ACTION_ARGUMENT_RE.finditer(template))
    indexes = sorted({int(match.group(1)) for match in matches})
    if indexes and indexes != list(range(1, indexes[-1] + 1)):
        raise ValueError(t("moderate.err.custom_action_args_not_sequential", token="{1}"))
    for match in matches:
        fallback = match.group(2)
        if fallback is not None and "," in fallback:
            raise ValueError(t("moderate.err.custom_action_fallback_comma"))
    malformed = re.search(r"\{(?:0|[1-9]\d+)(?::[^{}]*)?\}", template)
    remaining = _CUSTOM_ACTION_ARGUMENT_RE.sub("", template)
    if malformed or re.search(r"\{\d", remaining):
        raise ValueError(t("moderate.err.custom_action_args_range", start="{1}", end="{9}"))
    return indexes[-1] if indexes else 0


def _substitute_custom_action_arguments(template: str, args: list[str]) -> str:
    argument_count = _custom_action_argument_count(template)
    if len(args) > argument_count:
        raise ValueError(t("moderate.err.custom_action_too_many_args", max=argument_count, count=len(args)))

    def replace_argument(match: re.Match) -> str:
        index = int(match.group(1))
        if index <= len(args):
            value = args[index - 1]
            return _quote_custom_action_argument(value) if any(char.isspace() for char in value) else value
        fallback = match.group(2)
        if fallback is not None:
            return _quote_custom_action_argument(fallback) if any(char.isspace() for char in fallback) else fallback
        raise ValueError(t("moderate.err.custom_action_missing_arg", index=index))

    return _CUSTOM_ACTION_ARGUMENT_RE.sub(replace_argument, template)


def _custom_action_sample_invocation(alias_name: str, template: str) -> str:
    argument_count = _custom_action_argument_count(template)
    if argument_count == 0:
        return alias_name
    return alias_name + " " + " ".join("1m" for _ in range(argument_count))


def _expand_custom_action_aliases(action: str, custom_actions: dict[str, str]) -> list[str]:
    alias_map = {k.lower(): v for k, v in custom_actions.items()}

    def expand_chunk(chunk: str, chain: list[str]) -> list[str]:
        first_token = chunk.split(maxsplit=1)[0].strip().lower() if chunk.strip() else ""
        if first_token in alias_map:
            try:
                parts = _split_custom_action_tokens(chunk)
            except ValueError as error:
                raise ValueError(t("moderate.err.custom_action_bad_quotes", error=error)) from error
            cmd_name = parts[0].lower() if parts else ""
            if cmd_name in chain:
                raise ValueError(t("moderate.err.custom_action_circular", chain=' -> '.join(chain + [cmd_name])))
            rendered = _substitute_custom_action_arguments(alias_map[cmd_name], parts[1:])
            expanded = []
            for sub in _split_action_chunks(rendered):
                expanded.extend(expand_chunk(sub, chain + [cmd_name]))
            return expanded
        if chain and ("'" in chunk or '"' in chunk):
            try:
                return [" ".join(_split_custom_action_tokens(chunk))]
            except ValueError as error:
                raise ValueError(t("moderate.err.custom_action_expanded_bad_quotes", error=error)) from error
        return [chunk]

    expanded_actions = []
    for chunk in _split_action_chunks(action):
        expanded_actions.extend(expand_chunk(chunk, []))
    return expanded_actions


def _format_preview_duration(seconds: int, *, permanent_text: str | None = None) -> str:
    if seconds == 0:
        return permanent_text if permanent_text is not None else t("moderate.duration.permanent")
    return get_time_text(seconds) or i18n.tn("common.unit.seconds", seconds)


def _parse_action_duration(token: str, *, allow_zero: bool = True) -> tuple[int | None, str | None]:
    token = str(token or "").strip()
    if not _DURATION_TOKEN_RE.fullmatch(token):
        return None, t("moderate.err.duration_unrecognized", token=token)
    seconds = timestr_to_seconds(token)
    if seconds == 0 and token != "0":
        return None, t("moderate.err.duration_must_be_positive_token", token=token)
    if seconds == 0 and not allow_zero:
        return None, t("moderate.err.duration_must_be_positive")
    return seconds, None


def _suggest_shorthand_action(action: str) -> tuple[str | None, str | None]:
    chunks = _split_action_chunks(action)
    changed = False
    confirmation = None
    normalized_chunks = []

    for chunk in chunks:
        tokens = chunk.split()
        if len(tokens) == 1 and tokens[0].isdigit():
            minutes = int(tokens[0])
            if minutes <= 0:
                return None, t("moderate.err.mute_minutes_positive")
            if minutes * 60 > MAX_TIMEOUT_SECONDS:
                return None, t("moderate.err.timeout_max_minutes")
            normalized_chunks.append(f"mute {minutes}m")
            confirmation = t("moderate.confirm.mute_minutes", minutes=minutes)
            changed = True
            continue

        if len(tokens) == 1 and _DURATION_TOKEN_RE.fullmatch(tokens[0]) and tokens[0] != "0":
            seconds, error = _parse_action_duration(tokens[0], allow_zero=False)
            if error:
                return None, error
            if seconds > MAX_TIMEOUT_SECONDS:
                return None, t("moderate.err.timeout_max_days")
            normalized_chunks.append(f"mute {tokens[0]}")
            confirmation = t("moderate.confirm.mute_duration", duration=_format_preview_duration(seconds))
            changed = True
            continue

        if tokens and tokens[0].lower() in ("mute", "timeout", "to") and len(tokens) >= 2 and tokens[1].isdigit():
            minutes = int(tokens[1])
            if minutes <= 0:
                return None, t("moderate.err.mute_minutes_positive")
            if minutes * 60 > MAX_TIMEOUT_SECONDS:
                return None, t("moderate.err.timeout_max_minutes")
            tokens[1] = f"{minutes}m"
            normalized_chunks.append(" ".join(tokens))
            confirmation = t("moderate.confirm.mute_minutes_no_unit", minutes=minutes)
            changed = True
            continue

        normalized_chunks.append(chunk)

    if not changed:
        return None, None
    return ", ".join(normalized_chunks), confirmation


def analyze_action_string(
    action: str,
    guild_id: Optional[int] = None,
    *,
    infer_shorthand: bool = True,
    custom_actions_override: Optional[dict[str, str]] = None,
) -> dict:
    raw_action = str(action or "").strip()
    result = {
        "valid": False,
        "normalized": raw_action,
        "requires_confirmation": False,
        "confirmation": None,
        "preview": [],
        "error": None,
        "suggestions": [
            {"label": label, "value": value}
            for label, value in _action_input_suggestions()
        ],
    }
    if not raw_action:
        result["error"] = t("moderate.err.action_empty")
        return result
    if len(raw_action) > 500:
        result["error"] = t("moderate.err.action_too_long")
        return result

    if infer_shorthand:
        suggested, confirmation_or_error = _suggest_shorthand_action(raw_action)
        if suggested is not None:
            analyzed = analyze_action_string(
                suggested,
                guild_id,
                infer_shorthand=False,
                custom_actions_override=custom_actions_override,
            )
            if not analyzed["valid"]:
                return analyzed
            analyzed["requires_confirmation"] = True
            analyzed["confirmation"] = confirmation_or_error
            return analyzed
        if confirmation_or_error is not None:
            result["error"] = confirmation_or_error
            return result

    custom_actions = (
        custom_actions_override
        if custom_actions_override is not None
        else _load_custom_action_strings(guild_id)
    )
    try:
        actions = _expand_custom_action_aliases(raw_action, custom_actions)
    except ValueError as error:
        result["error"] = str(error)
        return result
    if not actions:
        result["error"] = t("moderate.err.needs_one_action")
        return result
    if len(actions) > 5:
        result["error"] = t("moderate.err.too_many_actions")
        return result

    previews = []
    last_reason = t("moderate.default_reason")
    for chunk in actions:
        tokens = chunk.split()
        command = tokens[0].lower() if tokens else ""
        if command not in BUILTIN_ACTIONS:
            available = i18n.join_list(sorted(BUILTIN_ACTIONS))
            result["error"] = t("moderate.err.unsupported_action", command=command or chunk, available=available)
            return result

        args = tokens[1:]
        if command == "ban":
            if not args or not args[0][0].isdigit():
                duration_seconds = 0
                delete_seconds = 0
                reason = " ".join(args) or last_reason
            else:
                duration_seconds, error = _parse_action_duration(args[0])
                if error:
                    result["error"] = error
                    return result
                if len(args) >= 2 and args[1][0].isdigit():
                    delete_seconds, error = _parse_action_duration(args[1])
                    if error:
                        result["error"] = error
                        return result
                    reason = " ".join(args[2:]) or last_reason
                elif len(args) >= 2:
                    result["error"] = t(
                        "moderate.err.ban_missing_delete_window",
                        example=f"ban {args[0]} 0 {' '.join(args[1:])}",
                    )
                    return result
                else:
                    delete_seconds = 0
                    reason = last_reason
            last_reason = reason
            previews.append(
                t("moderate.preview.ban",
                  duration=_format_preview_duration(duration_seconds),
                  delete_window=_format_preview_duration(delete_seconds, permanent_text=t("moderate.duration.no_deletion")),
                  reason=reason)
            )
        elif command == "kick":
            reason = " ".join(args) or last_reason
            previews.append(t("moderate.preview.kick", reason=reason))
        elif command in ("mute", "timeout", "to"):
            if not args or not args[0][0].isdigit():
                duration_seconds = 3600
                reason = " ".join(args) or last_reason
            else:
                duration_seconds, error = _parse_action_duration(args[0], allow_zero=False)
                if error:
                    result["error"] = error
                    return result
                if duration_seconds > MAX_TIMEOUT_SECONDS:
                    result["error"] = t("moderate.err.timeout_max_days")
                    return result
                reason = " ".join(args[1:]) or last_reason
            previews.append(t("moderate.preview.mute", duration=_format_preview_duration(duration_seconds), reason=reason))
        elif command == "unban":
            previews.append(t("moderate.preview.unban", reason=' '.join(args) or last_reason))
        elif command in ("unmute", "untimeout"):
            previews.append(t("moderate.preview.unmute", reason=' '.join(args) or last_reason))
        elif command == "delete":
            message_text = " ".join(args)
            previews.append(
                t("moderate.preview.delete_with_warn", message=message_text) if message_text
                else t("moderate.preview.delete")
            )
        elif command == "warn":
            previews.append(t("moderate.preview.warn", message=' '.join(args) or t("moderate.default_warn_message")))
        elif command in ("send_mod_message", "smm"):
            previews.append(t("moderate.preview.send_mod_message"))
        elif command == "force_verify":
            if args:
                duration_seconds, error = _parse_action_duration(args[0], allow_zero=False)
                if error:
                    result["error"] = error
                    return result
                previews.append(t("moderate.preview.force_verify_duration", duration=_format_preview_duration(duration_seconds)))
            else:
                previews.append(t("moderate.preview.force_verify"))

    result["valid"] = True
    result["preview"] = previews
    return result


MEMBER_JOIN_ACTIONS = {
    "ban",
    "kick",
    "mute",
    "timeout",
    "to",
    "force_verify",
    "send_mod_message",
    "smm",
}


def analyze_member_join_action(action: str, guild_id: Optional[int] = None) -> dict:
    """Validate an action that runs without a triggering Discord message."""
    analysis = analyze_action_string(action, guild_id)
    if not analysis["valid"]:
        return analysis

    try:
        expanded = _expand_custom_action_aliases(
            str(analysis["normalized"] or ""),
            _load_custom_action_strings(guild_id),
        )
    except ValueError as error:
        analysis["valid"] = False
        analysis["error"] = str(error)
        return analysis

    unsupported = []
    for chunk in expanded:
        command = chunk.split(maxsplit=1)[0].lower() if chunk.strip() else ""
        if command not in MEMBER_JOIN_ACTIONS and command not in unsupported:
            unsupported.append(command)
    if unsupported:
        analysis["valid"] = False
        analysis["requires_confirmation"] = False
        analysis["confirmation"] = None
        analysis["error"] = t(
            "moderate.err.member_join_unsupported_actions",
            actions=i18n.join_list(f"`{command}`" for command in unsupported),
        )
    return analysis


def action_autocomplete_choices(current: str) -> list[app_commands.Choice[str]]:
    choices = []
    current_text = str(current or "").strip()
    if current_text.isdigit():
        minutes = int(current_text)
        if 0 < minutes <= MAX_TIMEOUT_SECONDS // 60:
            choices.append(
                app_commands.Choice(
                    name=t("moderate.autocomplete.mute_minutes", minutes=minutes),
                    value=f"mute {minutes}m",
                )
            )

    lowered = current_text.casefold()
    for label, value in _action_input_suggestions():
        if lowered and lowered not in label.casefold() and lowered not in value.casefold():
            continue
        if any(choice.value == value for choice in choices):
            continue
        choices.append(app_commands.Choice(name=label, value=value))
    return choices[:25]


async def action_input_autocomplete(interaction: discord.Interaction, current: str):
    return action_autocomplete_choices(current)


def build_action_preview_embed(
    analysis: dict,
    *,
    title: str | None = None,
    saved: bool = False,
) -> discord.Embed:
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
    embed.add_field(
        name=t("moderate.field.stored_command"),
        value=f"```text\n{analysis['normalized']}\n```",
        inline=False,
    )
    preview = analysis.get("preview") or []
    embed.add_field(
        name=t("moderate.field.execution_preview"),
        value="\n".join(f"{index}. {line}" for index, line in enumerate(preview, 1))
        or t("moderate.no_executable_actions"),
        inline=False,
    )
    return embed


class ActionConfirmationView(i18n.I18nView):
    def __init__(
        self,
        owner_id: int,
        analysis: dict,
        confirm_callback,
        *,
        cancel_callback=None,
        timeout: float = 120,
    ):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.analysis = analysis
        self.confirm_callback = confirm_callback
        self.cancel_callback = cancel_callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(t("moderate.err.not_your_confirmation"), ephemeral=True)
        return False

    @discord.ui.button(label=i18n.K("moderate.btn.confirm_action"), style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.confirm_callback(interaction, self.analysis)
        self.stop()

    @discord.ui.button(label=i18n.K("common.btn.cancel"), style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        if self.cancel_callback is not None:
            await self.cancel_callback(interaction)
            return
        await interaction.response.edit_message(
            content=t("moderate.action_settings_unchanged"),
            embed=None,
            view=None,
        )


async def do_action_str(
    action: str,
    guild: Optional[discord.Guild] = None,
    user: Optional[discord.Member] = None,
    message: Optional[discord.Message] = None,
    moderator: Optional[discord.Member] = None,
    *,
    return_status: bool = False,
):
    """Execute an action string.

    ``return_status`` is intentionally opt-in so existing callers keep receiving
    the historical ``list[str]`` result.  Join-time automation uses the tuple
    form, whose second item is ``success``, ``skipped``, or ``failed``.
    """
    def finish(lines: list[str], status: str):
        return (lines, status) if return_status else lines

    def merge_status(current: str, incoming: str) -> str:
        priority = {"success": 0, "skipped": 1, "failed": 2}
        return incoming if priority[incoming] > priority[current] else current

    # if user is none just check if action is valid
    custom_actions = _load_custom_action_strings(guild.id if guild else None)
    try:
        actions = _expand_custom_action_aliases(action, custom_actions)
    except ValueError as e:
        return finish([t("moderate.log.error", error=e)], "failed")
    if len(actions) > 5:
        return finish([t("moderate.log.error", error=t("moderate.err.too_many_actions"))], "failed")
    validation = analyze_action_string(
        ", ".join(actions),
        infer_shorthand=False,
        custom_actions_override={},
    )
    if not validation["valid"]:
        return finish([t("moderate.log.error", error=validation['error'])], "failed")
    logs = []
    execution_status = "success"
    last_reason = t("moderate.default_reason")
    actions_json = []
    for a in actions:
        cmd = a.split(" ")
        cmd[0] = cmd[0].lower()
        if cmd[0] == "ban":
            # ban <delete_messages> <duration> <reason>
            if len(cmd) == 1:
                cmd.append("0s")
            if len(cmd) == 2:
                cmd.append("0s")
            if len(cmd) == 3:
                cmd.append(last_reason)

            if not cmd[1][0].isdigit():
                # cmd[1] is reason
                cmd[1], cmd[2], cmd[3] = "0s", "0s", cmd[1]

            duration_seconds = timestr_to_seconds(cmd[1]) if cmd[1] != "0" else 0
            delete_messages = timestr_to_seconds(cmd[2]) if cmd[2] != "0" else 0
            cmd.pop(0)  # remove "ban"
            cmd.pop(0)  # remove duration
            cmd.pop(0)  # remove delete_messages
            reason = " ".join(cmd)
            last_reason = reason
            success = True
            skipped = False
            if user:
                ok, msg = _bot_action_check(guild, user, "ban_members")
                if not ok:
                    logs.append(t("moderate.log.ban_skipped", reason=msg))
                    success = False
                    skipped = True
                    execution_status = merge_status(execution_status, "skipped")
                else:
                    success = await ban_user(guild, user, reason=reason, duration=duration_seconds, delete_message_seconds=delete_messages)
            if success:
                logs.append(t("moderate.log.ban_done", reason=reason, duration=duration_seconds, delete_seconds=delete_messages))
            elif user:
                logs.append(t("moderate.log.ban_failed"))
                if not skipped:
                    execution_status = merge_status(execution_status, "failed")
                break  # failed to ban, stop further actions to prevent confusion
            actions_json.append({"action": "ban", "duration": duration_seconds, "reason": reason})
        elif cmd[0] == "kick":
            # kick <reason>
            if len(cmd) == 1:
                cmd.append(last_reason)
            cmd.pop(0)  # remove "kick"
            reason = " ".join(cmd)
            if user:
                ok, msg = _bot_action_check(guild, user, "kick_members")
                if not ok:
                    logs.append(t("moderate.log.kick_skipped", reason=msg))
                    execution_status = merge_status(execution_status, "skipped")
                else:
                    await user.kick(reason=reason)
                    logs.append(t("moderate.log.kick_done", reason=reason))
            else:
                logs.append(t("moderate.log.kick_done", reason=reason))
            actions_json.append({"action": "kick", "reason": reason})
        elif cmd[0] in ("mute", "timeout", "to"):
            # mute <duration> <reason>
            if len(cmd) == 1:
                cmd.append("1h")
            if len(cmd) == 2:
                cmd.append(last_reason)

            if not cmd[1][0].isdigit():
                # cmd[1] is reason
                cmd[1], cmd[2] = "1h", cmd[1]

            duration_seconds = timestr_to_seconds(cmd[1]) if cmd[1] != "0" else 0
            cmd.pop(0)  # remove the timeout action name
            cmd.pop(0)  # remove duration
            reason = " ".join(cmd) if cmd else last_reason
            if user:
                ok, msg = _bot_action_check(guild, user, "moderate_members")
                if not ok:
                    logs.append(t("moderate.log.mute_skipped", reason=msg))
                    execution_status = merge_status(execution_status, "skipped")
                else:
                    await user.timeout(datetime.now(timezone.utc) + timedelta(seconds=duration_seconds), reason=reason)
                    logs.append(t("moderate.log.mute_done", reason=reason, duration=duration_seconds))
            else:
                logs.append(t("moderate.log.mute_done", reason=reason, duration=duration_seconds))
            actions_json.append({"action": "mute", "duration": duration_seconds, "reason": reason})
        elif cmd[0] == "unban":
            # unban <reason>
            if len(cmd) == 1:
                cmd.append(last_reason)
            cmd.pop(0)  # remove "unban"
            reason = " ".join(cmd)
            last_reason = reason
            if guild and user:
                ok, msg = _bot_action_check(guild, None, "ban_members")  # unban: no hierarchy concern
                if not ok:
                    logs.append(t("moderate.log.unban_skipped", reason=msg))
                else:
                    try:
                        await guild.unban(user, reason=reason)
                        set_user_data(guild.id, user.id, "unban_time", None)
                        logs.append(t("moderate.log.unban_done", reason=reason))
                    except Exception as e:
                        logs.append(t("moderate.log.unban_failed", error=e))
                        log(f"Error unbanning {user}: {e}", level=logging.ERROR, module_name="Moderate", guild=guild)
                        break  # failed to unban, stop further actions to prevent confusion
            else:
                logs.append(t("moderate.log.unban_done", reason=reason))
            actions_json.append({"action": "unban", "reason": reason})
        elif cmd[0] == "unmute" or cmd[0] == "untimeout":
            # unmute <reason>
            if len(cmd) == 1:
                cmd.append(last_reason)
            cmd.pop(0)  # remove "unmute" or "untimeout"
            reason = " ".join(cmd)
            if user:
                ok, msg = _bot_action_check(guild, user, "moderate_members")
                if not ok:
                    logs.append(t("moderate.log.unmute_skipped", reason=msg))
                else:
                    await user.timeout(None, reason=reason)
                    logs.append(t("moderate.log.unmute_done", reason=reason))
            else:
                logs.append(t("moderate.log.unmute_done", reason=reason))
            actions_json.append({"action": "unmute", "reason": reason})
        elif cmd[0] == "delete": # or cmd[0] == "delete_dm":
            # delete <warn_message>
            logs.append(t("moderate.log.deleted_message"))
            if message:
                try:
                    await message.delete()
                except:
                    logs.append(t("moderate.log.delete_message_failed"))
            if len(cmd) > 1:
                msg = cmd.copy()
                msg.pop(0)
                warn_message = " ".join(msg)
                warn_message = warn_message.replace("{user}", user.mention if user else t("moderate.fallback_user"))
                logs.append(t("moderate.log.also_warned", message=warn_message))
                if cmd[0] == "delete_dm" and user:
                    embed = discord.Embed(title=t("moderate.embed.you_were_warned"), description=warn_message, color=discord.Color.orange())
                    embed.set_footer(text=guild.name if guild else None, icon_url=guild.icon.url if guild and guild.icon else None)
                    await user.send(embed=embed)
                elif message:
                    msg = await message.channel.send(warn_message)
                    ignore_message_ids.add(msg.id)
                    # limit 100 ids in memory to prevent memory leak
                    if len(ignore_message_ids) > 100:
                        ignore_message_ids.pop()
        elif cmd[0] == "warn": # or cmd[0] == "warn_dm":
            # warn <warn_message>
            if len(cmd) == 1:
                cmd.append((user.mention if user else t("moderate.fallback_user")) + "，" + t("moderate.default_warn_reason"))
            msg = cmd.copy()
            msg.pop(0)
            warn_message = " ".join(msg)
            warn_message = warn_message.replace("{user}", user.mention if user else t("moderate.fallback_user"))
            logs.append(t("moderate.log.sent_warning", message=warn_message))
            if cmd[0] == "warn_dm" and user:
                embed = discord.Embed(title=t("moderate.embed.you_were_warned"), description=warn_message, color=discord.Color.orange())
                embed.set_footer(text=guild.name if guild else None, icon_url=guild.icon.url if guild and guild.icon else None)
                await user.send(embed=embed)
            elif message:
                msg = await message.reply(warn_message)
                ignore_message_ids.add(msg.id)
                # limit 100 ids in memory to prevent memory leak
                if len(ignore_message_ids) > 100:
                    ignore_message_ids.pop()
        elif cmd[0] == "send_mod_message" or cmd[0] == "smm":
            # send_mod_message
            if len(cmd) == 1:
                cmd.append(t("moderate.default_smm_reason"))
            if guild and user and moderator:
                sent = await moderation_message_settings(None, user, moderator, actions_json, direct=True, guild=guild)
                if sent or not return_status:
                    logs.append(t("moderate.log.sent_mod_message"))
                else:
                    logs.append(t("moderate.log.send_mod_message_failed"))
                if not sent:
                    execution_status = merge_status(execution_status, "failed")
            else:
                logs.append(
                    t("moderate.log.send_mod_message_failed")
                    if return_status
                    else t("moderate.log.sent_mod_message")
                )
                execution_status = merge_status(execution_status, "failed")
        elif cmd[0] == "force_verify":
            # force_verify <duration>
            if "ServerWebVerify" in modules:
                from ServerWebVerify import force_verify_user
                if user:
                    success, message = await force_verify_user(guild, user)
                    logs.append(message)
                    if not success:
                        execution_status = merge_status(execution_status, "skipped")
                if len(cmd) > 1:
                    duration_seconds = timestr_to_seconds(cmd[1]) if cmd[1] != "0" else 0
                    until_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
                    logs.append(t("moderate.log.force_verify_duration", duration=duration_seconds))
                    set_server_config(guild.id, "force_verify_until", until_time.timestamp())
            else:
                logs.append(t("moderate.log.force_verify_unavailable"))
                execution_status = merge_status(execution_status, "failed")
    return finish(logs, execution_status)


def _entity_name(entity, fallback: str) -> str:
    return str(
        getattr(entity, "display_name", None)
        or getattr(entity, "name", None)
        or fallback
    )


def _entity_avatar_url(entity) -> str:
    avatar = getattr(entity, "display_avatar", None)
    return str(getattr(avatar, "url", "") or "")


def build_moderation_template_values(
    guild: discord.Guild,
    user,
    moderator,
    *,
    reason: str,
    action_text: str,
    case_id: str,
    reported_message: str = "",
    report_context: str = "",
    ai_note: str = "",
    locale: str | None = None,
) -> dict[str, str]:
    user_name = _entity_name(user, t("moderate.fallback_user", locale=locale))
    system_auto = t("moderate.fallback_system_auto", locale=locale)
    moderator_name = _entity_name(moderator, system_auto) if moderator else system_auto
    return {
        "user": str(getattr(user, "mention", user_name)),
        "user_name": user_name,
        "user_id": str(getattr(user, "id", "")),
        "user_avatar": _entity_avatar_url(user),
        "moderator": str(getattr(moderator, "mention", moderator_name)) if moderator else system_auto,
        "moderator_name": moderator_name,
        "moderator_id": str(getattr(moderator, "id", "")) if moderator else "",
        "moderator_avatar": _entity_avatar_url(moderator) if moderator else "",
        "reason": str(reason or t("moderate.fallback_none", locale=locale)),
        "action": str(action_text or t("moderate.fallback_none", locale=locale)),
        "case_id": str(case_id),
        "guild": str(getattr(guild, "name", t("moderate.fallback_guild", locale=locale))),
        "guild_id": str(getattr(guild, "id", "")),
        "guild_icon": str(getattr(getattr(guild, "icon", None), "url", "") or ""),
        "reported_message": str(reported_message or ""),
        "report_context": str(report_context or ""),
        "ai_note": str(ai_note or ""),
    }


async def render_moderation_announcement(
    config_value: dict,
    values: dict[str, str],
    *,
    locale: str | None = None,
) -> tuple[str | None, discord.Embed | None]:
    normalized = normalize_moderation_announcement_config(config_value, locale=locale)
    content_template, extracted = extract_embed_tokens(normalized["template"])

    async def resolver(value: str) -> str:
        rendered = str(value)
        for key in MODERATION_TEMPLATE_VARIABLES:
            rendered = rendered.replace(f"{{{key}}}", str(values.get(key, "")))
        return rendered

    if extracted["color"] is not None:
        resolved_color = (await resolver(extracted["color"])).strip()
        if resolved_color and parse_embed_color(resolved_color) is None:
            raise ValueError(t("moderate.err.embed_color_invalid", locale=locale))

    content = (await resolver(content_template)).strip() or None
    embed = await build_embed_from_tokens(
        extracted,
        resolver,
        now=_taipei_now(),
    )
    validate_embed_output(content, embed)
    return content, embed


async def preview_moderation_announcement(
    guild: discord.Guild,
    *,
    config_value: Optional[dict] = None,
    user=None,
    moderator=None,
    reason: str | None = None,
    action_text: str | None = None,
    reported_message: str = "",
    report_context: str = "",
    ai_note: str = "",
) -> tuple[str | None, discord.Embed | None, str]:
    guild_locale = i18n.resolve_locale(guild_id=guild.id)
    reason = reason if reason is not None else t("moderate.sample.reason", locale=guild_locale)
    action_text = action_text if action_text is not None else t("moderate.sample.action_text", locale=guild_locale)
    normalized = normalize_moderation_announcement_config(
        config_value if config_value is not None else get_moderation_announcement_config(guild.id),
        locale=guild_locale,
    )
    channel_id = get_server_config(guild.id, "MODERATION_MESSAGE_CHANNEL_ID")
    channel = guild.get_channel(channel_id) if channel_id else None
    year, sequence = await _next_case_components(
        guild,
        channel,
        case_id_format=normalized["case_id_format"],
    )
    case_id = format_case_id(normalized["case_id_format"], year, sequence)
    sample_user = user or getattr(guild, "me", None)
    sample_moderator = moderator or getattr(guild, "me", None)
    values = build_moderation_template_values(
        guild,
        sample_user,
        sample_moderator,
        reason=reason,
        action_text=action_text,
        case_id=case_id,
        reported_message=reported_message,
        report_context=report_context,
        ai_note=ai_note,
        locale=guild_locale,
    )
    content, embed = await render_moderation_announcement(normalized, values, locale=guild_locale)
    return content, embed, case_id


async def send_moderation_announcement(
    guild: discord.Guild,
    channel: discord.TextChannel,
    user,
    moderator,
    *,
    reason: str,
    action_text: str,
    reported_message: str = "",
    report_context: str = "",
    ai_note: str = "",
) -> tuple[discord.Message, str]:
    async with _case_id_locks[guild.id]:
        guild_locale = i18n.resolve_locale(guild_id=guild.id)
        config_value = get_moderation_announcement_config(guild.id)
        year, sequence = await _next_case_components(
            guild,
            channel,
            case_id_format=config_value["case_id_format"],
        )
        case_id = format_case_id(config_value["case_id_format"], year, sequence)
        values = build_moderation_template_values(
            guild,
            user,
            moderator,
            reason=reason,
            action_text=action_text,
            case_id=case_id,
            reported_message=reported_message,
            report_context=report_context,
            ai_note=ai_note,
            locale=guild_locale,
        )
        content, embed = await render_moderation_announcement(config_value, values, locale=guild_locale)
        sent_message = await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        set_server_config(
            guild.id,
            MODERATION_CASE_STATE_KEY,
            {"year": year, "sequence": sequence},
        )
        return sent_message, case_id


async def moderation_message_settings(interaction: Optional[discord.Interaction], user: discord.Member, moderator: discord.Member, actions: list, direct: bool = False, guild: Optional[discord.Guild] = None):
    resolved_guild = guild if guild else (interaction.guild if interaction else None)
    if resolved_guild is None:
        if interaction:
            await interaction.followup.send(t("moderate.err.guild_only"), ephemeral=True)
        return

    guild_locale = i18n.resolve_locale(guild_id=resolved_guild.id)
    action_texts = []
    for action in actions:
        if action["action"] == "ban":
            duration_seconds = action.get("duration", 0)
            if duration_seconds > 0:
                action_texts.append(t("moderate.joke.ban_temp", locale=guild_locale, duration=get_time_text(duration_seconds, locale=guild_locale)))
            else:
                action_texts.append(t("moderate.joke.ban_perm", locale=guild_locale))
        elif action["action"] == "kick":
            action_texts.append(t("moderate.action_name.kick", locale=guild_locale))
        elif action["action"] == "mute":
            time_text = action.get("duration", 0)
            action_texts.append(t("moderate.joke.mute", locale=guild_locale, duration=get_time_text(time_text, locale=guild_locale)))
        elif action["action"] == "add_role":
            action_texts.append(t("moderate.action_text.add_role", locale=guild_locale, role=action['role']))
        elif action["action"] == "remove_role":
            action_texts.append(t("moderate.action_text.remove_role", locale=guild_locale, role=action['role']))
        elif action["action"] == "custom":
            action_texts.append(action.get("custom_action", t("moderate.fallback_none", locale=guild_locale)))
    action_text = "+".join(action_texts) if action_texts else t("moderate.fallback_none", locale=guild_locale)
    reason = t("moderate.fallback_none", locale=guild_locale)
    for action in actions:
        if 'reason' in action:
            reason = action['reason'] or t("moderate.fallback_none", locale=guild_locale)
            break

    async def render_preview():
        content, announcement_embed, _ = await preview_moderation_announcement(
            resolved_guild,
            user=user,
            moderator=moderator,
            reason=reason,
            action_text=action_text,
        )
        return content, announcement_embed

    async def send_feedback(target_interaction: discord.Interaction, message: str):
        if target_interaction.response.is_done():
            await target_interaction.followup.send(message, ephemeral=True)
        else:
            await target_interaction.response.send_message(message, ephemeral=True)

    async def send_message(feedback_interaction: Optional[discord.Interaction] = None):
        channel_id = get_server_config(resolved_guild.id, "MODERATION_MESSAGE_CHANNEL_ID")
        if channel_id is None:
            if feedback_interaction:
                await send_feedback(feedback_interaction, t("moderate.err.no_announcement_channel"))
            return False
        channel = resolved_guild.get_channel(channel_id)
        if channel is None:
            if feedback_interaction:
                await send_feedback(feedback_interaction, t("moderate.err.announcement_channel_not_found"))
            return False
        try:
            _, case_id = await send_moderation_announcement(
                resolved_guild,
                channel,
                user,
                moderator,
                reason=reason,
                action_text=action_text,
            )
            if feedback_interaction:
                await send_feedback(feedback_interaction, t("moderate.msg.announcement_sent", case_id=case_id))
            log(f"Sent announcement to #{channel.name}.", module_name="Moderate", guild=resolved_guild)
            return True
        except discord.Forbidden:
            if feedback_interaction:
                await send_feedback(feedback_interaction, t("moderate.err.announcement_forbidden"))
            log("Cannot send message in the announcement channel; bot is missing permissions.", level=logging.ERROR, module_name="Moderate", guild=resolved_guild)
            return False
        except Exception as e:
            if feedback_interaction:
                await send_feedback(feedback_interaction, t("moderate.err.announcement_send_error", error=e))
            log(f"Error sending announcement: {e}", level=logging.ERROR, module_name="Moderate", guild=resolved_guild)
            return False

    class MessageButtons(i18n.I18nView):
        def __init__(self, owner_id: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id

        async def interaction_check(self, component_interaction: discord.Interaction) -> bool:
            if component_interaction.user.id == self.owner_id:
                return True
            await component_interaction.response.send_message(t("moderate.err.not_your_preview"), ephemeral=True)
            return False

        @discord.ui.button(label=i18n.K("moderate.btn.change_reason"), style=discord.ButtonStyle.primary, row=0)
        async def change_reason_button(self, component_interaction: discord.Interaction, button: discord.ui.Button):
            parent_view = self

            class ReasonModal(i18n.I18nModal, title=i18n.K("moderate.btn.change_reason")):
                reason_input = discord.ui.TextInput(
                    label=t("moderate.field.action_reason"),
                    placeholder=t("moderate.placeholder.action_reason"),
                    default=str(reason),
                    required=True,
                    max_length=100,
                )

                async def on_submit(self, modal_interaction: discord.Interaction):
                    nonlocal reason
                    reason = self.reason_input.value
                    for action in actions:
                        if 'reason' in action:
                            action['reason'] = reason
                    content, announcement_embed = await render_preview()
                    await modal_interaction.response.edit_message(
                        content=content,
                        embed=announcement_embed,
                        view=parent_view,
                    )
            await component_interaction.response.send_modal(ReasonModal())

        @discord.ui.button(label=i18n.K("moderate.btn.change_result"), style=discord.ButtonStyle.primary, row=0)
        async def change_actions_button(self, component_interaction: discord.Interaction, button: discord.ui.Button):
            parent_view = self

            class ActionModal(i18n.I18nModal, title=i18n.K("moderate.btn.change_result")):
                new_actions = discord.ui.TextInput(
                    label=t("moderate.field.action_result"),
                    placeholder=t("moderate.placeholder.action_result"),
                    default=action_text,
                    required=True,
                    max_length=200,
                )

                async def on_submit(self, modal_interaction: discord.Interaction):
                    nonlocal action_text
                    action_text = self.new_actions.value
                    content, announcement_embed = await render_preview()
                    await modal_interaction.response.edit_message(
                        content=content,
                        embed=announcement_embed,
                        view=parent_view,
                    )
            await component_interaction.response.send_modal(ActionModal())

        @discord.ui.button(label=i18n.K("moderate.btn.confirm_and_send"), style=discord.ButtonStyle.success, row=1)
        async def confirm_button(self, component_interaction: discord.Interaction, button: discord.ui.Button):
            self.stop()
            await component_interaction.response.defer(ephemeral=True)
            await send_message(component_interaction)
            try:
                await component_interaction.message.edit(view=None)
            except discord.HTTPException:
                pass

    if direct:
        return await send_message(interaction)
    elif interaction:
        try:
            content, announcement_embed = await render_preview()
            await interaction.followup.send(
                content=content,
                embed=announcement_embed,
                view=MessageButtons(interaction.user.id),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except Exception as error:
            await interaction.followup.send(t("moderate.err.preview_generation_failed", error=error), ephemeral=True)
            

@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class Moderate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    
    @app_commands.command(name=app_commands.locale_str("multi-moderate", i18n_key="cmd.moderate.multi_moderate.name"), description=app_commands.locale_str("Take action on multiple users", i18n_key="cmd.moderate.multi_moderate.desc"))
    @app_commands.describe(users=app_commands.locale_str("Users (mentions or IDs, separated by commas, spaces, or newlines)", i18n_key="cmd.moderate.multi_moderate.param.users"), action=app_commands.locale_str("Action commands, comma separated, e.g. ban 1d spamming, mute 10m", i18n_key="cmd.moderate.multi_moderate.param.action"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def multi_moderate(self, interaction: discord.Interaction, users: str, action: str):
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(t("moderate.err.guild_only"), ephemeral=True)
            return
        
        # check bot permissions
        if not guild.me.guild_permissions.administrator:
            await interaction.followup.send(t("moderate.err.bot_needs_admin"), ephemeral=True)
            return
        
        user_list = [u.strip() for u in re.split(r'[,\s]+', users) if u.strip()]
        target_users: list[Union[discord.Member, discord.User]] = []
        failed_users: list[str] = []  # 無法解析的用戶原始輸入
        for u in user_list:
            member = None
            user_id = None
            # 嘗試解析 ID
            try:
                user_id = int(u)
                member = guild.get_member(user_id)
            except ValueError:
                pass
            # 嘗試解析提及格式
            if member is None and u.startswith("<@") and u.endswith(">"):
                try:
                    user_id = int(u[2:-1].replace("!", ""))
                    member = guild.get_member(user_id)
                except ValueError:
                    pass
            # 若不在伺服器內，嘗試透過 API 取得用戶
            if member is None and user_id is not None:
                try:
                    member = await self.bot.fetch_user(user_id)
                except discord.NotFound:
                    log(f"multi-moderate: skipped nonexistent user {u}", module_name="Moderate", guild=guild)
                    failed_users.append(t("moderate.multi.user_not_found", input=u))
                    continue
                except discord.HTTPException:
                    log(f"multi-moderate: error fetching user {u}, skipped", level=logging.WARNING, module_name="Moderate", guild=guild)
                    failed_users.append(t("moderate.multi.user_fetch_failed", input=u))
                    continue
            if member is None:
                log(f"multi-moderate: could not resolve user {u}, skipped", module_name="Moderate", guild=guild)
                failed_users.append(t("moderate.multi.user_unresolved", input=u))
                continue
            target_users.append(member)
        
        if not target_users:
            fail_note = i18n.join_list(failed_users) if failed_users else t("moderate.multi.check_input")
            await interaction.followup.send(t("moderate.multi.no_valid_users", failed=fail_note), ephemeral=True)
            return

        await interaction.followup.send(
            t("moderate.multi.starting", count=len(target_users), eta=get_time_text(len(target_users) * 2)),
            ephemeral=True)
        
        success_logs: list[str] = []  # (user, [logs])
        skipped_users: list[str] = []  # 因階層不足跳過的用戶
        for i, user in enumerate(target_users):
            ok, msg = check_member_hierarchy(interaction.user, user, guild.me)
            if not ok:
                skipped_users.append(t("moderate.multi.skipped_user", user=user.mention, reason=msg))
                continue
            result_logs = await do_action_str(action, guild=guild, user=user, moderator=interaction.user)
            success_logs.append("\n".join(f"> - {r}" for r in result_logs))
            # 避免 Discord API 429 速率限制，每處理一個用戶後短暫延遲
            if i < len(target_users) - 1:
                await asyncio.sleep(.5)
        
        # 組合輸出訊息
        output_parts: list[str] = []
        output_parts.append(t("moderate.multi.summary", done=len(success_logs), total=len(target_users)))
        if success_logs:
            output_parts.append(success_logs[0])  # 只顯示第一位用戶的操作記錄
        all_failed = failed_users + skipped_users
        if all_failed:
            output_parts.append(t("moderate.multi.failed_list") + "\n" + "\n".join(f"- {f}" for f in all_failed))
        
        # 若訊息過長則分段發送
        async def send_chunked(parts: list[str]):
            current = ""
            for part in parts:
                if len(current) + len(part) + 2 > 1900:
                    await interaction.followup.send(current, ephemeral=True)
                    current = part
                else:
                    current = current + "\n\n" + part if current else part
            if current:
                await interaction.followup.send(current, ephemeral=True)
        
        await send_chunked(output_parts)
    
    
    @app_commands.command(name=app_commands.locale_str("multi-moderate-action", i18n_key="cmd.moderate.multi_moderate_action.name"), description=app_commands.locale_str("Take multiple actions on one user", i18n_key="cmd.moderate.multi_moderate_action.desc"))
    @app_commands.describe(user=app_commands.locale_str("Choose a user", i18n_key="cmd.moderate.multi_moderate_action.param.user"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def multi_moderate_action(self, interaction: discord.Interaction, user: discord.Member):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(t("moderate.err.guild_only"), ephemeral=True)
            return
        
        # check bot permissions
        if not guild.me.guild_permissions.administrator:
            await interaction.response.send_message(t("moderate.err.bot_needs_admin"), ephemeral=True)
            return
        
        actions = []  # {"action": "mute/kick/ban/add_role/remove_role", "reason": "reason", "duration": minutes, "role": role_id}
        def actions_to_str(actions):
            if not actions:
                return t("moderate.fallback_none")
            return "\n".join(
                f"- {a['action']}"
                + (t("moderate.multi_action.mute_suffix", minutes=a['duration']) if a['action'] == 'mute' and 'duration' in a else '')
                + (t("moderate.multi_action.role_suffix", role_id=a['role'], role_name=interaction.guild.get_role(a['role']).name) if a['action'] in ['add_role', 'remove_role'] and 'role' in a else '')
                + (f": {a['reason']}" if 'reason' in a else '')
                for a in actions)
        class ActionButtons(i18n.I18nView):
            def __init__(self):
                super().__init__()

            @discord.ui.button(label=i18n.K("moderate.action_name.mute"), style=discord.ButtonStyle.primary, row=0)
            async def mute_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                class MuteModal(i18n.I18nModal, title=i18n.K("moderate.multi_action.mute_modal_title")):
                    minutes = discord.ui.TextInput(label=t("moderate.multi_action.mute_minutes_label"), placeholder=t("moderate.multi_action.mute_minutes_ph"), required=True)
                    reason = discord.ui.TextInput(label=t("moderate.multi_action.mute_reason_label"), placeholder=t("moderate.multi_action.mute_reason_ph"), required=True, max_length=100)
                    async def on_submit(self, interaction: discord.Interaction):
                        if not interaction.user.guild_permissions.administrator:
                            await interaction.response.send_message(t("moderate.err.no_permission"), ephemeral=True)
                            return
                        try:
                            duration = int(self.minutes.value)
                            if duration <= 0:
                                raise ValueError
                        except ValueError:
                            await interaction.response.send_message(t("moderate.err.invalid_mute_minutes"), ephemeral=True)
                            return
                        actions.append({"action": "mute", "duration": duration, "reason": self.reason.value})
                        embed.set_field_at(0, name=t("moderate.field.current_actions"), value=actions_to_str(actions), inline=False)
                        await interaction.response.edit_message(embed=embed, view=view)
                await interaction.response.send_modal(MuteModal())

            @discord.ui.button(label=i18n.K("moderate.action_name.kick"), style=discord.ButtonStyle.danger, row=0)
            async def kick_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                class KickModal(i18n.I18nModal, title=i18n.K("moderate.multi_action.kick_modal_title")):
                    reason = discord.ui.TextInput(label=t("moderate.multi_action.kick_reason_label"), placeholder=t("moderate.multi_action.kick_reason_ph"), required=True, max_length=100)
                    async def on_submit(self, interaction: discord.Interaction):
                        if not interaction.user.guild_permissions.administrator:
                            await interaction.response.send_message(t("moderate.err.no_permission"), ephemeral=True)
                            return
                        actions.append({"action": "kick", "reason": self.reason.value})
                        embed.set_field_at(0, name=t("moderate.field.current_actions"), value=actions_to_str(actions), inline=False)
                        await interaction.response.edit_message(embed=embed, view=view)
                await interaction.response.send_modal(KickModal())

            @discord.ui.button(label=i18n.K("moderate.action_name.ban"), style=discord.ButtonStyle.danger, row=0)
            async def ban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                class BanModal(i18n.I18nModal, title=i18n.K("moderate.multi_action.ban_modal_title")):
                    reason = discord.ui.TextInput(label=t("moderate.multi_action.ban_reason_label"), placeholder=t("moderate.multi_action.ban_reason_ph"), required=True, max_length=100)
                    async def on_submit(self, interaction: discord.Interaction):
                        if not interaction.user.guild_permissions.administrator:
                            await interaction.response.send_message(t("moderate.err.no_permission"), ephemeral=True)
                            return
                        actions.append({"action": "ban", "reason": self.reason.value})
                        embed.set_field_at(0, name=t("moderate.field.current_actions"), value=actions_to_str(actions), inline=False)
                        await interaction.response.edit_message(embed=embed, view=view)
                await interaction.response.send_modal(BanModal())

            @discord.ui.button(label=i18n.K("moderate.multi_action.add_role_btn"), style=discord.ButtonStyle.secondary, row=1)
            async def add_role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                class AddRoleModal(i18n.I18nModal, title=i18n.K("moderate.multi_action.add_role_modal_title")):
                    role = discord.ui.Label(text=t("moderate.multi_action.pick_role_label"), component=discord.ui.RoleSelect(placeholder=t("moderate.multi_action.pick_role_ph"), min_values=1, max_values=1))
                    async def on_submit(self, interaction: discord.Interaction):
                        if not interaction.user.guild_permissions.administrator:
                            await interaction.response.send_message(t("moderate.err.no_permission"), ephemeral=True)
                            return
                        role_id = self.role.component.values[0].id
                        actions.append({"action": "add_role", "role": role_id})
                        embed.set_field_at(0, name=t("moderate.field.current_actions"), value=actions_to_str(actions), inline=False)
                        await interaction.response.edit_message(embed=embed, view=view)
                await interaction.response.send_modal(AddRoleModal())

            @discord.ui.button(label=i18n.K("moderate.multi_action.remove_role_btn"), style=discord.ButtonStyle.secondary, row=1)
            async def remove_role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                class RemoveRoleModal(i18n.I18nModal, title=i18n.K("moderate.multi_action.remove_role_modal_title")):
                    role_name = discord.ui.TextInput(label=t("moderate.multi_action.role_name_label"), placeholder=t("moderate.multi_action.role_name_ph"), required=True, max_length=100)
                    async def on_submit(self, interaction: discord.Interaction):
                        if not interaction.user.guild_permissions.administrator:
                            await interaction.response.send_message(t("moderate.err.no_permission"), ephemeral=True)
                            return
                        role_id = guess_role(interaction.guild, self.role_name.value)
                        if role_id is None:
                            await interaction.response.send_message(t("moderate.err.role_not_found"), ephemeral=True)
                            return
                        actions.append({"action": "remove_role", "role": role_id})
                        embed.set_field_at(0, name=t("moderate.field.current_actions"), value=actions_to_str(actions), inline=False)
                        await interaction.response.edit_message(embed=embed, view=view)
                await interaction.response.send_modal(RemoveRoleModal())


            @discord.ui.button(label=i18n.K("moderate.multi_action.run_announcement_btn"), style=discord.ButtonStyle.success, row=1)
            async def moderation_message(self, interaction: discord.Interaction, button: discord.ui.Button):
                if not interaction.user.guild_permissions.administrator:
                    await interaction.response.send_message(t("moderate.err.no_permission"), ephemeral=True)
                    return
                if not actions:
                    await interaction.response.send_message(t("moderate.err.pick_one_action"), ephemeral=True)
                    return
                actions_with_mention = actions.copy()
                for action in actions_with_mention:
                    if action["action"] in ["add_role", "remove_role"]:
                        role = interaction.guild.get_role(action["role"])
                        if role:
                            action["role"] = role.mention
                        else:
                            action["role"] = str(action["role"])
                await moderation_message_settings(interaction, user, interaction.user, actions_with_mention)

            @discord.ui.button(label=i18n.K("moderate.multi_action.execute_btn"), style=discord.ButtonStyle.success, row=2)
            async def execute_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if not interaction.user.guild_permissions.administrator:
                    await interaction.response.send_message(t("moderate.err.no_permission"), ephemeral=True)
                    return
                if not actions:
                    await interaction.response.send_message(t("moderate.err.pick_one_action"), ephemeral=True)
                    return
                if len(actions) > 5:
                    await interaction.response.send_message(t("moderate.err.too_many_actions"), ephemeral=True)
                    return
                self.stop()
                # execute actions
                results = []
                for action in actions:
                    try:
                        if action["action"] == "mute":
                            duration = action.get("duration", 0)
                            await user.timeout(timedelta(minutes=duration), reason=action.get("reason") or None)
                            results.append(t("moderate.multi_action.mute_result", user=user.mention, duration=get_time_text(duration)))
                        elif action["action"] == "kick":
                            ModerationNotify.ignore_user(user.id)  # 避免重複通知
                            try:
                                await ModerationNotify.notify_user(user, interaction.guild, "踢出", action.get("reason") or None)  # i18n: skip (ModerationNotify action token)
                            except Exception as e:
                                print(f"[!] Failed to DM {user}: {e}")
                            await user.kick(reason=action.get("reason") or None)
                            results.append(t("moderate.multi_action.kick_result", user=user.mention))
                        elif action["action"] == "ban":
                            ModerationNotify.ignore_user(user.id)  # 避免重複通知
                            try:
                                await ModerationNotify.notify_user(user, interaction.guild, "封禁", action.get("reason") or None)  # i18n: skip (ModerationNotify action token)
                            except Exception as e:
                                print(f"[!] Failed to DM {user}: {e}")
                            await user.ban(reason=action.get("reason") or None)
                            results.append(t("moderate.multi_action.ban_result", user=user.mention))
                        elif action["action"] == "add_role":
                            role = interaction.guild.get_role(action["role"])
                            if role:
                                await user.add_roles(role, reason=t("moderate.audit.multi_action"))
                                results.append(t("moderate.multi_action.add_role_result", user=user.mention, role=role.name))
                            else:
                                results.append(t("moderate.multi_action.role_not_found_add", role_id=action['role']))
                        elif action["action"] == "remove_role":
                            role = interaction.guild.get_role(action["role"])
                            if role:
                                await user.remove_roles(role, reason=t("moderate.audit.multi_action"))
                                results.append(t("moderate.multi_action.remove_role_result", user=user.mention, role=role.name))
                            else:
                                results.append(t("moderate.multi_action.role_not_found_remove", role_id=action['role']))
                    except Exception as e:
                        results.append(t("moderate.multi_action.execute_error", action=action['action'], error=e))
                await interaction.response.edit_message(content="\n".join(results), embed=None, view=None)

            @discord.ui.button(label=i18n.K("common.btn.cancel"), style=discord.ButtonStyle.secondary, row=2)
            async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if not interaction.user.guild_permissions.administrator:
                    await interaction.response.send_message(t("moderate.err.no_permission"), ephemeral=True)
                    return
                actions.append({"action": "cancel", "user": user.id})
                self.stop()
                await interaction.response.edit_message(content=t("moderate.multi_action.cancelled"), view=None)

        embed = discord.Embed(title=t("moderate.multi_action.title"), description=t("moderate.multi_action.desc", user=user.name), color=0xff0000)
        embed.add_field(name=t("moderate.field.current_actions"), value=t("moderate.fallback_none"), inline=False)
        view = ActionButtons()
        message = await interaction.response.send_message(embed=embed, view=view)


    @app_commands.command(name=app_commands.locale_str("send-moderation-message", i18n_key="cmd.moderate.send_moderation_message.name"), description=app_commands.locale_str("Manually send a moderation announcement", i18n_key="cmd.moderate.send_moderation_message.desc"))
    @app_commands.describe(user=app_commands.locale_str("Choose a user", i18n_key="cmd.moderate.send_moderation_message.param.user"), reason=app_commands.locale_str("Reason for the action", i18n_key="cmd.moderate.send_moderation_message.param.reason"), action=app_commands.locale_str("Action taken", i18n_key="cmd.moderate.send_moderation_message.param.action"), moderator=app_commands.locale_str("Acting moderator (optional)", i18n_key="cmd.moderate.send_moderation_message.param.moderator"), direct=app_commands.locale_str("Send directly to the announcement channel without the settings UI", i18n_key="cmd.moderate.send_moderation_message.param.direct"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def send_moderation_message(self, interaction: discord.Interaction, user: Union[discord.Member, discord.User], reason: str, action: str, moderator: discord.Member=None, direct: bool=False):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(t("moderate.err.guild_only"), ephemeral=True)
            return
        if moderator is None:
            moderator = interaction.user
        actions = [{"action": "custom", "custom_action": action, "reason": reason}]
        await moderation_message_settings(interaction, user, moderator, actions, direct=direct, guild=guild)


    @app_commands.command(name=app_commands.locale_str("ban", i18n_key="cmd.moderate.ban.name"), description=app_commands.locale_str("Ban a user", i18n_key="cmd.moderate.ban.desc"))
    @app_commands.describe(user=app_commands.locale_str("Choose a user", i18n_key="cmd.moderate.ban.param.user"), reason=app_commands.locale_str("Ban reason (optional)", i18n_key="cmd.moderate.ban.param.reason"), duration=app_commands.locale_str("Ban duration (optional, default: permanent)", i18n_key="cmd.moderate.ban.param.duration"), delete_message=app_commands.locale_str("Message deletion period (optional, default: none)", i18n_key="cmd.moderate.ban.param.delete_message"), send_moderation_message=app_commands.locale_str("Also send a moderation announcement", i18n_key="cmd.moderate.ban.param.send_moderation_message"))
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.default_permissions(ban_members=True)
    async def ban_user(self, interaction: discord.Interaction, user: Union[discord.Member, discord.User], reason: str = None, duration: str = "", delete_message: str = "", send_moderation_message: bool = False):
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(t("moderate.err.guild_only"), ephemeral=True)
            return
        
        # check bot permissions
        if not guild.me.guild_permissions.ban_members:
            await interaction.followup.send(t("moderate.err.bot_missing_ban_perm"), ephemeral=True)
            return

        # 檢查身份組階層（ban 的目標可能是不在伺服器的 User，此時跳過）
        ok, msg = check_member_hierarchy(interaction.user, user, guild.me)
        if not ok:
            await interaction.followup.send(msg, ephemeral=True)
            return

        # 解析封禁時間（可選，若提供則記錄 unban_time）
        unban_time = None
        if duration:
            duration_seconds = timestr_to_seconds(duration)
            if duration_seconds <= 0:
                await interaction.followup.send(t("moderate.err.invalid_ban_duration"), ephemeral=True)
                return
            unban_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

        # 解析要刪除訊息的秒數
        delete_message_seconds = timestr_to_seconds(delete_message)

        success = await ban_user(guild, user, reason, duration=duration_seconds if unban_time else 0, delete_message_seconds=delete_message_seconds)
        if not success:
            await interaction.followup.send(t("moderate.err.ban_failed"))
            return

        if send_moderation_message:
            moderator = interaction.user
            actions_json = [{"action": "ban", "duration": duration_seconds if unban_time else 0, "reason": reason}]
            try:
                await moderation_message_settings(None, user, moderator, actions_json, direct=True, guild=guild)
            except Exception as e:
                pass

        mention = user.mention if user else f"<@{user.id}>"
        parts = [t("moderate.msg.banned", user=mention)]
        if reason:
            parts.append(t("moderate.msg.reason_line", reason=reason))
        if unban_time:
            parts.append(t("moderate.msg.ban_duration_line", duration=get_time_text(duration_seconds)))
        if delete_message_seconds > 0:
            parts.append(t("moderate.msg.delete_message_line", duration=get_time_text(delete_message_seconds)))
        await interaction.followup.send("\n".join(parts))


    @app_commands.command(name=app_commands.locale_str("unban", i18n_key="cmd.moderate.unban.name"), description=app_commands.locale_str("Unban a user", i18n_key="cmd.moderate.unban.desc"))
    @app_commands.describe(user=app_commands.locale_str("Choose a user", i18n_key="cmd.moderate.unban.param.user"))
    @app_commands.default_permissions(ban_members=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def unban_user(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(t("moderate.err.guild_only"))
            return
        
        # check bot permissions
        if not guild.me.guild_permissions.ban_members:
            await interaction.followup.send(t("moderate.err.bot_missing_unban_perm"))
            return

        user_id = user.id

        # 執行解封
        try:
            await guild.unban(user, reason=t("moderate.audit.manual_unban"))
            set_user_data(guild.id, user_id, "unban_time", None)
        except Exception as e:
            await interaction.followup.send(t("moderate.err.unban_error", error=e))
            return

        await interaction.followup.send(t("moderate.msg.unbanned", user=f"<@{user_id}>"))


    @app_commands.command(name=app_commands.locale_str("kick", i18n_key="cmd.moderate.kick.name"), description=app_commands.locale_str("Kick a user", i18n_key="cmd.moderate.kick.desc"))
    @app_commands.describe(user=app_commands.locale_str("Choose a user (@mention or ID)", i18n_key="cmd.moderate.kick.param.user"), reason=app_commands.locale_str("Kick reason (optional)", i18n_key="cmd.moderate.kick.param.reason"), send_moderation_message=app_commands.locale_str("Also send a moderation announcement", i18n_key="cmd.moderate.kick.param.send_moderation_message"))
    @app_commands.default_permissions(kick_members=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def kick_user(self, interaction: discord.Interaction, user: discord.Member, reason: str = None, send_moderation_message: bool = False):
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(t("moderate.err.guild_only"))
            return

        # check bot permissions
        if not guild.me.guild_permissions.kick_members:
            await interaction.followup.send(t("moderate.err.bot_missing_kick_perm"))
            return

        # 檢查身份組階層
        ok, msg = check_member_hierarchy(interaction.user, user, guild.me)
        if not ok:
            await interaction.followup.send(msg, ephemeral=True)
            return

        # 解析目標 user id / 取得 Member 物件
        user_id = user.id

        # 通知與忽略
        ModerationNotify.ignore_user(user_id)
        try:
            await ModerationNotify.notify_user(user, guild, "踢出", reason)  # i18n: skip (ModerationNotify action token)
        except Exception:
            pass

        # 執行踢出
        try:
            await user.kick(reason=reason)
        except Exception as e:
            await interaction.followup.send(t("moderate.err.kick_error", error=e))
            return

        if send_moderation_message:
            moderator = interaction.user
            actions_json = [{"action": "kick", "reason": reason}]
            try:
                await moderation_message_settings(None, user, moderator, actions_json, direct=True, guild=guild)
            except Exception as e:
                pass

        suffix = "\n" + t("moderate.msg.reason_line", reason=reason) if reason else ""
        await interaction.followup.send(t("moderate.msg.kicked", user=user.mention) + suffix)


    @app_commands.command(name=app_commands.locale_str("timeout", i18n_key="cmd.moderate.timeout.name"), description=app_commands.locale_str("Time out a user", i18n_key="cmd.moderate.timeout.desc"))
    @app_commands.describe(user=app_commands.locale_str("Choose a user", i18n_key="cmd.moderate.timeout.param.user"), reason=app_commands.locale_str("Timeout reason (optional)", i18n_key="cmd.moderate.timeout.param.reason"), duration=app_commands.locale_str("Timeout duration (optional, default: 10 minutes)", i18n_key="cmd.moderate.timeout.param.duration"), send_moderation_message=app_commands.locale_str("Also send a moderation announcement", i18n_key="cmd.moderate.timeout.param.send_moderation_message"))
    @app_commands.default_permissions(mute_members=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def timeout_user(self, interaction: discord.Interaction, user: discord.Member, reason: str = None, duration: str = "10m", send_moderation_message: bool = False):
        # 先 defer，避免耗時操作導致 interaction 過期
        await interaction.response.defer()

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(t("moderate.err.guild_only"))
            return
        
        # check bot permissions
        if not guild.me.guild_permissions.moderate_members:
            await interaction.followup.send(t("moderate.err.bot_missing_moderate_perm"))
            return

        # 檢查身份組階層
        ok, msg = check_member_hierarchy(interaction.user, user, guild.me)
        if not ok:
            await interaction.followup.send(msg, ephemeral=True)
            return

        # 解析 target
        user_id = user.id

        duration_seconds = timestr_to_seconds(duration)
        if duration_seconds <= 0:
            await interaction.followup.send(t("moderate.err.invalid_mute_duration"))
            return

        # 執行禁言（可能耗時）
        try:
            await user.timeout(timedelta(seconds=duration_seconds), reason=reason)
        except Exception as e:
            print(f"[!] Error muting {user}: {e}")
            await interaction.followup.send(t("moderate.err.mute_error", error=e))
            return

        if send_moderation_message:
            moderator = interaction.user
            actions_json = [{"action": "mute", "duration": duration_seconds, "reason": reason}]
            try:
                await moderation_message_settings(None, user, moderator, actions_json, direct=True, guild=guild)
            except Exception as e:
                pass

        # 使用 followup 送出最終訊息
        suffix = "\n" + t("moderate.msg.reason_line", reason=reason) if reason else ""
        await interaction.followup.send(t("moderate.msg.muted", user=user.mention, duration=get_time_text(duration_seconds)) + suffix)
        
    @app_commands.command(name=app_commands.locale_str("untimeout", i18n_key="cmd.moderate.untimeout.name"), description=app_commands.locale_str("Remove a user's timeout", i18n_key="cmd.moderate.untimeout.desc"))
    @app_commands.describe(user=app_commands.locale_str("Choose a user", i18n_key="cmd.moderate.untimeout.param.user"))
    @app_commands.default_permissions(mute_members=True)
    async def untimeout_user(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer()

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(t("moderate.err.guild_only"))
            return
        
        # check bot permissions
        if not guild.me.guild_permissions.moderate_members:
            await interaction.followup.send(t("moderate.err.bot_missing_moderate_perm"))
            return

        # 檢查身份組階層
        ok, msg = check_member_hierarchy(interaction.user, user, guild.me)
        if not ok:
            await interaction.followup.send(msg, ephemeral=True)
            return

        # 解析 target
        user_id = user.id

        # 執行解除禁言
        try:
            await user.timeout(None, reason=t("moderate.audit.untimeout"))
        except Exception as e:
            print(f"[!] Error untiming out {user}: {e}")
            await interaction.followup.send(t("moderate.err.untimeout_error", error=e))
            return

        await interaction.followup.send(t("moderate.msg.untimed_out", user=user.mention))
        
    @app_commands.command(name=app_commands.locale_str("moderation-message-channel", i18n_key="cmd.moderate.moderation_message_channel.name"), description=app_commands.locale_str("Set the moderation announcement channel", i18n_key="cmd.moderate.moderation_message_channel.desc"))
    @app_commands.describe(channel=app_commands.locale_str("Choose a channel", i18n_key="cmd.moderate.moderation_message_channel.param.channel"))
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def set_moderation_message_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer()
        permissions = channel.permissions_for(interaction.guild.me)
        if not (permissions.send_messages and permissions.view_channel):
            await interaction.followup.send(t("moderate.err.channel_no_send_perm"))
            return
        set_server_config(interaction.guild.id, "MODERATION_MESSAGE_CHANNEL_ID", channel.id)
        warning = ""
        if not permissions.read_message_history:
            warning = "\n" + t("moderate.warn.missing_history_perm")
        await interaction.followup.send(t("moderate.msg.announcement_channel_set", channel=channel.mention) + warning)

    @app_commands.command(name=app_commands.locale_str("moderation-message-format", i18n_key="cmd.moderate.moderation_message_format.name"), description=app_commands.locale_str("Configure the announcement template and case-ID format", i18n_key="cmd.moderate.moderation_message_format.desc"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def set_moderation_message_format(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(t("moderate.err.guild_only"), ephemeral=True)
            return
        current = get_moderation_announcement_config(guild.id)
        owner_id = interaction.user.id

        class ResetFormatView(i18n.I18nView):
            def __init__(self):
                super().__init__(timeout=300)

            async def interaction_check(self, reset_interaction: discord.Interaction) -> bool:
                if reset_interaction.user.id == owner_id:
                    return True
                await reset_interaction.response.send_message(t("moderate.err.not_your_settings"), ephemeral=True)
                return False

            @discord.ui.button(label=i18n.K("moderate.btn.reset_to_default"), style=discord.ButtonStyle.danger)
            async def reset(self, reset_interaction: discord.Interaction, button: discord.ui.Button):
                defaults = default_moderation_announcement_config(locale=i18n.resolve_locale(guild_id=guild.id))
                try:
                    content, announcement_embed, _ = await preview_moderation_announcement(
                        guild,
                        config_value=defaults,
                        user=reset_interaction.user,
                        moderator=reset_interaction.user,
                    )
                except Exception as error:
                    await reset_interaction.response.send_message(t("moderate.err.default_preview_failed", error=error), ephemeral=True)
                    return
                set_server_config(guild.id, MODERATION_ANNOUNCEMENT_CONFIG_KEY, defaults)
                await reset_interaction.response.edit_message(
                    content=content,
                    embed=announcement_embed,
                    view=self,
                )
                await reset_interaction.followup.send(t("moderate.msg.reset_to_default_done"), ephemeral=True)

        class FormatModal(i18n.I18nModal, title=i18n.K("moderate.modal.announcement_format_title")):
            template_input = discord.ui.TextInput(
                label=t("moderate.field.announcement_template"),
                style=discord.TextStyle.paragraph,
                default=current["template"],
                required=True,
                max_length=4000,
            )
            case_format_input = discord.ui.TextInput(
                label=t("moderate.field.case_id_format"),
                placeholder=t("moderate.placeholder.case_id_format"),
                default=current["case_id_format"],
                required=True,
                max_length=100,
            )

            async def on_submit(self, modal_interaction: discord.Interaction):
                proposed = {
                    "template": self.template_input.value,
                    "case_id_format": self.case_format_input.value,
                }
                try:
                    normalized = normalize_moderation_announcement_config(proposed)
                    content, announcement_embed, case_id = await preview_moderation_announcement(
                        guild,
                        config_value=normalized,
                        user=modal_interaction.user,
                        moderator=modal_interaction.user,
                    )
                except Exception as error:
                    await modal_interaction.response.send_message(t("moderate.err.format_invalid", error=error), ephemeral=True)
                    return
                set_server_config(guild.id, MODERATION_ANNOUNCEMENT_CONFIG_KEY, normalized)
                await modal_interaction.response.send_message(
                    t("moderate.msg.format_saved", case_id=case_id),
                    ephemeral=True,
                )
                await modal_interaction.followup.send(
                    content=content,
                    embed=announcement_embed,
                    view=ResetFormatView(),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )

        await interaction.response.send_modal(FormatModal())

    @app_commands.command(name=app_commands.locale_str("custom-action-add", i18n_key="cmd.moderate.custom_action_add.name"), description=app_commands.locale_str("Add or update a server custom moderation action", i18n_key="cmd.moderate.custom_action_add.desc"))
    @app_commands.describe(name=app_commands.locale_str("Custom command name (e.g. ad)", i18n_key="cmd.moderate.custom_action_add.param.name"), action=app_commands.locale_str("The action string to run (e.g. mute 1h advertising, smm)", i18n_key="cmd.moderate.custom_action_add.param.action"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.autocomplete(action=action_input_autocomplete)
    async def custom_action_add(self, interaction: discord.Interaction, name: str, action: str):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(t("moderate.err.guild_only"), ephemeral=True)
            return

        alias_name = name.strip()
        action_str = action.strip()
        if not alias_name or not action_str:
            await interaction.followup.send(t("moderate.err.custom_action_empty"), ephemeral=True)
            return
        if "," in alias_name or any(ch.isspace() for ch in alias_name):
            await interaction.followup.send(t("moderate.err.custom_action_name_whitespace"), ephemeral=True)
            return
        if len(alias_name) > 32:
            await interaction.followup.send(t("moderate.err.custom_action_name_too_long"), ephemeral=True)
            return
        if alias_name.lower() in BUILTIN_ACTIONS:
            await interaction.followup.send(t("moderate.err.custom_action_name_conflict"), ephemeral=True)
            return

        custom_actions = _load_custom_action_strings(guild.id)
        existed_key = _find_custom_action_key(custom_actions, alias_name)
        if existed_key is None and len(custom_actions) >= 10:
            await interaction.followup.send(t("moderate.err.custom_action_limit"), ephemeral=True)
            return

        test_actions = dict(custom_actions)
        if existed_key and existed_key != alias_name:
            test_actions.pop(existed_key, None)
        test_actions[alias_name] = action_str
        try:
            sample_invocation = _custom_action_sample_invocation(alias_name, action_str)
        except ValueError as error:
            await interaction.followup.send(t("moderate.err.custom_action_invalid_args", error=error), ephemeral=True)
            return
        analysis = analyze_action_string(
            sample_invocation,
            guild.id,
            infer_shorthand=False,
            custom_actions_override=test_actions,
        )
        analysis["normalized"] = action_str
        if not analysis["valid"]:
            await interaction.followup.send(
                embed=build_action_preview_embed(analysis),
                ephemeral=True,
            )
            return

        def persist_action(normalized_action: str) -> tuple[str | None, str | None]:
            test_actions = dict(custom_actions)
            if existed_key and existed_key != alias_name:
                test_actions.pop(existed_key, None)
            test_actions[alias_name] = normalized_action
            try:
                sample_invocation = _custom_action_sample_invocation(alias_name, normalized_action)
                expanded = _expand_custom_action_aliases(sample_invocation, test_actions)
            except ValueError as error:
                return None, t("moderate.err.custom_action_save_failed", error=error)
            if len(expanded) > 5:
                return None, t("moderate.err.custom_action_expands_too_many")

            if existed_key and existed_key != alias_name:
                custom_actions.pop(existed_key, None)
            custom_actions[alias_name] = normalized_action
            set_server_config(guild.id, "custom_action_strings", custom_actions)
            action_text_key = "moderate.custom_action.updated" if existed_key is not None else "moderate.custom_action.added"
            return (
                t(action_text_key, name=alias_name, count=len(custom_actions)),
                None,
            )

        if analysis["requires_confirmation"]:
            async def confirm_action(confirm_interaction: discord.Interaction, confirmed: dict):
                message, error = persist_action(confirmed["normalized"])
                if error:
                    await confirm_interaction.response.edit_message(content=error, embed=None, view=None)
                    return
                await confirm_interaction.response.edit_message(
                    content=message,
                    embed=build_action_preview_embed(confirmed, title=t("moderate.custom_action.setup_done_title"), saved=True),
                    view=None,
                )

            await interaction.followup.send(
                embed=build_action_preview_embed(analysis, title=t("moderate.confirm_your_intent_title")),
                view=ActionConfirmationView(interaction.user.id, analysis, confirm_action),
                ephemeral=True,
            )
            return

        message, error = persist_action(analysis["normalized"])
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        await interaction.followup.send(
            content=message,
            embed=build_action_preview_embed(analysis, title=t("moderate.custom_action.setup_done_title"), saved=True),
            ephemeral=True,
        )

    @app_commands.command(name=app_commands.locale_str("custom-action-remove", i18n_key="cmd.moderate.custom_action_remove.name"), description=app_commands.locale_str("Delete a server custom moderation action", i18n_key="cmd.moderate.custom_action_remove.desc"))
    @app_commands.describe(name=app_commands.locale_str("Name of the custom command to delete", i18n_key="cmd.moderate.custom_action_remove.param.name"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def custom_action_remove(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(t("moderate.err.guild_only"), ephemeral=True)
            return

        custom_actions = _load_custom_action_strings(guild.id)
        existed_key = _find_custom_action_key(custom_actions, name)
        if existed_key is None:
            await interaction.followup.send(t("moderate.err.custom_action_not_found"), ephemeral=True)
            return

        removed_value = custom_actions.pop(existed_key)
        set_server_config(guild.id, "custom_action_strings", custom_actions)
        await interaction.followup.send(
            t("moderate.custom_action.removed", name=existed_key, action=removed_value, count=len(custom_actions)),
            ephemeral=True,
        )

    @app_commands.command(name=app_commands.locale_str("custom-action-list", i18n_key="cmd.moderate.custom_action_list.name"), description=app_commands.locale_str("View server custom moderation actions", i18n_key="cmd.moderate.custom_action_list.desc"))
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def custom_action_list(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(t("moderate.err.guild_only"), ephemeral=True)
            return

        custom_actions = _load_custom_action_strings(guild.id)
        if not custom_actions:
            await interaction.response.send_message(t("moderate.custom_action.none"), ephemeral=True)
            return

        lines = [f"`{k}` -> `{v}`" for k, v in custom_actions.items()]
        embed = discord.Embed(title=t("moderate.custom_action.list_title", count=len(custom_actions)), color=0x00b894)
        embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("action-builder", i18n_key="cmd.moderate.action_builder.name"), description=app_commands.locale_str("Build a moderation action command string", i18n_key="cmd.moderate.action_builder.desc"))
    @app_commands.describe(
        action_type=app_commands.locale_str("Action type", i18n_key="cmd.moderate.action_builder.param.action_type"),
        duration=app_commands.locale_str("Duration (for mute/ban), e.g. 10m, 7d; 0 = permanent", i18n_key="cmd.moderate.action_builder.param.duration"),
        delete_message_duration=app_commands.locale_str("Ban only: delete the user's messages from this period, e.g. 1d; 0 = none", i18n_key="cmd.moderate.action_builder.param.delete_message_duration"),
        reason=app_commands.locale_str("Reason (for mute/kick/ban)", i18n_key="cmd.moderate.action_builder.param.reason"),
        message=app_commands.locale_str("Warning message (for delete/warn); {user} inserts the user", i18n_key="cmd.moderate.action_builder.param.message"),
        prepend=app_commands.locale_str("Existing command to prepend before this action (comma-separated actions)", i18n_key="cmd.moderate.action_builder.param.prepend"),
    )
    @app_commands.choices(
        action_type=[
            app_commands.Choice(name=app_commands.locale_str("Delete message", i18n_key="cmd.moderate.action_builder.choice.delete"), value="delete"),
            # app_commands.Choice(name="刪除訊息＋私訊警告", value="delete_dm"),
            app_commands.Choice(name=app_commands.locale_str("Public warning", i18n_key="cmd.moderate.action_builder.choice.warn"), value="warn"),
            # app_commands.Choice(name="私訊警告", value="warn_dm"),
            app_commands.Choice(name=app_commands.locale_str("Mute", i18n_key="cmd.moderate.action_builder.choice.mute"), value="mute"),
            app_commands.Choice(name=app_commands.locale_str("Kick", i18n_key="cmd.moderate.action_builder.choice.kick"), value="kick"),
            app_commands.Choice(name=app_commands.locale_str("Ban", i18n_key="cmd.moderate.action_builder.choice.ban"), value="ban"),
            app_commands.Choice(name=app_commands.locale_str("Send moderation notice", i18n_key="cmd.moderate.action_builder.choice.send_mod_message"), value="send_mod_message"),
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
        # elif action_type == "delete_dm":
        #     parts = ["delete_dm"]
        #     if message:
        #         parts.append(message)
        elif action_type == "warn":
            parts = ["warn"]
            parts.append(message or t("moderate.default_warn_message"))
        # elif action_type == "warn_dm":
        #     parts = ["warn_dm"]
        #     parts.append(message or t("moderate.default_warn_message"))
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
        embed.add_field(
            name=t("moderate.action_builder.usage_field"),
            value=t("moderate.action_builder.usage_value", command=await get_command_mention('multi-moderate')),
            inline=False,
        )
        try:
            preview = await do_action_str(generated, moderator=interaction.user)
            embed.add_field(name=t("moderate.action_builder.preview_field"), value="\n".join(f"• {a}" for a in preview), inline=False)
        except Exception:
            pass
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.command(aliases=["mod", "m", *sorted(BUILTIN_ACTIONS)])
    @commands.has_permissions(ban_members=True, kick_members=True, moderate_members=True, manage_messages=True)
    async def moderate(self, ctx: commands.Context, user: Union[discord.Member, discord.User, None] = None, *, commands_str: str = ""):
        """對用戶進行多重管理操作。

        用法：!moderate <用戶> <指令1> , <指令2> , ...

        指令格式：
        - ban <duration> <delete_messages> <reason>
        - kick <reason>
        - timeout|mute <duration> <reason>
        - delete <warn_message>
        - warn <warn_message>
        - send_mod_message|smm

        範例：
        !moderate @User ban 違規 1d 3600 , mute 30m 注意行為 , delete 請注意你的言論
        !ban @User 1d 3600 違規
        """
        # text command 的路徑不在 choke point 內，需顯式開 scope
        async with i18n.guild_scope(ctx.guild.id if ctx.guild else None, user_id=ctx.author.id):
            invoked_action = ctx.invoked_with.lower()
            if invoked_action in BUILTIN_ACTIONS:
                commands_str = f"{invoked_action} {commands_str}".strip()

            # check bot permissions
            if not ctx.guild.me.guild_permissions.ban_members or not ctx.guild.me.guild_permissions.kick_members or not ctx.guild.me.guild_permissions.manage_messages or not ctx.guild.me.guild_permissions.moderate_members:
                await ctx.send(t("moderate.err.bot_missing_all_perms"))
                return
            if user is None:
                await ctx.send(t("moderate.err.specify_user"))
                return
            if ctx.author.guild_permissions.ban_members is False and ctx.author.guild_permissions.kick_members is False and ctx.author.guild_permissions.moderate_members is False and ctx.author.guild_permissions.manage_messages is False:
                await ctx.send(t("moderate.err.no_permission") + ('\n-# 你傻逼吧你以為你是開發者你就可以濫權？' if ctx.author.id in config('owners') else ''))  # i18n: skip (owner-facing)
                return
            # 檢查身份組階層
            ok, msg = check_member_hierarchy(ctx.author, user, ctx.guild.me)
            if not ok:
                await ctx.send(msg)
                return
            logs = await do_action_str(commands_str, ctx.guild, user, message=None, moderator=ctx.author)
            if len(logs) == 0:
                msg = t("moderate.msg.no_actions_executed")
            elif len(logs) == 1:
                msg = t("moderate.msg.action_complete_one", user=user.name, log=logs[0])
            else:
                msg = t("moderate.msg.action_complete_many", user=user.name, logs="\n- " + "\n- ".join(logs))
            await ctx.send(msg)
            log(msg, module_name="Moderate", guild=ctx.guild)

    @commands.command(aliases=["mr", "mod_reply", *sorted(REPLY_ACTION_ALIASES)])
    @commands.has_permissions(ban_members=True, kick_members=True, moderate_members=True, manage_messages=True)
    async def moderate_reply(self, ctx: commands.Context, *, commands_str: str = ""):
        """對訊息發送者進行多重管理操作。

        用法：!moderate_reply <指令1> , <指令2> , ...

        指令格式：
        - ban <duration> <delete_messages> <reason>
        - kick <reason>
        - timeout|mute <duration> <reason>
        - delete <warn_message>
        - warn <warn_message>
        - send_mod_message|smm

        範例：
        !moderate_reply ban 違規 1d 3600 , mute 30m 注意行為 , delete 請注意你的言論
        !banr 1d 3600 違規
        """
        async with i18n.guild_scope(ctx.guild.id if ctx.guild else None, user_id=ctx.author.id):
            invoked_action = REPLY_ACTION_ALIASES.get(ctx.invoked_with.lower())
            if invoked_action:
                commands_str = f"{invoked_action} {commands_str}".strip()

            # check bot permissions
            if not ctx.guild.me.guild_permissions.ban_members or not ctx.guild.me.guild_permissions.kick_members or not ctx.guild.me.guild_permissions.manage_messages or not ctx.guild.me.guild_permissions.moderate_members:
                await ctx.send(t("moderate.err.bot_missing_all_perms"))
                return
            if ctx.author.guild_permissions.ban_members is False and ctx.author.guild_permissions.kick_members is False and ctx.author.guild_permissions.moderate_members is False and ctx.author.guild_permissions.manage_messages is False:
                await ctx.send(t("moderate.err.no_permission") + ('\n-# 你傻逼吧你以為你是開發者你就可以濫權？' if ctx.author.id in config('owners') else ''))  # i18n: skip (owner-facing)
                return
            if ctx.message.reference is None:
                await ctx.send(t("moderate.err.use_in_reply"))
                return
            try:
                referenced_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            except Exception:
                await ctx.send(t("moderate.err.cannot_fetch_replied_message"))
                return
            user = referenced_message.author
            # 檢查身份組階層
            ok, msg = check_member_hierarchy(ctx.author, user, ctx.guild.me)
            if not ok:
                await ctx.send(msg)
                return
            logs = await do_action_str(commands_str, ctx.guild, user, message=referenced_message, moderator=ctx.author)
            if len(logs) == 0:
                msg = t("moderate.msg.no_actions_executed")
            elif len(logs) == 1:
                msg = t("moderate.msg.action_complete_one", user=user.name, log=logs[0])
            else:
                msg = t("moderate.msg.action_complete_many", user=user.name, logs="\n- " + "\n- ".join(logs))
            await ctx.send(msg)
            log(msg, module_name="Moderate", user=user, guild=ctx.guild)



asyncio.run(bot.add_cog(Moderate(bot)))


# ====== /request 與 /vote 懲處系統 ======

MOD_ACTION_DEFS = {
    "ban":     {"perm": "ban_members"},
    "kick":    {"perm": "kick_members"},
    "timeout": {"perm": "moderate_members"},
}


def _action_name(action: str, *, locale: str | None = None) -> str:
    return t_enum("moderate.action_name", action, locale=locale)
active_requests = set()            # (guild_id, target_id, action)
active_votes = {}                  # (guild_id, target_id) -> VoteView（None 表示建立中佔位）
active_request_initiators = set()  # (guild_id, requester_id)：無權限者同時只能有一個進行中的請求
active_vote_initiators = set()     # (guild_id, initiator_id)：無權限者同時只能有一個進行中的投票
mod_creation_cooldowns = {}        # (guild_id, user_id) -> monotonic 建立時間
MOD_CREATE_COOLDOWN = 60

REQUEST_MODERATION_KEY = "request_moderation"
REQUEST_SETTING_DEFAULTS = {"enabled": False, "max_duration": 0}
VOTE_MODERATION_KEY = "vote_moderation"
VOTE_SETTING_DEFAULTS = {"enabled": False, "threshold": 0, "duration": 600, "max_duration": 0}
MAX_VOTE_DURATION_SECONDS = 86400


def _get_vote_settings(guild_id: int, action: str) -> dict:
    all_settings = get_server_config(guild_id, VOTE_MODERATION_KEY, {}) or {}
    merged = dict(VOTE_SETTING_DEFAULTS)
    merged.update(all_settings.get(action, {}))
    return merged


def _get_request_settings(guild_id: int, action: str) -> dict:
    all_settings = get_server_config(guild_id, REQUEST_MODERATION_KEY, {}) or {}
    merged = dict(REQUEST_SETTING_DEFAULTS)
    merged.update(all_settings.get(action, {}))
    return merged


def _has_mod_bypass(member: discord.Member, perm: str) -> bool:
    """有對應懲處權限或管理員權限者可繞過啟用狀態、冷卻與同時進行數限制。"""
    perms = member.guild_permissions
    return perms.administrator or getattr(perms, perm, False)


def _check_creation_cooldown(guild_id: int, user_id: int) -> int:
    """回傳剩餘冷卻秒數，0 表示可建立。"""
    key = (guild_id, user_id)
    last = mod_creation_cooldowns.get(key)
    if last is None:
        return 0
    elapsed = time.monotonic() - last
    if elapsed >= MOD_CREATE_COOLDOWN:
        mod_creation_cooldowns.pop(key, None)
        return 0
    return int(MOD_CREATE_COOLDOWN - elapsed) + 1


def _check_duration_limit(action: str, duration_seconds: int, max_seconds: int) -> Optional[str]:
    """檢查時長是否超過伺服器設定的上限，回傳錯誤訊息或 None。"""
    if max_seconds <= 0 or action == "kick":
        return None
    name = _action_name(action)
    if action == "ban" and duration_seconds <= 0:
        return t("moderate.err.duration_limit_no_permanent", name=name, max=get_time_text(max_seconds))
    if duration_seconds > max_seconds:
        return t("moderate.err.duration_limit_exceeded", name=name, max=get_time_text(max_seconds))
    return None


def _parse_max_duration_setting(text: str, action: str) -> tuple[Optional[int], Optional[str]]:
    """解析最長時間設定，"0" 表示不限制。回傳 (秒數, 錯誤訊息)。"""
    if text.strip() == "0":
        return 0, None
    seconds = timestr_to_seconds(text)
    if seconds <= 0:
        return None, t("moderate.err.invalid_time_or_zero")
    if action == "timeout" and seconds > MAX_TIMEOUT_SECONDS:
        return None, t("moderate.err.timeout_max_days")
    return seconds, None


def _bot_side_target_check(guild: discord.Guild, target) -> tuple[bool, str]:
    """僅檢查機器人端能否對目標操作（不含執行者階層）。"""
    if not isinstance(target, discord.Member):
        return True, ""
    if target == guild.owner:
        return False, t("moderate.err.cannot_target_owner_action")
    if target.top_role >= guild.me.top_role:
        return False, t("moderate.err.bot_hierarchy_too_low", target=target.mention)
    return True, ""


async def _execute_moderation(
    guild: discord.Guild,
    action: str,
    target_id: int,
    reason: str,
    duration_seconds: int = 0,
    executor: Optional[discord.Member] = None,
    locale: str | None = None,
) -> tuple[bool, str]:
    """執行 request / vote 通過後的懲處動作，回傳 (是否成功, 結果訊息)。

    executor 為 None（投票）或與目標同人（自我懲處）時，跳過執行者階層檢查。
    結果訊息會寫進公開的 request/vote embed，因此以 locale（guild locale）渲染。
    """
    if locale is None:
        locale = i18n.resolve_locale(guild_id=guild.id)
    info = MOD_ACTION_DEFS[action]
    member = guild.get_member(target_id)
    if action == "ban":
        target = member or bot.get_user(target_id)
        if target is None:
            try:
                target = await bot.fetch_user(target_id)
            except Exception:
                return False, t("moderate.err.user_not_found", locale=locale)
    else:
        if member is None:
            return False, t("moderate.err.target_left_guild", locale=locale)
        target = member

    if not getattr(guild.me.guild_permissions, info["perm"], False):
        return False, t("moderate.err.bot_missing_action_perm", locale=locale, name=_action_name(action, locale=locale))
    ok, msg = _bot_side_target_check(guild, target)
    if not ok:
        return False, msg
    if executor is not None and executor.id != target_id:
        ok, msg = check_member_hierarchy(executor, target, guild.me)
        if not ok:
            return False, msg

    if action == "ban":
        ok = await ban_user(guild, target, reason, duration=duration_seconds, moderator=executor)
        if not ok:
            return False, t("moderate.err.ban_failed", locale=locale)
        suffix = t("moderate.suffix.with_duration", locale=locale, duration=get_time_text(duration_seconds, locale=locale)) if duration_seconds > 0 else ""
        return True, t("moderate.exec.ban_result", locale=locale, user=target.mention, suffix=suffix)
    if action == "kick":
        ModerationNotify.ignore_user(target_id)
        try:
            await ModerationNotify.notify_user(member, guild, "踢出", reason)  # i18n: skip (ModerationNotify action token)
        except Exception:
            pass
        try:
            await member.kick(reason=reason)
        except Exception as e:
            return False, t("moderate.err.kick_error", locale=locale, error=e)
        log(f"Kicked {member}, reason: {reason}", module_name="Moderate", guild=guild)
        return True, t("moderate.msg.kicked", locale=locale, user=member.mention)
    # timeout
    try:
        await member.timeout(timedelta(seconds=duration_seconds), reason=reason)
    except Exception as e:
        return False, t("moderate.err.mute_error", locale=locale, error=e)
    log(f"Muted {member} for {duration_seconds}s, reason: {reason}", module_name="Moderate", guild=guild)
    return True, t("moderate.msg.muted", locale=locale, user=member.mention, duration=get_time_text(duration_seconds, locale=locale))


class RequestView(i18n.I18nView):
    """/request 的確認視圖：僅 approver 可確認，requester / approver 可取消。"""

    def __init__(self, *, guild: discord.Guild, requester_id: int, target_id: int,
                 approver_id: int, action: str, reason: str, duration_seconds: int,
                 embed: discord.Embed, timeout: float = 3600):
        super().__init__(timeout=timeout)
        self.guild = guild
        self.requester_id = requester_id
        self.target_id = target_id
        self.approver_id = approver_id
        self.action = action
        self.reason = reason
        self.duration_seconds = duration_seconds
        self.embed = embed
        self.message: Optional[discord.Message] = None
        self.finished = False
        self.locale = i18n.resolve_locale(guild_id=guild.id)

    def _release(self):
        active_requests.discard((self.guild.id, self.target_id, self.action))
        active_request_initiators.discard((self.guild.id, self.requester_id))

    def _disable_buttons(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label=i18n.K("common.btn.confirm"), emoji="✅", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            await interaction.response.send_message(t("moderate.err.request_already_handled"), ephemeral=True)
            return
        if interaction.user.id != self.approver_id:
            await interaction.response.send_message(t("moderate.err.only_approver_confirms", user=f"<@{self.approver_id}>"), ephemeral=True)
            return
        self.finished = True
        await interaction.response.defer()
        ok, msg = await _execute_moderation(
            self.guild, self.action, self.target_id, self.reason,
            self.duration_seconds, executor=interaction.user, locale=self.locale,
        )
        self._disable_buttons()
        self.embed.color = discord.Color.green() if ok else discord.Color.red()
        self.embed.add_field(name=t("moderate.field.result", locale=self.locale), value=msg if ok else f"⚠️ {msg}", inline=False)
        try:
            await interaction.message.edit(embed=self.embed, view=self)
        except Exception:
            pass
        self.stop()
        self._release()

    @discord.ui.button(label=i18n.K("common.btn.cancel"), emoji="❌", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            await interaction.response.send_message(t("moderate.err.request_already_handled"), ephemeral=True)
            return
        if interaction.user.id not in (self.requester_id, self.approver_id):
            await interaction.response.send_message(t("moderate.err.only_requester_or_approver_cancels"), ephemeral=True)
            return
        self.finished = True
        self._disable_buttons()
        self.embed.color = discord.Color.dark_grey()
        self.embed.add_field(name=t("moderate.field.result", locale=self.locale), value=t("moderate.msg.cancelled_by", locale=self.locale, user=interaction.user.mention), inline=False)
        await interaction.response.edit_message(embed=self.embed, view=self)
        self.stop()
        self._release()

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        self._release()
        self._disable_buttons()
        self.embed.color = discord.Color.dark_grey()
        self.embed.add_field(name=t("moderate.field.result", locale=self.locale), value=t("moderate.msg.request_timed_out", locale=self.locale), inline=False)
        if self.message:
            try:
                await self.message.edit(embed=self.embed, view=self)
            except Exception:
                pass


@app_commands.guild_only()
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class ModerationRequest(commands.GroupCog, name=app_commands.locale_str("request", i18n_key="cmd.moderate.request.root.name"), description=app_commands.locale_str("Request confirmation from the target or an authorized admin before acting.", i18n_key="cmd.moderate.request.root.desc")):
    """請求目標本人或有權限的管理員確認執行懲處。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    async def _create_request(self, interaction: discord.Interaction, action: str,
                              user: Union[discord.Member, discord.User],
                              request_to: Optional[discord.Member],
                              reason: Optional[str], duration_seconds: int):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(t("moderate.err.guild_only"), ephemeral=True)
            return
        info = MOD_ACTION_DEFS[action]
        perm = info["perm"]
        guild_locale = i18n.resolve_locale(guild_id=guild.id)
        name = _action_name(action, locale=guild_locale)
        reason_text = reason or t("moderate.not_provided", locale=guild_locale)
        settings = _get_request_settings(guild.id, action)
        bypass = _has_mod_bypass(interaction.user, perm)

        if not settings["enabled"] and not bypass:
            await interaction.response.send_message(t("moderate.err.request_not_enabled", name=_action_name(action)), ephemeral=True)
            return
        err = _check_duration_limit(action, duration_seconds, settings["max_duration"])
        if err and not bypass:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if not bypass:
            remaining = _check_creation_cooldown(guild.id, interaction.user.id)
            if remaining > 0:
                await interaction.response.send_message(
                    t("moderate.err.creation_cooldown", seconds=remaining), ephemeral=True)
                return
            if (guild.id, interaction.user.id) in active_request_initiators:
                await interaction.response.send_message(
                    t("moderate.err.request_already_active"), ephemeral=True)
                return

        if request_to is None:
            if isinstance(user, discord.Member):
                request_to = user
            else:
                await interaction.response.send_message(
                    t("moderate.err.target_not_in_guild"), ephemeral=True)
                return
        if request_to.bot:
            await interaction.response.send_message(t("moderate.err.cannot_request_bot"), ephemeral=True)
            return
        if not (request_to.id == user.id
                or getattr(request_to.guild_permissions, perm, False)
                or request_to.guild_permissions.administrator):
            await interaction.response.send_message(
                t("moderate.err.request_target_not_authorized", user=request_to.mention, name=_action_name(action)), ephemeral=True)
            return
        if not getattr(guild.me.guild_permissions, perm, False):
            await interaction.response.send_message(t("moderate.err.bot_missing_action_perm", name=_action_name(action)), ephemeral=True)
            return
        ok, msg = _bot_side_target_check(guild, user)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        key = (guild.id, user.id, action)
        if key in active_requests:
            await interaction.response.send_message(t("moderate.err.request_target_active"), ephemeral=True)
            return
        active_requests.add(key)
        active_request_initiators.add((guild.id, interaction.user.id))
        mod_creation_cooldowns[(guild.id, interaction.user.id)] = time.monotonic()

        title = t("moderate.request.title", locale=guild_locale, requester=interaction.user.name, name=name, target=user.name)
        if action == "timeout":
            title += " " + get_time_text(duration_seconds, locale=guild_locale)
        embed = discord.Embed(title=title, description=t("moderate.request.confirm_prompt", locale=guild_locale), color=discord.Color.orange())
        embed.add_field(name=t("moderate.field.reason", locale=guild_locale), value=reason_text, inline=False)
        if action == "timeout":
            embed.add_field(name=t("moderate.field.time", locale=guild_locale), value=get_time_text(duration_seconds, locale=guild_locale), inline=False)
        elif action == "ban":
            embed.add_field(name=t("moderate.field.time", locale=guild_locale), value=get_time_text(duration_seconds, locale=guild_locale) if duration_seconds > 0 else t("moderate.duration.permanent", locale=guild_locale), inline=False)

        view = RequestView(
            guild=guild, requester_id=interaction.user.id, target_id=user.id,
            approver_id=request_to.id, action=action, reason=reason_text,
            duration_seconds=duration_seconds, embed=embed,
        )
        try:
            await interaction.response.send_message(
                content=request_to.mention, embed=embed, view=view,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            msg_obj = await interaction.original_response()
        except Exception:
            active_requests.discard(key)
            active_request_initiators.discard((guild.id, interaction.user.id))
            raise
        # 取得可長期編輯的 Message（webhook token 15 分鐘後失效，view 存活 60 分鐘）
        try:
            view.message = await msg_obj.channel.fetch_message(msg_obj.id)
        except Exception:
            view.message = msg_obj

    @app_commands.command(name=app_commands.locale_str("ban", i18n_key="cmd.moderate.request.ban.name"), description=app_commands.locale_str("Request a user ban", i18n_key="cmd.moderate.request.ban.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to ban", i18n_key="cmd.moderate.request.ban.param.user"), request_to=app_commands.locale_str("Who confirms the request (default: the target)", i18n_key="cmd.moderate.request.ban.param.request_to"),
                           reason=app_commands.locale_str("Ban reason (optional)", i18n_key="cmd.moderate.request.ban.param.reason"), duration=app_commands.locale_str("Ban duration (optional, default: permanent)", i18n_key="cmd.moderate.request.ban.param.duration"))
    async def request_ban(self, interaction: discord.Interaction,
                          user: Union[discord.Member, discord.User],
                          request_to: Optional[discord.Member] = None,
                          reason: Optional[str] = None, duration: Optional[str] = None):
        duration_seconds = 0
        if duration:
            duration_seconds = timestr_to_seconds(duration)
            if duration_seconds <= 0:
                await interaction.response.send_message(t("moderate.err.invalid_ban_duration"), ephemeral=True)
                return
        await self._create_request(interaction, "ban", user, request_to, reason, duration_seconds)

    @app_commands.command(name=app_commands.locale_str("kick", i18n_key="cmd.moderate.request.kick.name"), description=app_commands.locale_str("Request a user kick", i18n_key="cmd.moderate.request.kick.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to kick", i18n_key="cmd.moderate.request.kick.param.user"), request_to=app_commands.locale_str("Who confirms the request (default: the target)", i18n_key="cmd.moderate.request.kick.param.request_to"), reason=app_commands.locale_str("Kick reason (optional)", i18n_key="cmd.moderate.request.kick.param.reason"))
    async def request_kick(self, interaction: discord.Interaction, user: discord.Member,
                           request_to: Optional[discord.Member] = None,
                           reason: Optional[str] = None):
        await self._create_request(interaction, "kick", user, request_to, reason, 0)

    @app_commands.command(name=app_commands.locale_str("timeout", i18n_key="cmd.moderate.request.timeout.name"), description=app_commands.locale_str("Request a user timeout", i18n_key="cmd.moderate.request.timeout.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to time out", i18n_key="cmd.moderate.request.timeout.param.user"), request_to=app_commands.locale_str("Who confirms the request (default: the target)", i18n_key="cmd.moderate.request.timeout.param.request_to"),
                           reason=app_commands.locale_str("Timeout reason (optional)", i18n_key="cmd.moderate.request.timeout.param.reason"), duration=app_commands.locale_str("Timeout duration (default: 10 minutes)", i18n_key="cmd.moderate.request.timeout.param.duration"))
    async def request_timeout(self, interaction: discord.Interaction, user: discord.Member,
                              request_to: Optional[discord.Member] = None,
                              reason: Optional[str] = None, duration: str = "10m"):
        duration_seconds = timestr_to_seconds(duration)
        if duration_seconds <= 0:
            await interaction.response.send_message(t("moderate.err.invalid_mute_duration"), ephemeral=True)
            return
        if duration_seconds > MAX_TIMEOUT_SECONDS:
            await interaction.response.send_message(t("moderate.err.timeout_max_days"), ephemeral=True)
            return
        await self._create_request(interaction, "timeout", user, request_to, reason, duration_seconds)

    @app_commands.command(name=app_commands.locale_str("settings", i18n_key="cmd.moderate.request.settings.name"), description=app_commands.locale_str("Configure the request feature (admins only)", i18n_key="cmd.moderate.request.settings.desc"))
    @app_commands.describe(action=app_commands.locale_str("The action to configure", i18n_key="cmd.moderate.request.settings.param.action"), enabled=app_commands.locale_str("Whether to enable it", i18n_key="cmd.moderate.request.settings.param.enabled"),
                           max_duration=app_commands.locale_str("Maximum timeout/ban duration (e.g. 1h, 0 = unlimited)", i18n_key="cmd.moderate.request.settings.param.max_duration"))
    @app_commands.choices(action=[
        app_commands.Choice(name=app_commands.locale_str("Ban", i18n_key="cmd.moderate.request.settings.choice.ban"), value="ban"),
        app_commands.Choice(name=app_commands.locale_str("Kick", i18n_key="cmd.moderate.request.settings.choice.kick"), value="kick"),
        app_commands.Choice(name=app_commands.locale_str("Timeout", i18n_key="cmd.moderate.request.settings.choice.timeout"), value="timeout"),
    ])
    async def request_settings(self, interaction: discord.Interaction,
                               action: Optional[str] = None,
                               enabled: Optional[bool] = None,
                               max_duration: Optional[str] = None):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(t("moderate.err.guild_only"), ephemeral=True)
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(t("moderate.err.admin_only"), ephemeral=True)
            return

        if action is None:
            if enabled is not None or max_duration is not None:
                await interaction.response.send_message(t("moderate.err.need_action_param"), ephemeral=True)
                return
            embed = discord.Embed(title=t("moderate.request.settings_title"), color=0x00b894)
            for act, info in MOD_ACTION_DEFS.items():
                s = _get_request_settings(guild.id, act)
                value = t("moderate.settings.status_line", status=t("moderate.status.enabled" if s['enabled'] else "moderate.status.disabled"))
                if act != "kick":
                    value += "\n" + t("moderate.settings.max_duration_line", max=t("moderate.duration.unlimited") if s['max_duration'] <= 0 else get_time_text(s['max_duration']))
                embed.add_field(name=_action_name(act), value=value, inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        info = MOD_ACTION_DEFS[action]
        if enabled and not getattr(guild.me.guild_permissions, info["perm"], False):
            await interaction.response.send_message(
                t("moderate.err.bot_missing_action_perm_enable", name=_action_name(action)), ephemeral=True)
            return
        max_seconds = None
        if max_duration is not None:
            if action == "kick":
                await interaction.response.send_message(t("moderate.err.kick_no_duration_limit"), ephemeral=True)
                return
            max_seconds, err = _parse_max_duration_setting(max_duration, action)
            if err:
                await interaction.response.send_message(err, ephemeral=True)
                return

        all_settings = get_server_config(guild.id, REQUEST_MODERATION_KEY, {}) or {}
        current = all_settings.setdefault(action, {})
        changes = []
        if enabled is not None:
            current["enabled"] = enabled
            changes.append(t("moderate.settings.status_line", status=t("common.state.enabled" if enabled else "common.state.disabled")))
        if max_seconds is not None:
            current["max_duration"] = max_seconds
            changes.append(t("moderate.settings.max_duration_line", max=t("moderate.duration.unlimited") if max_seconds == 0 else get_time_text(max_seconds)))

        if not changes:
            s = _get_request_settings(guild.id, action)
            lines = [t("moderate.request.current_settings", name=_action_name(action)),
                     "- " + t("moderate.settings.status_line", status=t("moderate.status.enabled" if s['enabled'] else "moderate.status.disabled"))]
            if action != "kick":
                lines.append("- " + t("moderate.settings.max_duration_line", max=t("moderate.duration.unlimited") if s['max_duration'] <= 0 else get_time_text(s['max_duration'])))
            await interaction.response.send_message("\n".join(lines), ephemeral=True)
            return

        set_server_config(guild.id, REQUEST_MODERATION_KEY, all_settings)
        await interaction.response.send_message(
            t("moderate.request.settings_updated", name=_action_name(action)) + "\n- " + "\n- ".join(changes), ephemeral=True)


class VoteView(i18n.I18nView):
    """/vote 的投票視圖：同意達閾值即執行，逾時失敗，允許改票。"""

    def __init__(self, *, guild: discord.Guild, initiator_id: int, target_id: int,
                 action: str, reason: str, duration_seconds: int,
                 threshold: int, embed: discord.Embed, timeout: float):
        super().__init__(timeout=timeout)
        self.guild = guild
        self.initiator_id = initiator_id
        self.target_id = target_id
        self.action = action
        self.reason = reason
        self.duration_seconds = duration_seconds
        self.threshold = threshold
        self.embed = embed
        self.agree = set()
        self.disagree = set()
        self.message: Optional[discord.Message] = None
        self.finished = False
        self.locale = i18n.resolve_locale(guild_id=guild.id)

    def _counts_text(self) -> str:
        return t("moderate.vote.counts", locale=self.locale, agree=len(self.agree), threshold=self.threshold, disagree=len(self.disagree))

    async def _handle_vote(self, interaction: discord.Interaction, agree: bool):
        if self.finished:
            await interaction.response.send_message(t("moderate.err.vote_ended"), ephemeral=True)
            return
        uid = interaction.user.id
        side = self.agree if agree else self.disagree
        other = self.disagree if agree else self.agree
        if uid in side:
            await interaction.response.send_message(t("moderate.err.already_voted"), ephemeral=True)
            return
        other.discard(uid)
        side.add(uid)
        self.embed.set_field_at(-1, name=t("moderate.field.current_votes", locale=self.locale), value=self._counts_text(), inline=False)
        if len(self.agree) >= self.threshold:
            self.finished = True
            await interaction.response.defer()
            await self._finish(success=True)
        else:
            await interaction.response.edit_message(embed=self.embed, view=self)

    async def _finish(self, success: bool):
        for child in self.children:
            child.disabled = True
        self.stop()
        active_votes.pop((self.guild.id, self.target_id), None)
        active_vote_initiators.discard((self.guild.id, self.initiator_id))
        if success:
            ok, msg = await _execute_moderation(
                self.guild, self.action, self.target_id, self.reason, self.duration_seconds, locale=self.locale)
            if ok:
                self.embed.color = discord.Color.green()
                self.embed.add_field(name=t("moderate.field.result", locale=self.locale), value=t("moderate.vote.passed", locale=self.locale, result=msg), inline=False)
            else:
                self.embed.color = discord.Color.red()
                self.embed.add_field(name=t("moderate.field.result", locale=self.locale), value=t("moderate.vote.passed_but_failed", locale=self.locale, result=msg), inline=False)
        else:
            self.embed.color = discord.Color.dark_grey()
            self.embed.add_field(name=t("moderate.field.result", locale=self.locale), value=t("moderate.vote.not_passed", locale=self.locale, threshold=self.threshold), inline=False)
        if self.message:
            try:
                await self.message.edit(embed=self.embed, view=self)
            except Exception:
                pass

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        await self._finish(success=False)

    @discord.ui.button(label=i18n.K("moderate.btn.vote_agree"), emoji="👍", style=discord.ButtonStyle.success)
    async def vote_agree(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_vote(interaction, agree=True)

    @discord.ui.button(label=i18n.K("moderate.btn.vote_disagree"), emoji="👎", style=discord.ButtonStyle.danger)
    async def vote_disagree(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_vote(interaction, agree=False)


@app_commands.guild_only()
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class ModerationVote(commands.GroupCog, name=app_commands.locale_str("vote", i18n_key="cmd.moderate.vote.root.name"), description=app_commands.locale_str("Start a moderation vote; the action runs once approvals reach the threshold.", i18n_key="cmd.moderate.vote.root.desc")):
    """發起懲處投票，同意數達閾值即執行。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    async def _start_vote(self, interaction: discord.Interaction, action: str,
                          user: Union[discord.Member, discord.User],
                          reason: Optional[str], duration_seconds: int):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(t("moderate.err.guild_only"), ephemeral=True)
            return
        info = MOD_ACTION_DEFS[action]
        perm = info["perm"]
        guild_locale = i18n.resolve_locale(guild_id=guild.id)
        name = _action_name(action, locale=guild_locale)
        reason_text = reason or t("moderate.not_provided", locale=guild_locale)
        settings = _get_vote_settings(guild.id, action)
        bypass = _has_mod_bypass(interaction.user, perm)

        if not settings["enabled"] and not bypass:
            await interaction.response.send_message(t("moderate.err.vote_not_enabled", name=_action_name(action)), ephemeral=True)
            return
        err = _check_duration_limit(action, duration_seconds, settings["max_duration"])
        if err and not bypass:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if not bypass:
            remaining = _check_creation_cooldown(guild.id, interaction.user.id)
            if remaining > 0:
                await interaction.response.send_message(
                    t("moderate.err.creation_cooldown", seconds=remaining), ephemeral=True)
                return
            if (guild.id, interaction.user.id) in active_vote_initiators:
                await interaction.response.send_message(
                    t("moderate.err.vote_already_active"), ephemeral=True)
                return
        if not getattr(guild.me.guild_permissions, perm, False):
            await interaction.response.send_message(t("moderate.err.bot_missing_action_perm", name=_action_name(action)), ephemeral=True)
            return
        ok, msg = _bot_side_target_check(guild, user)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        key = (guild.id, user.id)
        if key in active_votes:
            await interaction.response.send_message(t("moderate.err.vote_target_active"), ephemeral=True)
            return
        active_votes[key] = None  # 佔位，避免併發重複發起
        active_vote_initiators.add((guild.id, interaction.user.id))
        mod_creation_cooldowns[(guild.id, interaction.user.id)] = time.monotonic()

        try:
            await interaction.response.defer()

            threshold = settings["threshold"]
            if threshold <= 0:
                # 以頻道近 50 則訊息的不同發言者數（非機器人）的一半為閾值，最低 2 票
                try:
                    authors = {m.author.id async for m in interaction.channel.history(limit=50) if not m.author.bot}
                    threshold = max(2, (len(authors) + 1) // 2)
                except Exception:
                    threshold = 2

            vote_timeout = settings["duration"]
            deadline = datetime.now(timezone.utc) + timedelta(seconds=vote_timeout)

            duration_suffix = ""
            if action == "timeout" or (action == "ban" and duration_seconds > 0):
                duration_suffix = " " + get_time_text(duration_seconds, locale=guild_locale)
            embed = discord.Embed(
                title=t("moderate.vote.title", locale=guild_locale, initiator=interaction.user.name, name=name, target=user.name, suffix=duration_suffix),
                description=t("moderate.vote.prompt", locale=guild_locale, deadline=discord.utils.format_dt(deadline, 'R')),
                color=discord.Color.orange(),
            )
            embed.add_field(name=t("moderate.field.reason", locale=guild_locale), value=reason_text, inline=False)
            if action == "timeout":
                embed.add_field(name=t("moderate.field.time", locale=guild_locale), value=get_time_text(duration_seconds, locale=guild_locale), inline=False)
            elif action == "ban":
                embed.add_field(name=t("moderate.field.time", locale=guild_locale), value=get_time_text(duration_seconds, locale=guild_locale) if duration_seconds > 0 else t("moderate.duration.permanent", locale=guild_locale), inline=False)
            embed.add_field(name=t("moderate.field.current_votes", locale=guild_locale), value=t("moderate.vote.counts", locale=guild_locale, agree=0, threshold=threshold, disagree=0), inline=False)

            view = VoteView(
                guild=guild, initiator_id=interaction.user.id, target_id=user.id,
                action=action, reason=reason_text, duration_seconds=duration_seconds,
                threshold=threshold, embed=embed, timeout=vote_timeout,
            )
            msg_obj = await interaction.followup.send(embed=embed, view=view, wait=True)
        except Exception:
            active_votes.pop(key, None)
            active_vote_initiators.discard((guild.id, interaction.user.id))
            raise
        active_votes[key] = view
        try:
            view.message = await msg_obj.channel.fetch_message(msg_obj.id)
        except Exception:
            view.message = msg_obj

    @app_commands.command(name=app_commands.locale_str("ban", i18n_key="cmd.moderate.vote.ban.name"), description=app_commands.locale_str("Start a ban vote", i18n_key="cmd.moderate.vote.ban.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to ban", i18n_key="cmd.moderate.vote.ban.param.user"), reason=app_commands.locale_str("Ban reason (optional)", i18n_key="cmd.moderate.vote.ban.param.reason"), duration=app_commands.locale_str("Ban duration (optional, default: permanent)", i18n_key="cmd.moderate.vote.ban.param.duration"))
    async def vote_ban(self, interaction: discord.Interaction,
                       user: Union[discord.Member, discord.User],
                       reason: Optional[str] = None, duration: Optional[str] = None):
        duration_seconds = 0
        if duration:
            duration_seconds = timestr_to_seconds(duration)
            if duration_seconds <= 0:
                await interaction.response.send_message(t("moderate.err.invalid_ban_duration"), ephemeral=True)
                return
        await self._start_vote(interaction, "ban", user, reason, duration_seconds)

    @app_commands.command(name=app_commands.locale_str("kick", i18n_key="cmd.moderate.vote.kick.name"), description=app_commands.locale_str("Start a kick vote", i18n_key="cmd.moderate.vote.kick.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to kick", i18n_key="cmd.moderate.vote.kick.param.user"), reason=app_commands.locale_str("Kick reason (optional)", i18n_key="cmd.moderate.vote.kick.param.reason"))
    async def vote_kick(self, interaction: discord.Interaction, user: discord.Member,
                        reason: Optional[str] = None):
        await self._start_vote(interaction, "kick", user, reason, 0)

    @app_commands.command(name=app_commands.locale_str("timeout", i18n_key="cmd.moderate.vote.timeout.name"), description=app_commands.locale_str("Start a timeout vote", i18n_key="cmd.moderate.vote.timeout.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to time out", i18n_key="cmd.moderate.vote.timeout.param.user"), reason=app_commands.locale_str("Timeout reason (optional)", i18n_key="cmd.moderate.vote.timeout.param.reason"), duration=app_commands.locale_str("Timeout duration (default: 10 minutes)", i18n_key="cmd.moderate.vote.timeout.param.duration"))
    async def vote_timeout(self, interaction: discord.Interaction, user: discord.Member,
                           reason: Optional[str] = None, duration: str = "10m"):
        duration_seconds = timestr_to_seconds(duration)
        if duration_seconds <= 0:
            await interaction.response.send_message(t("moderate.err.invalid_mute_duration"), ephemeral=True)
            return
        if duration_seconds > MAX_TIMEOUT_SECONDS:
            await interaction.response.send_message(t("moderate.err.timeout_max_days"), ephemeral=True)
            return
        await self._start_vote(interaction, "timeout", user, reason, duration_seconds)

    @app_commands.command(name=app_commands.locale_str("settings", i18n_key="cmd.moderate.vote.settings.name"), description=app_commands.locale_str("Configure vote moderation (admins only)", i18n_key="cmd.moderate.vote.settings.desc"))
    @app_commands.describe(action=app_commands.locale_str("The action to configure", i18n_key="cmd.moderate.vote.settings.param.action"), enabled=app_commands.locale_str("Whether to enable it", i18n_key="cmd.moderate.vote.settings.param.enabled"),
                           threshold=app_commands.locale_str("Fixed vote threshold (0 = automatic)", i18n_key="cmd.moderate.vote.settings.param.threshold"), vote_duration=app_commands.locale_str("Vote duration (e.g. 10m, default 10 minutes)", i18n_key="cmd.moderate.vote.settings.param.vote_duration"),
                           max_duration=app_commands.locale_str("Maximum timeout/ban duration (e.g. 1h, 0 = unlimited)", i18n_key="cmd.moderate.vote.settings.param.max_duration"))
    @app_commands.choices(action=[
        app_commands.Choice(name=app_commands.locale_str("Ban", i18n_key="cmd.moderate.vote.settings.choice.ban"), value="ban"),
        app_commands.Choice(name=app_commands.locale_str("Kick", i18n_key="cmd.moderate.vote.settings.choice.kick"), value="kick"),
        app_commands.Choice(name=app_commands.locale_str("Timeout", i18n_key="cmd.moderate.vote.settings.choice.timeout"), value="timeout"),
    ])
    async def vote_settings(self, interaction: discord.Interaction,
                            action: Optional[str] = None,
                            enabled: Optional[bool] = None,
                            threshold: Optional[app_commands.Range[int, 0, 100]] = None,
                            vote_duration: Optional[str] = None,
                            max_duration: Optional[str] = None):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(t("moderate.err.guild_only"), ephemeral=True)
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(t("moderate.err.admin_only"), ephemeral=True)
            return

        if action is None:
            if enabled is not None or threshold is not None or vote_duration is not None or max_duration is not None:
                await interaction.response.send_message(t("moderate.err.need_action_param"), ephemeral=True)
                return
            embed = discord.Embed(title=t("moderate.vote.settings_title"), color=0x00b894)
            for act, info in MOD_ACTION_DEFS.items():
                s = _get_vote_settings(guild.id, act)
                value = t("moderate.vote.settings_value",
                          status=t("moderate.status.enabled" if s['enabled'] else "moderate.status.disabled"),
                          threshold=t("moderate.vote.threshold_auto") if s['threshold'] <= 0 else t("moderate.vote.threshold_votes", count=s['threshold']),
                          duration=get_time_text(s['duration']))
                if act != "kick":
                    value += "\n" + t("moderate.settings.max_duration_line", max=t("moderate.duration.unlimited") if s['max_duration'] <= 0 else get_time_text(s['max_duration']))
                embed.add_field(name=_action_name(act), value=value, inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        info = MOD_ACTION_DEFS[action]
        if enabled and not getattr(guild.me.guild_permissions, info["perm"], False):
            await interaction.response.send_message(
                t("moderate.err.bot_missing_action_perm_enable", name=_action_name(action)), ephemeral=True)
            return
        duration_seconds = None
        if vote_duration is not None:
            duration_seconds = timestr_to_seconds(vote_duration)
            if duration_seconds <= 0:
                await interaction.response.send_message(t("moderate.err.invalid_vote_duration"), ephemeral=True)
                return
            if duration_seconds > MAX_VOTE_DURATION_SECONDS:
                await interaction.response.send_message(t("moderate.err.vote_duration_max"), ephemeral=True)
                return
        max_seconds = None
        if max_duration is not None:
            if action == "kick":
                await interaction.response.send_message(t("moderate.err.kick_no_duration_limit"), ephemeral=True)
                return
            max_seconds, err = _parse_max_duration_setting(max_duration, action)
            if err:
                await interaction.response.send_message(err, ephemeral=True)
                return

        all_settings = get_server_config(guild.id, VOTE_MODERATION_KEY, {}) or {}
        current = all_settings.setdefault(action, {})
        changes = []
        if enabled is not None:
            current["enabled"] = enabled
            changes.append(t("moderate.settings.status_line", status=t("common.state.enabled" if enabled else "common.state.disabled")))
        if threshold is not None:
            current["threshold"] = threshold
            changes.append(t("moderate.vote.threshold_line", threshold=t("moderate.vote.threshold_auto") if threshold == 0 else t("moderate.vote.threshold_votes", count=threshold)))
        if duration_seconds is not None:
            current["duration"] = duration_seconds
            changes.append(t("moderate.vote.duration_line", duration=get_time_text(duration_seconds)))
        if max_seconds is not None:
            current["max_duration"] = max_seconds
            changes.append(t("moderate.settings.max_duration_line", max=t("moderate.duration.unlimited") if max_seconds == 0 else get_time_text(max_seconds)))

        if not changes:
            s = _get_vote_settings(guild.id, action)
            lines = [t("moderate.vote.current_settings", name=_action_name(action)),
                     "- " + t("moderate.settings.status_line", status=t("moderate.status.enabled" if s['enabled'] else "moderate.status.disabled")),
                     "- " + t("moderate.vote.threshold_line", threshold=t("moderate.vote.threshold_auto") if s['threshold'] <= 0 else t("moderate.vote.threshold_votes", count=s['threshold'])),
                     "- " + t("moderate.vote.duration_line", duration=get_time_text(s['duration']))]
            if action != "kick":
                lines.append("- " + t("moderate.settings.max_duration_line", max=t("moderate.duration.unlimited") if s['max_duration'] <= 0 else get_time_text(s['max_duration'])))
            await interaction.response.send_message("\n".join(lines), ephemeral=True)
            return

        set_server_config(guild.id, VOTE_MODERATION_KEY, all_settings)
        await interaction.response.send_message(
            t("moderate.vote.settings_updated", name=_action_name(action)) + "\n- " + "\n- ".join(changes), ephemeral=True)


asyncio.run(bot.add_cog(ModerationRequest(bot)))
asyncio.run(bot.add_cog(ModerationVote(bot)))


if __name__ == "__main__":
    start_bot()
