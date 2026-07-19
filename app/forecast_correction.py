from __future__ import annotations

"""Forecast correction layer for the night-charge planner.

This module keeps the tunable, data-driven forecast adjustments out of
energy_model_main.py. The stable contract is simple: take raw hourly PV/load
forecasts and return corrected hourly forecasts plus a human-readable rationale
that can be persisted for later validation.
"""

import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from app.utils import env_bool, env_float, env_float_clamped, to_float, to_int


@dataclass(frozen=True)
class ForecastCorrectionInput:
    rows: list[dict[str, Any]]
    hourly_load_forecast: dict[int, float]
    hourly_pv_forecast: dict[int, float]
    target_date: str
    latitude: float
    longitude: float
    timezone: str
    forecast: dict[str, object]


@dataclass(frozen=True)
class ForecastCorrectionPolicy:
    enabled: bool
    skip_pv_correction: bool
    allow_load_safety_floor: bool
    pv_ewma_alpha: float
    pv_ratio_min: float
    pv_ratio_max: float
    load_ewma_alpha: float
    load_ratio_min: float
    load_ratio_max: float

    @classmethod
    def from_env(
        cls,
        *,
        skip_pv_correction: bool = False,
        allow_load_safety_floor: bool = True,
    ) -> "ForecastCorrectionPolicy":
        pv_min = max(0.0, env_float("PV_RATIO_EWMA_MIN", default=0.9))
        load_min = max(0.0, env_float("LOAD_RATIO_EWMA_MIN", default=0.7))
        return cls(
            enabled=env_bool("FORECAST_CORRECTION_ENABLED", default=True),
            skip_pv_correction=skip_pv_correction,
            allow_load_safety_floor=allow_load_safety_floor,
            pv_ewma_alpha=env_float_clamped("PV_RATIO_EWMA_ALPHA", 0.2, min_val=0.0, max_val=1.0),
            pv_ratio_min=pv_min,
            pv_ratio_max=max(pv_min, env_float("PV_RATIO_EWMA_MAX", default=1.35)),
            load_ewma_alpha=env_float_clamped(
                "LOAD_RATIO_EWMA_ALPHA", 0.5, min_val=0.0, max_val=1.0
            ),
            load_ratio_min=load_min,
            load_ratio_max=max(load_min, env_float("LOAD_RATIO_EWMA_MAX", default=1.8)),
        )


def _clip_float(value: float, *, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, value))


def _coerce_hourly_values(value: object) -> dict[int, float]:
    if not isinstance(value, dict):
        return {}
    out: dict[int, float] = {}
    for raw_hour, raw_value in value.items():
        hour = to_int(raw_hour)
        numeric = to_float(raw_value)
        if hour is not None and 0 <= hour <= 23 and numeric is not None:
            out[hour] = max(0.0, numeric)
    return out


def _ewma_ratio_from_daily_pairs(
    pairs: list[tuple[str, float, float]],
    *,
    alpha: float,
    initial_value: float = 1.0,
) -> dict[str, object]:
    """Summarize forecast/actual ratios without letting target-day data leak in."""

    alpha = _clip_float(alpha, min_val=0.0, max_val=1.0)
    current = max(0.0, initial_value)
    used: list[dict[str, float | str]] = []
    for day, forecast_total, actual_total in sorted(pairs, key=lambda item: item[0]):
        if forecast_total <= 0:
            continue
        ratio = max(0.0, actual_total / forecast_total)
        current = alpha * ratio + (1.0 - alpha) * current
        used.append(
            {
                "date": day,
                "forecast_kwh": round(forecast_total, 4),
                "actual_kwh": round(actual_total, 4),
                "ratio": round(ratio, 4),
                "ewma_after_day": round(current, 4),
            }
        )
    return {
        "raw_ratio": current,
        "sample_count": len(used),
        "alpha": alpha,
        "latest_days": used[-7:],
    }


def _actual_hourly_totals_by_day(
    rows: list[dict[str, Any]],
    *,
    target_date: str,
) -> dict[str, dict[int, dict[str, float]]]:
    by_day: dict[str, dict[int, dict[str, float]]] = {}
    for row in rows:
        dt = row.get("dt")
        if not isinstance(dt, datetime):
            continue
        day = dt.date().isoformat()
        if day >= target_date:
            continue
        bucket = by_day.setdefault(day, {}).setdefault(dt.hour, {"pv": 0.0, "load": 0.0})
        bucket["pv"] += max(0.0, to_float(row.get("pv")) or 0.0)
        bucket["load"] += max(0.0, to_float(row.get("load")) or 0.0)
    return by_day


def _forecast_history_start_date(*, target_date: str) -> str:
    lookback_days = max(1, int(env_float("FORECAST_HOURLY_HISTORY_LOOKBACK_DAYS", default=60.0)))
    try:
        target_day = datetime.fromisoformat(target_date).date()
    except ValueError:
        return "0001-01-01"
    return (target_day - timedelta(days=lookback_days)).isoformat()


