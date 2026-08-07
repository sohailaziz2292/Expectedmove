"""Turn raw daily bars into the handful of features the ranking model uses.

Design rule: every feature here is computable from data we actually have. If an
input is missing the feature is `None` and the model widens its uncertainty
band instead of substituting a default.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from datetime import date

from .providers.base import Bar


@dataclass
class Features:
    symbol: str
    close: float
    dollar_volume: float
    atr_pct: float | None = None          # ATR(14) / close, in percent
    realized_vol_pct: float | None = None  # 20d close-to-close, daily, percent
    rvol: float | None = None              # volume vs 20d median
    prior_move_pct: float | None = None    # last session's close-to-close
    gap_pct: float | None = None           # extended-hours vs prior close
    hist_earnings_move: float | None = None  # median |move| on past report days
    n_earnings_obs: int = 0

    def completeness(self) -> float:
        """Fraction of optional features present — drives the confidence score."""
        optional = [
            self.atr_pct, self.realized_vol_pct, self.rvol,
            self.prior_move_pct, self.gap_pct,
        ]
        return sum(x is not None for x in optional) / len(optional)

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _with_prev_close(bars: list[Bar]) -> list[Bar]:
    prev = None
    for b in bars:
        if b.prev_close is None:
            b.prev_close = prev
        prev = b.close
    return bars


def atr_pct(bars: list[Bar], window: int = 14) -> float | None:
    bars = _with_prev_close(sorted(bars, key=lambda b: b.day))
    usable = [b for b in bars if b.prev_close][-window:]
    if len(usable) < max(5, window // 2) or not usable[-1].close:
        return None
    atr = statistics.fmean(b.true_range for b in usable)
    return atr / usable[-1].close * 100.0


def realized_vol_pct(bars: list[Bar], window: int = 20) -> float | None:
    closes = [b.close for b in sorted(bars, key=lambda b: b.day) if b.close > 0]
    if len(closes) < 8:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))][-window:]
    if len(rets) < 5:
        return None
    return statistics.pstdev(rets) * 100.0


def rvol(bars: list[Bar], window: int = 20) -> float | None:
    vols = [b.volume for b in sorted(bars, key=lambda b: b.day) if b.volume > 0]
    if len(vols) < 6:
        return None
    baseline = statistics.median(vols[-(window + 1):-1])
    if baseline <= 0:
        return None
    return vols[-1] / baseline


def prior_move_pct(bars: list[Bar]) -> float | None:
    bars = _with_prev_close(sorted(bars, key=lambda b: b.day))
    if not bars or not bars[-1].prev_close:
        return None
    return bars[-1].pct_change


def gap_pct(prior_close: float | None, extended_last: float | None) -> float | None:
    if not prior_close or not extended_last:
        return None
    return (extended_last / prior_close - 1.0) * 100.0


def historical_earnings_move(
    bars: list[Bar], report_days: list[date], max_obs: int = 8
) -> tuple[float | None, int]:
    """Median absolute reaction on prior report days.

    Far more informative than generic volatility for a name about to report:
    a stock that routinely moves 12% on earnings will likely do so again,
    regardless of how quiet it has been in between.
    """
    by_day = {b.day: b for b in _with_prev_close(sorted(bars, key=lambda b: b.day))}
    moves: list[float] = []
    for rd in sorted(report_days, reverse=True):
        for offset in (0, 1):  # BMO reacts same day, AMC the next
            bar = by_day.get(rd) if offset == 0 else None
            if bar is None:
                candidates = [d for d in by_day if d > rd]
                bar = by_day[min(candidates)] if candidates else None
            if bar and bar.pct_change is not None:
                moves.append(abs(bar.pct_change))
                break
        if len(moves) >= max_obs:
            break
    if not moves:
        return None, 0
    return statistics.median(moves), len(moves)


def build(
    symbol: str,
    bars: list[Bar],
    extended_last: float | None = None,
    report_days: list[date] | None = None,
) -> Features | None:
    bars = _with_prev_close(sorted(bars, key=lambda b: b.day))
    if not bars:
        return None
    last = bars[-1]
    hist, n_obs = historical_earnings_move(bars, report_days or [])
    return Features(
        symbol=symbol,
        close=last.close,
        dollar_volume=last.close * last.volume,
        atr_pct=atr_pct(bars),
        realized_vol_pct=realized_vol_pct(bars),
        rvol=rvol(bars),
        prior_move_pct=prior_move_pct(bars),
        gap_pct=gap_pct(last.close, extended_last),
        hist_earnings_move=hist,
        n_earnings_obs=n_obs,
    )
