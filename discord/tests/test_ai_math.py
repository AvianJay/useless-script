import sys
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai_math import (
    MATH_BACKGROUND_COLOR,
    MATH_FOREGROUND_COLOR,
    normalize_math_lines,
    render_display_math,
    render_math_png,
)


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

    def test_renderer_uses_dark_background_with_visible_light_glyphs(self):
        image = render_math_png(r"f_{\text{max}} = \frac{f_s}{2}")

        with Image.open(BytesIO(image)) as rendered:
            pixels = list(rendered.convert("RGBA").getdata())

        self.assertEqual(pixels[0], MATH_BACKGROUND_COLOR)
        self.assertLess(sum(pixel[:3] == (255, 255, 255) for pixel in pixels), len(pixels) * 0.001)
        self.assertGreater(
            sum(
                pixel[0] >= MATH_FOREGROUND_COLOR[0] - 5
                and pixel[1] >= MATH_FOREGROUND_COLOR[1] - 5
                and pixel[2] >= MATH_FOREGROUND_COLOR[2] - 5
                for pixel in pixels
            ),
            100,
        )

    def test_normalizes_common_latex_shorthand(self):
        self.assertEqual(
            normalize_math_lines(r"\frac12\log\frac{16}{125}+\sqrt[3]8+3\sqrt3"),
            [r"\frac{1}{2}\log\frac{16}{125}+\sqrt[3]{8}+3\sqrt{3}"],
        )

    def test_renders_photo_transcription_shorthand(self):
        response, attachments = render_display_math(
            r"$$\frac12\log\frac{16}{125}+\log\frac{125}{\sqrt[3]8}-\log\frac52$$"
        )

        self.assertEqual(len(attachments), 1)
        self.assertNotIn("$$", response)

    def test_renders_aligned_equations_as_one_multiline_image(self):
        expression = (
            "$$\\begin{aligned}\n"
            "6\\log_2\\sqrt2-\\frac32\\log_2 3+\\log_2(3\\sqrt3)\\\\\n"
            "&=6\\cdot\\frac12-\\frac32\\log_2 3+\\frac32\\log_2 3\\\\\n"
            "&=\\boxed3\n"
            "\\end{aligned}$$"
        )
        response, attachments = render_display_math(expression)

        self.assertEqual(len(attachments), 1)
        self.assertNotIn(r"\begin{aligned}", response)
        with Image.open(BytesIO(attachments[0]["content"])) as rendered:
            self.assertGreater(rendered.height, 250)


if __name__ == "__main__":
    unittest.main()
