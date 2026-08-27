from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from app.dashboard.models import DashboardRawData, DashboardSlice
from app.dashboard.repositories import DashboardLoadRequest
from app.dashboard.schedule import _build_latest_schedule_from_events, _default_latest_schedule
from app.dashboard.aggregation import (
    _build_cost_monthly, _build_energy_daily, _aggregation_close_day,
    _accounting_month_label, _accounting_period_bounds, _date_add_iso,
    _today_jst_iso,
)
from app.dashboard.slice_assembler import (
    build_dashboard_slice as _build_dashboard_slice,
    empty_dashboard_slice as _empty_dashboard_slice,
    extract_pv_forecast_diagnostics,
    merge_latest_plan_into_schedule as _merge_latest_plan_into_schedule,
)
from app.dashboard.repository_support import pick_min_max_dates as _pick_min_max_dates, to_date_or_none as _to_date_or_none
from app.dashboard.service import merge_forecast_hourly_actuals
from app.domain.tariff import tiered_day_cost
from app.parsing.numbers import to_float

_FIRESTORE_CLIENTS: dict[tuple[str | None, str], Any] = {}
_FIRESTORE_SLICE_CACHE: dict[tuple[str | None, str, str | None, int, bool], tuple[float, DashboardSlice]] = {}
_FIRESTORE_DASHBOARD_CACHE_SECONDS = 120.0


def _read_latest_pv_forecast_diagnostics_from_firestore(client: Any) -> dict[str, Any]:
    try:
        snapshot = client.collection("night_charge_plans").document("latest").get()
    except Exception:
        return {}
    return extract_pv_forecast_diagnostics(snapshot.to_dict() or {}) if snapshot.exists else {}
def _firestore_date_value(raw: Any) -> str | None:
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    text = str(raw or "").strip()
    return text[:10] if _to_date_or_none(text[:10]) else None


def _firestore_bounds(
    client: Any,
    collection_name: str,
    field_name: str = "date",
) -> tuple[str | None, str | None]:
    col = client.collection(collection_name)
    min_doc = next(col.order_by(field_name).limit(1).stream(), None)
    max_docs = col.order_by(field_name).limit_to_last(1).get()
    max_doc = max_docs[0] if max_docs else None
    min_date = None
    max_date = None
    if min_doc is not None:
        d = min_doc.to_dict() or {}
        min_date = _firestore_date_value(d.get(field_name))
    if max_doc is not None:
        d = max_doc.to_dict() or {}
        max_date = _firestore_date_value(d.get(field_name))
    return min_date, max_date


def _get_global_bounds_firestore(
    client: Any,
    *,
    bounds_reader: Callable[[Any, str, str], tuple[str | None, str | None]] | None = None,
) -> tuple[str | None, str | None]:
    read_bounds = bounds_reader or _firestore_bounds
    candidates: list[str | None] = []
    sources = [
        ("sunshine_daily", "date"),
        ("cost_daily", "date"),
        ("battery_daily_metrics", "date"),
        ("forecast_hourly", "date"),
        ("monitoring_samples", "ts"),
    ]
    for collection_name, field_name in sources:
        try:
            candidates.extend(read_bounds(client, collection_name, field_name))
        except Exception:
            continue
    return _pick_min_max_dates(candidates)


def _firestore_rows_between(
    client: Any,
    *,
    collection_name: str,
    start_date: str,
    end_date_iso: str,
    fields: list[str],
) -> list[dict[str, Any]]:
    q = (
        client.collection(collection_name)
        .where("date", ">=", start_date)
        .where("date", "<=", end_date_iso)
        .order_by("date")
    )
    out: list[dict[str, Any]] = []
    for doc in q.stream():
        row = doc.to_dict() or {}
        item = {k: row.get(k) for k in fields}
        item["date"] = row.get("date", doc.id)
        out.append(item)
    return out


