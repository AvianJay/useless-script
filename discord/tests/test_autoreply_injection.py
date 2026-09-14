"""Regression tests for template-directive injection through AutoReply variables.

``_render_response_segment`` 先把 ``{content}`` 等變數代入樣板，**之後**才跑
``{mention:}`` / ``{react:}`` / ``{sticker:}`` / ``{embed*:}`` / ``{guildvar:}``
這些指令解析器。代入的文字完全由發話者控制，所以在修正前，任何成員只要對一條
使用 ``{content}`` 的自動回覆送出：

    <觸發詞> {mention:true} @everyone

就會讓 ``_extract_mention_directive`` 把 allowed_mentions 翻成
``everyone=True``，由 bot 代為 ping 全伺服器——發話者本身完全不需要任何權限。

修正方式是代入時先用 ``neutralize_injected_value()`` 把大括號換成哨符，等所有
指令解析完再由 ``restore_injected_braces()`` 還原，因此樣板作者自己寫的
``{mention:true}`` 仍然有效。
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import AutoReply as autoreply_module

neutralize = autoreply_module.neutralize_injected_value
restore = autoreply_module.restore_injected_braces


class BraceNeutralisationTests(unittest.TestCase):
    def test_round_trip_preserves_literal_braces(self):
        for raw in ("{mention:true}", "a{b}c", "{{}}", "no braces here", "{", "}"):
            with self.subTest(raw=raw):
                self.assertEqual(raw, restore(neutralize(raw)))

    def test_text_without_braces_is_returned_unchanged(self):
        self.assertIs(neutralize("plain"), "plain")

    def test_none_becomes_empty_string(self):
        self.assertEqual("", neutralize(None))

    def test_neutralised_text_contains_no_parsable_braces(self):
        out = neutralize("{mention:true} @everyone {react:x} {guildvar:k:v}")
        self.assertNotIn("{", out)
        self.assertNotIn("}", out)


class MentionDirectiveInjectionTests(unittest.TestCase):
    def setUp(self):
        self.cog = autoreply_module.AutoReply.__new__(autoreply_module.AutoReply)

    def test_injected_mention_directive_does_not_grant_everyone(self):
        """代入的 {mention:true} 必須被當成字面文字，不得翻開 allowed_mentions。"""
        injected = neutralize("{mention:true} @everyone")
        cleaned, allowed = self.cog._extract_mention_directive(injected)

        self.assertFalse(allowed.everyone, "注入的 {mention:true} 不可以打開 everyone")
        self.assertFalse(allowed.roles, "注入的 {mention:true} 不可以打開 roles")
        self.assertEqual(
            "{mention:true} @everyone",
            restore(cleaned),
            "字面文字應原樣輸出（@everyone 因 allowed_mentions 而不會真的 ping）",
        )

    def test_template_author_directive_still_works(self):
        """樣板作者自己寫在樣板裡的 {mention:true} 不受影響。"""
        cleaned, allowed = self.cog._extract_mention_directive("{mention:true} hi")

        self.assertTrue(allowed.everyone)
        self.assertTrue(allowed.roles)
        self.assertEqual("hi", cleaned.strip())

    def test_mixed_template_directive_and_injected_text(self):
        """樣板的指令生效，代入文字裡的同名指令仍是字面值。"""
        template = "{mention:true} " + neutralize("{mention:false} raw")
        cleaned, allowed = self.cog._extract_mention_directive(template)

        self.assertTrue(allowed.everyone, "樣板自己的 {mention:true} 應生效")
        self.assertIn("{mention:false}", restore(cleaned))

    def test_default_allowed_mentions_never_ping_everyone(self):
        allowed = self.cog._build_allowed_mentions()
        self.assertFalse(allowed.everyone)
        self.assertFalse(allowed.roles)


class InjectedDirectiveSurvivalTests(unittest.TestCase):
    """其餘指令解析器看到的也必須是哨符，而不是可解析的大括號。"""

    PAYLOADS = (
        "{react:🔥}",
        "{sticker:123456789}",
        "{embedtitle:pwned}",
        "{embedimage:https://example.invalid/x.png}",
        "{guildvar:admin:1}",
    )

    def test_directive_patterns_do_not_match_neutralised_text(self):
        import re

        patterns = {
            "react": re.compile(r"\{react:([^\}]+)\}"),
            "sticker": re.compile(r"\{sticker:(\d+)\}"),
            "mention": re.compile(r"\{mention:(true|false)\}", re.IGNORECASE),
        }
        for payload in self.PAYLOADS:
            neutralised = neutralize(payload)
            for name, pattern in patterns.items():
                with self.subTest(payload=payload, pattern=name):
                    self.assertIsNone(
                        pattern.search(neutralised),
                        f"{name} 解析器仍然能比對到被代入的 {payload}",
                    )
            self.assertEqual(payload, restore(neutralised))


if __name__ == "__main__":
    unittest.main()
