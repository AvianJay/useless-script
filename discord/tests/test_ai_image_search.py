import base64
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import (
    AICommands,
    AIResponseBuilder,
    normalize_ai_fetch_proxy,
    redact_ai_fetch_proxy,
)


class AIImageSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())

    def test_proxy_validation_accepts_typo_and_redacts_credentials(self):
        normalized, error = normalize_ai_fetch_proxy("sock5://user:secret@127.0.0.1:1080")

        self.assertIsNone(error)
        self.assertEqual(normalized, "socks5://user:secret@127.0.0.1:1080")
        redacted = redact_ai_fetch_proxy(normalized)
        self.assertEqual(redacted, "socks5://***@127.0.0.1:1080")
        self.assertNotIn("user", redacted)
        self.assertNotIn("secret", redacted)

    def test_proxy_validation_rejects_non_socks_and_missing_port(self):
        self.assertIsNotNone(normalize_ai_fetch_proxy("http://proxy.example:8080")[1])
        self.assertIsNotNone(normalize_ai_fetch_proxy("socks5://proxy.example")[1])

    async def test_proxy_command_never_echoes_credentials(self):
        ctx = SimpleNamespace(send=AsyncMock())
        proxy_url = "socks5://user:secret@127.0.0.1:1080"

        with patch("ai._set_ai_fetch_proxy") as setter:
            await AICommands.ai_config_proxy_text.callback(
                self.cog,
                ctx,
                proxy_url=proxy_url,
            )

        setter.assert_called_once_with(proxy_url)
        response_text = ctx.send.await_args.args[0]
        self.assertNotIn("user", response_text)
        self.assertNotIn("secret", response_text)
        self.assertIn("socks5://***@127.0.0.1:1080", response_text)

    async def test_public_fetch_target_rejects_private_ip(self):
        error = await self.cog._validate_public_fetch_target("http://127.0.0.1/image.png")

        self.assertIn("non-public", error)

    async def test_image_fetch_rejects_private_redirect_before_second_request(self):
        class FakeResponse:
            status = 302
            headers = {"Location": "http://127.0.0.1/private.png"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

        class FakeSession:
            calls = 0

            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return FakeResponse()

        session = FakeSession()
        with (
            patch("ai.aiohttp.ClientSession", return_value=session),
            patch.object(self.cog, "_create_ai_fetch_connector", return_value=object()),
            patch.object(self.cog, "_validate_public_fetch_target", AsyncMock(return_value=None)),
        ):
            _, _, _, error = await self.cog._fetch_public_image_bytes(
                "https://example.com/public.png"
            )

        self.assertIn("redirect target", error)
        self.assertEqual(session.calls, 1)

    def test_external_image_is_reencoded_without_metadata(self):
        source = io.BytesIO()
        Image.new("RGB", (32, 24), (20, 40, 60)).save(
            source,
            format="PNG",
            pnginfo=None,
        )

        content, mime_type, extension, error = self.cog._reencode_external_image(source.getvalue())

        self.assertIsNone(error)
        self.assertEqual(mime_type, "image/jpeg")
        self.assertEqual(extension, "jpg")
        self.assertTrue(content.startswith(b"\xff\xd8"))

    async def test_image_search_queues_one_reviewed_attachment(self):
        serper_payload = {
            "images": [
                {
                    "title": "Example image",
                    "imageUrl": "https://images.example/photo.png",
                    "link": "https://example.com/source",
                    "domain": "example.com",
                }
            ]
        }
        tool_context = {}

        with (
            patch.object(self.cog, "_request_serper", AsyncMock(return_value=(serper_payload, None))),
            patch.object(
                self.cog,
                "_fetch_public_image_bytes",
                AsyncMock(return_value=(b"downloaded", "image/png", "https://images.example/photo.png", None)),
            ),
            patch.object(
                self.cog,
                "_reencode_external_image",
                return_value=(b"safe-image", "image/jpeg", "jpg", None),
            ),
            patch.object(
                self.cog,
                "_review_searched_image",
                AsyncMock(return_value={"approved": True, "human_review": False, "reason": "safe", "model": "review"}),
            ),
        ):
            result = await self.cog._tool_search_google_images(
                {"query": "example", "max_candidates": 3},
                tool_context,
            )

        self.assertTrue(result["attachment_url"].startswith("attachment://searched-image-"))
        self.assertEqual(result["source_url"], "https://example.com/source")
        self.assertEqual(len(tool_context["pending_image_attachments"]), 1)
        payload = tool_context["pending_image_attachments"][0]
        self.assertEqual(payload["kind"], "searched")
        self.assertEqual(payload["content"], b"safe-image")

    async def test_image_search_fails_closed_when_review_is_uncertain(self):
        serper_payload = {
            "images": [
                {
                    "title": f"Candidate {index}",
                    "imageUrl": f"https://images.example/{index}.png",
                    "link": "https://example.com/source",
                }
                for index in range(3)
            ]
        }
        tool_context = {}

        with (
            patch.object(self.cog, "_request_serper", AsyncMock(return_value=(serper_payload, None))),
            patch.object(
                self.cog,
                "_fetch_public_image_bytes",
                AsyncMock(return_value=(b"downloaded", "image/png", "https://images.example/photo.png", None)),
            ),
            patch.object(
                self.cog,
                "_reencode_external_image",
                return_value=(b"safe-image", "image/jpeg", "jpg", None),
            ),
            patch.object(
                self.cog,
                "_review_searched_image",
                AsyncMock(return_value={"approved": False, "human_review": True, "reason": "uncertain", "model": "review"}),
            ),
        ):
            result = await self.cog._tool_search_google_images(
                {"query": "example", "max_candidates": 3},
                tool_context,
            )

        self.assertIn("error", result)
        self.assertEqual(result["reviewed_candidates"], 3)
        self.assertNotIn("pending_image_attachments", tool_context)

    def test_attachment_image_tag_places_only_real_attachment(self):
        payloads = [{"filename": "searched.jpg", "content": b"image", "kind": "searched"}]
        items = AIResponseBuilder._iter_response_layout_items(
            "Above\n<attachment_image>attachment://searched.jpg</attachment_image>\nBelow",
            payloads,
        )

        image_refs = [value for item_type, value in items if item_type == "image"]
        self.assertEqual(image_refs, ["attachment://searched.jpg"])

    def test_attachment_image_tag_ignores_hallucinated_attachment(self):
        items = AIResponseBuilder._iter_response_layout_items(
            "<attachment_image>attachment://missing.jpg</attachment_image>",
            [],
        )

        self.assertFalse(any(item_type == "image" for item_type, _ in items))

    async def test_delivery_adds_missing_source_attribution(self):
        tool_context = {
            "pending_image_attachments": [
                {
                    "filename": "searched.jpg",
                    "content": b"image",
                    "kind": "searched",
                    "source_url": "https://example.com/source",
                }
            ]
        }

        _, display_text, _, attachments = await self.cog._prepare_ai_response_delivery(
            "Found one image.",
            tool_context,
        )

        self.assertIn("圖片來源：<https://example.com/source>", display_text)
        self.assertEqual(attachments[0]["kind"], "searched")

    def test_fetch_raw_is_exposed_with_larger_tool_budget(self):
        tool_names = {item["function"]["name"] for item in self.cog._build_ai_tools()}

        self.assertIn("fetch_raw", tool_names)
        self.assertEqual(
            self.cog._tool_result_max_length("fetch_raw"),
            self.cog.RAW_FETCH_TOOL_RESULT_MAX_LENGTH,
        )

    async def test_fetch_raw_rejects_private_url_without_request(self):
        with patch.object(self.cog, "_create_ai_fetch_connector") as connector:
            result = await self.cog._tool_fetch_raw(
                {"url": "http://127.0.0.1/private"},
                {},
            )

        self.assertIn("error", result)
        connector.assert_not_called()

    async def test_fetch_raw_returns_unmodified_text_and_metadata(self):
        class FakeContent:
            async def iter_chunked(self, _):
                yield b'{"ok": true, "source": "raw"}'

        class FakeResponse:
            status = 200
            headers = {"Content-Type": "application/json; charset=utf-8"}
            content = FakeContent()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

        class FakeSession:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def get(self, *_args, **_kwargs):
                return FakeResponse()

        with (
            patch("ai.aiohttp.ClientSession", FakeSession),
            patch.object(self.cog, "_create_ai_fetch_connector", return_value=object()),
            patch.object(self.cog, "_validate_public_fetch_target", AsyncMock(return_value=None)),
        ):
            result = await self.cog._tool_fetch_raw(
                {"url": "https://example.com/data.json", "max_chars": 1000},
                {},
            )

        self.assertEqual(result["status"], 200)
        self.assertEqual(result["content_type"], "application/json")
        self.assertEqual(result["charset"], "utf-8")
        self.assertEqual(result["content"], '{"ok": true, "source": "raw"}')
        self.assertFalse(result["truncated"])

    async def test_fetch_raw_rejects_binary_content_type(self):
        class FakeResponse:
            status = 200
            headers = {"Content-Type": "application/octet-stream"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

        class FakeSession:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def get(self, *_args, **_kwargs):
                return FakeResponse()

        with (
            patch("ai.aiohttp.ClientSession", FakeSession),
            patch.object(self.cog, "_create_ai_fetch_connector", return_value=object()),
            patch.object(self.cog, "_validate_public_fetch_target", AsyncMock(return_value=None)),
        ):
            result = await self.cog._tool_fetch_raw(
                {"url": "https://example.com/archive.bin"},
                {},
            )

        self.assertIn("textual content types", result["error"])

    def test_upload_html_is_exposed_and_forces_html_filename(self):
        tool_names = {item["function"]["name"] for item in self.cog._build_ai_tools()}

        self.assertIn("upload_html", tool_names)
        self.assertEqual(self.cog._sanitize_upload_html_name(None), "page.html")
        self.assertEqual(self.cog._sanitize_upload_html_name("../My Demo.txt"), "My-Demo.html")
        self.assertEqual(self.cog._sanitize_upload_html_name("chart.html"), "chart.html")

    async def test_upload_html_rejects_empty_and_oversized_without_request(self):
        with patch("ai.aiohttp.ClientSession") as session:
            empty = await self.cog._tool_upload_html({"html": "  "}, {})
            with patch.object(self.cog, "UPLOAD_HTML_MAX_BYTES", 10):
                oversized = await self.cog._tool_upload_html({"html": "<p>too large</p>"}, {})

        self.assertIn("required", empty["error"])
        self.assertIn("too large", oversized["error"])
        session.assert_not_called()

    async def test_upload_html_posts_base64_and_hides_edit_token(self):
        posted = {}

        class FakeContent:
            async def iter_chunked(self, _):
                yield b'{"url": "/p/abc123", "slug": "abc123", "editToken": "secret-token"}'

        class FakeResponse:
            status = 200
            content = FakeContent()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

        class FakeSession:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def post(self, url, **kwargs):
                posted["url"] = url
                posted["json"] = kwargs.get("json")
                return FakeResponse()

        tool_context = {}
        html_content = "<!doctype html><title>測試</title>"
        with patch("ai.aiohttp.ClientSession", FakeSession):
            result = await self.cog._tool_upload_html(
                {"html": html_content, "filename": "demo"},
                tool_context,
            )

        self.assertEqual(posted["url"], "https://sharemyhtml.com/api/upload")
        self.assertEqual(posted["json"]["fileName"], "demo.html")
        self.assertEqual(
            base64.b64decode(posted["json"]["contentBase64"]).decode("utf-8"),
            html_content,
        )
        self.assertEqual(result["url"], "https://sharemyhtml.com/p/abc123")
        self.assertNotIn("secret-token", str(result))
        self.assertEqual(self.cog._pop_uploaded_html_chars(tool_context), len(html_content))
        self.assertEqual(self.cog._pop_uploaded_html_chars(tool_context), 0)

    async def test_upload_html_reports_rate_limit_without_billing(self):
        class FakeResponse:
            status = 429

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

        class FakeSession:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def post(self, *_args, **_kwargs):
                return FakeResponse()

        tool_context = {}
        with patch("ai.aiohttp.ClientSession", FakeSession):
            result = await self.cog._tool_upload_html({"html": "<p>hi</p>"}, tool_context)

        self.assertIn("rate limiting", result["error"])
        self.assertEqual(self.cog._pop_uploaded_html_chars(tool_context), 0)


if __name__ == "__main__":
    unittest.main()
