from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.runtime.soc_reading import _retry_sleep_seconds, read_soc_with_fallback


def test_retry_sleep_reserves_budget_for_remaining_attempts() -> None:
    assert _retry_sleep_seconds(
        configured_delay_seconds=300.0,
        remaining_budget_seconds=60.0,
        remaining_attempts=2,
    ) == pytest.approx(20.0)
    assert _retry_sleep_seconds(
        configured_delay_seconds=300.0,
        remaining_budget_seconds=40.0,
        remaining_attempts=1,
    ) == pytest.approx(20.0)
    assert _retry_sleep_seconds(
        configured_delay_seconds=300.0,
        remaining_budget_seconds=60.0,
        remaining_attempts=0,
    ) == 0.0


def test_realtime_soc_policy_does_not_spend_whole_deadline_on_first_retry() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def latest_realtime() -> float | None:
        calls.append(len(calls) + 1)
        return None

    reading = read_soc_with_fallback(
        [],
        latest_realtime=latest_realtime,
        latest_csv=lambda _paths: (None, None),
        env_int=lambda name, default: 3 if name == "ADJUST03_REALTIME_SOC_RETRY_ATTEMPTS" else default,
        env_float=lambda name, default: 300.0 if name == "ADJUST03_REALTIME_SOC_RETRY_DELAY_SECONDS" else default,
        sleep=sleeps.append,
        deadline_monotonic=time.monotonic() + 60.0,
        allow_realtime=True,
        allow_csv_fallback=False,
    )

    assert calls == [1, 2, 3]
    assert len(sleeps) == 2
    assert 0.0 < sleeps[0] <= 20.1
    assert 0.0 < sleeps[1] <= 30.1
    assert reading.value_percent is None
    assert reading.source == "unavailable"
    assert "CSV SOC fallback disabled" in (reading.error or "")


def test_realtime_soc_attempt_count_is_never_zero() -> None:
    calls = 0

    def latest_realtime() -> float | None:
        nonlocal calls
        calls += 1
        return None

    reading = read_soc_with_fallback(
        [Path("unused.csv")],
        latest_realtime=latest_realtime,
        latest_csv=lambda _paths: (None, None),
        env_int=lambda _name, _default: 0,
        env_float=lambda _name, _default: 0.0,
        sleep=lambda _seconds: None,
        deadline_monotonic=time.monotonic() + 1.0,
        allow_realtime=True,
        allow_csv_fallback=False,
    )

    assert calls == 1
    assert reading.source == "unavailable"
