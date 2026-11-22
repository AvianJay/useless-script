"""公開的伺服器禁言排行榜。"""
import asyncio
from datetime import datetime, timezone
import logging
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands

from globalenv import bot, get_db_connection
import i18n
from i18n import t
from mute_leaderboard_service import AUTOMOD_ACTOR, CLEAR, UNKNOWN, MuteStore, Observation, now_ms


log = logging.getLogger(__name__)
ABSENT = object()
ACTIONS = {'audit': discord.AuditLogAction.member_update,
           'auto_audit': discord.AuditLogAction.automod_timeout_member}
_TITLES = {'count': 'muteleaderboard.title.count', 'duration': 'muteleaderboard.title.duration',
           'actor': 'muteleaderboard.title.actor'}
_STATUS = {'complete': 'muteleaderboard.status.complete', 'running': 'muteleaderboard.status.running',
           'no_permission': 'muteleaderboard.status.no_permission', 'retry': 'muteleaderboard.status.retry'}


def epoch(value):
    if value is ABSENT:
        return UNKNOWN
    return round(value.timestamp() * 1000) if value is not None else CLEAR


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def from_audit(entry, own_id):
    target = getattr(entry.target, 'id', None)
    if not target:
        return None
    ref, at = f'audit:{entry.id}', epoch(entry.created_at)
    if entry.action == ACTIONS['auto_audit']:
        return Observation(ref, entry.guild.id, target, at, UNKNOWN, UNKNOWN, 'auto_audit', AUTOMOD_ACTOR, entry.user_id)
    if entry.action != ACTIONS['audit']:
        return None
    before = getattr(entry.before, 'timed_out_until', ABSENT)
    after = getattr(entry.after, 'timed_out_until', ABSENT)
    # 只帶 old_value 的審核變更會解析成 after=None；缺少屬性則不是 timeout 變更。
    if after is ABSENT:
        return None
    prior, expiry = epoch(before), epoch(after)
    actor = entry.user_id
    source = ''
    if own_id is not None and actor == own_id and abs(expiry - at - 28 * 86_400_000) < 60_000:
        if entry.reason in {t('hackeddetector.audit.hacked_timeout', locale=loc) for loc in i18n.available_locales()}:
            source = 'hacked_cross_guild'
    return Observation(ref, entry.guild.id, target, at, prior, expiry, 'audit', actor, actor, source)


def duration_text(ms):
    seconds = ms // 1000
    parts = []
    for size, key in ((86400, 'common.unit.days'), (3600, 'common.unit.hours'),
                      (60, 'common.unit.minutes'), (1, 'common.unit.seconds')):
        units, seconds = divmod(seconds, size)
        if units:
            parts.append(i18n.tn(key, units))
    return ' '.join(parts) or i18n.tn('common.unit.seconds', 0)


