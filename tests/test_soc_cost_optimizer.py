from __future__ import annotations

import pytest

from app.soc_cost_optimizer import (
    PvForecastUncertainty,
    SocCostModel,
    optimize_soc_by_expected_cost,
)


def test_cost_optimizer_replays_may_27_tradeoff() -> None:
    """5/27の議論で使った形: 夜間原価も入れるとSOCは40%台へ寄る。"""

    hourly_pv = {
        7: 0.3706,
        8: 0.7681,
        9: 1.2925,
        10: 0.9496,
        11: 1.0504,
        12: 1.0605,
        13: 1.0612,
        14: 0.9829,
        15: 0.7656,
        16: 0.4869,
        17: 0.3004,
        18: 0.1797,
        19: 0.0264,
        20: 0.0,
        21: 0.0,
        22: 0.0,
    }
    hourly_load = {
        7: 1.0674,
        8: 0.9132,
        9: 0.8654,
        10: 0.7930,
        11: 0.8615,
        12: 0.9201,
        13: 0.8834,
        14: 0.9012,
        15: 0.8832,
        16: 0.8706,
        17: 0.9113,
        18: 0.9474,
        19: 1.0339,
        20: 1.1015,
        21: 1.2168,
        22: 1.1633,
    }
    result = optimize_soc_by_expected_cost(
        capacity_kwh=8.769544884445438,
        soc_now_percent=0.0,
        reserve_soc_percent=0.0,
        hourly_load_kwh=hourly_load,
        hourly_pv_kwh=hourly_pv,
        uncertainty=PvForecastUncertainty(
            mean_multiplier=1.0920529947666542,
            std_multiplier=0.30401243300270075,
            variance_multiplier=0.09242355942022161,
            sample_count=9,
            source="test_5_27",
        ),
        cost_model=SocCostModel(
            day_buy_rate_yen_per_kwh=39.10,
            night_buy_rate_yen_per_kwh=28.85,
            charge_efficiency=0.93175261390142,
            sell_value_ratio=0.75,
        ),
        soc_step_percent=1.0,
    )

    assert result is not None
    assert result.target_soc_7_percent == pytest.approx(43.0)
    assert result.total_expected_cost_yen == pytest.approx(190.96, abs=0.1)
    assert result.expected_day_buy_kwh == pytest.approx(1.893, abs=0.001)
    assert result.expected_sell_kwh == pytest.approx(0.023, abs=0.001)


def test_cost_optimizer_charges_more_when_daytime_power_is_expensive() -> None:
    hourly_pv = {10: 4.0, 11: 4.0}
    hourly_load = {7: 2.0, 8: 2.0, 18: 2.0, 19: 2.0}
    uncertainty = PvForecastUncertainty(
        mean_multiplier=1.0,
        std_multiplier=0.0,
        variance_multiplier=0.0,
        sample_count=10,
        source="deterministic",
    )

    cheap_day = optimize_soc_by_expected_cost(
        capacity_kwh=10.0,
        soc_now_percent=0.0,
        reserve_soc_percent=0.0,
        hourly_load_kwh=hourly_load,
        hourly_pv_kwh=hourly_pv,
        uncertainty=uncertainty,
        cost_model=SocCostModel(
            day_buy_rate_yen_per_kwh=31.0,
            night_buy_rate_yen_per_kwh=28.85,
            charge_efficiency=0.93,
            sell_value_ratio=0.75,
        ),
        soc_step_percent=1.0,
    )
    expensive_day = optimize_soc_by_expected_cost(
        capacity_kwh=10.0,
        soc_now_percent=0.0,
        reserve_soc_percent=0.0,
        hourly_load_kwh=hourly_load,
        hourly_pv_kwh=hourly_pv,
        uncertainty=uncertainty,
        cost_model=SocCostModel(
            day_buy_rate_yen_per_kwh=45.0,
            night_buy_rate_yen_per_kwh=28.85,
            charge_efficiency=0.93,
            sell_value_ratio=0.75,
        ),
        soc_step_percent=1.0,
    )

    assert cheap_day is not None
    assert expensive_day is not None
    assert expensive_day.target_soc_7_percent > cheap_day.target_soc_7_percent