def _firestore_monitoring_daily(
    client: Any,
    *,
    start_date: str,
    end_date_iso: str,
) -> list[dict[str, Any]]:
    daily_rows = _firestore_rows_between(
        client,
        collection_name="dashboard_daily_metrics",
        start_date=start_date,
        end_date_iso=end_date_iso,
        fields=[
            "actual_pv_kwh", "actual_load_kwh", "buy_kwh", "sell_kwh", "charge_kwh", "discharge_kwh",
            "day_buy_kwh", "night_buy_kwh", "review_night_charge_kwh", "morning_soc_percent",
            "soc_min_percent", "soc_max_percent", "day_soc_max_percent", "sample_count", "first_sample_at", "latest_sample_at",
        ],
    )
    if daily_rows:
        return daily_rows

    end_next = _date_add_iso(end_date_iso, 1) or end_date_iso
    q = (
        client.collection("monitoring_samples")
        .where("ts", ">=", start_date)
        .where("ts", "<", end_next)
        .order_by("ts")
    )
    by_day: dict[str, dict[str, float]] = {}
    for doc in q.stream():
        row = doc.to_dict() or {}
        ts = str(row.get("ts", doc.id))
        day = ts[:10]
        if not day:
            continue
        acc = by_day.setdefault(
            day,
            {"actual_pv_kwh": 0.0, "actual_load_kwh": 0.0, "charge_kwh": 0.0, "discharge_kwh": 0.0},
        )
        acc["actual_pv_kwh"] += float(row.get("pv_kwh") or 0.0)
        acc["actual_load_kwh"] += float(row.get("load_kwh") or 0.0)
        acc["charge_kwh"] += float(row.get("charge_kwh") or 0.0)
        acc["discharge_kwh"] += float(row.get("discharge_kwh") or 0.0)
    return [{"date": day, **values} for day, values in sorted(by_day.items())]


def _dashboard_firestore_config() -> tuple[str | None, str]:
    project_id = os.getenv("FIRESTORE_PROJECT_ID", "").strip() or None
    database_id = os.getenv("FIRESTORE_DATABASE_ID", "").strip() or "(default)"
    return project_id, database_id


def clear_dashboard_cache() -> None:
    _FIRESTORE_SLICE_CACHE.clear()


def _open_dashboard_firestore_client() -> Any:
    from google.cloud import firestore

    project_id, database_id = _dashboard_firestore_config()
    key = (project_id, database_id)
    client = _FIRESTORE_CLIENTS.get(key)
    if client is None:
        client = firestore.Client(project=project_id, database=database_id) if project_id else firestore.Client(database=database_id)
        _FIRESTORE_CLIENTS[key] = client
    return client


def _daily_metric_is_complete(row: dict[str, Any]) -> bool:
    return (
        int(to_float(row.get("sample_count")) or 0) >= 48
        and str(row.get("first_sample_at") or "")[11:16] <= "00:00"
        and str(row.get("latest_sample_at") or "")[11:16] >= "23:30"
    )


def _review_candidate_dates(
    energy_daily: list[dict[str, Any]],
    *,
    end_date_iso: str,
    today_iso: str | None = None,
) -> list[str]:
    yesterday = _date_add_iso(today_iso or _today_jst_iso(), -1) or end_date_iso
    cutoff = min(end_date_iso, yesterday)
    return sorted(
        (
            str(row.get("date"))
            for row in energy_daily
            if row.get("date")
            and str(row.get("date")) <= cutoff
            and to_float(row.get("actual_load_kwh")) is not None
        ),
        reverse=True,
    )


