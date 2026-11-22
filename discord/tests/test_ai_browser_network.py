import base64
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_browser_network import ChromiumRequestGuard, connection_policy


class BrowserRequestGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cdp = SimpleNamespace(send=AsyncMock())
        self.validate = AsyncMock(side_effect=lambda url: (url, None))
        self.guard = ChromiumRequestGuard(self.cdp, self.validate)
        self.guard.managed.add("managed")

    @staticmethod
    def event(frame="managed", url="https://site.example/app", resource_type="Document", **fields):
        return {"requestId": "request", "frameId": frame, "request": {"url": url},
                "resourceType": resource_type, **fields}

    def test_native_socket_policy_keeps_discord_gateways_and_workers(self):
        discord = connection_policy("https://discord.com/app")
        self.assertIn("wss://gateway.discord.gg", discord)
        self.assertIn("wss://*.discord.gg", discord)
        self.assertIn("worker-src 'self' blob: data:", discord)
        self.assertNotIn("wss://gateway.discord.gg", connection_policy("https://example.com/"))
        self.assertNotIn("wss://", connection_policy("http://example.com/"))
        self.assertNotIn(" ws:", discord)

    async def test_bystander_passes_without_dns_or_a_response_hook(self):
        self.guard._target({"targetInfo": {"targetId": "bystander", "type": "page"}})
        await self.guard._handle(self.event(frame="bystander"))
        self.validate.assert_not_awaited()
        self.cdp.send.assert_awaited_once_with("Fetch.continueRequest", {"requestId": "request"})

    async def test_new_popup_and_unknown_frame_are_checked_before_response(self):
        self.guard._target({"targetInfo": {"targetId": "popup", "type": "page", "openerId": "managed"}})
        for frame in ("popup", "not-announced-yet"):
            with self.subTest(frame=frame):
                await self.guard._handle(self.event(frame=frame))
                self.validate.assert_awaited_with("https://site.example/app")
                self.cdp.send.assert_awaited_with("Fetch.continueRequest", {"requestId": "request", "interceptResponse": True})
        self.validate.side_effect = None
        self.validate.return_value = (None, "non-public destination")
        await self.guard._handle(self.event(frame="popup", resource_type="XHR"))
        self.cdp.send.assert_awaited_with("Fetch.failRequest", {"requestId": "request", "errorReason": "BlockedByClient"})

    async def test_regular_script_does_not_force_buffering_but_worker_script_does(self):
        event = self.event(resource_type="Script")
        await self.guard._handle(event)
        self.cdp.send.assert_awaited_with("Fetch.continueRequest", {"requestId": "request"})
        event["request"]["headers"] = {"Sec-Fetch-Dest": "worker"}
        await self.guard._handle(event)
        self.cdp.send.assert_awaited_with("Fetch.continueRequest", {"requestId": "request", "interceptResponse": True})

    async def test_response_preserves_bytes_and_site_csp_and_passes_content_encoding_only_once(self):
        encoded = base64.b64encode(b"<html>\xe9</html>").decode("ascii")
        self.cdp.send.side_effect = [{"stream": "handle"}, {"data": encoded, "base64Encoded": True, "eof": True}, {}, {}]
        site_policy = {"name": "Content-Security-Policy", "value": "script-src 'self'"}
        content_type = {"name": "content-type", "value": "text/html; charset=iso-8859-1"}
        await self.guard._handle(self.event(responseStatusCode=200, responseHeaders=[site_policy, content_type,
            {"name": "Content-Encoding", "value": "gzip"}, {"name": "Content-Length", "value": "45"}]))
        method, params = self.cdp.send.await_args.args
        self.assertEqual(method, "Fetch.fulfillRequest")
        self.assertEqual(params["body"], encoded)
        self.assertEqual(params["responseHeaders"][:2], [site_policy, content_type])
        self.assertEqual(len(params["responseHeaders"]), 3)
        self.assertIn("connect-src", params["responseHeaders"][2]["value"])

    async def test_oversized_document_closes_stream_and_fails_closed(self):
        self.cdp.send.side_effect = [{"stream": "handle"}, {"data": "long", "eof": True}, {}, {}]
        with patch("ai_browser_network.MAX_POLICY_BODY", 3):
            await self.guard._handle(self.event(responseStatusCode=200,
                responseHeaders=[{"name": "Content-Type", "value": "text/html"}]))
        self.cdp.send.assert_any_await("IO.close", {"handle": "handle"})
        self.cdp.send.assert_awaited_with("Fetch.failRequest", {"requestId": "request", "errorReason": "BlockedByClient"})
