from globalenv import (
    bot, get_server_config, set_server_config,
    get_all_server_config_key, config, modules,
)
from logger import log
import discord
from discord import app_commands
from discord.ext import commands
import chat_exporter
import asyncio
import io
import re
import uuid
import logging
from datetime import datetime, timezone

ACTIVE_TICKETS_KEY = "tickets_active"
COUNTER_KEY = "tickets_counter"
PANEL_MESSAGE_KEY = "ticket_panel_message"
TICKET_TYPES_KEY = "ticket_types"
TRANSCRIPT_MESSAGE_LIMIT = 2000
TRANSCRIPT_CACHE_TTL = 3600
MAX_TICKET_TYPES = 10

BUTTON_STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}

_guild_locks: dict[int, asyncio.Lock] = {}
_closing_channels: set[int] = set()


def _get_lock(guild_id: int) -> asyncio.Lock:
    lock = _guild_locks.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        _guild_locks[guild_id] = lock
    return lock


# ============= Server config helpers =============

def get_active_tickets(guild_id: int) -> list:
    tickets = get_server_config(guild_id, ACTIVE_TICKETS_KEY, [])
    return tickets if isinstance(tickets, list) else []


def save_active_tickets(guild_id: int, tickets: list):
    set_server_config(guild_id, ACTIVE_TICKETS_KEY, tickets)


def find_ticket(guild_id: int, channel_id: int) -> dict | None:
    for entry in get_active_tickets(guild_id):
        if int(entry.get("channel_id", 0)) == channel_id:
            return entry
    return None


def resolve_ticket(guild_id: int, channel) -> dict | None:
    """Find the ticket entry for a channel; fall back to parsing the topic."""
    entry = find_ticket(guild_id, channel.id)
    if entry:
        return entry
    topic = getattr(channel, "topic", None) or ""
    match = re.search(r"票口 #(\d+)｜開啟者: (\d+)", topic)
    if match:
        return {
            "channel_id": channel.id,
            "owner_id": int(match.group(2)),
            "claimed_by": None,
            "subject": "",
            "opened_at": None,
            "number": int(match.group(1)),
            "message_id": None,
        }
    return None


# ============= Ticket types =============
# 類別為選填；未設定任何類別時面板顯示單一預設按鈕。
# 每個類別可覆寫分類/客服身分組/歡迎訊息，未覆寫的欄位使用全域設定。

def get_ticket_types(guild_id: int) -> list[dict]:
    types = get_server_config(guild_id, TICKET_TYPES_KEY, [])
    return [t for t in types if isinstance(t, dict) and t.get("id")] if isinstance(types, list) else []


def save_ticket_types(guild_id: int, types: list[dict]):
    set_server_config(guild_id, TICKET_TYPES_KEY, types)


def find_ticket_type(guild_id: int, type_id: str) -> dict | None:
    for t in get_ticket_types(guild_id):
        if t.get("id") == type_id:
            return t
    return None


def effective_category_id(guild_id: int, ticket_type: dict | None) -> int | None:
    if ticket_type and ticket_type.get("category_id"):
        return int(ticket_type["category_id"])
    value = get_server_config(guild_id, "ticket_category", None)
    return int(value) if value else None


def effective_staff_role_ids(guild_id: int, ticket_type: dict | None) -> list[int]:
    role_ids = get_staff_role_ids(guild_id)
    if ticket_type:
        for role_id in ticket_type.get("staff_roles") or []:
            try:
                role_id = int(role_id)
            except (TypeError, ValueError):
                continue
            if role_id not in role_ids:
                role_ids.append(role_id)
    return role_ids


def effective_welcome(guild_id: int, ticket_type: dict | None) -> str:
    if ticket_type and ticket_type.get("welcome_message"):
        return str(ticket_type["welcome_message"])
    return str(get_server_config(
        guild_id, "ticket_welcome_message",
        "{user} 你好，感謝開啟票口！請詳細描述你的問題。",
    ) or "")


def get_max_per_user(guild_id: int) -> int:
    try:
        value = int(get_server_config(guild_id, "ticket_max_per_user", 1) or 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(10, value))


def get_staff_role_ids(guild_id: int) -> list[int]:
    roles = get_server_config(guild_id, "ticket_staff_roles", [])
    if not isinstance(roles, list):
        return []
    out = []
    for role_id in roles:
        try:
            out.append(int(role_id))
        except (TypeError, ValueError):
            continue
    return out


def is_staff(member: discord.Member, guild_id: int) -> bool:
    if member.guild_permissions.manage_guild:
        return True
    staff_ids = set(get_staff_role_ids(guild_id))
    return any(role.id in staff_ids for role in member.roles)


def is_blacklisted(member: discord.Member, guild_id: int) -> bool:
    roles = get_server_config(guild_id, "ticket_blacklist_roles", [])
    if not isinstance(roles, list):
        return False
    blacklist_ids = set()
    for role_id in roles:
        try:
            blacklist_ids.add(int(role_id))
        except (TypeError, ValueError):
            continue
    return any(role.id in blacklist_ids for role in member.roles)


