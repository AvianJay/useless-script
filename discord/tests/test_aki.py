import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import rynaki


def load_aki_for_test():
    globalenv_stub = ModuleType("globalenv")
    globalenv_stub.bot = SimpleNamespace(add_cog=AsyncMock())
    i18n_stub = ModuleType("i18n")
    i18n_stub.I18nView = discord.ui.View
    i18n_stub.K = lambda key: key
    i18n_stub.t = lambda key, **kwargs: key

    module_name = "aki_under_test"
    path = Path(__file__).resolve().parents[1] / "aki.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        "globalenv": globalenv_stub, "i18n": i18n_stub, module_name: module,
    }):
        spec.loader.exec_module(module)
    return module


aki = load_aki_for_test()


class AkinatorCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        aki.in_game_sessions.clear()
        aki.last_game_time.clear()
        self.interaction = SimpleNamespace(
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_start_failure_releases_session_and_stops_view(self):
        game = SimpleNamespace(
            start=AsyncMock(side_effect=rynaki.AkinatorError("Invalid game")),
            view=SimpleNamespace(stop=MagicMock()),
        )
        with patch.object(aki, "AkinatorGame", return_value=game):
            await aki.Aki.aki_command.callback(None, self.interaction)

        self.assertNotIn(123, aki.in_game_sessions)
        game.view.stop.assert_called_once_with()
        self.interaction.followup.send.assert_awaited_once_with(
            "aki.err.service_unavailable", ephemeral=True,
        )

    async def test_failed_error_message_still_releases_session(self):
        game = SimpleNamespace(
            start=AsyncMock(side_effect=rynaki.AkinatorError("Invalid game")),
            view=SimpleNamespace(stop=MagicMock()),
        )
        self.interaction.followup.send.side_effect = RuntimeError("Message unavailable")
        with patch.object(aki, "AkinatorGame", return_value=game):
            with self.assertRaises(RuntimeError):
                await aki.Aki.aki_command.callback(None, self.interaction)

        self.assertNotIn(123, aki.in_game_sessions)
        game.view.stop.assert_called_once_with()

    async def test_service_error_after_answer_ends_the_game(self):
        game = SimpleNamespace(
            interaction=self.interaction,
            post_answer=AsyncMock(side_effect=rynaki.AkinatorError("Invalid response")),
            message=SimpleNamespace(edit=AsyncMock()),
        )
        view = aki.AkinatorView(game)
        aki.in_game_sessions[123] = True

        await view.handle_answer(self.interaction, "y")

        self.assertNotIn(123, aki.in_game_sessions)
        self.assertIn(123, aki.last_game_time)
        self.assertTrue(view.is_finished())
        game.message.edit.assert_awaited_once_with(content="aki.value.game_ended_error", view=None)
        self.interaction.followup.send.assert_awaited_once_with("aki.err.service_unavailable", ephemeral=True)


if __name__ == "__main__":
    unittest.main()
