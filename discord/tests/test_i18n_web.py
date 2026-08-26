import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import render_template


DISCORD_DIR = Path(__file__).resolve().parents[1]
if str(DISCORD_DIR) not in sys.path:
    sys.path.insert(0, str(DISCORD_DIR))

import globalenv

_original_modules = list(globalenv.modules)
globalenv.modules[:] = ["Website"]
try:
    import Website
finally:
    globalenv.modules[:] = _original_modules

import doc_markdown


CORE_TEMPLATES = (
    "index.html",
    "docs.html",
    "panel.html",
    "panel_guild.html",
    "panel_login.html",
    "contribute_feed_grass.html",
    "ServerVerify.html",
    "PrivacyPolicy.html",
    "TermsofService.html",
)
LANGUAGE_SELECTOR_RE = re.compile(
    r'<form class="language-selector".*?</form>', re.DOTALL)
CJK_RE = re.compile(r"[\u3400-\u9fff]")


def _fake_bot():
    avatar = SimpleNamespace(url="https://cdn.example.test/bot.png")
    user = SimpleNamespace(name="TestBot", id=123, avatar=avatar)
    application = SimpleNamespace(owner="test-owner")
    return SimpleNamespace(user=user, application=application)


def _without_language_selector(html: str) -> str:
    return LANGUAGE_SELECTOR_RE.sub("", html)


