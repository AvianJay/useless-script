"""Batch 1 codemod：斜線指令 metadata 全面遷移至 locale_str + i18n_key。

    python -m tools.i18n.migrate_commands --extract   # 掃描 → 種語言檔（不改程式碼）
    python -m tools.i18n.migrate_commands --rewrite   # 依語言檔改寫程式碼
    python -m tools.i18n.migrate_commands --rewrite --files Ticket.py

流程：
1. --extract：掃描全部指令宣告，把現有中文描述種進 zh-TW/_commands.json、
   名稱翻譯從 globalenv.translations 帶入，en/_commands.json 種 null
   （待人工翻譯）。絕不覆蓋既有 key。
2. 人工把 en/_commands.json 的 null 填成英文。
3. --rewrite：以 byte-span 替換把宣告改成
   name=app_commands.locale_str("<base>", i18n_key="cmd...")；
   description 的 base 取 en 語言檔值（缺英文則保留原文並回報 TODO）。

涵蓋的宣告形式：
- @app_commands.command / @bot.tree.command / @<group_var>.command
- @bot.tree.context_menu（中文 base 名 → 翻轉為英文 base + zh 在地化）
- class X(commands.GroupCog, name=..., description=...)（含 docstring 描述）
- xxx = app_commands.Group(name=..., description=..., parent=...)
- @app_commands.describe(param="...")
- @app_commands.choices(param=[Choice(name=..., value=...)])

已是 locale_str 的宣告會補上 i18n_key。文字（prefix）指令
（@commands.command / 文字 Group）不在範圍內、自動排除。

正確性註記：ast 的 col_offset 以 UTF-8 byte 為單位，本工具全程以
byte 運算；BOM（utf-8-sig）保留原樣寫回。
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

ZH_COMMANDS = ROOT / "locales" / "zh-TW" / "_commands.json"
EN_COMMANDS = ROOT / "locales" / "en" / "_commands.json"

_CJK_RE = re.compile(r"[一-鿿]")

# 掃描的模組 = modules.json + 其他含指令的根目錄檔案；排除工具/測試
_SKIP_FILES = {"i18n.py", "all.py", "database.py", "logger.py", "globalenv.py",
               "Language.py"}  # Language.py 已手動遷移


def _slug(text: str, fallback: str = "x") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
    return slug or fallback


def _load_translations() -> dict:
    """從 globalenv.py 的 AST 讀 translations dict（不 import，避免副作用）。"""
    tree = ast.parse((ROOT / "globalenv.py").read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "translations":
                    try:
                        return ast.literal_eval(node.value)
                    except ValueError:
                        return {}
    return {}


# ============= byte-span 編輯器 =============

class FileEditor:
    def __init__(self, path: Path):
        self.path = path
        raw = path.read_bytes()
        self.bom = raw.startswith(b"\xef\xbb\xbf")
        self.data = raw[3:] if self.bom else raw
        self.text = self.data.decode("utf-8")
        # 每行起始 byte offset（ast lineno 為 1-based，col_offset 為 byte）
        self.line_starts = [0]
        for line in self.data.splitlines(keepends=True):
            self.line_starts.append(self.line_starts[-1] + len(line))
        self.edits: list[tuple[int, int, str]] = []

    def span(self, node: ast.AST) -> tuple[int, int]:
        start = self.line_starts[node.lineno - 1] + node.col_offset
        end = self.line_starts[node.end_lineno - 1] + node.end_col_offset
        return start, end

    def replace(self, node: ast.AST, replacement: str):
        start, end = self.span(node)
        self.edits.append((start, end, replacement))

    def insert_after(self, node: ast.AST, insertion: str):
        _, end = self.span(node)
        self.edits.append((end, end, insertion))

    def insert_at(self, offset: int, insertion: str):
        self.edits.append((offset, offset, insertion))

    def apply(self) -> bool:
        if not self.edits:
            return False
        # 由右往左套用；插入（start==end）排在替換之後處理同位點
        data = self.data
        for start, end, replacement in sorted(
                self.edits, key=lambda e: (e[0], e[1]), reverse=True):
            data = data[:start] + replacement.encode("utf-8") + data[end:]
        if self.bom:
            data = b"\xef\xbb\xbf" + data
        self.path.write_bytes(data)
        return True


def _py_str(text: str) -> str:
    """產生 Python 雙引號字串字面值（保留非 ASCII）。"""
    return json.dumps(text, ensure_ascii=False)


# ============= 宣告掃描 =============

class Declaration:
    """一筆需要處理的字串位置。"""

    def __init__(self, kind: str, key: str, node: ast.AST, text: str,
                 is_locale_str: bool, editor: FileEditor):
        self.kind = kind          # name / desc / param / choice / ctx_name
        self.key = key            # 完整 i18n key（含 .name/.desc/...）
        self.node = node          # 要替換的節點（Constant 或 locale_str Call）
        self.text = text          # 現有 base 文字
        self.is_locale_str = is_locale_str
        self.editor = editor


class MissingDesc:
    """GroupCog docstring 描述 / 無 description kwarg 的指令：需插入 kwarg。"""

    def __init__(self, key: str, text: str, anchor: ast.AST, editor: FileEditor,
                 needs_leading_comma: bool):
        self.key = key
        self.text = text          # 現有中文描述（docstring 第一行）
        self.anchor = anchor      # 在此節點之後插入
        self.editor = editor
        self.needs_leading_comma = needs_leading_comma


def _call_path(func: ast.AST) -> list[str]:
    """把 a.b.c 展開成 ['a','b','c']。"""
    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return list(reversed(parts))


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _is_locale_str_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _call_path(node.func)[-1] == "locale_str"


def _string_of(node: ast.AST) -> str | None:
    """Constant str 或 locale_str("...") 的 message。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if _is_locale_str_call(node) and node.args and \
            isinstance(node.args[0], ast.Constant):
        return node.args[0].value
    return None


