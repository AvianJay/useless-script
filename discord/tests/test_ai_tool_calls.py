import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AICommands


class AIToolCallParsingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace(get_guild=lambda _guild_id: None))

    def test_emulated_tool_call_requires_explicit_envelope(self):
        message = SimpleNamespace(
            content=(
                '{"tool_calls":['
                '{"name":"get_bot_status","arguments":{}}'
                "]}"
            ),
            tool_calls=None,
        )

        calls = self.cog._extract_tool_calls(message)

        self.assertEqual(
            calls,
            [
                {
                    "id": "call_1",
                    "name": "get_bot_status",
                    "arguments": {},
                }
            ],
        )

    def test_activity_json_array_is_not_treated_as_tool_calls(self):
        message = SimpleNamespace(
            content=(
                "```json\n"
                '[{"type":"playing","name":"反覆重啟人生模擬器"},'
                '{"type":"watching","name":"Genshin Impact 啟動"},'
                '{"type":"listening","name":"你按下了 Alt+F4 試試"}]\n'
                "```"
            ),
            tool_calls=None,
        )

        self.assertEqual(self.cog._extract_tool_calls(message), [])
        progress_text = self.cog._extract_tool_progress_text(message.content)
        self.assertIn("反覆重啟人生模擬器", progress_text)
        self.assertIn("Genshin Impact 啟動", progress_text)

    def test_native_tool_call_is_still_supported(self):
        message = SimpleNamespace(
            content="我查一下。",
            tool_calls=[
                {
                    "id": "native-call-1",
                    "function": {
                        "name": "get_bot_status",
                        "arguments": "{}",
                    },
                }
            ],
        )

        self.assertEqual(
            self.cog._extract_tool_calls(message),
            [
                {
                    "id": "native-call-1",
                    "name": "get_bot_status",
                    "arguments": "{}",
                }
            ],
        )

    def test_single_named_json_object_is_not_treated_as_tool_call(self):
        message = SimpleNamespace(
            content='{"type":"playing","name":"角蛙 yt.ai"}',
            tool_calls=None,
        )

        self.assertEqual(self.cog._extract_tool_calls(message), [])

    async def test_activity_json_reaches_user_without_executing_tools(self):
        content = '[{"type":"playing","name":"角蛙 yt.ai"}]'
        response = SimpleNamespace(
            model="test-model",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=content,
                        tool_calls=None,
                        images=None,
                    )
                )
            ],
        )

        with (
            patch.object(
                self.cog,
                "_request_ai_completion",
                new=AsyncMock(return_value=(response, False)),
            ),
            patch.object(
                self.cog,
                "_execute_ai_tool",
                new=AsyncMock(),
            ) as execute_tool,
        ):
            response_text, model, _elapsed = await self.cog.generate_response(
                [{"role": "user", "content": "給我幾個 Discord 狀態"}],
                model="test-model",
                tool_context={"user": SimpleNamespace(id=1)},
            )

        self.assertEqual(response_text, content)
        self.assertEqual(model, "test-model")
        execute_tool.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
