from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests

from app.forecasting.pv_array_adapters import (
    aggregate_hourly as _aggregate,
    fetch_open_meteo_hourly,
    forecast_pv_arrays_forecast_solar,
    http_get_with_retry,
    parse_provider_time,
    response_json_object,
    round_finite as _round,
)
from app.forecasting.pv_array_calibration import (
    PvCalibrationInput,
    PvCalibrationPolicy,
    calibrate_performance_ratio,
    calibrate_performance_ratio_for,
    _normalize_weather_class,
)
from app.forecasting.pv_array_selection import (
    ensemble_pv_forecasts as _ensemble_pv_forecasts,
    provider_order_from_env,
    select_provider_forecasts,
)
from app.parsing.numbers import parse_csv_float


HttpGet = Callable[..., Any]

__all__ = [
    "PVArrayConfig",
    "PvCalibrationInput",
    "PvCalibrationPolicy",
    "build_pv_array_forecast",
    "calibrate_performance_ratio",
    "calibrate_performance_ratio_for",
    "fetch_open_meteo_hourly",
    "forecast_pv_arrays",
    "forecast_pv_arrays_forecast_solar",
    "load_pv_array_configs",
]


def _response_json_object(response: Any, *, provider: str) -> dict[str, Any]:
    """Compatibility wrapper for the provider adapter's JSON boundary."""
    return response_json_object(response, provider=provider)


def _http_get_with_retry(
    http_get: HttpGet,
    url: str,
    *,
    provider: str,
    **kwargs: Any,
) -> Any:
    """Compatibility wrapper for the provider adapter retry boundary."""
    return http_get_with_retry(http_get, url, provider=provider, **kwargs)


@dataclass(frozen=True)
class PVArrayConfig:
    name: str
    azimuth_deg: float
    tilt_deg: float
    capacity_kw: float
    # 公称出力から配線・変換・実運用の損失を差し引くため、PVの性能比を既定で適用する。
    performance_ratio: float = 0.82
    shading_factor: float = 1.0
    temp_coeff_per_deg: float = -0.0035


def _parse_time(raw: Any) -> datetime | None:
    return parse_provider_time(raw)


def _parse_forecast_solar_time(raw: Any) -> datetime | None:
    return parse_provider_time(raw, forecast_solar=True)


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
        raw_rows = fetch_open_meteo_hourly(
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


def _provider_order_from_env() -> list[str]:
    """Compatibility wrapper for configured provider precedence."""
    return provider_order_from_env()


# readable-code-audit: skip STRUCT-04 — provider candidates, calibration, and selected provenance belong to one auditable PV forecast snapshot
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
    calibration = calibrate_performance_ratio_for(
        PvCalibrationInput(
            arrays=arrays,
            rows=rows,
            target_date=target_date,
            latitude=lat,
            longitude=lon,
            timezone=timezone,
        ),
        PvCalibrationPolicy.from_env(),
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

    provider_mode = os.getenv("PV_ARRAY_PROVIDER_MODE", "ensemble").strip().lower() or "ensemble"
    successful_forecasts, provider_attempts = select_provider_forecasts(
        {
            "forecast_solar": lambda: forecast_pv_arrays_forecast_solar(arrays=arrays, target_date=target_date, lat=lat, lon=lon, timezone=timezone, calibration_factor=effective_factor, http_get=http_get),
            "open_meteo": lambda: forecast_pv_arrays(arrays=arrays, target_date=target_date, lat=lat, lon=lon, timezone=timezone, calibration_factor=effective_factor, http_get=http_get),
        },
        mode=provider_mode,
    )

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
