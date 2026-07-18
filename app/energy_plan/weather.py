from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WeatherHistoryFetchResult:
    rows: list[dict[str, object]]
    requested_dates: list[str]
    received_dates: list[str]
    missing_dates: list[str]
    errors: list[dict[str, object]]
    cache_hit_dates: list[str]
    requested_periods: list[dict[str, object]]
