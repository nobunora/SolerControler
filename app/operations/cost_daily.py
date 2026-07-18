from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from app.tariff import tiered_day_increment_cost
from app.time_windows import DailyWindow, parse_hhmm


@dataclass(frozen=True)
class EnergyInterval:
    timestamp: str
    load_kwh: float | None
    buy_kwh: float | None


@dataclass(frozen=True)
class DailyCostPolicy:
    tariff_mode: str = "flat"
    day_rate_yen_per_kwh: float = 0.0
    day_start_hhmm: str = "07:00"
    day_end_hhmm: str = "23:00"
    day_tier1_upper_kwh: float = 90.0
    day_tier2_upper_kwh: float = 230.0
    day_rate_tier1_yen: float = 31.80
    day_rate_tier2_yen: float = 39.10
    day_rate_tier3_yen: float = 43.62
    night_rate_yen: float = 28.85


@dataclass(frozen=True)
class DailyCostResult:
    date: str
    self_consumption_kwh: float
    savings_yen: float
    cumulative_kwh: float
    cumulative_yen: float


def calculate_daily_costs(
    intervals: list[EnergyInterval],
    policy: DailyCostPolicy,
) -> list[DailyCostResult]:
    mode = (policy.tariff_mode or "flat").strip().lower()
    if mode == "flat":
        by_day: dict[str, float] = defaultdict(float)
        for interval in intervals:
            timestamp = str(interval.timestamp or "").strip()
            if len(timestamp) < 10:
                continue
            load_kwh = float(interval.load_kwh or 0.0)
            buy_kwh = float(interval.buy_kwh or 0.0)
            by_day[timestamp[:10]] += max(0.0, load_kwh - buy_kwh)
        return _with_cumulative(
            (day, self_kwh, self_kwh * policy.day_rate_yen_per_kwh)
            for day, self_kwh in sorted(by_day.items())
        )
    if mode != "night8_tiered":
        raise ValueError(f"unsupported tariff_mode: {policy.tariff_mode}")

    window = DailyWindow(parse_hhmm(policy.day_start_hhmm), parse_hhmm(policy.day_end_hhmm))
    metrics: dict[str, dict[str, float]] = defaultdict(
        lambda: {"self": 0.0, "self_day": 0.0, "self_night": 0.0, "buy_day": 0.0, "buy_night": 0.0}
    )
    for interval in intervals:
        try:
            parsed_timestamp = datetime.fromisoformat(str(interval.timestamp or "").strip())
        except ValueError:
            continue
        load_kwh = max(0.0, float(interval.load_kwh or 0.0))
        buy_kwh = max(0.0, float(interval.buy_kwh or 0.0))
        self_kwh = max(0.0, load_kwh - buy_kwh)
        day = parsed_timestamp.date().isoformat()
        item = metrics[day]
        item["self"] += self_kwh
        if window.contains(parsed_timestamp.time().replace(tzinfo=None)):
            item["self_day"] += self_kwh
            item["buy_day"] += buy_kwh
        else:
            item["self_night"] += self_kwh
            item["buy_night"] += buy_kwh

    savings: dict[str, float] = {}
    days_by_month: dict[str, list[str]] = defaultdict(list)
    for day in sorted(metrics):
        days_by_month[day[:7]].append(day)
    for month in sorted(days_by_month):
        actual_day_total = 0.0
        counterfactual_day_total = 0.0
        for day in days_by_month[month]:
            item = metrics[day]
            actual = tiered_day_increment_cost(
                previous_kwh=actual_day_total, delta_kwh=item["buy_day"],
                tier1_upper_kwh=policy.day_tier1_upper_kwh, tier2_upper_kwh=policy.day_tier2_upper_kwh,
                rate_tier1_yen=policy.day_rate_tier1_yen, rate_tier2_yen=policy.day_rate_tier2_yen,
                rate_tier3_yen=policy.day_rate_tier3_yen,
            ) + item["buy_night"] * policy.night_rate_yen
            counterfactual_day_kwh = item["buy_day"] + item["self_day"]
            counterfactual = tiered_day_increment_cost(
                previous_kwh=counterfactual_day_total, delta_kwh=counterfactual_day_kwh,
                tier1_upper_kwh=policy.day_tier1_upper_kwh, tier2_upper_kwh=policy.day_tier2_upper_kwh,
                rate_tier1_yen=policy.day_rate_tier1_yen, rate_tier2_yen=policy.day_rate_tier2_yen,
                rate_tier3_yen=policy.day_rate_tier3_yen,
            ) + (item["buy_night"] + item["self_night"]) * policy.night_rate_yen
            savings[day] = counterfactual - actual
            actual_day_total += item["buy_day"]
            counterfactual_day_total += counterfactual_day_kwh
    return _with_cumulative((day, metrics[day]["self"], savings[day]) for day in sorted(metrics))


def _with_cumulative(rows: Iterable[tuple[str, float, float]]) -> list[DailyCostResult]:
    results: list[DailyCostResult] = []
    cumulative_kwh = 0.0
    cumulative_yen = 0.0
    for day, self_kwh, savings_yen in rows:
        cumulative_kwh += self_kwh
        cumulative_yen += savings_yen
        results.append(DailyCostResult(day, self_kwh, savings_yen, cumulative_kwh, cumulative_yen))
    return results
