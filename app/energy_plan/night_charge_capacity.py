"""Integrate forecast charging under the shared grid and household load cap."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.energy_plan.battery_response import BatterySocResponse

NIGHT_GRID_LIMIT_KW = 5.0


def expected_morning_soc(result: dict[str, Any], legacy_target: float, night_projection: object = None) -> float:
    """Read new forecast SOC; old immutable plans use their historical target."""
    if "expected_morning_soc_percent" not in result:
        return legacy_target
    value = result["expected_morning_soc_percent"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("expected morning SOC must be finite and within 0..100")
    if isinstance(night_projection, dict) and night_projection.get("status") == "ready":
        cutoff = night_projection.get("estimated_soc_at_cutoff_percent")
        if isinstance(cutoff, bool) or not isinstance(cutoff, (int, float)) or not math.isfinite(cutoff) or abs(value - cutoff) > 0.00011:
            raise ValueError("night SOC projection and morning forecast disagree")
    return float(value)


@dataclass(frozen=True)
class NightChargeCapacity:
    end_soc_percent: float
    charge_kwh: float
    pv_charge_kwh: float
    hourly_soc: dict[int, float]
    hourly_charge_kwh: dict[int, float]
    hourly_pv_charge_kwh: dict[int, float]
    reached_offset_hours: float | None


def simulate_night_charge_capacity(
    *, initial_soc_percent: float, target_soc_percent: float,
    rate_percent_per_hour: float, charge_soc_per_kwh: float,
    hourly_load_kwh: dict[int, float], hourly_pv_kwh: dict[int, float],
    battery_response: BatterySocResponse | None = None,
    grid_limit_kw: float = NIGHT_GRID_LIMIT_KW,
    start_offset_hours: float = 0.0,
) -> NightChargeCapacity:
    """Use hourly mean kW, with a 06:45 cutoff and a unity power factor.

    The observed SOC rate is itself load-dependent; treating it as a second
    ceiling avoids subtracting household consumption from it a second time.
    """
    values = (initial_soc_percent, target_soc_percent, rate_percent_per_hour,
              charge_soc_per_kwh, grid_limit_kw, start_offset_hours)
    if any(not math.isfinite(v) for v in values) or rate_percent_per_hour <= 0 or charge_soc_per_kwh <= 0 or grid_limit_kw <= 0 or start_offset_hours < 0:
        raise ValueError("night charging requires finite positive rate and capacity")
    if not 0 <= initial_soc_percent <= 100 or not 0 <= target_soc_percent <= 100:
        raise ValueError("night charging SOC is outside 0..100")
    for hour in range(3, 7):
        for forecast in (hourly_load_kwh, hourly_pv_kwh):
            value = forecast.get(hour)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("night charging needs complete hourly load and PV")
    soc = initial_soc_percent
    target = max(soc, target_soc_percent)
    reached = 0.0 if soc >= target else None
    hourly_soc: dict[int, float] = {}
    hourly_charge: dict[int, float] = {}
    hourly_pv_charge: dict[int, float] = {}
    for hour in range(3, 7):
        hourly_soc[hour] = soc
        coefficient = battery_response.charge_coefficient(hour) if battery_response else charge_soc_per_kwh
        headroom_kw = max(0.0, grid_limit_kw - hourly_load_kwh[hour] + hourly_pv_kwh[hour])
        effective_rate = min(rate_percent_per_hour, headroom_kw * coefficient)
        interval_start = max(hour - 3.0, start_offset_hours)
        interval_end = min(hour - 2.0, 3.75)
        duration = max(0.0, interval_end - interval_start)
        gain = min(max(0.0, target - soc), effective_rate * duration)
        battery_kwh = gain / coefficient
        active_hours = gain / effective_rate if effective_rate > 0 else 0.0
        pv_charge = min(battery_kwh, max(0.0, hourly_pv_kwh[hour] - hourly_load_kwh[hour]) * active_hours)
        hourly_pv_charge[hour] = pv_charge
        hourly_charge[hour] = battery_kwh - pv_charge
        if reached is None and gain >= target - soc and effective_rate > 0 and duration > 0:
            reached = interval_start + gain / effective_rate
        soc = min(100.0, soc + gain)
    return NightChargeCapacity(soc, sum(hourly_charge.values()), sum(hourly_pv_charge.values()),
                               hourly_soc, hourly_charge, hourly_pv_charge, reached)
