"""糟糕的 Make it a Quote：Discord markdown 解析、字型 fallback、特殊字元與名字欄 emoji。"""

import io
import sys
import unittest
from pathlib import Path

from PIL import Image

DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import ai_table
import MessageImage


def _resolve(kind, match):
    return f"[{kind}:{match.group(1)}]"


def _parse(text):
    return MessageImage._parse_quote_markdown(text, _resolve)


def _runs(block):
    return [(run.text, set(run.style)) for run in block.runs]


def _pieces(text, family="bold", size=40):
    return MessageImage._quote_pieces([MessageImage._QuoteRun(text)], family, size, MessageImage.QUOTE_TEXT_COLOR, 58)


def _avatar():
    return Image.new("RGB", (64, 64), (200, 120, 60))


class QuoteInlineMarkdownTests(unittest.TestCase):
    def test_inline_styles(self):
        [block] = _parse("**粗** *斜* __底__ ~~刪~~ ||雷|| `碼` [連結](https://example.com)")
        styled = {text: style for text, style in _runs(block) if text.strip()}
        self.assertEqual(styled["粗"], {"bold"})
        self.assertEqual(styled["斜"], {"italic"})
        self.assertEqual(styled["底"], {"underline"})
        self.assertEqual(styled["刪"], {"strike"})
        self.assertEqual(styled["雷"], {"spoiler"})
        self.assertEqual(styled["碼"], {"code"})
        self.assertEqual(styled["連結"], {"link"})

    def test_nested_and_bold_italic(self):
        [block] = _parse("***兩個*** __*底斜*__")
        styled = {text: style for text, style in _runs(block) if text.strip()}
        self.assertEqual(styled["兩個"], {"bold", "italic"})
        self.assertEqual(styled["底斜"], {"underline", "italic"})

    def test_literal_markers_stay_text(self):
        [block] = _parse(r"\*不是斜體\* snake_case_name 2 * 3 * 4 `**不解析**`")
        self.assertEqual(
            _runs(block),
            [("*不是斜體* snake_case_name 2 * 3 * 4 ", set()), ("**不解析**", {"code"})],
        )

    def test_urls_are_links_and_not_parsed(self):
        [block] = _parse("看 https://example.com/a_b_c 跟 <https://example.com/x>")
        self.assertEqual(
            _runs(block),
            [
                ("看 ", set()),
                ("https://example.com/a_b_c", {"link"}),
                (" 跟 ", set()),
                ("https://example.com/x", {"link"}),
            ],
        )

    def test_mentions_timestamps_and_custom_emoji(self):
        [block] = _parse("<@123456789012345678> <@&123456789012345679> <#123456789012345680> </ping:123456789012345681> <:blob:123456789012345682>")
        runs = [(run.text, set(run.style), run.emoji_id) for run in block.runs if run.text.strip()]
        self.assertEqual(runs[0], ("[user:123456789012345678]", {"mention"}, None))
        self.assertEqual(runs[1], ("[role:123456789012345679]", {"mention"}, None))
        self.assertEqual(runs[2], ("[channel:123456789012345680]", {"mention"}, None))
        self.assertEqual(runs[3], ("[command:ping]", {"mention"}, None))
        self.assertEqual(runs[4], (":blob:", set(), "123456789012345682"))

        [block] = MessageImage._parse_quote_markdown(
            "<t:0:d> <t:0>",
            lambda kind, match: MessageImage._quote_timestamp_label(match),
        )
        self.assertEqual(_runs(block), [("1970/01/01", {"timestamp"}), (" ", set()), ("1970/01/01 08:00", {"timestamp"})])

    def test_styles_can_span_lines(self):
        first, second = _parse("||第一行\n第二行||")
        self.assertEqual(_runs(first), [("第一行", {"spoiler"})])
        self.assertEqual(_runs(second), [("第二行", {"spoiler"})])

    def test_deep_nesting_does_not_recurse_forever(self):
        blocks = _parse("*" * 4000 + "\n" + "**__" * 500 + "x" + "__**" * 500)
        self.assertTrue(blocks)


