from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence
import io
import os
import sys
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from itertools import accumulate
import discord
import aiohttp
from discord.ext import commands
from discord import app_commands
from globalenv import bot, start_bot, on_ready_tasks, modules, get_command_mention, config, get_server_config, set_server_config
from playwright.async_api import async_playwright
import asyncio
import chat_exporter
from logger import log
import logging
import traceback
import random
import time
import json
import math
import base64
import hashlib
import tempfile
import i18n
from i18n import t
import ai_emoji
import ai_table
if "OwnerTools" in modules:
    import OwnerTools


if getattr(sys, 'frozen', False):
    fontdir = os.path.join(sys._MEIPASS, 'assets')
else:
    fontdir = os.path.join(os.path.dirname(__file__), 'assets')

import re
import emoji

WHATTISTHISGUYTALKING_STATIC_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
WHATTISTHISGUYTALKING_GIF_EXTENSIONS = {".gif"}
WHATTISTHISGUYTALKING_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv"}
WHATTISTHISGUYTALKING_GIF_COOLDOWN_SECONDS = 20
WHATTISTHISGUYTALKING_VIDEO_COOLDOWN_SECONDS = 60
DEFAULT_DISCORD_UPLOAD_LIMIT = 25 * 1024 * 1024

whatisthisguytalking_media = {"static": [], "gif": [], "video": []}
whatisthisguytalking_gif_cooldowns = {}
whatisthisguytalking_video_cooldowns = {}
whatisthisguytalking_images = []

QUOTE_SIZE = (1200, 630)
QUOTE_AVATAR_SIZE = 630
QUOTE_TEXT_BOX = (560, 50, 1150, 580)
QUOTE_FONT_SIZES = tuple(range(55, 19, -2))
QUOTE_NAME_FONT_SIZE = 25
QUOTE_NAME_MIN_FONT_SIZE = 14
QUOTE_NAME_GAP = 20
QUOTE_MAX_CUSTOM_EMOJIS = 50
QUOTE_ITALIC_SHEAR = 0.2
QUOTE_ITALIC_SUPERSAMPLE = 4
QUOTE_TEXT_COLOR = (255, 255, 255, 255)
QUOTE_SUBTEXT_COLOR = (148, 155, 164, 255)
QUOTE_LINK_COLOR = (0, 168, 252, 255)
QUOTE_MENTION_COLOR = (201, 205, 251, 255)
QUOTE_CODE_COLOR = (219, 222, 225, 255)
QUOTE_BAR_COLOR = (78, 80, 88, 255)
QUOTE_CODE_BLOCK_FILL = (43, 45, 49, 255)
# 行內色塊（樣式 -> 底色），依序畫，後面的蓋在前面上面
QUOTE_INLINE_FILLS = (
    ("spoiler", (255, 255, 255, 26)),
    ("code", (43, 45, 49, 255)),
    ("mention", (88, 101, 242, 77)),
    ("timestamp", (78, 80, 88, 122)),
)
QUOTE_PILL_STYLES = frozenset({"code", "mention", "timestamp"})
# 區塊種類 -> (字型家族, 字級倍率, 文字顏色)
QUOTE_BLOCK_STYLES = {
    "text": ("bold", 1.0, QUOTE_TEXT_COLOR),
    "h1": ("bold", 1.5, QUOTE_TEXT_COLOR),
    "h2": ("bold", 1.3, QUOTE_TEXT_COLOR),
    "h3": ("bold", 1.15, QUOTE_TEXT_COLOR),
    "subtext": ("light", 0.75, QUOTE_SUBTEXT_COLOR),
    "code": ("mono", 0.8, QUOTE_CODE_COLOR),
    "name": ("light", 1.0, QUOTE_TEXT_COLOR),
}
QUOTE_TIMEZONE = timezone(timedelta(hours=8))
QUOTE_TIMESTAMP_FORMATS = {"t": "%H:%M", "T": "%H:%M:%S", "d": "%Y/%m/%d", "D": "%Y/%m/%d"}

# 避頭尾：這些標點不放在行首／行尾
_QUOTE_NO_BREAK_BEFORE = frozenset("!),.:;?]}、。，．！？；：）」』】》〉〕…ー～")
_QUOTE_NO_BREAK_AFTER = frozenset("([{（「『【《〈〔")
# 常被拿來湊「空白名字」的填充字，字型多半沒有 glyph，當空白處理
_QUOTE_BLANK_CHARS = frozenset(map(chr, (0x115F, 0x1160, 0x2800, 0x3164, 0xFFA0)))
_QUOTE_ZERO_WIDTH_SPACE = chr(0x200B)
_QUOTE_TEXT_PRESENTATION = chr(0xFE0E)

_QUOTE_CODE_BLOCK = re.compile(r"```(?:[A-Za-z0-9_+\-.#]+?\n)?\n*([\s\S]*?)\n*```")
_QUOTE_HEADING = re.compile(r"(#{1,3}) +(?=\S)")
_QUOTE_SUBTEXT = re.compile(r"-# +(?=\S)")
_QUOTE_LIST_ITEM = re.compile(r"( *)([-*]|\d{1,9}\.) +(?=\S)")
# 跟 Discord（simple-markdown）一樣，在目前位置依序試每條規則，都不符合就當一般文字
_QUOTE_INLINE_RULES = (
    ("escape", re.compile(r"\\([^0-9A-Za-z\s])")),
    ("emoji", re.compile(r"<a?:(\w{1,32}):(\d{15,21})>")),
    ("user", re.compile(r"<@!?(\d{15,21})>")),
    ("role", re.compile(r"<@&(\d{15,21})>")),
    ("channel", re.compile(r"<#(\d{15,21})>")),
    ("timestamp", re.compile(r"<t:(-?\d{1,13})(?::([tTdDfFR]))?>")),
    ("command", re.compile(r"</([^<>:\n]{1,100}):\d{15,21}>")),
    ("autolink", re.compile(r"<(https?://[^\s>]+)>")),
    ("url", re.compile(r"https?://[^\s<]+[^<.,:;\"')\]\s]")),
    ("link", re.compile(r"\[((?:\\.|[^\[\]\\])+)\]\(\s*<?(https?://[^\s<>()]+)>?\s*\)")),
    ("code", re.compile(r"(`+)(.*?[^`])\1(?!`)", re.DOTALL)),
    ("bold", re.compile(r"\*\*(.+?)\*\*(?!\*)", re.DOTALL)),
    ("underline", re.compile(r"__(.+?)__(?!_)", re.DOTALL)),
    ("italic", re.compile(r"\*(?=\S)((?:\*\*|\\.|\s+(?:\\.|[^\s*\\]|\*\*)|[^\s*\\])+?)\*(?!\*)", re.DOTALL)),
    ("italic", re.compile(r"\b_((?:__|\\.|[^\\_])+?)_\b", re.DOTALL)),
    ("strike", re.compile(r"~~(.+?)~~", re.DOTALL)),
    ("spoiler", re.compile(r"\|\|(.+?)\|\|", re.DOTALL)),
)
_QUOTE_NESTED_RULES = frozenset({"bold", "underline", "italic", "strike", "spoiler"})
_QUOTE_INLINE_TEXT = re.compile(r".+?(?=[\\<*_~|\[`]|https?://|\Z)", re.DOTALL)
_QUOTE_MAX_NESTING = 16


@dataclass(slots=True)
class _QuoteRun:
    text: str
    style: frozenset = frozenset()
    emoji_id: str | None = None


@dataclass(slots=True)
class _QuoteBlock:
    """訊息裡的一行；同一組（引用、清單、同一個程式碼區塊）的連續行會靠左對齊成一塊"""
    runs: list
    kind: str = "text"
    group: object = None
    level: int = 0
    bullet: str | None = None


@dataclass(slots=True)
class _QuotePiece:
    """排版單位：一個字（含組合附加符號）、一個 emoji，或色塊兩側的留白"""
    text: str = ""
    font_path: str | None = None
    size: int = 0
    width: float = 0.0
    style: frozenset = frozenset()
    color: tuple = QUOTE_TEXT_COLOR
    emoji: str | None = None
    emoji_id: str | None = None
    space: bool = False
    breakable: bool = False  # 可以在這個 piece 前面換行


@dataclass(slots=True)
class _QuoteLine:
    pieces: list
    width: float
    ascent: int
    descent: int
    height: int
    gap: int
    size: int
    family: str
    color: tuple
    kind: str = "text"
    group: object = None
    inset: int = 0  # 引用線、清單縮排佔掉的寬度


def _quote_timestamp_label(match: re.Match) -> str:
    try:
        moment = datetime.fromtimestamp(int(match.group(1)), QUOTE_TIMEZONE)
    except (OverflowError, OSError, ValueError):
        return match.group(0)
    return moment.strftime(QUOTE_TIMESTAMP_FORMATS.get(match.group(2) or "f", "%Y/%m/%d %H:%M"))


def _quote_markup_label(message: discord.Message, kind: str, match: re.Match) -> str:
    """把提及、時間戳、指令換成 Discord 上看到的文字；快取裡沒有就找訊息自帶的提及資料"""
    if kind == "timestamp":
        return _quote_timestamp_label(match)
    if kind == "command":
        return f"/{match.group(1)}"
    guild = message.guild
    target_id = int(match.group(1))
    if kind == "user":
        user = (
            (guild and guild.get_member(target_id))
            or next((user for user in message.mentions if user.id == target_id), None)
            or bot.get_user(target_id)
        )
        return f"@{user.display_name}" if user else "@unknown-user"
    if kind == "role":
        role = (guild and guild.get_role(target_id)) or next(
            (role for role in message.role_mentions if role.id == target_id), None
        )
        return f"@{role.name}" if role else "@deleted-role"
    channel = (
        (guild and guild.get_channel_or_thread(target_id))
        or bot.get_channel(target_id)
        or next((channel for channel in message.channel_mentions if channel.id == target_id), None)
    )
    name = getattr(channel, "name", None)
    return f"#{name}" if name else "#unknown"


