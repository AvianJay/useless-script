"""把 AI 回覆裡的版面標記解析成 Discord Components V2 版面，並壓進 Discord 的上限。

這裡只處理純資料，實際建立 discord.ui 元件在 ai.py 的 AIResponseBuilder。
"""

import html
import re
from dataclasses import dataclass, field, replace
from typing import Callable
from urllib.parse import urlparse

import emoji as emoji_lib


MAX_COMPONENTS = 40
TEXT_BUDGET = 3950
MAX_MEDIA_COST = 9
THUMBNAIL_MEDIA_COST = 3
GALLERY_MAX_ITEMS = 10
ROW_MAX_BUTTONS = 5
FOLLOWUP_MAX_BUTTONS = 5
SELECT_MAX_OPTIONS = 25
BUTTON_LABEL_MAX = 80
OPTION_TEXT_MAX = 100
PLACEHOLDER_MAX = 150
MEDIA_DESCRIPTION_MAX = 256
LINK_URL_MAX = 512
DEFAULT_COLOR = 0x5865F2

NAMED_COLORS = {
    "blurple": 0x5865F2,
    "green": 0x57F287,
    "yellow": 0xFEE75C,
    "fuchsia": 0xEB459E,
    "pink": 0xEB459E,
    "red": 0xED4245,
    "orange": 0xE67E22,
    "gold": 0xF1C40F,
    "purple": 0x9B59B6,
    "blue": 0x3498DB,
    "teal": 0x1ABC9C,
    "grey": 0x95A5A6,
    "gray": 0x95A5A6,
    "white": 0xFFFFFF,
    "black": 0x000000,
}
BUTTON_STYLES = {
    "primary": "primary",
    "blurple": "primary",
    "secondary": "secondary",
    "grey": "secondary",
    "gray": "secondary",
    "success": "success",
    "green": "success",
    "danger": "danger",
    "red": "danger",
}

_BLOCK_TAGS = ("container", "section", "gallery", "buttons", "select")
_LEAF_TAGS = ("image", "thumbnail", "generated_image", "attachment_image", "file", "button", "option")
_KNOWN_TAGS = "|".join((*_BLOCK_TAGS, *_LEAF_TAGS, "separator"))
_MEDIA_TAGS = ("image", "thumbnail", "generated_image", "attachment_image")

# 自關閉形式要排在成對標籤前面，否則 <file/> 會跟後面的 </file> 配成一對
TOKEN_PATTERN = re.compile(
    r"(?P<hr>^[ \t]*---[ \t]*$)"
    r"|<(?P<sep>separator)\b(?P<sep_attrs>[^<>]*?)/?\s*>"
    r"|(?P<sep_close></separator\s*>)"
    r"|<(?P<self>file|generated_image|attachment_image|image|thumbnail)\b(?P<self_attrs>[^<>]*?)/\s*>"
    rf"|<(?P<leaf>{'|'.join(_LEAF_TAGS)})\b(?P<leaf_attrs>[^<>]*)>"
    rf"(?P<body>(?:(?!</?(?:{_KNOWN_TAGS})\b).)*?)</(?P=leaf)\s*>"
    rf"|<(?P<open>{'|'.join(_BLOCK_TAGS)})\b(?P<open_attrs>[^<>]*)>"
    rf"|</(?P<close>{'|'.join(_BLOCK_TAGS)})\s*>",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)
ATTR_PATTERN = re.compile(r"""([A-Za-z_][\w-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?""")
CODE_FENCE_PATTERN = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_PATTERN = re.compile(r"`[^`\n]+`")
CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:[A-Za-z0-9_]{2,32}:[0-9]{15,21}>")
_CODE_MARK = chr(1)
CODE_PLACEHOLDER_PATTERN = re.compile(_CODE_MARK + r"CODE(\d+)" + _CODE_MARK)


@dataclass
class Text:
    content: str


@dataclass
class Separator:
    visible: bool = True
    large: bool = False


@dataclass
class Media:
    ref: str
    description: str | None = None
    spoiler: bool = False


