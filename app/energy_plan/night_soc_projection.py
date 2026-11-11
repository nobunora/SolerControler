"""Persist an input-bound night forecast without changing device control."""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.parsing.numbers import to_float
from app.runtime.night_soc_time_contract import FORCED_MONITOR_CUTOFF
from app.energy_plan.battery_response import BatterySocResponse
from app.energy_plan.night_charge_capacity import NIGHT_GRID_LIMIT_KW, simulate_night_charge_capacity
from app.domain.measurement_time import CSV_TIME_BASIS, csv_soc_effective_at


def latest_night_soc_observation(
    *, rows: list[dict[str, Any]], target_date: str, timezone_name: str, generated_at: datetime,
) -> tuple[datetime, float] | None:
    """Choose a standby observation by its effective time, including valid zero."""
    zone = ZoneInfo(timezone_name)
    midnight = datetime.combine(datetime.fromisoformat(target_date).date(), time(), tzinfo=zone)
    start = midnight + timedelta(hours=3)
    generated = generated_at.astimezone(zone)
    observations: list[tuple[datetime, float]] = []
    for row in rows:
        stamp, soc = row.get("dt"), to_float(row.get("soc"))
        if not isinstance(stamp, datetime) or soc is None or not math.isfinite(soc) or not 0 <= soc <= 100:
            continue
        stamp = csv_soc_effective_at(stamp).astimezone(zone)
        if midnight - timedelta(hours=1) <= stamp <= min(generated, start):
            observations.append((stamp, soc))
    return max(observations) if observations else None


def build_night_soc_projection(
    *, target_date: str, timezone_name: str, generated_at: datetime,
    rows: list[dict[str, Any]], target_soc: Any, soc_per_kwh_charge: Any,
    rate_info: dict[str, Any],
    battery_response: BatterySocResponse | None = None,
    hourly_load_kwh: dict[int, float] | None = None,
    hourly_pv_kwh: dict[int, float] | None = None,
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
    observation = latest_night_soc_observation(rows=rows, target_date=target_date,
                                                timezone_name=timezone_name, generated_at=generated)
    if observation is None:
        return {**projection, "reason": "missing_night_start_observation"}
    observed_at, initial_soc = observation
    # Once charging may already have begun, a later run cannot reconstruct its
    # original starting state or use a newly optimized target for the past.
    if generated > start:
        return {**projection, "reason": "night_plan_generated_after_start"}
    if hourly_load_kwh is not None and hourly_pv_kwh is not None:
        if charge_coefficient is None or not math.isfinite(charge_coefficient) or charge_coefficient <= 0:
            return {**projection, "reason": "invalid_charge_coefficient"}
        try:
            schedule = simulate_night_charge_capacity(
                initial_soc_percent=initial_soc, target_soc_percent=target,
                rate_percent_per_hour=rate, charge_soc_per_kwh=charge_coefficient,
                hourly_load_kwh=hourly_load_kwh, hourly_pv_kwh=hourly_pv_kwh,
                battery_response=battery_response,
            )
        except ValueError:
            return {**projection, "reason": "invalid_night_power_forecast"}
        forecast_start = max(observed_at, generated)
        hourly_soc = {str(hour): round(schedule.hourly_soc.get(hour, initial_soc), 4)
                      for hour in range(7) if midnight + timedelta(hours=hour) >= forecast_start}
        hourly_charge = {str(hour): round(schedule.hourly_charge_kwh.get(hour, 0.0), 4)
                         for hour in range(7) if midnight + timedelta(hours=hour) >= forecast_start}
        hourly_pv_charge = {str(hour): round(schedule.hourly_pv_charge_kwh.get(hour, 0.0), 4)
                            for hour in range(7) if midnight + timedelta(hours=hour) >= forecast_start}
        reached_at = start + timedelta(hours=schedule.reached_offset_hours) if schedule.reached_offset_hours is not None else None
        return {**projection, "status": "ready", "reason": None,
                "initial_soc_percent": initial_soc, "initial_soc_observed_at": observed_at.isoformat(),
                "initial_soc_time_basis": CSV_TIME_BASIS, "target_soc_percent": target,
                "target_reachable": reached_at is not None, "soc_per_kwh_charge": charge_coefficient,
                "target_reached_at": reached_at.isoformat() if reached_at else None,
                "charge_stop_at": (reached_at or cutoff).isoformat(),
                "estimated_soc_at_cutoff_percent": round(schedule.end_soc_percent, 4),
                "grid_limit_kw_assuming_unity_power_factor": NIGHT_GRID_LIMIT_KW,
                "hourly_soc_percent": hourly_soc, "hourly_grid_charge_kwh": hourly_charge,
                "hourly_pv_charge_kwh": hourly_pv_charge}
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
            coefficient = battery_response.charge_coefficient(hour) if battery_response is not None else charge_coefficient
            hourly_charge[str(hour)] = round(gain / coefficient, 4)
    return {
        **projection, "status": "ready", "reason": None,
        "initial_soc_percent": initial_soc, "initial_soc_observed_at": observed_at.isoformat(),
        "initial_soc_time_basis": CSV_TIME_BASIS,
        "target_soc_percent": target, "target_reachable": arrival <= cutoff,
        "soc_per_kwh_charge": charge_coefficient,
        "target_reached_at": arrival.isoformat() if arrival <= cutoff else None,
        "charge_stop_at": stop.isoformat(), "hourly_soc_percent": hourly_soc,
        "hourly_grid_charge_kwh": hourly_charge,
    }
