from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from google.cloud import firestore

from app.operations.cost_daily import DailyCostPolicy, EnergyInterval, calculate_daily_costs
from app.night_plan_archive import (
    build_night_plan_firestore_document,
    read_plan_file,
    upload_night_plan_to_gcs,
)
from app.operations.domain import (
    extract_battery_daily_from_summary as _extract_battery_daily_from_summary,
    extract_final_pv_source_from_plan as _extract_final_pv_source_from_plan,
    extract_final_pv_totals_from_plan as _extract_final_pv_totals_from_plan,
    extract_hourly_forecast_from_plan as _extract_hourly_forecast_from_plan,
    fetch_open_meteo_daily_actual as _fetch_open_meteo_daily_actual,
    is_within_window as _is_within_window,
    iter_monitoring_rows as _iter_monitoring_rows,
    parse_hhmm_to_minute as _parse_hhmm_to_minute,
    read_json_if_exists as _read_json_if_exists,
    read_summary as _read_summary,
    tiered_increment_cost as _tiered_day_increment_cost,
)
from app.utils import env, to_float, to_int


def open_firestore():
    project_id = os.getenv("FIRESTORE_PROJECT_ID", "").strip() or None
    database_id = os.getenv("FIRESTORE_DATABASE_ID", "").strip() or "(default)"
    if project_id:
        return firestore.Client(project=project_id, database=database_id)
    return firestore.Client(database=database_id)


def ensure_schema(_client) -> None:
    # Firestore is schemaless.
    return


def pipeline_run_exists(client, *, run_key: str) -> bool:
    snap = client.collection("pipeline_runs").document(run_key).get()
    return snap.exists


def upsert_pipeline_run(
    client,
    *,
    run_key: str,
    slot: str,
    csv_run_id: str | None,
    settings_run_id: str | None,
    csv_rows_upserted: int,
    recorded_at: str,
) -> None:
    client.collection("pipeline_runs").document(run_key).set(
        {
            "run_key": run_key,
            "slot": slot,
            "csv_run_id": csv_run_id,
            "settings_run_id": settings_run_id,
            "csv_rows_upserted": int(csv_rows_upserted),
            "recorded_at": recorded_at,
        },
        merge=True,
    )


def ingest_monitoring_csvs(
    client,
    *,
    csv_paths: list[Path],
    ingested_at: str,
) -> int:
    upserted = 0
    batch = client.batch()
    batch_count = 0
    for csv_path in csv_paths:
        for row in _iter_monitoring_rows(csv_path):
            doc_ref = client.collection("monitoring_samples").document(row["ts"])
            payload = {
                **row,
                "source_csv": str(csv_path),
                "ingested_at": ingested_at,
            }
            batch.set(doc_ref, payload, merge=True)
            upserted += 1
            batch_count += 1
            if batch_count >= 450:
                batch.commit()
                batch = client.batch()
                batch_count = 0
    if batch_count > 0:
        batch.commit()
    return upserted


