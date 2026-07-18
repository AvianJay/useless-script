import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import discord
import FixLink


DIRECT_URL = (
    "https://www.threads.com/@tzu_tiao_pi_wai/post/Da2rw83jhc6"
    "?xmt=AQG0jWsCiWGUom59k3CHDsrqSAQdCTKn0epI1VsHoxeDHKLmvgnnq6qxqwuD4ENn6RZaH0s&slof=1"
)
CLEAN_DIRECT_URL = "https://www.threads.com/@tzu_tiao_pi_wai/post/Da2rw83jhc6"
SHARE_URL = "https://www.threads.com/share/_li58Zbrm/"
FZ_DIRECT_URL = "https://fzthreads.com/@tzu_tiao_pi_wai/post/Da2rw83jhc6"
FZ_SHARE_URL = "https://fzthreads.com/share/_li58Zbrm/"
FIXEMBED_DIRECT_URL = (
    "https://fixembed.app/embed?"
    "url=https%3A%2F%2Fwww.threads.com%2F%40tzu_tiao_pi_wai%2Fpost%2FDa2rw83jhc6&v=154"
)


def custom_platform(**overrides):
    raw = {
        "id": "custom1234567890",
        "name": "Example",
        "origins": ["social.example.com"],
        "path_prefixes": ["/post/"],
        "keep_query_keys": ["id"],
        "fixer": {
            "name": "ExampleFix",
            "endpoint": "https://fix.example.com/embed",
            "source_param": "url",
            "static_query": {"v": "1"},
        },
    }
    raw.update(overrides)
    return FixLink.normalize_custom_platform(raw)


