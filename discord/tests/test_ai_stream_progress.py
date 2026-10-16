import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AICommands, AIStreamProgress


def delta_chunk(*, content=None, name=None, arguments=None):
    tool_calls = None
    if name is not None or arguments is not None:
        tool_calls = [
            SimpleNamespace(
                index=0,
                id="call_1" if name else None,
                type="function" if name else None,
                function=SimpleNamespace(name=name, arguments=arguments),
            )
        ]
    return SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                index=0,
                delta=SimpleNamespace(
                    role=None,
                    content=content,
                    reasoning_content=None,
                    images=None,
                    tool_calls=tool_calls,
                ),
                finish_reason=None,
                logprobs=None,
            )
        ],
    )


def usage_chunk(completion_tokens):
    return SimpleNamespace(usage=SimpleNamespace(completion_tokens=completion_tokens), choices=[])


def streamed_tool_round():
    return [
        delta_chunk(content="我先查一下。"),
        delta_chunk(name="get_bot_", arguments='{"full"'),
        delta_chunk(name="status", arguments=":true}"),
        usage_chunk(42),
    ]


async def wait_until(predicate, timeout=1.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition was not reached in time")
        await asyncio.sleep(0.005)


class AIStreamProgressTests(unittest.TestCase):
    def test_collector_records_streaming_tool_call(self):
        progress = AIStreamProgress()

        response = AICommands._collect_ai_completion_stream(
            streamed_tool_round(),
            fallback_model="test-model",
            progress=progress,
        )

        tool_call = response.choices[0].message.tool_calls[0]
        self.assertEqual(tool_call.function.name, "get_bot_status")
        self.assertEqual(tool_call.function.arguments, '{"full":true}')
        self.assertEqual(progress.tool_names, ("get_bot_status",))
        self.assertEqual(progress.content_head, "我先查一下。")
        self.assertEqual(progress.tokens, 42)
        self.assertIsNotNone(progress.first_token_at)
        self.assertGreaterEqual(progress.finished_at, progress.first_token_at)

    def test_collector_without_progress_still_rebuilds_response(self):
        response = AICommands._collect_ai_completion_stream(streamed_tool_round(), fallback_model="test-model")

        self.assertEqual(response.choices[0].message.content, "我先查一下。")

    def test_estimate_counts_cjk_per_character(self):
        progress = AIStreamProgress()
        progress.add_text("你好，世界")
        progress.add_text("こんにちは")
        progress.add_text("abcdefgh")

        self.assertEqual(progress.tokens, 12)

    def test_reported_usage_replaces_the_estimate(self):
        progress = AIStreamProgress()
        progress.add_text("abcd" * 10)
        progress.record_usage(None)
        self.assertEqual(progress.tokens, 10)

        progress.record_usage("37")
        progress.record_usage(0)
        self.assertEqual(progress.tokens, 37)

    def test_content_head_is_capped(self):
        progress = AIStreamProgress()
        progress.add_text("a" * 300, content=True)
        progress.add_text("b" * 300, content=True)
        progress.add_text("thinking", content=False)

        self.assertEqual(len(progress.content_head), AIStreamProgress.CONTENT_HEAD_MAX_CHARS)
        self.assertNotIn("thinking", progress.content_head)

    def test_stats_use_live_rate_while_receiving_and_request_rate_when_done(self):
        progress = AIStreamProgress()
        progress.started_at = 100.0
        progress.first_token_at = 102.0
        progress.estimated_tokens = 400.0

        self.assertEqual(
            AICommands._format_ai_stream_stats(progress, receiving=True, now=106.0),
            "↓ 400 Tokens | 100.0 TPS",
        )

        progress.finished_at = 110.0
        progress.record_usage(1500)
        self.assertEqual(AICommands._format_ai_stream_stats(progress), "↑ 1,500 Tokens | 150.0 TPS")

    def test_stats_are_empty_when_nothing_streamed(self):
        self.assertEqual(AICommands._format_ai_stream_stats(None), "")
        self.assertEqual(AICommands._format_ai_stream_stats(AIStreamProgress()), "")


class StreamingToolNoticeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())

    @staticmethod
    def tool_progress(*, content=""):
        progress = AIStreamProgress()
        if content:
            progress.add_text(content, content=True)
        progress.add_text("search_google")
        progress.tool_names = ("search_google",)
        return progress

    async def test_receiving_notice_marks_the_label_and_keeps_stats_above_status(self):
        plain = await self.cog._build_tool_usage_notice([{"name": "search_google"}])
        notice = await self.cog._build_tool_usage_notice(
            [{"name": "search_google"}],
            status_text="Connecting to the browser...",
            stats_text="↓ 10 Tokens | 2.0 TPS",
            receiving=True,
        )

        lines = notice.splitlines()
        self.assertEqual(lines[0], plain + "...")
        self.assertEqual(lines[-2], "-# ↓ 10 Tokens | 2.0 TPS")
        self.assertEqual(lines[-1], "-# Connecting to the browser...")

    async def test_receiving_narration_is_not_double_ellipsized(self):
        narration = await self.cog._build_tool_usage_notice(
            [{"name": "search_google"}], progress_text="Let me check", receiving=True
        )
        trailing = await self.cog._build_tool_usage_notice(
            [{"name": "search_google"}], progress_text="Hmm...", receiving=True
        )
        sentence = await self.cog._build_tool_usage_notice(
            [{"name": "search_google"}], progress_text="我先查一下。", receiving=True
        )

        self.assertTrue(narration.endswith(" Let me check..."))
        self.assertTrue(trailing.endswith(" Hmm..."))
        self.assertTrue(sentence.endswith(" 我先查一下..."))

    async def test_streams_without_tool_calls_never_touch_the_notice(self):
        progress = AIStreamProgress()
        progress.add_text("a plain answer", content=True)
        callback = AsyncMock()

        with patch.object(AICommands, "TOOL_STREAM_POLL_SECONDS", 0.01):
            async with self.cog._streaming_tool_notice(progress, callback, 1):
                await asyncio.sleep(0.05)

        callback.assert_not_awaited()

    async def test_tool_call_shows_while_streaming_then_gains_live_stats(self):
        progress = self.tool_progress(content="Let me look.")
        calls = []

        async def callback(tool_calls, round_index, progress_text="", status_text="", stats_text="", receiving=False):
            calls.append((tool_calls, round_index, progress_text, stats_text, receiving))

        with patch.multiple(
            AICommands,
            TOOL_STREAM_POLL_SECONDS=0.01,
            TOOL_STREAM_NOTICE_INTERVAL_SECONDS=0.0,
            TOOL_STREAM_STATS_DELAY_SECONDS=5.0,
        ):
            async with self.cog._streaming_tool_notice(progress, callback, 2):
                await wait_until(lambda: calls)
                progress.first_token_at -= 10
                await wait_until(lambda: len(calls) >= 2)

        self.assertEqual(calls[0], ([{"name": "search_google"}], 2, "Let me look.", "", True))
        _tool_calls, _round_index, _progress_text, stats_text, receiving = calls[1]
        self.assertRegex(stats_text, r"^↓ \d+ Tokens \| \d+\.\d TPS$")
        self.assertTrue(receiving)

    async def test_edits_are_paced(self):
        progress = self.tool_progress()
        callback = AsyncMock()

        with patch.multiple(
            AICommands,
            TOOL_STREAM_POLL_SECONDS=0.01,
            TOOL_STREAM_NOTICE_INTERVAL_SECONDS=0.2,
            TOOL_STREAM_STATS_DELAY_SECONDS=0.0,
        ):
            async with self.cog._streaming_tool_notice(progress, callback, 1):
                await asyncio.sleep(0.3)

        # Stats change on every poll; without pacing this would be ~30 edits.
        self.assertIn(callback.await_count, (1, 2))

    async def test_round_exit_waits_for_the_in_flight_edit(self):
        progress = self.tool_progress()
        started = asyncio.Event()
        release = asyncio.Event()
        finished = []

        async def callback(*_args, **_kwargs):
            started.set()
            await release.wait()
            finished.append(True)

        async def run_round():
            async with self.cog._streaming_tool_notice(progress, callback, 1):
                await started.wait()
            return list(finished)

        with patch.object(AICommands, "TOOL_STREAM_POLL_SECONDS", 0.01):
            round_task = asyncio.create_task(run_round())
            await asyncio.wait_for(started.wait(), timeout=1)
            await asyncio.sleep(0.03)
            self.assertFalse(round_task.done())
            release.set()
            self.assertEqual(await asyncio.wait_for(round_task, timeout=1), [True])

    async def test_failed_edit_stops_the_watcher(self):
        progress = self.tool_progress()
        callback = AsyncMock(side_effect=RuntimeError("Unknown Message"))

        with patch.multiple(
            AICommands,
            TOOL_STREAM_POLL_SECONDS=0.01,
            TOOL_STREAM_NOTICE_INTERVAL_SECONDS=0.0,
            TOOL_STREAM_STATS_DELAY_SECONDS=0.0,
        ):
            async with self.cog._streaming_tool_notice(progress, callback, 1):
                await asyncio.sleep(0.08)

        self.assertEqual(callback.await_count, 1)

    async def test_ai_completion_feeds_progress_without_leaking_it_to_the_provider(self):
        create_completion = MagicMock(return_value=streamed_tool_round())
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_completion)))
        progress = AIStreamProgress()

        with (
            patch("ai._get_ai_model_rates", return_value={"test-model": 0.1}),
            patch("ai._create_ai_client", return_value=client),
        ):
            await self.cog._generate_ai_completion(
                model="test-model",
                messages=[{"role": "user", "content": "bot 在線嗎"}],
                stream_progress=progress,
            )

        self.assertNotIn("stream_progress", create_completion.call_args.kwargs)
        self.assertEqual(progress.tool_names, ("get_bot_status",))
        self.assertEqual(progress.tokens, 42)

    async def test_round_notice_reports_the_rounds_tokens(self):
        tool_round = SimpleNamespace(
            model="test-model",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="我先查一下。",
                        tool_calls=[{"id": "call-1", "function": {"name": "get_bot_status", "arguments": "{}"}}],
                        images=None,
                    )
                )
            ],
        )
        final_round = SimpleNamespace(
            model="test-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content="在線。", tool_calls=None, images=None))],
        )
        responses = [tool_round, final_round]

        async def request(_messages, **kwargs):
            progress = kwargs["stream_progress"]
            progress.started_at = 10.0
            progress.add_text("我先查一下。", content=True)
            progress.record_usage(120)
            progress.finished_at = 12.0
            return responses.pop(0), "native"

        calls = []

        async def callback(tool_calls, round_index, progress_text="", status_text="", stats_text="", receiving=False):
            calls.append((round_index, progress_text, stats_text, receiving))

        with (
            patch.object(self.cog, "_request_ai_completion", new=request),
            patch.object(self.cog, "_execute_ai_tool", new=AsyncMock(return_value={"ok": True, "data": {}})),
        ):
            text, _model, _elapsed, _reasoning = await self.cog.generate_response(
                [{"role": "user", "content": "bot 在線嗎"}],
                model="test-model",
                tool_context={"user": SimpleNamespace(id=1)},
                tool_progress_callback=callback,
            )

        self.assertEqual(text, "在線。")
        self.assertEqual(calls, [(1, "我先查一下。", "↑ 120 Tokens | 60.0 TPS", False)])


if __name__ == "__main__":
    unittest.main()