def ingest_sunshine_from_night_plan(
    client,
    *,
    night_plan_path: Path,
    timezone: str,
    ingested_at: str,
) -> None:
    if not night_plan_path.exists():
        return
    data = read_plan_file(night_plan_path)
    forecast = data.get("forecast", {})
    forecast_date = str(forecast.get("date", "")).strip()
    tomorrow_hours = forecast.get("sun_hours")
    tomorrow_temp = forecast.get("temp_c")
    tomorrow_weather_code = forecast.get("weather_code")
    tomorrow_precip_sum = forecast.get("precipitation_sum_mm")
    tomorrow_precip_probability = forecast.get("precipitation_probability_mean")
    tomorrow_shortwave = forecast.get("shortwave_radiation_sum_mj_m2")
    pv_forecast = data.get("pv_array_forecast", {})
    pv_totals = _extract_final_pv_totals_from_plan(data)
    pv_calibration = pv_forecast.get("calibration", {}) if isinstance(pv_forecast, dict) else {}
    forecast_source = _extract_final_pv_source_from_plan(data)
    lat = float(env("FORECAST_LATITUDE", default="35.67452"))
    lon = float(env("FORECAST_LONGITUDE", default="139.48216"))

    if forecast_date:
        archive_info = upload_night_plan_to_gcs(data, forecast_date=forecast_date)
        plan_doc = build_night_plan_firestore_document(
            data,
            source="night-charge-plan",
            updated_at=ingested_at,
            archive_info=archive_info,
        )
        client.collection("night_charge_plans").document(forecast_date).set(plan_doc, merge=True)
        client.collection("night_charge_plans").document("latest").set(
            {**plan_doc, "plan_json": json.dumps(data, ensure_ascii=False, separators=(",", ":"))},
            merge=True,
        )
        client.collection("sunshine_daily").document(forecast_date).set(
            {
                "date": forecast_date,
                "forecast_hours": float(tomorrow_hours) if tomorrow_hours is not None else None,
                "forecast_temp_c": float(tomorrow_temp) if tomorrow_temp is not None else None,
                "forecast_weather_code": to_int(tomorrow_weather_code),
                "forecast_precipitation_sum_mm": to_float(tomorrow_precip_sum),
                "forecast_precipitation_probability_mean": to_float(tomorrow_precip_probability),
                "forecast_shortwave_radiation_sum_mj_m2": to_float(tomorrow_shortwave),
                "forecast_pv_total_kwh": to_float(pv_totals.get("total_kwh") if isinstance(pv_totals, dict) else None),
                "forecast_pv_morning_kwh": to_float(pv_totals.get("morning_kwh") if isinstance(pv_totals, dict) else None),
                "forecast_pv_midday_kwh": to_float(pv_totals.get("midday_kwh") if isinstance(pv_totals, dict) else None),
                "forecast_pv_evening_kwh": to_float(pv_totals.get("evening_kwh") if isinstance(pv_totals, dict) else None),
                "forecast_pv_calibration_factor": to_float(
                    (
                        pv_calibration.get("effective_factor")
                        if isinstance(pv_calibration, dict)
                        else None
                    )
                    or (pv_calibration.get("factor") if isinstance(pv_calibration, dict) else None)
                ),
                "source": forecast_source,
                "updated_at": ingested_at,
            },
            merge=True,
        )
        hourly_rows = _extract_hourly_forecast_from_plan(data)
        batch = client.batch()
        batch_count = 0
        for doc in client.collection("forecast_hourly").where("date", "==", forecast_date).stream():
            batch.delete(doc.reference)
            batch_count += 1
            if batch_count >= 450:
                batch.commit()
                batch = client.batch()
                batch_count = 0
        for row in hourly_rows:
            doc_id = f"{forecast_date}-{int(row['hour']):02d}"
            doc_ref = client.collection("forecast_hourly").document(doc_id)
            batch.set(
                doc_ref,
                {
                    **row,
                    "source": "night-charge-plan-hourly",
                    "updated_at": ingested_at,
                },
                merge=True,
            )
            batch_count += 1
            if batch_count >= 450:
                batch.commit()
                batch = client.batch()
                batch_count = 0
        if batch_count > 0:
            batch.commit()

    today_date = datetime.now().date().isoformat()
    actual_weather: dict[str, float | int | None] = {}
    try:
        actual_weather = _fetch_open_meteo_daily_actual(
            lat=lat,
            lon=lon,
            date_ymd=today_date,
            timezone=timezone,
        )
    except Exception:
        actual_weather = {}

    if any(actual_weather.get(key) is not None for key in actual_weather):
        client.collection("sunshine_daily").document(today_date).set(
            {
                "date": today_date,
                "actual_hours": actual_weather.get("actual_hours"),
                "actual_temp_c": actual_weather.get("actual_temp_c"),
                "actual_weather_code": actual_weather.get("actual_weather_code"),
                "actual_precipitation_sum_mm": actual_weather.get("actual_precipitation_sum_mm"),
                "actual_shortwave_radiation_sum_mj_m2": actual_weather.get("actual_shortwave_radiation_sum_mj_m2"),
                "source": "open-meteo-archive",
                "updated_at": ingested_at,
            },
            merge=True,
        )