class URLHelperTests(unittest.TestCase):
    def test_extract_urls_ignores_angle_bracket_opt_out(self):
        content = f"<{DIRECT_URL}> {DIRECT_URL}."
        extracted = FixLink.extract_urls(content)
        self.assertEqual([item.url for item in extracted], [DIRECT_URL])

    def test_threads_direct_parser_and_exact_fixers(self):
        parsed = FixLink.parse_threads_url(DIRECT_URL)
        self.assertEqual(parsed["username"], "tzu_tiao_pi_wai")
        self.assertEqual(parsed["post_id"], "Da2rw83jhc6")
        self.assertEqual(
            FixLink.canonical_threads_url(DIRECT_URL, remove_tracker=True),
            CLEAN_DIRECT_URL,
        )
        self.assertEqual(
            FixLink.build_fzthreads_url(DIRECT_URL, remove_tracker=True),
            FZ_DIRECT_URL,
        )
        self.assertEqual(FixLink.build_fixembed_url(CLEAN_DIRECT_URL), FIXEMBED_DIRECT_URL)

    def test_threads_share_builds_fzthreads_without_resolution(self):
        parsed = FixLink.parse_threads_url(SHARE_URL)
        self.assertEqual(parsed["kind"], "share")
        self.assertEqual(
            FixLink.build_fzthreads_url(SHARE_URL, remove_tracker=True),
            FZ_SHARE_URL,
        )
        self.assertIsNone(FixLink.canonical_threads_url(SHARE_URL, remove_tracker=True))

    def test_threads_hosts_accept_www_and_net_but_reject_lookalikes(self):
        self.assertIsNotNone(FixLink.parse_threads_url("https://threads.net/@user/post/ABC_123"))
        self.assertIsNotNone(FixLink.parse_threads_url("https://www.threads.com/@user/post/ABC-123"))
        self.assertIsNone(FixLink.parse_threads_url("https://threads.com.example/@user/post/ABC123"))
        self.assertIsNone(FixLink.parse_threads_url("https://example.com/@user/post/ABC123"))

    def test_custom_query_fixer_encodes_source_once(self):
        platform = custom_platform()
        source = FixLink.build_custom_source_url(
            "https://social.example.com/post/1?id=42&utm_source=test",
            remove_tracker=True,
            keep_query_keys=platform["keep_query_keys"],
        )
        self.assertEqual(source, "https://social.example.com/post/1?id=42")
        fixed = FixLink.build_custom_fixer_url(platform, source)
        parsed = urlsplit(fixed)
        query = parse_qs(parsed.query)
        self.assertEqual(query["v"], ["1"])
        self.assertEqual(query["url"], [source])
        self.assertNotIn("%25", parsed.query)

    def test_custom_query_is_preserved_when_keep_list_is_empty(self):
        source = FixLink.build_custom_source_url(
            "https://social.example.com/post/1?id=42&utm_source=test#section",
            remove_tracker=True,
            keep_query_keys=[],
        )
        self.assertEqual(
            source,
            "https://social.example.com/post/1?id=42&utm_source=test#section",
        )

    def test_custom_endpoint_validation_rejects_unsafe_shapes(self):
        invalid_endpoints = [
            "http://fix.example.com/embed",
            "https://127.0.0.1/embed",
            "https://localhost/embed",
            "https://user:pass@fix.example.com/embed",
            "https://fix.example.com:8443/embed",
            "https://fix.example.com/embed?url=x",
            "https://fix.example.com/embed#fragment",
        ]
        for endpoint in invalid_endpoints:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                FixLink.normalize_custom_fixer_fields(
                    name="Fix",
                    endpoint=endpoint,
                    source_param="url",
                )

    def test_custom_source_rejects_threads_and_duplicate_match(self):
        with self.assertRaisesRegex(ValueError, "Threads"):
            source = FixLink.normalize_custom_source_fields(
                name="Bad",
                origins=["www.threads.com"],
                path_prefixes=["/post/"],
            )
            FixLink.validate_custom_source_conflicts(source, [])

        existing = [custom_platform()]
        source = FixLink.normalize_custom_source_fields(
            name="Other",
            origins=["social.example.com"],
            path_prefixes=["/post/"],
        )
        with self.assertRaisesRegex(ValueError, "相同"):
            FixLink.validate_custom_source_conflicts(source, existing)

    def test_custom_source_rejects_other_builtin_platforms(self):
        with self.assertRaisesRegex(ValueError, "Twitter"):
            source = FixLink.normalize_custom_source_fields(
                name="Bad Twitter",
                origins=["www.x.com"],
                path_prefixes=["/status/"],
            )
            FixLink.validate_custom_source_conflicts(source, [])

        with self.assertRaisesRegex(ValueError, "Newgrounds"):
            source = FixLink.normalize_custom_source_fields(
                name="Bad Newgrounds",
                origins=["artist.newgrounds.com"],
                path_prefixes=["/art/"],
            )
            FixLink.validate_custom_source_conflicts(source, [])

    def test_custom_matching_uses_longest_path_prefix(self):
        broad = custom_platform()
        specific = custom_platform(
            id="custom0987654321",
            name="Specific",
            path_prefixes=["/post/special/"],
            fixer={
                "name": "SpecificFix",
                "endpoint": "https://specific.example.com/embed",
                "source_param": "url",
                "static_query": {},
            },
        )
        matched = FixLink.find_custom_platform(
            "https://social.example.com/post/special/123",
            [broad, specific],
        )
        self.assertEqual(matched["name"], "Specific")

    def test_config_normalizer_fills_defaults_and_skips_invalid_custom_items(self):
        config = FixLink.normalize_fixlink_config(
            {
                "enabled": True,
                "preferred_fixers": {"Threads": "FixEmbed"},
                "custom_platforms": [
                    custom_platform(),
                    {"name": "Broken", "origins": ["bad"], "path_prefixes": ["/"]},
                ],
            }
        )
        self.assertTrue(config["enabled"])
        self.assertFalse(config["remove_tracker"])
        self.assertEqual(config["preferred_fixers"]["Threads"], "FixEmbed")
        self.assertEqual(config["preferred_fixers"]["Twitter"], "FxTwitter")
        self.assertEqual(set(config["preferred_fixers"]), set(FixLink.supported_platforms))
        self.assertEqual(len(config["custom_platforms"]), 1)

    def test_chunk_lines_respects_discord_limit(self):
        chunks = FixLink.chunk_lines(["a" * 1000, "b" * 1000, "c" * 100])
        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= FixLink.MAX_REPLY_CHUNK_LENGTH for chunk in chunks))


class MatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = FixLink.FixLink(FixLink.bot)

    async def test_direct_match_contains_both_fixers_and_author(self):
        config = FixLink.normalize_fixlink_config(
            {"enabled": True, "remove_tracker": True}
        )
        matches = await self.cog.match_message(DIRECT_URL, config)
        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertEqual(match.source_url, CLEAN_DIRECT_URL)
        self.assertEqual(dict(match.fixers)["FzThreads"], FZ_DIRECT_URL)
        self.assertEqual(dict(match.fixers)["FixEmbed"], FIXEMBED_DIRECT_URL)
        self.assertEqual(match.username, "tzu_tiao_pi_wai")
        self.assertEqual(match.primary_url, FZ_DIRECT_URL)

    async def test_builtin_platform_table(self):
        cases = [
            ("Twitter", "https://x.com/discord/status/1234567890?ref_src=test", "fxtwitter.com"),
            ("Instagram", "https://www.instagram.com/p/ABC_123/?img_index=2&utm_source=test", "vxinstagram.com"),
            ("TikTok", "https://www.tiktok.com/@user/video/1234567890?is_from_webapp=1", "tnktok.com"),
            ("Reddit", "https://www.reddit.com/r/test/comments/abc123/title/?utm_source=share", "vxreddit.com"),
            ("Facebook", "https://www.facebook.com/user/posts/pfbid123/?utm_source=test", "facebed.com"),
            ("Bilibili", "https://www.bilibili.com/video/BV1abc?p=2&spm_id_from=333", "vxbilibili.com"),
            ("Pixiv", "https://www.pixiv.net/artworks/12345678?utm_source=test", "phixiv.net"),
            ("Pinterest", "https://www.pinterest.com/pin/123456789/?utm_source=test", "pinterestez.com"),
            ("YouTube", "https://www.youtube.com/watch?v=abc_DEF-12&t=30&utm_source=test", "koutube.com"),
            ("Twitch", "https://www.twitch.tv/example/clip/FancySlug?filter=clips", "fxtwitch.seria.moe"),
            ("Bluesky", "https://bsky.app/profile/example.com/post/3abc123?ref=share", "fxbsky.app"),
            ("Spotify", "https://open.spotify.com/track/abc123?si=tracking", "fxspotify.com"),
            ("DeviantArt", "https://www.deviantart.com/artist/art/example-123456", "fixdeviantart.com"),
            ("Imgur", "https://imgur.com/gallery/abc123?utm_source=test", "imgurez.com"),
            ("Weibo", "https://weibo.com/1234567890/AbCdEf?refer_flag=test", "weiboez.com"),
            ("Newgrounds", "https://artist.newgrounds.com/art/view/artist/example", "fixnewgrounds.com"),
            ("PTT", "https://www.ptt.cc/bbs/Test/M.1234567890.A.123.html", "fxptt.seria.moe"),
            ("Roblox", "https://www.roblox.com/games/123456/example?privateServerLinkCode=test", "fixroblox.com"),
            ("Fur Affinity", "https://www.furaffinity.net/view/12345678/?utm_source=test", "xfuraffinity.net"),
        ]
        config = FixLink.normalize_fixlink_config(
            {"enabled": True, "remove_tracker": True}
        )
        for platform_name, source_url, primary_host in cases:
            with self.subTest(platform=platform_name):
                matches = await self.cog.match_message(source_url, config)
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0].platform_name, platform_name)
                self.assertEqual(urlsplit(matches[0].primary_url).hostname, primary_host)
                self.assertNotIn("utm_", matches[0].source_url)

        twitter = await self.cog.match_message(
            "https://x.com/discord/status/1234567890",
            config,
        )
        self.assertEqual(twitter[0].username, "discord")
        self.assertEqual(twitter[0].profile_url, "https://x.com/discord")

    async def test_builtin_query_retention_and_preferred_fixer(self):
        config = FixLink.normalize_fixlink_config(
            {
                "enabled": True,
                "remove_tracker": True,
                "preferred_fixers": {"Twitter": "VxTwitter"},
            }
        )
        content = (
            "https://x.com/discord/status/1234567890?ref_src=test "
            "https://www.youtube.com/watch?v=abc_DEF-12&t=30&utm_source=test "
            "https://www.bilibili.com/video/BV1abc?p=2&spm_id_from=333"
        )
        matches = await self.cog.match_message(content, config)
        self.assertEqual(urlsplit(matches[0].primary_url).hostname, "vxtwitter.com")
        self.assertEqual(parse_qs(urlsplit(matches[1].primary_url).query), {"v": ["abc_DEF-12"], "t": ["30"]})
        self.assertEqual(parse_qs(urlsplit(matches[2].primary_url).query), {"p": ["2"]})

    async def test_builtin_keeps_full_query_when_tracker_removal_is_disabled(self):
        source = "https://x.com/discord/status/1234567890?ref_src=test#fragment"
        config = FixLink.normalize_fixlink_config(
            {"enabled": True, "remove_tracker": False}
        )
        matches = await self.cog.match_message(source, config)
        self.assertEqual(matches[0].source_url, source)
        self.assertEqual(
            matches[0].primary_url,
            "https://fxtwitter.com/discord/status/1234567890?ref_src=test#fragment",
        )

    async def test_builtin_rejects_unsupported_shapes_and_honors_disabled(self):
        config = FixLink.normalize_fixlink_config(
            {"enabled": True, "disabled_platforms": ["Twitter"]}
        )
        matches = await self.cog.match_message(
            "https://x.com/discord/status/1234567890 "
            "https://www.youtube.com/watch?list=missing-video "
            "https://www.instagram.com/example/",
            config,
        )
        self.assertEqual(matches, [])

    async def test_short_reddit_and_bilibili_use_compatible_fixer(self):
        config = FixLink.normalize_fixlink_config(
            {"enabled": True, "remove_tracker": True}
        )
        matches = await self.cog.match_message(
            "https://redd.it/abc123 https://b23.tv/AbCd12",
            config,
        )
        self.assertEqual(matches[0].fixers, (("FixReddit", "https://rxddit.com/abc123"),))
        self.assertEqual(matches[0].primary_url, "https://rxddit.com/abc123")
        self.assertEqual(matches[1].primary_url, "https://vxb23.tv/AbCd12")

    async def test_share_resolution_adds_fixembed_and_uses_cache(self):
        config = FixLink.normalize_fixlink_config(
            {
                "enabled": True,
                "remove_tracker": True,
                "preferred_fixers": {"Threads": "FixEmbed"},
            }
        )
        with patch.object(
            self.cog,
            "_fetch_threads_redirect",
            AsyncMock(return_value=DIRECT_URL),
        ) as fetch:
            first = await self.cog.match_message(SHARE_URL, config)
            second = await self.cog.match_message(SHARE_URL, config)
        self.assertEqual(fetch.await_count, 1)
        self.assertEqual(first[0].source_url, CLEAN_DIRECT_URL)
        self.assertEqual(dict(first[0].fixers)["FzThreads"], FZ_SHARE_URL)
        self.assertEqual(dict(first[0].fixers)["FixEmbed"], FIXEMBED_DIRECT_URL)
        self.assertEqual(first[0].primary_url, FIXEMBED_DIRECT_URL)
        self.assertEqual(first, second)

    async def test_parallel_share_resolution_is_coalesced(self):
        async def delayed_fetch(url):
            await asyncio.sleep(0)
            return DIRECT_URL

        with patch.object(
            self.cog,
            "_fetch_threads_redirect",
            AsyncMock(side_effect=delayed_fetch),
        ) as fetch:
            results = await asyncio.gather(
                self.cog.resolve_threads_share(SHARE_URL),
                self.cog.resolve_threads_share(SHARE_URL),
            )
        self.assertEqual(results, [DIRECT_URL, DIRECT_URL])
        self.assertEqual(fetch.await_count, 1)

    async def test_share_failure_keeps_fzthreads_only(self):
        config = FixLink.normalize_fixlink_config(
            {
                "enabled": True,
                "remove_tracker": True,
                "preferred_fixers": {"Threads": "FixEmbed"},
            }
        )
        with patch.object(
            self.cog,
            "_fetch_threads_redirect",
            AsyncMock(return_value=None),
        ):
            matches = await self.cog.match_message(SHARE_URL, config)
        self.assertEqual(matches[0].fixers, (("FzThreads", FZ_SHARE_URL),))
        self.assertEqual(matches[0].primary_url, FZ_SHARE_URL)

    async def test_non_threads_redirect_is_rejected(self):
        with patch.object(
            self.cog,
            "_fetch_threads_redirect",
            AsyncMock(return_value="https://evil.example/post/1"),
        ):
            resolved = await self.cog.resolve_threads_share(SHARE_URL)
        self.assertIsNone(resolved)

    async def test_custom_platform_match(self):
        platform = custom_platform()
        config = FixLink.normalize_fixlink_config(
            {
                "enabled": True,
                "remove_tracker": True,
                "custom_platforms": [platform],
            }
        )
        matches = await self.cog.match_message(
            "See https://social.example.com/post/1?id=42&utm_source=test",
            config,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].platform_name, "Example")
        fixed_query = parse_qs(urlsplit(matches[0].primary_url).query)
        self.assertEqual(
            fixed_query["url"],
            ["https://social.example.com/post/1?id=42"],
        )


