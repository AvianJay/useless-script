import sys
import unittest
from pathlib import Path


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import ai_layout
from ai_emoji import render_twemoji, resolve_shortcodes, shortcode_to_emoji, twemoji_glyph


VS16 = chr(0xFE0F)
ZWJ = chr(0x200D)
PEPE = "<:pepe:123456789012345678>"
CUSTOM = {"pepe": PEPE}


class ShortcodeTests(unittest.TestCase):
    def test_discord_shortcodes_become_unicode_emoji(self):
        self.assertEqual(
            resolve_shortcodes(":white_check_mark: 完成 :x: 失敗 :flag_tw: :slight_smile: :one:"),
            f"✅ 完成 ❌ 失敗 🇹🇼 🙂 1{VS16}⃣",
        )

    def test_discord_names_win_over_emoji_package_names(self):
        # emoji 套件把 :pouting_face: 對到 🙎，Discord 用戶端是 😡
        self.assertEqual(shortcode_to_emoji("pouting_face"), "😡")

    def test_skin_tone_forms(self):
        self.assertEqual(resolve_shortcodes(":thumbsup::skin-tone-2: :+1_tone3:"), "👍🏼 👍🏽")
        self.assertEqual(
            resolve_shortcodes(":man_technologist_tone1:"),
            f"👨🏻{ZWJ}💻",
        )

    def test_falls_back_to_emoji_package_aliases(self):
        self.assertEqual(resolve_shortcodes(":e-mail: :grinning_face_with_big_eyes:"), "📧 😃")

    def test_custom_emoji_take_priority_and_ignore_case(self):
        self.assertEqual(resolve_shortcodes(":pepe: :PEPE:", CUSTOM), f"{PEPE} {PEPE}")
        self.assertEqual(resolve_shortcodes(":fire:", {"fire": "<:fire:223456789012345678>"}), "<:fire:223456789012345678>")

    def test_unknown_names_stay_as_text(self):
        self.assertEqual(resolve_shortcodes("看 :catjam_dance: 跳舞"), "看 :catjam_dance: 跳舞")

    def test_times_and_ratios_are_untouched(self):
        samples = [
            "影片 00:54:23 開始",
            "| 章節 | 時間 |\n|---|---|\n| 開場 | 00:54:23 |",
            "比例 16:9:4，位址 fe80::1:a:b，分數 1:100:2",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(resolve_shortcodes(sample, {"54": "<:54:123456789012345678>"}), sample)

    def test_short_names_still_convert_between_spaces(self):
        self.assertEqual(resolve_shortcodes("得分 :100: 分 :x:"), "得分 💯 分 ❌")

    def test_closing_colon_can_start_the_next_shortcode(self):
        self.assertEqual(resolve_shortcodes("10:30:fire: 出發"), "10:30🔥 出發")
        self.assertEqual(resolve_shortcodes(":smile::heart:"), f"😄❤{VS16}")

    def test_code_urls_and_discord_markup_are_untouched(self):
        original = (
            "`:fire:`\n```py\nx = ':fire:'\n```\n"
            "https://example.com/a:fire:b <t:1792259663:T> </ai chat:123456789012345678> " + PEPE
        )
        self.assertEqual(resolve_shortcodes(original, CUSTOM), original)

    def test_repairs_broken_custom_emoji_markup(self):
        broken = "<:<:pepe:123456789012345678>:123456789012345678> <:pepe:>"
        self.assertEqual(resolve_shortcodes(broken, CUSTOM), f"{PEPE} {PEPE}")

    def test_custom_emoji_in_layout_attributes_keep_the_tag_parseable(self):
        text = resolve_shortcodes(
            '<buttons><button emoji=":pepe:">一</button>'
            f'<button emoji="{PEPE}">二</button>'
            '<button emoji=":rocket:">:sparkles: 三</button></buttons>',
            CUSTOM,
        )
        layout = ai_layout.parse_layout(text, normalize_image_url=lambda url: url)
        buttons = layout.nodes[0].buttons
        self.assertEqual([button.emoji for button in buttons], [PEPE, PEPE, "🚀"])
        self.assertEqual([button.label for button in buttons], ["一", "二", "✨ 三"])


class TwemojiRenderTests(unittest.TestCase):
    def test_renders_color_tile(self):
        tile = render_twemoji("✅", 28)

        self.assertEqual(tile.size, (28, 28))
        colors = {color for _count, color in tile.getcolors(maxcolors=28 * 28)}
        self.assertIn((119, 178, 85, 255), colors)

    def test_resolves_sequences_and_missing_variation_selectors(self):
        for sequence in ("🇹🇼", "👍🏽", f"👨{ZWJ}💻", f"1{VS16}⃣", "1⃣", "✔", f"⚠{VS16}"):
            with self.subTest(sequence=sequence):
                self.assertIsNotNone(twemoji_glyph(sequence))
                self.assertIsNotNone(render_twemoji(sequence, 24))

    def test_non_emoji_has_no_tile(self):
        self.assertIsNone(render_twemoji("A", 24))


if __name__ == "__main__":
    unittest.main()
