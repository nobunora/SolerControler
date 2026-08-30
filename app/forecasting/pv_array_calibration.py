"""Typed input and policy boundary for PV performance calibration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable


@dataclass(frozen=True)
class PvCalibrationInput:
    arrays: list[Any]
    rows: list[dict[str, Any]]
    target_date: str
    latitude: float
    longitude: float
    timezone: str


@dataclass(frozen=True)
class PvCalibrationPolicy:
    lookback_days: int = 45
    min_days: int = 3
    min_factor: float = 0.2
    max_factor: float = 5.0

    @classmethod
    def from_env(cls) -> "PvCalibrationPolicy":
        return cls(
            lookback_days=int(os.getenv("PV_ARRAY_CALIBRATION_LOOKBACK_DAYS", "45")),
            min_days=int(os.getenv("PV_ARRAY_CALIBRATION_MIN_DAYS", "3")),
            min_factor=float(os.getenv("PV_ARRAY_CALIBRATION_MIN_FACTOR", "0.2")),
            max_factor=float(os.getenv("PV_ARRAY_CALIBRATION_MAX_FACTOR", "5.0")),
        )

import math
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import requests

from app.configuration.environment import env_bool
from app.domain.weather import open_meteo_weather_class
from app.forecasting.pv_array_adapters import http_get_with_retry as _http_get_with_retry, response_json_object as _response_json_object
from app.parsing.numbers import parse_csv_float, to_float, to_int

HttpGet = Callable[..., Any]
if TYPE_CHECKING:
    from app.forecasting.pv_array import PVArrayConfig


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)

def _daily_actual_pv(rows: list[dict[str, Any]], *, target_date: str, lookback_days: int) -> dict[str, float]:
    try:
        target = date.fromisoformat(target_date)
    except ValueError:
        return {}
    start = target - timedelta(days=lookback_days)
    out: dict[str, float] = defaultdict(float)
    for row in rows:
        dt = row.get("dt")
        if not isinstance(dt, datetime):
            continue
        day = dt.date()
        if day >= target or day < start:
            continue
        out[day.isoformat()] += max(0.0, parse_csv_float(row.get("pv"), default=0.0))
    return dict(out)


def _normalize_weather_class(value: str | None) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip().lower()
    if not text:
        return "unknown"
    if text in {"clear", "sunny"}:
        return "clear"
    if text in {"cloud", "cloudy", "overcast"}:
        return "cloudy"
    if text in {"rain", "rainy", "drizzle", "showers"}:
        return "rain"
    if text in {"storm", "thunder", "thunderstorm"}:
        return "storm"
    return text


def _weather_class_from_code(weather_code: int | None) -> str:
    return open_meteo_weather_class(weather_code)


def _ratio_distribution(values: list[float], *, source: str) -> dict[str, Any]:
    """Summarize forecast residual multipliers without hiding the sample count."""

    clean = [float(v) for v in values if math.isfinite(float(v)) and float(v) >= 0.0]
    if not clean:
        return {
            "source": source,
            "sample_count": 0,
            "mean_multiplier": 1.0,
            "std_multiplier": 0.30,
            "variance_multiplier": 0.09,
            "status": "fallback_no_samples",
        }
    arr = np.asarray(clean, dtype=float)
    mean = float(np.mean(arr))
    variance = float(np.var(arr, ddof=1)) if len(clean) >= 2 else 0.09
    std = float(math.sqrt(max(0.0, variance)))
    return {
        "source": source,
        "sample_count": len(clean),
        "mean_multiplier": _round(mean),
        "std_multiplier": _round(std),
        "variance_multiplier": _round(variance),
        "min_multiplier": _round(float(np.min(arr))),
        "p25_multiplier": _round(float(np.percentile(arr, 25))),
        "p50_multiplier": _round(float(np.percentile(arr, 50))),
        "p75_multiplier": _round(float(np.percentile(arr, 75))),
        "p90_multiplier": _round(float(np.percentile(arr, 90))),
        "max_multiplier": _round(float(np.max(arr))),
        "status": "ok",
    }


def _fetch_archive_weather_daily_by_day(
    *,
    lat: float,
    lon: float,
    timezone: str,
    start_date: str,
    end_date: str,
    http_get: HttpGet,
) -> dict[str, dict[str, float | str | None]]:
    resp = _http_get_with_retry(
        http_get,
        "https://archive-api.open-meteo.com/v1/archive",
        provider="Open-Meteo archive",
        params={
            "latitude": lat,
            "longitude": lon,
            "start_date": start_date,
            "end_date": end_date,
            "daily": "weather_code,sunshine_duration,precipitation_sum",
            "timezone": timezone,
        },
        timeout=30,
    )
    daily = _response_json_object(resp, provider="Open-Meteo archive").get("daily", {})
    if not isinstance(daily, dict):
        raise RuntimeError("Open-Meteo archive daily payload is not an object")
    times = daily.get("time", [])
    weather_codes = daily.get("weather_code", [])
    sunshine_values = daily.get("sunshine_duration", [])
    precipitation_values = daily.get("precipitation_sum", [])
    out: dict[str, dict[str, float | str | None]] = {}
    for idx, day in enumerate(times if isinstance(times, list) else []):
        weather_code = to_int(weather_codes[idx] if idx < len(weather_codes) else None)
        sunshine_hours = to_float(sunshine_values[idx] if idx < len(sunshine_values) else None)
        precipitation_sum = to_float(
            precipitation_values[idx] if idx < len(precipitation_values) else None
        )
        out[str(day)] = {
            "weather_class": _weather_class_from_code(weather_code),
            "sunshine_hours": (sunshine_hours / 3600.0) if sunshine_hours is not None else None,
            "precipitation_sum_mm": precipitation_sum,
        }
    return out


# readable-code-audit: skip STRUCT-04 — calibration produces one auditable result containing samples, factors, and regression diagnostics from one history window
def calibrate_performance_ratio_for(
    calibration_input: PvCalibrationInput,
    policy: PvCalibrationPolicy,
    *,
    http_get: HttpGet = requests.get,
) -> dict[str, Any]:
    from app.forecasting import pv_array

    fetch_open_meteo_hourly = pv_array.fetch_open_meteo_hourly
    _array_hourly_kwh = pv_array._array_hourly_kwh
    arrays = calibration_input.arrays
    rows = calibration_input.rows
    target_date = calibration_input.target_date
    lat = calibration_input.latitude
    lon = calibration_input.longitude
    timezone = calibration_input.timezone
    lookback_days = policy.lookback_days
    min_days = policy.min_days
    min_factor = policy.min_factor
    max_factor = policy.max_factor
    actual_by_day = _daily_actual_pv(rows, target_date=target_date, lookback_days=lookback_days)
    actual_by_day = {d: v for d, v in actual_by_day.items() if v > 0.05}
    if not arrays or len(actual_by_day) < min_days:
        return {
            "factor": 1.0,
            "raw_factor": None,
            "sample_days": len(actual_by_day),
            "actual_kwh": _round(sum(actual_by_day.values())),
            "modeled_kwh": None,
            "source": "insufficient_history",
        }

    start_date = min(actual_by_day)
    end_date = max(actual_by_day)
    modeled_by_day: dict[str, float] = defaultdict(float)
    endpoint = "https://archive-api.open-meteo.com/v1/archive"
    try:
        for array in arrays:
            hourly = fetch_open_meteo_hourly(
                endpoint=endpoint,
                lat=lat,
                lon=lon,
                timezone=timezone,
                start_date=start_date,
                end_date=end_date,
                array=array,
                http_get=http_get,
            )
            for row in _array_hourly_kwh(hourly, array=array, calibration_factor=1.0):
                dt = row.get("time")
                if isinstance(dt, datetime):
                    modeled_by_day[dt.date().isoformat()] += parse_csv_float(row.get("kwh"), default=0.0)
    except Exception:
        return {
            "factor": 1.0,
            "raw_factor": None,
            "sample_days": len(actual_by_day),
            "actual_kwh": _round(sum(actual_by_day.values())),
            "modeled_kwh": None,
            "source": "archive_fetch_failed",
        }

    common_days = sorted(set(actual_by_day) & set(modeled_by_day))
    actual_total = sum(actual_by_day[d] for d in common_days)
    modeled_total = sum(modeled_by_day[d] for d in common_days)
    if len(common_days) < min_days or modeled_total <= 0:
        return {
            "factor": 1.0,
            "raw_factor": None,
            "sample_days": len(common_days),
            "actual_kwh": _round(actual_total),
            "modeled_kwh": _round(modeled_total),
            "source": "insufficient_modeled_history",
        }

    raw_factor = actual_total / modeled_total
    factor = max(min_factor, min(max_factor, raw_factor))
    residual_multipliers = [
        actual_by_day[d] / max(1e-6, modeled_by_day[d] * factor)
        for d in common_days
    ]
    forecast_error_distribution = _ratio_distribution(
        residual_multipliers,
        source="actual_pv_vs_calibrated_open_meteo_gti",
    )

    weather_adjustments: dict[str, dict[str, float | int | None]] = {}
    weather_regression: dict[str, Any] = {}
    if env_bool("PV_ARRAY_WEATHER_CALIBRATION_ENABLED", default=True):
        try:
            weather_by_day = _fetch_archive_weather_daily_by_day(
                lat=lat,
                lon=lon,
                timezone=timezone,
                start_date=start_date,
                end_date=end_date,
                http_get=http_get,
            )

            by_class: dict[str, dict[str, float]] = defaultdict(
                lambda: {"actual": 0.0, "modeled": 0.0, "days": 0.0}
            )
            for day in common_days:
                weather_row: dict[str, float | str | None] = weather_by_day.get(day, {})
                weather_class = _normalize_weather_class(
                    str(weather_row.get("weather_class")) if weather_row.get("weather_class") is not None else None
                )
                slot = by_class[weather_class]
                slot["actual"] += actual_by_day.get(day, 0.0)
                slot["modeled"] += modeled_by_day.get(day, 0.0)
                slot["days"] += 1.0

            min_days_by_class = max(1, int(os.getenv("PV_ARRAY_WEATHER_CALIBRATION_MIN_DAYS", "2")))
            min_ratio = float(os.getenv("PV_ARRAY_WEATHER_ADJUSTMENT_MIN_RATIO", "0.7"))
            max_ratio = float(os.getenv("PV_ARRAY_WEATHER_ADJUSTMENT_MAX_RATIO", "1.3"))
            if min_ratio > max_ratio:
                min_ratio, max_ratio = max_ratio, min_ratio

            for weather_class, values in by_class.items():
                sample_days = int(values["days"])
                modeled_kwh = values["modeled"]
                actual_kwh = values["actual"]
                if sample_days < min_days_by_class or modeled_kwh <= 0:
                    continue
                raw_class_factor = actual_kwh / modeled_kwh
                class_factor = max(min_factor, min(max_factor, raw_class_factor))
                raw_ratio = class_factor / factor if factor > 0 else 1.0
                ratio = max(min_ratio, min(max_ratio, raw_ratio))
                weather_adjustments[weather_class] = {
                    "sample_days": sample_days,
                    "actual_kwh": _round(actual_kwh),
                    "modeled_kwh": _round(modeled_kwh),
                    "raw_factor": _round(raw_class_factor),
                    "factor": _round(class_factor),
                    "raw_multiplier": _round(raw_ratio),
                    "multiplier": _round(ratio),
                }

            if env_bool("PV_ARRAY_WEATHER_REGRESSION_ENABLED", default=True):
                regression_rows: list[tuple[float, float, float]] = []
                for day in common_days:
                    modeled_kwh = modeled_by_day.get(day, 0.0)
                    weather_row = weather_by_day.get(day, {})
                    if (
                        modeled_kwh <= 0
                        or not isinstance(weather_row, dict)
                    ):
                        continue
                    weather_class = _normalize_weather_class(
                        str(weather_row.get("weather_class")) if weather_row.get("weather_class") is not None else None
                    )
                    if weather_class not in {"cloudy", "rain"}:
                        continue
                    sunshine_hours = to_float(weather_row.get("sunshine_hours"))
                    precipitation_sum_mm = to_float(weather_row.get("precipitation_sum_mm"))
                    if sunshine_hours is None or precipitation_sum_mm is None:
                        continue
                    y_ratio = actual_by_day.get(day, 0.0) / modeled_kwh
                    y_ratio = max(min_factor, min(max_factor, y_ratio))
                    regression_rows.append((sunshine_hours, precipitation_sum_mm, y_ratio))

                regression_min_days = max(3, int(os.getenv("PV_ARRAY_WEATHER_REGRESSION_MIN_DAYS", "7")))
                regression_blend = parse_csv_float(os.getenv("PV_ARRAY_WEATHER_REGRESSION_BLEND", "0.1"), default=0.1)
                regression_blend = max(0.0, min(1.0, regression_blend))
                regression_ridge = parse_csv_float(os.getenv("PV_ARRAY_WEATHER_REGRESSION_RIDGE", "0.01"), default=0.01)
                regression_ridge = max(0.0, regression_ridge)
                weather_regression = {
                    "enabled": True,
                    "sample_days": len(regression_rows),
                    "blend": _round(regression_blend),
                    "ridge": _round(regression_ridge),
                    "min_factor": _round(min_factor),
                    "max_factor": _round(max_factor),
                    "target_classes": ["cloudy", "rain"],
                }
                if len(regression_rows) >= regression_min_days:
                    x = np.asarray([[1.0, row[0], row[1]] for row in regression_rows], dtype=float)
                    y = np.asarray([row[2] for row in regression_rows], dtype=float)
                    xtx = x.T @ x
                    ridge = regression_ridge * np.eye(xtx.shape[0], dtype=float)
                    beta = np.linalg.solve(xtx + ridge, x.T @ y)
                    weather_regression["coefficients"] = {
                        "intercept": _round(float(beta[0])),
                        "sunshine_hours": _round(float(beta[1])),
                        "precipitation_sum_mm": _round(float(beta[2])),
                    }
                    weather_regression["status"] = "fitted"
                else:
                    weather_regression["status"] = "insufficient_days"
        except Exception:
            # 天候別補正は補助情報なので、失敗時は全体補正のみで継続する
            weather_adjustments = {}
            weather_regression = {"enabled": False, "status": "failed"}

    return {
        "factor": _round(factor),
        "raw_factor": _round(raw_factor),
        "sample_days": len(common_days),
        "actual_kwh": _round(actual_total),
        "modeled_kwh": _round(modeled_total),
        "source": "actual_pv_vs_open_meteo_gti",
        "forecast_error_distribution": forecast_error_distribution,
        "weather_adjustments": weather_adjustments,
        "weather_regression": weather_regression,
    }


def calibrate_performance_ratio(
    *,
    arrays: list[PVArrayConfig],
    rows: list[dict[str, Any]],
    target_date: str,
    lat: float,
    lon: float,
    timezone: str,
    lookback_days: int = 45,
    min_days: int = 3,
    min_factor: float = 0.2,
    max_factor: float = 5.0,
    http_get: HttpGet = requests.get,
) -> dict[str, Any]:
    """Backward-compatible adapter for the typed calibration boundary."""
    return calibrate_performance_ratio_for(
        PvCalibrationInput(
            arrays=arrays,
            rows=rows,
            target_date=target_date,
            latitude=lat,
            longitude=lon,
            timezone=timezone,
        ),
        PvCalibrationPolicy(
            lookback_days=lookback_days,
            min_days=min_days,
            min_factor=min_factor,
            max_factor=max_factor,
        ),
        http_get=http_get,
    )


# readable-code-audit: skip STRUCT-04 — all array estimates use one weather payload and must preserve cross-array diagnostics
