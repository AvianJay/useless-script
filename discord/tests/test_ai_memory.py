import json
import sys
import unittest
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AICommands, ConversationManager


class AIMemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.guilds = {
            100: SimpleNamespace(id=100, name="Current Guild"),
            200: SimpleNamespace(id=200, name="Other Guild"),
        }
        self.cog = AICommands(
            SimpleNamespace(get_guild=lambda guild_id: self.guilds.get(guild_id))
        )
        self.user = SimpleNamespace(id=42)

    def test_memory_serialization_keeps_full_configured_content(self):
        content = "偏好" * 700

        serialized = self.cog._serialize_ai_memory_entry(
            {"memory_id": "pref", "content": content},
            "user_global",
        )

        self.assertEqual(serialized["content"], content)
        self.assertNotIn("truncated", serialized["content"])

    def test_memory_tool_budget_keeps_eight_full_entries_structured(self):
        payload = {
            "entries": [
                {"memory_id": str(index), "content": "x" * 2000}
                for index in range(8)
            ]
        }

        shrunk = self.cog._shrink_tool_data(
            payload,
            max_len=self.cog._tool_result_max_length("get_ai_memory"),
        )

        self.assertIs(shrunk, payload)

    def test_profile_context_includes_full_memory_and_ranks_relevant_old_entry(self):
        entries = [
            {
                "memory_id": f"recent-{index}",
                "content": f"一般偏好 {index}",
                "updated_at": f"2026-08-{index + 1:02d}T00:00:00+08:00",
            }
            for index in range(12)
        ]
        full_content = "鳳梨披薩" + ("很喜歡" * 500)
        entries.append(
            {
                "memory_id": "relevant-old",
                "content": full_content,
                "updated_at": "2020-01-01T00:00:00+08:00",
            }
        )

        with patch.object(
            self.cog,
            "_get_ai_memory_entries",
            side_effect=lambda scope, _: (entries, None) if scope == "user_global" else ([], None),
        ):
            context = self.cog._build_ai_profile_context(
                {"user": self.user, "request_text": "鳳梨披薩"}
            )

        self.assertIn("id=relevant-old", context)
        self.assertIn(full_content, context)
        self.assertNotIn("...[truncated]", context)

    async def test_regular_member_creates_members_writable_guild_memory(self):
        guild = SimpleNamespace(id=100, owner_id=1)
        user = SimpleNamespace(
            id=42,
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
        )

        with patch.object(self.cog, "_get_ai_memory_entries", return_value=([], None)), patch.object(
            self.cog, "_set_ai_memory_entries", return_value=None
        ):
            result = await self.cog._tool_upsert_ai_memory(
                {"scope": "guild_shared", "content": "server preference"},
                {"user": user, "guild": guild},
            )

        self.assertEqual(result["action"], "created")
        self.assertEqual(result["entry"]["write_access"], "members")

    def test_legacy_guild_memory_defaults_to_members_access(self):
        self.assertEqual(self.cog._get_ai_memory_write_access({}), "members")

    async def test_regular_member_cannot_create_or_take_admin_access(self):
        guild = SimpleNamespace(id=100, owner_id=1)
        user = SimpleNamespace(
            id=42,
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
        )

        result = await self.cog._tool_upsert_ai_memory(
            {"scope": "guild_shared", "content": "locked", "write_access": "admins"},
            {"user": user, "guild": guild},
        )

        self.assertIn("regular members", result["error"])

    async def test_admin_may_reclassify_guild_memory(self):
        guild = SimpleNamespace(id=100, owner_id=1)
        admin = SimpleNamespace(
            id=2,
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=True),
        )
        entry = {"memory_id": "shared", "content": "old", "write_access": "members"}
        with patch.object(self.cog, "_get_ai_memory_entries", return_value=([entry], None)), patch.object(
            self.cog, "_set_ai_memory_entries", return_value=None
        ) as setter:
            result = await self.cog._tool_upsert_ai_memory(
                {
                    "scope": "guild_shared",
                    "memory_id": "shared",
                    "content": "updated",
                    "write_access": "admins",
                },
                {"user": admin, "guild": guild},
            )

        self.assertEqual(result["entry"]["write_access"], "admins")
        self.assertEqual(setter.call_args.args[2][0]["write_access"], "admins")

    async def test_regular_member_cannot_update_or_delete_admin_memory(self):
        guild = SimpleNamespace(id=100, owner_id=1)
        user = SimpleNamespace(
            id=42,
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
        )
        entry = {"memory_id": "locked", "content": "old", "write_access": "admins"}
        with patch.object(self.cog, "_get_ai_memory_entries", return_value=([entry], None)):
            update_result = await self.cog._tool_upsert_ai_memory(
                {"scope": "guild_shared", "memory_id": "locked", "content": "new"},
                {"user": user, "guild": guild},
            )
            delete_result = await self.cog._tool_delete_ai_memory(
                {"scope": "guild_shared", "memory_id": "locked"},
                {"user": user, "guild": guild},
            )

        self.assertIn("guild manager", update_result["error"])
        self.assertIn("guild manager", delete_result["error"])

    async def test_user_global_rejects_memory_about_another_user(self):
        result = await self.cog._tool_upsert_ai_memory(
            {
                "scope": "user_global",
                "content": "other user's preference",
                "subject_user_id": 99,
            },
            {"user": self.user, "guild": None},
        )

        self.assertIn("current user", result["error"])

    async def test_oversized_memory_is_rejected_instead_of_silently_truncated(self):
        result = await self.cog._tool_upsert_ai_memory(
            {
                "scope": "user_global",
                "content": "x" * (self.cog.MAX_AI_MEMORY_CONTENT_LENGTH + 1),
            },
            {"user": self.user, "guild": None},
        )

        self.assertIn("exceeds", result["error"])
        self.assertIn("instead of silently truncating", result["error"])

    async def test_cross_server_context_filters_to_current_users_requested_scope(self):
        contexts = [
            {
                "guild_id": 0,
                "history": [{"role": "user", "content": "global note", "timestamp": 1}],
            },
            {
                "guild_id": 100,
                "history": [{"role": "user", "content": "current note", "timestamp": 2}],
            },
            {
                "guild_id": 200,
                "history": [{"role": "assistant", "content": "other guild note", "timestamp": 3}],
            },
        ]

        with patch.object(
            self.cog,
            "_get_stored_user_ai_contexts",
            return_value=(contexts, None),
        ):
            result = await self.cog._tool_get_user_context(
                {"scope": "other_guilds", "query": "other guild"},
                {"user": self.user, "guild": self.guilds[100]},
            )

        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["entries"][0]["guild_id"], 200)
        self.assertEqual(result["entries"][0]["guild_name"], "Other Guild")
        self.assertEqual(result["entries"][0]["content"], "other guild note")

    def test_database_context_loader_reads_only_current_user_conversation_keys(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchall.return_value = [
            (0, "ai_conversation_dm_42", json.dumps([{"role": "user", "content": "dm"}])),
            (200, "ai_conversation_200_42", json.dumps([{"role": "assistant", "content": "guild"}])),
            (200, "ai_conversation_200_99", json.dumps([{"role": "user", "content": "wrong user"}])),
        ]

        with patch("ai.get_db_connection", return_value=connection):
            contexts, error = self.cog._get_stored_user_ai_contexts({"user": self.user})

        self.assertIsNone(error)
        self.assertEqual([context["guild_id"] for context in contexts], [0, 200])
        connection.execute.assert_called_once()
        self.assertEqual(connection.execute.call_args.args[1], (42,))

    def test_personal_memory_display_is_owner_scoped_and_attaches_full_overflow(self):
        entries = [{"memory_id": "mine", "title": "Title", "content": "x" * 2500}]
        with patch("ai.get_user_data", return_value=entries) as getter:
            message, attachment, count = self.cog._build_personal_memory_display(42)

        self.assertEqual(count, 1)
        self.assertIsNotNone(attachment)
        self.assertEqual(attachment.filename, "ai_memory.txt")
        self.assertIn("1", message)
        getter.assert_called_with(0, 42, self.cog.AI_USER_GLOBAL_MEMORY_KEY, [])

    def test_personal_memory_search_filters_results(self):
        entries = [
            {"memory_id": "cat", "title": "Cats", "content": "likes cats"},
            {"memory_id": "dog", "title": "Dogs", "content": "likes dogs"},
        ]
        with patch("ai.get_user_data", return_value=entries):
            message, attachment, count = self.cog._build_personal_memory_display(42, "cats")

        self.assertEqual(count, 1)
        self.assertIsNone(attachment)
        self.assertIn("cat", message)
        self.assertNotIn("dog", message)

    def test_auto_summary_prepare_merges_existing_id_and_never_deletes(self):
        memories = [
            {"memory_id": "project", "title": "Project", "content": "old"},
            {"memory_id": "keep", "title": "Keep", "content": "unchanged"},
        ]
        prepared, count = self.cog._prepare_auto_summary_memories(
            42,
            memories,
            [{"memory_id": "project", "title": "Project", "content": "old plus new", "tags": []}],
        )

        self.assertEqual(count, 1)
        self.assertEqual(len(prepared), 2)
        self.assertEqual(next(item for item in prepared if item["memory_id"] == "project")["content"], "old plus new")
        self.assertTrue(any(item["memory_id"] == "keep" for item in prepared))

    async def test_auto_summary_zero_notes_charges_and_clears_history(self):
        history = [
            {"role": "user", "content": "hello", "timestamp": time.time() - 1900},
            {"role": "assistant", "content": "hi", "timestamp": time.time() - 1900},
        ]
        config = {"enabled": True, "due_at": time.time() - 10, "last_activity_at": time.time() - 1900}
        with patch.object(self.cog, "_get_auto_summary_config", side_effect=lambda *_: dict(config)), patch.object(
            self.cog, "_set_auto_summary_config", return_value=True
        ), patch.object(self.cog, "_get_personal_ai_memory_entries", return_value=[]), patch.object(
            self.cog, "_resolve_auto_summary_billing_target", new=AsyncMock(return_value={"payer_id": 42, "payer_user": self.user})
        ), patch.object(self.cog, "_charge_auto_summary", new=AsyncMock(return_value=(20.0, 100.0, 80.0))), patch.object(
            self.cog, "_request_auto_summary_actions", new=AsyncMock(return_value=[])
        ), patch.object(self.cog, "_get_default_model", new=AsyncMock(return_value="openai-fast")), patch.object(
            self.cog, "_log_economy_transaction"
        ), patch.object(self.cog, "_queue_economy_audit_log"), patch.object(
            self.cog, "_set_auto_summary_result"
        ), patch.object(ConversationManager, "get_history", return_value=history), patch.object(
            ConversationManager, "clear_history", return_value=True
        ) as clear_history:
            result = await self.cog._run_auto_summary(42, None, expected_due_at=config["due_at"])

        self.assertEqual(result, "success_no_memory")
        clear_history.assert_called_once_with(42, None)

    async def test_auto_summary_model_failure_refunds_and_keeps_history(self):
        history = [
            {"role": "user", "content": "hello", "timestamp": time.time() - 1900},
            {"role": "assistant", "content": "hi", "timestamp": time.time() - 1900},
        ]
        config = {"enabled": True, "due_at": time.time() - 10, "last_activity_at": time.time() - 1900}
        with patch.object(self.cog, "_get_auto_summary_config", side_effect=lambda *_: dict(config)), patch.object(
            self.cog, "_set_auto_summary_config", return_value=True
        ), patch.object(self.cog, "_get_personal_ai_memory_entries", return_value=[]), patch.object(
            self.cog, "_resolve_auto_summary_billing_target", new=AsyncMock(return_value={"payer_id": 42, "payer_user": self.user})
        ), patch.object(self.cog, "_charge_auto_summary", new=AsyncMock(return_value=(20.0, 100.0, 80.0))), patch.object(
            self.cog, "_request_auto_summary_actions", new=AsyncMock(side_effect=RuntimeError("boom"))
        ), patch.object(self.cog, "_get_default_model", new=AsyncMock(return_value="openai-fast")), patch.object(
            self.cog, "_refund_auto_summary", new=AsyncMock()
        ) as refund, patch.object(self.cog, "_log_economy_transaction"), patch.object(
            self.cog, "_queue_economy_audit_log"
        ), patch.object(self.cog, "_set_auto_summary_result"), patch.object(
            ConversationManager, "get_history", return_value=history
        ), patch.object(ConversationManager, "clear_history", return_value=True) as clear_history:
            result = await self.cog._run_auto_summary(42, None, expected_due_at=config["due_at"])

        self.assertEqual(result, "failed_refunded")
        refund.assert_awaited_once()
        clear_history.assert_not_called()

    async def test_auto_summary_insufficient_balance_does_not_call_model_or_clear(self):
        history = [
            {"role": "user", "content": "hello", "timestamp": time.time() - 1900},
            {"role": "assistant", "content": "hi", "timestamp": time.time() - 1900},
        ]
        config = {"enabled": True, "due_at": time.time() - 10, "last_activity_at": time.time() - 1900}
        with patch.object(self.cog, "_get_auto_summary_config", side_effect=lambda *_: dict(config)), patch.object(
            self.cog, "_set_auto_summary_config", return_value=True
        ), patch.object(self.cog, "_get_personal_ai_memory_entries", return_value=[]), patch.object(
            self.cog, "_resolve_auto_summary_billing_target", new=AsyncMock(return_value={"payer_id": 99, "payer_user": None})
        ), patch.object(self.cog, "_charge_auto_summary", new=AsyncMock(return_value=(0.0, 10.0, 10.0))), patch.object(
            self.cog, "_request_auto_summary_actions", new=AsyncMock()
        ) as request, patch.object(self.cog, "_get_default_model", new=AsyncMock(return_value="openai-fast")), patch.object(
            self.cog, "_set_auto_summary_result"
        ), patch.object(ConversationManager, "get_history", return_value=history), patch.object(
            ConversationManager, "clear_history", return_value=True
        ) as clear_history:
            result = await self.cog._run_auto_summary(42, 100, expected_due_at=config["due_at"])

        self.assertEqual(result, "insufficient_balance")
        request.assert_not_awaited()
        clear_history.assert_not_called()

    async def test_auto_summary_new_request_version_refunds_stale_work(self):
        history = [
            {"role": "user", "content": "hello", "timestamp": time.time() - 1900},
            {"role": "assistant", "content": "hi", "timestamp": time.time() - 1900},
        ]
        config = {"enabled": True, "due_at": time.time() - 10, "last_activity_at": time.time() - 1900}

        async def mark_new_request(**_kwargs):
            self.cog._begin_ai_request(42, None)
            return []

        with patch.object(self.cog, "_get_auto_summary_config", side_effect=lambda *_: dict(config)), patch.object(
            self.cog, "_set_auto_summary_config", return_value=True
        ), patch.object(self.cog, "_get_personal_ai_memory_entries", return_value=[]), patch.object(
            self.cog, "_resolve_auto_summary_billing_target", new=AsyncMock(return_value={"payer_id": 42, "payer_user": self.user})
        ), patch.object(self.cog, "_charge_auto_summary", new=AsyncMock(return_value=(20.0, 100.0, 80.0))), patch.object(
            self.cog, "_request_auto_summary_actions", new=AsyncMock(side_effect=mark_new_request)
        ), patch.object(self.cog, "_get_default_model", new=AsyncMock(return_value="openai-fast")), patch.object(
            self.cog, "_refund_auto_summary", new=AsyncMock()
        ) as refund, patch.object(self.cog, "_log_economy_transaction"), patch.object(
            self.cog, "_queue_economy_audit_log"
        ), patch.object(self.cog, "_set_auto_summary_result"), patch.object(
            ConversationManager, "get_history", return_value=history
        ), patch.object(ConversationManager, "clear_history", return_value=True) as clear_history:
            result = await self.cog._run_auto_summary(42, None, expected_due_at=config["due_at"])

        self.assertEqual(result, "stale_refunded")
        refund.assert_awaited_once()
        clear_history.assert_not_called()

    def test_auto_summary_configuration_isolated_by_context(self):
        stored = {}

        def fake_get(scope_id, user_id, key, default):
            return stored.get((scope_id, user_id, key), default)

        def fake_set(scope_id, user_id, key, value):
            stored[(scope_id, user_id, key)] = value
            return True

        with patch("ai.get_user_data", side_effect=fake_get), patch("ai.set_user_data", side_effect=fake_set), patch.object(
            ConversationManager, "get_history", return_value=[]
        ):
            self.cog._set_auto_summary_enabled(42, 100, True)
            guild_config = self.cog._get_auto_summary_config(42, 100)
            dm_config = self.cog._get_auto_summary_config(42, None)

        self.assertTrue(guild_config["enabled"])
        self.assertFalse(dm_config["enabled"])

    def test_ai_memory_command_shape_has_no_cross_user_or_guild_options(self):
        commands_by_name = {command.name: command for command in AICommands.ai_memory.commands}
        self.assertEqual(set(commands_by_name), {"view", "delete", "auto-summary"})
        self.assertEqual({parameter.name for parameter in commands_by_name["view"].parameters}, {"query"})
        self.assertEqual({parameter.name for parameter in commands_by_name["delete"].parameters}, {"memory_id"})
        self.assertNotIn("user", {parameter.name for command in commands_by_name.values() for parameter in command.parameters})
        self.assertNotIn("scope", {parameter.name for command in commands_by_name.values() for parameter in command.parameters})

        payload = AICommands.ai_memory.to_dict(self.cog.bot.tree if hasattr(self.cog.bot, "tree") else MagicMock())
        payload_by_name = {option["name"]: option for option in payload["options"]}
        self.assertEqual(payload_by_name["delete"]["options"][0]["name"], "memory_id")
        self.assertTrue(payload_by_name["delete"]["options"][0]["autocomplete"])
        self.assertEqual(payload_by_name["auto-summary"]["options"][0]["type"], 5)


if __name__ == "__main__":
    unittest.main()
