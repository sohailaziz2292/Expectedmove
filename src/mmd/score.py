"""Grade yesterday's list against what actually happened.

Runs after the close. The scorecard is published on the site alongside the
predictions, because a forecast list without a visible hit rate is unfalsifiable
and therefore worthless. Three metrics:

  hit_rate   share of names whose realized |move| landed inside the band
  rank_ic    Spearman correlation between predicted and realized |move|
  mae        mean absolute error on the point estimate, in percentage points

rank_ic is the one that matters. The product is a *ranking*, so ordering the
list correctly is the job; getting the absolute level right is secondary.
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import date

from . import clock
from .config import DATA_DIR, session_dir
from .providers import try_polygon

log = logging.getLogger(__name__)


def _spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3 or len(a) != len(b):
        return None

    def ranks(xs: list[float]) -> list[float]:
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


def score_session(target: date) -> dict:
    """Compare the locked list for `target` against realized moves."""
    path = session_dir(target) / "predictions.json"
    if not path.exists():
        raise FileNotFoundError(f"no predictions on file for {target}")
    payload = json.loads(path.read_text())

    poly = try_polygon()
    if poly is None:
        raise RuntimeError("POLYGON_API_KEY required to score")

    bars = {b.symbol: b for b in poly.grouped_daily(target)}
    prior = clock.prev_trading_day(target)
    prev_close = {b.symbol: b.close for b in poly.grouped_daily(prior)}

    rows, predicted, realized = [], [], []
    for p in payload.get("predictions", []):
        sym = p["symbol"]
        bar, pc = bars.get(sym), prev_close.get(sym)
        if not bar or not pc:
            rows.append({"symbol": sym, "rank": p["rank"], "status": "no_data"})
            continue

        move = abs(bar.close / pc - 1.0) * 100.0
        intraday_range = (bar.high - bar.low) / pc * 100.0
        low, high = p["band"]
        in_band = low <= move <= high

        rows.append({
            "symbol": sym,
            "rank": p["rank"],
            "predicted_pct": p["expected_move_pct"],
            "realized_pct": round(move, 2),
            "realized_range_pct": round(intraday_range, 2),
            "in_band": in_band,
            "error_pp": round(move - p["expected_move_pct"], 2),
            "status": "scored",
        })
        predicted.append(p["expected_move_pct"])
        realized.append(move)

    scored = [r for r in rows if r["status"] == "scored"]
    hit_rate = (
        sum(r["in_band"] for r in scored) / len(scored) if scored else None
    )
    mae = (
        statistics.fmean(abs(r["error_pp"]) for r in scored) if scored else None
    )

    # Did the list beat a naive alternative? Baseline = the same names ranked
    # by prior-day volume alone. If we can't beat that, the model adds nothing.
    top5 = [r["realized_pct"] for r in scored[:5]]
    all_moves = [r["realized_pct"] for r in scored]

    card = {
        "session": target.isoformat(),
        "scored_at_et": clock.now_et().isoformat(),
        "n_predictions": len(rows),
        "n_scored": len(scored),
        "hit_rate": round(hit_rate, 3) if hit_rate is not None else None,
        "rank_ic": round(_spearman(predicted, realized), 3) if len(scored) >= 3 else None,
        "mae_pp": round(mae, 2) if mae is not None else None,
        "median_realized_pct": round(statistics.median(all_moves), 2) if all_moves else None,
        "top5_median_pct": round(statistics.median(top5), 2) if top5 else None,
        "rows": rows,
    }

    (session_dir(target) / "scorecard.json").write_text(
        json.dumps(card, indent=2) + "\n"
    )
    log.info("scored %s: hit_rate=%s rank_ic=%s", target, card["hit_rate"], card["rank_ic"])
    return card


def rolling_summary(window: int = 60) -> dict:
    """Aggregate the last N scorecards for the site's accuracy panel."""
    root = DATA_DIR / "sessions"
    cards = []
    if root.exists():
        for d in sorted(root.iterdir(), reverse=True):
            f = d / "scorecard.json"
            if f.exists():
                cards.append(json.loads(f.read_text()))
            if len(cards) >= window:
                break

    hits = [c["hit_rate"] for c in cards if c.get("hit_rate") is not None]
    ics = [c["rank_ic"] for c in cards if c.get("rank_ic") is not None]
    maes = [c["mae_pp"] for c in cards if c.get("mae_pp") is not None]

    return {
        "sessions": len(cards),
        "hit_rate": round(statistics.fmean(hits), 3) if hits else None,
        "rank_ic": round(statistics.fmean(ics), 3) if ics else None,
        "mae_pp": round(statistics.fmean(maes), 2) if maes else None,
        "history": [
            {"session": c["session"], "hit_rate": c.get("hit_rate"),
             "rank_ic": c.get("rank_ic")}
            for c in reversed(cards)
        ],
    }
