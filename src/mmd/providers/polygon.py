"""Polygon.io adapter.

Polygon is the primary source because `/v2/aggs/grouped` returns every US
equity's daily bar in a single request, which is what makes a full-market
mover scan cheap enough to run on a cron.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from .base import Bar, NotConfigured, Provider, env

log = logging.getLogger(__name__)


class Polygon(Provider):
    def __init__(self) -> None:
        super().__init__(
            name="polygon",
            base_url="https://api.polygon.io",
            api_key=env("POLYGON_API_KEY"),
        )

    def _get(self, path: str, **params):
        return self.get(path, apiKey=self.require_key(), **params)

    def grouped_daily(self, day: date, include_otc: bool = False) -> list[Bar]:
        """Every ticker's OHLCV for one session. Empty list on a market holiday."""
        payload = self._get(
            f"/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}",
            adjusted="true",
            include_otc=str(include_otc).lower(),
        )
        out: list[Bar] = []
        for row in payload.get("results") or []:
            try:
                out.append(
                    Bar(
                        symbol=row["T"],
                        day=day,
                        open=float(row["o"]),
                        high=float(row["h"]),
                        low=float(row["l"]),
                        close=float(row["c"]),
                        volume=float(row["v"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        log.info("polygon grouped %s -> %d bars", day, len(out))
        return out

    def history(self, symbol: str, lookback: int = 60, end: date | None = None) -> list[Bar]:
        end = end or date.today()
        start = end - timedelta(days=int(lookback * 1.8) + 10)
        payload = self._get(
            f"/v2/aggs/ticker/{symbol}/range/1/day/{start.isoformat()}/{end.isoformat()}",
            adjusted="true", sort="asc", limit=5000,
        )
        bars: list[Bar] = []
        prev: float | None = None
        for row in payload.get("results") or []:
            d = date.fromtimestamp(row["t"] / 1000)
            bar = Bar(symbol, d, row["o"], row["h"], row["l"], row["c"], row["v"], prev)
            bars.append(bar)
            prev = bar.close
        return bars[-lookback:]

    def snapshot(self, symbol: str) -> dict:
        """Latest quote incl. extended-hours last trade, used for gap detection."""
        payload = self._get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}")
        return payload.get("ticker") or {}

    def reference(self, symbol: str) -> dict:
        payload = self._get(f"/v3/reference/tickers/{symbol}")
        return payload.get("results") or {}


def try_polygon() -> Polygon | None:
    try:
        p = Polygon()
        p.require_key()
        return p
    except NotConfigured:
        log.warning("polygon disabled: POLYGON_API_KEY not set")
        return None
