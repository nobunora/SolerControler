"""Finite-memory hourly consumption templates, independent of past forecasts."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any

from app.parsing.numbers import to_float

HOURS = tuple(range(3, 24))
LOOKBACK_DAYS = 56
SAMPLE_DAYS = 4


@lru_cache(maxsize=1)
def _calendar() -> tuple[frozenset[int], frozenset[date]]:
    # Bundled official calendar: no network request in the forecasting path.
    data = json.loads(Path(__file__).with_name("japan_holidays.json").read_text(encoding="utf-8"))
    return frozenset(data["years"]), frozenset(date.fromisoformat(d) for d in data["dates"])


def complete_hourly_load_history(
    rows: list[dict[str, Any]], *, target_date: str,
) -> dict[str, dict[int, float]]:
    """Require both valid half-hours; duplicates must agree, never fill missing data."""
    target = date.fromisoformat(target_date)
    intervals: dict[tuple[str, int, int], float] = {}
    invalid: set[tuple[str, int]] = set()
    for row in rows:
        stamp = row.get("dt")
        if not isinstance(stamp, datetime) or not 1 <= (target - stamp.date()).days <= LOOKBACK_DAYS:
            continue
        day, hour = stamp.date().isoformat(), stamp.hour
        value = to_float(row.get("load"))
        key = (day, hour, stamp.minute)
        if (stamp.minute not in (0, 30) or stamp.second or stamp.microsecond
                or value is None or value < 0 or isinstance(row.get("load"), bool)):
            invalid.add((day, hour))
            continue
        if key in intervals and intervals[key] != value:
            invalid.add((day, hour))
        intervals[key] = value
    result: dict[str, dict[int, float]] = {}
    for (day, hour, minute), value in intervals.items():
        if minute == 0 and (day, hour) not in invalid and (day, hour, 30) in intervals:
            result.setdefault(day, {})[hour] = value + intervals[(day, hour, 30)]
    return result


def apply_hourly_load_template(
    baseline: dict[int, float], *, history: dict[str, dict[int, float]], target_date: str,
) -> tuple[dict[int, float], dict[str, Any]]:
    """Use four latest complete matching calendar days; keep 00-02 unchanged."""
    target = date.fromisoformat(target_date)
    proof: dict[str, Any] = {
        "method": "same_calendar_daytype_four_day_hourly_median_v1",
        "applied": False, "lookback_days": LOOKBACK_DAYS, "required_days": SAMPLE_DAYS,
        "hours": list(HOURS), "dates": [], "reason": "insufficient_compatible_history",
    }
    years, holidays = _calendar()
    if target.year not in years:
        proof["reason"] = "holiday_calendar_year_unavailable"
        return dict(baseline), proof
    nonwork = target.weekday() >= 5 or target in holidays
    selected: list[str] = []
    for age in range(1, LOOKBACK_DAYS + 1):
        day = target - timedelta(days=age)
        if day.year not in years or (day.weekday() >= 5 or day in holidays) != nonwork:
            continue
        values = history.get(day.isoformat(), {})
        if not all(h in values and to_float(values[h]) is not None and values[h] >= 0 for h in HOURS):
            continue
        selected.append(day.isoformat())
        if len(selected) == SAMPLE_DAYS:
            break
    proof.update(dates=selected, calendar_nonwork=nonwork)
    if len(selected) < SAMPLE_DAYS:
        return dict(baseline), proof
    result = dict(baseline)
    result.update({h: float(median(history[d][h] for d in selected)) for h in HOURS})
    proof.update(applied=True, reason="ok", hourly_load_kwh={str(h): result[h] for h in HOURS})
    return result, proof
