"""Pure data transformations used by forecast correction orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.parsing.numbers import to_float, to_int


def clip_float(value: float, *, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, value))


def coerce_hourly_values(value: object) -> dict[int, float]:
    if not isinstance(value, dict):
        return {}
    hourly_values: dict[int, float] = {}
    for raw_hour, raw_value in value.items():
        hour = to_int(raw_hour)
        numeric = to_float(raw_value)
        if hour is not None and 0 <= hour <= 23 and numeric is not None:
            hourly_values[hour] = max(0.0, numeric)
    return hourly_values


def ewma_ratio_from_daily_pairs(
    pairs: list[tuple[str, float, float]], *, alpha: float, initial_value: float = 1.0
) -> dict[str, object]:
    """Summarize forecast/actual ratios without letting target-day data leak in."""
    alpha = clip_float(alpha, min_val=0.0, max_val=1.0)
    current = max(0.0, initial_value)
    used: list[dict[str, float | str]] = []
    for day, forecast_total, actual_total in sorted(pairs, key=lambda item: item[0]):
        if forecast_total <= 0:
            continue
        ratio = max(0.0, actual_total / forecast_total)
        current = alpha * ratio + (1.0 - alpha) * current
        used.append({
            "date": day, "forecast_kwh": round(forecast_total, 4), "actual_kwh": round(actual_total, 4),
            "ratio": round(ratio, 4), "ewma_after_day": round(current, 4),
        })
    return {"raw_ratio": current, "sample_count": len(used), "alpha": alpha, "latest_days": used[-7:]}


def actual_hourly_totals_by_day(
    rows: list[dict[str, Any]], *, target_date: str
) -> dict[str, dict[int, dict[str, float]]]:
    by_day: dict[str, dict[int, dict[str, float]]] = {}
    for row in rows:
        observed_at = row.get("dt")
        if not isinstance(observed_at, datetime):
            continue
        day = observed_at.date().isoformat()
        if day >= target_date:
            continue
        bucket = by_day.setdefault(day, {}).setdefault(observed_at.hour, {"pv": 0.0, "load": 0.0})
        bucket["pv"] += max(0.0, to_float(row.get("pv")) or 0.0)
        bucket["load"] += max(0.0, to_float(row.get("load")) or 0.0)
    return by_day


def daily_pairs_for_ratio(
    *, forecast_history: dict[str, dict[int, dict[str, float]]],
    actual_history: dict[str, dict[int, dict[str, float]]], key: str,
) -> list[tuple[str, float, float]]:
    pairs: list[tuple[str, float, float]] = []
    for day in sorted(set(forecast_history) & set(actual_history)):
        forecast_total = sum(max(0.0, values.get(key, 0.0)) for values in forecast_history[day].values())
        actual_total = sum(max(0.0, values.get(key, 0.0)) for values in actual_history[day].values())
        if forecast_total > 0:
            pairs.append((day, forecast_total, actual_total))
    return pairs


def weather_class(weather_code: object) -> str:
    code = to_int(weather_code)
    if code is None:
        return "unknown"
    if code <= 3:
        return "clear"
    if 45 <= code <= 48:
        return "fog"
    if 51 <= code <= 67:
        return "rain"
    if 80 <= code <= 99:
        return "shower"
    return "other"
