import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AICommands, normalize_piston_endpoint


class _FakeContent:
    def __init__(self, body: bytes):
        self.body = body

    async def iter_chunked(self, _size):
        yield self.body


class _FakeResponse:
    def __init__(self, status: int, body: bytes, charset: str = "utf-8"):
        self.status = status
        self.charset = charset
        self.content = _FakeContent(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _FakeSession:
    def __init__(self, response, capture: dict, **kwargs):
        self.response = response
        self.capture = capture
        self.capture["session_kwargs"] = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def post(self, url, **kwargs):
        self.capture["post_url"] = url
        self.capture["post_kwargs"] = kwargs
        return self.response


class AIPistonTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())

    def test_endpoint_normalization_accepts_private_server_root(self):
        normalized, error = normalize_piston_endpoint("HTTP://100.82.71.91:2000/")

        self.assertIsNone(error)
        self.assertEqual(normalized, "http://100.82.71.91:2000")

    def test_endpoint_normalization_rejects_unsafe_or_non_root_urls(self):
        invalid_values = (
            "ftp://example.com",
            "http://user:secret@example.com:2000",
            "http://example.com:2000/api/v2",
            "http://example.com:2000?mode=test",
            "http://example.com:2000/#fragment",
            "http://example.com:99999",
        )

        for value in invalid_values:
            with self.subTest(value=value):
                normalized, error = normalize_piston_endpoint(value)
                self.assertIsNone(normalized)
                self.assertIsNotNone(error)

    def test_execute_code_schema_and_result_budget_are_exposed(self):
        tools = {item["function"]["name"]: item["function"] for item in self.cog._build_ai_tools()}

        self.assertIn("execute_code", tools)
        schema = tools["execute_code"]["parameters"]
        self.assertEqual(schema["required"], ["language", "code"])
        self.assertEqual(schema["properties"]["args"]["items"], {"type": "string"})
        self.assertEqual(
            self.cog._tool_result_max_length("execute_code"),
            self.cog.PISTON_TOOL_RESULT_MAX_LENGTH,
        )

    async def test_owner_config_command_sets_normalized_endpoint(self):
        ctx = SimpleNamespace(send=AsyncMock())

        with patch("ai._set_piston_endpoint") as setter:
            await AICommands.ai_config_piston_endpoint_text.callback(
                self.cog,
                ctx,
                endpoint="HTTP://100.82.71.91:2000/",
            )

        setter.assert_called_once_with("http://100.82.71.91:2000")
        self.assertIn("http://100.82.71.91:2000", ctx.send.await_args.args[0])

    async def test_owner_config_command_views_and_clears_endpoint(self):
        ctx = SimpleNamespace(send=AsyncMock())
        with patch("ai._get_piston_endpoint", return_value="http://piston.example:2000"):
            await AICommands.ai_config_piston_endpoint_text.callback(
                self.cog,
                ctx,
                endpoint=None,
            )
        self.assertIn("http://piston.example:2000", ctx.send.await_args.args[0])

        ctx.send.reset_mock()
        with patch("ai._set_piston_endpoint") as setter:
            await AICommands.ai_config_piston_endpoint_text.callback(
                self.cog,
                ctx,
                endpoint="clear",
            )
        setter.assert_called_once_with("")
        self.assertIn("disabled", ctx.send.await_args.args[0])

    async def test_owner_config_command_rejects_invalid_endpoint(self):
        ctx = SimpleNamespace(send=AsyncMock())

        with patch("ai._set_piston_endpoint") as setter:
            await AICommands.ai_config_piston_endpoint_text.callback(
                self.cog,
                ctx,
                endpoint="http://user:secret@example.com:2000",
            )

        setter.assert_not_called()
        self.assertIn("Invalid Piston endpoint", ctx.send.await_args.args[0])

    def test_owner_config_command_keeps_owner_check(self):
        self.assertTrue(AICommands.ai_config_piston_endpoint_text.checks)

    async def test_disabled_endpoint_does_not_send_request(self):
        with (
            patch("ai._get_piston_endpoint", return_value=""),
            patch.object(self.cog, "_request_piston_execute", new=AsyncMock()) as request,
        ):
            result = await self.cog._tool_execute_code(
                {"language": "python", "code": "print(42)"},
                {},
            )

        self.assertIn("not configured", result["error"])
        request.assert_not_awaited()

    async def test_execute_code_builds_fixed_single_file_payload(self):
        response = {
            "language": "python",
            "version": "3.12.0",
            "run": {
                "stdout": "42\n",
                "stderr": "",
                "output": "42\n",
                "code": 0,
                "signal": None,
                "message": None,
                "status": None,
                "cpu_time": 8,
                "wall_time": 12,
                "memory": 1024,
            },
        }
        request = AsyncMock(return_value=(response, None))
        with (
            patch("ai._get_piston_endpoint", return_value="http://100.82.71.91:2000/"),
            patch.object(self.cog, "_request_piston_execute", new=request),
        ):
            result = await self.cog._tool_execute_code(
                {
                    "language": "python",
                    "code": "print(input(), __import__('sys').argv[1])",
                    "filename": "main.py",
                    "stdin": "42\n",
                    "args": ["ok"],
                },
                {},
            )

        endpoint, payload = request.await_args.args
        self.assertEqual(endpoint, "http://100.82.71.91:2000")
        self.assertEqual(payload["language"], "python")
        self.assertEqual(payload["version"], "*")
        self.assertEqual(
            payload["files"],
            [{"name": "main.py", "content": "print(input(), __import__('sys').argv[1])"}],
        )
        self.assertEqual(payload["stdin"], "42\n")
        self.assertEqual(payload["args"], ["ok"])
        self.assertEqual(payload["compile_timeout"], 10_000)
        self.assertEqual(payload["run_timeout"], 3_000)
        self.assertNotIn("compile_cpu_time", payload)
        self.assertNotIn("run_cpu_time", payload)
        self.assertEqual(result["run"]["stdout"], "42\n")
        self.assertNotIn("output", result["run"])

    async def test_compile_and_runtime_errors_are_preserved(self):
        cases = (
            (
                {
                    "language": "c",
                    "version": "10.2.0",
                    "compile": {
                        "stdout": "",
                        "stderr": "main.c: error",
                        "code": 1,
                        "signal": None,
                        "message": None,
                        "status": "RE",
                    },
                },
                "compile",
                "main.c: error",
            ),
            (
                {
                    "language": "python",
                    "version": "3.12.0",
                    "run": {
                        "stdout": "",
                        "stderr": "Traceback",
                        "code": 1,
                        "signal": None,
                        "message": None,
                        "status": "RE",
                    },
                },
                "run",
                "Traceback",
            ),
        )

        for response, stage, expected_stderr in cases:
            with self.subTest(stage=stage):
                with (
                    patch("ai._get_piston_endpoint", return_value="http://piston.example:2000"),
                    patch.object(
                        self.cog,
                        "_request_piston_execute",
                        new=AsyncMock(return_value=(response, None)),
                    ),
                ):
                    result = await self.cog._tool_execute_code(
                        {"language": response["language"], "code": "bad code"},
                        {},
                    )
                self.assertEqual(result[stage]["stderr"], expected_stderr)
                self.assertEqual(result[stage]["status"], "RE")

    async def test_request_error_is_returned_without_execution_data(self):
        request_error = {
            "error": "Piston returned HTTP 400: python-* runtime is unknown",
            "http_status": 400,
        }
        with (
            patch("ai._get_piston_endpoint", return_value="http://piston.example:2000"),
            patch.object(
                self.cog,
                "_request_piston_execute",
                new=AsyncMock(return_value=(None, request_error)),
            ),
        ):
            result = await self.cog._tool_execute_code(
                {"language": "python", "code": "print(42)"},
                {},
            )

        self.assertEqual(result, request_error)

    async def test_input_limits_reject_without_request(self):
        invalid_arguments = (
            {"language": "python", "code": "x" * (self.cog.PISTON_CODE_MAX_CHARS + 1)},
            {
                "language": "python",
                "code": "print(42)",
                "stdin": "x" * (self.cog.PISTON_STDIN_MAX_CHARS + 1),
            },
            {
                "language": "python",
                "code": "print(42)",
                "args": ["x"] * (self.cog.PISTON_MAX_ARGS + 1),
            },
            {
                "language": "python",
                "code": "print(42)",
                "args": ["x" * (self.cog.PISTON_ARG_MAX_CHARS + 1)],
            },
            {"language": "python", "code": "print(42)", "filename": "src/main.py"},
        )

        for arguments in invalid_arguments:
            with self.subTest(arguments=list(arguments)):
                request = AsyncMock()
                with (
                    patch("ai._get_piston_endpoint", return_value="http://piston.example:2000"),
                    patch.object(self.cog, "_request_piston_execute", new=request),
                ):
                    result = await self.cog._tool_execute_code(arguments, {})
                self.assertIn("error", result)
                request.assert_not_awaited()

    async def test_http_request_parses_success_and_uses_fixed_total_timeout(self):
        body = json.dumps(
            {
                "language": "python",
                "version": "3.12.0",
                "run": {"stdout": "42\n", "stderr": "", "code": 0},
            }
        ).encode()
        response = _FakeResponse(200, body)
        capture = {}

        with patch(
            "ai.aiohttp.ClientSession",
            side_effect=lambda **kwargs: _FakeSession(response, capture, **kwargs),
        ):
            data, error = await self.cog._request_piston_execute(
                "http://piston.example:2000",
                {"language": "python"},
            )

        self.assertIsNone(error)
        self.assertEqual(data["run"]["stdout"], "42\n")
        self.assertEqual(capture["session_kwargs"]["timeout"].total, 20)
        self.assertEqual(capture["post_url"], "http://piston.example:2000/api/v2/execute")
        self.assertEqual(capture["post_kwargs"]["json"], {"language": "python"})

    async def test_http_request_handles_unknown_runtime_and_invalid_json(self):
        cases = (
            (
                _FakeResponse(400, b'{"message":"python-* runtime is unknown"}'),
                "runtime is unknown",
                400,
            ),
            (_FakeResponse(200, b"not-json"), "invalid JSON", 200),
            (_FakeResponse(200, b""), "empty response", 200),
        )

        for response, expected_error, expected_status in cases:
            with self.subTest(expected_error=expected_error):
                with patch(
                    "ai.aiohttp.ClientSession",
                    side_effect=lambda **kwargs: _FakeSession(response, {}, **kwargs),
                ):
                    data, error = await self.cog._request_piston_execute(
                        "http://piston.example:2000",
                        {},
                    )
                self.assertIsNone(data)
                self.assertIn(expected_error, error["error"])
                self.assertEqual(error["http_status"], expected_status)

    async def test_http_request_ignores_an_invalid_declared_charset(self):
        response = _FakeResponse(
            200,
            b'{"language":"python","version":"3.12.0","run":{"stdout":"42\\n","code":0}}',
            charset="not-a-real-charset",
        )

        with patch(
            "ai.aiohttp.ClientSession",
            side_effect=lambda **kwargs: _FakeSession(response, {}, **kwargs),
        ):
            data, error = await self.cog._request_piston_execute(
                "http://piston.example:2000",
                {},
            )

        self.assertIsNone(error)
        self.assertEqual(data["run"]["stdout"], "42\n")

    async def test_http_request_handles_timeout_and_oversized_response(self):
        class TimeoutSession:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                raise asyncio.TimeoutError

            async def __aexit__(self, *_args):
                return False

        with patch("ai.aiohttp.ClientSession", TimeoutSession):
            data, error = await self.cog._request_piston_execute(
                "http://piston.example:2000",
                {},
            )
        self.assertIsNone(data)
        self.assertIn("timed out", error["error"])

        response = _FakeResponse(200, b"x" * 21)
        with (
            patch.object(self.cog, "PISTON_RESPONSE_MAX_BYTES", 20),
            patch(
                "ai.aiohttp.ClientSession",
                side_effect=lambda **kwargs: _FakeSession(response, {}, **kwargs),
            ),
        ):
            data, error = await self.cog._request_piston_execute(
                "http://piston.example:2000",
                {},
            )
        self.assertIsNone(data)
        self.assertIn("too large", error["error"])

    async def test_execute_code_is_registered_with_dispatcher(self):
        handler = AsyncMock(return_value={"run": {"stdout": "42\n"}})
        with patch.object(self.cog, "_tool_execute_code", new=handler):
            result = await self.cog._execute_ai_tool(
                "execute_code",
                {"language": "python", "code": "print(42)"},
                {},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["run"]["stdout"], "42\n")
        handler.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
