from __future__ import annotations

import math
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.forecasting.correction_history_io import _forecast_history_start_date
from app.parsing.numbers import to_float


ENERGY_FIELDS = {
    "pv": "pv_kwh", "load": "load_kwh", "buy": "buy_kwh",
    "sell": "sell_kwh", "charge": "charge_kwh", "discharge": "discharge_kwh",
}
STORAGE_FIELDS = ("ts", *ENERGY_FIELDS.values(), "soc_percent")


def _read_actual_samples(*, backend: str, start: str, end: str, limit: int) -> list[dict[str, Any]]:
    if backend == "firestore":
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        client = firestore.Client(
            project=os.getenv("FIRESTORE_PROJECT_ID", "").strip() or None,
            database=os.getenv("FIRESTORE_DATABASE_ID", "").strip() or "(default)",
        )
        query = (
            client.collection("monitoring_samples")
            .where(filter=FieldFilter("ts", ">=", start))
            .where(filter=FieldFilter("ts", "<", end))
            .order_by("ts").select(STORAGE_FIELDS).limit(limit + 1)
        )
        samples = [doc.to_dict() or {} for doc in query.stream()]
    elif backend == "sqlite":
        db_path = Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db"))
        if not db_path.is_file():
            return []
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            samples = [dict(row) for row in conn.execute(
                "SELECT ts, pv_kwh, load_kwh, buy_kwh, sell_kwh, charge_kwh, discharge_kwh, soc_percent "
                "FROM monitoring_samples WHERE ts >= ? AND ts < ? ORDER BY ts LIMIT ?",
                (start, end, limit + 1),
            )]
        finally:
            conn.close()
    else:
        raise ValueError("Unsupported actual-history backend")
    if len(samples) > limit:
        raise ValueError("Actual-history read limit exceeded")
    return samples


def load_historical_actual_rows(recent_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read older persisted half-hour actuals; recent CSV remains authoritative."""
    if not recent_rows:
        return []
    earliest = min(row["dt"] for row in recent_rows)
    latest = max(row["dt"] for row in recent_rows)
    start = _forecast_history_start_date(target_date=latest.date().isoformat()) + "T00:00:00"
    end = earliest.isoformat(timespec="seconds")
    if start >= end:
        return []
    backend = os.getenv("DATA_BACKEND", "sqlite").strip().lower() or "sqlite"
    # KP-NET persists one interval per 30 minutes. Bound reads to this cadence
    # and reject an incomplete history rather than quietly truncating it.
    limit = max(48, ((earliest.date() - datetime.fromisoformat(start).date()).days + 1) * 48)
    try:
        samples = _read_actual_samples(backend=backend, start=start, end=end, limit=limit)
    except Exception as exc:
        print(f"[forecast_actual_history] backend={backend} unavailable={type(exc).__name__}", flush=True)
        return []
    rows: list[dict[str, Any]] = []
    skipped = 0
    timezone_name = os.getenv("TIMEZONE", "Asia/Tokyo").strip() or "Asia/Tokyo"
    for sample in samples:
        try:
            observed = datetime.fromisoformat(str(sample["ts"]))
            if observed.tzinfo is not None:
                observed = observed.astimezone(ZoneInfo(timezone_name)).replace(tzinfo=None)
            if not datetime.fromisoformat(start) <= observed < earliest:
                raise ValueError("Actual outside requested range")
            row: dict[str, Any] = {"dt": observed}
            for name, field in ENERGY_FIELDS.items():
                value = to_float(sample.get(field))
                if value is None or not math.isfinite(value) or value < 0:
                    raise ValueError("Incomplete actual energy fields")
                row[name] = value
            soc = to_float(sample.get("soc_percent"))
            row["soc"] = soc if soc is not None and math.isfinite(soc) else float("nan")
            rows.append(row)
        except (KeyError, TypeError, ValueError):
            skipped += 1
    rows.sort(key=lambda row: row["dt"])
    print(
        f"[forecast_actual_history] backend={backend} start={start} end={end} "
        f"loaded={len(rows)} skipped={skipped}", flush=True,
    )
    return rows
