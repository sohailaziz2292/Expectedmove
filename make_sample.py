"""Generate a synthetic session so the site renders before any API key exists.

Everything it writes is clearly marked sample data. Run `mmd build` with real
keys to replace it.
"""
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mmd import clock, publish  # noqa: E402
from mmd.config import session_dir  # noqa: E402

random.seed(7)

CATALYSTS = [
    (["earnings_bmo"], "Reports before the open; median 8.4% reaction over 6 prior reports"),
    (["earnings_amc_prior"], "Reported after yesterday's close; gapping after hours"),
    (["momentum_carryover"], "3.1x normal volume after yesterday's move"),
    (["macro_sensitive"], "Reprices on scheduled macro data"),
]
SYMBOLS = ["AXON", "DKNG", "TTWO", "ELF", "RIVN", "AFRM", "CELH", "SMCI", "MARA",
           "UPST", "PLTR", "ROKU", "CVNA", "SOFI", "LYFT", "CHWY", "PATH", "IONQ",
           "NVDA", "KRE"]


def main() -> None:
    cycle = clock.resolve()
    target = cycle.target_session
    preds = []
    for i, sym in enumerate(SYMBOLS):
        tags, note = random.choice(CATALYSTS)
        em = round(random.uniform(3.0, 14.0) * (1 - i * 0.025), 2)
        conf = round(random.uniform(0.45, 0.9), 2)
        spread = 0.45 + 0.55 * (1 - conf)
        preds.append({
            "symbol": sym, "rank": 0,
            "expected_move_pct": em,
            "band": [round(em * max(0.25, 1 - spread), 2), round(em * (1 + spread), 2)],
            "confidence": conf, "catalysts": tags,
            "drivers": {"baseline_vol": 2.4, "catalyst_weight": 3.1},
            "note": note,
        })
    preds.sort(key=lambda p: -p["expected_move_pct"])
    for i, p in enumerate(preds, 1):
        p["rank"] = i

    payload = {
        "schema_version": 3,
        "target_session": target.isoformat(),
        "prior_session": clock.prev_trading_day(target).isoformat(),
        "generated_at_et": clock.now_et().isoformat(),
        "phase": cycle.phase.value,
        "locked": cycle.locked,
        "locked_at_et": clock.now_et().isoformat() if cycle.locked else None,
        "sources": ["SAMPLE DATA — no API keys configured"],
        "warnings": ["This is synthetic sample data. Set POLYGON_API_KEY and run `mmd build`."],
        "counts": {"universe": 412, "scored": 388, "skipped": 24, "published": len(preds)},
        "macro_events": [],
        "predictions": preds,
        "disclaimer": "Expected absolute move only — no directional view, no recommendation.",
    }
    (session_dir(target) / "predictions.json").write_text(json.dumps(payload, indent=2) + "\n")

    prior = clock.prev_trading_day(target)
    rows = []
    for i, p in enumerate(preds[:12], 1):
        realized = round(abs(random.gauss(p["expected_move_pct"], 3.0)), 2)
        rows.append({
            "symbol": p["symbol"], "rank": i,
            "predicted_pct": p["expected_move_pct"], "realized_pct": realized,
            "realized_range_pct": round(realized * 1.4, 2),
            "in_band": p["band"][0] <= realized <= p["band"][1],
            "error_pp": round(realized - p["expected_move_pct"], 2),
            "status": "scored",
        })
    card = {
        "session": prior.isoformat(), "scored_at_et": clock.now_et().isoformat(),
        "n_predictions": len(rows), "n_scored": len(rows),
        "hit_rate": round(sum(r["in_band"] for r in rows) / len(rows), 3),
        "rank_ic": 0.41, "mae_pp": 2.6,
        "median_realized_pct": 5.1, "top5_median_pct": 7.8, "rows": rows,
    }
    (session_dir(prior) / "scorecard.json").write_text(json.dumps(card, indent=2) + "\n")

    feed = publish.build_feed()
    print(f"sample written: phase={feed['cycle']['phase']} "
          f"rows={len((feed.get('list') or {}).get('predictions', []))}")


if __name__ == "__main__":
    main()
