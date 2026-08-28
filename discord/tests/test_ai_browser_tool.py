import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import (
    AICommands,
    derive_ai_browser_launch_endpoint,
    normalize_ai_browser_cdp_endpoint,
    redact_ai_browser_cdp_endpoint,
)


CDP_ENDPOINT = "http://avianjay-server:3000/api/profiles/1fec3e6d-71e2-4493-a09b-3c8b77ebe8b9/cdp"


def tool_context(user_id=1, guild_id=10, channel_id=20, request_text="browse"):
    return {
        "user": SimpleNamespace(id=user_id),
        "guild": SimpleNamespace(id=guild_id) if guild_id is not None else None,
        "channel": SimpleNamespace(id=channel_id),
        "request_text": request_text,
    }


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self, **_kwargs):
        return self.payload

    async def text(self):
        return json.dumps(self.payload)


class FakeManagerSession:
    def __init__(self, get_responses, post_response=None, capture=None, **_kwargs):
        self.get_responses = list(get_responses)
        self.post_response = post_response or FakeResponse(200, {})
        self.capture = capture if capture is not None else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def get(self, url, **kwargs):
        self.capture.setdefault("gets", []).append((url, kwargs))
        return self.get_responses.pop(0)

    def post(self, url, **kwargs):
        self.capture["post"] = (url, kwargs)
        return self.post_response


class FakeRoute:
    def __init__(self):
        self.continue_ = AsyncMock()
        self.abort = AsyncMock()


class BrowserConfigTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())

    def test_cdp_endpoint_validation_and_launch_derivation(self):
        normalized, error = normalize_ai_browser_cdp_endpoint("HTTP://" + CDP_ENDPOINT.removeprefix("http://"))
        self.assertIsNone(error)
        self.assertEqual(normalized, CDP_ENDPOINT)
        self.assertEqual(derive_ai_browser_launch_endpoint(CDP_ENDPOINT), CDP_ENDPOINT[:-3] + "launch")

    def test_cdp_endpoint_rejects_credentials_query_fragment_and_bad_profile_path(self):
        invalid = (
            "ftp://host/api/profiles/1fec3e6d-71e2-4493-a09b-3c8b77ebe8b9/cdp",
            "http://user:secret@host/api/profiles/1fec3e6d-71e2-4493-a09b-3c8b77ebe8b9/cdp",
            CDP_ENDPOINT + "?token=secret",
            CDP_ENDPOINT + "#fragment",
            "http://host/api/profiles/not-a-uuid/cdp",
            "http://host/api/profiles/1fec3e6d71e24493-a09b-3c8b77ebe8b9/cdp",
        )
        for value in invalid:
            with self.subTest(value=value):
                normalized, error = normalize_ai_browser_cdp_endpoint(value)
                self.assertIsNone(normalized)
                self.assertTrue(error)

    def test_status_redacts_profile_id(self):
        displayed = redact_ai_browser_cdp_endpoint(CDP_ENDPOINT)
        self.assertIn("avianjay-server:3000", displayed)
        self.assertIn("/api/profiles/***/cdp", displayed)
        self.assertNotIn("1fec3e6d", displayed)

    def test_browser_schemas_are_conditional(self):
        with patch("ai._get_ai_browser_cdp_endpoint", return_value=""):
            disabled = {item["function"]["name"] for item in self.cog._build_ai_tools()}
        with patch("ai._get_ai_browser_cdp_endpoint", return_value=CDP_ENDPOINT):
            enabled = {item["function"]["name"] for item in self.cog._build_ai_tools()}
        self.assertTrue({"browser_read", "browser_propose", "browser_confirm"}.isdisjoint(disabled))
        self.assertTrue({"browser_read", "browser_propose", "browser_confirm"}.issubset(enabled))
        self.assertEqual(self.cog._tool_result_max_length("browser_read"), 16_000)

    async def test_owner_config_sets_masks_and_clears_endpoint(self):
        ctx = SimpleNamespace(send=AsyncMock())
        with patch("ai._set_ai_browser_cdp_endpoint") as setter:
            await AICommands.ai_config_browser_cdp_text.callback(self.cog, ctx, endpoint=CDP_ENDPOINT)
        setter.assert_called_once_with(CDP_ENDPOINT)
        output = ctx.send.await_args.args[0]
        self.assertIn("/api/profiles/***/cdp", output)
        self.assertNotIn("1fec3e6d", output)

        ctx.send.reset_mock()
        with patch("ai._set_ai_browser_cdp_endpoint") as setter:
            await AICommands.ai_config_browser_cdp_text.callback(self.cog, ctx, endpoint="off")
        setter.assert_called_once_with("")

    async def test_preflight_launches_only_exact_stopped_response_and_polls(self):
        capture = {}
        fake_session = FakeManagerSession(
            [
                FakeResponse(404, {"detail": "Profile not running"}),
                FakeResponse(404, {"detail": "Profile not running"}),
                FakeResponse(200, {"cdp_url": "ws://cdp.example/devtools/browser/abc"}),
            ],
            post_response=FakeResponse(200, {"status": "launching"}),
            capture=capture,
        )
        with (
            patch("ai.aiohttp.ClientSession", return_value=fake_session),
            patch("ai.asyncio.sleep", new=AsyncMock()),
        ):
            cdp_url, error = await self.cog._preflight_browser_cdp(CDP_ENDPOINT)
        self.assertIsNone(error)
        self.assertEqual(cdp_url, "ws://cdp.example/devtools/browser/abc")
        self.assertEqual(capture["post"][0], derive_ai_browser_launch_endpoint(CDP_ENDPOINT))

    async def test_preflight_does_not_launch_other_http_errors(self):
        capture = {}
        fake_session = FakeManagerSession([FakeResponse(500, {"detail": "boom"})], capture=capture)
        with patch("ai.aiohttp.ClientSession", return_value=fake_session):
            cdp_url, error = await self.cog._preflight_browser_cdp(CDP_ENDPOINT)
        self.assertIsNone(cdp_url)
        self.assertIn("500", error)
        self.assertNotIn("post", capture)

    async def test_preflight_resolves_same_manager_relative_cdp_url(self):
        relative = "/api/profiles/1fec3e6d-71e2-4493-a09b-3c8b77ebe8b9/cdp"
        fake_session = FakeManagerSession([FakeResponse(200, {"cdp_url": relative})])
        with patch("ai.aiohttp.ClientSession", return_value=fake_session):
            cdp_url, error = await self.cog._preflight_browser_cdp(CDP_ENDPOINT)
        self.assertIsNone(error)
        self.assertEqual(cdp_url, CDP_ENDPOINT)

        cdp_url, error = self.cog._resolve_browser_cdp_connect_url(
            CDP_ENDPOINT,
            {"cdp_url": "//unexpected.example/cdp"},
        )
        self.assertIsNone(cdp_url)
        self.assertTrue(error)


class BrowserLeaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())
        self.cog.BROWSER_LEASE_TIMEOUT_SECONDS = 5

    async def asyncTearDown(self):
        contexts = [ticket.get("tool_context") for ticket in list(self.cog._browser_jobs_by_user.values())]
        for context in contexts:
            await self.cog._release_browser_lease(context)

    async def test_fifo_and_same_user_duplicate(self):
        first = tool_context(1)
        second = tool_context(2)
        third = tool_context(3)
        ticket, error = await self.cog._acquire_browser_lease(first)
        self.assertIsNone(error)
        duplicate, duplicate_error = await self.cog._acquire_browser_lease(tool_context(1, channel_id=21))
        self.assertIsNone(duplicate)
        self.assertTrue(duplicate_error)

        order = []

        async def acquire(context):
            acquired, acquire_error = await self.cog._acquire_browser_lease(context)
            self.assertIsNone(acquire_error)
            order.append(context["user"].id)
            return acquired

        second_task = asyncio.create_task(acquire(second))
        third_task = asyncio.create_task(acquire(third))
        await asyncio.sleep(0)
        self.assertEqual([ticket["user_id"] for ticket in self.cog._browser_waiters], [2, 3])
        await self.cog._release_browser_lease(first)
        await second_task
        self.assertEqual(order, [2])
        await self.cog._release_browser_lease(second)
        await third_task
        self.assertEqual(order, [2, 3])

    async def test_queue_limit_is_eight_waiters(self):
        first = tool_context(1)
        await self.cog._acquire_browser_lease(first)
        tasks = [asyncio.create_task(self.cog._acquire_browser_lease(tool_context(user_id))) for user_id in range(2, 10)]
        await asyncio.sleep(0)
        self.assertEqual(len(self.cog._browser_waiters), 8)
        ticket, error = await self.cog._acquire_browser_lease(tool_context(10))
        self.assertIsNone(ticket)
        self.assertTrue(error)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_queue_timeout_and_cancellation_remove_jobs(self):
        first = tool_context(1)
        await self.cog._acquire_browser_lease(first)
        self.cog.BROWSER_QUEUE_TIMEOUT_SECONDS = 0.01
        timed_out = tool_context(2)
        ticket, error = await self.cog._acquire_browser_lease(timed_out)
        self.assertIsNone(ticket)
        self.assertTrue(error)
        self.assertNotIn(2, self.cog._browser_jobs_by_user)

        self.cog.BROWSER_QUEUE_TIMEOUT_SECONDS = 5
        cancelled = tool_context(3)
        task = asyncio.create_task(self.cog._acquire_browser_lease(cancelled))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertNotIn(3, self.cog._browser_jobs_by_user)

    async def test_watchdog_releases_and_marks_expired(self):
        self.cog.BROWSER_LEASE_TIMEOUT_SECONDS = 0.01
        context = tool_context(1)
        await self.cog._acquire_browser_lease(context)
        await asyncio.sleep(0.03)
        self.assertTrue(context.get("_browser_lease_expired"))
        self.assertIsNone(self.cog._browser_active_ticket)
        ticket, error = await self.cog._acquire_browser_lease(context)
        self.assertIsNone(ticket)
        self.assertTrue(error)

    async def test_provider_failure_releases_existing_response_lease(self):
        context = tool_context(1)
        await self.cog._acquire_browser_lease(context)
        with patch.object(
            self.cog,
            "_request_ai_completion",
            new=AsyncMock(side_effect=RuntimeError("provider failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                await self.cog.generate_response(
                    [{"role": "user", "content": "browse"}],
                    model="test-model",
                    tool_context=context,
                )
        self.assertIsNone(self.cog._browser_active_ticket)
        self.assertNotIn(1, self.cog._browser_jobs_by_user)


class BrowserNetworkAndLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())

    async def asyncTearDown(self):
        for ticket in list(self.cog._browser_jobs_by_user.values()):
            await self.cog._release_browser_lease(ticket.get("tool_context"))

    async def test_public_url_and_dns_validation(self):
        with patch.object(self.cog, "_validate_public_fetch_target", new=AsyncMock(return_value=None)):
            normalized, error = await self.cog._validate_browser_public_url("https://example.com/path#fragment")
        self.assertIsNone(error)
        self.assertEqual(normalized, "https://example.com/path")

        for value in ("http://localhost/", "http://127.0.0.1/", "https://example.com:8443/", "https://u:p@example.com/"):
            normalized, error = await self.cog._validate_browser_public_url(value)
            self.assertIsNone(normalized)
            self.assertTrue(error)

        with patch.object(
            self.cog, "_validate_public_fetch_target", new=AsyncMock(return_value="target host resolved to a non-public address")
        ):
            normalized, error = await self.cog._validate_browser_public_url("https://rebind.example/path")
        self.assertIsNone(normalized)
        self.assertIn("non-public", error)

    async def test_page_route_blocks_private_redirect_and_subresource(self):
        session = {}
        route = FakeRoute()
        request = SimpleNamespace(url="http://10.0.0.1/private")
        await self.cog._browser_route_request(route, request, session)
        route.abort.assert_awaited_once()

        route = FakeRoute()
        request = SimpleNamespace(url="https://cdn.example/app.js")
        with patch.object(
            self.cog, "_validate_public_fetch_target", new=AsyncMock(return_value="target host resolved to a non-public address")
        ):
            await self.cog._browser_route_request(route, request, session)
        route.abort.assert_awaited_once()

        route = FakeRoute()
        request = SimpleNamespace(url="data:text/plain,ok")
        await self.cog._browser_route_request(route, request, session)
        route.continue_.assert_awaited_once()

        websocket_route = SimpleNamespace(close=AsyncMock())
        await self.cog._block_browser_websocket(websocket_route)
        websocket_route.close.assert_awaited_once_with(
            code=1008,
            reason="Browser tool allows public HTTP(S) requests only",
        )

    async def test_attach_uses_new_page_and_cleanup_never_closes_browser_or_original_pages(self):
        original_pages = [SimpleNamespace(close=AsyncMock()), SimpleNamespace(close=AsyncMock())]
        page = SimpleNamespace(
            route=AsyncMock(),
            route_web_socket=AsyncMock(),
            add_init_script=AsyncMock(),
            set_default_timeout=MagicMock(),
            on=MagicMock(),
            is_closed=MagicMock(return_value=False),
            close=AsyncMock(),
        )
        page_cdp_session = SimpleNamespace(send=AsyncMock(), detach=AsyncMock())
        context = SimpleNamespace(
            pages=original_pages,
            new_page=AsyncMock(return_value=page),
            new_cdp_session=AsyncMock(return_value=page_cdp_session),
        )
        browser = SimpleNamespace(contexts=[context], close=AsyncMock())
        playwright = SimpleNamespace(
            chromium=SimpleNamespace(connect_over_cdp=AsyncMock(return_value=browser)),
            stop=AsyncMock(),
        )
        starter = SimpleNamespace(start=AsyncMock(return_value=playwright))
        request_context = tool_context(1)
        with (
            patch("ai._get_ai_browser_cdp_endpoint", return_value=CDP_ENDPOINT),
            patch.object(self.cog, "_preflight_browser_cdp", new=AsyncMock(return_value=("ws://cdp", None))),
            patch("ai.async_playwright", return_value=starter),
        ):
            actual_page, error = await self.cog._get_browser_page(request_context)
            self.assertIsNone(error)
            self.assertIs(actual_page, page)
            self.assertEqual(request_context["_browser_session"]["original_page_count"], 2)
            await self.cog._release_browser_lease(request_context)

        page.close.assert_awaited_once()
        page_cdp_session.send.assert_any_await("Network.setBypassServiceWorker", {"bypass": True})
        page_cdp_session.detach.assert_awaited_once()
        playwright.stop.assert_awaited_once()
        browser.close.assert_not_awaited()
        for original in original_pages:
            original.close.assert_not_awaited()


class BrowserApprovalAndActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())
        self.page = SimpleNamespace(url="https://example.com/form", goto=AsyncMock())
        self.valid_url = AsyncMock(side_effect=lambda value: (str(value), None))
        self.operations = [
            {
                "action": "fill",
                "locator": {"strategy": "label", "value": "Display name"},
                "text": "private input",
            }
        ]

    async def _propose(self, context=None, operations=None):
        context = context or tool_context()
        with (
            patch.object(self.cog, "_get_browser_page", new=AsyncMock(return_value=(self.page, None))),
            patch.object(self.cog, "_validate_browser_public_url", new=self.valid_url),
        ):
            result = await self.cog._tool_browser_propose(
                {"operations": operations or self.operations}, context
            )
        token = result["confirmation_text"].split()[-1]
        return context, result, token

    async def test_propose_does_not_execute_and_same_turn_cannot_confirm(self):
        context, result, token = await self._propose()
        self.assertTrue(result["proposed"])
        self.assertIn("input hidden", " ".join(result["summary"]))
        with patch.object(self.cog, "_execute_browser_operations", new=AsyncMock()) as execute:
            rejected = await self.cog._tool_browser_confirm({"token": token}, context)
        self.assertIn("error", rejected)
        execute.assert_not_awaited()

    async def test_next_exact_turn_same_identity_confirms_once(self):
        _context, _result, token = await self._propose()
        confirm_context = tool_context(request_text=f"確認瀏覽器操作 {token}")
        await self.cog._prepare_browser_confirmation_turn(confirm_context)
        with (
            patch.object(self.cog, "_get_browser_page", new=AsyncMock(return_value=(self.page, None))),
            patch.object(self.cog, "_validate_browser_public_url", new=self.valid_url),
            patch.object(self.cog, "_execute_browser_operations", new=AsyncMock(return_value=[{"ok": True}])) as execute,
        ):
            confirmed = await self.cog._tool_browser_confirm({"token": token}, confirm_context)
            replay = await self.cog._tool_browser_confirm({"token": token}, confirm_context)
        self.assertTrue(confirmed["confirmed"])
        self.assertIn("error", replay)
        execute.assert_awaited_once()

    async def test_identity_channel_ttl_and_next_message_binding(self):
        _context, _result, token = await self._propose()
        wrong_channel = tool_context(channel_id=999, request_text=f"確認瀏覽器操作 {token}")
        await self.cog._prepare_browser_confirmation_turn(wrong_channel)
        self.assertNotIn("_browser_confirmation_token", wrong_channel)

        unrelated = tool_context(request_text="do something else")
        await self.cog._prepare_browser_confirmation_turn(unrelated)
        later = tool_context(request_text=f"確認瀏覽器操作 {token}")
        await self.cog._prepare_browser_confirmation_turn(later)
        self.assertNotIn("_browser_confirmation_token", later)

        _context, _result, token = await self._propose()
        self.cog._browser_approval_tokens[token]["expires_at"] = 0
        expired = tool_context(request_text=f"確認瀏覽器操作 {token}")
        await self.cog._prepare_browser_confirmation_turn(expired)
        self.assertNotIn("_browser_confirmation_token", expired)

    async def test_locator_and_operation_validation_rejects_bad_shapes(self):
        invalid_batches = (
            [],
            [{"action": "upload", "locator": {"strategy": "css", "value": "input"}}],
            [{"action": "click", "locator": {"strategy": "xpath", "value": "//button"}}],
            [{"action": "evaluate", "expression": "x" * (self.cog.BROWSER_MAX_EVALUATE_CHARS + 1)}],
            [{"action": "fill", "locator": {"strategy": "css", "value": "input"}, "text": "x" * 8001}],
        )
        for operations in invalid_batches:
            with self.subTest(operations=operations[:1]):
                normalized, error = self.cog._normalize_browser_operations(operations)
                self.assertIsNone(normalized)
                self.assertTrue(error)

    async def test_password_and_payment_fields_are_blocked(self):
        locator = SimpleNamespace(evaluate=AsyncMock(return_value={"type": "password", "name": "login"}))
        with self.assertRaisesRegex(ValueError, "password"):
            await self.cog._ensure_browser_field_allowed(locator)
        locator = SimpleNamespace(evaluate=AsyncMock(return_value={"type": "text", "name": "credit-card-number"}))
        with self.assertRaisesRegex(ValueError, "payment"):
            await self.cog._ensure_browser_field_allowed(locator)

    async def test_sensitive_browser_arguments_and_results_are_redacted_from_logs(self):
        captured = []
        with patch("ai.log", side_effect=lambda message, **_kwargs: captured.append(message)):
            self.cog._log_tool_request_batch(
                "model",
                [{"name": "browser_propose", "arguments": {"operations": self.operations}}],
                tool_context(),
            )
            self.cog._log_tool_result(
                "model",
                "browser_confirm",
                {"token": "secret-token"},
                {"ok": True, "data": {"token": "secret-token", "text": "private input"}},
                tool_context(),
            )
        joined = "\n".join(captured)
        self.assertNotIn("secret-token", joined)
        self.assertNotIn("private input", joined)
        self.assertIn("redacted", joined)

    async def test_long_proposal_json_is_queued_as_attachment(self):
        context, result, _token = await self._propose(
            operations=[{"action": "evaluate", "expression": "() => '" + ("x" * 2500) + "'"}]
        )
        self.assertTrue(result["details_attached"])
        self.assertEqual(context["pending_file_response"]["filename"], "browser-operation-proposal.json")
        self.assertIn(result["confirmation_text"], context["pending_file_response"]["summary"])


class BrowserReadResultTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())

    async def test_snapshot_is_untrusted_and_truncated(self):
        locator = SimpleNamespace(aria_snapshot=AsyncMock(return_value="x" * 15_000))
        page = SimpleNamespace(url="https://example.com/", locator=MagicMock(return_value=locator))
        with patch.object(self.cog, "_get_browser_page", new=AsyncMock(return_value=(page, None))):
            result = await self.cog._tool_browser_read({"action": "snapshot"}, tool_context())
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["snapshot"]), self.cog.BROWSER_SNAPSHOT_MAX_CHARS)
        self.assertIn("Untrusted", result["note"])

    async def test_screenshot_uses_pending_image_pipeline_and_enforces_size_limit(self):
        page = SimpleNamespace(url="https://example.com/", screenshot=AsyncMock(return_value=b"png"))
        context = tool_context()
        with patch.object(self.cog, "_get_browser_page", new=AsyncMock(return_value=(page, None))):
            result = await self.cog._tool_browser_read({"action": "screenshot"}, context)
        self.assertEqual(len(context["pending_image_attachments"]), 1)
        self.assertEqual(context["pending_image_attachments"][0]["kind"], "browser")
        self.assertTrue(result["attachment_ref"].startswith("attachment://"))

        page.screenshot = AsyncMock(return_value=b"x" * (self.cog.BROWSER_SCREENSHOT_MAX_BYTES + 1))
        context = tool_context()
        with patch.object(self.cog, "_get_browser_page", new=AsyncMock(return_value=(page, None))):
            result = await self.cog._tool_browser_read({"action": "screenshot"}, context)
        self.assertIn("error", result)
        self.assertNotIn("pending_image_attachments", context)


if __name__ == "__main__":
    unittest.main()
