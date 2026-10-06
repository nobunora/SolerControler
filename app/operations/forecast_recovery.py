"""Recover bounded missing original forecasts from saved contemporaneous plans."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zoneinfo import ZoneInfo

from app.backup.night_plan_archive import load_night_plan_detail_from_firestore_doc
from app.operations.forecast_persistence import _complete_hourly_rows
from app.operations.forecast_snapshot import build_forecast_snapshot_rows, persist_forecast_snapshots


def _eligible_snapshot(rows: list[dict[str, Any]], day: str) -> bool:
    if not _complete_hourly_rows(rows, target_date=day):
        return False
    issued_values = {str(row.get("issued_at") or "") for row in rows}
    if len(issued_values) != 1:
        return False
    try:
        issued = datetime.fromisoformat(next(iter(issued_values)).replace("Z", "+00:00"))
    except ValueError:
        return False
    if issued.tzinfo is None:
        return False
    return issued <= datetime.combine(date.fromisoformat(day), time(7), ZoneInfo("Asia/Tokyo"))


# HISTORICAL_FAILURE_LOCK: 2026-10-06 dedicated forecast job failed at startup.
# Recover ONLY missing original evidence, in the non-control data job, from saved
# plans with their original issued_at/run_id. Never reforecast the past, overwrite
# a valid vintage, scan unbounded history, or fetch archives from dashboard GETs.
def recover_missing_forecast_snapshots(
    client: Any, *, target_date: str, recorded_at: str, lookback_days: int = 31,
) -> dict[str, int]:
    if not 1 <= lookback_days <= 31:
        raise ValueError("forecast recovery lookback must be between 1 and 31 days")
    today = date.fromisoformat(target_date)
    days = {(today - timedelta(days=offset)).isoformat() for offset in range(1, lookback_days + 1)}
    start, end = min(days), max(days)
    original_dates: set[str] = set()
    for collection in ("forecast_hourly", "forecast_hourly_snapshots"):
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        query = client.collection(collection).where("date", ">=", start).where("date", "<=", end)
        for doc in query.stream():
            row = doc.to_dict() or {}
            day = str(row.get("date") or "")
            if day not in days:
                continue
            run_id = str(row.get("forecast_run_id") or "") if collection.endswith("snapshots") else ""
            if collection.endswith("snapshots") and not run_id:
                continue
            groups[(day, run_id)].append(row)
        for (day, _run_id), rows in groups.items():
            if collection == "forecast_hourly":
                identities = {str(row.get("forecast_run_id") or "") for row in rows}
                usable = len(identities) == 1 and _complete_hourly_rows(rows, target_date=day)
            else:
                usable = _eligible_snapshot(rows, day)
            if usable:
                original_dates.add(day)

    missing = sorted(days - original_dates)
    result = {"checked_days": lookback_days, "recovered_days": 0, "snapshot_rows": 0, "rejected_days": 0}
    if not missing:
        return result
    refs = [client.collection("night_charge_plans").document(day) for day in missing]
    for doc in client.get_all(refs):
        if not doc.exists:
            continue
        day = str(doc.id)
        if day not in missing:
            continue
        try:
            plan = load_night_plan_detail_from_firestore_doc(doc.to_dict() or {})
            if not plan:
                raise ValueError("saved plan detail unavailable")
            rows = build_forecast_snapshot_rows(plan, ingested_at=recorded_at, timezone="Asia/Tokyo")
            if not _eligible_snapshot(rows, day) or any(row.get("issued_at_source") != "plan" for row in rows):
                raise ValueError("saved plan is not complete contemporaneous forecast evidence")
            with TemporaryDirectory(prefix="solar-forecast-recovery-") as directory:
                path = Path(directory) / "plan.json"
                path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
                inserted = persist_forecast_snapshots(
                    client, backend="firestore", night_plan_path=path,
                    timezone="Asia/Tokyo", ingested_at=recorded_at,
                )
            result["snapshot_rows"] += inserted
            result["recovered_days"] += 1
            print(f"[forecast_recovery] recovered date={day} immutable_snapshot_rows={inserted}", flush=True)
        except Exception as exc:
            # Archive exceptions can contain credential-bearing storage locations.
            # Keep failures observable without emitting the exception text.
            result["rejected_days"] += 1
            print(f"[forecast_recovery] rejected date={day} error_type={type(exc).__name__}", flush=True)
    return result