def _has_i18n_key(node: ast.AST) -> bool:
    return _is_locale_str_call(node) and _kw(node, "i18n_key") is not None


def _docstring_first_line(node) -> str | None:
    doc = ast.get_docstring(node)
    if not doc:
        return None
    return doc.strip().splitlines()[0][:100]


class ModuleScanner:
    def __init__(self, path: Path):
        self.path = path
        self.module_ns = path.stem.lower()
        self.editor = FileEditor(path)
        self.tree = ast.parse(self.editor.text)
        self.decls: list[Declaration] = []
        self.missing_descs: list[MissingDesc] = []
        self.group_paths: dict[str, list[str]] = {}  # 變數名 -> group slug 路徑
        self.skipped: list[str] = []

    # ---- group 變數收集（含 parent= 鏈）----
    def _collect_groups(self):
        pending: list[tuple[str, ast.Call]] = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                call = node.value
                if _call_path(call.func)[-2:] == ["app_commands", "Group"] or \
                        _call_path(call.func) == ["Group"]:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            pending.append((target.id, call))
        # parent= 需要多輪解析
        for _ in range(4):
            for var, call in pending:
                if var in self.group_paths:
                    continue
                name = _string_of(_kw(call, "name")) or var
                parent_kw = _kw(call, "parent")
                if parent_kw is None:
                    self.group_paths[var] = [_slug(name)]
                elif isinstance(parent_kw, ast.Name) and \
                        parent_kw.id in self.group_paths:
                    self.group_paths[var] = \
                        self.group_paths[parent_kw.id] + [_slug(name)]
        self._group_calls = pending

    def _key(self, parts: list[str]) -> str:
        return ".".join(["cmd", self.module_ns] + parts)

    def _add(self, kind: str, key: str, node: ast.AST):
        text = _string_of(node)
        if text is None:
            self.skipped.append(f"{self.path.name}:{node.lineno} non-literal {kind}")
            return
        if _has_i18n_key(node):
            return  # 已遷移
        self.decls.append(Declaration(kind, key, node, text,
                                      _is_locale_str_call(node), self.editor))

    # ---- 主掃描 ----
    def scan(self):
        self._collect_groups()

        # Group 指派本身的 name/desc
        for var, call in self._group_calls:
            path = self.group_paths.get(var)
            if path is None:
                continue
            name_node = _kw(call, "name")
            desc_node = _kw(call, "description")
            base = path + ["root"]
            if name_node is not None:
                self._add("name", self._key(base + ["name"]), name_node)
            if desc_node is not None:
                self._add("desc", self._key(base + ["desc"]), desc_node)

        # GroupCog class 定義
        class_group: dict[int, list[str]] = {}  # ClassDef id -> group path
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                is_groupcog = any(
                    _call_path(base)[-1] == "GroupCog" for base in node.bases
                    if isinstance(base, (ast.Attribute, ast.Name)))
                if not is_groupcog:
                    continue
                name_kw = next((k for k in node.keywords if k.arg == "name"), None)
                desc_kw = next((k for k in node.keywords if k.arg == "description"), None)
                group_name = _string_of(name_kw.value) if name_kw else None
                path = [_slug(group_name or node.name)]
                class_group[id(node)] = path
                base = path + ["root"]
                if name_kw is not None:
                    self._add("name", self._key(base + ["name"]), name_kw.value)
                if desc_kw is not None:
                    self._add("desc", self._key(base + ["desc"]), desc_kw.value)
                else:
                    doc = _docstring_first_line(node)
                    if doc and name_kw is not None:
                        anchor = name_kw.value
                        self.missing_descs.append(MissingDesc(
                            self._key(base + ["desc"]), doc, anchor,
                            self.editor, needs_leading_comma=True))

        # 函式上的 decorator
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            parent_class = self._enclosing_class(node)
            group_path = class_group.get(id(parent_class)) if parent_class else None

            cmd_call = None
            cmd_key_parts: list[str] | None = None
            is_ctx_menu = False

            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                path = _call_path(decorator.func)
                tail = path[-1] if path else ""

                if tail == "command":
                    owner = path[-2] if len(path) >= 2 else ""
                    if owner == "app_commands":
                        prefix = group_path or []
                    elif owner == "tree":
                        prefix = []
                    elif owner in self.group_paths:
                        prefix = self.group_paths[owner]
                    else:
                        continue  # 文字指令（commands.command / 文字 group）
                    name_node = _kw(decorator, "name")
                    cmd_name = _string_of(name_node) if name_node is not None \
                        else node.name
                    cmd_key_parts = prefix + [_slug(cmd_name, node.name)]
                    cmd_call = decorator
                    if name_node is not None:
                        self._add("name", self._key(cmd_key_parts + ["name"]),
                                  name_node)
                    desc_node = _kw(decorator, "description")
                    if desc_node is not None:
                        self._add("desc", self._key(cmd_key_parts + ["desc"]),
                                  desc_node)

                elif tail == "context_menu" and "tree" in path:
                    cmd_key_parts = ["ctx", _slug(node.name)]
                    cmd_call = decorator
                    is_ctx_menu = True
                    name_node = _kw(decorator, "name")
                    if name_node is not None:
                        self._add("ctx_name", self._key(cmd_key_parts + ["name"]),
                                  name_node)

            if cmd_call is None:
                continue

            # 無 description kwarg：docstring 為描述來源
            if not is_ctx_menu and _kw(cmd_call, "description") is None:
                doc = _docstring_first_line(node)
                if doc:
                    if cmd_call.keywords:
                        anchor = cmd_call.keywords[-1].value
                        leading = True
                    elif cmd_call.args:
                        anchor = cmd_call.args[-1]
                        leading = True
                    else:
                        anchor = None
                        leading = False
                    if anchor is not None:
                        self.missing_descs.append(MissingDesc(
                            self._key(cmd_key_parts + ["desc"]), doc, anchor,
                            self.editor, needs_leading_comma=leading))
                    else:
                        # 空括號：插在括號內
                        start, end = self.editor.span(cmd_call)
                        self._empty_call_desc = (end - 1, self._key(
                            cmd_key_parts + ["desc"]), doc)
                        self.missing_descs.append(MissingDesc(
                            self._key(cmd_key_parts + ["desc"]), doc, None,
                            self.editor, needs_leading_comma=False))
                        self.missing_descs[-1].offset = end - 1

            # describe / choices（必須在指令 key 已知之後）
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                tail = _call_path(decorator.func)[-1:]
                tail = tail[0] if tail else ""
                if tail == "describe":
                    for keyword in decorator.keywords:
                        if keyword.arg is None:
                            continue
                        self._add("param",
                                  self._key(cmd_key_parts +
                                            ["param", keyword.arg]),
                                  keyword.value)
                elif tail == "choices":
                    for keyword in decorator.keywords:
                        if not isinstance(keyword.value, (ast.List, ast.Tuple)):
                            continue
                        used_slugs: set[str] = set()
                        for index, element in enumerate(keyword.value.elts):
                            if not (isinstance(element, ast.Call) and
                                    _call_path(element.func)[-1] == "Choice"):
                                continue
                            choice_name = _kw(element, "name")
                            choice_value = _kw(element, "value")
                            if choice_name is None:
                                continue
                            value_text = None
                            if isinstance(choice_value, ast.Constant):
                                value_text = str(choice_value.value)
                            slug = _slug(value_text
                                         if value_text is not None
                                         else (_string_of(choice_name) or ""),
                                         fallback="")
                            # 中文 value slug 成空字串 / 重複時以序號消歧
                            if not slug or slug in used_slugs:
                                slug = f"{slug}_c{index}" if slug else f"c{index}"
                            used_slugs.add(slug)
                            self._add("choice",
                                      self._key(cmd_key_parts +
                                                ["choice", slug]),
                                      choice_name)

    def _enclosing_class(self, target) -> ast.ClassDef | None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                for child in ast.walk(node):
                    if child is target:
                        # 確認不是巢狀函式裡的 class
                        return node
        return None


