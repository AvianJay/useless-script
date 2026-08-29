import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

DISCORD_DIR = Path(__file__).resolve().parents[1]
if str(DISCORD_DIR) not in sys.path:
    sys.path.insert(0, str(DISCORD_DIR))

import i18n
import Ticket


class TicketRuntimeNameTests(unittest.IsolatedAsyncioTestCase):
    def test_translation_helper_is_imported(self):
        self.assertIs(Ticket.t, i18n.t)

    async def test_claim_precheck_can_translate_before_ticket_loop(self):
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=2),
        )
        with (
            patch.object(Ticket, "find_ticket", return_value=None),
            patch.object(Ticket, "t", side_effect=lambda key, **kwargs: key),
        ):
            result = await Ticket.claim_ticket(interaction)

        self.assertEqual(result, "ticket.err.not_a_ticket")

    async def test_panel_command_can_translate_missing_channel_error(self):
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            guild=SimpleNamespace(id=1, get_channel=lambda channel_id: None),
        )
        with (
            patch.object(Ticket, "get_server_config", return_value=None),
            patch.object(Ticket, "t", side_effect=lambda key, **kwargs: key),
        ):
            await Ticket.TicketCog.panel.callback(None, interaction, None)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        interaction.followup.send.assert_awaited_once_with(
            "ticket.err.panel_channel_missing", ephemeral=True,
        )

    async def test_panel_command_can_translate_success(self):
        class FakeTextChannel:
            mention = "#tickets"

        channel = FakeTextChannel()
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            guild=SimpleNamespace(id=1),
        )
        publish_panel = AsyncMock(return_value=None)
        with (
            patch.object(Ticket.discord, "TextChannel", FakeTextChannel),
            patch.object(Ticket, "publish_panel", publish_panel),
            patch.object(Ticket, "t", side_effect=lambda key, **kwargs: key),
        ):
            await Ticket.TicketCog.panel.callback(None, interaction, channel)

        publish_panel.assert_awaited_once_with(interaction.guild, channel)
        interaction.followup.send.assert_awaited_once_with(
            "ticket.msg.panel_published", ephemeral=True,
        )


if __name__ == "__main__":
    unittest.main()
