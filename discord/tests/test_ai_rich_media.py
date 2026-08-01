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
        self.assertIn("```latex\nx\n```", response)
        self.assertNotIn("$$x$$", response)

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

    def test_single_dollar_math_uses_images_only_for_real_expressions(self):
        original = (
            "Variable $x$.\n"
            "- $132=6 \\times 22$\n"
            "- $7260=\\binom{6}{2}\\times 22^2+\\cdots$\n"
            "Near $\\sqrt{2}$.\n"
            "Use $x=22$.\n"
            "$22^6 - 132 \\cdot 22^5 + 7260 \\cdot 22^4$\n"
            "$$(x-8)(x-14)(x-22)(x-30)(x-36)(x-44)$$\n"
            "Answer when $x$ is a listed root."
        )

        response, attachments = render_rich_markdown_images(original, max_images=9)

        self.assertEqual([item["kind"] for item in attachments], ["math"] * 6)
        self.assertEqual(response.count("<generated_image>"), 6)
        self.assertEqual(response.count("`x`"), 2)
        self.assertNotIn("$132", response)
        self.assertNotIn("$$", response)

    def test_single_dollar_math_falls_back_to_code_without_media_budget(self):
        original = "Use $x$ and solve $x^2=4$."

        response, attachments = render_rich_markdown_images(original, max_images=0)

        self.assertEqual(attachments, [])
        self.assertEqual(response, "Use `x` and solve `x^2=4`.")

    def test_currency_prices_are_not_treated_as_math(self):
        samples = [
            "Kimi K3：$3 / $15（輸入／輸出，每百萬 token）",
            "Price: $3.50 / $15.00 per million tokens",
            "Budget: $1,000 / $2,000",
            "Regional: NT$100 / US$3",
            "A single price is $5$ today.",
            "Tickets cost $5 to $10 today.",
        ]

        for original in samples:
            with self.subTest(original=original):
                response, attachments = render_rich_markdown_images(original, max_images=9)

                self.assertEqual(response, original)
                self.assertEqual(attachments, [])

    def test_currency_pair_does_not_consume_later_inline_math(self):
        original = "Kimi costs $3 / $15; solve $x=2$ next."

        response, attachments = render_rich_markdown_images(original, max_images=9)

        self.assertEqual(len(attachments), 1)
        self.assertTrue(response.startswith("Kimi costs $3 / $15; solve "))
        self.assertNotIn("$x=2$", response)

    def test_currency_prices_inside_code_fence_are_preserved(self):
        original = "```text\nKimi K3: $3 / $15 per million tokens\n```"

        response, attachments = render_rich_markdown_images(original, max_images=9)

        self.assertEqual(response, original)
        self.assertEqual(attachments, [])

    def test_single_dollar_math_inside_code_is_preserved(self):
        original = "Use `$x^2$` or:\n```text\n$x=2$\n```"

        response, attachments = render_rich_markdown_images(original, max_images=9)

        self.assertEqual(response, original)
        self.assertEqual(attachments, [])


if __name__ == "__main__":
    unittest.main()
