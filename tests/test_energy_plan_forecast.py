from __future__ import annotations

from app.energy_plan import coerce_hourly_energy, estimate_sunset_hour, summarize_hourly_pv


def test_coerce_hourly_energy_preserves_missing_negative_and_hour_bounds() -> None:
    assert coerce_hourly_energy({"7": 1.5, 8: -1, "24": 2, "bad": 3, 9: None}) == {
        7: 1.5,
        8: 0.0,
    }
    assert coerce_hourly_energy(None) == {}


def test_hourly_pv_summary_preserves_windows_rounding_and_peak() -> None:
    assert summarize_hourly_pv({6: 9.0, 7: 1.11111, 10: 2.22222, 16: 3.33333, 23: 8.0}) == {
        "total_kwh": 23.6667,
        "morning_kwh": 1.1111,
        "midday_kwh": 2.2222,
        "evening_kwh": 3.3333,
        "peak_kw": 9.0,
    }


def test_sunset_hour_preserves_threshold_and_default() -> None:
    assert estimate_sunset_hour({18: 0.03, 19: 0.031, 23: 1.0}) == 19
    assert estimate_sunset_hour({18: 0.03}) == 18
