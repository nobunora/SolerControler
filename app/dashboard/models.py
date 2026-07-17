from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DashboardData:
    pv_daily: list[dict[str, Any]]
    cost_daily: list[dict[str, Any]]
    cost_monthly: list[dict[str, Any]]
    battery_daily: list[dict[str, Any]]
    model_parameters: list[dict[str, Any]]
    battery_flow_daily: list[dict[str, Any]] = field(default_factory=list)
    energy_daily: list[dict[str, Any]] = field(default_factory=list)
    forecast_hourly: list[dict[str, Any]] = field(default_factory=list)
    latest_schedule: dict[str, Any] = field(default_factory=dict)
    dashboard_warnings: list[dict[str, Any]] = field(default_factory=list)
    pv_forecast_diagnostics: dict[str, Any] = field(default_factory=dict)
    daily_review: dict[str, Any] = field(default_factory=dict)
    daily_reviews: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class DashboardRawData:
    pv_daily: list[dict[str, Any]]
    cost_daily: list[dict[str, Any]]
    cost_monthly: list[dict[str, Any]]
    battery_daily: list[dict[str, Any]]
    model_parameters: list[dict[str, Any]]
    battery_flow_daily: list[dict[str, Any]]
    energy_daily: list[dict[str, Any]]
    forecast_hourly: list[dict[str, Any]]
    latest_schedule: dict[str, Any]
    global_oldest: str | None
    global_newest: str | None


@dataclass(frozen=True)
class DashboardSlice:
    data: DashboardData
    meta: dict[str, Any]
