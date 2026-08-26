import sys
import unittest
from pathlib import Path


DISCORD_DIR = Path(__file__).resolve().parents[1]
if str(DISCORD_DIR) not in sys.path:
    sys.path.insert(0, str(DISCORD_DIR))

from UtilCommands import parse_changelog


class JapaneseChangelogTests(unittest.TestCase):
    def test_japanese_changelog_has_exact_recent_version_window(self):
        versions = parse_changelog(locale="ja")
        self.assertEqual(len(versions), 10)
        self.assertEqual(versions[0]["version"], "0.24.0")
        self.assertEqual(versions[-1]["version"], "0.21.9")


if __name__ == "__main__":
    unittest.main()
