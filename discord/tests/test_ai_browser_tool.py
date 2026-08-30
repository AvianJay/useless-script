import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import ai_browser
from ai import (
    AICommands,
    BrowserApprovalView,
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


def completion_response(content="", *, tool_calls=None):
    return SimpleNamespace(
        model="test-model",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls, images=None)
            )
        ],
    )


def snapshot_page(url="https://example.com/form", snapshot="- button \"Submit\" [ref=e5]"):
    return SimpleNamespace(
        url=url,
        goto=AsyncMock(),
        title=AsyncMock(return_value="Example"),
        aria_snapshot=AsyncMock(return_value=snapshot),
    )


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
        browser_tools = {"browser_read", "browser_view", "browser_act", "browser_evaluate"}
        with patch("ai._get_ai_browser_cdp_endpoint", return_value=""):
            disabled = {item["function"]["name"] for item in self.cog._build_ai_tools()}
        with patch("ai._get_ai_browser_cdp_endpoint", return_value=CDP_ENDPOINT):
            enabled_tools = self.cog._build_ai_tools()
            enabled = {item["function"]["name"] for item in enabled_tools}
        self.assertTrue(browser_tools.isdisjoint(disabled))
        self.assertTrue(browser_tools.issubset(enabled))
        self.assertNotIn("browser_propose", enabled)
        self.assertNotIn("browser_confirm", enabled)
        self.assertEqual(self.cog._tool_result_max_length("browser_act"), 16_000)
        self.assertEqual(self.cog._tool_result_max_length("browser_view"), 16_000)
        view_schema = next(
            item["function"] for item in enabled_tools if item["function"]["name"] == "browser_view"
        )
        self.assertEqual(set(view_schema["parameters"]["properties"]), {"prompt", "max_chars", "full_page"})
        self.assertEqual(self.cog.BROWSER_LEASE_TIMEOUT_SECONDS, 5 * 60)

    def test_act_schema_requires_ref_and_element(self):
        with patch("ai._get_ai_browser_cdp_endpoint", return_value=CDP_ENDPOINT):
            tools = {item["function"]["name"]: item["function"] for item in self.cog._build_ai_tools()}
        act = tools["browser_act"]["parameters"]
        self.assertEqual(act["required"], ["action", "ref", "element"])
        self.assertNotIn("locator", act["properties"])
        self.assertNotIn("evaluate", act["properties"]["action"]["enum"])
        self.assertEqual(tools["browser_evaluate"]["parameters"]["required"], ["expression"])
        read = tools["browser_read"]["parameters"]
        self.assertIn("scroll", read["properties"]["action"]["enum"])
        self.assertNotIn("hover", read["properties"]["action"]["enum"])

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
            patch("ai_browser.aiohttp.ClientSession", return_value=fake_session),
            patch("ai_browser.asyncio.sleep", new=AsyncMock()),
        ):
            cdp_url, error = await self.cog.browser.preflight_cdp(CDP_ENDPOINT)
        self.assertIsNone(error)
        self.assertEqual(cdp_url, "ws://cdp.example/devtools/browser/abc")
        self.assertEqual(capture["post"][0], derive_ai_browser_launch_endpoint(CDP_ENDPOINT))

    async def test_preflight_does_not_launch_other_http_errors(self):
        capture = {}
        fake_session = FakeManagerSession([FakeResponse(500, {"detail": "boom"})], capture=capture)
        with patch("ai_browser.aiohttp.ClientSession", return_value=fake_session):
            cdp_url, error = await self.cog.browser.preflight_cdp(CDP_ENDPOINT)
        self.assertIsNone(cdp_url)
        self.assertIn("500", error)
        self.assertNotIn("post", capture)

    async def test_preflight_resolves_same_manager_relative_cdp_url(self):
        relative = "/api/profiles/1fec3e6d-71e2-4493-a09b-3c8b77ebe8b9/cdp"
        fake_session = FakeManagerSession([FakeResponse(200, {"cdp_url": relative})])
        with patch("ai_browser.aiohttp.ClientSession", return_value=fake_session):
            cdp_url, error = await self.cog.browser.preflight_cdp(CDP_ENDPOINT)
        self.assertIsNone(error)
        self.assertEqual(cdp_url, CDP_ENDPOINT)

        cdp_url, error = self.cog.browser._resolve_cdp_connect_url(
            CDP_ENDPOINT,
            {"cdp_url": "//unexpected.example/cdp"},
        )
        self.assertIsNone(cdp_url)
        self.assertTrue(error)


class BrowserLeaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())

    async def asyncTearDown(self):
        contexts = [ticket.get("tool_context") for ticket in list(self.cog.browser._jobs_by_user.values())]
        for context in contexts:
            await self.cog.browser.release_lease(context)

    async def test_fifo_and_same_user_duplicate(self):
        first = tool_context(1)
        second = tool_context(2)
        third = tool_context(3)
        ticket, error = await self.cog.browser.acquire_lease(first)
        self.assertIsNone(error)
        duplicate, duplicate_error = await self.cog.browser.acquire_lease(tool_context(1, channel_id=21))
        self.assertIsNone(duplicate)
        self.assertTrue(duplicate_error)

        order = []

        async def acquire(context):
            acquired, acquire_error = await self.cog.browser.acquire_lease(context)
            self.assertIsNone(acquire_error)
            order.append(context["user"].id)
            return acquired

        second_task = asyncio.create_task(acquire(second))
        third_task = asyncio.create_task(acquire(third))
        await asyncio.sleep(0)
        self.assertEqual([ticket["user_id"] for ticket in self.cog.browser._waiters], [2, 3])
        await self.cog.browser.release_lease(first)
        await second_task
        self.assertEqual(order, [2])
        await self.cog.browser.release_lease(second)
        await third_task
        self.assertEqual(order, [2, 3])

    async def test_queue_limit_is_eight_waiters(self):
        first = tool_context(1)
        await self.cog.browser.acquire_lease(first)
        tasks = [
            asyncio.create_task(self.cog.browser.acquire_lease(tool_context(user_id)))
            for user_id in range(2, 10)
        ]
        await asyncio.sleep(0)
        self.assertEqual(len(self.cog.browser._waiters), 8)
        ticket, error = await self.cog.browser.acquire_lease(tool_context(10))
        self.assertIsNone(ticket)
        self.assertTrue(error)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_queue_timeout_and_cancellation_remove_jobs(self):
        first = tool_context(1)
        await self.cog.browser.acquire_lease(first)
        timed_out = tool_context(2)
        with patch("ai_browser.BROWSER_QUEUE_TIMEOUT_SECONDS", 0.01):
            ticket, error = await self.cog.browser.acquire_lease(timed_out)
        self.assertIsNone(ticket)
        self.assertTrue(error)
        self.assertNotIn(2, self.cog.browser._jobs_by_user)

        cancelled = tool_context(3)
        task = asyncio.create_task(self.cog.browser.acquire_lease(cancelled))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertNotIn(3, self.cog.browser._jobs_by_user)

    async def test_watchdog_releases_and_marks_expired(self):
        context = tool_context(1)
        with patch("ai_browser.BROWSER_LEASE_TIMEOUT_SECONDS", 0.01):
            await self.cog.browser.acquire_lease(context)
            await asyncio.sleep(0.03)
        self.assertTrue(context.get("_browser_lease_expired"))
        self.assertIsNone(self.cog.browser._active_ticket)
        ticket, error = await self.cog.browser.acquire_lease(context)
        self.assertIsNone(ticket)
        self.assertTrue(error)

    async def test_provider_failure_releases_existing_response_lease(self):
        context = tool_context(1)
        await self.cog.browser.acquire_lease(context)
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
        self.assertIsNone(self.cog.browser._active_ticket)
        self.assertNotIn(1, self.cog.browser._jobs_by_user)


class BrowserNetworkAndLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())

    async def asyncTearDown(self):
        for ticket in list(self.cog.browser._jobs_by_user.values()):
            await self.cog.browser.release_lease(ticket.get("tool_context"))

    async def test_public_url_and_dns_validation(self):
        with patch("ai_browser.validate_public_fetch_target", new=AsyncMock(return_value=None)):
            normalized, error = await self.cog.browser.validate_public_url("https://example.com/path#fragment")
        self.assertIsNone(error)
        self.assertEqual(normalized, "https://example.com/path")

        for value in ("http://localhost/", "http://127.0.0.1/", "https://example.com:8443/", "https://u:p@example.com/"):
            normalized, error = await self.cog.browser.validate_public_url(value)
            self.assertIsNone(normalized)
            self.assertTrue(error)

        with patch(
            "ai_browser.validate_public_fetch_target",
            new=AsyncMock(return_value="target host resolved to a non-public address"),
        ):
            normalized, error = await self.cog.browser.validate_public_url("https://rebind.example/path")
        self.assertIsNone(normalized)
        self.assertIn("non-public", error)

    async def test_page_route_blocks_private_redirect_and_subresource(self):
        session = {}
        route = FakeRoute()
        request = SimpleNamespace(url="http://10.0.0.1/private")
        await self.cog.browser._route_request(route, request, session)
        route.abort.assert_awaited_once()

        route = FakeRoute()
        request = SimpleNamespace(url="https://cdn.example/app.js")
        with patch(
            "ai_browser.validate_public_fetch_target",
            new=AsyncMock(return_value="target host resolved to a non-public address"),
        ):
            await self.cog.browser._route_request(route, request, session)
        route.abort.assert_awaited_once()

        route = FakeRoute()
        request = SimpleNamespace(url="data:text/plain,ok")
        await self.cog.browser._route_request(route, request, session)
        route.continue_.assert_awaited_once()

        websocket_route = SimpleNamespace(close=AsyncMock())
        await self.cog.browser._block_websocket(websocket_route)
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
            patch("ai_browser.get_ai_browser_cdp_endpoint", return_value=CDP_ENDPOINT),
            patch.object(self.cog.browser, "preflight_cdp", new=AsyncMock(return_value=("ws://cdp", None))),
            patch("ai_browser.async_playwright", return_value=starter),
        ):
            actual_page, error = await self.cog.browser.get_page(request_context)
            self.assertIsNone(error)
            self.assertIs(actual_page, page)
            self.assertEqual(request_context["_browser_session"]["original_page_count"], 2)
            await self.cog.browser.release_lease(request_context)

        page.close.assert_awaited_once()
        page_cdp_session.send.assert_any_await("Network.setBypassServiceWorker", {"bypass": True})
        page_cdp_session.detach.assert_awaited_once()
        playwright.stop.assert_awaited_once()
        browser.close.assert_not_awaited()
        for original in original_pages:
            original.close.assert_not_awaited()


class BrowserActionValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())

    def test_ref_normalization_accepts_bare_and_wrapped_forms(self):
        for value in ("e12", "[ref=e12]", " ref=e12 "):
            with self.subTest(value=value):
                self.assertEqual(ai_browser.normalize_ref(value), "e12")
        for value in ("", None, "e12 e13", "a" * 40, "e12'; DROP"):
            with self.subTest(value=value):
                self.assertIsNone(ai_browser.normalize_ref(value))

    def test_normalize_action_rejects_bad_shapes(self):
        invalid = (
            {},
            {"action": "upload", "ref": "e1", "element": "file"},
            {"action": "evaluate", "ref": "e1", "element": "page"},
            {"action": "click", "element": "button"},
            {"action": "click", "ref": "e1"},
            {"action": "fill", "ref": "e1", "element": "box", "text": "x" * 8001},
            {"action": "press", "ref": "e1", "element": "box", "key": ""},
            {"action": "select_option", "ref": "e1", "element": "list", "values": []},
        )
        for args in invalid:
            with self.subTest(args=args):
                normalized, error = self.cog.browser.normalize_action(args)
                self.assertIsNone(normalized)
                self.assertTrue(error)

        normalized, error = self.cog.browser.normalize_action(
            {"action": "click", "ref": "e3", "element": "  Submit   button  "}
        )
        self.assertIsNone(error)
        self.assertEqual(normalized["element"], "Submit button")
        self.assertEqual(normalized["timeout_ms"], ai_browser.BROWSER_ACTION_TIMEOUT_MS)

    def test_normalize_evaluate_enforces_length_and_ref(self):
        normalized, error = self.cog.browser.normalize_evaluate({"expression": ""})
        self.assertIsNone(normalized)
        self.assertTrue(error)

        normalized, error = self.cog.browser.normalize_evaluate(
            {"expression": "x" * (ai_browser.BROWSER_MAX_EVALUATE_CHARS + 1)}
        )
        self.assertIsNone(normalized)
        self.assertTrue(error)

        normalized, error = self.cog.browser.normalize_evaluate({"expression": "() => 1", "ref": "nope!"})
        self.assertIsNone(normalized)
        self.assertTrue(error)

        normalized, error = self.cog.browser.normalize_evaluate({"expression": "el => el.tagName", "ref": "[ref=e4]"})
        self.assertIsNone(error)
        self.assertEqual(normalized["ref"], "e4")

    def test_error_formatting_keeps_the_message_and_drops_the_call_log(self):
        error = TimeoutError("Locator.click: Timeout 15000ms exceeded.\nCall log:\n  - waiting for locator")
        formatted = ai_browser.format_browser_error(error)
        self.assertIn("TimeoutError", formatted)
        self.assertIn("Timeout 15000ms exceeded.", formatted)
        self.assertNotIn("Call log", formatted)
        self.assertTrue(ai_browser.is_element_resolution_error(formatted))

    async def test_password_and_payment_fields_are_blocked(self):
        locator = SimpleNamespace(evaluate=AsyncMock(return_value={"type": "password", "name": "login"}))
        with self.assertRaisesRegex(ValueError, "password"):
            await self.cog.browser.ensure_field_allowed(locator)
        locator = SimpleNamespace(evaluate=AsyncMock(return_value={"type": "text", "name": "credit-card-number"}))
        with self.assertRaisesRegex(ValueError, "payment"):
            await self.cog.browser.ensure_field_allowed(locator)

    async def test_guard_evaluate_uses_the_action_timeout(self):
        locator = SimpleNamespace(evaluate=AsyncMock(return_value={"type": "text"}))
        await self.cog.browser.ensure_field_allowed(locator, 2500)
        self.assertEqual(locator.evaluate.await_args.kwargs["timeout"], 2500)


class BrowserActionExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())
        self.locator = SimpleNamespace(
            click=AsyncMock(),
            fill=AsyncMock(),
            evaluate=AsyncMock(return_value=False),
        )
        self.page = snapshot_page()
        self.page.locator = MagicMock(return_value=self.locator)

    async def test_successful_action_returns_a_fresh_snapshot(self):
        action, _ = self.cog.browser.normalize_action(
            {"action": "click", "ref": "e5", "element": "Submit button"}
        )
        result = await self.cog.browser.execute_action(self.page, action, tool_context())

        self.page.locator.assert_called_once_with("aria-ref=e5")
        self.locator.click.assert_awaited_once()
        self.assertTrue(result["ok"])
        self.assertEqual(result["ref"], "e5")
        self.assertIn("[ref=e5]", result["snapshot"])
        self.assertTrue(result["refs_available"])
        self.assertIn("Untrusted", result["note"])

    async def test_stale_ref_failure_returns_hint_and_fresh_snapshot(self):
        self.locator.click = AsyncMock(
            side_effect=TimeoutError("Locator.click: Timeout 15000ms exceeded.\nCall log:\n  - aria-ref=e99")
        )
        action, _ = self.cog.browser.normalize_action(
            {"action": "click", "ref": "e99", "element": "old button"}
        )
        result = await self.cog.browser.execute_action(self.page, action, tool_context())

        self.assertIn("TimeoutError", result["error"])
        self.assertIn("Timeout 15000ms exceeded.", result["error"])
        self.assertIn("retry once", result["hint"])
        self.assertIn("[ref=e5]", result["snapshot"])

    async def test_blocked_request_reports_the_ssrf_reason_without_a_snapshot(self):
        self.locator.click = AsyncMock(side_effect=RuntimeError("net::ERR_BLOCKED_BY_CLIENT"))
        context = tool_context()
        context["_browser_session"] = {"last_blocked_url": "http://10.0.0.1/"}
        action, _ = self.cog.browser.normalize_action(
            {"action": "click", "ref": "e5", "element": "link"}
        )
        result = await self.cog.browser.execute_action(self.page, action, context)

        self.assertIn("non-public", result["error"])
        self.assertNotIn("snapshot", result)

    async def test_snapshot_failure_does_not_mask_a_successful_action(self):
        self.page.aria_snapshot = AsyncMock(side_effect=RuntimeError("snapshot exploded"))
        action, _ = self.cog.browser.normalize_action(
            {"action": "click", "ref": "e5", "element": "Submit button"}
        )
        result = await self.cog.browser.execute_action(self.page, action, tool_context())

        self.assertTrue(result["ok"])
        self.assertIn("snapshot exploded", result["snapshot_error"])

    async def test_evaluate_scopes_to_a_ref_and_shrinks_the_value(self):
        self.locator.evaluate = AsyncMock(return_value="BUTTON")
        spec, _ = self.cog.browser.normalize_evaluate({"expression": "el => el.tagName", "ref": "e5"})
        result = await self.cog.browser.execute_evaluate(self.page, spec, tool_context())
        self.assertEqual(result["value"], "BUTTON")
        self.assertIn("[ref=e5]", result["snapshot"])

        self.page.evaluate = AsyncMock(return_value="y" * 5000)
        spec, _ = self.cog.browser.normalize_evaluate({"expression": "() => document.body.innerText"})
        result = await self.cog.browser.execute_evaluate(self.page, spec, tool_context())
        self.assertTrue(result["value"]["truncated"])
        self.assertLessEqual(
            len(result["value"]["preview"]), ai_browser.BROWSER_EVALUATE_RESULT_MAX_CHARS
        )

    async def test_snapshot_truncation_marks_the_result(self):
        self.page.aria_snapshot = AsyncMock(return_value="x" * 15_000)
        result = await self.cog.browser.capture_snapshot(self.page)
        self.assertTrue(result["snapshot_truncated"])
        self.assertLessEqual(len(result["snapshot"]), ai_browser.BROWSER_SNAPSHOT_MAX_CHARS)
        self.assertIn("Untrusted", result["note"])
        self.assertEqual(self.page.aria_snapshot.await_args.kwargs["mode"], "ai")


class BrowserApprovalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())
        self.page = snapshot_page()
        self.cog.browser.get_page = AsyncMock(return_value=(self.page, None))
        self.cog.browser.validate_public_url = AsyncMock(side_effect=lambda value: (str(value), None))
        self.cog.browser.execute_action = AsyncMock(
            return_value={"action": "fill", "ok": True, "snapshot": "- textbox [ref=e1]"}
        )
        self.cog.browser.execute_evaluate = AsyncMock(return_value={"action": "evaluate", "ok": True, "value": 1})
        self.action_args = {
            "action": "fill",
            "ref": "e7",
            "element": "Display name field",
            "text": "private input",
            "reason": "Update the display name.",
        }

    async def _start_action(self, context=None, args=None, tool="act"):
        context = context or tool_context()
        presented = asyncio.get_running_loop().create_future()

        async def presenter(record):
            if not presented.done():
                presented.set_result(record)

        context["_browser_approval_presenter"] = presenter
        handler = self.cog._tool_browser_act if tool == "act" else self.cog._tool_browser_evaluate
        task = asyncio.create_task(handler(args or self.action_args, context))
        record = await asyncio.wait_for(presented, timeout=1)
        await asyncio.sleep(0)
        return context, task, record

    @staticmethod
    def _interaction(context, *, channel_id=None):
        channel = context["channel"] if channel_id is None else SimpleNamespace(id=channel_id)
        return SimpleNamespace(user=context["user"], guild=context["guild"], channel=channel)

    async def test_act_waits_for_button_then_authorizes_whole_session(self):
        context, task, record = await self._start_action()
        self.assertFalse(task.done())
        self.cog.browser.execute_action.assert_not_awaited()

        confirmed = await self.cog.browser.execute_approval_from_button(self._interaction(context), record)
        await self.cog.browser.resolve_approval(record, confirmed)
        result = await task

        self.assertTrue(result["authorized"])
        self.assertEqual(result["authorization_scope"], "current_browser_session")
        self.assertTrue(context["_browser_interaction_authorized"])
        self.page.goto.assert_not_awaited()
        self.cog.browser.execute_action.assert_awaited_once_with(self.page, record["payload"], context)

        second = await self.cog._tool_browser_act(
            {"action": "click", "ref": "e9", "element": "Save button"}, context
        )
        self.assertTrue(second["authorized"])
        self.assertEqual(self.cog.browser.execute_action.await_count, 2)

    async def test_evaluate_shares_the_same_session_authorization(self):
        context, task, record = await self._start_action(
            args={"expression": "() => document.title", "reason": "Read the title."}, tool="evaluate"
        )
        confirmed = await self.cog.browser.execute_approval_from_button(self._interaction(context), record)
        await self.cog.browser.resolve_approval(record, confirmed)
        await task

        self.cog.browser.execute_evaluate.assert_awaited_once()
        follow_up = await self.cog._tool_browser_act(
            {"action": "click", "ref": "e9", "element": "Save button"}, context
        )
        self.assertTrue(follow_up["authorized"])
        self.cog.browser.execute_action.assert_awaited_once()

    async def test_approval_prompt_shows_the_action_and_the_full_script(self):
        _context, task, record = await self._start_action()
        self.assertIn("fill", record["display_action"])
        self.assertIn("Display name field", record["display_action"])
        self.assertIn("private input", record["display_action"])
        self.assertEqual(record["display_reason"], "Update the display name.")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        script = "() => fetch('/admin/delete-everything')"
        _context, task, record = await self._start_action(
            tool_context(user_id=2), args={"expression": script}, tool="evaluate"
        )
        self.assertIn(script, record["display_action"])
        self.assertNotIn("content hidden", record["display_action"])
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_long_script_is_shown_truncated_and_attached_in_full(self):
        script = "() => '" + ("x" * 3000) + "'"
        context, task, record = await self._start_action(args={"expression": script}, tool="evaluate")
        self.assertEqual(record["details_file"]["filename"], "browser-evaluate-script.js")
        self.assertEqual(record["details_file"]["content"], script)
        self.assertNotIn(record["token"], record["details_file"]["content"])
        self.assertLess(len(record["display_action"]), len(script))

        rejected = await self.cog.browser.reject_approval_from_button(self._interaction(context), record)
        await self.cog.browser.resolve_approval(record, rejected)
        result = await task
        self.assertTrue(result["details_attached"])

    async def test_confirmation_is_single_use_and_bound_to_identity_channel_and_ttl(self):
        context, task, record = await self._start_action()
        wrong = await self.cog.browser.execute_approval_from_button(
            self._interaction(context, channel_id=999), record
        )
        self.assertIn("error", wrong)
        self.assertFalse(task.done())

        confirmed = await self.cog.browser.execute_approval_from_button(self._interaction(context), record)
        replay = await self.cog.browser.execute_approval_from_button(self._interaction(context), record)
        self.assertTrue(confirmed["authorized"])
        self.assertIn("error", replay)
        await self.cog.browser.resolve_approval(record, confirmed)
        await task

        context, task, record = await self._start_action(tool_context(user_id=2))
        record["expires_at"] = 0
        expired = await self.cog.browser.execute_approval_from_button(self._interaction(context), record)
        self.assertIn("error", expired)
        await self.cog.browser.resolve_approval(record, expired)
        await task

    async def test_approved_action_is_abandoned_when_the_page_navigated_away(self):
        context, task, record = await self._start_action()
        self.page.url = "https://example.com/somewhere-else"
        confirmed = await self.cog.browser.execute_approval_from_button(self._interaction(context), record)
        await self.cog.browser.resolve_approval(record, confirmed)
        result = await task

        self.assertIn("changed before", result["error"])
        self.cog.browser.execute_action.assert_not_awaited()

    async def test_reject_resumes_tool_and_blocks_later_interactions_in_session(self):
        context, task, record = await self._start_action()
        rejected = await self.cog.browser.reject_approval_from_button(self._interaction(context), record)
        await self.cog.browser.resolve_approval(record, rejected)
        result = await task

        self.assertTrue(result["rejected"])
        self.assertTrue(context["_browser_interaction_denied"])
        self.cog.browser.execute_action.assert_not_awaited()

        later = await self.cog._tool_browser_act(
            {"action": "click", "ref": "e9", "element": "Save button"}, context
        )
        self.assertIn("error", later)
        later_evaluate = await self.cog._tool_browser_evaluate({"expression": "() => 1"}, context)
        self.assertIn("error", later_evaluate)
        self.cog.browser.execute_action.assert_not_awaited()
        self.cog.browser.execute_evaluate.assert_not_awaited()

    async def test_timeout_resumes_pending_tool_and_denies_the_session(self):
        with patch("ai_browser.BROWSER_APPROVAL_TTL_SECONDS", 0.01):
            context, task, _record = await self._start_action()
            result = await asyncio.wait_for(task, timeout=1)
        self.assertIn("error", result)
        self.assertTrue(context["_browser_interaction_denied"])

    async def test_components_callbacks_resolve_pending_tool(self):
        context, task, record = await self._start_action()
        interaction = self._interaction(context)
        interaction.response = SimpleNamespace(edit_message=AsyncMock())
        interaction.edit_original_response = AsyncMock()
        view = BrowserApprovalView(self.cog, record)

        await view.confirm_callback(interaction)
        result = await task

        self.assertTrue(result["authorized"])
        interaction.response.edit_message.assert_awaited_once()
        edit_kwargs = interaction.edit_original_response.await_args.kwargs
        self.assertEqual(edit_kwargs["attachments"], [])
        self.assertIsNotNone(edit_kwargs["view"])

        context, task, record = await self._start_action(tool_context(user_id=2))
        interaction = self._interaction(context)
        interaction.response = SimpleNamespace(edit_message=AsyncMock())
        view = BrowserApprovalView(self.cog, record)

        await view.reject_callback(interaction)
        result = await task

        self.assertTrue(result["rejected"])
        interaction.response.edit_message.assert_awaited_once()

        context, task, _record = await self._start_action(tool_context(user_id=3))
        await self.cog.browser.release_lease(context)
        result = await asyncio.wait_for(task, timeout=1)
        self.assertIn("error", result)

    async def test_approval_view_renders_reason_and_action_without_the_token(self):
        context, task, record = await self._start_action()
        token = record["token"]
        view = BrowserApprovalView(self.cog, record)
        components = view.to_components()
        self.assertNotIn(token, json.dumps(components))
        container = components[0]
        self.assertEqual(container["type"], 17)
        self.assertEqual(container["components"][0]["content"], "**AI 想要操作瀏覽器**")
        self.assertEqual([item["type"] for item in container["components"]], [10, 14, 10, 14, 10, 14, 1])
        self.assertEqual(container["components"][2]["content"], "Update the display name.")
        self.assertIn("Display name field", container["components"][4]["content"])
        self.assertEqual(len(container["components"][6]["components"]), 2)

        rejected = await self.cog.browser.reject_approval_from_button(self._interaction(context), record)
        await self.cog.browser.resolve_approval(record, rejected)
        result = await task
        self.assertNotIn(token, json.dumps(result))

    async def test_sensitive_browser_arguments_and_results_are_redacted_from_logs(self):
        captured = []
        with patch("ai.log", side_effect=lambda message, **_kwargs: captured.append(message)):
            self.cog._log_tool_request_batch(
                "model",
                [
                    {"name": "browser_act", "arguments": self.action_args},
                    {"name": "browser_evaluate", "arguments": {"expression": "() => document.cookie"}},
                    {"name": "browser_view", "arguments": {"prompt": "secret visual prompt"}},
                ],
                tool_context(),
            )
            self.cog._log_tool_result(
                "model",
                "browser_act",
                self.action_args,
                {"ok": True, "data": {"token": "secret-token", "snapshot": "private input"}},
                tool_context(),
            )
        joined = "\n".join(captured)
        self.assertNotIn("secret-token", joined)
        self.assertNotIn("private input", joined)
        self.assertNotIn("document.cookie", joined)
        self.assertNotIn("secret visual prompt", joined)
        self.assertIn("redacted", joined)
        self.assertIn("expression_chars", joined)

    async def test_generate_response_continues_same_tool_loop_after_button(self):
        def act_call(call_id, ref):
            return completion_response(
                "Working on it.",
                tool_calls=[
                    {
                        "id": call_id,
                        "function": {
                            "name": "browser_act",
                            "arguments": json.dumps(
                                {"action": "click", "ref": ref, "element": "a button", "reason": "Proceed."}
                            ),
                        },
                    }
                ],
            )

        responses = [
            (act_call("browser-act-1", "e7"), "native"),
            (act_call("browser-act-2", "e8"), "native"),
            (
                completion_response(
                    "I will inspect the result.",
                    tool_calls=[
                        {
                            "id": "browser-read-3",
                            "function": {"name": "browser_read", "arguments": '{"action":"snapshot"}'},
                        }
                    ],
                ),
                "native",
            ),
            (completion_response("The browser task is complete."), "native"),
        ]
        events = []
        request_count = 0

        async def request(*_args, **_kwargs):
            nonlocal request_count
            request_count += 1
            events.append(f"request-{request_count}")
            return responses.pop(0)

        async def act(_page, _action, _context):
            events.append("act")
            return {"action": "click", "ok": True, "snapshot": "- button [ref=e9]"}

        async def read_page(_args, _context):
            events.append("read")
            return {"snapshot": "- textbox: updated", "url": self.page.url}

        async def release(_context, **_kwargs):
            events.append("release")

        context = tool_context()
        presented = asyncio.get_running_loop().create_future()

        async def presenter(record):
            presented.set_result(record)

        context["_browser_approval_presenter"] = presenter
        self.cog.browser.execute_action = AsyncMock(side_effect=act)
        self.cog._tool_browser_read = AsyncMock(side_effect=read_page)

        with (
            patch("ai._get_ai_browser_cdp_endpoint", return_value=CDP_ENDPOINT),
            patch.object(self.cog, "_request_ai_completion", new=AsyncMock(side_effect=request)) as provider,
            patch.object(self.cog.browser, "release_lease", new=AsyncMock(side_effect=release)),
        ):
            response_task = asyncio.create_task(
                self.cog.generate_response(
                    [{"role": "user", "content": "update it"}],
                    model="test-model",
                    tool_context=context,
                )
            )
            record = await asyncio.wait_for(presented, timeout=1)
            self.assertFalse(response_task.done())
            self.assertEqual(events, ["request-1"])

            confirmed = await self.cog.browser.execute_approval_from_button(self._interaction(context), record)
            await self.cog.browser.resolve_approval(record, confirmed)
            text, model, _elapsed = await asyncio.wait_for(response_task, timeout=1)

        self.assertEqual((text, model), ("The browser task is complete.", "test-model"))
        self.assertEqual(
            events,
            [
                "request-1",
                "act",
                "request-2",
                "act",
                "request-3",
                "read",
                "request-4",
                "release",
            ],
        )
        self.assertEqual(provider.await_count, 4)
        self.assertEqual(self.cog._tool_browser_read.await_count, 1)
        self.assertTrue(context["_browser_interaction_authorized"])


class BrowserReadResultTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())

    async def test_snapshot_is_untrusted_and_truncated(self):
        page = snapshot_page()
        page.aria_snapshot = AsyncMock(return_value="x" * 15_000)
        with patch.object(self.cog.browser, "get_page", new=AsyncMock(return_value=(page, None))):
            result = await self.cog._tool_browser_read({"action": "snapshot"}, tool_context())
        self.assertTrue(result["snapshot_truncated"])
        self.assertLessEqual(len(result["snapshot"]), self.cog.BROWSER_SNAPSHOT_MAX_CHARS)
        self.assertIn("Untrusted", result["note"])

    async def test_navigate_returns_a_fresh_snapshot_with_refs(self):
        page = snapshot_page()
        with (
            patch.object(self.cog.browser, "get_page", new=AsyncMock(return_value=(page, None))),
            patch.object(
                self.cog.browser,
                "validate_public_url",
                new=AsyncMock(return_value=("https://example.com/form", None)),
            ),
        ):
            result = await self.cog._tool_browser_read(
                {"action": "navigate", "url": "https://example.com/form"}, tool_context()
            )
        page.goto.assert_awaited_once()
        self.assertEqual(result["action"], "navigate")
        self.assertEqual(result["title"], "Example")
        self.assertIn("[ref=e5]", result["snapshot"])
        self.assertTrue(result["refs_available"])

    async def test_navigate_rejects_non_public_targets(self):
        page = snapshot_page()
        with patch.object(self.cog.browser, "get_page", new=AsyncMock(return_value=(page, None))):
            result = await self.cog._tool_browser_read(
                {"action": "navigate", "url": "http://127.0.0.1/admin"}, tool_context()
            )
        self.assertIn("error", result)
        page.goto.assert_not_awaited()

    async def test_unsupported_action_is_named_in_the_error(self):
        result = await self.cog._tool_browser_read({"action": "click"}, tool_context())
        self.assertIn("unsupported", result["error"])
        self.assertIn("snapshot", result["error"])

    async def test_scroll_uses_the_ref_locator_or_the_wheel(self):
        page = snapshot_page()
        locator = SimpleNamespace(scroll_into_view_if_needed=AsyncMock())
        page.locator = MagicMock(return_value=locator)
        page.mouse = SimpleNamespace(wheel=AsyncMock())
        with patch.object(self.cog.browser, "get_page", new=AsyncMock(return_value=(page, None))):
            await self.cog._tool_browser_read({"action": "scroll", "ref": "e5"}, tool_context())
            page.locator.assert_called_once_with("aria-ref=e5")
            locator.scroll_into_view_if_needed.assert_awaited_once()

            await self.cog._tool_browser_read({"action": "scroll", "delta_y": 400}, tool_context())
            page.mouse.wheel.assert_awaited_once_with(0, 400)

    async def test_screenshot_uses_pending_image_pipeline_and_enforces_size_limit(self):
        page = SimpleNamespace(url="https://example.com/", screenshot=AsyncMock(return_value=b"png"))
        context = tool_context()
        with patch.object(self.cog.browser, "get_page", new=AsyncMock(return_value=(page, None))):
            result = await self.cog._tool_browser_read({"action": "screenshot"}, context)
        self.assertEqual(len(context["pending_image_attachments"]), 1)
        self.assertEqual(context["pending_image_attachments"][0]["kind"], "browser")
        self.assertTrue(result["attachment_ref"].startswith("attachment://"))

        page.screenshot = AsyncMock(return_value=b"x" * (self.cog.BROWSER_SCREENSHOT_MAX_BYTES + 1))
        context = tool_context()
        with patch.object(self.cog.browser, "get_page", new=AsyncMock(return_value=(page, None))):
            result = await self.cog._tool_browser_read({"action": "screenshot"}, context)
        self.assertIn("error", result)
        self.assertNotIn("pending_image_attachments", context)

    async def test_browser_view_returns_visual_analysis_and_attaches_screenshot(self):
        page = SimpleNamespace(url="https://example.com/dashboard", screenshot=AsyncMock(return_value=b"png"))
        context = tool_context()
        analyze = AsyncMock(
            return_value={
                "analysis_model": "vision-model",
                "cost": 25.0,
                "currency": "coins",
                "summary": "A dashboard with a blue status chart.",
            }
        )
        with (
            patch.object(self.cog.browser, "get_page", new=AsyncMock(return_value=(page, None))),
            patch.object(self.cog, "_analyze_image_bytes_for_tool", new=analyze),
        ):
            result = await self.cog._tool_browser_view(
                {"prompt": "Inspect the status chart", "max_chars": 500},
                context,
            )

        self.assertEqual(result["analysis"]["summary"], "A dashboard with a blue status chart.")
        self.assertEqual(context["pending_image_attachments"][0]["kind"], "browser")
        self.assertTrue(result["attachment_ref"].startswith("attachment://browser-view-"))
        self.assertEqual(analyze.await_args.args[0], b"png")
        self.assertEqual(analyze.await_args.kwargs["prompt"], "Inspect the status chart")
        self.assertIn("untrusted webpage data", analyze.await_args.kwargs["system_prompt"])

    async def test_browser_view_rejects_oversize_before_analysis_or_attachment(self):
        page = SimpleNamespace(
            url="https://example.com/",
            screenshot=AsyncMock(return_value=b"x" * (self.cog.BROWSER_SCREENSHOT_MAX_BYTES + 1)),
        )
        context = tool_context()
        analyze = AsyncMock()
        with (
            patch.object(self.cog.browser, "get_page", new=AsyncMock(return_value=(page, None))),
            patch.object(self.cog, "_analyze_image_bytes_for_tool", new=analyze),
        ):
            result = await self.cog._tool_browser_view({}, context)

        self.assertIn("error", result)
        self.assertNotIn("pending_image_attachments", context)
        analyze.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
