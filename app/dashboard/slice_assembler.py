"""Backend-neutral dashboard slice assembly."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.dashboard.aggregation import _aggregation_close_day, _build_cost_monthly, _build_energy_daily, _today_jst_iso
from app.dashboard.models import DashboardData, DashboardRawData, DashboardSlice
from app.dashboard.repositories import DashboardQuerySnapshot
from app.dashboard.schedule import _build_latest_schedule_from_events, _default_latest_schedule
from app.dashboard.service import assemble_dashboard_slice
from app.dashboard.warnings import build_dashboard_warnings


def extract_pv_forecast_diagnostics(data: dict[str, Any]) -> dict[str, Any]:
    rationale = data.get("decision_rationale")
    optimization = data.get("daytime_soc_optimization")
    source = rationale if isinstance(rationale, dict) else optimization if isinstance(optimization, dict) else {}
    forecast = data.get("forecast")
    def mapping(key: str) -> dict[str, Any]:
        value = source.get(key) if isinstance(source, dict) else None
        return value if isinstance(value, dict) else {}
    return {"plan_date": forecast.get("date") if isinstance(forecast, dict) else None, "physical": mapping("pv_physical_forecast"), "forecast_correction": mapping("forecast_correction"), "hourly_weather_pv_shape": mapping("hourly_weather_pv_shape"), "overnight_load_forecast": mapping("overnight_discharge_guard")}


def read_latest_pv_forecast_diagnostics() -> dict[str, Any]:
    path = Path(os.getenv("NIGHT_CHARGE_PLAN_PATH", "artifacts/night_charge_plan.json"))
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return extract_pv_forecast_diagnostics(data) if isinstance(data, dict) else {}


def empty_dashboard_slice(*, window_days: int, schedule: dict[str, Any], global_oldest: str | None = None, global_newest: str | None = None) -> DashboardSlice:
    return DashboardSlice(data=DashboardData([], [], [], [], [], latest_schedule=schedule), meta={"window_days": window_days, "oldest_loaded_date": None, "newest_loaded_date": None, "global_oldest_date": global_oldest, "global_newest_date": global_newest, "has_more_before": False})


def dashboard_warnings(*, latest_schedule: dict[str, Any], battery_daily: list[dict[str, Any]], energy_daily: list[dict[str, Any]], end_date_iso: str, today_jst_iso: str | None = None) -> list[dict[str, Any]]:
    return build_dashboard_warnings(latest_schedule=latest_schedule, battery_daily=battery_daily, energy_daily=energy_daily, end_date_iso=end_date_iso, today_jst_iso=today_jst_iso or _today_jst_iso())


def dashboard_meta(*, window_days: int, global_oldest_date: str | None, global_newest_date: str | None, pv_daily: list[dict[str, Any]], cost_daily: list[dict[str, Any]], battery_daily: list[dict[str, Any]], energy_daily: list[dict[str, Any]] | None = None, forecast_hourly: list[dict[str, Any]] | None = None, battery_flow_daily: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [*pv_daily, *cost_daily, *battery_daily, *(energy_daily or []), *(forecast_hourly or []), *(battery_flow_daily or [])]
    dates = [str(row["date"]) for row in rows if row.get("date")]
    oldest, newest = (min(dates), max(dates)) if dates else (None, None)
    return {"window_days": window_days, "aggregation_close_day": _aggregation_close_day(), "oldest_loaded_date": oldest, "newest_loaded_date": newest, "global_oldest_date": global_oldest_date, "global_newest_date": global_newest_date, "has_more_before": bool(global_oldest_date and oldest and global_oldest_date < oldest)}


def build_dashboard_slice(raw: DashboardRawData, *, end_date_iso: str, window_days: int, pv_forecast_diagnostics: dict[str, Any] | None = None, daily_review: dict[str, Any] | None = None, daily_reviews: list[dict[str, Any]] | None = None, today_jst_iso: str | None = None) -> DashboardSlice:
    meta = dashboard_meta(window_days=window_days, global_oldest_date=raw.global_oldest, global_newest_date=raw.global_newest, pv_daily=raw.pv_daily, cost_daily=raw.cost_daily, battery_daily=raw.battery_daily, energy_daily=raw.energy_daily, forecast_hourly=raw.forecast_hourly, battery_flow_daily=raw.battery_flow_daily)
    return assemble_dashboard_slice(raw, meta=meta, warnings=dashboard_warnings(latest_schedule=raw.latest_schedule, battery_daily=raw.battery_daily, energy_daily=raw.energy_daily, end_date_iso=end_date_iso, today_jst_iso=today_jst_iso), pv_forecast_diagnostics=pv_forecast_diagnostics, daily_review=daily_review, daily_reviews=daily_reviews)


def build_slice_from_query_snapshot(snapshot: DashboardQuerySnapshot, *, window_days: int, include_static: bool, pv_forecast_diagnostics: dict[str, Any] | None = None, today_jst_iso: str | None = None) -> DashboardSlice:
    end_date_iso = snapshot.resolved_end_date
    try:
        end_obj = date.fromisoformat(end_date_iso) if end_date_iso else None
    except ValueError:
        end_obj = None
    if end_obj is None:
        return empty_dashboard_slice(window_days=window_days, schedule=_default_latest_schedule(), global_oldest=snapshot.global_oldest_date, global_newest=snapshot.global_newest_date)
    assert end_date_iso is not None
    start_date = (end_obj - timedelta(days=max(1, window_days) - 1)).isoformat()
    energy_daily = _build_energy_daily(start_date=start_date, end_date_iso=end_date_iso, pv_daily=snapshot.pv_daily, monitoring_daily=snapshot.monitoring_daily, forecast_hourly=snapshot.forecast_hourly)
    schedule = _default_latest_schedule(plan_date=end_date_iso)
    if include_static:
        schedule = _build_latest_schedule_from_events(event_rows=snapshot.settings_events, battery_row=snapshot.latest_battery, plan_date=end_date_iso)
    raw = DashboardRawData(pv_daily=snapshot.pv_daily, cost_daily=snapshot.cost_daily, cost_monthly=_build_cost_monthly(snapshot.all_cost_daily) if include_static else [], battery_daily=snapshot.battery_daily, model_parameters=snapshot.model_parameters, battery_flow_daily=snapshot.battery_flow_daily, energy_daily=energy_daily, forecast_hourly=snapshot.forecast_hourly, latest_schedule=schedule, global_oldest=snapshot.global_oldest_date, global_newest=snapshot.global_newest_date)
    diagnostics = pv_forecast_diagnostics if pv_forecast_diagnostics is not None else (read_latest_pv_forecast_diagnostics() if include_static else {})
    return build_dashboard_slice(raw, end_date_iso=end_date_iso, window_days=window_days, pv_forecast_diagnostics=diagnostics, today_jst_iso=today_jst_iso)
