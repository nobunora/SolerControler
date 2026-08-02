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


def assemble_constraint_set(
    *,
    reserve_soc_percent: float,
    apply_pv_headroom_caps: bool,
    raw_guards: list[tuple[str, dict[str, object]]],
    annotated_guards: list[dict[str, object]],
) -> SocConstraintSet:
    """Build the ordered, auditable set of SOC target caps."""
    active = [
        SocConstraint(
            name=name,
            applied=bool(guard.get("applied")),
            cap_target_soc_percent=_optional_float(guard.get("cap_target_soc_percent")),
            reason=str(guard.get("reason") or ""),
            evidence=dict(guard),
        )
        for (name, _), guard in zip(raw_guards, annotated_guards)
        if guard.get("applied")
    ]
    max_target_soc = 100.0
    if apply_pv_headroom_caps:
        for guard in annotated_guards:
            if guard.get("applied") or guard is annotated_guards[0]:
                cap = _optional_float(guard.get("cap_target_soc_percent"))
                max_target_soc = min(max_target_soc, cap if cap is not None else 100.0)
    return SocConstraintSet(
        reserve_soc_percent=reserve_soc_percent,
        max_target_soc_percent=max_target_soc,
        apply_pv_headroom_caps=apply_pv_headroom_caps,
        active_constraints=active,
        morning_headroom=annotated_guards[0],
        daytime_net_surplus=annotated_guards[1],
        historical_soc_gain=annotated_guards[2],
    )


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


