import html
import io
import os
import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from importlib.util import find_spec
from itertools import count
from pathlib import Path
from uuid import uuid4

import emoji as emoji_lib
from markdown_it import MarkdownIt
from PIL import Image, ImageDraw, ImageFont

import ai_emoji


ASSET_DIR = Path(__file__).resolve().parent / "assets"
TABLE_BACKGROUND_COLOR = (43, 45, 49, 255)
TABLE_HEADER_COLOR = (57, 60, 67, 255)
TABLE_ROW_COLOR = (47, 49, 54, 255)
TABLE_ALT_ROW_COLOR = (51, 53, 59, 255)
TABLE_GRID_COLOR = (83, 86, 94, 255)
TABLE_TEXT_COLOR = (242, 243, 245, 255)
TABLE_MUTED_TEXT_COLOR = (181, 186, 193, 255)
TABLE_MENTION_TEXT_COLOR = (201, 205, 251, 255)
# 跟 Discord 深色主題一樣，提及與時間戳底下墊一層半透明色塊
TABLE_MENTION_FILL = (88, 101, 242, 77)
TABLE_TIMESTAMP_FILL = (78, 80, 88, 122)
MAX_TABLE_COLUMNS = 10
MAX_TABLE_ROWS = 30
MAX_TABLE_CELL_CHARS = 1000
MAX_TABLE_CELL_LINES = 5
MAX_TABLE_IMAGE_WIDTH = 2000
MAX_TABLE_IMAGE_HEIGHT = 2000
MIN_COLUMN_WIDTH = 100
MAX_COLUMN_WIDTH = 440
CELL_PADDING_X = 20
CELL_PADDING_Y = 14
EMOJI_SCALE = 1.25
EMOJI_GAP = 2
PILL_PADDING = 3
DISPLAY_TIMEZONE = timezone(timedelta(hours=8))
INLINE_MARKUP_PATTERN = re.compile(
    r"<(?P<animated>a?):(?P<emoji_name>[A-Za-z0-9_]{2,32}):(?P<emoji_id>\d{15,21})>"
    r"|<t:(?P<timestamp>-?\d{1,13})(?::(?P<style>[tTdDfFR]))?>"
    r"|<(?P<mention>@!?|@&|#)(?P<mention_id>\d{15,21})>"
    r"|</(?P<command>[^<>:\n]{1,100}):(?P<command_id>\d{15,21})>"
)
_WINDOWS_FONTS = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
# Noto Sans TC 只有繁中字形，簡中、日文新字體與韓文要靠系統字型；(內文, 標題粗體)，內文盡量挑細字重
SYSTEM_FALLBACK_FONTS = (
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Light.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc", "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", None),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", None),
    ("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", None),
    ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", None),
    (str(_WINDOWS_FONTS / "msyhl.ttc"), str(_WINDOWS_FONTS / "msyhbd.ttc")),
    (str(_WINDOWS_FONTS / "msyh.ttc"), str(_WINDOWS_FONTS / "msyhbd.ttc")),
    (str(_WINDOWS_FONTS / "YuGothL.ttc"), str(_WINDOWS_FONTS / "YuGothB.ttc")),
    (str(_WINDOWS_FONTS / "YuGothM.ttc"), str(_WINDOWS_FONTS / "YuGothB.ttc")),
    (str(_WINDOWS_FONTS / "malgun.ttf"), str(_WINDOWS_FONTS / "malgunbd.ttf")),
    (str(_WINDOWS_FONTS / "seguisym.ttf"), None),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", None),
    ("/System/Library/Fonts/AppleSDGothicNeo.ttc", None),
)
# FreeType 字型物件不能跨執行緒同時使用，表格都在 to_thread 裡畫
_RENDER_LOCK = threading.Lock()


@dataclass(frozen=True)
class MarkdownTable:
    start_line: int
    end_line: int
    headers: list[str]
    rows: list[list[str]]
    alignments: list[str]


