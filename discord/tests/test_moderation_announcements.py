import sys
import asyncio
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import Moderate
import AutoReply as AutoReplyModule


def fake_entity(entity_id, name):
    return SimpleNamespace(
        id=entity_id,
        name=name,
        display_name=name,
        mention=f"<@{entity_id}>",
        display_avatar=SimpleNamespace(url=f"https://example.com/{entity_id}.png"),
    )


class FakeChannel:
    def __init__(self, messages=None, *, can_read=True):
        self.messages = list(messages or [])
        self.can_read = can_read
        self.sent = []
        self.name = "moderation"

    def permissions_for(self, member):
        return SimpleNamespace(read_message_history=self.can_read)

    async def history(self, *, limit):
        for message in self.messages[:limit]:
            yield message

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=1234, **kwargs)


class RecordingHistoryChannel(FakeChannel):
    async def send(self, **kwargs):
        sent = await super().send(**kwargs)
        self.messages.insert(
            0,
            fake_message(
                kwargs.get("content") or "",
                embeds=[kwargs["embed"]] if kwargs.get("embed") else [],
            ),
        )
        return sent


def fake_message(content="", *, embeds=None, year=2026, author_id=555):
    return SimpleNamespace(
        content=content,
        embeds=list(embeds or []),
        created_at=datetime(year, 6, 1, tzinfo=timezone.utc),
        author=SimpleNamespace(id=author_id, bot=True),
    )


def fake_guild(channel=None):
    me = fake_entity(999, "Yee")
    return SimpleNamespace(
        id=1,
        name="測試伺服器",
        me=me,
        icon=SimpleNamespace(url="https://example.com/guild.png"),
        get_channel=lambda channel_id: channel if channel_id == 10 else None,
    )


class ModerationTemplateTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_embed_helper_preserves_autoreply_output(self):
        cog = AutoReplyModule.AutoReply(SimpleNamespace())
        template = (
            "一般文字"
            "{embedtitle:標題}"
            "{embeddescription:內容}"
            "{embedcolor:5865F2}"
            "{embedfield:欄位:值}"
            "{embedfooter:頁尾}"
            "{embedtime:true}"
        )
        content, extracted = cog._extract_embed_tokens(template)
        context = {"now": datetime(2026, 8, 15, tzinfo=timezone.utc)}
        with patch.object(
            cog,
            "_resolve_response_variables",
            new=AsyncMock(side_effect=lambda value, message, context: value),
        ):
            embed = await cog._build_embed_from_tokens(extracted, SimpleNamespace(), context)

        self.assertEqual(content, "一般文字")
        self.assertEqual(embed.title, "標題")
        self.assertEqual(embed.description, "內容")
        self.assertEqual(embed.color.value, 0x5865F2)
        self.assertEqual(embed.fields[0].name, "欄位")
        self.assertEqual(embed.fields[0].value, "值")
        self.assertEqual(embed.footer.text, "頁尾")
        self.assertEqual(embed.timestamp, context["now"])

    async def test_current_markdown_is_the_default(self):
        guild = fake_guild()
        values = Moderate.build_moderation_template_values(
            guild,
            fake_entity(1, "Target"),
            fake_entity(2, "Moderator"),
            reason="洗版",
            action_text="禁言 10 分鐘",
            case_id="1150001",
        )
        content, embed = await Moderate.render_moderation_announcement(
            Moderate.default_moderation_announcement_config(),
            values,
        )
        self.assertIsNone(embed)
        self.assertIn("### ⛔ 違規處分", content)
        self.assertIn("裁判字號：1150001", content)
        self.assertIn("<@1>", content)

    async def test_pure_embed_template_is_supported(self):
        values = {key: "sample" for key in Moderate.MODERATION_TEMPLATE_VARIABLES}
        content, embed = await Moderate.render_moderation_announcement(
            {
                "template": "{embedtitle:違規處分}{embeddescription:{reason}}",
                "case_id_format": "{sequence}",
            },
            values,
        )
        self.assertIsNone(content)
        self.assertEqual(embed.title, "違規處分")
        self.assertEqual(embed.description, "sample")

    async def test_autoreply_embed_subset_supports_mixed_output(self):
        guild = fake_guild()
        values = Moderate.build_moderation_template_values(
            guild,
            fake_entity(1, "Target"),
            fake_entity(2, "Moderator"),
            reason="洗版",
            action_text="禁言",
            case_id="CASE-2026-0042",
        )
        config = {
            "template": (
                "通知 {user}"
                "{embedtitle:違規處分}"
                "{embeddescription:{reason}}"
                "{embedurl:https://example.com/case}"
                "{embedimage:https://example.com/image.png}"
                "{embedthumbnail:https://example.com/thumb.png}"
                "{embedcolor:ED4245}"
                "{embedauthor:{moderator_name}}"
                "{embedauthorurl:https://example.com/mod}"
                "{embedauthorimage:{moderator_avatar}}"
                "{embedfooter:{guild}}"
                "{embedfooterimage:{guild_icon}}"
                "{embedtime:true}"
                "{embedfield:裁判字號:{case_id}}"
            ),
            "case_id_format": "CASE-{year}-{sequence:04d}",
        }
        content, embed = await Moderate.render_moderation_announcement(config, values)
        self.assertEqual(content, "通知 <@1>")
        self.assertEqual(embed.title, "違規處分")
        self.assertEqual(embed.description, "洗版")
        self.assertEqual(embed.fields[0].value, "CASE-2026-0042")
        self.assertEqual(embed.color.value, 0xED4245)
        self.assertIsNotNone(embed.timestamp)

    async def test_invalid_color_and_unknown_variable_are_rejected(self):
        values = {key: "sample" for key in Moderate.MODERATION_TEMPLATE_VARIABLES}
        with self.assertRaisesRegex(ValueError, "顏色"):
            await Moderate.render_moderation_announcement(
                {"template": "{embedcolor:not-a-color}{embedtitle:test}", "case_id_format": "{sequence}"},
                values,
            )
        with self.assertRaisesRegex(ValueError, "不支援的模板變數"):
            Moderate.normalize_moderation_announcement_config(
                {"template": "{unknown}", "case_id_format": "{sequence}"}
            )

    async def test_discord_output_limits_are_rejected_before_send(self):
        values = {key: "sample" for key in Moderate.MODERATION_TEMPLATE_VARIABLES}
        with self.assertRaisesRegex(ValueError, "2000"):
            await Moderate.render_moderation_announcement(
                {"template": "x" * 2001, "case_id_format": "{sequence}"},
                values,
            )
        with self.assertRaisesRegex(ValueError, "1024"):
            await Moderate.render_moderation_announcement(
                {
                    "template": "{embedfield:欄位:" + ("x" * 1025) + "}",
                    "case_id_format": "{sequence}",
                },
                values,
            )
        with self.assertRaisesRegex(ValueError, "2048"):
            await Moderate.render_moderation_announcement(
                {
                    "template": "{embedtitle:test}{embedurl:https://example.com/" + ("x" * 2030) + "}",
                    "case_id_format": "{sequence}",
                },
                values,
            )

    def test_case_format_supports_year_and_padding(self):
        self.assertEqual(
            Moderate.format_case_id("CASE-{year}-{sequence:04d}", 2026, 42),
            "CASE-2026-0042",
        )
        with self.assertRaisesRegex(ValueError, "必須包含"):
            Moderate.format_case_id("CASE-{year}", 2026, 1)


