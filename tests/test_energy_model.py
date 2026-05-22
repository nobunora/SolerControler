from __future__ import annotations

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


def test_optimize_target_soc_for_daytime_prioritizes_no_buy_and_sunset() -> None:
    result = optimize_target_soc_for_daytime(
        effective_capacity_kwh_value=10.0,
        soc_now_percent=0.0,
        reserve_soc_percent=0.0,
        battery_round_trip_efficiency=1.0,
        hourly_load_kwh={7: 2.0, 8: 2.0, 9: 2.0, 10: 1.0, 11: 1.0},
        hourly_pv_kwh={10: 3.0, 11: 3.0},
        sunset_hour=11,
        soc_step_percent=1.0,
    )
    assert result is not None
    assert result.predicted_daytime_buy_kwh == pytest.approx(0.0)
    assert result.target_soc_7_percent == pytest.approx(100.0)


def test_optimize_target_soc_for_daytime_prefers_lower_soc_when_sunset_tied() -> None:
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
    # sunset SOC=100% を達成できる最小開始SOCを選択
    assert result.target_soc_7_percent == pytest.approx(50.0)