def count_open_tickets(guild: discord.Guild, user_id: int) -> tuple[int, list[int]]:
    """Count tickets owned by user whose channel still exists. Returns (count, channel_ids)."""
    channel_ids = [
        int(entry["channel_id"])
        for entry in get_active_tickets(guild.id)
        if int(entry.get("owner_id", 0)) == user_id and guild.get_channel(int(entry.get("channel_id", 0)))
    ]
    return len(channel_ids), channel_ids


def precheck_open(guild: discord.Guild, member: discord.Member, ticket_type: dict | None = None) -> str | None:
    """Return an error message if the user cannot open a ticket, else None."""
    if not get_server_config(guild.id, "ticket_enabled", False):
        return "票口系統目前未啟用。"
    if is_blacklisted(member, guild.id):
        return "你無法開啟票口。"
    category_id = effective_category_id(guild.id, ticket_type)
    if not category_id:
        return "管理員尚未設定票口分類。"
    category = guild.get_channel(int(category_id))
    if not isinstance(category, discord.CategoryChannel):
        return "票口分類已不存在，請通知管理員重新設定。"
    bot_perms = category.permissions_for(guild.me)
    if not (bot_perms.view_channel and bot_perms.manage_channels and bot_perms.manage_roles):
        return "機器人在票口分類缺少「管理頻道」或「管理權限」權限，請通知管理員調整。"
    if len(category.channels) >= 50:
        return "票口分類已滿（50 個頻道上限），請通知管理員清理。"
    limit = get_max_per_user(guild.id)
    count, channel_ids = count_open_tickets(guild, member.id)
    if count >= limit:
        mentions = "、".join(f"<#{cid}>" for cid in channel_ids[:5])
        return f"你已開啟 {count} 個票口（上限 {limit}），請先處理現有票口：{mentions}"
    return None


def sanitize_channel_name(name: str, number: int) -> str:
    name = name.strip().lower()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^0-9a-z一-鿿_\-]", "", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    if not name:
        name = f"ticket-{number}"
    return name[:100]


# ============= Ticket creation =============

async def create_ticket(guild: discord.Guild, opener: discord.Member,
                        subject: str, detail: str,
                        ticket_type: dict | None = None) -> discord.TextChannel:
    """Create a ticket channel. Raises TicketError with a user-facing message on failure."""
    guild_id = guild.id
    category = guild.get_channel(effective_category_id(guild_id, ticket_type) or 0)
    if not isinstance(category, discord.CategoryChannel):
        raise TicketError("票口分類已不存在，請通知管理員重新設定。")

    number = int(get_server_config(guild_id, COUNTER_KEY, 0) or 0) + 1
    set_server_config(guild_id, COUNTER_KEY, number)

    member_perms = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True,
        attach_files=True, embed_links=True,
    )
    staff_perms = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True,
        attach_files=True, embed_links=True, manage_messages=True,
    )
    bot_perms = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True,
        manage_channels=True, manage_messages=True,
        attach_files=True, embed_links=True,
    )

    if get_server_config(guild_id, "ticket_inherit_category_permissions", False):
        overwrites = dict(category.overwrites)
    else:
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    overwrites[guild.me] = bot_perms
    overwrites[opener] = member_perms
    for role_id in effective_staff_role_ids(guild_id, ticket_type):
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = staff_perms

    template = str(get_server_config(guild_id, "ticket_name_template", "ticket-{number}") or "ticket-{number}")
    channel_name = sanitize_channel_name(
        template.replace("{number}", str(number)).replace("{user}", opener.name),
        number,
    )
    topic = f"票口 #{number}｜開啟者: {opener.id}｜主題: {subject[:60]}"

    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=topic,
            reason=f"票口 #{number} - {opener}",
        )
    except discord.Forbidden:
        raise TicketError("機器人缺少「管理頻道」權限，無法建立票口。")
    except discord.HTTPException as e:
        log(f"建立票口頻道失敗: {e}", module_name="Ticket", level=logging.ERROR)
        raise TicketError("建立票口頻道時發生錯誤，請稍後再試。")

    embed = discord.Embed(title=f"🎫 票口 #{number}", color=discord.Color.blurple())
    embed.add_field(name="開啟者", value=opener.mention, inline=True)
    if ticket_type:
        embed.add_field(name="類別", value=ticket_type.get("label", "?"), inline=True)
    embed.add_field(name="主題", value=subject or "（未填寫）", inline=True)
    if detail:
        embed.add_field(name="問題描述", value=detail[:1024], inline=False)
    embed.add_field(name="開啟時間", value=discord.utils.format_dt(discord.utils.utcnow(), "f"), inline=True)

    welcome = effective_welcome(guild_id, ticket_type)
    welcome = welcome.replace("{user}", opener.mention).replace("{subject}", subject)

    message = None
    try:
        message = await channel.send(content=welcome, embed=embed, view=TicketControlView())
    except discord.HTTPException as e:
        log(f"發送票口首訊息失敗: {e}", module_name="Ticket", level=logging.ERROR)

    tickets = get_active_tickets(guild_id)
    tickets.append({
        "channel_id": channel.id,
        "owner_id": opener.id,
        "claimed_by": None,
        "subject": subject,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "number": number,
        "message_id": message.id if message else None,
        "type_id": ticket_type.get("id") if ticket_type else None,
    })
    save_active_tickets(guild_id, tickets)
    log(f"票口 #{number} 已建立 (Guild: {guild_id}, User: {opener.id})", module_name="Ticket")
    return channel


