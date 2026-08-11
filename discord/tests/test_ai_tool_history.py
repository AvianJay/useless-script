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


if __name__ == "__main__":
    unittest.main()
