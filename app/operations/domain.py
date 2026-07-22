from __future__ import annotations

import json
from datetime import time
from pathlib import Path
from typing import Any, Iterator

import requests

from app.monitoring_csv import iter_monitoring_points
from app.tariff import tiered_day_increment_cost
from app.time_windows import DailyWindow, minute_of_day, parse_hhmm
from app.utils import env, to_float, to_int


def extract_hourly_forecast_from_plan(data: dict[str, Any]) -> list[dict[str, Any]]:
    forecast = data.get("forecast", {})
    forecast_date = str(forecast.get("date", "")).strip() if isinstance(forecast, dict) else ""
    optimization = data.get("daytime_soc_optimization", {})
    if not forecast_date or not isinstance(optimization, dict):
        return []
    pv_by_hour = optimization.get("hourly_pv_forecast_kwh", {})
    load_by_hour = optimization.get("hourly_load_forecast_kwh", {})
    if not isinstance(pv_by_hour, dict):
        pv_by_hour = {}
    if not isinstance(load_by_hour, dict):
        load_by_hour = {}
    weather_by_hour: dict[int, dict[str, Any]] = {}
    hourly_weather = forecast.get("hourly_weather", []) if isinstance(forecast, dict) else []
    if isinstance(hourly_weather, list):
        for item in hourly_weather:
            if not isinstance(item, dict):
                continue
            hour = to_int(item.get("hour"))
            if hour is not None and 0 <= hour <= 23:
                weather_by_hour[hour] = item

    hours: set[int] = set(weather_by_hour)
    for source in (pv_by_hour, load_by_hour):
        for raw_hour in source:
            try:
                hour = int(raw_hour)
            except (TypeError, ValueError):
                continue
            if 0 <= hour <= 23:
                hours.add(hour)

    rows: list[dict[str, Any]] = []
    for hour in sorted(hours):
        pv_kwh = to_float(pv_by_hour.get(str(hour), pv_by_hour.get(hour))) or 0.0
        load_kwh = to_float(load_by_hour.get(str(hour), load_by_hour.get(hour))) or 0.0
        weather = weather_by_hour.get(hour, {})
        rows.append(
            {
                "date": forecast_date,
                "hour": hour,
                "forecast_pv_kwh": round(max(0.0, pv_kwh), 4),
                "forecast_load_kwh": round(max(0.0, load_kwh), 4),
                "forecast_charge_kwh": round(max(0.0, pv_kwh - load_kwh), 4),
                "forecast_weather_code": to_int(weather.get("weather_code")),
                "forecast_precipitation_mm": to_float(weather.get("precipitation_mm")),
                "forecast_precipitation_probability": to_float(weather.get("precipitation_probability")),
                "forecast_cloud_cover": to_float(weather.get("cloud_cover")),
                "forecast_shortwave_radiation_w_m2": to_float(weather.get("shortwave_radiation_w_m2")),
                "forecast_temp_c": to_float(weather.get("temp_c")),
                "forecast_relative_humidity_percent": to_float(weather.get("relative_humidity_percent")),
                "forecast_dew_point_c": to_float(weather.get("dew_point_c")),
                "forecast_wind_speed_10m": to_float(weather.get("wind_speed_10m")),
            }
        )
    return rows


def extract_final_pv_totals_from_plan(data: dict[str, Any]) -> dict[str, float | str | None]:
    hourly_rows = extract_hourly_forecast_from_plan(data)
    if hourly_rows:
        total = morning = midday = evening = peak = 0.0
        for row in hourly_rows:
            hour = to_int(row.get("hour"))
            pv_kwh = max(0.0, to_float(row.get("forecast_pv_kwh")) or 0.0)
            total += pv_kwh
            peak = max(peak, pv_kwh)
            if hour is None:
                continue
            if 7 <= hour < 10:
                morning += pv_kwh
            elif 10 <= hour < 16:
                midday += pv_kwh
            elif 16 <= hour < 23:
                evening += pv_kwh
        return {
            "total_kwh": round(total, 4), "morning_kwh": round(morning, 4),
            "midday_kwh": round(midday, 4), "evening_kwh": round(evening, 4),
            "peak_kw": round(peak, 4), "source": "daytime_soc_optimization.hourly_pv_forecast_kwh",
        }
    pv_forecast = data.get("pv_array_forecast", {})
    totals = pv_forecast.get("totals", {}) if isinstance(pv_forecast, dict) else {}
    return {
        "total_kwh": to_float(totals.get("total_kwh") if isinstance(totals, dict) else None),
        "morning_kwh": to_float(totals.get("morning_kwh") if isinstance(totals, dict) else None),
        "midday_kwh": to_float(totals.get("midday_kwh") if isinstance(totals, dict) else None),
        "evening_kwh": to_float(totals.get("evening_kwh") if isinstance(totals, dict) else None),
        "peak_kw": to_float(totals.get("peak_kw") if isinstance(totals, dict) else None),
        "source": "pv_array_forecast.totals",
    }


def extract_final_pv_source_from_plan(data: dict[str, Any]) -> str:
    result = data.get("result", {})
    if isinstance(result, dict) and (source := str(result.get("final_pv_forecast_source") or "").strip()):
        return source
    rationale = data.get("decision_rationale", {})
    final_pv = rationale.get("final_pv_forecast", {}) if isinstance(rationale, dict) else {}
    if isinstance(final_pv, dict) and (source := str(final_pv.get("source") or "").strip()):
        return source
    pv_forecast = data.get("pv_array_forecast", {})
    forecast = data.get("forecast", {})
    return str(
        (pv_forecast.get("source") if isinstance(pv_forecast, dict) else None)
        or (forecast.get("source") if isinstance(forecast, dict) else None)
        or "forecast"
    )


def safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def parse_hhmm_to_minute(*, value: str, name: str) -> int:
    return minute_of_day(parse_hhmm(value, name=name))


def is_within_window(value: int, *, start_minute: int, end_minute: int) -> bool:
    return DailyWindow(
        time(start_minute // 60, start_minute % 60),
        time(end_minute // 60, end_minute % 60),
    ).contains(time(value // 60, value % 60))


def tiered_increment_cost(**kwargs: float) -> float:
    return tiered_day_increment_cost(**kwargs)


def read_summary(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"summary root must be an object: {path}")
    return value


def read_json_if_exists(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _extract_settings_metric_source(
    *, summary: dict[str, Any], night_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    results = summary.get("setting_results")
    source_item: dict[str, Any] = {}
    source_index = 0
    if isinstance(results, list):
        for idx, item in enumerate(results):
            if not isinstance(item, dict):
                continue
            source_item, source_index = item, idx
            if str(item.get("status", "")).strip() in {"applied", "skipped-no-change"}:
                break
    run_id = str(summary.get("run_id") or "").strip()
    status = str(source_item.get("status") or "").strip()
    profile = str(source_item.get("profile") or "").strip()
    source_doc_id = str(source_item.get("source_doc_id") or "").strip()
    slot = str(summary.get("_metrics_slot") or "").strip()
    if not source_doc_id and run_id and slot and profile:
        source_doc_id = f"{run_id}-{slot}-{source_index:02d}-{profile}"
    root = night_plan if isinstance(night_plan, dict) else {}
    quality = root.get("plan_quality", {}) if isinstance(root.get("plan_quality", {}), dict) else {}
    should_apply = quality.get("should_apply")
    return {
        "settings_run_id": run_id, "source_doc_id": source_doc_id,
        "source_status": status, "source_profile": profile,
        "plan_quality_status": str(quality.get("status") or "").strip(),
        "plan_should_apply": int(should_apply) if isinstance(should_apply, bool) else None,
    }


def extract_battery_daily_from_summary(
    *, summary: dict[str, Any], night_plan: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    np_summary = summary.get("night_charge_plan", {})
    if not isinstance(np_summary, dict):
        np_summary = {}
    root = night_plan if isinstance(night_plan, dict) else {}
    result = root.get("result", {}) if isinstance(root.get("result", {}), dict) else {}
    forecast = root.get("forecast", {}) if isinstance(root.get("forecast", {}), dict) else {}
    quality = root.get("plan_quality", {}) if isinstance(root.get("plan_quality", {}), dict) else {}
    if quality.get("should_apply") is False:
        result = {}
    prefer_plan = env("DATA_PREFER_NIGHT_PLAN_METRICS", default="false").strip().lower() in {"1", "true", "yes", "on"}
    day = str(np_summary.get("forecast_date") or forecast.get("date") or "").strip()
    if not day:
        return None
    target_soc = to_float(result.get("target_soc_7_percent")) if prefer_plan else None
    target_soc = target_soc if target_soc is not None else to_float(np_summary.get("target_soc_7_percent_raw"))
    target_soc = target_soc if target_soc is not None else to_float(result.get("target_soc_7_percent"))
    night_kwh = to_float(result.get("required_night_charge_kwh")) if prefer_plan else None
    night_kwh = night_kwh if night_kwh is not None else to_float(np_summary.get("required_night_charge_kwh"))
    night_kwh = night_kwh if night_kwh is not None else to_float(result.get("required_night_charge_kwh"))
    return {
        "date": day, "target_soc": target_soc, "night_charge_kwh": night_kwh,
        "pv_charge_end_soc": None, "pv_charge_end_at": None,
        **_extract_settings_metric_source(summary=summary, night_plan=night_plan),
    }


def iter_monitoring_rows(csv_path: Path) -> Iterator[dict[str, Any]]:
    for point in iter_monitoring_points(csv_path):
        yield point.as_storage_row()


def _first_float(values: Any) -> float | None:
    if not values:
        return None
    try:
        return to_float(values[0])
    except Exception:
        return None


def fetch_open_meteo_daily_actual(
    *, lat: float, lon: float, date_ymd: str, timezone: str,
) -> dict[str, float | int | None]:
    params: dict[str, str | float] = {
        "latitude": lat, "longitude": lon, "start_date": date_ymd, "end_date": date_ymd,
        "daily": "sunshine_duration,temperature_2m_mean,weather_code,precipitation_sum,shortwave_radiation_sum",
        "timezone": timezone,
    }
    response = requests.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    daily_value = payload.get("daily", {}) if isinstance(payload, dict) else {}
    daily = daily_value if isinstance(daily_value, dict) else {}
    sunshine_s = _first_float(daily.get("sunshine_duration", []))
    weather_code = _first_float(daily.get("weather_code", []))
    return {
        "actual_hours": sunshine_s / 3600.0 if sunshine_s is not None else None,
        "actual_temp_c": _first_float(daily.get("temperature_2m_mean", [])),
        "actual_weather_code": int(weather_code) if weather_code is not None else None,
        "actual_precipitation_sum_mm": _first_float(daily.get("precipitation_sum", [])),
        "actual_shortwave_radiation_sum_mj_m2": _first_float(daily.get("shortwave_radiation_sum", [])),
    }


def fetch_open_meteo_today_actual(
    *, lat: float, lon: float, date_ymd: str, timezone: str,
) -> tuple[float | None, float | None]:
    actual = fetch_open_meteo_daily_actual(lat=lat, lon=lon, date_ymd=date_ymd, timezone=timezone)
    return to_float(actual.get("actual_hours")), to_float(actual.get("actual_temp_c"))
