"""Pure SOC, charge-rate, and monitor-timing calculations for Cloud Job."""

from __future__ import annotations

import math
import os
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from app.kpnet.monitoring_history import iter_charge_soc_points


PlanMeta = dict[str, float | str | None]


def hhmm_after_delay(*, timezone_name: str, delay_seconds: int) -> str:
    return (datetime.now(ZoneInfo(timezone_name)) + timedelta(seconds=max(0, delay_seconds))).strftime("%H:%M")


def required_charge_percent_from_plan(plan_meta: PlanMeta) -> float:
    target_soc = max(0.0, float(plan_meta.get("target_soc_7_percent", 0.0) or 0.0))
    soc_now_raw = plan_meta.get("soc_now_percent")
    if isinstance(soc_now_raw, (int, float)):
        return max(0.0, target_soc - max(0.0, min(100.0, float(soc_now_raw))))
    capacity_kwh = plan_meta.get("effective_capacity_kwh")
    required_kwh = plan_meta.get("required_night_charge_kwh", 0.0)
    if isinstance(capacity_kwh, (int, float)) and isinstance(required_kwh, (int, float)) and capacity_kwh > 0 and required_kwh > 0:
        return max(0.0, 100.0 * float(required_kwh) / float(capacity_kwh))
    return target_soc


def estimate_required_charge_kwh(*, plan_meta: PlanMeta, latest_soc_percent: float | None) -> float:
    target_soc = max(0.0, min(100.0, float(plan_meta.get("target_soc_7_percent", 0.0) or 0.0)))
    capacity_kwh = plan_meta.get("effective_capacity_kwh")
    if latest_soc_percent is not None and isinstance(capacity_kwh, (int, float)) and capacity_kwh > 0:
        soc_now = max(0.0, min(100.0, latest_soc_percent))
        efficiency = max(0.7, float(os.getenv("KP_NIGHT_CHARGE_EFFICIENCY", "0.93").strip() or "0.93"))
        return max(0.0, ((target_soc - soc_now) / 100.0 * float(capacity_kwh)) / efficiency)
    return max(0.0, float(plan_meta.get("required_night_charge_kwh", 0.0) or 0.0))


# readable-code-audit: skip STRUCT-04 — CSV filtering, robust rate estimation, and diagnostic counts must use the same source rows
# readable-code-audit: skip DUP-01 — this 14-day trend and EWMA forecast the next forced-charge stop time, unlike KP-NET's current-setting median.
def estimate_forced_charge_rate_percent_per_hour(csv_paths: list[Path]) -> dict[str, float | int | str]:
    fallback = float(os.getenv("ADJUST03_FORCE_CHARGE_RATE_FALLBACK_PERCENT_PER_HOUR", "35").strip() or "35")
    min_rate = float(os.getenv("ADJUST03_FORCE_CHARGE_RATE_MIN_PERCENT_PER_HOUR", "25").strip() or "25")
    max_rate = float(os.getenv("ADJUST03_FORCE_CHARGE_RATE_MAX_PERCENT_PER_HOUR", "50").strip() or "50")
    min_charge_kwh = float(os.getenv("ADJUST03_FORCE_CHARGE_SAMPLE_MIN_KWH", "1.2").strip() or "1.2")
    if max_rate < min_rate:
        max_rate = min_rate
    samples_by_day: dict[date, list[float]] = {}
    previous: tuple[datetime, float, float] | None = None
    for point in iter_charge_soc_points(csv_paths):
        if previous is not None:
            previous_dt, previous_soc, _previous_charge = previous
            observed_at, soc_percent, charge_kwh = point
            hours = (observed_at - previous_dt).total_seconds() / 3600.0
            delta_soc = soc_percent - previous_soc
            if 0 < hours <= 2.0 and delta_soc > 0 and charge_kwh >= min_charge_kwh:
                samples_by_day.setdefault(observed_at.date(), []).append(delta_soc / hours)
        previous = point
    daily_rates = [(day, statistics.median(values)) for day, values in sorted(samples_by_day.items()) if values]
    if not daily_rates:
        raw_rate = fallback
        source = "fallback-forced-charge-soc-rate"
    else:
        latest_day = daily_rates[-1][0]
        recent = [(day, rate) for day, rate in daily_rates if day >= latest_day - timedelta(days=13)]
        ewma_rate = recent[0][1]
        for _, daily_rate in recent[1:]:
            ewma_rate = 0.45 * daily_rate + 0.55 * ewma_rate
        origin = recent[0][0]
        x_values = [(day - origin).days for day, _ in recent]
        y_values = [rate for _, rate in recent]
        slopes = [(y_values[j] - y_values[i]) / (x_values[j] - x_values[i]) for i in range(len(recent)) for j in range(i + 1, len(recent)) if x_values[j] != x_values[i]]
        degradation_slope = min(0.0, statistics.median(slopes)) if slopes else 0.0
        intercept = statistics.median(rate - degradation_slope * x for x, (_, rate) in zip(x_values, recent))
        projected_x = (latest_day + timedelta(days=1) - origin).days
        trend_rate = intercept + degradation_slope * projected_x
        ordered_rates = sorted(y_values)
        lower_index = max(0, round((len(ordered_rates) - 1) * 0.15))
        trend_rate = max(ordered_rates[lower_index], min(statistics.median(y_values), trend_rate))
        raw_rate = 0.60 * trend_rate + 0.40 * ewma_rate
        source = "csv-14d-degradation-trend-ewma-soc-rate"
    return {
        "percent_per_hour": max(min_rate, min(max_rate, raw_rate)), "raw_percent_per_hour": raw_rate,
        "sample_count": len(daily_rates), "interval_sample_count": sum(len(values) for values in samples_by_day.values()),
        "lookback_days": 14, "degradation_trend_weight": 0.60, "ewma_weight": 0.40,
        "sample_min_charge_kwh": min_charge_kwh, "source": source,
    }


