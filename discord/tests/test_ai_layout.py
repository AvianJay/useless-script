import sys
import unittest
from pathlib import Path


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import ai_layout as L


CDN = "https://cdn.discordapp.com/attachments/1/2/"


def normalize(url):
    url = str(url or "").strip()
    return url if url.startswith(CDN) else None


def parse(text, **kwargs):
    return L.parse_layout(text, normalize_image_url=normalize, invalid_image_text="[invalid]", **kwargs)


class AILayoutParserTests(unittest.TestCase):
    def test_container_reads_colour_and_spoiler(self):
        layout = parse('<container color="#ff0000" spoiler>Hi</container><container color="green">Yo</container>')

        self.assertTrue(layout.has_containers)
        self.assertEqual(
            [(node.color, node.spoiler) for node in layout.nodes],
            [(0xFF0000, True), (L.NAMED_COLORS["green"], False)],
        )
        self.assertEqual(layout.nodes[0].children, [L.Text("Hi")])

    def test_nested_and_unclosed_containers_are_flattened(self):
        layout = parse("<container>A<container>B</container>C<container>D")

        self.assertEqual(
            layout.nodes,
            [L.Container([L.Text("A"), L.Text("B")]), L.Text("C"), L.Container([L.Text("D")])],
        )

    def test_unknown_tags_stay_as_text(self):
        layout = parse("<details>raw</details>")

        self.assertEqual(layout.nodes, [L.Text("<details>raw</details>")])

    def test_code_is_never_parsed(self):
        text = f"```\n<image>{CDN}a.png</image>\n---\n```\n`<button>x</button>`"
        layout = parse(text)

        self.assertEqual(layout.nodes, [L.Text(text)])

    def test_separator_variants(self):
        layout = parse('A\n---\nB\n<separator spacing="large" divider="false"/>\nC\n<separator>\nD')

        separators = [node for node in layout.nodes if isinstance(node, L.Separator)]
        self.assertEqual(
            separators,
            [L.Separator(True, False), L.Separator(False, True), L.Separator(True, False)],
        )

    def test_section_with_thumbnail_and_button_accessories(self):
        layout = parse(
            f'<section>Text<thumbnail alt="pic">{CDN}a.png</thumbnail></section>'
            '<section>More<button url="https://example.com">Open</button></section>'
            "<section>Plain only</section>"
        )

        first, second, third = layout.nodes
        self.assertEqual(first, L.Section("Text", L.Media(f"{CDN}a.png", description="pic")))
        self.assertEqual(second.accessory.url, "https://example.com")
        self.assertEqual(third, L.Text("Plain only"))

    def test_gallery_collects_items_with_alt_and_spoiler(self):
        layout = parse(
            f'<gallery><image alt="one">{CDN}a.png</image><image spoiler>attachment://gen.png</image></gallery>',
            attachments=[{"filename": "gen.png", "content": b"x"}],
        )

        self.assertEqual(
            layout.nodes,
            [L.Gallery([L.Media(f"{CDN}a.png", description="one"), L.Media("attachment://gen.png", spoiler=True)])],
        )
        self.assertEqual(layout.placed_attachment_refs, ["attachment://gen.png"])

    def test_invalid_image_url_inserts_notice(self):
        layout = parse("<image>https://example.com/a.png</image>")

        self.assertEqual(layout.nodes, [L.Text("[invalid]")])

    def test_unplaced_attachments_share_one_gallery(self):
        layout = parse("Done", attachments=[{"filename": f"{index}.png", "content": b"x"} for index in range(3)])

        self.assertEqual(layout.nodes[0], L.Text("Done"))
        self.assertEqual(
            [media.ref for media in layout.nodes[1].items],
            ["attachment://0.png", "attachment://1.png", "attachment://2.png"],
        )

    def test_media_budget_counts_thumbnails_as_three(self):
        layout = parse(
            "".join(f"<thumbnail>{CDN}{index}.png</thumbnail>" for index in range(4)),
            max_media_cost=9,
        )

        self.assertEqual(len(layout.nodes), 3)
        self.assertEqual(layout.omitted_media, 1)

    def test_file_tag_places_the_pending_file_once(self):
        layout = parse("Intro<file spoiler></file>Outro<file/>", file_name="answer.txt")

        self.assertEqual(
            layout.nodes,
            [L.Text("Intro"), L.FileBlock("attachment://answer.txt", spoiler=True), L.Text("Outro")],
        )

    def test_pending_file_is_appended_when_not_placed(self):
        layout = parse("Summary", file_name="answer.txt")

        self.assertEqual(layout.nodes[-1], L.FileBlock("attachment://answer.txt"))

    def test_link_buttons_require_http_urls_and_valid_emoji(self):
        layout = parse(
            '<buttons><button url="javascript:alert(1)">Bad</button>'
            '<button url="https://example.com" emoji="not-emoji">Good</button>'
            '<button url="https://example.com/b" emoji="🔗">Icon</button></buttons>'
        )

        (row,) = layout.nodes
        self.assertEqual([button.label for button in row.buttons], ["Good", "Icon"])
        self.assertEqual([button.emoji for button in row.buttons], [None, "🔗"])

    def test_adjacent_standalone_buttons_share_a_row(self):
        layout = parse("<button>A</button>\n<button>B</button>")

        self.assertEqual([[button.label for button in node.buttons] for node in layout.nodes], [["A", "B"]])

    def test_requester_only_is_inherited_and_can_be_overridden(self):
        layout = parse(
            '<buttons requester_only><button>Mine</button><button requester_only="false">Anyone</button></buttons>'
        )

        self.assertEqual([button.requester_only for button in layout.nodes[0].buttons], [True, False])

    def test_select_keeps_options_and_placeholder(self):
        layout = parse(
            '<select placeholder="Pick" requester_only><option description="d" emoji="🍎">Apple</option>'
            "<option>Banana</option></select>"
        )

        (menu,) = layout.nodes
        self.assertEqual(menu.placeholder, "Pick")
        self.assertTrue(menu.requester_only)
        self.assertEqual(menu.options, [L.Option("Apple", "d", "🍎"), L.Option("Banana")])


