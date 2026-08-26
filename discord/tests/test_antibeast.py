import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import AntiBeast as antibeast_module


class AntiBeastRuleSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_rule_builds_localized_action_for_guild(self):
        cog = antibeast_module.AntiBeast(SimpleNamespace())
        created_rule = SimpleNamespace(id=1234)
        guild = SimpleNamespace(
            id=5678,
            create_automod_rule=AsyncMock(return_value=created_rule),
        )
        config = {"bypass_roles": [], "rule_id": None}

        with (
            patch.object(cog, "_resolve_bypass_roles", return_value=[]),
            patch.object(cog, "_find_rule", new=AsyncMock(return_value=None)),
            patch.object(cog, "_build_trigger", return_value=object()),
            patch.object(antibeast_module.i18n, "resolve_locale", return_value="en") as resolve_locale,
            patch.object(antibeast_module, "t", return_value="localized block message"),
        ):
            rule = await cog._sync_rule(
                guild,
                config,
                enabled=True,
                create_if_missing=True,
                reason="test role reconciliation",
            )

        self.assertIs(rule, created_rule)
        self.assertEqual(config["rule_id"], created_rule.id)
        resolve_locale.assert_called_once_with(guild_id=guild.id)
        action = guild.create_automod_rule.await_args.kwargs["actions"][0]
        self.assertEqual(action.custom_message, "localized block message")


if __name__ == "__main__":
    unittest.main()
