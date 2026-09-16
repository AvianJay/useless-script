import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import i18n  # noqa: E402
import UtilCommands as util  # noqa: E402

from tests.i18n_base import ZhTWLocaleMixin  # noqa: E402


class FakeEmoji:
    def __init__(self, id: int, name: str = "emoji", animated: bool = False):
        self.id = id
        self.name = name
        self.animated = animated

    def __str__(self):
        return f"<{'a' if self.animated else ''}:{self.name}:{self.id}>"


class FakeGuild:
    def __init__(self, emojis=(), emoji_limit: int = 50, errors=None):
        self.emojis = list(emojis)
        self.emoji_limit = emoji_limit
        self.created = []
        self.errors = list(errors or [])

    async def create_custom_emoji(self, *, name, image, reason=None):
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        self.created.append({"name": name, "image": image, "reason": reason})
        emoji = FakeEmoji(9000 + len(self.created), name)
        self.emojis.append(emoji)
        return emoji


class FakeResponse:
    def __init__(self, status: int, data: bytes):
        self.status = status
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read(self):
        return self._data


class FakeSession:
    def __init__(self, status: int = 200, data: bytes = b"image-bytes", raises=None):
        self.status = status
        self.data = data
        self.raises = raises
        self.requested = []

    def get(self, url):
        self.requested.append(url)
        if self.raises is not None:
            raise self.raises
        return FakeResponse(self.status, self.data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def make_message(content: str = "", snapshots=()):
    return SimpleNamespace(content=content, message_snapshots=list(snapshots), reference=None)


class FakeUser:
    def __init__(self, id: int = 42, name: str = "tester"):
        self.id = id
        self.name = name

    def __str__(self):
        return self.name


def make_ctx(guild, content: str = "", replied=None):
    message = make_message(content)
    if replied is not None:
        message.reference = SimpleNamespace(resolved=None, message_id=777)
    author = FakeUser()
    ctx = SimpleNamespace(
        guild=guild,
        message=message,
        author=author,
        send=AsyncMock(),
        typing=lambda: FakeTyping(),
        channel=SimpleNamespace(fetch_message=AsyncMock(return_value=replied)),
        command=MagicMock(),
    )
    return ctx


def http_exception(code: int, text: str = "nope") -> discord.HTTPException:
    response = SimpleNamespace(status=400, reason="Bad Request")
    return discord.HTTPException(response, {"code": code, "message": text})


def run_steal(ctx, raw: str = "", session: FakeSession | None = None) -> FakeSession:
    session = session or FakeSession()
    with patch.object(util.aiohttp, "ClientSession", lambda **kwargs: session), \
            patch.object(util, "apply_ui_embed_emojis", AsyncMock(side_effect=lambda embed: embed)), \
            patch.object(util, "replace_native_ui_emojis", AsyncMock(side_effect=lambda text: text)):
        asyncio.run(util.steal_emoji.callback(ctx, emoji=raw))
    return session


def sent_embed(ctx) -> discord.Embed:
    return ctx.send.await_args.kwargs["embed"]


def field_value(embed: discord.Embed, name_key: str, count: int) -> str:
    expected = i18n.t(name_key, count=count)
    for field in embed.fields:
        if field.name == expected:
            return field.value
    raise AssertionError(f"field {expected!r} not found in {[f.name for f in embed.fields]}")


class EmojiNameTests(unittest.TestCase):
    def test_invalid_characters_become_underscores(self):
        self.assertEqual(util._sanitize_emoji_name("pepe-hug"), "pepe_hug")

    def test_short_names_are_padded_to_the_minimum(self):
        self.assertEqual(util._sanitize_emoji_name("a"), "emojia")
        self.assertEqual(util._sanitize_emoji_name("!!"), "emoji")

    def test_long_names_are_capped_at_32(self):
        self.assertEqual(len(util._sanitize_emoji_name("x" * 40)), 32)


class EmojiSlotTests(unittest.TestCase):
    def test_static_and_animated_are_counted_separately(self):
        guild = FakeGuild([
            FakeEmoji(1, animated=False),
            FakeEmoji(2, animated=True),
            FakeEmoji(3, animated=True),
        ], emoji_limit=50)
        self.assertEqual(util._emoji_slots(guild), (1, 2, 50))


class CollectTargetTests(unittest.TestCase):
    def test_duplicates_are_dropped_and_reply_fills_the_rest(self):
        replied = make_message("<:c:3> <:a:1>")
        ctx = make_ctx(FakeGuild(), content="!steal <:a:1> <:b:2> <:a:1>", replied=replied)
        targets = asyncio.run(util._collect_steal_targets(ctx, "<:a:1> <:b:2> <:a:1>"))
        self.assertEqual([(e.name, e.id) for e in targets], [("a", 1), ("b", 2), ("c", 3)])

    def test_forwarded_content_is_searched(self):
        ctx = make_ctx(FakeGuild(), content="!steal")
        ctx.message.message_snapshots = [SimpleNamespace(content="<a:party:55>")]
        targets = asyncio.run(util._collect_steal_targets(ctx, ""))
        self.assertEqual([(e.name, e.animated) for e in targets], [("party", True)])

    def test_plain_text_yields_nothing(self):
        ctx = make_ctx(FakeGuild(), content="!steal 😀")
        self.assertEqual(asyncio.run(util._collect_steal_targets(ctx, "😀")), [])


class StealEmojiCommandTests(ZhTWLocaleMixin, unittest.TestCase):
    def test_emoji_is_downloaded_and_created(self):
        guild = FakeGuild()
        ctx = make_ctx(guild, content="!steal <:pepehug:123>")
        session = run_steal(ctx, "<:pepehug:123>")

        self.assertEqual(session.requested, ["https://cdn.discordapp.com/emojis/123.png"])
        self.assertEqual(len(guild.created), 1)
        self.assertEqual(guild.created[0]["name"], "pepehug")
        self.assertEqual(guild.created[0]["image"], b"image-bytes")
        self.assertIn("tester", guild.created[0]["reason"])

        embed = sent_embed(ctx)
        self.assertIn(":pepehug:", field_value(embed, "utilcommands.steal_emoji.field.added", 1))

    def test_non_ascii_names_are_sanitized_before_upload(self):
        # \w 會吃到中日文，但 Discord 的表情符號名稱只收 [A-Za-z0-9_]
        guild = FakeGuild()
        ctx = make_ctx(guild, content="!steal <:pepe表情:123>")
        run_steal(ctx, "<:pepe表情:123>")
        self.assertEqual([c["name"] for c in guild.created], ["pepe"])

    def test_animated_emoji_uses_the_gif_url(self):
        guild = FakeGuild()
        ctx = make_ctx(guild, content="!steal <a:spin:456>")
        session = run_steal(ctx, "<a:spin:456>")
        self.assertEqual(session.requested, ["https://cdn.discordapp.com/emojis/456.gif"])

    def test_emoji_already_in_the_guild_is_skipped(self):
        guild = FakeGuild([FakeEmoji(123, "pepe")])
        ctx = make_ctx(guild, content="!steal <:pepe:123>")
        session = run_steal(ctx, "<:pepe:123>")

        self.assertEqual(session.requested, [])  # 沒有白下載
        self.assertEqual(guild.created, [])
        self.assertIn(i18n.t("utilcommands.steal_emoji.reason.already_added"),
                      field_value(sent_embed(ctx), "utilcommands.steal_emoji.field.failed", 1))

    def test_full_static_slots_skip_static_but_still_allow_animated(self):
        guild = FakeGuild([FakeEmoji(i) for i in range(5)], emoji_limit=5)
        ctx = make_ctx(guild, content="!steal <:flat:1000> <a:spin:1001>")
        run_steal(ctx, "<:flat:1000> <a:spin:1001>")

        self.assertEqual([c["name"] for c in guild.created], ["spin"])
        self.assertIn(i18n.t("utilcommands.steal_emoji.reason.no_slot"),
                      field_value(sent_embed(ctx), "utilcommands.steal_emoji.field.failed", 1))

    def test_oversized_image_is_rejected_before_upload(self):
        guild = FakeGuild()
        ctx = make_ctx(guild, content="!steal <:big:1>")
        run_steal(ctx, "<:big:1>", session=FakeSession(data=b"x" * (util._STEAL_EMOJI_MAX_BYTES + 1)))

        self.assertEqual(guild.created, [])
        self.assertIn(
            i18n.t("utilcommands.steal_emoji.reason.too_large",
                   limit=util._STEAL_EMOJI_MAX_BYTES // 1024),
            field_value(sent_embed(ctx), "utilcommands.steal_emoji.field.failed", 1))

    def test_download_failure_is_reported_per_emoji(self):
        guild = FakeGuild()
        ctx = make_ctx(guild, content="!steal <:gone:1>")
        run_steal(ctx, "<:gone:1>", session=FakeSession(status=404))

        self.assertEqual(guild.created, [])
        self.assertIn("HTTP 404", field_value(sent_embed(ctx), "utilcommands.steal_emoji.field.failed", 1))

    def test_max_emojis_error_stops_further_uploads_of_the_same_kind(self):
        guild = FakeGuild(errors=[http_exception(30008)])
        ctx = make_ctx(guild, content="!steal <:a:1> <:b:2>")
        run_steal(ctx, "<:a:1> <:b:2>")

        self.assertEqual(guild.created, [])  # 第二個直接跳過，不再打 API
        failed = field_value(sent_embed(ctx), "utilcommands.steal_emoji.field.failed", 2)
        self.assertEqual(failed.count(i18n.t("utilcommands.steal_emoji.reason.no_slot")), 2)

    def test_other_http_errors_do_not_stop_the_rest(self):
        guild = FakeGuild(errors=[http_exception(50035, "Invalid Form Body"), None])
        ctx = make_ctx(guild, content="!steal <:first:1> <:second:2>")
        run_steal(ctx, "<:first:1> <:second:2>")

        self.assertEqual([c["name"] for c in guild.created], ["second"])
        embed = sent_embed(ctx)
        self.assertIn("Invalid Form Body",
                      field_value(embed, "utilcommands.steal_emoji.field.failed", 1))
        self.assertIn(":second:", field_value(embed, "utilcommands.steal_emoji.field.added", 1))

    def test_more_than_the_limit_is_truncated(self):
        guild = FakeGuild()
        raw = " ".join(f"<:e{i}:{i}>" for i in range(1, util._STEAL_EMOJI_MAX + 3))
        ctx = make_ctx(guild, content=f"!steal {raw}")
        run_steal(ctx, raw)

        self.assertEqual(len(guild.created), util._STEAL_EMOJI_MAX)
        self.assertEqual(
            sent_embed(ctx).description,
            i18n.t("utilcommands.steal_emoji.truncated", count=util._STEAL_EMOJI_MAX))

    def test_no_emoji_found_releases_the_cooldown(self):
        guild = FakeGuild()
        ctx = make_ctx(guild, content="!steal 沒有表情符號")
        run_steal(ctx, "沒有表情符號")

        ctx.command.reset_cooldown.assert_called_once_with(ctx)
        self.assertEqual(guild.created, [])
        self.assertIn(i18n.t("utilcommands.steal_emoji.invalid_emoji"),
                      ctx.send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
