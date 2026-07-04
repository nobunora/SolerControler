from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from google.cloud import firestore

from app.operations_db import (
    _extract_battery_daily_from_summary,
    _extract_final_pv_totals_from_plan,
    _extract_hourly_forecast_from_plan,
    _fetch_open_meteo_daily_actual,
    _is_within_window,
    _iter_monitoring_rows,
    _parse_hhmm_to_minute,
    _read_json_if_exists,
    _read_summary,
    _tiered_day_increment_cost,
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
    data = json.loads(night_plan_path.read_text(encoding="utf-8"))
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
    forecast_source = str(
        (pv_forecast.get("source") if isinstance(pv_forecast, dict) else None)
        or forecast.get("source")
        or "forecast"
    )
    lat = float(env("FORECAST_LATITUDE", default="35.67452"))
    lon = float(env("FORECAST_LONGITUDE", default="139.48216"))

    if forecast_date:
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
                "detail_json": item,
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

    if mode == "flat":
        by_day: dict[str, float] = defaultdict(float)
        for row in rows:
            ts_text = str(row.get("ts", "")).strip()
            if len(ts_text) < 10:
                continue
            day = ts_text[:10]
            load_kwh = max(0.0, float(row.get("load_kwh") or 0.0))
            buy_kwh = max(0.0, float(row.get("buy_kwh") or 0.0))
            by_day[day] += max(0.0, load_kwh - buy_kwh)
        cumulative_kwh = 0.0
        cumulative_yen = 0.0
        batch = client.batch()
        batch_count = 0
        for day in sorted(by_day.keys()):
            self_kwh = float(by_day[day])
            yen = self_kwh * day_rate_yen_per_kwh
            cumulative_kwh += self_kwh
            cumulative_yen += yen
            doc_ref = client.collection("cost_daily").document(day)
            batch.set(
                doc_ref,
                {
                    "date": day,
                    "self_consumption_kwh": self_kwh,
                    "savings_yen": yen,
                    "cumulative_kwh": cumulative_kwh,
                    "cumulative_yen": cumulative_yen,
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

    if mode != "night8_tiered":
        raise ValueError(f"unsupported tariff_mode: {tariff_mode}")

    day_start_minute = _parse_hhmm_to_minute(value=night8_day_start_hhmm, name="NIGHT8_DAY_START_HHMM")
    day_end_minute = _parse_hhmm_to_minute(value=night8_day_end_hhmm, name="NIGHT8_DAY_END_HHMM")
    day_metrics: dict[str, dict[str, float]] = {}
    for row in rows:
        ts_text = str(row.get("ts", "")).strip()
        if not ts_text:
            continue
        try:
            ts = datetime.fromisoformat(ts_text)
        except ValueError:
            continue
        day = ts.date().isoformat()
        metrics = day_metrics.setdefault(
            day,
            {
                "self_total_kwh": 0.0,
                "self_day_kwh": 0.0,
                "self_night_kwh": 0.0,
                "buy_day_kwh": 0.0,
                "buy_night_kwh": 0.0,
            },
        )
        load_kwh = max(0.0, float(row.get("load_kwh") or 0.0))
        buy_kwh = max(0.0, float(row.get("buy_kwh") or 0.0))
        self_kwh = max(0.0, load_kwh - buy_kwh)
        minute_of_day = ts.hour * 60 + ts.minute
        is_day_window = _is_within_window(
            minute_of_day,
            start_minute=day_start_minute,
            end_minute=day_end_minute,
        )
        metrics["self_total_kwh"] += self_kwh
        if is_day_window:
            metrics["self_day_kwh"] += self_kwh
            metrics["buy_day_kwh"] += buy_kwh
        else:
            metrics["self_night_kwh"] += self_kwh
            metrics["buy_night_kwh"] += buy_kwh

    sorted_days = sorted(day_metrics.keys())
    by_month: dict[str, list[str]] = defaultdict(list)
    for day in sorted_days:
        by_month[day[:7]].append(day)

    daily_savings: dict[str, float] = {}
    for month in sorted(by_month.keys()):
        cumulative_actual_day_kwh = 0.0
        cumulative_counterfactual_day_kwh = 0.0
        for day in by_month[month]:
            metrics = day_metrics[day]
            actual_day_buy_kwh = metrics["buy_day_kwh"]
            actual_night_buy_kwh = metrics["buy_night_kwh"]
            counterfactual_day_buy_kwh = actual_day_buy_kwh + metrics["self_day_kwh"]
            counterfactual_night_buy_kwh = actual_night_buy_kwh + metrics["self_night_kwh"]

            actual_day_cost = _tiered_day_increment_cost(
                previous_kwh=cumulative_actual_day_kwh,
                delta_kwh=actual_day_buy_kwh,
                tier1_upper_kwh=night8_day_tier1_upper_kwh,
                tier2_upper_kwh=night8_day_tier2_upper_kwh,
                rate_tier1_yen=night8_day_rate_tier1_yen,
                rate_tier2_yen=night8_day_rate_tier2_yen,
                rate_tier3_yen=night8_day_rate_tier3_yen,
            )
            counterfactual_day_cost = _tiered_day_increment_cost(
                previous_kwh=cumulative_counterfactual_day_kwh,
                delta_kwh=counterfactual_day_buy_kwh,
                tier1_upper_kwh=night8_day_tier1_upper_kwh,
                tier2_upper_kwh=night8_day_tier2_upper_kwh,
                rate_tier1_yen=night8_day_rate_tier1_yen,
                rate_tier2_yen=night8_day_rate_tier2_yen,
                rate_tier3_yen=night8_day_rate_tier3_yen,
            )
            actual_night_cost = actual_night_buy_kwh * night8_night_rate_yen
            counterfactual_night_cost = counterfactual_night_buy_kwh * night8_night_rate_yen
            daily_savings[day] = (
                counterfactual_day_cost
                + counterfactual_night_cost
                - actual_day_cost
                - actual_night_cost
            )
            cumulative_actual_day_kwh += actual_day_buy_kwh
            cumulative_counterfactual_day_kwh += counterfactual_day_buy_kwh

    cumulative_kwh = 0.0
    cumulative_yen = 0.0
    batch = client.batch()
    batch_count = 0
    for day in sorted_days:
        metrics = day_metrics[day]
        self_kwh = float(metrics["self_total_kwh"])
        yen = float(daily_savings.get(day, 0.0))
        cumulative_kwh += self_kwh
        cumulative_yen += yen
        doc_ref = client.collection("cost_daily").document(day)
        batch.set(
            doc_ref,
            {
                "date": day,
                "self_consumption_kwh": self_kwh,
                "savings_yen": yen,
                "cumulative_kwh": cumulative_kwh,
                "cumulative_yen": cumulative_yen,
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
    pv_max_charge_kwh = metrics["pv_max_charge_kwh"]
    pv_charge_end_soc = metrics["pv_charge_end_soc"]
    pv_charge_end_at = metrics["pv_charge_end_at"]
    plan_should_apply = metrics["plan_should_apply"]
    client.collection("battery_daily_metrics").document(date).set(
        {
            "date": date,
            "setting_soc_target_percent": target_soc,
            "night_charge_kwh": night_charge_kwh,
            "pv_max_charge_kwh": pv_max_charge_kwh,
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