def _parse_quote_inline(text: str, style: frozenset, resolve, depth: int = 0) -> list[_QuoteRun]:
    runs = []
    rules = _QUOTE_INLINE_RULES if depth < _QUOTE_MAX_NESTING else ()
    position = 0
    while position < len(text):
        for kind, pattern in rules:
            match = pattern.match(text, position)
            if match:
                break
        else:
            kind, match = "text", _QUOTE_INLINE_TEXT.match(text, position)
        position = match.end()

        if kind in ("text", "escape"):
            value = match.group(1) if kind == "escape" else match.group(0)
            if runs and runs[-1].style == style and runs[-1].emoji_id is None:
                runs[-1].text += value
            else:
                runs.append(_QuoteRun(value, style))
        elif kind == "emoji":
            runs.append(_QuoteRun(f":{match.group(1)}:", style, match.group(2)))
        elif kind in ("autolink", "url"):
            runs.append(_QuoteRun(match.group(1) if kind == "autolink" else match.group(0), style | {"link"}))
        elif kind == "link":
            runs.extend(_parse_quote_inline(match.group(1), style | {"link"}, resolve, depth + 1))
        elif kind == "code":
            code = match.group(2)
            runs.append(_QuoteRun(code.strip() or code, style | {"code"}))
        elif kind in _QUOTE_NESTED_RULES:
            runs.extend(_parse_quote_inline(match.group(1), style | {kind}, resolve, depth + 1))
        else:
            label_style = "timestamp" if kind == "timestamp" else "mention"
            runs.append(_QuoteRun(resolve(kind, match), style | {label_style}))
    return runs


def _split_quote_runs(runs: list[_QuoteRun], line_count: int) -> list[list[_QuoteRun]]:
    lines = [[] for _ in range(line_count)]
    index = 0
    for run in runs:
        if run.emoji_id:
            lines[index].append(run)
            continue
        for offset, part in enumerate(run.text.split("\n")):
            if offset:
                index = min(index + 1, line_count - 1)
            if part:
                lines[index].append(_QuoteRun(part, run.style))
    return lines


def _parse_quote_lines(text: str, resolve) -> list[_QuoteBlock]:
    """先拆掉每行的區塊語法（引用、標題、小字、清單），剩下的接起來一起解析行內語法，跨行的 ||暴雷|| 才對得起來"""
    entries = []
    in_block_quote = False
    for line in text.split("\n"):
        quote = in_block_quote
        if not quote and line.startswith(">>> "):
            in_block_quote = quote = True
            line = line[4:]
        elif not quote and line.startswith("> "):
            quote = True
            line = line[2:]

        block = _QuoteBlock([])
        if match := _QUOTE_HEADING.match(line):
            block.kind = f"h{len(match.group(1))}"
        elif match := _QUOTE_SUBTEXT.match(line):
            block.kind = "subtext"
        elif match := _QUOTE_LIST_ITEM.match(line):
            block.level = min(len(match.group(1)) // 2, 3)
            marker = match.group(2)
            block.bullet = f"{marker} " if marker[0].isdigit() else ("• " if block.level == 0 else "◦ ")
        if match:
            line = line[match.end():]
        block.group = "quote" if quote else ("list" if block.bullet else None)
        entries.append((line, block))

    runs = _parse_quote_inline("\n".join(line for line, _ in entries), frozenset(), resolve)
    for (_, block), line_runs in zip(entries, _split_quote_runs(runs, len(entries))):
        block.runs = line_runs
    return [block for _, block in entries]


def _parse_quote_markdown(content: str, resolve) -> list[_QuoteBlock]:
    """把 Discord markdown 拆成一行一行的區塊；resolve(kind, match) 回傳提及、時間戳要顯示的文字"""
    content = unicodedata.normalize("NFC", content.replace("\r\n", "\n").replace("\r", "\n")).replace("\t", "    ")
    blocks = []
    cursor = 0
    for index, match in enumerate(_QUOTE_CODE_BLOCK.finditer(content)):
        before = content[cursor:match.start()]
        if cursor:
            before = before.removeprefix("\n")
        if before:
            blocks.extend(_parse_quote_lines(before.removesuffix("\n"), resolve))
        code = match.group(1)
        if code.strip():
            blocks.extend(
                _QuoteBlock([_QuoteRun(line)] if line else [], "code", ("code", index))
                for line in code.split("\n")
            )
        cursor = match.end()
    rest = content[cursor:]
    if cursor:
        rest = rest.removeprefix("\n")
    if rest:
        blocks.extend(_parse_quote_lines(rest, resolve))

    # 連續空行只留一行，頭尾的空行拿掉
    result = []
    for block in blocks:
        if block.kind != "code" and not block.bullet and not any(run.emoji_id or run.text.strip() for run in block.runs):
            if result and result[-1].kind != "blank":
                result.append(_QuoteBlock([], "blank", block.group))
        else:
            result.append(block)
    while result and result[-1].kind == "blank":
        result.pop()
    return result


@lru_cache(maxsize=None)
def _quote_font_paths(family: str) -> tuple[str, ...]:
    """字型家族的主字型，加上缺字時依序往後找的字型（系統字型清單跟 AI 表格圖片共用）"""
    bold = family == "bold"
    paths = [os.path.join(fontdir, "notobold.ttf" if bold else "notolight.ttf")]
    if family == "mono":
        paths.insert(0, ai_table._matplotlib_font("DejaVuSansMono.ttf"))
    if not bold:
        # Noto Sans TC 的拉丁擴充、西里爾、希臘字母不齊，先用同系列的 Noto Sans 補
        paths.append(os.path.join(fontdir, "notoregular.ttf"))
    paths.extend(ai_table._fallback_font_paths(bold))
    return tuple(dict.fromkeys(path for path in paths if path and os.path.exists(path)))


@lru_cache(maxsize=8192)
def _quote_font_path(family: str, char: str) -> str | None:
    """第一個有這個字的字型；都沒有時回傳 None"""
    codepoint = ord(char)
    return next((path for path in _quote_font_paths(family) if codepoint in ai_table._font_codepoints(path)), None)


@lru_cache(maxsize=64)
def _quote_font(path: str, size: int):
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)


@lru_cache(maxsize=65536)
def _quote_text_width(path: str, size: int, text: str) -> float:
    return _quote_font(path, size).getlength(text)


def _quote_pieces_width(pieces) -> float:
    return sum(piece.width for piece in pieces)


def _quote_bar_metrics(size: int) -> tuple[int, int]:
    """引用線的寬度，以及引用線跟文字的距離"""
    return max(2, round(size * 0.08)), max(4, round(size * 0.35))


def _quote_code_padding(size: int) -> int:
    return max(4, round(size * 0.3))


def _quote_emoji_margin(emoji_size: int) -> int:
    return max(1, round(emoji_size * 0.04))


def _is_variation_selector(char: str) -> bool:
    codepoint = ord(char)
    return 0xFE00 <= codepoint <= 0xFE0F or 0xE0100 <= codepoint <= 0xE01EF


def _append_quote_chars(pieces: list, text: str, family: str, size: int, color, style: frozenset):
    primary = _quote_font_paths(family)[0]
    breakable = False
    for char in text:
        if char.isspace() or char in _QUOTE_BLANK_CHARS:
            path = None if char in _QUOTE_BLANK_CHARS else _quote_font_path(family, char)
            width = _quote_text_width(path, size, char) if path else _quote_text_width(primary, size, " ")
            pieces.append(_QuotePiece(" ", None, size, width, style, color, space=True))
            continue
        if char == _QUOTE_ZERO_WIDTH_SPACE:
            breakable = True
            continue
        category = unicodedata.category(char)
        # 控制字元、方向覆寫、零寬字元、落單的變體選擇符都不畫，免得變成方塊
        if category in ("Cc", "Cf", "Cs") or _is_variation_selector(char):
            continue
        if category.startswith("M") and pieces and pieces[-1].font_path and not pieces[-1].space:
            # 組合附加符號（注音調號、重音、Zalgo）跟前一個字畫在一起
            previous = pieces[-1]
            if ord(char) in ai_table._font_codepoints(previous.font_path):
                previous.text += char
                previous.width = _quote_text_width(previous.font_path, previous.size, previous.text)
                continue
        path = _quote_font_path(family, char)
        if path is None:
            # 所有字型都沒有的字（例如 𝓐 這類花體）改用相容字形 A
            normalized = unicodedata.normalize("NFKC", char)
            if normalized != char and all(_quote_font_path(family, item) for item in normalized):
                _append_quote_chars(pieces, normalized, family, size, color, style)
                continue
            path = primary
        pieces.append(_QuotePiece(char, path, size, _quote_text_width(path, size, char), style, color, breakable=breakable))
        breakable = False


