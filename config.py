"""Configuration loading. File values are defaults; env vars win."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("MMD_DATA_DIR", REPO_ROOT / "data"))
SITE_DIR = Path(os.environ.get("MMD_SITE_DIR", REPO_ROOT / "site"))


@dataclass
class Config:
    list_size: int = 25
    max_per_catalyst: int = 12
    min_price: float = 1.50
    min_dollar_volume: float = 5_000_000
    history_days: int = 60
    universe_scan_limit: int = 600
    macro_sensitive: list[str] = field(default_factory=list)
    macro_releases: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or REPO_ROOT / "config.yaml"
        raw = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text()) or {}
        known = {k: v for k, v in raw.items() if k in cls.__annotations__}
        cfg = cls(**known)
        if os.environ.get("MMD_LIST_SIZE"):
            cfg.list_size = int(os.environ["MMD_LIST_SIZE"])
        return cfg


def session_dir(day) -> Path:
    d = DATA_DIR / "sessions" / day.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d
