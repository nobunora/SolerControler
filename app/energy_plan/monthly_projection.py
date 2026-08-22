"""Monthly daytime-buy projections used by Energy Plan quality decisions."""

from __future__ import annotations

import os
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any


def _parse_hhmm(value: str, *, default: str) -> dt_time:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        return dt_time(hour=hour, minute=minute)
    except (TypeError, ValueError):
        hour, minute = (int(part) for part in default.split(":", 1))
        return dt_time(hour=hour, minute=minute)


def _clock_minutes(value: dt_time) -> int:
    return value.hour * 60 + value.minute


def _is_within_window(minute_of_day: int, *, start_minute: int, end_minute: int) -> bool:
    if start_minute == end_minute:
        return True
    if start_minute < end_minute:
        return start_minute <= minute_of_day < end_minute
    return minute_of_day >= start_minute or minute_of_day < end_minute


def billing_period_for_target(target_day: date) -> tuple[date, date, int]:
    raw = os.getenv("SOC_MONTHLY_TIER_CLOSE_DAY", os.getenv("DASHBOARD_AGGREGATION_CLOSE_DAY", "14")).strip()
    try:
        close_day = max(1, min(28, int(raw)))
    except ValueError:
        close_day = 14
    if target_day.day <= close_day:
        period_end = target_day.replace(day=close_day)
        period_start = (period_end.replace(day=1) - timedelta(days=1)).replace(day=close_day + 1)
    else:
        next_month = (target_day.replace(day=28) + timedelta(days=4)).replace(day=1)
        period_start = target_day.replace(day=close_day + 1)
        period_end = next_month.replace(day=close_day)
    return period_start, period_end, close_day


# HISTORICAL_FAILURE_LOCK (541cd60): monthly estimation uses the complete previous
# billing period, not an invented current-month tier penalty or partial-month proxy.
def previous_billing_period_for_target(target_day: date) -> tuple[date, date, int]:
    """Return the complete billing period immediately before target_day's period."""

    current_start, _, _ = billing_period_for_target(target_day)
    return billing_period_for_target(current_start - timedelta(days=1))


def _daytime_buy_by_day(
    rows: list[dict[str, Any]],
    *,
    period_start: date,
    period_end: date,
    day_start: dt_time,
    day_end: dt_time,
) -> dict[date, float]:
    daily: dict[date, float] = {}
    for row in rows:
        timestamp = row.get("dt")
        if not isinstance(timestamp, datetime) or not (
            period_start <= timestamp.date() <= period_end
        ):
            continue
        if not _is_within_window(
            timestamp.hour * 60 + timestamp.minute,
            start_minute=_clock_minutes(day_start),
            end_minute=_clock_minutes(day_end),
        ):
            continue
        daily[timestamp.date()] = daily.get(timestamp.date(), 0.0) + max(
            0.0, float(row.get("buy", 0.0) or 0.0)
        )
    return daily


def monthly_day_buy_kwh_before_target(rows: list[dict[str, Any]], *, target_date: str) -> dict[str, object]:
    try:
        target_day = date.fromisoformat(target_date)
    except ValueError:
        return {"kwh": 0.0, "source": "invalid_target_date", "target_date": target_date}
    day_start = _parse_hhmm(os.getenv("NIGHT8_DAY_START_HHMM", "07:00"), default="07:00")
    day_end = _parse_hhmm(os.getenv("NIGHT8_DAY_END_HHMM", "23:00"), default="23:00")
    period_start, period_end, close_day = billing_period_for_target(target_day)
    total = 0.0
    sample_days: set[str] = set()
    for row in rows:
        timestamp = row.get("dt")
        if not isinstance(timestamp, datetime) or not (period_start <= timestamp.date() < target_day <= period_end):
            continue
        if not _is_within_window(timestamp.hour * 60 + timestamp.minute, start_minute=_clock_minutes(day_start), end_minute=_clock_minutes(day_end)):
            continue
        total += max(0.0, float(row.get("buy", 0.0) or 0.0))
        sample_days.add(timestamp.date().isoformat())
    return {"kwh": round(total, 4), "source": "csv_month_to_target_daytime_buy", "target_date": target_date, "billing_period_start": period_start.isoformat(), "billing_period_end": period_end.isoformat(), "billing_close_day": close_day, "day_window": f"{day_start:%H:%M}-{day_end:%H:%M}", "sample_day_count": len(sample_days), "sample_days": sorted(sample_days)[-10:]}


def expected_rest_of_month_day_buy_kwh(rows: list[dict[str, Any]], *, target_date: str) -> dict[str, object]:
    try:
        target_day = date.fromisoformat(target_date)
    except ValueError:
        return {"kwh": 0.0, "source": "invalid_target_date", "target_date": target_date}
    day_start = _parse_hhmm(os.getenv("NIGHT8_DAY_START_HHMM", "07:00"), default="07:00")
    day_end = _parse_hhmm(os.getenv("NIGHT8_DAY_END_HHMM", "23:00"), default="23:00")
    period_start, period_end, close_day = billing_period_for_target(target_day)
    previous_start, previous_end, _ = previous_billing_period_for_target(target_day)
    previous_daily = _daytime_buy_by_day(
        rows,
        period_start=previous_start,
        period_end=previous_end,
        day_start=day_start,
        day_end=day_end,
    )
    expected_previous_days = (previous_end - previous_start).days + 1
    remaining = max(0, (period_end - target_day).days)
    base = monthly_day_buy_kwh_before_target(rows, target_date=target_date)
    sample_days = sorted(previous_daily)
    result: dict[str, object] = {
        "target_date": target_date,
        "billing_period_start": period_start.isoformat(),
        "billing_period_end": period_end.isoformat(),
        "billing_close_day": close_day,
        "day_window": f"{day_start:%H:%M}-{day_end:%H:%M}",
        "previous_billing_period_start": previous_start.isoformat(),
        "previous_billing_period_end": previous_end.isoformat(),
        "previous_billing_period_expected_days": expected_previous_days,
        "previous_billing_period_sample_day_count": len(sample_days),
        "remaining_days_after_target": remaining,
        "sample_days": [day.isoformat() for day in sample_days[-10:]],
    }
    if len(previous_daily) < expected_previous_days:
        return {
            "kwh": 0.0,
            "source": "previous_billing_period_incomplete",
            **result,
            "current_day_buy_before_target_kwh": base.get("kwh", 0.0),
        }

    previous_total = sum(previous_daily.values())
    target_offset = max(0, (target_day - period_start).days)
    previous_target_day = min(
        previous_start + timedelta(days=target_offset),
        previous_end,
    )
    previous_target_day_buy = previous_daily[previous_target_day]
    raw_current_before_target = base.get("kwh", 0.0)
    current_before_target = (
        float(raw_current_before_target)
        if isinstance(raw_current_before_target, (int, float))
        else 0.0
    )
    return {
        "kwh": round(
            max(0.0, previous_total - current_before_target - previous_target_day_buy),
            4,
        ),
        "source": "previous_billing_period_daytime_buy",
        **result,
        "previous_billing_period_total_kwh": round(previous_total, 4),
        "previous_reference_target_date": previous_target_day.isoformat(),
        "previous_reference_target_day_buy_kwh": round(previous_target_day_buy, 4),
        "current_day_buy_before_target_kwh": round(current_before_target, 4),
        "projected_month_end_before_target_kwh": round(
            current_before_target
            + max(0.0, previous_total - current_before_target - previous_target_day_buy),
            4,
        ),
    }
