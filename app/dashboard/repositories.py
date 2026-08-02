from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.dashboard.models import DashboardSlice


@dataclass(frozen=True)
class DashboardLoadRequest:
    end_date: str | None
    window_days: int
    include_static: bool


@dataclass(frozen=True)
class DashboardQuerySnapshot:
    """Backend rows before common dashboard calculations and API assembly."""

    resolved_end_date: str | None
    global_oldest_date: str | None
    global_newest_date: str | None
    pv_daily: list[dict[str, Any]]
    cost_daily: list[dict[str, Any]]
    battery_daily: list[dict[str, Any]]
    forecast_hourly: list[dict[str, Any]]
    monitoring_daily: list[dict[str, Any]]
    battery_flow_daily: list[dict[str, Any]]
    all_cost_daily: list[dict[str, Any]]
    model_parameters: list[dict[str, Any]]
    settings_events: list[dict[str, Any]]
    latest_battery: dict[str, Any] | None


class DashboardRepository(Protocol):
    def load_dashboard(self, request: DashboardLoadRequest) -> DashboardSlice: ...