@dataclass
class Gallery:
    items: list[Media]


@dataclass
class Thumbnail:
    """沒有搭配文字的縮圖，沿用舊的 <thumbnail> 顯示方式"""
    media: Media


@dataclass
class Button:
    label: str
    url: str | None = None
    style: str = "secondary"
    emoji: str | None = None
    requester_only: bool = False
    index: int = 0

    @property
    def is_followup(self) -> bool:
        return self.url is None


@dataclass
class Section:
    text: str
    accessory: Media | Button


@dataclass
class ButtonRow:
    buttons: list[Button]


@dataclass
class Option:
    label: str
    description: str | None = None
    emoji: str | None = None


@dataclass
class SelectMenu:
    options: list[Option]
    placeholder: str | None = None
    requester_only: bool = False


@dataclass
class FileBlock:
    ref: str
    spoiler: bool = False


@dataclass
class Container:
    children: list
    color: int | None = None
    spoiler: bool = False


@dataclass
class Layout:
    nodes: list
    has_containers: bool = False
    omitted_media: int = 0
    media_cost: int = 0
    placed_attachment_refs: list[str] = field(default_factory=list)
    trimmed: bool = False


def _protect_code(text: str, fences: list[str]) -> str:
    def keep(match: re.Match) -> str:
        fences.append(match.group(0))
        return f"{_CODE_MARK}CODE{len(fences) - 1}{_CODE_MARK}"

    return INLINE_CODE_PATTERN.sub(keep, CODE_FENCE_PATTERN.sub(keep, text))


def _restore_code(text: str, fences: list[str]) -> str:
    return CODE_PLACEHOLDER_PATTERN.sub(lambda match: fences[int(match.group(1))], text)


def _parse_attrs(raw: str) -> dict:
    attrs = {}
    for match in ATTR_PATTERN.finditer(raw or ""):
        name = match.group(1).lower().replace("-", "_")
        value = next((group for group in match.group(2, 3, 4) if group is not None), None)
        attrs[name] = True if value is None else html.unescape(value)
    return attrs


def _flag(attrs: dict, name: str, default: bool = False) -> bool:
    value = attrs.get(name)
    if value is None:
        return default
    if value is True:
        return True
    return str(value).strip().lower() not in {"false", "0", "no", "off"}


def _one_line(value) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _valid_link(value) -> str | None:
    if not isinstance(value, str):
        return None
    url = html.unescape(value).strip().strip("<>")
    if not url or len(url) > LINK_URL_MAX or any(char.isspace() for char in url):
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return url


