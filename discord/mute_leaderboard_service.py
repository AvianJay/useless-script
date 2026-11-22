"""禁言排行榜的事件儲存與重播；不載入 Discord client。"""
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import astuple, dataclass
from itertools import groupby
import time


UNKNOWN = -1  # 沒有提供期限，和明確解除不同
CLEAR = 0
AUTOMOD_ACTOR = 0  # Discord snowflake 一定大於零
MATCH_MS = 15_000
WINDOW_MS = 45 * 86_400_000


def now_ms():
    return time.time_ns() // 1_000_000


@dataclass(frozen=True)
class Observation:
    ref: str
    guild: int
    target: int
    at: int
    before: int
    after: int
    channel: str  # audit / local / member / auto_live / auto_audit
    actor: int | None = None  # 排行歸屬的執行者
    issuer: int | None = None  # 實際呼叫 Discord 的帳號
    source: str = ''


_ORDER = {'audit': 0, 'local': 1, 'member': 2, 'auto_audit': 3, 'auto_live': 4}


def _compatible(group, event):
    for other in group:
        if other.channel == event.channel or abs(other.at - event.at) > MATCH_MS:
            return False
        if None not in (other.issuer, event.issuer) and other.issuer != event.issuer:
            return False
        native = event.channel.startswith('auto_') or other.channel.startswith('auto_')
        if native and (other.channel == 'local' or event.channel == 'local'):
            return False
        if native and other.channel == 'audit' and other.issuer is not None:
            # 需要原生審核紀錄的 issuer 佐證，不能只憑接近的時間歸給 AutoMod。
            if not any(e.channel == 'auto_audit' and e.issuer == other.issuer for e in (*group, event)):
                return False
        if UNKNOWN not in (other.before, event.before) and other.before != event.before:
            # 本機呼叫前讀到的成員快取可能過時；允許用相同的 bot 身分、明確期限配對。
            if not ({other.channel, event.channel} == {'audit', 'local'} and other.issuer is not None
                    and other.issuer == event.issuer and min(other.after, event.after) > 0):
                return False
        if UNKNOWN not in (other.after, event.after):
            tolerance = MATCH_MS if 'auto_live' in (other.channel, event.channel) else 1
            if (other.after == CLEAR) != (event.after == CLEAR) or abs(other.after - event.after) > tolerance:
                return False
        elif native and any(e.after != UNKNOWN and e.after <= e.at for e in (other, event)):
            return False
    return True


def transitions(observations):
    """依證據種類固定順序做一對一配對，結果不受匯入順序影響。"""
    buckets = defaultdict(list)
    groups = []
    ordered = sorted(observations, key=lambda e: (_ORDER[e.channel], e.at, e.ref))
    for index, event in enumerate(ordered):
        bucket = event.at // MATCH_MS
        matches = [g for k in (bucket - 1, bucket, bucket + 1) for g in buckets.get(k, ())
                   if _compatible(g, event)]
        if len(matches) > 1 and UNKNOWN not in (event.before, event.after):
            # 同一期限快速解除又重設時，單看一筆會有兩個候選；兩邊都有完整、等長的
            # 狀態序列才依各自順序一對一配對，不讓較晚到達的 gateway 事件改寫實際時間。
            matches.sort(key=lambda g: (g[0].at, g[0].ref))
            latest = matches[-1][0].at + MATCH_MS
            series = []
            for i in range(index, len(ordered)):
                e = ordered[i]
                if e.channel != event.channel or e.at > latest:
                    break
                if (e.before, e.after) == (event.before, event.after):
                    series.append(e)
            if len(series) == len(matches) and all(_compatible(g, e) for g, e in zip(matches, series)):
                matches = matches[:1]
        if len(matches) == 1:
            matches[0].append(event)
        else:
            group = [event]
            groups.append(group)
            buckets[bucket].append(group)

    result = []
    for group in groups:
        # 審核日誌給實際時間，成員更新給實際狀態，本機操作給真正的指令執行者。
        by = {e.channel: e for e in group}
        clock = by.get('audit') or by.get('auto_audit') or by.get('local') or group[0]
        states = [by[k] for k in ('audit', 'member', 'local', 'auto_live') if k in by]
        before = next((e.before for e in states if e.before != UNKNOWN), UNKNOWN)
        after = next((e.after for e in states if e.after != UNKNOWN), UNKNOWN)
        observed_before = any(e.channel in ('audit', 'member') and e.before != UNKNOWN for e in group)
        local = by.get('local')
        actor = (AUTOMOD_ACTOR if 'auto_audit' in by or 'auto_live' in by else
                 local.actor if local and local.actor is not None else
                 next((e.actor for e in group if e.actor is not None), None))
        excluded = (local.source == 'hacked_cross_guild' if local else
                    any(e.source == 'hacked_cross_guild' for e in group))
        result.append((clock.at, clock.ref, before, after, actor, excluded, observed_before))
    return sorted(result, key=lambda row: (row[0], row[1]))


