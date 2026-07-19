from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from globalenv import bot, get_emoji_by_name, get_server_config, set_server_config, start_bot
from logger import log


FIXLINK_CONFIG_KEY = "fixlink"
FIXLINK_WEBHOOKS_KEY = "fixlink_webhooks"
FIXEMBED_REVISION = "154"
SHARE_CACHE_SECONDS = 600
MAX_CUSTOM_PLATFORMS = 10
MAX_GENERATED_URL_LENGTH = 1800
MAX_REPLY_CHUNK_LENGTH = 1900
EMBED_PREVIEW_DELAY_SECONDS = 7
THREADS_HOSTS = {"threads.com", "threads.net"}
TRASH_EMOJI_FALLBACK = "🗑️"
TRAILING_URL_PUNCTUATION = ".,!?;:)]}\uff0c\u3002\uff01\uff1f\uff1b\uff1a\uff09\u3011\u300b\u300d\u300f"

URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
THREADS_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._]+$")
THREADS_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
QUERY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.~-]{1,64}$")
HOST_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


supported_platforms = {
    "Threads": {
        "origins": sorted(THREADS_HOSTS),
        "fixers": {
            "FzThreads": {"host": "fzthreads.com"},
            "FixEmbed": {"query_endpoint": "https://fixembed.app/embed"},
        },
        "default_fixer": "FzThreads",
        "special_handler": "threads",
    },
    "Twitter": {
        "origins": ["twitter.com", "x.com", "mobile.twitter.com"],
        "rules": [{"path": r"^/(?:i/(?:web/)?status|[^/]+/status)/[0-9]+(?:/.*)?$"}],
        "fixers": {
            "FxTwitter": {"host": "fxtwitter.com"},
            "VxTwitter": {"host": "vxtwitter.com"},
        },
        "default_fixer": "FxTwitter",
    },
    "Instagram": {
        "origins": ["instagram.com"],
        "rules": [
            {"path": r"^/(?:[^/]+/)?(?:p|reels?)/[A-Za-z0-9_-]+/?$"},
            {"path": r"^/reels/[A-Za-z0-9_-]+/?$"},
            {"path": r"^/share(?:/(?:p|reel))?/[A-Za-z0-9_-]+/?$"},
            {"path": r"^/stories/[^/]+/[0-9]+/?$"},
        ],
        "keep_query_keys": ["img_index"],
        "fixers": {
            "VxInstagram": {"host": "vxinstagram.com"},
            "KKInstagram": {"host": "kkinstagram.com"},
        },
        "default_fixer": "VxInstagram",
    },
    "TikTok": {
        "origins": ["tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"],
        "rules": [
            {
                "hosts": ["tiktok.com", "m.tiktok.com"],
                "path": r"^/@[^/]+/(?:video|photo)/[0-9]+/?$",
            },
            {"hosts": ["tiktok.com", "m.tiktok.com"], "path": r"^/(?:t|embed)/[A-Za-z0-9]+/?$"},
            {"hosts": ["vm.tiktok.com", "vt.tiktok.com"], "path": r"^/[A-Za-z0-9]+/?$"},
        ],
        "fixers": {
            "FxTikTok": {"host": "tnktok.com"},
            "TikTxk": {"host": "tiktxk.com"},
            "KKTikTok": {"host": "kktiktok.com"},
        },
        "default_fixer": "FxTikTok",
    },
    "Reddit": {
        "origins": ["reddit.com", "old.reddit.com", "redd.it"],
        "rules": [
            {
                "hosts": ["reddit.com", "old.reddit.com"],
                "path": r"^/(?:(?:r|u|user)/[^/]+/(?:comments|s)/[^/]+|comments/[^/]+)(?:/.*)?$",
            },
            {"hosts": ["redd.it"], "path": r"^/[A-Za-z0-9]+/?$"},
        ],
        "fixers": {
            "FixReddit": {"host": "rxddit.com"},
            "VxReddit": {"host": "vxreddit.com", "origins": ["reddit.com", "old.reddit.com"]},
        },
        "default_fixer": "VxReddit",
    },
    "Facebook": {
        "origins": ["facebook.com", "m.facebook.com"],
        "rules": [
            {"path": r"^/[^/]+/(?:posts|videos)/.+$"},
            {"path": r"^/(?:share(?:/[rpv])?|reel)/[^/]+/?$"},
            {"path": r"^/groups/[^/]+/(?:posts|permalink)/[^/]+/?$"},
            {"path": r"^/groups/[^/]+/?$", "query_keys": ["multi_permalinks"]},
            {"path": r"^/(?:photo(?:\.php)?|watch)/?$", "query_any": ["fbid", "v"]},
            {"path": r"^/story\.php/?$", "query_keys": ["story_fbid", "id"]},
            {"path": r"^/permalink\.php/?$", "query_keys": ["story_fbid", "id"]},
        ],
        "keep_query_keys": ["fbid", "v", "story_fbid", "id", "multi_permalinks"],
        "fixers": {"Facebed": {"host": "facebed.com"}},
        "default_fixer": "Facebed",
    },
    "Bilibili": {
        "origins": ["bilibili.com", "m.bilibili.com", "b23.tv"],
        "rules": [
            {
                "hosts": ["bilibili.com", "m.bilibili.com"],
                "path": r"^/(?:video|bangumi/(?:play|media)|opus|dynamic|space|detail|m/detail)/[^/]+/?$",
            },
            {
                "hosts": ["bilibili.com", "m.bilibili.com"],
                "path": r"^/bangumi/v2/media-index/?$",
                "query_keys": ["media_id"],
            },
            {"hosts": ["b23.tv"], "path": r"^/[A-Za-z0-9]+/?$"},
        ],
        "keep_query_keys": ["p", "media_id"],
        "fixers": {
            "BiliFix": {
                "host": "vxbilibili.com",
                "host_map": {"b23.tv": "vxb23.tv"},
            },
            "FxBilibili": {
                "host": "fxbilibili.seria.moe",
                "origins": ["bilibili.com", "m.bilibili.com"],
            },
        },
        "default_fixer": "BiliFix",
    },
    "Pixiv": {
        "origins": ["pixiv.net"],
        "rules": [
            {"path": r"^/(?:[a-z]{2}/)?artworks/[0-9]+(?:/[^/]+)?/?$"},
            {"path": r"^/member_illust\.php/?$", "query_keys": ["illust_id"]},
        ],
        "keep_query_keys": ["illust_id"],
        "fixers": {"Phixiv": {"host": "phixiv.net"}},
        "default_fixer": "Phixiv",
    },
    "Pinterest": {
        "origins": ["pinterest.com", "pin.it"],
        "rules": [
            {"hosts": ["pinterest.com"], "path": r"^/pin/[0-9]+/?$"},
            {"hosts": ["pin.it"], "path": r"^/[A-Za-z0-9]+/?$"},
        ],
        "fixers": {"EmbedEZ": {"host": "pinterestez.com"}},
        "default_fixer": "EmbedEZ",
    },
    "YouTube": {
        "origins": ["youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"],
        "rules": [
            {"hosts": ["youtube.com", "m.youtube.com", "music.youtube.com"], "path": r"^/watch/?$", "query_keys": ["v"]},
            {"hosts": ["youtube.com", "m.youtube.com"], "path": r"^/playlist/?$", "query_keys": ["list"]},
            {"hosts": ["youtube.com", "m.youtube.com"], "path": r"^/shorts/[A-Za-z0-9_-]+/?$"},
            {"hosts": ["youtu.be"], "path": r"^/[A-Za-z0-9_-]+/?$"},
        ],
        "keep_query_keys": ["v", "list", "index", "t", "start"],
        "fixers": {
            "Koutube": {"host": "koutube.com"},
            "FixYouTube": {"host": "y.outube.duckdns.org"},
        },
        "default_fixer": "Koutube",
    },
    "Twitch": {
        "origins": ["twitch.tv"],
        "rules": [
            {"hosts": ["twitch.tv"], "path": r"^/[^/]+/clip/[^/]+/?$"},
        ],
        "fixers": {"FxTwitch": {"host": "fxtwitch.seria.moe"}},
        "default_fixer": "FxTwitch",
    },
    "Bluesky": {
        "origins": ["bsky.app"],
        "rules": [{"path": r"^/profile/[^/]+/post/[^/]+/?$"}],
        "fixers": {
            "FxBluesky": {"host": "fxbsky.app"},
            "VixBluesky": {"host": "bskx.app"},
            "VxBluesky": {"host": "vxbsky.app"},
        },
        "default_fixer": "FxBluesky",
    },
    "Spotify": {
        "origins": ["open.spotify.com"],
        "rules": [{"path": r"^/(?:intl-[^/]+/)?track/[^/]+/?$"}],
        "fixers": {
            "FxSpotify": {"host": "fxspotify.com"},
            "FixSpotify": {"host": "fixspotify.com"},
        },
        "default_fixer": "FxSpotify",
    },
    "DeviantArt": {
        "origins": ["deviantart.com"],
        "rules": [{"path": r"^/(?:[^/]+/(?:art|journal)/[^/]+|deviation/[0-9]+)/?$"}],
        "fixers": {"FixDeviantArt": {"host": "fixdeviantart.com"}},
        "default_fixer": "FixDeviantArt",
    },
    "Imgur": {
        "origins": ["imgur.com"],
        "rules": [{"path": r"^/(?:gallery/|a/)?[A-Za-z0-9_-]+/?$"}],
        "fixers": {"EmbedEZ": {"host": "imgurez.com"}},
        "default_fixer": "EmbedEZ",
    },
    "Weibo": {
        "origins": ["weibo.com", "weibo.cn", "m.weibo.cn"],
        "rules": [
            {"hosts": ["weibo.com"], "path": r"^/[0-9]+/[A-Za-z0-9]+/?$"},
            {"hosts": ["weibo.cn", "m.weibo.cn"], "path": r"^/status/[A-Za-z0-9]+/?$"},
        ],
        "fixers": {"EmbedEZ": {"host": "weiboez.com"}},
        "default_fixer": "EmbedEZ",
    },
    "Newgrounds": {
        "origins": ["newgrounds.com"],
        "origin_suffixes": ["newgrounds.com"],
        "rules": [{"path": r"^/art/view/[^/]+/[^/]+/?$"}],
        "fixers": {"FixNewgrounds": {"host": "fixnewgrounds.com"}},
        "default_fixer": "FixNewgrounds",
    },
    "PTT": {
        "origins": ["ptt.cc"],
        "rules": [{"path": r"^/bbs/[^/]+/[^/]+\.html$"}],
        "fixers": {"FxPTT": {"host": "fxptt.seria.moe"}},
        "default_fixer": "FxPTT",
    },
    "Roblox": {
        "origins": ["roblox.com"],
        "rules": [{"path": r"^/(?:games|users|catalog|groups|communities)/[^/]+(?:/.*)?$"}],
        "fixers": {"FixRoblox": {"host": "fixroblox.com"}},
        "default_fixer": "FixRoblox",
    },
    "Fur Affinity": {
        "origins": ["furaffinity.net"],
        "rules": [{"path": r"^/view/[0-9]+/?$"}],
        "fixers": {
            "XFurAffinity": {"host": "xfuraffinity.net"},
            "FxRaffinity": {"host": "fxraffinity.net"},
        },
        "default_fixer": "XFurAffinity",
    },
}