class QuoteBlockMarkdownTests(unittest.TestCase):
    def test_headings_subtext_quotes_and_lists(self):
        blocks = _parse("# 大\n## 中\n### 小\n-# 註解\n> 引用\n- 一\n  - 二\n1. 三\n#沒有空白")
        self.assertEqual(
            [(block.kind, block.group, block.bullet, block.level) for block in blocks],
            [
                ("h1", None, None, 0),
                ("h2", None, None, 0),
                ("h3", None, None, 0),
                ("subtext", None, None, 0),
                ("text", "quote", None, 0),
                ("text", "list", "• ", 0),
                ("text", "list", "◦ ", 1),
                ("text", "list", "1. ", 0),
                ("text", None, None, 0),
            ],
        )
        self.assertEqual(_runs(blocks[0]), [("大", set())])
        self.assertEqual(_runs(blocks[-1]), [("#沒有空白", set())])

    def test_block_quote_covers_the_rest(self):
        blocks = _parse("前面\n>>> 一\n二\n\n三")
        self.assertEqual([block.group for block in blocks], [None, "quote", "quote", "quote", "quote"])
        self.assertEqual(blocks[3].kind, "blank")

    def test_code_block_keeps_indentation_and_markers(self):
        blocks = _parse("看：\n```py\ndef f():\n    return **x**\n```\n結束")
        self.assertEqual([block.kind for block in blocks], ["text", "code", "code", "text"])
        self.assertEqual(_runs(blocks[2]), [("    return **x**", set())])
        self.assertEqual(blocks[1].group, blocks[2].group)

    def test_blank_lines_are_collapsed_and_trimmed(self):
        blocks = _parse("\n\n一\n\n\n\n二\n\n")
        self.assertEqual([block.kind for block in blocks], ["text", "blank", "text"])


class QuoteFontTests(unittest.TestCase):
    def assert_drawable(self, pieces):
        for piece in pieces:
            if piece.font_path and not piece.space:
                codepoints = ai_table._font_codepoints(piece.font_path)
                for char in piece.text:
                    self.assertIn(ord(char), codepoints, f"{char!r} ({ord(char):X}) 不在 {piece.font_path}")

    def test_missing_glyphs_fall_back_to_other_fonts(self):
        # Noto Sans TC 沒有這些字：拉丁擴充、數學花體、箭頭
        pieces = _pieces("Łódź ő ẞ Ⅻ ➜ 𝔉𝔯𝔞𝔨𝔱𝔲𝔯")
        self.assert_drawable(pieces)
        self.assertNotIn(MessageImage._quote_font_paths("bold")[0], {piece.font_path for piece in pieces if piece.text in "Łő"})

    def test_light_family_prefers_noto_sans_for_latin(self):
        [piece] = _pieces("ő", family="light")
        self.assertTrue(piece.font_path.endswith("notoregular.ttf"))

    def test_invisible_and_control_characters_are_dropped(self):
        text = "a" + "".join(map(chr, (0x200B, 0x202E, 0xFE0F, 0x2060, 0x0007, 0xE0041))) + "b"
        pieces = _pieces(text)
        self.assertEqual([piece.text for piece in pieces], ["a", "b"])
        self.assertTrue(pieces[1].breakable, "零寬空白是換行點")

    def test_blank_filler_characters_become_spaces(self):
        pieces = _pieces(chr(0x3164) + "x" + chr(0x2800))
        self.assertEqual([piece.space for piece in pieces], [True, False, True])

    def test_combining_marks_stay_with_their_base(self):
        zalgo = "Z" + chr(0x0337) + chr(0x0322) + chr(0x031B)
        pieces = _pieces("e" + chr(0x0301) + zalgo)
        self.assertEqual(pieces[0].text, "e" + chr(0x0301))
        self.assertEqual("".join(piece.text for piece in pieces[1:]), zalgo)
        # 主字型沒有的附加符號會換字型另外畫，但不能跟前一個字斷開
        self.assertFalse(any(piece.breakable for piece in pieces[1:]))
        self.assert_drawable(pieces)

    def test_emoji_and_text_style_symbols_become_twemoji(self):
        pieces = _pieces("🏳️‍🌈👨‍👩‍👧1️⃣🇹🇼👍🏽©™♠❤‼")
        self.assertEqual(
            [piece.emoji for piece in pieces],
            ["🏳️‍🌈", "👨‍👩‍👧", "1️⃣", "🇹🇼", "👍🏽", "©", "™", "♠", "❤", "‼"],
        )

    def test_text_presentation_selector_keeps_symbol_as_text(self):
        pieces = _pieces("❤" + chr(0xFE0E) + "©")
        self.assertEqual([(piece.text, piece.emoji) for piece in pieces], [("❤", None), ("", "©")])


