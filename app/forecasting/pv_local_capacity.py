"""Local solar-position capability, reconstructed from preserved observations."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import math
from typing import Any
from zoneinfo import ZoneInfo

from app.forecasting.pv_ideal_max import (
    HourlyPvObservation, IdealMaxGeometry, _position, exceeds_ideal_max, ideal_max_base,
)

MODEL_ID = "physical_pv_ideal_max_local_v1"
PROJECTION_POLICY = "solar_position_cells_10x30_v1"
MAX_OBSERVATIONS = 24 * 1096


@lru_cache(maxsize=MAX_OBSERVATIONS)
def capacity_cell(day: date, hour: int, geometry: IdealMaxGeometry) -> str:
    """An observation supports its 10-degree altitude / 30-degree azimuth cell."""
    if type(hour) is not int or not 0 <= hour <= 23:
        raise ValueError("capacity cell requires an hourly interval")
    altitude, azimuth = _position(day, hour + .5, geometry)
    return f"{math.floor(max(0., altitude) / 10)}:{math.floor(azimuth / 30)}"


def fit_local_capacity(records: list[dict[str, Any]], geometry: IdealMaxGeometry, *,
                       as_of: datetime) -> tuple[dict[str, float], dict[str, Any]]:
    """Only a completed, available excess raises the corresponding cell."""
    if as_of.utcoffset() is None:
        raise ValueError("local capacity requires an aware as_of")
    if len(records) > MAX_OBSERVATIONS:
        raise ValueError("local capacity observation budget exceeded")
    zone = ZoneInfo(geometry.timezone_name)
    observations = []
    seen: set[datetime] = set()
    for row in records:
        halves = row.get("halfhour_kwh")
        if not isinstance(halves, list) or len(halves) != 2:
            raise ValueError("local capacity requires both measured half-hours")
        observation = HourlyPvObservation(datetime.fromisoformat(row["interval_start"]),
            halves[0], halves[1], datetime.fromisoformat(row["available_at"]))
        if observation.available_at > as_of:
            continue
        if observation.interval_start in seen:
            raise ValueError("duplicate local capacity observation")
        seen.add(observation.interval_start)
        if observation.interval_start + timedelta(hours=1) > as_of:
            raise ValueError("local capacity observation is not complete")
        base = row["base_kwh"]
        if (isinstance(base, bool) or not isinstance(base, (int, float))
                or not math.isfinite(base) or base < 0
                or observation.actual_kwh != row["actual_kwh"]):
            raise ValueError("invalid local capacity observation")
        observations.append((observation.available_at, observation.interval_start, observation, float(base)))
    coefficients: dict[str, float] = {}
    evidence: dict[str, dict[str, Any]] = {}
    updates = 0
    for _, _, observation, base in sorted(observations):
        local = observation.interval_start.astimezone(zone)
        key = capacity_cell(local.date(), local.hour, geometry)
        if exceeds_ideal_max(observation.actual_kwh, base * coefficients.get(key, 1.)):
            if base == 0:
                raise ValueError("a zero-base excess cannot define a local coefficient")
            coefficient = observation.actual_kwh / base
            if base * coefficient < observation.actual_kwh:
                coefficient = math.nextafter(coefficient, math.inf)
            if not math.isfinite(coefficient):
                raise ValueError("invalid local capacity coefficient")
            coefficients[key] = coefficient
            evidence[key] = {"interval_start": local.isoformat(), "available_at": observation.available_at.isoformat(),
                             "actual_kwh": observation.actual_kwh, "base_kwh": base}
            updates += 1
    latest = max((r[0] for r in observations), default=None)
    return coefficients, {"policy": PROJECTION_POLICY, "observation_count": len(observations),
                          "update_count": updates, "cell_evidence": evidence,
                          "training_as_of": latest.astimezone(timezone.utc).isoformat() if latest else None}


def calculate_local_capacity(day: date, geometry: IdealMaxGeometry, coefficients: dict[str, float]
                             ) -> tuple[dict[int, float], dict[int, str]]:
    cells = {hour: capacity_cell(day, hour, geometry) for hour in range(24)}
    result = {}
    for hour in range(24):
        coefficient = coefficients.get(cells[hour], 1.)
        if isinstance(coefficient, bool) or not math.isfinite(coefficient) or coefficient < 1:
            raise ValueError("invalid local capacity coefficient")
        maximum = ideal_max_base(day, hour, geometry) * coefficient
        if not math.isfinite(maximum):
            raise ValueError("local capacity projection is not finite")
        result[hour] = maximum
    return result, cells