# ============= 語言檔讀寫 =============

def _get_nested(data: dict, dotted: str):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _set_nested(data: dict, dotted: str, value, overwrite: bool = False):
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            return
    if overwrite or parts[-1] not in node or node[parts[-1]] is None:
        node[parts[-1]] = value


def _load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8-sig"))
    return {}


def _dump_json(path: Path, data: dict):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=4, sort_keys=True) + "\n",
        encoding="utf-8")


def _strip_cmd_prefix(key: str) -> str:
    assert key.startswith("cmd.")
    return key[len("cmd."):]


# ============= extract / rewrite =============

def scan_all(files: list[str] | None) -> list[ModuleScanner]:
    if files:
        paths = [ROOT / name for name in files]
    else:
        paths = sorted(
            p for p in ROOT.glob("*.py")
            if p.name not in _SKIP_FILES and not p.name.startswith("test"))
    scanners = []
    for path in paths:
        if not path.exists():
            print(f"!! not found: {path}", file=sys.stderr)
            continue
        scanner = ModuleScanner(path)
        try:
            scanner.scan()
        except SyntaxError as e:
            print(f"!! cannot parse {path.name}: {e}", file=sys.stderr)
            continue
        if scanner.decls or scanner.missing_descs:
            scanners.append(scanner)
    return scanners


