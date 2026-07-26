import sys
import unittest
from pathlib import Path

import discord


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AIResponseBuilder


class AIResponseMediaTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
