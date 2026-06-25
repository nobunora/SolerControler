from __future__ import annotations

"""Forecast correction layer for the night-charge planner.

This module keeps the tunable, data-driven forecast adjustments out of
energy_model_main.py. The stable contract is simple: take raw hourly PV/load
forecasts and return corrected hourly forecasts plus a human-readable rationale
that can be persisted for later validation.
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path

import requests

from app.utils import env_bool, env_float, env_float_clamped, to_float, to_int


def _clip_float(value: float, *, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, value))


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
    rows: list[dict[str, float | datetime]],
    *,
    target_date: str,
) -> dict[str, dict[int, dict[str, float]]]:
    by_day: dict[str, dict[int, dict[str, float]]] = {}
    for row in rows:
        dt = row.get("dt")
        if not isinstance(dt, datetime):
            continue
        day = dt.date().isoformat()
        if day >= target_date or dt.hour < 7 or dt.hour >= 23:
            continue
        bucket = by_day.setdefault(day, {}).setdefault(dt.hour, {"pv": 0.0, "load": 0.0})
        bucket["pv"] += max(0.0, float(row.get("pv", 0.0) or 0.0))
        bucket["load"] += max(0.0, float(row.get("load", 0.0) or 0.0))
    return by_day


def _load_forecast_hourly_history_from_sqlite(*, target_date: str) -> dict[str, dict[int, dict[str, float]]]:
    db_path = Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db"))
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT date, hour, forecast_pv_kwh, forecast_load_kwh, forecast_shortwave_radiation_w_m2
                FROM forecast_hourly
                WHERE date < ?
                ORDER BY date, hour
                """,
                (target_date,),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return {}

    out: dict[str, dict[int, dict[str, float]]] = {}
    for row in rows:
        hour = to_int(row["hour"])
        if hour is None or hour < 7 or hour >= 23:
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
    try:
        from google.cloud import firestore

        project_id = os.getenv("FIRESTORE_PROJECT_ID", "").strip() or None
        database_id = os.getenv("FIRESTORE_DATABASE_ID", "(default)").strip() or "(default)"
        client = (
            firestore.Client(project=project_id, database=database_id)
            if project_id else firestore.Client(database=database_id)
        )
        docs = list(client.collection("forecast_hourly").where("date", "<", target_date).stream())
    except Exception:
        return {}

    out: dict[str, dict[int, dict[str, float]]] = {}
    for doc in docs:
        row = doc.to_dict() or {}
        day = str(row.get("date", "")).strip()
        hour = to_int(row.get("hour"))
        if not day or hour is None or hour < 7 or hour >= 23:
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


def _fetch_hourly_temperatures(
    *,
    lat: float,
    lon: float,
    timezone: str,
    start_date: str,
    end_date: str,
    archive: bool,
) -> dict[str, dict[int, float]]:
    url = "https://archive-api.open-meteo.com/v1/archive" if archive else "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": "temperature_2m",
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
    out: dict[str, dict[int, float]] = {}
    for raw_time, raw_temp in zip(times if isinstance(times, list) else [], temps if isinstance(temps, list) else []):
        try:
            dt = datetime.fromisoformat(str(raw_time))
            temp_c = float(raw_temp)
        except Exception:
            continue
        out.setdefault(dt.date().isoformat(), {})[dt.hour] = temp_c
    return out


def _temperature_features_for_day(day: str, hourly_temps: dict[int, float]) -> dict[str, float | None]:
    if not hourly_temps:
        return {
            "cooling_degree_hours_28": None,
            "temp_ewma_12h_evening": None,
            "night_min_temp_c": None,
        }
    cdh28 = sum(max(0.0, float(hourly_temps.get(hour, 0.0)) - 28.0) for hour in range(0, 23))
    night_values = [float(hourly_temps[hour]) for hour in range(0, 7) if hour in hourly_temps]
    alpha = 1.0 - pow(2.718281828459045, -1.0 / 12.0)
    ewma: float | None = None
    ewma_by_hour: dict[int, float] = {}
    for hour in range(0, 23):
        if hour not in hourly_temps:
            continue
        value = float(hourly_temps[hour])
        ewma = value if ewma is None else alpha * value + (1.0 - alpha) * ewma
        ewma_by_hour[hour] = ewma
    evening_values = [ewma_by_hour[h] for h in range(17, 23) if h in ewma_by_hour]
    return {
        "cooling_degree_hours_28": cdh28,
        "temp_ewma_12h_evening": (sum(evening_values) / len(evening_values)) if evening_values else None,
        "night_min_temp_c": min(night_values) if night_values else None,
    }


