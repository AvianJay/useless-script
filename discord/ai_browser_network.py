"""Chromium request guard for the shared browser profile."""

import asyncio
import base64
from contextlib import suppress
from urllib.parse import urlsplit


MAX_POLICY_BODY = 8_000_000
WORKER_DESTINATIONS = {"worker", "sharedworker", "serviceworker"}
REPLACED_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}


def connection_policy(url):
    """保留網站自己的 CSP；只額外限制連線，不全面停用 Worker。"""
    parsed = urlsplit(url)
    sockets = []
    if parsed.scheme == "https" and parsed.hostname:
        sockets.append("wss://" + parsed.netloc)
        if parsed.hostname in {"discord.com", "canary.discord.com", "ptb.discord.com"}:
            sockets.extend(("wss://gateway.discord.gg", "wss://*.discord.gg"))
    return "connect-src http: https: blob: data:" + "".join(" " + s for s in sockets) + "; worker-src 'self' blob: data:"


class ChromiumRequestGuard:
    def __init__(self, cdp, validate_url):
        self.cdp = cdp
        self.validate_url = validate_url
        self.targets = {}
        self.managed = set()
        self.tasks = set()
        self.closing = False

    def _target(self, event):
        info = event["targetInfo"]
        target_id = info["targetId"]
        self.targets[target_id] = info
        if info.get("openerId") in self.managed:
            self.managed.add(target_id)

    def _destroyed(self, event):
        target = event["targetId"]
        self.targets.pop(target, None)
        self.managed.discard(target)

    def _paused(self, event):
        if not self.closing:
            task = asyncio.create_task(self._handle(event))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def start(self):
        self.cdp.on("Target.targetCreated", self._target)
        self.cdp.on("Target.targetInfoChanged", self._target)
        self.cdp.on("Target.targetDestroyed", self._destroyed)
        self.cdp.on("Fetch.requestPaused", self._paused)
        await self.cdp.send("Target.setDiscoverTargets", {"discover": True})
        await self.cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})

    async def close(self):
        if self.closing:
            return
        self.closing = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        with suppress(Exception):
            await asyncio.wait_for(self.cdp.send("Fetch.disable"), 3)
        with suppress(Exception):
            await asyncio.wait_for(self.cdp.detach(), 3)

    async def _handle(self, event):
        params = {"requestId": event["requestId"]}
        try:
            async with asyncio.timeout(15):
                await self._decide(event, params)
        except Exception:
            with suppress(Exception):
                await self.cdp.send("Fetch.failRequest", {**params, "errorReason": "BlockedByClient"})

    async def _decide(self, event, params):
        frame = event.get("frameId")
        # 已辨識的其他頂層分頁不改寫回應，也不做 DNS 查詢。
        if self.targets.get(frame, {}).get("type") == "page" and frame not in self.managed:
            await self.cdp.send("Fetch.continueRequest", params)
            return
        if "responseStatusCode" in event or "responseErrorReason" in event:
            await self._add_policy(event, params)
            return
        request = event["request"]
        url = request["url"]
        if urlsplit(url).scheme not in {"about", "blob", "data"}:
            normalized, error = await self.validate_url(url)
            if error or not normalized:
                await self.cdp.send("Fetch.failRequest", {**params, "errorReason": "BlockedByClient"})
                return
        destination = next((v.lower() for k, v in request.get("headers", {}).items()
                            if k.lower() == "sec-fetch-dest"), "")
        if event.get("resourceType") == "Document" or destination in WORKER_DESTINATIONS:
            params = {**params, "interceptResponse": True}
        await self.cdp.send("Fetch.continueRequest", params)

    async def _add_policy(self, event, params):
        status = event.get("responseStatusCode", 0)
        headers = event.get("responseHeaders", [])
        lookup = {entry["name"].lower(): entry["value"] for entry in headers}
        if lookup.get("content-disposition", "").lower().startswith("attachment"):
            await self.cdp.send("Fetch.failRequest", {**params, "errorReason": "BlockedByClient"})
            return
        if event.get("responseErrorReason") or status in {204, 205} or status < 200 or 300 <= status < 400:
            await self.cdp.send("Fetch.continueRequest", params)
            return
        content_type = lookup.get("content-type", "").partition(";")[0].strip().lower()
        if event.get("resourceType") == "Document" and content_type and not (
            content_type.startswith("text/") or content_type in {
                "application/xhtml+xml", "image/svg+xml", "application/xml", "application/octet-stream"
            }
        ):
            await self.cdp.send("Fetch.continueRequest", params)
            return
        # Chromium 146 未必會套用不帶 body 的標頭改寫。串流讀取確保有大小上限，
        # 保留原始 bytes（包含非 UTF-8），同時保留來源網站的 CSP 和其他標頭。
        stream = (await self.cdp.send("Fetch.takeResponseBodyAsStream", params))["stream"]
        body = bytearray()
        try:
            while True:
                chunk = await self.cdp.send("IO.read", {"handle": stream, "size": 65536})
                value = chunk["data"]
                body.extend(base64.b64decode(value) if chunk.get("base64Encoded") else value.encode("utf-8"))
                if len(body) > MAX_POLICY_BODY:
                    raise ValueError("Document exceeds browser policy body limit")
                if chunk["eof"]:
                    break
        finally:
            with suppress(Exception):
                await self.cdp.send("IO.close", {"handle": stream})
        await self.cdp.send("Fetch.fulfillRequest", {
            **params, "responseCode": status,
            "responseHeaders": [*(h for h in headers if h["name"].lower() not in REPLACED_HEADERS),
                                {"name": "Content-Security-Policy", "value": connection_policy(event["request"]["url"])}],
            "body": base64.b64encode(body).decode("ascii"),
        })
