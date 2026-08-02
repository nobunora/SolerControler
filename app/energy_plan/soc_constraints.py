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
