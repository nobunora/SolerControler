from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np
import requests

from app.utils import env_bool, env_float_clamped, parse_csv_float, to_float, to_int


HttpGet = Callable[..., Any]


@dataclass(frozen=True)
class PVArrayConfig:
    name: str
    azimuth_deg: float
    tilt_deg: float
    capacity_kw: float
    performance_ratio: float = 0.82
    shading_factor: float = 1.0
    temp_coeff_per_deg: float = -0.0035


def _parse_time(raw: Any) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_forecast_solar_time(raw: Any) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    text = text.replace(" ", "T", 1)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def load_pv_array_configs(path: Path | None = None) -> list[PVArrayConfig]:
    config_path = path or Path(os.getenv("PV_ARRAY_CONFIG_PATH", "config/pv_arrays.json"))
    if not config_path.exists():
        return []

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    rows = raw.get("arrays", raw) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError(f"PV array config must contain an arrays list: {config_path}")

    arrays: list[PVArrayConfig] = []
    for idx, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or f"array_{idx + 1}").strip()
        capacity_kw = parse_csv_float(item.get("capacity_kw"), default=0.0)
        if capacity_kw <= 0:
            continue
        arrays.append(
            PVArrayConfig(
                name=name,
                azimuth_deg=parse_csv_float(item.get("azimuth_deg")),
                tilt_deg=parse_csv_float(item.get("tilt_deg")),
                capacity_kw=capacity_kw,
                performance_ratio=parse_csv_float(item.get("performance_ratio"), default=0.82),
                shading_factor=parse_csv_float(item.get("shading_factor"), default=1.0),
                temp_coeff_per_deg=parse_csv_float(item.get("temp_coeff_per_deg"), default=-0.0035),
            )
        )
    return arrays


def _open_meteo_params(
    *,
    lat: float,
    lon: float,
    timezone: str,
    start_date: str,
    end_date: str,
    array: PVArrayConfig,
) -> dict[str, Any]:
    return {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": "global_tilted_irradiance,temperature_2m",
        "timezone": timezone,
        "tilt": array.tilt_deg,
        "azimuth": array.azimuth_deg,
    }


def _fetch_hourly(
    *,
    endpoint: str,
    lat: float,
    lon: float,
    timezone: str,
    start_date: str,
    end_date: str,
    array: PVArrayConfig,
    http_get: HttpGet,
    timeout_sec: int = 30,
) -> list[dict[str, Any]]:
    resp = http_get(
        endpoint,
        params=_open_meteo_params(
            lat=lat,
            lon=lon,
            timezone=timezone,
            start_date=start_date,
            end_date=end_date,
            array=array,
        ),
        timeout=timeout_sec,
    )
    resp.raise_for_status()
    hourly = resp.json().get("hourly", {})
    times = hourly.get("time", [])
    gti_values = hourly.get("global_tilted_irradiance", [])
    temp_values = hourly.get("temperature_2m", [])
    out: list[dict[str, Any]] = []
    for idx, raw_time in enumerate(times if isinstance(times, list) else []):
        dt = _parse_time(raw_time)
        if dt is None:
            continue
        gti = to_float(gti_values[idx] if idx < len(gti_values) else None)
        temp = to_float(temp_values[idx] if idx < len(temp_values) else None)
        out.append({"time": dt, "gti_w_m2": gti, "temp_c": temp})
    return out