class FakeResponseContext:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, head_context, get_context):
        self.head = MagicMock(return_value=head_context)
        self.get = MagicMock(return_value=get_context)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class RedirectFetcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_head_success_does_not_issue_get(self):
        cog = FixLink.FixLink(FixLink.bot)
        session = FakeSession(
            FakeResponseContext(SimpleNamespace(status=200, url=DIRECT_URL)),
            FakeResponseContext(SimpleNamespace(status=200, url=DIRECT_URL)),
        )
        with patch.object(FixLink.aiohttp, "ClientSession", return_value=session):
            resolved = await cog._fetch_threads_redirect(SHARE_URL)
        self.assertEqual(resolved, DIRECT_URL)
        session.get.assert_not_called()

    async def test_head_not_supported_falls_back_to_get(self):
        cog = FixLink.FixLink(FixLink.bot)
        session = FakeSession(
            FakeResponseContext(SimpleNamespace(status=405, url=SHARE_URL)),
            FakeResponseContext(SimpleNamespace(status=200, url=DIRECT_URL)),
        )
        with patch.object(FixLink.aiohttp, "ClientSession", return_value=session):
            resolved = await cog._fetch_threads_redirect(SHARE_URL)
        self.assertEqual(resolved, DIRECT_URL)
        session.get.assert_called_once()

    async def test_timeout_returns_none(self):
        cog = FixLink.FixLink(FixLink.bot)
        session = FakeSession(
            FakeResponseContext(error=asyncio.TimeoutError()),
            FakeResponseContext(SimpleNamespace(status=200, url=DIRECT_URL)),
        )
        with patch.object(FixLink.aiohttp, "ClientSession", return_value=session):
            resolved = await cog._fetch_threads_redirect(SHARE_URL)
        self.assertIsNone(resolved)


