import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai_table import (
    TABLE_BACKGROUND_COLOR,
    TABLE_MUTED_TEXT_COLOR,
    TABLE_TEXT_COLOR,
    parse_markdown_tables,
    render_markdown_tables,
    render_table_png,
)


class AITableRenderingTests(unittest.TestCase):
    def test_parses_gfm_table_with_alignment_and_inline_markdown(self):
        tables = parse_markdown_tables(
            "| Name | Value | Notes |\n"
            "|:--|--:|:--:|\n"
            "| **Alpha** | 10 | `ready` |\n"
            "| Beta | 20 | [docs](https://example.com) |"
        )

        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].headers, ["Name", "Value", "Notes"])
        self.assertEqual(tables[0].alignments, ["left", "right", "center"])
        self.assertEqual(tables[0].rows[0], ["Alpha", "10", "ready"])
        self.assertEqual(tables[0].rows[1][2], "docs")

    def test_parses_table_without_outer_pipes_and_escaped_pipe(self):
        tables = parse_markdown_tables(
            "Key | Meaning\n"
            "--- | ---\n"
            "A | left \\| right"
        )

        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].rows[0], ["A", "left | right"])

    def test_parses_single_column_table(self):
        tables = parse_markdown_tables(
            "| Status |\n"
            "|---|\n"
            "| Ready |"
        )

        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].headers, ["Status"])
        self.assertEqual(tables[0].rows, [["Ready"]])

    def test_renders_table_as_inline_attachment_in_source_order(self):
        original = (
            "Before\n\n"
            "| Route | ETA |\n"
            "|---|---:|\n"
            "| 307 | 5 min |\n\n"
            "After"
        )
        response, attachments = render_markdown_tables(original)

        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["kind"], "table")
        self.assertTrue(response.startswith("Before"))
        self.assertTrue(response.endswith("After"))
        self.assertIn(
            f"<generated_image>attachment://{attachments[0]['filename']}</generated_image>",
            response,
        )

    def test_does_not_render_table_inside_code_fence(self):
        original = "```markdown\n| A | B |\n|---|---|\n| 1 | 2 |\n```"
        response, attachments = render_markdown_tables(original)

        self.assertEqual(response, original)
        self.assertEqual(attachments, [])

    def test_rendered_image_has_dark_background_and_table_grid(self):
        _, attachments = render_markdown_tables(
            "| 路線 | 預計到站 |\n|---|---:|\n| 307 | 5 分鐘 |\n| 668 | 12 分鐘 |"
        )

        with Image.open(BytesIO(attachments[0]["content"])) as rendered:
            image = rendered.convert("RGBA")
            self.assertLessEqual(image.width, 2000)
            self.assertLessEqual(image.height, 2000)
            self.assertEqual(image.getpixel((0, 0)), TABLE_BACKGROUND_COLOR)
            self.assertGreater(len(image.getcolors(maxcolors=image.width * image.height)), 5)

    def test_render_failure_keeps_original_markdown(self):
        original = "| A | B |\n|---|---|\n| 1 | 2 |"

        with patch("ai_table.render_table_png", side_effect=ValueError("render failed")):
            response, attachments = render_markdown_tables(original)

        self.assertEqual(response, original)
        self.assertEqual(attachments, [])

    def test_last_real_row_is_not_muted_when_omission_note_does_not_fit(self):
        original = "| Status |\n|---|\n" + "\n".join(
            f"| row {index} |" for index in range(50)
        )
        table = parse_markdown_tables(original)[0]

        with patch("ai_table.MAX_TABLE_IMAGE_HEIGHT", 220):
            image_bytes = render_table_png(table)

        with Image.open(BytesIO(image_bytes)) as rendered:
            colors = rendered.convert("RGBA").getcolors(
                maxcolors=rendered.width * rendered.height
            )

        color_counts = {color: count for count, color in colors}
        self.assertGreater(color_counts.get(TABLE_TEXT_COLOR, 0), 0)
        self.assertEqual(color_counts.get(TABLE_MUTED_TEXT_COLOR, 0), 0)

    def test_invalid_table_remains_markdown(self):
        original = "| A | B |\n| no separator | here |\n| 1 | 2 |"
        response, attachments = render_markdown_tables(original)

        self.assertEqual(response, original)
        self.assertEqual(attachments, [])


if __name__ == "__main__":
    unittest.main()
