from dataclasses import dataclass
from uuid import uuid4

from ai_math import (
    CODE_PATTERN,
    DISPLAY_MATH_PATTERN,
    MAX_MATH_EXPRESSION_LENGTH,
    render_math_png,
)
from ai_table import parse_markdown_tables, render_table_png


@dataclass(frozen=True)
class _RichMediaCandidate:
    start: int
    end: int
    kind: str
    source: str
    value: object


def _line_offsets(text: str) -> tuple[list[str], list[int]]:
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return lines, offsets


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < range_end and end > range_start for range_start, range_end in ranges)


def _table_candidates(text: str) -> list[_RichMediaCandidate]:
    lines, offsets = _line_offsets(text)
    candidates: list[_RichMediaCandidate] = []

    for table in parse_markdown_tables(text):
        start = offsets[min(table.start_line, len(lines))]
        end = offsets[min(table.end_line, len(lines))]
        candidates.append(
            _RichMediaCandidate(
                start=start,
                end=end,
                kind="table",
                source=text[start:end],
                value=table,
            )
        )
    return candidates


def _math_candidates(
    text: str,
    excluded_ranges: list[tuple[int, int]],
) -> list[_RichMediaCandidate]:
    code_ranges = [match.span() for match in CODE_PATTERN.finditer(text)]
    blocked_ranges = excluded_ranges + code_ranges
    candidates: list[_RichMediaCandidate] = []

    for match in DISPLAY_MATH_PATTERN.finditer(text):
        start, end = match.span()
        if _overlaps(start, end, blocked_ranges):
            continue
        candidates.append(
            _RichMediaCandidate(
                start=start,
                end=end,
                kind="math",
                source=match.group(0),
                value=str(match.group(1) or "").strip(),
            )
        )
    return candidates


def _table_trailing_newline(source: str) -> str:
    if source.endswith("\r\n"):
        return "\r\n"
    if source.endswith("\n"):
        return "\n"
    if source.endswith("\r"):
        return "\r"
    return ""


def render_rich_markdown_images(
    markdown_text: str,
    *,
    max_images: int,
    max_tables: int = 4,
    max_math: int = 6,
) -> tuple[str, list[dict]]:
    text = str(markdown_text or "")
    if max_images <= 0 or ("|" not in text and "$$" not in text):
        return text, []

    tables = _table_candidates(text) if "|" in text else []
    table_ranges = [(candidate.start, candidate.end) for candidate in tables]
    math = _math_candidates(text, table_ranges) if "$$" in text else []
    candidates = sorted((*tables, *math), key=lambda candidate: candidate.start)
    if not candidates:
        return text, []

    output_parts: list[str] = []
    attachments: list[dict] = []
    rendered_counts = {"table": 0, "math": 0}
    limits = {"table": max(0, max_tables), "math": max(0, max_math)}
    cursor = 0

    for candidate in candidates:
        output_parts.append(text[cursor:candidate.start])
        replacement = candidate.source

        if len(attachments) < max_images and rendered_counts[candidate.kind] < limits[candidate.kind]:
            try:
                if candidate.kind == "table":
                    image_bytes = render_table_png(candidate.value)
                    filename = f"ai-table-{uuid4().hex[:12]}.png"
                    source_key = "table_source"
                else:
                    expression = str(candidate.value or "")
                    if not 0 < len(expression) <= MAX_MATH_EXPRESSION_LENGTH:
                        raise ValueError("unsupported math expression length")
                    image_bytes = render_math_png(expression)
                    filename = f"ai-math-{uuid4().hex[:12]}.png"
                    source_key = "math_source"
            except Exception:
                pass
            else:
                rendered_counts[candidate.kind] += 1
                attachments.append(
                    {
                        "filename": filename,
                        "content": image_bytes,
                        "size_bytes": len(image_bytes),
                        "kind": candidate.kind,
                        source_key: candidate.source,
                    }
                )
                trailing_newline = _table_trailing_newline(candidate.source) if candidate.kind == "table" else ""
                replacement = f"<generated_image>attachment://{filename}</generated_image>{trailing_newline}"

        output_parts.append(replacement)
        cursor = candidate.end

    output_parts.append(text[cursor:])
    return "".join(output_parts), attachments
