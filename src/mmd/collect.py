"""The pipeline. Assemble a candidate universe, tag catalysts, score, publish.

Candidate universe is deliberately narrow. Scanning 11,000 tickers every run
would blow through rate limits and mostly surface illiquid names whose "moves"
are one print wide. Instead the universe is the union of four buckets that
actually precede large next-session moves.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta

from . import clock, features, model
from .config import Config, session_dir
from .providers import EarningsEvent, try_finnhub, try_fmp, try_polygon
from .providers.base import Bar

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Universe
# --------------------------------------------------------------------------

def candidate_universe(
    prior_bars: list[Bar],
    prior_prev: dict[str, float],
    earnings: list[EarningsEvent],
    cfg: Config,
) -> dict[str, set[str]]:
    """Return {symbol: {catalyst, ...}}.

    Buckets:
      1. Confirmed earnings before tomorrow's open.
      2. Companies that reported after today's close.
      3. Prior-session outliers — large moves and volume spikes carry over.
      4. A fixed macro-sensitive list that reprices on scheduled data.
    """
    tagged: dict[str, set[str]] = defaultdict(set)

    for ev in earnings:
        if ev.session == "bmo":
            tagged[ev.symbol].add("earnings_bmo")
        elif ev.session == "amc":
            tagged[ev.symbol].add("earnings_amc_tonight")
        else:
            tagged[ev.symbol].add("earnings_bmo")  # unknown hour, treat as BMO

    # Prior-session outliers, filtered for tradability first.
    liquid = [
        b for b in prior_bars
        if b.close >= cfg.min_price and b.close * b.volume >= cfg.min_dollar_volume
    ]
    for b in liquid:
        pc = prior_prev.get(b.symbol)
        if pc:
            b.prev_close = pc
    moved = [b for b in liquid if b.pct_change is not None and abs(b.pct_change) >= 8.0]
    moved.sort(key=lambda b: -abs(b.pct_change or 0))
    for b in moved[: cfg.universe_scan_limit // 3]:
        tagged[b.symbol].add("momentum_carryover")

    by_dollar = sorted(liquid, key=lambda b: -(b.close * b.volume))
    for b in by_dollar[: cfg.universe_scan_limit // 3]:
        tagged[b.symbol].add("momentum_carryover")

    for sym in cfg.macro_sensitive:
        tagged[sym.upper()].add("macro_sensitive")

    log.info("candidate universe: %d symbols", len(tagged))
    return dict(tagged)


def reconcile_earnings(
    primary: list[EarningsEvent], secondary: list[EarningsEvent]
) -> list[EarningsEvent]:
    """Merge two calendars. Disagreement on the session flag downgrades to unknown."""
    merged: dict[tuple[str, date], EarningsEvent] = {}
    for ev in primary:
        merged[(ev.symbol, ev.report_date)] = ev
    for ev in secondary:
        key = (ev.symbol, ev.report_date)
        if key not in merged:
            merged[key] = ev
            continue
        existing = merged[key]
        if existing.session != ev.session and "unknown" not in (existing.session, ev.session):
            log.info("earnings session conflict for %s: %s vs %s",
                     ev.symbol, existing.session, ev.session)
            existing.session = "unknown"
            existing.confirmed = False
        existing.confirmed = existing.confirmed or ev.confirmed
        existing.source = f"{existing.source}+{ev.source}"
    return list(merged.values())


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def build_predictions(target: date, cfg: Config | None = None) -> dict:
    """Produce the watchlist for `target`. Safe to call repeatedly."""
    cfg = cfg or Config.load()
    poly, fin, fmp = try_polygon(), try_finnhub(), try_fmp()
    sources_used: list[str] = []
    warnings: list[str] = []

    if poly is None:
        raise RuntimeError(
            "POLYGON_API_KEY is required — price history has no fallback source"
        )
    sources_used.append("polygon")

    prior = clock.prev_trading_day(target)
    prior_bars = poly.grouped_daily(prior)
    if not prior_bars:
        warnings.append(f"no grouped bars for {prior}; falling back one session")
        prior = clock.prev_trading_day(prior)
        prior_bars = poly.grouped_daily(prior)

    prev_bars = poly.grouped_daily(clock.prev_trading_day(prior))
    prior_prev = {b.symbol: b.close for b in prev_bars}

    # Earnings: tonight's AMC reports and tomorrow's BMO reports both matter.
    earnings: list[EarningsEvent] = []
    if fin:
        try:
            earnings = fin.earnings_calendar(prior, target)
            sources_used.append("finnhub")
        except Exception as exc:  # noqa: BLE001 — degrade, never crash the run
            warnings.append(f"finnhub earnings unavailable: {exc}")
    if fmp:
        try:
            earnings = reconcile_earnings(earnings, fmp.earnings_calendar(prior, target))
            sources_used.append("fmp")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"fmp earnings unavailable: {exc}")
    if not earnings:
        warnings.append("no earnings calendar available — event weighting disabled")

    relevant: list[EarningsEvent] = []
    for ev in earnings:
        if ev.report_date == target and ev.session in ("bmo", "unknown"):
            relevant.append(ev)
        elif ev.report_date == prior and ev.session == "amc":
            ev.session = "amc"
            relevant.append(ev)

    universe = candidate_universe(prior_bars, prior_prev, relevant, cfg)

    # Reporting-tonight names get re-tagged: the reaction lands on `target`.
    amc_prior = {e.symbol for e in relevant if e.report_date == prior and e.session == "amc"}
    for sym in amc_prior:
        universe.setdefault(sym, set()).discard("earnings_amc_tonight")
        universe.setdefault(sym, set()).add("earnings_amc_prior")

    bars_by_symbol = {b.symbol: b for b in prior_bars}
    scored: list[model.Prediction] = []
    skipped = 0

    for symbol, tags in universe.items():
        bar = bars_by_symbol.get(symbol)
        if bar is None:
            skipped += 1
            continue
        try:
            history = poly.history(symbol, lookback=cfg.history_days, end=prior)
        except Exception as exc:  # noqa: BLE001
            log.debug("history failed for %s: %s", symbol, exc)
            skipped += 1
            continue
        if len(history) < 15:
            skipped += 1
            continue

        extended_last = None
        if "earnings_amc_prior" in tags or "momentum_carryover" in tags:
            try:
                snap = poly.snapshot(symbol)
                extended_last = (snap.get("min") or {}).get("c") or (
                    snap.get("lastTrade") or {}
                ).get("p")
            except Exception:  # noqa: BLE001
                pass

        report_days = [e.report_date for e in relevant if e.symbol == symbol]
        feat = features.build(symbol, history, extended_last, report_days)
        if feat is None:
            skipped += 1
            continue

        pred = model.score_one(feat, sorted(tags))
        if pred is None:
            skipped += 1
            continue
        pred.note = _explain(tags, feat)
        scored.append(pred)

    ranked = model.rank(scored, limit=cfg.list_size, max_per_catalyst=cfg.max_per_catalyst)

    return {
        "schema_version": 3,
        "target_session": target.isoformat(),
        "prior_session": prior.isoformat(),
        "generated_at_et": clock.now_et().isoformat(),
        "phase": clock.resolve().phase.value,
        "locked": False,
        "locked_at_et": None,
        "sources": sorted(set(sources_used)),
        "warnings": warnings,
        "counts": {
            "universe": len(universe),
            "scored": len(scored),
            "skipped": skipped,
            "published": len(ranked),
        },
        "macro_events": macro_events_for(target, cfg),
        "predictions": [p.as_dict() for p in ranked],
        "disclaimer": (
            "Expected absolute move only — no directional view, no recommendation. "
            "Informational research output, not investment advice."
        ),
    }


def _explain(tags: set[str], f: features.Features) -> str:
    parts = []
    if "earnings_bmo" in tags:
        parts.append("reports before the open")
    if "earnings_amc_prior" in tags:
        parts.append("reported after yesterday's close")
    if "earnings_amc_tonight" in tags:
        parts.append("reports after today's close")
    if f.hist_earnings_move and f.n_earnings_obs >= 3:
        parts.append(
            f"median {f.hist_earnings_move:.1f}% reaction over {f.n_earnings_obs} prior reports"
        )
    if f.gap_pct is not None and abs(f.gap_pct) > 1.5:
        parts.append(f"gapping {f.gap_pct:+.1f}% after hours")
    if f.rvol and f.rvol > 2:
        parts.append(f"{f.rvol:.1f}x normal volume")
    if "macro_sensitive" in tags and not parts:
        parts.append("reprices on scheduled macro data")
    return "; ".join(parts).capitalize() or "Elevated baseline volatility"


def macro_events_for(target: date, cfg: Config) -> list[dict]:
    """Scheduled releases that move the whole tape, from config."""
    out = []
    for name, spec in (cfg.macro_releases or {}).items():
        for iso in spec.get("dates", []):
            if iso == target.isoformat():
                out.append({
                    "name": name,
                    "time_et": spec.get("time_et", "08:30"),
                    "source": spec.get("source", ""),
                })
    return sorted(out, key=lambda e: e["time_et"])


def write(payload: dict, target: date, lock: bool = False) -> dict:
    """Persist. Locking is one-way: a locked file is never overwritten."""
    path = session_dir(target) / "predictions.json"

    if path.exists():
        existing = json.loads(path.read_text())
        if existing.get("locked"):
            log.warning("%s already locked at %s — refusing to overwrite",
                        target, existing.get("locked_at_et"))
            return existing

    if lock:
        payload["locked"] = True
        payload["locked_at_et"] = clock.now_et().isoformat()

    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    log.info("wrote %s (%d rows, locked=%s)",
             path, len(payload.get("predictions", [])), payload["locked"])
    return payload
