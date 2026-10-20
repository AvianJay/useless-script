import sys
import unittest
from pathlib import Path

import discord


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AIFollowupButton, AIFollowupSelect, AIResponseBuilder


class AIResponseMediaTests(unittest.TestCase):
    def test_external_images_thumbnails_and_galleries_render_direct_urls(self):
        image_url = "https://images.example.com/photo.png?width=640&height=480"
        thumbnail_url = "http://static.example.net:8080/thumb.jpg"
        gallery_url = "https://another.example.org/gallery.webp"
        view = AIResponseBuilder.create_response_view(
            f"<image>{image_url.replace('&', '&amp;')}</image>"
            f"<section>Preview<thumbnail>{thumbnail_url}</thumbnail></section>"
            f"<gallery><image>{gallery_url}</image></gallery>",
            user=None,
            show_cost=False,
            show_model=False,
        )

        gallery_urls = [
            entry["media"]["url"]
            for item in view.walk_children()
            if isinstance(item, discord.ui.MediaGallery)
            for entry in item.to_component_dict()["items"]
        ]
        thumbnail_urls = [
            item.to_component_dict()["media"]["url"]
            for item in view.walk_children()
            if isinstance(item, discord.ui.Thumbnail)
        ]

        self.assertEqual(gallery_urls, [image_url, gallery_url])
        self.assertEqual(thumbnail_urls, [thumbnail_url])

    def test_external_image_urls_are_preserved_in_history(self):
        image_url = "https://images.example.com/photo.png"
        thumbnail_url = "http://static.example.net/thumb.jpg"

        history = AIResponseBuilder.strip_media_tags_for_history(
            f"<image>{image_url}</image><thumbnail>{thumbnail_url}</thumbnail>"
        )

        self.assertIn(image_url, history)
        self.assertIn(thumbnail_url, history)
        self.assertNotIn("<image>", history)
        self.assertNotIn("<thumbnail>", history)

    def test_invalid_display_urls_are_omitted(self):
        for url in (
            "javascript:alert(1)",
            "file:///C:/image.png",
            "https://",
            "https://[invalid/image.png",
            "https://example.com:invalid/image.png",
            "https://user:password@example.com/image.png",
        ):
            with self.subTest(url=url):
                items = AIResponseBuilder._iter_response_layout_items(f"<image>{url}</image>")
                self.assertFalse(any(item_type == "image" for item_type, _ in items))

    def test_empty_generated_image_tag_only_uses_native_images(self):
        payloads = [
            {"filename": "table.png", "content": b"table", "kind": "table"},
            {"filename": "native.png", "content": b"native"},
            {"filename": "math.png", "content": b"math", "kind": "math"},
        ]
        response = (
            "<generated_image></generated_image>\n"
            "<generated_image>attachment://table.png</generated_image>\n"
            "<generated_image>attachment://math.png</generated_image>"
        )

        items = AIResponseBuilder._iter_response_layout_items(response, payloads)
        image_refs = [value for item_type, value in items if item_type == "image"]

        self.assertEqual(
            image_refs,
            [
                "attachment://native.png",
                "attachment://table.png",
                "attachment://math.png",
            ],
        )

    def test_reserved_media_count_includes_remote_media_and_attachments(self):
        response = (
            "<image>https://cdn.discordapp.com/attachments/a/image.png</image>\n"
            "<thumbnail>https://media.discordapp.net/attachments/b/thumb.png</thumbnail>\n"
            "<generated_image>attachment://native.png</generated_image>\n"
            "```html\n"
            "<image>https://cdn.discordapp.com/attachments/ignored.png</image>\n"
            "```"
        )

        reserved = AIResponseBuilder.count_reserved_media_components(
            response,
            [{"filename": "native.png", "content": b"native"}],
        )

        self.assertEqual(reserved, 5)

    def test_only_attachments_that_fit_the_view_are_sent(self):
        payloads = [
            {"filename": f"native-{index}.png", "content": b"image"}
            for index in range(10)
        ]

        filtered = AIResponseBuilder.filter_renderable_image_attachments("Done", payloads)

        self.assertEqual(len(filtered), AIResponseBuilder.RESPONSE_MAX_MEDIA_COMPONENTS)
        self.assertEqual(filtered[-1]["filename"], "native-8.png")

    def test_layout_view_contains_each_attachment_once(self):
        payloads = [
            {"filename": "native.png", "content": b"native"},
            {"filename": "table.png", "content": b"table", "kind": "table"},
            {"filename": "math.png", "content": b"math", "kind": "math"},
        ]
        response = (
            "<generated_image></generated_image>\n"
            "<generated_image>attachment://table.png</generated_image>\n"
            "<generated_image>attachment://math.png</generated_image>"
        )

        view = AIResponseBuilder.create_response_view(
            response,
            user=None,
            show_cost=False,
            show_model=False,
            generated_image_attachments=payloads,
        )
        galleries = [
            child
            for container in view.children
            for child in getattr(container, "children", [])
            if isinstance(child, discord.ui.MediaGallery)
        ]
        urls = [gallery.to_component_dict()["items"][0]["media"]["url"] for gallery in galleries]

        self.assertEqual(
            urls,
            [
                "attachment://native.png",
                "attachment://table.png",
                "attachment://math.png",
            ],
        )
        self.assertEqual(len(urls), len(set(urls)))

    def test_ai_containers_replace_the_default_wrapper(self):
        view = AIResponseBuilder.create_response_view(
            '<container color="green" spoiler>Card A</container><container>Card B</container>',
            user=None,
            billing_info="cost",
            model_name="model",
            response_time="1s",
        )

        containers = [child for child in view.children if isinstance(child, discord.ui.Container)]
        payloads = [container.to_component_dict() for container in containers]
        self.assertEqual([payload["accent_color"] for payload in payloads], [0x57F287, 0x5865F2])
        self.assertEqual([payload["spoiler"] for payload in payloads], [True, False])
        self.assertIsInstance(view.children[-1], discord.ui.TextDisplay)
        self.assertTrue(view.children[-1].content.startswith("-# cost"))

    def test_followup_buttons_and_select_use_persistent_custom_ids(self):
        button_view = AIResponseBuilder.create_response_view(
            '<buttons requester_only><button url="https://example.com">Docs</button><button>Ask</button></buttons>',
            user=None,
            show_cost=False,
            show_model=False,
            requester_id=42,
        )
        select_view = AIResponseBuilder.create_response_view(
            "<select><option>A</option><option>B</option></select>",
            user=None,
            show_cost=False,
            show_model=False,
            requester_id=7,
        )

        buttons = [item for item in button_view.walk_children() if isinstance(item, AIFollowupButton)]
        self.assertEqual([button.custom_id for button in buttons], ["ai:fu:42:r:0"])
        link = next(
            item
            for item in button_view.walk_children()
            if isinstance(item, discord.ui.Button) and item.style is discord.ButtonStyle.link
        )
        self.assertEqual(link.url, "https://example.com")
        select = next(item for item in select_view.walk_children() if isinstance(item, AIFollowupSelect))
        self.assertEqual(select.custom_id, "ai:fs:7:a")
        self.assertEqual([option.value for option in select.item.options], ["0", "1"])

    def test_file_name_adds_a_file_component(self):
        view = AIResponseBuilder.create_response_view(
            "Summary",
            user=None,
            show_cost=False,
            show_model=False,
            file_name="answer.txt",
        )

        files = [item for item in view.walk_children() if isinstance(item, discord.ui.File)]
        self.assertEqual([item.to_component_dict()["file"]["url"] for item in files], ["attachment://answer.txt"])

    def test_oversized_layout_stays_within_discord_limits(self):
        response = "\n---\n".join(
            f"<section>Item {index}<button url='https://example.com/{index}'>Open</button></section>" + "x" * 200
            for index in range(60)
        )

        view = AIResponseBuilder.create_response_view(
            response,
            user=None,
            billing_info="cost",
            model_name="model",
            response_time="1s",
            warning="careful",
        )

        self.assertLessEqual(view.total_children_count, 40)
        self.assertLessEqual(view.content_length(), 3950)
        self.assertIn("-# ", view.children[0].children[-1].content)

    def test_empty_response_still_has_a_component(self):
        view = AIResponseBuilder.create_response_view("", user=None, show_cost=False, show_model=False)

        self.assertEqual(view.total_children_count, 2)


if __name__ == "__main__":
    unittest.main()
