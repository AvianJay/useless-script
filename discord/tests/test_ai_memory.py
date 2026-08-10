import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AICommands


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

    async def test_guild_shared_write_requires_manager_permission(self):
        guild = SimpleNamespace(id=100, owner_id=1)
        user = SimpleNamespace(
            id=42,
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
        )

        result = await self.cog._tool_upsert_ai_memory(
            {"scope": "guild_shared", "content": "server preference"},
            {"user": user, "guild": guild},
        )

        self.assertIn("guild manager", result["error"])

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


if __name__ == "__main__":
    unittest.main()