@dataclass(slots=True)
class _Piece:
    """排版的最小單位：一個字、一張 emoji 圖，或提及底色兩側的留白"""
    text: str = ""
    font: object = None
    image: Image.Image | None = None
    width: float = 0.0
    color: tuple | None = None
    span: int = 0
    style: str | None = None

    @property
    def is_space(self) -> bool:
        return self.image is None and self.text.isspace()


@lru_cache(maxsize=1)
def _table_parser() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": True}).enable("table")


@lru_cache(maxsize=8)
def _load_font(size: int, bold: bool = False):
    filename = "notobold.ttf" if bold else "notolight.ttf"
    return ImageFont.truetype(str(ASSET_DIR / filename), size, layout_engine=ImageFont.Layout.BASIC)


def _plain_inline_text(token) -> str:
    children = token.children or []
    if not children:
        return html.unescape(str(token.content or "")).strip()

    parts: list[str] = []
    for child in children:
        if child.type in {"text", "code_inline"}:
            parts.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append("\n")
        elif child.type == "image":
            parts.append(child.content or child.attrGet("alt") or "[image]")
        elif child.type == "html_inline" and re.fullmatch(r"<br\s*/?>", child.content, re.IGNORECASE):
            parts.append("\n")
    return html.unescape("".join(parts)).strip()


def _alignment_from_token(token) -> str:
    style = str(token.attrGet("style") or "").lower()
    if "text-align:right" in style:
        return "right"
    if "text-align:center" in style:
        return "center"
    return "left"


def parse_markdown_tables(markdown_text: str) -> list[MarkdownTable]:
    text = str(markdown_text or "")
    tokens = _table_parser().parse(text)
    tables: list[MarkdownTable] = []
    index = 0

    while index < len(tokens):
        opening = tokens[index]
        if opening.type != "table_open" or opening.level != 0 or not opening.map:
            index += 1
            continue

        headers: list[str] = []
        rows: list[list[str]] = []
        alignments: list[str] = []
        current_row: list[str] | None = None
        current_row_alignments: list[str] = []
        current_alignment = "left"
        in_header = False
        end_index = index + 1

        while end_index < len(tokens):
            token = tokens[end_index]
            if token.type == "table_close" and token.level == opening.level:
                break
            if token.type == "thead_open":
                in_header = True
            elif token.type == "thead_close":
                in_header = False
            elif token.type == "tr_open":
                current_row = []
                current_row_alignments = []
            elif token.type in {"th_open", "td_open"}:
                current_alignment = _alignment_from_token(token)
            elif token.type == "inline" and current_row is not None:
                current_row.append(_plain_inline_text(token)[:MAX_TABLE_CELL_CHARS])
                current_row_alignments.append(current_alignment)
            elif token.type == "tr_close" and current_row is not None:
                if in_header and not headers:
                    headers = current_row
                    alignments = current_row_alignments
                else:
                    rows.append(current_row)
                current_row = None
            end_index += 1

        if headers and len(headers) <= MAX_TABLE_COLUMNS:
            column_count = len(headers)
            normalized_rows = [
                (row + [""] * column_count)[:column_count]
                for row in rows
            ]
            tables.append(
                MarkdownTable(
                    start_line=int(opening.map[0]),
                    end_line=int(opening.map[1]),
                    headers=headers,
                    rows=normalized_rows,
                    alignments=(alignments + ["left"] * column_count)[:column_count],
                )
            )
        index = end_index + 1

    return tables


def collect_table_markup(markdown_text: str) -> list[re.Match]:
    """表格裡要先在外面查好的 Discord 標記（自訂表情、時間戳、提及），依出現順序且不重複"""
    text = str(markdown_text or "")
    if "|" not in text or "<" not in text:
        return []
    found: dict[str, re.Match] = {}
    for table in parse_markdown_tables(text):
        for row in (table.headers, *table.rows[:MAX_TABLE_ROWS]):
            for cell in row:
                for match in INLINE_MARKUP_PATTERN.finditer(cell):
                    found.setdefault(match.group(0), match)
    return list(found.values())


