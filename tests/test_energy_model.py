from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.energy_model import (
    EnergyModelCoefficients,
    NightChargeInputs,
    compute_night_charge_target,
    effective_capacity_kwh,
    fit_coefficients_from_csv,
    forecast_pv_energy_kwh,
    optimize_target_soc_for_daytime,
)
from energy_model_main import (
    _build_forecast_correction,
    _historical_daytime_soc_gain_guard,
    _estimate_remaining_overnight_discharge_kwh,
    _risk_adjusted_peak_penalty,
)


def _coeff() -> EnergyModelCoefficients:
    return EnergyModelCoefficients(
        soc_per_kwh_charge=10.0,
        soc_per_kwh_discharge=10.0,
        soc_drift_per_slot=0.0,
        battery_round_trip_efficiency=0.9,
        battery_usable_capacity_kwh=10.0,
        pv_self_consumption_ratio=0.7,
        pv_direct_use_ratio=0.5,
        pv_to_battery_ratio=0.2,
        pv_kwh_per_sunhour=2.0,
        pv_temp_coeff_per_deg=-0.01,
        battery_temp_coeff_per_deg=-0.01,
        battery_cycle_capacity_fade_per_cycle=0.001,
    )


def test_forecast_pv_energy_applies_temperature_coeff() -> None:
    coeff = _coeff()
    # 25C基準では 2.0kWh/h * 5h = 10kWh
    assert forecast_pv_energy_kwh(5.0, 25.0, coeff) == pytest.approx(10.0)
    # 35C では 10% 低下
    assert forecast_pv_energy_kwh(5.0, 35.0, coeff) == pytest.approx(9.0)


def test_effective_capacity_includes_degradation_and_temperature() -> None:
    coeff = _coeff()
    # cycle=100, temp=35 => 10*(1-0.1)*(1-0.1) = 8.1
    assert effective_capacity_kwh(coeff, cycle_count=100.0, battery_temp_c=35.0) == pytest.approx(8.1)


def test_compute_night_charge_target() -> None:
    coeff = _coeff()
    inp = NightChargeInputs(
        soc_now_percent=0.0,
        sun_hours_forecast=5.0,
        temp_forecast_c=25.0,
        daytime_load_forecast_kwh=8.0,
        morning_load_forecast_kwh=3.0,
        morning_pv_ratio=0.2,
        midday_surplus_ratio=0.3,
        reserve_soc_percent=10.0,
        cycle_count=0.0,
        battery_temp_c=25.0,
    )
    result = compute_night_charge_target(coeff, inp)
    assert result.predicted_pv_kwh == pytest.approx(10.0)
    assert result.predicted_morning_pv_kwh == pytest.approx(2.0)
    assert result.predicted_morning_deficit_kwh == pytest.approx(1.0)
    assert result.predicted_midday_surplus_kwh == pytest.approx(3.0)
    # e_target=2.0kWh, eta=0.9, soc_now=0% -> 2/0.9
    assert result.required_night_charge_kwh == pytest.approx(2.222222, rel=1e-4)
    assert result.target_soc_7_percent == pytest.approx(20.0)


def test_compute_night_charge_target_accepts_hourly_pv_overrides() -> None:
    coeff = _coeff()
    inp = NightChargeInputs(
        soc_now_percent=0.0,
        sun_hours_forecast=5.0,
        temp_forecast_c=25.0,
        daytime_load_forecast_kwh=8.0,
        morning_load_forecast_kwh=3.0,
        morning_pv_ratio=0.2,
        midday_surplus_ratio=0.3,
        reserve_soc_percent=10.0,
        cycle_count=0.0,
        battery_temp_c=25.0,
        predicted_pv_kwh_override=12.0,
        predicted_morning_pv_kwh_override=3.0,
        predicted_midday_surplus_kwh_override=5.0,
    )
    result = compute_night_charge_target(coeff, inp)

    assert result.predicted_pv_kwh == pytest.approx(12.0)
    assert result.predicted_morning_pv_kwh == pytest.approx(3.0)
    assert result.predicted_morning_deficit_kwh == pytest.approx(0.0)
    assert result.predicted_daytime_deficit_kwh == pytest.approx(0.0)
    assert result.predicted_midday_surplus_kwh == pytest.approx(5.0)


