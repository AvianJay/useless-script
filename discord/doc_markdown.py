from __future__ import annotations

from html import escape
from pathlib import Path
import json
import re

try:
    import markdown as markdown_package
except ImportError:
    markdown_package = None


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^-\s+(.*)$")
_BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$")
_CODE_FENCE_RE = re.compile(r"^```([A-Za-z0-9_-]+)?\s*$")


def read_markdown_file(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def _render_inline(text: str) -> str:
    placeholders: list[str] = []

    def stash_code(match: re.Match) -> str:
        placeholders.append(match.group(1))
        return f"@@CODE{len(placeholders) - 1}@@"

    text = re.sub(r"`([^`]+)`", stash_code, text)
    text = escape(text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: f'<a href="{escape(match.group(2), quote=True)}">{match.group(1)}</a>',
        text,
    )
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)

    for index, code in enumerate(placeholders):
        text = text.replace(f"@@CODE{index}@@", f"<code>{escape(code)}</code>")
    return text


def _render_markdown_fallback(markdown_text: str) -> str:
    lines = str(markdown_text or "").splitlines()
    html_parts: list[str] = []
    paragraph_lines: list[str] = []
    list_items: list[str] = []
    quote_lines: list[str] = []
    code_lines: list[str] = []
    code_lang = ""
    in_code_block = False

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if paragraph_lines:
            html_parts.append(f"<p>{_render_inline(' '.join(paragraph_lines).strip())}</p>")
            paragraph_lines = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            html_parts.append("<ul>")
            html_parts.extend(f"<li>{item}</li>" for item in list_items)
            html_parts.append("</ul>")
            list_items = []

    def flush_quote() -> None:
        nonlocal quote_lines
        if quote_lines:
            content = " ".join(line.strip() for line in quote_lines if line.strip())
            if content:
                html_parts.append(f'<div class="doc-note"><p>{_render_inline(content)}</p></div>')
            quote_lines = []

    def flush_code() -> None:
        nonlocal code_lines, code_lang
        if code_lines:
            lang_attr = f' class="language-{escape(code_lang)}"' if code_lang else ""
            html_parts.append(f"<pre><code{lang_attr}>{escape(chr(10).join(code_lines))}</code></pre>")
            code_lines = []
            code_lang = ""

    def flush_all() -> None:
        flush_paragraph()
        flush_list()
        flush_quote()

    for raw_line in lines:
        line = raw_line.rstrip()

        fence_match = _CODE_FENCE_RE.match(line)
        if fence_match:
            if in_code_block:
                flush_code()
                in_code_block = False
            else:
                flush_all()
                in_code_block = True
                code_lang = fence_match.group(1) or ""
            continue

        if in_code_block:
            code_lines.append(raw_line)
            continue

        if not line.strip():
            flush_all()
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            flush_all()
            level = min(len(heading_match.group(1)), 6)
            heading_text = _render_inline(heading_match.group(2).strip())
            html_parts.append(f"<h{level}>{heading_text}</h{level}>")
            continue

        quote_match = _BLOCKQUOTE_RE.match(line)
        if quote_match:
            flush_paragraph()
            flush_list()
            quote_lines.append(quote_match.group(1))
            continue

        list_match = _LIST_RE.match(line)
        if list_match:
            flush_paragraph()
            flush_quote()
            list_items.append(_render_inline(list_match.group(1).strip()))
            continue

        paragraph_lines.append(line.strip())

    if in_code_block:
        flush_code()
    flush_all()
    return "\n".join(html_parts)


def render_markdown(markdown_text: str) -> str:
    if markdown_package is not None:
        return markdown_package.markdown(
            str(markdown_text or ""),
            extensions=[
                "fenced_code",
                "tables",
                "sane_lists",
                "nl2br",
            ],
        )
    return _render_markdown_fallback(markdown_text)


def load_markdown_documents(directory: str | Path) -> dict[str, str]:
    docs: dict[str, str] = {}
    docs_dir = Path(directory)
    if not docs_dir.exists():
        return docs
    for path in sorted(docs_dir.rglob("*.md")):
        docs[path.stem.lower()] = render_markdown(read_markdown_file(path))
    return docs


def load_docs_site(directory: str | Path) -> tuple[list[dict], list[dict]]:
    docs_dir = Path(directory)
    manifest_path = docs_dir / "manifest.json"
    sections_dir = docs_dir / "sections"
    if not manifest_path.exists():
        return [], []

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return [], []

    groups: list[dict] = []
    sections: list[dict] = []
    seen_section_ids: set[str] = set()

    for raw_group in manifest.get("groups", []):
        group_title = str(raw_group.get("title", "")).strip()
        raw_items = raw_group.get("items") or []
        group_items: list[dict] = []

        for raw_item in raw_items:
            section_id = str(raw_item.get("id", "")).strip()
            if not section_id:
                continue
            file_slug = str(raw_item.get("file") or section_id.lower()).strip()
            markdown_path = sections_dir / f"{file_slug}.md"
            if not markdown_path.exists():
                continue
            label = str(raw_item.get("label") or section_id).strip()
            html = render_markdown(read_markdown_file(markdown_path))

            item = {
                "id": section_id,
                "label": label,
                "file": file_slug,
            }
            group_items.append(item)
            if section_id not in seen_section_ids:
                sections.append(
                    {
                        "id": section_id,
                        "label": label,
                        "file": file_slug,
                        "html": html,
                    }
                )
                seen_section_ids.add(section_id)

        if group_title and group_items:
            groups.append({"title": group_title, "items": group_items})

    return groups, sections


def _strip_markdown_inline(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_markdown_search_entries(markdown_text: str, source: str) -> list[dict]:
    entries: list[dict] = []
    document_title = ""
    intro_lines: list[str] = []
    current_heading = ""
    current_lines: list[str] = []

    def flush_section() -> None:
        nonlocal current_heading, current_lines
        if not current_heading:
            current_lines = []
            return
        section_text = _strip_markdown_inline("\n".join(current_lines))
        if section_text:
            title = f"{document_title} / {current_heading}" if document_title else current_heading
            entries.append(
                {
                    "category": "docs",
                    "title": title,
                    "text": section_text,
                    "source": source,
                }
            )
        current_lines = []

    for raw_line in str(markdown_text or "").splitlines():
        line = raw_line.rstrip()
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            flush_section()
            level = len(heading_match.group(1))
            heading_text = _strip_markdown_inline(heading_match.group(2))
            if level == 1 and not document_title:
                document_title = heading_text
                continue
            current_heading = heading_text
            continue

        if current_heading:
            current_lines.append(re.sub(r"^>\s?", "", line))
        else:
            intro_lines.append(re.sub(r"^>\s?", "", line))

    flush_section()

    intro_text = _strip_markdown_inline("\n".join(intro_lines))
    if document_title:
        entries.insert(
            0,
            {
                "category": "module",
                "title": document_title,
                "text": intro_text,
                "source": source,
            },
        )
    return entries
