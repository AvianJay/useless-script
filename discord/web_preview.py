"""Server-rendered, read-only Discord link previews (not bot messages)."""

import re
from collections.abc import Callable
from urllib.parse import urlencode, urlsplit, urlunsplit


ACCENT_COLOR = 0x5865F2
_PAGES = {
    "index": ("/", "web.index.title", "web.index.hero_tagline"),
    "docs": ("/docs", "web.docs.title", "web.preview.docs_intro"),
    "privacy": ("/privacy-policy", "web.nav.privacy", "web.preview.privacy_intro"),
    "terms": ("/terms-of-service", "web.nav.terms", "web.preview.terms_intro"),
}


def _website_base(value: str) -> str:
    """Only use the configured HTTPS site; never copy request hosts or queries."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or re.search(r'[\s\x00-\x1f\x7f\\<>"\x27]', value):
        return ""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.query or parsed.fragment):
            return ""
        # Accessing port also validates malformed / out-of-range ports.
        _ = parsed.port
    except ValueError:
        return ""
    return urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _heading_text(value: str) -> str:
    # Keep a name on one line and prevent it from introducing Discord markdown.
    return re.sub(r"([\\`*_~|\[\]()<>#])", r"\\\1", " ".join(value.split()))


def build_link_preview(
    page: str, *, website_url: str, name: str, bot_id: int | None,
    locale: str, translate: Callable[[str], str],
) -> dict:
    """Build metadata and the component-embed payload for one public page."""
    path, title_key, intro_key = _PAGES[page]
    site_name = " ".join(name.split())
    page_title = translate(title_key)
    intro = translate(intro_key)
    summary = translate(f"web.preview.{page}_summary")
    base = _website_base(website_url)

    def site_link(target: str) -> str:
        target_path, _, fragment = target.partition("#")
        url = f"{base}{target_path}?{urlencode({'lang': locale})}"
        return f"{url}#{fragment}" if fragment else url

    preview = {
        "title": f"{site_name} | {page_title}",
        "description": " ".join(f"{intro} {summary}".split()),
        "site_name": site_name,
        "url": site_link(path) if base else "",
        "image_url": f"{base}/og-image.png" if base else "",
        "component_embed": None,
    }
    if not base:
        return preview

    if page == "index":
        links = [
            ("web.nav.docs", site_link("/docs")),
            ("web.nav.panel", site_link("/panel/")),
        ]
        if bot_id:
            links.insert(0, (
                "web.index.invite_btn",
                f"https://discord.com/oauth2/authorize?{urlencode({'client_id': str(bot_id)})}",
            ))
    elif page == "docs":
        links = [
            ("web.preview.read_docs", site_link("/docs")),
            ("web.preview.getting_started", site_link("/docs#getting-started")),
            ("web.preview.home", site_link("/")),
        ]
    elif page == "privacy":
        links = [
            ("web.preview.read_policy", site_link("/privacy-policy")),
            ("web.nav.terms", site_link("/terms-of-service")),
            ("web.preview.home", site_link("/")),
        ]
    else:
        links = [
            ("web.preview.read_terms", site_link("/terms-of-service")),
            ("web.nav.privacy", site_link("/privacy-policy")),
            ("web.preview.home", site_link("/")),
        ]

    # Buttons have a stricter URL limit than media. An unusable configured base
    # must not turn the entire preview into an invalid component embed.
    if any(len(url) > 512 for _, url in links):
        return preview
    heading = site_name if page == "index" else f"{site_name} | {page_title}"
    preview["component_embed"] = {
        "component": {
            "type": 17,
            "accent_color": ACCENT_COLOR,
            "components": [
                {
                    "type": 9,
                    "components": [{
                        "type": 10,
                        "content": f"## {_heading_text(heading)}\n{intro}",
                    }],
                    "accessory": {
                        "type": 11,
                        "media": {"url": preview["image_url"]},
                        "description": site_name,
                    },
                },
                {"type": 10, "content": summary},
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 1, "components": [
                    {"type": 2, "style": 5, "label": translate(key), "url": url}
                    for key, url in links
                ]},
            ],
        },
    }
    return preview
