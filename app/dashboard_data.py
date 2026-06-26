from __future__ import annotations

import os
import json
import math
import sqlite3
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.utils import to_float


@dataclass(frozen=True)
class DashboardData:
    sunshine_daily: list[dict[str, Any]]
    cost_daily: list[dict[str, Any]]
    cost_monthly: list[dict[str, Any]]
    battery_daily: list[dict[str, Any]]
    model_parameters: list[dict[str, Any]]
    battery_flow_daily: list[dict[str, Any]] = field(default_factory=list)
    energy_daily: list[dict[str, Any]] = field(default_factory=list)
    forecast_hourly: list[dict[str, Any]] = field(default_factory=list)
    latest_schedule: dict[str, Any] = field(default_factory=dict)
    dashboard_warnings: list[dict[str, Any]] = field(default_factory=list)
    pv_forecast_diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DashboardSlice:
    data: DashboardData
    meta: dict[str, Any]


def _rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
            continue
        if isinstance(row, sqlite3.Row):
            out.append(dict(row))
            continue
        if hasattr(row, "keys"):
            out.append({k: row[k] for k in row.keys()})
            continue
        out.append(dict(row))
    return out


def _to_date_or_none(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _pick_min_max_dates(values: list[str | None]) -> tuple[str | None, str | None]:
    dates = [v for v in values if v]
    if not dates:
        return None, None
    return min(dates), max(dates)


def _read_operation_conditions_config() -> dict[str, Any]:
    default = {"priority_order": ["fixed", "variable"], "fixed": [], "variable": []}
    path = Path(os.getenv("KP_OPERATION_CONDITIONS_PATH", "config/operation_conditions.json"))
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    if not isinstance(data, dict):
        return default
    return {
        "priority_order": data.get("priority_order") if isinstance(data.get("priority_order"), list) else default["priority_order"],
        "fixed": data.get("fixed") if isinstance(data.get("fixed"), list) else [],
        "variable": data.get("variable") if isinstance(data.get("variable"), list) else [],
    }


def _extract_pv_forecast_diagnostics(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    rationale = data.get("decision_rationale")
    optimization = data.get("daytime_soc_optimization")
    source = rationale if isinstance(rationale, dict) else optimization if isinstance(optimization, dict) else {}
    physical = source.get("pv_physical_forecast") if isinstance(source, dict) else None
    correction = source.get("forecast_correction") if isinstance(source, dict) else None
    hourly_shape = source.get("hourly_weather_pv_shape") if isinstance(source, dict) else None
    overnight = source.get("overnight_discharge_guard") if isinstance(source, dict) else None
    forecast = data.get("forecast")
    return {
        "plan_date": forecast.get("date") if isinstance(forecast, dict) else None,
        "physical": physical if isinstance(physical, dict) else {},
        "forecast_correction": correction if isinstance(correction, dict) else {},
        "hourly_weather_pv_shape": hourly_shape if isinstance(hourly_shape, dict) else {},
        "overnight_load_forecast": overnight if isinstance(overnight, dict) else {},
    }


def _read_latest_pv_forecast_diagnostics() -> dict[str, Any]:
    path = Path(os.getenv("NIGHT_CHARGE_PLAN_PATH", "artifacts/night_charge_plan.json"))
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return _extract_pv_forecast_diagnostics(data)


def _read_latest_pv_forecast_diagnostics_from_firestore(client: Any) -> dict[str, Any]:
    try:
        snap = client.collection("night_charge_plans").document("latest").get()
    except Exception:
        return {}
    if not snap.exists:
        return {}
    row = snap.to_dict() or {}
    plan_text = str(row.get("plan_json") or "").strip()
    if plan_text:
        try:
            plan = json.loads(plan_text)
        except Exception:
            plan = {}
        if isinstance(plan, dict):
            diagnostics = _extract_pv_forecast_diagnostics(plan)
            if diagnostics:
                return diagnostics
    return _extract_pv_forecast_diagnostics(row)


def _find_variable_condition_value(conditions: dict[str, Any], *, target_id: str, default: str) -> str:
    for item in conditions.get("variable", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("id", "")).strip() == target_id:
            value = str(item.get("value", "")).strip()
            if value:
                return value
    return default

def _date_add_iso(date_text: str, delta_days: int) -> str | None:
    d = _to_date_or_none(date_text)
    if d is None:
        return None
    return (d + timedelta(days=delta_days)).isoformat()


def _today_jst_iso() -> str:
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _aggregation_close_day() -> int:
    raw = os.getenv("DASHBOARD_AGGREGATION_CLOSE_DAY", "14").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 14
    return max(1, min(31, value))


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12) + (month - 1) + delta
    return index // 12, (index % 12) + 1


def _month_end_day(year: int, month: int, close_day: int) -> int:
    return min(close_day, monthrange(year, month)[1])


def _accounting_month_label(day_text: str, *, close_day: int) -> str | None:
    day = _to_date_or_none(day_text)
    if day is None:
        return None
    effective_close = _month_end_day(day.year, day.month, close_day)
    year = day.year
    month = day.month
    if day.day > effective_close:
        year, month = _add_months(year, month, 1)
    return f"{year:04d}-{month:02d}"


def _accounting_period_bounds(month_label: str, *, close_day: int) -> tuple[str, str] | None:
    try:
        year_text, month_text = month_label.split("-", 1)
        year = int(year_text)
        month = int(month_text)
    except Exception:
        return None
    if month < 1 or month > 12:
        return None
    end_day = _month_end_day(year, month, close_day)
    end = date(year, month, end_day)
    prev_year, prev_month = _add_months(year, month, -1)
    prev_end_day = _month_end_day(prev_year, prev_month, close_day)
    start = date(prev_year, prev_month, prev_end_day) + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _build_cost_monthly(cost_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    close_day = _aggregation_close_day()
    by_month: dict[str, dict[str, float]] = {}
    for row in cost_rows:
        label = _accounting_month_label(str(row.get("date", "")), close_day=close_day)
        if label is None:
            continue
        acc = by_month.setdefault(label, {"self_consumption_kwh": 0.0, "savings_yen": 0.0})
        acc["self_consumption_kwh"] += float(row.get("self_consumption_kwh") or 0.0)
        acc["savings_yen"] += float(row.get("savings_yen") or 0.0)

    out: list[dict[str, Any]] = []
    for month, values in sorted(by_month.items()):
        bounds = _accounting_period_bounds(month, close_day=close_day)
        period_start, period_end = bounds if bounds is not None else (None, None)
        out.append(
            {
                "month": month,
                "period_start": period_start,
                "period_end": period_end,
                "self_consumption_kwh": values["self_consumption_kwh"],
                "savings_yen": values["savings_yen"],
            }
        )
    return out


def _model_param_value(params: list[dict[str, Any]], name: str, default: float) -> float:
    for row in params:
        if str(row.get("name", "")).strip() != name:
            continue
        value = to_float(row.get("mean_value"))
        if value is not None:
            return value
    return default


def _forecast_pv_kwh(
    sunshine_row: dict[str, Any] | None,
    *,
    pv_kwh_per_sunhour: float,
    pv_temp_coeff_per_deg: float,
) -> float | None:
    if not sunshine_row:
        return None
    array_forecast = to_float(sunshine_row.get("forecast_pv_total_kwh"))
    if array_forecast is not None:
        return max(0.0, array_forecast)
    sun_hours = to_float(sunshine_row.get("forecast_hours"))
    if sun_hours is None:
        return None
    temp_c = to_float(sunshine_row.get("forecast_temp_c"))
    if temp_c is None:
        temp_c = 25.0
    factor = max(0.0, 1.0 + pv_temp_coeff_per_deg * (temp_c - 25.0))
    return max(0.0, pv_kwh_per_sunhour * sun_hours * factor)


def _rolling_load_forecast(
    day: str,
    actual_by_day: dict[str, dict[str, Any]],
    *,
    lookback_days: int = 14,
) -> float | None:
    day_obj = _to_date_or_none(day)
    if day_obj is None:
        return None
    values: list[float] = []
    for prev_day in sorted(actual_by_day):
        prev_obj = _to_date_or_none(prev_day)
        if prev_obj is None or prev_obj >= day_obj:
            continue
        if (day_obj - prev_obj).days > lookback_days:
            continue
        value = to_float(actual_by_day[prev_day].get("actual_load_kwh"))
        if value is not None:
            values.append(value)
    if not values:
        return None
    return sum(values) / len(values)


def _build_energy_daily(
    *,
    start_date: str,
    end_date_iso: str,
    sunshine_daily: list[dict[str, Any]],
    monitoring_daily: list[dict[str, Any]],
    model_parameters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pv_kwh_per_sunhour = _model_param_value(model_parameters, "pv_kwh_per_sunhour", 1.45)
    pv_temp_coeff_per_deg = _model_param_value(model_parameters, "pv_temp_coeff_per_deg", -0.0035)
    sunshine_by_day = {str(row.get("date")): row for row in sunshine_daily if row.get("date")}
    actual_by_day = {str(row.get("date")): row for row in monitoring_daily if row.get("date")}
    dates = {
        d
        for d in set(sunshine_by_day) | set(actual_by_day)
        if start_date <= d <= end_date_iso
    }
    out: list[dict[str, Any]] = []
    for day in sorted(dates):
        actual = actual_by_day.get(day, {})
        sunshine = sunshine_by_day.get(day)
        forecast_pv = _forecast_pv_kwh(
            sunshine,
            pv_kwh_per_sunhour=pv_kwh_per_sunhour,
            pv_temp_coeff_per_deg=pv_temp_coeff_per_deg,
        )
        out.append(
            {
                "date": day,
                "forecast_pv_kwh": forecast_pv,
                "forecast_pv_morning_kwh": (sunshine or {}).get("forecast_pv_morning_kwh"),
                "forecast_pv_midday_kwh": (sunshine or {}).get("forecast_pv_midday_kwh"),
                "forecast_pv_evening_kwh": (sunshine or {}).get("forecast_pv_evening_kwh"),
                "forecast_pv_calibration_factor": (sunshine or {}).get("forecast_pv_calibration_factor"),
                "actual_pv_kwh": actual.get("actual_pv_kwh"),
                "forecast_load_kwh": _rolling_load_forecast(day, actual_by_day),
                "actual_load_kwh": actual.get("actual_load_kwh"),
            }
        )
    return out


def _parse_hhmm_minutes(raw: str | None) -> int | None:
    if not raw:
        return None
    text = str(raw).strip()
    try:
        h_str, m_str = text.split(":", 1)
        h = int(h_str)
        m = int(m_str)
    except Exception:
        return None
    if h < 0 or h > 23 or m < 0 or m > 59:
        return None
    return h * 60 + m


def _minutes_to_hhmm(minutes: int) -> str:
    v = max(0, min(23 * 60 + 59, int(minutes)))
    h = v // 60
    m = v % 60
    return f"{h:02d}:{m:02d}"


def _json_object_or_empty(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _default_latest_schedule(plan_date: str | None = None) -> dict[str, Any]:
    conditions = _read_operation_conditions_config()
    day_discharge_start = os.getenv("KP_DAY_DISCHARGE_WINDOW_START", "07:00").strip() or "07:00"
    day_discharge_end = os.getenv("KP_DAY_DISCHARGE_WINDOW_END", "23:00").strip() or "23:00"
    night_window_start = os.getenv("KP_NIGHT_CHARGE_WINDOW_START", "23:00").strip() or "23:00"
    night_window_end = os.getenv("KP_NIGHT_CHARGE_WINDOW_END", "07:00").strip() or "07:00"
    charge_end_time = (
        os.getenv("ADJUST03_FORCE_MONITOR_CUTOFF_HHMM", "").strip()
        or os.getenv("KP_NIGHT_CHARGE_WINDOW_END", "").strip()
        or _find_variable_condition_value(conditions, target_id="night_charge_end_time", default="07:00")
    )
    return {
        "plan_date": plan_date,
        "charge_start_time": None,
        "charge_end_time": charge_end_time,
        "night_window_start": night_window_start,
        "night_window_end": night_window_end,
        "day_discharge_window_start": day_discharge_start,
        "day_discharge_window_end": day_discharge_end,
        "discharge_fixed_window": f"{day_discharge_start}-{day_discharge_end}",
        "soc_safety_mode": None,
        "soc_economy_mode": "0",
        "soc_charge_mode": None,
        "mode": "green",
        "battery_operating_mode": "green",
        "estimated_charge_power_kw": float(os.getenv("KP_DEFAULT_CHARGE_POWER_KW", "1.8") or "1.8"),
        "status": "fallback-default",
        "recorded_at": None,
        "settings_completed": False,
        "settings_completed_status": None,
        "settings_completed_at": None,
        "settings_completed_profile": None,
        "constraints": conditions,
    }


def _build_latest_schedule_from_events(
    *,
    event_rows: list[dict[str, Any]],
    battery_row: dict[str, Any] | None,
    plan_date: str | None,
) -> dict[str, Any]:
    schedule = _default_latest_schedule(plan_date=plan_date)
    chosen_row: dict[str, Any] | None = None
    completed_row: dict[str, Any] | None = None
    schedule_source_locked = False
    for row in event_rows:
        status = str(row.get("status", "") or "")
        if completed_row is None and status in {"applied", "skipped-no-change"}:
            completed_row = row
        detail = _json_object_or_empty(row.get("detail_json"))
        if not detail:
            continue
        detail_plan_date = str(detail.get("plan_date") or "").strip()
        if plan_date and detail_plan_date and detail_plan_date != plan_date:
            continue
        is_monitor_schedule = str(detail.get("schedule_source") or "") == "03-monitor"
        if schedule_source_locked and not is_monitor_schedule:
            continue
        changed = False
        for key in (
            "charge_start_time",
            "charge_end_time",
            "night_window_start",
            "night_window_end",
            "day_discharge_window_start",
            "day_discharge_window_end",
            "discharge_fixed_window",
            "soc_safety_mode",
            "soc_economy_mode",
            "soc_charge_mode",
            "mode",
            "battery_operating_mode",
            "estimated_charge_power_kw",
            "schedule_source",
            "estimated_charge_minutes",
            "delay_before_force_seconds",
            "estimated_charge_rate_percent_per_hour",
            "charge_rate_source",
            "charge_rate_sample_count",
            "required_charge_percent_at_schedule",
        ):
            value = detail.get(key)
            if value is None or value == "":
                continue
            schedule[key] = value
            changed = True
        if changed and (chosen_row is None or is_monitor_schedule):
            chosen_row = row
            schedule_source_locked = is_monitor_schedule

    if chosen_row is not None:
        schedule["status"] = str(chosen_row.get("status", "from-settings-events"))
        schedule["recorded_at"] = str(chosen_row.get("recorded_at", ""))
        schedule["slot"] = str(chosen_row.get("slot", ""))
        schedule["profile"] = str(chosen_row.get("profile", ""))

    if completed_row is not None:
        schedule["settings_completed"] = True
        schedule["settings_completed_status"] = str(completed_row.get("status", ""))
        schedule["settings_completed_at"] = str(completed_row.get("recorded_at", ""))
        schedule["settings_completed_profile"] = str(completed_row.get("profile", ""))

    if battery_row:
        target_soc = to_float(battery_row.get("setting_soc_target_percent"))
        if target_soc is not None:
            schedule["soc_charge_mode"] = str(int(round(target_soc)))
        if schedule.get("plan_date") is None and battery_row.get("date"):
            schedule["plan_date"] = str(battery_row.get("date"))

    charge_start = _parse_hhmm_minutes(str(schedule.get("charge_start_time") or ""))
    charge_end = _parse_hhmm_minutes(str(schedule.get("charge_end_time") or ""))
    power_kw = to_float(schedule.get("estimated_charge_power_kw")) or 1.8
    night_kwh = to_float(battery_row.get("night_charge_kwh") if battery_row else None) or 0.0

    if charge_start is None and charge_end is not None and night_kwh > 0 and power_kw > 0:
        duration_minutes = int(math.ceil((night_kwh / power_kw) * 60.0))
        duration_minutes = max(30, duration_minutes)
        estimated_start = max(0, charge_end - duration_minutes)
        schedule["charge_start_time"] = _minutes_to_hhmm(estimated_start)
        schedule["status"] = "estimated-from-night-kwh"

    return schedule


def _latest_row_by_date(rows: list[dict[str, Any]], *, date_key: str = "date") -> dict[str, Any] | None:
    dated = [row for row in rows if row.get(date_key)]
    if not dated:
        return None
    return max(dated, key=lambda row: str(row.get(date_key)))


def _build_dashboard_warnings(
    *,
    latest_schedule: dict[str, Any],
    battery_daily: list[dict[str, Any]],
    energy_daily: list[dict[str, Any]],
    end_date_iso: str,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []

    def add(code: str, severity: str, title: str, message: str, detail: dict[str, Any] | None = None) -> None:
        warnings.append(
            {
                "code": code,
                "severity": severity,
                "title": title,
                "message": message,
                "detail": detail or {},
            }
        )

    latest_battery = _latest_row_by_date(battery_daily)
    if latest_battery:
        target_soc = to_float(latest_battery.get("setting_soc_target_percent"))
        pv_end_soc = to_float(latest_battery.get("pv_charge_end_soc_percent"))
        if target_soc is not None and pv_end_soc is not None and pv_end_soc + 5.0 < target_soc:
            add(
                "soc_target_unreached",
                "warning",
                "目標SOC未達",
                f"{latest_battery.get('date')} の太陽光充電終了時SOCが目標より低いです。",
                {
                    "date": latest_battery.get("date"),
                    "target_soc_percent": target_soc,
                    "pv_charge_end_soc_percent": pv_end_soc,
                },
            )

        night_charge = to_float(latest_battery.get("night_charge_kwh")) or 0.0
        source = str(latest_schedule.get("schedule_source") or "")
        charge_start_time = str(latest_schedule.get("charge_start_time") or "").strip()
        if night_charge > 0.1 and source != "03-monitor" and not charge_start_time:
            add(
                "monitor_schedule_missing",
                "warning",
                "03実行計画が未記録",
                "夜間充電が必要な日に、03ジョブが決めた実開始時刻を確認できません。",
                {
                    "date": latest_battery.get("date"),
                    "night_charge_kwh": night_charge,
                    "schedule_source": source or None,
                },
            )

    actual_dates = [
        str(row.get("date"))
        for row in energy_daily
        if row.get("date") and (
            to_float(row.get("actual_pv_kwh")) is not None
            or to_float(row.get("actual_load_kwh")) is not None
        )
    ]
    latest_actual = max(actual_dates) if actual_dates else None
    today_jst = _today_jst_iso()
    if end_date_iso < today_jst and (latest_actual is None or latest_actual < end_date_iso):
        add(
            "csv_actual_stale",
            "info",
            "CSV実績未更新",
            "表示終了日の実績CSVがまだ反映されていない可能性があります。",
            {"latest_actual_date": latest_actual, "display_end_date": end_date_iso},
        )

    completed = bool(latest_schedule.get("settings_completed"))
    status = str(latest_schedule.get("settings_completed_status") or latest_schedule.get("status") or "")
    if not completed and status not in {"applied", "skipped-no-change"}:
        add(
            "settings_completion_unconfirmed",
            "warning",
            "設定完了未確認",
            "最新設定の正常完了イベントを確認できません。",
            {
                "plan_date": latest_schedule.get("plan_date"),
                "status": status or None,
                "schedule_source": latest_schedule.get("schedule_source"),
            },
        )

    return warnings


def _get_global_bounds_sqlite(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    candidates: list[str | None] = []
    for table in ("sunshine_daily", "cost_daily", "battery_daily_metrics", "forecast_hourly"):
        if not _sqlite_table_exists(conn, table):
            continue
        row = conn.execute(f"SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM {table}").fetchone()
        candidates.extend([row["min_date"], row["max_date"]])
    if not _sqlite_table_exists(conn, "monitoring_samples"):
        return _pick_min_max_dates(candidates)
    row = conn.execute(
        "SELECT MIN(substr(ts,1,10)) AS min_date, MAX(substr(ts,1,10)) AS max_date FROM monitoring_samples"
    ).fetchone()
    candidates.extend([row["min_date"], row["max_date"]])
    return _pick_min_max_dates(candidates)


def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _get_global_bounds_postgres(cur) -> tuple[str | None, str | None]:
    candidates: list[str | None] = []
    for table in ("sunshine_daily", "cost_daily", "battery_daily_metrics", "forecast_hourly"):
        cur.execute(f"SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM {table}")
        row = cur.fetchone()
        candidates.extend([row.get("min_date"), row.get("max_date")])
    cur.execute("SELECT MIN(substring(ts,1,10)) AS min_date, MAX(substring(ts,1,10)) AS max_date FROM monitoring_samples")
    row = cur.fetchone()
    candidates.extend([row.get("min_date"), row.get("max_date")])
    return _pick_min_max_dates(candidates)


def _meta_from_data(
    *,
    window_days: int,
    global_oldest_date: str | None,
    global_newest_date: str | None,
    sunshine_daily: list[dict[str, Any]],
    cost_daily: list[dict[str, Any]],
    battery_daily: list[dict[str, Any]],
    energy_daily: list[dict[str, Any]] | None = None,
    forecast_hourly: list[dict[str, Any]] | None = None,
    battery_flow_daily: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    all_dates: list[str] = []
    all_dates.extend([str(x.get("date", "")) for x in sunshine_daily if x.get("date")])
    all_dates.extend([str(x.get("date", "")) for x in cost_daily if x.get("date")])
    all_dates.extend([str(x.get("date", "")) for x in battery_daily if x.get("date")])
    all_dates.extend([str(x.get("date", "")) for x in energy_daily or [] if x.get("date")])
    all_dates.extend([str(x.get("date", "")) for x in forecast_hourly or [] if x.get("date")])
    all_dates.extend([str(x.get("date", "")) for x in battery_flow_daily or [] if x.get("date")])
    oldest_loaded = min(all_dates) if all_dates else None
    newest_loaded = max(all_dates) if all_dates else None
    has_more_before = False
    if global_oldest_date and oldest_loaded:
        has_more_before = global_oldest_date < oldest_loaded
    return {
        "window_days": window_days,
        "aggregation_close_day": _aggregation_close_day(),
        "oldest_loaded_date": oldest_loaded,
        "newest_loaded_date": newest_loaded,
        "global_oldest_date": global_oldest_date,
        "global_newest_date": global_newest_date,
        "has_more_before": has_more_before,
    }


def _load_sqlite_slice(
    db_path: Path,
    *,
    end_date: str | None,
    window_days: int,
    include_static: bool,
) -> DashboardSlice:
    empty_schedule = _default_latest_schedule()
    if not db_path.exists():
        return DashboardSlice(
            data=DashboardData([], [], [], [], [], latest_schedule=empty_schedule),
            meta={
                "window_days": window_days,
                "oldest_loaded_date": None,
                "newest_loaded_date": None,
                "global_oldest_date": None,
                "global_newest_date": None,
                "has_more_before": False,
            },
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        global_oldest, global_newest = _get_global_bounds_sqlite(conn)
        if not global_newest:
            return DashboardSlice(
                data=DashboardData([], [], [], [], [], latest_schedule=empty_schedule),
                meta={
                    "window_days": window_days,
                    "oldest_loaded_date": None,
                    "newest_loaded_date": None,
                    "global_oldest_date": None,
                    "global_newest_date": None,
                    "has_more_before": False,
                },
            )

        end_obj = _to_date_or_none(end_date) or _to_date_or_none(global_newest)
        if end_obj is None:
            return DashboardSlice(
                data=DashboardData([], [], [], [], [], latest_schedule=empty_schedule),
                meta={
                    "window_days": window_days,
                    "oldest_loaded_date": None,
                    "newest_loaded_date": None,
                    "global_oldest_date": global_oldest,
                    "global_newest_date": global_newest,
                    "has_more_before": False,
                },
            )
        start_obj = end_obj - timedelta(days=max(1, window_days) - 1)
        start_date = start_obj.isoformat()
        end_date_iso = end_obj.isoformat()

        sunshine = []
        if _sqlite_table_exists(conn, "sunshine_daily"):
            sunshine = _rows_to_dicts(
                conn.execute(
                    """
                    SELECT *
                    FROM sunshine_daily
                    WHERE date >= ? AND date <= ?
                    ORDER BY date
                    """,
                    (start_date, end_date_iso),
                ).fetchall()
            )
        cost_daily = []
        if _sqlite_table_exists(conn, "cost_daily"):
            cost_daily = _rows_to_dicts(
                conn.execute(
                    """
                    SELECT date, self_consumption_kwh, savings_yen, cumulative_kwh, cumulative_yen
                    FROM cost_daily
                    WHERE date >= ? AND date <= ?
                    ORDER BY date
                    """,
                    (start_date, end_date_iso),
                ).fetchall()
            )
        battery_daily = []
        if _sqlite_table_exists(conn, "battery_daily_metrics"):
            battery_daily = _rows_to_dicts(
                conn.execute(
                    """
                    SELECT *
                    FROM battery_daily_metrics
                    WHERE date >= ? AND date <= ?
                    ORDER BY date
                    """,
                    (start_date, end_date_iso),
                ).fetchall()
            )
        forecast_hourly = []
        if _sqlite_table_exists(conn, "forecast_hourly"):
            forecast_hourly = _rows_to_dicts(
                conn.execute(
                    """
                    SELECT fh.date, fh.hour, fh.forecast_pv_kwh, fh.forecast_load_kwh,
                           fh.forecast_charge_kwh, ah.actual_load_kwh, ah.latest_sample_at,
                           fh.source, fh.updated_at
                    FROM forecast_hourly fh
                    LEFT JOIN (
                        SELECT substr(ts,1,10) AS date,
                               CAST(strftime('%H', ts) AS INTEGER) AS hour,
                               COALESCE(SUM(COALESCE(load_kwh,0)), 0) AS actual_load_kwh,
                               MAX(ts) AS latest_sample_at
                        FROM monitoring_samples
                        WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) <= ?
                        GROUP BY substr(ts,1,10), CAST(strftime('%H', ts) AS INTEGER)
                    ) ah ON ah.date = fh.date AND ah.hour = fh.hour
                    WHERE fh.date >= ? AND fh.date <= ?
                    ORDER BY fh.date, fh.hour
                    """,
                    (start_date, end_date_iso, start_date, end_date_iso),
                ).fetchall()
            )
        history_start = (start_obj - timedelta(days=14)).isoformat()
        monitoring_daily = []
        battery_flow_daily = []
        if _sqlite_table_exists(conn, "monitoring_samples"):
            monitoring_daily = _rows_to_dicts(
                conn.execute(
                    """
                    SELECT substr(ts,1,10) AS date,
                           COALESCE(SUM(COALESCE(pv_kwh,0)), 0) AS actual_pv_kwh,
                           COALESCE(SUM(COALESCE(load_kwh,0)), 0) AS actual_load_kwh
                    FROM monitoring_samples
                    WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) <= ?
                    GROUP BY substr(ts,1,10)
                    ORDER BY date
                    """,
                    (history_start, end_date_iso),
                ).fetchall()
            )
            battery_flow_daily = _rows_to_dicts(
                conn.execute(
                    """
                    SELECT substr(ts,1,10) AS date,
                           COALESCE(SUM(COALESCE(charge_kwh,0)), 0) AS charge_kwh,
                           COALESCE(SUM(COALESCE(discharge_kwh,0)), 0) AS discharge_kwh
                    FROM monitoring_samples
                    WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) <= ?
                    GROUP BY substr(ts,1,10)
                    ORDER BY date
                    """,
                    (start_date, end_date_iso),
                ).fetchall()
            )
        params_for_energy = []
        if _sqlite_table_exists(conn, "model_parameters"):
            params_for_energy = _rows_to_dicts(
                conn.execute(
                    """
                    SELECT name, mean_value
                    FROM model_parameters
                    ORDER BY name
                    """
                ).fetchall()
            )
        energy_daily = _build_energy_daily(
            start_date=start_date,
            end_date_iso=end_date_iso,
            sunshine_daily=sunshine,
            monitoring_daily=monitoring_daily,
            model_parameters=params_for_energy,
        )

        cost_monthly: list[dict[str, Any]] = []
        params: list[dict[str, Any]] = []
        latest_schedule = _default_latest_schedule(plan_date=end_date_iso)
        if include_static:
            all_cost_daily = []
            if _sqlite_table_exists(conn, "cost_daily"):
                all_cost_daily = _rows_to_dicts(
                    conn.execute(
                        """
                        SELECT date, self_consumption_kwh, savings_yen
                        FROM cost_daily
                        ORDER BY date
                        """
                    ).fetchall()
                )
            cost_monthly = _build_cost_monthly(all_cost_daily)
            params = []
            if _sqlite_table_exists(conn, "model_parameters"):
                params = _rows_to_dicts(
                    conn.execute(
                        """
                        SELECT name, mean_value, variance, sample_count
                        FROM model_parameters
                        ORDER BY name
                        """
                    ).fetchall()
                )
            latest_events = []
            if _sqlite_table_exists(conn, "settings_events"):
                latest_events = _rows_to_dicts(
                    conn.execute(
                        """
                        SELECT slot, profile, status, detail_json, recorded_at
                        FROM settings_events
                        ORDER BY recorded_at DESC, event_id DESC
                        LIMIT 40
                        """
                    ).fetchall()
                )
            latest_battery = None
            if _sqlite_table_exists(conn, "battery_daily_metrics"):
                latest_battery = conn.execute(
                    """
                    SELECT date, setting_soc_target_percent, night_charge_kwh
                    FROM battery_daily_metrics
                    ORDER BY date DESC
                    LIMIT 1
                    """
                ).fetchone()
            latest_schedule = _build_latest_schedule_from_events(
                event_rows=latest_events,
                battery_row=dict(latest_battery) if latest_battery is not None else None,
                plan_date=end_date_iso,
            )

        meta = _meta_from_data(
            window_days=window_days,
            global_oldest_date=global_oldest,
            global_newest_date=global_newest,
            sunshine_daily=sunshine,
            cost_daily=cost_daily,
            battery_daily=battery_daily,
            energy_daily=energy_daily,
            forecast_hourly=forecast_hourly,
            battery_flow_daily=battery_flow_daily,
        )
        return DashboardSlice(
            data=DashboardData(
                sunshine_daily=sunshine,
                cost_daily=cost_daily,
                cost_monthly=cost_monthly,
                battery_daily=battery_daily,
                model_parameters=params,
                battery_flow_daily=battery_flow_daily,
                energy_daily=energy_daily,
                forecast_hourly=forecast_hourly,
                latest_schedule=latest_schedule,
                dashboard_warnings=_build_dashboard_warnings(
                    latest_schedule=latest_schedule,
                    battery_daily=battery_daily,
                    energy_daily=energy_daily,
                    end_date_iso=end_date_iso,
                ),
                pv_forecast_diagnostics=_read_latest_pv_forecast_diagnostics() if include_static else {},
            ),
            meta=meta,
        )
    finally:
        conn.close()


def _load_postgres_slice(
    *,
    end_date: str | None,
    window_days: int,
    include_static: bool,
) -> DashboardSlice:
    import psycopg
    from psycopg.rows import dict_row

    empty_schedule = _default_latest_schedule()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        conn = psycopg.connect(database_url, row_factory=dict_row)
    else:
        host = os.getenv("PGHOST", "").strip()
        dbname = os.getenv("PGDATABASE", "").strip()
        user = os.getenv("PGUSER", "").strip()
        password = os.getenv("PGPASSWORD", "").strip()
        port = int(os.getenv("PGPORT", "5432"))
        sslmode = os.getenv("PGSSLMODE", "prefer").strip() or "prefer"
        if not host or not dbname or not user or not password:
            return DashboardSlice(
                data=DashboardData([], [], [], [], [], latest_schedule=empty_schedule),
                meta={
                    "window_days": window_days,
                    "oldest_loaded_date": None,
                    "newest_loaded_date": None,
                    "global_oldest_date": None,
                    "global_newest_date": None,
                    "has_more_before": False,
                },
            )
        conn = psycopg.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password,
            sslmode=sslmode,
            row_factory=dict_row,
        )
    try:
        with conn.cursor() as cur:
            global_oldest, global_newest = _get_global_bounds_postgres(cur)
            if not global_newest:
                return DashboardSlice(
                    data=DashboardData([], [], [], [], [], latest_schedule=empty_schedule),
                    meta={
                        "window_days": window_days,
                        "oldest_loaded_date": None,
                        "newest_loaded_date": None,
                        "global_oldest_date": None,
                        "global_newest_date": None,
                        "has_more_before": False,
                    },
                )
            end_obj = _to_date_or_none(end_date) or _to_date_or_none(global_newest)
            if end_obj is None:
                return DashboardSlice(
                    data=DashboardData([], [], [], [], [], latest_schedule=empty_schedule),
                    meta={
                        "window_days": window_days,
                        "oldest_loaded_date": None,
                        "newest_loaded_date": None,
                        "global_oldest_date": global_oldest,
                        "global_newest_date": global_newest,
                        "has_more_before": False,
                    },
                )
            start_obj = end_obj - timedelta(days=max(1, window_days) - 1)
            start_date = start_obj.isoformat()
            end_date_iso = end_obj.isoformat()

            cur.execute(
                """
                SELECT *
                FROM sunshine_daily
                WHERE date >= %s AND date <= %s
                ORDER BY date
                """,
                (start_date, end_date_iso),
            )
            sunshine = _rows_to_dicts(cur.fetchall())

            cur.execute(
                """
                SELECT date, self_consumption_kwh, savings_yen, cumulative_kwh, cumulative_yen
                FROM cost_daily
                WHERE date >= %s AND date <= %s
                ORDER BY date
                """,
                (start_date, end_date_iso),
            )
            cost_daily = _rows_to_dicts(cur.fetchall())

            cur.execute(
                """
                SELECT *
                FROM battery_daily_metrics
                WHERE date >= %s AND date <= %s
                ORDER BY date
                """,
                (start_date, end_date_iso),
            )
            battery_daily = _rows_to_dicts(cur.fetchall())

            cur.execute(
                """
                SELECT fh.date, fh.hour, fh.forecast_pv_kwh, fh.forecast_load_kwh,
                       fh.forecast_charge_kwh, ah.actual_load_kwh, ah.latest_sample_at,
                       fh.source, fh.updated_at
                FROM forecast_hourly fh
                LEFT JOIN (
                    SELECT substring(ts,1,10) AS date,
                           EXTRACT(HOUR FROM CAST(ts AS timestamp))::integer AS hour,
                           COALESCE(SUM(COALESCE(load_kwh,0)), 0) AS actual_load_kwh,
                           MAX(ts) AS latest_sample_at
                    FROM monitoring_samples
                    WHERE substring(ts,1,10) >= %s AND substring(ts,1,10) <= %s
                    GROUP BY substring(ts,1,10), EXTRACT(HOUR FROM CAST(ts AS timestamp))::integer
                ) ah ON ah.date = fh.date AND ah.hour = fh.hour
                WHERE fh.date >= %s AND fh.date <= %s
                ORDER BY fh.date, fh.hour
                """,
                (start_date, end_date_iso, start_date, end_date_iso),
            )
            forecast_hourly = _rows_to_dicts(cur.fetchall())

            history_start = (start_obj - timedelta(days=14)).isoformat()
            cur.execute(
                """
                SELECT substring(ts,1,10) AS date,
                       COALESCE(SUM(COALESCE(pv_kwh,0)), 0) AS actual_pv_kwh,
                       COALESCE(SUM(COALESCE(load_kwh,0)), 0) AS actual_load_kwh
                FROM monitoring_samples
                WHERE substring(ts,1,10) >= %s AND substring(ts,1,10) <= %s
                GROUP BY substring(ts,1,10)
                ORDER BY date
                """,
                (history_start, end_date_iso),
            )
            monitoring_daily = _rows_to_dicts(cur.fetchall())

            cur.execute(
                """
                SELECT substring(ts,1,10) AS date,
                       COALESCE(SUM(COALESCE(charge_kwh,0)), 0) AS charge_kwh,
                       COALESCE(SUM(COALESCE(discharge_kwh,0)), 0) AS discharge_kwh
                FROM monitoring_samples
                WHERE substring(ts,1,10) >= %s AND substring(ts,1,10) <= %s
                GROUP BY substring(ts,1,10)
                ORDER BY date
                """,
                (start_date, end_date_iso),
            )
            battery_flow_daily = _rows_to_dicts(cur.fetchall())

            cur.execute(
                """
                SELECT name, mean_value
                FROM model_parameters
                ORDER BY name
                """
            )
            params_for_energy = _rows_to_dicts(cur.fetchall())
            energy_daily = _build_energy_daily(
                start_date=start_date,
                end_date_iso=end_date_iso,
                sunshine_daily=sunshine,
                monitoring_daily=monitoring_daily,
                model_parameters=params_for_energy,
            )

            cost_monthly: list[dict[str, Any]] = []
            params: list[dict[str, Any]] = []
            latest_schedule = _default_latest_schedule(plan_date=end_date_iso)
            if include_static:
                cur.execute(
                    """
                    SELECT date, self_consumption_kwh, savings_yen
                    FROM cost_daily
                    ORDER BY date
                    """
                )
                cost_monthly = _build_cost_monthly(_rows_to_dicts(cur.fetchall()))

                cur.execute(
                    """
                    SELECT name, mean_value, variance, sample_count
                    FROM model_parameters
                    ORDER BY name
                    """
                )
                params = _rows_to_dicts(cur.fetchall())

                cur.execute(
                    """
                    SELECT slot, profile, status, detail_json, recorded_at
                    FROM settings_events
                    ORDER BY recorded_at DESC, event_id DESC
                    LIMIT 40
                    """
                )
                latest_events = _rows_to_dicts(cur.fetchall())

                cur.execute(
                    """
                    SELECT date, setting_soc_target_percent, night_charge_kwh
                    FROM battery_daily_metrics
                    ORDER BY date DESC
                    LIMIT 1
                    """
                )
                latest_battery = cur.fetchone()
                latest_schedule = _build_latest_schedule_from_events(
                    event_rows=latest_events,
                    battery_row=latest_battery if isinstance(latest_battery, dict) else None,
                    plan_date=end_date_iso,
                )

            meta = _meta_from_data(
                window_days=window_days,
                global_oldest_date=global_oldest,
                global_newest_date=global_newest,
                sunshine_daily=sunshine,
                cost_daily=cost_daily,
                battery_daily=battery_daily,
                energy_daily=energy_daily,
                forecast_hourly=forecast_hourly,
                battery_flow_daily=battery_flow_daily,
            )
            return DashboardSlice(
                data=DashboardData(
                    sunshine_daily=sunshine,
                    cost_daily=cost_daily,
                    cost_monthly=cost_monthly,
                    battery_daily=battery_daily,
                    model_parameters=params,
                    battery_flow_daily=battery_flow_daily,
                    energy_daily=energy_daily,
                    forecast_hourly=forecast_hourly,
                    latest_schedule=latest_schedule,
                    dashboard_warnings=_build_dashboard_warnings(
                        latest_schedule=latest_schedule,
                        battery_daily=battery_daily,
                        energy_daily=energy_daily,
                        end_date_iso=end_date_iso,
                    ),
                    pv_forecast_diagnostics=_read_latest_pv_forecast_diagnostics() if include_static else {},
                ),
                meta=meta,
            )
    finally:
        conn.close()


def _firestore_bounds(client, collection_name: str) -> tuple[str | None, str | None]:
    col = client.collection(collection_name)
    min_doc = next(col.order_by("date").limit(1).stream(), None)
    max_docs = col.order_by("date").limit_to_last(1).get()
    max_doc = max_docs[0] if max_docs else None
    min_date = None
    max_date = None
    if min_doc is not None:
        d = min_doc.to_dict() or {}
        min_date = str(d.get("date", "")).strip() or None
    if max_doc is not None:
        d = max_doc.to_dict() or {}
        max_date = str(d.get("date", "")).strip() or None
    return min_date, max_date


def _firestore_rows_between(
    client,
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
    client,
    *,
    start_date: str,
    end_date_iso: str,
) -> list[dict[str, Any]]:
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


def _load_firestore_slice(
    *,
    end_date: str | None,
    window_days: int,
    include_static: bool,
) -> DashboardSlice:
    from google.cloud import firestore

    empty_schedule = _default_latest_schedule()
    project_id = os.getenv("FIRESTORE_PROJECT_ID", "").strip() or None
    database_id = os.getenv("FIRESTORE_DATABASE_ID", "").strip() or "(default)"
    client = firestore.Client(project=project_id, database=database_id) if project_id else firestore.Client(database=database_id)

    b1 = _firestore_bounds(client, "sunshine_daily")
    b2 = _firestore_bounds(client, "cost_daily")
    b3 = _firestore_bounds(client, "battery_daily_metrics")
    b4 = _firestore_bounds(client, "forecast_hourly")
    global_oldest, global_newest = _pick_min_max_dates([b1[0], b1[1], b2[0], b2[1], b3[0], b3[1], b4[0], b4[1]])
    if not global_newest:
        return DashboardSlice(
            data=DashboardData([], [], [], [], [], latest_schedule=empty_schedule),
            meta={
                "window_days": window_days,
                "oldest_loaded_date": None,
                "newest_loaded_date": None,
                "global_oldest_date": None,
                "global_newest_date": None,
                "has_more_before": False,
            },
        )

    end_obj = _to_date_or_none(end_date) or _to_date_or_none(global_newest)
    if end_obj is None:
        return DashboardSlice(
            data=DashboardData([], [], [], [], [], latest_schedule=empty_schedule),
            meta={
                "window_days": window_days,
                "oldest_loaded_date": None,
                "newest_loaded_date": None,
                "global_oldest_date": global_oldest,
                "global_newest_date": global_newest,
                "has_more_before": False,
            },
        )
    start_obj = end_obj - timedelta(days=max(1, window_days) - 1)
    start_date = start_obj.isoformat()
    end_date_iso = end_obj.isoformat()

    sunshine = _firestore_rows_between(
        client,
        collection_name="sunshine_daily",
        start_date=start_date,
        end_date_iso=end_date_iso,
        fields=[
            "forecast_hours",
            "actual_hours",
            "forecast_temp_c",
            "actual_temp_c",
            "forecast_pv_total_kwh",
            "forecast_pv_morning_kwh",
            "forecast_pv_midday_kwh",
            "forecast_pv_evening_kwh",
            "forecast_pv_calibration_factor",
        ],
    )
    cost_daily = _firestore_rows_between(
        client,
        collection_name="cost_daily",
        start_date=start_date,
        end_date_iso=end_date_iso,
        fields=["self_consumption_kwh", "savings_yen", "cumulative_kwh", "cumulative_yen"],
    )
    battery_daily = _firestore_rows_between(
        client,
        collection_name="battery_daily_metrics",
        start_date=start_date,
        end_date_iso=end_date_iso,
        fields=["setting_soc_target_percent", "night_charge_kwh", "pv_max_charge_kwh", "pv_charge_end_soc_percent", "pv_charge_end_at"],
    )
    forecast_hourly = _firestore_rows_between(
        client,
        collection_name="forecast_hourly",
        start_date=start_date,
        end_date_iso=end_date_iso,
        fields=["hour", "forecast_pv_kwh", "forecast_load_kwh", "forecast_charge_kwh", "source", "updated_at"],
    )
    forecast_hourly.sort(key=lambda row: (str(row.get("date", "")), int(row.get("hour") or 0)))
    history_start = (start_obj - timedelta(days=14)).isoformat()
    monitoring_daily = _firestore_monitoring_daily(
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
    params_for_energy: list[dict[str, Any]] = []
    for doc in client.collection("model_parameters").order_by("name").stream():
        row = doc.to_dict() or {}
        params_for_energy.append(
            {
                "name": row.get("name", doc.id),
                "mean_value": row.get("mean_value"),
            }
        )
    energy_daily = _build_energy_daily(
        start_date=start_date,
        end_date_iso=end_date_iso,
        sunshine_daily=sunshine,
        monitoring_daily=monitoring_daily,
        model_parameters=params_for_energy,
    )

    cost_monthly: list[dict[str, Any]] = []
    params: list[dict[str, Any]] = []
    latest_schedule = _default_latest_schedule(plan_date=end_date_iso)
    if include_static:
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
                    "slot": row.get("slot"),
                    "profile": row.get("profile"),
                    "status": row.get("status"),
                    "detail_json": row.get("detail_json"),
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

    meta = _meta_from_data(
        window_days=window_days,
        global_oldest_date=global_oldest,
        global_newest_date=global_newest,
        sunshine_daily=sunshine,
        cost_daily=cost_daily,
        battery_daily=battery_daily,
        energy_daily=energy_daily,
        forecast_hourly=forecast_hourly,
        battery_flow_daily=battery_flow_daily,
    )
    return DashboardSlice(
        data=DashboardData(
            sunshine_daily=sunshine,
            cost_daily=cost_daily,
            cost_monthly=cost_monthly,
            battery_daily=battery_daily,
            model_parameters=params,
            battery_flow_daily=battery_flow_daily,
            energy_daily=energy_daily,
            forecast_hourly=forecast_hourly,
            latest_schedule=latest_schedule,
            dashboard_warnings=_build_dashboard_warnings(
                latest_schedule=latest_schedule,
                battery_daily=battery_daily,
                energy_daily=energy_daily,
                end_date_iso=end_date_iso,
            ),
            pv_forecast_diagnostics=(
                _read_latest_pv_forecast_diagnostics_from_firestore(client) if include_static else {}
            ),
        ),
        meta=meta,
    )


def load_dashboard_slice(
    db_path: Path,
    *,
    end_date: str | None,
    window_days: int = 31,
    include_static: bool = True,
) -> DashboardSlice:
    backend = os.getenv("DATA_BACKEND", "sqlite").strip().lower()
    days = min(max(1, int(window_days)), 365)
    if backend == "postgres":
        return _load_postgres_slice(end_date=end_date, window_days=days, include_static=include_static)
    if backend == "firestore":
        return _load_firestore_slice(end_date=end_date, window_days=days, include_static=include_static)
    return _load_sqlite_slice(db_path, end_date=end_date, window_days=days, include_static=include_static)


def load_dashboard_data(db_path: Path) -> DashboardData:
    # Backward compatible full load API.
    return load_dashboard_slice(db_path, end_date=None, window_days=365, include_static=True).data