class LanguageEndpointTests(unittest.TestCase):
    def setUp(self):
        self.bot_patcher = patch.object(Website, "bot", _fake_bot())
        self.bot_patcher.start()
        self.addCleanup(self.bot_patcher.stop)
        Website.app.config.update(TESTING=True)
        self.client = Website.app.test_client()

    def test_anonymous_switch_persists_in_session(self):
        response = self.client.post(
            "/api/language",
            data={"lang": "en", "next": "/privacy-policy?from=home#details"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"],
            "/privacy-policy?from=home#details",
        )
        with self.client.session_transaction() as current_session:
            self.assertEqual(current_session["lang"], "en")

        page = self.client.get("/privacy-policy")
        self.assertIn('<html lang="en">', page.get_data(as_text=True))
        self.assertEqual(Website._i18n.current_locale(), Website._i18n.DEFAULT_LOCALE)

    def test_authenticated_switch_updates_global_user_locale(self):
        with self.client.session_transaction() as current_session:
            current_session["panel_user"] = {"id": "42"}
        with patch.object(Website._i18n, "set_user_locale", return_value=True) as save:
            response = self.client.post(
                "/api/language",
                data={"lang": "en", "next": "/panel/"},
            )
        self.assertEqual(response.status_code, 302)
        save.assert_called_once_with(42, "en")

    def test_failed_preference_write_keeps_session_and_logs(self):
        with self.client.session_transaction() as current_session:
            current_session["panel_user"] = {"id": "42"}
        with (
            patch.object(Website._i18n, "set_user_locale", return_value=False),
            patch.object(Website, "log") as log,
        ):
            response = self.client.post(
                "/api/language",
                data={"lang": "en", "next": "/panel/"},
            )
        self.assertEqual(response.status_code, 302)
        log.assert_called_once()
        with self.client.session_transaction() as current_session:
            self.assertEqual(current_session["lang"], "en")

    def test_query_language_is_web_only(self):
        with self.client.session_transaction() as current_session:
            current_session["panel_user"] = {"id": "42"}
        with patch.object(Website._i18n, "set_user_locale") as save:
            response = self.client.get("/privacy-policy?lang=en")
        self.assertEqual(response.status_code, 200)
        save.assert_not_called()
        with self.client.session_transaction() as current_session:
            self.assertEqual(current_session["lang"], "en")

    def test_japanese_switch_persists_in_session(self):
        response = self.client.post(
            "/api/language", data={"lang": "ja", "next": "/"})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as current_session:
            self.assertEqual(current_session["lang"], "ja")
        page = self.client.get("/")
        html = page.get_data(as_text=True)
        self.assertIn('<html lang="ja">', html)
        self.assertIn(">日本語<", html)

    def test_invalid_language_returns_400_without_changing_session(self):
        response = self.client.post(
            "/api/language", data={"lang": "fr", "next": "/"})
        self.assertEqual(response.status_code, 400)
        with self.client.session_transaction() as current_session:
            self.assertNotIn("lang", current_session)

    def test_safe_next_preserves_verification_and_submission_tokens(self):
        for target in (
            "/server-verify?auth_token=verify-token#captcha",
            "/contribute-feed-grass?token=submit-token#editor",
        ):
            with self.subTest(target=target):
                response = self.client.post(
                    "/api/language",
                    data={"lang": "en", "next": target},
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], target)

    def test_external_and_protocol_relative_redirects_are_rejected(self):
        for target in (
            "",
            "https://example.com/steal",
            "//example.com/steal",
            "/\\example.com/steal",
        ):
            with self.subTest(target=target):
                response = self.client.post(
                    "/api/language",
                    data={"lang": "en", "next": target},
                )
                self.assertEqual(response.status_code, 400)

    def test_missing_next_defaults_to_home(self):
        response = self.client.post("/api/language", data={"lang": "en"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")


class CoreTemplateTests(unittest.TestCase):
    def setUp(self):
        self.bot_patcher = patch.object(Website, "bot", _fake_bot())
        self.bot_patcher.start()
        self.addCleanup(self.bot_patcher.stop)
        Website.app.config.update(TESTING=True)
        self.client = Website.app.test_client()

    def test_all_core_templates_include_shared_selector_and_bootstrap(self):
        for name in CORE_TEMPLATES:
            source = (DISCORD_DIR / "templates" / name).read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertIn("_language_selector.html", source)
                self.assertIn("_i18n_bootstrap.html", source)

    def test_i18n_helper_loads_before_synchronous_panel_script(self):
        bootstrap = (DISCORD_DIR / "templates" / "_i18n_bootstrap.html").read_text(
            encoding="utf-8")
        panel = (DISCORD_DIR / "templates" / "panel_guild.html").read_text(
            encoding="utf-8")
        self.assertNotIn("defer", bootstrap)
        self.assertLess(
            panel.index("_i18n_bootstrap.html"),
            panel.index("js/panel.js"),
        )

    def test_english_home_and_legal_pages_have_no_unintended_cjk(self):
        for path in ("/?lang=en", "/privacy-policy?lang=en", "/terms-of-service?lang=en"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertIn('<html lang="en">', html)
                self.assertIn(">繁體中文<", html)
                self.assertIn(">English<", html)
                self.assertIsNone(CJK_RE.search(_without_language_selector(html)))

    def test_english_verification_page_title_catalog_and_captcha_locale(self):
        with Website.app.test_request_context(
            "/server-verify?auth_token=verify-token&lang=en"):
            Website.app.preprocess_request()
            html = render_template(
                "ServerVerify.html",
                bot=Website.bot,
                guild_name="Example Guild",
                captcha_type="turnstile",
                site_key_turnstile="public-site-key",
                site_key_recaptcha="public-site-key",
                gtag="",
                error=None,
            )
        self.assertIn("TestBot | Web Verification", html)
        self.assertIn("Example Guild Verification", html)
        self.assertIn("api.js?language=en", html)
        self.assertIsNone(CJK_RE.search(_without_language_selector(html)))

    def test_japanese_home_legal_and_verification_pages(self):
        pages = {
            "/?lang=ja": "Discordボット",
            "/privacy-policy?lang=ja": "プライバシー ポリシー",
            "/terms-of-service?lang=ja": "利用規約",
        }
        for path, expected in pages.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertIn('<html lang="ja">', html)
                self.assertIn(">日本語<", html)
                self.assertIn(expected, html)

        with Website.app.test_request_context(
            "/server-verify?auth_token=verify-token&lang=ja"
        ):
            Website.app.preprocess_request()
            html = render_template(
                "ServerVerify.html",
                bot=Website.bot,
                guild_name="Example Guild",
                captcha_type="turnstile",
                site_key_turnstile="public-site-key",
                site_key_recaptcha="public-site-key",
                gtag="",
                error=None,
            )
        self.assertIn('<html lang="ja">', html)
        self.assertIn("TestBot | Web認証", html)
        self.assertIn("api.js?language=ja", html)
        self.assertIn("api.js?hl=ja", html)

    def test_language_selector_initial_next_keeps_query(self):
        response = self.client.get("/privacy-policy?lang=en&source=test")
        html = response.get_data(as_text=True)
        self.assertIn(
            'name="next" value="/privacy-policy?lang=en&amp;source=test"',
            html,
        )


class DocumentationLocaleTests(unittest.TestCase):
    def test_all_english_manifest_sections_load(self):
        docs_dir = DISCORD_DIR / "docs"
        manifest = json.loads(
            (docs_dir / "en" / "manifest.json").read_text(encoding="utf-8"))
        expected = sum(len(group.get("items", [])) for group in manifest["groups"])
        groups, sections = doc_markdown.load_docs_site(docs_dir, locale="en")
        self.assertTrue(groups)
        self.assertEqual(len(sections), expected)
        self.assertTrue(all(section["translated"] for section in sections))

    def test_all_japanese_manifest_sections_load_without_fallback(self):
        docs_dir = DISCORD_DIR / "docs"
        manifest = json.loads(
            (docs_dir / "ja" / "manifest.json").read_text(encoding="utf-8"))
        expected = sum(len(group.get("items", [])) for group in manifest["groups"])
        self.assertEqual(expected, 36)
        groups, sections = doc_markdown.load_docs_site(docs_dir, locale="ja")
        self.assertTrue(groups)
        self.assertEqual(len(sections), 36)
        self.assertTrue(all(section["translated"] for section in sections))

    def test_missing_english_section_falls_back_one_section_at_a_time(self):
        manifest = {
            "groups": [{
                "title": "Guides",
                "items": [{"id": "intro", "label": "Introduction", "file": "intro"}],
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            docs_dir = Path(temporary)
            for locale in ("zh-TW", "en"):
                (docs_dir / locale / "sections").mkdir(parents=True)
                (docs_dir / locale / "manifest.json").write_text(
                    json.dumps(manifest), encoding="utf-8")
            (docs_dir / "zh-TW" / "sections" / "intro.md").write_text(
                "# 原文\n\nFallback body", encoding="utf-8")

            _groups, sections = doc_markdown.load_docs_site(docs_dir, locale="en")

        self.assertEqual(len(sections), 1)
        self.assertFalse(sections[0]["translated"])
        self.assertIn("Fallback body", sections[0]["html"])


if __name__ == "__main__":
    unittest.main()