def _append_quote_text(pieces: list, text: str, family: str, size: int, color, style: frozenset, emoji_size: int):
    cursor = 0
    for found in emoji.emoji_list(text):
        _append_quote_chars(pieces, text[cursor:found["match_start"]], family, size, color, style)
        sequence = found["emoji"]
        # ©、™、♠ 這類預設是文字樣式的符號也畫成 Twemoji；只有後面接 U+FE0E（明確要求文字樣式）才照文字畫
        text_presentation = text[found["match_end"]:found["match_end"] + 1] == _QUOTE_TEXT_PRESENTATION
        if text_presentation or ai_emoji.twemoji_glyph(sequence) is None:
            _append_quote_chars(pieces, sequence, family, size, color, style)
        else:
            width = emoji_size + 2 * _quote_emoji_margin(emoji_size)
            pieces.append(_QuotePiece(width=width, style=style, color=color, emoji=sequence))
        cursor = found["match_end"]
    _append_quote_chars(pieces, text[cursor:], family, size, color, style)


def _quote_run_format(style: frozenset, family: str, size: int, color) -> tuple[str, int, tuple]:
    if "bold" in style and family == "light":
        family = "bold"
    if "code" in style:
        family, size, color = "mono", max(1, round(size * 0.85)), QUOTE_CODE_COLOR
    if "mention" in style:
        color = QUOTE_MENTION_COLOR
    elif "link" in style:
        color = QUOTE_LINK_COLOR
    return family, size, color


def _is_cjk(char):
    """判斷字元是否為 CJK（中日韓）字元，用於逐字換行"""
    cp = ord(char)
    return (
        (0x4E00 <= cp <= 0x9FFF) or    # CJK Unified Ideographs
        (0x3400 <= cp <= 0x4DBF) or    # CJK Unified Ideographs Extension A
        (0x20000 <= cp <= 0x2A6DF) or  # CJK Unified Ideographs Extension B
        (0x2A700 <= cp <= 0x2B73F) or  # CJK Unified Ideographs Extension C
        (0x2B740 <= cp <= 0x2B81F) or  # CJK Unified Ideographs Extension D
        (0xF900 <= cp <= 0xFAFF) or    # CJK Compatibility Ideographs
        (0x3000 <= cp <= 0x303F) or    # CJK Symbols and Punctuation
        (0xFF00 <= cp <= 0xFFEF) or    # Halfwidth and Fullwidth Forms
        (0x3040 <= cp <= 0x309F) or    # Hiragana
        (0x30A0 <= cp <= 0x30FF) or    # Katakana
        (0xAC00 <= cp <= 0xD7AF)       # Hangul Syllables
    )


def _quote_piece_is_wide(piece: _QuotePiece) -> bool:
    return bool(piece.emoji or piece.emoji_id or (piece.text and _is_cjk(piece.text[0])))


def _mark_quote_breaks(pieces: list):
    """空白後面可以換行；中日韓文字、emoji 前後都可以換行，但避開不能放行首／行尾的標點"""
    for previous, piece in zip(pieces, pieces[1:]):
        if piece.space:
            continue
        if previous.space:
            piece.breakable = True
        elif (
            (_quote_piece_is_wide(previous) or _quote_piece_is_wide(piece))
            and piece.text[:1] not in _QUOTE_NO_BREAK_BEFORE
            and previous.text[-1:] not in _QUOTE_NO_BREAK_AFTER
        ):
            piece.breakable = True


def _quote_pieces(runs, family: str, size: int, color, emoji_size: int, custom_emojis=None) -> list[_QuotePiece]:
    custom_emojis = custom_emojis or {}
    pieces = []
    for run in runs:
        run_family, run_size, run_color = _quote_run_format(run.style, family, size, color)
        pill_padding = max(2, round(size * 0.12)) if run.style & QUOTE_PILL_STYLES else 0
        if pill_padding:
            pieces.append(_QuotePiece(width=pill_padding, style=run.style))
        if run.emoji_id and run.emoji_id in custom_emojis:
            width = emoji_size + 2 * _quote_emoji_margin(emoji_size)
            pieces.append(_QuotePiece(width=width, style=run.style, emoji_id=run.emoji_id))
        else:
            _append_quote_text(pieces, run.text, run_family, run_size, run_color, run.style, emoji_size)
        if pill_padding:
            pieces.append(_QuotePiece(width=pill_padding, style=run.style))
    _mark_quote_breaks(pieces)
    return pieces


def _strip_quote_spaces(row: list) -> list:
    end = len(row)
    while end and row[end - 1].space:
        end -= 1
    return row[:end]


def _wrap_quote_pieces(pieces: list, max_width: float, keep_indent: bool = False) -> list[list]:
    rows = []
    row = []
    width = 0.0
    for piece in pieces:
        if not row and piece.space and (rows or not keep_indent):
            continue
        if row and not piece.space and width + piece.width > max_width:
            cut = None if piece.breakable else next(
                (index for index in range(len(row) - 1, 0, -1) if row[index].breakable), None
            )
            if cut is None:
                # 這個 piece 前面本來就能換行，或整段都沒有換行點（很長的英文字）就硬斷
                rows.append(_strip_quote_spaces(row))
                row = []
            else:
                rows.append(_strip_quote_spaces(row[:cut]))
                row = row[cut:]
            width = _quote_pieces_width(row)
            if row and width + piece.width > max_width:
                rows.append(row)
                row = []
                width = 0.0
        row.append(piece)
        width += piece.width
    rows.append(_strip_quote_spaces(row))
    return rows


def _layout_quote_block(block: _QuoteBlock, size: int, max_width: float, custom_emojis) -> list[_QuoteLine]:
    family, scale, color = QUOTE_BLOCK_STYLES.get(block.kind, QUOTE_BLOCK_STYLES["text"])
    block_size = max(1, round(size * scale))
    ascent, descent = _quote_font(_quote_font_paths(family)[0], block_size).getmetrics()
    gap = max(2, round(block_size * 0.18))

    def make_line(pieces, inset):
        return _QuoteLine(
            pieces, _quote_pieces_width(pieces), ascent, descent, ascent + descent + gap, gap,
            block_size, family, color, block.kind, block.group, inset,
        )

    if block.kind == "blank":
        # 段落間的空行只留半行高
        blank = make_line([], 0)
        blank.height //= 2
        blank.gap = 0
        return [blank]

    inset = 0
    if block.group == "quote":
        inset += sum(_quote_bar_metrics(size))
    if block.bullet:
        inset += block.level * block_size
    emoji_size = ascent + descent
    bullet = _quote_pieces([_QuoteRun(block.bullet)], family, block_size, color, emoji_size) if block.bullet else []
    hanging = _quote_pieces_width(bullet)
    padding = 2 * _quote_code_padding(size) if block.kind == "code" else 0
    pieces = _quote_pieces(block.runs, family, block_size, color, emoji_size, custom_emojis)
    rows = _wrap_quote_pieces(pieces, max(1, max_width - inset - hanging - padding), keep_indent=block.kind == "code")
    return [make_line(bullet + rows[0], inset)] + [make_line(row, inset + hanging) for row in rows[1:]]


def _layout_quote(blocks, size: int, max_width: float, custom_emojis) -> list[_QuoteLine]:
    return [line for block in blocks for line in _layout_quote_block(block, size, max_width, custom_emojis)]


def _quote_layout_height(lines: list[_QuoteLine], size: int) -> int:
    code_groups = {line.group for line in lines if line.kind == "code"}
    return sum(line.height for line in lines) + len(code_groups) * 2 * _quote_code_padding(size)


def _ellipsize_quote_line(line: _QuoteLine, max_width: float) -> _QuoteLine:
    suffix = []
    _append_quote_chars(suffix, "…", line.family, line.size, line.color, frozenset())
    suffix_width = _quote_pieces_width(suffix)
    pieces = list(line.pieces)
    while pieces and (pieces[-1].space or _quote_pieces_width(pieces) + suffix_width > max_width):
        pieces.pop()
    pieces += suffix
    return replace(line, pieces=pieces, width=_quote_pieces_width(pieces))


def _fit_quote_layout(blocks, max_width: int, max_height: int, custom_emojis) -> tuple[int, list[_QuoteLine]]:
    """找放得下的最大字級；最小字級還放不下就截斷，最後一行補上刪節號"""
    low, high = 0, len(QUOTE_FONT_SIZES) - 1
    best = None
    while low <= high:
        middle = (low + high) // 2
        size = QUOTE_FONT_SIZES[middle]
        lines = _layout_quote(blocks, size, max_width, custom_emojis)
        if _quote_layout_height(lines, size) <= max_height:
            best = size, lines
            high = middle - 1
        else:
            low = middle + 1
    if best:
        return best

    size = QUOTE_FONT_SIZES[-1]
    kept = []
    for line in _layout_quote(blocks, size, max_width, custom_emojis):
        if kept and _quote_layout_height(kept + [line], size) > max_height:
            break
        kept.append(line)
    while len(kept) > 1 and kept[-1].kind == "blank":
        kept.pop()
    last = kept[-1]
    padding = 2 * _quote_code_padding(size) if last.kind == "code" else 0
    kept[-1] = _ellipsize_quote_line(last, max_width - last.inset - padding)
    return size, kept


def _alpha_composite(target: Image.Image, image: Image.Image, x, y):
    """Image.alpha_composite 的位置不能是負的，也不能超出邊界，先裁掉外面的部分"""
    x, y = round(x), round(y)
    left, top = max(0, -x), max(0, -y)
    right, bottom = min(image.width, target.width - x), min(image.height, target.height - y)
    if right <= left or bottom <= top:
        return
    if (left, top, right, bottom) != (0, 0, image.width, image.height):
        image = image.crop((left, top, right, bottom))
    target.alpha_composite(image, (x + left, y + top))


def _fill_quote_rect(target: Image.Image, box, fill, radius: int = 0):
    left, top, right, bottom = (round(value) for value in box)
    if right <= left or bottom <= top:
        return
    tile = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, tile.width - 1, tile.height - 1), radius=radius, fill=fill)
    _alpha_composite(target, tile, left, top)