class TicketError(Exception):
    """User-facing ticket error."""


# ============= Ticket closing =============

async def build_transcript_file(channel: discord.TextChannel, entry: dict) -> tuple[discord.File | None, int]:
    """Export channel history. Returns (file, message_count). File may be a .txt fallback."""
    messages = [m async for m in channel.history(limit=TRANSCRIPT_MESSAGE_LIMIT)]
    count = len(messages)
    number = entry.get("number", "?")
    try:
        html = await chat_exporter.raw_export(
            channel,
            messages=messages,
            tz_info="Asia/Taipei",
            guild=channel.guild,
            bot=bot,
        )
        if html:
            return discord.File(io.BytesIO(html.encode("utf-8")), filename=f"ticket-{number}.html"), count
    except Exception as e:
        log(f"chat_exporter 逐字稿產生失敗: {e}", module_name="Ticket", level=logging.ERROR)

    # 純文字 fallback
    lines = [f"票口 #{number} 逐字稿（共 {count} 則訊息）", ""]
    for msg in reversed(messages):
        ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"[{ts}] {msg.author} ({msg.author.id}): {msg.content}")
        for att in msg.attachments:
            lines.append(f"    [附件] {att.filename}: {att.url}")
    text = "\n".join(lines)
    return discord.File(io.BytesIO(text.encode("utf-8")), filename=f"ticket-{number}.txt"), count


async def close_ticket(channel: discord.TextChannel, closer: discord.Member, reason: str = None) -> str | None:
    """Close a ticket channel. Returns an error message, or None on success."""
    guild = channel.guild
    entry = resolve_ticket(guild.id, channel)
    if entry is None:
        return "這裡不是有效的票口頻道。"
    if channel.id in _closing_channels:
        return "此票口正在關閉中。"
    _closing_channels.add(channel.id)
    try:
        number = entry.get("number", "?")

        transcript_file, message_count = None, 0
        if channel.permissions_for(guild.me).read_message_history:
            try:
                transcript_file, message_count = await build_transcript_file(channel, entry)
            except discord.HTTPException as e:
                log(f"讀取票口歷史訊息失敗: {e}", module_name="Ticket", level=logging.ERROR)
        else:
            log(f"機器人缺少「讀取訊息歷史」權限，跳過逐字稿 (Channel: {channel.id})", module_name="Ticket", level=logging.WARNING)

        # 發送逐字稿到紀錄頻道
        log_channel_id = get_server_config(guild.id, "ticket_log_channel", None)
        log_channel = guild.get_channel(int(log_channel_id)) if log_channel_id else None
        if log_channel is not None:
            log_perms = log_channel.permissions_for(guild.me)
            if not (log_perms.view_channel and log_perms.send_messages and log_perms.attach_files):
                log(f"機器人在紀錄頻道缺少「發送訊息」或「附加檔案」權限，跳過逐字稿發送 (Guild: {guild.id})", module_name="Ticket", level=logging.WARNING)
                log_channel = None
        if log_channel and transcript_file:
            embed = discord.Embed(title=f"🎫 票口 #{number} 已關閉", color=discord.Color.red())
            embed.add_field(name="開啟者", value=f"<@{entry['owner_id']}>", inline=True)
            claimed_by = entry.get("claimed_by")
            embed.add_field(name="認領者", value=f"<@{claimed_by}>" if claimed_by else "（未認領）", inline=True)
            embed.add_field(name="關閉者", value=closer.mention, inline=True)
            if entry.get("subject"):
                embed.add_field(name="主題", value=entry["subject"], inline=True)
            embed.add_field(name="訊息數", value=str(message_count), inline=True)
            if reason:
                embed.add_field(name="關閉原因", value=reason[:1024], inline=False)
            opened_at = entry.get("opened_at")
            if opened_at:
                try:
                    opened_dt = datetime.fromisoformat(opened_at)
                    duration = datetime.now(timezone.utc) - opened_dt
                    hours, remainder = divmod(int(duration.total_seconds()), 3600)
                    minutes = remainder // 60
                    embed.add_field(name="持續時間", value=f"{hours} 小時 {minutes} 分鐘", inline=True)
                except ValueError:
                    pass
            try:
                log_message = await log_channel.send(embed=embed, file=transcript_file)
                website_url = str(config("website_url", "") or "").rstrip("/")
                if "Website" in modules and website_url and any(
                    att.filename.endswith(".html") for att in log_message.attachments
                ):
                    view = discord.ui.View()
                    view.add_item(discord.ui.Button(
                        label="在網頁檢視逐字稿",
                        emoji="🌐",
                        style=discord.ButtonStyle.link,
                        url=f"{website_url}/tickets/{guild.id}/{log_message.id}",
                    ))
                    await log_message.edit(view=view)
            except (discord.Forbidden, discord.HTTPException) as e:
                log(f"發送票口逐字稿到紀錄頻道失敗: {e}", module_name="Ticket", level=logging.WARNING)

        # 先移除追蹤再刪頻道，讓 on_guild_channel_delete 冪等
        tickets = [t for t in get_active_tickets(guild.id) if int(t.get("channel_id", 0)) != channel.id]
        save_active_tickets(guild.id, tickets)

        try:
            await channel.delete(reason=f"票口 #{number} 由 {closer} 關閉")
        except discord.Forbidden:
            try:
                await channel.send("⚠️ 機器人缺少權限，無法刪除此頻道，請手動刪除。")
            except discord.HTTPException:
                pass
            log(f"無法刪除票口頻道 {channel.id}（缺少權限）", module_name="Ticket", level=logging.WARNING)
            return "逐字稿已保存，但機器人缺少權限刪除頻道，請手動刪除。"

        log(f"票口 #{number} 已由 {closer.id} 關閉 (Guild: {guild.id})", module_name="Ticket")
        return None
    finally:
        _closing_channels.discard(channel.id)