def reduce_target(events, as_of):
    """重建一位成員的區間，傳回次數、毫秒、執行者次數。"""
    count = duration = 0
    actors = Counter()
    until, last, included = CLEAR, 0, False
    for at, _, before, after, actor, excluded, observed_before in transitions(events):
        if at > as_of:
            break
        if included:
            duration += max(0, min(at, until) - last)
        last = at
        active = until > at
        previous = before if observed_before or not active else until
        if after == UNKNOWN:  # 原生 AutoMod 審核只確認執行，不證明時長
            new = True
            included = False
        elif after <= at:
            new = included = False
        else:
            new = (not active or after != until) and (previous <= at or after > previous)
            if excluded:
                included = False
            elif new:
                included = True
            elif not active:
                included = False  # 只看到舊處分縮短，不推測其開始或來源
        until = after
        if new and not excluded:
            count += 1
            if actor is not None:
                actors[actor] += 1
    if included:
        duration += max(0, min(as_of, until) - last)
    return count, duration, actors


_INSERT = '''INSERT INTO mute_observations
    (ref, guild, target, at, prior, expiry, channel, actor, issuer, source)
    VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ref) DO UPDATE SET
        actor = COALESCE(mute_observations.actor, excluded.actor),
        issuer = COALESCE(mute_observations.issuer, excluded.issuer),
        source = CASE WHEN mute_observations.source = '' THEN excluded.source ELSE mute_observations.source END
    WHERE excluded.channel IN ('audit', 'auto_audit') AND (
        (mute_observations.actor IS NULL AND excluded.actor IS NOT NULL) OR
        (mute_observations.issuer IS NULL AND excluded.issuer IS NOT NULL) OR
        (mute_observations.source = '' AND excluded.source != ''))'''


class MuteStore:
    def __init__(self, connect):
        self.connect = connect

    def initialize(self):
        with closing(self.connect()) as conn, conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS mute_observations (
                    ref TEXT PRIMARY KEY, guild INTEGER NOT NULL, target INTEGER NOT NULL,
                    at INTEGER NOT NULL, prior INTEGER NOT NULL, expiry INTEGER NOT NULL,
                    channel TEXT NOT NULL, actor INTEGER, issuer INTEGER, source TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS mute_by_target ON mute_observations(guild, target, at);
                CREATE TABLE IF NOT EXISTS mute_install (
                    id INTEGER PRIMARY KEY CHECK (id=1), since INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS mute_backfill (
                    guild INTEGER NOT NULL, stream TEXT NOT NULL, since INTEGER NOT NULL,
                    boundary INTEGER NOT NULL, cursor INTEGER NOT NULL DEFAULT 0,
                    done INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(guild, stream));
            ''')
            conn.execute('INSERT OR IGNORE INTO mute_install VALUES (1, ?)', (now_ms(),))

    def record(self, event):
        with closing(self.connect()) as conn, conn:
            conn.execute(_INSERT, astuple(event))

    def coverage(self, guild, joined_at=0):
        with closing(self.connect()) as conn:
            since = max(conn.execute('SELECT since FROM mute_install WHERE id=1').fetchone()[0], joined_at)
            row = conn.execute('SELECT MIN(since), SUM(done) FROM mute_backfill WHERE guild=?', (guild,)).fetchone()
            complete = row[1] == 2
            return min(since, row[0]) if complete else since, complete

    def begin(self, guild, at):
        with closing(self.connect()) as conn, conn:
            conn.executemany('INSERT OR IGNORE INTO mute_backfill(guild, stream, since, boundary) VALUES (?,?,?,?)',
                             [(guild, stream, at - WINDOW_MS, at) for stream in ('audit', 'auto_audit')])
            return conn.execute('SELECT stream, since, boundary, cursor FROM mute_backfill WHERE guild=? AND NOT done',
                                (guild,)).fetchall()

    def page(self, guild, stream, events, cursor, done):
        with closing(self.connect()) as conn, conn:
            conn.executemany(_INSERT, (astuple(e) for e in events))
            conn.execute('UPDATE mute_backfill SET cursor=?,done=? WHERE guild=? AND stream=?',
                         (cursor, int(done), guild, stream))

    def ranking(self, guild, metric, as_of):
        scores = Counter()
        with closing(self.connect()) as conn:
            rows = conn.execute('''SELECT ref,guild,target,at,prior,expiry,channel,actor,issuer,source
                                   FROM mute_observations WHERE guild=? ORDER BY target,at''', (guild,))
            for target, observations in groupby((Observation(*r) for r in rows), key=lambda e: e.target):
                count, duration, actors = reduce_target(observations, as_of)
                if metric == 'actor':
                    scores.update(actors)
                else:
                    score = duration if metric == 'duration' else count
                    if score:
                        scores[target] = score
        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:10]
