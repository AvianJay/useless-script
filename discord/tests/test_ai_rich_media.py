import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai_rich_media import render_rich_markdown_images


ATTACHMENT_TAG_PATTERN = re.compile(
    r"<generated_image>(attachment://[^<]+)</generated_image>"
)


class AIRichMarkdownMediaTests(unittest.TestCase):
    def test_renders_tables_and_math_in_source_order(self):
        original = (
            "Start\n\n"
            "$$x^2$$\n\n"
            "| Route | ETA |\n"
            "|---|---:|\n"
            "| 307 | 5 min |\n\n"
            "$$y^2$$\n\n"
            "| Stop | Status |\n"
            "|---|---|\n"
            "| Main | Ready |\n\n"
            "End"
        )

        response, attachments = render_rich_markdown_images(original, max_images=9)

        self.assertEqual([item["kind"] for item in attachments], ["math", "table", "math", "table"])
        self.assertEqual(
            ATTACHMENT_TAG_PATTERN.findall(response),
            [f"attachment://{item['filename']}" for item in attachments],
        )
        self.assertTrue(response.startswith("Start"))
        self.assertTrue(response.endswith("End"))

    def test_shared_limit_is_consumed_in_source_order(self):
        original = (
            "| First | Value |\n"
            "|---|---|\n"
            "| A | 1 |\n\n"
            "$$x$$"
        )

        response, attachments = render_rich_markdown_images(original, max_images=1)

        self.assertEqual([item["kind"] for item in attachments], ["table"])
        self.assertEqual(response.count("<generated_image>"), 1)
        self.assertIn("$$x$$", response)

    def test_math_inside_table_is_not_rendered_separately(self):
        original = "| Formula |\n|---|\n| $$x^2$$ |"

        response, attachments = render_rich_markdown_images(original, max_images=9)

        self.assertEqual([item["kind"] for item in attachments], ["table"])
        self.assertNotIn("$$x^2$$", response)

    def test_code_fences_are_not_rendered(self):
        original = (
            "```markdown\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n"
            "$$x$$\n"
            "```"
        )

        response, attachments = render_rich_markdown_images(original, max_images=9)

        self.assertEqual(response, original)
        self.assertEqual(attachments, [])

    def test_failed_table_render_does_not_block_later_math(self):
        original = "| A | B |\n|---|---|\n| 1 | 2 |\n\n$$x$$"

        with patch("ai_rich_media.render_table_png", side_effect=ValueError("render failed")):
            response, attachments = render_rich_markdown_images(original, max_images=9)

        self.assertEqual([item["kind"] for item in attachments], ["math"])
        self.assertIn("| A | B |", response)
        self.assertNotIn("$$x$$", response)


if __name__ == "__main__":
    unittest.main()
