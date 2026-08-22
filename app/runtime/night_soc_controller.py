"""Pure decisions for the night SOC single-owner controller.

This module deliberately contains no network or Firestore I/O.  The Cloud Job
and KP-NET adapters use these values to keep the continuous planning target
separate from the device's discrete SOC candidate code.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, cast


CONTROLLED_SETTING_FIELDS: tuple[str, ...] = (
    "batteryOperatingMode",
    "socSafetyMode",
    "socEconomyMode",
    "socContactInput",
    "socChargeMode",
    "chargeStartTimeH",
    "chargeStartTimeM",
    "chargeEndTimeH",
    "chargeEndTimeM",
    "dischargeStartTimeH",
    "dischargeStartTimeM",
    "dischargeEndTimeH",
    "dischargeEndTimeM",
)


@dataclass(frozen=True)
class NightPlanSnapshot:
    plan_id: str
    plan_date: str
    revision: str
    content_hash: str
    raw_target_soc_percent: float
    required_night_charge_kwh: float
    effective_capacity_kwh: float | None
    generated_at_utc: str


@dataclass(frozen=True)
class DeviceSocGuard:
    raw_target_soc_percent: float
    device_soc_code: str
    device_soc_ceiling_percent: float
    stop_threshold_percent: float


def _finite_float(value: object, *, name: str, minimum: float = 0.0) -> float:
    try:
        result = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return result


def make_plan_snapshot(plan: Mapping[str, Any]) -> NightPlanSnapshot:
    """Create an immutable correlation identity from a validated plan JSON."""
    forecast = plan.get("forecast")
    result = plan.get("result")
    if not isinstance(forecast, Mapping) or not isinstance(result, Mapping):
        raise ValueError("night plan must contain forecast and result objects")
    plan_date = str(forecast.get("date") or "").strip()
    if not plan_date:
        raise ValueError("night plan forecast.date is required")
    raw_target = _finite_float(result.get("target_soc_7_percent"), name="target_soc_7_percent")
    if raw_target > 100.0:
        raise ValueError("target_soc_7_percent must be <= 100")
    required_kwh = _finite_float(
        result.get("required_night_charge_kwh", 0.0), name="required_night_charge_kwh"
    )
    capacity = result.get("effective_capacity_kwh")
    capacity_value = None if capacity is None else _finite_float(capacity, name="effective_capacity_kwh")
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    revision = str(plan.get("plan_revision") or plan.get("revision") or "1").strip() or "1"
    plan_id = f"{plan_date}-{revision}-{content_hash[:16]}"
    generated_at = str(plan.get("generated_at") or plan.get("generated_at_utc") or "").strip()
    if not generated_at:
        generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return NightPlanSnapshot(
        plan_id=plan_id,
        plan_date=plan_date,
        revision=revision,
        content_hash=content_hash,
        raw_target_soc_percent=raw_target,
        required_night_charge_kwh=required_kwh,
        effective_capacity_kwh=capacity_value,
        generated_at_utc=generated_at,
    )


# HISTORICAL_FAILURE_LOCK (d1d7792): the real device may expose no SocChargeMode
# candidate at or above the plan target. Keep this fail-closed boundary; do not
# restore a fallback-to-maximum candidate without approval and replay tests.
def build_device_soc_guard(
    value_map: Mapping[str, str],
    *,
    raw_target_soc_percent: float,
    stop_margin_percent: float,
) -> DeviceSocGuard:
    """Choose the smallest candidate not below the target.

    The candidate is an upper guard only.  The monitor must stop at the target
    threshold and then verify the standby read-back.
    """
    target = _finite_float(raw_target_soc_percent, name="raw_target_soc_percent")
    if target > 100.0:
        raise ValueError("raw_target_soc_percent must be <= 100")
    margin = _finite_float(stop_margin_percent, name="stop_margin_percent")
    margin = min(margin, target)
    candidates: list[tuple[float, str]] = []
    for code, value in value_map.items():
        try:
            percent = float(str(value).strip().removesuffix("%"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(percent) and target <= percent <= 100.0:
            candidates.append((percent, str(code)))
    if not candidates:
        raise ValueError("SocChargeMode has no candidate at or above target")
    ceiling, code = min(candidates, key=lambda item: (item[0], item[1]))
    return DeviceSocGuard(
        raw_target_soc_percent=target,
        device_soc_code=code,
        device_soc_ceiling_percent=ceiling,
        stop_threshold_percent=max(0.0, target - margin),
    )


def validate_soc_observation(
    soc_percent: float | None,
    observed_at: datetime | None,
    *,
    now: datetime,
    max_age_seconds: float,
) -> tuple[bool, str | None]:
    if soc_percent is None:
        return False, "soc_unavailable"
    if not math.isfinite(soc_percent) or not 0.0 <= soc_percent <= 100.0:
        return False, "soc_out_of_range"
    if observed_at is None:
        return False, "soc_timestamp_missing"
    if observed_at.tzinfo is None or now.tzinfo is None:
        return False, "soc_timestamp_not_timezone_aware"
    if not math.isfinite(max_age_seconds) or max_age_seconds < 0:
        raise ValueError("max_age_seconds must be finite and non-negative")
    age = now - observed_at
    if age < timedelta(0):
        return False, "soc_timestamp_in_future"
    if age.total_seconds() > max_age_seconds:
        return False, "soc_stale"
    return True, None


def compare_setting_readback(
    requested: Mapping[str, object],
    observed: Mapping[str, object],
    fields: tuple[str, ...] = CONTROLLED_SETTING_FIELDS,
) -> tuple[bool, tuple[str, ...]]:
    mismatches = tuple(
        field
        for field in fields
        if field in requested and str(requested.get(field, "")) != str(observed.get(field, ""))
    )
    return not mismatches, mismatches


def effective_target_soc(raw_target_soc_percent: float, minimum_target_soc_percent: float) -> float:
    raw = _finite_float(raw_target_soc_percent, name="raw_target_soc_percent")
    minimum = _finite_float(minimum_target_soc_percent, name="minimum_target_soc_percent")
    return min(100.0, max(raw, minimum))
