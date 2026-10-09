"""Fresh API SOC acquisition and bounded retries for the Cloud Job monitor."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.kpnet.soc_api import KpNetSocClient
from app.runtime.night_soc_time_contract import SOC_OPERATION_MAX_SECONDS


@dataclass(frozen=True)
class SocReading:
    value_percent: float | None
    source: str
    error: str | None
    observed_at: datetime | None
    retrieved_at: datetime | None = None


def _emit_03_soc_read_attempt(*, attempt: int, max_attempts: int, outcome: str, retry_delay_seconds: float) -> None:
    """Emit a secret-free best-effort Cloud Logging event for a failed SOC read."""
    try:
        print(
            json.dumps(
                {
                    "message": "03-soc-read-attempt",
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "outcome": outcome,
                    "retry_delay_seconds": retry_delay_seconds,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
    except Exception:
        pass


def _retry_sleep_seconds(
    *,
    configured_delay_seconds: float,
    remaining_budget_seconds: float,
    remaining_attempts: int,
) -> float:
    """Bound retry sleep so one delay cannot consume the whole SOC read budget.

    The historical default delay is 300 seconds while one realtime-SOC operation is
    intentionally bounded to 60 seconds. Sleeping the whole remaining budget after
    attempt 1 therefore reduced a nominal three-attempt policy to one useful attempt.
    Reserve an equal share of the remaining budget for every remaining attempt.
    """
    if configured_delay_seconds <= 0 or remaining_budget_seconds <= 0 or remaining_attempts <= 0:
        return 0.0
    fair_share = remaining_budget_seconds / float(remaining_attempts + 1)
    return max(0.0, min(configured_delay_seconds, fair_share))


def latest_realtime_soc_reading(*, deadline_monotonic: float | None = None) -> SocReading:
    operation_start = time.monotonic()
    operation_deadline = deadline_monotonic if deadline_monotonic is not None else operation_start + SOC_OPERATION_MAX_SECONDS
    if operation_deadline <= operation_start:
        raise TimeoutError("SOC deadline expired")
    client = KpNetSocClient(deadline_monotonic=operation_deadline)
    try:
        sample = client.read_soc()
        return SocReading(sample.value_percent, "realtime", None, sample.measured_at, sample.retrieved_at)
    finally:
        client.close()


def read_realtime_soc_with_retry(
    *,
    latest_realtime: Callable[[], SocReading | None],
    env_int: Callable[[str, int], int],
    env_float: Callable[[str, float], float],
    sleep: Callable[[float], None] = time.sleep,
    deadline_monotonic: float | None = None,
    allow_realtime: bool = True,
) -> SocReading:
    attempts = max(1, env_int("ADJUST03_REALTIME_SOC_RETRY_ATTEMPTS", 3))
    delay_seconds = max(0.0, env_float("ADJUST03_REALTIME_SOC_RETRY_DELAY_SECONDS", 300.0))
    errors: list[str] = []
    operation_start = time.monotonic()
    operation_deadline = deadline_monotonic if deadline_monotonic is not None else operation_start + SOC_OPERATION_MAX_SECONDS
    if not allow_realtime:
        errors.append("03 SOC safe budget unavailable")
    elif operation_deadline <= operation_start:
        errors.append("SOC deadline expired")
    else:
        for attempt in range(1, attempts + 1):
            if time.monotonic() >= operation_deadline:
                errors.append("SOC deadline expired")
                break
            try:
                reading = latest_realtime()
                if reading is not None and reading.value_percent is not None:
                    return reading
                errors.append("realtime returned no SOC")
                outcome = "no_value"
            except Exception as exc:
                errors.append(type(exc).__name__)
                outcome = "error"
            remaining_attempts = attempts - attempt
            remaining_budget = max(0.0, operation_deadline - time.monotonic())
            sleep_seconds = _retry_sleep_seconds(
                configured_delay_seconds=delay_seconds,
                remaining_budget_seconds=remaining_budget,
                remaining_attempts=remaining_attempts,
            )
            _emit_03_soc_read_attempt(
                attempt=attempt,
                max_attempts=attempts,
                outcome=outcome,
                retry_delay_seconds=sleep_seconds,
            )
            if sleep_seconds > 0:
                sleep(sleep_seconds)

    return SocReading(None, "unavailable", "; ".join(errors), None)