def _temperature_feature_vector(features: dict[str, float | None]) -> list[float]:
    cdh28 = float(features.get("cooling_degree_hours_28") or 0.0)
    ewma_evening = float(features.get("temp_ewma_12h_evening") or 24.0)
    night_min = float(features.get("night_min_temp_c") or 20.0)
    return [1.0, cdh28 / 10.0, (ewma_evening - 24.0) / 5.0, (night_min - 20.0) / 5.0]


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
    lower = env_float("EVENING_LOAD_TEMPERATURE_MIN_MULTIPLIER_DELTA", default=-0.10)
    upper = env_float("EVENING_LOAD_TEMPERATURE_MAX_MULTIPLIER_DELTA", default=0.45)
    regularization = max(0.0, env_float("EVENING_LOAD_TEMPERATURE_RIDGE_LAMBDA", default=1.0))
    if not enabled:
        return {"enabled": False, "applied": False, "multiplier_delta": 0.0, "reason": "disabled"}

    feature_rows: list[list[float]] = []
    residuals: list[float] = []
    training_days: list[str] = []
    for day in sorted(set(forecast_history) & set(actual_history) & set(historical_temperature_features)):
        forecast_evening = sum(
            max(0.0, forecast_history[day].get(hour, {}).get("load", 0.0)) * max(0.0, load_ratio)
            for hour in range(17, 23)
        )
        actual_evening = sum(
            max(0.0, actual_history[day].get(hour, {}).get("load", 0.0))
            for hour in range(17, 23)
        )
        if forecast_evening <= 0:
            continue
        feature_rows.append(_temperature_feature_vector(historical_temperature_features[day]))
        residuals.append(_clip_float(actual_evening / forecast_evening - 1.0, min_val=-0.50, max_val=1.0))
        training_days.append(day)

    if len(feature_rows) < min_samples:
        return {
            "enabled": True,
            "applied": False,
            "multiplier_delta": 0.0,
            "reason": "insufficient_history",
            "sample_count": len(feature_rows),
            "min_samples": min_samples,
        }

    coefficients = _solve_ridge_regression(feature_rows, residuals, regularization=regularization)
    if not coefficients:
        return {
            "enabled": True,
            "applied": False,
            "multiplier_delta": 0.0,
            "reason": "fit_failed",
            "sample_count": len(feature_rows),
        }
    raw_delta = sum(value * weight for value, weight in zip(_temperature_feature_vector(target_features), coefficients))
    delta = _clip_float(raw_delta, min_val=lower, max_val=upper)
    return {
        "enabled": True,
        "applied": True,
        "method": "ridge_evening_load_residual",
        "sample_count": len(feature_rows),
        "training_days": training_days[-7:],
        "coefficients": [round(x, 6) for x in coefficients],
        "raw_multiplier_delta": round(raw_delta, 6),
        "multiplier_delta": round(delta, 6),
        "bounds": [lower, upper],
        "target_features": target_features,
    }


def _risk_adjusted_peak_penalty(
    *,
    target_features: dict[str, float | None],
    pv_ratio_raw: float,
    pv_ratio_applied: float,
) -> dict[str, object]:
    enabled = env_bool("SOC_PEAK_UNMET_PENALTY_ENABLED", default=True)
    base_factor = max(0.0, env_float("SOC_PEAK_UNMET_BASE_FACTOR", default=1.0))
    risk_factor = max(base_factor, env_float("SOC_PEAK_UNMET_RISK_FACTOR", default=2.0))
    max_factor = max(base_factor, env_float("SOC_PEAK_UNMET_MAX_FACTOR", default=risk_factor))
    target_peak_soc = env_float_clamped("SOC_PEAK_UNMET_TARGET_SOC_PERCENT", 95.0, min_val=0.0, max_val=100.0)
    cdh_threshold = env_float("SOC_HIGH_TEMP_CDH28_THRESHOLD", default=10.0)
    ewma_threshold = env_float("SOC_HIGH_TEMP_EWMA12_EVENING_THRESHOLD", default=26.0)
    night_min_threshold = env_float("SOC_HIGH_TEMP_NIGHT_MIN_THRESHOLD", default=20.0)
    pv_epsilon = max(0.0, env_float("SOC_PV_OVERRATIO_CAP_EPSILON", default=1e-6))
    cdh28 = to_float(target_features.get("cooling_degree_hours_28")) or 0.0
    ewma_evening = to_float(target_features.get("temp_ewma_12h_evening")) or 0.0
    night_min = to_float(target_features.get("night_min_temp_c")) or 0.0
    high_temperature = cdh28 >= cdh_threshold or ewma_evening >= ewma_threshold or night_min >= night_min_threshold
    pv_overconfidence = pv_ratio_raw > pv_ratio_applied + pv_epsilon
    risk_reasons: list[str] = []
    if high_temperature:
        risk_reasons.append("high_temperature")
    if pv_overconfidence:
        risk_reasons.append("pv_overconfidence")
    applied_factor = base_factor
    if high_temperature and pv_overconfidence:
        applied_factor = min(risk_factor, max_factor)
    if not enabled:
        applied_factor = 0.0
    return {
        "enabled": enabled,
        "target_peak_soc_percent": target_peak_soc,
        "base_factor": base_factor,
        "risk_factor": risk_factor,
        "max_factor": max_factor,
        "applied_factor": applied_factor,
        "risk_reasons": risk_reasons,
        "high_temperature": high_temperature,
        "pv_overconfidence": pv_overconfidence,
        "thresholds": {
            "cooling_degree_hours_28": cdh_threshold,
            "temp_ewma_12h_evening": ewma_threshold,
            "night_min_temp_c": night_min_threshold,
        },
    }


