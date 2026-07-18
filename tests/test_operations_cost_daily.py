from __future__ import annotations

import pytest

from app.operations.cost_daily import DailyCostPolicy, EnergyInterval, calculate_daily_costs


def test_calculate_daily_costs_characterizes_tiered_boundaries_and_missing_values() -> None:
    intervals = [
        EnergyInterval("2026-05-01T06:59:00+09:00", -1.0, -2.0),
        EnergyInterval("2026-05-01T07:00:00+09:00", 2.0, 1.0),
        EnergyInterval("2026-05-01T22:59:00+09:00", 0.0, 1.0),
        EnergyInterval("2026-05-01T23:00:00+09:00", 1.0, 0.0),
        EnergyInterval("2026-05-02T00:00:00+09:00", None, None),
        EnergyInterval("not-a-timestamp", 100.0, 0.0),
    ]
    policy = DailyCostPolicy(
        tariff_mode="night8_tiered",
        day_rate_tier1_yen=1.0,
        day_rate_tier2_yen=2.0,
        day_rate_tier3_yen=3.0,
        night_rate_yen=4.0,
    )

    results = calculate_daily_costs(intervals, policy)

    assert [(x.date, x.self_consumption_kwh, x.savings_yen) for x in results] == [
        ("2026-05-01", 2.0, 5.0),
        ("2026-05-02", 0.0, 0.0),
    ]


@pytest.mark.parametrize("mode", ["flat", "night8_tiered"])
def test_calculate_daily_costs_empty_input_writes_no_results(mode: str) -> None:
    assert calculate_daily_costs([], DailyCostPolicy(tariff_mode=mode)) == []


def test_calculate_daily_costs_preserves_flat_precision_and_cumulative_values() -> None:
    results = calculate_daily_costs(
        [
            EnergyInterval("2026-05-01T07:00:00", 1.001, 0.001),
            EnergyInterval("2026-05-02T07:00:00", 2.0, 0.5),
        ],
        DailyCostPolicy(tariff_mode="flat", day_rate_yen_per_kwh=31.0),
    )

    assert results[0].savings_yen == pytest.approx(31.0)
    assert results[1].cumulative_kwh == pytest.approx(2.5)
    assert results[1].cumulative_yen == pytest.approx(77.5)