def _valid_emoji(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if CUSTOM_EMOJI_PATTERN.fullmatch(value) or emoji_lib.is_emoji(value):
        return value
    return None


def _parse_color(value) -> int | None:
    if not isinstance(value, str):
        return None
    raw = value.strip().lower()
    if raw in NAMED_COLORS:
        return NAMED_COLORS[raw]
    raw = raw.removeprefix("#").removeprefix("0x")
    if re.fullmatch(r"[0-9a-f]{3}", raw):
        raw = "".join(char * 2 for char in raw)
    if re.fullmatch(r"[0-9a-f]{6}", raw):
        return int(raw, 16)
    return None


class _LayoutParser:
    def __init__(
        self,
        attachments: list[dict] | None,
        file_name: str | None,
        normalize_image_url: Callable[[str], str | None],
        invalid_image_text: str,
        max_media_cost: float,
    ):
        payloads = [payload for payload in attachments or [] if isinstance(payload, dict)]
        self.attachment_refs: list[str] = []
        self.generated_refs: list[str] = []
        for payload in payloads:
            filename = str(payload.get("filename") or "").strip()
            if not filename:
                continue
            ref = f"attachment://{filename}"
            if ref not in self.attachment_refs:
                self.attachment_refs.append(ref)
            # 空的 <generated_image> 只放 AI 自己產生或瀏覽器截的圖，表格／數學圖要靠明確的 ref
            if str(payload.get("kind") or "generated") in {"generated", "browser"}:
                self.generated_refs.append(ref)
        self.file_ref = f"attachment://{file_name}" if file_name else None
        self.normalize_image_url = normalize_image_url
        self.invalid_image_text = invalid_image_text
        self.max_media_cost = max_media_cost
        self.media_cost = 0
        self.omitted_media = 0
        self.used_refs: list[str] = []
        self.generated_index = 0
        self.file_placed = False
        self.fences: list[str] = []
        self.root: list = []
        self.container: Container | None = None
        self.block: dict | None = None

    def parse(self, text: str) -> Layout:
        protected = _protect_code(str(text or ""), self.fences)
        cursor = 0
        for match in TOKEN_PATTERN.finditer(protected):
            self._add_text(protected[cursor:match.start()])
            self._handle(match)
            cursor = match.end()
        self._add_text(protected[cursor:])
        self._close_block()
        self._close_container()
        self._append_leftovers()

        nodes = _limit_followups(_merge_button_rows(self.root))
        return Layout(
            nodes=nodes,
            has_containers=any(isinstance(node, Container) for node in nodes),
            omitted_media=self.omitted_media,
            media_cost=self.media_cost,
            placed_attachment_refs=list(self.used_refs),
        )

    def _restore(self, text: str) -> str:
        return _restore_code(text, self.fences)

    def _emit(self, node):
        (self.container.children if self.container is not None else self.root).append(node)

    def _block_kind(self) -> str | None:
        return self.block["kind"] if self.block is not None else None

    def _add_text(self, raw: str):
        text = self._restore(raw)
        if not text.strip():
            return
        if self._block_kind() == "section":
            self.block["text"].append(text)
            return
        # gallery／buttons／select 裡散落的文字直接放在區塊前面
        self._emit(Text(text.strip("\n")))

    def _handle(self, match: re.Match):
        if match.group("hr") is not None:
            self._separator(visible=True, large=False)
            return
        if match.group("sep"):
            attrs = _parse_attrs(self._restore(match.group("sep_attrs")))
            self._separator(
                visible=_flag(attrs, "divider", True),
                large=str(attrs.get("spacing") or "").strip().lower() == "large",
            )
            return
        if match.group("sep_close") is not None:
            return
        if match.group("self"):
            name = match.group("self").lower()
            attrs = _parse_attrs(self._restore(match.group("self_attrs")))
            if name == "file":
                self._file("", attrs)
            else:
                self._media(name, "", attrs)
            return
        if match.group("leaf"):
            name = match.group("leaf").lower()
            attrs = _parse_attrs(self._restore(match.group("leaf_attrs")))
            body = self._restore(match.group("body"))
            if name == "button":
                self._button(body, attrs)
            elif name == "option":
                self._option(body, attrs)
            elif name == "file":
                self._file(body, attrs)
            else:
                self._media(name, body, attrs)
            return
        if match.group("open"):
            self._open(match.group("open").lower(), _parse_attrs(self._restore(match.group("open_attrs"))))
            return
        self._close(match.group("close").lower())

    def _separator(self, *, visible: bool, large: bool):
        if self.block is not None:
            return
        self._emit(Separator(visible=visible, large=large))

    def _file(self, body: str, attrs: dict):
        ref = body.strip()
        if not self.file_ref or self.file_placed:
            return
        if ref and ref not in (self.file_ref, self.file_ref.removeprefix("attachment://")):
            return
        self._close_block()
        self.file_placed = True
        self._emit(FileBlock(self.file_ref, spoiler=_flag(attrs, "spoiler")))

    def _resolve_media(self, name: str, body: str) -> tuple[str | None, bool]:
        """回傳 (ref, 是否為無效網址)"""
        raw = body.strip()
        if raw.startswith("attachment://") or name in ("generated_image", "attachment_image"):
            if raw in self.attachment_refs and raw not in self.used_refs:
                return raw, False
            if name != "generated_image":
                return None, False
            if raw:
                return self.normalize_image_url(raw), False
            while self.generated_index < len(self.generated_refs):
                candidate = self.generated_refs[self.generated_index]
                self.generated_index += 1
                if candidate not in self.used_refs:
                    return candidate, False
            return None, False
        if not raw:
            return None, False
        ref = self.normalize_image_url(raw)
        return ref, ref is None

    def _media(self, name: str, body: str, attrs: dict):
        ref, invalid = self._resolve_media(name, body)
        if invalid:
            if self.invalid_image_text:
                self._emit(Text(self.invalid_image_text))
            return
        if ref is None:
            return

        kind = self._block_kind()
        if kind == "section" and self.block["accessory"] is None:
            placement = "accessory"
        elif kind == "gallery" and len(self.block["items"]) < GALLERY_MAX_ITEMS:
            placement = "gallery"
        else:
            if kind in ("gallery", "buttons", "select"):
                self._close_block()
            placement = "thumbnail" if name == "thumbnail" else "image"

        cost = THUMBNAIL_MEDIA_COST if placement in ("accessory", "thumbnail") else 1
        if self.media_cost + cost > self.max_media_cost:
            self.omitted_media += 1
            return
        self.media_cost += cost
        if ref in self.attachment_refs:
            self.used_refs.append(ref)

        description = _clip(_one_line(attrs.get("alt") or attrs.get("description")), MEDIA_DESCRIPTION_MAX)
        media = Media(ref, description=description or None, spoiler=_flag(attrs, "spoiler"))
        if placement == "accessory":
            self.block["accessory"] = media
        elif placement == "gallery":
            self.block["items"].append(media)
        elif placement == "thumbnail":
            self._emit(Thumbnail(media))
        else:
            self._emit(Gallery([media]))

    def _button(self, body: str, attrs: dict):
        label = _clip(_one_line(body), BUTTON_LABEL_MAX)
        emoji = _valid_emoji(attrs.get("emoji"))
        url = None
        if "url" in attrs or "href" in attrs:
            url = _valid_link(attrs.get("url", attrs.get("href")))
            if url is None:
                return
            if not label and not emoji:
                return
        elif not label:
            # 追問按鈕送出的就是文字，沒有文字就沒意義
            return

        kind = self._block_kind()
        group_requester_only = bool(self.block["requester_only"]) if kind == "buttons" else False
        button = Button(
            label=label,
            url=url,
            style=BUTTON_STYLES.get(str(attrs.get("style") or "").strip().lower(), "secondary"),
            emoji=emoji,
            requester_only=_flag(attrs, "requester_only", group_requester_only),
        )
        if kind == "buttons":
            self.block["items"].append(button)
            return
        if kind == "section" and self.block["accessory"] is None:
            self.block["accessory"] = button
            return
        if kind in ("gallery", "select"):
            self._close_block()
        self._emit(ButtonRow([button]))

    def _option(self, body: str, attrs: dict):
        if self._block_kind() != "select" or len(self.block["items"]) >= SELECT_MAX_OPTIONS:
            return
        label = _clip(_one_line(body), OPTION_TEXT_MAX)
        if not label:
            return
        description = _clip(_one_line(attrs.get("description")), OPTION_TEXT_MAX)
        self.block["items"].append(
            Option(label, description=description or None, emoji=_valid_emoji(attrs.get("emoji")))
        )

    def _open(self, name: str, attrs: dict):
        if name == "container":
            if self.container is not None:
                return
            self._close_block()
            self.container = Container(
                [],
                color=_parse_color(attrs.get("color", attrs.get("colour"))),
                spoiler=_flag(attrs, "spoiler"),
            )
            return
        self._close_block()
        self.block = {
            "kind": name,
            "text": [],
            "accessory": None,
            "items": [],
            "requester_only": _flag(attrs, "requester_only"),
            "placeholder": _clip(_one_line(attrs.get("placeholder")), PLACEHOLDER_MAX) or None,
        }

    def _close(self, name: str):
        if name == "container":
            self._close_block()
            self._close_container()
        elif self._block_kind() == name:
            self._close_block()

    def _close_block(self):
        block, self.block = self.block, None
        if block is None:
            return
        kind = block["kind"]
        items = block["items"]
        if kind == "section":
            text = "".join(block["text"]).strip("\n")
            accessory = block["accessory"]
            if accessory is not None and text.strip():
                self._emit(Section(text, accessory))
                return
            if text.strip():
                self._emit(Text(text))
            if isinstance(accessory, Media):
                self._emit(Thumbnail(accessory))
            elif isinstance(accessory, Button):
                self._emit(ButtonRow([accessory]))
        elif kind == "gallery":
            for start in range(0, len(items), GALLERY_MAX_ITEMS):
                self._emit(Gallery(items[start:start + GALLERY_MAX_ITEMS]))
        elif kind == "buttons":
            for start in range(0, len(items), ROW_MAX_BUTTONS):
                self._emit(ButtonRow(items[start:start + ROW_MAX_BUTTONS]))
        elif kind == "select" and items:
            self._emit(SelectMenu(items, placeholder=block["placeholder"], requester_only=block["requester_only"]))

    def _close_container(self):
        container, self.container = self.container, None
        if container is not None and container.children:
            self.root.append(container)

    def _append_leftovers(self):
        if self.file_ref and not self.file_placed:
            self.file_placed = True
            self.root.append(FileBlock(self.file_ref))
        leftovers: list[Media] = []
        for ref in self.attachment_refs:
            if ref in self.used_refs:
                continue
            if self.media_cost + 1 > self.max_media_cost:
                self.omitted_media += 1
                continue
            self.media_cost += 1
            self.used_refs.append(ref)
            leftovers.append(Media(ref))
        for start in range(0, len(leftovers), GALLERY_MAX_ITEMS):
            self.root.append(Gallery(leftovers[start:start + GALLERY_MAX_ITEMS]))


def _merge_button_rows(nodes: list) -> list:
    merged: list = []
    for node in nodes:
        if isinstance(node, Container):
            node = replace(node, children=_merge_button_rows(node.children))
        previous = merged[-1] if merged else None
        if (
            isinstance(node, ButtonRow)
            and isinstance(previous, ButtonRow)
            and len(previous.buttons) + len(node.buttons) <= ROW_MAX_BUTTONS
        ):
            merged[-1] = ButtonRow(previous.buttons + node.buttons)
            continue
        merged.append(node)
    return merged


def _limit_followups(nodes: list) -> list:
    """每則回覆只留一組追問：第一個 select，或最先出現的最多 5 顆追問按鈕"""
    state = {"kind": None, "count": 0}

    def take_button(button: Button) -> bool:
        if state["kind"] not in (None, "buttons") or state["count"] >= FOLLOWUP_MAX_BUTTONS:
            return False
        state["kind"] = "buttons"
        button.index = state["count"]
        state["count"] += 1
        return True

    def walk(items: list) -> list:
        result: list = []
        for node in items:
            if isinstance(node, Container):
                children = walk(node.children)
                if children:
                    result.append(replace(node, children=children))
            elif isinstance(node, SelectMenu):
                if state["kind"] is None:
                    state["kind"] = "select"
                    result.append(node)
            elif isinstance(node, ButtonRow):
                buttons = [button for button in node.buttons if not button.is_followup or take_button(button)]
                if buttons:
                    result.append(ButtonRow(buttons))
            elif isinstance(node, Section) and isinstance(node.accessory, Button) and node.accessory.is_followup:
                result.append(node if take_button(node.accessory) else Text(node.text))
            else:
                result.append(node)
        return result

    return walk(nodes)


def parse_layout(
    text: str,
    *,
    normalize_image_url: Callable[[str], str | None],
    attachments: list[dict] | None = None,
    file_name: str | None = None,
    invalid_image_text: str = "",
    max_media_cost: float = MAX_MEDIA_COST,
) -> Layout:
    return _LayoutParser(
        attachments,
        file_name,
        normalize_image_url,
        invalid_image_text,
        max_media_cost,
    ).parse(text)


def count_components(nodes: list) -> int:
    """照 discord.py 的算法計數：Container／ActionRow 連同子元件，Section 是自己＋文字＋配件"""
    total = 0
    for node in nodes:
        if isinstance(node, Container):
            total += 1 + count_components(node.children)
        elif isinstance(node, (Section, Thumbnail)):
            total += 3
        elif isinstance(node, ButtonRow):
            total += 1 + len(node.buttons)
        elif isinstance(node, SelectMenu):
            total += 2
        else:
            total += 1
    return total


def text_length(nodes: list) -> int:
    total = 0
    for node in nodes:
        if isinstance(node, Container):
            total += text_length(node.children)
        elif isinstance(node, Text):
            total += len(node.content)
        elif isinstance(node, Section):
            total += len(node.text)
        elif isinstance(node, Thumbnail):
            total += 1
    return total


def _merge_texts(nodes: list) -> list:
    merged: list = []
    for node in nodes:
        if isinstance(node, Container):
            node = replace(node, children=_merge_texts(node.children))
        if isinstance(node, Text) and merged and isinstance(merged[-1], Text):
            merged[-1] = Text(f"{merged[-1].content}\n{node.content}")
            continue
        merged.append(node)
    return merged


def _drop_separators(nodes: list) -> list:
    result: list = []
    for node in nodes:
        if isinstance(node, Separator):
            continue
        if isinstance(node, Container):
            node = replace(node, children=_drop_separators(node.children))
        result.append(node)
    return result


def _thumbnails_to_galleries(nodes: list) -> list:
    result: list = []
    for node in nodes:
        if isinstance(node, Container):
            node = replace(node, children=_thumbnails_to_galleries(node.children))
        elif isinstance(node, Thumbnail):
            node = Gallery([node.media])
        previous = result[-1] if result else None
        if (
            isinstance(node, Gallery)
            and isinstance(previous, Gallery)
            and len(previous.items) + len(node.items) <= GALLERY_MAX_ITEMS
        ):
            result[-1] = Gallery(previous.items + node.items)
            continue
        result.append(node)
    return result


def _sections_to_text(nodes: list) -> list:
    result: list = []
    for node in nodes:
        if isinstance(node, Container):
            node = replace(node, children=_sections_to_text(node.children))
        elif isinstance(node, Section):
            node = Text(node.text)
        result.append(node)
    return result


def _drop_last(nodes: list) -> list:
    nodes = list(nodes)
    last = nodes[-1]
    if isinstance(last, Container) and len(last.children) > 1:
        nodes[-1] = replace(last, children=last.children[:-1])
    else:
        nodes.pop()
    return nodes


def _truncate_text(nodes: list, allowance: int) -> list:
    remaining = allowance

    def walk(items: list) -> list:
        nonlocal remaining
        result: list = []
        for node in items:
            if isinstance(node, Container):
                children = walk(node.children)
                if children:
                    result.append(replace(node, children=children))
                continue
            if isinstance(node, (Text, Section)):
                content = node.content if isinstance(node, Text) else node.text
                if len(content) <= remaining:
                    remaining -= len(content)
                    result.append(node)
                elif remaining > 1:
                    result.append(Text(content[: remaining - 1].rstrip() + "…"))
                    remaining = 0
                else:
                    remaining = 0
                continue
            if isinstance(node, Thumbnail):
                remaining = max(0, remaining - 1)
            result.append(node)
        return result

    return walk(nodes)


def fit_layout(
    layout: Layout,
    *,
    reserved_components: int = 0,
    reserved_text: int = 0,
    max_components: int = MAX_COMPONENTS,
    text_budget: int = TEXT_BUDGET,
) -> Layout:
    """依序退化直到符合元件數與字數上限：分隔線 → 縮圖併進圖庫 → section 變文字 → 從尾端刪"""
    nodes = _merge_texts(layout.nodes)
    component_budget = max(0, max_components - reserved_components)
    for degrade in (_drop_separators, _thumbnails_to_galleries, _sections_to_text):
        if count_components(nodes) <= component_budget:
            break
        nodes = _merge_texts(degrade(nodes))

    trimmed = False
    while nodes and count_components(nodes) > component_budget:
        nodes = _drop_last(nodes)
        trimmed = True

    text_allowance = max(0, text_budget - reserved_text)
    if text_length(nodes) > text_allowance:
        nodes = _truncate_text(nodes, text_allowance)
        trimmed = True

    layout.nodes = nodes
    layout.trimmed = layout.trimmed or trimmed
    return layout


def flatten_layout_items(nodes: list) -> list[tuple[str, object]]:
    """攤平成舊版 _iter_response_layout_items 的 (type, value) 格式"""
    items: list[tuple[str, object]] = []
    for node in nodes:
        if isinstance(node, Container):
            items.extend(flatten_layout_items(node.children))
        elif isinstance(node, Text):
            items.append(("text", [node.content]))
        elif isinstance(node, Separator):
            items.append(("separator", None))
        elif isinstance(node, Gallery):
            items.extend(("image", media.ref) for media in node.items)
        elif isinstance(node, Thumbnail):
            items.append(("thumbnail", node.media.ref))
        elif isinstance(node, Section):
            items.append(("text", [node.text]))
            if isinstance(node.accessory, Media):
                items.append(("thumbnail", node.accessory.ref))
    return items


def strip_layout_tags(
    text: str,
    *,
    keep_media: bool,
    normalize_image_url: Callable[[str], str | None] | None = None,
) -> str:
    """拿掉版面標記，保留內容。

    keep_media=True：給版面被 Discord 拒絕時重送用，留下圖片標籤，追問改成清單文字。
    keep_media=False：存進對話歷史，圖片轉成文字標記，追問改成模型看得懂的標記。
    """
    fences: list[str] = []
    protected = _protect_code(str(text or ""), fences)

    def replace_token(match: re.Match) -> str:
        if match.group("hr") is not None:
            return match.group(0)
        if match.group("sep"):
            return "\n---\n"
        if match.group("self"):
            name = match.group("self").lower()
            if keep_media and name in ("file", "generated_image"):
                return f"<{name}></{name}>"
            return ""
        if not match.group("leaf"):
            return ""

        name = match.group("leaf").lower()
        attrs = _parse_attrs(_restore_code(match.group("leaf_attrs"), fences))
        body = _restore_code(match.group("body"), fences)
        if name in ("button", "option"):
            label = _one_line(body)
            url = _valid_link(attrs.get("url", attrs.get("href"))) if name == "button" else None
            if url:
                return f"[{label or url}]({url})"
            if not label:
                return ""
            return f"\n• {label}" if keep_media else f"\n[follow-up: {label}]"
        if keep_media:
            return f"<{name}>{body.strip()}</{name}>"
        if name not in ("image", "thumbnail") or body.strip().startswith("attachment://"):
            return ""
        # i18n: skip-start (stored assistant history is replayed back to the model, not shown to users)
        label = "縮圖" if name == "thumbnail" else "圖片"
        image_url = normalize_image_url(body) if normalize_image_url else None
        return f"[{label}: {image_url}]" if image_url else f"[{label}: 已略過無效 URL]"
        # i18n: skip-end

    return _restore_code(TOKEN_PATTERN.sub(replace_token, protected), fences)


def visible_text_length(text: str) -> int:
    """去掉版面標記後的可見字數，按鈕與選項文字也算進去"""
    fences: list[str] = []
    protected = _protect_code(str(text or ""), fences)

    def replace_token(match: re.Match) -> str:
        if match.group("hr") is not None:
            return match.group(0)
        if match.group("leaf") and match.group("leaf").lower() in ("button", "option"):
            return _one_line(match.group("body"))
        return ""

    return len(_restore_code(TOKEN_PATTERN.sub(replace_token, protected), fences))
