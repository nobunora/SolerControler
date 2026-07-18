from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ForecastSettings:
    latitude: float
    longitude: float
    timezone: str

    @classmethod
    def from_env(cls) -> "ForecastSettings":
        return cls(
            latitude=float(os.getenv("FORECAST_LATITUDE", "35.67452")),
            longitude=float(os.getenv("FORECAST_LONGITUDE", "139.48216")),
            timezone=os.getenv("TIMEZONE", "Asia/Tokyo"),
        )


@dataclass(frozen=True)
class HistoricalInputSettings:
    artifacts_dir: Path
    min_training_days: int
    fallback_window_days: int

    @classmethod
    def from_env(cls) -> "HistoricalInputSettings":
        return cls(
            artifacts_dir=Path(os.getenv("ARTIFACTS_DIR", "artifacts")),
            min_training_days=int(os.getenv("CONSUMPTION_MODEL_MIN_TRAINING_DAYS", "45")),
            fallback_window_days=int(os.getenv("CONSUMPTION_MODEL_FALLBACK_WINDOW_DAYS", "14")),
        )
