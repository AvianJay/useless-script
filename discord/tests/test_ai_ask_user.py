import asyncio
import json
import sys
import time
import unittest
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AICommands


class AIAskUserTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())
        self.message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
        self.sent = asyncio.Event()
        self.view = None

        async def send(**kwargs):
            self.view = kwargs["view"]
            self.sent.set()
            return self.message

        self.channel = SimpleNamespace(send=AsyncMock(side_effect=send))
        self.context = {"user": SimpleNamespace(id=42), "channel": self.channel}

    def interaction(self, user_id=42):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            response=SimpleNamespace(
                send_message=AsyncMock(), send_modal=AsyncMock(), defer=AsyncMock(),
            ),
        )

    async def start_tool(self, **args):
        task = asyncio.create_task(self.cog._tool_ask_user({"question": "Which color?", **args}, self.context))

        async def cleanup():
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(cleanup)
        await asyncio.wait_for(self.sent.wait(), timeout=2)
        return task

    def assert_closed(self, status):
        self.assertEqual(self.view.result["status"], status)
        self.assertTrue(self.view.is_finished())
        self.assertTrue(all(item.disabled for item in self.view.walk_children() if isinstance(item, discord.ui.Button)))
        self.message.delete.assert_awaited_once_with()
        self.message.edit.assert_not_awaited()
        self.assertIsNone(self.view.bound_message)

    async def test_choice_returns_to_the_active_request(self):
        task = await self.start_tool(choices=["Red", "Blue"])
        self.assertIn("<@42>", json.dumps(self.view.to_components()))
        mentions = self.channel.send.await_args.kwargs["allowed_mentions"]
        self.assertEqual([user.id for user in mentions.users], [42])
        self.assertFalse(mentions.roles)
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.replied_user)
        buttons = [item for item in self.view.walk_children() if isinstance(item, discord.ui.Button)]
        interaction = self.interaction()
        await buttons[1].callback(interaction)

        self.assertEqual(await asyncio.wait_for(task, timeout=2), {
            "status": "answered", "answer": "Blue", "timeout_seconds": 120,
        })
        interaction.response.defer.assert_awaited_once()
        self.assert_closed("answered")

    async def test_free_text_modal_returns_trimmed_answer(self):
        task = await self.start_tool()
        interaction = self.interaction()
        await self.view.reply_callback(interaction)
        modal = interaction.response.send_modal.await_args.args[0]

        modal.answer._value = "  My own answer  "
        await modal.on_submit(self.interaction())

        result = await asyncio.wait_for(task, timeout=2)
        self.assertEqual(result["answer"], "My own answer")
        self.assertTrue(modal.is_finished())
        self.assert_closed("answered")

    async def test_other_users_and_blank_answers_do_not_resolve_question(self):
        task = await self.start_tool()
        other = self.interaction(7)
        await self.view.submit_answer(other, "Red")
        other.response.send_message.assert_awaited_once()
        self.assertTrue(other.response.send_message.await_args.kwargs["ephemeral"])
        empty = self.interaction()
        await self.view.submit_answer(empty, "  ")
        empty.response.send_message.assert_awaited_once()
        self.assertIsNone(self.view.result)
        self.assertFalse(task.done())
        await self.view.submit_answer(self.interaction(), "Blue")
        await asyncio.wait_for(task, timeout=2)

    async def test_timeout_closes_open_modal_and_returns_no_answer(self):
        task = await self.start_tool(timeout_seconds=1)
        interaction = self.interaction()
        await self.view.reply_callback(interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        # Even if Discord refreshes the view's idle timeout, the tool's deadline stays fixed.
        self.view.timeout = 300

        self.assertEqual(await asyncio.wait_for(task, timeout=3), {
            "status": "timed_out", "answer": None, "timeout_seconds": 1,
        })
        self.assertTrue(modal.is_finished())
        self.assert_closed("timed_out")
        late = self.interaction()
        modal.answer._value = "Too late"
        await modal.on_submit(late)
        late.response.send_message.assert_awaited_once()
        self.assertIsNone(self.view.result["answer"])

    async def test_expired_deadline_rejects_answer_before_timeout_callback_runs(self):
        task = await self.start_tool()
        self.view.deadline = time.monotonic() - 1
        interaction = self.interaction()
        await self.view.submit_answer(interaction, "Too late")

        self.assertEqual((await asyncio.wait_for(task, timeout=2))["status"], "timed_out")
        interaction.response.defer.assert_not_awaited()
        self.assert_closed("timed_out")

    async def test_first_answer_wins(self):
        task = await self.start_tool()
        await self.view.submit_answer(self.interaction(), "First")
        duplicate = self.interaction()
        await self.view.submit_answer(duplicate, "Second")

        self.assertEqual((await asyncio.wait_for(task, timeout=2))["answer"], "First")
        duplicate.response.send_message.assert_awaited_once()

    async def test_cancellation_propagates_and_deletes_question(self):
        task = await self.start_tool()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_closed("cancelled")

    async def test_failed_send_stops_the_unsent_view(self):
        async def failed_send(**kwargs):
            self.view = kwargs["view"]
            raise RuntimeError("send failed")

        self.channel.send.side_effect = failed_send
        with self.assertRaisesRegex(RuntimeError, "send failed"):
            await self.cog._tool_ask_user({"question": "Hello?"}, self.context)
        self.assertTrue(self.view.is_finished())
        self.assertEqual(self.view.result["status"], "cancelled")
        self.message.edit.assert_not_awaited()
        self.message.delete.assert_not_awaited()

    async def test_delete_failure_does_not_discard_answer(self):
        self.message.delete.side_effect = RuntimeError("message already deleted")
        task = await self.start_tool()
        await self.view.submit_answer(self.interaction(), "Blue")
        self.assertEqual((await asyncio.wait_for(task, timeout=2))["answer"], "Blue")

    async def test_presenter_supports_interaction_followups_without_channel_send(self):
        sender = self.channel.send
        self.context["_ask_user_presenter"] = lambda view: sender(view=view)
        self.context.pop("channel")
        task = await self.start_tool(timeout_seconds=9999)
        self.assertEqual(self.view.timeout, 300)
        await self.view.submit_answer(self.interaction(), "Blue")
        self.assertEqual((await asyncio.wait_for(task, timeout=2))["timeout_seconds"], 300)

    async def test_invalid_questions_and_choices_are_rejected_before_sending(self):
        for args in [
            {}, {"question": " "}, {"question": "x" * 1501},
            {"question": "Q?", "choices": "Red"},
            {"question": "Q?", "choices": ["A"] * 5},
            {"question": "Q?", "choices": ["A", " A "]},
            {"question": "Q?", "choices": [""]},
            {"question": "Q?", "choices": [123]},
            {"question": "Q?", "choices": ["x" * 81]},
        ]:
            with self.subTest(args=args):
                self.assertIn("error", await self.cog._tool_ask_user(args, self.context))
        self.channel.send.assert_not_awaited()
        self.assertIn("error", await self.cog._tool_ask_user({"question": "Q?"}, {}))

    async def test_tool_is_exposed_and_dispatched(self):
        tools = {entry["function"]["name"]: entry["function"] for entry in self.cog._build_request_ai_tools(self.context)}
        self.assertIn("ask_user", tools)
        self.assertEqual(tools["ask_user"]["parameters"]["required"], ["question"])
        with patch.object(self.cog, "_tool_ask_user", new=AsyncMock(return_value={"status": "timed_out", "answer": None})) as handler:
            result = await self.cog._execute_ai_tool("ask_user", {"question": "Q?"}, self.context)
        handler.assert_awaited_once_with({"question": "Q?"}, self.context)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["status"], "timed_out")

    async def test_native_and_emulated_loops_continue_after_answer_or_timeout(self):
        for mode, status in product(("native", "emulated"), ("answered", "timed_out")):
            with self.subTest(mode=mode, status=status):
                context = dict(self.context)

                async def present(view):
                    view.finish(status, "Blue" if status == "answered" else None)
                    return self.message

                context["_ask_user_presenter"] = present
                call = {"id": "ask-1", "function": {"name": "ask_user", "arguments": '{"question":"Which color?"}'}}
                first = SimpleNamespace(
                    content="" if mode == "native" else '{"tool_calls":[{"name":"ask_user","arguments":{"question":"Which color?"}}]}',
                    tool_calls=[call] if mode == "native" else None,
                )
                final_text = "You chose Blue." if status == "answered" else "No answer was provided."
                final = SimpleNamespace(content=final_text, tool_calls=None)
                responses = [
                    (SimpleNamespace(model="test", choices=[SimpleNamespace(message=message)]), mode)
                    for message in (first, final)
                ]
                with patch.object(self.cog, "_request_ai_completion", new=AsyncMock(side_effect=responses)) as request:
                    answer, _model, _elapsed, _reasoning = await self.cog.generate_response(
                        [{"role": "user", "content": "Help me pick a color"}], model="test", tool_context=context,
                    )
                self.assertEqual(answer, final_text)
                messages = request.await_args_list[1].args[0]
                payload = json.loads(messages[-1]["content"].removeprefix("Tool results:\n"))
                data = payload["data"] if mode == "native" else payload["tool_results"][0]["result"]["data"]
                self.assertEqual(data["status"], status)
                self.assertEqual(data["answer"], "Blue" if status == "answered" else None)
                self.assertNotIn("_ask_user_presenter", context)


if __name__ == "__main__":
    unittest.main()