def historical_daytime_soc_gain_guard(
    rows: list[dict[str, Any]], *, reserve_soc_percent: float, target_date: str
) -> dict[str, object]:
    """Cap morning SOC using observed PV-driven SOC gain."""
    enabled = _env_bool("HISTORICAL_DAYTIME_SOC_GAIN_GUARD_ENABLED", True)
    percentile = _env_float_clamped("HISTORICAL_DAYTIME_SOC_GAIN_PERCENTILE", 25.0, min_value=0.0, max_value=100.0)
    floor_percent = _env_float_clamped("HISTORICAL_DAYTIME_SOC_GAIN_FLOOR_PERCENT", 15.0, min_value=0.0, max_value=100.0)
    min_days = max(1, int(_env_float("HISTORICAL_DAYTIME_SOC_GAIN_MIN_DAYS", 5.0)))
    long_term_days = max(1, int(_env_float("HISTORICAL_DAYTIME_SOC_GAIN_LONG_TERM_DAYS", 180.0)))
    recent_days = max(1, int(_env_float("HISTORICAL_DAYTIME_SOC_GAIN_RECENT_DAYS", 30.0)))
    max_morning_soc = _env_float_clamped("HISTORICAL_DAYTIME_SOC_GAIN_MAX_MORNING_SOC", 70.0, min_value=0.0, max_value=100.0)
    full_soc_threshold = _env_float_clamped("HISTORICAL_DAYTIME_SOC_GAIN_FULL_SOC_THRESHOLD", 98.0, min_value=0.0, max_value=100.0)
    min_pv_kwh = max(0.0, _env_float("HISTORICAL_DAYTIME_SOC_GAIN_MIN_PV_KWH", 0.1))
    min_samples = max(1, int(_env_float("HISTORICAL_DAYTIME_SOC_GAIN_MIN_SAMPLES", 30.0)))
    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        row_datetime = row.get("dt")
        if isinstance(row_datetime, datetime):
            by_day.setdefault(row_datetime.date().isoformat(), []).append(row)
    candidates: list[dict[str, object]] = []
    excluded = {"incomplete": 0, "low_pv": 0, "missing_morning_soc": 0, "full_soc_clipped": 0, "high_morning_soc": 0, "future_or_target_day": 0}
    for day, day_rows in sorted(by_day.items()):
        if day >= target_date:
            excluded["future_or_target_day"] += 1
            continue
        day_rows = sorted(day_rows, key=lambda row: row["dt"] if isinstance(row.get("dt"), datetime) else datetime.min)
        if len(day_rows) < min_samples:
            excluded["incomplete"] += 1
            continue
        last_datetime = day_rows[-1]["dt"]
        if not isinstance(last_datetime, datetime) or last_datetime.hour < 18:
            excluded["incomplete"] += 1
            continue
        total_pv = sum(float(row.get("pv", 0.0) or 0.0) for row in day_rows)
        if total_pv < min_pv_kwh:
            excluded["low_pv"] += 1
            continue
        morning_rows = [row for row in day_rows if isinstance(row.get("dt"), datetime) and 6 <= row["dt"].hour <= 8]
        if not morning_rows:
            excluded["missing_morning_soc"] += 1
            continue
        morning = min(morning_rows, key=lambda row: abs(((row["dt"].hour * 60 + row["dt"].minute) - 420) if isinstance(row.get("dt"), datetime) else 10_000))
        morning_soc = _optional_float(morning.get("soc"))
        if morning_soc is None or morning_soc != morning_soc:
            excluded["missing_morning_soc"] += 1
            continue
        day_soc_values = [_optional_float(row.get("soc")) for row in day_rows if isinstance(row.get("dt"), datetime) and 5 <= row["dt"].hour <= 18]
        valid_day_soc_values = [value for value in day_soc_values if value is not None and value == value]
        if not valid_day_soc_values:
            excluded["incomplete"] += 1
            continue
        max_soc = max(valid_day_soc_values)
        if max_soc >= full_soc_threshold:
            excluded["full_soc_clipped"] += 1
            continue
        if morning_soc > max_morning_soc:
            excluded["high_morning_soc"] += 1
            continue
        gain = max(0.0, max_soc - morning_soc)
        candidates.append({"date": day, "morning_soc_percent": round(morning_soc, 3), "max_daytime_soc_percent": round(max_soc, 3), "daytime_soc_gain_percent": round(gain, 3), "pv_kwh": round(total_pv, 4)})
    selected = candidates[-recent_days:] if len(candidates) >= long_term_days else candidates
    source_window = f"recent_{recent_days}_days" if len(candidates) >= long_term_days else "all_available_until_180_days"
    gains = [_optional_float(item.get("daytime_soc_gain_percent")) or 0.0 for item in selected]
    percentile_gain = _percentile(gains, percentile)
    applied = bool(enabled and percentile_gain is not None and len(gains) >= min_days)
    guard_gain = max(floor_percent, percentile_gain or 0.0) if applied else 0.0
    cap_target_soc = max(reserve_soc_percent, max(0.0, min(100.0, 100.0 - guard_gain))) if applied else 100.0
    return {"enabled": enabled, "applied": applied, "reason": "ok" if applied else ("disabled" if not enabled else "insufficient_history"), "target_date": target_date, "source_window": source_window, "sample_count": len(gains), "total_candidate_days": len(candidates), "percentile": percentile, "percentile_gain_percent": round(percentile_gain, 3) if percentile_gain is not None else None, "floor_percent": floor_percent, "guard_gain_percent": round(guard_gain, 3), "cap_target_soc_percent": round(cap_target_soc, 3), "reserve_soc_percent": reserve_soc_percent, "selection_rules": {"max_morning_soc_percent": max_morning_soc, "full_soc_threshold_percent": full_soc_threshold, "min_pv_kwh": min_pv_kwh, "min_samples_per_day": min_samples}, "excluded_counts": excluded, "lowest_days": sorted(selected, key=lambda item: _optional_float(item.get("daytime_soc_gain_percent")) or 0.0)[:8]}


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


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * max(0.0, min(100.0, percentile)) / 100.0
    low_index = int(position)
    high_index = min(len(ordered) - 1, low_index + 1)
    fraction = position - low_index
    return ordered[low_index] * (1.0 - fraction) + ordered[high_index] * fraction