class QuoteWrapTests(unittest.TestCase):
    def test_cjk_breaks_anywhere_but_not_before_closing_punctuation(self):
        pieces = _pieces("一二三四五六七八，九十")
        width = sum(piece.width for piece in pieces[:8]) + 1
        rows = MessageImage._wrap_quote_pieces(pieces, width)
        self.assertEqual("".join(piece.text for piece in rows[0]), "一二三四五六七")
        self.assertEqual("".join(piece.text for piece in rows[1]), "八，九十")

    def test_latin_breaks_at_spaces_and_long_words_hard_break(self):
        pieces = _pieces("hello world " + "x" * 40)
        rows = MessageImage._wrap_quote_pieces(pieces, sum(piece.width for piece in pieces[:11]) + 1)
        texts = ["".join(piece.text for piece in row) for row in rows]
        self.assertEqual(texts[0], "hello world")
        self.assertEqual("".join(texts[1:]), "x" * 40)
        self.assertTrue(all(len(text) < 40 for text in texts[1:]))


class QuoteRenderTests(unittest.TestCase):
    def test_name_renders_emoji_and_fallback_glyphs(self):
        name = "𝓐𝓿𝓲𝓪𝓷 한글 🐦✨"
        line = MessageImage._layout_quote_block(
            MessageImage._QuoteBlock([MessageImage._QuoteRun(f"- {name}", frozenset({"italic"}))], "name"),
            25,
            float("inf"),
            {},
        )[0]
        self.assertEqual([piece.emoji for piece in line.pieces if piece.emoji], ["🐦", "✨"])
        image = MessageImage._render_quote_name(name, 590)
        self.assertIsNotNone(image)
        colors = {pixel[:3] for pixel in image.getdata() if pixel[3] == 255}
        self.assertTrue(any(red != green or green != blue for red, green, blue in colors), "emoji 應該是彩色的")

    def test_long_name_fits_width(self):
        image = MessageImage._render_quote_name("非常長的名字" * 10 + " 🎉" * 10, 590)
        self.assertLessEqual(image.width, 590)

    def test_render_static_quote(self):
        blocks = _parse("# 標題\n**粗體** ||暴雷|| `code` 😀\n> 引用\n```\ncode\n```")
        buffer, ext = MessageImage._render_quote_image(_avatar(), "小明 🐦", blocks, {})
        self.assertEqual(ext, "png")
        self.assertEqual(Image.open(buffer).size, MessageImage.QUOTE_SIZE)

    def test_overlong_message_is_truncated_instead_of_crashing(self):
        blocks = _parse("很長的訊息，" * 1000)
        size, lines = MessageImage._fit_quote_layout(blocks, 590, 480, {})
        self.assertEqual(size, MessageImage.QUOTE_FONT_SIZES[-1])
        self.assertLessEqual(MessageImage._quote_layout_height(lines, size), 480)
        self.assertEqual(lines[-1].pieces[-1].text, "…")
        MessageImage._render_quote_image(_avatar(), "name", blocks, {})

    def test_custom_emoji_falls_back_to_name_when_missing(self):
        blocks = _parse("<:here:123456789012345678><:gone:123456789012345679>")
        emoji_image = Image.new("RGBA", (64, 32), (255, 0, 255, 255))
        lines = MessageImage._layout_quote(blocks, 40, 590, {"123456789012345678": emoji_image})
        pieces = lines[0].pieces
        self.assertEqual(pieces[0].emoji_id, "123456789012345678")
        self.assertEqual("".join(piece.text for piece in pieces[1:]), ":gone:")
        MessageImage._render_quote_image(_avatar(), "n", blocks, {"123456789012345678": emoji_image})

    def test_animated_avatar_keeps_frames(self):
        frames = [Image.new("RGB", (32, 32), color) for color in ((255, 0, 0), (0, 255, 0))]
        source = io.BytesIO()
        frames[0].save(source, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
        source.seek(0)
        buffer, ext = MessageImage._render_quote_image(Image.open(source), "n", _parse("動圖"), {}, animate_gif=True)
        self.assertEqual(ext, "gif")
        self.assertEqual(Image.open(buffer).n_frames, 2)


if __name__ == "__main__":
    unittest.main()