def test_compute_night_charge_target_uses_daytime_deficit_for_low_pv_days() -> None:
    coeff = _coeff()
    inp = NightChargeInputs(
        soc_now_percent=0.0,
        sun_hours_forecast=0.0,
        temp_forecast_c=25.0,
        daytime_load_forecast_kwh=8.0,
        morning_load_forecast_kwh=3.0,
        morning_pv_ratio=0.2,
        midday_surplus_ratio=0.3,
        reserve_soc_percent=10.0,
        cycle_count=0.0,
        battery_temp_c=25.0,
    )
    result = compute_night_charge_target(coeff, inp)

    assert result.predicted_pv_kwh == pytest.approx(0.0)
    assert result.predicted_morning_deficit_kwh == pytest.approx(3.0)
    assert result.predicted_daytime_deficit_kwh == pytest.approx(8.0)
    assert result.target_soc_7_percent == pytest.approx(90.0)
    assert result.required_night_charge_kwh == pytest.approx(10.0)


def test_compute_night_charge_target_includes_remaining_overnight_discharge() -> None:
    coeff = _coeff()
    inp = NightChargeInputs(
        soc_now_percent=30.0,
        sun_hours_forecast=5.0,
        temp_forecast_c=25.0,
        daytime_load_forecast_kwh=3.0,
        morning_load_forecast_kwh=1.0,
        morning_pv_ratio=0.2,
        midday_surplus_ratio=0.3,
        reserve_soc_percent=30.0,
        cycle_count=0.0,
        battery_temp_c=25.0,
        expected_overnight_discharge_kwh=1.8,
    )

    result = compute_night_charge_target(coeff, inp)

    assert result.target_soc_7_percent == pytest.approx(30.0)
    assert result.required_night_charge_kwh == pytest.approx(2.0)


def test_fit_coefficients_from_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "fit.csv"
    csv_path.write_text(
        "\n".join(
            [
                "年月日,時刻,発電電力量[kWh],消費電力量[kWh],売電電力量[kWh],買電電力量[kWh],充電電力量[kWh],放電電力量[kWh],蓄電残量(SOC)[%]",
                "2026/05/01,00:00,1.0,0.5,0.2,0.0,0.0,0.0,50",
                "2026/05/01,00:30,1.2,0.5,0.3,0.0,1.0,0.0,55",
                "2026/05/01,01:00,1.1,0.5,0.1,0.0,0.0,1.0,52",
                "2026/05/01,01:30,1.3,0.5,0.0,0.0,0.0,0.0,52",
            ]
        ),
        encoding="utf-8",
    )

    coeff = fit_coefficients_from_csv([csv_path])

    assert coeff.soc_per_kwh_charge > 0
    assert coeff.soc_per_kwh_discharge > 0
    assert 0 < coeff.battery_round_trip_efficiency <= 1.5
    assert coeff.battery_usable_capacity_kwh > 0


def test_optimize_target_soc_for_daytime_prioritizes_no_buy_and_peak_soc() -> None:
    result = optimize_target_soc_for_daytime(
        effective_capacity_kwh_value=10.0,
        soc_now_percent=0.0,
        reserve_soc_percent=0.0,
        battery_round_trip_efficiency=1.0,
        hourly_load_kwh={7: 2.0, 8: 2.0, 9: 2.0, 10: 1.0, 11: 1.0},
        hourly_pv_kwh={10: 6.0, 11: 6.0},
        sunset_hour=11,
        soc_step_percent=1.0,
    )
    assert result is not None
    assert result.predicted_daytime_buy_kwh == pytest.approx(0.0)
    assert result.predicted_daytime_sell_kwh == pytest.approx(0.0)
    assert result.target_soc_7_percent == pytest.approx(60.0)
    assert result.predicted_daytime_max_soc_percent == pytest.approx(100.0)


def test_optimize_target_soc_for_daytime_prefers_peak_target_over_sunset_tie() -> None:
    result = optimize_target_soc_for_daytime(
        effective_capacity_kwh_value=10.0,
        soc_now_percent=0.0,
        reserve_soc_percent=0.0,
        battery_round_trip_efficiency=1.0,
        hourly_load_kwh={7: 1.0, 10: 0.0},
        hourly_pv_kwh={10: 6.0},
        sunset_hour=10,
        soc_step_percent=1.0,
    )
    assert result is not None
    assert result.predicted_daytime_buy_kwh == pytest.approx(0.0)
    assert result.predicted_daytime_sell_kwh == pytest.approx(0.0)
    # 夕方100%固定ではなく、日中ピークが99%付近になる開始SOCを選択
    assert result.target_soc_7_percent == pytest.approx(49.0)
    assert result.predicted_daytime_max_soc_percent == pytest.approx(99.0)


