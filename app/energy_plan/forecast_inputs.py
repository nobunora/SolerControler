"""Build hourly load and PV inputs for an energy-plan optimization."""

from __future__ import annotations

import statistics
from datetime import date, datetime
from typing import Any


def load_rows_for_consumption_forecast(rows: list[dict[str, Any]]) -> list[dict[str, object]]:
    """Keep the timestamp and load fields required by the consumption forecaster."""
    out: list[dict[str, object]] = []
    for row in rows:
        timestamp = row.get("dt")
        if not isinstance(timestamp, datetime):
            continue
        out.append({"dt": timestamp, "load": float(row.get("load", 0.0) or 0.0)})
    return out


def pv_forecast_totals(pv_forecast: dict[str, object] | None) -> dict[str, object]:
    """Return PV forecast totals only when the optional forecast is enabled."""
    if not pv_forecast or not pv_forecast.get("enabled"):
        return {}
    totals = pv_forecast.get("totals", {})
    return totals if isinstance(totals, dict) else {}


def hourly_pv_kwh_from_forecast(
    pv_forecast: dict[str, object] | None,
    *,
    target_date: str,
) -> dict[int, float]:
    """Extract non-negative daytime PV energy for the requested local date."""
    out: dict[int, float] = {}
    if not isinstance(pv_forecast, dict) or not pv_forecast.get("enabled"):
        return out
    hourly = pv_forecast.get("hourly", [])
    if not isinstance(hourly, list):
        return out
    for row in hourly:
        if not isinstance(row, dict):
            continue
        raw_time = str(row.get("time") or "").strip()
        if not raw_time:
            continue
        try:
            timestamp = datetime.fromisoformat(raw_time)
        except ValueError:
            continue
        if timestamp.date().isoformat() != target_date or not 7 <= timestamp.hour < 23:
            continue
        total_kwh = _optional_float(row.get("total_kwh")) or 0.0
        out[timestamp.hour] = out.get(timestamp.hour, 0.0) + max(0.0, total_kwh)
    return out


def historical_hourly_profile(
    rows: list[dict[str, Any]],
    *,
    key: str,
    start_hour: int,
    end_hour_exclusive: int,
) -> dict[int, float]:
    """Average complete half-hour pairs into one hourly historical profile."""
    values_by_day_hour: dict[tuple[date, int], dict[int, float]] = {}
    for row in rows:
        timestamp = row.get("dt")
        if not isinstance(timestamp, datetime):
            continue
        if not start_hour <= timestamp.hour < end_hour_exclusive:
            continue
        if timestamp.minute not in {0, 30}:
            continue
        value = max(0.0, float(row.get(key, 0.0) or 0.0))
        values_by_day_hour.setdefault((timestamp.date(), timestamp.hour), {})[timestamp.minute] = value

    complete_values_by_hour: dict[int, list[float]] = {}
    for (_, hour), interval_values in values_by_day_hour.items():
        if set(interval_values) != {0, 30}:
            continue
        complete_values_by_hour.setdefault(hour, []).append(sum(interval_values.values()))

    return {
        hour: statistics.mean(complete_values_by_hour.get(hour, []))
        if complete_values_by_hour.get(hour)
        else 0.0
        for hour in range(start_hour, end_hour_exclusive)
    }


def normalize_profile(profile: dict[int, float], *, hours: list[int]) -> dict[int, float]:
    """Convert non-negative hourly values to weights, with a uniform zero-data fallback."""
    values = {hour: max(0.0, profile.get(hour, 0.0)) for hour in hours}
    total = sum(values.values())
    if total <= 0:
        uniform = 1.0 / len(hours) if hours else 0.0
        return {hour: uniform for hour in hours}
    return {hour: values[hour] / total for hour in hours}


