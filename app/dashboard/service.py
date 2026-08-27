from __future__ import annotations

import math
from typing import Any

from app.dashboard.models import DashboardData, DashboardRawData, DashboardSlice


def merge_forecast_hourly_actuals(
    forecast_rows: list[dict[str, Any]],
    monitoring_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach hourly actual load and latest SOC values to forecast rows."""
    actuals: dict[tuple[str, int], dict[str, Any]] = {}
    for row in monitoring_rows:
        ts = str(row.get("ts") or "")
        if len(ts) < 13:
            continue
        try:
            hour = int(ts[11:13])
        except (TypeError, ValueError):
            continue
        if not 0 <= hour <= 23:
            continue
        key = (ts[:10], hour)
        acc = actuals.setdefault(
            key,
            {"actual_load_kwh": 0.0, "actual_soc_percent": None, "latest_sample_at": None},
        )
        latest = acc["latest_sample_at"]
        if latest is None or ts > latest:
            acc["latest_sample_at"] = ts

        try:
            load_kwh = float(row.get("load_kwh") or 0.0)
        except (TypeError, ValueError):
            load_kwh = None
        if load_kwh is not None and math.isfinite(load_kwh):
            acc["actual_load_kwh"] += load_kwh

        soc_value = row.get("soc_percent")
        try:
            soc = float(soc_value) if soc_value is not None else None
        except (TypeError, ValueError):
            soc = None
        if soc is not None and math.isfinite(soc) and 0.0 <= soc <= 100.0:
            current_soc_at = acc.get("actual_soc_at")
            if current_soc_at is None or ts >= current_soc_at:
                acc["actual_soc_percent"] = soc
                acc["actual_soc_at"] = ts

    merged: list[dict[str, Any]] = []
    for row in forecast_rows:
        item = dict(row)
        try:
            raw_hour = item.get("hour")
            key = (str(item.get("date") or ""), int(raw_hour)) if raw_hour is not None else ("", -1)
        except (TypeError, ValueError):
            key = ("", -1)
        actual = actuals.get(key)
        item["actual_load_kwh"] = actual["actual_load_kwh"] if actual else None
        item["actual_soc_percent"] = actual.get("actual_soc_percent") if actual else None
        item["latest_sample_at"] = actual["latest_sample_at"] if actual else None
        merged.append(item)
    merged.sort(key=lambda row: (str(row.get("date", "")), int(row.get("hour") or 0)))
    return merged


def assemble_dashboard_slice(
    raw: DashboardRawData,
    *,
    meta: dict[str, Any],
    warnings: list[dict[str, Any]],
    pv_forecast_diagnostics: dict[str, Any] | None = None,
    daily_review: dict[str, Any] | None = None,
    daily_reviews: list[dict[str, Any]] | None = None,
) -> DashboardSlice:
    """Build the stable API model from normalized backend rows."""
    return DashboardSlice(
        data=DashboardData(
            pv_daily=raw.pv_daily,
            cost_daily=raw.cost_daily,
            cost_monthly=raw.cost_monthly,
            battery_daily=raw.battery_daily,
            model_parameters=raw.model_parameters,
            battery_flow_daily=raw.battery_flow_daily,
            energy_daily=raw.energy_daily,
            forecast_hourly=raw.forecast_hourly,
            latest_schedule=raw.latest_schedule,
            dashboard_warnings=warnings,
            pv_forecast_diagnostics=pv_forecast_diagnostics or {},
            daily_review=daily_review or {},
            daily_reviews=daily_reviews or [],
        ),
        meta=meta,
    )
