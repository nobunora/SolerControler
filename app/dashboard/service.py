from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

from app.dashboard.models import DashboardData, DashboardRawData, DashboardSlice


def merge_forecast_hourly_actuals(
    forecast_rows: list[dict[str, Any]],
    monitoring_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Display saved halfhours and observed SOC without manufacturing missing values."""
    actuals: dict[tuple[str, int], dict[str, Any]] = {}
    for row in monitoring_rows:
        try:
            stamp = datetime.fromisoformat(str(row.get("ts") or "").replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(ZoneInfo("Asia/Tokyo"))
        if stamp.minute not in (0, 30) or stamp.second or stamp.microsecond:
            continue
        ts = stamp.isoformat()
        key = (stamp.date().isoformat(), stamp.hour)
        acc = actuals.setdefault(
            key,
            {
                "actual_load_kwh": None,
                "actual_pv_kwh": None,
                "energy_slots": {"actual_pv_kwh": {}, "actual_load_kwh": {}},
                "actual_soc_percent": None,
                "latest_sample_at": None,
                "first_sample_at": None,
                "opening_soc_percent": None,
            },
        )
        first = acc["first_sample_at"]
        if first is None or ts < first:
            acc["first_sample_at"] = ts
        latest = acc["latest_sample_at"]
        if latest is None or ts > latest:
            acc["latest_sample_at"] = ts

        for source, field in (("pv_kwh", "actual_pv_kwh"), ("load_kwh", "actual_load_kwh")):
            value = row.get(source)
            numeric = float(value) if not isinstance(value, bool) and isinstance(value, (int, float)) else None
            if numeric is not None and (not math.isfinite(numeric) or numeric < 0):
                numeric = None
            slots = acc["energy_slots"][field]
            if stamp.minute not in slots:
                slots[stamp.minute] = numeric
            elif slots[stamp.minute] != numeric:
                slots[stamp.minute] = None

        soc_value = row.get("soc_percent")
        soc = float(soc_value) if not isinstance(soc_value, bool) and isinstance(soc_value, (int, float)) else None
        if soc is not None and math.isfinite(soc) and 0.0 <= soc <= 100.0:
            opening_soc_at = acc.get("opening_soc_at")
            if opening_soc_at is None or ts < opening_soc_at:
                acc["opening_soc_percent"] = soc
                acc["opening_soc_at"] = ts
            current_soc_at = acc.get("actual_soc_at")
            if current_soc_at is None or ts >= current_soc_at:
                acc["actual_soc_percent"] = soc
                acc["actual_soc_at"] = ts

    for aggregate in actuals.values():
        for field, slots in aggregate["energy_slots"].items():
            if set(slots) == {0, 30} and all(value is not None for value in slots.values()):
                aggregate[field] = sum(slots.values())

    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for row in forecast_rows:
        item = dict(row)
        try:
            raw_hour = item.get("hour")
            key = (str(item.get("date") or ""), int(raw_hour)) if raw_hour is not None else ("", -1)
        except (TypeError, ValueError):
            key = ("", -1)
        actual = actuals.get(key)
        for field in ("actual_pv_kwh", "actual_load_kwh", "actual_soc_percent", "first_sample_at",
                      "opening_soc_percent", "latest_sample_at"):
            if item.get(field) is None:
                item[field] = actual.get(field) if actual else None
        seen.add(key)
        merged.append(item)
    for key, actual in actuals.items():
        if key not in seen:
            merged.append({"date": key[0], "hour": key[1], "forecast_pv_kwh": None,
                           "forecast_load_kwh": None, "forecast_charge_kwh": None,
                           "forecast_run_id": None, **{field: actual.get(field) for field in
                           ("actual_pv_kwh", "actual_load_kwh", "actual_soc_percent", "first_sample_at",
                            "opening_soc_percent", "latest_sample_at")}})
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
    private_markers = {"_raw_plan_sha256", "_control_version_id", "_plan_generated_at"}
    public_hourly = [{key: value for key, value in row.items() if key not in private_markers}
                     for row in raw.forecast_hourly]
    public_energy = [{key: value for key, value in row.items() if key not in private_markers}
                     for row in raw.energy_daily]
    return DashboardSlice(
        data=DashboardData(
            pv_daily=raw.pv_daily,
            cost_daily=raw.cost_daily,
            cost_monthly=raw.cost_monthly,
            battery_daily=raw.battery_daily,
            model_parameters=raw.model_parameters,
            battery_flow_daily=raw.battery_flow_daily,
            energy_daily=public_energy,
            forecast_hourly=public_hourly,
            latest_schedule=raw.latest_schedule,
            dashboard_warnings=warnings,
            pv_forecast_diagnostics=pv_forecast_diagnostics or {},
            daily_review=daily_review or {},
            daily_reviews=daily_reviews or [],
        ),
        meta=meta,
    )
