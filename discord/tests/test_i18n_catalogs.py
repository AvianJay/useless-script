import sys
import unittest
from pathlib import Path

DISCORD_DIR = Path(__file__).resolve().parents[1]
if str(DISCORD_DIR) not in sys.path:
    sys.path.insert(0, str(DISCORD_DIR))

from tools.i18n import lint


class CatalogLintTests(unittest.TestCase):
    def test_lint_no_errors(self):
        report = lint.run(quiet=True)
        self.assertEqual(report.errors, [],
                         "i18n lint errors:\n" + "\n".join(report.errors))


if __name__ == "__main__":
    unittest.main()
