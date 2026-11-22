import asyncio
from dataclasses import replace
from datetime import timedelta
import itertools
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

import discord
import i18n
from mute_leaderboard_service import AUTOMOD_ACTOR, UNKNOWN, WINDOW_MS, MuteStore, Observation, reduce_target

# 匯入時會註冊 Cog 並建表；不可動到實際排行榜資料庫。
with patch.object(MuteStore, 'initialize'):
    import MuteLeaderboard as lb


MIN = 60_000
START = 1_800_000_000_000


def event(ref='a', at=0, before=0, after=60, channel='audit', actor=11, source=''):
    def ts(minute):
        return minute if minute in (0, UNKNOWN) else START + minute * MIN
    return Observation(ref, 1, 2, START + at * MIN, ts(before), ts(after), channel, actor, None, source)


class ReducerTests(unittest.TestCase):
    def test_extension_and_early_release(self):
        events = [event(), event('b', 15, 60, 120, actor=12), event('c', 25, 120, 0)]
        for order in itertools.permutations(events):
            self.assertEqual(reduce_target(order, START + 90 * MIN), (2, 25 * MIN, {11: 1, 12: 1}))

    def test_shortening_natural_expiry_and_new_timeout(self):
        events = [event(), event('b', 15, 60, 30), event('c', 40, 30, 50, actor=None)]
        self.assertEqual(reduce_target(events, START + 45 * MIN), (2, 35 * MIN, {11: 1}))
        self.assertEqual(reduce_target(events, START + 70 * MIN), (2, 40 * MIN, {11: 1}))
        self.assertEqual(reduce_target(events[1:2], START + 70 * MIN), (0, 0, {}))

    def test_three_observers_one_operation(self):
        audit = replace(event(), issuer=99, actor=99)
        gateway = replace(audit, ref='g', channel='member', at=START+400, issuer=None, actor=None)
        local = replace(audit, ref='l', channel='local', at=START+700, actor=12)
        for order in itertools.permutations([audit, gateway, local]):
            self.assertEqual(reduce_target(order, START+MIN), (1, MIN, {12: 1}))

    def test_local_cache_stale_but_audit_matches_exact_bot_and_deadline(self):
        start = event()
        audit = replace(event('b', 1, 60, 90), issuer=99, actor=99)
        local = replace(audit, ref='local:b', at=audit.at+300, before=0, channel='local', actor=14)
        self.assertEqual(reduce_target([start, local, audit], START+2*MIN), (2, 2*MIN, {11: 1, 14: 1}))

    def test_observed_fresh_start_after_missed_release(self):
        events = [event(), event('b', 15, 0, 30, actor=13)]
        self.assertEqual(reduce_target(events, START+31*MIN)[:1], (2,))

    def test_quick_release_and_reapply_same_expiry(self):
        a = event()
        b = replace(event('b', before=60, after=0), at=START+400)
        c = replace(a, ref='c', actor=12, at=START+800)
        gateway = [replace(e, ref=f'm:{e.ref}', at=e.at+600, channel='member', actor=None) for e in (a, b, c)]
        self.assertEqual(reduce_target([c, *gateway, a, b], START+MIN), (2, MIN-400, {11: 1, 12: 1}))

    def test_exclusion_splits_included_time(self):
        protected = event('b', 15, 60, 40_335, source='hacked_cross_guild')
        self.assertEqual(reduce_target([event(), protected, event('c', 45, 40_335, 0)], START+90*MIN),
                         (1, 15*MIN, {11: 1}))
        self.assertEqual(reduce_target([replace(protected, before=0, source='bot')], START+20*MIN),
                         (1, 5*MIN, {11: 1}))

    def test_automod_unknown_and_member_update_with_actual_expiry(self):
        native = event('a', before=UNKNOWN, after=UNKNOWN, channel='auto_audit', actor=AUTOMOD_ACTOR)
        self.assertEqual(reduce_target([native], START+MIN), (1, 0, {AUTOMOD_ACTOR: 1}))
        member = replace(event('b', channel='member', actor=None), at=START+500)
        live = replace(event('c', before=UNKNOWN, channel='auto_live', actor=AUTOMOD_ACTOR), at=START+1000,
                       after=member.after+1000)
        self.assertEqual(reduce_target([native, live, member], START+61*MIN), (1, 60*MIN, {AUTOMOD_ACTOR: 1}))

    def test_native_issuer_only_matches_same_audit_issuer(self):
        native = replace(event('a', before=UNKNOWN, after=UNKNOWN, channel='auto_audit'), issuer=99)
        actual = replace(event('b'), issuer=99, actor=99)
        self.assertEqual(reduce_target([native, actual], START+MIN), (1, MIN, {AUTOMOD_ACTOR: 1}))
        other = replace(actual, at=START+500, issuer=42, actor=42)
        self.assertEqual(reduce_target([native, other], START+MIN)[2], {AUTOMOD_ACTOR: 1, 42: 1})


