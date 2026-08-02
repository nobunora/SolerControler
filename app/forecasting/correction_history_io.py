from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


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
