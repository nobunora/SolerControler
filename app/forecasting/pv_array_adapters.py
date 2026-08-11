"""HTTP provider adapters for PV array forecasts.

This module owns retrying requests and validating provider JSON.  Domain
calibration and provider selection remain outside this I/O boundary.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

import requests

from app.parsing.numbers import parse_csv_float, to_float

if TYPE_CHECKING:
    from app.forecasting.pv_array import PVArrayConfig


HttpGet = Callable[..., Any]


def parse_provider_time(raw: Any, *, forecast_solar: bool = False) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if forecast_solar:
        text = text.replace(" ", "T", 1)
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def round_finite(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def aggregate_hourly(rows: list[dict[str, Any]]) -> dict[str, float]:
    totals = {"total_kwh": 0.0, "daytime_kwh": 0.0, "morning_kwh": 0.0, "midday_kwh": 0.0, "evening_kwh": 0.0, "peak_kw": 0.0}
    for row in rows:
        dt = row.get("time")
        if not isinstance(dt, datetime):
            continue
        kwh = max(0.0, parse_csv_float(row.get("kwh"), default=0.0))
        totals["total_kwh"] += kwh
        totals["peak_kw"] = max(totals["peak_kw"], kwh)
        if 7 <= dt.hour < 23:
            totals["daytime_kwh"] += kwh
        if 5 <= dt.hour < 10:
            totals["morning_kwh"] += kwh
        if 10 <= dt.hour < 16:
            totals["midday_kwh"] += kwh
        if 16 <= dt.hour < 23:
            totals["evening_kwh"] += kwh
    return totals


def response_json_object(response: Any, *, provider: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{provider} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{provider} returned a non-object JSON payload")
    return payload


def http_get_with_retry(
    http_get: HttpGet,
    url: str,
    *,
    provider: str,
    **kwargs: Any,
) -> Any:
    """Request one provider endpoint, retrying only transient HTTP failures."""
    max_attempts = max(1, int(os.getenv("PV_HTTP_MAX_ATTEMPTS", "2")))
    retry_delay_seconds = max(0.0, float(os.getenv("PV_HTTP_RETRY_DELAY_SECONDS", "0.5")))
    for attempt in range(1, max_attempts + 1):
        try:
            response = http_get(url, **kwargs)
            response.raise_for_status()
            return response
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            retryable = status_code is None or 500 <= int(status_code) < 600
            if attempt >= max_attempts or not retryable:
                raise RuntimeError(f"{provider} request failed after {attempt} attempt(s)") from exc
            if retry_delay_seconds > 0:
                time.sleep(retry_delay_seconds)
    raise AssertionError("unreachable")


def open_meteo_params(*, lat: float, lon: float, timezone: str, start_date: str, end_date: str, array: PVArrayConfig) -> dict[str, Any]:
    return {"latitude": lat, "longitude": lon, "start_date": start_date, "end_date": end_date, "hourly": "global_tilted_irradiance,temperature_2m", "timezone": timezone, "tilt": array.tilt_deg, "azimuth": array.azimuth_deg}


def fetch_open_meteo_hourly(*, endpoint: str, lat: float, lon: float, timezone: str, start_date: str, end_date: str, array: PVArrayConfig, http_get: HttpGet, timeout_sec: int = 30) -> list[dict[str, Any]]:
    response = http_get_with_retry(http_get, endpoint, provider="Open-Meteo", params=open_meteo_params(lat=lat, lon=lon, timezone=timezone, start_date=start_date, end_date=end_date, array=array), timeout=timeout_sec)
    hourly = response_json_object(response, provider="Open-Meteo").get("hourly", {})
    if not isinstance(hourly, dict):
        raise RuntimeError("Open-Meteo hourly payload is not an object")
    times = hourly.get("time", [])
    gti_values = hourly.get("global_tilted_irradiance", [])
    temp_values = hourly.get("temperature_2m", [])
    rows: list[dict[str, Any]] = []
    for index, raw_time in enumerate(times if isinstance(times, list) else []):
        dt = parse_provider_time(raw_time)
        if dt is not None:
            rows.append({"time": dt, "gti_w_m2": to_float(gti_values[index] if index < len(gti_values) else None), "temp_c": to_float(temp_values[index] if index < len(temp_values) else None)})
    return rows


def forecast_solar_url(*, lat: float, lon: float, array: PVArrayConfig) -> str:
    base = os.getenv("FORECAST_SOLAR_BASE_URL", "https://api.forecast.solar").rstrip("/")
    return f"{base}/estimate/{lat:.6f}/{lon:.6f}/{array.tilt_deg:.3f}/{array.azimuth_deg:.3f}/{array.capacity_kw:.3f}"


def forecast_solar_series_to_rows(payload: dict[str, Any], *, array: PVArrayConfig, target_date: str, calibration_factor: float) -> list[dict[str, Any]]:
    result = payload.get("result", {})
    if not isinstance(result, dict):
        raise RuntimeError("Forecast.Solar response does not contain result")
    series = result.get("watt_hours_period")
    mode = "watt_hours_period"
    if not isinstance(series, dict) or not series:
        series, mode = result.get("watts"), "watts"
    if not isinstance(series, dict) or not series:
        cumulative = result.get("watt_hours")
        if not isinstance(cumulative, dict) or not cumulative:
            raise RuntimeError("Forecast.Solar response does not contain hourly energy")
        mode = "watt_hours"
        parsed = sorted((dt, value) for raw_time, raw_value in cumulative.items() if (dt := parse_provider_time(raw_time, forecast_solar=True)) is not None and (value := to_float(raw_value)) is not None)
        series = {}
        previous: float | None = None
        for dt, value in parsed:
            series[dt.isoformat()] = value if previous is None else max(0.0, value - previous)
            previous = value
    factor = array.performance_ratio * array.shading_factor * calibration_factor
    rows = []
    for raw_time, raw_value in series.items():
        dt = parse_provider_time(raw_time, forecast_solar=True)
        wh = to_float(raw_value)
        if dt is not None and wh is not None and dt.date().isoformat() == target_date:
            rows.append({"time": dt, "kwh": max(0.0, wh / 1000.0 * factor), "forecast_solar_raw_wh": wh, "forecast_solar_series": mode})
    if not rows:
        raise RuntimeError(f"Forecast.Solar returned no rows for {target_date}")
    return rows


def forecast_pv_arrays_forecast_solar(*, arrays: list[PVArrayConfig], target_date: str, lat: float, lon: float, timezone: str, calibration_factor: float = 1.0, http_get: HttpGet = requests.get) -> dict[str, Any]:
    hourly_by_time: dict[datetime, dict[str, Any]] = {}
    summaries = []
    timeout_sec = int(os.getenv("FORECAST_SOLAR_TIMEOUT_SEC", "30").strip() or "30")
    for array in arrays:
        response = http_get_with_retry(http_get, forecast_solar_url(lat=lat, lon=lon, array=array), provider="Forecast.Solar", timeout=timeout_sec)
        rows = forecast_solar_series_to_rows(response_json_object(response, provider="Forecast.Solar"), array=array, target_date=target_date, calibration_factor=calibration_factor)
        totals = aggregate_hourly(rows)
        summaries.append({**asdict(array), "effective_performance_ratio": round_finite(array.performance_ratio * calibration_factor), "effective_factor": round_finite(array.performance_ratio * array.shading_factor * calibration_factor), **{key: round_finite(value) for key, value in totals.items()}})
        for row in rows:
            dt = row["time"]
            item = hourly_by_time.setdefault(dt, {"time": dt, "total_kwh": 0.0})
            kwh = parse_csv_float(row.get("kwh"), default=0.0)
            item["total_kwh"] += kwh
            item[f"{array.name}_kwh"] = kwh
            item[f"{array.name}_forecast_solar_raw_wh"] = parse_csv_float(row.get("forecast_solar_raw_wh"), default=0.0)
    hourly = []
    for dt, row in sorted(hourly_by_time.items()):
        rounded = {key: round_finite(value) if isinstance(value, (int, float)) else value for key, value in row.items()}
        rounded["time"] = dt.isoformat(timespec="minutes")
        rounded["total_kw"] = rounded.get("total_kwh")
        hourly.append(rounded)
    totals = aggregate_hourly([{"time": dt, "kwh": row["total_kwh"]} for dt, row in sorted(hourly_by_time.items())])
    return {"enabled": True, "source": "forecast-solar-estimate", "provider": "forecast_solar", "target_date": target_date, "timezone": timezone, "calibration_factor": round_finite(calibration_factor), "totals": {key: round_finite(value) for key, value in totals.items()}, "arrays": summaries, "hourly": hourly}
