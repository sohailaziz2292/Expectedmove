"""Build `site/feed.json` — the only file the front end reads.

Visibility rules are enforced here, server-side, not in JavaScript. Between the
opening and closing bell the next session's file is not merely hidden; it is not
written into the feed at all, so it cannot leak through the browser dev tools.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from . import clock, score
from .config import SITE_DIR, session_dir

log = logging.getLogger(__name__)


def _load(day: date, name: str) -> dict | None:
    path = session_dir(day) / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        log.error("corrupt %s for %s", name, day)
        return None


def _staleness(payload: dict | None, cycle: clock.Cycle) -> dict:
    """Tell the reader plainly whether the list on screen is trustworthy."""
    if payload is None:
        return {"state": "missing",
                "message": "No list published for this session yet."}

    if cycle.phase in (clock.Phase.LOCKED, clock.Phase.SESSION) and not payload.get("locked"):
        return {"state": "unlocked",
                "message": "Published after the 8:25 ET freeze. Treat as provisional."}

    if payload.get("warnings"):
        return {"state": "degraded",
                "message": f"Built with {len(payload['warnings'])} data source warning(s)."}

    return {"state": "ok", "message": ""}


def build_feed() -> dict:
    cycle = clock.resolve()

    current: dict | None = None
    upcoming: dict | None = None
    results: dict | None = None

    if cycle.phase in (clock.Phase.DRAFT, clock.Phase.REFRESH,
                       clock.Phase.FINAL, clock.Phase.LOCKED):
        # Pre-market and overnight: the target session's list is the headline.
        upcoming = _load(cycle.target_session, "predictions.json")

    elif cycle.phase is clock.Phase.SESSION:
        # Bell to bell: today only. Tomorrow is deliberately absent.
        current = _load(cycle.display_session, "predictions.json")

    elif cycle.phase in (clock.Phase.SETTLE, clock.Phase.CLOSED):
        current = _load(cycle.display_session, "predictions.json")
        results = _load(cycle.display_session, "scorecard.json")

    headline = upcoming or current
    prior = clock.prev_trading_day(cycle.display_session)

    feed = {
        "schema_version": 3,
        "built_at_et": clock.now_et().isoformat(),
        "cycle": cycle.as_dict(),
        "seconds_to_lock": clock.seconds_to_lock(),
        "next_lock_et": "08:25",
        "deadline_et": "08:30",
        "status": _staleness(headline, cycle),
        "list": headline,
        "scorecard": results or _load(prior, "scorecard.json"),
        "accuracy": score.rolling_summary(),
        "phase_copy": PHASE_COPY.get(cycle.phase.value, {}),
    }

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "feed.json").write_text(json.dumps(feed, indent=2) + "\n")
    log.info("feed built: phase=%s rows=%d",
             cycle.phase.value, len((headline or {}).get("predictions", [])))
    return feed


PHASE_COPY = {
    "draft": {
        "label": "Building tomorrow",
        "detail": "First pass on the next session. Names will be added and dropped overnight.",
    },
    "refresh": {
        "label": "Refreshing overnight",
        "detail": "Rebuilding as earnings confirmations and after-hours prices arrive.",
    },
    "final": {
        "label": "Final pass",
        "detail": "Last rebuild before the 8:25 ET freeze.",
    },
    "locked": {
        "label": "Locked",
        "detail": "Frozen at 8:25 ET. This list will not change before the bell.",
    },
    "session": {
        "label": "Market open",
        "detail": "Today's list, read-only. Tomorrow's opens at 5:00 PM ET.",
    },
    "settle": {
        "label": "Scoring",
        "detail": "Grading today's list against realized moves.",
    },
    "closed": {
        "label": "Closed",
        "detail": "Markets are closed. Next list opens at 5:00 PM ET the evening before.",
    },
}
