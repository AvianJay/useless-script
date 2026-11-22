import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import discord

import globalenv
import HackedDetector
import Moderate
from mute_tracking import tracked_timeout


class TimeoutAttributionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.record = AsyncMock()
        self.lookup = self.enterContext(patch.object(
            globalenv.bot, "get_cog", return_value=SimpleNamespace(record_local=self.record),
        ))
        self.member = SimpleNamespace(id=20, mention="<@20>", timed_out_until=None, timeout=AsyncMock())
        self.guild = SimpleNamespace(
            id=1, get_member=lambda _: self.member,
            me=SimpleNamespace(id=99, guild_permissions=SimpleNamespace(moderate_members=True)),
        )
        self.member.guild = self.guild

    async def test_relative_deadline_is_from_dispatch_not_ack_or_member_cache(self):
        sent = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ack = sent + timedelta(seconds=12)
        duration = timedelta(minutes=5)
        before = self.member.timed_out_until = sent + timedelta(minutes=1)

        async def apply(until, *, reason):
            self.record.assert_not_awaited()
            # A gateway update can change the cache while the HTTP call is in flight.
            self.member.timed_out_until = sent + timedelta(hours=1)

        self.member.timeout.side_effect = apply
        with patch.object(discord.utils, "utcnow", side_effect=[sent, ack]):
            await tracked_timeout(self.member, duration, reason="r", actor_id=42)
        self.member.timeout.assert_awaited_once_with(duration, reason="r")
        self.record.assert_awaited_once_with(self.member, before, sent + duration, ack, 42, "bot")

    async def test_failed_discord_call_is_not_recorded(self):
        error = ValueError("timeout failed")
        self.member.timeout.side_effect = error
        with self.assertRaises(ValueError) as caught:
            await tracked_timeout(self.member, None)
        self.assertIs(caught.exception, error)
        self.record.assert_not_awaited()

    async def test_absent_or_broken_recorder_does_not_change_discord_result(self):
        until = datetime.now(timezone.utc) + timedelta(minutes=1)
        self.record.side_effect = RuntimeError("unavailable")
        with self.assertLogs("mute_tracking", level="ERROR"):
            result = await tracked_timeout(self.member, until, reason="r")
        self.assertIs(result, self.member.timeout.return_value)
        self.lookup.return_value = None
        await tracked_timeout(self.member, None)
        self.assertEqual(self.member.timeout.await_count, 2)
        self.member.timeout.assert_awaited_with(None, reason=None)
        self.record.assert_awaited_once_with(self.member, None, until, ANY, None, "bot")

    async def test_request_requires_confirmation_and_credits_confirmer(self):
        view = Moderate.RequestView(
            guild=self.guild, requester_id=10, approver_id=11, target_id=20, action="timeout",
            reason="r", duration_seconds=120, embed=discord.Embed(),
        )
        interaction = SimpleNamespace(user=SimpleNamespace(id=10), response=AsyncMock(), message=AsyncMock())
        await view.confirm.callback(interaction)
        self.record.assert_not_awaited()
        interaction.user.id = 11
        with patch.object(Moderate, "log"):
            await view.confirm.callback(interaction)
        self.member.timeout.assert_awaited_once_with(timedelta(seconds=120), reason="r")
        self.record.assert_awaited_once_with(self.member, None, ANY, ANY, 11, "request")

    async def test_vote_only_records_on_passing_and_credits_initiator_not_last_voter(self):
        view = Moderate.VoteView(
            guild=self.guild, initiator_id=10, target_id=20, action="timeout", reason="r",
            duration_seconds=120, threshold=2, timeout=60, embed=discord.Embed().add_field(name="votes", value="0"),
        )
        interaction = SimpleNamespace(user=SimpleNamespace(id=11), response=AsyncMock())
        await view.vote_agree.callback(interaction)
        self.record.assert_not_awaited()
        interaction.user.id = 12
        with patch.object(Moderate, "log"):
            await view.vote_agree.callback(interaction)
        self.member.timeout.assert_awaited_once_with(timedelta(seconds=120), reason="r")
        self.record.assert_awaited_once_with(self.member, None, ANY, ANY, 10, "vote")

    async def test_fixed_and_ordinary_28_day_mutes_are_distinct_and_clear_is_recorded(self):
        self.member.guild_permissions = SimpleNamespace(administrator=False)
        self.member.timeout.side_effect = lambda until, **_: setattr(self.member, "timed_out_until", until)
        expires, _ = await HackedDetector.HackedDetector()._apply_fixed_cross_guild_timeout(self.member)
        self.record.assert_awaited_once_with(self.member, None, expires, ANY, 99, "hacked_cross_guild")
        self.assertEqual(self.member.timeout.await_args.args, (expires,))
        self.record.reset_mock()
        with patch.object(Moderate, "_load_custom_action_strings", return_value={}):
            await Moderate.do_action_str("untimeout", self.guild, self.member, moderator=SimpleNamespace(id=11))
            self.record.assert_awaited_once_with(self.member, expires, None, ANY, 11, "bot")
            self.record.reset_mock()
            await Moderate.do_action_str("mute 28d", self.guild, self.member, moderator=self.guild.me)
        deadline = self.member.timeout.await_args.args[0]
        self.assertIsNotNone(deadline.utcoffset())
        self.record.assert_awaited_once_with(self.member, None, deadline, ANY, 99, "bot")
