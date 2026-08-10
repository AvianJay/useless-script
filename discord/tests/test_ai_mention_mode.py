import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AICommands


class AIMentionModeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot_user = SimpleNamespace(id=999, bot=True)
        self.context = SimpleNamespace(valid=False)
        self.bot = SimpleNamespace(
            user=self.bot_user,
            get_context=AsyncMock(return_value=self.context),
        )
        self.cog = AICommands(self.bot)
        self.guild = SimpleNamespace(id=100, owner_id=1)
        self.author = SimpleNamespace(id=42, bot=False)

    def _message(self, content, *, reference=None, webhook_id=None):
        return SimpleNamespace(
            content=content,
            guild=self.guild,
            author=self.author,
            reference=reference,
            webhook_id=webhook_id,
        )

    def test_mention_mode_defaults_to_off(self):
        with patch("ai.get_server_config", side_effect=lambda guild_id, key, default: default):
            self.assertFalse(self.cog._get_guild_ai_mention_mode(self.guild.id))

    async def test_admin_command_persists_enabled_state(self):
        response = SimpleNamespace(send_message=AsyncMock())
        interaction = SimpleNamespace(
            guild=self.guild,
            user=SimpleNamespace(
                id=1,
                guild_permissions=SimpleNamespace(administrator=False, manage_guild=True),
            ),
            response=response,
        )

        with patch("ai.set_server_config") as set_config:
            await AICommands.ai_mention_mode.callback(self.cog, interaction, True)

        set_config.assert_called_once_with(self.guild.id, self.cog.AI_GUILD_MENTION_MODE_KEY, True)
        self.assertIn("已開啟", response.send_message.await_args.args[0])

    async def test_direct_mention_triggers_with_bot_mention_removed(self):
        command = AsyncMock()
        message = self._message(f"<@{self.bot_user.id}>   你好")

        with (
            patch.object(self.cog, "_get_guild_ai_mention_mode", return_value=True),
            patch.object(self.cog, "ai_text_command", command),
        ):
            await self.cog.on_message(message)

        command.assert_awaited_once_with(self.context, message="你好")

    async def test_mention_without_remaining_text_is_ignored(self):
        command = AsyncMock()
        message = self._message(f"  <@!{self.bot_user.id}>  ")

        with (
            patch.object(self.cog, "_get_guild_ai_mention_mode", return_value=True),
            patch.object(self.cog, "ai_text_command", command),
        ):
            await self.cog.on_message(message)

        command.assert_not_awaited()
        self.bot.get_context.assert_not_awaited()

    async def test_reply_to_cached_ai_message_triggers(self):
        command = AsyncMock()
        reference = SimpleNamespace(
            message_id=777,
            type=discord.MessageReferenceType.reply,
        )
        message = self._message("繼續說明", reference=reference)

        with (
            patch.object(self.cog, "_get_guild_ai_mention_mode", return_value=True),
            patch.object(self.cog, "ai_text_command", command),
        ):
            self.cog._remember_ai_response_message(self.guild.id, 777)
            await self.cog.on_message(message)

        command.assert_awaited_once_with(self.context, message="繼續說明")

    async def test_components_v2_without_cached_id_does_not_trigger(self):
        command = AsyncMock()
        reference = SimpleNamespace(
            message_id=888,
            type=discord.MessageReferenceType.reply,
            resolved=SimpleNamespace(components=[object()]),
        )
        message = self._message("這是一般 Bot 元件訊息", reference=reference)

        with (
            patch.object(self.cog, "_get_guild_ai_mention_mode", return_value=True),
            patch.object(self.cog, "ai_text_command", command),
        ):
            await self.cog.on_message(message)

        command.assert_not_awaited()

    def test_components_v2_text_can_supply_reply_context(self):
        components = [
            SimpleNamespace(
                content="AI 的 Components V2 回覆內容",
                children=[],
                options=[],
                accessory=None,
            )
        ]

        self.assertIn("AI 的 Components V2 回覆內容", self.cog._extract_component_text(components))

    async def test_existing_text_command_is_not_triggered_twice(self):
        command = AsyncMock()
        self.context.valid = True
        reference = SimpleNamespace(
            message_id=777,
            type=discord.MessageReferenceType.reply,
        )
        message = self._message("y!ai 繼續", reference=reference)

        with (
            patch.object(self.cog, "_get_guild_ai_mention_mode", return_value=True),
            patch.object(self.cog, "ai_text_command", command),
        ):
            self.cog._remember_ai_response_message(self.guild.id, 777)
            await self.cog.on_message(message)

        command.assert_not_awaited()

    def test_cached_ai_message_expires(self):
        with (
            patch.object(self.cog, "_get_guild_ai_mention_mode", return_value=True),
            patch("ai.time.monotonic", return_value=100.0),
        ):
            self.cog._remember_ai_response_message(self.guild.id, 777)

        with patch(
            "ai.time.monotonic",
            return_value=100.0 + self.cog.AI_MENTION_MESSAGE_CACHE_TTL_SECONDS + 1,
        ):
            self.assertFalse(self.cog._is_cached_ai_response_message(self.guild.id, 777))


if __name__ == "__main__":
    unittest.main()
