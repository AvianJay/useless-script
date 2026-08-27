import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import globalenv


owner_tools_added = "OwnerTools" not in globalenv.modules
previous_owner_tools = sys.modules.get("OwnerTools")
if owner_tools_added:
    globalenv.modules.append("OwnerTools")
sys.modules["OwnerTools"] = types.SimpleNamespace(
    is_owner=lambda: (lambda function: function),
)
try:
    import dsize
finally:
    if owner_tools_added:
        globalenv.modules.remove("OwnerTools")
    if previous_owner_tools is None:
        sys.modules.pop("OwnerTools", None)
    else:
        sys.modules["OwnerTools"] = previous_owner_tools


class FakeResponse:
    def __init__(self):
        self.modal = None
        self.messages = []

    async def send_modal(self, modal):
        self.modal = modal

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


class FakeInteraction:
    def __init__(self):
        self.user = types.SimpleNamespace(id=7, display_name="Operator")
        self.guild = types.SimpleNamespace(id=123, name="Guild")
        self.guild_id = self.guild.id
        self.response = FakeResponse()
        self.edits = []

    async def edit_original_response(self, **kwargs):
        payload = dict(kwargs)
        if payload.get("embed") is not None:
            payload["embed"] = payload["embed"].to_dict()
        self.edits.append(payload)


class DSizeScalpelTests(unittest.IsolatedAsyncioTestCase):
    async def test_scalpel_uses_same_final_size_for_record_display_and_history(self):
        today = datetime.now(timezone(timedelta(hours=8))).date()
        target = types.SimpleNamespace(
            id=42,
            display_name="Target",
            mention="<@42>",
        )
        data = {
            (123, 42, "last_dsize"): today,
            (123, 42, "last_dsize_size"): 10,
        }

        def get_user_data(guild_id, user_id, key, default=None):
            return data.get((guild_id, user_id, key), default)

        def set_user_data(guild_id, user_id, key, value):
            data[(guild_id, user_id, key)] = value

        item_system = types.SimpleNamespace(
            remove_item_from_user=AsyncMock(return_value=True),
        )
        interaction = FakeInteraction()

        with (
            patch.object(dsize, "get_user_data", side_effect=get_user_data),
            patch.object(dsize, "set_user_data", side_effect=set_user_data),
            patch.object(dsize, "get_server_config", return_value=10),
            patch.object(dsize, "ItemSystem", item_system, create=True),
            patch.object(dsize.random, "randint", return_value=3),
            patch.object(dsize.asyncio, "sleep", new=AsyncMock()),
            patch.object(dsize, "log"),
        ):
            await dsize.use_scalpel(interaction)
            modal = interaction.response.modal
            modal.target_user.component._values = [target]
            await modal.on_submit(interaction)

        self.assertEqual(data[(123, 42, "last_dsize_size")], 13)
        self.assertEqual(data[(123, 42, "dsize_history")][-1]["size"], 13)
        self.assertEqual(interaction.edits[-1]["embed"]["fields"][0]["name"], "13 cm")


if __name__ == "__main__":
    unittest.main()
