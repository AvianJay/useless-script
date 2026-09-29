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

    def test_api_history_marks_long_gap_on_user_message_only(self):
        start = 1_790_000_000.0
        history = [
            {"role": "user", "content": "晚安", "timestamp": start},
            {"role": "assistant", "content": "晚安～", "timestamp": start + 5},
            {"role": "user", "content": "早安", "timestamp": start + 5 + 9 * 3600},
            {"role": "assistant", "content": "早", "timestamp": start + 5 + 9 * 3600 + 5},
        ]

        formatted = ConversationManager.format_for_api(history)

        self.assertEqual(formatted[0]["content"], "晚安")
        self.assertTrue(formatted[2]["content"].startswith("[Time gap: about 9 hours after the previous message"))
        self.assertTrue(formatted[2]["content"].endswith("\n早安"))
        self.assertEqual(formatted[3]["content"], "早")

    def test_api_history_skips_short_gaps_and_missing_timestamps(self):
        start = 1_790_000_000.0
        history = [
            {"role": "user", "content": "a", "timestamp": start},
            {"role": "assistant", "content": "b", "timestamp": start + 60},
            {"role": "user", "content": "c", "timestamp": start + 600},
            {"role": "user", "content": "d"},
        ]

        formatted = ConversationManager.format_for_api(history)

        self.assertEqual([item["content"] for item in formatted], ["a", "b", "c", "d"])

    def test_time_gap_note_is_stable_for_same_timestamps(self):
        first = ConversationManager.build_time_gap_note(1_790_000_000.0, 1_790_000_000.0 + 3 * 86400)
        second = ConversationManager.build_time_gap_note(1_790_000_000.0, 1_790_000_000.0 + 3 * 86400)

        self.assertEqual(first, second)
        self.assertIn("about 3 days", first)
        self.assertIn("UTC+08:00", first)

    def test_describe_elapsed_uses_coarse_units(self):
        self.assertEqual(ConversationManager.describe_elapsed(45 * 60), "about 45 minutes")
        self.assertEqual(ConversationManager.describe_elapsed(3600), "about 1 hour")
        self.assertEqual(ConversationManager.describe_elapsed(20 * 86400), "about 2 weeks")
        self.assertEqual(ConversationManager.describe_elapsed(100 * 86400), "about 3 months")
        self.assertEqual(ConversationManager.describe_elapsed(800 * 86400), "about 2 years")


if __name__ == "__main__":
    unittest.main()