def do_extract(scanners: list[ModuleScanner]):
    translations = _load_translations()
    zh = _load_json(ZH_COMMANDS)
    en = _load_json(EN_COMMANDS)
    stats = {"zh_seeded": 0, "en_todo": 0}

    for scanner in scanners:
        for decl in scanner.decls:
            rel = _strip_cmd_prefix(decl.key)
            if decl.kind == "name":
                # base 是英文名；zh 名稱從 translations 帶入
                zh_name = translations.get(decl.text)
                if zh_name:
                    _set_nested(zh, rel, zh_name)
                    stats["zh_seeded"] += 1
                _set_nested(en, rel, decl.text)
            elif decl.kind == "ctx_name":
                # context menu：base 目前是中文
                _set_nested(zh, rel, decl.text)
                if _get_nested(en, rel) is None:
                    _set_nested(en, rel, None)
                    stats["en_todo"] += 1
            else:  # desc / param / choice：base 目前是中文
                _set_nested(zh, rel, decl.text)
                if _get_nested(en, rel) is None:
                    _set_nested(en, rel, None)
                    stats["en_todo"] += 1
        for missing in scanner.missing_descs:
            rel = _strip_cmd_prefix(missing.key)
            _set_nested(zh, rel, missing.text)
            if _get_nested(en, rel) is None:
                _set_nested(en, rel, None)
                stats["en_todo"] += 1

    _dump_json(ZH_COMMANDS, zh)
    _dump_json(EN_COMMANDS, en)
    print(f"seeded zh names: {stats['zh_seeded']}, en TODO(null): {stats['en_todo']}")
    for scanner in scanners:
        for message in scanner.skipped:
            print(f"  skipped: {message}")


