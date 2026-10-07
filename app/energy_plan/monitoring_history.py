"""Observed forecast days and billing history, independent of CSV month boundaries."""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.energy_plan.monthly_projection import (
    _clock_minutes,
    _is_within_window,
    _parse_hhmm,
    billing_period_for_target,
    previous_billing_period_for_target,
)
from app.parsing.numbers import to_float

FORECAST_HISTORY_DAYS = 7
HISTORY_PAGE_SAMPLES = 48 * 8
MAX_HISTORY_SAMPLES = 48 * 180
MONITORING_FIELDS = {
    "pv": "pv_kwh", "load": "load_kwh", "buy": "buy_kwh", "sell": "sell_kwh",
    "charge": "charge_kwh", "discharge": "discharge_kwh", "soc": "soc_percent",
}


def recent_forecast_rows(rows: list[dict[str, Any]], *, target_date: str) -> list[dict[str, Any]]:
    # HISTORICAL_FAILURE_LOCK: forecast history is seven observed complete days,
    # not seven calendar days or the newest CSV month. Missing telemetry is not zero.
    target = date.fromisoformat(target_date)
    by_day: dict[date, dict[datetime, dict[str, Any]]] = {}
    for row in rows:
        timestamp = row.get("dt")
        if isinstance(timestamp, datetime) and timestamp.date() < target:
            by_day.setdefault(timestamp.date(), {})[timestamp] = row
    selected: list[date] = []
    for day in sorted(by_day, reverse=True):
        samples = by_day[day]
        expected = {datetime.combine(day, datetime.min.time()) + timedelta(minutes=30 * slot) for slot in range(48)}
        if set(samples) != expected:
            continue
        if any(to_float(row.get(key)) is None or float(row[key]) < 0 for row in samples.values() for key in ("pv", "load")):
            continue
        selected.append(day)
        if len(selected) == FORECAST_HISTORY_DAYS:
            break
    return [by_day[day][timestamp] for day in sorted(selected) for timestamp in sorted(by_day[day])]


def _monitoring_row(raw: dict[str, Any]) -> dict[str, Any]:
    timestamp = datetime.fromisoformat(str(raw["ts"]))
    return {"dt": timestamp, **{key: to_float(raw.get(field)) for key, field in MONITORING_FIELDS.items()}}


def merge_monitoring_rows(stored: list[dict[str, Any]], fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_timestamp = {row["dt"]: row for row in stored}
    by_timestamp.update({row["dt"]: row for row in fresh})
    return [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]


def load_monitoring_history(*, target_date: str) -> list[dict[str, Any]]:
    """Read billing periods plus bounded pages until seven observed days exist."""
    target = date.fromisoformat(target_date)
    previous_start, _, _ = previous_billing_period_for_target(target)
    start_ts, end_ts = f"{previous_start}T00:00:00", f"{target_date}T00:00:00"
    rows: list[dict[str, Any]] = []
    if os.getenv("DATA_BACKEND", "").strip().lower() == "firestore":
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        client = firestore.Client(
            project=os.getenv("FIRESTORE_PROJECT_ID") or None,
            database=os.getenv("FIRESTORE_DATABASE_ID", "(default)"),
        )
        collection = client.collection("monitoring_samples")
        billing_query = collection.where(filter=FieldFilter("ts", ">=", start_ts)).where(filter=FieldFilter("ts", "<", end_ts)).order_by("ts")
        rows = [_monitoring_row(doc.to_dict() or {}) for doc in billing_query.stream()]
        before = start_ts
        scanned = 0
        while len(recent_forecast_rows(rows, target_date=target_date)) < 48 * FORECAST_HISTORY_DAYS and scanned < MAX_HISTORY_SAMPLES:
            query = collection.where(filter=FieldFilter("ts", "<", before)).order_by("ts", direction=firestore.Query.DESCENDING).limit(HISTORY_PAGE_SAMPLES)
            page = [_monitoring_row(doc.to_dict() or {}) for doc in query.stream()]
            if not page:
                break
            rows.extend(page)
            scanned += len(page)
            before = min(row["dt"] for row in page).isoformat()
        print(f"[forecast_history] stored_rows={len(rows)} older_samples={scanned}", flush=True)
        return sorted(rows, key=lambda row: row["dt"])
    db_path = Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db"))
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        table_exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='monitoring_samples'").fetchone()
        if not table_exists:
            return []
        raw_rows = connection.execute("SELECT * FROM monitoring_samples WHERE ts >= ? AND ts < ? ORDER BY ts", (start_ts, end_ts)).fetchall()
        rows = [_monitoring_row(dict(row)) for row in raw_rows]
        if len(recent_forecast_rows(rows, target_date=target_date)) < 48 * FORECAST_HISTORY_DAYS:
            older = connection.execute("SELECT * FROM monitoring_samples WHERE ts < ? ORDER BY ts DESC LIMIT ?", (start_ts, MAX_HISTORY_SAMPLES)).fetchall()
            rows.extend(_monitoring_row(dict(row)) for row in older)
    return sorted(rows, key=lambda row: row["dt"])


def validate_current_billing_history(rows: list[dict[str, Any]], *, target_date: str) -> None:
    # HISTORICAL_FAILURE_LOCK: a new CSV month must not reset billing-period buy.
    # Missing prior-month days must stop the plan instead of inventing cheap tariffs.
    target = date.fromisoformat(target_date)
    start, _, _ = billing_period_for_target(target)
    day_start = _clock_minutes(_parse_hhmm(os.getenv("NIGHT8_DAY_START_HHMM", "07:00"), default="07:00"))
    day_end = _clock_minutes(_parse_hhmm(os.getenv("NIGHT8_DAY_END_HHMM", "23:00"), default="23:00"))
    observed = set()
    for row in rows:
        timestamp = row.get("dt")
        if isinstance(timestamp, datetime) and start <= timestamp.date() < target and _is_within_window(
            timestamp.hour * 60 + timestamp.minute, start_minute=day_start, end_minute=day_end,
        ):
            buy = to_float(row.get("buy"))
            if buy is not None and buy >= 0:
                observed.add(timestamp.date())
    missing = [(start + timedelta(days=offset)).isoformat() for offset in range((target - start).days) if start + timedelta(days=offset) not in observed]
    if missing:
        raise RuntimeError(f"billing-period monitoring history is missing days: {','.join(missing)}")