def ingest_settings_summary(
    client,
    *,
    settings_summary_path: Path,
    slot: str,
    ingested_at: str,
) -> None:
    if not settings_summary_path.exists():
        return
    summary = _read_summary(settings_summary_path)
    run_id = str(summary.get("run_id", settings_summary_path.parent.name))
    settings_results = summary.get("setting_results", [])
    batch = client.batch()
    batch_count = 0
    for idx, item in enumerate(settings_results):
        profile = str(item.get("profile", "unknown"))
        status = str(item.get("status", "unknown"))
        changed_fields = item.get("changed_fields", [])
        detail = dict(item)
        night_plan = summary.get("night_charge_plan")
        if slot in {"3", "03"} and isinstance(night_plan, dict):
            detail.update(
                {
                    "plan_date": night_plan.get("forecast_date"),
                    "charge_start_time": night_plan.get("charge_start_time"),
                    "charge_end_time": night_plan.get("charge_end_time"),
                    "soc_charge_mode": night_plan.get("soc_charge_mode"),
                    "battery_operating_mode": night_plan.get("battery_operating_mode_preference"),
                    "estimated_charge_power_kw": night_plan.get("estimated_charge_power_kw"),
                    "estimated_charge_minutes": night_plan.get("duration_minutes"),
                    "schedule_source": "03-dynamic",
                }
            )
        event_id = f"{run_id}-{slot}-{idx}-{profile}"
        doc_ref = client.collection("settings_events").document(event_id)
        batch.set(
            doc_ref,
            {
                "event_id": event_id,
                "run_id": run_id,
                "slot": slot,
                "profile": profile,
                "status": status,
                "changed_fields_json": changed_fields,
                "detail_json": detail,
                "source_doc_id": event_id,
                "recorded_at": ingested_at,
            },
            merge=True,
        )
        batch_count += 1
        if batch_count >= 450:
            batch.commit()
            batch = client.batch()
            batch_count = 0
    if batch_count > 0:
        batch.commit()


def record_planned_day_mode(client, *, settings_summary_path: Path, recorded_at: str) -> None:
    summary = json.loads(settings_summary_path.read_text(encoding="utf-8"))
    run_id = str(summary.get("run_id", settings_summary_path.parent.name))
    day_plan = summary.get("daytime_mode_plan")
    if not isinstance(day_plan, dict):
        return
    event_id = f"{run_id}-07-planned-green"
    client.collection("settings_events").document(event_id).set(
        {
            "event_id": event_id,
            "run_id": run_id,
            "slot": "07",
            "profile": "green-mode",
            "status": "planned-from-23",
            "changed_fields_json": [],
            "detail_json": day_plan,
            "recorded_at": recorded_at,
        },
        merge=True,
    )


def recalc_cost_daily(
    client,
    *,
    day_rate_yen_per_kwh: float,
    updated_at: str,
    tariff_mode: str = "flat",
    night8_day_start_hhmm: str = "07:00",
    night8_day_end_hhmm: str = "23:00",
    night8_day_tier1_upper_kwh: float = 90.0,
    night8_day_tier2_upper_kwh: float = 230.0,
    night8_day_rate_tier1_yen: float = 31.80,
    night8_day_rate_tier2_yen: float = 39.10,
    night8_day_rate_tier3_yen: float = 43.62,
    night8_night_rate_yen: float = 28.85,
) -> None:
    mode = (tariff_mode or "flat").strip().lower()
    rows = []
    for doc in client.collection("monitoring_samples").order_by("ts").stream():
        x = doc.to_dict() or {}
        x["ts"] = x.get("ts", doc.id)
        rows.append(x)

    results = calculate_daily_costs(
        [
            EnergyInterval(
                timestamp=str(row.get("ts", "")),
                load_kwh=row.get("load_kwh"),
                buy_kwh=row.get("buy_kwh"),
            )
            for row in rows
        ],
        DailyCostPolicy(
            tariff_mode=tariff_mode,
            day_rate_yen_per_kwh=day_rate_yen_per_kwh,
            day_start_hhmm=night8_day_start_hhmm,
            day_end_hhmm=night8_day_end_hhmm,
            day_tier1_upper_kwh=night8_day_tier1_upper_kwh,
            day_tier2_upper_kwh=night8_day_tier2_upper_kwh,
            day_rate_tier1_yen=night8_day_rate_tier1_yen,
            day_rate_tier2_yen=night8_day_rate_tier2_yen,
            day_rate_tier3_yen=night8_day_rate_tier3_yen,
            night_rate_yen=night8_night_rate_yen,
        ),
    )
    batch = client.batch()
    batch_count = 0
    for result in results:
        doc_ref = client.collection("cost_daily").document(result.date)
        batch.set(
            doc_ref,
            {
                "date": result.date,
                "self_consumption_kwh": result.self_consumption_kwh,
                "savings_yen": result.savings_yen,
                "cumulative_kwh": result.cumulative_kwh,
                "cumulative_yen": result.cumulative_yen,
                "updated_at": updated_at,
            },
            merge=True,
        )
        batch_count += 1
        if batch_count >= 450:
            batch.commit()
            batch = client.batch()
            batch_count = 0
    if batch_count > 0:
        batch.commit()
    return
