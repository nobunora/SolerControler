"""Typed input and policy boundary for PV performance calibration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PvCalibrationInput:
    arrays: list[Any]
    rows: list[dict[str, Any]]
    target_date: str
    latitude: float
    longitude: float
    timezone: str


@dataclass(frozen=True)
class PvCalibrationPolicy:
    lookback_days: int = 45
    min_days: int = 3
    min_factor: float = 0.2
    max_factor: float = 5.0

    @classmethod
    def from_env(cls) -> "PvCalibrationPolicy":
        return cls(
            lookback_days=int(os.getenv("PV_ARRAY_CALIBRATION_LOOKBACK_DAYS", "45")),
            min_days=int(os.getenv("PV_ARRAY_CALIBRATION_MIN_DAYS", "3")),
            min_factor=float(os.getenv("PV_ARRAY_CALIBRATION_MIN_FACTOR", "0.2")),
            max_factor=float(os.getenv("PV_ARRAY_CALIBRATION_MAX_FACTOR", "5.0")),
        )
