from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Callable

import numpy as np

try:
    from sklearn.ensemble import HistGradientBoostingRegressor
except Exception:  # pragma: no cover - optional runtime dependency guard
    HistGradientBoostingRegressor = None  # type: ignore[misc, assignment]


MODEL_NAME = "adaptive_comfort_thermal_inertia_hgb_v1"
THERMAL_HALF_LIVES = (3, 12, 24)
ADAPTIVE_LOOKBACK_HOURS = 168

FEATURE_NAMES = [
    "hour_sin",
    "hour_cos",
    "weekday_sin",
    "weekday_cos",
    "is_weekend",
    "temp_current",
    "humidity_current",
    "wind_current",
    "prevailing_temp",
    "adaptive_comfort_center",
    "comfort_delta",
    "comfort_magnitude",
    "moist_air_enthalpy",
    "temp_humidity_interaction",
    "wind_comfort_exchange",
    *[
        f"{name}_ewm_{half_life}h"
        for half_life in THERMAL_HALF_LIVES
        for name in ("temp", "humidity", "enthalpy", "comfort_magnitude")
    ],
]


def _moist_air_enthalpy(temp_c: float, relative_humidity_percent: float) -> float:
    humidity = min(100.0, max(0.0, relative_humidity_percent))
    saturation_hpa = 6.112 * math.exp(17.67 * temp_c / (temp_c + 243.5))
    vapor_hpa = saturation_hpa * humidity / 100.0
    humidity_ratio = 0.62198 * vapor_hpa / max(1.0, 1013.25 - vapor_hpa)
    return 1.006 * temp_c + humidity_ratio * (2501.0 + 1.86 * temp_c)


def _flatten_weather(
    weather_by_day: dict[str, dict[int, dict[str, float]]],
) -> dict[datetime, dict[str, float]]:
    flattened: dict[datetime, dict[str, float]] = {}
    for raw_day, hourly in weather_by_day.items():
        try:
            day = datetime.fromisoformat(raw_day)
        except ValueError:
            continue
        for hour, row in hourly.items():
            if 0 <= hour <= 23:
                flattened[day.replace(hour=hour)] = row
    return flattened


def _exponential_history(
    weather: dict[datetime, dict[str, float]],
    ts: datetime,
    value_fn: Callable[[dict[str, float]], float],
    *,
    half_life_hours: int,
    lookback_hours: int,
    default: float,
) -> float:
    weighted_sum = 0.0
    total_weight = 0.0
    for offset in range(1, lookback_hours + 1):
        row = weather.get(ts - timedelta(hours=offset))
        if row is None:
            continue
        weight = math.exp(-math.log(2.0) * offset / half_life_hours)
        weighted_sum += weight * value_fn(row)
        total_weight += weight
    return weighted_sum / total_weight if total_weight else default


def _feature_map(
    ts: datetime,
    weather: dict[datetime, dict[str, float]],
) -> dict[str, float] | None:
    current = weather.get(ts)
    if current is None:
        return None
    temp = float(current.get("temp_c", 24.0))
    humidity = float(current.get("relative_humidity_percent", 60.0))
    wind = max(0.0, float(current.get("wind_speed_10m", 0.0)))
    enthalpy = _moist_air_enthalpy(temp, humidity)
    prevailing_temp = _exponential_history(
        weather,
        ts,
        lambda row: float(row.get("temp_c", 24.0)),
        half_life_hours=24,
        lookback_hours=ADAPTIVE_LOOKBACK_HOURS,
        default=temp,
    )
    comfort_center = 0.31 * prevailing_temp + 17.8
    comfort_delta = temp - comfort_center
    comfort_magnitude = math.sqrt(comfort_delta * comfort_delta + 1.0)
    hour_angle = 2.0 * math.pi * ts.hour / 24.0
    weekday_angle = 2.0 * math.pi * ts.weekday() / 7.0
    output = {
        "hour_sin": math.sin(hour_angle),
        "hour_cos": math.cos(hour_angle),
        "weekday_sin": math.sin(weekday_angle),
        "weekday_cos": math.cos(weekday_angle),
        "is_weekend": float(ts.weekday() >= 5),
        "temp_current": temp,
        "humidity_current": humidity,
        "wind_current": wind,
        "prevailing_temp": prevailing_temp,
        "adaptive_comfort_center": comfort_center,
        "comfort_delta": comfort_delta,
        "comfort_magnitude": comfort_magnitude,
        "moist_air_enthalpy": enthalpy,
        "temp_humidity_interaction": temp * humidity / 100.0,
        "wind_comfort_exchange": wind * comfort_magnitude,
    }
    for half_life in THERMAL_HALF_LIVES:
        lookback = max(24, half_life * 6)
        output[f"temp_ewm_{half_life}h"] = _exponential_history(
            weather,
            ts,
            lambda row: float(row.get("temp_c", 24.0)),
            half_life_hours=half_life,
            lookback_hours=lookback,
            default=temp,
        )
        output[f"humidity_ewm_{half_life}h"] = _exponential_history(
            weather,
            ts,
            lambda row: float(row.get("relative_humidity_percent", 60.0)),
            half_life_hours=half_life,
            lookback_hours=lookback,
            default=humidity,
        )
        output[f"enthalpy_ewm_{half_life}h"] = _exponential_history(
            weather,
            ts,
            lambda row: _moist_air_enthalpy(
                float(row.get("temp_c", 24.0)),
                float(row.get("relative_humidity_percent", 60.0)),
            ),
            half_life_hours=half_life,
            lookback_hours=lookback,
            default=enthalpy,
        )
        output[f"comfort_magnitude_ewm_{half_life}h"] = _exponential_history(
            weather,
            ts,
            lambda row: math.sqrt((float(row.get("temp_c", 24.0)) - comfort_center) ** 2 + 1.0),
            half_life_hours=half_life,
            lookback_hours=lookback,
            default=comfort_magnitude,
        )
    return output


