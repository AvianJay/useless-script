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


BLACKLIST_API_URL = "https://kurokusa.nkhost.dev/api/v1/blacklist?status=1"
BLACKLIST_SYNC_INTERVAL_MINUTES = 30
LOCAL_FLAG_LOOKBACK_MONTHS = 3
FLAGGED_USER_ACTION_SOURCES = {"local", "api", "both"}
FLAGGED_USER_LOCAL_MATCH_MODES = {"active", "history"}

if "Moderate" in modules:
    import Moderate
else:
    log("Moderate module not found", level=logging.ERROR, module_name="AutoModerate")

ACTION_INPUT_SUGGESTIONS = (
    Moderate.ACTION_INPUT_SUGGESTIONS
    if "Moderate" in modules
    else [
        ("刪除訊息", "delete"),
        ("公開警告", "warn {user}，請注意你的行為。"),
        ("禁言 10 分鐘", "mute 10m 違規"),
        ("禁言 1 小時", "mute 1h 違規"),
        ("踢出", "kick 違規"),
        ("永久封禁", "ban 0 0 違規"),
    ]
)
FLAGGED_USER_ACTION_INPUT_SUGGESTIONS = (
    [
        (label, value)
        for label, value in Moderate.ACTION_INPUT_SUGGESTIONS
        if Moderate.analyze_member_join_action(value)["valid"]
    ]
    if "Moderate" in modules
    else []
)


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
        raise ValueError("API 回應不是 JSON 物件")
    if payload.get("code") != 200:
        raise ValueError("API 回應 code 不是 200")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("API 回應缺少 data.items")

    normalized: dict[int, list[dict]] = {}
    for item in data["items"]:
        if not isinstance(item, dict):
            raise ValueError("API 黑名單項目格式錯誤")
        raw_user_id = str(item.get("userid", "")).strip()
        if not raw_user_id.isdigit() or int(raw_user_id) <= 0:
            raise ValueError("API 黑名單包含無效 userid")
        try:
            status = int(item.get("status", 1))
        except (TypeError, ValueError) as error:
            raise ValueError("API 黑名單包含無效 status") from error
        if status != 1:
            raise ValueError("status=1 查詢回傳了非有效紀錄")
        reason = item.get("reason")
        reported_at = item.get("reported_at")
        reporter = item.get("reporter")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("API 黑名單包含空白 reason")
        if not isinstance(reported_at, str) or not reported_at.strip():
            raise ValueError("API 黑名單包含無效 reported_at")
        try:
            datetime.fromisoformat(reported_at.strip().replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("API 黑名單包含無效 reported_at") from error
        if not isinstance(reporter, dict):
            raise ValueError("API 黑名單包含無效 reporter")
        reporter_id = str(reporter.get("id", "")).strip()
        reporter_name = reporter.get("name")
        if not reporter_id.isdigit() or not isinstance(reporter_name, str) or not reporter_name.strip():
            raise ValueError("API 黑名單包含無效 reporter 資料")

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
                    "guild_name": row["name"] or "未知伺服器",
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
        suffix = f"\n…另有 {omitted} 筆紀錄未顯示。"
        joined = "\n".join(output)
        return joined[: max(0, limit - len(suffix))].rstrip() + suffix
    return "\n".join(output) or "無"


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
        app_commands.Choice(name=app_commands.locale_str(key), value=key)
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
                    name=f"禁言 {minutes} 分鐘",
                    value=f"mute {minutes}m",
                )
            )

    lowered = current_text.casefold()
    suggestions = (
        [("只通知（清除處置）", "clear"), *FLAGGED_USER_ACTION_INPUT_SUGGESTIONS]
        if setting == "flagged_user-action"
        else ACTION_INPUT_SUGGESTIONS
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
            choices.append(app_commands.Choice(name=f"禁言 {minutes} 分鐘", value=f"mute {minutes}m"))
    lowered = current_text.casefold()
    for label, value in ACTION_INPUT_SUGGESTIONS:
        if lowered and lowered not in label.casefold() and lowered not in value.casefold():
            continue
        if any(choice.value == value for choice in choices):
            continue
        choices.append(app_commands.Choice(name=label, value=value))
    return choices[:25]


def build_action_preview_embed(analysis: dict, *, title: str = "動作預覽", saved: bool = False) -> discord.Embed:
    if not analysis.get("valid"):
        return discord.Embed(
            title="動作指令無效",
            description=analysis.get("error") or "無法解析動作指令。",
            color=discord.Color.red(),
        )

    embed = discord.Embed(
        title=title,
        description="設定已儲存。" if saved else (analysis.get("confirmation") or "請確認解析結果。"),
        color=discord.Color.green() if saved else discord.Color.orange(),
    )
    embed.add_field(name="實際儲存的指令", value=f"```text\n{analysis['normalized']}\n```", inline=False)
    preview = analysis.get("preview") or []
    embed.add_field(
        name="執行預覽",
        value="\n".join(f"{index}. {line}" for index, line in enumerate(preview, 1)) or "沒有可執行的動作",
        inline=False,
    )
    return embed

async def do_action_str(action: str, guild: Optional[discord.Guild] = None, user: Optional[discord.Member] = None, message: Optional[discord.Message] = None):
    """AutoModerate wrapper：以機器人身份執行動作，委派給 Moderate.do_action_str。"""
    # 以 bot 本身作為 moderator，讓 send_mod_message 能在自動處置中正常運作
    moderator = guild.me if guild else None
    return await Moderate.do_action_str(action, guild=guild, user=user, message=message, moderator=moderator)


# 快速設定的處置預設選項（value 為 __custom__ 時會跳出 Modal 讓使用者輸入）
ACTION_PRESETS = [
    *ACTION_INPUT_SUGGESTIONS,
    ("自訂...", "__custom__"),
]


class QuickSetupActionConfirmationView(discord.ui.View):
    def __init__(self, quick_setup_view: "QuickSetupView", analysis: dict, owner_id: int):
        super().__init__(timeout=120)
        self.quick_setup_view = quick_setup_view
        self.analysis = analysis
        self.owner_id = owner_id

        confirm = discord.ui.Button(label="是，使用這個動作", style=discord.ButtonStyle.success)
        confirm.callback = self.confirm
        self.add_item(confirm)
        retry = discord.ui.Button(label="不是，重新輸入", style=discord.ButtonStyle.secondary)
        retry.callback = self.retry
        self.add_item(retry)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("只有原本設定的人可以確認。", ephemeral=True)
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


class CustomActionModal(discord.ui.Modal, title="自訂處置動作"):
    action_input = discord.ui.TextInput(
        label="處置動作指令",
        placeholder="例：mute 30m 刷頻, delete {user} 請勿刷頻",
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
                embed=build_action_preview_embed(analysis, title="確認你的意思"),
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
            return "無"

        mentions = []
        for channel_id in normalized_ids[:10]:
            ch = guild.get_channel(channel_id)
            mentions.append(ch.mention if ch else f"<#{channel_id}>")
        if len(normalized_ids) > 10:
            mentions.append(f"... 共 {len(normalized_ids)} 個")
        return "、".join(mentions)

    def _get_embed(self, guild: discord.Guild):
        embed = discord.Embed(title="⚡ 自動管理快速設定", color=0x5865F2)
        if self.step == 1:
            embed.description = "請選擇要設定的功能："
        elif self.step == 2 and self.feature:
            feat_names = {
                "scamtrap": "🪤 詐騙陷阱",
                "escape_punish": "🏃 逃避責任懲處",
                "too_many_h1": "📢 標題過多",
                "too_many_emojis": "😂 表情符號過多",
                "anti_invite_link": "🔗 邀請連結",
                "anti_uispam": "📲 用戶安裝應用程式濫用",
                "anti_raid": "🚨 防突襲",
                "anti_spam": "🔁 防刷頻",
                "automod_detect": "🛡️ AutoMod 偵測",
                "flagged_user": "🚩 標記用戶加入",
            }
            embed.description = f"正在設定 **{feat_names.get(self.feature, self.feature)}**\n請完成下方選項後點擊「完成設定」。"
            if self.config:
                for k, v in self.config.items():
                    if k == "log_channel" and v:
                        ch = guild.get_channel(int(v))
                        embed.add_field(name="通知頻道", value=ch.mention if ch else v, inline=False)
                    elif k == "channel_id" and v:
                        ch = guild.get_channel(int(v))
                        embed.add_field(name="頻道", value=ch.mention if ch else v, inline=False)
                    elif k == "ignore_channels":
                        embed.add_field(name="忽略頻道", value=self._format_channel_list(guild, v), inline=False)
                    elif k == "allow_current_server":
                        embed.add_field(name="允許本伺服器連結", value="是" if _is_truthy(v) else "否", inline=False)
                    elif k == "action":
                        embed.add_field(name="處置動作", value=f"`{str(v)[:50]}{'...' if len(str(v)) > 50 else ''}`", inline=False)
                    else:
                        embed.add_field(name=k, value=str(v), inline=True)
        return embed

    def _update_components_step1(self):
        self.clear_items()
        opts = [
            discord.SelectOption(label="詐騙陷阱", value="scamtrap", description="蜜罐頻道"),
            discord.SelectOption(label="逃避責任懲處", value="escape_punish", description="禁言期間離開者"),
            discord.SelectOption(label="標題過多", value="too_many_h1", description="Markdown 大標題洗版"),
            discord.SelectOption(label="表情符號過多", value="too_many_emojis", description="過多 emoji"),
            discord.SelectOption(label="邀請連結", value="anti_invite_link", description="偵測 Discord 邀請連結"),
            discord.SelectOption(label="用戶安裝應用程式濫用", value="anti_uispam", description="User Install 濫用"),
            discord.SelectOption(label="防突襲", value="anti_raid", description="大量加入偵測"),
            discord.SelectOption(label="防刷頻", value="anti_spam", description="相似訊息刷頻"),
            discord.SelectOption(label="AutoMod 偵測", value="automod_detect", description="偵測 Discord 原生 AutoMod 觸發"),
            discord.SelectOption(label="標記用戶加入", value="flagged_user", description="本機與 API 標記命中"),
        ]
        sel = discord.ui.Select(placeholder="選擇功能", options=opts)
        sel.callback = self._on_feature_select
        self.add_item(sel)

    def _update_components_step2(self, guild: discord.Guild):
        self.clear_items()
        automod_settings = get_server_config(self.guild_id, "automod", {}).get(self.feature, {})
        defaults = automod_settings.copy()

        if self.feature == "scamtrap":
            ch_sel = discord.ui.ChannelSelect(
                placeholder="選擇陷阱頻道",
                channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                min_values=1, max_values=1,
            )
            ch_sel.callback = self._on_scamtrap_channel
            self.add_item(ch_sel)
        elif self.feature == "anti_invite_link":
            allow_sel = discord.ui.Select(placeholder="是否允許本伺服器邀請連結", options=[
                discord.SelectOption(label="允許", value="True", description="只阻擋其他伺服器的邀請連結"),
                discord.SelectOption(label="不允許", value="False", description="任何 Discord 邀請連結都會觸發"),
            ])
            allow_sel.callback = self._on_invite_allow_current_server_select
            self.add_item(allow_sel)
        elif self.feature == "escape_punish":
            punish_sel = discord.ui.Select(placeholder="懲處方式", options=[
                discord.SelectOption(label="封禁", value="ban", description="永久封禁"),
            ])
            punish_sel.callback = self._on_escape_punish_select
            self.add_item(punish_sel)
            dur_sel = discord.ui.Select(placeholder="封禁時長", options=[
                discord.SelectOption(label="永久", value="0"),
                discord.SelectOption(label="7 天", value="7d"),
                discord.SelectOption(label="30 天", value="30d"),
            ])
            dur_sel.callback = self._on_escape_duration_select
            self.add_item(dur_sel)
            # escape_punish 不需 action
            btn = discord.ui.Button(label="完成設定", style=discord.ButtonStyle.success)
            btn.callback = self._on_finish
            self.add_item(btn)
            return
        elif self.feature == "too_many_h1":
            len_sel = discord.ui.Select(placeholder="最大標題字數", options=[
                discord.SelectOption(label="15", value="15"),
                discord.SelectOption(label="20", value="20"),
                discord.SelectOption(label="30", value="30"),
                discord.SelectOption(label="50", value="50"),
            ])
            len_sel.callback = self._on_h1_length_select
            self.add_item(len_sel)
        elif self.feature == "too_many_emojis":
            emoji_sel = discord.ui.Select(placeholder="最大表情符號數", options=[
                discord.SelectOption(label="5", value="5"),
                discord.SelectOption(label="10", value="10"),
                discord.SelectOption(label="15", value="15"),
                discord.SelectOption(label="20", value="20"),
            ])
            emoji_sel.callback = self._on_emojis_select
            self.add_item(emoji_sel)
        elif self.feature == "anti_uispam":
            cnt_sel = discord.ui.Select(placeholder="時間窗口內最大觸發次數", options=[
                discord.SelectOption(label="3", value="3"),
                discord.SelectOption(label="5", value="5"),
                discord.SelectOption(label="10", value="10"),
            ])
            cnt_sel.callback = self._on_uispam_count_select
            self.add_item(cnt_sel)
            win_sel = discord.ui.Select(placeholder="偵測時間窗口（秒）", options=[
                discord.SelectOption(label="30 秒", value="30"),
                discord.SelectOption(label="60 秒", value="60"),
                discord.SelectOption(label="120 秒", value="120"),
            ])
            win_sel.callback = self._on_uispam_window_select
            self.add_item(win_sel)
        elif self.feature == "anti_raid":
            joins_sel = discord.ui.Select(placeholder="時間窗口內最大加入數", options=[
                discord.SelectOption(label="3", value="3"),
                discord.SelectOption(label="5", value="5"),
                discord.SelectOption(label="10", value="10"),
            ])
            joins_sel.callback = self._on_raid_joins_select
            self.add_item(joins_sel)
            win_sel = discord.ui.Select(placeholder="偵測時間窗口（秒）", options=[
                discord.SelectOption(label="30 秒", value="30"),
                discord.SelectOption(label="60 秒", value="60"),
                discord.SelectOption(label="120 秒", value="120"),
            ])
            win_sel.callback = self._on_raid_window_select
            self.add_item(win_sel)
        elif self.feature == "anti_spam":
            msg_sel = discord.ui.Select(placeholder="最大相似訊息數", options=[
                discord.SelectOption(label="3", value="3"),
                discord.SelectOption(label="5", value="5"),
                discord.SelectOption(label="10", value="10"),
            ])
            msg_sel.callback = self._on_spam_messages_select
            self.add_item(msg_sel)
            win_sel = discord.ui.Select(placeholder="偵測時間窗口（秒）", options=[
                discord.SelectOption(label="30 秒", value="30"),
                discord.SelectOption(label="60 秒", value="60"),
            ])
            win_sel.callback = self._on_spam_window_select
            self.add_item(win_sel)
            sim_sel = discord.ui.Select(placeholder="相似度閾值", options=[
                discord.SelectOption(label="50%", value="50"),
                discord.SelectOption(label="75%", value="75"),
                discord.SelectOption(label="90%", value="90"),
            ])
            sim_sel.callback = self._on_spam_similarity_select
            self.add_item(sim_sel)
        elif self.feature == "automod_detect":
            ch_sel = discord.ui.ChannelSelect(
                placeholder="選擇通知頻道",
                channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                min_values=1, max_values=1,
            )
            ch_sel.callback = self._on_automod_detect_channel
            self.add_item(ch_sel)
        elif self.feature == "flagged_user":
            ch_sel = discord.ui.ChannelSelect(
                placeholder="選擇通知頻道",
                channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                min_values=1,
                max_values=1,
            )
            ch_sel.callback = self._on_automod_detect_channel
            self.add_item(ch_sel)
            source_sel = discord.ui.Select(placeholder="選擇處置來源", options=[
                discord.SelectOption(label="本機與 API", value="both"),
                discord.SelectOption(label="僅本機", value="local"),
                discord.SelectOption(label="僅 API", value="api"),
            ])
            source_sel.callback = self._on_flagged_action_source_select
            self.add_item(source_sel)
            mode_sel = discord.ui.Select(placeholder="選擇本機命中模式", options=[
                discord.SelectOption(label="僅目前標記", value="active"),
                discord.SelectOption(label="三個月內所有紀錄", value="history"),
            ])
            mode_sel.callback = self._on_flagged_local_match_mode_select
            self.add_item(mode_sel)

        if self.feature in AUTOMOD_IGNORE_CHANNEL_FEATURES:
            ignore_sel = discord.ui.ChannelSelect(
                placeholder="選擇要忽略的頻道（可多選）",
                channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                min_values=0, max_values=25,
            )
            ignore_sel.callback = self._on_ignore_channels_select
            self.add_item(ignore_sel)

        action_presets = (
            [*FLAGGED_USER_ACTION_INPUT_SUGGESTIONS, ("自訂...", "__custom__")]
            if self.feature == "flagged_user"
            else ACTION_PRESETS
        )
        action_opts = [discord.SelectOption(label=l, value=v) for l, v in action_presets]
        action_sel = discord.ui.Select(placeholder="處置動作（選一個）", options=action_opts)
        action_sel.callback = self._on_action_select
        self.add_item(action_sel)

        btn = discord.ui.Button(label="完成設定", style=discord.ButtonStyle.success)
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
            await interaction.response.send_message("無效的功能。", ephemeral=True)
            return
        if self.feature == "scamtrap" and "channel_id" not in self.config:
            await interaction.response.send_message("詐騙陷阱請先選擇陷阱頻道。", ephemeral=True)
            return
        if self.feature == "automod_detect" and "log_channel" not in self.config:
            await interaction.response.send_message("AutoMod 偵測請先選擇通知頻道。", ephemeral=True)
            return
        if self.feature == "flagged_user" and "log_channel" not in self.config:
            await interaction.response.send_message("標記用戶加入偵測請先選擇通知頻道。", ephemeral=True)
            return
        if "action" not in self.config and self.feature in ("scamtrap", "too_many_h1", "too_many_emojis", "anti_invite_link", "anti_uispam", "anti_raid", "anti_spam"):
            await interaction.response.send_message("請選擇處置動作。", ephemeral=True)
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

        feat_names = {"scamtrap": "詐騙陷阱", "escape_punish": "逃避責任懲處", "too_many_h1": "標題過多",
                      "too_many_emojis": "表情符號過多", "anti_invite_link": "邀請連結",
                      "anti_uispam": "用戶安裝應用程式濫用",
                      "anti_raid": "防突襲", "anti_spam": "防刷頻", "automod_detect": "AutoMod 偵測",
                      "flagged_user": "標記用戶加入"}
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(title="✅ 設定完成", color=0x00ff00,
                description=f"已完成 **{feat_names.get(self.feature, self.feature)}** 的快速設定並啟用。"),
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


class SaveActionConfirmationView(discord.ui.View):
    def __init__(self, guild_id: int, feature: str, analysis: dict, owner_id: int):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.feature = feature
        self.analysis = analysis
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("只有原本設定的人可以確認。", ephemeral=True)
        return False

    @discord.ui.button(label="是，儲存這個動作", style=discord.ButtonStyle.success)
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
            embed=build_action_preview_embed(self.analysis, title="動作設定完成", saved=True),
            view=None,
        )

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="已取消，沒有變更動作設定。", embed=None, view=None)
    


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class AutoModerate(commands.GroupCog, name=app_commands.locale_str("automod")):
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
            raise RuntimeError("尚未設定 blacklist_api_key")

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
                    raise RuntimeError(f"Blacklist API 回傳 HTTP {response.status}")
                try:
                    payload = await response.json()
                except (aiohttp.ContentTypeError, json.JSONDecodeError) as error:
                    raise ValueError("Blacklist API 回應不是有效 JSON") from error
                return _normalize_blacklist_payload(payload)
        raise RuntimeError("Blacklist API 超過呼叫頻率限制")

    async def sync_blacklist_cache(self) -> bool:
        if not str(config("blacklist_api_key", "") or "").strip():
            self._blacklist_last_error = "尚未設定 API key"
            if not self._missing_blacklist_key_logged:
                log(
                    "Blacklist API 同步未啟用：尚未在 config.json 設定 blacklist_api_key。",
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
                f"Blacklist API 同步失敗，保留最後成功快取: {error}",
                level=logging.ERROR,
                module_name="AutoModerate",
            )
            return False

        self._blacklist_cache = snapshot
        self._blacklist_last_success = datetime.now(timezone.utc)
        self._blacklist_last_error = None
        record_count = sum(len(records) for records in snapshot.values())
        log(
            f"Blacklist API 同步完成：{len(snapshot)} 位用戶、{record_count} 筆有效紀錄。",
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
        
    @app_commands.command(name=app_commands.locale_str("view"), description="查看自動管理設定")
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
            await interaction.response.send_message("自動管理尚未啟用。", ephemeral=True)
            return

        embed = discord.Embed(title="自動管理設定", color=0x00ff00)
        desc = ""
        for key, value in automod_settings.items():
            desc += f"**{key}**:"
            for subkey, subvalue in value.items():
                desc += f"\n - {subkey}: {subvalue}"
            desc += "\n"
        embed.description = desc
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name=app_commands.locale_str("toggle"), description="啟用或停用自動管理設定")
    @app_commands.describe(setting="要啟用或停用的自動管理設定名稱", enable="是否啟用該設定")
    @app_commands.choices(
        setting=[
            app_commands.Choice(name="詐騙陷阱", value="scamtrap"),
            app_commands.Choice(name="逃避責任懲處", value="escape_punish"),
            app_commands.Choice(name="標題過多", value="too_many_h1"),
            app_commands.Choice(name="表情符號過多", value="too_many_emojis"),
            app_commands.Choice(name="邀請連結", value="anti_invite_link"),
            app_commands.Choice(name="用戶安裝應用程式濫用", value="anti_uispam"),
            app_commands.Choice(name="防突襲（大量加入偵測）", value="anti_raid"),
            app_commands.Choice(name="防刷頻", value="anti_spam"),
            app_commands.Choice(name="AutoMod 偵測（原生 AutoMod 觸發）", value="automod_detect"),
            app_commands.Choice(name="標記用戶加入", value="flagged_user"),
        ],
        enable=[
            app_commands.Choice(name="啟用", value="True"),
            app_commands.Choice(name="停用", value="False"),
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
        await interaction.response.send_message(f"已將自動管理設定 '{setting}' 設為 {'啟用' if enable == 'True' else '停用'}。")
        
        if setting == "scamtrap" and enable == "True":
            # settings
            if "channel_id" not in automod_settings.get("scamtrap", {}):
                await interaction.followup.send(f"請注意，詐騙陷阱已啟用，但尚未設定頻道ID。請使用 {await get_command_mention('automod', 'settings')} 來設定頻道ID。", ephemeral=True)
            if "action" not in automod_settings.get("scamtrap", {}):
                await interaction.followup.send(f"請注意，詐騙陷阱已啟用，但尚未設定動作指令。請使用 {await get_command_mention('automod', 'settings')} 來設定動作指令。", ephemeral=True)

        if setting == "automod_detect" and enable == "True":
            if "log_channel" not in automod_settings.get("automod_detect", {}):
                await interaction.followup.send(f"請注意，AutoMod 偵測已啟用，但尚未設定通知頻道。請使用 {await get_command_mention('automod', 'settings')} 來設定 `automod_detect-log_channel`。", ephemeral=True)
        if setting == "flagged_user" and enable == "True":
            if not automod_settings.get("flagged_user", {}).get("log_channel"):
                legacy_channel = get_server_config(guild_id, "flagged_user_onjoin_channel")
                if not legacy_channel:
                    await interaction.followup.send(
                        f"請注意，標記用戶加入偵測已啟用，但尚未設定通知頻道。請使用 {await get_command_mention('automod', 'settings')} 設定 `flagged_user-log_channel`。",
                        ephemeral=True,
                    )
        if setting == "anti_invite_link" and enable == "True":
            if "action" not in automod_settings.get("anti_invite_link", {}):
                await interaction.followup.send(f"請注意，邀請連結偵測已啟用，但尚未設定動作指令。請使用 {await get_command_mention('automod', 'settings')} 來設定 `anti_invite_link-action`。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("quick-setup"), description="互動式快速設定精靈（選單引導）")
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

    @app_commands.command(name=app_commands.locale_str("settings"), description="設定自動管理選項")
    @app_commands.describe(
        setting="要設定的自動管理選項",
        value="選項的值"
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
                and str(value).strip().lower() in {"none", "null", "clear", "無", "清空"}
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
                        embed=build_action_preview_embed(action_analysis, title="確認你的意思"),
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
                await interaction.response.send_message("`allow_current_server` 只接受 true / false。", ephemeral=True)
                return
            value = _is_truthy(normalized_value)
        if setting_base == "flagged_user" and setting_key == "action_source":
            value = str(value).strip().lower()
            if value not in FLAGGED_USER_ACTION_SOURCES:
                await interaction.response.send_message("`action_source` 只接受 local / api / both。", ephemeral=True)
                return
        if setting_base == "flagged_user" and setting_key == "local_match_mode":
            value = str(value).strip().lower()
            if value not in FLAGGED_USER_LOCAL_MATCH_MODES:
                await interaction.response.send_message("`local_match_mode` 只接受 active / history。", ephemeral=True)
                return
        if setting_key == "ignore_channels":
            raw_text = str(value or "").strip()
            if raw_text.lower() in {"none", "null", "clear", "[]"} or raw_text in {"無", "清空"}:
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
                            f"⚠️ 無法解析以下頻道：`{', '.join(invalid_tokens)}`。請使用頻道提及或頻道 ID，並以逗號分隔。",
                            ephemeral=True,
                        )
                        return
                    for channel_id in parsed_channels:
                        if interaction.guild.get_channel(channel_id) is None:
                            await interaction.response.send_message(
                                f"⚠️ 找不到頻道（ID: `{channel_id}`），請確認這些頻道屬於目前伺服器。",
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
                await interaction.response.send_message(f"⚠️ 找不到頻道（ID: `{value}`），請確認輸入是否正確。", ephemeral=True)
                return
            perms = channel_obj.permissions_for(interaction.guild.me)
            if not (perms.view_channel and perms.send_messages):
                await interaction.response.send_message(f"⚠️ 機器人在 {channel_obj.mention} 沒有檢視頻道或發送訊息的權限，請先調整後再設定。", ephemeral=True)
                return
        automod_settings[setting_base][setting_key] = value
        set_server_config(guild_id, "automod", automod_settings)
        if action_analysis is not None:
            await interaction.response.send_message(
                embed=build_action_preview_embed(action_analysis, title="動作設定完成", saved=True),
            )
        else:
            await interaction.response.send_message(f"已將自動管理設定 '{setting}' 設為 {value}。")
    
    @app_commands.command(name=app_commands.locale_str("check-action"), description="檢查自動管理動作指令是否有效")
    @app_commands.describe(action="要檢查的動作指令")
    @app_commands.autocomplete(action=action_autocomplete)
    async def check_automod_action(self, interaction: discord.Interaction, action: str):
        analysis = Moderate.analyze_action_string(action, interaction.guild_id)
        await interaction.response.send_message(
            embed=build_action_preview_embed(
                analysis,
                title="確認你的意思" if analysis["requires_confirmation"] else "動作檢查結果",
            ),
            ephemeral=not analysis["valid"],
        )

    @app_commands.command(name=app_commands.locale_str("action-builder"), description="產生動作指令字串")
    @app_commands.describe(
        action_type="動作類型",
        duration="時長（mute/ban/force_verify 用），如 10m、7d、0 表示永久",
        delete_message_duration="ban 專用：刪除該用戶最近多少時間的訊息，如 1d、0 表示不刪",
        reason="原因（mute/kick/ban 用）",
        message="警告訊息（delete/warn 用），可用 {user} 代表用戶",
        prepend="要接在此動作前面的既有指令（用逗號分隔多個動作時）",
    )
    @app_commands.choices(
        action_type=[
            app_commands.Choice(name="刪除訊息", value="delete"),
            app_commands.Choice(name="刪除訊息＋私訊警告", value="delete_dm"),
            app_commands.Choice(name="公開警告", value="warn"),
            app_commands.Choice(name="私訊警告", value="warn_dm"),
            app_commands.Choice(name="禁言", value="mute"),
            app_commands.Choice(name="踢出", value="kick"),
            app_commands.Choice(name="封禁", value="ban"),
            app_commands.Choice(name="傳送管理通知", value="send_mod_message"),
            app_commands.Choice(name="強制驗證", value="force_verify"),
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
            parts.append(message or "{user}，請注意你的行為。")
        elif action_type == "warn_dm":
            parts = ["warn_dm"]
            parts.append(message or "{user}，請注意你的行為。")
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
            await interaction.response.send_message("錯誤：動作總數不得超過 5 個。", ephemeral=True)
            return

        embed = discord.Embed(title="動作指令產生結果", color=0x00ff00)
        embed.description = f"```\n{generated}\n```"
        embed.add_field(name="使用方式", value=f"複製上方字串，用於 {await get_command_mention('automod', 'settings')} 的 action 值，或 {await get_command_mention('automod', 'setup')} 的 action 參數。", inline=False)
        try:
            preview = await do_action_str(generated)
            embed.add_field(name="預覽效果", value="\n".join(f"• {a}" for a in preview), inline=False)
        except Exception:
            pass
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("scan-flagged-users"), description="掃描伺服器中的標記用戶")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(user="要掃描的用戶，若不指定則掃描所有用戶")
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
            f"本機資料：{'讀取失敗 (' + local_error + ')' if local_error else '已套用三個月內 / ' + feature_config['local_match_mode']}",
            "API 快取：" + (
                "最後成功同步 " + self._blacklist_last_success.astimezone(timezone.utc).isoformat()
                if self._blacklist_last_success
                else "尚未成功同步"
            ),
            "API 最近狀態：" + (
                self._blacklist_last_error
                or ("成功" if self._blacklist_last_success else "尚無同步結果")
            ),
            "",
        ]
        for user_id in sorted(matched_ids):
            member = interaction.guild.get_member(user_id)
            status_lines.append(f"用戶: {member.name if member else '未知用戶'} (ID: {user_id})")
            for entry in local_records.get(user_id, []):
                active_text = "目前標記" if entry["flagged_role"] else "歷史紀錄"
                status_lines.append(
                    f" - [本機/{active_text}] {entry['guild_name']}，標記時間: {entry['flagged_at']}"
                )
            for entry in self._blacklist_cache.get(user_id, []):
                status_lines.append(
                    f" - [API] {entry['reason']}；回報者: {entry['reporter_name']} ({entry['reporter_id']})；時間: {entry['reported_at']}"
                )
            status_lines.append("")

        if not matched_ids:
            await interaction.followup.send("\n".join(status_lines) + "掃描完成，未找到標記用戶。", ephemeral=True)
            return
        file = discord.File(io.StringIO("\n".join(status_lines)), filename="flagged_users.txt")
        await interaction.followup.send(content=f"掃描完成，共找到 {len(matched_ids)} 位標記用戶。", file=file, ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("flagged-user-alert-channel"), description="設置用戶加入伺服器時的通知頻道")
    @app_commands.describe(channel="用於接收用戶加入通知的頻道")
    async def set_flagged_user_onjoin_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages):
            await interaction.response.send_message(f"⚠️ 機器人在 {channel.mention} 沒有檢視頻道或發送訊息的權限，請先調整後再設定。", ephemeral=True)
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
        await interaction.response.send_message(f"已將用戶加入通知頻道設置為 {channel.mention}。")
    
    @app_commands.command(name=app_commands.locale_str("info"), description="查看自動管理功能介紹")
    async def automod_info(self, interaction: discord.Interaction):
        embed = discord.Embed(title="自動管理功能介紹", color=0x5865F2)
        embed.description = (
            "自動管理 (AutoModerate) 提供多種自動化保護功能，協助管理員維護伺服器秩序。\n"
            f"使用 {await get_command_mention('automod', 'quick-setup')} 互動式快速設定（推薦），"
            f"使用 {await get_command_mention('automod', 'setup')} 一次設定某功能的所有選項，"
            f"使用 {await get_command_mention('automod', 'toggle')} 啟用或停用功能，"
            f"使用 {await get_command_mention('automod', 'settings')} 單獨調整參數，"
            f"使用 {await get_command_mention('automod', 'view')} 查看目前設定。"
        )
        embed.add_field(
            name="🪤 詐騙陷阱 (scamtrap)",
            value="設定一個蜜罐頻道，任何在該頻道發送訊息的用戶將被自動處置。\n"
                  "設定項: `channel_id`（頻道）、`action`（處置動作）",
            inline=False
        )
        embed.add_field(
            name="🏃 逃避責任懲處 (escape_punish)",
            value="當用戶在禁言期間離開伺服器時，自動執行額外懲處（如封禁）。\n"
                  "設定項: `punishment`（懲處方式）、`duration`（持續時間）",
            inline=False
        )
        embed.add_field(
            name="📢 標題過多 (too_many_h1)",
            value="偵測訊息中 Markdown 大標題 (`# `) 的總字數過長，防止洗版。\n"
                  "設定項: `max_length`（最大字數，預設20）、`action`、`ignore_channels`（忽略頻道）",
            inline=False
        )
        embed.add_field(
            name="😂 表情符號過多 (too_many_emojis)",
            value="偵測訊息中的表情符號數量（含自訂及 Unicode emoji），超過上限自動處置。\n"
                  "設定項: `max_emojis`（最大數量，預設10）、`action`、`ignore_channels`（忽略頻道）",
            inline=False
        )
        embed.add_field(
            name="🔗 邀請連結 (anti_invite_link)",
            value="偵測 Discord 邀請連結，可選擇是否允許本伺服器的邀請連結。\n"
                  "設定項: `allow_current_server`（是否允許本服邀請）、`action`、`ignore_channels`（忽略頻道）",
            inline=False
        )
        embed.add_field(
            name="📲 用戶安裝應用程式濫用 (anti_uispam)",
            value="偵測用戶透過 User Install 方式觸發的指令頻率，防止濫用。\n"
                  "設定項: `max_count`（最大次數，預設5）、`time_window`（秒，預設60）、`action`、`ignore_channels`（忽略頻道）",
            inline=False
        )
        embed.add_field(
            name="🚨 防突襲 (anti_raid)",
            value="偵測短時間內大量用戶加入伺服器，觸發時對所有新加入者執行處置。\n"
                  "設定項: `max_joins`（最大加入數，預設5）、`time_window`（秒，預設60）、`action`",
            inline=False
        )
        embed.add_field(
            name="🔁 防刷頻 (anti_spam)",
            value="偵測用戶短時間內發送相同或高度相似的訊息。\n"
                  "設定項: `max_messages`（最大訊息數，預設5）、`time_window`（秒，預設30）、`similarity`（相似度閾值 0~100，預設75）、`action`、`ignore_channels`（忽略頻道）",
            inline=False
        )
        embed.add_field(
            name="🛡️ AutoMod 偵測 (automod_detect)",
            value="偵測 Discord 原生 AutoMod 規則被觸發時，發送通知到指定頻道，並可選擇執行額外處置動作。\n"
                  "設定項: `log_channel`（通知頻道）、`action`（額外處置動作，可選）\n"
                  "過濾條件: `filter_rule`（規則名稱過濾，支援多個用 `|` 分隔）、`filter_action_type`（動作類型過濾: block/alert/timeout/block_interactions，支援多個用 `|` 分隔）",
            inline=False
        )
        embed.add_field(
            name="🚩 標記用戶加入 (flagged_user)",
            value="合併本機三個月內標記資料與 Blacklist API 快取，在標記用戶加入時通知並可執行處置。\n"
                  "設定項: `log_channel`（通知頻道）、`action`（可選）、"
                  "`action_source`（local/api/both）、`local_match_mode`（active/history）",
            inline=False,
        )
        embed.add_field(
            name="⚙️ 動作指令語法",
            value="動作可用逗號 `,` 串接，最多5個。可用動作:\n"
                  "`delete` / `delete_dm` — 刪除訊息（可附帶警告）\n"
                  "`warn` / `warn_dm` — 發送警告訊息\n"
                  "`mute <時長>` — 禁言用戶\n"
                  "`kick` — 踢出用戶\n"
                  "`ban <時長> <刪除訊息時長>` — 封禁用戶\n"
                  "`send_mod_message` — 傳送管理通知\n"
                  "`force_verify <時長>` — 強制驗證用戶 (需先啟用網頁驗證) \n"
                  f"使用 {await get_command_mention('automod', 'action-builder')} 產生動作字串，"
                  f"或 {await get_command_mention('automod', 'check-action')} 預覽效果。",
            inline=False
        )
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_automod_action(self, execution: discord.AutoModAction):
        """偵測 Discord 原生 AutoMod 規則被觸發"""
        guild = execution.guild
        if not guild:
            return
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
        rule_name = "未知規則"
        try:
            rule = await guild.fetch_automod_rule(execution.rule_id)
            rule_name = rule.name
        except Exception:
            pass

        # 觸發類型對應名稱
        trigger_type_names = {
            discord.AutoModRuleTriggerType.keyword: "關鍵字",
            discord.AutoModRuleTriggerType.harmful_link: "有害連結",
            discord.AutoModRuleTriggerType.spam: "疑似垃圾訊息",
            discord.AutoModRuleTriggerType.keyword_preset: "預設關鍵字",
            discord.AutoModRuleTriggerType.mention_spam: "提及濫用",
            discord.AutoModRuleTriggerType.member_profile: "用戶個人資料",
        }
        trigger_type_str = trigger_type_names.get(execution.rule_trigger_type, str(execution.rule_trigger_type))

        # 執行動作類型對應名稱
        action_type_names = {
            discord.AutoModRuleActionType.block_message: "封鎖訊息",
            discord.AutoModRuleActionType.send_alert_message: "傳送警報",
            discord.AutoModRuleActionType.timeout: "禁言用戶",
            discord.AutoModRuleActionType.block_member_interactions: "封鎖成員互動",
        }
        executed_action_str = action_type_names.get(execution.action.type, str(execution.action.type))

        # 頻道資訊
        channel_mention = f"<#{execution.channel_id}>" if execution.channel_id else "未知頻道"

        # 建立通知 embed
        embed = discord.Embed(
            title="🛡️ AutoMod 規則觸發",
            color=0xED4245,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="用戶", value=f"{user_mention} (ID: {execution.user_id})", inline=True)
        embed.add_field(name="規則名稱", value=rule_name, inline=True)
        embed.add_field(name="觸發類型", value=trigger_type_str, inline=True)
        embed.add_field(name="執行動作", value=executed_action_str, inline=True)
        embed.add_field(name="頻道", value=channel_mention, inline=True)
        if execution.matched_keyword:
            embed.add_field(name="匹配關鍵字", value=f"`{execution.matched_keyword}`", inline=True)
        if execution.matched_content:
            embed.add_field(name="匹配內容", value=execution.matched_content[:200], inline=False)
        if execution.content:
            embed.add_field(name="訊息內容", value=execution.content[:500], inline=False)

        # 傳送通知到指定頻道
        if log_channel_id:
            log_channel = guild.get_channel(int(log_channel_id))
            if log_channel:
                try:
                    await log_channel.send(embed=embed)
                except Exception as e:
                    log(f"無法傳送 AutoMod 偵測通知到頻道 {log_channel_id}: {e}", level=logging.ERROR, module_name="AutoModerate", guild=guild)

        log(f"AutoMod 規則 '{rule_name}' 被用戶 {execution.user_id} 觸發 (類型: {trigger_type_str}, 動作: {executed_action_str})", module_name="AutoModerate", guild=guild)

        # 如果有設定額外處置動作，先檢查過濾條件是否符合
        if action and member:
            # 規則名稱過濾
            filter_rule = automod_settings["automod_detect"].get("filter_rule", "")
            if filter_rule:
                allowed_rules = [r.strip() for r in filter_rule.split("|") if r.strip()]
                if allowed_rules and rule_name not in allowed_rules:
                    log(f"AutoMod 偵測: 規則 '{rule_name}' 不在過濾清單 {allowed_rules} 中，跳過額外處置。", module_name="AutoModerate", guild=guild)
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
                    log(f"AutoMod 偵測: 動作類型 '{executed_action_str}' 不在過濾清單 {allowed_types} 中，跳過額外處置。", module_name="AutoModerate", guild=guild)
                    return

            try:
                result = await do_action_str(action, guild=guild, user=member)
                res = '\n'.join(result)
                log(f"AutoMod 偵測額外處置: {action}\n執行結果: {res}", module_name="AutoModerate", guild=guild)
            except Exception as e:
                log(f"無法對用戶 {member} 執行 AutoMod 偵測的額外處置: {e}", level=logging.ERROR, module_name="AutoModerate", guild=guild)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild_id = member.guild.id

        # 防突襲檢查
        automod_settings = get_server_config(guild_id, "automod", {})
        if automod_settings.get("anti_raid", {}).get("enabled", False):
            max_joins = int(automod_settings["anti_raid"].get("max_joins", 5))
            time_window = int(automod_settings["anti_raid"].get("time_window", 60))
            action = automod_settings["anti_raid"].get("action", "kick 突襲偵測自動封禁")
            
            now = datetime.now(timezone.utc)
            join_list = _raid_tracker.setdefault(guild_id, [])
            join_list.append((member, now))
            
            # 清除過期的記錄
            join_list[:] = [(m, t) for m, t in join_list if (now - t).total_seconds() < time_window]
            
            if len(join_list) >= max_joins:
                # 觸發 raid 偵測，對所有在時間窗口內加入的用戶執行動作
                raid_members = [m for m, t in join_list]
                log(f"偵測到突襲！{time_window}秒內有 {len(raid_members)} 個用戶加入，開始處理。", module_name="AutoModerate", guild=member.guild)
                for raid_member in raid_members:
                    try:
                        await do_action_str(action, guild=member.guild, user=raid_member)
                        log(f"突襲用戶 {raid_member} 已被處理: {action}", module_name="AutoModerate", user=raid_member, guild=member.guild)
                    except Exception as e:
                        log(f"無法對突襲用戶 {raid_member} 執行處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=raid_member, guild=member.guild)
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
                f"讀取本機標記資料失敗: {type(error).__name__}: {error}",
                level=logging.ERROR,
                module_name="AutoModerate",
                user=member,
                guild=member.guild,
            )
        api_results = list(self._blacklist_cache.get(member.id, []))
        if not local_results and not api_results:
            return

        action_result = "未設定自動處置。"
        action = flagged_config["action"]
        should_act = bool(action) and _action_matches_sources(
            flagged_config["action_source"],
            has_local=bool(local_results),
            has_api=bool(api_results),
        )
        if should_act:
            analysis = Moderate.analyze_member_join_action(action, guild_id)
            if not analysis["valid"] or analysis["requires_confirmation"]:
                action_result = "處置未執行：已儲存的動作不適用於成員加入事件。"
                log(
                    f"標記用戶加入處置設定無效: {analysis.get('error') or analysis.get('confirmation')}",
                    level=logging.ERROR,
                    module_name="AutoModerate",
                    user=member,
                    guild=member.guild,
                )
            else:
                try:
                    result_lines = await do_action_str(action, guild=member.guild, user=member)
                    action_result = "\n".join(result_lines) or "處置已執行。"
                    log(
                        f"標記用戶加入處置完成: {action_result}",
                        module_name="AutoModerate",
                        user=member,
                        guild=member.guild,
                    )
                except Exception as error:
                    action_result = f"處置失敗：{type(error).__name__}"
                    log(
                        f"標記用戶加入處置失敗: {error}",
                        level=logging.ERROR,
                        module_name="AutoModerate",
                        user=member,
                        guild=member.guild,
                    )
        elif action:
            action_result = f"動作來源設定為 {flagged_config['action_source']}，本次來源不符合，未執行。"

        embed = discord.Embed(title="🚩 標記用戶加入伺服器", color=0xff0000)
        embed.add_field(name="用戶", value=f"{member.mention} (ID: {member.id})", inline=False)
        if local_results:
            embed.add_field(
                name=f"本機資料（{len(local_results)} 筆）",
                value=_format_embed_record_lines([
                    "• "
                    + str(result["guild_name"])
                    + f"｜{result['flagged_at']}｜"
                    + ("目前標記" if result["flagged_role"] else "歷史紀錄")
                    for result in local_results
                ]),
                inline=False,
            )
        if api_results:
            embed.add_field(
                name=f"Blacklist API（{len(api_results)} 筆）",
                value=_format_embed_record_lines([
                    f"• {result['reason']}｜回報者: {result['reporter_name']} "
                    f"({result['reporter_id']})｜{result['reported_at']}"
                    for result in api_results
                ]),
                inline=False,
            )
        embed.add_field(name="處置結果", value=action_result[:1024], inline=False)

        channel_id = flagged_config["log_channel"]
        try:
            channel = member.guild.get_channel(int(channel_id)) if channel_id else None
        except (TypeError, ValueError):
            channel = None
        if channel is None:
            log(
                f"標記用戶通知頻道無效: {channel_id}",
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
                f"無法傳送標記用戶加入通知到頻道 {channel_id}: {error}",
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
                            await Moderate.ban_user(member.guild, member, reason="逃避禁言", duration=duration_seconds if duration_seconds > 0 else 0)
                        else:
                            print("[!] Moderate module not loaded, cannot ban user.")
                            raise Exception("Moderate module not loaded")
                    # 好像也就只有 ban 可以用了，我在做什麼呀
                    print(f"[+] 用戶 {member} 因逃避禁言被 {punishment}")
                except Exception as e:
                    print(f"[!] 無法對用戶 {member} 執行懲處: {e}")
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
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
                action = anti_uispam_settings.get("action", "delete {user}，請勿濫用用戶安裝的應用程式指令。, mute 10m 濫用用戶安裝指令")
                
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
                        log(f"用戶 {triggering_user} 因濫用用戶安裝應用程式被處理 (在 {time_window}秒內觸發 {len(user_timestamps)} 次): {action}", module_name="AutoModerate", user=triggering_user, guild=message.guild)
                        # 重置計數器避免重複處罰
                        user_timestamps.clear()
                    except Exception as e:
                        log(f"無法對用戶 {triggering_user} 執行用戶安裝應用程式濫用的處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=triggering_user, guild=message.guild)
        
        
        # 詐騙陷阱檢查
        if automod_settings.get("scamtrap", {}).get("enabled", False):
            scamtrap_channel_id = int(automod_settings["scamtrap"].get("channel_id", 0))
            action = automod_settings["scamtrap"].get("action", "delete {user} 是最後一個被封禁的帳號，不要在這裡講話！, ban {user} 5s 12h [自動封禁] 疑似被盜帳號")
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
                    log(f"詐騙陷阱: 無法對用戶 {target} 執行處理，因為階級不足: {msg}", level=logging.ERROR, module_name="AutoModerate", user=target, guild=message.guild)
                    return
                try:
                    result = await do_action_str(action, guild=message.guild, user=target, message=message)
                    res = '\n'.join(result)
                    # print(f"[+] 用戶 {message.author} 因進入詐騙陷阱頻道被處理: {action}")
                    log(f"用戶 {target} 因進入詐騙陷阱頻道被處理: {action}\n執行結果: {res}", module_name="AutoModerate", user=target, guild=message.guild)
                except Exception as e:
                    # print(f"[!] 無法對用戶 {message.author} 執行詐騙陷阱的處理: {e}")
                    log(f"無法對用戶 {target} 執行詐騙陷阱的處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=target, guild=message.guild)

        if message.author.bot:
            return
        if message.author.guild_permissions.administrator:
            return

        # 邀請連結檢查
        anti_invite_settings = automod_settings.get("anti_invite_link", {})
        if anti_invite_settings.get("enabled", False) and not _is_ignored_channel(anti_invite_settings, message_channel_id):
            allow_current_server = _is_truthy(anti_invite_settings.get("allow_current_server", False))
            action = anti_invite_settings.get("action", "delete {user}，請勿發送其他伺服器的邀請連結。")
            external_invite_codes = await _get_external_invite_codes(message, guild_id, allow_current_server)
            if external_invite_codes:
                try:
                    await do_action_str(action, guild=message.guild, user=message.author, message=message)
                    log(
                        f"用戶 {message.author} 因發送邀請連結被處理 (允許本服連結: {allow_current_server}, 觸發代碼: {', '.join(external_invite_codes)}): {action}",
                        module_name="AutoModerate",
                        user=message.author,
                        guild=message.guild,
                    )
                except Exception as e:
                    log(
                        f"無法對用戶 {message.author} 執行邀請連結的處理: {e}",
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
                    log(f"用戶 {message.author} 因標題長度過長被處理: {action}", module_name="AutoModerate", user=message.author, guild=message.guild)
                except Exception as e:
                    # print(f"[!] 無法對用戶 {message.author} 執行標題過多的處理: {e}")
                    log(f"無法對用戶 {message.author} 執行標題過多的處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=message.author, guild=message.guild)
        
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
                    log(f"用戶 {message.author} 因表情符號過多被處理: {action}", module_name="AutoModerate", user=message.author, guild=message.guild)
                except Exception as e:
                    log(f"無法對用戶 {message.author} 執行表情符號過多的處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=message.author, guild=message.guild)
        
        # 刷頻偵測檢查
        anti_spam_settings = automod_settings.get("anti_spam", {})
        if anti_spam_settings.get("enabled", False) and not _is_ignored_channel(anti_spam_settings, message_channel_id):
            max_messages = int(anti_spam_settings.get("max_messages", 5))
            time_window = int(anti_spam_settings.get("time_window", 30))
            similarity_threshold = int(anti_spam_settings.get("similarity", 75)) / 100.0
            action = anti_spam_settings.get("action", "mute 10m 刷頻自動禁言, delete {user}，請勿刷頻。")
            
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
                        log(f"用戶 {message.author} 因刷頻被處理 (在 {time_window}秒內發送 {similar_count + 1} 條相似訊息): {action}", module_name="AutoModerate", user=message.author, guild=message.guild)
                        # 重置計數器避免重複處罰
                        user_history.clear()
                    except Exception as e:
                        log(f"無法對用戶 {message.author} 執行刷頻的處理: {e}", level=logging.ERROR, module_name="AutoModerate", user=message.author, guild=message.guild)

asyncio.run(bot.add_cog(AutoModerate(bot)))

if __name__ == "__main__":
    start_bot()