def http_exception(status=403):
    response = SimpleNamespace(status=status, reason="Forbidden", headers={})
    return discord.HTTPException(response, {"message": "failed", "code": 0})


class NormalReplyPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = FixLink.FixLink(FixLink.bot)
        self.match = FixLink.LinkMatch(
            platform_key="Threads",
            platform_name="Threads",
            source_url=CLEAN_DIRECT_URL,
            start=0,
            end=len(DIRECT_URL),
            fixers=(("FzThreads", FZ_DIRECT_URL),),
            primary_url=FZ_DIRECT_URL,
        )

    async def test_preview_suppresses_original_embed(self):
        sent_channel = SimpleNamespace(
            fetch_message=AsyncMock(return_value=SimpleNamespace(embeds=[object()]))
        )
        sent = SimpleNamespace(id=11, channel=sent_channel, delete=AsyncMock())
        message = SimpleNamespace(
            reply=AsyncMock(return_value=sent),
            channel=SimpleNamespace(send=AsyncMock()),
            edit=AsyncMock(),
            guild=SimpleNamespace(id=2),
            id=1,
        )
        with (
            patch.object(self.cog, "_can_send", return_value=True),
            patch.object(FixLink.asyncio, "sleep", new=AsyncMock()) as sleep,
        ):
            await self.cog.send_normal_reply(message, [self.match])

        sleep.assert_awaited_once_with(FixLink.EMBED_PREVIEW_DELAY_SECONDS)
        sent.delete.assert_not_awaited()
        message.edit.assert_awaited_once_with(suppress=True)

    async def test_missing_preview_deletes_repair_message(self):
        sent_channel = SimpleNamespace(fetch_message=AsyncMock(return_value=SimpleNamespace(embeds=[])))
        sent = SimpleNamespace(id=11, channel=sent_channel, delete=AsyncMock())
        message = SimpleNamespace(
            reply=AsyncMock(return_value=sent),
            channel=SimpleNamespace(send=AsyncMock()),
            edit=AsyncMock(),
            guild=SimpleNamespace(id=2),
            id=1,
        )
        with (
            patch.object(self.cog, "_can_send", return_value=True),
            patch.object(FixLink.asyncio, "sleep", new=AsyncMock()),
        ):
            await self.cog.send_normal_reply(message, [self.match])

        sent.delete.assert_awaited_once_with()
        message.edit.assert_not_awaited()


class DeleteButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_button_only_allows_original_author(self):
        button = FixLink.FixLinkDeleteButton(123)
        view = FixLink.FixLinkDeleteView(123)
        self.assertTrue(button.is_persistent())
        self.assertTrue(view.is_persistent())

        allowed = SimpleNamespace(user=SimpleNamespace(id=123))
        self.assertTrue(await button.interaction_check(allowed))

        denied = SimpleNamespace(
            user=SimpleNamespace(id=456),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        self.assertFalse(await button.interaction_check(denied))
        denied.response.send_message.assert_awaited_once_with(
            "只有原訊息作者可以刪除這則訊息。",
            ephemeral=True,
        )

    async def test_button_deletes_webhook_message(self):
        button = FixLink.FixLinkDeleteButton(123)
        interaction = SimpleNamespace(
            message=SimpleNamespace(delete=AsyncMock()),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await button.callback(interaction)
        interaction.response.defer.assert_awaited_once_with()
        interaction.message.delete.assert_awaited_once_with()


class SettingsViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_builtin_platform_selector_and_component_layout(self):
        cog = FixLink.FixLink(FixLink.bot)
        config = FixLink.normalize_fixlink_config({"enabled": True})
        interaction = SimpleNamespace(guild_id=2, user=SimpleNamespace(id=123))
        with patch.object(cog, "get_config", return_value=config):
            view = FixLink.FixLinkSettingsView(cog, interaction)

        self.assertEqual(len(view.builtin_select.options), len(FixLink.supported_platforms))
        self.assertLessEqual(len(view.builtin_select.options), 25)
        view.selected_builtin_name = "Twitter"
        view.refresh_components()
        self.assertEqual(
            {option.value for option in view.preferred_select.options},
            {"FxTwitter", "VxTwitter"},
        )
        self.assertEqual(view.toggle_builtin.label, "停用 Twitter")
        row_counts = {}
        for child in view.children:
            row_counts[child.row] = row_counts.get(child.row, 0) + 1
        self.assertTrue(all(count <= 5 for count in row_counts.values()))


class WebhookTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = FixLink.FixLink(FixLink.bot)
        self.match = FixLink.LinkMatch(
            platform_key="Threads",
            platform_name="Threads",
            source_url=CLEAN_DIRECT_URL,
            start=0,
            end=len(DIRECT_URL),
            fixers=(("FzThreads", FZ_DIRECT_URL),),
            primary_url=FZ_DIRECT_URL,
        )

    async def test_webhook_send_happens_before_original_delete(self):
        events = []
        webhook = SimpleNamespace()
        clone = SimpleNamespace(id=10, delete=AsyncMock())

        async def fetch_message(message_id, **kwargs):
            events.append(("fetch", message_id))
            return SimpleNamespace(embeds=[object()])

        async def finalize(**kwargs):
            events.append(("finalize", kwargs))
            return clone

        webhook.fetch_message = AsyncMock(side_effect=fetch_message)
        clone.edit = AsyncMock(side_effect=finalize)

        async def send_clone(message, content):
            events.append(("send", content))
            return webhook, clone

        async def delete_original():
            events.append(("delete", None))

        message = SimpleNamespace(
            content=DIRECT_URL,
            delete=AsyncMock(side_effect=delete_original),
            id=1,
            guild=SimpleNamespace(id=2),
            channel=SimpleNamespace(),
            author=SimpleNamespace(id=123),
        )
        with (
            patch.object(self.cog, "_can_webhook_replace", return_value=True),
            patch.object(self.cog, "_send_webhook_clone", side_effect=send_clone),
            patch.object(FixLink.asyncio, "sleep", new=AsyncMock()),
            patch.object(FixLink, "get_trash_button_emoji", new=AsyncMock(return_value="🗑️")),
        ):
            replaced = await self.cog.replace_with_webhook(message, [self.match])
        self.assertTrue(replaced)
        self.assertEqual(events[0], ("send", FZ_DIRECT_URL))
        self.assertEqual(events[1], ("fetch", 10))
        self.assertEqual(events[2][0], "finalize")
        self.assertEqual(events[3][0], "delete")
        self.assertNotIn("content", events[2][1])
        clone.delete.assert_not_awaited()

    async def test_missing_webhook_preview_restores_original_content(self):
        webhook = SimpleNamespace(
            fetch_message=AsyncMock(return_value=SimpleNamespace(embeds=[]))
        )
        clone = SimpleNamespace(id=10, delete=AsyncMock())
        clone.edit = AsyncMock(return_value=clone)
        message = SimpleNamespace(
            content=DIRECT_URL,
            delete=AsyncMock(),
            id=1,
            guild=SimpleNamespace(id=2),
            channel=SimpleNamespace(),
            author=SimpleNamespace(id=123),
        )
        with (
            patch.object(self.cog, "_can_webhook_replace", return_value=True),
            patch.object(self.cog, "_send_webhook_clone", return_value=(webhook, clone)),
            patch.object(FixLink.asyncio, "sleep", new=AsyncMock()),
            patch.object(FixLink, "get_trash_button_emoji", new=AsyncMock(return_value="🗑️")),
        ):
            replaced = await self.cog.replace_with_webhook(message, [self.match])

        self.assertTrue(replaced)
        edit_kwargs = clone.edit.await_args.kwargs
        self.assertEqual(edit_kwargs["content"], DIRECT_URL)
        self.assertIsInstance(edit_kwargs["view"], FixLink.FixLinkDeleteView)
        self.assertIsInstance(edit_kwargs["allowed_mentions"], discord.AllowedMentions)
        message.delete.assert_awaited_once_with()

    async def test_delete_failure_rolls_back_clone(self):
        events = []

        async def rollback():
            events.append("rollback")

        webhook = SimpleNamespace(
            fetch_message=AsyncMock(return_value=SimpleNamespace(embeds=[object()]))
        )
        clone = SimpleNamespace(id=10, delete=AsyncMock(side_effect=rollback))
        clone.edit = AsyncMock(return_value=clone)

        async def send_clone(message, content):
            events.append("send")
            return webhook, clone

        message = SimpleNamespace(
            content=DIRECT_URL,
            delete=AsyncMock(side_effect=http_exception()),
            id=1,
            guild=SimpleNamespace(id=2),
            channel=SimpleNamespace(),
            author=SimpleNamespace(id=123),
        )
        with (
            patch.object(self.cog, "_can_webhook_replace", return_value=True),
            patch.object(self.cog, "_send_webhook_clone", side_effect=send_clone),
            patch.object(FixLink.asyncio, "sleep", new=AsyncMock()),
            patch.object(FixLink, "get_trash_button_emoji", new=AsyncMock(return_value="🗑️")),
        ):
            replaced = await self.cog.replace_with_webhook(message, [self.match])
        self.assertFalse(replaced)
        self.assertEqual(events, ["send", "rollback"])


if __name__ == "__main__":
    unittest.main()
