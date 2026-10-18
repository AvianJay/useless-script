"""AI 回覆裡的 emoji。

- resolve_shortcodes：把 AI 寫的 :名稱: 換成 Discord 會顯示的表情。伺服器自訂表情優先，其次是 Discord
  內建短碼（assets/discord_emoji_shortcodes.json，用 python -m tools.update_discord_emoji 重建），最後才找
  emoji 套件的 GitHub／CLDR 名稱。查不到的原樣保留，所以 00:54:23 這類時間不會被動到。
- render_twemoji：把 Unicode emoji 畫成 Twemoji 圖，給表格這類自己畫字的圖片用。
"""

import html
import io
import json
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import emoji as emoji_lib
from PIL import Image, ImageDraw, ImageFont


ASSET_DIR = Path(__file__).resolve().parent / "assets"
SHORTCODE_FILE = ASSET_DIR / "discord_emoji_shortcodes.json"
TWEMOJI_FILE = ASSET_DIR / "twemoji.ttf"
VARIATION_SELECTOR = chr(0xFE0F)
SKIN_TONE_MODIFIERS = frozenset(chr(0x1F3FB + index) for index in range(5))
FOREGROUND_COLOR = (242, 243, 245, 255)
PRIVATE_USE_START = 0xF0000

_CUSTOM_EMOJI = r"<a?:[A-Za-z0-9_]{2,32}:\d+>"
CODE_PATTERN = re.compile(r"```.*?(?:```|\Z)|`[^`\n]+`", re.DOTALL)
CUSTOM_EMOJI_PATTERN = re.compile(_CUSTOM_EMOJI)
BROKEN_NESTED_EMOJI_PATTERN = re.compile(r"<:?(<a?:[A-Za-z0-9_]{2,32}:\d+>):?\d*>")
EMOJI_WRAPPER_PATTERN = re.compile(r"<a?:([A-Za-z0-9_]{2,32}):>")
# 含冒號但不是短碼的東西：網址、已成形的自訂表情（含跳脫過的）、時間戳、斜線指令提及
KEEP_PATTERN = re.compile(
    rf"<?https?://[^\s<>]+>?|{_CUSTOM_EMOJI}|&lt;a?:[A-Za-z0-9_]{{2,32}}:\d+&gt;"
    r"|<t:-?\d+(?::[A-Za-z])?>|</[^<>\n]+:\d+>"
)
SHORTCODE_PATTERN = re.compile(r":([A-Za-z0-9_+\-]{1,64}):(?::skin-tone-([1-5]):)?")
TONE_SUFFIX_PATTERN = re.compile(r"(.+)_tone([1-5])")
_LAYOUT_TAGS = (
    "button|option|select|buttons|container|section|gallery|image|thumbnail"
    "|separator|file|generated_image|attachment_image"
)
# 版面標籤的屬性值裡出現自訂表情時要跳脫，否則 ai_layout 認不出整個標籤
LAYOUT_TAG_PATTERN = re.compile(
    rf"<(?:{_LAYOUT_TAGS})\b(?:[^<>\"']|\"(?:[^\"<>\n]|{_CUSTOM_EMOJI})*\"|'(?:[^'<>\n]|{_CUSTOM_EMOJI})*')*>",
    re.IGNORECASE,
)
_KEEP_MARK = chr(2)
KEEP_PLACEHOLDER_PATTERN = re.compile(_KEEP_MARK + r"(\d+)" + _KEEP_MARK)
_TWEMOJI_LOCK = threading.Lock()


@dataclass(frozen=True)
class _Shortcodes:
    emoji: dict[str, str]
    tones: dict[str, list[str]]


@dataclass(frozen=True)
class _TwemojiFont:
    data: bytes
    glyphs: dict[str, str]
    layers: dict[str, tuple[tuple[str, tuple[int, int, int, int] | None], ...]]
    baseline: float


