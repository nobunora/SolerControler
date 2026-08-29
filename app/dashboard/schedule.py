from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.parsing.numbers import to_float
from app.runtime.night_soc_time_contract import FORCED_MONITOR_CUTOFF


SETTINGS_COMPLETED_STATUSES = {"applied", "skipped-no-change", "skipped-no-charge"}


def _read_operation_conditions_config() -> dict[str, Any]:
    default = {"priority_order": ["fixed", "variable"], "fixed": [], "variable": []}
    path = Path(os.getenv("KP_OPERATION_CONDITIONS_PATH", "config/operation_conditions.json"))
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    if not isinstance(data, dict):
        return default
    return {
        "priority_order": data.get("priority_order") if isinstance(data.get("priority_order"), list) else default["priority_order"],
        "fixed": data.get("fixed") if isinstance(data.get("fixed"), list) else default["fixed"],
        "variable": data.get("variable") if isinstance(data.get("variable"), list) else default["variable"],
    }


def _find_variable_condition_value(conditions: dict[str, Any], *, target_id: str, default: str) -> str:
    for item in conditions.get("variable", []):
        if not isinstance(item, dict) or str(item.get("id") or "") != target_id:
            continue
        value = str(item.get("value") or "").strip()
        if value:
            return value
    return default


def _parse_hhmm_minutes(raw: str | None) -> int | None:
    try:
        hour_text, minute_text = str(raw or "").split(":", maxsplit=1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError):
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour * 60 + minute


def _minutes_to_hhmm(minutes: int) -> str:
    normalized = max(0, min(23 * 60 + 59, minutes))
    return f"{normalized // 60:02d}:{normalized % 60:02d}"


