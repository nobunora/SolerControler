"""Contracts and shared policy for auditable SOC safety constraints."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SocConstraint:
    """One calculated SOC safety constraint and the evidence that produced it."""

    name: str
    applied: bool
    cap_target_soc_percent: float | None
    reason: str
    evidence: dict[str, object]


@dataclass
class SocConstraintSet:
    """All SOC caps considered for one energy-plan snapshot."""

    reserve_soc_percent: float
    max_target_soc_percent: float
    apply_pv_headroom_caps: bool
    active_constraints: list[SocConstraint]
    morning_headroom: dict[str, object]
    daytime_net_surplus: dict[str, object]
    historical_soc_gain: dict[str, object]


def active_constraint_names(
    *,
    morning_headroom_guard: dict[str, object],
    daytime_net_surplus_headroom_guard: dict[str, object],
    historical_soc_gain_guard: dict[str, object],
    respect_morning_headroom_guard: bool,
) -> list[str]:
    """List caps that are enforced for the selected PV forecast method."""
    active = ["reserve_soc"]
    morning_enforced = morning_headroom_guard.get(
        "enforced_as_target_cap", morning_headroom_guard.get("applied")
    )
    daytime_enforced = daytime_net_surplus_headroom_guard.get(
        "enforced_as_target_cap", daytime_net_surplus_headroom_guard.get("applied")
    )
    historical_enforced = historical_soc_gain_guard.get(
        "enforced_as_target_cap", historical_soc_gain_guard.get("applied")
    )
    if respect_morning_headroom_guard and morning_enforced:
        active.append("morning_pv_headroom_guard")
    if daytime_enforced:
        active.append("daytime_net_surplus_headroom_guard")
    if historical_enforced:
        active.append("historical_daytime_soc_gain_guard")
    return active


def morning_pv_headroom_guard(
    *,
    hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float],
    effective_capacity_kwh: float,
    reserve_soc_percent: float,
    enabled: bool,
    guard_ratio: float,
    min_guard_kwh: float,
) -> dict[str, object]:
    """Limit morning SOC only when forecast PV needs usable battery headroom."""
    hours = [7, 8, 9]
    morning_pv = sum(max(0.0, hourly_pv_kwh.get(hour, 0.0)) for hour in hours)
    morning_load = sum(max(0.0, hourly_load_kwh.get(hour, 0.0)) for hour in hours)
    morning_deficit = max(0.0, morning_load - morning_pv)
    capacity = max(0.0, effective_capacity_kwh)
    clamped_ratio = max(0.0, min(1.0, guard_ratio))
    required_headroom = max(0.0, min_guard_kwh)
    guard_headroom = max(0.0, morning_pv * clamped_ratio - morning_deficit)
    applied = enabled and capacity > 0 and guard_headroom >= required_headroom
    cap_target_soc = (
        max(reserve_soc_percent, 100.0 - guard_headroom / capacity * 100.0)
        if applied
        else 100.0
    )
    return {
        "enabled": enabled,
        "applied": applied,
        "hours": hours,
        "guard_ratio": clamped_ratio,
        "min_guard_kwh": required_headroom,
        "morning_pv_kwh": morning_pv,
        "morning_load_kwh": morning_load,
        "morning_deficit_kwh": morning_deficit,
        "guard_headroom_kwh": guard_headroom,
        "cap_target_soc_percent": max(0.0, min(100.0, cap_target_soc)),
    }
