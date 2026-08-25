"""i18n 語言檔 linter（CI 閘門，也由 tests/test_i18n_catalogs.py 執行）。

    python -m tools.i18n.lint                 # 全部檢查
    python -m tools.i18n.lint --update-ratchet   # 重設 untranslated ratchet
    python -m tools.i18n.lint --update-snapshot  # 重建 DSL 快照

規則（error 會使 exit code 非 0；warning 只列出）：
- positional-placeholder (error)：語言檔值含 {} / {0} 位置參數（譯者會重排，禁用）
- placeholders (error)：同一 key 的 {name} 集合在不同語言不一致（count 除外）
- plural-other (error)：複數項缺少必填的 "other"
- plural-en-one (warning)：en 複數項缺 "one"
- plural-needed (warning)：zh 值為「{x} + 量詞」但 en 值是純字串（幾乎必然漏了英文複數）
- untranslated (ratchet)：source 有、目標語言缺/null 的 key 數不得增加（locales/.coverage.json）
- cmd-name (error)：cmd.*.name 的值必須是合法 Discord 指令名稱（含小寫檢查）
- hardcoded (error)：locales/.migrated 內的模組不得再有中文字面值（docstring 與 # i18n: skip 除外）
- dsl-frozen (error)：受保護的 DSL 字彙不得改動（tools/i18n/dsl_snapshot.json）
- unused (warning)：語言檔 key 沒有被任何程式碼字面值引用
- manifest-parity (error)：docs/<locale>/manifest.json 的 section id 集合必須一致
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import i18n  # noqa: E402

MIGRATED_FILE = ROOT / "locales" / ".migrated"
RATCHET_FILE = ROOT / "locales" / ".coverage.json"
SNAPSHOT_FILE = Path(__file__).parent / "dsl_snapshot.json"

_CJK_RE = re.compile(r"[一-鿿]")
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
_MEASURE_WORD_RE = re.compile(r"\{\w+\}\s*(個|次|位|天|秒|則|名|張|場|人|筆|條)")
_PLURAL_KEYS = {"zero", "one", "two", "few", "many", "other"}


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, rule: str, message: str):
        self.errors.append(f"[{rule}] {message}")

    def warning(self, rule: str, message: str):
        self.warnings.append(f"[{rule}] {message}")


def _placeholder_names(text: str, report: Report, where: str) -> set[str]:
    names = set()
    for match in _PLACEHOLDER_RE.finditer(text):
        token = match.group(1)
        if token == "" or token.isdigit():
            report.error("positional-placeholder", f"{where}: {{{token}}}")
            continue
        names.add(token.split(":")[0].split("!")[0])
    return names


def _iter_values(value):
    """str 或複數 dict -> [(variant, str)]"""
    if isinstance(value, str):
        yield None, value
    elif isinstance(value, dict):
        for variant, text in value.items():
            if isinstance(text, str):
                yield variant, text


def check_catalogs(report: Report) -> dict[str, int]:
    """回傳 {locale: untranslated 數}（相對 source）。"""
    i18n.reload_catalogs()
    catalogs = i18n._catalogs
    source = catalogs.get(i18n.SOURCE_LOCALE, {})
    untranslated: dict[str, int] = {}

    # 每個 key 的 source placeholder 集合
    source_placeholders: dict[str, set[str]] = {}
    for key, value in source.items():
        names = set()
        for variant, text in _iter_values(value):
            where = f"{i18n.SOURCE_LOCALE}:{key}" + (f".{variant}" if variant else "")
            names |= _placeholder_names(text, report, where)
        source_placeholders[key] = names

    for locale, catalog in catalogs.items():
        for key, value in catalog.items():
            # 複數項結構
            if isinstance(value, dict):
                if not set(value.keys()) <= _PLURAL_KEYS:
                    report.error("plural-other",
                                 f"{locale}:{key} has non-plural sub-keys {sorted(value)}")
                elif "other" not in value:
                    report.error("plural-other", f"{locale}:{key} missing required \"other\"")
                if locale.split("-")[0] == "en" and "one" not in value:
                    report.warning("plural-en-one", f"{locale}:{key} has no \"one\" form")

            # 指令名稱合法性（排除 .param./.choice. 底下叫 name 的參數描述，
            # 與 .ctx. 的 context menu 名稱——後者允許空白與大寫）
            if key.startswith("cmd.") and key.endswith(".name") and \
                    isinstance(value, str) and \
                    ".param." not in key and ".choice." not in key and \
                    ".ctx." not in key:
                if not i18n._valid_command_name(value):
                    report.error("cmd-name", f"{locale}:{key} = {value!r}")

            # placeholder 一致性（與 source 比；count 允許只在其中一邊）
            if locale != i18n.SOURCE_LOCALE and key in source_placeholders:
                names = set()
                for variant, text in _iter_values(value):
                    where = f"{locale}:{key}" + (f".{variant}" if variant else "")
                    names |= _placeholder_names(text, report, where)
                expected = source_placeholders[key]
                if (names - {"count"}) != (expected - {"count"}):
                    report.error(
                        "placeholders",
                        f"{locale}:{key} placeholders {sorted(names)} != "
                        f"source {sorted(expected)}")

        # untranslated（只對 source 之外的語言統計）
        if locale != i18n.SOURCE_LOCALE:
            missing = sum(
                1 for key, value in source.items()
                if value is not None and catalog.get(key) is None)
            untranslated[locale] = missing

    # plural-needed：zh 是「{x} 量詞」純字串、en 也是純字串
    en = catalogs.get("en", {})
    for key, value in source.items():
        if isinstance(value, str) and _MEASURE_WORD_RE.search(value):
            en_value = en.get(key)
            if isinstance(en_value, str):
                report.warning("plural-needed",
                               f"{key}: zh has a counted noun but en is not a plural entry")
    return untranslated


def check_ratchet(report: Report, untranslated: dict[str, int], update: bool):
    stored = {}
    if RATCHET_FILE.exists():
        try:
            stored = json.loads(RATCHET_FILE.read_text(encoding="utf-8"))
        except ValueError:
            report.warning("untranslated", f"{RATCHET_FILE} is corrupt; rewriting")
    changed = False
    for locale, count in untranslated.items():
        previous = stored.get(locale)
        if previous is None or count < previous or update:
            stored[locale] = count
            changed = True
        elif count > previous:
            report.error("untranslated",
                         f"{locale}: untranslated keys increased {previous} -> {count} "
                         f"(run with --update-ratchet only if intentional)")
    if changed:
        RATCHET_FILE.write_text(
            json.dumps(stored, indent=4, sort_keys=True) + "\n", encoding="utf-8")


# ============= hardcoded：已遷移模組不得再有中文字面值 =============

def _docstring_nodes(tree: ast.AST) -> set[int]:
    """回傳所有 docstring Constant 節點的 id。"""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                found.add(id(body[0].value))
    return found


def check_hardcoded(report: Report):
    if not MIGRATED_FILE.exists():
        return
    migrated = [line.strip() for line in
                MIGRATED_FILE.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")]
    for module in migrated:
        path = ROOT / f"{module}.py"
        if not path.exists():
            report.warning("hardcoded", f"{module} listed in .migrated but {path.name} missing")
            continue
        source = path.read_text(encoding="utf-8-sig")
        lines = source.splitlines()
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            report.error("hardcoded", f"{path.name}: cannot parse ({e})")
            continue
        # 區域標記：# i18n: skip-start / skip-end 之間整段跳過
        # （擁有者導向的 dev-* 指令等，依約定保留中文）
        skipped_lines = set()
        in_skip = False
        for lineno, line in enumerate(lines, start=1):
            if "# i18n: skip-start" in line:
                in_skip = True
            if in_skip:
                skipped_lines.add(lineno)
            if "# i18n: skip-end" in line:
                in_skip = False
        docstrings = _docstring_nodes(tree)
        # dict 的 key 是查表資料（如中文動作詞 -> en key 的輸入正規化表），
        # 與 extract.py 的 dict_key 規則一致，不視為需要翻譯的字面值
        dict_keys = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key_node in node.keys:
                    if key_node is not None:
                        dict_keys.add(id(key_node))
        # 模組層級 ALL_CAPS 常數是 DSL / 領域資料 / AI prompt（排除清單），
        # 與 extract.py 的 allcaps 規則一致
        allcaps = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        for child in ast.walk(node.value):
                            allcaps.add(id(child))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in docstrings or id(node) in dict_keys or id(node) in allcaps:
                continue
            if not _CJK_RE.search(node.value):
                continue
            if node.lineno in skipped_lines:
                continue
            line = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
            if "# i18n: skip" in line:
                continue
            report.error("hardcoded",
                         f"{path.name}:{node.lineno} Chinese literal {node.value[:30]!r}")


# ============= dsl-frozen =============

def _literal_assign(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except ValueError:
                        return None
    return None


def collect_dsl() -> dict:
    result = {}
    embed_tree = ast.parse((ROOT / "embed_template.py").read_text(encoding="utf-8-sig"))
    directives = _literal_assign(embed_tree, "EMBED_DIRECTIVES")
    result["embed_directives"] = sorted(directives.keys()) if directives else []

    moderate_tree = ast.parse((ROOT / "Moderate.py").read_text(encoding="utf-8-sig"))
    variables = _literal_assign(moderate_tree, "MODERATION_TEMPLATE_VARIABLES")
    result["moderation_template_variables"] = sorted(variables) if variables else []
    for node in ast.walk(moderate_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "timestr_to_seconds":
            units = _literal_assign(node, "units")
            result["timestr_units"] = sorted(units.keys()) if units else []
            break

    antibeast_tree = ast.parse((ROOT / "AntiBeast.py").read_text(encoding="utf-8-sig"))
    prefixes = _literal_assign(antibeast_tree, "SUPPORTED_ACTION_PREFIXES")
    result["antibeast_action_prefixes"] = sorted(prefixes) if prefixes else []
    return result


def check_dsl_frozen(report: Report, update: bool):
    current = collect_dsl()
    for name, values in current.items():
        if not values:
            report.warning("dsl-frozen", f"could not extract {name}")
    if update or not SNAPSHOT_FILE.exists():
        SNAPSHOT_FILE.write_text(
            json.dumps(current, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
        if not update:
            report.warning("dsl-frozen", f"snapshot created at {SNAPSHOT_FILE}")
        return
    snapshot = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    for name, values in current.items():
        if snapshot.get(name) != values:
            report.error(
                "dsl-frozen",
                f"{name} changed vs snapshot "
                f"(added={sorted(set(values) - set(snapshot.get(name, [])))}, "
                f"removed={sorted(set(snapshot.get(name, [])) - set(values))}); "
                f"these are persisted DSL vocabularies — translating/renaming them "
                f"breaks stored guild data. Run --update-snapshot only if intentional.")


# ============= unused =============

_WHITELIST_PREFIXES = ("common.",)


def check_unused(report: Report):
    referenced: set[str] = set()
    enum_prefixes: set[str] = set()
    for path in ROOT.glob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                referenced.add(node.value)
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name == "t_enum" and node.args and \
                        isinstance(node.args[0], ast.Constant):
                    enum_prefixes.add(node.args[0].value + ".")
            # f-string 動態 key（如 i18n_key=f"cmd.automoderate.setting.{key}"）
            # 以開頭的常數部分作為前綴白名單
            if isinstance(node, ast.JoinedStr) and node.values:
                first = node.values[0]
                if isinstance(first, ast.Constant) and \
                        isinstance(first.value, str) and "." in first.value:
                    enum_prefixes.add(first.value)
    # templates 與前端 JS 以原文比對（t('key') / t("key")）
    web_blob = []
    for pattern in ("templates/*.html", "static/js/*.js"):
        for path in ROOT.glob(pattern):
            try:
                web_blob.append(path.read_text(encoding="utf-8"))
            except OSError:
                pass
    web_blob = "\n".join(web_blob)

    source = i18n._catalogs.get(i18n.SOURCE_LOCALE, {})
    for key in source:
        if key in referenced or key in web_blob:
            continue
        if any(key.startswith(prefix) for prefix in _WHITELIST_PREFIXES):
            continue
        if any(key.startswith(prefix) for prefix in enum_prefixes):
            continue
        # …param_name.<x> 由 i18n.annotate_parameter_name_keys 於 sync 前從
        # 對應的 …param.<x> describe key 動態推導，程式碼中不會出現字面值；
        # 視同其 …param.<x> 兄弟 key 被引用
        if ".param_name." in key:
            prefix, _, pname = key.rpartition(".param_name.")
            if f"{prefix}.param.{pname}" in referenced or f"{prefix}.desc" in referenced:
                continue
        report.warning("unused", f"{key} is never referenced from code")


# ============= manifest-parity =============

def check_manifest_parity(report: Report):
    docs = ROOT / "docs"
    manifests = {}
    if not docs.exists():
        return
    for entry in docs.iterdir():
        manifest = entry / "manifest.json"
        if entry.is_dir() and manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8-sig"))
            except ValueError as e:
                report.error("manifest-parity", f"{manifest}: invalid JSON ({e})")
                continue
            ids = set()
            def collect(node):
                if isinstance(node, dict):
                    if "id" in node:
                        ids.add(node["id"])
                    for value in node.values():
                        collect(value)
                elif isinstance(node, list):
                    for item in node:
                        collect(item)
            collect(data)
            manifests[entry.name] = ids
    if len(manifests) > 1:
        locales = sorted(manifests)
        base = manifests[locales[0]]
        for locale in locales[1:]:
            if manifests[locale] != base:
                report.error("manifest-parity",
                             f"docs/{locales[0]} vs docs/{locale}: section ids differ "
                             f"({sorted(base ^ manifests[locale])})")


def run(update_ratchet: bool = False, update_snapshot: bool = False,
        quiet: bool = False) -> Report:
    report = Report()
    untranslated = check_catalogs(report)
    check_ratchet(report, untranslated, update_ratchet)
    check_hardcoded(report)
    check_dsl_frozen(report, update_snapshot)
    check_unused(report)
    check_manifest_parity(report)
    if not quiet:
        for message in report.errors:
            print(f"ERROR   {message}")
        for message in report.warnings:
            print(f"warning {message}")
        total = {loc: n for loc, n in untranslated.items()}
        print(f"-- {len(report.errors)} error(s), {len(report.warnings)} warning(s), "
              f"untranslated: {total}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-ratchet", action="store_true")
    parser.add_argument("--update-snapshot", action="store_true")
    args = parser.parse_args()
    report = run(update_ratchet=args.update_ratchet,
                 update_snapshot=args.update_snapshot)
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
