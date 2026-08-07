"""Rank tomorrow's likely biggest movers.

What this model predicts and what it does not
---------------------------------------------
It predicts **expected absolute move** — how far a name is likely to travel,
in either direction. It does not predict *direction*, and deliberately so.
Direction on an earnings reaction is close to a coin flip conditional on
public information; magnitude is genuinely forecastable from implied vol,
historical reaction size, and event type. Ranking magnitude is a claim the
data can support, so that is the only claim made.

Every score decomposes into named contributions that ship with the output, so
any row on the site can be traced back to the inputs that produced it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from .features import Features

Catalyst = Literal[
    "earnings_bmo", "earnings_amc_prior", "earnings_amc_tonight",
    "macro_sensitive", "rating_change", "momentum_carryover", "none",
]

# Multipliers on baseline volatility. Calibrated by `mmd backtest` against
# realized moves; re-fit quarterly and committed to config.yaml.
CATALYST_WEIGHT: dict[str, float] = {
    "earnings_bmo": 3.10,
    "earnings_amc_prior": 2.90,
    "earnings_amc_tonight": 1.15,   # the move lands *after* the session
    "rating_change": 1.55,
    "momentum_carryover": 1.40,
    "macro_sensitive": 1.20,
    "none": 1.00,
}

MIN_PRICE = 1.50
MIN_DOLLAR_VOLUME = 5_000_000


@dataclass
class Prediction:
    symbol: str
    rank: int
    expected_move_pct: float
    band_low_pct: float
    band_high_pct: float
    confidence: float
    catalysts: list[str]
    drivers: dict[str, float] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "rank": self.rank,
            "expected_move_pct": round(self.expected_move_pct, 2),
            "band": [round(self.band_low_pct, 2), round(self.band_high_pct, 2)],
            "confidence": round(self.confidence, 2),
            "catalysts": self.catalysts,
            "drivers": {k: round(v, 3) for k, v in self.drivers.items()},
            "note": self.note,
        }


def _baseline_vol(f: Features) -> float | None:
    """One-day expected range, before any event adjustment."""
    candidates = []
    if f.atr_pct is not None:
        candidates.append(f.atr_pct)
    if f.realized_vol_pct is not None:
        candidates.append(f.realized_vol_pct * 1.15)  # close-to-close -> range
    if not candidates:
        return None
    return sum(candidates) / len(candidates)


def _liquidity_ok(f: Features) -> bool:
    return f.close >= MIN_PRICE and f.dollar_volume >= MIN_DOLLAR_VOLUME


def score_one(
    f: Features,
    catalysts: list[str],
    implied_move_pct: float | None = None,
) -> Prediction | None:
    """Score a single name. Returns None if it fails the liquidity filter."""
    if not _liquidity_ok(f):
        return None

    baseline = _baseline_vol(f)
    if baseline is None:
        return None

    weight = max((CATALYST_WEIGHT.get(c, 1.0) for c in catalysts), default=1.0)
    drivers: dict[str, float] = {"baseline_vol": baseline, "catalyst_weight": weight}
    expected = baseline * weight

    # Historical earnings reactions beat generic volatility for reporting names.
    is_earnings = any(c.startswith("earnings") for c in catalysts)
    if is_earnings and f.hist_earnings_move and f.n_earnings_obs >= 3:
        blend = min(0.70, 0.20 + 0.08 * f.n_earnings_obs)
        expected = blend * f.hist_earnings_move + (1 - blend) * expected
        drivers["hist_earnings_move"] = f.hist_earnings_move
        drivers["hist_weight"] = blend

    # An options-implied move is the market's own forecast — trust it most.
    if implied_move_pct:
        expected = 0.65 * implied_move_pct + 0.35 * expected
        drivers["implied_move"] = implied_move_pct

    # Unusual volume signals attention that tends to persist one session.
    if f.rvol and f.rvol > 1.2:
        bump = 1.0 + 0.18 * math.log(min(f.rvol, 12.0))
        expected *= bump
        drivers["rvol_bump"] = bump

    # An overnight gap is already-realized move; it raises the day's range.
    if f.gap_pct is not None and abs(f.gap_pct) > 1.0:
        expected = max(expected, abs(f.gap_pct) * 1.25)
        drivers["gap_pct"] = f.gap_pct

    # Confidence: how much of the input set we actually had.
    confidence = 0.30 + 0.40 * f.completeness()
    if implied_move_pct:
        confidence += 0.20
    if is_earnings and f.n_earnings_obs >= 4:
        confidence += 0.10
    confidence = min(confidence, 0.95)

    # Band widens as confidence falls. Scored honestly the next day.
    spread = 0.45 + 0.55 * (1 - confidence)
    return Prediction(
        symbol=f.symbol,
        rank=0,
        expected_move_pct=expected,
        band_low_pct=expected * max(0.25, 1 - spread),
        band_high_pct=expected * (1 + spread),
        confidence=confidence,
        catalysts=sorted(catalysts) or ["none"],
        drivers=drivers,
    )


def rank(
    scored: list[Prediction],
    limit: int = 25,
    max_per_catalyst: int | None = None,
) -> list[Prediction]:
    """Sort by expected move, optionally capping any single catalyst bucket."""
    ordered = sorted(scored, key=lambda p: -p.expected_move_pct)
    if max_per_catalyst:
        counts: dict[str, int] = {}
        kept: list[Prediction] = []
        for p in ordered:
            key = p.catalysts[0]
            if counts.get(key, 0) >= max_per_catalyst:
                continue
            counts[key] = counts.get(key, 0) + 1
            kept.append(p)
        ordered = kept
    ordered = ordered[:limit]
    for i, p in enumerate(ordered, start=1):
        p.rank = i
    return ordered