@lru_cache(maxsize=1)
def _shortcodes() -> _Shortcodes:
    try:
        data = json.loads(SHORTCODE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return _Shortcodes(dict(data.get("emoji") or {}), dict(data.get("tones") or {}))


def _unicode_shortcode(name: str) -> str | None:
    value = _shortcodes().emoji.get(name)
    if value:
        return value
    converted = emoji_lib.emojize(f":{name}:", language="alias")
    return None if converted == f":{name}:" else converted


def shortcode_to_emoji(name: str, tone: int | None = None) -> str | None:
    """Discord 內建短碼轉 Unicode emoji；tone 是 1～5 的膚色，也接受 name_tone2 寫法"""
    if tone is None:
        suffix = TONE_SUFFIX_PATTERN.fullmatch(name)
        if suffix and _unicode_shortcode(name) is None:
            name, tone = suffix.group(1), int(suffix.group(2))
    base = _unicode_shortcode(name)
    if base is None or tone is None:
        return base
    variants = _shortcodes().tones.get(base)
    return variants[tone - 1] if variants else base


def _touches_ascii_word(text: str, index: int) -> bool:
    return 0 <= index < len(text) and text[index].isascii() and text[index].isalnum()


def _shortcode_replacement(text: str, match: re.Match, custom_emojis: dict[str, str]) -> str | None:
    name, tone = match.group(1), match.group(2)
    start, end = match.span()
    if start and text[start - 1] == "<":
        return None
    # :x:、:100: 這種短名稱夾在英數字中間時多半是時間、比例或位址，不轉
    if (len(name) <= 2 or name.isdigit()) and (
        _touches_ascii_word(text, start - 1) or _touches_ascii_word(text, end)
    ):
        return None
    if tone is None:
        custom = custom_emojis.get(name.lower())
        if custom:
            return custom
    return shortcode_to_emoji(name, int(tone) if tone else None)


def _replace_shortcodes(text: str, custom_emojis: dict[str, str], *, escape: bool) -> str:
    kept: list[str] = []

    def keep(match: re.Match) -> str:
        value = match.group(0)
        if escape and CUSTOM_EMOJI_PATTERN.fullmatch(value):
            value = html.escape(value)
        kept.append(value)
        return f"{_KEEP_MARK}{len(kept) - 1}{_KEEP_MARK}"

    protected = KEEP_PATTERN.sub(keep, text)
    protected = EMOJI_WRAPPER_PATTERN.sub(r":\1:", protected)

    parts: list[str] = []
    cursor = position = 0
    while (match := SHORTCODE_PATTERN.search(protected, position)) is not None:
        replacement = _shortcode_replacement(protected, match, custom_emojis)
        if replacement is None:
            # 結尾的冒號可能是下一個短碼的開頭，例如 10:30:fire:
            position = match.start() + 1
            continue
        parts.append(protected[cursor:match.start()])
        parts.append(html.escape(replacement) if escape else replacement)
        cursor = position = match.end()
    parts.append(protected[cursor:])
    return KEEP_PLACEHOLDER_PATTERN.sub(lambda match: kept[int(match.group(1))], "".join(parts))


def _map_outside(pattern: re.Pattern, text: str, outside, inside=None) -> str:
    parts: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        parts.append(outside(text[cursor:match.start()]))
        parts.append(inside(match.group(0)) if inside else match.group(0))
        cursor = match.end()
    parts.append(outside(text[cursor:]))
    return "".join(parts)


def resolve_shortcodes(text: str, custom_emojis: dict[str, str] | None = None) -> str:
    """把 AI 輸出的 :名稱: 換成表情；custom_emojis 是小寫名稱 -> <:name:id>，code 裡的內容不動"""
    value = str(text or "")
    if ":" not in value:
        return value

    # 修復常見壞格式，例如 <:<:emoji:1234>:1234> 或 <<:emoji:1234>1234>
    for _ in range(3):
        collapsed = BROKEN_NESTED_EMOJI_PATTERN.sub(r"\1", value)
        if collapsed == value:
            break
        value = collapsed

    custom = custom_emojis or {}

    def plain(segment: str) -> str:
        return _replace_shortcodes(segment, custom, escape=False)

    def tag(segment: str) -> str:
        return _replace_shortcodes(segment, custom, escape=True)

    return _map_outside(
        CODE_PATTERN,
        value,
        lambda segment: _map_outside(LAYOUT_TAG_PATTERN, segment, plain, tag),
    )


@lru_cache(maxsize=1)
def _twemoji_font() -> _TwemojiFont | None:
    """Twemoji Mozilla 是 COLR 字型，但 base glyph 的外框是空的，Pillow 量出來是 0 而畫不出東西。

    所以把每個圖層 glyph 對應到 PUA 碼位，畫的時候逐層畫遮罩再套調色盤顏色。
    """
    try:
        from fontTools.ttLib import TTFont

        source = TTFont(str(TWEMOJI_FILE))
        cmap = source.getBestCmap()
        codepoints: dict[str, int] = {}
        for codepoint, glyph in cmap.items():
            codepoints.setdefault(glyph, codepoint)
        glyphs = {chr(codepoint): glyph for codepoint, glyph in cmap.items()}
        for lookup in source["GSUB"].table.LookupList.Lookup:
            for subtable in lookup.SubTable:
                subtable = getattr(subtable, "ExtSubTable", subtable)
                if subtable.LookupType != 4:
                    continue
                for first, ligatures in subtable.ligatures.items():
                    for ligature in ligatures:
                        sequence = [codepoints.get(glyph) for glyph in (first, *ligature.Component)]
                        if None not in sequence:
                            glyphs.setdefault("".join(map(chr, sequence)), ligature.LigGlyph)

        palette = source["CPAL"].palettes[0]
        color_layers = source["COLR"].ColorLayers
        layer_names = sorted({record.name for records in color_layers.values() for record in records})
        private_use = {name: chr(PRIVATE_USE_START + index) for index, name in enumerate(layer_names)}
        layers = {}
        for base, records in color_layers.items():
            entries = []
            for record in records:
                color = None
                if record.colorID != 0xFFFF:
                    entry = palette[record.colorID]
                    color = (entry.red, entry.green, entry.blue, entry.alpha)
                entries.append((private_use[record.name], color))
            layers[base] = tuple(entries)
        baseline = source["OS/2"].sTypoAscender / source["head"].unitsPerEm
        source.close()

        # 另開一份只改 cmap，其他表原樣複製，存檔才不用重新編譯整個字型
        patched = TTFont(str(TWEMOJI_FILE))
        for table in patched["cmap"].tables:
            if table.isUnicode() and table.format == 12:
                table.cmap.update({ord(char): name for name, char in private_use.items()})
        buffer = io.BytesIO()
        patched.save(buffer)
        patched.close()
    except Exception:
        return None
    return _TwemojiFont(buffer.getvalue(), glyphs, layers, baseline)


@lru_cache(maxsize=8)
def _twemoji_pil_font(size: int):
    return ImageFont.truetype(io.BytesIO(_twemoji_font().data), size, layout_engine=ImageFont.Layout.BASIC)


def twemoji_glyph(sequence: str) -> str | None:
    """找出 emoji 序列對應的彩色 glyph；VS16 有無、膚色都試過，最後退回第一個字"""
    font = _twemoji_font()
    if font is None or not sequence:
        return None
    stripped = sequence.replace(VARIATION_SELECTOR, "")
    untoned = "".join(char for char in stripped if char not in SKIN_TONE_MODIFIERS)
    candidates = (
        sequence,
        stripped,
        stripped[:1] + VARIATION_SELECTOR + stripped[1:],
        untoned,
        stripped[:1],
    )
    for candidate in candidates:
        glyph = font.glyphs.get(candidate)
        if glyph in font.layers:
            return glyph
    return None


@lru_cache(maxsize=512)
def render_twemoji(sequence: str, size: int) -> Image.Image | None:
    """回傳 size×size 的 RGBA Twemoji；共用快取，呼叫端不要修改它"""
    glyph = twemoji_glyph(sequence)
    if glyph is None or size <= 0:
        return None
    font = _twemoji_font()
    with _TWEMOJI_LOCK:
        pil_font = _twemoji_pil_font(size)
        tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        baseline = round(size * font.baseline)
        for char, color in font.layers[glyph]:
            mask = Image.new("L", tile.size, 0)
            ImageDraw.Draw(mask).text((0, baseline), char, font=pil_font, fill=255, anchor="ls")
            red, green, blue, alpha = color or FOREGROUND_COLOR
            layer = Image.new("RGBA", tile.size, (red, green, blue, 0))
            layer.putalpha(mask if alpha == 255 else mask.point(lambda value: value * alpha // 255))
            tile.alpha_composite(layer)
    return tile
