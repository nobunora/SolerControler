from __future__ import annotations

from datetime import datetime

import pytest

from app.energy_plan import build_historical_profile


def test_historical_profile_preserves_hour_boundaries_and_ratios() -> None:
    rows = [
        {"dt": datetime(2026, 7, 18, 6, 59), "load": 100.0, "pv": 100.0},
        {"dt": datetime(2026, 7, 18, 7, 0), "load": 1.0, "pv": 1.0},
        {"dt": datetime(2026, 7, 18, 9, 59), "load": 2.0, "pv": 1.0},
        {"dt": datetime(2026, 7, 18, 10, 0), "load": 3.0, "pv": 2.0},
        {"dt": datetime(2026, 7, 18, 22, 59), "load": 4.0, "pv": 0.0},
        {"dt": datetime(2026, 7, 18, 23, 0), "load": 100.0, "pv": 100.0},
    ]

    profile = build_historical_profile(rows)

    assert profile == {
        "avg_day_load_kwh": 10.0,
        "avg_morning_load_kwh": 3.0,
        "morning_pv_ratio": 0.5,
        "midday_surplus_ratio": 0.375,
    }


def test_historical_profile_preserves_empty_and_zero_pv_fallbacks() -> None:
    with pytest.raises(RuntimeError, match="日次集計対象データがありません"):
        build_historical_profile([])
    profile = build_historical_profile(
        [{"dt": datetime(2026, 7, 18, 7, 0), "load": 1.0, "pv": 0.0}]
    )
    assert profile["morning_pv_ratio"] == 0.25
