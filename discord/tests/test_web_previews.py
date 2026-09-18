import asyncio
import json
import sys
import unittest
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image
import aiohttp
from aiohttp import web


DISCORD_DIR = Path(__file__).resolve().parents[1]
if str(DISCORD_DIR) not in sys.path:
    sys.path.insert(0, str(DISCORD_DIR))

import globalenv

_original_modules = list(globalenv.modules)
globalenv.modules[:] = ["Website"]
try:
    import Website
finally:
    globalenv.modules[:] = _original_modules


SITE = "https://bot.example.test"
DISCORDBOT = "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"
PAGES = {
    "/": ("index", ["invite", "/docs", "/panel/"]),
    "/docs": ("docs", ["/docs", "/docs#getting-started", "/"]),
    "/privacy-policy": ("privacy", ["/privacy-policy", "/terms-of-service", "/"]),
    "/terms-of-service": ("terms", ["/terms-of-service", "/privacy-policy", "/"]),
}


class PreviewHTML(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.metadata = {}
        self.scripts = []
        self.script_attributes = []
        self.in_preview = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            self.metadata.setdefault(attrs.get("property", attrs.get("name")), []).append(
                attrs.get("content"))
        if tag == "script" and attrs.get("id") == "discord:component-embed":
            self.in_preview = True
            self.script_attributes.append(attrs)
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_preview = False

    def handle_data(self, data):
        if self.in_preview:
            self.scripts[-1] += data


def _components(node):
    yield node
    for child in node.get("components", []):
        yield from _components(child)
    if "accessory" in node:
        yield from _components(node["accessory"])


class WebPreviewTests(unittest.TestCase):
    def setUp(self):
        self.bot = SimpleNamespace(
            user=SimpleNamespace(name="TestBot", id=123, avatar=None),
            application=SimpleNamespace(owner="test-owner"),
        )
        self.settings = {"website_url": SITE}
        for patcher in (
            patch.object(Website, "bot", self.bot),
            patch.object(Website, "config", side_effect=lambda key, default=None: self.settings.get(key, default)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        Website.app.config.update(TESTING=True)
        self.client = Website.app.test_client()

    def get_page(self, path):
        response = self.client.get(path, headers={"User-Agent": DISCORDBOT})
        self.assertEqual(response.status_code, 200)
        return PreviewHTML(response.get_data(as_text=True))

    def test_four_pages_in_three_languages_have_valid_initial_previews(self):
        for locale in ("zh-TW", "en", "ja"):
            for path, (page, targets) in PAGES.items():
                with self.subTest(locale=locale, page=page):
                    html = self.get_page(f"{path}?lang={locale}&auth_token=do-not-copy&source=test")
                    self.assertEqual(len(html.scripts), 1)
                    self.assertEqual(html.script_attributes[0]["type"], "application/json")
                    payload = json.loads(html.scripts[0])
                    self.assertEqual(set(payload), {"component"})
                    root = payload["component"]
                    self.assertEqual(root["type"], 17)
                    self.assertEqual(root["accent_color"], 0x5865F2)
                    nodes = list(_components(root))
                    self.assertLessEqual(len(nodes), 40)
                    self.assertEqual(sum(n["type"] == 17 for n in nodes), 1)
                    self.assertTrue(all(n["type"] in {1, 2, 9, 10, 11, 14, 17} for n in nodes))
                    self.assertEqual([n["type"] for n in root["components"]], [9, 10, 14, 1])
                    section = root["components"][0]
                    self.assertEqual(section["components"][0]["type"], 10)
                    self.assertEqual(section["accessory"]["media"], {"url": f"{SITE}/og-image.png"})
                    buttons = root["components"][-1]["components"]
                    self.assertEqual(len(buttons), 3)
                    for button, target in zip(buttons, targets):
                        self.assertEqual(set(button), {"type", "style", "label", "url"})
                        self.assertEqual(button["type"], 2)
                        self.assertEqual(button["style"], 5)
                        self.assertTrue(0 < len(button["label"]) <= 80)
                        self.assertLessEqual(len(button["url"]), 512)
                        if target == "invite":
                            self.assertEqual(button["url"], "https://discord.com/oauth2/authorize?client_id=123")
                        else:
                            target_path, _, fragment = target.partition("#")
                            expected = f"{SITE}{target_path}?lang={locale}"
                            if fragment:
                                expected += f"#{fragment}"
                            self.assertEqual(button["url"], expected)
                    with Website._i18n.use_locale(locale):
                        self.assertEqual(root["components"][1]["content"], Website._i18n.t(f"web.preview.{page}_summary"))
                    self.assertNotIn("web.preview.", html.scripts[0])
                    self.assertNotIn("do-not-copy", html.scripts[0])
                    self.assertNotIn("source=", html.scripts[0])
                    for key in ("description", "og:type", "og:title", "og:description", "og:url", "og:image", "og:site_name", "twitter:card", "twitter:title", "twitter:description", "twitter:image"):
                        self.assertEqual(len(html.metadata[key]), 1, key)
                        self.assertTrue(html.metadata[key][0], key)
                    self.assertEqual(html.metadata["og:url"], [f"{SITE}{path}?lang={locale}"])
                    self.assertEqual(html.metadata["og:image"], [f"{SITE}/og-image.png"])
                    self.assertEqual(html.metadata["twitter:title"], html.metadata["og:title"])
                    self.assertEqual(html.metadata["twitter:description"], html.metadata["og:description"])
                    self.assertNotIn("do-not-copy", json.dumps(html.metadata))

    def test_name_cannot_break_json_html_or_add_markdown_lines(self):
        self.bot.user.name = 'A "bot"\n**bold** [link](https://evil.test) `code` </script><script>alert(1)</script>'
        html = self.get_page("/?lang=en")
        self.assertEqual(len(html.scripts), 1)
        source = html.scripts[0]
        self.assertNotIn("</script>", source)
        payload = json.loads(source)
        heading = payload["component"]["components"][0]["components"][0]["content"]
        self.assertEqual(len(heading.splitlines()), 2)
        self.assertNotIn("**bold**", heading)
        self.assertNotIn("[link](https://evil.test)", heading)
        self.assertIn(r"\*\*bold\*\*", heading)
        self.assertIn('A "bot"', html.metadata["og:title"][0])

    def test_missing_or_invalid_site_omits_cv2_but_keeps_text_metadata(self):
        for site in ("", None, "http://bot.example.test", "//bot.example.test", "https://", "https://user:password@bot.example.test", "https://bot.example.test?token=private", "https://bot.example.test/#fragment", "https://bot.example.test:bad", "https://bad host.test", "https://bot.example.test\\evil"):
            self.settings["website_url"] = site
            for path in PAGES:
                with self.subTest(site=site, path=path):
                    html = self.get_page(path)
                    self.assertEqual(html.scripts, [])
                    self.assertTrue(html.metadata["og:title"][0])
                    self.assertTrue(html.metadata["og:description"][0])
                    self.assertNotIn("og:url", html.metadata)
                    self.assertNotIn("og:image", html.metadata)

    def test_base_trailing_slashes_and_optional_path_are_preserved(self):
        for site in (SITE + "/", SITE + "///", SITE + "/bot/"):
            with self.subTest(site=site):
                self.settings["website_url"] = site
                html = self.get_page("/docs?lang=ja")
                self.assertEqual(html.metadata["og:url"], [site.rstrip("/") + "/docs?lang=ja"])
                self.assertEqual(html.metadata["og:image"], [site.rstrip("/") + "/og-image.png"])

    def test_locale_follows_existing_header_session_and_query_precedence(self):
        response = self.client.get("/docs", headers={"Accept-Language": "ja"})
        self.assertEqual(PreviewHTML(response.get_data(as_text=True)).metadata["og:url"], [SITE + "/docs?lang=ja"])
        with self.client.session_transaction() as current_session:
            current_session["lang"] = "en"
        html = self.get_page("/docs")
        self.assertEqual(parse_qs(urlsplit(html.metadata["og:url"][0]).query), {"lang": ["en"]})
        html = self.get_page("/docs?lang=zh-TW")
        self.assertEqual(html.metadata["og:url"], [SITE + "/docs?lang=zh-TW"])

    def test_excessively_long_button_urls_disable_only_cv2(self):
        self.settings["website_url"] = SITE + "/" + "a" * 512
        html = self.get_page("/")
        self.assertEqual(html.scripts, [])
        self.assertTrue(html.metadata["og:title"][0])

    def test_non_public_templates_do_not_include_link_previews(self):
        for name in ("panel.html", "panel_guild.html", "panel_login.html", "ServerVerify.html", "contribute_feed_grass.html"):
            source = (DISCORD_DIR / "templates" / name).read_text(encoding="utf-8")
            self.assertNotIn("_link_preview.html", source)
            self.assertNotIn("discord:component-embed", source)


class PreviewImageTests(unittest.TestCase):
    def setUp(self):
        self.bot = SimpleNamespace(user=SimpleNamespace(avatar=SimpleNamespace(url="https://cdn.example.test/avatar.png")))
        for patcher in (
            patch.object(Website, "bot", self.bot),
            patch.object(Website, "AVATAR_PNG", None),
            patch.object(Website, "_avatar_png_url", None),
            patch.object(Website, "_avatar_png_retry_at", 0),
            patch.object(Website, "log"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        Website.app.config.update(TESTING=True)
        self.client = Website.app.test_client()

    def assert_png_response(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")
        self.assertTrue(response.cache_control.public)
        with Image.open(BytesIO(response.data)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (512, 512))

    def test_download_caches_success_in_memory(self):
        source = BytesIO()
        Image.new("RGB", (32, 32), "red").save(source, format="PNG")
        with patch.object(Website, "_download_og_avatar", new_callable=AsyncMock, return_value=source.getvalue()) as get:
            first = self.client.get("/og-image.png", headers={"User-Agent": DISCORDBOT})
            second = self.client.get("/og-image.png")
        self.assert_png_response(first)
        self.assertEqual(first.data, second.data)
        self.assertEqual(first.cache_control.max_age, 3600)
        get.assert_awaited_once_with(self.bot.user.avatar.url)
        with Image.open(BytesIO(first.data)) as image:
            self.assertEqual(image.getpixel((256, 256)), (255, 0, 0, 255))

    def test_no_avatar_and_bot_not_ready_use_local_fallback(self):
        with patch.object(Website, "_download_og_avatar", new_callable=AsyncMock) as get:
            self.bot.user.avatar = None
            response = self.client.get("/og-image.png")
            self.assert_png_response(response)
            self.bot.user = None
            self.assert_png_response(self.client.get("/og-image.png"))
        get.assert_not_called()

    def test_timeout_returns_png_and_waits_before_retrying(self):
        with patch.object(Website, "_download_og_avatar", new_callable=AsyncMock, side_effect=TimeoutError()) as get:
            first = self.client.get("/og-image.png")
            second = self.client.get("/og-image.png")
            self.assert_png_response(first)
            self.assertEqual(first.data, second.data)
            self.assertEqual(first.cache_control.max_age, 60)
            get.assert_called_once()
            Website._avatar_png_retry_at = 0
            self.assert_png_response(self.client.get("/og-image.png"))
            self.assertEqual(get.call_count, 2)

    def test_http_error_or_invalid_image_returns_png(self):
        for error in (None, aiohttp.ClientError()):
            with self.subTest(error=type(error).__name__):
                Website._avatar_png_retry_at = 0
                with patch.object(Website, "_download_og_avatar", new_callable=AsyncMock, return_value=b"not an image", side_effect=error):
                    self.assert_png_response(self.client.get("/og-image.png"))

    def test_changed_avatar_retries_even_during_failure_cooldown(self):
        with patch.object(Website, "_download_og_avatar", new_callable=AsyncMock, side_effect=TimeoutError()) as get:
            self.client.get("/og-image.png")
            self.bot.user.avatar.url = "https://cdn.example.test/new-avatar.png"
            self.assert_png_response(self.client.get("/og-image.png"))
            self.assertEqual(get.call_count, 2)

    def test_concurrent_download_does_not_block_other_crawlers(self):
        Website._avatar_png_lock.acquire()
        try:
            with patch.object(Website, "_download_og_avatar", new_callable=AsyncMock) as get:
                self.assert_png_response(self.client.get("/og-image.png"))
                get.assert_not_called()
        finally:
            Website._avatar_png_lock.release()


class AvatarDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def start_server(self, handler):
        app = web.Application()
        app.router.add_get("/avatar", handler)
        runner = web.AppRunner(app, shutdown_timeout=0.1)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return f"http://127.0.0.1:{runner.addresses[0][1]}/avatar"

    async def test_download_reads_response_and_rejects_http_error(self):
        async def handler(request):
            return web.Response(body=b"image bytes", status=int(request.query.get("status", "200")))

        url = await self.start_server(handler)
        self.assertEqual(await Website._download_og_avatar(url), b"image bytes")
        with self.assertRaises(aiohttp.ClientResponseError):
            await Website._download_og_avatar(url + "?status=404")

    async def test_total_timeout_covers_continuous_slow_stream(self):
        async def handler(request):
            response = web.StreamResponse()
            await response.prepare(request)
            try:
                while True:
                    await response.write(b".")
                    await asyncio.sleep(0.02)
            except ConnectionResetError:
                return response

        url = await self.start_server(handler)
        self.assertLess(Website._OG_IMAGE_TIMEOUT.total, 10)
        # No read timeout: continuous chunks can only be stopped by the total deadline.
        with patch.object(Website, "_OG_IMAGE_TIMEOUT", aiohttp.ClientTimeout(total=0.15)):
            with self.assertRaises(TimeoutError):
                await Website._download_og_avatar(url)

    async def test_oversized_avatar_is_rejected(self):
        async def handler(request):
            return web.Response(body=b"x" * (8 * 1024 * 1024 + 1))

        url = await self.start_server(handler)
        with self.assertRaises(ValueError):
            await Website._download_og_avatar(url)


if __name__ == "__main__":
    unittest.main()