class AILayoutFollowupLimitTests(unittest.TestCase):
    def test_first_select_wins_over_later_buttons(self):
        layout = parse(
            '<select><option>X</option></select><buttons><button url="https://example.com">Link</button>'
            "<button>Ask</button></buttons>"
        )

        self.assertIsInstance(layout.nodes[0], L.SelectMenu)
        self.assertEqual([button.label for button in layout.nodes[1].buttons], ["Link"])

    def test_first_buttons_win_over_later_select(self):
        layout = parse("<button>Ask</button><select><option>X</option></select>")

        self.assertEqual(len(layout.nodes), 1)
        self.assertIsInstance(layout.nodes[0], L.ButtonRow)

    def test_only_five_followup_buttons_are_kept_and_indexed(self):
        layout = parse(
            "<buttons>" + "".join(f"<button>Q{index}</button>" for index in range(5)) + "</buttons>"
            "<section>Extra<button>Q5</button></section>"
        )

        self.assertEqual([button.index for button in layout.nodes[0].buttons], [0, 1, 2, 3, 4])
        self.assertEqual(layout.nodes[1], L.Text("Extra"))


class AILayoutFitTests(unittest.TestCase):
    def test_fit_degrades_until_components_fit(self):
        text = "\n".join(f"Line {index}\n---\n<thumbnail>{CDN}{index}.png</thumbnail>" for index in range(30))
        layout = parse(text, max_media_cost=float("inf"))

        L.fit_layout(layout, reserved_components=5)

        self.assertLessEqual(L.count_components(layout.nodes), 35)
        self.assertFalse(any(isinstance(node, (L.Separator, L.Thumbnail)) for node in layout.nodes))

    def test_fit_trims_from_the_end_when_degrading_is_not_enough(self):
        layout = parse("".join(f"<buttons><button url='https://e.com/{index}'>B</button></buttons>Text {index}" for index in range(30)))

        L.fit_layout(layout)

        self.assertTrue(layout.trimmed)
        self.assertLessEqual(L.count_components(layout.nodes), L.MAX_COMPONENTS)

    def test_fit_truncates_text_to_the_budget(self):
        layout = parse("<container>" + "a" * 5000 + "</container>")

        L.fit_layout(layout, reserved_text=100)

        self.assertTrue(layout.trimmed)
        self.assertEqual(L.text_length(layout.nodes), L.TEXT_BUDGET - 100)
        self.assertTrue(layout.nodes[0].children[0].content.endswith("…"))


class AILayoutTextTests(unittest.TestCase):
    def test_history_text_keeps_content_and_marks_followups(self):
        text = (
            f'<container color="red"><section>Title<thumbnail>{CDN}a.png</thumbnail></section>'
            '<separator/><buttons><button url="https://example.com">Docs</button><button>Ask more</button></buttons>'
            "</container>```\n<button>code</button>\n```"
        )

        history = L.strip_layout_tags(text, keep_media=False, normalize_image_url=normalize)

        self.assertNotIn("<container", history)
        self.assertIn("Title", history)
        self.assertIn(f"{CDN}a.png", history)
        self.assertIn("---", history)
        self.assertIn("[Docs](https://example.com)", history)
        self.assertIn("[follow-up: Ask more]", history)
        self.assertIn("<button>code</button>", history)

    def test_fallback_text_keeps_media_tags_without_attributes(self):
        text = f'<gallery><image alt="x" spoiler>{CDN}a.png</image></gallery><button>Ask</button>'

        fallback = L.strip_layout_tags(text, keep_media=True)

        self.assertEqual(fallback, f"<image>{CDN}a.png</image>\n• Ask")

    def test_visible_length_ignores_tags_but_counts_labels(self):
        text = '<container color="green"><button url="https://example.com">Docs</button>Hello</container>'

        self.assertEqual(L.visible_text_length(text), len("DocsHello"))


if __name__ == "__main__":
    unittest.main()
