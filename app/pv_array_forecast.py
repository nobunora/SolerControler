from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import requests


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


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
        capacity_kw = _to_float(item.get("capacity_kw"), 0.0)
        if capacity_kw <= 0:
            continue
        arrays.append(
            PVArrayConfig(
                name=name,
                azimuth_deg=_to_float(item.get("azimuth_deg")),
                tilt_deg=_to_float(item.get("tilt_deg")),
                capacity_kw=capacity_kw,
                performance_ratio=_to_float(item.get("performance_ratio"), 0.82),
                shading_factor=_to_float(item.get("shading_factor"), 1.0),
                temp_coeff_per_deg=_to_float(item.get("temp_coeff_per_deg"), -0.0035),
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
        gti = _to_optional_float(gti_values[idx] if idx < len(gti_values) else None)
        temp = _to_optional_float(temp_values[idx] if idx < len(temp_values) else None)
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
        gti = max(0.0, _to_float(row.get("gti_w_m2"), 0.0))
        temp_c = _to_float(row.get("temp_c"), 25.0)
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
        kwh = max(0.0, _to_float(row.get("kwh"), 0.0))
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
        out[day.isoformat()] += max(0.0, _to_float(row.get("pv"), 0.0))
    return dict(out)


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
                    modeled_by_day[dt.date().isoformat()] += _to_float(row.get("kwh"), 0.0)
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
    return {
        "factor": _round(factor),
        "raw_factor": _round(raw_factor),
        "sample_days": len(common_days),
        "actual_kwh": _round(actual_total),
        "modeled_kwh": _round(modeled_total),
        "source": "actual_pv_vs_open_meteo_gti",
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
            kwh = _to_float(row.get("kwh"), 0.0)
            item["total_kwh"] += kwh
            item[f"{array.name}_kwh"] = kwh
            item[f"{array.name}_gti_w_m2"] = _to_float(row.get("gti_w_m2"), 0.0)

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
            {"time": dt, "kwh": _to_float(row.get("total_kwh"), 0.0)}
            for dt, row in sorted(hourly_by_time.items())
        ]
    )
    return {
        "enabled": True,
        "source": "open-meteo-global_tilted_irradiance",
        "target_date": target_date,
        "timezone": timezone,
        "calibration_factor": _round(calibration_factor),
        "totals": {k: _round(v) for k, v in totals.items()},
        "arrays": array_summaries,
        "hourly": hourly,
    }


def build_pv_array_forecast(
    *,
    arrays: list[PVArrayConfig],
    rows: list[dict[str, Any]],
    target_date: str,
    lat: float,
    lon: float,
    timezone: str,
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
    forecast = forecast_pv_arrays(
        arrays=arrays,
        target_date=target_date,
        lat=lat,
        lon=lon,
        timezone=timezone,
        calibration_factor=_to_float(calibration.get("factor"), 1.0),
        http_get=http_get,
    )
    forecast["calibration"] = calibration
    return forecast

