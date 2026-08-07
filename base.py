"""Shared HTTP plumbing for market data providers.

Every provider is optional. The pipeline degrades: if a provider is missing a
key or returns an error, its contribution is dropped and the affected fields
are marked absent rather than guessed. Nothing is ever back-filled with a
plausible-looking number.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import requests

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    pass


class NotConfigured(ProviderError):
    """Raised when a provider has no API key. Callers should skip, not fail."""


@dataclass
class Bar:
    symbol: str
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    prev_close: float | None = None

    @property
    def pct_change(self) -> float | None:
        if not self.prev_close:
            return None
        return (self.close / self.prev_close - 1.0) * 100.0

    @property
    def true_range(self) -> float:
        pc = self.prev_close if self.prev_close else self.close
        return max(self.high - self.low, abs(self.high - pc), abs(self.low - pc))


@dataclass
class EarningsEvent:
    symbol: str
    report_date: date
    session: str              # "bmo" | "amc" | "unknown"
    eps_estimate: float | None = None
    revenue_estimate: float | None = None
    confirmed: bool = False
    source: str = ""


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str | None = None
    timeout: int = 20
    max_retries: int = 4
    session: requests.Session = field(default_factory=requests.Session)

    def require_key(self) -> str:
        if not self.api_key:
            raise NotConfigured(f"{self.name}: no API key in environment")
        return self.api_key

    def get(self, path: str, **params: Any) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        delay = 1.0
        last: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code in RETRY_STATUS:
                    wait = float(resp.headers.get("Retry-After") or delay)
                    log.warning(
                        "%s %s -> %s, retry in %.1fs (%d/%d)",
                        self.name, path, resp.status_code, wait,
                        attempt + 1, self.max_retries,
                    )
                    time.sleep(wait)
                    delay = min(delay * 2, 30)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:  # network-level
                last = exc
                time.sleep(delay)
                delay = min(delay * 2, 30)

        raise ProviderError(f"{self.name}: {path} failed after retries: {last}")


def env(*names: str) -> str | None:
    for n in names:
        val = os.environ.get(n)
        if val:
            return val.strip()
    return None
