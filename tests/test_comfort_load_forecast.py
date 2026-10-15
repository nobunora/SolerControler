from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.forecasting.comfort_load import MODEL_NAME, build_comfort_feature_map, predict_hourly_comfort_load


def _synthetic_history(*, days: int) -> tuple[
    dict[str, dict[int, dict[str, float]]],
    dict[str, dict[int, dict[str, float]]],
    str,
]:
    start = datetime(2026, 6, 1)
    actual: dict[str, dict[int, dict[str, float]]] = {}
    weather: dict[str, dict[int, dict[str, float]]] = {}
    for day_offset in range(days + 1):
        current = start + timedelta(days=day_offset)
        day = current.date().isoformat()
        weather[day] = {}
        if day_offset < days:
            actual[day] = {}
        for hour in range(24):
            temp = 25.0 + 5.0 * math.sin(2.0 * math.pi * (hour - 8) / 24.0) + day_offset * 0.05
            humidity = 70.0 - 15.0 * math.sin(2.0 * math.pi * (hour - 8) / 24.0)
            weather[day][hour] = {
                "temp_c": temp,
                "relative_humidity_percent": humidity,
                "dew_point_c": 18.0,
                "wind_speed_10m": 2.0 + hour % 3,
            }
            if day_offset < days:
                actual[day][hour] = {
                    "load": 0.8 + 0.03 * abs(temp - 25.0) + 0.002 * humidity,
                    "pv": 0.0,
                }
    return actual, weather, (start + timedelta(days=days)).date().isoformat()


def test_predict_hourly_comfort_load_returns_complete_nonnegative_forecast() -> None:
    actual, weather, target_date = _synthetic_history(days=20)

    result = predict_hourly_comfort_load(
        actual_history=actual,
        weather_by_day=weather,
        target_date=target_date,
        min_samples=336,
    )

    assert result["applied"] is True
    assert result["model"] == MODEL_NAME
    assert result["sample_count"] == 20 * 24
    training_end = result["training_end"]
    assert isinstance(training_end, str)
    assert training_end < f"{target_date}T00:00:00"
    hourly = result["hourly_load_kwh"]
    assert isinstance(hourly, dict)
    assert sorted(hourly) == list(range(24))
    assert all(value >= 0.0 for value in hourly.values())
    assert result["_residual_multipliers"]


def test_predict_hourly_comfort_load_rejects_insufficient_history() -> None:
    actual, weather, target_date = _synthetic_history(days=2)

    result = predict_hourly_comfort_load(
        actual_history=actual,
        weather_by_day=weather,
        target_date=target_date,
        min_samples=336,
    )

    assert result == {
        "enabled": True,
        "applied": False,
        "reason": "insufficient_history",
        "model": MODEL_NAME,
        "sample_count": 48,
        "min_samples": 336,
    }


def test_measured_zero_load_counts_but_invalid_load_does_not():
    actual, weather, target = _synthetic_history(days=20)
    for hours in actual.values():
        hours[0]["load"] = 0.0
        hours[1]["load"] = float("nan")
        hours[2]["load"] = -1.0
        hours[3].pop("load")
    result = predict_hourly_comfort_load(actual_history=actual, weather_by_day=weather, target_date=target, min_samples=336)
    assert result["applied"] is True
    assert result["sample_count"] == 20 * 21


def test_previous_day_temperature_changes_thermal_context_with_same_current_weather():
    target = datetime(2026, 10, 15)
    current = {"temp_c": 24.0, "relative_humidity_percent": 60.0, "wind_speed_10m": 2.0}
    cold = {target - timedelta(hours=h): {**current, "temp_c": 16.0} for h in range(1, 49)}
    warm = {target - timedelta(hours=h): {**current, "temp_c": 32.0} for h in range(1, 49)}
    cold[target] = warm[target] = current
    cold_features = build_comfort_feature_map(target, cold)
    warm_features = build_comfort_feature_map(target, warm)
    assert cold_features is not None and warm_features is not None
    assert cold_features["temp_current"] == warm_features["temp_current"]
    for name in ("prevailing_temp", "temp_ewm_12h", "temp_ewm_24h"):
        assert cold_features[name] < warm_features[name]
    # Later weather must never change a historical sample's feature vector.
    cold[target + timedelta(hours=1)] = {**current, "temp_c": 50.0}
    assert build_comfort_feature_map(target, cold) == cold_features