class MuteLeaderboard(commands.Cog):
    def __init__(self, client, store=None):
        self.bot = client
        self.store = store or MuteStore(get_db_connection)
        self.store.initialize()
        self._jobs = {}
        self._slots = asyncio.Semaphore(3)

    def cog_unload(self):
        for job in list(self._jobs.values()):
            job.cancel()

    async def record_local(self, member, before_until, after_until, event_at, actor_id, source):
        issuer = self.bot.user.id if self.bot.user else None
        await asyncio.to_thread(self.store.record, Observation(
            f'local:{uuid4().hex}', member.guild.id, member.id, epoch(event_at), epoch(before_until),
            epoch(after_until), 'local', actor_id if actor_id is not None else issuer, issuer, source))

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.timed_out_until != after.timed_out_until:
            await asyncio.to_thread(self.store.record, Observation(
                f'member:{uuid4().hex}', after.guild.id, after.id, now_ms(),
                epoch(before.timed_out_until), epoch(after.timed_out_until), 'member'))

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry):
        if entry.action in ACTIONS.values():
            event = from_audit(entry, self.bot.user.id if self.bot.user else None)
            if event is not None:
                await asyncio.to_thread(self.store.record, event)

    @commands.Cog.listener()
    async def on_automod_action(self, execution):
        if execution.action.type != discord.AutoModRuleActionType.timeout:
            return
        at, duration = now_ms(), execution.action.duration
        after = at + round(duration.total_seconds() * 1000) if duration is not None else UNKNOWN
        ref = (f'auto:{execution.guild_id}:{execution.rule_id}:{execution.user_id}:{execution.message_id}'
               if execution.message_id else f'auto:{uuid4().hex}')
        await asyncio.to_thread(self.store.record, Observation(
            ref, execution.guild_id, execution.user_id, at, UNKNOWN, after, 'auto_live', AUTOMOD_ACTOR))

    async def _backfill(self, guild):
        try:
            async with self._slots:
                streams = await asyncio.to_thread(self.store.begin, guild.id, now_ms())
                for stream, since, boundary, cursor in streams:
                    while True:
                        page = [entry async for entry in guild.audit_logs(
                            action=ACTIONS[stream], limit=100, oldest_first=True, before=utc(boundary),
                            after=discord.Object(id=cursor) if cursor else utc(since))]
                        observations = [obs for entry in page if (obs := from_audit(entry, self.bot.user.id)) is not None]
                        done = len(page) < 100
                        cursor = max((entry.id for entry in page), default=cursor)
                        await asyncio.to_thread(self.store.page, guild.id, stream, observations, cursor, done)
                        if done:
                            break
            return 'complete'
        except discord.Forbidden:
            return 'no_permission'
        except Exception:
            log.exception('Timeout history import failed for guild %s', guild.id)
            return 'retry'

    async def coverage(self, guild):
        joined = epoch(getattr(guild.me, 'joined_at', None))
        since, complete = await asyncio.to_thread(self.store.coverage, guild.id, joined)
        if complete:
            return since, 'complete'
        if not guild.me or not guild.me.guild_permissions.view_audit_log:
            return since, 'no_permission'
        job = self._jobs.get(guild.id)
        if job is None:
            job = self._jobs[guild.id] = asyncio.create_task(self._backfill(guild))
            job.add_done_callback(lambda _: self._jobs.pop(guild.id, None))
        done, _ = await asyncio.wait((job,), timeout=1.25)
        if not done:
            return since, 'running'
        since, _ = await asyncio.to_thread(self.store.coverage, guild.id, joined)
        return since, job.result()

    @app_commands.command(name=app_commands.locale_str('mute-leaderboard', i18n_key='cmd.muteleaderboard.mute_leaderboard.name'),
                          description=app_commands.locale_str('Show this server’s timeout leaderboard', i18n_key='cmd.muteleaderboard.mute_leaderboard.desc'))
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.describe(metric=app_commands.locale_str('How to rank members', i18n_key='cmd.muteleaderboard.mute_leaderboard.param.metric'))
    @app_commands.choices(metric=[
        app_commands.Choice(name=app_commands.locale_str('Times timed out', i18n_key='cmd.muteleaderboard.mute_leaderboard.choice.count'), value='count'),
        app_commands.Choice(name=app_commands.locale_str('Time spent in timeout', i18n_key='cmd.muteleaderboard.mute_leaderboard.choice.duration'), value='duration'),
        app_commands.Choice(name=app_commands.locale_str('Timeouts issued', i18n_key='cmd.muteleaderboard.mute_leaderboard.choice.actor'), value='actor')])
    async def leaderboard(self, interaction: discord.Interaction, metric: str = 'count'):
        await interaction.response.defer()
        guild = interaction.guild
        since, status = await self.coverage(guild)
        rows = await asyncio.to_thread(self.store.ranking, guild.id, metric, now_ms())
        lines = []
        for rank, (user_id, score) in enumerate(rows, 1):
            if user_id == AUTOMOD_ACTOR:
                name = t('muteleaderboard.automod')
            else:
                member = guild.get_member(user_id)
                name = discord.utils.escape_markdown(member.display_name) if member else str(user_id)
            medal = ('🥇', '🥈', '🥉')[rank-1] if rank <= 3 else f'**#{rank}**'
            value = duration_text(score) if metric == 'duration' else i18n.tn('muteleaderboard.count', score)
            lines.append(f'{medal} {name} — {value}')
        description = '\n'.join(lines) or t('muteleaderboard.empty')
        if metric == 'duration':
            description += '\n\n' + t('muteleaderboard.duration_note')
        embed = discord.Embed(title=t(_TITLES[metric]), description=description, color=0xf1c40f)
        embed.set_footer(text=t('muteleaderboard.footer', date=utc(since).date().isoformat(), status=t(_STATUS[status])))
        await interaction.followup.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


asyncio.run(bot.add_cog(MuteLeaderboard(bot)))
