from __future__ import annotations

import csv
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.domain.constants import SOCBounds
from app.energy_plan.soc_cost import simulate_daytime_soc_replay


@dataclass(frozen=True)
class PlannedNightChargeSchedule:
    charge_start_time: str
    charge_end_time: str
    estimated_charge_power_kw: float
    requested_charge_duration_minutes: int
    planned_charge_duration_minutes: int
    duration_clipped_to_available_window: bool
    not_before_minute: int


def _parse_hhmm(value: str) -> int:
    hour_text, minute_text = str(value).strip().split(":", maxsplit=1)
    hour = int(hour_text)
    minute = int(minute_text)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"invalid HH:MM value: {value!r}")
    return hour * 60 + minute


def _format_hhmm(minutes: int) -> str:
    bounded = max(0, min(23 * 60 + 59, int(minutes)))
    return f"{bounded // 60:02d}:{bounded % 60:02d}"


def _within_window(minute: int, start: int, end: int) -> bool:
    if start <= end:
        return start <= minute < end
    return minute >= start or minute < end


def estimate_charge_power_kw_from_csv(
    csv_paths: list[Path],
    *,
    night_window_start: str,
    night_window_end: str,
    fallback_kw: float,
) -> float:
    """Estimate grid charge power from retained 30-minute KP-NET samples."""

    start_minute = _parse_hhmm(night_window_start)
    end_minute = _parse_hhmm(night_window_end)
    samples: list[float] = []
    for csv_path in csv_paths:
        if not csv_path.exists():
            continue
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                date_text = (row.get("年月日") or "").strip()
                time_text = (row.get("時刻") or "").strip()
                if not date_text or not time_text:
                    continue
                try:
                    dt = datetime.strptime(f"{date_text} {time_text}", "%Y/%m/%d %H:%M")
                    charge_kwh = float((row.get("充電電力量[kWh]") or "0").strip() or "0")
                except ValueError:
                    continue
                if charge_kwh <= 0:
                    continue
                if _within_window(dt.hour * 60 + dt.minute, start_minute, end_minute):
                    samples.append(charge_kwh)
    if samples:
        return statistics.median(samples) * 2.0
    return max(0.0, float(fallback_kw))


def resolve_planned_charge_end_time(*, conditions_path: Path, default_hhmm: str) -> str:
    """Resolve the planner-owned end time from the existing operation-conditions file."""

    try:
        payload = json.loads(conditions_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default_hhmm
    rules = payload.get("variable", []) if isinstance(payload, dict) else []
    if not isinstance(rules, list):
        return default_hhmm
    candidates = [
        item for item in rules
        if isinstance(item, dict)
        and bool(item.get("enabled", True))
        and str(item.get("id") or "").strip() == "night_charge_end_time"
    ]
    candidates.sort(key=lambda item: int(item.get("priority", 0)), reverse=True)
    for item in candidates:
        raw = str(item.get("value") or "").strip()
        try:
            _parse_hhmm(raw)
        except (ValueError, TypeError):
            continue
        return raw
    return default_hhmm


def build_planned_night_charge_schedule(
    *,
    required_night_charge_kwh: float,
    estimated_charge_power_kw: float,
    charge_end_time: str,
    not_before_minute: int,
) -> PlannedNightChargeSchedule:
    """Create the planner-owned same-day schedule available after the 03 job starts."""

    end_minute = _parse_hhmm(charge_end_time)
    not_before = max(0, min(end_minute, int(not_before_minute)))
    power = max(0.0, float(estimated_charge_power_kw))
    required = max(0.0, float(required_night_charge_kwh))
    requested = int(math.ceil(required / power * 60.0)) if power > 0 and required > 0 else 0
    available = max(0, end_minute - not_before)
    planned = min(requested, available)
    start_minute = end_minute - planned
    return PlannedNightChargeSchedule(
        charge_start_time=_format_hhmm(start_minute),
        charge_end_time=_format_hhmm(end_minute),
        estimated_charge_power_kw=power,
        requested_charge_duration_minutes=requested,
        planned_charge_duration_minutes=planned,
        duration_clipped_to_available_window=requested > planned,
        not_before_minute=not_before,
    )


def allocate_hourly_grid_charge(
    *,
    schedule: PlannedNightChargeSchedule,
    required_night_charge_kwh: float,
) -> dict[int, float]:
    """Allocate only the charge energy the planned time window can actually deliver."""

    start = _parse_hhmm(schedule.charge_start_time)
    end = _parse_hhmm(schedule.charge_end_time)
    deliverable = min(
        max(0.0, float(required_night_charge_kwh)),
        max(0.0, schedule.estimated_charge_power_kw)
        * schedule.planned_charge_duration_minutes
        / 60.0,
    )
    if deliverable <= 0 or end <= start:
        return {hour: 0.0 for hour in range(24)}
    overlaps: dict[int, int] = {}
    for hour in range(24):
        hour_start = hour * 60
        overlaps[hour] = max(0, min(hour_start + 60, end) - max(hour_start, start))
    total_overlap = sum(overlaps.values())
    if total_overlap <= 0:
        return {hour: 0.0 for hour in range(24)}
    return {hour: deliverable * minutes / total_overlap for hour, minutes in overlaps.items()}


def build_hourly_soc_projection(
    *,
    anchor_hour: int,
    anchor_soc_percent: float,
    capacity_kwh: float,
    charge_efficiency: float,
    hourly_grid_charge_kwh: dict[int, float],
    hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float],
) -> dict[int, float | None]:
    """Project SOC from the 03 actual anchor, then reuse the optimizer daytime replay."""

    capacity = max(0.01, float(capacity_kwh))
    efficiency = max(0.01, float(charge_efficiency))
    anchor = max(0, min(23, int(anchor_hour)))
    energy = capacity * float(SOCBounds.clamp(anchor_soc_percent)) / 100.0
    forecast: dict[int, float | None] = {hour: None for hour in range(24)}

    for hour in range(anchor, 7):
        forecast[hour] = round(float(SOCBounds.clamp(100.0 * energy / capacity)), 1)
        energy += max(0.0, hourly_grid_charge_kwh.get(hour, 0.0)) * efficiency
        energy = max(0.0, min(capacity, energy))

    replay = simulate_daytime_soc_replay(
        start_energy_kwh=energy,
        capacity_kwh=capacity,
        hourly_load_kwh=hourly_load_kwh,
        hourly_pv_kwh=hourly_pv_kwh,
        pv_multiplier=1.0,
        load_multiplier=1.0,
    )
    for hour, value in replay.hourly_soc_percent.items():
        forecast[hour] = round(value, 1)
    forecast[23] = round(replay.end_soc_percent, 1)
    return forecast
