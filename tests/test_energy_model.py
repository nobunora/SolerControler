from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import requests

from app.energy_model import (
    EnergyModelCoefficients,
    NightChargeInputs,
    compute_night_charge_target,
    effective_capacity_kwh,
    fit_coefficients_from_csv,
    forecast_pv_energy_kwh,
    optimize_target_soc_for_daytime,
)
from app.forecast_correction import (
    _evening_temperature_correction,
    _recent_and_analog_daytime_floor,
    _temperature_hourly_multipliers,
)
from energy_model_main import (
    _active_constraint_names,
    _archive_weather_history,
    _annotate_pv_headroom_guard_policy,
    _build_hourly_load_forecast,
    _build_forecast_correction,
    _daytime_net_surplus_headroom_guard,
    _decision_cost_breakdown,
    _historical_daytime_soc_gain_guard,
    _historical_hourly_profile,
    _hourly_weather_summary,
    _monthly_day_buy_kwh_before_target,
    _load_scenarios_for_cost_optimizer,
    _expected_rest_of_month_day_buy_kwh,
    _reshape_hourly_pv_by_weather,
    _estimate_remaining_overnight_load_kwh,
    _risk_adjusted_peak_penalty,
    _selected_pv_uncertainty,
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


class _WeatherResponse:
    def __init__(self, payload: object, *, status_code: int = 200, http_error: bool = False) -> None:
        self._payload = payload
        self.status_code = status_code
        self._http_error = http_error

    def raise_for_status(self) -> None:
        if self._http_error:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _daily_weather_payload(days: list[str]) -> dict[str, object]:
    count = len(days)
    return {
        "daily": {
            "time": days,
            "sunshine_duration": [3600.0] * count,
            "temperature_2m_mean": [30.0] * count,
            "weather_code": [1] * count,
            "precipitation_sum": [0.0] * count,
            "shortwave_radiation_sum": [20.0] * count,
        }
    }


def test_archive_weather_history_preserves_partial_chunks_and_diagnostics(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEATHER_ARCHIVE_CACHE_PATH", str(tmp_path / "weather.json"))
    monkeypatch.setenv("WEATHER_ARCHIVE_CHUNK_DAYS", "2")
    calls = 0

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _WeatherResponse(_daily_weather_payload(["2026-06-01", "2026-06-02"]))
        raise requests.Timeout("archive timeout")

    monkeypatch.setattr("energy_model_main.requests.get", fake_get)
    rows = [
        {"dt": datetime.fromisoformat("2026-06-01T00:00:00")},
        {"dt": datetime.fromisoformat("2026-06-03T00:00:00")},
    ]

    result = _archive_weather_history(rows, lat=35.0, lon=139.0, timezone="Asia/Tokyo")

    assert result.received_dates == ["2026-06-01", "2026-06-02"]
    assert result.missing_dates == ["2026-06-03"]
    assert result.errors[0]["exception_type"] == "Timeout"
    assert result.requested_periods[0]["received_day_count"] == 2
    assert result.requested_periods[1]["received_day_count"] == 0


def test_archive_weather_history_reuses_cached_days(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEATHER_ARCHIVE_CACHE_PATH", str(tmp_path / "weather.json"))
    rows = [
        {"dt": datetime.fromisoformat("2026-06-01T00:00:00")},
        {"dt": datetime.fromisoformat("2026-06-02T00:00:00")},
    ]
    monkeypatch.setattr(
        "energy_model_main.requests.get",
        lambda *args, **kwargs: _WeatherResponse(_daily_weather_payload(["2026-06-01", "2026-06-02"])),
    )
    first = _archive_weather_history(rows, lat=35.0, lon=139.0, timezone="Asia/Tokyo")

    def unexpected_get(*args, **kwargs):
        raise AssertionError("cache hit must not call the API")

    monkeypatch.setattr("energy_model_main.requests.get", unexpected_get)
    second = _archive_weather_history(rows, lat=35.0, lon=139.0, timezone="Asia/Tokyo")

    assert first.received_dates == second.received_dates
    assert second.cache_hit_dates == ["2026-06-01", "2026-06-02"]
    assert second.requested_periods == []


@pytest.mark.parametrize(
    ("failure", "expected_type", "expected_status"),
    [
        (requests.ConnectionError("offline"), "ConnectionError", None),
        (_WeatherResponse(ValueError("invalid json")), "ValueError", 200),
        (_WeatherResponse({}, status_code=503, http_error=True), "HTTPError", 503),
    ],
)
def test_archive_weather_history_classifies_fetch_failures(
    monkeypatch, tmp_path, failure: object, expected_type: str, expected_status: int | None
) -> None:
    monkeypatch.setenv("WEATHER_ARCHIVE_CACHE_PATH", str(tmp_path / "weather.json"))

    def fake_get(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr("energy_model_main.requests.get", fake_get)
    rows = [{"dt": datetime.fromisoformat("2026-06-01T00:00:00")}]

    result = _archive_weather_history(rows, lat=35.0, lon=139.0, timezone="Asia/Tokyo")

    assert result.received_dates == []
    assert result.missing_dates == ["2026-06-01"]
    assert result.errors[0]["exception_type"] == expected_type
    assert result.errors[0]["http_status"] == expected_status


def test_decision_cost_breakdown_contains_complete_objective() -> None:
    payload = {
        "night_charge_cost_yen": 10.0,
        "expected_day_buy_cost_yen": 20.0,
        "expected_sell_opportunity_cost_yen": 3.0,
        "expected_peak_unmet_cost_yen": 4.0,
        "expected_monthly_tier_landing_penalty_yen": 5.0,
        "decision_prior_cost_yen": 6.0,
        "total_expected_cost_yen": 48.0,
    }

    breakdown = _decision_cost_breakdown(payload)

    component_total = sum(value for key, value in breakdown.items() if key != "total_expected_yen")
    assert component_total == pytest.approx(breakdown["total_expected_yen"])


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


def test_monthly_day_buy_uses_billing_close_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOC_MONTHLY_TIER_CLOSE_DAY", "14")
    rows = [
        {"dt": datetime(2026, 6, 14, 12), "buy": 10.0},
        {"dt": datetime(2026, 6, 15, 12), "buy": 1.0},
        {"dt": datetime(2026, 6, 20, 12), "buy": 2.0},
        {"dt": datetime(2026, 6, 21, 12), "buy": 4.0},
        {"dt": datetime(2026, 7, 15, 12), "buy": 8.0},
    ]

    before = _monthly_day_buy_kwh_before_target(rows, target_date="2026-06-21")
    rest = _expected_rest_of_month_day_buy_kwh(rows, target_date="2026-06-21")

    assert before["kwh"] == pytest.approx(3.0)
    assert before["billing_period_start"] == "2026-06-15"
    assert before["billing_period_end"] == "2026-07-14"
    assert rest["remaining_days_after_target"] == 23


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


def test_physical_pv_selection_marks_headroom_cap_not_enforced() -> None:
    guard = {"enabled": True, "applied": True, "cap_target_soc_percent": 85.0}

    annotated = _annotate_pv_headroom_guard_policy(
        guard,
        apply_caps=False,
        selected_method="physical_global",
    )
    active = _active_constraint_names(
        morning_headroom_guard={"applied": False},
        daytime_net_surplus_headroom_guard={"applied": False},
        historical_soc_gain_guard=annotated,
        overnight_discharge_guard={"enabled": True},
        respect_morning_headroom_guard=True,
    )

    assert annotated["applied"] is True
    assert annotated["enforced_as_target_cap"] is False
    assert annotated["enforcement_skip_reason"] == "physical_pv_selected"
    assert "historical_daytime_soc_gain_guard" not in active


def test_hourly_weather_summary_counts_rain_and_low_radiation() -> None:
    hourly = [
        {
            "hour": hour,
            "weather_code": 61 if hour in {9, 10} else 3,
            "precipitation_mm": 0.2 if hour in {9, 10} else 0.0,
            "shortwave_radiation_w_m2": 80.0 if hour in {11, 12} else 300.0,
            "temp_c": 20.0,
        }
        for hour in range(7, 18)
    ]

    summary = _hourly_weather_summary(hourly)

    assert summary["rain_hours_7_17"] == 2
    assert summary["low_shortwave_hours_9_15"] == 2
    assert summary["dominant_weather_class_7_17"] == "cloudy"


def test_reshape_hourly_pv_by_weather_preserves_total_and_moves_shape(monkeypatch) -> None:
    monkeypatch.setenv("HOURLY_WEATHER_PV_SHAPE_ENABLED", "true")
    monkeypatch.setenv("HOURLY_WEATHER_PV_SHAPE_BLEND", "1.0")
    hourly_pv = {hour: 1.0 for hour in range(7, 23)}
    forecast = {
        "source": "test",
        "hourly_weather": [
            {"hour": hour, "shortwave_radiation_w_m2": 1000.0 if hour == 12 else 0.0}
            for hour in range(7, 23)
        ],
    }

    reshaped, rationale = _reshape_hourly_pv_by_weather(hourly_pv, forecast)

    assert rationale["enabled"] is True
    assert sum(reshaped.values()) == pytest.approx(sum(hourly_pv.values()))
    assert reshaped[12] == pytest.approx(sum(hourly_pv.values()))
    assert reshaped[11] == pytest.approx(0.0)


def test_daytime_net_surplus_headroom_guard_caps_only_clear_surplus_days(monkeypatch) -> None:
    monkeypatch.setenv("DAYTIME_NET_SURPLUS_HEADROOM_GUARD_ENABLED", "true")
    monkeypatch.setenv("DAYTIME_NET_SURPLUS_HEADROOM_MIN_KWH", "1.0")
    monkeypatch.setenv("DAYTIME_NET_SURPLUS_HEADROOM_RATIO", "0.5")
    hourly_load = {hour: 1.0 for hour in range(7, 18)}
    hourly_pv = {hour: 2.0 for hour in range(7, 18)}
    forecast = {
        "hourly_weather_summary": {
            "rain_hours_7_17": 0,
            "low_shortwave_hours_9_15": 0,
        }
    }

    guard = _daytime_net_surplus_headroom_guard(
        hourly_load_kwh=hourly_load,
        hourly_pv_kwh=hourly_pv,
        forecast=forecast,
        effective_capacity_kwh_value=10.0,
        reserve_soc_percent=0.0,
    )

    assert guard["applied"] is True
    assert guard["cap_target_soc_percent"] < 100.0

    rainy = _daytime_net_surplus_headroom_guard(
        hourly_load_kwh=hourly_load,
        hourly_pv_kwh=hourly_pv,
        forecast={"hourly_weather_summary": {"rain_hours_7_17": 8, "low_shortwave_hours_9_15": 0}},
        effective_capacity_kwh_value=10.0,
        reserve_soc_percent=0.0,
    )

    assert rainy["applied"] is False
    assert rainy["reason"] == "rain_or_low_radiation_relaxed"


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


def test_estimate_remaining_overnight_load_uses_recent_matching_slots(monkeypatch) -> None:
    monkeypatch.setenv("OVERNIGHT_DISCHARGE_GUARD_MIN_DAYS", "2")
    monkeypatch.setenv("OVERNIGHT_DISCHARGE_GUARD_PERCENTILE", "50")
    rows = [
        {"dt": datetime.fromisoformat("2026-06-09T23:00:00"), "load": 0.3, "discharge": 0.0, "charge": 9.0, "soc": 40.0},
        {"dt": datetime.fromisoformat("2026-06-09T23:30:00"), "load": 0.5, "discharge": 0.0, "charge": 9.0, "soc": 40.0},
        {"dt": datetime.fromisoformat("2026-06-10T00:00:00"), "load": 0.1, "discharge": 0.0, "charge": 9.0, "soc": 35.0},
        {"dt": datetime.fromisoformat("2026-06-10T00:30:00"), "load": 0.3, "discharge": 0.0, "charge": 9.0, "soc": 35.0},
        {"dt": datetime.fromisoformat("2026-06-10T23:00:00"), "load": 0.4, "discharge": 0.0, "charge": 9.0, "soc": 42.0},
        {"dt": datetime.fromisoformat("2026-06-10T23:30:00"), "load": 0.6, "discharge": 0.0, "charge": 9.0, "soc": 42.0},
        {"dt": datetime.fromisoformat("2026-06-11T00:00:00"), "load": 0.2, "discharge": 0.0, "charge": 9.0, "soc": 36.0},
        {"dt": datetime.fromisoformat("2026-06-11T00:30:00"), "load": 0.4, "discharge": 0.0, "charge": 9.0, "soc": 36.0},
        {"dt": datetime.fromisoformat("2026-06-11T22:00:00"), "load": 0.0, "discharge": 0.0, "charge": 0.0, "soc": 30.0},
    ]

    guard = _estimate_remaining_overnight_load_kwh(rows, target_date="2026-06-12")

    assert guard["reason"] == "history_percentile"
    assert guard["sample_count"] == 2
    assert guard["expected_kwh"] == pytest.approx(1.4)
    assert guard["source"] == "monitoring_load_kwh"
    assert guard["hourly_load_forecast_kwh"]["23"] == pytest.approx(0.9)
    assert guard["hourly_load_forecast_kwh"]["0"] == pytest.approx(0.5)
    assert guard["hourly_aggregation"]["complete_hour_count"] == 4


def test_historical_hourly_profile_sums_complete_intervals_and_ignores_incomplete() -> None:
    rows = [
        {"dt": datetime.fromisoformat("2026-06-10T00:00:00"), "load": 1.0},
        {"dt": datetime.fromisoformat("2026-06-10T00:30:00"), "load": 1.2},
        {"dt": datetime.fromisoformat("2026-06-11T00:00:00"), "load": 1.4},
        {"dt": datetime.fromisoformat("2026-06-11T00:30:00"), "load": 1.6},
        {"dt": datetime.fromisoformat("2026-06-12T01:00:00"), "load": 9.0},
        {"dt": datetime.fromisoformat("2026-06-12T02:00:00"), "load": 0.0},
        {"dt": datetime.fromisoformat("2026-06-12T02:30:00"), "load": 0.0},
    ]

    profile = _historical_hourly_profile(rows, key="load", start_hour=0, end_hour_exclusive=3)

    assert profile[0] == pytest.approx(2.6)
    assert profile[1] == pytest.approx(0.0)
    assert profile[2] == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("cap_kwh", "expected_kwh", "cap_applied"),
    [("0", 14.8, False), ("10", 10.0, True)],
)
def test_estimate_remaining_overnight_load_optional_cap(
    monkeypatch, cap_kwh: str, expected_kwh: float, cap_applied: bool
) -> None:
    monkeypatch.setenv("OVERNIGHT_DISCHARGE_GUARD_MIN_DAYS", "1")
    monkeypatch.setenv("OVERNIGHT_DISCHARGE_GUARD_CAP_KWH", cap_kwh)
    rows = [
        {"dt": datetime.fromisoformat("2026-06-10T23:00:00"), "load": 3.0},
        {"dt": datetime.fromisoformat("2026-06-10T23:30:00"), "load": 4.0},
        {"dt": datetime.fromisoformat("2026-06-11T00:00:00"), "load": 3.8},
        {"dt": datetime.fromisoformat("2026-06-11T00:30:00"), "load": 4.0},
        {"dt": datetime.fromisoformat("2026-06-11T22:00:00"), "load": 0.0},
    ]

    guard = _estimate_remaining_overnight_load_kwh(rows, target_date="2026-06-12")

    assert guard["expected_kwh"] == pytest.approx(expected_kwh)
    assert guard["uncapped_expected_kwh"] == pytest.approx(14.8)
    assert guard["cap_kwh"] == pytest.approx(float(cap_kwh))
    assert guard["cap_applied"] is cap_applied


def test_estimate_remaining_overnight_load_excludes_elapsed_night_slots(monkeypatch) -> None:
    monkeypatch.setenv("OVERNIGHT_DISCHARGE_GUARD_MIN_DAYS", "1")
    rows = [
        {"dt": datetime.fromisoformat("2026-06-10T23:00:00"), "load": 8.0},
        {"dt": datetime.fromisoformat("2026-06-10T23:30:00"), "load": 8.0},
        {"dt": datetime.fromisoformat("2026-06-11T03:00:00"), "load": 7.0},
        {"dt": datetime.fromisoformat("2026-06-11T03:30:00"), "load": 1.0},
        {"dt": datetime.fromisoformat("2026-06-11T04:00:00"), "load": 1.2},
        {"dt": datetime.fromisoformat("2026-06-11T04:30:00"), "load": 1.2},
        {"dt": datetime.fromisoformat("2026-06-12T03:00:00"), "load": 0.5},
    ]

    guard = _estimate_remaining_overnight_load_kwh(rows, target_date="2026-06-12")

    assert guard["expected_kwh"] == pytest.approx(3.4)
    assert set(guard["hourly_load_forecast_kwh"]) == {"4"}


def test_estimate_remaining_overnight_load_includes_evening_after_latest_sample(monkeypatch) -> None:
    monkeypatch.setenv("OVERNIGHT_DISCHARGE_GUARD_MIN_DAYS", "1")
    rows = [
        {"dt": datetime.fromisoformat("2026-06-10T21:30:00"), "load": 1.0},
        {"dt": datetime.fromisoformat("2026-06-10T22:00:00"), "load": 1.1},
        {"dt": datetime.fromisoformat("2026-06-10T22:30:00"), "load": 1.2},
        {"dt": datetime.fromisoformat("2026-06-11T00:00:00"), "load": 1.3},
        {"dt": datetime.fromisoformat("2026-06-11T00:30:00"), "load": 1.4},
        {"dt": datetime.fromisoformat("2026-06-11T21:00:00"), "load": 0.5},
    ]

    guard = _estimate_remaining_overnight_load_kwh(rows, target_date="2026-06-12")

    assert guard["expected_kwh"] == pytest.approx(6.0)
    assert guard["hourly_load_forecast_kwh"]["22"] == pytest.approx(2.3)
    assert guard["hourly_aggregation"]["incomplete_hour_count"] == 1
    assert guard["hourly_aggregation"]["missing_interval_count"] == 1


def test_estimate_remaining_overnight_load_preserves_floor_without_cap(monkeypatch) -> None:
    monkeypatch.setenv("OVERNIGHT_DISCHARGE_GUARD_MIN_DAYS", "3")
    monkeypatch.setenv("OVERNIGHT_DISCHARGE_GUARD_FLOOR_KWH", "4.5")
    monkeypatch.setenv("OVERNIGHT_DISCHARGE_GUARD_CAP_KWH", "0")
    rows = [{"dt": datetime.fromisoformat("2026-06-11T21:00:00"), "load": 0.5}]

    guard = _estimate_remaining_overnight_load_kwh(rows, target_date="2026-06-12")

    assert guard["reason"] == "insufficient_history"
    assert guard["expected_kwh"] == pytest.approx(4.5)
    assert guard["uncapped_expected_kwh"] == pytest.approx(4.5)
    assert guard["cap_applied"] is False


def test_build_hourly_load_forecast_fills_overnight_hours_from_history() -> None:
    rows = [
        {"dt": datetime.fromisoformat("2026-06-10T00:00:00"), "load": 0.1},
        {"dt": datetime.fromisoformat("2026-06-10T00:30:00"), "load": 0.3},
        {"dt": datetime.fromisoformat("2026-06-11T00:00:00"), "load": 0.2},
        {"dt": datetime.fromisoformat("2026-06-11T00:30:00"), "load": 0.4},
        {"dt": datetime.fromisoformat("2026-06-10T23:00:00"), "load": 0.3},
        {"dt": datetime.fromisoformat("2026-06-10T23:30:00"), "load": 0.5},
        {"dt": datetime.fromisoformat("2026-06-11T23:00:00"), "load": 0.4},
        {"dt": datetime.fromisoformat("2026-06-11T23:30:00"), "load": 0.6},
        {"dt": datetime.fromisoformat("2026-06-10T07:00:00"), "load": 1.0},
        {"dt": datetime.fromisoformat("2026-06-10T10:00:00"), "load": 1.0},
    ]

    forecast = _build_hourly_load_forecast(
        rows,
        daytime_load_kwh=4.0,
        morning_load_kwh=1.0,
        overnight_load_by_hour={23: 0.7},
    )

    assert forecast[0] == pytest.approx(0.5)
    assert forecast[23] == pytest.approx(0.7)


def test_risk_adjusted_peak_penalty_does_not_duplicate_temperature_uncertainty(monkeypatch) -> None:
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
    assert both["applied_factor"] == pytest.approx(1.0)
    assert both["risk_reasons"] == ["high_temperature", "pv_overconfidence"]
    assert both["temperature_uncertainty_integrated_in_load_scenarios"] is True


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
    assert rationale["corrected_hourly_load_forecast_kwh"]["7"] == pytest.approx(1.2)
    assert rationale["corrected_hourly_load_forecast_kwh"]["17"] == pytest.approx(1.2)
    assert rationale["soc_peak_unmet_penalty"]["applied_factor"] == pytest.approx(1.0)
    assert len(correction["load_scenarios"]) == 5


def test_nonlinear_temperature_residual_grows_for_unseen_heat(monkeypatch) -> None:
    monkeypatch.setenv("EVENING_LOAD_TEMPERATURE_MIN_SAMPLES", "3")
    monkeypatch.setenv("EVENING_LOAD_TEMPERATURE_MIN_EFFECTIVE_SAMPLES", "0")
    forecast_history = {}
    actual_history = {}
    temperature_history = {}
    for index, temp in enumerate((24.0, 26.0, 28.0, 30.0, 32.0, 34.0), start=1):
        day = f"2026-06-{index:02d}"
        forecast_history[day] = {hour: {"load": 1.0} for hour in range(7, 23)}
        actual_ratio = 1.0 + max(0.0, temp - 28.0) * 0.04
        actual_history[day] = {hour: {"load": actual_ratio} for hour in range(7, 23)}
        temperature_history[day] = {
            "cooling_degree_hours_24": max(0.0, temp - 24.0) * 23.0,
            "cooling_degree_hours_28": max(0.0, temp - 28.0) * 23.0,
            "cooling_degree_hours_32": max(0.0, temp - 32.0) * 23.0,
            "hot_hours_35": 0.0,
            "max_temp_c": temp,
            "temp_ewma_12h_evening": temp,
            "night_min_temp_c": temp - 5.0,
        }

    mild = _evening_temperature_correction(
        forecast_history=forecast_history,
        actual_history=actual_history,
        historical_temperature_features=temperature_history,
        target_features={
            "cooling_degree_hours_24": 46.0,
            "cooling_degree_hours_28": 0.0,
            "cooling_degree_hours_32": 0.0,
            "hot_hours_35": 0.0,
            "max_temp_c": 28.0,
            "temp_ewma_12h_evening": 28.0,
            "night_min_temp_c": 23.0,
        },
        load_ratio=1.0,
    )
    hot = _evening_temperature_correction(
        forecast_history=forecast_history,
        actual_history=actual_history,
        historical_temperature_features=temperature_history,
        target_features={
            "cooling_degree_hours_24": 230.0,
            "cooling_degree_hours_28": 138.0,
            "cooling_degree_hours_32": 46.0,
            "hot_hours_35": 12.0,
            "max_temp_c": 40.0,
            "temp_ewma_12h_evening": 36.0,
            "night_min_temp_c": 29.0,
        },
        load_ratio=1.0,
    )

    assert hot["multiplier"] > mild["multiplier"]
    assert 0.0 < hot["confidence"] < 1.0
    assert [row["label"] for row in hot["load_scenarios"]] == [
        "load_q10",
        "load_q30",
        "load_q50",
        "load_q70",
        "load_q90",
    ]
    assert hot["load_scenarios"][2]["multiplier"] == pytest.approx(1.0)


def test_high_temperature_correction_cannot_reduce_forecast(monkeypatch) -> None:
    monkeypatch.setenv("EVENING_LOAD_TEMPERATURE_MIN_SAMPLES", "3")
    monkeypatch.setenv("EVENING_LOAD_TEMPERATURE_MIN_EFFECTIVE_SAMPLES", "0")
    forecast_history = {}
    actual_history = {}
    temperature_history = {}
    features = {
        "cooling_degree_hours_24": 150.0,
        "cooling_degree_hours_28": 58.0,
        "cooling_degree_hours_32": 5.0,
        "hot_hours_35": 0.0,
        "max_temp_c": 34.0,
        "temp_ewma_12h_evening": 31.0,
        "night_min_temp_c": 27.0,
    }
    for index in range(1, 7):
        day = f"2026-06-{index:02d}"
        forecast_history[day] = {hour: {"load": 1.0} for hour in range(24)}
        actual_history[day] = {hour: {"load": 0.5} for hour in range(24)}
        temperature_history[day] = features

    correction = _evening_temperature_correction(
        forecast_history=forecast_history,
        actual_history=actual_history,
        historical_temperature_features=temperature_history,
        target_features=features,
        load_ratio=1.0,
    )

    assert correction["multiplier_before_monotonic_floor"] < 1.0
    assert correction["multiplier"] == pytest.approx(1.0)
    assert correction["high_temperature"] is True
    assert correction["monotonic_floor_applied"] is True


def test_temperature_correction_uses_prior_when_similar_history_is_insufficient(monkeypatch) -> None:
    monkeypatch.setenv("EVENING_LOAD_TEMPERATURE_MIN_SAMPLES", "3")
    monkeypatch.setenv("EVENING_LOAD_TEMPERATURE_MIN_EFFECTIVE_SAMPLES", "5")
    forecast_history = {}
    actual_history = {}
    temperature_history = {}
    cool_features = {
        "cooling_degree_hours_24": 0.0,
        "cooling_degree_hours_28": 0.0,
        "cooling_degree_hours_32": 0.0,
        "hot_hours_35": 0.0,
        "max_temp_c": 20.0,
        "temp_ewma_12h_evening": 18.0,
        "night_min_temp_c": 15.0,
    }
    for index in range(1, 7):
        day = f"2026-06-{index:02d}"
        forecast_history[day] = {hour: {"load": 1.0} for hour in range(24)}
        actual_history[day] = {hour: {"load": 0.8} for hour in range(24)}
        temperature_history[day] = cool_features
    hot_features = {**cool_features, "max_temp_c": 36.0, "cooling_degree_hours_28": 80.0}

    correction = _evening_temperature_correction(
        forecast_history=forecast_history,
        actual_history=actual_history,
        historical_temperature_features=temperature_history,
        target_features=hot_features,
        load_ratio=1.0,
    )

    assert correction["reason"] == "insufficient_similar_temperature_history"
    assert correction["data_regression_suppressed"] is True
    assert correction["multiplier"] > 1.0


def test_temperature_hourly_shape_tracks_heat_and_preserves_total() -> None:
    load = {7: 2.0, 8: 2.0, 9: 2.0}
    multipliers = _temperature_hourly_multipliers(
        hourly_load_forecast=load,
        hourly_temperatures={7: 25.0, 8: 30.0, 9: 35.0},
        correction_hours={7, 8, 9},
        total_multiplier=1.2,
    )

    assert multipliers[7] < multipliers[8] < multipliers[9]
    corrected_total = sum(load[hour] * multipliers[hour] for hour in load)
    assert corrected_total == pytest.approx(sum(load.values()) * 1.2)


def test_recent_and_analog_floor_uses_similar_day_with_safety_factor(monkeypatch) -> None:
    monkeypatch.setenv("LOAD_ANALOG_SAFETY_FACTOR", "1.20")
    monkeypatch.setenv("LOAD_ANALOG_MIN_SIMILARITY", "0.50")
    actual_history = {
        "2026-07-10": {7: {"load": 10.0, "pv": 5.0}},
        "2026-07-11": {7: {"load": 8.0, "pv": 2.0}},
        "2026-07-12": {7: {"load": 7.0, "pv": 1.0}},
    }
    historical_features = {
        "2026-07-10": {
            "cooling_degree_hours_28": 30.0,
            "temp_ewma_12h_evening": 29.0,
            "night_min_temp_c": 25.0,
        },
        "2026-07-11": {
            "cooling_degree_hours_28": 0.0,
            "temp_ewma_12h_evening": 23.0,
            "night_min_temp_c": 20.0,
        },
        "2026-07-12": {
            "cooling_degree_hours_28": 0.0,
            "temp_ewma_12h_evening": 22.0,
            "night_min_temp_c": 19.0,
        },
    }

    floor = _recent_and_analog_daytime_floor(
        actual_history=actual_history,
        historical_temperature_features=historical_features,
        target_features={
            "cooling_degree_hours_28": 30.0,
            "temp_ewma_12h_evening": 29.0,
            "night_min_temp_c": 25.0,
        },
        target_pv_kwh=5.0,
    )

    assert floor["source"] == "analog"
    assert floor["analog_day"] == "2026-07-10"
    assert floor["analog_similarity"] == pytest.approx(1.0)
    assert floor["analog_floor_kwh"] == pytest.approx(12.0)
    assert floor["floor_kwh"] == pytest.approx(12.0)


def test_cost_optimizer_uses_adaptive_load_scenarios() -> None:
    scenarios = _load_scenarios_for_cost_optimizer(
        {
            "load_scenarios": [
                {"label": "load_q10", "probability": 0.2, "multiplier": 0.8},
                {"label": "load_q50", "probability": 0.6, "multiplier": 1.0},
                {"label": "load_q90", "probability": 0.2, "multiplier": 1.3},
            ]
        }
    )

    assert scenarios is not None
    assert [scenario.label for scenario in scenarios] == ["load_q10", "load_q50", "load_q90"]
    assert scenarios[-1].load_multiplier == pytest.approx(1.3)


def test_build_forecast_correction_can_skip_pv_ratio_for_physical_model(monkeypatch) -> None:
    monkeypatch.setenv("PV_RATIO_EWMA_ALPHA", "0.5")
    monkeypatch.setenv("PV_RATIO_EWMA_MIN", "0.9")
    monkeypatch.setenv("PV_RATIO_EWMA_MAX", "1.35")

    def fake_history(*, target_date: str):
        return {"2026-05-30": {7: {"pv": 1.0, "load": 1.0}}}, "test_history"

    def fake_temperature(*, lat: float, lon: float, timezone: str, start_date: str, end_date: str, archive: bool):
        return {day: {hour: 20.0 for hour in range(23)} for day in ["2026-05-30", "2026-05-31"]}

    monkeypatch.setattr("app.forecast_correction._load_forecast_hourly_history", fake_history)
    monkeypatch.setattr("app.forecast_correction._fetch_hourly_temperatures", fake_temperature)

    correction = _build_forecast_correction(
        rows=[{"dt": datetime.fromisoformat("2026-05-30T07:00:00"), "pv": 4.0, "load": 1.0}],
        hourly_load_forecast={7: 1.0},
        hourly_pv_forecast={7: 2.0},
        target_date="2026-05-31",
        lat=35.0,
        lon=139.0,
        timezone="Asia/Tokyo",
        forecast={"date": "2026-05-31", "temp_c": 20.0},
        skip_pv_correction=True,
    )

    rationale = correction["rationale"]
    assert rationale["pv_ratio_ewma_raw"] > 1.0
    assert rationale["pv_ratio_ewma_skipped"] is True
    assert rationale["corrected_hourly_pv_forecast_kwh"]["7"] == pytest.approx(2.0)


def test_selected_pv_uncertainty_uses_physical_neutral_mean(monkeypatch) -> None:
    monkeypatch.setenv("PV_FORECAST_ERROR_MIN_SAMPLE_DAYS", "1")
    monkeypatch.setenv("PHYSICAL_PV_FORECAST_ERROR_RATIO_STD", "0.22")
    pv_array_forecast = {
        "calibration": {
            "forecast_error_distribution": {
                "sample_count": 5,
                "mean_multiplier": 0.55,
                "std_multiplier": 0.11,
                "source": "legacy_pv",
            }
        }
    }

    physical = _selected_pv_uncertainty(
        physical_pv_selected=True,
        physical_pv_diagnostics={
            "selected_method": "physical_altitude_shortwave",
            "data_quality": {"global_days": 8},
        },
        pv_array_forecast=pv_array_forecast,
    )
    legacy = _selected_pv_uncertainty(
        physical_pv_selected=False,
        physical_pv_diagnostics={},
        pv_array_forecast=pv_array_forecast,
    )

    assert physical.mean_multiplier == pytest.approx(1.0)
    assert physical.std_multiplier == pytest.approx(0.22)
    assert physical.sample_count == 8
    assert physical.source == "physical_altitude_shortwave_neutral_mean"
    assert legacy.mean_multiplier == pytest.approx(0.55)
