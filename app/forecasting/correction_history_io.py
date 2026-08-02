from __future__ import annotations

import math
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from app.configuration.environment import env_bool, env_float
from app.parsing.numbers import to_float, to_int


def _clip_float(value: float, *, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, value))
def _forecast_history_start_date(*, target_date: str) -> str:
    lookback_days = max(1, int(env_float("FORECAST_HOURLY_HISTORY_LOOKBACK_DAYS", default=60.0)))
    try:
        target_day = datetime.fromisoformat(target_date).date()
    except ValueError:
        return "0001-01-01"
    return (target_day - timedelta(days=lookback_days)).isoformat()


def _load_forecast_hourly_history_from_sqlite(*, target_date: str) -> dict[str, dict[int, dict[str, float]]]:
    db_path = Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db"))
    if not db_path.exists():
        return {}
    start_date = _forecast_history_start_date(target_date=target_date)
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT date, hour, forecast_pv_kwh, forecast_load_kwh, forecast_shortwave_radiation_w_m2, forecast_weather_code
                FROM forecast_hourly
                WHERE date >= ? AND date < ?
                ORDER BY date, hour
                """,
                (start_date, target_date),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return {}

    out: dict[str, dict[int, dict[str, float]]] = {}
    for row in rows:
        hour = to_int(row["hour"])
        if hour is None or hour < 0 or hour > 23:
            continue
        values = {
            "pv": max(0.0, float(row["forecast_pv_kwh"] or 0.0)),
            "load": max(0.0, float(row["forecast_load_kwh"] or 0.0)),
            "shortwave": max(0.0, float(row["forecast_shortwave_radiation_w_m2"] or 0.0)),
        }
        weather_code = to_float(row["forecast_weather_code"])
        if weather_code is not None:
            values["weather_code"] = weather_code
        out.setdefault(str(row["date"]), {})[hour] = values
    return out


def load_forecast_hourly_history_from_firestore(*, target_date: str) -> dict[str, dict[int, dict[str, float]]]:
    """Load persisted hourly forecast history for offline correction analysis."""
    backend = os.getenv("DATA_BACKEND", "").strip().lower()
    if backend != "firestore" and not os.getenv("FIRESTORE_PROJECT_ID", "").strip():
        return {}
    start_date = _forecast_history_start_date(target_date=target_date)
    try:
        from google.cloud import firestore

        project_id = os.getenv("FIRESTORE_PROJECT_ID", "").strip() or None
        database_id = os.getenv("FIRESTORE_DATABASE_ID", "(default)").strip() or "(default)"
        client = (
            firestore.Client(project=project_id, database=database_id)
            if project_id else firestore.Client(database=database_id)
        )
        docs = list(
            client.collection("forecast_hourly")
            .where("date", ">=", start_date)
            .where("date", "<", target_date)
            .stream()
        )
    except Exception:
        return {}

    out: dict[str, dict[int, dict[str, float]]] = {}
    for doc in docs:
        row = doc.to_dict() or {}
        day = str(row.get("date", "")).strip()
        hour = to_int(row.get("hour"))
        if not day or hour is None or hour < 0 or hour > 23:
            continue
        values = {
            "pv": max(0.0, to_float(row.get("forecast_pv_kwh")) or 0.0),
            "load": max(0.0, to_float(row.get("forecast_load_kwh")) or 0.0),
            "shortwave": max(0.0, to_float(row.get("forecast_shortwave_radiation_w_m2")) or 0.0),
        }
        weather_code = to_float(row.get("forecast_weather_code"))
        if weather_code is not None:
            values["weather_code"] = weather_code
        out.setdefault(day, {})[hour] = values
    return out


def _load_forecast_hourly_history(*, target_date: str) -> tuple[dict[str, dict[int, dict[str, float]]], str]:
    sqlite_history = _load_forecast_hourly_history_from_sqlite(target_date=target_date)
    if sqlite_history:
        return sqlite_history, "sqlite_forecast_hourly"
    firestore_history = load_forecast_hourly_history_from_firestore(target_date=target_date)
    if firestore_history:
        return firestore_history, "firestore_forecast_hourly"
    return {}, "unavailable"


def fetch_hourly_weather(
    *,
    lat: float,
    lon: float,
    timezone: str,
    start_date: str,
    end_date: str,
    archive: bool,
) -> dict[str, dict[int, dict[str, float]]]:
    """Fetch hourly weather data in the normalized correction input shape."""
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
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        hourly = resp.json().get("hourly", {})
    except Exception:
        return {}
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    humidity = hourly.get("relative_humidity_2m", [])
    dew_points = hourly.get("dew_point_2m", [])
    raw_winds = hourly.get("wind_speed_10m", [])
    winds = raw_winds if isinstance(raw_winds, list) and len(raw_winds) == len(times) else [0.0] * len(times)
    out: dict[str, dict[int, dict[str, float]]] = {}
    values = zip(
        times if isinstance(times, list) else [],
        temps if isinstance(temps, list) else [],
        humidity if isinstance(humidity, list) else [],
        dew_points if isinstance(dew_points, list) else [],
        winds,
    )
    for raw_time, raw_temp, raw_humidity, raw_dew_point, raw_wind in values:
        try:
            dt = datetime.fromisoformat(str(raw_time))
            temp_c = float(raw_temp)
            humidity_percent = float(raw_humidity)
            dew_point_c = float(raw_dew_point)
            wind_speed_10m = max(0.0, float(raw_wind))
        except Exception:
            continue
        out.setdefault(dt.date().isoformat(), {})[dt.hour] = {
            "temp_c": temp_c,
            "relative_humidity_percent": humidity_percent,
            "dew_point_c": dew_point_c,
            "wind_speed_10m": wind_speed_10m,
        }
    return out


def _moist_air_enthalpy(temp_c: float, relative_humidity_percent: float) -> float:
    saturation_hpa = 6.112 * math.exp((17.67 * temp_c) / (temp_c + 243.5))
    vapor_hpa = saturation_hpa * _clip_float(relative_humidity_percent, min_val=0.0, max_val=100.0) / 100.0
    humidity_ratio = 0.622 * vapor_hpa / max(1.0, 1013.25 - vapor_hpa)
    return 1.006 * temp_c + humidity_ratio * (2501.0 + 1.86 * temp_c)


def add_thermal_states(weather: dict[str, dict[int, dict[str, float]]]) -> None:
    """Add thermal-history fields required by the comfort-load correction model."""
    states = {"thermal_6h": 0.0, "thermal_24h": 0.0, "thermal_72h": 0.0, "latent_24h": 0.0, "latent_72h": 0.0}
    half_lives = {"thermal_6h": 6.0, "thermal_24h": 24.0, "thermal_72h": 72.0, "latent_24h": 24.0, "latent_72h": 72.0}
    for day in sorted(weather):
        for hour in sorted(weather[day]):
            row = weather[day][hour]
            temp_c = float(row.get("temp_c", 24.0))
            humidity = float(row.get("relative_humidity_percent", 60.0))
            dew_point = float(row.get("dew_point_c", 16.0))
            enthalpy = _moist_air_enthalpy(temp_c, humidity)
            row["enthalpy_kj_kg"] = enthalpy
            thermal_input = max(0.0, temp_c - 24.0) + 0.12 * max(0.0, enthalpy - 55.0)
            latent_input = max(0.0, dew_point - 16.0)
            for name, half_life in half_lives.items():
                alpha = 1.0 - math.exp(-math.log(2.0) / half_life)
                value = latent_input if name.startswith("latent") else thermal_input
                states[name] = alpha * value + (1.0 - alpha) * states[name]
                row[name] = states[name]


