# https://dctw.xyz/docs/?tags=api
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
import i18n
from i18n import t


API_BASE = "https://dctw.xyz"
SITE_BASE = "https://dctw.xyz"
USER_KEY_NAME = "dctw_api_key"
CACHE_TTL_SECONDS = 300
OWNED_CACHE_TTL_SECONDS = 120
BROWSE_PAGE_SIZE = 10
AGGREGATE_LIMIT = 300
FETCH_LIMIT = 50
BUMP_COOLDOWN_SECONDS = 60
SAFE_MENTIONS = discord.AllowedMentions.none()

def _bot_tags() -> dict:
    return {
        "music": t("dctw.tag.bot.music"),
        "minigames": t("dctw.tag.bot.minigames"),
        "fun": t("dctw.tag.bot.fun"),
        "utility": t("dctw.tag.bot.utility"),
        "management": t("dctw.tag.bot.management"),
        "customizable": t("dctw.tag.bot.customizable"),
        "automation": t("dctw.tag.bot.automation"),
        "roleplay": t("dctw.tag.bot.roleplay"),
        "nsfw": "NSFW",
    }


def _server_tags() -> dict:
    return {
        "gaming": t("dctw.tag.server.gaming"),
        "community": t("dctw.tag.server.community"),
        "anime": t("dctw.tag.server.anime"),
        "art": t("dctw.tag.server.art"),
        "hangout": t("dctw.tag.server.hangout"),
        "programming": t("dctw.tag.server.programming"),
        "programing": t("dctw.tag.server.programming"),
        "acting": t("dctw.tag.server.acting"),
        "nsfw": "NSFW",
        "roleplay": t("dctw.tag.server.roleplay"),
        "politics": t("dctw.tag.server.politics"),
    }


def _template_tags() -> dict:
    return {
        "community": t("dctw.tag.template.community"),
        "gaming": t("dctw.tag.template.gaming"),
        "anime": t("dctw.tag.template.anime"),
        "art": t("dctw.tag.template.art"),
        "nsfw": "NSFW",
    }


def _tag_mapping(resource: str) -> dict:
    if resource == "bots":
        return _bot_tags()
    if resource == "servers":
        return _server_tags()
    if resource == "templates":
        return _template_tags()
    return {}


def _sort_mode_label(sort_mode: str) -> str:
    labels = {
        "newest": t("dctw.sort.newest"),
        "votes": t("dctw.sort.votes"),
        "members": t("dctw.sort.members"),
        "servers": t("dctw.sort.servers"),
        "bumped": t("dctw.sort.bumped"),
    }
    return labels.get(sort_mode, sort_mode)


def _resource_title(resource: str) -> str:
    titles = {
        "bots": t("dctw.resource_title.bots"),
        "servers": t("dctw.resource_title.servers"),
        "templates": t("dctw.resource_title.templates"),
    }
    return titles.get(resource, resource)


