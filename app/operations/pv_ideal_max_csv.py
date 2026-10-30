"""Actual CSV aggregation shared by explicit replay and normal ingestion."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.forecasting.pv_ideal_max import HourlyPvObservation


def hourly_observations(rows: list[dict[str, Any]], *, zone: ZoneInfo, as_of: datetime,
                        hours: tuple[int, int], replay: bool) -> tuple[list[HourlyPvObservation], dict[str, int]]:
    """Require two actual half-hours; retain zero and never fill a missing value."""
    samples: dict[datetime, float | None] = {}
    counts = {"duplicate_halfhours": 0, "incomplete_hours": 0, "future_hours": 0,
              "outside_learning_hours": 0}
    for row in rows:
        stamp = row["dt"].replace(tzinfo=zone)
        if stamp.minute not in (0, 30) or stamp.second or stamp.microsecond:
            raise ValueError("CSV contains a non-half-hour boundary")
        actual = row["pv"]
        if actual is not None and (isinstance(actual, bool) or not math.isfinite(actual) or actual < 0):
            raise ValueError("CSV PV energy must be finite and nonnegative")
        if stamp in samples:
            if samples[stamp] != actual:
                raise ValueError("conflicting CSV half-hour observations")
            counts["duplicate_halfhours"] += 1
        samples[stamp] = actual
    observations = []
    starts = sorted({stamp.replace(minute=0) for stamp in samples})
    for start in starts:
        if not hours[0] <= start.hour < hours[1]:
            counts["outside_learning_hours"] += 1
            continue
        end = start + timedelta(hours=1)
        if end > as_of:
            counts["future_hours"] += 1
            continue
        first, second = samples.get(start), samples.get(start + timedelta(minutes=30))
        if first is None or second is None:
            counts["incomplete_hours"] += 1
            continue
        observations.append(HourlyPvObservation(start, first, second, end if replay else as_of))
    return observations, counts