# ============= Claiming =============

async def claim_ticket(interaction: discord.Interaction) -> str | None:
    """Claim the current ticket. Returns an error message, or None on success."""
    guild = interaction.guild
    channel = interaction.channel
    entry = find_ticket(guild.id, channel.id)
    if entry is None:
        return "這裡不是有效的票口頻道。"
    if not is_staff(interaction.user, guild.id):
        return "只有客服人員可以認領票口。"
    if entry.get("claimed_by"):
        return f"此票口已由 <@{entry['claimed_by']}> 認領。"

    tickets = get_active_tickets(guild.id)
    for t in tickets:
        if int(t.get("channel_id", 0)) == channel.id:
            t["claimed_by"] = interaction.user.id
            break
    save_active_tickets(guild.id, tickets)

    # 更新首訊息 embed 的認領者欄位
    message = None
    if interaction.message and interaction.message.embeds:
        message = interaction.message
    elif entry.get("message_id"):
        try:
            message = await channel.fetch_message(int(entry["message_id"]))
        except discord.HTTPException:
            message = None
    if message and message.embeds:
        embed = message.embeds[0]
        for index, field in enumerate(embed.fields):
            if field.name == "認領者":
                embed.set_field_at(index, name="認領者", value=interaction.user.mention, inline=True)
                break
        else:
            embed.add_field(name="認領者", value=interaction.user.mention, inline=True)
        try:
            await message.edit(embed=embed)
        except discord.HTTPException:
            pass

    try:
        await channel.send(f"🙋 {interaction.user.mention} 已認領此票口。")
    except discord.HTTPException:
        pass
    return None


# ============= Views / Modal =============

class TicketOpenModal(discord.ui.Modal, title="開啟票口"):
    subject = discord.ui.TextInput(label="主題", max_length=100, required=True)
    detail = discord.ui.TextInput(
        label="問題描述", style=discord.TextStyle.paragraph,
        max_length=1000, required=False,
    )

    def __init__(self, ticket_type: dict | None = None):
        super().__init__()
        self.ticket_type = ticket_type
        if ticket_type:
            self.title = f"開啟票口 - {str(ticket_type.get('label', ''))[:30]}"

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        async with _get_lock(guild.id):
            error = precheck_open(guild, interaction.user, self.ticket_type)
            if error:
                await interaction.followup.send(error, ephemeral=True)
                return
            try:
                channel = await create_ticket(
                    guild, interaction.user,
                    str(self.subject.value or "").strip(),
                    str(self.detail.value or "").strip(),
                    ticket_type=self.ticket_type,
                )
            except TicketError as e:
                await interaction.followup.send(str(e), ephemeral=True)
                return
        await interaction.followup.send(f"✅ 票口已建立：{channel.mention}", ephemeral=True)


async def handle_open_button(interaction: discord.Interaction, ticket_type: dict | None):
    error = precheck_open(interaction.guild, interaction.user, ticket_type)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    await interaction.response.send_modal(TicketOpenModal(ticket_type))


class TicketOpenButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"ticket_open:(?P<type_id>[0-9a-z]+|default)",
):
    """面板開票按鈕；type_id 編碼在 custom_id 中，重啟後不需逐一重新註冊。"""

    def __init__(self, type_id: str, *, label: str = "開啟票口",
                 style: discord.ButtonStyle = discord.ButtonStyle.primary,
                 emoji: str | None = "🎫"):
        super().__init__(discord.ui.Button(
            label=label, style=style, emoji=emoji,
            custom_id=f"ticket_open:{type_id}",
        ))
        self.type_id = type_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match):
        return cls(match.group("type_id"))

    async def callback(self, interaction: discord.Interaction):
        ticket_type = None
        if self.type_id != "default":
            ticket_type = find_ticket_type(interaction.guild.id, self.type_id)
            if ticket_type is None:
                await interaction.response.send_message(
                    "此票口類別已被移除，請通知管理員重新發布面板。", ephemeral=True,
                )
                return
        await handle_open_button(interaction, ticket_type)