def estimate_required_charge_percent_for_schedule(*, plan_meta: PlanMeta, latest_soc_percent: float | None) -> float:
    target_soc = max(0.0, min(100.0, float(plan_meta.get("target_soc_7_percent", 0.0) or 0.0)))
    if latest_soc_percent is not None:
        return max(0.0, target_soc - max(0.0, min(100.0, latest_soc_percent)))
    return required_charge_percent_from_plan(plan_meta)


def estimate_forced_charge_minutes(
    *, plan_meta: PlanMeta, latest_soc_percent: float | None, csv_paths: list[Path],
    rate_estimator: Callable[[list[Path]], dict[str, float | int | str]] = estimate_forced_charge_rate_percent_per_hour,
) -> tuple[int, dict[str, float | int | str]]:
    charge_rate_info = rate_estimator(csv_paths)
    required_percent = estimate_required_charge_percent_for_schedule(plan_meta=plan_meta, latest_soc_percent=latest_soc_percent)
    rate = max(1.0, float(charge_rate_info["percent_per_hour"]))
    minutes = int(math.ceil((required_percent / rate) * 60.0)) if required_percent > 0 else 0
    charge_rate_info["required_charge_percent"] = required_percent
    return minutes, charge_rate_info


class ForcedChargeCompletionEstimator:
    """Estimate the next SOC confirmation time while forced charging is active."""

    def __init__(self, *, rate_percent_per_hour: float, confirm_before_minutes: int = 5) -> None:
        self.rate_percent_per_hour = max(1.0, float(rate_percent_per_hour))
        self.confirm_before_minutes = max(0, int(confirm_before_minutes))

    def remaining_minutes(self, *, target_soc: float, latest_soc: float) -> int:
        required_percent = max(0.0, min(100.0, target_soc) - max(0.0, min(100.0, latest_soc)))
        return int(math.ceil((required_percent / self.rate_percent_per_hour) * 60.0)) if required_percent > 0 else 0

    def next_check_seconds(self, *, target_soc: float, latest_soc: float | None, fallback_poll_seconds: int, cutoff_seconds: int) -> int:
        fallback = max(60, int(fallback_poll_seconds))
        cutoff = max(0, int(cutoff_seconds))
        if cutoff <= 0:
            return 0
        if latest_soc is None:
            return min(fallback, cutoff)
        remaining = self.remaining_minutes(target_soc=target_soc, latest_soc=latest_soc)
        if remaining <= 0:
            return 0
        return min(max(60, (remaining - self.confirm_before_minutes) * 60), fallback, cutoff)
