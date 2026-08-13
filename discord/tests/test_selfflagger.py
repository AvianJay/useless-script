import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SELFFLAGGER_DIR = Path(__file__).resolve().parents[1] / "selfflagger"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


channel_selection = load_module(
    "selfflagger_channel_selection",
    SELFFLAGGER_DIR / "channel_selection.py",
)
database = load_module(
    "selfflagger_database",
    SELFFLAGGER_DIR / "database.py",
)


class FakeChannel:
    def __init__(self, channel_id, position, visible_member_ids):
        self.id = channel_id
        self.position = position
        self.visible_member_ids = set(visible_member_ids)

    def permissions_for(self, member):
        return SimpleNamespace(view_channel=member.id in self.visible_member_ids)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "flagged.db"
        self.db_patch = patch.object(database, "DB_PATH", str(self.db_path))
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_new_database_stores_is_admin(self):
        database.init_db()
        with closing(database.get_db_connection()) as conn:
            database.add_flagged_user(conn, 1, 2, True, True)
            record = database.get_flagged_user(conn, 1, 2)[0]

        self.assertEqual(record[2:], (1, 1))

    def test_existing_database_is_migrated_without_losing_rows(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript("""
                CREATE TABLE guilds (
                    id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    UNIQUE(id)
                );
                CREATE TABLE flagged_users (
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER,
                    flagged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    flagged_role BOOLEAN DEFAULT 0,
                    UNIQUE(user_id, guild_id)
                );
                INSERT INTO flagged_users (user_id, guild_id, flagged_role)
                VALUES (10, 20, 1);
            """)
            conn.commit()

        database.init_db()

        with closing(database.get_db_connection()) as conn:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(flagged_users)")
            }
            record = database.get_flagged_user(conn, 10, 20)[0]
        self.assertIn("is_admin", columns)
        self.assertEqual(record[2:], (1, 0))


class ChannelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.self_member = SimpleNamespace(
            id=1,
            guild_permissions=SimpleNamespace(administrator=True),
        )
        self.members = [
            self.self_member,
            SimpleNamespace(id=2),
            SimpleNamespace(id=3),
            SimpleNamespace(id=4),
        ]

    def test_selects_channel_visible_to_most_cached_members(self):
        smaller = FakeChannel(100, 0, {1, 2})
        larger = FakeChannel(200, 1, {1, 2, 3, 4})
        guild = SimpleNamespace(
            me=self.self_member,
            members=self.members,
            text_channels=[smaller, larger],
        )

        channel, visible_count, member_count = (
            channel_selection.select_most_visible_channel(guild)
        )

        self.assertIs(channel, larger)
        self.assertEqual((visible_count, member_count), (4, 4))

    def test_excludes_channels_not_visible_to_self(self):
        hidden = FakeChannel(100, 0, {2, 3, 4})
        visible = FakeChannel(200, 1, {1, 2})
        guild = SimpleNamespace(
            me=self.self_member,
            members=self.members,
            text_channels=[hidden, visible],
        )

        result = channel_selection.select_most_visible_channel(guild)

        self.assertIs(result[0], visible)

    def test_ties_use_position_then_channel_id(self):
        later = FakeChannel(100, 2, {1, 2})
        higher_id = FakeChannel(300, 1, {1, 2})
        lower_id = FakeChannel(200, 1, {1, 2})
        guild = SimpleNamespace(
            me=self.self_member,
            members=self.members,
            text_channels=[later, higher_id, lower_id],
        )

        result = channel_selection.select_most_visible_channel(guild)

        self.assertIs(result[0], lower_id)

    def test_no_self_member_or_visible_channel_returns_none(self):
        no_self = SimpleNamespace(
            me=None,
            members=self.members,
            text_channels=[],
        )
        hidden = SimpleNamespace(
            me=self.self_member,
            members=self.members,
            text_channels=[FakeChannel(100, 0, {2, 3})],
        )

        self.assertIsNone(channel_selection.select_most_visible_channel(no_self))
        self.assertIsNone(channel_selection.select_most_visible_channel(hidden))

    def test_member_is_admin_defaults_to_false(self):
        self.assertTrue(channel_selection.member_is_admin(self.self_member))
        self.assertFalse(channel_selection.member_is_admin(SimpleNamespace()))


if __name__ == "__main__":
    unittest.main()
