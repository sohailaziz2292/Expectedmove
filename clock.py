"""Trading calendar and publication-phase logic.

Everything in this project is anchored to America/New_York, never to UTC and
never to the runner's local time. GitHub Actions cron fires in UTC and does not
observe US daylight saving, so workflows over-schedule and this module decides
whether a given run should actually do anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# NYSE full-day closures. Update each December; `scripts/refresh_holidays.py`
# regenerates this from the NYSE calendar feed.
MARKET_HOLIDAYS: set[date] = {
    date(2026, 1, 1),   date(2026, 1, 19),  date(2026, 2, 16),
    date(2026, 4, 3),   date(2026, 5, 25),  date(2026, 6, 19),
    date(2026, 7, 3),   date(2026, 9, 7),   date(2026, 11, 26),
    date(2026, 12, 25),
    date(2027, 1, 1),   date(2027, 1, 18),  date(2027, 2, 15),
    date(2027, 3, 26),  date(2027, 5, 31),  date(2027, 6, 18),
    date(2027, 7, 5),   date(2027, 9, 6),   date(2027, 11, 25),
    date(2027, 12, 24),
}

# 1:00pm ET closes. The lock still happens at 08:25 on these days.
HALF_DAYS: set[date] = {
    date(2026, 11, 27), date(2026, 12, 24),
    date(2027, 11, 26),
}


class Phase(str, Enum):
    """Where we are in the publish/lock lifecycle."""

    DRAFT = "draft"        # 17:00–20:00 ET — next session's list opens
    REFRESH = "refresh"    # 20:00–07:40 ET — overnight incremental updates
    FINAL = "final"        # 07:40–08:25 ET — last full rebuild
    LOCKED = "locked"      # 08:25–09:30 ET — frozen, published, no edits
    SESSION = "session"    # 09:30–close — read-only, today only
    SETTLE = "settle"      # close–17:00 ET — score the day, no new list
    CLOSED = "closed"      # weekends/holidays outside the above


# Phase boundaries as ET wall-clock times.
DRAFT_OPEN = time(17, 0)
REFRESH_OPEN = time(20, 0)
FINAL_OPEN = time(7, 40)
LOCK_AT = time(8, 25)      # hard deadline: list must be on disk before 08:30
OPEN_BELL = time(9, 30)


def now_et() -> datetime:
    return datetime.now(ET)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in MARKET_HOLIDAYS


def close_time(d: date) -> time:
    return time(13, 0) if d in HALF_DAYS else time(16, 0)


def next_trading_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while not is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def prev_trading_day(d: date) -> date:
    prv = d - timedelta(days=1)
    while not is_trading_day(prv):
        prv -= timedelta(days=1)
    return prv


@dataclass(frozen=True)
class Cycle:
    """The resolved state of a single moment in ET."""

    at: datetime
    phase: Phase
    target_session: date      # the session the current list is *for*
    display_session: date     # the session the site should show
    locked: bool

    @property
    def should_build(self) -> bool:
        """Whether a pipeline run at this moment may write predictions."""
        return self.phase in (Phase.DRAFT, Phase.REFRESH, Phase.FINAL)

    @property
    def should_score(self) -> bool:
        return self.phase is Phase.SETTLE

    def as_dict(self) -> dict:
        return {
            "at_et": self.at.isoformat(),
            "phase": self.phase.value,
            "target_session": self.target_session.isoformat(),
            "display_session": self.display_session.isoformat(),
            "locked": self.locked,
        }


def resolve(at: datetime | None = None) -> Cycle:
    """Map an instant to its publication phase.

    The two rules the site depends on:
      1. Between the opening and closing bell, only the current session's list
         is visible. Tomorrow does not exist yet.
      2. Tomorrow's list appears at 17:00 ET and is frozen at 08:25 ET.
    """
    at = (at or now_et()).astimezone(ET)
    today, clock = at.date(), at.timetz().replace(tzinfo=None)
    trading = is_trading_day(today)

    # --- Evening: tomorrow's list opens ------------------------------------
    if clock >= DRAFT_OPEN:
        target = next_trading_day(today)
        phase = Phase.DRAFT if clock < REFRESH_OPEN else Phase.REFRESH
        return Cycle(at, phase, target, target, locked=False)

    # --- Overnight / pre-market: still building for today (or next open) ---
    if clock < OPEN_BELL:
        target = today if trading else next_trading_day(today)
        if clock < FINAL_OPEN:
            phase = Phase.REFRESH
        elif clock < LOCK_AT:
            phase = Phase.FINAL
        else:
            phase = Phase.LOCKED
        return Cycle(at, phase, target, target, locked=phase is Phase.LOCKED)

    if not trading:
        nxt = next_trading_day(today)
        return Cycle(at, Phase.CLOSED, nxt, nxt, locked=False)

    # --- Regular session: today only, read-only ---------------------------
    if clock < close_time(today):
        return Cycle(at, Phase.SESSION, today, today, locked=True)

    # --- Between the bell and 17:00: score, show results ------------------
    return Cycle(at, Phase.SETTLE, today, today, locked=True)


def seconds_to_lock(at: datetime | None = None) -> int:
    """Seconds until the next 08:25 ET freeze. Negative once past it."""
    at = (at or now_et()).astimezone(ET)
    deadline = datetime.combine(at.date(), LOCK_AT, tzinfo=ET)
    if at >= deadline:
        deadline = datetime.combine(next_trading_day(at.date()), LOCK_AT, tzinfo=ET)
    return int((deadline - at).total_seconds())
