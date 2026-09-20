import sys
import unittest
from pathlib import Path


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import ConversationManager


class AIToolHistoryTests(unittest.TestCase):
    def test_legacy_tool_usage_line_is_removed(self):
        content = (
            "我來搜尋一下。\n"
            '[used tools: search_google(query="2023_myself who is", gl="tw")]'
        )

        cleaned = ConversationManager.strip_legacy_tool_usage_history(content)

        self.assertEqual(cleaned, "我來搜尋一下。")

    def test_inline_example_is_not_removed(self):
        content = "格式範例是 [used tools: search_google]，但這不是獨立紀錄行。"

        cleaned = ConversationManager.strip_legacy_tool_usage_history(content)

        self.assertEqual(cleaned, content)

    def test_api_history_excludes_legacy_tool_usage_lines(self):
        history = [
            {"role": "user", "content": "請搜尋"},
            {
                "role": "assistant",
                "content": "搜尋結果摘要\n[used tools: search_google(query=\"test\")]",
            },
        ]

        formatted = ConversationManager.format_for_api(history)

        self.assertEqual(
            formatted,
            [
                {"role": "user", "content": "請搜尋"},
                {"role": "assistant", "content": "搜尋結果摘要"},
            ],
        )

    def test_api_history_preserves_reasoning_content_for_thinking_mode(self):
        history = [
            {"role": "user", "content": "請先思考再回答"},
            {
                "role": "assistant",
                "content": "答案",
                "reasoning_content": "必須原樣回傳的思考內容",
            },
        ]

        formatted = ConversationManager.format_for_api(history)

        self.assertEqual(
            formatted[1],
            {
                "role": "assistant",
                "content": "答案",
                "reasoning_content": "必須原樣回傳的思考內容",
            },
        )

    def test_api_history_preserves_empty_reasoning_content_field(self):
        formatted = ConversationManager.format_for_api(
            [{"role": "assistant", "content": "答案", "reasoning_content": ""}]
        )

        self.assertIn("reasoning_content", formatted[0])
        self.assertEqual(formatted[0]["reasoning_content"], "")


if __name__ == "__main__":
    unittest.main()