def test_optimize_target_soc_for_daytime_respects_max_target_cap() -> None:
    result = optimize_target_soc_for_daytime(
        effective_capacity_kwh_value=10.0,
        soc_now_percent=0.0,
        reserve_soc_percent=0.0,
        battery_round_trip_efficiency=1.0,
        hourly_load_kwh={7: 1.0, 8: 1.0, 12: 1.0},
        hourly_pv_kwh={8: 3.0, 12: 5.0},
        sunset_hour=12,
        soc_step_percent=1.0,
        max_target_soc_percent=80.0,
    )

    assert result is not None
    assert result.target_soc_7_percent <= 80.0


def test_historical_daytime_soc_gain_guard_uses_lower_quartile(monkeypatch) -> None:
    monkeypatch.setenv("HISTORICAL_DAYTIME_SOC_GAIN_MIN_DAYS", "4")
    monkeypatch.setenv("HISTORICAL_DAYTIME_SOC_GAIN_MIN_SAMPLES", "3")
    monkeypatch.setenv("HISTORICAL_DAYTIME_SOC_GAIN_FLOOR_PERCENT", "15")
    rows = []
    gains = [20.0, 10.0, 40.0, 30.0]
    for index, gain in enumerate(gains, start=1):
        day = f"2026-05-{index:02d}"
        rows.extend(
            [
                {"dt": datetime.fromisoformat(f"{day}T07:00:00"), "pv": 0.2, "soc": 20.0},
                {"dt": datetime.fromisoformat(f"{day}T12:00:00"), "pv": 0.4, "soc": 20.0 + gain},
                {"dt": datetime.fromisoformat(f"{day}T18:30:00"), "pv": 0.1, "soc": 20.0 + gain - 1.0},
            ]
        )

    guard = _historical_daytime_soc_gain_guard(rows, reserve_soc_percent=0.0, target_date="2026-05-05")

    assert guard["applied"] is True
    assert guard["sample_count"] == 4
    assert guard["percentile_gain_percent"] == pytest.approx(17.5)
    assert guard["cap_target_soc_percent"] == pytest.approx(82.5)


def test_estimate_remaining_overnight_discharge_uses_recent_matching_slots(monkeypatch) -> None:
    monkeypatch.setenv("OVERNIGHT_DISCHARGE_GUARD_MIN_DAYS", "2")
    monkeypatch.setenv("OVERNIGHT_DISCHARGE_GUARD_PERCENTILE", "50")
    rows = [
        {"dt": datetime.fromisoformat("2026-06-09T22:30:00"), "discharge": 0.5, "charge": 0.0, "soc": 40.0},
        {"dt": datetime.fromisoformat("2026-06-10T00:30:00"), "discharge": 0.4, "charge": 0.0, "soc": 35.0},
        {"dt": datetime.fromisoformat("2026-06-10T22:30:00"), "discharge": 0.7, "charge": 0.0, "soc": 42.0},
        {"dt": datetime.fromisoformat("2026-06-11T00:30:00"), "discharge": 0.6, "charge": 0.0, "soc": 36.0},
        {"dt": datetime.fromisoformat("2026-06-11T22:00:00"), "discharge": 0.0, "charge": 0.0, "soc": 30.0},
    ]

    guard = _estimate_remaining_overnight_discharge_kwh(rows, target_date="2026-06-12")

    assert guard["reason"] == "history_percentile"
    assert guard["sample_count"] == 2
    assert guard["expected_kwh"] == pytest.approx(1.1)