def _matplotlib_font(filename: str) -> str | None:
    spec = find_spec("matplotlib")
    if spec is None or not spec.submodule_search_locations:
        return None
    return str(Path(spec.submodule_search_locations[0]) / "mpl-data" / "fonts" / "ttf" / filename)


@lru_cache(maxsize=None)
def _font_codepoints(path: str) -> frozenset[int]:
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(path, fontNumber=0, lazy=True)
        try:
            return frozenset(font.getBestCmap() or ())
        finally:
            font.close()
    except Exception:
        return frozenset()


def _fallback_font_paths(bold: bool) -> list[str]:
    # DejaVu／STIX 跟著 matplotlib 一起裝，補符號、拉丁擴充、希臘、西里爾、阿拉伯與數學字母
    candidates = [
        _matplotlib_font("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
        _matplotlib_font("STIXGeneralBol.ttf" if bold else "STIXGeneral.ttf"),
    ]
    for regular, bold_variant in SYSTEM_FALLBACK_FONTS:
        use_bold = bold and bold_variant and os.path.exists(bold_variant)
        candidates.append(bold_variant if use_bold else regular)
    return list(dict.fromkeys(path for path in candidates if path and os.path.exists(path)))


class _FontChain:
    """主字型缺字時依序往後找字型，每個字只查一次"""

    def __init__(self, size: int, bold: bool):
        self.size = size
        self.primary = _load_font(size, bold)
        primary_path = str(ASSET_DIR / ("notobold.ttf" if bold else "notolight.ttf"))
        self.paths = [primary_path, *_fallback_font_paths(bold)]
        self.fonts = {primary_path: self.primary}
        ascent, descent = self.primary.getmetrics()
        self.ascent = ascent
        self.text_height = ascent + descent
        self.line_height = self.text_height + 4
        self.emoji_size = round(size * EMOJI_SCALE)
        self._char_fonts: dict[str, object] = {}
        self._widths: dict[tuple[int, str], float] = {}

    def font_for(self, char: str):
        """有這個字的第一個字型；都沒有時回傳 None"""
        if char not in self._char_fonts:
            codepoint = ord(char)
            found = None
            for path in list(self.paths):
                if codepoint not in _font_codepoints(path):
                    continue
                try:
                    found = self._font(path)
                except OSError:
                    self.paths.remove(path)
                    continue
                break
            self._char_fonts[char] = found
        return self._char_fonts[char]

    def _font(self, path: str):
        if path not in self.fonts:
            self.fonts[path] = ImageFont.truetype(path, self.size, layout_engine=ImageFont.Layout.BASIC)
        return self.fonts[path]

    def width(self, font, char: str) -> float:
        key = (id(font), char)
        if key not in self._widths:
            self._widths[key] = font.getlength(char)
        return self._widths[key]


@lru_cache(maxsize=4)
def _font_chain(size: int, bold: bool) -> _FontChain:
    return _FontChain(size, bold)


def _decode_emoji_image(data: bytes | None, size: int) -> Image.Image | None:
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGBA")
    except Exception:
        return None
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tile.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
    return tile


class _InlineAssets:
    def __init__(self, emoji_images: dict[str, bytes] | None, labels: dict[str, str] | None):
        self.emoji_images = emoji_images or {}
        self.labels = labels or {}
        self._tiles: dict[tuple[str, int], Image.Image | None] = {}

    def emoji_tile(self, emoji_id: str, size: int) -> Image.Image | None:
        key = (emoji_id, size)
        if key not in self._tiles:
            self._tiles[key] = _decode_emoji_image(self.emoji_images.get(emoji_id), size)
        return self._tiles[key]


def _default_timestamp(timestamp: int, style: str) -> str:
    try:
        moment = datetime.fromtimestamp(timestamp, DISPLAY_TIMEZONE)
    except (OverflowError, OSError, ValueError):
        return str(timestamp)
    patterns = {"t": "%H:%M", "T": "%H:%M:%S", "d": "%Y/%m/%d", "D": "%Y/%m/%d"}
    return moment.strftime(patterns.get(style, "%Y/%m/%d %H:%M"))


def _markup_label(match: re.Match, labels: dict[str, str]) -> str:
    label = labels.get(match.group(0))
    if label:
        return label
    if match.group("timestamp") is not None:
        return _default_timestamp(int(match.group("timestamp")), match.group("style") or "f")
    mention = match.group("mention")
    if mention == "#":
        return "#unknown"
    if mention == "@&":
        return "@deleted-role"
    if mention:
        return "@unknown-user"
    return f"/{match.group('command')}"


def _is_invisible(char: str) -> bool:
    codepoint = ord(char)
    return (
        unicodedata.category(char) in ("Cc", "Cf")
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
    )


def _char_pieces(text: str, chain: _FontChain, color, span: int = 0, style: str | None = None) -> list[_Piece]:
    pieces = []
    for char in text:
        if _is_invisible(char):
            continue
        font = chain.font_for(char) or chain.primary
        pieces.append(_Piece(char, font, None, chain.width(font, char), color, span, style))
    return pieces


def _emoji_pieces(sequence: str, chain: _FontChain, color, span: int = 0, style: str | None = None) -> list[_Piece]:
    # ©、™、不帶 VS16 的 ⚠ 這類預設是文字樣式的字，有字型就照文字畫
    data = emoji_lib.EMOJI_DATA.get(sequence) or {}
    if len(sequence) == 1 and data.get("status") == emoji_lib.STATUS["unqualified"] and chain.font_for(sequence):
        return _char_pieces(sequence, chain, color, span, style)
    tile = ai_emoji.render_twemoji(sequence, chain.emoji_size)
    if tile is None:
        return _char_pieces(sequence, chain, color, span, style)
    return [_Piece(image=tile, width=tile.width + EMOJI_GAP, span=span, style=style)]


def _text_pieces(text: str, chain: _FontChain, color, span: int = 0, style: str | None = None) -> list[_Piece]:
    pieces = []
    cursor = 0
    for found in emoji_lib.emoji_list(text):
        pieces.extend(_char_pieces(text[cursor:found["match_start"]], chain, color, span, style))
        pieces.extend(_emoji_pieces(found["emoji"], chain, color, span, style))
        cursor = found["match_end"]
    pieces.extend(_char_pieces(text[cursor:], chain, color, span, style))
    return pieces


def _markup_pieces(match: re.Match, chain: _FontChain, assets: _InlineAssets, color, span: int) -> list[_Piece]:
    if match.group("emoji_id"):
        tile = assets.emoji_tile(match.group("emoji_id"), chain.emoji_size)
        if tile is not None:
            return [_Piece(image=tile, width=tile.width + EMOJI_GAP)]
        return _text_pieces(f":{match.group('emoji_name')}:", chain, color)

    style = "timestamp" if match.group("timestamp") is not None else "mention"
    if style == "mention":
        color = TABLE_MENTION_TEXT_COLOR
    label = _text_pieces(_markup_label(match, assets.labels), chain, color, span, style)
    return [
        _Piece(width=PILL_PADDING, span=span, style=style),
        *label,
        _Piece(width=PILL_PADDING, span=span, style=style),
    ]


def _cell_paragraphs(text: str, chain: _FontChain, assets: _InlineAssets, color) -> list[list[_Piece]]:
    value = unicodedata.normalize("NFC", str(text or "")).replace("\t", " ")
    spans = count(1)
    paragraphs = []
    for paragraph in value.split("\n"):
        paragraph = paragraph.strip()
        pieces: list[_Piece] = []
        cursor = 0
        for match in INLINE_MARKUP_PATTERN.finditer(paragraph):
            pieces.extend(_text_pieces(paragraph[cursor:match.start()], chain, color))
            pieces.extend(_markup_pieces(match, chain, assets, color, next(spans)))
            cursor = match.end()
        pieces.extend(_text_pieces(paragraph[cursor:], chain, color))
        paragraphs.append(pieces)
    return paragraphs


def _strip_trailing_spaces(pieces: list[_Piece]) -> list[_Piece]:
    end = len(pieces)
    while end and pieces[end - 1].is_space:
        end -= 1
    return pieces[:end]


def _line_width(pieces: list[_Piece]) -> float:
    return sum(piece.width for piece in _strip_trailing_spaces(pieces))


def _wrap_paragraph(pieces: list[_Piece], max_width: int) -> list[list[_Piece]]:
    lines: list[list[_Piece]] = []
    line: list[_Piece] = []
    width = 0.0
    for piece in pieces:
        if not line and piece.is_space:
            continue
        if line and not piece.is_space and width + piece.width > max_width:
            # 優先在最後一個空白換行，沒有空白（例如中文）就直接斷在這個字前面
            last_space = max((index for index, item in enumerate(line) if item.is_space), default=-1)
            if last_space > 0:
                lines.append(_strip_trailing_spaces(line[:last_space]))
                line = line[last_space + 1:]
            else:
                lines.append(_strip_trailing_spaces(line))
                line = []
            width = sum(item.width for item in line)
            if line and width + piece.width > max_width:
                lines.append(line)
                line = []
                width = 0.0
        line.append(piece)
        width += piece.width
    lines.append(_strip_trailing_spaces(line))
    return lines


def _fit_ellipsis(line: list[_Piece], chain: _FontChain, max_width: int, color) -> list[_Piece]:
    suffix = _char_pieces("...", chain, color)
    suffix_width = sum(piece.width for piece in suffix)
    trimmed = _strip_trailing_spaces(line)
    while trimmed and _line_width(trimmed) + suffix_width > max_width:
        trimmed = _strip_trailing_spaces(trimmed[:-1])
    return trimmed + suffix


def _wrap_cell(paragraphs: list[list[_Piece]], chain: _FontChain, max_width: int, color) -> list[list[_Piece]]:
    lines = [line for paragraph in paragraphs for line in _wrap_paragraph(paragraph, max_width)]
    if len(lines) > MAX_TABLE_CELL_LINES:
        lines = lines[:MAX_TABLE_CELL_LINES]
        lines[-1] = _fit_ellipsis(lines[-1], chain, max_width, color)
    return lines or [[]]


def _scale_column_widths(widths: list[int], available_width: int) -> list[int]:
    if sum(widths) <= available_width:
        return widths

    column_count = len(widths)
    minimum = min(MIN_COLUMN_WIDTH, max(60, available_width // column_count))
    remaining = max(0, available_width - minimum * column_count)
    extras = [max(0, width - minimum) for width in widths]
    total_extra = sum(extras)
    if not total_extra:
        scaled = [available_width // column_count] * column_count
    else:
        scaled = [minimum + round(remaining * extra / total_extra) for extra in extras]

    difference = available_width - sum(scaled)
    scaled[-1] += difference
    return scaled


def _blend(background, overlay) -> tuple[int, int, int, int]:
    alpha = overlay[3] / 255
    red, green, blue = (
        round(back * (1 - alpha) + front * alpha)
        for back, front in zip(background[:3], overlay[:3])
    )
    return red, green, blue, 255


def _draw_pieces(image: Image.Image, draw, pieces: list[_Piece], x: int, top: int, chain: _FontChain, background):
    spans: list[list] = []
    runs: list[list] = []
    cursor = float(x)
    for piece in pieces:
        if piece.span:
            if spans and spans[-1][0] == piece.span:
                spans[-1][3] = cursor + piece.width
            else:
                spans.append([piece.span, piece.style, cursor, cursor + piece.width])
        if piece.text:
            # 相鄰、同字型同顏色的字併成一段畫
            last = runs[-1] if runs else None
            if last and last[2] is piece.font and last[3] == piece.color and last[4] == cursor:
                last[1] += piece.text
                last[4] = cursor + piece.width
            else:
                runs.append([cursor, piece.text, piece.font, piece.color, cursor + piece.width])
        cursor += piece.width

    for _, style, start, end in spans:
        fill = _blend(background, TABLE_MENTION_FILL if style == "mention" else TABLE_TIMESTAMP_FILL)
        draw.rounded_rectangle((round(start), top, round(end) - 1, top + chain.text_height - 1), radius=4, fill=fill)

    baseline = top + chain.ascent
    for start, text, font, color, _ in runs:
        draw.text((round(start), baseline), text, font=font, fill=color, anchor="ls")

    cursor = float(x)
    for piece in pieces:
        if piece.image is not None:
            offset = max(0, (chain.text_height - piece.image.height) // 2)
            image.alpha_composite(piece.image, (round(cursor + EMOJI_GAP / 2), top + offset))
        cursor += piece.width


def _render_table(table: MarkdownTable, assets: _InlineAssets) -> bytes:
    header_chain = _font_chain(24, True)
    body_chain = _font_chain(22, False)
    column_count = len(table.headers)
    header_cells = [
        _cell_paragraphs(cell, header_chain, assets, TABLE_TEXT_COLOR)
        for cell in table.headers
    ]
    body_rows = [
        [_cell_paragraphs(cell, body_chain, assets, TABLE_TEXT_COLOR) for cell in row]
        for row in table.rows[:MAX_TABLE_ROWS]
    ]

    natural_widths = []
    for column in range(column_count):
        widths = [_line_width(paragraph) for paragraph in header_cells[column]]
        widths.extend(_line_width(paragraph) for row in body_rows for paragraph in row[column])
        natural_widths.append(
            min(MAX_COLUMN_WIDTH, max(MIN_COLUMN_WIDTH, round(max(widths, default=0)) + CELL_PADDING_X * 2))
        )

    column_widths = _scale_column_widths(natural_widths, MAX_TABLE_IMAGE_WIDTH - 4)

    def prepare(cells, chain, color=TABLE_TEXT_COLOR):
        wrapped = [
            _wrap_cell(paragraphs, chain, max(20, column_widths[index] - CELL_PADDING_X * 2), color)
            for index, paragraphs in enumerate(cells)
        ]
        height = max(len(lines) for lines in wrapped) * chain.line_height + CELL_PADDING_Y * 2
        return wrapped, height

    header_cells, header_height = prepare(header_cells, header_chain)
    visible_rows = []
    content_height = header_height
    for row in body_rows:
        cells, height = prepare(row, body_chain)
        if content_height + height > MAX_TABLE_IMAGE_HEIGHT - 70:
            break
        visible_rows.append((cells, height, False))
        content_height += height

    omitted_rows = max(0, len(table.rows) - len(visible_rows))
    if omitted_rows:
        note_cells = [_cell_paragraphs(f"... {omitted_rows} more rows", body_chain, assets, TABLE_MUTED_TEXT_COLOR)]
        note_cells.extend([[]] for _ in range(column_count - 1))
        note_row = prepare(note_cells, body_chain, TABLE_MUTED_TEXT_COLOR)
        if content_height + note_row[1] <= MAX_TABLE_IMAGE_HEIGHT:
            visible_rows.append((*note_row, True))
            content_height += note_row[1]

    image_width = sum(column_widths) + 2
    image_height = content_height + 2
    image = Image.new("RGBA", (image_width, image_height), TABLE_BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)

    def draw_row(cells, top, height, chain, background):
        draw.rectangle((1, top, image_width - 2, top + height), fill=background)
        left = 1
        for column, lines in enumerate(cells):
            width = column_widths[column]
            line_top = top + max(CELL_PADDING_Y, (height - len(lines) * chain.line_height) // 2)
            alignment = table.alignments[column]
            for line in lines:
                line_width = round(_line_width(line))
                if alignment == "right":
                    text_x = left + width - CELL_PADDING_X - line_width
                elif alignment == "center":
                    text_x = left + (width - line_width) // 2
                else:
                    text_x = left + CELL_PADDING_X
                _draw_pieces(image, draw, line, text_x, line_top, chain, background)
                line_top += chain.line_height
            left += width

    current_y = 1
    draw_row(header_cells, current_y, header_height, header_chain, TABLE_HEADER_COLOR)
    current_y += header_height
    for index, (cells, height, _) in enumerate(visible_rows):
        background = TABLE_ALT_ROW_COLOR if index % 2 else TABLE_ROW_COLOR
        draw_row(cells, current_y, height, body_chain, background)
        current_y += height

    x = 1
    draw.line((1, 1, image_width - 1, 1), fill=TABLE_GRID_COLOR, width=2)
    draw.line((1, image_height - 1, image_width - 1, image_height - 1), fill=TABLE_GRID_COLOR, width=2)
    draw.line((1, 1, 1, image_height - 1), fill=TABLE_GRID_COLOR, width=2)
    for width in column_widths:
        x += width
        draw.line((x, 1, x, image_height - 1), fill=TABLE_GRID_COLOR, width=2)
    y = 1 + header_height
    draw.line((1, y, image_width - 1, y), fill=TABLE_GRID_COLOR, width=2)
    for _, height, _ in visible_rows:
        y += height
        draw.line((1, y, image_width - 1, y), fill=TABLE_GRID_COLOR, width=2)

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_table_png(
    table: MarkdownTable,
    *,
    emoji_images: dict[str, bytes] | None = None,
    markup_labels: dict[str, str] | None = None,
) -> bytes:
    """emoji_images：自訂表情 ID -> 圖片 bytes；markup_labels：原始 Discord 標記 -> 顯示文字。沒給的用預設文字"""
    with _RENDER_LOCK:
        return _render_table(table, _InlineAssets(emoji_images, markup_labels))


def render_markdown_tables(
    markdown_text: str,
    *,
    max_images: int = 4,
    emoji_images: dict[str, bytes] | None = None,
    markup_labels: dict[str, str] | None = None,
) -> tuple[str, list[dict]]:
    text = str(markdown_text or "")
    if max_images <= 0 or "|" not in text:
        return text, []

    tables = parse_markdown_tables(text)
    if not tables:
        return text, []

    source_lines = text.splitlines(keepends=True)
    output_parts: list[str] = []
    attachments: list[dict] = []
    cursor = 0

    for table in tables:
        output_parts.append("".join(source_lines[cursor:table.start_line]))
        original = "".join(source_lines[table.start_line:table.end_line])
        replacement = original
        if len(attachments) < max_images:
            try:
                image_bytes = render_table_png(table, emoji_images=emoji_images, markup_labels=markup_labels)
            except Exception:
                pass
            else:
                filename = f"ai-table-{uuid4().hex[:12]}.png"
                attachments.append(
                    {
                        "filename": filename,
                        "content": image_bytes,
                        "size_bytes": len(image_bytes),
                        "kind": "table",
                        "table_source": original,
                    }
                )
                newline = "\n" if original.endswith(("\n", "\r")) else ""
                replacement = f"<generated_image>attachment://{filename}</generated_image>{newline}"
        output_parts.append(replacement)
        cursor = table.end_line

    output_parts.append("".join(source_lines[cursor:]))
    return "".join(output_parts), attachments
