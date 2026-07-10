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


def test_decision_prior_regret_can_shift_close_soc_choice() -> None:
    result = optimize_soc_by_expected_cost(
        capacity_kwh=10.0,
        soc_now_percent=0.0,
        reserve_soc_percent=0.0,
        hourly_load_kwh={7: 0.01},
        hourly_pv_kwh={},
        uncertainty=PvForecastUncertainty(
            mean_multiplier=1.0,
            std_multiplier=0.0,
            variance_multiplier=0.0,
            sample_count=1,
            source="test",
        ),
        cost_model=SocCostModel(
            day_buy_rate_yen_per_kwh=1.0,
            night_buy_rate_yen_per_kwh=1.0,
            charge_efficiency=1.0,
            sell_value_ratio=0.0,
        ),
        soc_step_percent=1.0,
        decision_prior_regret_yen_by_soc={0: 100.0, 20: 0.0, 100: 80.0},
        decision_prior_weight=1.0,
        decision_prior_max_penalty_yen=200.0,
    )

    assert result is not None
    assert result.target_soc_7_percent == pytest.approx(20.0)
    assert result.decision_prior_cost_yen == pytest.approx(0.0)


def test_cost_optimizer_required_charge_includes_expected_overnight_discharge() -> None:
    candidate = evaluate_soc_candidate(
        target_soc_percent=30.0,
        soc_now_percent=30.0,
        capacity_kwh=10.0,
        hourly_load_kwh={7: 1.0},
        hourly_pv_kwh={},
        uncertainty=PvForecastUncertainty(
            mean_multiplier=1.0,
            std_multiplier=0.0,
            variance_multiplier=0.0,
            sample_count=10,
            source="deterministic",
        ),
        cost_model=SocCostModel(
            day_buy_rate_yen_per_kwh=39.10,
            night_buy_rate_yen_per_kwh=28.85,
            charge_efficiency=0.9,
            sell_value_ratio=0.75,
        ),
        expected_overnight_discharge_kwh=1.8,
    )

    assert candidate.required_night_charge_kwh == pytest.approx(2.0)


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


def test_cost_optimizer_returns_selected_and_rejected_candidate_summaries() -> None:
    hourly_pv = {10: 2.0, 11: 2.0}
    hourly_load = {7: 1.0, 18: 1.0, 19: 1.0}
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
    )

    assert result is not None
    assert result.candidate_summaries
    assert result.candidate_summaries[0].target_soc_percent == result.target_soc_7_percent
    assert result.candidate_summaries[0].rejection_reason == "selected"
    assert any(summary.rejection_reason != "selected" for summary in result.candidate_summaries)


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


def test_cost_model_can_penalize_or_credit_export() -> None:
    penalty = SocCostModel(
        day_buy_rate_yen_per_kwh=39.10,
        night_buy_rate_yen_per_kwh=28.85,
        charge_efficiency=0.93,
        sell_value_ratio=0.0,
        export_value_mode="penalty",
    )
    revenue = SocCostModel(
        day_buy_rate_yen_per_kwh=39.10,
        night_buy_rate_yen_per_kwh=28.85,
        charge_efficiency=0.93,
        sell_value_ratio=0.0,
        export_value_mode="revenue",
        sell_revenue_yen_per_kwh=16.0,
    )
    neutral = SocCostModel(
        day_buy_rate_yen_per_kwh=39.10,
        night_buy_rate_yen_per_kwh=28.85,
        charge_efficiency=0.93,
        sell_value_ratio=0.0,
        export_value_mode="neutral",
    )

    assert penalty.sell_opportunity_loss_yen_per_kwh == pytest.approx(39.10)
    assert revenue.sell_opportunity_loss_yen_per_kwh == pytest.approx(-16.0)
    assert neutral.sell_opportunity_loss_yen_per_kwh == pytest.approx(0.0)


def test_cost_model_uses_tiered_day_buy_increment_cost() -> None:
    cost_model = SocCostModel(
        day_buy_rate_yen_per_kwh=39.10,
        night_buy_rate_yen_per_kwh=28.85,
        charge_efficiency=0.93,
        sell_value_ratio=0.0,
        tariff_mode="night8_tiered",
        monthly_day_buy_kwh_before_target=89.0,
        day_tier1_upper_kwh=90.0,
        day_tier2_upper_kwh=230.0,
        day_tier1_rate_yen_per_kwh=31.80,
        day_tier2_rate_yen_per_kwh=39.10,
        day_tier3_rate_yen_per_kwh=43.62,
    )

    assert cost_model.day_buy_cost_yen(2.0) == pytest.approx(31.80 + 39.10)


def test_cost_model_monthly_tier_landing_penalty_discourages_tier_crossing() -> None:
    cost_model = SocCostModel(
        day_buy_rate_yen_per_kwh=39.10,
        night_buy_rate_yen_per_kwh=28.85,
        charge_efficiency=0.93,
        sell_value_ratio=0.0,
        tariff_mode="night8_tiered",
        monthly_day_buy_kwh_before_target=80.0,
        expected_rest_of_month_day_buy_kwh=8.0,
        monthly_tier_landing_enabled=True,
        day_tier1_upper_kwh=90.0,
        day_tier2_upper_kwh=230.0,
        tier1_underuse_penalty_yen_per_kwh=0.2,
        tier1_crossing_penalty_yen_per_kwh=30.0,
    )

    assert cost_model.monthly_tier_landing_penalty_yen(1.0) == pytest.approx(0.2)
    assert cost_model.monthly_tier_landing_penalty_yen(4.0) == pytest.approx(60.0)


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
