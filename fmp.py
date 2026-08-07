"""Financial Modeling Prep adapter — used as a cross-check, not a primary.

Two independent earnings calendars disagree often enough that reconciling them
is worth the extra call. Where they conflict on the BMO/AMC flag, the event is
downgraded to `unknown` and its catalyst weight is reduced rather than picking
a winner arbitrarily.
"""

from __future__ import annotations

import logging
from datetime import date

from .base import EarningsEvent, NotConfigured, Provider, env

log = logging.getLogger(__name__)

_TIME_MAP = {"bmo": "bmo", "amc": "amc", "--": "unknown", None: "unknown"}


class FMP(Provider):
    def __init__(self) -> None:
        super().__init__(
            name="fmp",
            base_url="https://financialmodelingprep.com/api/v3",
            api_key=env("FMP_API_KEY"),
        )

    def _get(self, path: str, **params):
        return self.get(path, apikey=self.require_key(), **params)

    def earnings_calendar(self, start: date, end: date) -> list[EarningsEvent]:
        rows = self._get(
            "/earning_calendar", **{"from": start.isoformat(), "to": end.isoformat()}
        ) or []
        events: list[EarningsEvent] = []
        for row in rows:
            sym = (row.get("symbol") or "").strip().upper()
            if not sym or "." in sym:
                continue
            try:
                rd = date.fromisoformat(str(row.get("date"))[:10])
            except ValueError:
                continue
            events.append(
                EarningsEvent(
                    symbol=sym,
                    report_date=rd,
                    session=_TIME_MAP.get(row.get("time"), "unknown"),
                    eps_estimate=row.get("epsEstimated"),
                    revenue_estimate=row.get("revenueEstimated"),
                    confirmed=row.get("epsEstimated") is not None,
                    source="fmp",
                )
            )
        log.info("fmp earnings %s..%s -> %d", start, end, len(events))
        return events


def try_fmp() -> "FMP | None":
    try:
        f = FMP()
        f.require_key()
        return f
    except NotConfigured:
        log.warning("fmp disabled: FMP_API_KEY not set")
        return None
