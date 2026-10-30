"""Bounded hourly evaluation using preserved issued maxima, never refitted ones."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.forecasting.pv_ideal_max_store import IdealMaxStore


def evaluate_ideal_max_hours(store: IdealMaxStore, start: datetime, end: datetime, *, as_of: datetime,
                             issued_not_before: datetime | None = None) -> dict[str, Any]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("evaluation as_of requires a timezone")
    if any(value.minute or value.second or value.microsecond for value in (start, end)):
        raise ValueError("evaluation requires hourly boundaries")
    if issued_not_before is not None and (issued_not_before.tzinfo is None or issued_not_before.utcoffset() is None):
        raise ValueError("issue evidence boundary requires a timezone")
    records = {datetime.fromisoformat(r["interval_start"]): r for r in store.observations_between(start, end)}
    hourly: list[dict[str, Any]] = []
    cursor = start
    while cursor < end:
        record = records.get(cursor)
        row: dict[str, Any] = {"interval_start": cursor.isoformat(), "actual_kwh": None,
                               "issued_maximum_kwh": None, "excess_kwh": None}
        if cursor + timedelta(hours=1) > as_of:
            row["status"] = "FUTURE_INTERVAL"
        elif record is None or datetime.fromisoformat(record["available_at"]) > as_of:
            row["status"] = "PENDING_ACTUALS"
        else:
            row["actual_kwh"] = record["actual_kwh"]
            issued = record["issued_prediction"]
            if issued is None:
                row["status"] = "NO_PREISSUED_MAXIMUM"
            elif issued_not_before is not None and (cursor < issued_not_before
                    or datetime.fromisoformat(issued["issued_at"]) < issued_not_before):
                # Retrospective/replay ledgers can carry an assumed as_of. Do
                # not rewrite them or certify predictions issued before evidence.
                row["status"] = "UNVERIFIED_ISSUE_TIME"
            else:
                row["issued_maximum_kwh"] = issued["maximum_kwh"]
                row["excess_kwh"] = record["actual_kwh"] - issued["maximum_kwh"] if record["issued_exceeded"] else 0.0
                row["status"] = "EXCEEDED" if record["issued_exceeded"] else "WITHIN_TOLERANCE"
                row["issued_at"] = issued["issued_at"]
                row["issued_coefficient"] = issued["coefficient"]
                row["issued_revision"] = issued["revision"]
        hourly.append(row)
        cursor += timedelta(hours=1)
    evaluated = [r for r in hourly if r["issued_maximum_kwh"] is not None]
    actuals = [r for r in hourly if r["actual_kwh"] is not None]
    return {"status": "EVALUATED" if evaluated else ("NO_PREISSUED_MAXIMUM" if actuals else "PENDING_ACTUALS"),
            "as_of": as_of.isoformat(), "evaluated_hours": len(evaluated), "actual_hours": len(actuals),
            "pending_actual_hours": sum(r["status"] == "PENDING_ACTUALS" for r in hourly),
            "no_preissued_hours": sum(r["status"] == "NO_PREISSUED_MAXIMUM" for r in hourly),
            "unverified_issue_hours": sum(r["status"] == "UNVERIFIED_ISSUE_TIME" for r in hourly),
            "issued_not_before": issued_not_before.isoformat() if issued_not_before is not None else None,
            "future_hours": sum(r["status"] == "FUTURE_INTERVAL" for r in hourly),
            "exceedances": sum(r["status"] == "EXCEEDED" for r in hourly),
            "excess_kwh": sum(r["excess_kwh"] for r in evaluated), "hourly": hourly}
