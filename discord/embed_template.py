from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from urllib.parse import urlparse

import discord


class EmbedTemplateSyntaxError(ValueError):
    pass


EMBED_DIRECTIVES = {
    "embedtitle:": "title",
    "embeddescription:": "description",
    "embedurl:": "url",
    "embedimage:": "image",
    "embedcolor:": "color",
    "embedthumbnail:": "thumbnail",
    "embedfooter:": "footer",
    "embedfooterimage:": "footer_image",
    "embedauthor:": "author",
    "embedauthorurl:": "author_url",
    "embedauthorimage:": "author_image",
    "embedtime:": "time",
    "embedfield:": "field",
}


def find_matching_brace(value: str, start_index: int) -> int:
    depth = 0
    for index in range(start_index, len(value)):
        if value[index] == "{":
            depth += 1
        elif value[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def split_top_level(value: str, separator: str = ":") -> tuple[str, str | None]:
    depth = 0
    for index, char in enumerate(value):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == separator and depth == 0:
            return value[:index], value[index + 1 :]
    return value, None


def validate_embed_template(template: str, allowed_variables: set[str]) -> None:
    def validate_segment(segment: str) -> None:
        index = 0
        while index < len(segment):
            if segment[index] == "}":
                raise EmbedTemplateSyntaxError("出現未配對的 `}`。")
            if segment[index] != "{":
                index += 1
                continue

            closing_index = find_matching_brace(segment, index)
            if closing_index == -1:
                raise EmbedTemplateSyntaxError("出現未閉合的 `{`。")

            token = segment[index + 1 : closing_index]
            lowered = token.lower()
            matched_prefix = next(
                (prefix for prefix in EMBED_DIRECTIVES if lowered.startswith(prefix)),
                None,
            )
            if matched_prefix is not None:
                payload = token[len(matched_prefix) :]
                if not payload:
                    raise EmbedTemplateSyntaxError(f"`{matched_prefix[:-1]}` 內容不得為空。")
                if matched_prefix == "embedfield:":
                    field_name, field_value = split_top_level(payload)
                    if field_value is None or not field_name or not field_value:
                        raise EmbedTemplateSyntaxError(
                            "Embed 欄位格式必須是 `{embedfield:欄位名:欄位內容}`。"
                        )
                    validate_segment(field_name)
                    validate_segment(field_value)
                else:
                    validate_segment(payload)
            elif token not in allowed_variables:
                raise EmbedTemplateSyntaxError(f"不支援的模板變數 `{{{token}}}`。")
            index = closing_index + 1

    validate_segment(template)


def extract_embed_tokens(template: str) -> tuple[str, dict]:
    extracted = {
        "title": None,
        "description": None,
        "url": None,
        "image": None,
        "color": None,
        "thumbnail": None,
        "footer": None,
        "footer_image": None,
        "author": None,
        "author_url": None,
        "author_image": None,
        "time": None,
        "fields": [],
    }
    output = []
    index = 0

    while index < len(template):
        if template[index] != "{":
            output.append(template[index])
            index += 1
            continue

        remaining = template[index + 1 :].lower()
        matched = next(
            ((prefix, key) for prefix, key in EMBED_DIRECTIVES.items() if remaining.startswith(prefix)),
            None,
        )
        if matched is None:
            output.append(template[index])
            index += 1
            continue

        prefix, key = matched
        value_start = index + 1 + len(prefix)
        closing_index = find_matching_brace(template, index)
        if closing_index == -1:
            output.append(template[index])
            index += 1
            continue

        payload = template[value_start:closing_index]
        if key == "field":
            field_name, field_value = split_top_level(payload)
            if field_value is not None:
                extracted["fields"].append((field_name, field_value))
        else:
            extracted[key] = payload
        index = closing_index + 1

    return "".join(output), extracted


def parse_embed_color(value: str) -> int | None:
    raw_value = str(value).strip().lower()
    if raw_value.startswith("#"):
        raw_value = raw_value[1:]
    elif raw_value.startswith("0x"):
        raw_value = raw_value[2:]
    if not raw_value:
        return None
    try:
        color_value = int(raw_value, 16)
    except ValueError:
        return None
    return color_value if 0 <= color_value <= 0xFFFFFF else None


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


async def build_embed_from_tokens(
    extracted: dict,
    resolver: Callable[[str], Awaitable[str]],
    *,
    now: datetime,
) -> discord.Embed | None:
    embed_requested = any(
        extracted[key] is not None
        for key in (
            "title",
            "description",
            "url",
            "image",
            "color",
            "thumbnail",
            "footer",
            "footer_image",
            "author",
            "author_url",
            "author_image",
            "time",
        )
    ) or bool(extracted["fields"])
    if not embed_requested:
        return None

    embed = discord.Embed()
    if extracted["title"] is not None:
        title = (await resolver(extracted["title"])).strip()
        if title:
            embed.title = title
    if extracted["description"] is not None:
        description = (await resolver(extracted["description"])).strip()
        if description:
            embed.description = description
    if extracted["url"] is not None:
        url = (await resolver(extracted["url"])).strip()
        if url:
            embed.url = url
    if extracted["image"] is not None:
        image_url = (await resolver(extracted["image"])).strip()
        if image_url:
            embed.set_image(url=image_url)
    if extracted["thumbnail"] is not None:
        thumbnail_url = (await resolver(extracted["thumbnail"])).strip()
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

    footer_text = (
        (await resolver(extracted["footer"])).strip()
        if extracted["footer"] is not None
        else ""
    )
    footer_image = (
        (await resolver(extracted["footer_image"])).strip()
        if extracted["footer_image"] is not None
        else ""
    )
    if footer_text or footer_image:
        embed.set_footer(text=footer_text or "\u200b", icon_url=footer_image or None)

    author_name = (
        (await resolver(extracted["author"])).strip()
        if extracted["author"] is not None
        else ""
    )
    author_url = (
        (await resolver(extracted["author_url"])).strip()
        if extracted["author_url"] is not None
        else ""
    )
    author_image = (
        (await resolver(extracted["author_image"])).strip()
        if extracted["author_image"] is not None
        else ""
    )
    if author_name or author_url or author_image:
        embed.set_author(
            name=author_name or "\u200b",
            url=author_url or None,
            icon_url=author_image or None,
        )

    if extracted["color"] is not None:
        color = parse_embed_color(await resolver(extracted["color"]))
        if color is not None:
            embed.color = discord.Colour(color)
    if extracted["time"] is not None and parse_bool(await resolver(extracted["time"])):
        embed.timestamp = now

    for field_name, field_value in extracted["fields"][:25]:
        resolved_name = (await resolver(field_name)).strip()
        resolved_value = (await resolver(field_value)).strip()
        if resolved_name and resolved_value:
            embed.add_field(name=resolved_name, value=resolved_value, inline=False)
    return embed


def _valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_embed_output(content: str | None, embed: discord.Embed | None) -> None:
    if content and len(content) > 2000:
        raise ValueError("公告的一般訊息內容超過 Discord 的 2000 字限制。")
    if embed is None:
        if not content:
            raise ValueError("公告模板必須產生一般文字或 Embed。")
        return

    data = embed.to_dict()
    if len(str(data.get("title", ""))) > 256:
        raise ValueError("Embed 標題超過 256 字。")
    if len(str(data.get("description", ""))) > 4096:
        raise ValueError("Embed 內容超過 4096 字。")
    fields = data.get("fields", [])
    if len(fields) > 25:
        raise ValueError("Embed 欄位超過 25 個。")
    for field in fields:
        if len(str(field.get("name", ""))) > 256:
            raise ValueError("Embed 欄位名稱超過 256 字。")
        if len(str(field.get("value", ""))) > 1024:
            raise ValueError("Embed 欄位內容超過 1024 字。")
    if len(str(data.get("footer", {}).get("text", ""))) > 2048:
        raise ValueError("Embed footer 超過 2048 字。")
    if len(str(data.get("author", {}).get("name", ""))) > 256:
        raise ValueError("Embed author 超過 256 字。")
    if len(embed) > 6000:
        raise ValueError("Embed 總文字長度超過 6000 字。")

    url_values = [
        data.get("url"),
        data.get("image", {}).get("url"),
        data.get("thumbnail", {}).get("url"),
        data.get("footer", {}).get("icon_url"),
        data.get("author", {}).get("url"),
        data.get("author", {}).get("icon_url"),
    ]
    if any(value and len(str(value)) > 2048 for value in url_values):
        raise ValueError("Embed 網址超過 Discord 的 2048 字限制。")
    if any(value and not _valid_http_url(str(value)) for value in url_values):
        raise ValueError("Embed 網址必須是有效的 http 或 https 網址。")