def predict_hourly_comfort_load(
    *,
    actual_history: dict[str, dict[int, dict[str, float]]],
    weather_by_day: dict[str, dict[int, dict[str, float]]],
    target_date: str,
    min_samples: int,
) -> dict[str, object]:
    if HistGradientBoostingRegressor is None:
        return {"enabled": True, "applied": False, "reason": "sklearn_unavailable", "model": MODEL_NAME}
    try:
        target_day = datetime.fromisoformat(target_date)
    except ValueError:
        return {"enabled": True, "applied": False, "reason": "invalid_target_date", "model": MODEL_NAME}

    weather = _flatten_weather(weather_by_day)
    training_rows: list[list[float]] = []
    training_targets: list[float] = []
    training_times: list[datetime] = []
    for day in sorted(actual_history):
        if day >= target_date:
            continue
        try:
            current_day = datetime.fromisoformat(day)
        except ValueError:
            continue
        for hour, values in sorted(actual_history[day].items()):
            ts = current_day.replace(hour=hour)
            features = _feature_map(ts, weather)
            load = float(values.get("load", 0.0))
            if features is None or load <= 0.0:
                continue
            training_rows.append([features[name] for name in FEATURE_NAMES])
            training_targets.append(load)
            training_times.append(ts)

    required_samples = max(24, min_samples)
    if len(training_rows) < required_samples:
        return {
            "enabled": True,
            "applied": False,
            "reason": "insufficient_history",
            "model": MODEL_NAME,
            "sample_count": len(training_rows),
            "min_samples": required_samples,
        }

    target_times = [target_day.replace(hour=hour) for hour in range(24)]
    target_features = [_feature_map(ts, weather) for ts in target_times]
    if any(features is None for features in target_features):
        return {
            "enabled": True,
            "applied": False,
            "reason": "incomplete_target_weather",
            "model": MODEL_NAME,
            "sample_count": len(training_rows),
        }

    model = HistGradientBoostingRegressor(
        learning_rate=0.05,
        max_iter=250,
        max_leaf_nodes=15,
        min_samples_leaf=15,
        l2_regularization=1.0,
        random_state=42,
    )
    x_train = np.asarray(training_rows, dtype=float)
    y_train = np.asarray(training_targets, dtype=float)
    try:
        model.fit(x_train, y_train)
        fitted = np.maximum(0.0, np.asarray(model.predict(x_train), dtype=float))
        x_target = np.asarray(
            [[features[name] for name in FEATURE_NAMES] for features in target_features if features is not None],
            dtype=float,
        )
        predicted = np.maximum(0.0, np.asarray(model.predict(x_target), dtype=float))
    except Exception as exc:
        return {
            "enabled": True,
            "applied": False,
            "reason": "fit_failed",
            "model": MODEL_NAME,
            "error_type": type(exc).__name__,
            "sample_count": len(training_rows),
        }

    residual_multipliers = [
        actual / prediction
        for actual, prediction in zip(training_targets, fitted.tolist())
        if prediction > 0.05 and math.isfinite(actual / prediction)
    ]
    hourly = {hour: float(predicted[hour]) for hour in range(24)}
    mae = float(np.mean(np.abs(fitted - y_train)))
    confidence = len(training_rows) / (len(training_rows) + required_samples)
    return {
        "enabled": True,
        "applied": True,
        "reason": "ok",
        "model": MODEL_NAME,
        "sample_count": len(training_rows),
        "min_samples": required_samples,
        "training_start": min(training_times).isoformat(),
        "training_end": max(training_times).isoformat(),
        "feature_names": FEATURE_NAMES,
        "training_mae_kwh": round(mae, 6),
        "confidence": round(confidence, 6),
        "hourly_load_kwh": hourly,
        "_residual_multipliers": residual_multipliers,
    }
