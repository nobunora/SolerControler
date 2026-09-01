from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


_COST_OBJECTIVE = "minimize_night_charge_cost_plus_expected_day_buy_cost_plus_expected_sell_opportunity_loss"
_LEGACY_OBJECTIVE = "avoid_daytime_buy_and_sell_then_peak_soc_near_target"
_CANDIDATE_FIELDS = (
    "target_soc_percent",
    "total_expected_cost_yen",
    "required_night_charge_kwh",
    "expected_day_buy_kwh",
    "expected_sell_kwh",
    "expected_peak_unmet_kwh",
    "expected_monthly_tier_landing_penalty_yen",
    "decision_prior_cost_yen",
    "rejection_reason",
)
_CONSTRAINT_DETAIL_FIELDS = (
    "applied",
    "reason",
    "cap_target_soc_percent",
    "guard_headroom_kwh",
    "usable_headroom_kwh",
    "guard_gain_percent",
    "expected_net_surplus_kwh",
)
_CONSTRAINT_DETAIL_KEYS = (
    "morning_pv_headroom_guard",
    "daytime_net_surplus_headroom_guard",
    "historical_daytime_soc_gain_guard",
)


@dataclass(frozen=True)
class NightPlanResult:
    target_soc_7_percent: float
    required_night_charge_kwh: float
    predicted_midday_surplus_kwh: float | None


