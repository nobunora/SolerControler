"""Contracts and shared policy for auditable SOC safety constraints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import os
from typing import Any


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


def daytime_net_surplus_headroom_guard(
    *,
    hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float],
    forecast: dict[str, object],
    effective_capacity_kwh: float,
    reserve_soc_percent: float,
) -> dict[str, object]:
    """Limit morning SOC when clear daytime PV surplus needs battery headroom."""
    enabled = _env_bool("DAYTIME_NET_SURPLUS_HEADROOM_GUARD_ENABLED", True)
    hours = list(range(7, 18))
    solar_hours = list(range(9, 16))
    net_by_hour = {
        hour: max(0.0, hourly_pv_kwh.get(hour, 0.0) - hourly_load_kwh.get(hour, 0.0))
        for hour in hours
    }
    expected_surplus = sum(net_by_hour.values())
    solar_surplus = sum(net_by_hour.get(hour, 0.0) for hour in solar_hours)
    min_surplus = _env_float("DAYTIME_NET_SURPLUS_HEADROOM_MIN_KWH", 1.0)
    guard_ratio = _env_float_clamped("DAYTIME_NET_SURPLUS_HEADROOM_RATIO", 0.65, min_value=0.0, max_value=1.0)
    max_guard_kwh = _env_float("DAYTIME_NET_SURPLUS_HEADROOM_MAX_KWH", 6.0)
    min_solar_surplus_share = _env_float_clamped("DAYTIME_NET_SURPLUS_HEADROOM_MIN_SOLAR_SHARE", 0.55, min_value=0.0, max_value=1.0)
    summary = forecast.get("hourly_weather_summary")
    rain_hours = 0
    low_shortwave_hours = 0
    if isinstance(summary, dict):
        rain_hours = int(_optional_float(summary.get("rain_hours_7_17")) or 0)
        low_shortwave_hours = int(_optional_float(summary.get("low_shortwave_hours_9_15")) or 0)
    rain_relax_hours = int(_env_float("DAYTIME_NET_SURPLUS_HEADROOM_RAIN_RELAX_HOURS", 7.0))
    low_shortwave_relax_hours = int(_env_float("DAYTIME_NET_SURPLUS_HEADROOM_LOW_SHORTWAVE_RELAX_HOURS", 5.0))
    solar_share = solar_surplus / expected_surplus if expected_surplus > 0 else 0.0
    rainy_or_low_radiation = rain_hours >= rain_relax_hours or low_shortwave_hours >= low_shortwave_relax_hours
    usable_surplus = min(max_guard_kwh, expected_surplus * guard_ratio)
    capacity = max(0.0, effective_capacity_kwh)
    applied = bool(enabled and capacity > 0 and expected_surplus >= min_surplus and solar_share >= min_solar_surplus_share and not rainy_or_low_radiation)
    cap_target_soc = max(reserve_soc_percent, 100.0 - (usable_surplus / capacity * 100.0)) if applied else 100.0
    if not enabled:
        reason = "disabled"
    elif expected_surplus < min_surplus:
        reason = "insufficient_net_surplus"
    elif solar_share < min_solar_surplus_share:
        reason = "surplus_not_concentrated_in_solar_hours"
    elif rainy_or_low_radiation:
        reason = "rain_or_low_radiation_relaxed"
    else:
        reason = "ok"
    return {"enabled": enabled, "applied": applied, "reason": reason, "hours": hours, "solar_hours": solar_hours, "expected_net_surplus_kwh": round(expected_surplus, 4), "solar_net_surplus_kwh": round(solar_surplus, 4), "solar_surplus_share": round(solar_share, 4), "guard_ratio": guard_ratio, "min_surplus_kwh": min_surplus, "max_guard_kwh": max_guard_kwh, "usable_headroom_kwh": round(usable_surplus if applied else 0.0, 4), "cap_target_soc_percent": round(max(0.0, min(100.0, cap_target_soc)), 3), "rain_hours_7_17": rain_hours, "low_shortwave_hours_9_15": low_shortwave_hours, "net_surplus_by_hour_kwh": {str(hour): round(net_by_hour[hour], 4) for hour in hours}}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if math.isfinite(value) else default


def _env_float_clamped(name: str, default: float, *, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, _env_float(name, default)))


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
