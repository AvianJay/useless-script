"""讚按鈕（UpvoteButton）的狀態全部存在 custom_id 裡，不靠資料庫也不靠 view 存活。"""

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import MessageImage

UpvoteButton = MessageImage.UpvoteButton
PATTERN = UpvoteButton.__discord_ui_compiled_template__
AUTHOR_ID = 1150000000000000001


class FakeInteraction:
    def __init__(self, message, user_id, guild=None):
        self.message = message
        self.user = SimpleNamespace(id=user_id)
        self.guild = guild
        self.response = SimpleNamespace(defer=AsyncMock())
        self.followup = SimpleNamespace(send=AsyncMock())
        self.edits = []

    async def edit_original_response(self, **kwargs):
        await asyncio.sleep(0)  # 讓並行的點擊有機會交錯
        self.edits.append(kwargs)


def _message(message_id, attachments=()):
    return SimpleNamespace(id=message_id, attachments=list(attachments), components=[])


async def _click(custom_id, interaction):
    """模擬 ViewStore.schedule_dynamic_item_call：從訊息上的 custom_id 重建按鈕再呼叫 callback"""
    item = await UpvoteButton.from_custom_id(interaction, None, PATTERN.fullmatch(custom_id))
    view = discord.ui.View(timeout=None)
    view.add_item(item)
    await item.callback(interaction)
    return item


class _ViewWithExtraButton(MessageImage.UpvoteView):
    @discord.ui.button(label="GIF")
    async def extra(self, interaction, button):
        pass


class UpvoteCustomIdTests(unittest.IsolatedAsyncioTestCase):
    async def test_worst_case_custom_id_fits_and_round_trips(self):
        big = 2**63 - 1
        button = UpvoteButton(big, count=99999, board_id=big, guild_board_id=big, voters=2**288 - 1)

        self.assertLessEqual(len(button.custom_id), 100)
        restored = await UpvoteButton.from_custom_id(None, None, PATTERN.fullmatch(button.custom_id))
        self.assertEqual(
            (restored.author_id, restored.count, restored.board_id, restored.guild_board_id, restored.voters),
            (big, 99999, big, big, 2**288 - 1),
        )

    async def test_votes_once_per_user_and_does_not_store_temporary_view(self):
        message = _message(101)
        first = FakeInteraction(message, user_id=1)
        item = await _click(UpvoteButton(AUTHOR_ID).custom_id, first)

        self.assertEqual(item.count, 1)
        self.assertIsNotNone(item.item.label)
        self.assertTrue(first.edits[0]["view"].is_finished(), "重建的 view 不能被存進 ViewStore")

        again = FakeInteraction(message, user_id=1)
        await _click(item.custom_id, again)
        again.followup.send.assert_awaited_once()
        self.assertEqual(again.edits, [])

        other = FakeInteraction(message, user_id=2)
        item = await _click(item.custom_id, other)
        self.assertEqual(item.count, 2)

    async def test_concurrent_clicks_with_stale_custom_id_are_not_lost(self):
        message = _message(102)
        stale = UpvoteButton(AUTHOR_ID).custom_id
        a, b = FakeInteraction(message, user_id=1), FakeInteraction(message, user_id=2)

        await asyncio.gather(_click(stale, a), _click(stale, b))

        final = (b.edits or a.edits)[-1]["view"].children[0]
        self.assertEqual(final.count, 2)
        for user_id in (1, 2):
            bits = MessageImage._upvote_voter_bits(message.id, user_id)
            self.assertEqual(final.voters & bits, bits)
        self.assertNotIn(message.id, MessageImage._upvote_queues)

    async def test_board_is_posted_once_then_edited(self):
        message = _message(103, attachments=[SimpleNamespace(url="https://cdn.example/quote.png")])
        board_message = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(
            send=AsyncMock(return_value=SimpleNamespace(id=4242)),
            get_partial_message=MagicMock(return_value=board_message),
        )
        author = SimpleNamespace(display_name="作者", name="author", display_avatar=SimpleNamespace(url="https://cdn.example/a.png"))

        with (
            patch.object(MessageImage, "config", return_value=999),
            patch.object(MessageImage.bot, "get_channel", return_value=channel),
            patch.object(MessageImage.bot, "get_user", return_value=author),
        ):
            custom_id = UpvoteButton(AUTHOR_ID).custom_id
            for user_id in range(1, 6):
                item = await _click(custom_id, FakeInteraction(message, user_id))
                custom_id = item.custom_id

            channel.send.assert_awaited_once()
            self.assertEqual(item.board_id, 4242)

            item = await _click(custom_id, FakeInteraction(message, user_id=6))

        channel.send.assert_awaited_once()
        channel.get_partial_message.assert_called_with(4242)
        board_message.edit.assert_awaited_once()
        self.assertEqual(item.count, 6)


class UpvoteViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_upvote_stays_first_and_syncs_from_message(self):
        view = _ViewWithExtraButton(SimpleNamespace(id=AUTHOR_ID))
        self.assertIs(view.children[0], view.upvote_button)

        current = UpvoteButton(AUTHOR_ID, count=3).custom_id
        message = _message(104)
        message.components = [SimpleNamespace(children=[
            SimpleNamespace(custom_id=current),
            SimpleNamespace(custom_id=view.extra.custom_id),
        ])]
        self.assertTrue(await view.interaction_check(FakeInteraction(message, user_id=1)))
        self.assertEqual(view.upvote_button.count, 3)
        self.assertEqual(view.upvote_button.custom_id, current)

    async def test_timeout_restores_dynamic_item_registration(self):
        store = discord.ui.view.ViewStore(state=None)
        store.add_dynamic_items(UpvoteButton)
        view = _ViewWithExtraButton(SimpleNamespace(id=AUTHOR_ID))
        store.add_view(view, 105)

        view.stop()  # 與逾時同樣走 ViewStore.remove_view
        self.assertNotIn(
            PATTERN,
            store._dynamic_items,
            "discord.py 不再於移除 view 時取消註冊 DynamicItem 了，UpvoteView.on_timeout 可以拿掉",
        )

        with patch.object(MessageImage.bot, "add_dynamic_items", store.add_dynamic_items):
            await view.on_timeout()
        self.assertIs(store._dynamic_items[PATTERN], UpvoteButton)


if __name__ == "__main__":
    unittest.main()