def _array_hourly_kwh(
    rows: list[dict[str, Any]],
    *,
    array: PVArrayConfig,
    calibration_factor: float = 1.0,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    effective_pr = array.performance_ratio * calibration_factor
    for row in rows:
        dt = row.get("time")
        if not isinstance(dt, datetime):
            continue
        gti = max(0.0, parse_csv_float(row.get("gti_w_m2"), default=0.0))
        temp_c = parse_csv_float(row.get("temp_c"), default=25.0)
        temp_factor = max(0.0, 1.0 + array.temp_coeff_per_deg * (temp_c - 25.0))
        # Open-Meteo hourly GTI is a preceding-hour mean W/m2 value.
        # For a one-hour interval, W/m2 / 1000 is approximately kWh/m2.
        kwh = array.capacity_kw * (gti / 1000.0) * effective_pr * array.shading_factor * temp_factor
        out.append(
            {
                "time": dt,
                "kwh": max(0.0, kwh),
                "gti_w_m2": gti,
                "temp_c": temp_c,
            }
        )
    return out


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    total = 0.0
    daytime = 0.0
    morning = 0.0
    midday = 0.0
    evening = 0.0
    peak_kw = 0.0
    for row in rows:
        dt = row.get("time")
        if not isinstance(dt, datetime):
            continue
        kwh = max(0.0, parse_csv_float(row.get("kwh"), default=0.0))
        total += kwh
        peak_kw = max(peak_kw, kwh)
        if 7 <= dt.hour < 23:
            daytime += kwh
        if 7 <= dt.hour < 10:
            morning += kwh
        if 10 <= dt.hour < 16:
            midday += kwh
        if 16 <= dt.hour < 23:
            evening += kwh
    return {
        "total_kwh": total,
        "daytime_kwh": daytime,
        "morning_kwh": morning,
        "midday_kwh": midday,
        "evening_kwh": evening,
        "peak_kw": peak_kw,
    }


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
    if weather_code is None:
        return "unknown"
    if weather_code == 0:
        return "clear"
    if 1 <= weather_code <= 3:
        return "cloudy"
    if weather_code in {45, 48}:
        return "fog"
    if 51 <= weather_code <= 67 or 80 <= weather_code <= 82:
        return "rain"
    if 71 <= weather_code <= 77 or 85 <= weather_code <= 86:
        return "snow"
    if 95 <= weather_code <= 99:
        return "storm"
    return "other"


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
) -> dict[str, str]:
    resp = http_get(
        "https://archive-api.open-meteo.com/v1/archive",
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
    resp.raise_for_status()
    daily = resp.json().get("daily", {})
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
            hourly = _fetch_hourly(
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
                weather_row = weather_by_day.get(day, {})
                weather_class = _normalize_weather_class(
                    weather_row.get("weather_class") if isinstance(weather_row, dict) else None
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
                    weather_class = _normalize_weather_class(weather_row.get("weather_class"))
                    if weather_class not in {"cloudy", "rain"}:
                        continue
                    sunshine_hours = to_float(weather_row.get("sunshine_hours"))
                    precipitation_sum_mm = to_float(weather_row.get("precipitation_sum_mm"))
                    if sunshine_hours is None or precipitation_sum_mm is None:
                        continue
                    y = actual_by_day.get(day, 0.0) / modeled_kwh
                    y = max(min_factor, min(max_factor, y))
                    regression_rows.append((sunshine_hours, precipitation_sum_mm, y))

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


def forecast_pv_arrays(
    *,
    arrays: list[PVArrayConfig],
    target_date: str,
    lat: float,
    lon: float,
    timezone: str,
    calibration_factor: float = 1.0,
    http_get: HttpGet = requests.get,
) -> dict[str, Any]:
    endpoint = "https://api.open-meteo.com/v1/forecast"
    hourly_by_time: dict[datetime, dict[str, Any]] = {}
    array_summaries: list[dict[str, Any]] = []

    for array in arrays:
        raw_rows = _fetch_hourly(
            endpoint=endpoint,
            lat=lat,
            lon=lon,
            timezone=timezone,
            start_date=target_date,
            end_date=target_date,
            array=array,
            http_get=http_get,
            timeout_sec=20,
        )
        hourly_rows = _array_hourly_kwh(raw_rows, array=array, calibration_factor=calibration_factor)
        totals = _aggregate(hourly_rows)
        array_summaries.append(
            {
                **asdict(array),
                "effective_performance_ratio": _round(array.performance_ratio * calibration_factor),
                **{k: _round(v) for k, v in totals.items()},
            }
        )
        for row in hourly_rows:
            dt = row.get("time")
            if not isinstance(dt, datetime):
                continue
            item = hourly_by_time.setdefault(dt, {"time": dt, "total_kwh": 0.0})
            kwh = parse_csv_float(row.get("kwh"), default=0.0)
            item["total_kwh"] += kwh
            item[f"{array.name}_kwh"] = kwh
            item[f"{array.name}_gti_w_m2"] = parse_csv_float(row.get("gti_w_m2"), default=0.0)

    hourly = []
    for dt, row in sorted(hourly_by_time.items()):
        rounded = {
            key: (_round(value) if isinstance(value, (int, float)) else value)
            for key, value in row.items()
        }
        rounded["time"] = dt.isoformat(timespec="minutes")
        rounded["total_kw"] = rounded.get("total_kwh")
        hourly.append(rounded)

    totals = _aggregate(
        [
            {"time": dt, "kwh": parse_csv_float(row.get("total_kwh"), default=0.0)}
            for dt, row in sorted(hourly_by_time.items())
        ]
    )
    return {
        "enabled": True,
        "source": "open-meteo-global_tilted_irradiance",
        "provider": "open_meteo",
        "target_date": target_date,
        "timezone": timezone,
        "calibration_factor": _round(calibration_factor),
        "totals": {k: _round(v) for k, v in totals.items()},
        "arrays": array_summaries,
        "hourly": hourly,
    }


def _forecast_solar_url(
    *,
    lat: float,
    lon: float,
    array: PVArrayConfig,
) -> str:
    base = os.getenv("FORECAST_SOLAR_BASE_URL", "https://api.forecast.solar").rstrip("/")
    return (
        f"{base}/estimate/"
        f"{lat:.6f}/{lon:.6f}/{array.tilt_deg:.3f}/{array.azimuth_deg:.3f}/{array.capacity_kw:.3f}"
    )


def _forecast_solar_series_to_rows(
    payload: dict[str, Any],
    *,
    array: PVArrayConfig,
    target_date: str,
    calibration_factor: float,
) -> list[dict[str, Any]]:
    result = payload.get("result", {})
    if not isinstance(result, dict):
        raise RuntimeError("Forecast.Solar response does not contain result")

    series = result.get("watt_hours_period")
    mode = "watt_hours_period"
    if not isinstance(series, dict) or not series:
        series = result.get("watts")
        mode = "watts"
    if not isinstance(series, dict) or not series:
        cumulative = result.get("watt_hours")
        if isinstance(cumulative, dict) and cumulative:
            mode = "watt_hours"
            sorted_items = [
                (dt, value)
                for dt, value in (
                    (_parse_forecast_solar_time(raw_time), to_float(value))
                    for raw_time, value in cumulative.items()
                )
                if dt is not None and value is not None
            ]
            sorted_items.sort(key=lambda item: item[0])
            series = {}
            prev_value: float | None = None
            for dt, value in sorted_items:
                period_wh = value if prev_value is None else max(0.0, value - prev_value)
                series[dt.isoformat()] = period_wh
                prev_value = value
        else:
            raise RuntimeError("Forecast.Solar response does not contain hourly energy")

    rows: list[dict[str, Any]] = []
    effective_factor = array.performance_ratio * array.shading_factor * calibration_factor
    for raw_time, value in series.items():
        dt = _parse_forecast_solar_time(raw_time)
        wh = to_float(value)
        if dt is None or wh is None:
            continue
        if dt.date().isoformat() != target_date:
            continue
        if mode == "watts":
            kwh = wh / 1000.0
        else:
            kwh = wh / 1000.0
        rows.append(
            {
                "time": dt,
                "kwh": max(0.0, kwh * effective_factor),
                "forecast_solar_raw_wh": wh,
                "forecast_solar_series": mode,
            }
        )
    if not rows:
        raise RuntimeError(f"Forecast.Solar returned no rows for {target_date}")
    return rows


def forecast_pv_arrays_forecast_solar(
    *,
    arrays: list[PVArrayConfig],
    target_date: str,
    lat: float,
    lon: float,
    timezone: str,
    calibration_factor: float = 1.0,
    http_get: HttpGet = requests.get,
) -> dict[str, Any]:
    hourly_by_time: dict[datetime, dict[str, Any]] = {}
    array_summaries: list[dict[str, Any]] = []
    timeout_sec = int(os.getenv("FORECAST_SOLAR_TIMEOUT_SEC", "30").strip() or "30")

    for array in arrays:
        url = _forecast_solar_url(lat=lat, lon=lon, array=array)
        resp = http_get(url, timeout=timeout_sec)
        resp.raise_for_status()
        payload = resp.json()
        hourly_rows = _forecast_solar_series_to_rows(
            payload,
            array=array,
            target_date=target_date,
            calibration_factor=calibration_factor,
        )
        totals = _aggregate(hourly_rows)
        array_summaries.append(
            {
                **asdict(array),
                "effective_performance_ratio": _round(array.performance_ratio * calibration_factor),
                "effective_factor": _round(array.performance_ratio * array.shading_factor * calibration_factor),
                **{k: _round(v) for k, v in totals.items()},
            }
        )
        for row in hourly_rows:
            dt = row.get("time")
            if not isinstance(dt, datetime):
                continue
            item = hourly_by_time.setdefault(dt, {"time": dt, "total_kwh": 0.0})
            kwh = parse_csv_float(row.get("kwh"), default=0.0)
            item["total_kwh"] += kwh
            item[f"{array.name}_kwh"] = kwh
            item[f"{array.name}_forecast_solar_raw_wh"] = parse_csv_float(row.get("forecast_solar_raw_wh"), default=0.0)

    hourly = []
    for dt, row in sorted(hourly_by_time.items()):
        rounded = {
            key: (_round(value) if isinstance(value, (int, float)) else value)
            for key, value in row.items()
        }
        rounded["time"] = dt.isoformat(timespec="minutes")
        rounded["total_kw"] = rounded.get("total_kwh")
        hourly.append(rounded)

    totals = _aggregate(
        [
            {"time": dt, "kwh": parse_csv_float(row.get("total_kwh"), default=0.0)}
            for dt, row in sorted(hourly_by_time.items())
        ]
    )
    return {
        "enabled": True,
        "source": "forecast-solar-estimate",
        "provider": "forecast_solar",
        "target_date": target_date,
        "timezone": timezone,
        "calibration_factor": _round(calibration_factor),
        "totals": {k: _round(v) for k, v in totals.items()},
        "arrays": array_summaries,
        "hourly": hourly,
    }


def _provider_order_from_env() -> list[str]:
    raw = os.getenv("PV_ARRAY_PROVIDER", "forecast_solar,open_meteo").strip()
    if not raw:
        raw = "forecast_solar,open_meteo"
    aliases = {
        "forecast.solar": "forecast_solar",
        "forecast-solar": "forecast_solar",
        "forecast_solar": "forecast_solar",
        "open-meteo": "open_meteo",
        "open_meteo": "open_meteo",
        "openmeteo": "open_meteo",
    }
    providers: list[str] = []
    for part in raw.split(","):
        key = aliases.get(part.strip().lower())
        if key and key not in providers:
            providers.append(key)
    return providers or ["forecast_solar", "open_meteo"]


def _forecast_hourly_map(forecast: dict[str, Any]) -> dict[datetime, float]:
    out: dict[datetime, float] = {}
    hourly = forecast.get("hourly", [])
    if not isinstance(hourly, list):
        return out
    for row in hourly:
        if not isinstance(row, dict):
            continue
        dt = _parse_time(row.get("time")) or _parse_forecast_solar_time(row.get("time"))
        if dt is None:
            continue
        out[dt] = max(0.0, parse_csv_float(row.get("total_kwh"), default=0.0))
    return out


def _ensemble_hourly_value(*, hour: int, forecast_solar_kwh: float, open_meteo_kwh: float) -> tuple[float, str]:
    if 7 <= hour < 10:
        return max(forecast_solar_kwh, open_meteo_kwh), "morning_max"
    if 10 <= hour < 16:
        weight = env_float_clamped("PV_ENSEMBLE_OPEN_METEO_WEIGHT_MIDDAY", 0.35, max_val=1.0)
        return (forecast_solar_kwh * (1.0 - weight) + open_meteo_kwh * weight), "midday_blend"
    if 16 <= hour < 23:
        weight = env_float_clamped("PV_ENSEMBLE_OPEN_METEO_WEIGHT_EVENING", 0.25, max_val=1.0)
        return (forecast_solar_kwh * (1.0 - weight) + open_meteo_kwh * weight), "evening_blend"
    weight = env_float_clamped("PV_ENSEMBLE_OPEN_METEO_WEIGHT_OTHER", 0.50, max_val=1.0)
    return (forecast_solar_kwh * (1.0 - weight) + open_meteo_kwh * weight), "other_blend"


def _ensemble_pv_forecasts(
    *,
    forecasts: list[dict[str, Any]],
    target_date: str,
    timezone: str,
    calibration_factor: float,
) -> dict[str, Any]:
    by_provider = {str(f.get("provider") or ""): f for f in forecasts if isinstance(f, dict)}
    forecast_solar = by_provider.get("forecast_solar")
    open_meteo = by_provider.get("open_meteo")
    if forecast_solar is None or open_meteo is None:
        raise RuntimeError("PV ensemble requires forecast_solar and open_meteo forecasts")

    fs_hourly = _forecast_hourly_map(forecast_solar)
    om_hourly = _forecast_hourly_map(open_meteo)
    hourly = []
    rows_for_totals: list[dict[str, Any]] = []
    for dt in sorted(set(fs_hourly) | set(om_hourly)):
        fs_kwh = fs_hourly.get(dt)
        om_kwh = om_hourly.get(dt)
        if fs_kwh is None:
            total_kwh = max(0.0, om_kwh or 0.0)
            method = "open_meteo_only"
        elif om_kwh is None:
            total_kwh = max(0.0, fs_kwh)
            method = "forecast_solar_only"
        else:
            total_kwh, method = _ensemble_hourly_value(
                hour=dt.hour,
                forecast_solar_kwh=max(0.0, fs_kwh),
                open_meteo_kwh=max(0.0, om_kwh),
            )
        item = {
            "time": dt.isoformat(timespec="minutes"),
            "total_kwh": _round(total_kwh),
            "total_kw": _round(total_kwh),
            "forecast_solar_kwh": _round(fs_kwh) if fs_kwh is not None else None,
            "open_meteo_kwh": _round(om_kwh) if om_kwh is not None else None,
            "ensemble_method": method,
        }
        hourly.append(item)
        rows_for_totals.append({"time": dt, "kwh": total_kwh})

    totals = _aggregate(rows_for_totals)
    return {
        "enabled": True,
        "source": "ensemble-forecast-solar-open-meteo",
        "provider": "ensemble",
        "target_date": target_date,
        "timezone": timezone,
        "calibration_factor": _round(calibration_factor),
        "totals": {k: _round(v) for k, v in totals.items()},
        "arrays": forecast_solar.get("arrays") or open_meteo.get("arrays") or [],
        "hourly": hourly,
        "provider_forecasts": {
            "forecast_solar": forecast_solar,
            "open_meteo": open_meteo,
        },
        "ensemble": {
            "method": "morning_max_midday_weighted_blend",
            "morning_hours": [7, 8, 9],
            "open_meteo_weight_midday": env_float_clamped("PV_ENSEMBLE_OPEN_METEO_WEIGHT_MIDDAY", 0.35, max_val=1.0),
            "open_meteo_weight_evening": env_float_clamped("PV_ENSEMBLE_OPEN_METEO_WEIGHT_EVENING", 0.25, max_val=1.0),
            "open_meteo_weight_other": env_float_clamped("PV_ENSEMBLE_OPEN_METEO_WEIGHT_OTHER", 0.50, max_val=1.0),
        },
    }


def build_pv_array_forecast(
    *,
    arrays: list[PVArrayConfig],
    rows: list[dict[str, Any]],
    target_date: str,
    lat: float,
    lon: float,
    timezone: str,
    target_weather_class: str | None = None,
    target_sun_hours: float | None = None,
    target_precipitation_sum_mm: float | None = None,
    http_get: HttpGet = requests.get,
) -> dict[str, Any] | None:
    if not arrays:
        return None
    calibration = calibrate_performance_ratio(
        arrays=arrays,
        rows=rows,
        target_date=target_date,
        lat=lat,
        lon=lon,
        timezone=timezone,
        lookback_days=int(os.getenv("PV_ARRAY_CALIBRATION_LOOKBACK_DAYS", "45")),
        min_days=int(os.getenv("PV_ARRAY_CALIBRATION_MIN_DAYS", "3")),
        min_factor=float(os.getenv("PV_ARRAY_CALIBRATION_MIN_FACTOR", "0.2")),
        max_factor=float(os.getenv("PV_ARRAY_CALIBRATION_MAX_FACTOR", "5.0")),
        http_get=http_get,
    )
    base_factor = parse_csv_float(calibration.get("factor"), default=1.0)
    weather_class = _normalize_weather_class(target_weather_class)
    weather_adjustments = calibration.get("weather_adjustments")
    weather_multiplier = 1.0
    adjustment_strategy = "base"
    if isinstance(weather_adjustments, dict):
        entry = weather_adjustments.get(weather_class)
        if isinstance(entry, dict):
            weather_multiplier = parse_csv_float(entry.get("multiplier"), default=1.0)
            adjustment_strategy = "class_multiplier"
    effective_factor = max(0.0, base_factor * weather_multiplier)

    weather_regression = calibration.get("weather_regression")
    if (
        weather_class in {"cloudy", "rain"}
        and isinstance(weather_regression, dict)
        and str(weather_regression.get("status")) == "fitted"
        and target_sun_hours is not None
        and target_precipitation_sum_mm is not None
    ):
        coefficients = weather_regression.get("coefficients")
        if isinstance(coefficients, dict):
            intercept = parse_csv_float(coefficients.get("intercept"), default=base_factor)
            coef_sun = parse_csv_float(coefficients.get("sunshine_hours"), default=0.0)
            coef_precip = parse_csv_float(coefficients.get("precipitation_sum_mm"), default=0.0)
            blend = parse_csv_float(weather_regression.get("blend"), default=0.1)
            blend = max(0.0, min(1.0, blend))
            reg_factor_raw = intercept + coef_sun * target_sun_hours + coef_precip * target_precipitation_sum_mm
            reg_min = parse_csv_float(weather_regression.get("min_factor"), default=0.2)
            reg_max = parse_csv_float(weather_regression.get("max_factor"), default=5.0)
            if reg_min > reg_max:
                reg_min, reg_max = reg_max, reg_min
            reg_factor = max(reg_min, min(reg_max, reg_factor_raw))
            effective_factor = max(0.0, base_factor * (1.0 - blend) + reg_factor * blend)
            weather_multiplier = (effective_factor / base_factor) if base_factor > 0 else 1.0
            adjustment_strategy = "regression_blend"

    provider_attempts: list[dict[str, Any]] = []
    successful_forecasts: list[dict[str, Any]] = []
    provider_mode = os.getenv("PV_ARRAY_PROVIDER_MODE", "ensemble").strip().lower() or "ensemble"
    for provider in _provider_order_from_env():
        try:
            if provider == "forecast_solar":
                current_forecast = forecast_pv_arrays_forecast_solar(
                    arrays=arrays,
                    target_date=target_date,
                    lat=lat,
                    lon=lon,
                    timezone=timezone,
                    calibration_factor=effective_factor,
                    http_get=http_get,
                )
            elif provider == "open_meteo":
                current_forecast = forecast_pv_arrays(
                    arrays=arrays,
                    target_date=target_date,
                    lat=lat,
                    lon=lon,
                    timezone=timezone,
                    calibration_factor=effective_factor,
                    http_get=http_get,
                )
            else:
                continue
            provider_attempts.append({"provider": provider, "ok": True})
            successful_forecasts.append(current_forecast)
            if provider_mode in {"first", "first_success", "fallback"}:
                break
        except Exception as exc:
            provider_attempts.append({"provider": provider, "ok": False, "error": str(exc)})

    if not successful_forecasts:
        raise RuntimeError(f"PV array forecast failed for providers: {provider_attempts}")

    if provider_mode == "ensemble" and len(successful_forecasts) >= 2:
        forecast_solar_ok = any(f.get("provider") == "forecast_solar" for f in successful_forecasts)
        open_meteo_ok = any(f.get("provider") == "open_meteo" for f in successful_forecasts)
        if forecast_solar_ok and open_meteo_ok:
            forecast = _ensemble_pv_forecasts(
                forecasts=successful_forecasts,
                target_date=target_date,
                timezone=timezone,
                calibration_factor=effective_factor,
            )
        else:
            forecast = successful_forecasts[0]
    else:
        forecast = successful_forecasts[0]

    calibration["target_weather_class"] = weather_class
    calibration["target_sun_hours"] = _round(target_sun_hours)
    calibration["target_precipitation_sum_mm"] = _round(target_precipitation_sum_mm)
    calibration["adjustment_strategy"] = adjustment_strategy
    calibration["weather_multiplier"] = _round(weather_multiplier)
    calibration["effective_factor"] = _round(effective_factor)
    forecast["calibration_factor"] = _round(effective_factor)
    forecast["calibration"] = calibration
    forecast["provider_attempts"] = provider_attempts
    return forecast
