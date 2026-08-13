import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import (
    AICommands,
    AI_GUILD_CUSTOM_PROMPT_LIMIT_CONFIG_KEY,
    DEFAULT_AI_GUILD_CUSTOM_PROMPT_LIMIT,
    MAX_AI_GUILD_CUSTOM_PROMPT_LIMIT,
    MIN_AI_GUILD_CUSTOM_PROMPT_LIMIT,
)


class AICustomPromptLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())

    def test_per_guild_limit_defaults_and_clamps_invalid_stored_values(self):
        cases = (
            (None, DEFAULT_AI_GUILD_CUSTOM_PROMPT_LIMIT),
            ("invalid", DEFAULT_AI_GUILD_CUSTOM_PROMPT_LIMIT),
            (0, MIN_AI_GUILD_CUSTOM_PROMPT_LIMIT),
            (MAX_AI_GUILD_CUSTOM_PROMPT_LIMIT + 1, MAX_AI_GUILD_CUSTOM_PROMPT_LIMIT),
            ("3200", 3200),
        )

        for stored_value, expected in cases:
            with self.subTest(stored_value=stored_value):
                with patch.object(self.cog, "_get_server_config_fallback", return_value=stored_value) as getter:
                    self.assertEqual(self.cog._get_guild_ai_custom_prompt_limit(123), expected)
                getter.assert_called_once_with(123, AI_GUILD_CUSTOM_PROMPT_LIMIT_CONFIG_KEY, DEFAULT_AI_GUILD_CUSTOM_PROMPT_LIMIT)

    def test_different_guilds_use_different_limits(self):
        stored_limits = {123: 1200, 456: 4200}
        with patch.object(
            self.cog,
            "_get_server_config_fallback",
            side_effect=lambda guild_id, _key, default: stored_limits.get(guild_id, default),
        ):
            self.assertEqual(self.cog._get_guild_ai_custom_prompt_limit(123), 1200)
            self.assertEqual(self.cog._get_guild_ai_custom_prompt_limit(456), 4200)
            self.assertEqual(self.cog._get_guild_ai_custom_prompt_limit(789), DEFAULT_AI_GUILD_CUSTOM_PROMPT_LIMIT)

    def test_prompt_sanitizer_uses_configured_limit(self):
        with patch.object(self.cog, "_get_guild_ai_custom_prompt_limit", return_value=5) as getter:
            self.assertEqual(self.cog._sanitize_guild_ai_custom_prompt("  abc   def  ", 123), "abc d")
        getter.assert_called_once_with(123)

    async def test_server_prompt_set_uses_current_guild_limit(self):
        guild = SimpleNamespace(id=456, owner_id=1)
        interaction = SimpleNamespace(
            guild=guild,
            user=SimpleNamespace(id=1),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        with (
            patch.object(self.cog, "_get_guild_ai_custom_prompt_limit", return_value=5) as getter,
            patch("ai.set_server_config") as setter,
        ):
            await AICommands.ai_server_prompt_set.callback(
                self.cog,
                interaction,
                "abcdefgh",
            )

        getter.assert_any_call(456)
        setter.assert_called_once_with(456, self.cog.AI_GUILD_CUSTOM_PROMPT_KEY, "abcde")
        self.assertIn("5/5", interaction.response.send_message.await_args.args[0])

    async def test_owner_config_command_updates_valid_limit(self):
        ctx = SimpleNamespace(send=AsyncMock())

        with patch("ai.set_server_config") as setter:
            await AICommands.ai_config_server_prompt_limit_text.callback(
                self.cog,
                ctx,
                guild_id=123,
                limit=3200,
            )

        setter.assert_called_once_with(123, AI_GUILD_CUSTOM_PROMPT_LIMIT_CONFIG_KEY, 3200)
        self.assertIn("123", ctx.send.await_args.args[0])
        self.assertIn("3200", ctx.send.await_args.args[0])

    async def test_owner_config_command_views_requested_guild_limit(self):
        ctx = SimpleNamespace(send=AsyncMock())

        with patch.object(self.cog, "_get_guild_ai_custom_prompt_limit", return_value=2400) as getter:
            await AICommands.ai_config_server_prompt_limit_text.callback(
                self.cog,
                ctx,
                guild_id=456,
                limit=None,
            )

        getter.assert_called_once_with(456)
        self.assertIn("456", ctx.send.await_args.args[0])
        self.assertIn("2400", ctx.send.await_args.args[0])

    async def test_owner_config_command_rejects_out_of_range_limit(self):
        ctx = SimpleNamespace(send=AsyncMock())

        with patch("ai.set_server_config") as setter:
            await AICommands.ai_config_server_prompt_limit_text.callback(
                self.cog,
                ctx,
                guild_id=123,
                limit=MAX_AI_GUILD_CUSTOM_PROMPT_LIMIT + 1,
            )

        setter.assert_not_called()
        self.assertIn("must be between", ctx.send.await_args.args[0])

    def test_owner_config_command_keeps_owner_check(self):
        self.assertTrue(AICommands.ai_config_server_prompt_limit_text.checks)

    def test_long_prompt_display_uses_text_attachment(self):
        custom_prompt = "a" * 2100

        with patch.object(self.cog, "_get_guild_ai_custom_prompt_limit", return_value=6000) as getter:
            message, prompt_file = self.cog._build_guild_ai_custom_prompt_display(custom_prompt, 123)

        getter.assert_called_once_with(123)
        self.assertIn("2100/6000", message)
        self.assertEqual(prompt_file.filename, "ai_custom_prompt.txt")
        self.assertEqual(prompt_file.fp.read().decode("utf-8"), custom_prompt)
        prompt_file.close()


if __name__ == "__main__":
    unittest.main()
