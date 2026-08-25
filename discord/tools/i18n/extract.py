"""i18n 抽取工具 — Phase A：普查（census）。

    python -m tools.i18n.extract                       # 全部根目錄模組
    python -m tools.i18n.extract --files Ticket.py     # 指定模組
    python -m tools.i18n.extract -o i18n-candidates.json

掃描所有含中文的字串字面值與 f-string，依 AST 父節點分類 kind、標記
風險（risk）與建議動作（action），輸出 i18n-candidates.json 供後續
key 命名與改寫（Phase B/C）使用。**本階段不改寫任何檔案。**

風險分類（自動涵蓋計畫中的硬跳過清單）：
- comparison   : 出現在 == / != / in 比較中（資料往返，skip）
- regex        : re.compile/search/match/sub/fullmatch 引數（legacy parser，skip）
- dict_key     : dict 的 key（查表用，skip）
- allcaps      : 指派給模組層級 ALL_CAPS 常數（DSL / 領域資料，skip）
- db_write     : 流向 set_user_data / set_server_config / db.* / json.dump（skip）
- locale_str   : 已是 locale_str 引數（skip）
- class_body   : 於 class body / decorator 求值（manual：K() + I18nView/I18nModal）
- default_arg  : 函式參數預設值（manual）
- (無)         : 可自動改寫候選
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_CJK_RE = re.compile(r"[一-鿿]")

# 跳過的目錄與檔案
_SKIP_FILES = {"i18n.py"}

_RE_FUNCS = {"compile", "search", "match", "fullmatch", "sub", "subn", "split", "findall"}
_DB_WRITE_FUNCS = {"set_user_data", "set_server_config", "set_global_config", "dump", "dumps"}

# keyword 引數名 -> kind
_KEYWORD_KINDS = {
    "title": "title",
    "description": "description",
    "label": "label",
    "placeholder": "placeholder",
    "content": "content",
    "text": "text",
    "name": "name",
    "value": "value",
    "reason": "reason",
    "custom_id": "custom_id",
}

# 呼叫對象名 -> kind 前綴
_CALL_KINDS = {
    "Embed": "embed",
    "add_field": "field",
    "set_footer": "footer",
    "set_author": "author",
    "send": "send",
    "send_message": "send",
    "followup": "send",
    "edit_message": "send",
    "reply": "send",
    "TextInput": "textinput",
    "TextDisplay": "text_display",
    "Button": "button",
    "button": "button",
    "SelectOption": "option",
    "select": "select",
    "Select": "select",
    "Choice": "choice",
    "describe": "describe",
    "command": "cmd",
    "Modal": "modal",
    "locale_str": "locale_str",
    "log": "log",
}


def _build_parents(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _docstring_nodes(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                found.add(id(body[0].value))
    return found


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _ancestors(node: ast.AST, parents: dict[int, ast.AST]):
    current = node
    while id(current) in parents:
        current = parents[id(current)]
        yield current


def _enclosing(node: ast.AST, parents: dict[int, ast.AST]) -> str:
    names = []
    for ancestor in _ancestors(node, parents):
        if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(ancestor.name)
    return ".".join(reversed(names)) or "<module>"


def _classify(node: ast.AST, parents: dict[int, ast.AST]) -> tuple[str, list[str]]:
    """回傳 (kind, risks)。"""
    kind = "plain"
    risks: list[str] = []
    previous = node
    in_function = False

    for ancestor in _ancestors(node, parents):
        # 比較
        if isinstance(ancestor, ast.Compare):
            risks.append("comparison")
        # dict key
        if isinstance(ancestor, ast.Dict) and previous in ancestor.keys:
            risks.append("dict_key")
        # keyword 引數
        if isinstance(ancestor, ast.keyword) and kind == "plain":
            kind = _KEYWORD_KINDS.get(ancestor.arg or "", "plain")
        # 呼叫
        if isinstance(ancestor, ast.Call):
            name = _call_name(ancestor)
            if name in _RE_FUNCS:
                value = ancestor.func
                if isinstance(value, ast.Attribute) and \
                        isinstance(value.value, ast.Name) and value.value.id == "re":
                    risks.append("regex")
            if name in _DB_WRITE_FUNCS:
                risks.append("db_write")
            if name == "locale_str":
                risks.append("locale_str")
            if name in _CALL_KINDS and kind in ("plain", "value", "name",
                                                "title", "description", "label",
                                                "placeholder", "content", "text"):
                prefix = _CALL_KINDS[name]
                kind = f"{prefix}_{kind}" if kind != "plain" else prefix
        # ALL_CAPS 模組常數
        if isinstance(ancestor, ast.Assign):
            for target in ancestor.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    risks.append("allcaps")
        # 函式預設值
        if isinstance(ancestor, ast.arguments):
            risks.append("default_arg")
        # class body / decorator（未進入函式就先遇到 ClassDef，或在 decorator 裡）
        if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if previous in getattr(ancestor, "decorator_list", []):
                risks.append("class_body")
            in_function = True
        if isinstance(ancestor, ast.ClassDef) and not in_function:
            risks.append("class_body")
        previous = ancestor
    return kind, sorted(set(risks))


def _fstring_parts(node: ast.JoinedStr) -> str:
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            parts.append("{…}")
    return "".join(parts)


def scan_file(path: Path) -> list[dict]:
    source = path.read_text(encoding="utf-8-sig")
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        print(f"!! cannot parse {path.name}: {e}", file=sys.stderr)
        return []
    parents = _build_parents(tree)
    docstrings = _docstring_nodes(tree)
    module = path.stem
    results = []
    seen_fstring_parts: set[int] = set()

    for node in ast.walk(tree):
        text = None
        node_kind = None
        if isinstance(node, ast.JoinedStr):
            joined = _fstring_parts(node)
            if _CJK_RE.search(joined):
                text = joined
                node_kind = "fstring"
                for value in node.values:
                    seen_fstring_parts.add(id(value))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings or id(node) in seen_fstring_parts:
                continue
            if _CJK_RE.search(node.value):
                text = node.value
                node_kind = "literal"
        if text is None:
            continue

        line = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
        if "# i18n: skip" in line:
            continue

        kind, risks = _classify(node, parents)
        action = "rewrite"
        if any(risk in ("comparison", "regex", "dict_key", "allcaps",
                        "db_write", "locale_str") for risk in risks):
            action = "skip"
        elif any(risk in ("class_body", "default_arg") for risk in risks):
            action = "manual"

        results.append({
            "file": path.name,
            "line": node.lineno,
            "col": node.col_offset,
            "node": node_kind,
            "kind": kind,
            "risk": risks,
            "action": action,
            "enclosing": _enclosing(node, parents),
            "text": text if len(text) <= 200 else text[:200] + "…",
            "suggested_ns": module.lower(),
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", nargs="*", help="只掃描指定檔案")
    parser.add_argument("-o", "--output", default="i18n-candidates.json")
    args = parser.parse_args()

    if args.files:
        paths = [ROOT / name for name in args.files]
    else:
        paths = sorted(p for p in ROOT.glob("*.py") if p.name not in _SKIP_FILES)

    all_results: list[dict] = []
    for path in paths:
        if path.exists():
            all_results.extend(scan_file(path))
        else:
            print(f"!! not found: {path}", file=sys.stderr)

    output = ROOT / args.output
    output.write_text(
        json.dumps(all_results, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")

    by_file = Counter(item["file"] for item in all_results)
    by_action = Counter(item["action"] for item in all_results)
    print(f"{len(all_results)} candidates -> {output}")
    print("actions:", dict(by_action))
    print("top files:")
    for name, count in by_file.most_common(15):
        print(f"  {count:5d}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
