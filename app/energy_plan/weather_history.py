"""Pure Open-Meteo weather-history normalization for the Energy Plan."""
from __future__ import annotations

from typing import Any, cast


def weather_class(weather_code: int | None) -> str:
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


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    value_as_float = _optional_float(value)
    return int(value_as_float) if value_as_float is not None else None


def _list_value(values: object, index: int) -> object | None:
    if not isinstance(values, list) or index >= len(values):
        return None
    return cast(object, values[index])


def hourly_weather_summary(
    hourly_weather: list[dict[str, object]],
    *,
    rain_probability_threshold: float,
    rain_mm_threshold: float,
    low_shortwave_threshold: float,
) -> dict[str, object]:
    daytime = [row for row in hourly_weather if 7 <= (_optional_int(row.get("hour")) or -1) < 18]
    solar = [row for row in hourly_weather if 9 <= (_optional_int(row.get("hour")) or -1) < 16]
    rain_hours = 0
    low_shortwave_hours = 0
    shortwave_sum = 0.0
    weather_codes: list[int] = []
    temp_values: list[float] = []
    for row in daytime:
        code = _optional_int(row.get("weather_code"))
        if code is not None:
            weather_codes.append(code)
        temp = _optional_float(row.get("temp_c"))
        if temp is not None:
            temp_values.append(temp)
        precipitation = _optional_float(row.get("precipitation_mm")) or 0.0
        probability = _optional_float(row.get("precipitation_probability"))
        if weather_class(code) in {"rain", "storm"} or precipitation >= rain_mm_threshold or (
            probability is not None and probability >= rain_probability_threshold
        ):
            rain_hours += 1
    for row in solar:
        shortwave = _optional_float(row.get("shortwave_radiation_w_m2")) or 0.0
        shortwave_sum += shortwave
        if shortwave <= low_shortwave_threshold:
            low_shortwave_hours += 1
    dominant_code = max(set(weather_codes), key=weather_codes.count) if weather_codes else None
    return {
        "daytime_hour_count": len(daytime), "solar_hour_count": len(solar),
        "rain_hours_7_17": rain_hours, "low_shortwave_hours_9_15": low_shortwave_hours,
        "shortwave_sum_9_15_wh_m2": round(shortwave_sum, 3),
        "dominant_weather_code_7_17": dominant_code,
        "dominant_weather_class_7_17": weather_class(dominant_code),
        "mean_temp_c_7_17": round(sum(temp_values) / len(temp_values), 3) if temp_values else None,
    }


def hourly_weather_records_from_open_meteo(
    hourly: dict[str, object], *, target_date: str, suffix: str = ""
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    times = hourly.get("time", [])
    if not isinstance(times, list):
        return records
    for index, raw_time in enumerate(times):
        time_text = str(raw_time)
        if not time_text.startswith(f"{target_date}T"):
            continue
        try:
            hour = int(time_text.split("T", 1)[1].split(":", 1)[0])
        except (IndexError, ValueError):
            continue
        weather_code = _optional_int(_list_value(hourly.get(f"weather_code{suffix}"), index))
        records.append({
            "time": time_text, "hour": hour, "weather_code": weather_code,
            "weather_class": weather_class(weather_code),
            "precipitation_mm": _optional_float(_list_value(hourly.get(f"precipitation{suffix}"), index)),
            "precipitation_probability": _optional_float(_list_value(hourly.get(f"precipitation_probability{suffix}"), index)),
            "cloud_cover": _optional_float(_list_value(hourly.get(f"cloud_cover{suffix}"), index)),
            "shortwave_radiation_w_m2": _optional_float(_list_value(hourly.get(f"shortwave_radiation{suffix}"), index)),
            "temp_c": _optional_float(_list_value(hourly.get(f"temperature_2m{suffix}"), index)),
            "relative_humidity_percent": _optional_float(_list_value(hourly.get(f"relative_humidity_2m{suffix}"), index)),
            "dew_point_c": _optional_float(_list_value(hourly.get(f"dew_point_2m{suffix}"), index)),
            "wind_speed_10m": _optional_float(_list_value(hourly.get(f"wind_speed_10m{suffix}"), index)),
        })
    return records