RESOURCE_CONFIG = {
    "bots": {
        "list_path": "/api/v2/bots",
        "detail_path": "/api/v2/bots/{id}",
        "id_key": "id",
        "name_key": "name",
        "sort_map": {
            "newest": "created_at",
            "votes": "vote_count",
            "servers": "servers",
            "bumped": "bumped_at",
        },
    },
    "servers": {
        "list_path": "/api/v2/servers",
        "detail_path": "/api/v2/servers/{id}",
        "id_key": "id",
        "name_key": "name",
        "sort_map": {
            "newest": "created_at",
            "votes": "vote_count",
            "members": "members",
            "bumped": "bumped_at",
        },
    },
    "templates": {
        "list_path": "/api/v2/templates",
        "detail_path": "/api/v2/templates/{id}",
        "id_key": "id",
        "name_key": "name",
        "sort_map": {
            "newest": "created_at",
            "votes": "vote_count",
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


def _normalize_text(value, fallback: str | None = None) -> str:
    if fallback is None:
        fallback = t("common.state.none")
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
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            ts = int(dt.timestamp())
            if ts > 0:
                return f"<t:{ts}:f>"
        except (ValueError, OverflowError, OSError):
            pass
    ts = _safe_int(value)
    if ts <= 0:
        return t("dctw.value.unknown")
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


def _compact_join(values, fallback: str | None = None) -> str:
    if fallback is None:
        fallback = t("common.state.none")
    if not isinstance(values, list):
        return fallback
    normalized = [str(value).strip() for value in values if str(value or "").strip()]
    if not normalized:
        return fallback
    return ", ".join(normalized[:10])


def _format_tag_labels(resource: str, tags) -> str:
    if not isinstance(tags, list):
        return t("common.state.none")

    mapping = _tag_mapping(resource)
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
        return t("common.state.none")
    return ", ".join(labels[:10])


def _extract_thumbnail_url(resource: str, item: dict) -> str | None:
    candidates: list = []
    if resource == "bots":
        candidates.append(item.get("avatar"))
    elif resource == "servers":
        candidates.append(item.get("avatar"))
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
    banner_url = _normalize_url(item.get("banner"))
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

    if resource == "bots":
        invite_url = _normalize_url(item.get("inviteLink"))
        if invite_url:
            buttons.append((t("dctw.btn.invite_bot"), invite_url))
        support_url = _normalize_url(item.get("serverLink"))
        website_url = _normalize_url(item.get("webLink"))
        if support_url:
            buttons.append((t("dctw.btn.support_server"), support_url))
        if website_url:
            buttons.append((t("dctw.btn.official_website"), website_url))
    elif resource == "servers":
        invite_url = _normalize_url(item.get("inviteLink"))
        if invite_url:
            buttons.append((t("dctw.btn.join_server"), invite_url))
    elif resource == "templates":
        share_url = _normalize_url(item.get("shareLink"))
        if share_url:
            buttons.append((t("dctw.btn.apply_template"), share_url))

    page_url = _listing_page_url(resource, listing_id)
    if page_url:
        buttons.append((t("dctw.btn.dctw_page"), page_url))

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
        query_text: str | None = None,
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
        self.query_text = (query_text or "").strip()
        self.page_index = page_index
        self.prev_button = discord.ui.Button(label=t("common.btn.prev"), style=discord.ButtonStyle.secondary)
        self.prev_button.callback = self._on_prev_page
        self.next_button = discord.ui.Button(label=t("common.btn.next"), style=discord.ButtonStyle.secondary)
        self.next_button.callback = self._on_next_page
        self.pick_select = discord.ui.Select(placeholder=t("dctw.select.pick_item_ph"), min_values=1, max_values=1)
        self.pick_select.callback = self._on_pick_item
        self._refresh_page()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(t("dctw.err.not_your_browse_list"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
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
            t("dctw.embed.browse_title", resource_title=_resource_title(self.resource)),
            t("dctw.value.sort_label", sort=_sort_mode_label(self.sort_mode)),
            t("dctw.value.page_label", page=self.page_index + 1, total=self._total_pages()),
            t("dctw.value.total_label", count=len(self.items)),
        ]
        if self.query_text:
            header_lines.append(t("dctw.value.search_label", query=self.query_text))
        if self.truncated:
            header_lines.append(t("dctw.value.truncated_note", count=AGGREGATE_LIMIT, limit=AGGREGATE_LIMIT))

        if not page_items:
            return "\n".join(header_lines + ["", t("dctw.value.no_data")])

        lines = []
        for idx, item in page_items:
            listing_id = _safe_int(item.get(conf["id_key"]))
            name = _normalize_text(item.get(conf["name_key"]), t("dctw.value.no_name"))
            votes = _safe_int(item.get("vote_count"))
            extra = ""
            if self.resource == "bots":
                extra = t("dctw.value.extra_servers", count=_safe_int(item.get('servers')))
            elif self.resource == "servers":
                extra = t("dctw.value.extra_members", count=_safe_int(item.get('members')))
            lines.append(t("dctw.value.list_item_line", index=idx + 1, name=name, id=listing_id, votes=votes, extra=extra))

        return "\n".join(header_lines + ["", *lines])

    def _refresh_page(self):
        self.prev_button.disabled = self.page_index <= 0
        self.next_button.disabled = self.page_index >= self._total_pages() - 1

        page_items = self._current_page_items()
        if not page_items:
            self.pick_select.disabled = True
            self.pick_select.options = [discord.SelectOption(label=t("dctw.select.no_items"), value="none")]
        else:
            self.pick_select.disabled = False
            options = []
            conf = RESOURCE_CONFIG[self.resource]
            for idx, item in page_items:
                listing_id = _safe_int(item.get(conf["id_key"]))
                name = _normalize_text(item.get(conf["name_key"]), f"{_resource_title(self.resource)}{listing_id}")
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
            await interaction.response.send_message(t("dctw.err.no_selectable_data"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        selected_index = _safe_int(self.pick_select.values[0], -1)
        if selected_index < 0 or selected_index >= len(self.items):
            await interaction.response.send_message(t("dctw.err.item_not_found"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
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
            query_text=self.query_text,
            selected_item=selected,
        )
        await interaction.edit_original_response(view=detail_view)


class DCTWDetailView(discord.ui.LayoutView):
    def __init__(self, *, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.back_button = discord.ui.Button(label=t("dctw.btn.back_to_list"), style=discord.ButtonStyle.secondary)
        self.back_button.callback = self._back_to_list
        self.comments_button = discord.ui.Button(label=t("dctw.btn.view_comments"), style=discord.ButtonStyle.primary)
        self.comments_button.callback = self._show_comments
        self.vote_button = discord.ui.Button(label=t("dctw.btn.vote"), style=discord.ButtonStyle.success)
        self.vote_button.callback = self._vote_item
        self.bump_button = discord.ui.Button(label=t("dctw.btn.bump"), style=discord.ButtonStyle.danger)
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
        query_text: str | None,
        selected_item: dict,
    ) -> "DCTWDetailView":
        self = cls(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.resource = resource
        self.sort_mode = sort_mode
        self.items = items
        self.page_index = page_index
        self.query_text = (query_text or "").strip()
        self.selected_item = selected_item
        await self._hydrate_selected_item()
        await self._refresh_bump_permission()
        await self._refresh_layout()
        return self

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(t("dctw.err.not_your_detail_page"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
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
        name = _normalize_text(self.selected_item.get(conf["name_key"]), t("dctw.value.no_name"))
        votes = _safe_int(self.selected_item.get("vote_count"))
        created_at = _format_timestamp(self.selected_item.get("created_at"))
        bumped_at = _format_timestamp(self.selected_item.get("bumped_at"))
        thumbnail_url = _extract_thumbnail_url(self.resource, self.selected_item)
        gallery_urls = _extract_gallery_urls(self.resource, self.selected_item)

        lines = [
            t("dctw.value.resource_id_line", resource_title=_resource_title(self.resource), id=listing_id),
            t("dctw.value.votes_line", votes=votes),
            t("dctw.value.created_at_line", created_at=created_at),
            t("dctw.value.bumped_at_line", bumped_at=bumped_at),
        ]
        if self.resource == "bots":
            lines.extend(
                [
                    t("dctw.value.servers_line", count=_safe_int(self.selected_item.get('servers'))),
                    t("dctw.value.verified_status_line", status=t("dctw.value.verified_yes") if self.selected_item.get('is_dc_verified') else t("dctw.value.verified_no")),
                    t("dctw.value.official_verified_line", status=t("common.state.yes") if self.selected_item.get('is_official_verified') else t("common.state.no")),
                    t("dctw.value.prefix_line", prefix=_normalize_text(self.selected_item.get('prefix'))),
                ]
            )
        elif self.resource == "servers":
            lines.extend(
                [
                    t("dctw.value.members_line", count=_safe_int(self.selected_item.get('members'))),
                    t("dctw.value.online_members_line", count=_safe_int(self.selected_item.get('onlineMembers'))),
                ]
            )

        none_label = t("common.state.none")
        tags_line = _format_tag_labels(self.resource, self.selected_item.get("tags"))
        if tags_line != none_label:
            lines.append(t("dctw.value.tags_line", tags=tags_line))

        keywords_line = _compact_join(self.selected_item.get("keywords"))
        if keywords_line != none_label:
            lines.append(t("dctw.value.keywords_line", keywords=keywords_line))

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
        description = _normalize_text(self.selected_item.get("description"), t("dctw.value.no_description"))
        introduce = _normalize_text(self.selected_item.get("introduce"), "")
        body_sections.append((t("dctw.heading.intro"), description))
        if introduce and introduce != description:
            body_sections.append((t("dctw.heading.introduce"), introduce))

        social_links = self.selected_item.get("socialLinks") if isinstance(self.selected_item.get("socialLinks"), dict) else {}
        social_lines = []
        for key, label in [("Line", "LINE"), ("Facebook", "Facebook"), ("Instagram", "Instagram"), ("Twitch", "Twitch"), ("Threads", "Threads"), ("X", "X")]:
            url = _normalize_url(social_links.get(key))
            if url:
                social_lines.append(f"{label}: {url}")
        if social_lines:
            body_sections.append((t("dctw.heading.social_links"), "\n".join(social_lines)))

        if self.resource == "servers":
            features_raw = self.selected_item.get("features")
            features = _compact_join([f.strip() for f in str(features_raw).split(",") if f.strip()], "") if isinstance(features_raw, str) else _compact_join(features_raw, "")
            if features:
                body_sections.append((t("dctw.heading.server_features"), features))
        elif self.resource == "bots":
            developers = await self.cog._format_user_refs(self.selected_item.get("devs"), bullet_prefix="- ")
            if developers:
                body_sections.append((t("dctw.heading.developers"), developers))

        if self.resource == "servers":
            admins = await self.cog._format_server_admins(self.selected_item.get("admins"))
            if admins:
                body_sections.append((t("dctw.heading.admins"), admins))

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
                description = t("dctw.value.gallery_image_indexed", name=name, index=index) if len(gallery_urls) > 1 else t("dctw.value.gallery_image", name=name)
                gallery.add_item(media=image_url, description=description[:256])
            self.add_item(gallery)

        link_specs = _build_detail_link_specs(self.resource, self.selected_item)
        if link_specs:
            for offset in range(0, len(link_specs), 5):
                row = discord.ui.ActionRow()
                for label, url in link_specs[offset:offset + 5]:
                    row.add_item(discord.ui.Button(label=label, url=url, style=discord.ButtonStyle.link))
                self.add_item(row)

        can_bump = bool(getattr(self, "can_bump", False))
        row = discord.ui.ActionRow(
            self.back_button,
            self.comments_button,
            self.vote_button,
        )
        if can_bump:
            row.add_item(self.bump_button)
        self.add_item(row)

    async def _refresh_bump_permission(self):
        conf = RESOURCE_CONFIG[self.resource]
        listing_id = _safe_int(self.selected_item.get(conf["id_key"]))
        if listing_id <= 0:
            self.can_bump = False
            return
        self.can_bump = await self.cog._is_owned_listing(self.user_id, self.resource, listing_id)

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
            query_text=self.query_text,
            page_index=self.page_index,
        )
        await interaction.response.edit_message(view=list_view)

    async def _show_comments(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        comments = self.selected_item.get("comments")
        if not isinstance(comments, list) or not comments:
            await interaction.followup.send(t("dctw.value.no_comments"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        preview_lines = []
        for idx, comment in enumerate(comments[:5], start=1):
            user_id = _safe_int(comment.get("userId"))
            author_name = await self.cog._resolve_user_name(user_id)
            stars = _safe_int(comment.get("stars"))
            content = str(comment.get("content") or t("dctw.value.no_content")).replace("\n", " ")
            created_at = comment.get("created_at", "")
            if created_at:
                ts_line = _format_timestamp(created_at)
            else:
                ts_line = t("dctw.value.unknown")
            preview_lines.append(f"{idx}. {author_name} | {stars}★ | {ts_line}\n{content[:120]}")
        await interaction.followup.send("\n".join(preview_lines), ephemeral=True, allowed_mentions=SAFE_MENTIONS)

    async def _vote_item(self, interaction: discord.Interaction):
        listing_id = self._listing_id()
        await self.cog._do_post_action(interaction, self.resource, listing_id, "vote")

    async def _bump_item(self, interaction: discord.Interaction):
        listing_id = self._listing_id()
        await self.cog._do_post_action(interaction, self.resource, listing_id, "bump")


@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
class DCTW(commands.GroupCog, name=app_commands.locale_str("dctw", i18n_key="cmd.dctw.dctw.root.name"), description=app_commands.locale_str("The DCTW browser!", i18n_key="cmd.dctw.dctw.root.desc")):
    dctw_bot = app_commands.Group(name=app_commands.locale_str("bot", i18n_key="cmd.dctw.bot.root.name"), description=app_commands.locale_str("Bot commands", i18n_key="cmd.dctw.bot.root.desc"))
    dctw_server = app_commands.Group(name=app_commands.locale_str("server", i18n_key="cmd.dctw.server.root.name"), description=app_commands.locale_str("Server commands", i18n_key="cmd.dctw.server.root.desc"))
    dctw_template = app_commands.Group(name=app_commands.locale_str("template", i18n_key="cmd.dctw.template.root.name"), description=app_commands.locale_str("Template commands", i18n_key="cmd.dctw.template.root.desc"))
    dctw_key = app_commands.Group(name=app_commands.locale_str("key", i18n_key="cmd.dctw.key.root.name"), description=app_commands.locale_str("Manage your DCTW API key", i18n_key="cmd.dctw.key.root.desc"))

    def __init__(self, bot_: commands.Bot):
        self.bot = bot_
        self.default_api_key = config("dctw_api_key")
        self._session: aiohttp.ClientSession | None = None
        self._cache_lock = asyncio.Lock()
        self._list_cache: dict[tuple, dict] = {}
        self._owned_cache_lock = asyncio.Lock()
        self._owned_cache: dict[tuple[int, str], dict] = {}
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
            return _normalize_text(raw_user_id, t("dctw.value.unknown"))

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
            raise ValueError(t("dctw.err.unsupported_sort"))

        cache_key = (resource, sort_mode, FETCH_LIMIT, AGGREGATE_LIMIT, "auth")
        now = time.time()
        async with self._cache_lock:
            cached = self._list_cache.get(cache_key)
            if cached and cached["expires_at"] > now:
                self._cache_hits += 1
                return cached["items"], True, cached["truncated"]

        self._cache_misses += 1

        items: list[dict] = []
        truncated = False

        try:
            payload = await self._request_json("GET", conf["list_path"], api_key=api_key)
        except Exception:
            payload = []

        if isinstance(payload, list):
            items = payload
            if len(items) > AGGREGATE_LIMIT:
                items = items[:AGGREGATE_LIMIT]
                truncated = True

        def _sort_value(item: dict):
            v = item.get(sort_key)
            if isinstance(v, (int, float)):
                return (0, -v, "")
            s = str(v or "")
            try:
                return (0, -float(s), "")
            except (ValueError, TypeError):
                pass
            return (1, 0, s)

        items.sort(key=_sort_value)

        async with self._cache_lock:
            self._list_cache[cache_key] = {
                "expires_at": time.time() + CACHE_TTL_SECONDS,
                "items": items,
                "truncated": truncated,
            }

        return items, False, truncated

    async def _fetch_owned_ids(self, user_id: int, resource: str, *, api_key: str, force_refresh: bool = False) -> set[int]:
        # 官方 API 目前沒有 owned item endpoint，等下一版再做
        # 暫時返回空 set
        return set()

    async def _is_owned_listing(self, user_id: int, resource: str, listing_id: int) -> bool:
        # 官方 API 目前沒有 owned item endpoint，暫時一律返回 True
        return True

    async def _post_action(self, resource: str, listing_id: int, action: str, *, api_key: str):
        conf = RESOURCE_CONFIG[resource]
        path = f"{conf['list_path']}/{listing_id}/{action}"
        await self._request_json("POST", path, api_key=api_key)

    def _matches_search(self, resource: str, item: dict, keyword: str) -> bool:
        conf = RESOURCE_CONFIG[resource]
        listing_id = _safe_int(item.get(conf["id_key"]), -1)
        if listing_id > 0 and keyword in str(listing_id):
            return True

        for text_field in ("name", "description", "introduce"):
            value = str(item.get(text_field) or "").casefold()
            if value and keyword in value:
                return True

        for list_field in ("keywords", "tags"):
            values = item.get(list_field)
            if isinstance(values, list):
                for entry in values:
                    if keyword in str(entry or "").casefold():
                        return True

        return False

    async def _send_search(self, interaction: discord.Interaction, resource: str, keyword: str, sort_mode: str):
        query = keyword.strip()
        if not query:
            await interaction.response.send_message(t("dctw.err.empty_search_query"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        await interaction.response.defer()
        request_key = self._get_read_api_key(interaction.user.id)
        if not request_key:
            await interaction.followup.send(t("dctw.err.api_key_required"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        try:
            items, cached, truncated = await self._fetch_and_sort_resource(resource, sort_mode, api_key=request_key)
        except Exception as exc:
            await interaction.followup.send(t("dctw.err.search_failed", error=_format_error(exc)), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        normalized = query.casefold()
        filtered_items = [item for item in items if self._matches_search(resource, item, normalized)]
        if not filtered_items:
            await interaction.followup.send(t("dctw.err.no_search_results", query=query), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        view = DCTWBrowseView(
            self,
            user_id=interaction.user.id,
            resource=resource,
            sort_mode=sort_mode,
            items=filtered_items,
            cached=cached,
            truncated=truncated,
            query_text=query,
            page_index=0,
        )
        await interaction.followup.send(view=view, allowed_mentions=SAFE_MENTIONS)

    async def _do_bumpall(self, interaction: discord.Interaction, resources: list[str]):
        await interaction.response.defer(ephemeral=True, thinking=True)
        user_key = self._get_user_key(interaction.user.id)
        if not user_key:
            await interaction.followup.send(t("dctw.err.personal_key_required"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        owned_targets: list[tuple[str, int]] = []
        fetch_errors: list[str] = []
        for resource in resources:
            try:
                request_key = self._get_read_api_key(interaction.user.id)
                items, _, _ = await self._fetch_and_sort_resource(resource, "bumped", api_key=request_key)
                conf = RESOURCE_CONFIG[resource]
                for item in items:
                    listing_id = _safe_int(item.get(conf["id_key"]))
                    if listing_id > 0:
                        owned_targets.append((resource, listing_id))
            except Exception as exc:
                fetch_errors.append(f"{resource}: {_format_error(exc)}")
                continue

        if not owned_targets:
            error_part = t("dctw.value.fetch_error_suffix", errors="; ".join(fetch_errors)) if fetch_errors else ""
            await interaction.followup.send(t("dctw.err.no_bumpable_resources", error_part=error_part), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        owned_targets.sort(key=lambda t: t[1])

        estimated_minutes = math.ceil(max(0, len(owned_targets) - 1) * BUMP_COOLDOWN_SECONDS / 60)
        await interaction.followup.send(
            t("dctw.msg.bump_in_progress", minutes=estimated_minutes),
            ephemeral=True,
            allowed_mentions=SAFE_MENTIONS,
        )

        success_count = 0
        failed: list[str] = []
        for idx, (resource, listing_id) in enumerate(owned_targets):
            if idx > 0:
                await asyncio.sleep(BUMP_COOLDOWN_SECONDS)
            try:
                await self._post_action(resource, listing_id, "bump", api_key=user_key)
                success_count += 1
            except Exception as exc:
                failed.append(f"{resource}:{listing_id} -> {_format_error(exc)}")

        summary_lines = [
            t("dctw.msg.bump_summary", success=success_count, total=len(owned_targets)),
            t("dctw.value.resource_category_line", resources=", ".join(resources)),
        ]
        if fetch_errors:
            summary_lines.append(t("dctw.value.owned_fetch_errors_header"))
            summary_lines.extend(f"- {line}" for line in fetch_errors[:10])
        if failed:
            summary_lines.append(t("dctw.value.bump_failed_header"))
            summary_lines.extend(f"- {line}" for line in failed[:15])

        await interaction.followup.send("\n".join(summary_lines), ephemeral=True, allowed_mentions=SAFE_MENTIONS)

    async def _send_browse(self, interaction: discord.Interaction, resource: str, sort_mode: str):
        await interaction.response.defer()
        request_key = self._get_read_api_key(interaction.user.id)
        if not request_key:
            await interaction.followup.send(t("dctw.err.api_key_required"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        try:
            items, cached, truncated = await self._fetch_and_sort_resource(resource, sort_mode, api_key=request_key)
        except Exception as exc:
            await interaction.followup.send(t("dctw.err.search_failed", error=_format_error(exc)), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        view = DCTWBrowseView(
            self,
            user_id=interaction.user.id,
            resource=resource,
            sort_mode=sort_mode,
            items=items,
            cached=cached,
            truncated=truncated,
            query_text=None,
            page_index=0,
        )
        await interaction.followup.send(view=view, allowed_mentions=SAFE_MENTIONS)

    async def _do_post_action(self, interaction: discord.Interaction, resource: str, listing_id: int, action: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        user_key = self._get_user_key(interaction.user.id)
        if not user_key:
            await interaction.followup.send(t("dctw.err.personal_key_required"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        try:
            await self._post_action(resource, listing_id, action, api_key=user_key)
        except Exception as exc:
            await interaction.followup.send(t("dctw.err.action_failed", error=_format_error(exc)), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return

        await interaction.followup.send(t("dctw.msg.action_done", resource=resource, id=listing_id, action=action), ephemeral=True, allowed_mentions=SAFE_MENTIONS)

    @dctw_key.command(name=app_commands.locale_str("set", i18n_key="cmd.dctw.key.set.name"), description=app_commands.locale_str("Set your DCTW API key", i18n_key="cmd.dctw.key.set.desc"))
    async def key_set(self, interaction: discord.Interaction, api_key: str):
        set_user_data(0, interaction.user.id, USER_KEY_NAME, api_key.strip())
        await interaction.response.send_message(t("dctw.msg.key_saved"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)

    @dctw_key.command(name=app_commands.locale_str("clear", i18n_key="cmd.dctw.key.clear.name"), description=app_commands.locale_str("Clear your DCTW API key", i18n_key="cmd.dctw.key.clear.desc"))
    async def key_clear(self, interaction: discord.Interaction):
        set_user_data(0, interaction.user.id, USER_KEY_NAME, "")
        await interaction.response.send_message(t("dctw.msg.key_cleared"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)

    @dctw_key.command(name=app_commands.locale_str("show", i18n_key="cmd.dctw.key.show.name"), description=app_commands.locale_str("Check whether a DCTW API key is set", i18n_key="cmd.dctw.key.show.desc"))
    async def key_show(self, interaction: discord.Interaction):
        api_key = self._get_user_key(interaction.user.id)
        if not api_key:
            await interaction.response.send_message(t("dctw.err.no_key_set"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        masked = api_key[:4] + "*" * max(0, len(api_key) - 8) + api_key[-4:]
        await interaction.response.send_message(t("dctw.value.current_key_line", masked=masked), ephemeral=True, allowed_mentions=SAFE_MENTIONS)

    @dctw_key.command(name=app_commands.locale_str("help", i18n_key="cmd.dctw.key.help.name"), description=app_commands.locale_str("How to get a DCTW API key", i18n_key="cmd.dctw.key.help.desc"))
    async def key_help(self, interaction: discord.Interaction):
        embed = discord.Embed(title=t("dctw.embed.help_title"), color=discord.Colour.blue())
        embed.add_field(
            name=t("dctw.embed.help.field1_name"),
            value=t("dctw.embed.help.field1_value"),
            inline=False,
        )
        embed.add_field(
            name=t("dctw.embed.help.field2_name"),
            value=t("dctw.embed.help.field2_value"),
            inline=False,
        )
        embed.add_field(
            name=t("dctw.embed.help.field3_name"),
            value=t("dctw.embed.help.field3_value"),
            inline=False,
        )
        embed.add_field(
            name=t("dctw.embed.help.field4_name"),
            value=t("dctw.embed.help.field4_value"),
            inline=False,
        )
        embed.set_footer(text=t("dctw.embed.help_footer"))
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=SAFE_MENTIONS)

    @dctw_bot.command(name=app_commands.locale_str("browse", i18n_key="cmd.dctw.bot.browse.name"), description=app_commands.locale_str("Browse the bot list", i18n_key="cmd.dctw.bot.browse.desc"))
    @app_commands.choices(
        sort=[
            app_commands.Choice(name=app_commands.locale_str("Newest", i18n_key="cmd.dctw.bot.browse.choice.newest"), value="newest"),
            app_commands.Choice(name=app_commands.locale_str("Votes", i18n_key="cmd.dctw.bot.browse.choice.votes"), value="votes"),
            app_commands.Choice(name=app_commands.locale_str("Servers", i18n_key="cmd.dctw.bot.browse.choice.servers"), value="servers"),
            app_commands.Choice(name=app_commands.locale_str("Bumped", i18n_key="cmd.dctw.bot.browse.choice.bumped"), value="bumped"),
        ]
    )
    async def bot_browse(self, interaction: discord.Interaction, sort: app_commands.Choice[str] | None = None):
        await self._send_browse(interaction, "bots", sort.value if sort else "bumped")

    @dctw_server.command(name=app_commands.locale_str("browse", i18n_key="cmd.dctw.server.browse.name"), description=app_commands.locale_str("Browse the server list", i18n_key="cmd.dctw.server.browse.desc"))
    @app_commands.choices(
        sort=[
            app_commands.Choice(name=app_commands.locale_str("Newest", i18n_key="cmd.dctw.server.browse.choice.newest"), value="newest"),
            app_commands.Choice(name=app_commands.locale_str("Votes", i18n_key="cmd.dctw.server.browse.choice.votes"), value="votes"),
            app_commands.Choice(name=app_commands.locale_str("Members", i18n_key="cmd.dctw.server.browse.choice.members"), value="members"),
            app_commands.Choice(name=app_commands.locale_str("Bumped", i18n_key="cmd.dctw.server.browse.choice.bumped"), value="bumped"),
        ]
    )
    async def server_browse(self, interaction: discord.Interaction, sort: app_commands.Choice[str] | None = None):
        await self._send_browse(interaction, "servers", sort.value if sort else "bumped")

    @dctw_template.command(name=app_commands.locale_str("browse", i18n_key="cmd.dctw.template.browse.name"), description=app_commands.locale_str("Browse the template list", i18n_key="cmd.dctw.template.browse.desc"))
    @app_commands.choices(
        sort=[
            app_commands.Choice(name=app_commands.locale_str("Newest", i18n_key="cmd.dctw.template.browse.choice.newest"), value="newest"),
            app_commands.Choice(name=app_commands.locale_str("Votes", i18n_key="cmd.dctw.template.browse.choice.votes"), value="votes"),
            app_commands.Choice(name=app_commands.locale_str("Bumped", i18n_key="cmd.dctw.template.browse.choice.bumped"), value="bumped"),
        ]
    )
    async def template_browse(self, interaction: discord.Interaction, sort: app_commands.Choice[str] | None = None):
        await self._send_browse(interaction, "templates", sort.value if sort else "bumped")

    @dctw_bot.command(name=app_commands.locale_str("search", i18n_key="cmd.dctw.bot.search.name"), description=app_commands.locale_str("Search bots", i18n_key="cmd.dctw.bot.search.desc"))
    @app_commands.choices(
        sort=[
            app_commands.Choice(name=app_commands.locale_str("Newest", i18n_key="cmd.dctw.bot.search.choice.newest"), value="newest"),
            app_commands.Choice(name=app_commands.locale_str("Votes", i18n_key="cmd.dctw.bot.search.choice.votes"), value="votes"),
            app_commands.Choice(name=app_commands.locale_str("Servers", i18n_key="cmd.dctw.bot.search.choice.servers"), value="servers"),
            app_commands.Choice(name=app_commands.locale_str("Bumped", i18n_key="cmd.dctw.bot.search.choice.bumped"), value="bumped"),
        ]
    )
    async def bot_search(self, interaction: discord.Interaction, keyword: str, sort: app_commands.Choice[str] | None = None):
        await self._send_search(interaction, "bots", keyword, sort.value if sort else "bumped")

    @dctw_server.command(name=app_commands.locale_str("search", i18n_key="cmd.dctw.server.search.name"), description=app_commands.locale_str("Search servers", i18n_key="cmd.dctw.server.search.desc"))
    @app_commands.choices(
        sort=[
            app_commands.Choice(name=app_commands.locale_str("Newest", i18n_key="cmd.dctw.server.search.choice.newest"), value="newest"),
            app_commands.Choice(name=app_commands.locale_str("Votes", i18n_key="cmd.dctw.server.search.choice.votes"), value="votes"),
            app_commands.Choice(name=app_commands.locale_str("Members", i18n_key="cmd.dctw.server.search.choice.members"), value="members"),
            app_commands.Choice(name=app_commands.locale_str("Bumped", i18n_key="cmd.dctw.server.search.choice.bumped"), value="bumped"),
        ]
    )
    async def server_search(self, interaction: discord.Interaction, keyword: str, sort: app_commands.Choice[str] | None = None):
        await self._send_search(interaction, "servers", keyword, sort.value if sort else "bumped")

    @dctw_template.command(name=app_commands.locale_str("search", i18n_key="cmd.dctw.template.search.name"), description=app_commands.locale_str("Search templates", i18n_key="cmd.dctw.template.search.desc"))
    @app_commands.choices(
        sort=[
            app_commands.Choice(name=app_commands.locale_str("Newest", i18n_key="cmd.dctw.template.search.choice.newest"), value="newest"),
            app_commands.Choice(name=app_commands.locale_str("Votes", i18n_key="cmd.dctw.template.search.choice.votes"), value="votes"),
            app_commands.Choice(name=app_commands.locale_str("Bumped", i18n_key="cmd.dctw.template.search.choice.bumped"), value="bumped"),
        ]
    )
    async def template_search(self, interaction: discord.Interaction, keyword: str, sort: app_commands.Choice[str] | None = None):
        await self._send_search(interaction, "templates", keyword, sort.value if sort else "bumped")

    @app_commands.command(name=app_commands.locale_str("bumpall", i18n_key="cmd.dctw.dctw.bumpall.name"), description=app_commands.locale_str("Bump everything you own at once", i18n_key="cmd.dctw.dctw.bumpall.desc"))
    async def bumpall(self, interaction: discord.Interaction):
        await self._do_bumpall(interaction, ["bots", "servers", "templates"])

    @dctw_bot.command(name=app_commands.locale_str("bumpall", i18n_key="cmd.dctw.bot.bumpall.name"), description=app_commands.locale_str("Bump all bots you own at once", i18n_key="cmd.dctw.bot.bumpall.desc"))
    async def bot_bumpall(self, interaction: discord.Interaction):
        await self._do_bumpall(interaction, ["bots"])

    @dctw_server.command(name=app_commands.locale_str("bumpall", i18n_key="cmd.dctw.server.bumpall.name"), description=app_commands.locale_str("Bump all servers you own at once", i18n_key="cmd.dctw.server.bumpall.desc"))
    async def server_bumpall(self, interaction: discord.Interaction):
        await self._do_bumpall(interaction, ["servers"])

    @dctw_template.command(name=app_commands.locale_str("bumpall", i18n_key="cmd.dctw.template.bumpall.name"), description=app_commands.locale_str("Bump all templates you own at once", i18n_key="cmd.dctw.template.bumpall.desc"))
    async def template_bumpall(self, interaction: discord.Interaction):
        await self._do_bumpall(interaction, ["templates"])

    @dctw_bot.command(name=app_commands.locale_str("vote", i18n_key="cmd.dctw.bot.vote.name"), description=app_commands.locale_str("Vote for a specific bot", i18n_key="cmd.dctw.bot.vote.desc"))
    async def bot_vote(self, interaction: discord.Interaction, target: str):
        bot_id = _parse_user_mention_or_id(target)
        if bot_id is None:
            await interaction.response.send_message(t("dctw.err.invalid_bot_target"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        await self._do_post_action(interaction, "bots", bot_id, "vote")

    @dctw_bot.command(name=app_commands.locale_str("bump", i18n_key="cmd.dctw.bot.bump.name"), description=app_commands.locale_str("Bump a specific bot", i18n_key="cmd.dctw.bot.bump.desc"))
    async def bot_bump(self, interaction: discord.Interaction, target: str):
        bot_id = _parse_user_mention_or_id(target)
        if bot_id is None:
            await interaction.response.send_message(t("dctw.err.invalid_bot_target"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        await self._do_post_action(interaction, "bots", bot_id, "bump")

    @dctw_server.command(name=app_commands.locale_str("vote", i18n_key="cmd.dctw.server.vote.name"), description=app_commands.locale_str("Vote for a specific server", i18n_key="cmd.dctw.server.vote.desc"))
    async def server_vote(self, interaction: discord.Interaction, target: str):
        server_id = _parse_numeric_id(target)
        if server_id is None:
            await interaction.response.send_message(t("dctw.err.invalid_server_target"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        await self._do_post_action(interaction, "servers", server_id, "vote")

    @dctw_server.command(name=app_commands.locale_str("bump", i18n_key="cmd.dctw.server.bump.name"), description=app_commands.locale_str("Bump a specific server", i18n_key="cmd.dctw.server.bump.desc"))
    async def server_bump(self, interaction: discord.Interaction, target: str):
        server_id = _parse_numeric_id(target)
        if server_id is None:
            await interaction.response.send_message(t("dctw.err.invalid_server_target"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        await self._do_post_action(interaction, "servers", server_id, "bump")

    @dctw_template.command(name=app_commands.locale_str("vote", i18n_key="cmd.dctw.template.vote.name"), description=app_commands.locale_str("Vote for a specific template", i18n_key="cmd.dctw.template.vote.desc"))
    async def template_vote(self, interaction: discord.Interaction, target: str):
        template_id = _parse_numeric_id(target)
        if template_id is None:
            await interaction.response.send_message(t("dctw.err.invalid_template_target"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        await self._do_post_action(interaction, "templates", template_id, "vote")

    @dctw_template.command(name=app_commands.locale_str("bump", i18n_key="cmd.dctw.template.bump.name"), description=app_commands.locale_str("Bump a specific template", i18n_key="cmd.dctw.template.bump.desc"))
    async def template_bump(self, interaction: discord.Interaction, target: str):
        template_id = _parse_numeric_id(target)
        if template_id is None:
            await interaction.response.send_message(t("dctw.err.invalid_template_target"), ephemeral=True, allowed_mentions=SAFE_MENTIONS)
            return
        await self._do_post_action(interaction, "templates", template_id, "bump")

    @app_commands.command(name=app_commands.locale_str("cache-stats", i18n_key="cmd.dctw.dctw.cache_stats.name"), description=app_commands.locale_str("View DCTW cache status", i18n_key="cmd.dctw.dctw.cache_stats.desc"))
    async def cache_stats(self, interaction: discord.Interaction):
        total = self._cache_hits + self._cache_misses
        hit_rate = (self._cache_hits / total * 100.0) if total else 0.0
        await interaction.response.send_message(
            t(
                "dctw.msg.cache_stats",
                ttl=CACHE_TTL_SECONDS,
                cached_items=len(self._list_cache),
                hits=self._cache_hits,
                misses=self._cache_misses,
                hit_rate=f"{hit_rate:.2f}",
            ),
            ephemeral=True,
            allowed_mentions=SAFE_MENTIONS,
        )


asyncio.run(bot.add_cog(DCTW(bot)))
