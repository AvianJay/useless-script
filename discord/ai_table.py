import html
import io
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from markdown_it import MarkdownIt
from PIL import Image, ImageDraw, ImageFont


ASSET_DIR = Path(__file__).resolve().parent / "assets"
TABLE_BACKGROUND_COLOR = (43, 45, 49, 255)
TABLE_HEADER_COLOR = (57, 60, 67, 255)
TABLE_ROW_COLOR = (47, 49, 54, 255)
TABLE_ALT_ROW_COLOR = (51, 53, 59, 255)
TABLE_GRID_COLOR = (83, 86, 94, 255)
TABLE_TEXT_COLOR = (242, 243, 245, 255)
TABLE_MUTED_TEXT_COLOR = (181, 186, 193, 255)
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


@dataclass(frozen=True)
class MarkdownTable:
    start_line: int
    end_line: int
    headers: list[str]
    rows: list[list[str]]
    alignments: list[str]


@lru_cache(maxsize=1)
def _table_parser() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": True}).enable("table")


@lru_cache(maxsize=8)
def _load_font(size: int, bold: bool = False):
    filename = "notobold.ttf" if bold else "notolight.ttf"
    return ImageFont.truetype(str(ASSET_DIR / filename), size)


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


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    return round(draw.textlength(str(text or ""), font=font))


def _fit_ellipsis(draw, text: str, font, max_width: int) -> str:
    suffix = "..."
    candidate = str(text or "").rstrip()
    while candidate and _text_width(draw, candidate + suffix, font) > max_width:
        candidate = candidate[:-1].rstrip()
    return (candidate + suffix) if candidate else suffix


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    value = str(text or "")
    wrapped: list[str] = []

    for paragraph in value.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            wrapped.append("")
            continue

        start = 0
        while start < len(paragraph):
            low = start + 1
            high = len(paragraph)
            best = start + 1
            while low <= high:
                middle = (low + high) // 2
                if _text_width(draw, paragraph[start:middle], font) <= max_width:
                    best = middle
                    low = middle + 1
                else:
                    high = middle - 1

            split_at = best
            if split_at < len(paragraph):
                whitespace = paragraph.rfind(" ", start, split_at)
                if whitespace > start:
                    split_at = whitespace
            line = paragraph[start:split_at].strip()
            wrapped.append(line or paragraph[start:best])
            start = max(split_at, best if not line else split_at)
            while start < len(paragraph) and paragraph[start].isspace():
                start += 1

    if len(wrapped) > MAX_TABLE_CELL_LINES:
        wrapped = wrapped[:MAX_TABLE_CELL_LINES]
        wrapped[-1] = _fit_ellipsis(draw, wrapped[-1], font, max_width)
    return wrapped or [""]


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


def _prepare_render_rows(table: MarkdownTable, draw, header_font, body_font, column_widths):
    line_height = body_font.getmetrics()[0] + body_font.getmetrics()[1] + 4
    header_line_height = header_font.getmetrics()[0] + header_font.getmetrics()[1] + 4

    def prepare(cells, font, row_line_height):
        wrapped_cells = [
            _wrap_text(draw, cell, font, max(20, column_widths[index] - CELL_PADDING_X * 2))
            for index, cell in enumerate(cells)
        ]
        height = max(len(lines) for lines in wrapped_cells) * row_line_height + CELL_PADDING_Y * 2
        return wrapped_cells, height

    header = prepare(table.headers, header_font, header_line_height)
    prepared_rows = []
    for row in table.rows[:MAX_TABLE_ROWS]:
        prepared_rows.append(prepare(row, body_font, line_height))
    return header, prepared_rows, header_line_height, line_height


def render_table_png(table: MarkdownTable) -> bytes:
    header_font = _load_font(24, bold=True)
    body_font = _load_font(22, bold=False)
    measurement_image = Image.new("RGB", (1, 1))
    measurement_draw = ImageDraw.Draw(measurement_image)
    column_count = len(table.headers)
    sample_rows = table.rows[:MAX_TABLE_ROWS]

    natural_widths = []
    for column in range(column_count):
        widths = [_text_width(measurement_draw, table.headers[column], header_font)]
        widths.extend(_text_width(measurement_draw, row[column], body_font) for row in sample_rows)
        natural_widths.append(
            min(MAX_COLUMN_WIDTH, max(MIN_COLUMN_WIDTH, max(widths) + CELL_PADDING_X * 2))
        )

    column_widths = _scale_column_widths(natural_widths, MAX_TABLE_IMAGE_WIDTH - 4)
    header, prepared_rows, header_line_height, body_line_height = _prepare_render_rows(
        table,
        measurement_draw,
        header_font,
        body_font,
        column_widths,
    )

    header_cells, header_height = header
    visible_rows = []
    content_height = header_height
    for row in prepared_rows:
        if content_height + row[1] > MAX_TABLE_IMAGE_HEIGHT - 70:
            break
        visible_rows.append((*row, False))
        content_height += row[1]

    omitted_rows = max(0, len(table.rows) - len(visible_rows))
    if omitted_rows:
        note_cells = [f"... {omitted_rows} more rows"] + [""] * (column_count - 1)
        note_row = _prepare_render_rows(
            MarkdownTable(0, 0, note_cells, [], table.alignments),
            measurement_draw,
            body_font,
            body_font,
            column_widths,
        )[0]
        if content_height + note_row[1] <= MAX_TABLE_IMAGE_HEIGHT:
            visible_rows.append((*note_row, True))
            content_height += note_row[1]

    image_width = sum(column_widths) + 2
    image_height = content_height + 2
    image = Image.new("RGBA", (image_width, image_height), TABLE_BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)

    def draw_row(cells, top, height, font, line_height, background, muted=False):
        draw.rectangle((1, top, image_width - 2, top + height), fill=background)
        left = 1
        for column, lines in enumerate(cells):
            width = column_widths[column]
            block_height = len(lines) * line_height
            text_y = top + max(CELL_PADDING_Y, (height - block_height) // 2)
            alignment = table.alignments[column]
            for line in lines:
                line_width = _text_width(draw, line, font)
                if alignment == "right":
                    text_x = left + width - CELL_PADDING_X - line_width
                elif alignment == "center":
                    text_x = left + (width - line_width) // 2
                else:
                    text_x = left + CELL_PADDING_X
                draw.text(
                    (text_x, text_y),
                    line,
                    font=font,
                    fill=TABLE_MUTED_TEXT_COLOR if muted else TABLE_TEXT_COLOR,
                )
                text_y += line_height
            left += width

    current_y = 1
    draw_row(header_cells, current_y, header_height, header_font, header_line_height, TABLE_HEADER_COLOR)
    current_y += header_height
    for index, (cells, height, is_note) in enumerate(visible_rows):
        background = TABLE_ALT_ROW_COLOR if index % 2 else TABLE_ROW_COLOR
        draw_row(cells, current_y, height, body_font, body_line_height, background, muted=is_note)
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


def render_markdown_tables(markdown_text: str, *, max_images: int = 4) -> tuple[str, list[dict]]:
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
                image_bytes = render_table_png(table)
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