class CrossBotCaseIdTests(unittest.IsolatedAsyncioTestCase):
    async def test_latest_recognizable_message_from_another_bot_is_used(self):
        channel = FakeChannel([
            fake_message("一般聊天內容", author_id=111),
            fake_message("> - 裁判字號：1150042", author_id=222),
        ])
        guild = fake_guild(channel)
        with (
            patch.object(Moderate, "_taipei_now", return_value=datetime(2026, 8, 15, tzinfo=Moderate.TAIPEI_TIMEZONE)),
            patch.object(Moderate, "get_moderation_announcement_config", return_value=Moderate.default_moderation_announcement_config()),
        ):
            self.assertEqual(await Moderate._next_case_components(guild, channel), (2026, 43))

    async def test_embed_field_is_recognized_and_conflicting_message_is_skipped(self):
        conflicting = fake_message("> - 裁判字號：1150044\n> - 裁判字號：1150045")
        embed = discord.Embed(title="違規處分")
        embed.add_field(name="裁判字號", value="1150043")
        older = fake_message(embeds=[embed], author_id=777)
        channel = FakeChannel([conflicting, older])
        guild = fake_guild(channel)
        with (
            patch.object(Moderate, "_taipei_now", return_value=datetime(2026, 8, 15, tzinfo=Moderate.TAIPEI_TIMEZONE)),
            patch.object(Moderate, "get_moderation_announcement_config", return_value=Moderate.default_moderation_announcement_config()),
        ):
            self.assertEqual(await Moderate._next_case_components(guild, channel), (2026, 44))

    async def test_previous_year_resets_sequence(self):
        channel = FakeChannel([fake_message("> - 裁判字號：1140999", year=2025)])
        guild = fake_guild(channel)
        with (
            patch.object(Moderate, "_taipei_now", return_value=datetime(2026, 1, 1, tzinfo=Moderate.TAIPEI_TIMEZONE)),
            patch.object(Moderate, "get_moderation_announcement_config", return_value=Moderate.default_moderation_announcement_config()),
        ):
            self.assertEqual(await Moderate._next_case_components(guild, channel), (2026, 1))

    async def test_missing_history_permission_uses_local_state(self):
        channel = FakeChannel(can_read=False)
        guild = fake_guild(channel)

        def get_config(guild_id, key, default=None):
            if key == Moderate.MODERATION_CASE_STATE_KEY:
                return {"year": 2026, "sequence": 12}
            return default

        with (
            patch.object(Moderate, "_taipei_now", return_value=datetime(2026, 8, 15, tzinfo=Moderate.TAIPEI_TIMEZONE)),
            patch.object(Moderate, "get_server_config", side_effect=get_config),
            patch.object(Moderate, "get_moderation_announcement_config", return_value=Moderate.default_moderation_announcement_config()),
        ):
            self.assertEqual(await Moderate._next_case_components(guild, channel), (2026, 13))

    async def test_unrecognizable_history_and_format_change_use_local_state(self):
        channel = FakeChannel([
            fake_message("OLD-0042", author_id=222),
            fake_message("一般聊天內容", author_id=333),
        ])
        guild = fake_guild(channel)

        def get_config(guild_id, key, default=None):
            if key == Moderate.MODERATION_CASE_STATE_KEY:
                return {"year": 2026, "sequence": 12}
            return default

        with (
            patch.object(Moderate, "_taipei_now", return_value=datetime(2026, 8, 15, tzinfo=Moderate.TAIPEI_TIMEZONE)),
            patch.object(Moderate, "get_server_config", side_effect=get_config),
        ):
            result = await Moderate._next_case_components(
                guild,
                channel,
                case_id_format="NEW-{sequence:04d}",
            )
        self.assertEqual(result, (2026, 13))

    async def test_messages_beyond_the_1000_message_scan_limit_are_ignored(self):
        messages = [fake_message("一般聊天內容") for _ in range(1000)]
        messages.append(fake_message("> - 裁判字號：1150999", author_id=222))
        channel = FakeChannel(messages)
        guild = fake_guild(channel)

        def get_config(guild_id, key, default=None):
            if key == Moderate.MODERATION_CASE_STATE_KEY:
                return {"year": 2026, "sequence": 7}
            return default

        with (
            patch.object(Moderate, "_taipei_now", return_value=datetime(2026, 8, 15, tzinfo=Moderate.TAIPEI_TIMEZONE)),
            patch.object(Moderate, "get_server_config", side_effect=get_config),
        ):
            result = await Moderate._next_case_components(
                guild,
                channel,
                case_id_format=Moderate.DEFAULT_MODERATION_CASE_ID_FORMAT,
            )
        self.assertEqual(result, (2026, 8))

    async def test_preview_scans_with_the_proposed_case_format(self):
        channel = FakeChannel([fake_message("NEW-0042", author_id=222)])
        guild = fake_guild(channel)

        def get_config(guild_id, key, default=None):
            if key == "MODERATION_MESSAGE_CHANNEL_ID":
                return 10
            return default

        with (
            patch.object(Moderate, "_taipei_now", return_value=datetime(2026, 8, 15, tzinfo=Moderate.TAIPEI_TIMEZONE)),
            patch.object(Moderate, "get_server_config", side_effect=get_config),
        ):
            _, _, case_id = await Moderate.preview_moderation_announcement(
                guild,
                config_value={
                    "template": "裁判字號：{case_id}",
                    "case_id_format": "NEW-{sequence:04d}",
                },
            )
        self.assertEqual(case_id, "NEW-0043")

    async def test_same_bot_concurrent_sends_use_distinct_case_ids(self):
        channel = RecordingHistoryChannel([
            fake_message("> - 裁判字號：1150042", author_id=222),
        ])
        guild = fake_guild(channel)
        with (
            patch.object(Moderate, "_taipei_now", return_value=datetime(2026, 8, 15, tzinfo=Moderate.TAIPEI_TIMEZONE)),
            patch.object(Moderate, "get_moderation_announcement_config", return_value=Moderate.default_moderation_announcement_config()),
            patch.object(Moderate, "set_server_config"),
        ):
            results = await asyncio.gather(*(
                Moderate.send_moderation_announcement(
                    guild,
                    channel,
                    fake_entity(index, f"Target {index}"),
                    fake_entity(2, "Moderator"),
                    reason="洗版",
                    action_text="禁言",
                )
                for index in (1, 3)
            ))
        self.assertEqual({case_id for _, case_id in results}, {"1150043", "1150044"})

    async def test_state_is_written_only_after_successful_send(self):
        channel = FakeChannel([fake_message("> - 裁判字號：1150042", author_id=222)])
        guild = fake_guild(channel)
        with (
            patch.object(Moderate, "_taipei_now", return_value=datetime(2026, 8, 15, tzinfo=Moderate.TAIPEI_TIMEZONE)),
            patch.object(Moderate, "get_moderation_announcement_config", return_value=Moderate.default_moderation_announcement_config()),
            patch.object(Moderate, "set_server_config") as save,
        ):
            _, case_id = await Moderate.send_moderation_announcement(
                guild,
                channel,
                fake_entity(1, "Target"),
                fake_entity(2, "Moderator"),
                reason="洗版",
                action_text="禁言",
            )
        self.assertEqual(case_id, "1150043")
        save.assert_called_once_with(
            guild.id,
            Moderate.MODERATION_CASE_STATE_KEY,
            {"year": 2026, "sequence": 43},
        )
        self.assertFalse(channel.sent[0]["allowed_mentions"].everyone)
        self.assertFalse(channel.sent[0]["allowed_mentions"].roles)
        self.assertTrue(channel.sent[0]["allowed_mentions"].users)

    async def test_failed_send_does_not_update_state(self):
        channel = FakeChannel()
        channel.send = AsyncMock(side_effect=discord.HTTPException(SimpleNamespace(status=500, reason="error"), "failed"))
        guild = fake_guild(channel)
        with (
            patch.object(Moderate, "_next_case_components", new=AsyncMock(return_value=(2026, 1))),
            patch.object(Moderate, "get_moderation_announcement_config", return_value=Moderate.default_moderation_announcement_config()),
            patch.object(Moderate, "set_server_config") as save,
        ):
            with self.assertRaises(discord.HTTPException):
                await Moderate.send_moderation_announcement(
                    guild,
                    channel,
                    fake_entity(1, "Target"),
                    fake_entity(2, "Moderator"),
                    reason="洗版",
                    action_text="禁言",
                )
        save.assert_not_called()


class ModerationCommandSchemaTests(unittest.TestCase):
    def test_format_command_serializes_as_guild_admin_command(self):
        commands = {
            command.name: command
            for command in Moderate.Moderate.__cog_app_commands__
        }
        command = commands["moderation-message-format"]
        payload = command.to_dict(Moderate.bot.tree)
        self.assertEqual(payload["name"], "moderation-message-format")
        self.assertEqual(payload["default_member_permissions"], 8)
        self.assertEqual(payload["contexts"], [0])


if __name__ == "__main__":
    unittest.main()
