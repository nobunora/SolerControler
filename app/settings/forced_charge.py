from __future__ import annotations

from dataclasses import dataclass
from datetime import time
import os

from app.time_windows import parse_hhmm
from app.utils import env_float, env_int


@dataclass(frozen=True)
class ForcedChargeSettings:
    cutoff: time
    poll_interval_seconds: int
    retry_attempts: int
    retry_delay_seconds: float

    @classmethod
    def from_env(cls) -> "ForcedChargeSettings":
        return cls(
            cutoff=parse_hhmm(
                os.getenv("ADJUST03_FORCE_MONITOR_CUTOFF_HHMM", "07:00"),
                name="ADJUST03_FORCE_MONITOR_CUTOFF_HHMM",
            ),
            poll_interval_seconds=max(1, env_int("ADJUST03_FORCE_MONITOR_POLL_SECONDS", default=180)),
            retry_attempts=max(1, env_int("ADJUST03_SOC_RETRY_ATTEMPTS", default=3)),
            retry_delay_seconds=max(0.0, env_float("ADJUST03_SOC_RETRY_DELAY_SECONDS", default=5.0)),
        )
