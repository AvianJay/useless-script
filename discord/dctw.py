# https://dctw.nkhost.dev/api/v2/openapi.json
from __future__ import annotations

from globalenv import bot, get_user_data, set_user_data, config
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import datetime
import math
import re
import time


API_BASE = "https://dctw.nkhost.dev"
SITE_BASE = "https://dctw.xyz"
USER_KEY_NAME = "dctw_api_key"
CACHE_TTL_SECONDS = 300
BROWSE_PAGE_SIZE = 10
AGGREGATE_LIMIT = 300
FETCH_LIMIT = 50
SAFE_MENTIONS = discord.AllowedMentions.none()

BOT_TAGS = {
    "music": "音樂",
    "minigames": "小遊戲",
    "fun": "有趣",
    "utility": "工具",
    "management": "管理",
    "customizable": "可自訂",
    "automation": "自動化",
    "roleplay": "角色扮演",
    "nsfw": "NSFW",
}

SERVER_TAGS = {
    "gaming": "遊戲",
    "community": "社群",
    "anime": "動漫",
    "art": "藝術",
    "hangout": "閒聊",
    "programming": "程式設計",
    "programing": "程式設計",
    "acting": "表演",
    "nsfw": "NSFW",
    "roleplay": "角色扮演",
    "politics": "政治",
}

TEMPLATE_TAGS = {
    "community": "支援",
    "gaming": "遊戲",
    "anime": "大型",
    "art": "趣味",
    "nsfw": "NSFW",
}

TAG_MAPPINGS = {
    "bots": BOT_TAGS,
    "servers": SERVER_TAGS,
    "templates": TEMPLATE_TAGS,
}


RESOURCE_CONFIG = {
    "bots": {
        "list_path": "/api/v2/bots",
        "detail_path": "/api/v2/bots/{id}",
        "comments_path": "/api/v2/bots/{id}/comments",
        "id_key": "id",
        "title": "機器人",
        "name_key": "name",
        "sort_map": {
            "newest": "created_at",
            "votes": "votes",
            "servers": "server_count",
            "bumped": "bumped_at",
        },
    },
    "servers": {
        "list_path": "/api/v2/servers",
        "detail_path": "/api/v2/servers/{id}",
        "comments_path": "/api/v2/servers/{id}/comments",
        "id_key": "id",
        "title": "伺服器",
        "name_key": "name",
        "sort_map": {
            "newest": "created_at",
            "votes": "votes",
            "members": "member_count",
            "bumped": "bumped_at",
        },
    },
    "templates": {
        "list_path": "/api/v2/templates",
        "detail_path": "/api/v2/templates/{id}",
        "comments_path": "/api/v2/templates/{id}/comments",
        "id_key": "id",
        "title": "模板",
        "name_key": "name",
        "sort_map": {
            "newest": "created_at",
            "votes": "votes",
            "bumped": "bumped_at",
        },
    },
}


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_user_mention_or_id(raw: str) -> int | None:
    mention_match = re.fullmatch(r"<@!?(\d+)>", raw.strip())
    if mention_match:
        return int(mention_match.group(1))
    if raw.strip().isdigit():
        return int(raw.strip())
    return None


def _parse_numeric_id(raw: str) -> int | None:
    value = raw.strip()
    if value.isdigit():
        return int(value)
    return None


def _format_error(exc: Exception) -> str:
    msg = str(exc).strip()
    if msg:
        return msg
    return f"{exc.__class__.__name__}"


def _normalize_text(value, fallback: str = "無") -> str:
    text = str(value or "").strip()
    return text or fallback


def _normalize_url(value) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith(("http://", "https://")):
        return text
    if re.match(r"^[\w.-]+\.[A-Za-z]{2,}([/:?#].*)?$", text):
        return f"https://{text}"
    return None


def _format_timestamp(value) -> str:
    ts = _safe_int(value)
    if ts <= 0:
        return "未知"
    try:
        datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return str(ts)
    return f"<t:{ts}:f>"


