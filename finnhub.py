"""Finnhub adapter — earnings calendar and analyst rating changes.

The earnings calendar is the single most important input: a confirmed
before-open report is the strongest predictor of a large next-session move.
Finnhub's calendar carries the BMO/AMC hour flag, which most free feeds drop.
"""

from __future__ import annotations

import logging
from datetime import date

from .base import EarningsEvent, NotConfigured, Provider, env

log = logging.getLogger(__name__)

_HOUR_MAP = {"bmo": "bmo", "amc": "amc", "dmh": "intraday", "": "unknown"}


class Finnhub(Provider):
    def __init__(self) -> None:
        super().__init__(
            name="finnhub",
            base_url="https://finnhub.io/api/v1",
            api_key=env("FINNHUB_API_KEY"),
        )

    def _get(self, path: str, **params):
        return self.get(path, token=self.require_key(), **params)

    def earnings_calendar(self, start: date, end: date) -> list[EarningsEvent]:
        payload = self._get(
            "/calendar/earnings", **{"from": start.isoformat(), "to": end.isoformat()}
        )
        events: list[EarningsEvent] = []
        for row in payload.get("earningsCalendar") or []:
            sym = (row.get("symbol") or "").strip().upper()
            if not sym:
                continue
            try:
                rd = date.fromisoformat(row["date"])
            except (KeyError, ValueError):
                continue
            events.append(
                EarningsEvent(
                    symbol=sym,
                    report_date=rd,
                    session=_HOUR_MAP.get((row.get("hour") or "").lower(), "unknown"),
                    eps_estimate=row.get("epsEstimate"),
                    revenue_estimate=row.get("revenueEstimate"),
                    confirmed=bool(row.get("epsEstimate") is not None),
                    source="finnhub",
                )
            )
        log.info("finnhub earnings %s..%s -> %d", start, end, len(events))
        return events

    def rating_changes(self, symbol: str) -> list[dict]:
        return self._get("/stock/upgrade-downgrade", symbol=symbol) or []

    def company_news(self, symbol: str, start: date, end: date) -> list[dict]:
        return self._get(
            "/company-news", symbol=symbol,
            **{"from": start.isoformat(), "to": end.isoformat()},
        ) or []

    def profile(self, symbol: str) -> dict:
        return self._get("/stock/profile2", symbol=symbol) or {}


def try_finnhub() -> "Finnhub | None":
    try:
        f = Finnhub()
        f.require_key()
        return f
    except NotConfigured:
        log.warning("finnhub disabled: FINNHUB_API_KEY not set")
        return None