def upsert_battery_daily_metrics(
    client,
    *,
    summary_path: Path,
    updated_at: str,
    night_plan_path: Path | None = None,
    slot: str | None = None,
) -> None:
    if not summary_path.exists():
        return
    summary = _read_summary(summary_path)
    summary.setdefault("run_id", summary_path.parent.name)
    if slot:
        summary["_metrics_slot"] = slot
    night_plan = _read_json_if_exists(night_plan_path)
    metrics = _extract_battery_daily_from_summary(summary=summary, night_plan=night_plan)
    if metrics is None:
        return
    date = str(metrics["date"])
    target_soc = metrics["target_soc"]
    night_charge_kwh = metrics["night_charge_kwh"]
    pv_charge_end_soc = metrics["pv_charge_end_soc"]
    pv_charge_end_at = metrics["pv_charge_end_at"]
    plan_should_apply = metrics["plan_should_apply"]
    client.collection("battery_daily_metrics").document(date).set(
        {
            "date": date,
            "setting_soc_target_percent": target_soc,
            "night_charge_kwh": night_charge_kwh,
            "pv_charge_end_soc_percent": pv_charge_end_soc,
            "pv_charge_end_at": pv_charge_end_at,
            "settings_run_id": metrics["settings_run_id"],
            "source_doc_id": metrics["source_doc_id"],
            "source_status": metrics["source_status"],
            "source_profile": metrics["source_profile"],
            "plan_quality_status": metrics["plan_quality_status"],
            "plan_should_apply": bool(plan_should_apply) if plan_should_apply is not None else None,
            "updated_at": updated_at,
        },
        merge=True,
    )


def recalc_battery_pv_charge_end_soc(client, *, updated_at: str) -> int:
    latest_by_day: dict[str, tuple[str, float]] = {}
    for doc in client.collection("monitoring_samples").stream():
        row = doc.to_dict() or {}
        ts = str(row.get("ts", doc.id)).strip()
        if len(ts) < 10:
            continue
        try:
            pv_kwh = float(row.get("pv_kwh") or 0.0)
            charge_kwh = float(row.get("charge_kwh") or 0.0)
        except (TypeError, ValueError):
            continue
        if pv_kwh <= 0.0 or charge_kwh <= 0.0:
            continue
        soc_raw = row.get("soc_percent")
        if soc_raw is None:
            continue
        try:
            soc = float(soc_raw)
        except (TypeError, ValueError):
            continue
        day = ts[:10]
        prev = latest_by_day.get(day)
        if prev is None or ts > prev[0]:
            latest_by_day[day] = (ts, soc)

    if not latest_by_day:
        return 0

    batch = client.batch()
    count = 0
    updated = 0
    for day, (ts, soc) in latest_by_day.items():
        ref = client.collection("battery_daily_metrics").document(day)
        snap = ref.get()
        if not snap.exists:
            continue
        batch.set(
            ref,
            {
                "pv_charge_end_soc_percent": soc,
                "pv_charge_end_at": ts,
                "updated_at": updated_at,
            },
            merge=True,
        )
        count += 1
        updated += 1
        if count >= 450:
            batch.commit()
            batch = client.batch()
            count = 0
    if count > 0:
        batch.commit()
    return updated


