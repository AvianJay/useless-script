import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AICommands, AIFollowupButton, AIFollowupSelect
from i18n import t


class AIFollowupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace(user=SimpleNamespace(id=999)))
        self.message = SimpleNamespace(id=500)

    def _interaction(self, user_id: int):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            message=self.message,
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            client=SimpleNamespace(get_cog=lambda name: self.cog if name == "AICommands" else None),
        )

    async def _start(self, user_id: int, *, requester_only: bool = False):
        interaction = self._interaction(user_id)
        chat = AsyncMock()

        async def run_request(_user_id, request):
            await request("state")
            return True

        with (
            patch.object(self.cog, "_run_ai_request", side_effect=run_request) as run,
            patch.object(self.cog, "_ai_chat_request", new=chat),
        ):
            await self.cog._start_ai_followup(interaction, " Next step ", owner_id=42, requester_only=requester_only)
        return interaction, run, chat

    async def test_button_custom_id_round_trips(self):
        button = AIFollowupButton(42, True, 3, label="Next step", style=discord.ButtonStyle.primary)

        restored = await AIFollowupButton.from_custom_id(None, button.item, button.template.fullmatch(button.custom_id))

        self.assertEqual(restored.custom_id, "ai:fu:42:r:3")
        self.assertEqual((restored.owner_id, restored.requester_only), (42, True))
        self.assertEqual((restored.item.label, restored.item.style), ("Next step", discord.ButtonStyle.primary))

    async def test_select_custom_id_round_trips_options(self):
        options = [discord.SelectOption(label="A", value="0"), discord.SelectOption(label="B", value="1")]
        select = AIFollowupSelect(7, False, options=options, placeholder="Pick")

        restored = await AIFollowupSelect.from_custom_id(None, select.item, select.template.fullmatch(select.custom_id))

        self.assertEqual(restored.custom_id, "ai:fs:7:a")
        self.assertEqual([option.label for option in restored.item.options], ["A", "B"])
        self.assertEqual(restored.item.placeholder, "Pick")

    async def test_button_callback_sends_its_label(self):
        interaction = self._interaction(42)

        with patch.object(self.cog, "_start_ai_followup", new=AsyncMock()) as start:
            await AIFollowupButton(42, True, 0, label="Next").callback(interaction)

        start.assert_awaited_once_with(interaction, "Next", owner_id=42, requester_only=True)

    async def test_select_callback_sends_the_chosen_label(self):
        options = [discord.SelectOption(label="Apple", value="0"), discord.SelectOption(label="Banana", value="1")]
        select = AIFollowupSelect(42, False, options=options)
        select.item._values = ["1"]
        interaction = self._interaction(5)

        with patch.object(self.cog, "_start_ai_followup", new=AsyncMock()) as start:
            await select.callback(interaction)

        start.assert_awaited_once_with(interaction, "Banana", owner_id=42, requester_only=False)

    async def test_requester_only_rejects_other_users(self):
        interaction, run, _chat = await self._start(5, requester_only=True)

        run.assert_not_awaited()
        self.assertEqual(interaction.response.send_message.await_args.args[0], t("ai.err.not_your_conversation"))
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_other_users_get_the_reply_as_context(self):
        interaction, run, chat = await self._start(5)

        self.assertEqual(run.await_args.args[0], 5)
        chat.assert_awaited_once_with(
            interaction,
            "Next step",
            request_state="state",
            followup={"label": "Next step", "source_message": self.message},
        )

    async def test_requester_follow_up_relies_on_history(self):
        interaction, _run, chat = await self._start(42, requester_only=True)

        chat.assert_awaited_once_with(
            interaction,
            "Next step",
            request_state="state",
            followup={"label": "Next step", "source_message": None},
        )

    async def test_busy_user_is_told_to_wait(self):
        interaction = self._interaction(5)

        with patch.object(self.cog, "_run_ai_request", new=AsyncMock(return_value=False)):
            await self.cog._start_ai_followup(interaction, "Next", owner_id=42, requester_only=False)

        self.assertEqual(interaction.response.send_message.await_args.args[0], t("ai.err.request_in_progress"))


class AIFileDeliveryTests(unittest.TestCase):
    def test_file_is_inlined_only_when_it_fits_beside_the_images(self):
        cog = AICommands(SimpleNamespace(user=SimpleNamespace(id=999)))
        images = [{"filename": "a.png", "content": b"img"}]

        self.assertTrue(cog._can_inline_file_response({"filename": "answer.txt", "content": "x"}, images))
        self.assertFalse(
            cog._can_inline_file_response(
                {"filename": "answer.txt", "content": "x", "size_bytes": cog.IMAGE_BYTES_PER_MESSAGE},
                images,
            )
        )
        self.assertFalse(cog._can_inline_file_response({"filename": "a.png", "content": "x"}, images))
        self.assertFalse(
            cog._can_inline_file_response(
                {"filename": "answer.txt", "content": "x"},
                [{"filename": f"{index}.png", "content": b"img"} for index in range(cog.IMAGE_FILES_PER_MESSAGE)],
            )
        )


if __name__ == "__main__":
    unittest.main()
