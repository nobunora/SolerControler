"""Evidence-only replay for missing dashboard configured-SOC history.

This module is deliberately display/data-plane only.  It never talks to KP-NET and
never changes battery settings.  Historical values are recovered only from retained
``forecast_plans`` evidence and only when the plan was issued no later than the
07:00 JST target-day cutoff used by the dashboard history contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
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
    time proving the evidence existed by 07:00 JST on the target day.  This prevents
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


def replay_missing_plan_soc_firestore(
    client: Any,
    *,
    start_date: str,
    end_date: str,
    apply: bool = False,
    replayed_at: str | None = None,
) -> dict[str, Any]:
    """Inventory or backfill missing configured-SOC dashboard rows.

    ``apply=False`` is a read-only dry run.  ``apply=True`` writes only missing
    ``battery_daily_metrics`` fields with ``merge=True``; it never overwrites an
    existing configured SOC or night-charge value.
    """
    if date.fromisoformat(start_date) > date.fromisoformat(end_date):
        raise ValueError("start_date must be <= end_date")
    now = replayed_at or datetime.now(tz=_JST).isoformat()
    plan_docs = (
        client.collection("forecast_plans")
        .where("date", ">=", start_date)
        .where("date", "<=", end_date)
        .stream()
    )
    report: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "apply": apply,
        "candidates": 0,
        "would_write": 0,
        "written": 0,
        "skipped_untrusted": 0,
        "skipped_existing": 0,
        "dates": [],
    }
    for plan_doc in plan_docs:
        plan = plan_doc.to_dict() or {}
        if not plan.get("date"):
            plan["date"] = plan_doc.id
        candidate = candidate_from_forecast_plan(plan)
        if candidate is None:
            report["skipped_untrusted"] += 1
            continue
        report["candidates"] += 1
        target_ref = client.collection("battery_daily_metrics").document(candidate.date)
        snapshot = target_ref.get()
        existing = snapshot.to_dict() or {} if snapshot.exists else {}
        patch = build_missing_plan_soc_patch(existing, candidate, replayed_at=now)
        if not patch:
            report["skipped_existing"] += 1
            continue
        report["would_write"] += 1
        report["dates"].append(candidate.date)
        if apply:
            target_ref.set(patch, merge=True)
            report["written"] += 1
    return report
