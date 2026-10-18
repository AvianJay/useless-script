import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import ai_table
from ai_table import (
    TABLE_BACKGROUND_COLOR,
    TABLE_MENTION_FILL,
    TABLE_MUTED_TEXT_COLOR,
    TABLE_ROW_COLOR,
    TABLE_TEXT_COLOR,
    TABLE_TIMESTAMP_FILL,
    collect_table_markup,
    parse_markdown_tables,
    render_markdown_tables,
    render_table_png,
)


def _colors(image_bytes: bytes) -> set:
    with Image.open(BytesIO(image_bytes)) as rendered:
        image = rendered.convert("RGBA")
        return {color for _count, color in image.getcolors(maxcolors=image.width * image.height)}


def _solid_png(color) -> bytes:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((8, 8, 56, 56), fill=color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


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

    def test_unicode_emoji_are_drawn_in_color(self):
        table = parse_markdown_tables("| 功能 | 支援 |\n|---|:---:|\n| 表格 | ✅ |")[0]

        # Twemoji 的 ✅ 是綠底，以前會畫成 Noto 缺字的方框
        self.assertIn((119, 178, 85, 255), _colors(render_table_png(table)))

    def test_cells_with_line_breaks_render(self):
        _, attachments = render_markdown_tables("| 項目 |\n|---|\n| 第一行<br>第二行 |")

        self.assertEqual(len(attachments), 1)

    def test_custom_emoji_use_fetched_images(self):
        table = parse_markdown_tables("| 表情 |\n|---|\n| <:pepe:123456789012345678> |")[0]

        with_image = render_table_png(table, emoji_images={"123456789012345678": _solid_png((255, 0, 255, 255))})
        without_image = render_table_png(table)

        self.assertIn((255, 0, 255, 255), _colors(with_image))
        self.assertNotIn((255, 0, 255, 255), _colors(without_image))

    def test_timestamps_and_mentions_get_discord_style_backgrounds(self):
        table = parse_markdown_tables(
            "| 時間 | 誰 |\n|---|---|\n| <t:1792259663:T> | <@123456789012345678> |"
        )[0]

        colors = _colors(render_table_png(table, markup_labels={"<t:1792259663:T>": "00:54:23"}))

        self.assertIn(ai_table._blend(TABLE_ROW_COLOR, TABLE_TIMESTAMP_FILL), colors)
        self.assertIn(ai_table._blend(TABLE_ROW_COLOR, TABLE_MENTION_FILL), colors)

    def test_markup_labels_replace_raw_discord_markup(self):
        chain = ai_table._font_chain(22, False)
        assets = ai_table._InlineAssets({}, {"<t:1792259663:T>": "00:54:23"})
        cases = {
            "<t:1792259663:T>": "00:54:23",
            "<t:1792259663:d>": "2026/10/18",
            "<@123456789012345678>": "@unknown-user",
            "</ai chat:123456789012345678>": "/ai chat",
            "<:ghost:123456789012345678>": ":ghost:",
            "影片 00:54:23": "影片 00:54:23",
        }
        for markup, expected in cases.items():
            with self.subTest(markup=markup):
                paragraphs = ai_table._cell_paragraphs(markup, chain, assets, TABLE_TEXT_COLOR)
                self.assertEqual("".join(piece.text for piece in paragraphs[0]), expected)

    def test_missing_glyphs_fall_back_and_invisible_characters_are_dropped(self):
        chain = ai_table._font_chain(22, False)

        # ☐ 不在 Noto Sans TC 裡，改用 DejaVu
        self.assertIsNotNone(chain.font_for("☐"))
        self.assertIsNot(chain.font_for("☐"), chain.primary)
        pieces = ai_table._char_pieces(f"A{chr(0x200B)}{chr(0xFE0F)}B", chain, TABLE_TEXT_COLOR)
        self.assertEqual("".join(piece.text for piece in pieces), "AB")

    def test_collect_table_markup_only_reads_table_cells(self):
        text = (
            "<t:1792259663:R> 在表格外\n\n"
            "| 時間 | 表情 |\n|---|---|\n"
            "| <t:1792259663:T> | <:pepe:123456789012345678> <:pepe:123456789012345678> |\n\n"
            "```\n| <@123456789012345678> |\n|---|\n```"
        )

        self.assertEqual(
            [match.group(0) for match in collect_table_markup(text)],
            ["<t:1792259663:T>", "<:pepe:123456789012345678>"],
        )


if __name__ == "__main__":
    unittest.main()
