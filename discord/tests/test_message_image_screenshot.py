import asyncio
import io
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import MessageImage


class ScreenshotMessageCollectionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _message(message_id, author_id, created_at, channel=None):
        return SimpleNamespace(
            id=message_id,
            author=SimpleNamespace(id=author_id),
            created_at=created_at,
            channel=channel,
        )

    async def test_context_is_newest_first_and_stops_at_other_author(self):
        now = datetime.now(timezone.utc)
        channel = SimpleNamespace()
        target = self._message(3, 10, now, channel)
        previous = self._message(2, 10, now - timedelta(minutes=1), channel)
        interruption = self._message(1, 20, now - timedelta(minutes=2), channel)

        async def history(**kwargs):
            self.assertEqual(kwargs["limit"], 9)
            yield previous
            yield interruption

        channel.history = history
        messages = await MessageImage._collect_screenshot_messages(
            target,
            include_context=True,
            context_limit=10,
            context_window_seconds=300,
        )

        self.assertEqual([message.id for message in messages], [3, 2])

    async def test_single_message_mode_does_not_read_history(self):
        channel = SimpleNamespace(history=AsyncMock())
        target = self._message(3, 10, datetime.now(timezone.utc), channel)

        messages = await MessageImage._collect_screenshot_messages(
            target,
            include_context=False,
            context_limit=10,
            context_window_seconds=300,
        )

        self.assertEqual(messages, [target])
        channel.history.assert_not_called()


class ScreenshotBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_render_expands_spoilers_and_closes_page(self):
        locator = SimpleNamespace(
            bounding_box=AsyncMock(return_value={"width": 600, "height": 200, "x": 0, "y": 0}),
            screenshot=AsyncMock(return_value=b"png"),
        )
        page = SimpleNamespace(
            set_content=AsyncMock(),
            add_style_tag=AsyncMock(),
            evaluate=AsyncMock(),
            locator=lambda _selector: locator,
            set_viewport_size=AsyncMock(),
            close=AsyncMock(),
        )
        fake_browser = SimpleNamespace(
            is_connected=lambda: True,
            new_page=AsyncMock(return_value=page),
        )
        channel = SimpleNamespace(id=22, guild=SimpleNamespace(id=1))
        message = SimpleNamespace(
            id=33,
            channel=channel,
            created_at=datetime.now(timezone.utc),
        )
        render_metadata = {}

        with (
            patch.object(MessageImage, "browser", fake_browser),
            patch.object(MessageImage.chat_exporter, "raw_export", new=AsyncMock(return_value="<html></html>")),
        ):
            rendered = await MessageImage._render_message_screenshot(
                message,
                include_context=False,
                context_limit=10,
                context_window_seconds=300,
                render_metadata=render_metadata,
            )

        self.assertEqual(rendered, b"png")
        page.evaluate.assert_awaited_once()
        self.assertIn("spoiler--hidden", page.evaluate.await_args.args[0])
        page.close.assert_awaited_once()
        self.assertEqual(render_metadata["message_ids"], ["33"])
        self.assertEqual(render_metadata["display_order"], "oldest_to_newest")

    async def test_size_limit_is_enforced(self):
        with patch.object(
            MessageImage,
            "_render_message_screenshot",
            new=AsyncMock(return_value=b"12345"),
        ):
            with self.assertRaisesRegex(Exception, "exceeds 4 bytes"):
                await MessageImage.screenshot(SimpleNamespace(), max_bytes=4)

    async def test_timeout_is_reported(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(1)
            return b"png"

        with patch.object(MessageImage, "_render_message_screenshot", new=slow):
            with self.assertRaisesRegex(Exception, "timed out"):
                await MessageImage.screenshot(SimpleNamespace(), timeout=0.01)


if __name__ == "__main__":
    unittest.main()