class StoreCase:
    def setup_store(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'history.db'
        self.store = MuteStore(lambda: sqlite3.connect(self.path))
        self.store.initialize()


class StoreTests(StoreCase, unittest.TestCase):
    def setUp(self):
        self.setup_store()

    def test_isolation_idempotent_stable_top_ten_and_no_rolling_window(self):
        for target in reversed(range(1, 13)):
            row = replace(event(), ref=f'a:{target}', target=target)
            self.store.record(row)
            self.store.record(row)
        self.store.record(replace(event(), ref='other', guild=2, target=999))
        self.assertEqual(self.store.ranking(1, 'count', START+2*WINDOW_MS), [(i, 1) for i in range(1, 11)])
        self.assertEqual(self.store.ranking(1, 'actor', START+MIN), [(11, 12)])
        self.assertEqual(self.store.ranking(2, 'duration', START+MIN), [(999, MIN)])

    def test_rest_can_fill_missing_issuer_for_same_gateway_audit_id(self):
        audit = event(actor=None)
        self.store.record(audit)
        self.store.record(replace(audit, actor=22, issuer=22))
        self.store.record(audit)
        self.assertEqual(self.store.ranking(1, 'count', START+MIN), [(2, 1)])
        self.assertEqual(self.store.ranking(1, 'actor', START+MIN), [(22, 1)])

    def test_backfill_resumes_per_action(self):
        self.assertEqual(len(self.store.begin(1, START)), 2)
        self.store.page(1, 'audit', [event()], 70, False)
        self.store.page(1, 'auto_audit', [], 0, True)
        self.assertEqual(self.store.begin(1, START+MIN), [('audit', START-WINDOW_MS, START, 70)])
        self.assertFalse(self.store.coverage(1)[1])
        self.store.page(1, 'audit', [event()], 70, True)
        self.assertTrue(self.store.coverage(1)[1])
        self.assertEqual(self.store.begin(1, START+MIN), [])
        self.assertEqual(self.store.ranking(1, 'count', START+MIN), [(2, 1)])


def entry(ref=123, target=2, actor=99, before=0, after=60, at=START, kind='audit', reason=None):
    a = event(before=before, after=after)
    def diff(ms):
        return NS() if ms == UNKNOWN else NS(timed_out_until=lb.utc(ms) if ms else None)
    return NS(id=ref, guild=NS(id=1), target=discord.Object(id=target), user_id=actor,
              before=diff(a.before), after=diff(a.after), action=lb.ACTIONS[kind],
              created_at=lb.utc(at), reason=reason)


class AdapterTests(unittest.TestCase):
    def test_no_timeout_diff_is_not_release(self):
        self.assertIsNone(lb.from_audit(entry(before=UNKNOWN, after=UNKNOWN), 99))
        self.assertEqual(lb.from_audit(entry(before=60, after=0), 99).after, 0)
        self.assertEqual(lb.from_audit(entry(kind='auto_audit'), 99).after, UNKNOWN)

    def test_legacy_exclusion_requires_this_bot_exact_reason_and_duration(self):
        for locale in ('zh-TW', 'en', 'ja'):
            reason = i18n.t('hackeddetector.audit.hacked_timeout', locale=locale)
            ev = entry(after=28*1440, reason=reason)
            self.assertEqual(lb.from_audit(ev, 99).source, 'hacked_cross_guild')
            self.assertFalse(lb.from_audit(ev, 100).source)
            self.assertFalse(lb.from_audit(entry(after=60, reason=reason), 99).source)
            self.assertFalse(lb.from_audit(entry(after=28*1440, reason='other'), 99).source)


class CogTests(StoreCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setup_store()
        self.guild = NS(id=1, me=NS(joined_at=None, guild_permissions=NS(view_audit_log=False)),
                        get_member=lambda _: None)
        self.cog = lb.MuteLeaderboard(NS(user=NS(id=99)), self.store)

    async def test_permission_restored_single_job_and_public_command(self):
        calls = []
        async def history(**kwargs):
            calls.append(kwargs)
            await asyncio.sleep(0)
            if kwargs['action'] == lb.ACTIONS['audit']:
                yield entry()
        self.guild.audit_logs = history
        with patch.object(lb, 'now_ms', return_value=START + MIN):
            it = NS(guild=self.guild, response=NS(defer=AsyncMock()), followup=NS(send=AsyncMock()))
            await self.cog.leaderboard.callback(self.cog, it)
            it.response.defer.assert_awaited_once_with()
            sent = it.followup.send.await_args.kwargs
            self.assertEqual(sent['embed'].description, i18n.t('muteleaderboard.empty'))
            self.assertFalse(sent.get('ephemeral', False))
            self.assertFalse(calls)
            self.guild.me.guild_permissions.view_audit_log = True
            first, second = await asyncio.gather(self.cog.coverage(self.guild), self.cog.coverage(self.guild))
            self.assertEqual((first[1], second[1]), ('complete', 'complete'))
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]['before'] - calls[0]['after'], timedelta(days=45))
            await self.cog.on_audit_log_entry_create(entry())
            self.assertEqual(self.store.ranking(1, 'actor', START+MIN), [(99, 1)])
            for mode in ('count', 'actor', 'duration'):
                await self.cog.leaderboard.callback(self.cog, it, mode)
                self.assertIn('🥇', it.followup.send.await_args.kwargs['embed'].description)
            self.assertEqual(len(calls), 2)
            self.assertEqual([c.value for c in self.cog.leaderboard.parameters[0].choices], ['count', 'duration', 'actor'])

    async def test_retry_after_second_page_failure(self):
        calls = []
        async def history(action, after, **kwargs):
            calls.append((action, after))
            if action != lb.ACTIONS['audit']:
                return
            if not isinstance(after, discord.Object):
                for i in range(1, 101):
                    yield entry(i, target=i)
            elif len(calls) == 2:
                raise OSError('interrupted')
            else:
                self.assertEqual(after.id, 100)
                yield entry(101, target=101)
        self.guild.audit_logs = history
        with self.assertLogs(lb.log, 'ERROR'):
            self.assertEqual(await self.cog._backfill(self.guild), 'retry')
        self.assertFalse(self.store.coverage(1)[1])
        self.assertEqual(await self.cog._backfill(self.guild), 'complete')
        self.assertEqual(self.store.ranking(1, 'actor', START+MIN), [(99, 101)])

    async def test_live_sources_converge(self):
        member = NS(id=2, guild=self.guild, timed_out_until=lb.utc(START + 60*MIN))
        with patch.object(lb, 'now_ms', return_value=START):
            await self.cog.on_member_update(NS(timed_out_until=None), member)
            await self.cog.record_local(member, None, member.timed_out_until, lb.utc(START), 27, 'bot')
            await self.cog.on_audit_log_entry_create(entry())
        self.assertEqual(self.store.ranking(1, 'actor', START+MIN), [(27, 1)])
