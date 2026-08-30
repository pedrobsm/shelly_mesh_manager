"""Configuration — environment variables only (SPEC §3.2)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _float(value: str | None, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    port: int
    scan_subnet: str
    scan_interval_min: int
    demo_mode: bool
    http_timeout_s: float
    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mesh.db"


def load_settings() -> Settings:
    return Settings(
        port=_int(os.getenv("PORT"), 8099),
        scan_subnet=(os.getenv("SCAN_SUBNET") or "").strip(),
        scan_interval_min=_int(os.getenv("SCAN_INTERVAL_MIN"), 15),
        demo_mode=_bool(os.getenv("DEMO_MODE"), False),
        http_timeout_s=_float(os.getenv("HTTP_TIMEOUT_S"), 3.0),
        # DATA_DIR is a development convenience only; the container always uses /data.
        data_dir=Path(os.getenv("DATA_DIR") or "/data"),
    )


settings = load_settings()
