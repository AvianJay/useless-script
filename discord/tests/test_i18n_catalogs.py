import json
import sys
import unittest
from pathlib import Path

DISCORD_DIR = Path(__file__).resolve().parents[1]
if str(DISCORD_DIR) not in sys.path:
    sys.path.insert(0, str(DISCORD_DIR))

from tools.i18n import lint


LOCALES_DIR = DISCORD_DIR / "locales"
ALIASES = {"commands": "cmd", "errors": "err"}
PLURAL_KEYS = {"zero", "one", "two", "few", "many", "other"}


def _flatten_catalog_file(path: Path) -> dict:
    namespace = path.stem
    if namespace.startswith("_"):
        namespace = ALIASES.get(namespace[1:], namespace[1:])
    result = {}

    def walk(node, prefix):
        if isinstance(node, dict) and not (node and set(node) <= PLURAL_KEYS):
            for key, value in node.items():
                walk(value, f"{prefix}.{key}" if prefix else str(key))
        else:
            result[prefix] = node

    walk(json.loads(path.read_text(encoding="utf-8-sig")), namespace)
    return result


def _load_locale(locale: str) -> tuple[set[str], dict]:
    files = set()
    catalog = {}
    for path in (LOCALES_DIR / locale).glob("*.json"):
        files.add(path.name)
        catalog.update(_flatten_catalog_file(path))
    return files, catalog


class CatalogLintTests(unittest.TestCase):
    def test_lint_no_errors(self):
        report = lint.run(quiet=True)
        self.assertEqual(report.errors, [],
                         "i18n lint errors:\n" + "\n".join(report.errors))

    def test_japanese_catalog_covers_source_and_english_union(self):
        zh_files, zh = _load_locale("zh-TW")
        en_files, en = _load_locale("en")
        ja_files, ja = _load_locale("ja")
        self.assertEqual(ja_files, zh_files | en_files)
        self.assertEqual(len(ja_files), 50)
        expected_keys = set(zh) | set(en)
        self.assertTrue(expected_keys <= set(ja))
        self.assertEqual(len(ja), 5741)

    def test_japanese_catalog_contains_no_null_values(self):
        _files, ja = _load_locale("ja")
        null_keys = [key for key, value in ja.items() if value is None]
        self.assertEqual(null_keys, [])

    def test_japanese_coverage_ratchet_is_zero(self):
        coverage = json.loads(
            (LOCALES_DIR / ".coverage.json").read_text(encoding="utf-8"))
        self.assertEqual(coverage.get("ja"), 0)


if __name__ == "__main__":
    unittest.main()