class TicketPanelView(discord.ui.View):
    """舊版單一按鈕面板（保留註冊讓既有面板訊息的按鈕繼續運作）。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="開啟票口", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="ticket_open_button")
    async def open_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_open_button(interaction, None)


def build_panel_view(guild_id: int) -> discord.ui.View:
    """Build the panel view: one button per ticket type, or a single default button."""
    view = discord.ui.View(timeout=None)
    types = get_ticket_types(guild_id)[:MAX_TICKET_TYPES]
    if not types:
        view.add_item(TicketOpenButton("default"))
        return view
    for t in types:
        view.add_item(TicketOpenButton(
            str(t["id"]),
            label=str(t.get("label", "開啟票口"))[:80],
            style=BUTTON_STYLES.get(str(t.get("style", "primary")), discord.ButtonStyle.primary),
            emoji=str(t["emoji"]) if t.get("emoji") else None,
        ))
    return view


class TicketCloseConfirmView(discord.ui.View):
    def __init__(self, closer: discord.Member, reason: str = None):
        super().__init__(timeout=60)
        self.closer = closer
        self.reason = reason

    @discord.ui.button(label="確認關閉", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="🔒 正在關閉票口並產生逐字稿...", view=None)
        error = await close_ticket(interaction.channel, self.closer, reason=self.reason)
        if error:
            try:
                await interaction.edit_original_response(content=error)
            except discord.HTTPException:
                pass
        self.stop()

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="已取消關閉。", view=None)
        self.stop()


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="認領", style=discord.ButtonStyle.secondary, emoji="🙋", custom_id="ticket_claim_button")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        error = await claim_ticket(interaction)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
        elif not interaction.response.is_done():
            await interaction.response.defer()

    @discord.ui.button(label="關閉", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_close_button")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = resolve_ticket(interaction.guild.id, interaction.channel)
        if entry is None:
            await interaction.response.send_message("這裡不是有效的票口頻道。", ephemeral=True)
            return
        if not is_staff(interaction.user, interaction.guild.id) and interaction.user.id != int(entry.get("owner_id", 0)):
            await interaction.response.send_message("只有客服人員或開票者可以關閉票口。", ephemeral=True)
            return
        await interaction.response.send_message(
            "確定要關閉此票口嗎？關閉後頻道將被刪除，逐字稿會保存到紀錄頻道。",
            view=TicketCloseConfirmView(interaction.user),
            ephemeral=True,
        )


# ============= Panel publishing =============

def build_panel_embed(guild_id: int) -> discord.Embed:
    color = discord.Color.blurple()
    raw_color = str(get_server_config(guild_id, "ticket_panel_color", "") or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", raw_color):
        color = discord.Color(int(raw_color, 16))
    embed = discord.Embed(
        title=str(get_server_config(guild_id, "ticket_panel_title", "需要協助嗎？") or "需要協助嗎？"),
        description=str(get_server_config(
            guild_id, "ticket_panel_description",
            "點擊下方按鈕開啟私人票口，我們的團隊將盡快協助你。",
        ) or ""),
        color=color,
    )
    image_url = str(get_server_config(guild_id, "ticket_panel_image", "") or "").strip()
    if image_url.startswith(("http://", "https://")):
        embed.set_image(url=image_url)
    return embed


async def publish_panel(guild: discord.Guild, channel: discord.TextChannel) -> str | None:
    """Publish the ticket panel to a channel, removing the previous panel. Returns an error message or None."""
    try:
        message = await channel.send(embed=build_panel_embed(guild.id), view=build_panel_view(guild.id))
    except discord.Forbidden:
        return "機器人沒有權限在該頻道發送訊息。"
    except discord.HTTPException as e:
        log(f"發布票口面板失敗: {e}", module_name="Ticket", level=logging.ERROR)
        return "發布面板時發生錯誤，請檢查面板圖片網址與類別按鈕的 emoji 是否有效。"

    # 刪除舊面板訊息
    old = get_server_config(guild.id, PANEL_MESSAGE_KEY, None)
    if isinstance(old, dict) and old.get("message_id"):
        old_channel = guild.get_channel(int(old.get("channel_id", 0) or 0))
        if old_channel and not (old_channel.id == channel.id and int(old["message_id"]) == message.id):
            try:
                old_message = await old_channel.fetch_message(int(old["message_id"]))
                await old_message.delete()
            except discord.HTTPException:
                pass
    set_server_config(guild.id, PANEL_MESSAGE_KEY, {"channel_id": channel.id, "message_id": message.id})
    return None


async def refresh_panel(guild: discord.Guild) -> bool:
    """Edit the existing panel message in place (e.g. after type changes). Returns True on success."""
    info = get_server_config(guild.id, PANEL_MESSAGE_KEY, None)
    if not isinstance(info, dict) or not info.get("message_id"):
        return False
    channel = guild.get_channel(int(info.get("channel_id", 0) or 0))
    if channel is None:
        return False
    try:
        message = await channel.fetch_message(int(info["message_id"]))
        await message.edit(embed=build_panel_embed(guild.id), view=build_panel_view(guild.id))
        return True
    except discord.HTTPException:
        return False


# ============= Slash commands =============

@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class TicketCog(commands.GroupCog, name=app_commands.locale_str("ticket")):
    def __init__(self, bot):
        self.bot = bot
        self.persistent_views_registered = False

    @app_commands.command(name=app_commands.locale_str("panel"), description="發布票口面板訊息")
    @app_commands.describe(channel="要發布面板的頻道（省略時使用設定的面板頻道）")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if channel is None:
            channel_id = get_server_config(guild.id, "ticket_panel_channel", None)
            channel = guild.get_channel(int(channel_id)) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("請指定頻道，或先在面板設定「面板頻道」。", ephemeral=True)
            return

        error = await publish_panel(guild, channel)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        await interaction.followup.send(f"✅ 票口面板已發布到 {channel.mention}。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("setup"), description="快速設定票口系統")
    @app_commands.describe(
        category="票口頻道建立的分類",
        panel_channel="開票按鈕面板所在的頻道",
        staff_role="客服身分組（更多身分組可到伺服器面板設定）",
        log_channel="票口關閉後逐字稿發送的頻道（省略則不保存逐字稿）",
        publish="是否立即發布面板訊息（預設是）",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(
        self,
        interaction: discord.Interaction,
        category: discord.CategoryChannel,
        panel_channel: discord.TextChannel,
        staff_role: discord.Role,
        log_channel: discord.TextChannel = None,
        publish: bool = True,
    ):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        problems = []
        category_perms = category.permissions_for(guild.me)
        if not (category_perms.view_channel and category_perms.manage_channels and category_perms.manage_roles):
            problems.append(f"機器人在 {category.name} 分類缺少「管理頻道」或「管理權限」權限。")
        panel_perms = panel_channel.permissions_for(guild.me)
        if not (panel_perms.view_channel and panel_perms.send_messages and panel_perms.embed_links):
            problems.append(f"機器人在 {panel_channel.mention} 缺少「發送訊息」或「嵌入連結」權限。")
        if log_channel:
            log_perms = log_channel.permissions_for(guild.me)
            if not (log_perms.view_channel and log_perms.send_messages and log_perms.attach_files):
                problems.append(f"機器人在 {log_channel.mention} 缺少「發送訊息」或「附加檔案」權限。")
        if problems:
            await interaction.followup.send("⚠️ 設定未儲存，請先修正以下權限問題：\n" + "\n".join(f"- {p}" for p in problems), ephemeral=True)
            return

        set_server_config(guild.id, "ticket_category", category.id)
        set_server_config(guild.id, "ticket_panel_channel", panel_channel.id)
        staff_roles = get_staff_role_ids(guild.id)
        if staff_role.id not in staff_roles:
            staff_roles.append(staff_role.id)
        set_server_config(guild.id, "ticket_staff_roles", staff_roles)
        if log_channel:
            set_server_config(guild.id, "ticket_log_channel", log_channel.id)
        set_server_config(guild.id, "ticket_enabled", True)

        lines = [
            "✅ 票口系統已設定完成並啟用！",
            f"- 票口分類：{category.name}",
            f"- 面板頻道：{panel_channel.mention}",
            f"- 客服身分組：{staff_role.mention}",
            f"- 紀錄頻道：{log_channel.mention if log_channel else '（未設定，不保存逐字稿）'}",
        ]
        if publish:
            error = await publish_panel(guild, panel_channel)
            lines.append(f"- 面板：{'發布失敗：' + error if error else '已發布 🎫'}")
        else:
            lines.append("- 面板：尚未發布，請使用 /ticket panel 發布")
        lines.append("其他選項（歡迎訊息、黑名單、每人上限等）可到伺服器面板調整。")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("close"), description="關閉目前的票口")
    @app_commands.describe(reason="關閉原因（會記錄在逐字稿摘要中）")
    async def close(self, interaction: discord.Interaction, reason: str = None):
        entry = resolve_ticket(interaction.guild.id, interaction.channel)
        if entry is None:
            await interaction.response.send_message("這裡不是票口頻道。", ephemeral=True)
            return
        if not is_staff(interaction.user, interaction.guild.id) and interaction.user.id != int(entry.get("owner_id", 0)):
            await interaction.response.send_message("只有客服人員或開票者可以關閉票口。", ephemeral=True)
            return
        await interaction.response.send_message(
            "確定要關閉此票口嗎？關閉後頻道將被刪除，逐字稿會保存到紀錄頻道。",
            view=TicketCloseConfirmView(interaction.user, reason=reason),
            ephemeral=True,
        )

    @app_commands.command(name=app_commands.locale_str("claim"), description="認領目前的票口")
    async def claim(self, interaction: discord.Interaction):
        error = await claim_ticket(interaction)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
        else:
            await interaction.response.send_message("✅ 已認領此票口。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("add"), description="將用戶加入目前的票口")
    @app_commands.describe(member="要加入的用戶")
    async def add(self, interaction: discord.Interaction, member: discord.Member):
        entry = find_ticket(interaction.guild.id, interaction.channel.id)
        if entry is None:
            await interaction.response.send_message("這裡不是票口頻道。", ephemeral=True)
            return
        if not is_staff(interaction.user, interaction.guild.id):
            await interaction.response.send_message("只有客服人員可以管理票口成員。", ephemeral=True)
            return
        try:
            await interaction.channel.set_permissions(
                member,
                view_channel=True, send_messages=True,
                read_message_history=True, attach_files=True, embed_links=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message("機器人缺少權限，無法修改頻道權限。", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ 已將 {member.mention} 加入此票口。")

    @app_commands.command(name=app_commands.locale_str("remove"), description="將用戶移出目前的票口")
    @app_commands.describe(member="要移出的用戶")
    async def remove(self, interaction: discord.Interaction, member: discord.Member):
        entry = find_ticket(interaction.guild.id, interaction.channel.id)
        if entry is None:
            await interaction.response.send_message("這裡不是票口頻道。", ephemeral=True)
            return
        if not is_staff(interaction.user, interaction.guild.id):
            await interaction.response.send_message("只有客服人員可以管理票口成員。", ephemeral=True)
            return
        if member.id == int(entry.get("owner_id", 0)):
            await interaction.response.send_message("無法移除開票者。", ephemeral=True)
            return
        try:
            await interaction.channel.set_permissions(member, overwrite=None)
        except discord.Forbidden:
            await interaction.response.send_message("機器人缺少權限，無法修改頻道權限。", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ 已將 {member.mention} 移出此票口。")

    # ============= Ticket types =============

    types_group = app_commands.Group(
        name=app_commands.locale_str("types"),
        description="管理票口類別（面板上的多個開票按鈕）",
    )

    async def _type_id_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=f"{t.get('emoji', '') or ''} {t.get('label', '?')}".strip(), value=str(t["id"]))
            for t in get_ticket_types(interaction.guild.id)
            if current.lower() in str(t.get("label", "")).lower()
        ][:25]

    @types_group.command(name=app_commands.locale_str("add"), description="新增票口類別")
    @app_commands.describe(
        label="按鈕文字（例如：技術支援）",
        emoji="按鈕 emoji（選填）",
        style="按鈕顏色（預設藍色）",
        category="此類別專屬的頻道分類（省略時使用全域設定）",
        staff_role="此類別額外的客服身分組（省略時只用全域客服）",
        welcome="此類別專屬的歡迎訊息，可用 {user}、{subject}（省略時使用全域設定）",
    )
    @app_commands.choices(style=[
        app_commands.Choice(name="藍色", value="primary"),
        app_commands.Choice(name="灰色", value="secondary"),
        app_commands.Choice(name="綠色", value="success"),
        app_commands.Choice(name="紅色", value="danger"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def types_add(self, interaction: discord.Interaction, label: str,
                        emoji: str = None, style: str = "primary",
                        category: discord.CategoryChannel = None,
                        staff_role: discord.Role = None, welcome: str = None):
        types = get_ticket_types(interaction.guild.id)
        if len(types) >= MAX_TICKET_TYPES:
            await interaction.response.send_message(f"最多只能建立 {MAX_TICKET_TYPES} 個票口類別。", ephemeral=True)
            return
        types.append({
            "id": uuid.uuid4().hex[:8],
            "label": label.strip()[:80],
            "emoji": (emoji or "").strip() or None,
            "style": style if style in BUTTON_STYLES else "primary",
            "category_id": category.id if category else None,
            "staff_roles": [staff_role.id] if staff_role else [],
            "welcome_message": (welcome or "").strip() or None,
        })
        save_ticket_types(interaction.guild.id, types)
        refreshed = await refresh_panel(interaction.guild)
        await interaction.response.send_message(
            f"✅ 已新增票口類別「{label}」。" + ("面板已更新。" if refreshed else "請用 /ticket panel 重新發布面板。"),
            ephemeral=True,
        )

    @types_group.command(name=app_commands.locale_str("edit"), description="編輯票口類別")
    @app_commands.describe(
        type="要編輯的類別",
        label="新的按鈕文字",
        emoji="新的按鈕 emoji（輸入 - 可清除）",
        style="新的按鈕顏色",
        category="新的專屬分類（選擇後覆寫）",
        staff_role="新的額外客服身分組（選擇後覆寫）",
        welcome="新的專屬歡迎訊息（輸入 - 可清除改用全域設定）",
    )
    @app_commands.choices(style=[
        app_commands.Choice(name="藍色", value="primary"),
        app_commands.Choice(name="灰色", value="secondary"),
        app_commands.Choice(name="綠色", value="success"),
        app_commands.Choice(name="紅色", value="danger"),
    ])
    @app_commands.autocomplete(type=_type_id_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def types_edit(self, interaction: discord.Interaction, type: str,
                         label: str = None, emoji: str = None, style: str = None,
                         category: discord.CategoryChannel = None,
                         staff_role: discord.Role = None, welcome: str = None):
        types = get_ticket_types(interaction.guild.id)
        target = next((t for t in types if t.get("id") == type), None)
        if target is None:
            await interaction.response.send_message("找不到該票口類別。", ephemeral=True)
            return
        if label is not None:
            target["label"] = label.strip()[:80]
        if emoji is not None:
            target["emoji"] = None if emoji.strip() == "-" else emoji.strip() or None
        if style in BUTTON_STYLES:
            target["style"] = style
        if category is not None:
            target["category_id"] = category.id
        if staff_role is not None:
            target["staff_roles"] = [staff_role.id]
        if welcome is not None:
            target["welcome_message"] = None if welcome.strip() == "-" else welcome.strip() or None
        save_ticket_types(interaction.guild.id, types)
        refreshed = await refresh_panel(interaction.guild)
        await interaction.response.send_message(
            f"✅ 已更新票口類別「{target['label']}」。" + ("面板已更新。" if refreshed else "請用 /ticket panel 重新發布面板。"),
            ephemeral=True,
        )

    @types_group.command(name=app_commands.locale_str("remove"), description="移除票口類別")
    @app_commands.describe(type="要移除的類別")
    @app_commands.autocomplete(type=_type_id_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def types_remove(self, interaction: discord.Interaction, type: str):
        types = get_ticket_types(interaction.guild.id)
        remaining = [t for t in types if t.get("id") != type]
        if len(remaining) == len(types):
            await interaction.response.send_message("找不到該票口類別。", ephemeral=True)
            return
        save_ticket_types(interaction.guild.id, remaining)
        refreshed = await refresh_panel(interaction.guild)
        await interaction.response.send_message(
            "✅ 已移除票口類別。" + ("面板已更新。" if refreshed else "請用 /ticket panel 重新發布面板。"),
            ephemeral=True,
        )

    @types_group.command(name=app_commands.locale_str("list"), description="列出所有票口類別")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def types_list(self, interaction: discord.Interaction):
        types = get_ticket_types(interaction.guild.id)
        if not types:
            await interaction.response.send_message(
                "尚未建立任何票口類別，面板會顯示單一預設按鈕。用 /ticket types add 新增。",
                ephemeral=True,
            )
            return
        guild = interaction.guild
        lines = []
        for t in types:
            parts = [f"{t.get('emoji', '') or ''} **{t.get('label', '?')}**".strip(), f"顏色: {t.get('style', 'primary')}"]
            if t.get("category_id"):
                cat = guild.get_channel(int(t["category_id"]))
                parts.append(f"分類: {cat.name if cat else '（已刪除）'}")
            if t.get("staff_roles"):
                parts.append("客服: " + "、".join(f"<@&{r}>" for r in t["staff_roles"]))
            if t.get("welcome_message"):
                parts.append("自訂歡迎訊息 ✓")
            lines.append("- " + "｜".join(parts))
        embed = discord.Embed(
            title=f"🎫 票口類別（{len(types)}/{MAX_TICKET_TYPES}）",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ============= Events =============

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.persistent_views_registered:
            bot.add_view(TicketPanelView())
            bot.add_view(TicketControlView())
            bot.add_dynamic_items(TicketOpenButton)
            self.persistent_views_registered = True

        # 清除頻道已不存在的票口紀錄
        for guild_id, tickets in get_all_server_config_key(ACTIVE_TICKETS_KEY).items():
            if not isinstance(tickets, list) or not tickets:
                continue
            guild = bot.get_guild(int(guild_id))
            if guild is None:
                continue
            remaining = [t for t in tickets if guild.get_channel(int(t.get("channel_id", 0)))]
            if len(remaining) != len(tickets):
                save_active_tickets(int(guild_id), remaining)
                log(f"已清除 {len(tickets) - len(remaining)} 筆失效的票口紀錄 (Guild: {guild_id})", module_name="Ticket")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        tickets = get_active_tickets(channel.guild.id)
        remaining = [t for t in tickets if int(t.get("channel_id", 0)) != channel.id]
        if len(remaining) != len(tickets):
            save_active_tickets(channel.guild.id, remaining)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        for entry in get_active_tickets(member.guild.id):
            if int(entry.get("owner_id", 0)) != member.id:
                continue
            channel = member.guild.get_channel(int(entry.get("channel_id", 0)))
            if channel:
                try:
                    await channel.send("⚠️ 開票者已離開伺服器。")
                except discord.HTTPException:
                    pass


# ============= Website transcript route =============

if "Website" in modules:
    from Website import app
    from flask import Response, abort
    from expiring_dict import ExpiringDict

    _transcript_cache = ExpiringDict(ttl=TRANSCRIPT_CACHE_TTL)  # key -> html bytes 或 None（負快取）

    async def _fetch_transcript_attachment(guild_id: int, message_id: int) -> bytes | None:
        # 從該伺服器自己的設定反查紀錄頻道，並確認頻道屬於該伺服器，
        # 避免此 route 變成任意附件 proxy
        channel_id = get_server_config(guild_id, "ticket_log_channel", None)
        if not channel_id:
            return None
        channel = bot.get_channel(int(channel_id))
        if channel is None or getattr(channel, "guild", None) is None or channel.guild.id != guild_id:
            return None
        # 沒權限就不打 API，避免註定失敗的請求消耗 rate limit
        perms = channel.permissions_for(channel.guild.me)
        if not (perms.view_channel and perms.read_message_history):
            return None
        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
        if bot.user is None or message.author.id != bot.user.id:
            return None
        for attachment in message.attachments:
            if attachment.filename.endswith(".html"):
                try:
                    return await attachment.read()
                except discord.HTTPException:
                    return None
        return None

    @app.route("/tickets/<int:guild_id>/<int:message_id>")
    def ticket_transcript_view(guild_id, message_id):
        cache_key = f"{guild_id}:{message_id}"
        if cache_key in _transcript_cache:
            cached = _transcript_cache[cache_key]
            if cached is None:
                abort(404)
            return Response(cached, mimetype="text/html; charset=utf-8")

        try:
            html = asyncio.run_coroutine_threadsafe(
                _fetch_transcript_attachment(guild_id, message_id), bot.loop,
            ).result(timeout=15)
        except Exception as e:
            log(f"讀取票口逐字稿失敗: {e}", module_name="Ticket", level=logging.WARNING)
            return Response("暫時無法讀取逐字稿，請稍後再試。", status=503, mimetype="text/plain; charset=utf-8")

        _transcript_cache[cache_key] = html
        if html is None:
            abort(404)
        return Response(html, mimetype="text/html; charset=utf-8")


asyncio.run(bot.add_cog(TicketCog(bot)))

if __name__ == "__main__":
    from globalenv import start_bot
    start_bot()
