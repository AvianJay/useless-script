import sys
import unittest
from pathlib import Path


DISCORD_DIR = Path(__file__).resolve().parents[1]
if str(DISCORD_DIR) not in sys.path:
    sys.path.insert(0, str(DISCORD_DIR))

from UtilCommands import parse_changelog


class ChangelogLocaleSyncTests(unittest.TestCase):
    def test_all_changelogs_start_with_current_version(self):
        for locale in ("zh-TW", "en", "ja"):
            with self.subTest(locale=locale):
                versions = parse_changelog(locale=locale)
                self.assertTrue(versions)
                self.assertEqual(versions[0]["version"], "0.24.2")


if __name__ == "__main__":
    unittest.main()