def do_rewrite(scanners: list[ModuleScanner]):
    en = _load_json(EN_COMMANDS)
    todo: list[str] = []
    changed = 0

    for scanner in scanners:
        for decl in scanner.decls:
            rel = _strip_cmd_prefix(decl.key)
            if decl.kind in ("name",):
                base = decl.text  # 名稱 base 維持英文原名
            else:
                en_value = _get_nested(en, rel)
                if isinstance(en_value, str) and en_value:
                    base = en_value
                else:
                    base = decl.text  # 缺英文：暫保留原文
                    todo.append(decl.key)
            decl.editor.replace(
                decl.node,
                f"app_commands.locale_str({_py_str(base)}, "
                f"i18n_key={_py_str(decl.key)})")

        for missing in scanner.missing_descs:
            rel = _strip_cmd_prefix(missing.key)
            en_value = _get_nested(en, rel)
            if isinstance(en_value, str) and en_value:
                base = en_value
            else:
                base = missing.text
                todo.append(missing.key)
            text = (f"description=app_commands.locale_str({_py_str(base)}, "
                    f"i18n_key={_py_str(missing.key)})")
            if missing.anchor is not None:
                prefix = ", " if missing.needs_leading_comma else ""
                missing.editor.insert_after(missing.anchor, prefix + text)
            else:
                missing.editor.insert_at(missing.offset, text)

        if scanner.editor.apply():
            changed += 1
            print(f"rewrote {scanner.path.name} "
                  f"({len(scanner.editor.edits)} edits)")

    if todo:
        print(f"\n{len(todo)} keys still lack English (kept Chinese base):")
        for key in sorted(set(todo))[:40]:
            print(f"  {key}")
        if len(todo) > 40:
            print(f"  ... and {len(todo) - 40} more")
    print(f"\n{changed} file(s) rewritten")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--rewrite", action="store_true")
    parser.add_argument("--files", nargs="*")
    args = parser.parse_args()
    if not args.extract and not args.rewrite:
        parser.error("choose --extract or --rewrite")

    scanners = scan_all(args.files)
    total = sum(len(s.decls) for s in scanners)
    missing = sum(len(s.missing_descs) for s in scanners)
    print(f"{len(scanners)} file(s), {total} declaration string(s), "
          f"{missing} docstring description(s)")

    if args.extract:
        do_extract(scanners)
    if args.rewrite:
        do_rewrite(scanners)
    return 0


if __name__ == "__main__":
    sys.exit(main())
