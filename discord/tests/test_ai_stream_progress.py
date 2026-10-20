import asyncio
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AICommands, AIStreamProgress
from i18n import t


def delta_chunk(*, content=None, reasoning=None, name=None, arguments=None):
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
                    reasoning_content=reasoning,
                    images=None,
                    tool_calls=tool_calls,
                ),
                finish_reason=None,
                logprobs=None,
            )
        ],
    )


def usage_chunk(completion_tokens, reasoning_tokens=None):
    details = SimpleNamespace(reasoning_tokens=reasoning_tokens) if reasoning_tokens is not None else None
    usage = SimpleNamespace(completion_tokens=completion_tokens, completion_tokens_details=details)
    return SimpleNamespace(usage=usage, choices=[])


def streamed_tool_round():
    return [
        delta_chunk(content="我先查一下。"),
        delta_chunk(name="get_bot_", arguments='{"full"'),
        delta_chunk(name="status", arguments=":true}"),
        usage_chunk(42),
    ]


def first_token_text(seconds: str) -> str:
    return t("ai.value.stream_first_token", seconds=seconds)


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
        self.assertEqual(progress.tool_calls, (("get_bot_status", '{"full":true}'),))
        self.assertEqual(progress.content_head, "我先查一下。")
        self.assertEqual(progress.tokens, 42)
        self.assertIsNotNone(progress.first_token_at)
        self.assertGreaterEqual(progress.finished_at, progress.first_token_at)

    def test_collector_without_progress_still_rebuilds_response(self):
        response = AICommands._collect_ai_completion_stream(streamed_tool_round(), fallback_model="test-model")

        self.assertEqual(response.choices[0].message.content, "我先查一下。")

    def test_collector_records_reported_reasoning_tokens(self):
        progress = AIStreamProgress()

        AICommands._collect_ai_completion_stream(
            [delta_chunk(reasoning="想一想"), delta_chunk(content="好"), usage_chunk(42, 30)],
            fallback_model="test-model",
            progress=progress,
        )

        self.assertEqual((progress.tokens, progress.reasoning), (42, 30))

    def test_collector_stops_reading_once_cancelled(self):
        progress = AIStreamProgress()

        class Stream:
            pulled = 0
            closed = False

            def __iter__(self):
                for _ in range(100):
                    self.pulled += 1
                    if self.pulled == 3:
                        progress.cancelled = True
                    yield delta_chunk(content="x")

            def close(self):
                self.closed = True

        stream = Stream()
        response = AICommands._collect_ai_completion_stream(stream, fallback_model="test-model", progress=progress)

        self.assertEqual(stream.pulled, 3)
        self.assertTrue(stream.closed)
        self.assertEqual(response.choices[0].message.content, "xx")

    def test_estimate_counts_cjk_per_character(self):
        progress = AIStreamProgress()
        progress.add_text("你好，世界")
        progress.add_text("こんにちは")
        progress.add_text("abcdefgh")

        self.assertEqual(progress.tokens, 12)

    def test_reported_usage_replaces_the_estimate(self):
        progress = AIStreamProgress()
        progress.add_text("abcd" * 8, reasoning=True)
        progress.add_text("efgh" * 4)
        self.assertEqual((progress.tokens, progress.reasoning), (12, 8))

        progress.record_usage(None)
        progress.record_usage(0, 5)
        self.assertEqual((progress.tokens, progress.reasoning), (12, 8))

        progress.record_usage("40", 25)
        self.assertEqual((progress.tokens, progress.reasoning), (40, 25))

        progress.record_usage(50)
        self.assertEqual((progress.tokens, progress.reasoning), (50, 8))

    def test_content_head_is_capped(self):
        progress = AIStreamProgress()
        progress.add_text("a" * 300, content=True)
        progress.add_text("b" * 300, content=True)
        progress.add_text("thinking", reasoning=True)

        self.assertEqual(len(progress.content_head), AIStreamProgress.CONTENT_HEAD_MAX_CHARS)
        self.assertNotIn("thinking", progress.content_head)

    def test_tool_call_arguments_head_is_capped_but_still_counted(self):
        progress = AIStreamProgress()
        progress.add_tool_call_delta(0, "upload_html", '{"html":"' + "x" * 1000)
        head = progress.tool_calls
        progress.add_tool_call_delta(0, None, "y" * 400)

        self.assertIs(progress.tool_calls, head)
        self.assertEqual(len(head[0][1]), AIStreamProgress.ARGUMENTS_HEAD_MAX_CHARS)
        self.assertEqual(progress.tokens, round((len("upload_html") + 9 + 1000 + 400) / 4))

    def test_stats_use_live_rate_while_receiving_and_request_rate_when_done(self):
        progress = AIStreamProgress()
        progress.started_at = 100.0
        progress.first_token_at = 102.0
        progress.estimated_tokens = 400.0

        self.assertEqual(
            AICommands._format_ai_stream_stats(progress, receiving=True, now=106.0),
            f"↓ 400 Tokens | 100.0 TPS | {first_token_text('2.0')}",
        )

        progress.finished_at = 110.0
        progress.record_usage(1500)
        self.assertEqual(
            AICommands._format_ai_stream_stats(progress),
            f"↑ 1,500 Tokens | 150.0 TPS | {first_token_text('2.0')}",
        )

    def test_stats_show_the_reasoning_share(self):
        progress = AIStreamProgress()
        progress.started_at = 0.0
        progress.first_token_at = 1.0
        progress.finished_at = 4.0
        progress.record_usage(2000, 1500)

        reasoning_text = t("ai.value.stream_reasoning_tokens", tokens="1,500")
        self.assertEqual(
            AICommands._format_ai_stream_stats(progress),
            f"↑ 2,000 Tokens ({reasoning_text}) | 500.0 TPS | {first_token_text('1.0')}",
        )

    def test_stats_are_empty_when_nothing_streamed(self):
        self.assertEqual(AICommands._format_ai_stream_stats(None), "")
        self.assertEqual(AICommands._format_ai_stream_stats(AIStreamProgress()), "")


class ToolUsageDetailTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())

    async def test_label_shows_its_argument_once_the_value_is_complete(self):
        label = t("ai.tool_label.search_google")
        with_query = t("ai.value.tool_label_detail", label=label, detail="台北天氣")

        complete = await self.cog._build_tool_usage_notice(
            [{"name": "search_google", "arguments": '{"query": "台北天氣", "gl": "tw"}'}]
        )
        streaming = await self.cog._build_tool_usage_notice(
            [{"name": "search_google", "arguments": '{"query": "台北天'}]
        )
        emulated = await self.cog._build_tool_usage_notice(
            [{"name": "search_google", "arguments": {"query": "台北天氣"}}]
        )

        self.assertTrue(complete.endswith(with_query))
        self.assertTrue(emulated.endswith(with_query))
        self.assertTrue(streaming.endswith(f" {label}"))

    def test_fetch_preview_shows_only_the_domain_for_complete_and_streamed_arguments(self):
        url = "https://user:password@news.example.com:443/a_b?q=private#section"
        for tool_name in ("fetch_webpage", "fetch_raw"):
            for arguments in ({"url": url}, '{"url": "' + url + '", "max_chars":'):
                with self.subTest(tool_name=tool_name, arguments=arguments):
                    detail = AICommands._tool_usage_detail(tool_name, arguments)
                    self.assertEqual(detail, "news.example.com")

    def test_invalid_or_unfinished_urls_show_no_preview(self):
        for arguments in (
            {"url": "https://"},
            {"url": "https://[invalid/path"},
            {"url": "javascript:alert(1)"},
            {"url": "example.com/path"},
            '{"url": "https://example.com/path',
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(AICommands._tool_usage_detail("fetch_webpage", arguments), "")

    def test_long_values_are_truncated_and_mentions_escaped(self):
        long_detail = AICommands._tool_usage_detail("generate_image", {"prompt": "x" * 200})
        mention_detail = AICommands._tool_usage_detail("search_google", {"query": "@everyone hi"})

        self.assertEqual(len(long_detail), AICommands.TOOL_USAGE_DETAIL_MAX_LENGTH)
        self.assertTrue(long_detail.endswith("..."))
        self.assertNotIn("@everyone", mention_detail)

    def test_tools_without_a_public_field_show_no_arguments(self):
        self.assertEqual(AICommands._tool_usage_detail("browser_act", '{"text": "secret"}'), "")
        self.assertEqual(AICommands._tool_usage_detail("search_google", '{"gl": "tw"}'), "")
        self.assertEqual(AICommands._tool_usage_detail("search_google", '{"query": 5}'), "")


class StreamingNoticeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())

    @staticmethod
    def tool_progress(*, content=""):
        progress = AIStreamProgress()
        if content:
            progress.add_text(content, content=True)
        progress.add_tool_call_delta(0, "search_google", '{"query":"台北天氣"}')
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

    async def test_short_plain_streams_never_touch_the_notice(self):
        progress = AIStreamProgress()
        progress.add_text("a plain answer", content=True)
        callback = AsyncMock()

        with patch.object(AICommands, "STREAM_NOTICE_POLL_SECONDS", 0.01):
            async with self.cog._streaming_notice(progress, callback, 1):
                await asyncio.sleep(0.05)

        callback.assert_not_awaited()

    async def test_long_stream_without_tool_call_shows_thinking_then_writing(self):
        progress = AIStreamProgress()
        progress.add_text("hmm", reasoning=True)
        progress.first_token_at -= 10
        calls = []

        async def callback(tool_calls, round_index, progress_text="", status_text="", stats_text="", receiving=False):
            calls.append((tool_calls, progress_text, stats_text, receiving))

        with patch.multiple(
            AICommands,
            STREAM_NOTICE_POLL_SECONDS=0.01,
            STREAM_NOTICE_INTERVAL_SECONDS=0.0,
            STREAM_STATS_DELAY_SECONDS=5.0,
        ):
            async with self.cog._streaming_notice(progress, callback, 1):
                await wait_until(lambda: calls)
                progress.add_text("答案", content=True)
                await wait_until(lambda: calls[-1][1] == t("ai.value.stream_writing"))

        tool_calls, progress_text, stats_text, receiving = calls[0]
        self.assertEqual((tool_calls, progress_text, receiving), ([], t("ai.value.stream_thinking"), True))
        self.assertTrue(stats_text.startswith("↓ "))

    async def test_tool_call_shows_while_streaming_then_gains_live_stats(self):
        progress = self.tool_progress(content="Let me look.")
        calls = []

        async def callback(tool_calls, round_index, progress_text="", status_text="", stats_text="", receiving=False):
            calls.append((tool_calls, round_index, progress_text, stats_text, receiving))

        with patch.multiple(
            AICommands,
            STREAM_NOTICE_POLL_SECONDS=0.01,
            STREAM_NOTICE_INTERVAL_SECONDS=0.0,
            STREAM_STATS_DELAY_SECONDS=5.0,
        ):
            async with self.cog._streaming_notice(progress, callback, 2):
                await wait_until(lambda: calls)
                progress.first_token_at -= 10
                await wait_until(lambda: len(calls) >= 2)

        streaming_call = [{"name": "search_google", "arguments": '{"query":"台北天氣"}'}]
        self.assertEqual(calls[0], (streaming_call, 2, "Let me look.", "", True))
        _tool_calls, _round_index, _progress_text, stats_text, receiving = calls[1]
        self.assertRegex(stats_text, r"^↓ \d+ Tokens \| \d+\.\d TPS \| ")
        self.assertTrue(receiving)

    async def test_edits_are_paced(self):
        progress = self.tool_progress()
        callback = AsyncMock()

        with patch.multiple(
            AICommands,
            STREAM_NOTICE_POLL_SECONDS=0.01,
            STREAM_NOTICE_INTERVAL_SECONDS=0.2,
            STREAM_STATS_DELAY_SECONDS=0.0,
        ):
            async with self.cog._streaming_notice(progress, callback, 1):
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
            async with self.cog._streaming_notice(progress, callback, 1):
                await started.wait()
            return list(finished)

        with patch.object(AICommands, "STREAM_NOTICE_POLL_SECONDS", 0.01):
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
            STREAM_NOTICE_POLL_SECONDS=0.01,
            STREAM_NOTICE_INTERVAL_SECONDS=0.0,
            STREAM_STATS_DELAY_SECONDS=0.0,
        ):
            async with self.cog._streaming_notice(progress, callback, 1):
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
        self.assertEqual(progress.tool_calls, (("get_bot_status", '{"full":true}'),))
        self.assertEqual(progress.tokens, 42)

    async def test_cancelled_request_stops_reading_the_stream(self):
        pulled = []
        closed = threading.Event()

        class SlowStream:
            def __iter__(self):
                for _ in range(500):
                    pulled.append(1)
                    time.sleep(0.01)
                    yield delta_chunk(content="x")

            def close(self):
                closed.set()

        create_completion = MagicMock(return_value=SlowStream())
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_completion)))

        with (
            patch("ai._get_ai_model_rates", return_value={"test-model": 0.1}),
            patch("ai._create_ai_client", return_value=client),
        ):
            request = asyncio.create_task(
                self.cog._generate_ai_completion(
                    model="test-model",
                    messages=[{"role": "user", "content": "寫一篇長文"}],
                )
            )
            await wait_until(lambda: len(pulled) >= 3)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            self.assertTrue(await asyncio.to_thread(closed.wait, 2))

        self.assertLess(len(pulled), 500)

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
            progress.add_text("我先查一下。", content=True)
            progress.started_at, progress.first_token_at, progress.finished_at = 10.0, 10.5, 12.0
            progress.record_usage(120)
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
        self.assertEqual(
            calls,
            [(1, "我先查一下。", f"↑ 120 Tokens | 60.0 TPS | {first_token_text('0.5')}", False)],
        )

    async def test_forced_final_round_is_watched_too(self):
        tool_round = SimpleNamespace(
            model="test-model",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[{"id": "call-1", "function": {"name": "get_bot_status", "arguments": "{}"}}],
                        images=None,
                    )
                )
            ],
        )
        final_round = SimpleNamespace(
            model="test-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content="好了。", tool_calls=None, images=None))],
        )
        request = AsyncMock(side_effect=[(tool_round, "native"), (final_round, None)])

        with (
            patch.object(AICommands, "MAX_TOOL_ITERATIONS", 1),
            patch.object(self.cog, "_request_ai_completion", new=request),
            patch.object(self.cog, "_execute_ai_tool", new=AsyncMock(return_value={"ok": True, "data": {}})),
        ):
            text, *_ = await self.cog.generate_response(
                [{"role": "user", "content": "bot 在線嗎"}],
                model="test-model",
                tool_context={"user": SimpleNamespace(id=1)},
                tool_progress_callback=AsyncMock(),
            )

        self.assertEqual(text, "好了。")
        self.assertIsInstance(request.await_args_list[-1].kwargs["stream_progress"], AIStreamProgress)


if __name__ == "__main__":
    unittest.main()