DEFAULT_PREFERRED_FIXERS = {
    name: platform["default_fixer"] for name, platform in supported_platforms.items()
}


DEFAULT_FIXLINK_CONFIG = {
    "enabled": False,
    "remove_tracker": False,
    "webhook_mode": False,
    "webhook_only_with_tracker": False,
    "disabled_platforms": [],
    "preferred_fixers": dict(DEFAULT_PREFERRED_FIXERS),
    "custom_platforms": [],
}


@dataclass(frozen=True)
class ExtractedURL:
    url: str
    start: int
    end: int


@dataclass(frozen=True)
class LinkMatch:
    platform_key: str
    platform_name: str
    source_url: str
    start: int
    end: int
    fixers: tuple[tuple[str, str], ...]
    primary_url: str
    has_tracker: bool = False
    username: str | None = None
    profile_url: str | None = None


def _unique_strings(values, *, limit: int | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if limit is not None and len(result) >= limit:
            break
    return result


def _split_form_values(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = re.split(r"[,\n]", str(value))
    return _unique_strings(raw_values)


def _normalize_display_name(value, field_name: str) -> str:
    text = str(value or "").strip()
    if not 1 <= len(text) <= 40:
        raise ValueError(f"{field_name}\u5fc5\u9808\u662f 1 \u5230 40 \u500b\u5b57\u3002")
    if any(ord(character) < 32 for character in text):
        raise ValueError(f"{field_name}\u4e0d\u80fd\u5305\u542b\u63a7\u5236\u5b57\u5143\u3002")
    return text


def _normalize_public_hostname(value, field_name: str) -> str:
    hostname = str(value or "").strip().rstrip(".").casefold()
    if not hostname or any(token in hostname for token in ("://", "/", "@", "*", ":")):
        raise ValueError(f"{field_name}\u5fc5\u9808\u662f\u7cbe\u78ba\u7db2\u57df\uff0c\u4e0d\u80fd\u5305\u542b scheme\u3001port \u6216 wildcard\u3002")
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError(f"{field_name}\u4e0d\u662f\u6709\u6548\u7db2\u57df\u3002") from error
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError(f"{field_name}\u4e0d\u5141\u8a31 IP literal\u3002")
    if "." not in hostname or hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError(f"{field_name}\u5fc5\u9808\u662f\u516c\u958b\u7db2\u57df\u3002")
    labels = hostname.split(".")
    if len(hostname) > 253 or any(not HOST_LABEL_PATTERN.fullmatch(label) for label in labels):
        raise ValueError(f"{field_name}\u4e0d\u662f\u6709\u6548\u7db2\u57df\u3002")
    return hostname


def _normalize_endpoint(value) -> str:
    endpoint = str(value or "").strip()
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Endpoint URL \u683c\u5f0f\u4e0d\u6b63\u78ba\u3002") from error
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise ValueError("Endpoint \u5fc5\u9808\u4f7f\u7528 HTTPS \u4e26\u5305\u542b\u516c\u958b\u7db2\u57df\u3002")
    if parsed.username or parsed.password or port not in (None, 443):
        raise ValueError("Endpoint \u4e0d\u80fd\u5305\u542b userinfo \u6216\u81ea\u8a02 port\u3002")
    if parsed.query or parsed.fragment:
        raise ValueError("Endpoint \u4e0d\u80fd\u81ea\u5e36 query \u6216 fragment\uff0c\u8acb\u6539\u7528\u975c\u614b query \u6b04\u4f4d\u3002")
    hostname = _normalize_public_hostname(parsed.hostname, "Endpoint \u7db2\u57df")
    return urlunsplit(("https", hostname, parsed.path or "/", "", ""))


def _normalize_query_key(value, field_name: str) -> str:
    key = str(value or "").strip()
    if not QUERY_KEY_PATTERN.fullmatch(key):
        raise ValueError(f"{field_name}\u53ea\u80fd\u4f7f\u7528\u82f1\u6578\u5b57\u3001`.`\u3001`_`\u3001`~` \u6216 `-`\uff0c\u9577\u5ea6\u4e0a\u9650 64\u3002")
    return key


def _normalize_path_prefixes(value) -> list[str]:
    prefixes = _split_form_values(value)
    if not 1 <= len(prefixes) <= 5:
        raise ValueError("\u8acb\u8f38\u5165 1 \u5230 5 \u500b\u8def\u5f91\u524d\u7db4\u3002")
    normalized: list[str] = []
    for prefix in prefixes:
        if not prefix.startswith("/") or any(token in prefix for token in ("?", "#", "\\")):
            raise ValueError("\u8def\u5f91\u524d\u7db4\u5fc5\u9808\u4ee5 `/` \u958b\u982d\uff0c\u4e14\u4e0d\u80fd\u5305\u542b query\u3001fragment \u6216\u53cd\u659c\u7dda\u3002")
        if any(character.isspace() for character in prefix):
            raise ValueError("\u8def\u5f91\u524d\u7db4\u4e0d\u80fd\u5305\u542b\u7a7a\u767d\u5b57\u5143\u3002")
        if prefix not in normalized:
            normalized.append(prefix)
    return normalized


def _normalize_static_query(value) -> dict[str, str]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        pairs = [(str(key), str(item)) for key, item in value.items()]
    else:
        query_text = str(value).strip().replace("\r\n", "\n").replace("\n", "&")
        if not query_text:
            return {}
        try:
            pairs = parse_qsl(query_text, keep_blank_values=True, strict_parsing=True)
        except ValueError as error:
            raise ValueError("\u975c\u614b query \u5fc5\u9808\u4f7f\u7528 `key=value`\uff0c\u591a\u7d44\u53ef\u5206\u884c\u8f38\u5165\u3002") from error
    if len(pairs) > 10:
        raise ValueError("\u975c\u614b query \u6700\u591a 10 \u7d44\u3002")
    result: dict[str, str] = {}
    for raw_key, raw_value in pairs:
        key = _normalize_query_key(raw_key, "\u975c\u614b query key")
        if key in result:
            raise ValueError(f"\u975c\u614b query key `{key}` \u91cd\u8907\u3002")
        text = str(raw_value)
        if len(text) > 256:
            raise ValueError("\u975c\u614b query value \u9577\u5ea6\u4e0a\u9650\u70ba 256\u3002")
        result[key] = text
    return result


def normalize_custom_source_fields(
    *,
    name,
    origins,
    path_prefixes,
    keep_query_keys=None,
    platform_id: str | None = None,
) -> dict:
    normalized_name = _normalize_display_name(name, "\u5e73\u53f0\u540d\u7a31")
    normalized_origins = _unique_strings(
        (_normalize_public_hostname(origin, "\u4f86\u6e90\u7db2\u57df") for origin in _split_form_values(origins))
    )
    if not 1 <= len(normalized_origins) <= 5:
        raise ValueError("\u8acb\u8f38\u5165 1 \u5230 5 \u500b\u4f86\u6e90\u7db2\u57df\u3002")
    normalized_keep_keys = _unique_strings(
        (_normalize_query_key(key, "\u5fc5\u8981 query key") for key in _split_form_values(keep_query_keys)),
        limit=10,
    )
    if platform_id:
        normalized_id = str(platform_id).strip()
    else:
        normalized_id = uuid.uuid4().hex[:16]
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", normalized_id):
        raise ValueError("\u81ea\u8a02\u5e73\u53f0 ID \u7121\u6548\u3002")
    return {
        "id": normalized_id,
        "name": normalized_name,
        "origins": normalized_origins,
        "path_prefixes": _normalize_path_prefixes(path_prefixes),
        "keep_query_keys": normalized_keep_keys,
    }


def normalize_custom_fixer_fields(*, name, endpoint, source_param, static_query=None) -> dict:
    normalized_source_param = _normalize_query_key(source_param, "\u4f86\u6e90 URL \u53c3\u6578\u540d")
    normalized_static_query = _normalize_static_query(static_query)
    if normalized_source_param in normalized_static_query:
        raise ValueError("\u4f86\u6e90 URL \u53c3\u6578\u540d\u4e0d\u80fd\u8207\u975c\u614b query key \u91cd\u8907\u3002")
    return {
        "name": _normalize_display_name(name, "Fixer \u540d\u7a31"),
        "endpoint": _normalize_endpoint(endpoint),
        "source_param": normalized_source_param,
        "static_query": normalized_static_query,
    }


def builtin_platform_for_hostname(hostname: str) -> str | None:
    normalized = str(hostname or "").casefold().rstrip(".")
    if normalized.startswith("www."):
        normalized = normalized[4:]
    for name, platform in supported_platforms.items():
        if normalized in platform.get("origins", []):
            return name
        if any(
            normalized == suffix or normalized.endswith(f".{suffix}")
            for suffix in platform.get("origin_suffixes", [])
        ):
            return name
    return None


def validate_custom_source_conflicts(source: dict, existing: list[dict], *, exclude_id: str | None = None):
    name_key = source["name"].casefold()
    for origin in source["origins"]:
        builtin_name = builtin_platform_for_hostname(origin)
        if builtin_name:
            raise ValueError(f"{builtin_name} \u70ba\u5167\u5efa\u5e73\u53f0\uff0c\u4e0d\u80fd\u88ab\u81ea\u8a02\u898f\u5247\u8986\u84cb\u3002")
    for item in existing:
        if item.get("id") == exclude_id:
            continue
        if str(item.get("name", "")).casefold() == name_key:
            raise ValueError("\u81ea\u8a02\u5e73\u53f0\u540d\u7a31\u4e0d\u80fd\u91cd\u8907\u3002")
        existing_origins = set(item.get("origins", []))
        existing_prefixes = set(item.get("path_prefixes", []))
        for origin in source["origins"]:
            if origin not in existing_origins:
                continue
            for prefix in source["path_prefixes"]:
                if prefix in existing_prefixes:
                    raise ValueError(f"`{origin}{prefix}` \u5df2\u6709\u76f8\u540c\u7684\u81ea\u8a02\u5339\u914d\u898f\u5247\u3002")


def normalize_custom_platform(raw: dict, existing: list[dict] | None = None, *, exclude_id: str | None = None) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("\u81ea\u8a02\u5e73\u53f0\u8cc7\u6599\u5fc5\u9808\u662f object\u3002")
    raw_id = raw.get("id")
    if not raw_id:
        seed = f"{raw.get('name', '')}|{raw.get('origins', '')}|{raw.get('path_prefixes', '')}"
        raw_id = uuid.uuid5(uuid.NAMESPACE_URL, f"fixlink:{seed}").hex[:16]
    source = normalize_custom_source_fields(
        name=raw.get("name"),
        origins=raw.get("origins"),
        path_prefixes=raw.get("path_prefixes"),
        keep_query_keys=raw.get("keep_query_keys"),
        platform_id=raw_id,
    )
    fixer = raw.get("fixer") or {}
    source["fixer"] = normalize_custom_fixer_fields(
        name=fixer.get("name"),
        endpoint=fixer.get("endpoint"),
        source_param=fixer.get("source_param"),
        static_query=fixer.get("static_query"),
    )
    validate_custom_source_conflicts(source, existing or [], exclude_id=exclude_id)
    return source


def normalize_fixlink_config(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    config = {
        "enabled": raw.get("enabled") if isinstance(raw.get("enabled"), bool) else False,
        "remove_tracker": raw.get("remove_tracker") if isinstance(raw.get("remove_tracker"), bool) else False,
        "webhook_mode": raw.get("webhook_mode") if isinstance(raw.get("webhook_mode"), bool) else False,
        "webhook_only_with_tracker": (
            raw.get("webhook_only_with_tracker")
            if isinstance(raw.get("webhook_only_with_tracker"), bool)
            else False
        ),
        "disabled_platforms": _unique_strings(raw.get("disabled_platforms", []), limit=50)
        if isinstance(raw.get("disabled_platforms", []), list)
        else [],
        "preferred_fixers": dict(DEFAULT_PREFERRED_FIXERS),
        "custom_platforms": [],
    }
    preferred_fixers = raw.get("preferred_fixers")
    if isinstance(preferred_fixers, dict):
        for name, platform in supported_platforms.items():
            selected = preferred_fixers.get(name)
            if selected in platform["fixers"]:
                config["preferred_fixers"][name] = selected
    for item in raw.get("custom_platforms", []) if isinstance(raw.get("custom_platforms"), list) else []:
        if len(config["custom_platforms"]) >= MAX_CUSTOM_PLATFORMS:
            break
        try:
            normalized = normalize_custom_platform(item, config["custom_platforms"])
        except (TypeError, ValueError):
            continue
        config["custom_platforms"].append(normalized)
    return config


def extract_urls(content: str) -> list[ExtractedURL]:
    extracted: list[ExtractedURL] = []
    for match in URL_PATTERN.finditer(content or ""):
        raw_url = match.group(0).rstrip(TRAILING_URL_PUNCTUATION)
        end = match.start() + len(raw_url)
        is_suppressed = (
            match.start() > 0
            and end < len(content)
            and content[match.start() - 1] == "<"
            and content[end] == ">"
        )
        if not is_suppressed:
            extracted.append(ExtractedURL(raw_url, match.start(), end))
    return extracted


def _normalized_host(url: str, *, strip_www: bool = False) -> str:
    try:
        hostname = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""
    if strip_www and hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def match_builtin_platform(url: str) -> tuple[str, dict, Any, str] | None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or parsed.username or parsed.password:
        return None
    if port not in (None, 80, 443):
        return None
    hostname = _normalized_host(url, strip_www=True)
    platform_name = builtin_platform_for_hostname(hostname)
    if platform_name is None:
        return None
    platform = supported_platforms[platform_name]
    if platform.get("special_handler"):
        return None
    decoded_path = unquote(parsed.path or "/")
    query_keys = {key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    for rule in platform.get("rules", []):
        rule_hosts = rule.get("hosts")
        if rule_hosts and hostname not in rule_hosts:
            continue
        if re.fullmatch(rule["path"], decoded_path, flags=re.IGNORECASE) is None:
            continue
        if not set(rule.get("query_keys", [])).issubset(query_keys):
            continue
        query_any = set(rule.get("query_any", []))
        if query_any and query_any.isdisjoint(query_keys):
            continue
        return platform_name, platform, parsed, hostname
    return None


def build_builtin_match_urls(
    url: str,
    platform: dict,
    parsed,
    hostname: str,
    *,
    remove_tracker: bool,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    if remove_tracker:
        allowed_query_keys = set(platform.get("keep_query_keys", []))
        query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if key in allowed_query_keys
            ]
        )
        fragment = ""
        source_url = urlunsplit(("https", hostname, parsed.path, query, fragment))
    else:
        query = parsed.query
        fragment = parsed.fragment
        source_url = url

    fixers: list[tuple[str, str]] = []
    for fixer_name, fixer in platform["fixers"].items():
        fixer_origins = fixer.get("origins")
        if fixer_origins and hostname not in fixer_origins:
            continue
        target_host = fixer.get("host_map", {}).get(hostname, fixer.get("host"))
        if not target_host:
            continue
        fixed_url = urlunsplit(("https", target_host, parsed.path, query, fragment))
        if len(fixed_url) <= MAX_GENERATED_URL_LENGTH:
            fixers.append((fixer_name, fixed_url))
    return source_url, tuple(fixers)


def builtin_url_has_tracker(platform: dict, parsed) -> bool:
    allowed_query_keys = set(platform.get("keep_query_keys", []))
    has_extra_query = any(
        key not in allowed_query_keys
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    )
    return bool(parsed.fragment or has_extra_query)


def get_builtin_profile(platform_name: str, path: str) -> tuple[str | None, str | None]:
    decoded_path = unquote(path or "/")
    patterns = {
        "Twitter": (r"^/([^/]+)/status/", "https://x.com/{username}"),
        "Instagram": (r"^/([^/]+)/(?:p|reels?)/", "https://www.instagram.com/{username}"),
        "TikTok": (r"^/@([^/]+)/(?:video|photo)/", "https://www.tiktok.com/@{username}"),
        "Bluesky": (r"^/profile/([^/]+)/post/", "https://bsky.app/profile/{username}"),
        "Newgrounds": (r"^/art/view/([^/]+)/", "https://{username}.newgrounds.com/"),
    }
    profile = patterns.get(platform_name)
    if profile is None:
        return None, None
    match = re.match(profile[0], decoded_path, flags=re.IGNORECASE)
    if match is None:
        return None, None
    username = match.group(1)
    return username, profile[1].format(username=username)


def parse_threads_url(url: str) -> dict | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or _normalized_host(url, strip_www=True) not in THREADS_HOSTS:
        return None
    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    if (
        len(segments) == 3
        and segments[0].startswith("@")
        and segments[1].casefold() == "post"
        and THREADS_USERNAME_PATTERN.fullmatch(segments[0][1:])
        and THREADS_ID_PATTERN.fullmatch(segments[2])
    ):
        username = segments[0][1:]
        post_id = segments[2]
        return {
            "kind": "post",
            "username": username,
            "post_id": post_id,
            "path": f"/@{username}/post/{post_id}",
        }
    if len(segments) == 2 and segments[0].casefold() == "share" and THREADS_ID_PATTERN.fullmatch(segments[1]):
        return {
            "kind": "share",
            "share_code": segments[1],
            "path": f"/share/{segments[1]}/",
        }
    return None


def canonical_threads_url(url: str, *, remove_tracker: bool) -> str | None:
    parts = parse_threads_url(url)
    if not parts or parts["kind"] != "post":
        return None
    parsed = urlsplit(url)
    return urlunsplit(
        (
            "https",
            "www.threads.com",
            parts["path"],
            "" if remove_tracker else parsed.query,
            "" if remove_tracker else parsed.fragment,
        )
    )


def build_fzthreads_url(url: str, *, remove_tracker: bool) -> str | None:
    parts = parse_threads_url(url)
    if not parts:
        return None
    parsed = urlsplit(url)
    return urlunsplit(
        (
            "https",
            "fzthreads.com",
            parts["path"],
            "" if remove_tracker else parsed.query,
            "" if remove_tracker else parsed.fragment,
        )
    )


def build_fixembed_url(url: str) -> str:
    return f"https://fixembed.app/embed?{urlencode({'url': url, 'v': FIXEMBED_REVISION})}"


def build_custom_source_url(url: str, *, remove_tracker: bool, keep_query_keys: list[str]) -> str:
    parsed = urlsplit(url)
    query = parsed.query
    if remove_tracker and keep_query_keys:
        allowed = set(keep_query_keys)
        query = urlencode([(key, value) for key, value in parse_qsl(query, keep_blank_values=True) if key in allowed])
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def custom_url_has_tracker(url: str, keep_query_keys: list[str]) -> bool:
    if not keep_query_keys:
        return False
    allowed_query_keys = set(keep_query_keys)
    parsed = urlsplit(url)
    return any(
        key not in allowed_query_keys
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    )


def build_custom_fixer_url(platform: dict, source_url: str) -> str:
    fixer = platform["fixer"]
    endpoint = urlsplit(fixer["endpoint"])
    query_items = list(fixer["static_query"].items())
    query_items.append((fixer["source_param"], source_url))
    return urlunsplit((endpoint.scheme, endpoint.netloc, endpoint.path, urlencode(query_items), ""))


def find_custom_platform(url: str, platforms: list[dict]) -> dict | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"}:
        return None
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    decoded_path = unquote(parsed.path or "/")
    candidates: list[tuple[int, dict]] = []
    for platform in platforms:
        if hostname not in platform.get("origins", []):
            continue
        for prefix in platform.get("path_prefixes", []):
            if decoded_path.startswith(prefix):
                candidates.append((len(prefix), platform))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def chunk_lines(lines: list[str], *, max_length: int = MAX_REPLY_CHUNK_LENGTH) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in lines:
        if len(line) > max_length:
            continue
        added = len(line) + (1 if current else 0)
        if current and current_length + added > max_length:
            chunks.append("\n".join(current))
            current = [line]
            current_length = len(line)
        else:
            current.append(line)
            current_length += added
    if current:
        chunks.append("\n".join(current))
    return chunks


def _escape_label(value: str) -> str:
    return discord.utils.escape_markdown(str(value), as_needed=True)


def format_match_line(match: LinkMatch) -> str:
    parts = [f"[{_escape_label(match.platform_name)}](<{match.source_url}>)"]
    if match.username and match.profile_url:
        parts.append(f"[@{_escape_label(match.username)}](<{match.profile_url}>)")
    parts.extend(f"[{_escape_label(name)}]({url})" for name, url in match.fixers)
    return " \u2022 ".join(parts)


async def get_trash_button_emoji():
    try:
        emoji = await get_emoji_by_name("trash")
    except discord.HTTPException:
        emoji = None
    return emoji or TRASH_EMOJI_FALLBACK


class FixLinkDeleteButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"fixlink_delete:(?P<author_id>[0-9]{1,20})",
):
    def __init__(self, author_id: int, emoji=TRASH_EMOJI_FALLBACK):
        self.author_id = int(author_id)
        super().__init__(
            discord.ui.Button(
                # label="刪除",
                style=discord.ButtonStyle.danger,
                emoji=emoji,
                custom_id=f"fixlink_delete:{self.author_id}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item,
        match: re.Match,
    ):
        emoji = item.emoji if isinstance(item, discord.ui.Button) else TRASH_EMOJI_FALLBACK
        return cls(int(match.group("author_id")), emoji=emoji or TRASH_EMOJI_FALLBACK)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message("只有原訊息作者可以刪除這則訊息。", ephemeral=True)
        return False

    async def callback(self, interaction: discord.Interaction):
        if interaction.message is None:
            await interaction.response.send_message("找不到要刪除的訊息。", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            await interaction.followup.send("無法刪除這則訊息。", ephemeral=True)


class FixLinkDeleteView(discord.ui.View):
    def __init__(self, author_id: int, emoji=TRASH_EMOJI_FALLBACK):
        super().__init__(timeout=None)
        self.add_item(FixLinkDeleteButton(author_id, emoji=emoji))


class BuiltinPlatformSelect(discord.ui.Select):
    def __init__(self, settings_view: "FixLinkSettingsView"):
        self.settings_view = settings_view
        super().__init__(
            custom_id="fixlink_builtin_platform_select",
            placeholder="選擇內建平台",
            min_values=1,
            max_values=1,
            row=1,
            options=[],
        )

    async def callback(self, interaction: discord.Interaction):
        self.settings_view.selected_builtin_name = self.values[0]
        self.settings_view.refresh_components()
        await interaction.response.edit_message(
            embed=self.settings_view.build_embed(),
            view=self.settings_view,
        )


class PreferredFixerSelect(discord.ui.Select):
    def __init__(self, settings_view: "FixLinkSettingsView"):
        self.settings_view = settings_view
        super().__init__(
            custom_id="fixlink_preferred_fixer",
            placeholder="內建平台主要修復服務",
            min_values=1,
            max_values=1,
            row=2,
            options=[],
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        platform_name = self.settings_view.selected_builtin_name
        await self.settings_view.mutate_config(
            interaction,
            lambda config: config["preferred_fixers"].__setitem__(platform_name, selected),
        )


class CustomPlatformSelect(discord.ui.Select):
    def __init__(self, settings_view: "FixLinkSettingsView"):
        self.settings_view = settings_view
        super().__init__(
            custom_id="fixlink_custom_platform_select",
            placeholder="\u9078\u64c7\u81ea\u8a02\u5e73\u53f0",
            min_values=1,
            max_values=1,
            row=3,
            options=[discord.SelectOption(label="\u5c1a\u7121\u81ea\u8a02\u5e73\u53f0", value="__none__")],
            disabled=True,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if selected == "__none__":
            await interaction.response.defer()
            return
        self.settings_view.selected_custom_id = selected
        self.settings_view.refresh_components()
        await interaction.response.edit_message(
            embed=self.settings_view.build_embed(),
            view=self.settings_view,
        )


class FixLinkSettingsView(discord.ui.View):
    def __init__(self, cog: "FixLink", interaction: discord.Interaction):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = interaction.guild_id
        self.owner_id = interaction.user.id
        self.message: discord.InteractionMessage | None = None
        self.selected_builtin_name = "Threads"
        self.selected_custom_id: str | None = None
        self.config = cog.get_config(self.guild_id)
        self.builtin_select = BuiltinPlatformSelect(self)
        self.preferred_select = PreferredFixerSelect(self)
        self.custom_select = CustomPlatformSelect(self)
        self.add_item(self.builtin_select)
        self.add_item(self.preferred_select)
        self.add_item(self.custom_select)
        self.refresh_components()

    def selected_custom(self) -> dict | None:
        return next(
            (item for item in self.config["custom_platforms"] if item["id"] == self.selected_custom_id),
            None,
        )

    def refresh_components(self):
        self.toggle_enabled.label = "\u5df2\u555f\u7528" if self.config["enabled"] else "\u5df2\u505c\u7528"
        self.toggle_enabled.style = discord.ButtonStyle.success if self.config["enabled"] else discord.ButtonStyle.secondary
        self.toggle_tracker.label = "\u79fb\u9664\u8ffd\u8e64\uff1a\u958b" if self.config["remove_tracker"] else "\u79fb\u9664\u8ffd\u8e64\uff1a\u95dc"
        self.toggle_tracker.style = discord.ButtonStyle.primary if self.config["remove_tracker"] else discord.ButtonStyle.secondary
        self.toggle_webhook.label = "Webhook\uff1a\u958b" if self.config["webhook_mode"] else "Webhook\uff1a\u95dc"
        self.toggle_webhook.style = discord.ButtonStyle.primary if self.config["webhook_mode"] else discord.ButtonStyle.secondary
        tracker_only = self.config["webhook_only_with_tracker"]
        self.toggle_webhook_tracker.label = "Webhook：僅追蹤碼" if tracker_only else "Webhook：全部連結"
        self.toggle_webhook_tracker.style = (
            discord.ButtonStyle.primary if tracker_only else discord.ButtonStyle.secondary
        )
        if self.selected_builtin_name not in supported_platforms:
            self.selected_builtin_name = "Threads"
        self.builtin_select.options = [
            discord.SelectOption(
                label=name,
                value=name,
                default=name == self.selected_builtin_name,
            )
            for name in supported_platforms
        ]
        platform = supported_platforms[self.selected_builtin_name]
        platform_disabled = self.selected_builtin_name in self.config["disabled_platforms"]
        self.toggle_builtin.label = (
            f"啟用 {self.selected_builtin_name}" if platform_disabled else f"停用 {self.selected_builtin_name}"
        )[:80]
        self.toggle_builtin.style = (
            discord.ButtonStyle.secondary if platform_disabled else discord.ButtonStyle.success
        )

        preferred = self.config["preferred_fixers"][self.selected_builtin_name]
        self.preferred_select.placeholder = f"{self.selected_builtin_name} 主要修復服務"[:150]
        self.preferred_select.options = [
            discord.SelectOption(label=name, value=name, default=name == preferred)
            for name in platform["fixers"]
        ]

        custom_platforms = self.config["custom_platforms"]
        valid_ids = {item["id"] for item in custom_platforms}
        if self.selected_custom_id not in valid_ids:
            self.selected_custom_id = custom_platforms[0]["id"] if custom_platforms else None
        if custom_platforms:
            self.custom_select.disabled = False
            self.custom_select.options = [
                discord.SelectOption(
                    label=item["name"][:100],
                    value=item["id"],
                    description=", ".join(item["origins"])[:100],
                    default=item["id"] == self.selected_custom_id,
                )
                for item in custom_platforms
            ]
        else:
            self.custom_select.disabled = True
            self.custom_select.options = [discord.SelectOption(label="\u5c1a\u7121\u81ea\u8a02\u5e73\u53f0", value="__none__")]
        has_selected = self.selected_custom() is not None
        self.edit_custom.disabled = not has_selected
        self.toggle_custom.disabled = not has_selected
        self.delete_custom.disabled = not has_selected
        self.add_custom.disabled = len(custom_platforms) >= MAX_CUSTOM_PLATFORMS
        if has_selected:
            key = f"custom:{self.selected_custom_id}"
            disabled = key in self.config["disabled_platforms"]
            self.toggle_custom.label = "\u555f\u7528\u81ea\u8a02" if disabled else "\u505c\u7528\u81ea\u8a02"

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="FixLink \u8a2d\u5b9a", color=discord.Color.blurple())
        mode = "Webhook \u66ff\u63db" if self.config["webhook_mode"] else "\u4e00\u822c\u56de\u8986"
        webhook_condition = (
            "僅含追蹤碼的連結"
            if self.config["webhook_only_with_tracker"]
            else "全部支援連結"
        )
        enabled_text = "\u555f\u7528" if self.config["enabled"] else "\u505c\u7528"
        tracker_text = "\u555f\u7528" if self.config["remove_tracker"] else "\u505c\u7528"
        embed.description = (
            f"\u529f\u80fd\uff1a**{enabled_text}**\n"
            f"\u6a21\u5f0f\uff1a**{mode}**\n"
            f"Webhook 條件：**{webhook_condition}**\n"
            f"\u79fb\u9664\u8ffd\u8e64\uff1a**{tracker_text}**"
        )
        platform = supported_platforms[self.selected_builtin_name]
        platform_enabled = self.selected_builtin_name not in self.config["disabled_platforms"]
        platform_status = "\u555f\u7528" if platform_enabled else "\u505c\u7528"
        fixer_names = "、".join(platform["fixers"])
        embed.add_field(
            name=f"內建：{self.selected_builtin_name}",
            value=(
                f"\u72c0\u614b\uff1a{platform_status}\n"
                f"\u4e3b\u8981\u670d\u52d9\uff1a{self.config['preferred_fixers'][self.selected_builtin_name]}\n"
                f"可用：{fixer_names}"
            ),
            inline=False,
        )
        custom = self.selected_custom()
        if custom:
            disabled = f"custom:{custom['id']}" in self.config["disabled_platforms"]
            custom_status = "\u505c\u7528" if disabled else "\u555f\u7528"
            fixer = custom["fixer"]
            embed.add_field(
                name=f"\u81ea\u8a02\uff1a{custom['name']}",
                value=(
                    f"\u72c0\u614b\uff1a{custom_status}\n"
                    f"\u4f86\u6e90\uff1a{', '.join(custom['origins'])}\n"
                    f"\u8def\u5f91\uff1a{', '.join(custom['path_prefixes'])}\n"
                    f"Fixer\uff1a{fixer['name']} (`{urlsplit(fixer['endpoint']).hostname}`)"
                )[:1024],
                inline=False,
            )
        else:
            embed.add_field(name="\u81ea\u8a02\u5e73\u53f0", value="\u5c1a\u7121\u81ea\u8a02\u5e73\u53f0\u3002", inline=False)
        enabled_builtin_count = sum(
            name not in self.config["disabled_platforms"] for name in supported_platforms
        )
        embed.set_footer(
            text=(
                f"內建平台：{enabled_builtin_count}/{len(supported_platforms)} • "
                f"\u81ea\u8a02\u5e73\u53f0\uff1a{len(self.config['custom_platforms'])}/{MAX_CUSTOM_PLATFORMS}"
            )
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        permissions = getattr(interaction.user, "guild_permissions", None)
        allowed = (
            interaction.guild_id == self.guild_id
            and interaction.user.id == self.owner_id
            and permissions is not None
            and permissions.manage_guild
            and permissions.manage_webhooks
        )
        if not allowed:
            await interaction.response.send_message("\u53ea\u6709\u958b\u555f\u6b64\u9762\u677f\u7684\u7ba1\u7406\u54e1\u53ef\u4ee5\u4fee\u6539 FixLink \u8a2d\u5b9a\u3002", ephemeral=True)
        return allowed

    async def mutate_config(self, interaction: discord.Interaction, mutator: Callable[[dict], None]):
        config = self.cog.get_config(self.guild_id)
        mutator(config)
        if not self.cog.save_config(self.guild_id, config):
            await interaction.response.send_message("\u5132\u5b58 FixLink \u8a2d\u5b9a\u5931\u6557\uff0c\u8acb\u7a0d\u5f8c\u518d\u8a66\u3002", ephemeral=True)
            return
        self.config = config
        self.refresh_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def refresh_message(self):
        self.config = self.cog.get_config(self.guild_id)
        self.refresh_components()
        if self.message:
            try:
                await self.message.edit(embed=self.build_embed(), view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="\u5df2\u505c\u7528", style=discord.ButtonStyle.secondary, row=0, custom_id="fixlink_toggle_enabled")
    async def toggle_enabled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.mutate_config(interaction, lambda config: config.__setitem__("enabled", not config["enabled"]))

    @discord.ui.button(label="\u79fb\u9664\u8ffd\u8e64\uff1a\u95dc", style=discord.ButtonStyle.secondary, row=0, custom_id="fixlink_toggle_tracker")
    async def toggle_tracker(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.mutate_config(
            interaction,
            lambda config: config.__setitem__("remove_tracker", not config["remove_tracker"]),
        )

    @discord.ui.button(label="Webhook\uff1a\u95dc", style=discord.ButtonStyle.secondary, row=0, custom_id="fixlink_toggle_webhook")
    async def toggle_webhook(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.mutate_config(
            interaction,
            lambda config: config.__setitem__("webhook_mode", not config["webhook_mode"]),
        )

    @discord.ui.button(
        label="Webhook：全部連結",
        style=discord.ButtonStyle.secondary,
        row=0,
        custom_id="fixlink_toggle_webhook_tracker",
    )
    async def toggle_webhook_tracker(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.mutate_config(
            interaction,
            lambda config: config.__setitem__(
                "webhook_only_with_tracker",
                not config["webhook_only_with_tracker"],
            ),
        )

    @discord.ui.button(
        label="停用平台",
        style=discord.ButtonStyle.success,
        row=4,
        custom_id="fixlink_toggle_builtin",
    )
    async def toggle_builtin(self, interaction: discord.Interaction, button: discord.ui.Button):
        platform_name = self.selected_builtin_name

        def mutate(config):
            disabled = config["disabled_platforms"]
            if platform_name in disabled:
                disabled.remove(platform_name)
            else:
                disabled.append(platform_name)

        await self.mutate_config(interaction, mutate)

    @discord.ui.button(label="\u65b0\u589e\u81ea\u8a02", style=discord.ButtonStyle.primary, row=4, custom_id="fixlink_add_custom")
    async def add_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CustomPlatformSourceModal(self))

    @discord.ui.button(label="\u7de8\u8f2f\u81ea\u8a02", style=discord.ButtonStyle.secondary, row=4, custom_id="fixlink_edit_custom")
    async def edit_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        custom = self.selected_custom()
        if custom is None:
            await interaction.response.send_message("\u8acb\u5148\u9078\u64c7\u81ea\u8a02\u5e73\u53f0\u3002", ephemeral=True)
            return
        await interaction.response.send_modal(CustomPlatformSourceModal(self, existing=custom))

    @discord.ui.button(label="\u505c\u7528\u81ea\u8a02", style=discord.ButtonStyle.secondary, row=4, custom_id="fixlink_toggle_custom")
    async def toggle_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        custom = self.selected_custom()
        if custom is None:
            await interaction.response.send_message("\u8acb\u5148\u9078\u64c7\u81ea\u8a02\u5e73\u53f0\u3002", ephemeral=True)
            return
        key = f"custom:{custom['id']}"

        def mutate(config):
            disabled = config["disabled_platforms"]
            if key in disabled:
                disabled.remove(key)
            else:
                disabled.append(key)

        await self.mutate_config(interaction, mutate)

    @discord.ui.button(label="\u522a\u9664\u81ea\u8a02", style=discord.ButtonStyle.danger, row=4, custom_id="fixlink_delete_custom")
    async def delete_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        custom = self.selected_custom()
        if custom is None:
            await interaction.response.send_message("\u8acb\u5148\u9078\u64c7\u81ea\u8a02\u5e73\u53f0\u3002", ephemeral=True)
            return
        await interaction.response.send_message(
            f"\u78ba\u5b9a\u8981\u522a\u9664 **{discord.utils.escape_markdown(custom['name'])}** \u55ce\uff1f",
            view=CustomDeleteConfirmView(self, custom["id"]),
            ephemeral=True,
        )

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class CustomPlatformSourceModal(discord.ui.Modal, title="\u81ea\u8a02\u5e73\u53f0\u4f86\u6e90"):
    def __init__(self, settings_view: FixLinkSettingsView, existing: dict | None = None):
        super().__init__(timeout=300)
        self.settings_view = settings_view
        self.existing = existing
        self.name_input = discord.ui.TextInput(
            label="\u5e73\u53f0\u540d\u7a31",
            default=existing["name"] if existing else None,
            max_length=40,
        )
        self.origins_input = discord.ui.TextInput(
            label="\u4f86\u6e90\u7db2\u57df\uff081-5 \u500b\uff0c\u9017\u865f\u6216\u5206\u884c\uff09",
            default="\n".join(existing["origins"]) if existing else None,
            placeholder="example.com",
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.paths_input = discord.ui.TextInput(
            label="\u8def\u5f91\u524d\u7db4\uff081-5 \u500b\uff09",
            default="\n".join(existing["path_prefixes"]) if existing else None,
            placeholder="/post/",
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.keep_query_input = discord.ui.TextInput(
            label="\u53bb\u8ffd\u8e64\u6642\u4fdd\u7559\u7684 query keys\uff08\u53ef\u7559\u7a7a\uff09",
            default="\n".join(existing.get("keep_query_keys", [])) if existing else None,
            placeholder="id\nlang",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        for item in (self.name_input, self.origins_input, self.paths_input, self.keep_query_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.settings_view.interaction_check(interaction):
            return
        config = self.settings_view.cog.get_config(self.settings_view.guild_id)
        existing_id = self.existing["id"] if self.existing else None
        try:
            source = normalize_custom_source_fields(
                name=self.name_input.value,
                origins=self.origins_input.value,
                path_prefixes=self.paths_input.value,
                keep_query_keys=self.keep_query_input.value,
                platform_id=existing_id,
            )
            validate_custom_source_conflicts(
                source,
                config["custom_platforms"],
                exclude_id=existing_id,
            )
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await interaction.response.send_message(
            f"\u4f86\u6e90\u5df2\u9a57\u8b49\uff1a**{discord.utils.escape_markdown(source['name'])}**\n\u7e7c\u7e8c\u8a2d\u5b9a query fixer\u3002",
            view=CustomFixerDraftView(self.settings_view, source, self.existing),
            ephemeral=True,
        )


class CustomFixerDraftView(discord.ui.View):
    def __init__(self, settings_view: FixLinkSettingsView, source: dict, existing: dict | None):
        super().__init__(timeout=300)
        self.settings_view = settings_view
        self.source = source
        self.existing = existing

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.settings_view.interaction_check(interaction)

    @discord.ui.button(label="\u8a2d\u5b9a Query fixer", style=discord.ButtonStyle.primary)
    async def configure(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CustomFixerModal(self.settings_view, self.source, self.existing))


class CustomFixerModal(discord.ui.Modal, title="\u81ea\u8a02 Query fixer"):
    def __init__(self, settings_view: FixLinkSettingsView, source: dict, existing: dict | None):
        super().__init__(timeout=300)
        self.settings_view = settings_view
        self.source = source
        self.existing = existing
        existing_fixer = existing.get("fixer", {}) if existing else {}
        self.name_input = discord.ui.TextInput(
            label="Fixer \u540d\u7a31",
            default=existing_fixer.get("name"),
            max_length=40,
        )
        self.endpoint_input = discord.ui.TextInput(
            label="HTTPS endpoint\uff08\u4e0d\u542b query\uff09",
            default=existing_fixer.get("endpoint"),
            placeholder="https://fix.example.com/embed",
            max_length=500,
        )
        self.source_param_input = discord.ui.TextInput(
            label="\u4f86\u6e90 URL \u7684 query \u53c3\u6578\u540d",
            default=existing_fixer.get("source_param", "url"),
            placeholder="url",
            max_length=64,
        )
        static_default = "\n".join(
            f"{key}={value}" for key, value in existing_fixer.get("static_query", {}).items()
        )
        self.static_query_input = discord.ui.TextInput(
            label="\u975c\u614b query\uff08key=value\uff0c\u53ef\u7559\u7a7a\uff09",
            default=static_default or None,
            placeholder="v=1\nmode=embed",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1500,
        )
        for item in (self.name_input, self.endpoint_input, self.source_param_input, self.static_query_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.settings_view.interaction_check(interaction):
            return
        config = self.settings_view.cog.get_config(self.settings_view.guild_id)
        existing_id = self.existing["id"] if self.existing else None
        try:
            fixer = normalize_custom_fixer_fields(
                name=self.name_input.value,
                endpoint=self.endpoint_input.value,
                source_param=self.source_param_input.value,
                static_query=self.static_query_input.value,
            )
            candidate = normalize_custom_platform(
                {**self.source, "fixer": fixer},
                config["custom_platforms"],
                exclude_id=existing_id,
            )
            remaining = [item for item in config["custom_platforms"] if item["id"] != candidate["id"]]
            if existing_id is None and len(remaining) >= MAX_CUSTOM_PLATFORMS:
                raise ValueError(f"\u6bcf\u500b\u4f3a\u670d\u5668\u6700\u591a {MAX_CUSTOM_PLATFORMS} \u500b\u81ea\u8a02\u5e73\u53f0\u3002")
            config["custom_platforms"] = remaining + [candidate]
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        if not self.settings_view.cog.save_config(self.settings_view.guild_id, config):
            await interaction.response.send_message("\u5132\u5b58\u81ea\u8a02\u5e73\u53f0\u5931\u6557\uff0c\u8acb\u7a0d\u5f8c\u518d\u8a66\u3002", ephemeral=True)
            return
        self.settings_view.selected_custom_id = candidate["id"]
        await interaction.response.send_message(
            f"\u5df2\u5132\u5b58\u81ea\u8a02\u5e73\u53f0 **{discord.utils.escape_markdown(candidate['name'])}**\u3002",
            ephemeral=True,
        )
        await self.settings_view.refresh_message()


class CustomDeleteConfirmView(discord.ui.View):
    def __init__(self, settings_view: FixLinkSettingsView, platform_id: str):
        super().__init__(timeout=60)
        self.settings_view = settings_view
        self.platform_id = platform_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.settings_view.interaction_check(interaction)

    @discord.ui.button(label="\u78ba\u5b9a\u522a\u9664", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.settings_view.cog.get_config(self.settings_view.guild_id)
        original_count = len(config["custom_platforms"])
        config["custom_platforms"] = [item for item in config["custom_platforms"] if item["id"] != self.platform_id]
        key = f"custom:{self.platform_id}"
        config["disabled_platforms"] = [item for item in config["disabled_platforms"] if item != key]
        if len(config["custom_platforms"]) == original_count:
            await interaction.response.edit_message(content="\u627e\u4e0d\u5230\u9019\u500b\u81ea\u8a02\u5e73\u53f0\u3002", view=None)
            return
        if not self.settings_view.cog.save_config(self.settings_view.guild_id, config):
            await interaction.response.edit_message(content="\u522a\u9664\u81ea\u8a02\u5e73\u53f0\u5931\u6557\u3002", view=None)
            return
        self.settings_view.selected_custom_id = None
        await interaction.response.edit_message(content="\u5df2\u522a\u9664\u81ea\u8a02\u5e73\u53f0\u3002", view=None)
        await self.settings_view.refresh_message()

    @discord.ui.button(label="\u53d6\u6d88", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="\u5df2\u53d6\u6d88\u3002", view=None)


@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.default_permissions(manage_guild=True, manage_webhooks=True)
class FixLink(commands.GroupCog, name="fixlink", description="\u9023\u7d50\u4fee\u5fa9\u5668"):
    def __init__(self, client: commands.Bot):
        super().__init__()
        self.bot = client
        self.default_config = DEFAULT_FIXLINK_CONFIG
        self._share_cache: dict[str, tuple[float, str | None]] = {}
        self._share_inflight: dict[str, asyncio.Task] = {}
        self._webhook_locks: dict[int, asyncio.Lock] = {}
        self._invalid_config_counts: dict[int, int] = {}

    def get_config(self, guild_id: int) -> dict:
        raw = get_server_config(guild_id, FIXLINK_CONFIG_KEY, DEFAULT_FIXLINK_CONFIG)
        config = normalize_fixlink_config(raw)
        raw_custom_count = len(raw.get("custom_platforms", [])) if isinstance(raw, dict) and isinstance(raw.get("custom_platforms"), list) else 0
        invalid_count = max(0, raw_custom_count - len(config["custom_platforms"]))
        if invalid_count and self._invalid_config_counts.get(guild_id) != invalid_count:
            log(
                f"Ignored {invalid_count} invalid custom platform configuration(s) for guild {guild_id}.",
                level=logging.WARNING,
                module_name="FixLink",
            )
        self._invalid_config_counts[guild_id] = invalid_count
        return config

    def save_config(self, guild_id: int, config: dict) -> bool:
        return bool(set_server_config(guild_id, FIXLINK_CONFIG_KEY, normalize_fixlink_config(config)))

    @app_commands.command(name="settings", description="\u958b\u555f FixLink \u4e92\u52d5\u5f0f\u8a2d\u5b9a\u9762\u677f")
    async def settings(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("\u6b64\u6307\u4ee4\u53ea\u80fd\u5728\u4f3a\u670d\u5668\u4e2d\u4f7f\u7528\u3002", ephemeral=True)
            return
        view = FixLinkSettingsView(self, interaction)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)
        view.message = await interaction.original_response()

    async def _fetch_threads_redirect(self, url: str) -> str | None:
        timeout = aiohttp.ClientTimeout(total=5)
        headers = {"User-Agent": "Mozilla/5.0 (compatible; FixLink/1.0)"}

        async def request_redirect():
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.head(url, allow_redirects=True, max_redirects=5) as response:
                    final_url = str(response.url)
                    if response.status not in {405, 501} and parse_threads_url(final_url):
                        return final_url
                async with session.get(
                    url,
                    allow_redirects=True,
                    max_redirects=5,
                    headers={"Range": "bytes=0-0"},
                ) as response:
                    return str(response.url)

        try:
            return await asyncio.wait_for(request_redirect(), timeout=5)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None

    async def resolve_threads_share(self, url: str) -> str | None:
        parts = parse_threads_url(url)
        if not parts or parts["kind"] != "share":
            return None
        cache_key = parts["path"]
        cached = self._share_cache.get(cache_key)
        now = time.monotonic()
        if cached and cached[0] > now:
            return cached[1]
        inflight = self._share_inflight.get(cache_key)
        if inflight is None:
            async def resolve_uncached():
                resolved_url = await self._fetch_threads_redirect(
                    urlunsplit(("https", "www.threads.com", parts["path"], "", ""))
                )
                resolved_parts = parse_threads_url(resolved_url) if resolved_url else None
                if not resolved_parts or resolved_parts["kind"] != "post":
                    resolved_url = None
                cache_seconds = SHARE_CACHE_SECONDS if resolved_url else 60
                self._share_cache[cache_key] = (time.monotonic() + cache_seconds, resolved_url)
                return resolved_url

            inflight = asyncio.create_task(resolve_uncached())
            self._share_inflight[cache_key] = inflight
        try:
            return await inflight
        finally:
            if self._share_inflight.get(cache_key) is inflight and inflight.done():
                self._share_inflight.pop(cache_key, None)

    async def _match_url(self, extracted: ExtractedURL, config: dict) -> LinkMatch | None:
        threads = parse_threads_url(extracted.url)
        if threads:
            if "Threads" in config["disabled_platforms"]:
                return None
            remove_tracker = config["remove_tracker"]
            resolved_url = extracted.url
            if threads["kind"] == "share":
                resolved_url = await self.resolve_threads_share(extracted.url)
            direct_parts = parse_threads_url(resolved_url) if resolved_url else None
            source_url = extracted.url
            if remove_tracker:
                if direct_parts and direct_parts["kind"] == "post":
                    source_url = canonical_threads_url(resolved_url, remove_tracker=True) or extracted.url
                else:
                    parsed_source = urlsplit(extracted.url)
                    source_url = urlunsplit((parsed_source.scheme, parsed_source.netloc, parsed_source.path, "", ""))
            fzthreads_url = build_fzthreads_url(extracted.url, remove_tracker=remove_tracker)
            fixers: list[tuple[str, str]] = []
            if fzthreads_url and len(fzthreads_url) <= MAX_GENERATED_URL_LENGTH:
                fixers.append(("FzThreads", fzthreads_url))
            if direct_parts and direct_parts["kind"] == "post":
                fixembed_source = canonical_threads_url(resolved_url, remove_tracker=remove_tracker)
                if fixembed_source:
                    fixembed_url = build_fixembed_url(fixembed_source)
                    if len(fixembed_url) <= MAX_GENERATED_URL_LENGTH:
                        fixers.append(("FixEmbed", fixembed_url))
            if not fixers:
                return None
            preferred = config["preferred_fixers"].get("Threads", "FzThreads")
            primary_url = next((url for name, url in fixers if name == preferred), fixers[0][1])
            username = direct_parts.get("username") if direct_parts and direct_parts["kind"] == "post" else None
            return LinkMatch(
                platform_key="Threads",
                platform_name="Threads",
                source_url=source_url,
                start=extracted.start,
                end=extracted.end,
                fixers=tuple(fixers),
                primary_url=primary_url,
                has_tracker=bool(urlsplit(extracted.url).query or urlsplit(extracted.url).fragment),
                username=username,
                profile_url=f"https://www.threads.com/@{username}" if username else None,
            )

        builtin = match_builtin_platform(extracted.url)
        if builtin:
            platform_name, platform, parsed, hostname = builtin
            if platform_name in config["disabled_platforms"]:
                return None
            source_url, fixers = build_builtin_match_urls(
                extracted.url,
                platform,
                parsed,
                hostname,
                remove_tracker=config["remove_tracker"],
            )
            if not fixers:
                return None
            preferred = config["preferred_fixers"].get(
                platform_name,
                platform["default_fixer"],
            )
            primary_url = next((url for name, url in fixers if name == preferred), fixers[0][1])
            username, profile_url = get_builtin_profile(platform_name, parsed.path)
            return LinkMatch(
                platform_key=platform_name,
                platform_name=platform_name,
                source_url=source_url,
                start=extracted.start,
                end=extracted.end,
                fixers=fixers,
                primary_url=primary_url,
                has_tracker=builtin_url_has_tracker(platform, parsed),
                username=username,
                profile_url=profile_url,
            )

        custom = find_custom_platform(extracted.url, config["custom_platforms"])
        if custom is None or f"custom:{custom['id']}" in config["disabled_platforms"]:
            return None
        source_url = build_custom_source_url(
            extracted.url,
            remove_tracker=config["remove_tracker"],
            keep_query_keys=custom["keep_query_keys"],
        )
        fixed_url = build_custom_fixer_url(custom, source_url)
        if len(fixed_url) > MAX_GENERATED_URL_LENGTH:
            return None
        return LinkMatch(
            platform_key=f"custom:{custom['id']}",
            platform_name=custom["name"],
            source_url=source_url,
            start=extracted.start,
            end=extracted.end,
            fixers=((custom["fixer"]["name"], fixed_url),),
            primary_url=fixed_url,
            has_tracker=custom_url_has_tracker(extracted.url, custom["keep_query_keys"]),
        )

    async def match_message(self, content: str, config: dict) -> list[LinkMatch]:
        extracted = extract_urls(content)
        if not extracted:
            return []
        matches = await asyncio.gather(*(self._match_url(item, config) for item in extracted))
        return sorted((match for match in matches if match is not None), key=lambda item: item.start)

    def _can_send(self, message: discord.Message) -> bool:
        guild = message.guild
        if guild is None or guild.me is None or not hasattr(message.channel, "permissions_for"):
            return False
        permissions = message.channel.permissions_for(guild.me)
        if isinstance(message.channel, discord.Thread):
            return permissions.view_channel and permissions.send_messages_in_threads
        return permissions.view_channel and permissions.send_messages

    async def send_normal_reply(self, message: discord.Message, matches: list[LinkMatch]):
        if not self._can_send(message):
            return
        chunks = chunk_lines([format_match_line(match) for match in matches])
        sent_messages: list[discord.Message] = []
        for index, chunk in enumerate(chunks):
            try:
                if index == 0:
                    sent = await message.reply(
                        chunk,
                        mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                else:
                    sent = await message.channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())
                sent_messages.append(sent)
            except discord.HTTPException as error:
                log(
                    f"Normal reply failed for message {message.id}: {type(error).__name__}",
                    level=logging.ERROR,
                    module_name="FixLink",
                    guild=message.guild,
                )
                break
        if not sent_messages:
            return

        await asyncio.sleep(EMBED_PREVIEW_DELAY_SECONDS)
        has_preview = False
        for sent in sent_messages:
            try:
                refreshed = await sent.channel.fetch_message(sent.id)
            except discord.NotFound:
                continue
            except discord.HTTPException as error:
                log(
                    f"Normal preview check failed for message {sent.id}: {type(error).__name__}",
                    level=logging.WARNING,
                    module_name="FixLink",
                    guild=message.guild,
                )
                continue
            if refreshed.embeds:
                has_preview = True
                continue
            try:
                await sent.delete()
            except discord.HTTPException:
                pass

        if has_preview:
            try:
                await message.edit(suppress=True)
            except discord.HTTPException as error:
                log(
                    f"Original embed suppression failed for message {message.id}: {type(error).__name__}",
                    level=logging.WARNING,
                    module_name="FixLink",
                    guild=message.guild,
                )

    def _webhook_parent(self, channel):
        if isinstance(channel, discord.Thread):
            return channel.parent
        return channel

    def _can_webhook_replace(self, message: discord.Message) -> bool:
        if message.reference or message.stickers or getattr(message, "poll", None) is not None or message.flags.voice:
            return False
        guild = message.guild
        parent = self._webhook_parent(message.channel)
        if guild is None or guild.me is None or parent is None or not hasattr(parent, "permissions_for"):
            return False
        channel_permissions = message.channel.permissions_for(guild.me)
        parent_permissions = parent.permissions_for(guild.me)
        can_send = (
            channel_permissions.send_messages_in_threads
            if isinstance(message.channel, discord.Thread)
            else channel_permissions.send_messages
        )
        return bool(
            channel_permissions.view_channel
            and can_send
            and channel_permissions.manage_messages
            and (not message.attachments or channel_permissions.attach_files)
            and parent_permissions.manage_webhooks
        )

    def _webhook_mapping(self, guild_id: int) -> dict[str, str]:
        value = get_server_config(guild_id, FIXLINK_WEBHOOKS_KEY, {})
        if not isinstance(value, dict):
            return {}
        return {str(key): str(url) for key, url in value.items() if isinstance(url, str)}

    async def _get_or_create_webhook(self, guild: discord.Guild, parent) -> discord.Webhook:
        lock = self._webhook_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            mapping = self._webhook_mapping(guild.id)
            key = str(parent.id)
            webhook_url = mapping.get(key)
            if webhook_url:
                try:
                    return discord.Webhook.from_url(webhook_url, client=self.bot)
                except ValueError:
                    mapping.pop(key, None)
                    set_server_config(guild.id, FIXLINK_WEBHOOKS_KEY, mapping)
            webhook = await parent.create_webhook(name="FixLink", reason="FixLink webhook mode")
            mapping[key] = str(webhook.url)
            if not set_server_config(guild.id, FIXLINK_WEBHOOKS_KEY, mapping):
                try:
                    await webhook.delete(reason="Unable to persist FixLink webhook")
                except discord.HTTPException:
                    pass
                raise RuntimeError("Unable to persist FixLink webhook")
            return webhook

    async def _invalidate_webhook(self, guild_id: int, parent_id: int):
        lock = self._webhook_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            mapping = self._webhook_mapping(guild_id)
            if mapping.pop(str(parent_id), None) is not None:
                set_server_config(guild_id, FIXLINK_WEBHOOKS_KEY, mapping)

    async def _attachment_files(self, message: discord.Message) -> list[discord.File]:
        files: list[discord.File] = []
        try:
            for attachment in message.attachments:
                files.append(await attachment.to_file(use_cached=True))
        except Exception:
            for file in files:
                file.close()
            raise
        return files

    async def _send_webhook_clone(
        self,
        message: discord.Message,
        content: str,
    ) -> tuple[discord.Webhook, discord.WebhookMessage]:
        guild = message.guild
        parent = self._webhook_parent(message.channel)
        if guild is None or parent is None:
            raise RuntimeError("Missing guild or webhook parent")
        last_error: Exception | None = None
        for attempt in range(2):
            webhook = await self._get_or_create_webhook(guild, parent)
            files: list[discord.File] = []
            try:
                files = await self._attachment_files(message)
                send_kwargs = {
                    "content": content,
                    "username": message.author.display_name[:80],
                    "avatar_url": str(message.author.display_avatar.url),
                    "allowed_mentions": discord.AllowedMentions.none(),
                    "wait": True,
                }
                if files:
                    send_kwargs["files"] = files
                if isinstance(message.channel, discord.Thread):
                    send_kwargs["thread"] = message.channel
                sent = await webhook.send(**send_kwargs)
                if sent is None:
                    raise RuntimeError("Webhook did not return a message")
                return webhook, sent
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, RuntimeError) as error:
                last_error = error
                retryable = isinstance(error, discord.NotFound) or (
                    isinstance(error, discord.HTTPException) and getattr(error, "status", None) in {401, 404}
                )
                if attempt == 0 and retryable:
                    await self._invalidate_webhook(guild.id, parent.id)
                    continue
                raise
            finally:
                for file in files:
                    file.close()
        raise last_error or RuntimeError("Webhook send failed")

    async def replace_with_webhook(self, message: discord.Message, matches: list[LinkMatch]) -> bool:
        if not self._can_webhook_replace(message):
            return False
        content = message.content
        for match in reversed(matches):
            content = content[: match.start] + match.primary_url + content[match.end :]
        if not content or len(content) > 2000:
            return False
        try:
            webhook, clone = await self._send_webhook_clone(message, content)
        except Exception as error:
            log(
                f"Webhook resend failed for message {message.id}: {type(error).__name__}",
                level=logging.ERROR,
                module_name="FixLink",
                guild=message.guild,
            )
            return False

        await asyncio.sleep(EMBED_PREVIEW_DELAY_SECONDS)
        try:
            fetch_kwargs = {"thread": message.channel} if isinstance(message.channel, discord.Thread) else {}
            refreshed = await webhook.fetch_message(clone.id, **fetch_kwargs)
            delete_view = FixLinkDeleteView(message.author.id, emoji=await get_trash_button_emoji())
            if refreshed.embeds:
                clone = await clone.edit(view=delete_view)
            else:
                clone = await clone.edit(
                    content=message.content,
                    view=delete_view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except Exception as error:
            try:
                await clone.delete()
            except discord.HTTPException:
                pass
            log(
                f"Webhook preview finalization failed for message {message.id}: {type(error).__name__}",
                level=logging.ERROR,
                module_name="FixLink",
                guild=message.guild,
            )
            return False
        try:
            await message.delete()
            return True
        except discord.HTTPException as error:
            try:
                await clone.delete()
            except discord.HTTPException:
                log(
                    f"Webhook rollback failed for message {message.id} after {type(error).__name__}",
                    level=logging.ERROR,
                    module_name="FixLink",
                    guild=message.guild,
                )
            return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or message.webhook_id is not None or not message.content:
            return
        config = self.get_config(message.guild.id)
        if not config["enabled"]:
            return
        matches = await self.match_message(message.content, config)
        if not matches:
            return
        should_use_webhook = config["webhook_mode"] and (
            not config["webhook_only_with_tracker"]
            or any(match.has_tracker for match in matches)
        )
        if should_use_webhook and await self.replace_with_webhook(message, matches):
            return
        await self.send_normal_reply(message, matches)


bot.add_dynamic_items(FixLinkDeleteButton)
asyncio.run(bot.add_cog(FixLink(bot)))


if __name__ == "__main__":
    start_bot()