def _build_forecast_correction(
    *,
    rows: list[dict[str, float | datetime]],
    hourly_load_forecast: dict[int, float],
    hourly_pv_forecast: dict[int, float],
    target_date: str,
    lat: float,
    lon: float,
    timezone: str,
    forecast: dict[str, object],
    skip_pv_correction: bool = False,
) -> dict[str, object]:
    enabled = env_bool("FORECAST_CORRECTION_ENABLED", default=True)
    if not enabled:
        return {
            "enabled": False,
            "hourly_load_kwh": hourly_load_forecast,
            "hourly_pv_kwh": hourly_pv_forecast,
            "rationale": {"enabled": False, "reason": "disabled"},
        }

    forecast_history, history_source = _load_forecast_hourly_history(target_date=target_date)
    actual_history = _actual_hourly_totals_by_day(rows, target_date=target_date)
    pv_alpha = env_float_clamped("PV_RATIO_EWMA_ALPHA", 0.2, min_val=0.0, max_val=1.0)
    pv_min = max(0.0, env_float("PV_RATIO_EWMA_MIN", default=0.9))
    pv_max = max(pv_min, env_float("PV_RATIO_EWMA_MAX", default=1.35))
    load_alpha = env_float_clamped("LOAD_RATIO_EWMA_ALPHA", 0.5, min_val=0.0, max_val=1.0)
    load_min = max(0.0, env_float("LOAD_RATIO_EWMA_MIN", default=0.7))
    load_max = max(load_min, env_float("LOAD_RATIO_EWMA_MAX", default=1.8))

    pv_summary = _ewma_ratio_from_daily_pairs(
        _daily_pairs_for_ratio(forecast_history=forecast_history, actual_history=actual_history, key="pv"),
        alpha=pv_alpha,
    )
    load_summary = _ewma_ratio_from_daily_pairs(
        _daily_pairs_for_ratio(forecast_history=forecast_history, actual_history=actual_history, key="load"),
        alpha=load_alpha,
    )
    pv_ratio_raw = float(pv_summary["raw_ratio"])
    load_ratio_raw = float(load_summary["raw_ratio"])
    pv_ratio = _clip_float(pv_ratio_raw, min_val=pv_min, max_val=pv_max)
    load_ratio = _clip_float(load_ratio_raw, min_val=load_min, max_val=load_max)

    history_dates = sorted(set(forecast_history) & set(actual_history))
    historical_temperature_features: dict[str, dict[str, float | None]] = {}
    if history_dates:
        historical_temps = _fetch_hourly_temperatures(
            lat=lat,
            lon=lon,
            timezone=timezone,
            start_date=history_dates[0],
            end_date=history_dates[-1],
            archive=True,
        )
        historical_temperature_features = {
            day: _temperature_features_for_day(day, temps)
            for day, temps in historical_temps.items()
        }

    target_temps = _fetch_hourly_temperatures(
        lat=lat,
        lon=lon,
        timezone=timezone,
        start_date=target_date,
        end_date=target_date,
        archive=False,
    ).get(target_date, {})
    if not target_temps:
        fallback_temp = to_float(forecast.get("temp_c"))
        if fallback_temp is not None:
            target_temps = {hour: fallback_temp for hour in range(0, 23)}
    target_features = _temperature_features_for_day(target_date, target_temps)
    temperature_correction = _evening_temperature_correction(
        forecast_history=forecast_history,
        actual_history=actual_history,
        historical_temperature_features=historical_temperature_features,
        target_features=target_features,
        load_ratio=load_ratio,
    )
    evening_delta = float(temperature_correction.get("multiplier_delta") or 0.0)

    pv_multiplier = 1.0 if skip_pv_correction else pv_ratio
    corrected_pv = {
        hour: max(0.0, value) * pv_multiplier
        for hour, value in hourly_pv_forecast.items()
    }
    corrected_load: dict[int, float] = {}
    for hour, value in hourly_load_forecast.items():
        multiplier = load_ratio * (1.0 + evening_delta if 17 <= hour < 23 else 1.0)
        corrected_load[hour] = max(0.0, value) * max(0.0, multiplier)

    peak_penalty = _risk_adjusted_peak_penalty(
        target_features=target_features,
        pv_ratio_raw=pv_ratio_raw,
        pv_ratio_applied=pv_ratio,
    )
    return {
        "enabled": True,
        "hourly_load_kwh": corrected_load,
        "hourly_pv_kwh": corrected_pv,
        "peak_penalty": peak_penalty,
        "rationale": {
            "enabled": True,
            "method": "pv_load_ewma_with_evening_temperature_state",
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
            "soc_peak_unmet_penalty": peak_penalty,
            "raw_hourly_load_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(hourly_load_forecast.items())},
            "raw_hourly_pv_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(hourly_pv_forecast.items())},
            "corrected_hourly_load_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(corrected_load.items())},
            "corrected_hourly_pv_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(corrected_pv.items())},
        },
    }
