import asyncio
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

from ai import AICommands, TOOL_USAGE_PROMPT


def png_bytes(color=(40, 80, 120)):
    output = io.BytesIO()
    Image.new("RGB", (24, 24), color).save(output, format="PNG")
    return output.getvalue()


class AIVisualMetadataTests(unittest.TestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())
        self.cog._get_visual_description_cache = Mock(return_value={})

    def test_message_metadata_includes_unicode_custom_sticker_and_reaction(self):
        custom_reaction = SimpleNamespace(
            emoji=SimpleNamespace(
                id=123,
                name="wave",
                animated=False,
                url="https://cdn.discordapp.com/emojis/123.png",
            ),
            count=4,
        )
        message = SimpleNamespace(
            content="好笑 😂 <:wave:123>",
            reactions=[custom_reaction],
            stickers=[
                SimpleNamespace(
                    id=456,
                    name="party",
                    format=SimpleNamespace(name="png"),
                    url="https://cdn.discordapp.com/stickers/456.png",
                )
            ],
        )

        metadata = self.cog._build_message_visual_metadata(message)

        by_key = {item.get("cache_key") or item.get("emoji"): item for item in metadata["items"]}
        self.assertEqual(by_key["😂"]["name"], "face with tears of joy")
        self.assertEqual(by_key["emoji:123"]["reaction_count"], 4)
        self.assertEqual(by_key["sticker:456"]["format"], "png")
        self.assertEqual(metadata["total_distinct"], 3)

    def test_visual_metadata_caps_distinct_items_at_eight(self):
        content = " ".join(f"<:e{i}:{1000 + i}>" for i in range(10))
        message = SimpleNamespace(content=content, reactions=[], stickers=[])

        metadata = self.cog._build_message_visual_metadata(message)

        self.assertEqual(len(metadata["items"]), 8)
        self.assertEqual(metadata["total_distinct"], 10)
        self.assertTrue(metadata["truncated"])

    def test_lottie_sticker_is_metadata_only(self):
        item = self.cog._build_sticker_metadata(
            SimpleNamespace(
                id=999,
                name="moving",
                format=SimpleNamespace(name="lottie"),
                url="https://cdn.discordapp.com/stickers/999.json",
            )
        )

        self.assertEqual(item["cache_key"], "sticker:999")
        self.assertEqual(item["format"], "lottie")
        self.assertFalse(item["analyzable"])


class AIVisualCacheDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "visual-cache.db")
        self.cog = AICommands(SimpleNamespace())
        self.connections = []

        def open_connection():
            connection = sqlite3.connect(self.db_path)
            self.connections.append(connection)
            return connection

        self.connection_patch = patch(
            "ai.get_db_connection",
            side_effect=open_connection,
        )
        self.connection_patch.start()

    def tearDown(self):
        self.connection_patch.stop()
        for connection in self.connections:
            connection.close()
        self.temp_dir.cleanup()

    def test_emoji_cache_persists_by_id(self):
        self.cog._set_visual_description_cache(
            [
                {
                    "cache_key": "emoji:123",
                    "asset_type": "custom_emoji",
                    "description": "一隻揮手的小動物",
                    "model": "openai",
                }
            ]
        )

        cached = self.cog._get_visual_description_cache(["emoji:123"])

        self.assertEqual(cached["emoji:123"]["description"], "一隻揮手的小動物")

    def test_profile_cache_expires_after_ttl(self):
        self.cog._set_visual_description_cache(
            [
                {
                    "cache_key": "profile:1:avatar:oldhash",
                    "asset_type": "avatar",
                    "description": "舊頭像",
                    "model": "openai",
                    "created_at": 1,
                }
            ]
        )

        with patch("ai.time.time", return_value=self.cog.VISUAL_DESCRIPTION_PROFILE_TTL_SECONDS + 10):
            cached = self.cog._get_visual_description_cache(["profile:1:avatar:oldhash"])

        self.assertEqual(cached, {})

    def test_new_profile_hash_replaces_old_hash_entry(self):
        self.cog._set_visual_description_cache(
            [
                {
                    "cache_key": "profile:1:avatar:oldhash",
                    "asset_type": "avatar",
                    "description": "舊頭像",
                    "model": "openai",
                }
            ]
        )
        self.cog._set_visual_description_cache(
            [
                {
                    "cache_key": "profile:1:avatar:newhash",
                    "asset_type": "avatar",
                    "description": "新頭像",
                    "model": "openai",
                }
            ]
        )

        cached = self.cog._get_visual_description_cache(
            ["profile:1:avatar:oldhash", "profile:1:avatar:newhash"]
        )

        self.assertNotIn("profile:1:avatar:oldhash", cached)
        self.assertEqual(cached["profile:1:avatar:newhash"]["description"], "新頭像")


class AIVisualAnalysisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())
        self.cache = {}
        self.cog._get_visual_description_cache = Mock(
            side_effect=lambda keys: {
                key: self.cache[key]
                for key in keys
                if key in self.cache
            }
        )

        def store(entries):
            for entry in entries:
                self.cache[entry["cache_key"]] = {
                    "description": entry["description"],
                    "model": entry["model"],
                }

        self.cog._set_visual_description_cache = Mock(side_effect=store)
        self.cog._fetch_discord_image_bytes = AsyncMock(return_value=(png_bytes(), None))
        self.cog._resolve_ai_billing_target = AsyncMock(
            return_value={
                "payer_id": 7,
                "payer_user": None,
                "display_name": "payer",
            }
        )
        self.cog._get_global_balance = Mock(return_value=1000.0)
        self.cog._charge_global_balance = Mock(return_value=(25.0, 975.0))
        self.cog._refund_global_balance = Mock(return_value=1000.0)
        self.cog._log_economy_transaction = Mock()
        self.cog._queue_economy_audit_log = Mock()
        self.tool_context = {
            "user": SimpleNamespace(id=11),
            "guild": None,
            "model": "openai",
        }

    async def test_concurrent_same_emoji_charges_and_analyzes_once(self):
        self.cog._generate_ai_completion = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"items":[{"label":1,"description":"揮手表情"}]}'
                        )
                    )
                ],
                model="openai",
            )
        )
        asset = {
            "cache_key": "emoji:123",
            "asset_type": "custom_emoji",
            "kind": "custom_emoji",
            "id": 123,
            "name": "wave",
            "url": "https://cdn.discordapp.com/emojis/123.png",
            "analyzable": True,
        }

        first, second = await asyncio.gather(
            self.cog._analyze_visual_assets([asset], self.tool_context),
            self.cog._analyze_visual_assets([asset], self.tool_context),
        )

        self.assertEqual(self.cog._generate_ai_completion.await_count, 1)
        self.assertEqual(self.cog._charge_global_balance.call_count, 1)
        self.assertEqual(self.cache["emoji:123"]["description"], "揮手表情")
        self.assertEqual(sorted([first["cost"], second["cost"]]), [0.0, 25.0])

    async def test_cached_and_uncached_assets_are_one_paid_batch(self):
        self.cache["emoji:1"] = {"description": "已快取", "model": "old"}
        self.cog._generate_ai_completion = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"items":[{"label":1,"description":"新描述"}]}'
                        )
                    )
                ],
                model="openai",
            )
        )
        assets = [
            {
                "cache_key": "emoji:1",
                "asset_type": "custom_emoji",
                "url": "https://cdn.discordapp.com/emojis/1.png",
                "analyzable": True,
            },
            {
                "cache_key": "sticker:2",
                "asset_type": "sticker",
                "url": "https://cdn.discordapp.com/stickers/2.png",
                "analyzable": True,
            },
        ]

        result = await self.cog._analyze_visual_assets(assets, self.tool_context)

        self.assertEqual(result["cost"], 25.0)
        self.assertEqual(self.cog._generate_ai_completion.await_count, 1)
        self.assertEqual({item["cache_key"] for item in result["items"]}, {"emoji:1", "sticker:2"})

    async def test_analysis_failure_refunds_and_does_not_cache(self):
        self.cog._generate_ai_completion = AsyncMock(side_effect=RuntimeError("provider down"))
        asset = {
            "cache_key": "emoji:404",
            "asset_type": "custom_emoji",
            "url": "https://cdn.discordapp.com/emojis/404.png",
            "analyzable": True,
        }

        result = await self.cog._analyze_visual_assets([asset], self.tool_context)

        self.assertIn("provider down", result["error"])
        self.assertEqual(result["refunded"], 25.0)
        self.cog._refund_global_balance.assert_called_once_with(7, 25.0)
        self.cog._set_visual_description_cache.assert_not_called()

    async def test_lottie_only_batch_is_free(self):
        result = await self.cog._analyze_visual_assets(
            [
                {
                    "cache_key": "sticker:9",
                    "asset_type": "sticker",
                    "format": "lottie",
                    "url": "https://cdn.discordapp.com/stickers/9.json",
                    "analyzable": False,
                }
            ],
            self.tool_context,
        )

        self.assertEqual(result["cost"], 0.0)
        self.assertTrue(result["items"][0]["analysis_unavailable"])
        self.cog._charge_global_balance.assert_not_called()


class AIProfileAndChannelToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_tool_combines_avatar_and_global_banner(self):
        bot = SimpleNamespace(
            fetch_user=AsyncMock(
                return_value=SimpleNamespace(
                    banner=SimpleNamespace(
                        url="https://cdn.discordapp.com/banners/5/bannerhash.png",
                        key="bannerhash",
                    )
                )
            )
        )
        cog = AICommands(bot)
        target = SimpleNamespace(
            id=5,
            display_avatar=SimpleNamespace(
                url="https://cdn.discordapp.com/avatars/5/avatarhash.png",
                key="avatarhash",
            ),
        )
        cog._resolve_visible_user_for_tool = AsyncMock(return_value=(target, None))
        cog._resolve_user_display = AsyncMock(return_value={"id": 5, "display_name": "five"})
        cog._analyze_visual_assets = AsyncMock(
            return_value={
                "items": [
                    {"asset_type": "avatar", "description": "頭像描述", "cached": False},
                    {"asset_type": "banner", "description": "橫幅描述", "cached": False},
                ],
                "cost": 25.0,
                "currency": "全域幣",
                "all_cached": False,
            }
        )

        result = await cog._tool_analyze_user_profile_media(
            {"user_id": 5, "media": "both"},
            {"user": SimpleNamespace(id=5), "guild": None},
        )

        assets = cog._analyze_visual_assets.await_args.args[0]
        self.assertEqual({asset["asset_type"] for asset in assets}, {"avatar", "banner"})
        self.assertEqual(assets[0]["cache_key"], "profile:5:avatar:avatarhash")
        self.assertEqual(result["media"]["avatar"]["description"], "頭像描述")
        self.assertEqual(result["media"]["banner"]["description"], "橫幅描述")
        self.assertEqual(result["cost"], 25.0)

    async def test_profile_tool_reports_missing_banner(self):
        bot = SimpleNamespace(fetch_user=AsyncMock(return_value=SimpleNamespace(banner=None)))
        cog = AICommands(bot)
        target = SimpleNamespace(id=5, display_avatar=None, avatar=None)
        cog._resolve_visible_user_for_tool = AsyncMock(return_value=(target, None))
        cog._resolve_user_display = AsyncMock(return_value={"id": 5})

        result = await cog._tool_analyze_user_profile_media(
            {"user_id": 5, "media": "banner"},
            {"user": SimpleNamespace(id=5), "guild": None},
        )

        self.assertFalse(result["media"]["banner"]["available"])
        self.assertEqual(result["cost"], 0.0)

    async def test_list_channels_omits_hidden_and_private_unjoined_thread(self):
        class FakeMember:
            def __init__(self, member_id):
                self.id = member_id
                self.guild = None

        class FakeChannel:
            def __init__(self, channel_id, name, guild, visibility):
                self.id = channel_id
                self.name = name
                self.guild = guild
                self.type = "text"
                self.mention = f"<#{channel_id}>"
                self.visibility = visibility

            def permissions_for(self, member):
                can_view = self.visibility.get(member.id, False)
                return SimpleNamespace(view_channel=can_view, read_message_history=can_view)

        class FakeThread(FakeChannel):
            def __init__(self, *args, members=None, **kwargs):
                super().__init__(*args, **kwargs)
                self.members = members or []
                self.parent = None
                self.archived = False
                self.locked = False

            def is_private(self):
                return True

        requester = FakeMember(1)
        bot_member = FakeMember(2)
        guild = SimpleNamespace(id=10, channels=[], threads=[])
        requester.guild = guild
        bot_member.guild = guild
        visible = FakeChannel(11, "visible", guild, {1: True, 2: True})
        hidden = FakeChannel(12, "secret", guild, {1: False, 2: True})
        private = FakeThread(13, "private", guild, {1: True, 2: True}, members=[bot_member])
        guild.channels = [visible, hidden]
        guild.threads = [private]
        guild.get_member = lambda member_id: requester if member_id == 1 else bot_member
        cog = AICommands(SimpleNamespace(user=SimpleNamespace(id=2)))
        cog._get_guild_bot_member = Mock(return_value=bot_member)

        with (
            patch("ai.discord.Member", FakeMember),
            patch("ai.discord.Thread", FakeThread),
        ):
            result = await cog._tool_list_channels(
                {"include_threads": True},
                {"user": requester, "guild": guild},
            )

        self.assertEqual(result["returned_count"], 1)
        self.assertEqual(result["channels"][0]["name"], "visible")
        self.assertNotIn("secret", str(result))
        self.assertNotIn("private", str(result))

    async def test_message_visual_tool_rejects_inaccessible_channel(self):
        cog = AICommands(SimpleNamespace(user=SimpleNamespace(id=2)))
        cog._resolve_message_for_visual_tool = AsyncMock(
            return_value=(None, None, "Current user is missing required channel permissions: view_channel")
        )

        result = await cog._tool_analyze_message_emojis(
            {"message_id": 1},
            {"user": SimpleNamespace(id=1)},
        )

        self.assertIn("missing required channel permissions", result["error"])


