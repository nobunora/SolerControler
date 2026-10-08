from __future__ import annotations

import os
import sqlite3
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


from app.configuration.environment import env_float
from app.parsing.numbers import to_float, to_int


def _history_timestamp(value: object) -> datetime | None:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def _complete_calibration_day(rows: list[dict[str, Any]], day: str) -> bool:
    if len(rows) != 24 or {to_int(row.get("hour")) for row in rows} != set(range(24)):
        return False
    for row in rows:
        if row.get("date") != day:
            return False
        for key in ("forecast_pv_kwh", "forecast_shortwave_radiation_w_m2"):
            value = to_float(row.get(key))
            if value is None or not math.isfinite(value) or value < 0:
                return False
    return True


def _available_calibration_day(rows: list[dict[str, Any]], day: str, cutoff: datetime) -> bool:
    return _complete_calibration_day(rows, day) and all(
        (stamp := _history_timestamp(row.get("updated_at"))) is not None and stamp <= cutoff
        for row in rows
    )


def _select_calibration_history(
    canonical: list[dict[str, Any]], snapshots: list[dict[str, Any]], *, target_date: str,
    as_of: datetime,
) -> dict[str, dict[int, dict[str, float]]]:
    # CALIBRATION_HISTORY_CONTRACT: preserve complete canonical days; fill only
    # gaps from one complete original early run. Never stitch runs, invent zeros,
    # rewrite saved forecasts, or use a snapshot recorded after this prediction.
    days: dict[str, list[dict[str, Any]]] = {}
    runs: dict[str, dict[tuple[str, str], list[dict[str, Any]]]] = {}
    start_date = _forecast_history_start_date(target_date=target_date)
    for row in canonical:
        day = str(row.get("date", ""))
        if start_date <= day < target_date:
            days.setdefault(day, []).append(row)
    for row in snapshots:
        day = str(row.get("date", ""))
        if start_date <= day < target_date:
            key = (str(row.get("issued_at", "")), str(row.get("forecast_run_id", "")))
            runs.setdefault(day, {}).setdefault(key, []).append(row)
    out: dict[str, dict[int, dict[str, float]]] = {}
    for day in sorted(days.keys() | runs.keys()):
        selected = days.get(day, [])
        if not _available_calibration_day(selected, day, as_of):
            selected = []
            try:
                early_limit = datetime.fromisoformat(day).replace(hour=7, tzinfo=ZoneInfo("Asia/Tokyo"))
            except ValueError:
                continue
            candidates = sorted(runs.get(day, {}).values(), key=lambda rows:
                                _history_timestamp(rows[0].get("issued_at")) or datetime.max.replace(tzinfo=timezone.utc))
            for candidate in candidates:
                issued = _history_timestamp(candidate[0].get("issued_at"))
                if (issued is not None and issued <= min(early_limit, as_of)
                        and _complete_calibration_day(candidate, day) and all(
                            (stamp := _history_timestamp(row.get("recorded_at"))) is not None and stamp <= as_of
                            for row in candidate
                        )):
                    selected = candidate
                    print(f"[forecast-history] calibration snapshot fallback date={day} hours=24")
                    break
        if not selected:
            continue
        for row in selected:
            values = {
                "pv": float(row["forecast_pv_kwh"]),
                "load": max(0.0, to_float(row.get("forecast_load_kwh")) or 0.0),
                "shortwave": float(row["forecast_shortwave_radiation_w_m2"]),
            }
            weather_code = to_float(row.get("forecast_weather_code"))
            if weather_code is not None:
                values["weather_code"] = weather_code
            out.setdefault(day, {})[int(row["hour"])] = values
    return out


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
                SELECT *
                FROM forecast_hourly
                WHERE date >= ? AND date < ?
                ORDER BY date, hour
                """,
                (start_date, target_date),
            ).fetchall()
            try:
                snapshots = conn.execute(
                    "SELECT * FROM forecast_hourly_snapshots WHERE date >= ? AND date < ?",
                    (start_date, target_date),
                ).fetchall()
            except sqlite3.OperationalError:
                snapshots = []  # Older local databases have no snapshot table.
        finally:
            conn.close()
    except Exception:
        return {}

    return _select_calibration_history(
        [dict(row) for row in rows], [dict(row) for row in snapshots],
        target_date=target_date, as_of=datetime.now(timezone.utc),
    )


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
        canonical = [doc.to_dict() or {} for doc in docs]
        by_day: dict[str, list[dict[str, Any]]] = {}
        for row in canonical:
            by_day.setdefault(str(row.get("date", "")), []).append(row)
        cutoff = datetime.now(timezone.utc)
        missing = []
        cursor = datetime.fromisoformat(start_date)
        while cursor.date().isoformat() < target_date:
            day = cursor.date().isoformat()
            if not _available_calibration_day(by_day.get(day, []), day, cutoff):
                missing.append(day)
            cursor += timedelta(days=1)
        snapshots: list[dict[str, Any]] = []
        # Read only dates with gaps; never scan the unbounded snapshot archive.
        for offset in range(0, len(missing), 30):
            snapshots.extend(doc.to_dict() or {} for doc in
                             client.collection("forecast_hourly_snapshots")
                             .where("date", "in", missing[offset:offset + 30]).stream())
    except Exception:
        return {}

    return _select_calibration_history(canonical, snapshots, target_date=target_date, as_of=cutoff)


def _load_forecast_hourly_history(*, target_date: str) -> tuple[dict[str, dict[int, dict[str, float]]], str]:
    sqlite_history = _load_forecast_hourly_history_from_sqlite(target_date=target_date)
    if sqlite_history:
        return sqlite_history, "sqlite_forecast_hourly"
    firestore_history = load_forecast_hourly_history_from_firestore(target_date=target_date)
    if firestore_history:
        return firestore_history, "firestore_forecast_hourly"
    return {}, "unavailable"
