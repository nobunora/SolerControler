"""Pure Open-Meteo weather-history normalization for the Energy Plan."""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import requests

from app.domain.weather import open_meteo_weather_class
from app.energy_plan.weather import WeatherHistoryFetchResult


def weather_class(weather_code: int | None) -> str:
    # Compatibility name; classification ownership lives in app.domain.weather.
    return open_meteo_weather_class(weather_code)


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


def weather_archive_cache_path() -> Path:
    return Path(os.getenv("WEATHER_ARCHIVE_CACHE_PATH", "artifacts/weather_archive_cache.json"))


def load_weather_archive_cache(path: Path) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    if not path.exists():
        return {}, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("rows", {}) if isinstance(payload, dict) else {}
        if not isinstance(rows, dict):
            raise ValueError("cache rows must be an object")
        return {str(day): dict(row) for day, row in rows.items() if isinstance(row, dict)}, []
    except Exception as exc:
        return {}, [{"stage": "cache_read", "exception_type": type(exc).__name__, "message": str(exc)}]


def save_weather_archive_cache(path: Path, rows_by_date: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "rows": rows_by_date}, ensure_ascii=False, indent=2), encoding="utf-8")
        return []
    except Exception as exc:
        return [{"stage": "cache_write", "exception_type": type(exc).__name__, "message": str(exc)}]


def consecutive_date_chunks(days: list[date], *, chunk_days: int) -> list[list[date]]:
    chunks: list[list[date]] = []
    for day in days:
        if not chunks or len(chunks[-1]) >= chunk_days or day != chunks[-1][-1] + timedelta(days=1):
            chunks.append([day])
        else:
            chunks[-1].append(day)
    return chunks


def weather_rows_from_daily(daily: object) -> list[dict[str, object]]:
    if not isinstance(daily, dict):
        raise ValueError("daily weather payload must be an object")
    times = daily.get("time", [])
    if not isinstance(times, list):
        raise ValueError("daily.time must be a list")
    rows: list[dict[str, object]] = []
    for index, raw_day in enumerate(times):
        try:
            date.fromisoformat(str(raw_day))
        except ValueError:
            continue
        weather_code = _optional_int(_list_value(daily.get("weather_code"), index))
        sunshine_seconds = _optional_float(_list_value(daily.get("sunshine_duration"), index))
        mean_temperature = _optional_float(_list_value(daily.get("temperature_2m_mean"), index))
        if mean_temperature is None:
            continue
        rows.append({
            "date": str(raw_day), "temp": mean_temperature,
            "weather_code": weather_code if weather_code is not None else "unknown",
            "sunshine_hours": sunshine_seconds / 3600.0 if sunshine_seconds is not None else 0.0,
            "precipitation": _optional_float(_list_value(daily.get("precipitation_sum"), index)) or 0.0,
            "shortwave_radiation_sum_mj_m2": _optional_float(_list_value(daily.get("shortwave_radiation_sum"), index)) or 0.0,
        })
    return rows


def forecast_weather_row(forecast: dict[str, object]) -> dict[str, object]:
    precipitation = _optional_float(forecast.get("precipitation_sum_mm"))
    if precipitation is None:
        # Fallback APIs can provide only probability; retain the existing weak rain signal.
        probability = _optional_float(forecast.get("precipitation_probability_mean"))
        precipitation = probability / 100.0 if probability is not None else 0.0
    return {
        "date": forecast["date"],
        "temp": _optional_float(forecast.get("temp_c")) or 0.0,
        "weather_code": forecast.get("weather_code") if forecast.get("weather_code") is not None else "unknown",
        "sunshine_hours": _optional_float(forecast.get("sun_hours")) or 0.0,
        "precipitation": precipitation,
    }


def archive_weather_history(
    rows: list[dict[str, Any]], *, lat: float, lon: float, timezone: str,
    cache_path: Path | None = None, chunk_days: int | None = None, timeout_seconds: float | None = None,
) -> WeatherHistoryFetchResult:
    if chunk_days is None:
        try:
            chunk_days = max(1, int(float(os.getenv("WEATHER_ARCHIVE_CHUNK_DAYS", "14").strip() or "14")))
        except ValueError:
            chunk_days = 14
    if timeout_seconds is None:
        try:
            timeout_seconds = max(1.0, float(os.getenv("WEATHER_ARCHIVE_TIMEOUT_SECONDS", "30").strip() or "30"))
        except ValueError:
            timeout_seconds = 30.0
    dates = sorted({row["dt"].date() for row in rows if hasattr(row.get("dt"), "date")})
    if not dates:
        return WeatherHistoryFetchResult([], [], [], [], [], [], [])
    requested_days = [dates[0] + timedelta(days=offset) for offset in range((dates[-1] - dates[0]).days + 1)]
    requested_dates = [day.isoformat() for day in requested_days]
    selected_cache_path = cache_path or weather_archive_cache_path()
    cached_rows, errors = load_weather_archive_cache(selected_cache_path)
    rows_by_date = {day: cached_rows[day] for day in requested_dates if day in cached_rows}
    cache_hit_dates = sorted(rows_by_date)
    missing_days = [day for day in requested_days if day.isoformat() not in rows_by_date]
    requested_periods: list[dict[str, object]] = []
    url = "https://archive-api.open-meteo.com/v1/archive"
    for chunk in consecutive_date_chunks(missing_days, chunk_days=chunk_days):
        params: dict[str, str | float] = {
            "latitude": lat, "longitude": lon, "start_date": chunk[0].isoformat(), "end_date": chunk[-1].isoformat(),
            "daily": "sunshine_duration,temperature_2m_mean,weather_code,precipitation_sum,shortwave_radiation_sum", "timezone": timezone,
        }
        period: dict[str, object] = {"start_date": params["start_date"], "end_date": params["end_date"], "requested_day_count": len(chunk)}
        response: object | None = None
        try:
            response = requests.get(url, params=params, timeout=timeout_seconds)
            period["http_status"] = getattr(response, "status_code", None)
            response.raise_for_status()
            payload = response.json()
            fetched_rows = weather_rows_from_daily(payload.get("daily") if isinstance(payload, dict) else None)
            allowed_dates = {day.isoformat() for day in chunk}
            for weather_row in fetched_rows:
                weather_date = str(weather_row["date"])
                if weather_date in allowed_dates:
                    rows_by_date[weather_date] = weather_row
            period["received_day_count"] = sum(1 for day in allowed_dates if day in rows_by_date)
        except Exception as exc:
            period["received_day_count"] = 0
            errors.append({"stage": "http_fetch", "start_date": str(params["start_date"]), "end_date": str(params["end_date"]), "http_status": getattr(response, "status_code", None), "exception_type": type(exc).__name__, "message": str(exc)})
        requested_periods.append(period)
    received_dates = sorted(day for day in requested_dates if day in rows_by_date)
    missing_dates = sorted(set(requested_dates) - set(received_dates))
    if received_dates and set(received_dates) != set(cache_hit_dates):
        errors.extend(save_weather_archive_cache(selected_cache_path, {**cached_rows, **rows_by_date}))
    return WeatherHistoryFetchResult([rows_by_date[day] for day in received_dates], requested_dates, received_dates, missing_dates, errors, cache_hit_dates, requested_periods)