def _nested_dict(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    value: Any = source
    for key in keys:
        if not isinstance(value, dict):
            return {}
        value = value.get(key)
    return value if isinstance(value, dict) else {}


def _plan_forecast_correction(plan: dict[str, Any]) -> dict[str, Any]:
    correction = _nested_dict(plan, "decision_rationale", "forecast_correction")
    if correction:
        return correction
    return _nested_dict(plan, "daytime_soc_optimization", "forecast_correction")


def _weather_label(weather_code: Any) -> str:
    code = to_float(weather_code)
    if code is None:
        return "-"
    value = int(code)
    if value == 0:
        return "快晴"
    if 1 <= value <= 3:
        return "晴れ・曇り"
    if value in {45, 48}:
        return "霧"
    if 51 <= value <= 67 or 80 <= value <= 82:
        return "雨"
    if 71 <= value <= 77 or 85 <= value <= 86:
        return "雪"
    if 95 <= value <= 99:
        return "雷雨"
    return f"天気コード{value}"


def _weather_class_label(weather_class: Any) -> str | None:
    labels = {
        "clear": "快晴",
        "sunny": "晴れ",
        "cloudy": "曇り",
        "fog": "霧",
        "rain": "雨",
        "snow": "雪",
        "thunderstorm": "雷雨",
    }
    value = str(weather_class or "").strip().lower()
    return labels.get(value) if value else None


def _plan_analog_summary(plan: dict[str, Any], analog_plan: dict[str, Any] | None) -> dict[str, Any]:
    floor = _nested_dict(_plan_forecast_correction(plan), "recent_and_analog_hourly_floor")
    analog_day = str(floor.get("analog_day") or "").strip() or None
    analog_forecast = _nested_dict(analog_plan or {}, "forecast")
    analog_hourly_summary = _nested_dict(analog_forecast, "hourly_weather_summary")
    analog_features = _nested_dict(
        _plan_forecast_correction(analog_plan or {}),
        "evening_load_temperature",
        "target_features",
    )
    selected_features = _nested_dict(floor, "analog_features") or analog_features
    hourly_temperatures = [
        value
        for row in analog_forecast.get("hourly_weather", [])
        if isinstance(row, dict) and (value := to_float(row.get("temp_c"))) is not None
    ]
    weather = _weather_class_label(analog_hourly_summary.get("dominant_weather_class_7_17"))
    if weather is None:
        weather = _weather_class_label(analog_forecast.get("weather_class"))
    return {
        "analog_date": analog_day,
        "analog_similarity": to_float(floor.get("analog_similarity")),
        "analog_weather": weather or (_weather_label(analog_forecast.get("weather_code")) if analog_day else None),
        "analog_max_temp_c": max(hourly_temperatures) if hourly_temperatures else to_float(selected_features.get("max_temp_c")),
        "analog_min_temp_c": min(hourly_temperatures) if hourly_temperatures else to_float(selected_features.get("night_min_temp_c")),
    }


# readable-code-audit: skip STRUCT-04 — billing totals and their source-period validation must use the same filtered dashboard rows
def _billing_usage_summary(
    review_date: str,
    daily_metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    close_day = _aggregation_close_day()
    label = _accounting_month_label(review_date, close_day=close_day)
    bounds = _accounting_period_bounds(label or "", close_day=close_day)
    if bounds is None:
        return {}
    period_start, period_end = bounds
    rows = sorted(
        (
            row for row in daily_metrics
            if period_start <= str(row.get("date") or "") <= min(review_date, period_end)
        ),
        key=lambda row: str(row.get("date") or ""),
    )
    day_kwh = sum(max(0.0, to_float(row.get("day_buy_kwh")) or 0.0) for row in rows)
    night_kwh = sum(max(0.0, to_float(row.get("night_buy_kwh")) or 0.0) for row in rows)
    start_obj = date.fromisoformat(period_start)
    end_obj = date.fromisoformat(period_end)
    review_obj = min(date.fromisoformat(review_date), end_obj)
    period_days = (end_obj - start_obj).days + 1
    elapsed_days = max(1, (review_obj - start_obj).days + 1)
    complete_period = review_obj >= end_obj
    projected_day_kwh = day_kwh if complete_period else day_kwh * period_days / elapsed_days
    projected_night_kwh = night_kwh if complete_period else night_kwh * period_days / elapsed_days
    tier1 = max(0.0, float(os.getenv("NIGHT8_DAY_TIER1_UPPER_KWH", "90") or "90"))
    tier2 = max(tier1, float(os.getenv("NIGHT8_DAY_TIER2_UPPER_KWH", "230") or "230"))
    rate1 = max(0.0, float(os.getenv("NIGHT8_DAY_RATE_TIER1_YEN", "31.80") or "31.80"))
    rate2 = max(0.0, float(os.getenv("NIGHT8_DAY_RATE_TIER2_YEN", "39.10") or "39.10"))
    rate3 = max(0.0, float(os.getenv("NIGHT8_DAY_RATE_TIER3_YEN", "43.62") or "43.62"))
    night_rate = max(0.0, float(os.getenv("NIGHT8_NIGHT_RATE_YEN", "28.85") or "28.85"))

    def arrival(threshold: float, *, first_tier: bool = False) -> dict[str, Any]:
        if first_tier:
            return {"date": period_start, "status": "reached"}
        cumulative = 0.0
        for row in rows:
            cumulative += max(0.0, to_float(row.get("day_buy_kwh")) or 0.0)
            if cumulative >= threshold:
                return {"date": str(row.get("date")), "status": "reached"}
        daily_average = day_kwh / elapsed_days
        if daily_average <= 0 or projected_day_kwh < threshold:
            return {"date": None, "status": "not_expected"}
        offset = max(0, math.ceil(threshold / daily_average) - 1)
        predicted = min(start_obj + timedelta(days=offset), end_obj)
        return {"date": predicted.isoformat(), "status": "forecast"}

    projected_day_cost = tiered_day_cost(
        projected_day_kwh,
        tier1_upper_kwh=tier1,
        tier2_upper_kwh=tier2,
        rate_tier1_yen=rate1,
        rate_tier2_yen=rate2,
        rate_tier3_yen=rate3,
    )
    projected_night_cost = projected_night_kwh * night_rate
    return {
        "billing_period_start": period_start,
        "billing_period_end": period_end,
        "billing_close_day": close_day,
        "cumulative_night_buy_kwh": night_kwh,
        "cumulative_day_buy_kwh": day_kwh,
        "projected_night_buy_kwh": projected_night_kwh,
        "projected_day_buy_kwh": projected_day_kwh,
        "projected_day_cost_yen": projected_day_cost,
        "projected_night_cost_yen": projected_night_cost,
        "projected_energy_cost_yen": projected_day_cost + projected_night_cost,
        "tier1_arrival": arrival(0.0, first_tier=True),
        "tier2_arrival": arrival(tier1),
        "tier3_arrival": arrival(tier2),
    }


def _firestore_plans_by_date(client: Any, dates: list[str]) -> dict[str, dict[str, Any]]:
    unique_dates = sorted({day for day in dates if _to_date_or_none(day) is not None})
    if not unique_dates:
        return {}
    refs = [client.collection("night_charge_plans").document(day) for day in unique_dates]
    out: dict[str, dict[str, Any]] = {}
    for snap in client.get_all(refs):
        if snap.exists:
            out[str(snap.id)] = snap.to_dict() or {}
    return out


def _build_firestore_daily_reviews(
    client: Any,
    *,
    end_date_iso: str,
    energy_daily: list[dict[str, Any]],
    battery_daily: list[dict[str, Any]],
    forecast_hourly: list[dict[str, Any]],
    daily_metrics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates = _review_candidate_dates(energy_daily, end_date_iso=end_date_iso)
    complete_metrics = {
        str(row.get("date")): row
        for row in daily_metrics
        if row.get("date") and _daily_metric_is_complete(row)
    }
    candidates = [day for day in candidates if day in complete_metrics]
    plans = _firestore_plans_by_date(client, candidates)
    analog_dates: list[str] = []
    for plan in plans.values():
        floor = _nested_dict(_plan_forecast_correction(plan), "recent_and_analog_hourly_floor")
        if floor.get("analog_day"):
            analog_dates.append(str(floor["analog_day"]))
    analog_plans = _firestore_plans_by_date(client, analog_dates)
    energy_by_date = {str(row.get("date")): row for row in energy_daily if row.get("date")}
    battery_by_date = {str(row.get("date")): row for row in battery_daily if row.get("date")}
    hourly_by_date: dict[str, list[dict[str, Any]]] = {}
    for row in forecast_hourly:
        hourly_by_date.setdefault(str(row.get("date") or ""), []).append(row)

    reviews: list[dict[str, Any]] = []
    for review_date in sorted(candidates):
        actual = energy_by_date[review_date]
        metrics = complete_metrics[review_date]
        battery = battery_by_date.get(review_date, {})
        hourly = hourly_by_date.get(review_date, [])
        plan = plans.get(review_date, {})
        result = _nested_dict(plan, "result")
        plan_forecast = _nested_dict(plan, "forecast")
        analog_day = _plan_analog_summary(plan, None).get("analog_date")
        forecast_load = sum(max(0.0, to_float(row.get("forecast_load_kwh")) or 0.0) for row in hourly)
        review = {
            "date": review_date,
            "review_date": review_date,
            "forecast_date": review_date if hourly else None,
            "plan_date": plan_forecast.get("date") or (review_date if plan else None),
            "data_last_at": metrics.get("latest_sample_at"),
            "complete_day": True,
            "target_soc_percent": battery.get("setting_soc_target_percent") if battery.get("setting_soc_target_percent") is not None else result.get("target_soc_7_percent"),
            "actual_morning_soc_percent": metrics.get("morning_soc_percent"),
            "forecast_night_charge_kwh": battery.get("night_charge_kwh") if battery.get("night_charge_kwh") is not None else result.get("required_night_charge_kwh"),
            "forecast_pv_kwh": result.get("final_predicted_pv_kwh", actual.get("forecast_pv_kwh")),
            "forecast_load_kwh": forecast_load if hourly else actual.get("forecast_load_kwh"),
            "forecast_load_source": "forecast_hourly" if hourly else "energy_daily_fallback",
            "forecast_day_buy_kwh": result.get("soc_expected_day_buy_kwh"),
            "forecast_sell_kwh": result.get("soc_expected_sell_kwh"),
            "actual_pv_kwh": actual.get("actual_pv_kwh"),
            "actual_load_kwh": actual.get("actual_load_kwh"),
            "actual_night_charge_kwh": metrics.get("review_night_charge_kwh"),
            "actual_day_buy_kwh": metrics.get("day_buy_kwh"),
            "actual_sell_kwh": metrics.get("sell_kwh"),
            "actual_soc_min_percent": metrics.get("soc_min_percent"),
            "actual_soc_max_percent": metrics.get("soc_max_percent"),
            "actual_day_soc_max_percent": metrics.get("day_soc_max_percent"),
            **_plan_analog_summary(plan, analog_plans.get(str(analog_day))),
            **_billing_usage_summary(review_date, daily_metrics),
        }
        reviews.append(review)
    return reviews


def _firestore_forecast_hourly_between(
    client: Any,
    *,
    start_date: str,
    end_date_iso: str,
) -> list[dict[str, Any]]:
    rows = _firestore_rows_between(
        client,
        collection_name="forecast_hourly",
        start_date=start_date,
        end_date_iso=end_date_iso,
        fields=[
            "hour",
            "forecast_pv_kwh",
            "forecast_load_kwh",
            "forecast_charge_kwh",
            "source",
            "updated_at",
        ],
    )
    end_next = _date_add_iso(end_date_iso, 1) or end_date_iso
    monitoring_rows: list[dict[str, Any]] = []
    for doc in (
        client.collection("monitoring_samples")
        .where("ts", ">=", start_date)
        .where("ts", "<", end_next)
        .order_by("ts")
        .stream()
    ):
        row = doc.to_dict() or {}
        monitoring_rows.append(
            {
                "ts": row.get("ts", doc.id),
                "load_kwh": row.get("load_kwh"),
                "soc_percent": row.get("soc_percent"),
            }
        )
    return merge_forecast_hourly_actuals(rows, monitoring_rows)


# readable-code-audit: skip STRUCT-04 — Firestore requires separate document reads and aggregation, so splitting every stage would hide the snapshot contract
def load_firestore_slice(
    *,
    end_date: str | None,
    window_days: int,
    include_static: bool,
    client_factory: Callable[[], Any] | None = None,
    bounds_reader: Callable[[Any], tuple[str | None, str | None]] | None = None,
    rows_reader: Callable[..., list[dict[str, Any]]] | None = None,
    monitoring_reader: Callable[..., list[dict[str, Any]]] | None = None,
    hourly_reader: Callable[..., list[dict[str, Any]]] | None = None,
) -> DashboardSlice:
    empty_schedule = _default_latest_schedule()
    client = (client_factory or _open_dashboard_firestore_client)()
    bounds = bounds_reader or _get_global_bounds_firestore
    rows = rows_reader or _firestore_rows_between
    monitoring = monitoring_reader or _firestore_monitoring_daily
    hourly = hourly_reader or _firestore_forecast_hourly_between

    global_oldest, global_newest = bounds(client)
    if not global_newest:
        return _empty_dashboard_slice(window_days=window_days, schedule=empty_schedule)

    end_obj = _to_date_or_none(end_date) or _to_date_or_none(global_newest)
    if end_obj is None:
        return _empty_dashboard_slice(
            window_days=window_days,
            schedule=empty_schedule,
            global_oldest=global_oldest,
            global_newest=global_newest,
        )
    start_obj = end_obj - timedelta(days=max(1, window_days) - 1)
    start_date = start_obj.isoformat()
    end_date_iso = end_obj.isoformat()

    pv_daily = rows(
        client,
        collection_name="sunshine_daily",
        start_date=start_date,
        end_date_iso=end_date_iso,
        fields=[
            "forecast_temp_c",
            "actual_temp_c",
            "forecast_pv_total_kwh",
            "forecast_pv_morning_kwh",
            "forecast_pv_midday_kwh",
            "forecast_pv_evening_kwh",
            "forecast_pv_calibration_factor",
        ],
    )
    cost_daily = rows(
        client,
        collection_name="cost_daily",
        start_date=start_date,
        end_date_iso=end_date_iso,
        fields=["self_consumption_kwh", "savings_yen", "cumulative_kwh", "cumulative_yen"],
    )
    battery_daily = rows(
        client,
        collection_name="battery_daily_metrics",
        start_date=start_date,
        end_date_iso=end_date_iso,
        fields=[
            "setting_soc_target_percent",
            "night_charge_kwh",
            "pv_charge_end_soc_percent",
            "pv_charge_end_at",
            "end_of_day_soc_percent",
            "settings_run_id",
            "source_doc_id",
            "source_status",
            "source_profile",
            "plan_quality_status",
            "plan_should_apply",
            "updated_at",
        ],
    )
    forecast_hourly = hourly(
        client,
        start_date=start_date,
        end_date_iso=end_date_iso,
    )
    # 直近2週間を添えて、表示期間の初日に必要な移動集計・比較の前提データを欠かさない。
    history_start = (start_obj - timedelta(days=14)).isoformat()
    monitoring_daily = monitoring(
        client,
        start_date=history_start,
        end_date_iso=end_date_iso,
    )
    battery_flow_daily = [
        {
            "date": row.get("date"),
            "charge_kwh": row.get("charge_kwh"),
            "discharge_kwh": row.get("discharge_kwh"),
        }
        for row in monitoring_daily
        if start_date <= str(row.get("date", "")) <= end_date_iso
    ]
    energy_daily = _build_energy_daily(
        start_date=start_date,
        end_date_iso=end_date_iso,
        pv_daily=pv_daily,
        monitoring_daily=monitoring_daily,
        forecast_hourly=forecast_hourly,
    )
    daily_reviews: list[dict[str, Any]] = []
    cost_monthly: list[dict[str, Any]] = []
    params: list[dict[str, Any]] = []
    latest_schedule = _default_latest_schedule(plan_date=end_date_iso)
    if include_static:
        daily_reviews = _build_firestore_daily_reviews(
            client,
            end_date_iso=end_date_iso,
            energy_daily=energy_daily,
            battery_daily=battery_daily,
            forecast_hourly=forecast_hourly,
            daily_metrics=monitoring_daily,
        )
        all_cost_daily: list[dict[str, Any]] = []
        for doc in client.collection("cost_daily").order_by("date").stream():
            row = doc.to_dict() or {}
            d = str(row.get("date", doc.id))
            all_cost_daily.append(
                {
                    "date": d,
                    "self_consumption_kwh": row.get("self_consumption_kwh"),
                    "savings_yen": row.get("savings_yen"),
                }
            )
        cost_monthly = _build_cost_monthly(all_cost_daily)
        for doc in client.collection("model_parameters").order_by("name").stream():
            row = doc.to_dict() or {}
            params.append(
                {
                    "name": row.get("name", doc.id),
                    "mean_value": row.get("mean_value"),
                    "variance": row.get("variance"),
                    "sample_count": row.get("sample_count"),
                }
            )
        latest_events: list[dict[str, Any]] = []
        events_tail = client.collection("settings_events").order_by("recorded_at").limit_to_last(40).get()
        for doc in reversed(events_tail):
            row = doc.to_dict() or {}
            latest_events.append(
                {
                    "event_id": row.get("event_id") or doc.id,
                    "run_id": row.get("run_id"),
                    "slot": row.get("slot"),
                    "profile": row.get("profile"),
                    "status": row.get("status"),
                    "detail_json": row.get("detail_json"),
                    "source_doc_id": row.get("source_doc_id") or doc.id,
                    "recorded_at": row.get("recorded_at"),
                }
            )
        latest_battery_docs = client.collection("battery_daily_metrics").order_by("date").limit_to_last(1).get()
        latest_battery = None
        for doc in latest_battery_docs:
            latest_battery = doc.to_dict() or {}
            break
        latest_schedule = _build_latest_schedule_from_events(
            event_rows=latest_events,
            battery_row=latest_battery,
            plan_date=end_date_iso,
        )
        latest_schedule = _merge_latest_plan_into_schedule(
            latest_schedule,
            _firestore_plans_by_date(client, [end_date_iso]).get(end_date_iso),
        )

    raw = DashboardRawData(
        pv_daily=pv_daily,
        cost_daily=cost_daily,
        cost_monthly=cost_monthly,
        battery_daily=battery_daily,
        model_parameters=params,
        battery_flow_daily=battery_flow_daily,
        energy_daily=energy_daily,
        forecast_hourly=forecast_hourly,
        latest_schedule=latest_schedule,
        global_oldest=global_oldest,
        global_newest=global_newest,
    )
    return _build_dashboard_slice(
        raw,
        end_date_iso=end_date_iso,
        window_days=window_days,
        pv_forecast_diagnostics=(
            _read_latest_pv_forecast_diagnostics_from_firestore(client) if include_static else {}
        ),
        daily_review=daily_reviews[-1] if daily_reviews else {},
        daily_reviews=daily_reviews,
    )


@dataclass(frozen=True)
class FirestoreDashboardRepository:
    def load_dashboard(self, request: DashboardLoadRequest) -> DashboardSlice:
        return load_firestore_slice(
            end_date=request.end_date,
            window_days=request.window_days,
            include_static=request.include_static,
        )