def test_risk_adjusted_peak_penalty_requires_high_temp_and_pv_overconfidence(monkeypatch) -> None:
    monkeypatch.setenv("SOC_PEAK_UNMET_BASE_FACTOR", "1")
    monkeypatch.setenv("SOC_PEAK_UNMET_RISK_FACTOR", "2")
    monkeypatch.setenv("SOC_PEAK_UNMET_MAX_FACTOR", "2")
    high_temp = {
        "cooling_degree_hours_28": 12.0,
        "temp_ewma_12h_evening": 26.5,
        "night_min_temp_c": 21.0,
    }
    normal_temp = {
        "cooling_degree_hours_28": 0.0,
        "temp_ewma_12h_evening": 23.0,
        "night_min_temp_c": 18.0,
    }

    assert _risk_adjusted_peak_penalty(
        target_features=high_temp,
        pv_ratio_raw=1.2,
        pv_ratio_applied=1.2,
    )["applied_factor"] == pytest.approx(1.0)
    assert _risk_adjusted_peak_penalty(
        target_features=normal_temp,
        pv_ratio_raw=1.5,
        pv_ratio_applied=1.35,
    )["applied_factor"] == pytest.approx(1.0)
    both = _risk_adjusted_peak_penalty(
        target_features=high_temp,
        pv_ratio_raw=1.5,
        pv_ratio_applied=1.35,
    )
    assert both["applied_factor"] == pytest.approx(2.0)
    assert both["risk_reasons"] == ["high_temperature", "pv_overconfidence"]


def test_build_forecast_correction_keeps_raw_and_corrected_branches(monkeypatch) -> None:
    monkeypatch.setenv("PV_RATIO_EWMA_ALPHA", "0.5")
    monkeypatch.setenv("PV_RATIO_EWMA_MIN", "0.9")
    monkeypatch.setenv("PV_RATIO_EWMA_MAX", "1.35")
    monkeypatch.setenv("LOAD_RATIO_EWMA_ALPHA", "0.5")
    monkeypatch.setenv("SOC_PEAK_UNMET_BASE_FACTOR", "1")
    monkeypatch.setenv("SOC_PEAK_UNMET_RISK_FACTOR", "2")

    forecast_history = {
        "2026-05-28": {7: {"pv": 1.0, "load": 1.0}, 17: {"pv": 0.0, "load": 1.0}},
        "2026-05-29": {7: {"pv": 1.0, "load": 1.0}, 17: {"pv": 0.0, "load": 1.0}},
        "2026-05-30": {7: {"pv": 1.0, "load": 1.0}, 17: {"pv": 0.0, "load": 1.0}},
    }

    def fake_history(*, target_date: str):
        return forecast_history, "test_history"

    def fake_temperature(*, lat: float, lon: float, timezone: str, start_date: str, end_date: str, archive: bool):
        return {
            day: {hour: 30.0 for hour in range(23)}
            for day in ["2026-05-28", "2026-05-29", "2026-05-30", "2026-05-31"]
        }

    def fake_evening_temperature_correction(**kwargs):
        return {
            "enabled": True,
            "applied": True,
            "multiplier_delta": 0.2,
            "target_features": kwargs["target_features"],
        }

    monkeypatch.setattr("app.forecast_correction._load_forecast_hourly_history", fake_history)
    monkeypatch.setattr("app.forecast_correction._fetch_hourly_temperatures", fake_temperature)
    monkeypatch.setattr("app.forecast_correction._evening_temperature_correction", fake_evening_temperature_correction)

    rows = []
    for day in ["2026-05-28", "2026-05-29", "2026-05-30"]:
        rows.extend(
            [
                {"dt": datetime.fromisoformat(f"{day}T07:00:00"), "pv": 4.0, "load": 1.0, "soc": 10.0},
                {"dt": datetime.fromisoformat(f"{day}T17:00:00"), "pv": 0.0, "load": 1.0, "soc": 20.0},
            ]
        )

    correction = _build_forecast_correction(
        rows=rows,
        hourly_load_forecast={7: 1.0, 17: 1.0},
        hourly_pv_forecast={7: 1.0, 17: 0.0},
        target_date="2026-05-31",
        lat=35.0,
        lon=139.0,
        timezone="Asia/Tokyo",
        forecast={"date": "2026-05-31", "temp_c": 30.0},
    )

    rationale = correction["rationale"]
    assert rationale["pv_ratio_ewma_applied"] == pytest.approx(1.35)
    assert rationale["pv_ratio_ewma_raw"] > rationale["pv_ratio_ewma_applied"]
    assert rationale["corrected_hourly_pv_forecast_kwh"]["7"] == pytest.approx(1.35)
    assert rationale["corrected_hourly_load_forecast_kwh"]["17"] == pytest.approx(1.2)
    assert rationale["soc_peak_unmet_penalty"]["applied_factor"] == pytest.approx(2.0)
