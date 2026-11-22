"""記錄成功的禁言操作，統計故障不影響懲處。"""

import logging
from datetime import datetime, timedelta

import discord


_log = logging.getLogger(__name__)


def _aware_or_none(value):
    return value if isinstance(value, datetime) and value.utcoffset() is not None else None


async def tracked_timeout(
    member: discord.Member,
    until: datetime | timedelta | None,
    *,
    reason: str | None = None,
    actor_id: int | None = None,
    source: str = "bot",
):
    try:
        from globalenv import bot

        cog = bot.get_cog("MuteLeaderboard")
        if cog is not None:
            before_until = _aware_or_none(getattr(member, "timed_out_until", None))
            # discord.py 在第一次 await 前把 timedelta 換成 UTC 期限。使用送出時刻，不混入 HTTP 等待時間。
            after_until = discord.utils.utcnow() + until if isinstance(until, timedelta) else _aware_or_none(until)
    except Exception:
        _log.exception("Could not prepare local timeout observation")
        cog = None

    result = await member.timeout(until, reason=reason)
    if cog is not None:
        try:
            await cog.record_local(member, before_until, after_until, discord.utils.utcnow(), actor_id, source)
        except Exception:
            _log.exception("Could not record successful local timeout")
    return result