class AIToolPromptTests(unittest.TestCase):
    def test_new_tools_are_registered_and_context_rule_is_in_both_prompts(self):
        cog = AICommands(SimpleNamespace())
        tools = cog._build_ai_tools()
        tool_names = {tool["function"]["name"] for tool in tools}

        self.assertTrue(
            {"analyze_message_emojis", "analyze_user_profile_media", "list_channels"}.issubset(tool_names)
        )
        self.assertIn("上面在講什麼", TOOL_USAGE_PROMPT)
        emulated = cog._prepare_tool_emulation_messages([{"role": "user", "content": "上面在講什麼"}], tools)
        self.assertIn("always call read_channel first", emulated[0]["content"])

    def test_current_request_text_metadata_uses_cached_description(self):
        cog = AICommands(SimpleNamespace())
        cog._get_visual_description_cache = Mock(
            return_value={
                "emoji:123": {
                    "description": "一個揮手的黃色角色",
                    "model": "openai",
                }
            }
        )

        metadata = cog._build_text_visual_metadata("你好 <:wave:123>")
        context = cog._format_visual_metadata_for_context(metadata)

        self.assertIn("emoji:123", {item.get("cache_key") for item in metadata["items"]})
        self.assertIn("一個揮手的黃色角色", context)

    def test_channel_list_uses_extended_tool_budget(self):
        cog = AICommands(SimpleNamespace())
        self.assertEqual(
            cog._tool_result_max_length("list_channels"),
            cog.CHANNEL_LIST_TOOL_RESULT_MAX_LENGTH,
        )


if __name__ == "__main__":
    unittest.main()
