"""Forecast-only Firestore persistence for the dedicated daily forecast job."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.backup.night_plan_archive import read_plan_file
from app.operations.domain import (
    extract_final_pv_source_from_plan,
    extract_final_pv_totals_from_plan,
    extract_hourly_forecast_from_plan,
)
from app.operations.forecast_snapshot import build_forecast_snapshot_rows, persist_forecast_snapshots
from app.parsing.numbers import to_float, to_int


def _complete_hourly_rows(rows: list[dict[str, Any]], *, target_date: str) -> bool:
    return len(rows) == 24 and {row.get("date") for row in rows} == {target_date} and {
        row.get("hour") for row in rows
    } == set(range(24))


def persist_forecast_only_plan(
    client: Any,
    *,
    plan_path: Path,
    target_date: str,
    timezone_name: str,
    recorded_at: str | None = None,
) -> int:
    """Persist a validated forecast without modifying control-plan documents."""
    data = read_plan_file(plan_path)
    forecast_value = data.get("forecast")
    forecast: dict[str, Any] = forecast_value if isinstance(forecast_value, dict) else {}
    forecast_date = str(forecast.get("date") or "").strip()
    hourly_rows = extract_hourly_forecast_from_plan(data)
    if forecast_date != target_date:
        raise ValueError("forecast target date does not match the intended date")
    if not _complete_hourly_rows(hourly_rows, target_date=target_date):
        raise ValueError("forecast hourly rows must contain exactly hours 0 through 23")

    result_value = data.get("result")
    plan_result: dict[str, Any] = result_value if isinstance(result_value, dict) else {}
    planned_target_soc_percent = to_float(plan_result.get("target_soc_7_percent"))
    planned_night_charge_kwh = to_float(plan_result.get("required_night_charge_kwh"))

    now = recorded_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    snapshot_rows = build_forecast_snapshot_rows(data, ingested_at=now, timezone=timezone_name)
    if not _complete_hourly_rows(snapshot_rows, target_date=target_date):
        raise ValueError("forecast snapshot rows must contain exactly hours 0 through 23")
    forecast_run_id = str(snapshot_rows[0].get("forecast_run_id") or "").strip()
    forecast_issued_at = str(snapshot_rows[0].get("issued_at") or "").strip()
    if not forecast_run_id:
        raise ValueError("forecast snapshot rows must include a forecast_run_id")

    snapshot_count = persist_forecast_snapshots(
        client,
        backend="firestore",
        night_plan_path=plan_path,
        timezone=timezone_name,
        ingested_at=now,
    )
    existing = list(client.collection("forecast_hourly").where("date", "==", target_date).stream())
    batch = client.batch()
    for document in existing:
        batch.delete(document.reference)
    for row in hourly_rows:
        batch.set(
            client.collection("forecast_hourly").document(f"{target_date}-{int(row['hour']):02d}"),
            {
                **row,
                "source": "forecast-only-hourly",
                "forecast_run_id": forecast_run_id,
                "forecast_issued_at": forecast_issued_at or None,
                "updated_at": now,
            },
            merge=True,
        )

    pv_totals = extract_final_pv_totals_from_plan(data)
    pv_value = data.get("pv_array_forecast")
    pv_forecast: dict[str, Any] = pv_value if isinstance(pv_value, dict) else {}
    calibration_value = pv_forecast.get("calibration")
    calibration: dict[str, Any] = calibration_value if isinstance(calibration_value, dict) else {}
    batch.set(
        client.collection("sunshine_daily").document(target_date),
        {
            "date": target_date,
            "forecast_hours": to_float(forecast.get("sun_hours")),
            "forecast_temp_c": to_float(forecast.get("temp_c")),
            "forecast_weather_code": to_int(forecast.get("weather_code")),
            "forecast_precipitation_sum_mm": to_float(forecast.get("precipitation_sum_mm")),
            "forecast_precipitation_probability_mean": to_float(forecast.get("precipitation_probability_mean")),
            "forecast_shortwave_radiation_sum_mj_m2": to_float(forecast.get("shortwave_radiation_sum_mj_m2")),
            "forecast_pv_total_kwh": to_float(pv_totals.get("total_kwh")),
            "forecast_pv_morning_kwh": to_float(pv_totals.get("morning_kwh")),
            "forecast_pv_midday_kwh": to_float(pv_totals.get("midday_kwh")),
            "forecast_pv_evening_kwh": to_float(pv_totals.get("evening_kwh")),
            "forecast_pv_calibration_factor": to_float(calibration.get("effective_factor") or calibration.get("factor")),
            "source": extract_final_pv_source_from_plan(data),
            "updated_at": now,
        },
        merge=True,
    )
    batch.set(
        client.collection("forecast_plans").document(target_date),
        {
            "date": target_date,
            "hourly_row_count": len(hourly_rows),
            "forecast_pv_total_kwh": to_float(pv_totals.get("total_kwh")),
            "forecast_source": extract_final_pv_source_from_plan(data),
            "forecast_run_id": forecast_run_id,
            "forecast_issued_at": forecast_issued_at or None,
            "planned_target_soc_percent": planned_target_soc_percent,
            "planned_night_charge_kwh": planned_night_charge_kwh,
            "forecast_json": json.dumps(forecast, ensure_ascii=False, separators=(",", ":")),
            "updated_at": now,
        },
        merge=True,
    )
    batch.commit()
    return snapshot_count
