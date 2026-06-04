from __future__ import annotations

import pytest

from app.soc_cost_optimizer import (
    ForecastScenario,
    PvForecastUncertainty,
    SocCostModel,
    evaluate_soc_candidate,
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


def test_cost_optimizer_peak_unmet_penalty_raises_soc_target() -> None:
    hourly_pv = {12: 3.0}
    hourly_load = {18: 1.0, 19: 1.0}
    uncertainty = PvForecastUncertainty(
        mean_multiplier=1.0,
        std_multiplier=0.0,
        variance_multiplier=0.0,
        sample_count=10,
        source="deterministic",
    )
    cost_model = SocCostModel(
        day_buy_rate_yen_per_kwh=39.10,
        night_buy_rate_yen_per_kwh=28.85,
        charge_efficiency=0.93,
        sell_value_ratio=0.75,
    )

    without_penalty = optimize_soc_by_expected_cost(
        capacity_kwh=10.0,
        soc_now_percent=0.0,
        reserve_soc_percent=0.0,
        hourly_load_kwh=hourly_load,
        hourly_pv_kwh=hourly_pv,
        uncertainty=uncertainty,
        cost_model=cost_model,
        soc_step_percent=1.0,
    )
    with_penalty = optimize_soc_by_expected_cost(
        capacity_kwh=10.0,
        soc_now_percent=0.0,
        reserve_soc_percent=0.0,
        hourly_load_kwh=hourly_load,
        hourly_pv_kwh=hourly_pv,
        uncertainty=uncertainty,
        cost_model=cost_model,
        soc_step_percent=1.0,
        peak_soc_target_percent=95.0,
        peak_soc_unmet_penalty_yen_per_kwh=39.10 * 2.0,
    )

    assert without_penalty is not None
    assert with_penalty is not None
    assert with_penalty.target_soc_7_percent > without_penalty.target_soc_7_percent
    assert with_penalty.expected_peak_unmet_kwh <= without_penalty.selected_candidate.target_energy_kwh


def test_cost_optimizer_expands_load_scenarios_and_normalizes_probability() -> None:
    hourly_pv = {10: 2.0, 11: 2.0}
    hourly_load = {18: 1.0, 19: 1.0}
    uncertainty = PvForecastUncertainty(
        mean_multiplier=1.0,
        std_multiplier=0.0,
        variance_multiplier=0.0,
        sample_count=10,
        source="deterministic",
    )

    result = optimize_soc_by_expected_cost(
        capacity_kwh=10.0,
        soc_now_percent=0.0,
        reserve_soc_percent=0.0,
        hourly_load_kwh=hourly_load,
        hourly_pv_kwh=hourly_pv,
        uncertainty=uncertainty,
        cost_model=SocCostModel(
            day_buy_rate_yen_per_kwh=39.10,
            night_buy_rate_yen_per_kwh=28.85,
            charge_efficiency=0.93,
            sell_value_ratio=0.75,
        ),
        soc_step_percent=10.0,
        load_scenarios=(
            ForecastScenario("load_low", 0.2, 1.0, 0.82),
            ForecastScenario("load_mid", 0.6, 1.0, 1.0),
            ForecastScenario("load_high", 0.2, 1.0, 1.18),
        ),
    )

    assert result is not None
    assert len(result.forecast_scenarios) == 18
    assert sum(s.probability for s in result.forecast_scenarios) == pytest.approx(1.0)
    assert any("load_low" in s.label for s in result.forecast_scenarios)
    assert any("load_high" in s.label for s in result.forecast_scenarios)


def test_cost_optimizer_sell_loss_override_changes_cost() -> None:
    hourly_pv = {12: 5.0}
    hourly_load = {}
    uncertainty = PvForecastUncertainty(
        mean_multiplier=1.0,
        std_multiplier=0.0,
        variance_multiplier=0.0,
        sample_count=10,
        source="deterministic",
    )

    base = evaluate_soc_candidate(
        target_soc_percent=100.0,
        soc_now_percent=0.0,
        capacity_kwh=10.0,
        hourly_load_kwh=hourly_load,
        hourly_pv_kwh=hourly_pv,
        uncertainty=uncertainty,
        cost_model=SocCostModel(
            day_buy_rate_yen_per_kwh=39.10,
            night_buy_rate_yen_per_kwh=28.85,
            charge_efficiency=0.93,
            sell_value_ratio=0.0,
        ),
        weather_upside_probability=0.0,
    )
    overridden = evaluate_soc_candidate(
        target_soc_percent=100.0,
        soc_now_percent=0.0,
        capacity_kwh=10.0,
        hourly_load_kwh=hourly_load,
        hourly_pv_kwh=hourly_pv,
        uncertainty=uncertainty,
        cost_model=SocCostModel(
            day_buy_rate_yen_per_kwh=39.10,
            night_buy_rate_yen_per_kwh=28.85,
            charge_efficiency=0.93,
            sell_value_ratio=0.0,
            sell_opportunity_loss_yen_per_kwh_override=38.75,
        ),
        weather_upside_probability=0.0,
    )

    assert overridden.expected_sell_opportunity_cost_yen > base.expected_sell_opportunity_cost_yen
    assert overridden.total_expected_cost_yen > base.total_expected_cost_yen


def test_cost_optimizer_peak_penalty_factor_scales_cost() -> None:
    hourly_pv = {12: 3.0}
    hourly_load = {18: 1.0, 19: 1.0}
    uncertainty = PvForecastUncertainty(
        mean_multiplier=1.0,
        std_multiplier=0.0,
        variance_multiplier=0.0,
        sample_count=10,
        source="deterministic",
    )
    cost_model = SocCostModel(
        day_buy_rate_yen_per_kwh=39.10,
        night_buy_rate_yen_per_kwh=28.85,
        charge_efficiency=0.93,
        sell_value_ratio=0.75,
    )

    factor_100 = evaluate_soc_candidate(
        target_soc_percent=0.0,
        soc_now_percent=0.0,
        capacity_kwh=10.0,
        hourly_load_kwh=hourly_load,
        hourly_pv_kwh=hourly_pv,
        uncertainty=uncertainty,
        cost_model=cost_model,
        peak_soc_target_percent=95.0,
        peak_soc_unmet_penalty_yen_per_kwh=10.0,
        peak_soc_unmet_penalty_factor=1.0,
    )
    factor_045 = evaluate_soc_candidate(
        target_soc_percent=0.0,
        soc_now_percent=0.0,
        capacity_kwh=10.0,
        hourly_load_kwh=hourly_load,
        hourly_pv_kwh=hourly_pv,
        uncertainty=uncertainty,
        cost_model=cost_model,
        peak_soc_target_percent=95.0,
        peak_soc_unmet_penalty_yen_per_kwh=10.0,
        peak_soc_unmet_penalty_factor=0.45,
    )

    assert factor_100.expected_peak_unmet_cost_yen > 0.0
    assert factor_045.expected_peak_unmet_cost_yen == pytest.approx(factor_100.expected_peak_unmet_cost_yen * 0.45)