def recalc_dashboard_daily_metrics(client, *, updated_at: str) -> int:
    """Materialize daily monitoring totals for fast dashboard reads."""

    by_day: dict[str, dict[str, float | str | None]] = {}
    review_night_charge_by_day: dict[str, float] = defaultdict(float)
    for doc in client.collection("monitoring_samples").stream():
        row = doc.to_dict() or {}
        ts = str(row.get("ts", doc.id)).strip()
        if len(ts) < 10:
            continue
        day = ts[:10]
        acc = by_day.setdefault(
            day,
            {
                "actual_pv_kwh": 0.0,
                "actual_load_kwh": 0.0,
                "buy_kwh": 0.0,
                "sell_kwh": 0.0,
                "charge_kwh": 0.0,
                "discharge_kwh": 0.0,
                "day_buy_kwh": 0.0,
                "night_buy_kwh": 0.0,
                "morning_soc_percent": None,
                "soc_min_percent": None,
                "soc_max_percent": None,
                "day_soc_max_percent": None,
                "sample_count": 0.0,
                "first_sample_at": ts,
                "latest_sample_at": ts,
            },
        )
        for field in ("pv_kwh", "load_kwh", "buy_kwh", "sell_kwh", "charge_kwh", "discharge_kwh"):
            target = "actual_pv_kwh" if field == "pv_kwh" else "actual_load_kwh" if field == "load_kwh" else field
            acc[target] = float(acc[target] or 0.0) + max(0.0, float(row.get(field) or 0.0))
        minute = ts[11:16]
        buy_kwh = max(0.0, float(row.get("buy_kwh") or 0.0))
        if "07:00" <= minute < "23:00":
            acc["day_buy_kwh"] = float(acc["day_buy_kwh"] or 0.0) + buy_kwh
        else:
            acc["night_buy_kwh"] = float(acc["night_buy_kwh"] or 0.0) + buy_kwh
        charge_kwh = max(0.0, float(row.get("charge_kwh") or 0.0))
        review_day = day
        if minute >= "23:00":
            review_day = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
        if minute < "07:00" or minute >= "23:00":
            review_night_charge_by_day[review_day] += charge_kwh
        soc = to_float(row.get("soc_percent"))
        if soc is not None:
            acc["soc_min_percent"] = soc if acc["soc_min_percent"] is None else min(float(acc["soc_min_percent"]), soc)
            acc["soc_max_percent"] = soc if acc["soc_max_percent"] is None else max(float(acc["soc_max_percent"]), soc)
            if "07:00" <= minute < "23:00":
                acc["day_soc_max_percent"] = soc if acc["day_soc_max_percent"] is None else max(float(acc["day_soc_max_percent"]), soc)
            if minute == "07:00":
                acc["morning_soc_percent"] = soc
        acc["sample_count"] = float(acc["sample_count"] or 0.0) + 1.0
        if ts < str(acc["first_sample_at"] or ""):
            acc["first_sample_at"] = ts
        if ts > str(acc["latest_sample_at"] or ""):
            acc["latest_sample_at"] = ts

    batch = client.batch()
    count = 0
    for day, metrics in by_day.items():
        metrics["review_night_charge_kwh"] = review_night_charge_by_day.get(day, 0.0)
        batch.set(client.collection("dashboard_daily_metrics").document(day), {"date": day, **metrics, "updated_at": updated_at}, merge=True)
        count += 1
        if count >= 450:
            batch.commit()
            batch = client.batch()
            count = 0
    if count:
        batch.commit()
    return len(by_day)


def recalc_battery_end_of_day_soc(client, *, updated_at: str) -> int:
    # Backward-compatible entry point. The dashboard now tracks the SOC at the
    # last PV charging sample, not the final sample of the day.
    return recalc_battery_pv_charge_end_soc(client, updated_at=updated_at)


def upsert_model_parameters_from_plan(client, *, night_plan_path: Path, updated_at: str) -> None:
    if not night_plan_path.exists():
        return
    data = json.loads(night_plan_path.read_text(encoding="utf-8"))
    coeff = data.get("coefficients", {})
    if not isinstance(coeff, dict):
        return
    for name, value in coeff.items():
        try:
            mean = float(value)
        except (TypeError, ValueError):
            continue
        variance = abs(mean) * 0.05
        ref = client.collection("model_parameters").document(name)
        snap = ref.get()
        sample_count = 1
        if snap.exists:
            prev = snap.to_dict() or {}
            sample_count = int(prev.get("sample_count") or 0) + 1
        ref.set(
            {
                "name": name,
                "mean_value": mean,
                "variance": variance,
                "sample_count": sample_count,
                "hit_rate": None,
                "updated_at": updated_at,
            },
            merge=True,
        )


def recalc_model_hit_rates(client, *, updated_at: str) -> float | None:
    rows = []
    for doc in client.collection("sunshine_daily").stream():
        row = doc.to_dict() or {}
        fh = row.get("forecast_hours")
        ah = row.get("actual_hours")
        if fh is None or ah is None:
            continue
        rows.append((float(fh), float(ah)))
    if not rows:
        return None

    smape_values: list[float] = []
    for fh, ah in rows:
        # Use sMAPE-like normalization to avoid low-actual-day over-penalty.
        denom = max((abs(ah) + abs(fh)) / 2.0, 0.5)
        smape = abs(ah - fh) / denom
        smape_values.append(min(smape, 2.0))
    if not smape_values:
        return None

    mean_smape = sum(smape_values) / len(smape_values)
    hit_rate = max(0.0, min(1.0, 1.0 - (mean_smape / 2.0)))
    batch = client.batch()
    count = 0
    for doc in client.collection("model_parameters").stream():
        batch.set(
            doc.reference,
            {
                "hit_rate": hit_rate,
                "updated_at": updated_at,
            },
            merge=True,
        )
        count += 1
        if count >= 450:
            batch.commit()
            batch = client.batch()
            count = 0
    if count > 0:
        batch.commit()
    return hit_rate
