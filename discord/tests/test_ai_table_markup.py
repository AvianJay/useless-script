import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import i18n
from ai import AICommands
from tests.i18n_base import ZhTWLocaleMixin


TIMESTAMP = 1792259663  # 2026-10-18 01:54:23 UTC+8，星期日
USER_ID = 111111111111111111
ROLE_ID = 222222222222222222
VISIBLE_CHANNEL_ID = 333333333333333333
HIDDEN_CHANNEL_ID = 444444444444444444
UNKNOWN_USER_ID = 555555555555555555
EMOJI_ID = 666666666666666666


def _channel(name: str, visible: bool):
    return SimpleNamespace(name=name, permissions_for=lambda _member: SimpleNamespace(view_channel=visible))


class AITableMarkupTests(ZhTWLocaleMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.bot = SimpleNamespace(
            user=SimpleNamespace(id=999),
            get_user=lambda _user_id: None,
            fetch_user=AsyncMock(side_effect=discord.HTTPException(MagicMock(status=404), "missing")),
        )
        self.cog = AICommands(self.bot)
        channels = {
            VISIBLE_CHANNEL_ID: _channel("公告", True),
            HIDDEN_CHANNEL_ID: _channel("管理員密談", False),
        }
        self.guild = SimpleNamespace(
            get_role=lambda role_id: SimpleNamespace(name="版主") if role_id == ROLE_ID else None,
            get_channel_or_thread=channels.get,
            get_member=lambda user_id: SimpleNamespace(display_name="Avian") if user_id == USER_ID else None,
        )
        self.tool_context = {"guild": self.guild, "user": MagicMock(spec=discord.Member)}

    async def test_resolves_labels_and_fetches_custom_emoji(self):
        text = (
            "| 時間 | 誰 | 表情 |\n|---|---|---|\n"
            f"| <t:{TIMESTAMP}:T> | <@{USER_ID}> <@&{ROLE_ID}> | <:pepe:{EMOJI_ID}> |\n"
            f"| <t:{TIMESTAMP}:D> | <#{VISIBLE_CHANNEL_ID}> <#{HIDDEN_CHANNEL_ID}> <@{UNKNOWN_USER_ID}> | <a:pepe:{EMOJI_ID}> |"
        )

        with patch.object(self.cog, "_fetch_discord_image_bytes", new=AsyncMock(return_value=(b"png", None))) as fetch:
            emoji_images, labels = await self.cog._resolve_table_markup(text, self.tool_context)

        self.assertEqual(emoji_images, {str(EMOJI_ID): b"png"})
        fetch.assert_awaited_once_with(f"https://cdn.discordapp.com/emojis/{EMOJI_ID}.png?size=64")
        self.assertEqual(
            labels,
            {
                f"<t:{TIMESTAMP}:T>": "01:54:23",
                f"<t:{TIMESTAMP}:D>": "2026年10月18日",
                f"<@{USER_ID}>": "@Avian",
                f"<@&{ROLE_ID}>": "@版主",
                f"<#{VISIBLE_CHANNEL_ID}>": "#公告",
                f"<#{HIDDEN_CHANNEL_ID}>": "#未知頻道",
                f"<@{UNKNOWN_USER_ID}>": "@未知使用者",
            },
        )

    async def test_failed_emoji_fetch_is_skipped(self):
        text = f"| 表情 |\n|---|\n| <:pepe:{EMOJI_ID}> |"

        with patch.object(self.cog, "_fetch_discord_image_bytes", new=AsyncMock(side_effect=TimeoutError())):
            emoji_images, labels = await self.cog._resolve_table_markup(text, self.tool_context)

        self.assertEqual((emoji_images, labels), ({}, {}))

    async def test_text_without_tables_skips_lookups(self):
        with patch.object(self.cog, "_fetch_discord_image_bytes", new=AsyncMock()) as fetch:
            result = await self.cog._resolve_table_markup(f"<:pepe:{EMOJI_ID}> <t:{TIMESTAMP}:T>", self.tool_context)

        self.assertEqual(result, ({}, {}))
        fetch.assert_not_awaited()

    async def test_delivery_passes_resolved_markup_to_table_renderer(self):
        text = f"| 時間 |\n|---|\n| <t:{TIMESTAMP}:T> |"

        with patch("ai.render_rich_markdown_images", return_value=("rendered", [])) as render:
            _, display, _, _ = await self.cog._prepare_ai_response_delivery(text, {})

        self.assertEqual(display, "rendered")
        self.assertEqual(render.call_args.kwargs["markup_labels"], {f"<t:{TIMESTAMP}:T>": "01:54:23"})
        self.assertEqual(render.call_args.kwargs["emoji_images"], {})


class AITimestampFormatTests(unittest.TestCase):
    def test_formats_follow_discord_per_locale(self):
        expected = {
            "zh-TW": ("01:54", "01:54:23", "2026/10/18", "2026年10月18日星期日 01:54"),
            "en": ("1:54 AM", "1:54:23 AM", "10/18/2026", "Sunday, October 18, 2026 1:54 AM"),
            "ja": ("01:54", "01:54:23", "2026/10/18", "2026年10月18日 日曜日 01:54"),
        }
        for locale, values in expected.items():
            with self.subTest(locale=locale), i18n.use_locale(locale):
                self.assertEqual(
                    tuple(AICommands._format_discord_timestamp(TIMESTAMP, style) for style in ("t", "T", "d", "F")),
                    values,
                )

    def test_relative_time(self):
        cases = {
            "zh-TW": ("幾秒前", "3 小時前", "3 小時後", "1 個月前", "2 年前"),
            "en": ("a few seconds ago", "3 hours ago", "in 3 hours", "a month ago", "2 years ago"),
        }
        offsets = (10, 3 * 3600, -3 * 3600, 40 * 86400, 800 * 86400)
        for locale, values in cases.items():
            with self.subTest(locale=locale), i18n.use_locale(locale):
                self.assertEqual(
                    tuple(
                        AICommands._format_discord_timestamp(TIMESTAMP, "R", now=TIMESTAMP + offset)
                        for offset in offsets
                    ),
                    values,
                )


if __name__ == "__main__":
    unittest.main()