def _round_up(value: int, multiple: int) -> int:
    return -(-value // multiple) * multiple


def _draw_quote_text(target: Image.Image, x: float, baseline: int, text: str, font_path: str, size: int, color, italic: bool):
    """先畫成遮罩再上色合成，透明圖層上的字邊緣才不會變黑；斜體放大畫再斜切縮小，邊緣比較銳利"""
    scale = QUOTE_ITALIC_SUPERSAMPLE if italic else 1
    shear = QUOTE_ITALIC_SHEAR if italic else 0
    font = _quote_font(font_path, size * scale)
    left, top, right, bottom = font.getbbox(text, anchor="ls")
    if right <= left or bottom <= top:
        return
    origin_x = _round_up(max(0, math.ceil(shear * max(bottom, 0)) - left), scale)
    origin_y = _round_up(max(0, -top), scale)
    width = _round_up(origin_x + right + math.ceil(shear * max(-top, 0)), scale)
    height = _round_up(origin_y + max(bottom, 0), scale)
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).text((origin_x, origin_y), text, font=font, fill=255, anchor="ls")
    if italic:
        mask = mask.transform(
            mask.size,
            Image.Transform.AFFINE,
            (1, shear, -shear * origin_y, 0, 1, 0),
            resample=Image.Resampling.BICUBIC,
        )
        mask = mask.resize((width // scale, height // scale), Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", mask.size, color[:3] + (0,))
    tile.putalpha(mask if color[3] == 255 else mask.point(lambda value: value * color[3] // 255))
    _alpha_composite(target, tile, x - origin_x // scale, baseline - origin_y // scale)


def _quote_emoji_image(piece: _QuotePiece, size: int, custom_emojis, cache: dict) -> Image.Image | None:
    if piece.emoji:
        return ai_emoji.render_twemoji(piece.emoji, size)
    key = (piece.emoji_id, size)
    if key not in cache:
        source = custom_emojis.get(piece.emoji_id)
        tile = None
        if source is not None:
            image = ImageOps.contain(source, (size, size), Image.Resampling.LANCZOS)
            tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            tile.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
        cache[key] = tile
    return cache[key]


def _quote_style_spans(pieces, positions, style: str) -> list[list]:
    spans = []
    for piece, left in zip(pieces, positions):
        if style not in piece.style:
            continue
        if spans and abs(spans[-1][1] - left) < 0.01:
            spans[-1][1] = left + piece.width
        else:
            spans.append([left, left + piece.width, piece.color])
    return spans


def _draw_quote_line(target: Image.Image, line: _QuoteLine, x: float, top: int, custom_emojis, cache: dict):
    baseline = top + line.ascent
    positions = list(accumulate((piece.width for piece in line.pieces), initial=x))
    pill_top, pill_bottom = baseline - round(line.size * 0.95), baseline + round(line.size * 0.3)
    for style, fill in QUOTE_INLINE_FILLS:
        for start, end, _ in _quote_style_spans(line.pieces, positions, style):
            _fill_quote_rect(target, (start, pill_top, end, pill_bottom), fill, radius=max(2, line.size // 8))

    # 相鄰、同字型同顏色的字併成一段畫；空白不畫，順便當分段點
    runs = []
    for piece, left in zip(line.pieces, positions):
        if piece.space or not piece.text or not piece.font_path:
            continue
        key = (piece.font_path, piece.size, piece.color, "italic" in piece.style)
        if runs and runs[-1][0] == key and abs(runs[-1][3] - left) < 0.01:
            runs[-1][2] += piece.text
            runs[-1][3] = left + piece.width
        else:
            runs.append([key, left, piece.text, left + piece.width])
    for (font_path, size, color, italic), left, text, _ in runs:
        _draw_quote_text(target, left, baseline, text, font_path, size, color, italic)

    emoji_size = line.ascent + line.descent
    margin = _quote_emoji_margin(emoji_size)
    for piece, left in zip(line.pieces, positions):
        if piece.emoji or piece.emoji_id:
            image = _quote_emoji_image(piece, emoji_size, custom_emojis, cache)
            if image is not None:
                _alpha_composite(target, image, left + margin, top)

    thickness = max(1, round(line.size / 16))
    for style, offset in (("underline", round(line.size * 0.12)), ("strike", -round(line.size * 0.3))):
        for start, end, color in _quote_style_spans(line.pieces, positions, style):
            _fill_quote_rect(target, (start, baseline + offset, end, baseline + offset + thickness), color)


def _draw_quote_lines(target: Image.Image, lines: list[_QuoteLine], size: int, center_x: int, top: int, custom_emojis) -> int:
    """一般的行各自置中；引用、清單、程式碼區塊整組置中、組內靠左。回傳畫完後的 y"""
    cache = {}
    y = top
    index = 0
    while index < len(lines):
        group = lines[index].group
        end = index + 1
        while group is not None and end < len(lines) and lines[end].group == group:
            end += 1
        members = lines[index:end]
        index = end
        if group is None:
            line = members[0]
            _draw_quote_line(target, line, center_x - line.width / 2, y, custom_emojis, cache)
            y += line.height
            continue

        width = max(line.inset + line.width for line in members)
        left = round(center_x - width / 2)
        content_height = sum(line.height for line in members) - members[-1].gap
        padding = _quote_code_padding(size) if members[0].kind == "code" else 0
        if padding:
            box = (left - padding, y, left + width + padding, y + content_height + 2 * padding)
            _fill_quote_rect(target, box, QUOTE_CODE_BLOCK_FILL, radius=padding)
            y += padding
        if group == "quote":
            bar_width, _ = _quote_bar_metrics(size)
            _fill_quote_rect(target, (left, y, left + bar_width, y + content_height), QUOTE_BAR_COLOR, radius=bar_width // 2)
        for line in members:
            _draw_quote_line(target, line, left + line.inset, y, custom_emojis, cache)
            y += line.height
        y += padding
    return y


def _render_quote_name(name: str, max_width: int) -> Image.Image | None:
    """署名用細體斜體；跟內文一樣有字型 fallback 跟 emoji，但不解析 markdown（Discord 名稱本來就不會）"""
    name = unicodedata.normalize("NFC", name.strip())
    block = _QuoteBlock([_QuoteRun(f"- {name}", frozenset({"italic"}))], "name")
    for size in range(QUOTE_NAME_FONT_SIZE, QUOTE_NAME_MIN_FONT_SIZE - 1, -1):
        line = _layout_quote_block(block, size, math.inf, {})[0]
        if line.width <= max_width:
            break
    else:
        line = _ellipsize_quote_line(line, max_width)
    padding = line.size
    canvas = Image.new("RGBA", (math.ceil(line.width) + padding * 2, line.ascent + line.descent + padding), (0, 0, 0, 0))
    _draw_quote_line(canvas, line, padding, 0, {}, {})
    bbox = canvas.getbbox()
    return canvas.crop(bbox) if bbox else None


async def _download_quote_emojis(session: aiohttp.ClientSession, blocks) -> dict[str, Image.Image]:
    emoji_ids = list(dict.fromkeys(run.emoji_id for block in blocks for run in block.runs if run.emoji_id))
    emoji_ids = emoji_ids[:QUOTE_MAX_CUSTOM_EMOJIS]

    async def fetch(emoji_id):
        url = f"https://cdn.discordapp.com/emojis/{emoji_id}.png?size=128"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()
            with Image.open(io.BytesIO(data)) as source:
                return source.convert("RGBA")
        except Exception as e:
            log(f"Failed to load emoji {url}: {e}", module_name="MessageImage", level=logging.WARNING)
            return None

    images = await asyncio.gather(*(fetch(emoji_id) for emoji_id in emoji_ids))
    # 抓不到的自訂表情會照 :名稱: 畫成文字
    return {emoji_id: image for emoji_id, image in zip(emoji_ids, images) if image is not None}


def _render_quote_image(avatar_img: Image.Image, name: str, blocks, custom_emojis, animate_gif=False) -> tuple[io.BytesIO, str]:
    # 判斷是否為動態圖片
    is_animated = getattr(avatar_img, "is_animated", False)

    width, height = QUOTE_SIZE

    # --- 2. 建立「前景層」(文字與遮罩) ---
    # 建立一個完全透明的圖層，我們把所有文字和遮罩畫在這裡，只需畫一次以節省效能
    foreground = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw_fg = ImageDraw.Draw(foreground)

    # 畫漸層弧形遮罩
    for i in range(51):
        alpha = min((i+1) * 5, 255)
        draw_fg.arc([-300 + i, -200, 630 + i, 1030], 270, 90, fill=(0, 0, 0, alpha), width=150)

    # 畫右側黑底區塊
    draw_fg.rectangle([540, 0, 700, 300], fill="black")

    # Area constraints
    x_min, y_min, x_max, y_max = QUOTE_TEXT_BOX
    max_w = x_max - x_min
    max_h = y_max - y_min
    center_x = (x_min + x_max) // 2

    # 署名先排好，內文能用的高度要扣掉它
    name_image = _render_quote_name(name, max_w)
    name_height = name_image.height + QUOTE_NAME_GAP if name_image else 0
    font_size, lines = _fit_quote_layout(blocks, max_w, max_h - name_height, custom_emojis)

    # 文字畫在獨立的透明圖層，最後一次合成到前景
    text_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    start_y = y_min + (max_h - _quote_layout_height(lines, font_size) - name_height) // 2
    current_y = _draw_quote_lines(text_layer, lines, font_size, center_x, start_y, custom_emojis)
    if name_image:
        _alpha_composite(text_layer, name_image, center_x - name_image.width // 2, current_y + QUOTE_NAME_GAP)
    foreground.alpha_composite(text_layer)

    # --- 3. 合成最終圖片 (處理 GIF 幀數或靜態 PNG) ---
    output_buffer = io.BytesIO()

    if is_animated and animate_gif:
        frames = []
        durations = []

        for frame in ImageSequence.Iterator(avatar_img):
            # 取得每一幀的持續時間 (預設 50 毫秒)
            durations.append(frame.info.get('duration', 50))

            # 處理單幀背景
            frame_rgba = frame.convert("RGBA").resize((QUOTE_AVATAR_SIZE, QUOTE_AVATAR_SIZE), Image.Resampling.LANCZOS)
            base = Image.new("RGBA", (width, height), (0, 0, 0, 255))
            base.paste(frame_rgba, (0, 0), frame_rgba)

            # 將前面準備好的文字前景疊加上去
            combined = Image.alpha_composite(base, foreground)

            # 轉換為 RGB 以利 GIF 儲存 (去透明底)
            frames.append(combined.convert("RGB"))

        # 儲存為動態 GIF
        frames[0].save(
            output_buffer,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            optimize=True
        )
        ext = "gif"
    else:
        # 靜態 PNG 處理
        avatar_rgba = avatar_img.convert("RGBA").resize((QUOTE_AVATAR_SIZE, QUOTE_AVATAR_SIZE), Image.Resampling.LANCZOS)
        base = Image.new("RGBA", (width, height), (0, 0, 0, 255))
        base.paste(avatar_rgba, (0, 0), avatar_rgba)

        combined = Image.alpha_composite(base, foreground)
        combined.save(output_buffer, format="PNG")
        ext = "png"

    output_buffer.seek(0)

    # 回傳 buffer 和副檔名，讓外層可以決定檔案名稱
    return output_buffer, ext


async def create(message: discord.Message, animate_gif=False) -> tuple[io.BytesIO, str]:
    blocks = _parse_quote_markdown(
        message.content.strip(),
        lambda kind, match: _quote_markup_label(message, kind, match),
    )

    # 1. 載入頭像與自訂表情
    async with aiohttp.ClientSession() as session:
        async with session.get(message.author.display_avatar.url) as resp:
            avatar_data = await resp.read()
        custom_emojis = await _download_quote_emojis(session, blocks)
    avatar_img = Image.open(io.BytesIO(avatar_data))

    return _render_quote_image(avatar_img, message.author.display_name, blocks, custom_emojis, animate_gif)


# 讚按鈕不存資料庫，所有狀態都編進 custom_id（上限 100 字元）：
#   uv:<作者 ID>:<讚數>:<看板訊息 ID>:<伺服器看板訊息 ID>:<按過讚的人>
# ID 用 base36 縮短，0 表示還沒上看板；按過讚的人用 288 bit 的 Bloom filter 記錄，
# base64 後固定 48 字元。Bloom filter 不會漏判，但有小機率把沒按過的人誤判成按過
# （約 20 票時 0.3%、50 票時 6%）。
UPVOTE_VOTER_FILTER_BYTES = 36
UPVOTE_VOTER_FILTER_HASHES = 4
_BASE36_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


def _to_base36(value: int) -> str:
    digits = ""
    while True:
        value, remainder = divmod(value, 36)
        digits = _BASE36_DIGITS[remainder] + digits
        if not value:
            return digits


def _upvote_voter_bits(message_id: int, user_id: int) -> int:
    digest = hashlib.blake2b(f"{message_id}:{user_id}".encode(), digest_size=UPVOTE_VOTER_FILTER_HASHES * 2).digest()
    bits = 0
    for i in range(0, len(digest), 2):
        bits |= 1 << (int.from_bytes(digest[i:i + 2], "big") % (UPVOTE_VOTER_FILTER_BYTES * 8))
    return bits


class _UpvoteQueue:
    """同一則訊息的按讚要依序處理；排隊中的 interaction 帶的是舊的 custom_id，所以記下最新的"""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.custom_id = None
        self.pending = 0


_upvote_queues: dict[int, _UpvoteQueue] = {}


class UpvoteButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"uv:(?P<author_id>[0-9a-z]+):(?P<count>[0-9]+):(?P<board_id>[0-9a-z]+):(?P<guild_board_id>[0-9a-z]+):(?P<voters>[A-Za-z0-9_-]{48})",
):
    def __init__(self, author_id: int, count: int = 0, board_id: int = 0, guild_board_id: int = 0, voters: int = 0):
        self.author_id = author_id
        self.count = count
        self.board_id = board_id
        self.guild_board_id = guild_board_id
        self.voters = voters
        super().__init__(discord.ui.Button(emoji="⬆️", style=discord.ButtonStyle.green, custom_id=self._encode()))
        self._refresh()

    def _encode(self) -> str:
        voters = base64.urlsafe_b64encode(self.voters.to_bytes(UPVOTE_VOTER_FILTER_BYTES, "big")).decode()
        return f"uv:{_to_base36(self.author_id)}:{self.count}:{_to_base36(self.board_id)}:{_to_base36(self.guild_board_id)}:{voters}"

    def _refresh(self):
        self.custom_id = self._encode()
        self.item.label = t("messageimage.value.upvote_count_suffix", count=self.count) if self.count else None

    @staticmethod
    def _decode(match: re.Match) -> dict:
        return {
            "author_id": int(match["author_id"], 36),
            "count": int(match["count"]),
            "board_id": int(match["board_id"], 36),
            "guild_board_id": int(match["guild_board_id"], 36),
            "voters": int.from_bytes(base64.urlsafe_b64decode(match["voters"]), "big"),
        }

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item, match: re.Match):
        return cls(**cls._decode(match))

    def sync(self, custom_id: str):
        """換成 custom_id 記錄的狀態；不是讚按鈕的 custom_id 就忽略"""
        match = self.template.fullmatch(custom_id)
        if match:
            for name, value in self._decode(match).items():
                setattr(self, name, value)
            self._refresh()

    async def callback(self, interaction: discord.Interaction):
        message = interaction.message
        await interaction.response.defer()
        queue = _upvote_queues.setdefault(message.id, _UpvoteQueue())
        queue.pending += 1
        try:
            async with queue.lock:
                if queue.custom_id:
                    self.sync(queue.custom_id)
                voter_bits = _upvote_voter_bits(message.id, interaction.user.id)
                if self.voters & voter_bits == voter_bits:
                    await interaction.followup.send(t("messageimage.err.already_upvoted"), ephemeral=True)
                    return
                self.count += 1
                self.voters |= voter_bits
                if self.count >= 5:
                    await self._update_boards(interaction)
                self._refresh()
                # self.view 是從訊息重建的暫時 view，先 stop 才不會被存進 ViewStore，
                # 蓋掉原本 view 還在處理的 GIF / 重新產生等按鈕
                self.view.stop()
                await interaction.edit_original_response(view=self.view)
                queue.custom_id = self.custom_id
        finally:
            queue.pending -= 1
            if not queue.pending:
                del _upvote_queues[message.id]

    async def _update_boards(self, interaction: discord.Interaction):
        message = interaction.message
        image = message.attachments[0].url if message.attachments else None
        if not image:
            return
        self.board_id = await self._post_to_board(interaction, bot.get_channel(config("upvote_board_channel_id")), self.board_id, image)
        guild_channel_id = get_server_config(interaction.guild.id, "upvote_board_channel_id") if interaction.guild else None
        if guild_channel_id:
            self.guild_board_id = await self._post_to_board(interaction, bot.get_channel(guild_channel_id), self.guild_board_id, image)

    async def _post_to_board(self, interaction: discord.Interaction, channel, board_message_id: int, image: str) -> int:
        """還沒上看板就發新訊息，已經上了就更新讚數；回傳看板訊息 ID"""
        if channel is None:
            return board_message_id
        content = t("messageimage.value.upvote_board_content", count=self.count)
        try:
            if board_message_id:
                await channel.get_partial_message(board_message_id).edit(content=content)
                return board_message_id
            author = (
                (interaction.guild and interaction.guild.get_member(self.author_id))
                or bot.get_user(self.author_id)
                or await bot.fetch_user(self.author_id)
            )
            embed = discord.Embed()
            embed.set_image(url=image)
            embed.set_author(name=author.display_name + f"({author.name})", icon_url=author.display_avatar.url)
            sent_message = await channel.send(embed=embed, content=content)
            return sent_message.id
        except discord.HTTPException as e:
            log(f"Failed to update board message: {e}", module_name="MessageImage", level=logging.ERROR)
            return board_message_id


bot.add_dynamic_items(UpvoteButton)


class UpvoteView(i18n.I18nView):
    def __init__(self, original_user: discord.abc.User):
        super().__init__()
        self.upvote_button = UpvoteButton(original_user.id)
        # 讚按鈕維持在第一個（子類別用裝飾器宣告的按鈕在 super().__init__() 就已經加進來了）
        other_items = self.children
        self.clear_items()
        self.add_item(self.upvote_button)
        for item in other_items:
            self.add_item(item)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # 讚數是 UpvoteButton 直接改在訊息上的，這個 view 手上的還是舊值；
        # 先同步，子類別的按鈕用 view=self 編輯訊息時才不會把讚數蓋回去
        for row in interaction.message.components:
            for component in getattr(row, "children", ()):
                self.upvote_button.sync(getattr(component, "custom_id", None) or "")
        return True

    async def on_timeout(self):
        # discord.py 移除逾時的 view 時，會把它含有的 DynamicItem 從全域註冊一併移除，要補回去
        bot.add_dynamic_items(UpvoteButton)


@bot.tree.command(name=app_commands.locale_str("upvoteboard", i18n_key="cmd.messageimage.upvoteboard.name"), description=app_commands.locale_str("Set up the upvote board; posts here once 5 people upvote a message.", i18n_key="cmd.messageimage.upvoteboard.desc"))
@app_commands.describe(channel=app_commands.locale_str("The channel to use (omit to clear the setting)", i18n_key="cmd.messageimage.upvoteboard.param.channel"))
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.default_permissions(manage_guild=True)
async def set_upvoteboard(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if channel:
        set_server_config(interaction.guild.id, "upvote_board_channel_id", channel.id)
        await interaction.response.send_message(t("messageimage.msg.upvoteboard_set", channel=channel.mention))
    else:
        set_server_config(interaction.guild.id, "upvote_board_channel_id", None)
        await interaction.response.send_message(t("messageimage.msg.upvoteboard_cleared"))


class BadQuoteView(UpvoteView):
    def __init__(self, message: discord.Message, user: discord.User = None):
        super().__init__(message.author)
        self.original_message = message
        self.user = user

    @discord.ui.button(label="GIF", style=discord.ButtonStyle.gray)
    async def toggle_gif(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.original_message.content or self.original_message.content.strip() == "":
            await interaction.response.send_message(t("messageimage.err.empty_message"), ephemeral=True)
            return
        if self.user.id != interaction.user.id:
            await interaction.response.send_message(t("messageimage.err.not_original_requester_button"), ephemeral=True)
            return
        button.style = discord.ButtonStyle.primary
        button.disabled = True  # 點擊後禁用按鈕，避免重複點擊造成多次生成
        await interaction.response.edit_message(view=self)
        try:
            output_buffer, ext = await create(self.original_message, animate_gif=True)
            await interaction.edit_original_response(attachments=[discord.File(output_buffer, filename=f"message_quote.{ext}")])
        except discord.HTTPException as e:
            await interaction.followup.send(t("messageimage.err.upload_failed_size_limit"), ephemeral=True)


@bot.tree.context_menu(name=app_commands.locale_str("Terrible Make it a Quote", i18n_key="cmd.messageimage.ctx.make_it_a_quote.name"))
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def make_it_a_quote(interaction: discord.Interaction, message: discord.Message):
    if not message.content or message.content.strip() == "":
        await interaction.response.send_message(t("messageimage.err.empty_message"), ephemeral=True)
        return
    await interaction.response.defer()
    output_buffer, ext = await create(message)
    view = BadQuoteView(message, user=interaction.user) if message.author.display_avatar.is_animated() else UpvoteView(original_user=message.author)
    await interaction.followup.send(file=discord.File(output_buffer, filename=f"message_quote.{ext}"), view=view)

@bot.command(name="badquote", aliases=["bquote", "bq", "makeitaquote", "miq"])
async def badquote(ctx: commands.Context):
    """
    糟糕的 Make it a Quote。
    回覆一則訊息以生成糟糕的 Make it a Quote 圖片。
    """
    # try to get replied message
    if ctx.message.reference:
        ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        message = ref_msg
    else:
        await ctx.send(t("messageimage.err.no_replied_message"))
        return
    if not message:
        await ctx.send(t("messageimage.err.replied_message_not_found"))
        return
    if not message.content or message.content.strip() == "":
        await ctx.send(t("messageimage.err.empty_message"))
        return
    output_buffer, ext = await create(message)
    view = BadQuoteView(message, user=ctx.author) if message.author.display_avatar.is_animated() else UpvoteView(original_user=message.author)
    await ctx.reply(file=discord.File(output_buffer, filename=f"message_quote.{ext}"), view=view)


class _ExportChannelAdapter:
    """Read-only channel facade for chat_exporter, including DM channels."""

    def __init__(self, channel):
        self._channel = channel
        self.guild = getattr(channel, "guild", None)
        self.id = getattr(channel, "id", 0)
        self.name = getattr(channel, "name", None) or "Direct Message"
        self.topic = getattr(channel, "topic", None)
        self.created_at = getattr(channel, "created_at", None)

    def __getattr__(self, name):
        return getattr(self._channel, name)


async def _collect_screenshot_messages(
    message: discord.Message,
    *,
    include_context: bool,
    context_limit: int,
    context_window_seconds: int,
) -> list[discord.Message]:
    """Return newest-first messages, which is the ordering raw_export expects."""
    messages = [message]
    if not include_context or context_limit <= 1:
        return messages

    previous_limit = max(min(int(context_limit) - 1, 9), 0)
    message_time = message.created_at
    try:
        async for previous in message.channel.history(
            limit=previous_limit,
            before=message.created_at,
            oldest_first=False,
        ):
            age = (message_time - previous.created_at).total_seconds()
            if previous.author.id != message.author.id or age >= context_window_seconds:
                break
            messages.append(previous)
    except Exception:
        pass
    return messages


async def _render_message_screenshot(
    message: discord.Message,
    *,
    include_context: bool,
    context_limit: int,
    context_window_seconds: int,
    render_metadata: dict | None = None,
) -> bytes:
    global browser
    if browser is None:
        raise Exception(t("messageimage.err.browser_not_started"))
    if not browser.is_connected():
        raise Exception(t("messageimage.err.browser_closed"))
    
    # make a stopwatch for debugging
    times = {"getting_messages": 0, "generating_html": 0, "taking_screenshot": 0}
    start_time = time.perf_counter()

    messages = await _collect_screenshot_messages(
        message,
        include_context=include_context,
        context_limit=context_limit,
        context_window_seconds=context_window_seconds,
    )
    if render_metadata is not None:
        rendered_messages = list(reversed(messages))
        render_metadata.update({
            "message_count": len(rendered_messages),
            "message_ids": [str(getattr(item, "id", "")) for item in rendered_messages],
            "display_order": "oldest_to_newest",
        })
    times["getting_messages"] = time.perf_counter() - start_time
    start_time = time.perf_counter()

    try:
        export_channel = (
            message.channel
            if getattr(message.channel, "guild", None) is not None
            else _ExportChannelAdapter(message.channel)
        )
        html_content = await chat_exporter.raw_export(
            export_channel,
            messages=messages,
            tz_info="Asia/Taipei",
            guild=getattr(message.channel, "guild", None),
            bot=bot,
            raise_exceptions=True
        )
    except Exception as e:
        log(f"Failed to generate HTML: {e}", module_name="MessageImage", level=logging.ERROR)
        traceback.print_exc()
        raise Exception(t("messageimage.err.html_generation_failed", error=str(e)))
    times["generating_html"] = time.perf_counter() - start_time
    start_time = time.perf_counter()

    page = None
    try:
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        await page.set_content(html_content, wait_until="load")
        # Force width to fit content so the screenshot isn't full width
        await page.add_style_tag(content=".chatlog__message-group { width: fit-content; } .chatlog { padding: 0px 1rem 0px 0px !important; border-top: unset !important; width: fit-content; }")
        # Remove spoiler hidden class
        await page.evaluate("() => { document.querySelectorAll('.spoiler--hidden').forEach(el => el.classList.remove('spoiler--hidden')); }")
        # Resize viewport to fit the actual content size so nothing gets clipped
        chatlog = page.locator('.chatlog')
        bounding_box = await chatlog.bounding_box()
        if bounding_box:
            new_width = max(int(bounding_box['width'] + bounding_box['x']) + 50, 800)
            new_height = max(int(bounding_box['height'] + bounding_box['y']) + 50, 600)
            await page.set_viewport_size({"width": new_width, "height": new_height})
        # Locate the message group container
        image_bytes = await chatlog.screenshot(type="png")
    except Exception as e:
        log(f"Screenshot failed: {e}", module_name="MessageImage", level=logging.ERROR)
        raise Exception(t("messageimage.err.screenshot_failed", error=str(e)))
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
    times["taking_screenshot"] = time.perf_counter() - start_time
    # log the times for debugging
    log(f"Screenshot generated: get_messages={times['getting_messages']*1000:.2f}ms, generate_html={times['generating_html']*1000:.2f}ms, screenshot={times['taking_screenshot']*1000:.2f}ms", module_name="MessageImage")

    return image_bytes


async def screenshot(
    message: discord.Message,
    *,
    include_context: bool = True,
    context_limit: int = 10,
    context_window_seconds: int = 300,
    timeout: float = 30.0,
    max_bytes: int | None = None,
):
    """Render a Discord message transcript fragment as a PNG buffer."""
    render_metadata = {}
    try:
        image_bytes = await asyncio.wait_for(
            _render_message_screenshot(
                message,
                include_context=include_context,
                context_limit=max(1, min(int(context_limit), 10)),
                context_window_seconds=max(1, int(context_window_seconds)),
                render_metadata=render_metadata,
            ),
            timeout=max(1.0, float(timeout)),
        )
    except asyncio.TimeoutError as exc:
        raise Exception(t("messageimage.err.screenshot_failed", error="generation timed out")) from exc

    if max_bytes is not None and len(image_bytes) > max_bytes:
        raise Exception(f"screenshot exceeds {int(max_bytes)} bytes")
    output = io.BytesIO(image_bytes)
    output.render_metadata = render_metadata
    return output


@bot.tree.context_menu(name=app_commands.locale_str("Screenshot Generator", i18n_key="cmd.messageimage.ctx.screenshot_generator.name"))
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def screenshot_generator(interaction: discord.Interaction, message: discord.Message):
    await interaction.response.defer()
    try:
        buffer = await screenshot(message)
        await interaction.followup.send(file=discord.File(buffer, filename="screenshot.png"), view=UpvoteView(message.author))
        log("Screenshot generation complete", module_name="MessageImage", user=interaction.user, guild=interaction.guild)
    except Exception as e:
        await interaction.followup.send(t("messageimage.err.screenshot_failed", error=str(e)), ephemeral=True)


@bot.command(name="screenshot", aliases=["ss", "sgen", "screenshotgen"])
async def screenshot_cmd(ctx: commands.Context):
    """
    截圖生成器。
    回覆一則訊息以截圖該訊息。
    """
    # try to get replied message
    message = None
    if ctx.message.reference:
        ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        message = ref_msg
    
    if not message:
        await ctx.reply(t("messageimage.err.no_replied_message"))
        return

    try:
        buffer = await screenshot(message)
        await ctx.reply(file=discord.File(buffer, filename="screenshot.png"), view=UpvoteView(message.author))
        log("Screenshot generation complete", module_name="MessageImage", user=ctx.author, guild=ctx.guild)
    except Exception as e:
        await ctx.reply(t("messageimage.err.screenshot_failed", error=str(e)))

def _classify_whatisthisguytalking_file(file_path: str) -> str | None:
    ext = os.path.splitext(file_path)[1].lower()
    if ext in WHATTISTHISGUYTALKING_STATIC_EXTENSIONS:
        return "static"
    if ext in WHATTISTHISGUYTALKING_GIF_EXTENSIONS:
        return "gif"
    if ext in WHATTISTHISGUYTALKING_VIDEO_EXTENSIONS:
        return "video"
    return None


def _pick_whatisthisguytalking_media(media_type: str | None = None) -> tuple[str, str]:
    if media_type is not None:
        choices = whatisthisguytalking_media.get(media_type, [])
        if not choices:
            raise RuntimeError(t("messageimage.err.no_media_of_type", media_type=media_type))
        return random.choice(choices), media_type

    for candidate_type in ("static", "gif", "video"):
        choices = whatisthisguytalking_media.get(candidate_type, [])
        if choices:
            return random.choice(choices), candidate_type

    raise RuntimeError(t("messageimage.err.no_media_available"))


def _resize_whatisthisguytalking_screenshot(screenshot_bytes: bytes, target_width: int) -> Image.Image:
    with Image.open(io.BytesIO(screenshot_bytes)) as screenshot_pil_image:
        screenshot_rgb = screenshot_pil_image.convert("RGB")

    screenshot_aspect_ratio = screenshot_rgb.width / screenshot_rgb.height
    new_screenshot_height = max(int(target_width / screenshot_aspect_ratio), 1)
    return screenshot_rgb.resize((target_width, new_screenshot_height), Image.Resampling.LANCZOS)


def _compose_whatisthisguytalking_png(
    screenshot_bytes: bytes,
    template_image: Image.Image,
) -> io.BytesIO:
    template_rgba = template_image.convert("RGBA")
    resized_screenshot = _resize_whatisthisguytalking_screenshot(
        screenshot_bytes,
        template_rgba.width,
    )

    combined_height = resized_screenshot.height + template_rgba.height
    combined_image = Image.new("RGB", (template_rgba.width, combined_height))
    combined_image.paste(resized_screenshot, (0, 0))
    combined_image.paste(template_rgba, (0, resized_screenshot.height), template_rgba)

    image_bytes = io.BytesIO()
    combined_image.save(image_bytes, "PNG")
    image_bytes.seek(0)
    return image_bytes


async def _run_media_command(command: list[str]) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return (
        process.returncode,
        stdout.decode("utf-8", errors="ignore"),
        stderr.decode("utf-8", errors="ignore"),
    )


async def _probe_video_dimensions(file_path: str) -> tuple[int, int]:
    return_code, stdout, stderr = await _run_media_command([
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        file_path,
    ])
    if return_code != 0:
        raise RuntimeError(t("messageimage.err.ffprobe_failed", error=stderr.strip() or stdout.strip()))

    payload = json.loads(stdout or "{}")
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError(t("messageimage.err.no_video_stream"))

    width = int(streams[0].get("width") or 0)
    height = int(streams[0].get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError(t("messageimage.err.cannot_get_video_size"))

    return width, height


async def _extract_video_preview_frame(file_path: str) -> Image.Image:
    with tempfile.TemporaryDirectory(prefix="whatisthisguytalking-frame-") as temp_dir:
        frame_path = os.path.join(temp_dir, "frame.png")
        return_code, stdout, stderr = await _run_media_command([
            "ffmpeg",
            "-y",
            "-i",
            file_path,
            "-frames:v",
            "1",
            frame_path,
        ])
        if return_code != 0 or not os.path.exists(frame_path):
            raise RuntimeError(t("messageimage.err.frame_extraction_failed", error=stderr.strip() or stdout.strip()))

        with Image.open(frame_path) as frame_image:
            return frame_image.convert("RGBA").copy()


async def _load_whatisthisguytalking_preview_template(
    file_path: str,
    media_type: str,
) -> Image.Image:
    if media_type == "video":
        return await _extract_video_preview_frame(file_path)

    with Image.open(file_path) as template_image:
        return template_image.convert("RGBA").copy()


async def _build_whatisthisguytalking_preview(screenshot_bytes: bytes) -> io.BytesIO:
    file_path, media_type = _pick_whatisthisguytalking_media()
    template_image = await _load_whatisthisguytalking_preview_template(file_path, media_type)
    return _compose_whatisthisguytalking_png(screenshot_bytes, template_image)


async def generate_whatisthisguytalking_gif(screenshot_bytes: bytes) -> io.BytesIO:
    file_path, _ = _pick_whatisthisguytalking_media("gif")

    with Image.open(file_path) as template_image:
        resized_screenshot = _resize_whatisthisguytalking_screenshot(
            screenshot_bytes,
            template_image.width,
        )
        frames = []
        durations = []
        loop = template_image.info.get("loop", 0)

        for frame in ImageSequence.Iterator(template_image):
            frame_rgba = frame.convert("RGBA")
            if frame_rgba.width != template_image.width:
                new_height = max(
                    int(frame_rgba.height * (template_image.width / frame_rgba.width)),
                    1,
                )
                frame_rgba = frame_rgba.resize(
                    (template_image.width, new_height),
                    Image.Resampling.LANCZOS,
                )

            combined = Image.new(
                "RGBA",
                (template_image.width, resized_screenshot.height + frame_rgba.height),
                (0, 0, 0, 255),
            )
            combined.paste(resized_screenshot, (0, 0))
            combined.paste(frame_rgba, (0, resized_screenshot.height), frame_rgba)
            frames.append(combined.convert("RGB"))
            durations.append(
                frame.info.get("duration", template_image.info.get("duration", 80)) or 80
            )

    if not frames:
        raise RuntimeError(t("messageimage.err.no_gif_frames"))

    output_buffer = io.BytesIO()
    frames[0].save(
        output_buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=loop,
        optimize=True,
    )
    output_buffer.seek(0)
    return output_buffer


async def generate_whatisthisguytalking_video(screenshot_bytes: bytes) -> io.BytesIO:
    file_path, _ = _pick_whatisthisguytalking_media("video")
    target_width, _ = await _probe_video_dimensions(file_path)
    resized_screenshot = _resize_whatisthisguytalking_screenshot(screenshot_bytes, target_width)

    with tempfile.TemporaryDirectory(prefix="whatisthisguytalking-video-") as temp_dir:
        screenshot_path = os.path.join(temp_dir, "screenshot.png")
        output_path = os.path.join(temp_dir, "whatisthisguytalking.mp4")
        resized_screenshot.save(screenshot_path, "PNG")

        return_code, stdout, stderr = await _run_media_command([
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-framerate",
            "30",
            "-i",
            screenshot_path,
            "-i",
            file_path,
            "-filter_complex",
            "[0:v][1:v]vstack=inputs=2,pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p[v]",
            "-map",
            "[v]",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            "-shortest",
            output_path,
        ])
        if return_code != 0 or not os.path.exists(output_path):
            raise RuntimeError(t("messageimage.err.ffmpeg_video_failed", error=stderr.strip() or stdout.strip()))

        with open(output_path, "rb") as video_file:
            video_bytes = io.BytesIO(video_file.read())

    video_bytes.seek(0)
    return video_bytes


def _consume_whatisthisguytalking_cooldown(
    cooldowns: dict[int, float],
    user_id: int,
    cooldown_seconds: int,
) -> int:
    now = time.monotonic()
    last_used = cooldowns.get(user_id)
    if last_used is not None:
        remaining = cooldown_seconds - (now - last_used)
        if remaining > 0:
            return math.ceil(remaining)

    cooldowns[user_id] = now
    return 0


def _get_interaction_upload_limit(interaction: discord.Interaction) -> int:
    if interaction.guild and interaction.guild.filesize_limit:
        return interaction.guild.filesize_limit
    return DEFAULT_DISCORD_UPLOAD_LIMIT

def _composite_whatisthisguytalking(screenshot_bytes: bytes) -> io.BytesIO:
    """將快取的截圖 bytes 與隨機模板圖片合成"""
    file_path = random.choice(whatisthisguytalking_images)
    template_image = Image.open(file_path)
    screenshot_pil_image = Image.open(io.BytesIO(screenshot_bytes))

    # Determine target width (use the width of the template image)
    target_width = template_image.width

    # Resize screenshot image to match the target width, maintaining aspect ratio
    screenshot_aspect_ratio = screenshot_pil_image.width / screenshot_pil_image.height
    new_screenshot_height = int(target_width / screenshot_aspect_ratio)
    resized_screenshot = screenshot_pil_image.resize((target_width, new_screenshot_height), Image.LANCZOS)

    # Create a new blank image with the combined height and target width
    combined_height = resized_screenshot.height + template_image.height
    combined_image = Image.new("RGB", (target_width, combined_height))

    # Paste the resized screenshot image at the top
    combined_image.paste(resized_screenshot, (0, 0))
    # Paste the template image below the screenshot
    combined_image.paste(template_image, (0, resized_screenshot.height))

    image_bytes = io.BytesIO()
    combined_image.save(image_bytes, "PNG")
    image_bytes.seek(0)
    return image_bytes

async def generate_whatisthisguytalking(message: discord.Message) -> tuple[io.BytesIO, bytes]:
    """生成圖片，回傳 (合成圖片 buffer, 截圖原始 bytes 用於快取)"""
    screenshot_buffer = await screenshot(message)
    screenshot_bytes = screenshot_buffer.getvalue()
    result = await _build_whatisthisguytalking_preview(screenshot_bytes)
    return result, screenshot_bytes


class WhatIsThisGuyTalkingView(UpvoteView):
    def __init__(self, screenshot_bytes: bytes, user: discord.User = None, original_user: discord.User = None):
        super().__init__(original_user=original_user)
        self.screenshot_bytes = screenshot_bytes  # 快取截圖 bytes，避免重複呼叫 chat_exporter
        self.user = user

    @discord.ui.button(emoji="🔄", style=discord.ButtonStyle.blurple)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.user and interaction.user.id != self.user.id:
            await interaction.followup.send(t("messageimage.err.not_original_requester_regen"), ephemeral=True)
            return
        try:
            buffer = await _build_whatisthisguytalking_preview(self.screenshot_bytes)
            await interaction.edit_original_response(
                attachments=[discord.File(buffer, filename="whatisthisguytalking.png")],
            )
        except Exception as e:
            await interaction.followup.send(t("messageimage.err.regen_failed", error=str(e)), ephemeral=True)


@bot.tree.context_menu(name=app_commands.locale_str("What is this guy talking about", i18n_key="cmd.messageimage.ctx.whatisthisguytalking.name"))
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def whatisthisguytalking(interaction: discord.Interaction, message: discord.Message):
    await interaction.response.defer()
    try:
        buffer, screenshot_bytes = await generate_whatisthisguytalking(message)
        # msg = f"現正開放投稿！\n-# {await get_command_mention('contribute', 'what-is-this-guy-talking-about')}"
        await interaction.followup.send(file=discord.File(buffer, filename="whatisthisguytalking.png"), view=EnhancedWhatIsThisGuyTalkingView(screenshot_bytes, user=interaction.user, original_user=message.author))
        log("Quote image generation complete", module_name="MessageImage", user=interaction.user, guild=interaction.guild)
    except Exception as e:
        await interaction.followup.send(t("messageimage.err.quote_generation_failed", error=str(e)), ephemeral=True)

class EnhancedWhatIsThisGuyTalkingView(UpvoteView):
    def __init__(self, screenshot_bytes: bytes, user: discord.User = None, original_user: discord.User = None):
        super().__init__(original_user=original_user)
        self.screenshot_bytes = screenshot_bytes
        self.user = user
        self._media_lock = asyncio.Lock()
        self.render_gif.disabled = not bool(whatisthisguytalking_media["gif"])
        self.render_video.disabled = not bool(whatisthisguytalking_media["video"])

    async def _start_media_generation(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
        media_type: str,
        cooldowns: dict[int, float],
        cooldown_seconds: int,
    ) -> bool:
        if self.user and interaction.user.id != self.user.id:
            await interaction.response.send_message(t("messageimage.err.not_original_requester_press"), ephemeral=True)
            return False

        if not whatisthisguytalking_media.get(media_type):
            await interaction.response.send_message(t("messageimage.err.no_media_of_type_period", media_type=media_type), ephemeral=True)
            return False

        remaining = _consume_whatisthisguytalking_cooldown(
            cooldowns,
            interaction.user.id,
            cooldown_seconds,
        )
        if remaining > 0:
            await interaction.response.send_message(
                t("messageimage.err.media_cooldown", count=remaining, remaining=remaining, media_type=media_type),
                ephemeral=True,
            )
            return False

        async with self._media_lock:
            if button.disabled:
                await interaction.response.send_message(t("messageimage.err.button_already_used"), ephemeral=True)
                return False

            button.disabled = True
            button.style = discord.ButtonStyle.primary
            await interaction.response.edit_message(view=self)

        return True

    @discord.ui.button(emoji="🔄", style=discord.ButtonStyle.blurple)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.user and interaction.user.id != self.user.id:
            await interaction.followup.send(t("messageimage.err.not_original_requester_refresh"), ephemeral=True)
            return
        try:
            buffer = await _build_whatisthisguytalking_preview(self.screenshot_bytes)
            await interaction.edit_original_response(
                attachments=[discord.File(buffer, filename="whatisthisguytalking.png")],
            )
        except Exception as e:
            await interaction.followup.send(t("messageimage.err.regen_failed", error=str(e)), ephemeral=True)

    @discord.ui.button(label="GIF", style=discord.ButtonStyle.gray)
    async def render_gif(self, interaction: discord.Interaction, button: discord.ui.Button):
        allowed = await self._start_media_generation(
            interaction,
            button,
            "gif",
            whatisthisguytalking_gif_cooldowns,
            WHATTISTHISGUYTALKING_GIF_COOLDOWN_SECONDS,
        )
        if not allowed:
            return

        try:
            output_buffer = await generate_whatisthisguytalking_gif(self.screenshot_bytes)
            if output_buffer.getbuffer().nbytes > _get_interaction_upload_limit(interaction):
                await interaction.followup.send(t("messageimage.err.gif_exceeds_limit"), ephemeral=True)
                return

            await interaction.edit_original_response(
                attachments=[discord.File(output_buffer, filename="whatisthisguytalking.gif")],
            )
        except discord.HTTPException as e:
            await interaction.followup.send(t("messageimage.err.gif_generation_failed", error=str(e)), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(t("messageimage.err.gif_generation_failed", error=str(e)), ephemeral=True)

    @discord.ui.button(label=i18n.K("messageimage.btn.video"), style=discord.ButtonStyle.gray)
    async def render_video(self, interaction: discord.Interaction, button: discord.ui.Button):
        allowed = await self._start_media_generation(
            interaction,
            button,
            "video",
            whatisthisguytalking_video_cooldowns,
            WHATTISTHISGUYTALKING_VIDEO_COOLDOWN_SECONDS,
        )
        if not allowed:
            return

        try:
            output_buffer = await generate_whatisthisguytalking_video(self.screenshot_bytes)
            if output_buffer.getbuffer().nbytes > _get_interaction_upload_limit(interaction):
                await interaction.followup.send(t("messageimage.err.video_exceeds_limit"), ephemeral=True)
                return

            await interaction.edit_original_response(
                attachments=[discord.File(output_buffer, filename="whatisthisguytalking.mp4")],
            )
        except discord.HTTPException as e:
            await interaction.followup.send(t("messageimage.err.video_generation_failed", error=str(e)), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(t("messageimage.err.video_generation_failed", error=str(e)), ephemeral=True)


browser = None

async def setup_browser():
    global browser
    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.launch()
    except Exception as e:
        log(f"Failed to launch browser: {e}", module_name="MessageImage", level=logging.ERROR)
    log("Playwright browser started", module_name="MessageImage")
    while True:
        await asyncio.sleep(60)
        if not browser.is_connected():
            log("Playwright browser disconnected, restarting...", module_name="MessageImage", level=logging.WARNING)
            browser = await playwright.chromium.launch()
            log("Playwright browser restarted", module_name="MessageImage")

on_ready_tasks.append(setup_browser)

async def load_whatisthisguytalking_images():
    global whatisthisguytalking_media, whatisthisguytalking_images
    try:
        dir = "./whatisthisguytalking-images"
        os.makedirs(dir, exist_ok=True)
        whatisthisguytalking_media = {"static": [], "gif": [], "video": []}
        whatisthisguytalking_images = []
        count    = 0
        for file in os.listdir(dir):
            file_path = os.path.join(dir, file)
            media_type = _classify_whatisthisguytalking_file(file_path)
            if media_type is None:
                continue
            whatisthisguytalking_media[media_type].append(file_path)
            whatisthisguytalking_images.append(file_path)
            count += 1
        log(f"Loaded {count} \"whatisthisguytalking\" images", module_name="MessageImage")
        return count
    except Exception as e:
        log(f"Failed to load \"whatisthisguytalking\" images: {e}", module_name="MessageImage", level=logging.ERROR)
        return 0

on_ready_tasks.append(load_whatisthisguytalking_images)

# i18n: skip-start (owner-facing)
@bot.command(aliases=["rwi"])
@((OwnerTools.is_owner()) if "OwnerTools" in modules else commands.check(lambda ctx: False))
async def reload_whatisthisguytalking_images(ctx: commands.Context):
    count = await load_whatisthisguytalking_images()
    if count == 0:
        await ctx.reply("重新載入「這傢伙在說什麼呢？」的圖片失敗")
    else:
        await ctx.reply(f"重新載入「這傢伙在說什麼呢？」的圖片完成，載入了 {count} 張圖片")
# i18n: skip-end


if __name__ == "__main__":
    start_bot()