def _json_object_or_empty(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _default_latest_schedule(plan_date: str | None = None) -> dict[str, Any]:
    conditions = _read_operation_conditions_config()
    day_discharge_start = os.getenv("KP_DAY_DISCHARGE_WINDOW_START", "07:00").strip() or "07:00"
    day_discharge_end = os.getenv("KP_DAY_DISCHARGE_WINDOW_END", "23:00").strip() or "23:00"
    night_window_start = os.getenv("KP_NIGHT_CHARGE_WINDOW_START", "23:00").strip() or "23:00"
    night_window_end = os.getenv("KP_NIGHT_CHARGE_WINDOW_END", "07:00").strip() or "07:00"
    charge_end_time = FORCED_MONITOR_CUTOFF.strftime("%H:%M")
    return {
        "plan_date": plan_date, "charge_start_time": None, "charge_end_time": charge_end_time,
        "night_window_start": night_window_start, "night_window_end": night_window_end,
        "day_discharge_window_start": day_discharge_start, "day_discharge_window_end": day_discharge_end,
        "discharge_fixed_window": f"{day_discharge_start}-{day_discharge_end}",
        "soc_safety_mode": None, "soc_economy_mode": "0", "soc_charge_mode": None,
        "mode": "green", "battery_operating_mode": "green",
        "estimated_charge_power_kw": float(os.getenv("KP_DEFAULT_CHARGE_POWER_KW", "1.8") or "1.8"),
        "status": "fallback-default", "recorded_at": None, "settings_completed": False,
        "settings_completed_status": None, "settings_completed_at": None,
        "settings_completed_profile": None, "settings_completed_run_id": None,
        "settings_completed_source_doc_id": None, "constraints": conditions,
    }


def _schedule_event_priority(candidate: tuple[dict[str, Any], dict[str, Any]]) -> int:
    source = str(candidate[1].get("schedule_source") or "")
    if source == "03-monitor":
        return 0
    if source == "03-no-charge":
        return 1
    return 2


def _event_recency_key(row: dict[str, Any]) -> tuple[int, float, str]:
    recorded_at = row.get("recorded_at")
    try:
        parsed = recorded_at if isinstance(recorded_at, datetime) else datetime.fromisoformat(str(recorded_at or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        timestamp = parsed.timestamp()
        valid = 1
    except (TypeError, ValueError, OverflowError):
        timestamp = float("-inf")
        valid = 0
    stable_id = str(row.get("event_id") or row.get("source_doc_id") or row.get("run_id") or "")
    return valid, timestamp, stable_id


def _schedule_event_candidates(event_rows: list[dict[str, Any]], plan_date: str | None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in event_rows:
        detail = _json_object_or_empty(row.get("detail_json"))
        if not detail:
            continue
        detail_plan_date = str(detail.get("plan_date") or "").strip()
        if plan_date and not detail_plan_date:
            continue
        if plan_date and detail_plan_date and detail_plan_date != plan_date:
            continue
        candidates.append((row, detail))
    return candidates


def _select_schedule_event(event_rows: list[dict[str, Any]], plan_date: str | None) -> tuple[dict[str, Any], dict[str, Any]] | None:
    candidates = _schedule_event_candidates(event_rows, plan_date)
    best_priority = min((_schedule_event_priority(candidate) for candidate in candidates), default=None)
    if best_priority is None:
        return None
    matching_candidates = [
        candidate for candidate in candidates if _schedule_event_priority(candidate) == best_priority
    ]
    if not matching_candidates:
        return None
    return max(matching_candidates, key=lambda candidate: _event_recency_key(candidate[0]))


# readable-code-audit: skip STRUCT-04 — event selection, same-run completion, and battery provenance must be assembled into one internally consistent display schedule
def _build_latest_schedule_from_events(*, event_rows: list[dict[str, Any]], battery_row: dict[str, Any] | None, plan_date: str | None) -> dict[str, Any]:
    schedule = _default_latest_schedule(plan_date=plan_date)
    candidates = _schedule_event_candidates(event_rows, plan_date)
    completed_row: dict[str, Any] | None = None
    schedule_row = _select_schedule_event(event_rows, plan_date)
    if schedule_row is not None:
        chosen_row, chosen_detail = schedule_row
        for key in ("charge_start_time", "charge_end_time", "night_window_start", "night_window_end", "day_discharge_window_start", "day_discharge_window_end", "discharge_fixed_window", "soc_safety_mode", "soc_economy_mode", "soc_charge_mode", "mode", "battery_operating_mode", "estimated_charge_power_kw", "schedule_source", "estimated_charge_minutes", "estimated_charge_rate_percent_per_hour", "charge_rate_source", "charge_rate_sample_count", "required_charge_percent_at_schedule"):
            value = chosen_detail.get(key)
            if value is not None and value != "":
                schedule[key] = value
        schedule["status"] = str(chosen_row.get("status", "from-settings-events"))
        schedule["recorded_at"] = str(chosen_row.get("recorded_at", ""))
        schedule["slot"] = str(chosen_row.get("slot", ""))
        schedule["profile"] = str(chosen_row.get("profile", ""))
        chosen_run_id = str(chosen_row.get("run_id") or "")
        if chosen_run_id:
            completed_row = max((row for row, _detail in candidates if str(row.get("run_id") or "") == chosen_run_id and str(row.get("status") or "") in SETTINGS_COMPLETED_STATUSES), key=_event_recency_key, default=None)
    if completed_row is not None:
        schedule["settings_completed"] = True
        schedule["settings_completed_status"] = str(completed_row.get("status", ""))
        schedule["settings_completed_at"] = str(completed_row.get("recorded_at", ""))
        schedule["settings_completed_profile"] = str(completed_row.get("profile", ""))
        schedule["settings_completed_run_id"] = str(completed_row.get("run_id", ""))
        schedule["settings_completed_source_doc_id"] = str(completed_row.get("source_doc_id", ""))
    if battery_row:
        battery_date = str(battery_row.get("date") or "")
        battery_matches_plan = not plan_date or battery_date == plan_date
        target_soc = to_float(battery_row.get("setting_soc_target_percent")) if battery_matches_plan else None
        if target_soc is not None:
            schedule["soc_charge_mode"] = str(int(round(target_soc)))
        if schedule.get("plan_date") is None and battery_date:
            schedule["plan_date"] = str(battery_row.get("date"))
        source_status = str(battery_row.get("source_status") or "")
        if not schedule.get("settings_completed") and source_status in SETTINGS_COMPLETED_STATUSES and battery_matches_plan:
            schedule["settings_completed"] = True
            schedule["settings_completed_status"] = source_status
            schedule["settings_completed_at"] = str(battery_row.get("updated_at") or "")
            schedule["settings_completed_profile"] = str(battery_row.get("source_profile") or "")
            schedule["settings_completed_run_id"] = str(battery_row.get("settings_run_id") or "")
            schedule["settings_completed_source_doc_id"] = str(battery_row.get("source_doc_id") or "")
    charge_start = _parse_hhmm_minutes(str(schedule.get("charge_start_time") or ""))
    charge_end = _parse_hhmm_minutes(str(schedule.get("charge_end_time") or ""))
    power_kw = to_float(schedule.get("estimated_charge_power_kw")) or 1.8
    battery_date = str(battery_row.get("date") if battery_row else "")
    battery_matches_plan = bool(battery_row) and (not plan_date or battery_date == plan_date)
    matching_battery_row = battery_row if battery_matches_plan else None
    night_kwh = to_float(matching_battery_row.get("night_charge_kwh") if matching_battery_row is not None else None) or 0.0
    if charge_start is None and charge_end is not None and night_kwh > 0 and power_kw > 0:
        duration_minutes = max(30, int(math.ceil((night_kwh / power_kw) * 60.0)))
        schedule["charge_start_time"] = _minutes_to_hhmm(max(0, charge_end - duration_minutes))
        schedule["status"] = "estimated-from-night-kwh"
    return schedule
