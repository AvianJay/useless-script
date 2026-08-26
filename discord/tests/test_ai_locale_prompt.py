import asyncio
import sys
import unittest
from pathlib import Path


DISCORD_DIR = Path(__file__).resolve().parents[1]
if str(DISCORD_DIR) not in sys.path:
    sys.path.insert(0, str(DISCORD_DIR))

import i18n
from ai import AICommands, build_ai_response_language_prompt


class AILocalePromptTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_locale_language_instructions(self):
        zh = build_ai_response_language_prompt("zh-TW")
        en = build_ai_response_language_prompt("en")
        ja = build_ai_response_language_prompt("ja")
        self.assertIn("繁體中文", zh)
        self.assertIn("明確要求", zh)
        self.assertIn("natural English", en)
        self.assertIn("explicitly requests", en)
        self.assertIn("自然な日本語", ja)
        self.assertIn("明確に指定", ja)

    def test_system_prompt_uses_effective_locale(self):
        command = AICommands.__new__(AICommands)
        command._build_runtime_prompt_context = lambda _context: ""
        command._get_docs_feature_prompt = lambda: ""
        command._build_guild_ai_custom_prompt_context = lambda _context: ""
        command._build_ai_profile_context = lambda _context: ""
        for locale, expected in (
            ("zh-TW", "繁體中文"),
            ("en", "natural English"),
            ("ja", "自然な日本語"),
        ):
            with self.subTest(locale=locale), i18n.use_locale(locale):
                prompt = command._build_system_with_context()
                self.assertIn(expected, prompt)

    async def test_parallel_locale_scopes_do_not_cross_contaminate(self):
        async def worker(locale):
            with i18n.use_locale(locale):
                await asyncio.sleep(0)
                return build_ai_response_language_prompt()

        zh, en, ja = await asyncio.gather(
            worker("zh-TW"), worker("en"), worker("ja"))
        self.assertIn("繁體中文", zh)
        self.assertIn("natural English", en)
        self.assertIn("自然な日本語", ja)


if __name__ == "__main__":
    unittest.main()
