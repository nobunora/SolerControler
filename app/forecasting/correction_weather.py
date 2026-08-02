"""Open-Meteo boundary for forecast-correction weather inputs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import requests


def fetch_hourly_weather(
    *,
    lat: float,
    lon: float,
    timezone: str,
    start_date: str,
    end_date: str,
    archive: bool,
    http_get: Callable[..., Any] = requests.get,
) -> dict[str, dict[int, dict[str, float]]]:
    """Fetch Open-Meteo weather in the normalized correction input shape."""
    url = "https://archive-api.open-meteo.com/v1/archive" if archive else "https://api.open-meteo.com/v1/forecast"
    params: dict[str, str | float] = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": "temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m",
        "timezone": timezone,
    }
    try:
        response = http_get(url, params=params, timeout=20)
        response.raise_for_status()
        hourly = response.json().get("hourly", {})
    except Exception:
        return {}

    times = hourly.get("time", [])
    temperatures = hourly.get("temperature_2m", [])
    humidities = hourly.get("relative_humidity_2m", [])
    dew_points = hourly.get("dew_point_2m", [])
    raw_winds = hourly.get("wind_speed_10m", [])
    winds = raw_winds if isinstance(raw_winds, list) and len(raw_winds) == len(times) else [0.0] * len(times)
    weather_by_day: dict[str, dict[int, dict[str, float]]] = {}
    for raw_time, raw_temperature, raw_humidity, raw_dew_point, raw_wind in zip(
        times if isinstance(times, list) else [],
        temperatures if isinstance(temperatures, list) else [],
        humidities if isinstance(humidities, list) else [],
        dew_points if isinstance(dew_points, list) else [],
        winds,
    ):
        try:
            timestamp = datetime.fromisoformat(str(raw_time))
            weather = {
                "temp_c": float(raw_temperature),
                "relative_humidity_percent": float(raw_humidity),
                "dew_point_c": float(raw_dew_point),
                "wind_speed_10m": max(0.0, float(raw_wind)),
            }
        except Exception:
            continue
        weather_by_day.setdefault(timestamp.date().isoformat(), {})[timestamp.hour] = weather
    return weather_by_day
