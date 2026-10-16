import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import AutoReply as autoreply_module
import ItemSystem as item_module


class CustomItemTemplateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents(guilds=True))
        self.cog = autoreply_module.AutoReply(self.bot)
        await self.bot.add_cog(self.cog)
        self.addAsyncCleanup(self.bot.close)
        bot_patch = patch.object(item_module, "bot", self.bot)
        bot_patch.start()
        self.addCleanup(bot_patch.stop)

        guild = SimpleNamespace(
            id=123,
            name="Test Guild",
            icon=None,
            banner=None,
            owner=None,
            member_count=1,
            roles=[],
            premium_subscription_count=0,
            emojis=[],
            stickers=[],
        )
        user = SimpleNamespace(
            id=42,
            name="Test User",
            mention="<@42>",
            display_avatar=None,
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        self.interaction = SimpleNamespace(
            guild_id=guild.id,
            guild=guild,
            user=user,
            channel=SimpleNamespace(name="items"),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    async def test_cog_lookup_keeps_command_group_localization(self):
        self.assertIs(item_module._get_autoreply_cog(), self.cog)
        group = self.bot.tree.get_command("autoreply")
        self.assertIsNotNone(group)
        self.assertEqual(
            group._locale_name.extras["i18n_key"],
            "cmd.autoreply.autoreply.root.name",
        )
        self.assertEqual(
            group._locale_description.extras["i18n_key"],
            "cmd.autoreply.autoreply.root.desc",
        )

    async def test_custom_item_renders_user_variables_and_newlines(self):
        await item_module._send_custom_item_content(
            self.interaction,
            "custom_coriander",
            "{user}高高興興地吃完了一大把香菜，並表示喜歡，下次還想吃。\\n{author} / {guild}",
            False,
        )

        self.interaction.response.send_message.assert_awaited_once()
        args, kwargs = self.interaction.response.send_message.await_args
        self.assertEqual(
            args[0],
            "<@42>高高興興地吃完了一大把香菜，並表示喜歡，下次還想吃。\nTest User / Test Guild",
        )
        self.assertFalse(kwargs["ephemeral"])
        self.assertTrue(kwargs["allowed_mentions"].users)
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertFalse(kwargs["allowed_mentions"].roles)

    async def test_custom_item_renders_conditional_embed(self):
        await item_module._send_custom_item_content(
            self.interaction,
            "custom_coriander",
            "{if:{id}==42:{embedtitle:香菜}{embeddescription:{user} 吃完了。}:wrong user}",
            True,
        )

        args, kwargs = self.interaction.response.send_message.await_args
        self.assertIsNone(args[0])
        self.assertEqual(kwargs["embed"].title, "香菜")
        self.assertEqual(kwargs["embed"].description, "<@42> 吃完了。")
        self.assertTrue(kwargs["ephemeral"])

    async def test_custom_item_rejects_invalid_autoreply_syntax(self):
        with self.assertRaises(autoreply_module.TemplateSyntaxError):
            item_module._validate_custom_item_template("{if:broken}")


if __name__ == "__main__":
    unittest.main()