def build_hourly_load_forecast(
    rows: list[dict[str, Any]],
    *,
    daytime_load_kwh: float,
    morning_load_kwh: float,
    overnight_load_by_hour: dict[int, float] | None = None,
) -> dict[int, float]:
    """Distribute daily load forecasts using historical profiles and fixed overnight data."""
    overnight_hours = [0, 1, 2, 3, 4, 5, 6, 23]
    morning_hours = [7, 8, 9]
    daytime_rest_hours = list(range(10, 23))
    early_overnight = historical_hourly_profile(rows, key="load", start_hour=0, end_hour_exclusive=7)
    late_overnight = historical_hourly_profile(rows, key="load", start_hour=23, end_hour_exclusive=24)
    morning_profile = normalize_profile(
        historical_hourly_profile(rows, key="load", start_hour=7, end_hour_exclusive=10),
        hours=morning_hours,
    )
    daytime_rest_profile = normalize_profile(
        historical_hourly_profile(rows, key="load", start_hour=10, end_hour_exclusive=23),
        hours=daytime_rest_hours,
    )

    out = {
        hour: early_overnight.get(hour, late_overnight.get(hour, 0.0))
        for hour in overnight_hours
    }
    morning_total = max(0.0, morning_load_kwh)
    daytime_rest_total = max(0.0, daytime_load_kwh - morning_total)
    out.update({hour: morning_total * morning_profile[hour] for hour in morning_hours})
    out.update({hour: daytime_rest_total * daytime_rest_profile[hour] for hour in daytime_rest_hours})
    if overnight_load_by_hour:
        for hour, value in overnight_load_by_hour.items():
            if 0 <= int(hour) <= 23:
                out[int(hour)] = max(0.0, float(value or 0.0))
    return out


def build_hourly_pv_forecast(
    rows: list[dict[str, Any]],
    *,
    pv_forecast: dict[str, object] | None,
    target_date: str,
    fallback_total_kwh: float,
) -> dict[int, float]:
    """Use array-level hourly PV when present, otherwise shape the fallback total from history."""
    from_forecast = hourly_pv_kwh_from_forecast(pv_forecast, target_date=target_date)
    if sum(max(0.0, value) for value in from_forecast.values()) > 0:
        return from_forecast
    hours = list(range(7, 23))
    profile = normalize_profile(
        historical_hourly_profile(rows, key="pv", start_hour=7, end_hour_exclusive=23),
        hours=hours,
    )
    total = max(0.0, fallback_total_kwh)
    return {hour: total * profile[hour] for hour in hours}


def reshape_hourly_pv_by_weather(
    hourly_pv_kwh: dict[int, float],
    forecast: dict[str, object],
    *,
    enabled: bool,
    blend: float,
) -> tuple[dict[int, float], dict[str, object]]:
    """Blend the PV profile toward forecast shortwave radiation without changing its total."""
    if not enabled:
        return hourly_pv_kwh, {"enabled": False, "reason": "disabled"}
    hourly_weather = forecast.get("hourly_weather")
    if not isinstance(hourly_weather, list):
        return hourly_pv_kwh, {"enabled": False, "reason": "no_hourly_weather"}

    weights: dict[int, float] = {}
    for row in hourly_weather:
        if not isinstance(row, dict):
            continue
        hour = _optional_int(row.get("hour"))
        if hour is None or not 7 <= hour < 23:
            continue
        shortwave = _optional_float(row.get("shortwave_radiation_w_m2"))
        if shortwave is not None:
            weights[hour] = max(0.0, shortwave)
    if sum(weights.values()) <= 0:
        return hourly_pv_kwh, {"enabled": False, "reason": "no_positive_shortwave"}

    original_total = sum(max(0.0, value) for value in hourly_pv_kwh.values())
    if original_total <= 0:
        return hourly_pv_kwh, {"enabled": False, "reason": "no_hourly_pv_total"}

    total_weight = sum(weights.values())
    reshaped = {hour: original_total * weights.get(hour, 0.0) / total_weight for hour in range(7, 23)}
    clamped_blend = max(0.0, min(1.0, blend))
    out = {
        hour: max(0.0, hourly_pv_kwh.get(hour, 0.0)) * (1.0 - clamped_blend)
        + reshaped[hour] * clamped_blend
        for hour in range(7, 23)
    }
    return out, {
        "enabled": True,
        "method": "blend_existing_pv_shape_with_hourly_shortwave",
        "blend": clamped_blend,
        "source": forecast.get("source"),
        "original_total_kwh": round(original_total, 4),
        "reshaped_total_kwh": round(sum(out.values()), 4),
        "shortwave_hours": sorted(weights),
    }


def _optional_float(value: object) -> float | None:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
