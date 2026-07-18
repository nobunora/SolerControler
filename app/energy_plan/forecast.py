from __future__ import annotations


def coerce_hourly_energy(value: object) -> dict[int, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[int, float] = {}
    for raw_hour, raw_value in value.items():
        try:
            hour = int(raw_hour)
            numeric = float(raw_value)
        except (TypeError, ValueError):
            continue
        if 0 <= hour <= 23:
            result[hour] = max(0.0, numeric)
    return result


def summarize_hourly_pv(hourly_pv_kwh: dict[int, float]) -> dict[str, float]:
    total = 0.0
    morning = 0.0
    midday = 0.0
    evening = 0.0
    peak = 0.0
    for hour, value in hourly_pv_kwh.items():
        pv = max(0.0, float(value or 0.0))
        total += pv
        peak = max(peak, pv)
        if 7 <= hour < 10:
            morning += pv
        elif 10 <= hour < 16:
            midday += pv
        elif 16 <= hour < 23:
            evening += pv
    return {
        "total_kwh": round(total, 4),
        "morning_kwh": round(morning, 4),
        "midday_kwh": round(midday, 4),
        "evening_kwh": round(evening, 4),
        "peak_kw": round(peak, 4),
    }


def estimate_sunset_hour(hourly_pv_kwh: dict[int, float]) -> int:
    active_hours = [
        hour for hour, value in hourly_pv_kwh.items() if 7 <= hour < 23 and value > 0.03
    ]
    return max(active_hours) if active_hours else 18