def _split_text_for_display(text: str, max_len: int = 3800) -> list[str]:
    text = text.strip()
    if not text:
        return []

    parts: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            parts.append(remaining)
            break

        split_point = remaining.rfind("\n\n", 0, max_len)
        if split_point == -1:
            split_point = remaining.rfind("\n", 0, max_len)
        if split_point == -1:
            split_point = remaining.rfind(" ", 0, max_len)
        if split_point == -1:
            split_point = max_len

        parts.append(remaining[:split_point].rstrip())
        remaining = remaining[split_point:].lstrip()

    return parts


def _resource_colour(resource: str) -> discord.Colour:
    if resource == "bots":
        return discord.Colour.blurple()
    if resource == "servers":
        return discord.Colour.green()
    return discord.Colour.gold()


def _listing_page_url(resource: str, listing_id: int) -> str:
    return f"{SITE_BASE}/{resource}/{listing_id}"


def _compact_join(values, fallback: str = "無") -> str:
    if not isinstance(values, list):
        return fallback
    normalized = [str(value).strip() for value in values if str(value or "").strip()]
    if not normalized:
        return fallback
    return ", ".join(normalized[:10])


def _format_tag_labels(resource: str, tags) -> str:
    if not isinstance(tags, list):
        return "無"

    mapping = TAG_MAPPINGS.get(resource, {})
    labels = []
    seen: set[str] = set()
    for raw_tag in tags:
        tag = str(raw_tag or "").strip()
        if not tag:
            continue
        key = tag.casefold()
        label = mapping.get(key, tag)
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)

    if not labels:
        return "無"
    return ", ".join(labels[:10])


def _extract_thumbnail_url(resource: str, item: dict) -> str | None:
    candidates: list = []
    if resource == "bots":
        candidates.append(item.get("avatar_url"))
    elif resource == "servers":
        candidates.append(item.get("icon_url"))
    elif resource == "templates":
        screenshots = item.get("screenshots")
        if isinstance(screenshots, list):
            candidates.append(screenshots[0] if screenshots else None)

    for candidate in candidates:
        url = _normalize_url(candidate)
        if url:
            return url
    return None


def _extract_gallery_urls(resource: str, item: dict) -> list[str]:
    banner_url = _normalize_url(item.get("banner_url"))
    if resource in {"bots", "servers"} and banner_url:
        return [banner_url]

    candidates: list = []
    if banner_url:
        candidates.append(banner_url)

    screenshots = item.get("screenshots")
    if isinstance(screenshots, list):
        candidates.extend(screenshots[:10])

    urls: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        url = _normalize_url(candidate)
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)

    return urls[:10]


def _build_detail_link_specs(resource: str, item: dict) -> list[tuple[str, str]]:
    conf = RESOURCE_CONFIG[resource]
    listing_id = _safe_int(item.get(conf["id_key"]))
    buttons: list[tuple[str, str]] = []

    primary_url = _normalize_url(item.get("url"))
    if resource == "bots" and primary_url:
        buttons.append(("邀請機器人", primary_url))
    elif resource == "servers" and primary_url:
        buttons.append(("加入伺服器", primary_url))
    elif resource == "templates" and primary_url:
        buttons.append(("套用模板", primary_url))

    if resource == "bots":
        support_url = _normalize_url(item.get("discord_url"))
        website_url = _normalize_url(item.get("website_url"))
        if support_url:
            buttons.append(("支援伺服器", support_url))
        if website_url:
            buttons.append(("官方網站", website_url))

    page_url = _listing_page_url(resource, listing_id)
    if page_url:
        buttons.append(("DCTW 頁面", page_url))

    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, url in buttons:
        if url in seen:
            continue
        seen.add(url)
        deduped.append((label, url))
    return deduped


