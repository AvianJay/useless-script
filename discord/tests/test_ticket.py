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


def _config_getter(values: dict):
    return lambda guild_id, key, default=None: values.get(key, default)


class TicketOpenModalConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_form_uses_localized_texts(self):
        with (
            patch.object(Ticket, "get_server_config", side_effect=_config_getter({})),
            i18n.use_locale("en"),
        ):
            modal = Ticket.TicketOpenModal(1)

        self.assertEqual(modal.title, "Open a ticket")
        self.assertEqual(len(modal.children), 2)
        self.assertEqual(modal.subject.label, "Subject")
        self.assertIsNone(modal.subject.placeholder)
        self.assertTrue(modal.subject.required)
        self.assertEqual(modal.detail.label, "Description")
        self.assertFalse(modal.detail.required)

    async def test_custom_form_texts_and_type_placeholder(self):
        values = {
            "ticket_modal_title": "申請 {type}",
            "ticket_modal_subject_label": "你的遊戲 ID",
            "ticket_modal_subject_placeholder": "例如 Steve123",
            "ticket_modal_subject_required": False,
            "ticket_modal_detail_label": "原因",
            "ticket_modal_detail_placeholder": "請說明原因",
            "ticket_modal_detail_required": True,
        }
        with patch.object(Ticket, "get_server_config", side_effect=_config_getter(values)):
            modal = Ticket.TicketOpenModal(1, {"id": "abc", "label": "白名單"})

        self.assertEqual(modal.title, "申請 白名單")
        self.assertEqual(modal.subject.label, "你的遊戲 ID")
        self.assertEqual(modal.subject.placeholder, "例如 Steve123")
        self.assertFalse(modal.subject.required)
        self.assertEqual(modal.detail.label, "原因")
        self.assertEqual(modal.detail.placeholder, "請說明原因")
        self.assertTrue(modal.detail.required)

    async def test_texts_are_truncated_to_discord_limits(self):
        values = {
            "ticket_modal_title": "T" * 100,
            "ticket_modal_subject_label": "L" * 100,
            "ticket_modal_subject_placeholder": "P" * 200,
        }
        with patch.object(Ticket, "get_server_config", side_effect=_config_getter(values)):
            modal = Ticket.TicketOpenModal(1)

        self.assertEqual(len(modal.title), Ticket.MODAL_TITLE_MAX)
        self.assertEqual(len(modal.subject.label), Ticket.MODAL_LABEL_MAX)
        self.assertEqual(len(modal.subject.placeholder), Ticket.MODAL_PLACEHOLDER_MAX)

    async def test_detail_field_can_be_hidden(self):
        values = {"ticket_modal_detail_enabled": False}
        with patch.object(Ticket, "get_server_config", side_effect=_config_getter(values)):
            modal = Ticket.TicketOpenModal(1)

        self.assertEqual(modal.children, [modal.subject])
        self.assertIsNone(modal.detail)

        interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()))
        modal.subject._value = "  help  "
        open_ticket = AsyncMock()
        with patch.object(Ticket, "open_ticket", open_ticket):
            await modal.on_submit(interaction)
        open_ticket.assert_awaited_once_with(interaction, None, "help", "")


class TicketOpenButtonTests(unittest.IsolatedAsyncioTestCase):
    def _interaction(self):
        return SimpleNamespace(
            guild=SimpleNamespace(id=1),
            user=SimpleNamespace(id=2),
            response=SimpleNamespace(defer=AsyncMock(), send_modal=AsyncMock(), send_message=AsyncMock()),
        )

    async def test_modal_disabled_opens_ticket_directly(self):
        interaction = self._interaction()
        open_ticket = AsyncMock()
        with (
            patch.object(Ticket, "precheck_open", return_value=None),
            patch.object(Ticket, "get_server_config", side_effect=_config_getter({"ticket_modal_enabled": False})),
            patch.object(Ticket, "open_ticket", open_ticket),
        ):
            await Ticket.handle_open_button(interaction, None)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        interaction.response.send_modal.assert_not_awaited()
        open_ticket.assert_awaited_once_with(interaction, None, "", "")

    async def test_modal_enabled_by_default(self):
        interaction = self._interaction()
        open_ticket = AsyncMock()
        with (
            patch.object(Ticket, "precheck_open", return_value=None),
            patch.object(Ticket, "get_server_config", side_effect=_config_getter({})),
            patch.object(Ticket, "open_ticket", open_ticket),
        ):
            await Ticket.handle_open_button(interaction, None)

        interaction.response.send_modal.assert_awaited_once()
        self.assertIsInstance(interaction.response.send_modal.await_args.args[0], Ticket.TicketOpenModal)
        open_ticket.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
