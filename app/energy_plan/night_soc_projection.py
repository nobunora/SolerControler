"""Persist an input-bound night forecast without changing device control."""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.parsing.numbers import to_float
from app.runtime.night_soc_time_contract import FORCED_MONITOR_CUTOFF


def build_night_soc_projection(
    *, target_date: str, timezone_name: str, generated_at: datetime,
    rows: list[dict[str, Any]], target_soc: Any, soc_per_kwh_charge: Any,
    rate_info: dict[str, Any],
) -> dict[str, Any]:
    """Use the same forced-charge rate and cutoff as the 03 monitor.

    Standby is modelled as constant SOC. Only observations from the current
    standby window (23:00 until 03:00) can anchor a forecast because the
    controller owns standby throughout that window; missing values are not zero.
    A daytime rerun must not manufacture a forecast of the past night.
    """
    zone = ZoneInfo(timezone_name)
    midnight = datetime.combine(datetime.fromisoformat(target_date).date(), time(), tzinfo=zone)
    start = midnight + timedelta(hours=3)
    cutoff = datetime.combine(midnight.date(), FORCED_MONITOR_CUTOFF, tzinfo=zone)
    generated = generated_at.astimezone(zone)
    projection: dict[str, Any] = {
        "schema_version": 1, "status": "unavailable", "target_date": target_date,
        "generated_at": generated.isoformat(), "charge_start_time": "03:00",
        "charge_end_time": FORCED_MONITOR_CUTOFF.strftime("%H:%M"),
        "charge_rate": rate_info, "standby_model": "constant_soc_no_parasitic_load",
        "hourly_soc_percent": {}, "hourly_grid_charge_kwh": {},
    }
    if generated >= cutoff:
        return {**projection, "reason": "night_window_already_elapsed"}
    target = to_float(target_soc)
    rate = to_float(rate_info.get("percent_per_hour"))
    charge_coefficient = to_float(soc_per_kwh_charge)
    if target is None or not math.isfinite(target) or not 0 <= target <= 100 or rate is None or not math.isfinite(rate) or rate <= 0:
        return {**projection, "reason": "invalid_target_or_charge_rate"}
    observations: list[tuple[datetime, float]] = []
    for row in rows:
        stamp, soc = row.get("dt"), to_float(row.get("soc"))
        if not isinstance(stamp, datetime) or soc is None or not math.isfinite(soc) or not 0 <= soc <= 100:
            continue
        stamp = stamp.replace(tzinfo=zone) if stamp.tzinfo is None else stamp.astimezone(zone)
        if midnight - timedelta(hours=1) <= stamp <= min(generated, start):
            observations.append((stamp, soc))
    if not observations:
        return {**projection, "reason": "missing_night_start_observation"}
    observed_at, initial_soc = max(observations)
    # Once charging may already have begun, a later run cannot reconstruct its
    # original starting state or use a newly optimized target for the past.
    if generated > start:
        return {**projection, "reason": "night_plan_generated_after_start"}
    required = max(0.0, target - initial_soc)
    arrival = start + timedelta(hours=required / rate)
    stop = min(arrival, cutoff)

    def soc_at(stamp: datetime) -> float:
        elapsed = max(0.0, (min(stamp, cutoff) - start).total_seconds() / 3600)
        return initial_soc + min(required, elapsed * rate)

    forecast_start = max(observed_at, generated)
    hourly_soc = {str(hour): round(soc_at(midnight + timedelta(hours=hour)), 4)
                  for hour in range(7) if midnight + timedelta(hours=hour) >= forecast_start}
    hourly_charge = {}
    if charge_coefficient is not None and math.isfinite(charge_coefficient) and charge_coefficient > 0:
        for hour in range(7):
            interval_start = midnight + timedelta(hours=hour)
            if interval_start < forecast_start:
                continue
            gain = soc_at(interval_start + timedelta(hours=1)) - soc_at(interval_start)
            hourly_charge[str(hour)] = round(gain / charge_coefficient, 4)
    return {
        **projection, "status": "ready", "reason": None,
        "initial_soc_percent": initial_soc, "initial_soc_observed_at": observed_at.isoformat(),
        "target_soc_percent": target, "target_reachable": arrival <= cutoff,
        "soc_per_kwh_charge": charge_coefficient,
        "target_reached_at": arrival.isoformat() if arrival <= cutoff else None,
        "charge_stop_at": stop.isoformat(), "hourly_soc_percent": hourly_soc,
        "hourly_grid_charge_kwh": hourly_charge,
    }
