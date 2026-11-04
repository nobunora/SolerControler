"""Shared interval-energy and instantaneous-state time contract.

CSV labels are preserved. Energy covers [label, label+30min). The SOC end-state
interpretation is inferred from the Nov 3/4 device API, not a vendor guarantee.
This explicit basis must travel with derived values; API measurement times are
already instantaneous and must never receive this conversion.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

CSV_TIME_BASIS = "kpnet_energy_start_soc_end_inferred_api_20261103_04_v1"
JST = ZoneInfo("Asia/Tokyo")


def csv_measurement_window(label: datetime) -> tuple[datetime, datetime]:
    start = label.replace(tzinfo=JST) if label.tzinfo is None else label.astimezone(JST)
    if start.minute not in (0, 30) or start.second or start.microsecond:
        raise ValueError("CSV measurement must have a half-hour boundary")
    return start, start + timedelta(minutes=30)


def csv_soc_effective_at(label: datetime) -> datetime:
    return csv_measurement_window(label)[1]


def hourly_time_contract(row: dict[str, Any]) -> dict[str, Any]:
    """Attach times from the same contract to forecast and measured hourly values."""
    result = dict(row)
    start = datetime.fromisoformat(str(row["date"])).replace(hour=int(row["hour"]), tzinfo=JST)
    result.update(interval_start=start.isoformat(), interval_end=(start+timedelta(hours=1)).isoformat(),
                  forecast_soc_at=start.isoformat())
    if not any(key in row for key in ("actual_soc_at_hour_start_percent", "actual_soc_at_half_hour_percent")):
        return result
    result["measurement_time_basis"] = CSV_TIME_BASIS
    points = []
    for minute, key in ((0, "actual_soc_at_hour_start_percent"), (30, "actual_soc_at_half_hour_percent")):
        label = start + timedelta(minutes=minute)
        raw = row.get(key)
        value = (float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool)
                 and math.isfinite(raw) and 0 <= raw <= 100 else None)
        points.append({"at": csv_soc_effective_at(label).isoformat(), "percent": value,
                       "csv_label_at": label.isoformat()})
    result["actual_soc_points"] = points
    return result
