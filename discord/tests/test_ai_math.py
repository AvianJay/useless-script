import sys
import unittest
from pathlib import Path


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai_math import render_display_math, render_math_png


class AIMathRenderingTests(unittest.TestCase):
    def test_renders_supported_display_math_as_inline_attachment(self):
        response, attachments = render_display_math(
            "Before\n\n$$f_{\\text{max}} = \\frac{f_s}{2}$$\n\nAfter"
        )

        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["kind"], "math")
        self.assertTrue(attachments[0]["content"].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(
            f"<generated_image>attachment://{attachments[0]['filename']}</generated_image>",
            response,
        )
        self.assertTrue(response.startswith("Before"))
        self.assertTrue(response.endswith("After"))

    def test_renders_multiple_supported_expressions(self):
        response, attachments = render_display_math(
            "$$f_{\\text{max}} = \\frac{f_s}{2}$$ "
            "$$\\text{DR} \\approx 6.02 \\times \\text{bit} \\quad (\\text{dB})$$"
        )

        self.assertEqual(len(attachments), 2)
        self.assertNotIn("$$", response)

    def test_preserves_math_inside_code(self):
        original = "```text\n$$not_math$$\n``` and `$$also_not_math$$`"
        response, attachments = render_display_math(original)

        self.assertEqual(response, original)
        self.assertEqual(attachments, [])

    def test_preserves_invalid_math(self):
        original = r"The result is $$\not_a_mathtext_command{value}$$."
        response, attachments = render_display_math(original)

        self.assertEqual(response, original)
        self.assertEqual(attachments, [])

    def test_respects_image_limit(self):
        response, attachments = render_display_math("$$x$$ $$y$$", max_images=1)

        self.assertEqual(len(attachments), 1)
        self.assertEqual(response.count("<generated_image>"), 1)
        self.assertIn("$$y$$", response)

    def test_renderer_returns_png_bytes(self):
        image = render_math_png(r"\mathrm{DR} \approx 6.02 \times \mathrm{bit}")

        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
