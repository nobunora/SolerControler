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
    stop_soc_margin_percent: float
    max_consecutive_soc_failures: int
    reapply_if_soc_not_increasing: bool
    reapply_after_polls: int
    reapply_min_soc_delta_percent: float
    completion_confirm_before_minutes: int
    no_charge_percent_epsilon: float
    no_charge_kwh_epsilon: float

    @classmethod
    def from_env(cls) -> "ForcedChargeSettings":
        return cls(
            cutoff=parse_hhmm(
                os.getenv("ADJUST03_FORCE_MONITOR_CUTOFF_HHMM", "07:00").strip() or "07:00",
                name="ADJUST03_FORCE_MONITOR_CUTOFF_HHMM",
            ),
            poll_interval_seconds=max(60, env_int("ADJUST03_FORCE_MONITOR_POLL_SECONDS", default=180)),
            retry_attempts=max(1, env_int("ADJUST03_SOC_RETRY_ATTEMPTS", default=3)),
            retry_delay_seconds=max(0.0, env_float("ADJUST03_SOC_RETRY_DELAY_SECONDS", default=5.0)),
            stop_soc_margin_percent=max(
                0.0, env_float("ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT", default=1.0)
            ),
            max_consecutive_soc_failures=max(
                1, env_int("ADJUST03_MAX_CONSECUTIVE_SOC_FAILURES", default=3)
            ),
            reapply_if_soc_not_increasing=(
                os.getenv("ADJUST03_FORCE_REAPPLY_IF_SOC_NOT_INCREASING", "true")
                .strip()
                .lower()
                in {"1", "true", "yes", "on"}
            ),
            reapply_after_polls=max(
                1, env_int("ADJUST03_FORCE_REAPPLY_AFTER_POLLS", default=2)
            ),
            reapply_min_soc_delta_percent=max(
                0.0,
                env_float("ADJUST03_FORCE_REAPPLY_MIN_SOC_DELTA_PERCENT", default=0.1),
            ),
            completion_confirm_before_minutes=max(
                0, env_int("ADJUST03_COMPLETION_CONFIRM_BEFORE_MINUTES", default=5)
            ),
            no_charge_percent_epsilon=max(
                0.0, env_float("ADJUST03_NO_CHARGE_PERCENT_EPSILON", default=0.5)
            ),
            no_charge_kwh_epsilon=max(
                0.0, env_float("ADJUST03_NO_CHARGE_KWH_EPSILON", default=0.05)
            ),
        )