@dataclass(frozen=True)
class NightPlan:
    forecast_date: date
    result: NightPlanResult
    should_apply: bool | None
    raw: dict[str, Any]


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _filtered_candidate(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    candidate: dict[str, Any] = {}
    for field in _CANDIDATE_FIELDS:
        field_value = value.get(field)
        if field == "rejection_reason":
            if isinstance(field_value, str):
                candidate[field] = field_value
        elif _optional_number(field_value) is not None:
            candidate[field] = _optional_number(field_value)
    return candidate or None


def _candidate_digest(
    optimization: dict[str, Any], selected: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    summaries = optimization.get("candidate_summaries")
    if not isinstance(summaries, list):
        return None, None, None
    deduped: dict[float, dict[str, Any]] = {}
    for value in summaries:
        candidate = _filtered_candidate(value)
        target = _optional_number(candidate.get("target_soc_percent") if candidate else None)
        if candidate is not None and target is not None:
            deduped.setdefault(target, candidate)
    candidate_100 = deduped.get(100.0)
    selected_target = _optional_number(selected.get("target_soc_percent") if selected else None)
    if selected_target is None:
        return candidate_100, None, None
    lower = max((target for target in deduped if target < selected_target), default=None)
    higher = min((target for target in deduped if target > selected_target), default=None)
    return candidate_100, deduped.get(lower) if lower is not None else None, deduped.get(higher) if higher is not None else None


def _constraint_details(rationale: dict[str, Any]) -> dict[str, dict[str, Any]]:
    details: dict[str, dict[str, Any]] = {}
    for key in _CONSTRAINT_DETAIL_KEYS:
        value = rationale.get(key)
        if not isinstance(value, dict):
            continue
        filtered: dict[str, Any] = {}
        for field in _CONSTRAINT_DETAIL_FIELDS:
            field_value = value.get(field)
            if field in {"applied"} and isinstance(field_value, bool):
                filtered[field] = field_value
            elif field == "reason" and isinstance(field_value, str):
                filtered[field] = field_value
            elif field not in {"applied", "reason"} and _optional_number(field_value) is not None:
                filtered[field] = _optional_number(field_value)
        if filtered:
            details[key] = filtered
    return details


def build_night_plan_provenance(raw: dict[str, Any], *, plan_sha256: str) -> dict[str, Any]:
    """Build a deterministic, secret-free diagnostic view of a loaded night plan."""
    forecast = _as_dict(raw.get("forecast"))
    result = _as_dict(raw.get("result"))
    rationale = _as_dict(raw.get("decision_rationale"))
    optimization = _as_dict(raw.get("daytime_soc_optimization"))

    result_base = _optional_number(result.get("target_soc_7_percent_base"))
    rationale_base = _optional_number(rationale.get("raw_target_soc_7_percent"))
    base = result_base if result_base is not None else rationale_base
    base_conflict = result_base is not None and rationale_base is not None and result_base != rationale_base
    final = _optional_number(result.get("target_soc_7_percent"))
    required = _optional_number(result.get("required_night_charge_kwh"))
    cost_marker = _optional_number(result.get("target_soc_7_percent_cost_optimized"))
    objective = _optional_string(optimization.get("objective"))
    rationale_objective = _optional_string(rationale.get("objective"))
    legacy_payload = optimization.get("legacy_peak_objective")
    legacy_objective = legacy_payload.get("objective") if isinstance(legacy_payload, dict) else None
    if cost_marker is not None or objective == _COST_OBJECTIVE:
        optimizer_kind = "cost"
    elif result_base is not None and (objective == _LEGACY_OBJECTIVE or legacy_objective == _LEGACY_OBJECTIVE):
        optimizer_kind = "legacy"
    elif result_base is None and final is not None and not optimization:
        optimizer_kind = "none"
    else:
        optimizer_kind = "unknown"

    selected = _filtered_candidate(optimization.get("selected_candidate"))
    candidate_100, lower, higher = _candidate_digest(optimization, selected)
    active_constraints = rationale.get("active_constraints")
    if not isinstance(active_constraints, list):
        active_constraints = []
    active_constraints = [value for value in active_constraints if isinstance(value, str)]
    max_target = _optional_number(optimization.get("max_target_soc_percent_after_guards"))
    if max_target is None or not 0.0 <= max_target <= 100.0:
        max_target = None
    forecast_date = _optional_string(forecast.get("date"))
    if base_conflict:
        provenance_status = "conflict"
    elif forecast_date is not None and final is not None and bool(plan_sha256) and optimizer_kind != "unknown":
        provenance_status = "complete"
    else:
        provenance_status = "partial"
    payload: dict[str, Any] = {
        "schema_version": 1,
        "forecast_date": forecast_date,
        "plan_sha256": plan_sha256 or None,
        "base_target_soc_7_percent": base,
        "final_target_soc_7_percent": final,
        "required_night_charge_kwh": required,
        "optimizer_kind": optimizer_kind,
        "optimizer_objective": objective or rationale_objective,
        "max_target_soc_percent_after_guards": max_target,
        "active_constraints": active_constraints,
        "selected_candidate": selected,
        "candidate_100_percent": candidate_100,
        "nearest_lower_candidate": lower,
        "nearest_higher_candidate": higher,
        "provenance_status": provenance_status,
    }
    details = _constraint_details(rationale)
    if details:
        payload["constraint_details"] = details
    return payload


def _finite_float(source: dict[str, Any], key: str, *, required: bool) -> float | None:
    if key not in source:
        if required:
            raise ValueError(f"night plan is missing result.{key}")
        return None
    try:
        value = float(source[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"night plan result.{key} must be numeric") from exc
    if not math.isfinite(value):
        raise ValueError(f"night plan result.{key} must be finite")
    return value


def parse_night_plan(raw: dict[str, Any]) -> NightPlan:
    forecast = raw.get("forecast")
    result = raw.get("result")
    quality = raw.get("plan_quality")
    if not isinstance(forecast, dict) or not isinstance(result, dict):
        raise ValueError("night plan forecast and result must be objects")
    try:
        forecast_date = date.fromisoformat(str(forecast["date"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("night plan forecast.date must be YYYY-MM-DD") from exc
    target = _finite_float(result, "target_soc_7_percent", required=True)
    required = _finite_float(result, "required_night_charge_kwh", required=True)
    surplus = _finite_float(result, "predicted_midday_surplus_kwh", required=False)
    assert target is not None and required is not None
    if not 0.0 <= target <= 100.0 or required < 0.0:
        raise ValueError("night plan target SOC or required charge is out of range")
    should_apply = quality.get("should_apply") if isinstance(quality, dict) else None
    if should_apply is not None and not isinstance(should_apply, bool):
        raise ValueError("night plan plan_quality.should_apply must be boolean")
    return NightPlan(
        forecast_date=forecast_date,
        result=NightPlanResult(target, required, surplus),
        should_apply=should_apply,
        raw=raw,
    )


def read_night_plan(path: Path) -> NightPlan:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("night plan root must be an object")
    return parse_night_plan(raw)

