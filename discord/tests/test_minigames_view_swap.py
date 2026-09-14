"""Regression tests for the MiniGames view-swap ordering.

discord.py 的 ``ViewStore`` 以 message_id 為 key 共用一張 dispatch 表，表內的
key 是 ``(component_type, custom_id)``。``TowerGameView`` 的 custom_id
（``tile_{層}_{格}``、``tower_end``）在每個 view 實例之間都一樣，所以：

* ``edit_message(view=new)`` 會把新 view 的按鈕寫進那張共用表（覆蓋舊的同名 key）
* 之後才呼叫 ``old_view.stop()`` → ``ViewStore.remove_view(old)`` 會依舊 view 的
  snapshot key 去 pop，**連剛寫進去的新按鈕一起刪掉**

結果就是 Tower 在第一層之後每次點擊都變成「互動失敗」，而且因為 discord.py
只是找不到 item 就丟棄，伺服器端不會留下任何 traceback。

修法是把 ``old_view.stop()`` 移到 edit 之前。以下兩個測試分別鎖住
「discord.py 的行為」與「MiniGames.py 的實際寫法」。
"""

import ast
import asyncio
import sys
import unittest
from pathlib import Path

import discord
from discord.ui import Button, View

DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

MINIGAMES = DISCORD_DIR / "MiniGames.py"
BUTTON = discord.ComponentType.button.value
MESSAGE_ID = 1234567890


def _tower_like_view():
    """A view whose custom_ids are stable across instances, like TowerGameView."""
    view = View(timeout=120)
    for level in (1, 2):
        for tile in range(3):
            view.add_item(Button(label="x", custom_id=f"tile_{level}_{tile}"))
    return view


class ViewSwapDispatchTest(unittest.TestCase):
    """Locks in the discord.py behaviour that makes the ordering matter."""

    def _run(self, stop_before_edit):
        async def scenario():
            store = discord.ui.view.ViewStore(state=None)
            old, new = _tower_like_view(), _tower_like_view()

            store.add_view(old, MESSAGE_ID)
            if stop_before_edit:
                old.stop()
                store.add_view(new, MESSAGE_ID)
            else:
                store.add_view(new, MESSAGE_ID)
                old.stop()

            item = store._views.get(MESSAGE_ID, {}).get((BUTTON, "tile_2_0"))
            return item, new

        return asyncio.run(scenario())

    def test_stopping_the_old_view_after_the_edit_kills_the_new_buttons(self):
        item, _ = self._run(stop_before_edit=False)
        self.assertIsNone(
            item,
            "discord.py 的語意變了：舊 view 晚停不再清掉新 view 的 dispatch 項目。"
            "若確實如此，MiniGames.py 的順序註解需要一併更新。",
        )

    def test_stopping_the_old_view_before_the_edit_keeps_them_alive(self):
        item, new = self._run(stop_before_edit=True)
        self.assertIsNotNone(item, "新 view 的按鈕應該仍可被 dispatch")
        self.assertIn(item, new.children, "dispatch 到的必須是新 view 的按鈕")


class MiniGamesOrderingTest(unittest.TestCase):
    """Every view swap in MiniGames.py must stop the old view before the edit."""

    #: 每個置換點的區塊都由這行開始
    ANCHOR = "old_view = game.active_view"
    STOP = "old_view.stop()"
    EDITS = (".edit_message(", "message.edit(", "await message.edit")

    def test_source_parses(self):
        source = MINIGAMES.read_text(encoding="utf-8-sig")
        ast.parse(source)  # raises on syntax error

    def test_old_view_is_stopped_before_the_edit(self):
        lines = MINIGAMES.read_text(encoding="utf-8-sig").splitlines()
        anchors = [i for i, line in enumerate(lines) if self.ANCHOR in line]
        self.assertTrue(anchors, "找不到任何 view 置換點 — 測試的錨點過期了")

        offenders = []
        for start in anchors:
            block = lines[start:start + 20]
            stop_at = next((i for i, l in enumerate(block) if self.STOP in l), None)
            edit_at = next(
                (i for i, l in enumerate(block) if any(e in l for e in self.EDITS)),
                None,
            )
            if stop_at is None or edit_at is None:
                continue  # 沒有同時出現兩者的區塊不受此規則約束
            if stop_at > edit_at:
                offenders.append(
                    f"MiniGames.py:{start + 1 + edit_at} — "
                    f"old_view.stop() 出現在 edit 之後（第 {start + 1 + stop_at} 行）"
                )

        self.assertEqual(
            [],
            offenders,
            "舊 view 必須在 edit 註冊新 view 之前停掉，否則新按鈕會被一起移除：\n"
            + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
