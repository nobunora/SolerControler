from __future__ import annotations

import math
import os
from datetime import datetime
from typing import Any, Callable

from app.forecasting.comfort_load import predict_hourly_comfort_load
from app.forecasting.correction_calculations import clip_float as _clip_float, weather_class as _weather_class
from app.forecasting.correction_history_io import _moist_air_enthalpy, fetch_hourly_weather
from app.configuration.environment import env_bool, env_float
from app.parsing.numbers import to_float, to_int
def _physical_vector_residual_correction(
    *, forecast_history: dict[str, dict[int, dict[str, float]]], actual_history: dict[str, dict[int, dict[str, float]]], forecast: dict[str, object], hourly_pv: dict[int, float]
) -> tuple[dict[int, float], dict[str, object]]:
    hourly_weather = forecast.get("hourly_weather")
    if not isinstance(hourly_weather, list) or not env_bool("PHYSICAL_PV_VECTOR_RESIDUAL_ENABLED", default=True):
        return hourly_pv, {"enabled": False, "reason": "disabled_or_weather_missing"}
    target = {to_int(item.get("hour")): item for item in hourly_weather if isinstance(item, dict) and to_int(item.get("hour")) is not None}
    spread = max(0.01, env_float("PHYSICAL_PV_VECTOR_RESIDUAL_SPREAD_KWH", default=0.6))
    corrected, applied = dict(hourly_pv), []
    for hour, value in hourly_pv.items():
        weather = target.get(hour, {})
        shortwave = to_float(weather.get("shortwave_radiation_w_m2")) or 0.0
        cls = _weather_class(weather.get("weather_code"))
        residuals = []
        for day, history in forecast_history.items():
            prior = history.get(hour, {})
            actual = actual_history.get(day, {}).get(hour, {})
            prior_pv, prior_sw = to_float(prior.get("pv")) or 0.0, to_float(prior.get("shortwave")) or 0.0
            if prior_pv <= 0 or prior_sw <= 0 or shortwave <= 0 or _weather_class(prior.get("weather_code")) != cls:
                continue
            if 0.7 * shortwave <= prior_sw <= 1.3 * shortwave:
                residuals.append((to_float(actual.get("pv")) or 0.0) - prior_pv)
        if not residuals:
            continue
        center = sorted(residuals)[len(residuals) // 2]
        variance = (spread ** 2 if len(residuals) == 1 else sum((item - center) ** 2 for item in residuals) / len(residuals))
        weight = (len(residuals) / (len(residuals) + 2.0)) * (spread ** 2 / (spread ** 2 + variance))
        corrected[hour] = max(0.0, value + weight * center)
        applied.append({"hour": hour, "count": len(residuals), "weight": round(weight, 4), "residual_kwh": round(center, 4)})
    return corrected, {"enabled": True, "spread_kwh": spread, "applied": applied}


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


def _paired_forecast_error_scenarios(
    *,
    forecast_history: dict[str, dict[int, dict[str, float]]],
    actual_history: dict[str, dict[int, dict[str, float]]],
    current_pv_correction: float,
    current_load_correction: float,
    max_days: int = 14,
) -> list[dict[str, float | str]]:
    """Keep realized PV/load errors paired, with a prior for sparse history."""

    pairs: list[tuple[str, float, float]] = []
    for day in sorted(set(forecast_history) & set(actual_history))[-max(1, max_days):]:
        forecast_pv = sum(max(0.0, item.get("pv", 0.0)) for item in forecast_history[day].values())
        forecast_load = sum(max(0.0, item.get("load", 0.0)) for item in forecast_history[day].values())
        actual_pv = sum(max(0.0, item.get("pv", 0.0)) for item in actual_history[day].values())
        actual_load = sum(max(0.0, item.get("load", 0.0)) for item in actual_history[day].values())
        if forecast_pv <= 0.0 or forecast_load <= 0.0:
            continue
        pairs.append((day, actual_pv / forecast_pv, actual_load / forecast_load))
    if len(pairs) < 2:
        return []

    confidence = len(pairs) / (len(pairs) + 5.0)
    base_probability = 1.0 - confidence
    pair_probability = confidence / len(pairs)
    scenarios: list[dict[str, float | str]] = [
        {
            "label": "paired_prior",
            "probability": base_probability,
            "pv_multiplier": 1.0,
            "load_multiplier": 1.0,
        }
    ]
    pv_baseline = max(0.01, current_pv_correction)
    load_baseline = max(0.01, current_load_correction)
    for day, pv_ratio, load_ratio in pairs:
        relative_pv = max(0.01, pv_ratio / pv_baseline)
        relative_load = max(0.01, load_ratio / load_baseline)
        scenarios.append(
            {
                "label": f"paired_{day}",
                "probability": pair_probability,
                "pv_multiplier": _clip_float(
                    _bounded_exp(confidence * math.log(relative_pv)),
                    min_val=0.25,
                    max_val=3.0,
                ),
                "load_multiplier": _clip_float(
                    _bounded_exp(confidence * math.log(relative_load)),
                    min_val=0.50,
                    max_val=2.0,
                ),
            }
        )
    return scenarios


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


def _temperature_training_samples(
    *,
    forecast_history: dict[str, dict[int, dict[str, float]]],
    actual_history: dict[str, dict[int, dict[str, float]]],
    historical_temperature_features: dict[str, dict[str, float | None]],
    correction_hours: range,
    load_ratio: float,
) -> tuple[list[list[float]], list[float], list[dict[str, float | None]], list[str]]:
    """Build valid historical load-residual samples for temperature fitting."""
    feature_rows: list[list[float]] = []
    residual_targets: list[float] = []
    feature_objects: list[dict[str, float | None]] = []
    training_days: list[str] = []
    for day in sorted(set(forecast_history) & set(actual_history) & set(historical_temperature_features)):
        forecast_load = sum(
            max(0.0, forecast_history[day].get(hour, {}).get("load", 0.0)) * max(0.0, load_ratio)
            for hour in correction_hours
        )
        actual_load = sum(max(0.0, actual_history[day].get(hour, {}).get("load", 0.0)) for hour in correction_hours)
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
    return feature_rows, residual_targets, feature_objects, training_days


# readable-code-audit: skip STRUCT-04 — fitted correction, confidence gate, and diagnostic payload must share the same training sample snapshot
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

    correction_hours = _temperature_correction_hours()
    feature_rows, residual_targets, feature_objects, training_days = _temperature_training_samples(
        forecast_history=forecast_history,
        actual_history=actual_history,
        historical_temperature_features=historical_temperature_features,
        correction_hours=correction_hours,
        load_ratio=load_ratio,
    )

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


def _target_weather_from_forecast(
    forecast: dict[str, object],
    *,
    target_date: str,
    latitude: float,
    longitude: float,
    timezone: str,
    weather_fetch: Callable[..., dict[str, dict[int, dict[str, float]]]] = fetch_hourly_weather,
) -> dict[int, dict[str, float]]:
    """Read target weather from the forecast, then use provider/fallback data when absent."""
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
                "wind_speed_10m": to_float(item.get("wind_speed_10m")) or 0.0,
            }
    if not target_weather:
        target_weather = weather_fetch(
            lat=latitude, lon=longitude, timezone=timezone,
            start_date=target_date, end_date=target_date, archive=False,
        ).get(target_date, {})
    if not target_weather:
        fallback_temp = to_float(forecast.get("temp_c"))
        if fallback_temp is not None:
            target_weather = {
                hour: {"temp_c": fallback_temp, "relative_humidity_percent": 60.0,
                       "dew_point_c": 16.0, "wind_speed_10m": 0.0}
                for hour in range(24)
            }
    return target_weather


def _correct_hourly_pv(
    *,
    hourly_pv_forecast: dict[int, float],
    pv_ratio: float,
    skip_pv_correction: bool,
    forecast_history: dict[str, dict[int, dict[str, float]]],
    actual_history: dict[str, dict[int, dict[str, float]]],
    forecast: dict[str, object],
) -> tuple[dict[int, float], dict[str, object], float]:
    """Apply the ratio correction or physical-model residual correction to hourly PV."""
    pv_multiplier = 1.0 if skip_pv_correction else pv_ratio
    corrected_pv = {
        hour: max(0.0, value) * pv_multiplier
        for hour, value in hourly_pv_forecast.items()
    }
    vector_residual: dict[str, object] = {"enabled": False, "reason": "not_physical"}
    if skip_pv_correction:
        corrected_pv, vector_residual = _physical_vector_residual_correction(
            forecast_history=forecast_history,
            actual_history=actual_history,
            forecast=forecast,
            hourly_pv=corrected_pv,
        )
    return corrected_pv, vector_residual, pv_multiplier


# readable-code-audit: skip STRUCT-04 — the correction result must include all diagnostics from the same source snapshot, so calculation and provenance stay together
