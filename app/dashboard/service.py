from __future__ import annotations

import math
from typing import Any

from app.dashboard.models import DashboardData, DashboardRawData, DashboardSlice


def merge_forecast_hourly_actuals(
    forecast_rows: list[dict[str, Any]],
    monitoring_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach hourly actual load totals without changing the forecast row contract."""
    actuals: dict[tuple[str, int], dict[str, Any]] = {}
    for row in monitoring_rows:
        ts = str(row.get("ts") or "")
        if len(ts) < 13:
            continue
        try:
            hour = int(ts[11:13])
            load_kwh = float(row.get("load_kwh") or 0.0)
        except (TypeError, ValueError):
            continue
        if not 0 <= hour <= 23 or not math.isfinite(load_kwh):
            continue
        key = (ts[:10], hour)
        acc = actuals.setdefault(key, {"actual_load_kwh": 0.0, "latest_sample_at": None})
        acc["actual_load_kwh"] += load_kwh
        latest = acc["latest_sample_at"]
        if latest is None or ts > latest:
            acc["latest_sample_at"] = ts

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
