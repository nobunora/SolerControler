from copy import deepcopy
from datetime import datetime

import pytest

from app.forecasting import pv_physical
from app.forecasting.correction_model import _correct_hourly_pv


@pytest.fixture(autouse=True)
def isolated_physical_history(monkeypatch):
    monkeypatch.setattr(pv_physical, "_actual_hourly_from_sqlite", lambda **kwargs: {})
    monkeypatch.setenv("PHYSICAL_PV_FORECAST_ENABLED", "true")
    monkeypatch.setenv("PHYSICAL_PV_GLOBAL_MIN_DAYS", "0")
    monkeypatch.setenv("PHYSICAL_PV_RADIATION_SCALE", "1")
    monkeypatch.setenv("PHYSICAL_PV_VECTOR_RESIDUAL_ENABLED", "true")
    monkeypatch.setenv("PHYSICAL_PV_VECTOR_RESIDUAL_SPREAD_KWH", "0.6")


def forecast(pulse_hour):
    return {"hourly_weather": [
        {"hour": hour, "shortwave_radiation_w_m2": 400.0 if hour == pulse_hour else 0.0,
         "weather_code": 3}
        for hour in range(24)
    ]}


def candidate(weather, *, rows=None, history=None):
    return pv_physical.build_physical_pv_candidate(
        rows=rows or [], forecast_history=history or {}, existing_hourly_pv={},
        forecast=weather, target_date="2026-06-25", lat=35.0, lon=139.0,
        timezone="Asia/Tokyo",
    )


@pytest.mark.parametrize("provider_hour", [6, 9, 17])
def test_previous_hour_mean_generates_in_its_actual_interval(provider_hour):
    weather = forecast(provider_hour)
    original = deepcopy(weather)
    result = candidate(weather)

    assert result.hourly_pv_kwh[provider_hour - 1] > 0
    assert result.hourly_pv_kwh[provider_hour] == 0
    assert weather == original


def test_past_calibration_uses_the_same_radiation_interval(monkeypatch):
    monkeypatch.setenv("PHYSICAL_PV_RADIATION_SCALE", "0")
    history = {
        day: {hour: {"shortwave": 400.0 if hour == 9 else 0.0} for hour in range(24)}
        for day in ["2026-06-23", "2026-06-24"]
    }
    rows = [{"dt": datetime.fromisoformat(day + "T08:00:00"), "pv": 1.0} for day in history]
    original = deepcopy(history)
    result = candidate(forecast(9), rows=rows, history=history)

    assert result.hourly_pv_kwh[8] == pytest.approx(1.0, abs=0.02)
    assert result.hourly_pv_kwh[9] == 0
    assert result.diagnostics["scales"]["radiation_scale_fit"]["sample_count"] == 2
    assert history == original

    # Sunrise-only inputs must not leak into the protected 07+ calibration window.
    early = deepcopy(history)
    for hours in early.values():
        hours[6]["shortwave"] = 800.0
        hours[7]["shortwave"] = 800.0
    with_early_radiation = candidate(forecast(9), rows=rows, history=early)
    assert with_early_radiation.hourly_pv_kwh == result.hourly_pv_kwh
    assert with_early_radiation.diagnostics["scales"] == result.diagnostics["scales"]


def test_residual_uses_interval_radiation_without_shifting_pv_or_weather_code():
    weather = forecast(9)
    weather["hourly_weather"][9]["weather_code"] = 95
    history = {"2026-06-24": {
        8: {"pv": 1.0, "shortwave": 0.0, "weather_code": 3},
        9: {"pv": 9.0, "shortwave": 400.0, "weather_code": 0},
    }}
    original = deepcopy(history)
    corrected, metadata, multiplier = _correct_hourly_pv(
        hourly_pv_forecast={8: 1.0}, pv_ratio=1.5, skip_pv_correction=True,
        forecast_history=history, actual_history={"2026-06-24": {8: {"pv": 1.5}}},
        forecast=weather,
    )

    assert corrected[8] == pytest.approx(1 + 0.5 / 6)
    assert multiplier == 1.0
    assert metadata["applied"][0]["hour"] == 8
    assert metadata["applied"][0]["count"] == 1
    assert history == original


def test_missing_interval_radiation_does_not_fall_back_to_same_hour():
    weather = {"hourly_weather": [{"hour": 8, "shortwave_radiation_w_m2": 400.0, "weather_code": 3}]}
    corrected, metadata, _ = _correct_hourly_pv(
        hourly_pv_forecast={8: 1.0}, pv_ratio=1.0, skip_pv_correction=True,
        forecast_history={"2026-06-24": {8: {"pv": 1.0, "shortwave": 400.0, "weather_code": 3}}},
        actual_history={"2026-06-24": {8: {"pv": 0.0}}}, forecast=weather,
    )
    assert corrected == {8: 1.0}
    assert metadata["applied"] == []
