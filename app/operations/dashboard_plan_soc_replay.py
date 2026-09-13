"""Evidence-only replay for missing dashboard configured-SOC history.

This module is deliberately display/data-plane only. It never talks to KP-NET and
never changes battery settings. Historical values are recovered only from retained
``forecast_plans`` evidence and only when the plan was issued no later than the
07:00 JST target-day cutoff used by the dashboard history contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.parsing.numbers import to_float

_JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class PlanSocReplayCandidate:
    date: str
    target_soc_percent: float
    night_charge_kwh: float | None
    forecast_run_id: str | None
    evidence_at: str


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_JST)
    return parsed.astimezone(_JST)


def candidate_from_forecast_plan(row: dict[str, Any]) -> PlanSocReplayCandidate | None:
    """Return a trustworthy replay candidate or fail closed.

    We require a valid date, one finite 0..100 target, and retained issuance/update
    time proving the evidence existed by 07:00 JST on the target day. This prevents
    a later rerun/reconstruction from being mistaken for the historical setting plan.
    """
    day = str(row.get("date") or "").strip()
    try:
        target_day = date.fromisoformat(day)
    except ValueError:
        return None
    target = to_float(row.get("planned_target_soc_percent"))
    if target is None or not 0.0 <= target <= 100.0:
        return None
    evidence_at = _parse_datetime(row.get("forecast_issued_at")) or _parse_datetime(
        row.get("updated_at")
    )
    if evidence_at is None:
        return None
    cutoff = datetime.combine(target_day, time(7, 0), tzinfo=_JST)
    if evidence_at > cutoff:
        return None
    night_charge = to_float(row.get("planned_night_charge_kwh"))
    if night_charge is not None and night_charge < 0.0:
        night_charge = None
    run_id = str(row.get("forecast_run_id") or "").strip() or None
    return PlanSocReplayCandidate(
        date=day,
        target_soc_percent=target,
        night_charge_kwh=night_charge,
        forecast_run_id=run_id,
        evidence_at=evidence_at.isoformat(),
    )


def build_missing_plan_soc_patch(
    existing: dict[str, Any] | None,
    candidate: PlanSocReplayCandidate,
    *,
    replayed_at: str,
) -> dict[str, Any]:
    """Build a missing-only patch. Existing applied/control evidence always wins."""
    current = existing or {}
    patch: dict[str, Any] = {}
    if to_float(current.get("setting_soc_target_percent")) is None:
        patch["setting_soc_target_percent"] = candidate.target_soc_percent
    if (
        candidate.night_charge_kwh is not None
        and to_float(current.get("night_charge_kwh")) is None
    ):
        patch["night_charge_kwh"] = candidate.night_charge_kwh
    if not patch:
        return {}
    patch.update(
        {
            "date": candidate.date,
            "plan_display_source": "forecast_plans_replay",
            "plan_replay_forecast_run_id": candidate.forecast_run_id,
            "plan_replay_evidence_at": candidate.evidence_at,
            "plan_replayed_at": replayed_at,
        }
    )
    return patch


def _date_range(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        raise ValueError("start_date must be <= end_date")
    days: list[str] = []
    cursor = start
    while cursor <= end:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def _documents_by_date(
    client: Any,
    collection_name: str,
    *,
    start_date: str,
    end_date: str,
) -> dict[str, dict[str, Any]]:
    documents = (
        client.collection(collection_name)
        .where("date", ">=", start_date)
        .where("date", "<=", end_date)
        .stream()
    )
    result: dict[str, dict[str, Any]] = {}
    for document in documents:
        row = document.to_dict() or {}
        day = str(row.get("date") or document.id or "").strip()
        if not day:
            continue
        row.setdefault("date", day)
        result[day] = row
    return result


def replay_missing_plan_soc_firestore(
    client: Any,
    *,
    start_date: str,
    end_date: str,
    apply: bool = False,
    replayed_at: str | None = None,
) -> dict[str, Any]:
    """Inventory or backfill missing configured-SOC dashboard rows.

    ``apply=False`` is a read-only dry run. ``apply=True`` writes only missing
    ``battery_daily_metrics`` fields with ``merge=True``; it never overwrites an
    existing configured SOC or night-charge value. The report distinguishes every
    missing day into recoverable and irrecoverable groups so a successful replay
    cannot silently hide historical gaps that have no trustworthy planning evidence.
    """
    days = _date_range(start_date, end_date)
    now = replayed_at or datetime.now(tz=_JST).isoformat()
    plan_by_date = _documents_by_date(
        client,
        "forecast_plans",
        start_date=start_date,
        end_date=end_date,
    )
    existing_by_date = _documents_by_date(
        client,
        "battery_daily_metrics",
        start_date=start_date,
        end_date=end_date,
    )

    candidates: dict[str, PlanSocReplayCandidate] = {}
    rejected_plan_dates: set[str] = set()
    for day, plan in plan_by_date.items():
        candidate = candidate_from_forecast_plan(plan)
        if candidate is None:
            rejected_plan_dates.add(day)
            continue
        candidates[day] = candidate

    missing_dates = [
        day
        for day in days
        if to_float(existing_by_date.get(day, {}).get("setting_soc_target_percent")) is None
    ]
    recoverable_dates = [day for day in missing_dates if day in candidates]
    irrecoverable_dates = [day for day in missing_dates if day not in candidates]

    report: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "apply": apply,
        "candidate_count": len(candidates),
        "missing_count": len(missing_dates),
        "recoverable_count": len(recoverable_dates),
        "irrecoverable_count": len(irrecoverable_dates),
        "would_write": 0,
        "written": 0,
        "skipped_untrusted": len(rejected_plan_dates),
        "skipped_existing": 0,
        "missing_dates": missing_dates,
        "recoverable_dates": recoverable_dates,
        "irrecoverable_dates": irrecoverable_dates,
        "items": [],
    }

    for day in sorted(candidates):
        candidate = candidates[day]
        existing = existing_by_date.get(day, {})
        patch = build_missing_plan_soc_patch(existing, candidate, replayed_at=now)
        item: dict[str, Any] = {
            "date": day,
            "target_soc_percent": candidate.target_soc_percent,
            "night_charge_kwh": candidate.night_charge_kwh,
            "forecast_run_id": candidate.forecast_run_id,
            "evidence_at": candidate.evidence_at,
            "action": "skip_existing" if not patch else "would_write",
        }
        if not patch:
            report["skipped_existing"] += 1
            report["items"].append(item)
            continue
        report["would_write"] += 1
        if apply:
            client.collection("battery_daily_metrics").document(day).set(patch, merge=True)
            report["written"] += 1
            item["action"] = "written"
        report["items"].append(item)

    return report