def _load_forecast_hourly_history_from_sqlite(*, target_date: str) -> dict[str, dict[int, dict[str, float]]]:
    db_path = Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db"))
    if not db_path.exists():
        return {}
    start_date = _forecast_history_start_date(target_date=target_date)
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT date, hour, forecast_pv_kwh, forecast_load_kwh, forecast_shortwave_radiation_w_m2
                FROM forecast_hourly
                WHERE date >= ? AND date < ?
                ORDER BY date, hour
                """,
                (start_date, target_date),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return {}

    out: dict[str, dict[int, dict[str, float]]] = {}
    for row in rows:
        hour = to_int(row["hour"])
        if hour is None or hour < 0 or hour > 23:
            continue
        out.setdefault(str(row["date"]), {})[hour] = {
            "pv": max(0.0, float(row["forecast_pv_kwh"] or 0.0)),
            "load": max(0.0, float(row["forecast_load_kwh"] or 0.0)),
            "shortwave": max(0.0, float(row["forecast_shortwave_radiation_w_m2"] or 0.0)),
        }
    return out


def _load_forecast_hourly_history_from_firestore(*, target_date: str) -> dict[str, dict[int, dict[str, float]]]:
    backend = os.getenv("DATA_BACKEND", "").strip().lower()
    if backend != "firestore" and not os.getenv("FIRESTORE_PROJECT_ID", "").strip():
        return {}
    start_date = _forecast_history_start_date(target_date=target_date)
    try:
        from google.cloud import firestore

        project_id = os.getenv("FIRESTORE_PROJECT_ID", "").strip() or None
        database_id = os.getenv("FIRESTORE_DATABASE_ID", "(default)").strip() or "(default)"
        client = (
            firestore.Client(project=project_id, database=database_id)
            if project_id else firestore.Client(database=database_id)
        )
        docs = list(
            client.collection("forecast_hourly")
            .where("date", ">=", start_date)
            .where("date", "<", target_date)
            .stream()
        )
    except Exception:
        return {}

    out: dict[str, dict[int, dict[str, float]]] = {}
    for doc in docs:
        row = doc.to_dict() or {}
        day = str(row.get("date", "")).strip()
        hour = to_int(row.get("hour"))
        if not day or hour is None or hour < 0 or hour > 23:
            continue
        out.setdefault(day, {})[hour] = {
            "pv": max(0.0, to_float(row.get("forecast_pv_kwh")) or 0.0),
            "load": max(0.0, to_float(row.get("forecast_load_kwh")) or 0.0),
            "shortwave": max(0.0, to_float(row.get("forecast_shortwave_radiation_w_m2")) or 0.0),
        }
    return out


def _load_forecast_hourly_history(*, target_date: str) -> tuple[dict[str, dict[int, dict[str, float]]], str]:
    sqlite_history = _load_forecast_hourly_history_from_sqlite(target_date=target_date)
    if sqlite_history:
        return sqlite_history, "sqlite_forecast_hourly"
    firestore_history = _load_forecast_hourly_history_from_firestore(target_date=target_date)
    if firestore_history:
        return firestore_history, "firestore_forecast_hourly"
    return {}, "unavailable"


def _daily_pairs_for_ratio(
    *,
    forecast_history: dict[str, dict[int, dict[str, float]]],
    actual_history: dict[str, dict[int, dict[str, float]]],
    key: str,
) -> list[tuple[str, float, float]]:
    pairs: list[tuple[str, float, float]] = []
    for day in sorted(set(forecast_history) & set(actual_history)):
        forecast_total = sum(max(0.0, values.get(key, 0.0)) for values in forecast_history[day].values())
        actual_total = sum(max(0.0, values.get(key, 0.0)) for values in actual_history[day].values())
        if forecast_total > 0:
            pairs.append((day, forecast_total, actual_total))
    return pairs


def _fetch_hourly_weather(
    *,
    lat: float,
    lon: float,
    timezone: str,
    start_date: str,
    end_date: str,
    archive: bool,
) -> dict[str, dict[int, dict[str, float]]]:
    url = "https://archive-api.open-meteo.com/v1/archive" if archive else "https://api.open-meteo.com/v1/forecast"
    params: dict[str, str | float] = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": "temperature_2m,relative_humidity_2m,dew_point_2m",
        "timezone": timezone,
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        hourly = resp.json().get("hourly", {})
    except Exception:
        return {}
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    humidity = hourly.get("relative_humidity_2m", [])
    dew_points = hourly.get("dew_point_2m", [])
    out: dict[str, dict[int, dict[str, float]]] = {}
    values = zip(
        times if isinstance(times, list) else [],
        temps if isinstance(temps, list) else [],
        humidity if isinstance(humidity, list) else [],
        dew_points if isinstance(dew_points, list) else [],
    )
    for raw_time, raw_temp, raw_humidity, raw_dew_point in values:
        try:
            dt = datetime.fromisoformat(str(raw_time))
            temp_c = float(raw_temp)
            humidity_percent = float(raw_humidity)
            dew_point_c = float(raw_dew_point)
        except Exception:
            continue
        out.setdefault(dt.date().isoformat(), {})[dt.hour] = {
            "temp_c": temp_c,
            "relative_humidity_percent": humidity_percent,
            "dew_point_c": dew_point_c,
        }
    return out


def _moist_air_enthalpy(temp_c: float, relative_humidity_percent: float) -> float:
    saturation_hpa = 6.112 * math.exp((17.67 * temp_c) / (temp_c + 243.5))
    vapor_hpa = saturation_hpa * _clip_float(relative_humidity_percent, min_val=0.0, max_val=100.0) / 100.0
    humidity_ratio = 0.622 * vapor_hpa / max(1.0, 1013.25 - vapor_hpa)
    return 1.006 * temp_c + humidity_ratio * (2501.0 + 1.86 * temp_c)


def _add_thermal_states(weather: dict[str, dict[int, dict[str, float]]]) -> None:
    states = {"thermal_6h": 0.0, "thermal_24h": 0.0, "thermal_72h": 0.0, "latent_24h": 0.0, "latent_72h": 0.0}
    half_lives = {"thermal_6h": 6.0, "thermal_24h": 24.0, "thermal_72h": 72.0, "latent_24h": 24.0, "latent_72h": 72.0}
    for day in sorted(weather):
        for hour in sorted(weather[day]):
            row = weather[day][hour]
            temp_c = float(row.get("temp_c", 24.0))
            humidity = float(row.get("relative_humidity_percent", 60.0))
            dew_point = float(row.get("dew_point_c", 16.0))
            enthalpy = _moist_air_enthalpy(temp_c, humidity)
            row["enthalpy_kj_kg"] = enthalpy
            thermal_input = max(0.0, temp_c - 24.0) + 0.12 * max(0.0, enthalpy - 55.0)
            latent_input = max(0.0, dew_point - 16.0)
            for name, half_life in half_lives.items():
                alpha = 1.0 - math.exp(-math.log(2.0) / half_life)
                value = latent_input if name.startswith("latent") else thermal_input
                states[name] = alpha * value + (1.0 - alpha) * states[name]
                row[name] = states[name]


def _temperature_features_for_day(
    day: str,
    hourly_weather: dict[int, dict[str, float]],
) -> dict[str, float | None]:
    if not hourly_weather:
        return {
            "cooling_degree_hours_24": None,
            "cooling_degree_hours_28": None,
            "cooling_degree_hours_32": None,
            "hot_hours_35": None,
            "max_temp_c": None,
            "temp_ewma_12h_evening": None,
            "night_min_temp_c": None,
            "mean_relative_humidity_percent": None,
            "mean_dew_point_c": None,
            "mean_enthalpy_kj_kg": None,
            "thermal_24h_end": None,
            "thermal_72h_end": None,
            "latent_72h_end": None,
        }
    hourly_temps = {hour: float(row.get("temp_c", 24.0)) for hour, row in hourly_weather.items()}
    cdh24 = sum(max(0.0, float(hourly_temps.get(hour, 0.0)) - 24.0) for hour in range(24))
    cdh28 = sum(max(0.0, float(hourly_temps.get(hour, 0.0)) - 28.0) for hour in range(24))
    cdh32 = sum(max(0.0, float(hourly_temps.get(hour, 0.0)) - 32.0) for hour in range(24))
    hot_hours_35 = sum(1 for hour in range(24) if float(hourly_temps.get(hour, 0.0)) >= 35.0)
    night_values = [float(hourly_temps[hour]) for hour in range(0, 7) if hour in hourly_temps]
    alpha = 1.0 - pow(2.718281828459045, -1.0 / 12.0)
    ewma: float | None = None
    ewma_by_hour: dict[int, float] = {}
    for hour in range(24):
        if hour not in hourly_temps:
            continue
        value = float(hourly_temps[hour])
        ewma = value if ewma is None else alpha * value + (1.0 - alpha) * ewma
        ewma_by_hour[hour] = ewma
    evening_values = [ewma_by_hour[h] for h in range(17, 23) if h in ewma_by_hour]
    weather_values = list(hourly_weather.values())
    last_hour = max(hourly_weather)
    last = hourly_weather[last_hour]
    return {
        "cooling_degree_hours_24": cdh24,
        "cooling_degree_hours_28": cdh28,
        "cooling_degree_hours_32": cdh32,
        "hot_hours_35": float(hot_hours_35),
        "max_temp_c": max(float(value) for value in hourly_temps.values()),
        "temp_ewma_12h_evening": (sum(evening_values) / len(evening_values)) if evening_values else None,
        "night_min_temp_c": min(night_values) if night_values else None,
        "mean_relative_humidity_percent": sum(float(row.get("relative_humidity_percent", 60.0)) for row in weather_values) / len(weather_values),
        "mean_dew_point_c": sum(float(row.get("dew_point_c", 16.0)) for row in weather_values) / len(weather_values),
        "mean_enthalpy_kj_kg": sum(float(row.get("enthalpy_kj_kg", 50.0)) for row in weather_values) / len(weather_values),
        "thermal_24h_end": float(last.get("thermal_24h", 0.0)),
        "thermal_72h_end": float(last.get("thermal_72h", 0.0)),
        "latent_72h_end": float(last.get("latent_72h", 0.0)),
    }


def _temperature_feature_vector(features: dict[str, float | None]) -> list[float]:
    cdh24 = float(features.get("cooling_degree_hours_24") or 0.0)
    cdh28 = float(features.get("cooling_degree_hours_28") or 0.0)
    cdh32 = float(features.get("cooling_degree_hours_32") or 0.0)
    hot_hours_35 = float(features.get("hot_hours_35") or 0.0)
    ewma_evening = float(features.get("temp_ewma_12h_evening") or 24.0)
    night_min = float(features.get("night_min_temp_c") or 22.0)
    humidity = float(features.get("mean_relative_humidity_percent") or 60.0)
    dew_point = float(features.get("mean_dew_point_c") or 16.0)
    enthalpy = float(features.get("mean_enthalpy_kj_kg") or 50.0)
    thermal_24h = float(features.get("thermal_24h_end") or 0.0)
    thermal_72h = float(features.get("thermal_72h_end") or 0.0)
    latent_72h = float(features.get("latent_72h_end") or 0.0)
    band_24_28 = max(0.0, cdh24 - cdh28)
    band_28_32 = max(0.0, cdh28 - cdh32)
    above_32 = max(0.0, cdh32)
    return [
        1.0,
        band_24_28 / 24.0,
        band_28_32 / 16.0,
        above_32 / 8.0,
        (ewma_evening - 24.0) / 5.0,
        (night_min - 20.0) / 5.0,
        hot_hours_35 / 8.0,
        max(0.0, ewma_evening - 30.0) / 6.0,
        max(0.0, night_min - 26.0) / 6.0,
        max(0.0, humidity - 55.0) / 30.0,
        max(0.0, dew_point - 16.0) / 10.0,
        max(0.0, enthalpy - 50.0) / 25.0,
        thermal_24h / 8.0,
        thermal_72h / 8.0,
        latent_72h / 8.0,
    ]


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(value for value in values if math.isfinite(value))
    if not ordered:
        return 1.0
    position = max(0.0, min(1.0, probability)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _bounded_exp(value: float) -> float:
    return math.exp(max(-20.0, min(20.0, value)))


def _temperature_prior_log_multiplier(features: dict[str, float | None]) -> float:
    max_temp = float(features.get("max_temp_c") or 28.0)
    heat_fraction = max(0.0, min(1.0, (max_temp - 28.0) / 12.0))
    return float(math.log(1.18) * heat_fraction**1.25)


def _effective_temperature_sample_count(
    historical_features: list[dict[str, float | None]],
    target_features: dict[str, float | None],
) -> float:
    target_max = float(target_features.get("max_temp_c") or 24.0)
    return sum(
        math.exp(-pow((float(features.get("max_temp_c") or 24.0) - target_max) / 3.0, 2.0))
        for features in historical_features
    )


def _adaptive_load_scenarios(
    residual_multipliers: list[float],
    *,
    confidence: float,
) -> list[dict[str, float | str]]:
    probabilities = (0.10, 0.30, 0.50, 0.70, 0.90)
    prior = (0.82, 0.92, 1.00, 1.10, 1.22)
    if residual_multipliers:
        data = tuple(_quantile(residual_multipliers, probability) for probability in probabilities)
    else:
        data = prior
    blended = [
        _bounded_exp(
            confidence * math.log(max(0.01, data_value))
            + (1.0 - confidence) * math.log(prior_value)
        )
        for data_value, prior_value in zip(data, prior)
    ]
    median = max(0.01, blended[2])
    return [
        {
            "label": f"load_q{int(probability * 100):02d}",
            "probability": 0.20,
            "multiplier": value / median,
        }
        for probability, value in zip(probabilities, blended)
    ]


def _temperature_correction_hours() -> range:
    raw = os.getenv("LOAD_TEMPERATURE_CORRECTION_HOURS", "0-23").strip()
    if "-" not in raw:
        return range(0, 24)
    start_text, end_text = raw.split("-", 1)
    try:
        start = max(0, min(23, int(start_text.strip())))
        end = max(start, min(23, int(end_text.strip())))
    except ValueError:
        return range(0, 24)
    return range(start, end + 1)


def _solve_ridge_regression(feature_rows: list[list[float]], targets: list[float], *, regularization: float) -> list[float]:
    if not feature_rows or len(feature_rows) != len(targets):
        return []
    size = len(feature_rows[0])
    matrix = [[0.0 for _ in range(size)] for _ in range(size)]
    rhs = [0.0 for _ in range(size)]
    for row, target in zip(feature_rows, targets):
        if len(row) != size:
            return []
        for i in range(size):
            rhs[i] += row[i] * target
            for j in range(size):
                matrix[i][j] += row[i] * row[j]
    for i in range(1, size):
        matrix[i][i] += max(0.0, regularization)

    for i in range(size):
        pivot = max(range(i, size), key=lambda row_index: abs(matrix[row_index][i]))
        if abs(matrix[pivot][i]) < 1e-9:
            return []
        matrix[i], matrix[pivot] = matrix[pivot], matrix[i]
        rhs[i], rhs[pivot] = rhs[pivot], rhs[i]
        divisor = matrix[i][i]
        matrix[i] = [value / divisor for value in matrix[i]]
        rhs[i] /= divisor
        for row_index in range(size):
            if row_index == i:
                continue
            factor = matrix[row_index][i]
            matrix[row_index] = [
                matrix[row_index][column] - factor * matrix[i][column]
                for column in range(size)
            ]
            rhs[row_index] -= factor * rhs[i]
    return rhs


def _evening_temperature_correction(
    *,
    forecast_history: dict[str, dict[int, dict[str, float]]],
    actual_history: dict[str, dict[int, dict[str, float]]],
    historical_temperature_features: dict[str, dict[str, float | None]],
    target_features: dict[str, float | None],
    load_ratio: float,
) -> dict[str, object]:
    enabled = env_bool("EVENING_LOAD_TEMPERATURE_CORRECTION_ENABLED", default=True)
    min_samples = max(1, int(env_float("EVENING_LOAD_TEMPERATURE_MIN_SAMPLES", default=3.0)))
    min_effective_samples = max(
        0.0,
        env_float("EVENING_LOAD_TEMPERATURE_MIN_EFFECTIVE_SAMPLES", default=5.0),
    )
    regularization = max(0.0, env_float("EVENING_LOAD_TEMPERATURE_RIDGE_LAMBDA", default=1.0))
    high_temperature_floor_enabled = env_bool("LOAD_TEMPERATURE_HIGH_FLOOR_ENABLED", default=True)
    high_cdh28_threshold = max(0.0, env_float("LOAD_TEMPERATURE_HIGH_CDH28_THRESHOLD", default=10.0))
    high_max_temp_threshold = env_float("LOAD_TEMPERATURE_HIGH_MAX_C", default=32.0)
    target_cdh28 = float(target_features.get("cooling_degree_hours_28") or 0.0)
    target_max_temp = float(target_features.get("max_temp_c") or 0.0)
    high_temperature = (
        target_cdh28 >= high_cdh28_threshold
        or target_max_temp >= high_max_temp_threshold
    )
    prior_log_multiplier = _temperature_prior_log_multiplier(target_features)

    def prior_only(reason: str, *, sample_count: int, effective_samples: float) -> dict[str, object]:
        multiplier_before_floor = _bounded_exp(prior_log_multiplier)
        multiplier = max(1.0, multiplier_before_floor) if high_temperature_floor_enabled and high_temperature else multiplier_before_floor
        return {
            "enabled": True,
            "applied": abs(multiplier - 1.0) > 1e-9,
            "method": "temperature_prior_confidence_fallback",
            "reason": reason,
            "sample_count": sample_count,
            "min_samples": min_samples,
            "effective_temperature_sample_count": round(effective_samples, 4),
            "min_effective_temperature_samples": min_effective_samples,
            "confidence": 0.0,
            "data_regression_suppressed": True,
            "prior_log_multiplier": round(prior_log_multiplier, 6),
            "multiplier_before_monotonic_floor": round(multiplier_before_floor, 6),
            "multiplier": round(multiplier, 6),
            "multiplier_delta": round(multiplier - 1.0, 6),
            "high_temperature": high_temperature,
            "monotonic_floor_applied": multiplier > multiplier_before_floor,
            "high_temperature_thresholds": {
                "cooling_degree_hours_28": high_cdh28_threshold,
                "max_temp_c": high_max_temp_threshold,
            },
            "load_scenarios": _adaptive_load_scenarios([], confidence=0.0),
            "target_features": target_features,
        }
    if not enabled:
        return {"enabled": False, "applied": False, "multiplier_delta": 0.0, "reason": "disabled"}

    feature_rows: list[list[float]] = []
    residual_targets: list[float] = []
    feature_objects: list[dict[str, float | None]] = []
    training_days: list[str] = []
    correction_hours = _temperature_correction_hours()
    for day in sorted(set(forecast_history) & set(actual_history) & set(historical_temperature_features)):
        forecast_load = sum(
            max(0.0, forecast_history[day].get(hour, {}).get("load", 0.0)) * max(0.0, load_ratio)
            for hour in correction_hours
        )
        actual_load = sum(
            max(0.0, actual_history[day].get(hour, {}).get("load", 0.0))
            for hour in correction_hours
        )
        if forecast_load <= 0:
            continue
        features = historical_temperature_features[day]
        ratio = actual_load / forecast_load
        if ratio <= 0.0 or not math.isfinite(ratio):
            continue
        feature_rows.append(_temperature_feature_vector(features))
        residual_targets.append(ratio - 1.0)
        feature_objects.append(features)
        training_days.append(day)

    if len(feature_rows) < min_samples:
        effective_samples = _effective_temperature_sample_count(feature_objects, target_features)
        return prior_only("insufficient_history", sample_count=len(feature_rows), effective_samples=effective_samples)

    median_residual = _quantile(residual_targets, 0.50)
    absolute_deviations = [abs(value - median_residual) for value in residual_targets]
    robust_scale = max(0.05, 1.4826 * _quantile(absolute_deviations, 0.50))
    robust_targets = [
        max(median_residual - 3.0 * robust_scale, min(median_residual + 3.0 * robust_scale, value))
        for value in residual_targets
    ]
    coefficients = _solve_ridge_regression(feature_rows, robust_targets, regularization=regularization)
    if not coefficients:
        effective_samples = _effective_temperature_sample_count(feature_objects, target_features)
        return prior_only("fit_failed", sample_count=len(feature_rows), effective_samples=effective_samples)
    coefficients = [coefficients[0], *(max(0.0, value) for value in coefficients[1:])]
    target_vector = _temperature_feature_vector(target_features)
    data_delta = sum(value * weight for value, weight in zip(target_vector, coefficients))
    data_multiplier = max(0.01, 1.0 + data_delta)
    data_log_multiplier = math.log(data_multiplier)
    effective_samples = _effective_temperature_sample_count(feature_objects, target_features)
    if effective_samples < min_effective_samples:
        return prior_only(
            "insufficient_similar_temperature_history",
            sample_count=len(feature_rows),
            effective_samples=effective_samples,
        )
    confidence = effective_samples / (effective_samples + 8.0)
    blended_log_multiplier = (
        confidence * data_log_multiplier
        + (1.0 - confidence) * prior_log_multiplier
    )

    fitted_multipliers = [
        max(0.01, 1.0 + sum(value * weight for value, weight in zip(row, coefficients)))
        for row in feature_rows
    ]
    residual_multipliers = [
        max(0.01, 1.0 + actual_delta) / max(0.01, fitted)
        for actual_delta, fitted in zip(residual_targets, fitted_multipliers)
    ]
    residual_median = _quantile(residual_multipliers, 0.50)
    multiplier_before_floor = _bounded_exp(blended_log_multiplier) * max(0.01, residual_median)
    monotonic_floor_applied = (
        high_temperature_floor_enabled
        and high_temperature
        and multiplier_before_floor < 1.0
    )
    multiplier = 1.0 if monotonic_floor_applied else multiplier_before_floor
    load_scenarios = _adaptive_load_scenarios(
        residual_multipliers,
        confidence=confidence,
    )
    return {
        "enabled": True,
        "applied": True,
        "method": "non_overlapping_temperature_bands_with_confidence_gate_and_high_temperature_floor",
        "applied_hours": [min(correction_hours), max(correction_hours)] if correction_hours else [],
        "sample_count": len(feature_rows),
        "effective_temperature_sample_count": round(effective_samples, 4),
        "min_effective_temperature_samples": min_effective_samples,
        "confidence": round(confidence, 6),
        "data_regression_suppressed": False,
        "training_days": training_days[-7:],
        "coefficients": [round(x, 6) for x in coefficients],
        "feature_names": [
            "intercept",
            "degree_hours_24_28",
            "degree_hours_28_32",
            "degree_hours_above_32",
            "evening_temperature_ewma",
            "night_min_temperature",
            "hours_above_35",
            "evening_temperature_above_30",
            "night_min_temperature_above_26",
            "relative_humidity_above_55",
            "dew_point_above_16",
            "moist_air_enthalpy_above_50",
            "thermal_state_24h",
            "thermal_state_72h",
            "latent_state_72h",
        ],
        "data_log_multiplier": round(data_log_multiplier, 6),
        "prior_log_multiplier": round(prior_log_multiplier, 6),
        "residual_median": round(residual_median, 6),
        "multiplier_before_monotonic_floor": round(multiplier_before_floor, 6),
        "multiplier": round(multiplier, 6),
        "multiplier_delta": round(multiplier - 1.0, 6),
        "high_temperature": high_temperature,
        "monotonic_floor_applied": monotonic_floor_applied,
        "high_temperature_thresholds": {
            "cooling_degree_hours_28": high_cdh28_threshold,
            "max_temp_c": high_max_temp_threshold,
        },
        "load_scenarios": load_scenarios,
        "target_features": target_features,
    }


def _temperature_hourly_multipliers(
    *,
    hourly_load_forecast: dict[int, float],
    hourly_temperatures: dict[int, float],
    hourly_weather: dict[int, dict[str, float]] | None = None,
    correction_hours: set[int],
    total_multiplier: float,
) -> dict[int, float]:
    eligible_hours = [
        hour
        for hour in sorted(hourly_load_forecast)
        if hour in correction_hours
    ]
    if not eligible_hours:
        return {}
    bounded_total = max(0.0, total_multiplier)
    if bounded_total <= 1.0 or not hourly_temperatures:
        return {hour: bounded_total for hour in eligible_hours}
    weather = hourly_weather or {}
    weights: dict[int, float] = {}
    for hour in eligible_hours:
        row = weather.get(hour, {})
        temperature = float(hourly_temperatures.get(hour, row.get("temp_c", 24.0)))
        humidity = float(row.get("relative_humidity_percent", 60.0))
        enthalpy = float(row.get("enthalpy_kj_kg", _moist_air_enthalpy(temperature, humidity)))
        thermal_state = max(float(row.get("thermal_24h", 0.0)), float(row.get("thermal_72h", 0.0)))
        latent_state = float(row.get("latent_72h", 0.0))
        weights[hour] = (
            1.0
            + max(0.0, temperature - 24.0)
            + 0.12 * max(0.0, enthalpy - 55.0)
            + 0.35 * thermal_state
            + 0.20 * latent_state
        )
    total_load = sum(max(0.0, hourly_load_forecast.get(hour, 0.0)) for hour in eligible_hours)
    if total_load <= 0.0:
        return {hour: bounded_total for hour in eligible_hours}
    weighted_mean = sum(
        max(0.0, hourly_load_forecast.get(hour, 0.0)) * weights[hour]
        for hour in eligible_hours
    ) / total_load
    if weighted_mean <= 0.0:
        return {hour: bounded_total for hour in eligible_hours}
    return {
        hour: 1.0 + (bounded_total - 1.0) * weights[hour] / weighted_mean
        for hour in eligible_hours
    }


def _recent_and_analog_hourly_floor(
    *,
    actual_history: dict[str, dict[int, dict[str, float]]],
    historical_temperature_features: dict[str, dict[str, float | None]],
    target_features: dict[str, float | None],
    target_pv_kwh: float,
    target_hourly_pv_kwh: dict[int, float] | None = None,
) -> dict[str, object]:
    """Build one 24-hour q75/similar-day blend floor without day/night branches."""

    enabled = env_bool("LOAD_RECENT_ANALOG_FLOOR_ENABLED", default=True)
    window_days = max(1, int(env_float("LOAD_RECENT_ANALOG_WINDOW_DAYS", default=14.0)))
    min_days = max(1, int(env_float("LOAD_RECENT_ANALOG_MIN_DAYS", default=3.0)))
    quantile_probability = env_float_clamped("LOAD_RECENT_ANALOG_QUANTILE", 0.75, min_val=0.0, max_val=1.0)
    analog_safety_factor = max(1.0, env_float("LOAD_ANALOG_SAFETY_FACTOR", default=1.20))
    similarity_threshold = env_float_clamped("LOAD_ANALOG_MIN_SIMILARITY", 0.50, min_val=0.0, max_val=1.0)
    analog_neighbor_count = max(1, int(env_float("LOAD_ANALOG_NEIGHBOR_COUNT", default=5.0)))
    pv_profile_distance_scale = max(
        0.1,
        env_float("LOAD_ANALOG_PV_PROFILE_DISTANCE_SCALE_KWH", default=2.5),
    )
    days = sorted(day for day, hourly in actual_history.items() if any(values.get("load", 0.0) > 0 for values in hourly.values()))
    if not enabled:
        return {"enabled": False, "applied": False, "reason": "disabled", "hourly_floor_kwh": {}}
    if len(days) < min_days:
        return {
            "enabled": True,
            "applied": False,
            "reason": "insufficient_history",
            "sample_count": len(days),
            "min_days": min_days,
            "hourly_floor_kwh": {},
        }

    target_cdh28 = float(target_features.get("cooling_degree_hours_28") or 0.0)
    target_enthalpy = float(target_features.get("mean_enthalpy_kj_kg") or 50.0)
    target_thermal72 = float(target_features.get("thermal_72h_end") or 0.0)
    target_latent72 = float(target_features.get("latent_72h_end") or 0.0)
    target_pv_profile = target_hourly_pv_kwh or {}
    analog_candidates: list[tuple[float, str, float]] = []
    for day in days:
        features = historical_temperature_features.get(day)
        if features is None:
            continue
        historical_pv = sum(max(0.0, values.get("pv", 0.0)) for values in actual_history[day].values())
        pv_profile_distance = 0.0
        if target_pv_profile:
            pv_profile_distance = math.sqrt(
                sum(
                    pow(
                        max(0.0, target_pv_profile.get(hour, 0.0))
                        - max(0.0, actual_history[day].get(hour, {}).get("pv", 0.0)),
                        2.0,
                    )
                    for hour in range(7, 23)
                )
                / 16.0
            )
        distance = math.sqrt(
            pow((target_cdh28 - float(features.get("cooling_degree_hours_28") or 0.0)) / 10.0, 2.0)
            + pow((target_enthalpy - float(features.get("mean_enthalpy_kj_kg") or 50.0)) / 8.0, 2.0)
            + pow((target_thermal72 - float(features.get("thermal_72h_end") or 0.0)) / 4.0, 2.0)
            + pow((target_latent72 - float(features.get("latent_72h_end") or 0.0)) / 4.0, 2.0)
            + pow((max(0.0, target_pv_kwh) - historical_pv) / 5.0, 2.0)
            + pow(pv_profile_distance / pv_profile_distance_scale, 2.0)
        )
        analog_candidates.append((distance, day, math.exp(-distance)))
    analog_candidates.sort(key=lambda item: item[0])
    analog_day = analog_candidates[0][1] if analog_candidates else None
    analog_similarity = analog_candidates[0][2] if analog_candidates else 0.0
    analog_allowed = analog_day is not None and analog_similarity >= similarity_threshold
    analog_features = historical_temperature_features.get(analog_day, {}) if analog_day else {}
    similar_days = [
        (day, similarity)
        for _, day, similarity in analog_candidates[:analog_neighbor_count]
        if similarity >= similarity_threshold
    ]

    recent_days = days[-window_days:]
    hourly_floors: dict[str, float] = {}
    hourly_details: dict[str, dict[str, float | str | bool | None]] = {}
    for hour in range(24):
        recent_values = [
            max(0.0, actual_history[day].get(hour, {}).get("load", 0.0))
            for day in recent_days
            if hour in actual_history[day]
        ]
        recent_values = [value for value in recent_values if value > 0.0]
        if not recent_values:
            continue
        q75 = _quantile(recent_values, quantile_probability)
        neighbor_values = [
            (
                max(0.0, actual_history[day][hour].get("load", 0.0)),
                similarity,
            )
            for day, similarity in similar_days
            if hour in actual_history.get(day, {})
            and actual_history[day][hour].get("load", 0.0) > 0.0
        ]
        neighbor_weight = sum(similarity for _, similarity in neighbor_values)
        analog_actual = (
            sum(value * similarity for value, similarity in neighbor_values) / neighbor_weight
            if neighbor_weight > 0.0 else 0.0
        )
        analog_floor = analog_actual * analog_safety_factor if analog_actual > 0.0 else 0.0
        floor = max(q75, analog_floor)
        hourly_floors[str(hour)] = round(floor, 4)
        hourly_details[str(hour)] = {
            "quantile_kwh": round(q75, 4),
            "analog_floor_kwh": round(analog_floor, 4),
            "analog_blended_actual_kwh": round(analog_actual, 4),
            "analog_neighbor_count": len(neighbor_values),
            "source": "analog_blend" if analog_floor > q75 else "q75",
        }
    return {
        "enabled": True,
        "applied": bool(hourly_floors),
        "reason": "ok",
        "method": "unified_24h_q75_with_similarity_weighted_analog_blend_1p20",
        "sample_count": len(days),
        "window_days": window_days,
        "quantile": quantile_probability,
        "analog_day": analog_day,
        "analog_similarity": round(analog_similarity, 6),
        "analog_features": analog_features,
        "analog_min_similarity": similarity_threshold,
        "analog_safety_factor": analog_safety_factor,
        "analog_allowed": analog_allowed,
        "analog_neighbor_limit": analog_neighbor_count,
        "pv_profile_distance_scale_kwh": pv_profile_distance_scale,
        "analog_vector_features": [
            "temperature_humidity_thermal_state",
            "daily_pv_kwh",
            "hourly_pv_profile_07_22",
        ],
        "analog_days": [
            {"date": day, "similarity": round(similarity, 6)}
            for day, similarity in similar_days
        ],
        "hourly_floor_kwh": hourly_floors,
        "hourly_details": hourly_details,
    }


def build_forecast_correction(
    correction_input: ForecastCorrectionInput,
    policy: ForecastCorrectionPolicy,
) -> dict[str, object]:
    rows = correction_input.rows
    hourly_load_forecast = correction_input.hourly_load_forecast
    hourly_pv_forecast = correction_input.hourly_pv_forecast
    target_date = correction_input.target_date
    forecast = correction_input.forecast
    skip_pv_correction = policy.skip_pv_correction
    allow_load_safety_floor = policy.allow_load_safety_floor
    if not policy.enabled:
        return {
            "enabled": False,
            "hourly_load_kwh": hourly_load_forecast,
            "hourly_pv_kwh": hourly_pv_forecast,
            "rationale": {"enabled": False, "reason": "disabled"},
        }

    forecast_history, history_source = _load_forecast_hourly_history(target_date=target_date)
    actual_history = _actual_hourly_totals_by_day(rows, target_date=target_date)
    pv_alpha = policy.pv_ewma_alpha
    pv_min = policy.pv_ratio_min
    pv_max = policy.pv_ratio_max
    load_alpha = policy.load_ewma_alpha
    load_min = policy.load_ratio_min
    load_max = policy.load_ratio_max

    pv_summary = _ewma_ratio_from_daily_pairs(
        _daily_pairs_for_ratio(forecast_history=forecast_history, actual_history=actual_history, key="pv"),
        alpha=pv_alpha,
    )
    load_summary = _ewma_ratio_from_daily_pairs(
        _daily_pairs_for_ratio(forecast_history=forecast_history, actual_history=actual_history, key="load"),
        alpha=load_alpha,
    )
    pv_ratio_raw = to_float(pv_summary.get("raw_ratio")) or 1.0
    load_ratio_raw = to_float(load_summary.get("raw_ratio")) or 1.0
    pv_ratio = _clip_float(pv_ratio_raw, min_val=pv_min, max_val=pv_max)
    load_ratio = _clip_float(load_ratio_raw, min_val=load_min, max_val=load_max)

    history_dates = sorted(set(forecast_history) & set(actual_history))
    historical_temperature_features: dict[str, dict[str, float | None]] = {}
    all_weather: dict[str, dict[int, dict[str, float]]] = {}
    if history_dates:
        all_weather = _fetch_hourly_weather(
            lat=correction_input.latitude,
            lon=correction_input.longitude,
            timezone=correction_input.timezone,
            start_date=history_dates[0],
            end_date=history_dates[-1],
            archive=True,
        )
    target_weather: dict[int, dict[str, float]] = {}
    raw_hourly_weather = forecast.get("hourly_weather")
    if isinstance(raw_hourly_weather, list):
        for item in raw_hourly_weather:
            if not isinstance(item, dict):
                continue
            hour = to_int(item.get("hour"))
            temp = to_float(item.get("temp_c"))
            if hour is None or not 0 <= hour <= 23 or temp is None:
                continue
            target_weather[hour] = {
                "temp_c": temp,
                "relative_humidity_percent": to_float(item.get("relative_humidity_percent")) or 60.0,
                "dew_point_c": to_float(item.get("dew_point_c")) or 16.0,
            }
    if not target_weather:
        target_weather = _fetch_hourly_weather(
            lat=correction_input.latitude,
            lon=correction_input.longitude,
            timezone=correction_input.timezone,
            start_date=target_date,
            end_date=target_date,
            archive=False,
        ).get(target_date, {})
    if not target_weather:
        fallback_temp = to_float(forecast.get("temp_c"))
        if fallback_temp is not None:
            target_weather = {
                hour: {"temp_c": fallback_temp, "relative_humidity_percent": 60.0, "dew_point_c": 16.0}
                for hour in range(24)
            }
    all_weather[target_date] = target_weather
    _add_thermal_states(all_weather)
    historical_temperature_features = {
        day: _temperature_features_for_day(day, hourly)
        for day, hourly in all_weather.items()
        if day < target_date
    }
    target_weather = all_weather.get(target_date, {})
    target_temps = {hour: float(item.get("temp_c", 24.0)) for hour, item in target_weather.items()}
    target_features = _temperature_features_for_day(target_date, target_weather)
    temperature_correction = _evening_temperature_correction(
        forecast_history=forecast_history,
        actual_history=actual_history,
        historical_temperature_features=historical_temperature_features,
        target_features=target_features,
        load_ratio=load_ratio,
    )
    raw_temperature_multiplier = temperature_correction.get("multiplier")
    temperature_multiplier = (
        float(raw_temperature_multiplier)
        if isinstance(raw_temperature_multiplier, (int, float))
        else 1.0 + (to_float(temperature_correction.get("multiplier_delta")) or 0.0)
    )
    load_scenarios = temperature_correction.get("load_scenarios")
    if not isinstance(load_scenarios, list):
        load_scenarios = _adaptive_load_scenarios([], confidence=0.0)

    pv_multiplier = 1.0 if skip_pv_correction else pv_ratio
    corrected_pv = {
        hour: max(0.0, value) * pv_multiplier
        for hour, value in hourly_pv_forecast.items()
    }
    corrected_load: dict[int, float] = {}
    correction_hours = set(_temperature_correction_hours())
    temperature_hourly_multipliers = _temperature_hourly_multipliers(
        hourly_load_forecast=hourly_load_forecast,
        hourly_temperatures=target_temps,
        hourly_weather=target_weather,
        correction_hours=correction_hours,
        total_multiplier=temperature_multiplier,
    )
    temperature_correction["hourly_multipliers"] = {
        str(hour): round(multiplier, 6)
        for hour, multiplier in temperature_hourly_multipliers.items()
    }
    temperature_correction["hourly_shape_method"] = (
        "load_weighted_cooling_degree_distribution_preserving_total"
        if temperature_multiplier > 1.0 and target_temps
        else "uniform_temperature_multiplier"
    )
    for hour, value in hourly_load_forecast.items():
        multiplier = load_ratio * temperature_hourly_multipliers.get(hour, 1.0)
        corrected_load[hour] = max(0.0, value) * max(0.0, multiplier)

    load_safety_floor = _recent_and_analog_hourly_floor(
        actual_history=actual_history,
        historical_temperature_features=historical_temperature_features,
        target_features=target_features,
        target_pv_kwh=sum(max(0.0, value) for hour, value in hourly_pv_forecast.items() if 7 <= hour < 23),
        target_hourly_pv_kwh=hourly_pv_forecast,
    )
    hourly_floor_raw = load_safety_floor.get("hourly_floor_kwh")
    hourly_floor = _coerce_hourly_values(hourly_floor_raw)
    applied_hours: list[int] = []
    if allow_load_safety_floor:
        for hour, floor in hourly_floor.items():
            if floor > corrected_load.get(hour, 0.0) + 1e-9:
                corrected_load[hour] = floor
                applied_hours.append(hour)
    safety_floor_applied = bool(applied_hours)
    load_safety_floor["allowed_by_occupancy"] = allow_load_safety_floor
    load_safety_floor["applied"] = safety_floor_applied
    load_safety_floor["applied_hours"] = applied_hours
    load_safety_floor["forecast_after_floor_kwh"] = round(
        sum(max(0.0, corrected_load.get(hour, 0.0)) for hour in range(24)),
        4,
    )
    peak_penalty = {"enabled": False, "applied_factor": 0.0, "reason": "removed"}
    return {
        "enabled": True,
        "hourly_load_kwh": corrected_load,
        "hourly_pv_kwh": corrected_pv,
        "load_scenarios": load_scenarios,
        "peak_penalty": peak_penalty,
        "rationale": {
            "enabled": True,
            "method": "unified_24h_temperature_humidity_thermal_history_q75",
            "history_source": history_source,
            "history_days": history_dates[-14:],
            "pv_ratio_ewma_raw": round(pv_ratio_raw, 6),
            "pv_ratio_ewma_applied": round(pv_multiplier, 6),
            "pv_ratio_ewma_skipped": bool(skip_pv_correction),
            "pv_ratio_floor": pv_min,
            "pv_ratio_cap": pv_max,
            "pv_ewma_alpha": pv_alpha,
            "pv_sample_count": pv_summary["sample_count"],
            "pv_latest_days": pv_summary["latest_days"],
            "load_ratio_ewma_raw": round(load_ratio_raw, 6),
            "load_ratio_ewma_applied": round(load_ratio, 6),
            "load_ratio_floor": load_min,
            "load_ratio_cap": load_max,
            "load_ewma_alpha": load_alpha,
            "load_sample_count": load_summary["sample_count"],
            "load_latest_days": load_summary["latest_days"],
            "evening_load_temperature": temperature_correction,
            "recent_and_analog_hourly_floor": load_safety_floor,
            "load_scenarios": load_scenarios,
            "soc_peak_unmet_penalty": peak_penalty,
            "raw_hourly_load_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(hourly_load_forecast.items())},
            "raw_hourly_pv_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(hourly_pv_forecast.items())},
            "corrected_hourly_load_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(corrected_load.items())},
            "corrected_hourly_pv_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(corrected_pv.items())},
        },
    }


def _build_forecast_correction(
    *,
    rows: list[dict[str, Any]],
    hourly_load_forecast: dict[int, float],
    hourly_pv_forecast: dict[int, float],
    target_date: str,
    lat: float,
    lon: float,
    timezone: str,
    forecast: dict[str, object],
    skip_pv_correction: bool = False,
    allow_load_safety_floor: bool = True,
) -> dict[str, object]:
    """Backward-compatible adapter for callers that still pass individual values."""
    return build_forecast_correction(
        ForecastCorrectionInput(
            rows=rows,
            hourly_load_forecast=hourly_load_forecast,
            hourly_pv_forecast=hourly_pv_forecast,
            target_date=target_date,
            latitude=lat,
            longitude=lon,
            timezone=timezone,
            forecast=forecast,
        ),
        ForecastCorrectionPolicy.from_env(
            skip_pv_correction=skip_pv_correction,
            allow_load_safety_floor=allow_load_safety_floor,
        ),
    )
