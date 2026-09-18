from __future__ import annotations

import json
import math
import os
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class NightChargeScheduleSettings:
    night_window_start: str
    night_window_end: str
    operation_conditions_path: Path
    fallback_charge_power_kw: float

    @classmethod
    def from_env(cls) -> "NightChargeScheduleSettings":
        return cls(
            night_window_start=os.getenv("KP_NIGHT_CHARGE_WINDOW_START", "23:00").strip() or "23:00",
            night_window_end=os.getenv("KP_NIGHT_CHARGE_WINDOW_END", "07:00").strip() or "07:00",
            operation_conditions_path=Path(
                os.getenv("KP_OPERATION_CONDITIONS_PATH", "config/operation_conditions.json").strip()
                or "config/operation_conditions.json"
            ),
            fallback_charge_power_kw=max(
                0.1,
                float(os.getenv("KP_DEFAULT_CHARGE_POWER_KW", "4.0").strip() or "4.0"),
            ),
        )


@dataclass(frozen=True)
class PlannedNightChargeSchedule:
    anchor_minute: int
    start_minute: int
    end_minute: int
    required_charge_kwh: float
    deliverable_charge_kwh: float
    unmet_charge_kwh: float
    estimated_charge_power_kw: float
    requested_duration_minutes: int
    planned_duration_minutes: int
    limitation_reason: str
    hourly_grid_charge_kwh: dict[int, float]

    @property
    def anchor_time(self) -> str:
        return _minute_to_hhmm(self.anchor_minute)

    @property
    def start_time(self) -> str:
        return _minute_to_hhmm(self.start_minute)

    @property
    def end_time(self) -> str:
        return _minute_to_hhmm(self.end_minute)


def _parse_hhmm(value: str) -> int:
    parts = str(value or "").strip().split(":", maxsplit=1)
    if len(parts) != 2:
        raise ValueError(f"invalid HH:MM value: {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"invalid HH:MM value: {value!r}")
    return hour * 60 + minute


def _minute_to_hhmm(value: int) -> str:
    minute = max(0, min(23 * 60 + 59, int(value)))
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _in_window(value: int, start: int, end: int) -> bool:
    return start <= value < end if start <= end else value >= start or value < end


def resolve_night_charge_end_time(path: Path, *, default: str = "07:00") -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    rules = data.get("variable", []) if isinstance(data, dict) else []
    if not isinstance(rules, list):
        return default
    candidates = [
        rule
        for rule in rules
        if isinstance(rule, dict)
        and bool(rule.get("enabled", True))
        and str(rule.get("id") or "") == "night_charge_end_time"
    ]
    candidates.sort(key=lambda rule: int(rule.get("priority", 0)), reverse=True)
    for rule in candidates:
        value = str(rule.get("value") or "").strip()
        try:
            _parse_hhmm(value)
        except (TypeError, ValueError):
            continue
        return value
    return default


def forecast_anchor_minute(rows: list[dict[str, Any]], *, target_date: str) -> int:
    observations: list[datetime] = []
    for row in rows:
        dt = row.get("dt")
        if not isinstance(dt, datetime):
            continue
        try:
            soc = float(row.get("soc"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(soc) and 0.0 <= soc <= 100.0:
            observations.append(dt)
    control_start = 3 * 60
    if not observations:
        return control_start
    latest = max(observations)
    if latest.date().isoformat() < target_date:
        return control_start
    if latest.date().isoformat() > target_date:
        return 7 * 60
    minute = latest.hour * 60 + latest.minute
    rounded_up = int(math.ceil(minute / 60.0) * 60) if minute else 0
    return max(control_start, min(7 * 60, rounded_up))


def estimate_night_charge_power_kw(
    rows: list[dict[str, Any]],
    *,
    night_window_start: str,
    night_window_end: str,
    fallback_kw: float,
) -> float:
    start = _parse_hhmm(night_window_start)
    end = _parse_hhmm(night_window_end)
    samples: list[float] = []
    for row in rows:
        dt = row.get("dt")
        if not isinstance(dt, datetime):
            continue
        if not _in_window(dt.hour * 60 + dt.minute, start, end):
            continue
        try:
            charge_kwh = float(row.get("charge", 0.0))
        except (TypeError, ValueError):
            continue
        if math.isfinite(charge_kwh) and charge_kwh > 0.0:
            samples.append(charge_kwh * 2.0)
    if samples:
        return max(0.1, statistics.median(samples))
    return max(0.1, float(fallback_kw))


def build_planned_night_charge_schedule(
    *,
    required_charge_kwh: float,
    estimated_charge_power_kw: float,
    anchor_minute: int,
    charge_end_time: str,
) -> PlannedNightChargeSchedule:
    """Plan the expected forced-charge interval from the current 03 owner anchor.

    The 03 controller starts forced mode immediately and stops from live SOC.
    Therefore the forecast interval starts at the anchor and its end is the
    expected target-reach time. The KP-NET adapter may keep a wider device
    safety window; that applied window is recorded separately.
    """
    required = max(0.0, float(required_charge_kwh))
    power = max(0.1, float(estimated_charge_power_kw))
    anchor = max(0, min(7 * 60, int(anchor_minute)))
    hard_end = _parse_hhmm(charge_end_time)
    requested_duration = int(math.ceil(required / power * 60.0)) if required > 0.0 else 0

    if required <= 0.0:
        start = anchor
        end = anchor
        limitation = "no_charge_required"
    elif anchor >= hard_end:
        start = hard_end
        end = hard_end
        limitation = "no_remaining_charge_window"
    else:
        start = anchor
        end = min(hard_end, anchor + requested_duration)
        limitation = "none"

    available_minutes = max(0, end - start)
    deliverable = min(required, power * available_minutes / 60.0)
    unmet = max(0.0, required - deliverable)
    if unmet > 1e-9 and required > 0.0 and anchor < hard_end:
        limitation = "insufficient_remaining_charge_window"

    hourly = {hour: 0.0 for hour in range(24)}
    remaining = deliverable
    if remaining > 0.0:
        for hour in range(max(0, start // 60), min(23, (end - 1) // 60) + 1):
            hour_start = hour * 60
            overlap = max(0, min(hour_start + 60, end) - max(hour_start, start))
            if overlap <= 0:
                continue
            amount = min(remaining, power * overlap / 60.0)
            hourly[hour] = round(amount, 6)
            remaining = max(0.0, remaining - amount)

    return PlannedNightChargeSchedule(
        anchor_minute=anchor,
        start_minute=start,
        end_minute=end,
        required_charge_kwh=required,
        deliverable_charge_kwh=round(deliverable, 6),
        unmet_charge_kwh=round(unmet, 6),
        estimated_charge_power_kw=power,
        requested_duration_minutes=requested_duration,
        planned_duration_minutes=available_minutes,
        limitation_reason=limitation,
        hourly_grid_charge_kwh=hourly,
    )