class DCTWBrowseView(discord.ui.LayoutView):

    def __init__(
        self,
        cog: "DCTW",
        *,
        user_id: int,
        resource: str,
        sort_mode: str,
        items: list[dict],
        cached: bool,
        truncated: bool,
        page_index: int = 0,
    ):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.resource = resource
        self.sort_mode = sort_mode
        self.items = items
        self.cached = cached
        self.truncated = truncated
        self.page_index = page_index
        self.prev_button = discord.ui.Button(label="上一頁", style=discord.ButtonStyle.secondary)
        self.prev_button.callback = self._on_prev_page
        self.next_button = discord.ui.Button(label="下一頁", style=discord.ButtonStyle.secondary)
        self.next_button.callback = self._on_next_page
        self.pick_select = discord.ui.Select(placeholder="選擇項目查看詳細", min_values=1, max_values=1)
        self.pick_select.callback = self._on_pick_item
        self._refresh_page()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("這不是你的瀏覽清單。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return False
        return True

    def _total_pages(self) -> int:
        if not self.items:
            return 1
        return math.ceil(len(self.items) / BROWSE_PAGE_SIZE)

    def _current_page_items(self) -> list[tuple[int, dict]]:
        start = self.page_index * BROWSE_PAGE_SIZE
        end = start + BROWSE_PAGE_SIZE
        return list(enumerate(self.items[start:end], start=start))

    def _summary_text(self) -> str:
        conf = RESOURCE_CONFIG[self.resource]
        page_items = self._current_page_items()
        header_lines = [
            f"## DCTW {conf['title']} 瀏覽",
            f"排序: `{self.sort_mode}`",
            f"頁數: {self.page_index + 1}/{self._total_pages()}",
            f"總數: {len(self.items)}",
            f"來源: {'快取' if self.cached else '即時'}",
        ]
        if self.truncated:
            header_lines.append(f"-# 已使用前 {AGGREGATE_LIMIT} 筆資料排序")

        if not page_items:
            return "\n".join(header_lines + ["", "目前沒有資料。"])

        lines = []
        for idx, item in page_items:
            listing_id = _safe_int(item.get(conf["id_key"]))
            name = _normalize_text(item.get(conf["name_key"]), "(無名稱)")
            votes = _safe_int(item.get("votes"))
            extra = ""
            if self.resource == "bots":
                extra = f" | 伺服器數: {_safe_int(item.get('server_count'))}"
            elif self.resource == "servers":
                extra = f" | 成員數: {_safe_int(item.get('member_count'))}"
            lines.append(f"{idx + 1}. {name} (ID: {listing_id}) | 票數: {votes}{extra}")

        return "\n".join(header_lines + ["", *lines])

    def _refresh_page(self):
        self.prev_button.disabled = self.page_index <= 0
        self.next_button.disabled = self.page_index >= self._total_pages() - 1

        page_items = self._current_page_items()
        if not page_items:
            self.pick_select.disabled = True
            self.pick_select.options = [discord.SelectOption(label="沒有可選項目", value="none")]
        else:
            self.pick_select.disabled = False
            options = []
            conf = RESOURCE_CONFIG[self.resource]
            for idx, item in page_items:
                listing_id = _safe_int(item.get(conf["id_key"]))
                name = _normalize_text(item.get(conf["name_key"]), f"{conf['title']}{listing_id}")
                options.append(
                    discord.SelectOption(
                        label=name[:100],
                        value=str(idx),
                        description=f"ID: {listing_id}"[:100],
                    )
                )
            self.pick_select.options = options

        self.clear_items()
        container = discord.ui.Container(accent_colour=_resource_colour(self.resource))
        for part in _split_text_for_display(self._summary_text()):
            container.add_item(discord.ui.TextDisplay(part))
        self.add_item(container)
        self.add_item(discord.ui.ActionRow(self.prev_button, self.next_button))
        self.add_item(discord.ui.ActionRow(self.pick_select))

    async def _on_prev_page(self, interaction: discord.Interaction):
        self.page_index = max(0, self.page_index - 1)
        self._refresh_page()
        await interaction.response.edit_message(view=self)

    async def _on_next_page(self, interaction: discord.Interaction):
        self.page_index = min(self._total_pages() - 1, self.page_index + 1)
        self._refresh_page()
        await interaction.response.edit_message(view=self)

    async def _on_pick_item(self, interaction: discord.Interaction):
        if not self.pick_select.values or self.pick_select.values[0] == "none":
            await interaction.response.send_message("目前沒有可查看的資料。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        selected_index = _safe_int(self.pick_select.values[0], -1)
        if selected_index < 0 or selected_index >= len(self.items):
            await interaction.response.send_message("選擇的項目不存在。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        await interaction.response.defer()
        selected = self.items[selected_index]
        detail_view = await DCTWDetailView.build(
            self.cog,
            user_id=self.user_id,
            resource=self.resource,
            sort_mode=self.sort_mode,
            items=self.items,
            page_index=self.page_index,
            selected_item=selected,
        )
        await interaction.message.edit(view=detail_view)


class DCTWDetailView(discord.ui.LayoutView):
    def __init__(self, *, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.back_button = discord.ui.Button(label="回到清單", style=discord.ButtonStyle.secondary)
        self.back_button.callback = self._back_to_list
        self.comments_button = discord.ui.Button(label="查看留言", style=discord.ButtonStyle.primary)
        self.comments_button.callback = self._show_comments
        self.vote_button = discord.ui.Button(label="投票", style=discord.ButtonStyle.success)
        self.vote_button.callback = self._vote_item
        self.bump_button = discord.ui.Button(label="置頂", style=discord.ButtonStyle.danger)
        self.bump_button.callback = self._bump_item

    @classmethod
    async def build(
        cls,
        cog: "DCTW",
        *,
        user_id: int,
        resource: str,
        sort_mode: str,
        items: list[dict],
        page_index: int,
        selected_item: dict,
    ) -> "DCTWDetailView":
        self = cls(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.resource = resource
        self.sort_mode = sort_mode
        self.items = items
        self.page_index = page_index
        self.selected_item = selected_item
        await self._hydrate_selected_item()
        await self._refresh_layout()
        return self

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("這不是你的詳細頁。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return False
        return True

    async def _hydrate_selected_item(self):
        conf = RESOURCE_CONFIG[self.resource]
        listing_id = _safe_int(self.selected_item.get(conf["id_key"]))
        if listing_id <= 0:
            return

        read_key = self.cog._get_read_api_key(self.user_id)
        if not read_key:
            return

        try:
            payload = await self.cog._request_json(
                "GET",
                conf["detail_path"].format(id=listing_id),
                api_key=read_key,
            )
        except Exception:
            return

        if isinstance(payload, dict):
            self.selected_item = payload

    async def _refresh_layout(self):
        conf = RESOURCE_CONFIG[self.resource]
        listing_id = _safe_int(self.selected_item.get(conf["id_key"]))
        name = _normalize_text(self.selected_item.get(conf["name_key"]), "(無名稱)")
        votes = _safe_int(self.selected_item.get("votes"))
        created_at = _format_timestamp(self.selected_item.get("created_at"))
        bumped_at = _format_timestamp(self.selected_item.get("bumped_at"))
        thumbnail_url = _extract_thumbnail_url(self.resource, self.selected_item)
        gallery_urls = _extract_gallery_urls(self.resource, self.selected_item)

        lines = [f"{conf['title']} ID: `{listing_id}`", f"票數: {votes}", f"建立時間: {created_at}", f"置頂時間: {bumped_at}"]
        if self.resource == "bots":
            lines.extend(
                [
                    f"伺服器數: {_safe_int(self.selected_item.get('server_count'))}",
                    f"驗證狀態: {'已驗證' if self.selected_item.get('verified') else '未驗證'}",
                    f"Slash 指令: {'是' if self.selected_item.get('is_slash') else '否'}",
                    f"前綴: {_normalize_text(self.selected_item.get('prefix'))}",
                ]
            )
        elif self.resource == "servers":
            lines.extend(
                [
                    f"成員數: {_safe_int(self.selected_item.get('member_count'))}",
                    f"線上成員數: {_safe_int(self.selected_item.get('online_member_count'))}",
                ]
            )

        tags_line = _format_tag_labels(self.resource, self.selected_item.get("tags"))
        if tags_line != "無":
            lines.append(f"標籤: {tags_line}")

        keywords_line = _compact_join(self.selected_item.get("keywords"))
        if keywords_line != "無":
            lines.append(f"關鍵字: {keywords_line}")

        container = discord.ui.Container(accent_colour=_resource_colour(self.resource))

        title_block = f"## {name}"
        meta_block = "\n".join(lines)
        if thumbnail_url:
            container.add_item(
                discord.ui.Section(
                    title_block,
                    meta_block,
                    accessory=discord.ui.Thumbnail(thumbnail_url, description=f"{name} thumbnail"),
                )
            )
        else:
            container.add_item(discord.ui.TextDisplay(title_block))
            container.add_item(discord.ui.TextDisplay(meta_block))

        body_sections: list[tuple[str, str]] = []
        description = _normalize_text(self.selected_item.get("description"), "無描述")
        introduce = _normalize_text(self.selected_item.get("introduce"), "")
        body_sections.append(("簡介", description))
        if introduce and introduce != "無" and introduce != description:
            body_sections.append(("介紹", introduce))

        social_links = self.selected_item.get("social_links") if isinstance(self.selected_item.get("social_links"), dict) else {}
        social_lines = []
        for key, label in [("line", "LINE"), ("facebook", "Facebook"), ("instagram", "Instagram"), ("twitch", "Twitch"), ("threads", "Threads"), ("x", "X")]:
            url = _normalize_url(social_links.get(key))
            if url:
                social_lines.append(f"{label}: {url}")
        if social_lines:
            body_sections.append(("社群連結", "\n".join(social_lines)))

        if self.resource == "servers":
            features = _compact_join(self.selected_item.get("features"), "")
            if features:
                body_sections.append(("伺服器功能", features))
        elif self.resource == "bots":
            developers = await self.cog._format_user_refs(self.selected_item.get("developers"), bullet_prefix="- ")
            if developers:
                body_sections.append(("開發者", developers))

        if self.resource == "servers":
            admins = await self.cog._format_server_admins(self.selected_item.get("admins"))
            if admins:
                body_sections.append(("管理員", admins))

        if body_sections:
            container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        for heading, body in body_sections:
            if not body:
                continue
            for part in _split_text_for_display(f"### {heading}\n{body}"):
                container.add_item(discord.ui.TextDisplay(part))

        self.clear_items()
        self.add_item(container)

        if gallery_urls:
            gallery = discord.ui.MediaGallery()
            for index, image_url in enumerate(gallery_urls, start=1):
                description = f"{name} 圖片 {index}" if len(gallery_urls) > 1 else f"{name} 圖片"
                gallery.add_item(media=image_url, description=description[:256])
            self.add_item(gallery)

        link_specs = _build_detail_link_specs(self.resource, self.selected_item)
        if link_specs:
            for offset in range(0, len(link_specs), 5):
                row = discord.ui.ActionRow()
                for label, url in link_specs[offset:offset + 5]:
                    row.add_item(discord.ui.Button(label=label, url=url, style=discord.ButtonStyle.link))
                self.add_item(row)

        self.add_item(
            discord.ui.ActionRow(
                self.back_button,
                self.comments_button,
                self.vote_button,
                self.bump_button,
            )
        )

    def _listing_id(self) -> int:
        conf = RESOURCE_CONFIG[self.resource]
        return _safe_int(self.selected_item.get(conf["id_key"]))

    async def _back_to_list(self, interaction: discord.Interaction):
        list_view = DCTWBrowseView(
            self.cog,
            user_id=self.user_id,
            resource=self.resource,
            sort_mode=self.sort_mode,
            items=self.items,
            cached=True,
            truncated=len(self.items) >= AGGREGATE_LIMIT,
            page_index=self.page_index,
        )
        await interaction.response.edit_message(view=list_view)

    async def _show_comments(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        conf = RESOURCE_CONFIG[self.resource]
        listing_id = self._listing_id()
        path = conf["comments_path"].format(id=listing_id)
        read_key = self.cog._get_read_api_key(interaction.user.id)
        if not read_key:
            await interaction.followup.send("查看留言需要 API key。請先使用 /dctw key set。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        try:
            payload = await self.cog._request_json("GET", path, api_key=read_key)
        except Exception as exc:
            await interaction.followup.send(f"取得留言失敗：{_format_error(exc)}", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        items = payload.get("items", []) if isinstance(payload, dict) else []
        if not items:
            await interaction.followup.send("目前沒有留言。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        preview_lines = []
        for idx, comment in enumerate(items[:5], start=1):
            user_id = _safe_int(comment.get("user_id"))
            author_name = await self.cog._resolve_user_name(user_id)
            stars = _safe_int(comment.get("stars"))
            content = str(comment.get("content") or "(無內容)").replace("\n", " ")
            updated_at = _format_timestamp(comment.get("updated_at"))
            preview_lines.append(f"{idx}. {author_name} | {stars}★ | {updated_at}\n{content[:120]}")
        await interaction.followup.send("\n".join(preview_lines), ephemeral=True, allowed_mentions=SAFE_MENTIONS)

    async def _vote_item(self, interaction: discord.Interaction):
        listing_id = self._listing_id()
        await self.cog._do_post_action(interaction, self.resource, listing_id, "vote")

    async def _bump_item(self, interaction: discord.Interaction):
        listing_id = self._listing_id()
        await self.cog._do_post_action(interaction, self.resource, listing_id, "bump")


@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
class DCTW(commands.GroupCog, name="dctw", description="DCTW 瀏覽器！"):
    dctw_bot = app_commands.Group(name="bot", description="Bot 相關指令")
    dctw_server = app_commands.Group(name="server", description="Server 相關指令")
    dctw_template = app_commands.Group(name="template", description="Template 相關指令")
    dctw_key = app_commands.Group(name="key", description="管理你的 DCTW API key")

    def __init__(self, bot_: commands.Bot):
        self.bot = bot_
        self.default_api_key = config("dctw_api_key")
        self._session: aiohttp.ClientSession | None = None
        self._cache_lock = asyncio.Lock()
        self._list_cache: dict[tuple, dict] = {}
        self._cache_hits = 0
        self._cache_misses = 0
        self._user_name_cache: dict[int, str] = {}

    async def cog_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        api_key: str | None = None,
    ) -> dict:
        url = f"{API_BASE}{path}"
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["X-API-KEY"] = api_key

        session = await self._get_session()
        async with session.request(method, url, params=params, headers=headers) as resp:
            if resp.status >= 400:
                body = await resp.text()
                raise RuntimeError(f"HTTP {resp.status} {path}: {body[:200]}")
            if resp.status == 204:
                return {}
            return await resp.json()

    def _get_user_key(self, user_id: int) -> str:
        return str(get_user_data(0, user_id, USER_KEY_NAME, "") or "")

    def _get_read_api_key(self, user_id: int) -> str:
        return self._get_user_key(user_id) or str(self.default_api_key or "")

    async def _resolve_user_name(self, raw_user_id) -> str:
        user_id = _safe_int(raw_user_id, -1)
        if user_id <= 0:
            return _normalize_text(raw_user_id, "未知")

        cached = self._user_name_cache.get(user_id)
        if cached:
            return cached

        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except Exception:
                user = None

        if user is None:
            display = str(user_id)
        else:
            name = getattr(user, "display_name", None) or getattr(user, "global_name", None) or user.name
            display = f"{name} ({user_id})"

        self._user_name_cache[user_id] = display
        return display

    async def _format_user_refs(self, values, *, bullet_prefix: str = "") -> str:
        if not isinstance(values, list):
            return ""

        lines = []
        for raw_value in values[:10]:
            resolved = await self._resolve_user_name(raw_value)
            lines.append(f"{bullet_prefix}{resolved}")
        return "\n".join(lines)

    async def _format_server_admins(self, admins) -> str:
        if not isinstance(admins, list):
            return ""

        lines = []
        for admin in admins[:10]:
            if not isinstance(admin, dict):
                continue
            resolved = await self._resolve_user_name(admin.get("id"))
            job = _normalize_text(admin.get("job"), "")
            if job:
                lines.append(f"- {resolved} | {job}")
            else:
                lines.append(f"- {resolved}")
        return "\n".join(lines)

    async def _fetch_and_sort_resource(self, resource: str, sort_mode: str, *, api_key: str) -> tuple[list[dict], bool, bool]:
        conf = RESOURCE_CONFIG[resource]
        sort_key = conf["sort_map"].get(sort_mode)
        if sort_key is None:
            raise ValueError("不支援的排序方式")

        cache_key = (resource, sort_mode, FETCH_LIMIT, AGGREGATE_LIMIT, "auth")
        now = time.time()
        async with self._cache_lock:
            cached = self._list_cache.get(cache_key)
            if cached and cached["expires_at"] > now:
                self._cache_hits += 1
                return cached["items"], True, cached["truncated"]

        self._cache_misses += 1

        items: list[dict] = []
        cursor = None
        truncated = False
        while len(items) < AGGREGATE_LIMIT:
            params = {"limit": min(FETCH_LIMIT, 50)}
            if cursor:
                params["cursor"] = cursor
            payload = await self._request_json("GET", conf["list_path"], params=params, api_key=api_key)
            batch = payload.get("items") or []
            if not batch:
                break
            items.extend(batch)
            if len(items) >= AGGREGATE_LIMIT:
                items = items[:AGGREGATE_LIMIT]
                truncated = True
                break
            cursor = payload.get("next_cursor")
            if not cursor:
                break

        items.sort(key=lambda i: _safe_int(i.get(sort_key), 0), reverse=True)

        async with self._cache_lock:
            self._list_cache[cache_key] = {
                "expires_at": time.time() + CACHE_TTL_SECONDS,
                "items": items,
                "truncated": truncated,
            }

        return items, False, truncated

    async def _send_browse(self, interaction: discord.Interaction, resource: str, sort_mode: str):
        await interaction.response.defer()
        request_key = self._get_read_api_key(interaction.user.id)
        if not request_key:
            await interaction.followup.send("查詢需要 API key。請先使用 /dctw key set 設定你的 key。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        try:
            items, cached, truncated = await self._fetch_and_sort_resource(resource, sort_mode, api_key=request_key)
        except Exception as exc:
            await interaction.followup.send(f"查詢失敗：{_format_error(exc)}", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        view = DCTWBrowseView(
            self,
            user_id=interaction.user.id,
            resource=resource,
            sort_mode=sort_mode,
            items=items,
            cached=cached,
            truncated=truncated,
            page_index=0,
        )
        await interaction.followup.send(view=view, allowed_mentions=SAFE_MENTIONS)

    async def _do_post_action(self, interaction: discord.Interaction, resource: str, listing_id: int, action: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        user_key = self._get_user_key(interaction.user.id)
        if not user_key:
            await interaction.followup.send("你還沒設定個人 API key，先用 /dctw key set。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        conf = RESOURCE_CONFIG[resource]
        path = f"{conf['list_path']}/{listing_id}/{action}"
        try:
            await self._request_json("POST", path, api_key=user_key)
        except Exception as exc:
            await interaction.followup.send(f"操作失敗：{_format_error(exc)}", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        await interaction.followup.send(f"✅ 已對 {resource}:{listing_id} 執行 {action}。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)

    @dctw_key.command(name="set", description="設定你的 DCTW API key")
    async def key_set(self, interaction: discord.Interaction, api_key: str):
        set_user_data(0, interaction.user.id, USER_KEY_NAME, api_key.strip())
        await interaction.response.send_message("✅ 已儲存你的 DCTW API key。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)

    @dctw_key.command(name="clear", description="清除你的 DCTW API key")
    async def key_clear(self, interaction: discord.Interaction):
        set_user_data(0, interaction.user.id, USER_KEY_NAME, "")
        await interaction.response.send_message("✅ 已清除你的 DCTW API key。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)

    @dctw_key.command(name="show", description="查看是否已設定 DCTW API key")
    async def key_show(self, interaction: discord.Interaction):
        api_key = self._get_user_key(interaction.user.id)
        if not api_key:
            await interaction.response.send_message("你目前沒有設定 key。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        masked = api_key[:4] + "*" * max(0, len(api_key) - 8) + api_key[-4:]
        await interaction.response.send_message(f"目前 key: {masked}", ephemeral=True, allowed_mentions=SAFE_MENTIONS)

    @dctw_bot.command(name="browse", description="瀏覽機器人清單")
    @app_commands.choices(
        sort=[
            app_commands.Choice(name="newest", value="newest"),
            app_commands.Choice(name="votes", value="votes"),
            app_commands.Choice(name="servers", value="servers"),
            app_commands.Choice(name="bumped", value="bumped"),
        ]
    )
    async def bot_browse(self, interaction: discord.Interaction, sort: app_commands.Choice[str] | None = None):
        await self._send_browse(interaction, "bots", sort.value if sort else "bumped")

    @dctw_server.command(name="browse", description="瀏覽伺服器清單")
    @app_commands.choices(
        sort=[
            app_commands.Choice(name="newest", value="newest"),
            app_commands.Choice(name="votes", value="votes"),
            app_commands.Choice(name="members", value="members"),
            app_commands.Choice(name="bumped", value="bumped"),
        ]
    )
    async def server_browse(self, interaction: discord.Interaction, sort: app_commands.Choice[str] | None = None):
        await self._send_browse(interaction, "servers", sort.value if sort else "bumped")

    @dctw_template.command(name="browse", description="瀏覽模板清單")
    @app_commands.choices(
        sort=[
            app_commands.Choice(name="newest", value="newest"),
            app_commands.Choice(name="votes", value="votes"),
            app_commands.Choice(name="bumped", value="bumped"),
        ]
    )
    async def template_browse(self, interaction: discord.Interaction, sort: app_commands.Choice[str] | None = None):
        await self._send_browse(interaction, "templates", sort.value if sort else "bumped")

    @dctw_bot.command(name="vote", description="對指定 bot 投票")
    async def bot_vote(self, interaction: discord.Interaction, target: str):
        bot_id = _parse_user_mention_or_id(target)
        if bot_id is None:
            await interaction.response.send_message("請輸入 bot ID 或 mention（例如 <@123...>）。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        await self._do_post_action(interaction, "bots", bot_id, "vote")

    @dctw_bot.command(name="bump", description="對指定 bot 置頂")
    async def bot_bump(self, interaction: discord.Interaction, target: str):
        bot_id = _parse_user_mention_or_id(target)
        if bot_id is None:
            await interaction.response.send_message("請輸入 bot ID 或 mention（例如 <@123...>）。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        await self._do_post_action(interaction, "bots", bot_id, "bump")

    @dctw_server.command(name="vote", description="對指定 server 投票")
    async def server_vote(self, interaction: discord.Interaction, target: str):
        server_id = _parse_numeric_id(target)
        if server_id is None:
            await interaction.response.send_message("請輸入 server ID（數字）。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        await self._do_post_action(interaction, "servers", server_id, "vote")

    @dctw_server.command(name="bump", description="對指定 server 置頂")
    async def server_bump(self, interaction: discord.Interaction, target: str):
        server_id = _parse_numeric_id(target)
        if server_id is None:
            await interaction.response.send_message("請輸入 server ID（數字）。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        await self._do_post_action(interaction, "servers", server_id, "bump")

    @dctw_template.command(name="vote", description="對指定 template 投票")
    async def template_vote(self, interaction: discord.Interaction, target: str):
        template_id = _parse_numeric_id(target)
        if template_id is None:
            await interaction.response.send_message("請輸入 template ID（數字）。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        await self._do_post_action(interaction, "templates", template_id, "vote")

    @dctw_template.command(name="bump", description="對指定 template 置頂")
    async def template_bump(self, interaction: discord.Interaction, target: str):
        template_id = _parse_numeric_id(target)
        if template_id is None:
            await interaction.response.send_message("請輸入 template ID（數字）。", ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        await self._do_post_action(interaction, "templates", template_id, "bump")

    @app_commands.command(name="cache-stats", description="查看 DCTW 快取狀態")
    async def cache_stats(self, interaction: discord.Interaction):
        total = self._cache_hits + self._cache_misses
        hit_rate = (self._cache_hits / total * 100.0) if total else 0.0
        await interaction.response.send_message(
            (
                "DCTW 快取統計\n"
                f"- TTL: {CACHE_TTL_SECONDS} 秒\n"
                f"- 快取項目數: {len(self._list_cache)}\n"
                f"- 命中: {self._cache_hits}\n"
                f"- 未命中: {self._cache_misses}\n"
                f"- 命中率: {hit_rate:.2f}%"
            ),
            ephemeral=True,
            allowed_mentions=SAFE_MENTIONS,
        )


asyncio.run(bot.add_cog(DCTW(bot)))
