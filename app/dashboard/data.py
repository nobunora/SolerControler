from __future__ import annotations

import os
import json
import math
import sqlite3
import time
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.dashboard.models import DashboardData, DashboardRawData, DashboardSlice
from app.dashboard.repositories import DashboardLoadRequest, DashboardQuerySnapshot
from app.dashboard.schedule import (
    _build_latest_schedule_from_events,
    _default_latest_schedule,
    _select_schedule_event,
)
from app.dashboard.service import assemble_dashboard_slice, merge_forecast_hourly_actuals
from app.dashboard.warnings import build_dashboard_warnings
from app.domain.tariff import tiered_day_cost
from app.parsing.numbers import to_float


_FIRESTORE_CLIENTS: dict[tuple[str | None, str], Any] = {}
_FIRESTORE_SLICE_CACHE: dict[
    tuple[str | None, str, str | None, int, bool], tuple[float, "DashboardSlice"]
] = {}
_FIRESTORE_DASHBOARD_CACHE_SECONDS = 120.0


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
    return _extract_pv_forecast_diagnostics(row)


def _date_add_iso(date_text: str, delta_days: int) -> str | None:
    d = _to_date_or_none(date_text)
    if d is None:
        return None
    return (d + timedelta(days=delta_days)).isoformat()


def _today_jst_iso() -> str:
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _aggregation_close_day() -> int:
    # 契約月の締め日を既定14日とし、暦月ではなく料金請求期間で集計をそろえる。
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


def _forecast_pv_kwh(
    sunshine_row: dict[str, Any] | None,
) -> float | None:
    if not sunshine_row:
        return None
    array_forecast = to_float(sunshine_row.get("forecast_pv_total_kwh"))
    if array_forecast is not None:
        return max(0.0, array_forecast)
    return None


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
    pv_daily: list[dict[str, Any]],
    monitoring_daily: list[dict[str, Any]],
    forecast_hourly: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    pv_by_day = {str(row.get("date")): row for row in pv_daily if row.get("date")}
    actual_by_day = {str(row.get("date")): row for row in monitoring_daily if row.get("date")}
    hourly_load_by_day: dict[str, float] = {}
    for row in forecast_hourly or []:
        day = str(row.get("date") or "")
        value = to_float(row.get("forecast_load_kwh"))
        if day and value is not None:
            hourly_load_by_day[day] = hourly_load_by_day.get(day, 0.0) + max(0.0, value)
    dates = {
        d
        for d in set(pv_by_day) | set(actual_by_day)
        if start_date <= d <= end_date_iso
    }
    out: list[dict[str, Any]] = []
    for day in sorted(dates):
        actual = actual_by_day.get(day, {})
        pv = pv_by_day.get(day)
        forecast_pv = _forecast_pv_kwh(pv)
        out.append(
            {
                "date": day,
                "forecast_pv_kwh": forecast_pv,
                "forecast_pv_morning_kwh": (pv or {}).get("forecast_pv_morning_kwh"),
                "forecast_pv_midday_kwh": (pv or {}).get("forecast_pv_midday_kwh"),
                "forecast_pv_evening_kwh": (pv or {}).get("forecast_pv_evening_kwh"),
                "forecast_pv_calibration_factor": (pv or {}).get("forecast_pv_calibration_factor"),
                "actual_pv_kwh": actual.get("actual_pv_kwh"),
                "forecast_load_kwh": (
                    hourly_load_by_day[day]
                    if day in hourly_load_by_day
                    else _rolling_load_forecast(day, actual_by_day)
                ),
                "forecast_load_source": (
                    "forecast_hourly" if day in hourly_load_by_day else "rolling_14d_fallback"
                ),
                "actual_load_kwh": actual.get("actual_load_kwh"),
            }
        )
    return out


def _empty_dashboard_slice(*, window_days: int, schedule: dict[str, Any], global_oldest: str | None = None, global_newest: str | None = None) -> DashboardSlice:
    """Build the consistent empty response used when a backend has no usable rows."""
    return DashboardSlice(
        data=DashboardData([], [], [], [], [], latest_schedule=schedule),
        meta={
            "window_days": window_days,
            "oldest_loaded_date": None,
            "newest_loaded_date": None,
            "global_oldest_date": global_oldest,
            "global_newest_date": global_newest,
            "has_more_before": False,
        },
    )


def _merge_latest_plan_into_schedule(
    schedule: dict[str, Any],
    plan: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(schedule)
    if not plan:
        return merged
    schedule_date = str(merged.get("plan_date") or "").strip()
    plan_date = str(plan.get("date") or _nested_dict(plan, "forecast").get("date") or "").strip()
    if schedule_date and plan_date != schedule_date:
        return merged
    result = _nested_dict(plan, "result")
    target_soc = to_float(result.get("target_soc_7_percent"))
    night_charge = to_float(result.get("required_night_charge_kwh"))
    if target_soc is not None:
        merged["planned_target_soc_percent"] = target_soc
    if night_charge is not None:
        merged["planned_night_charge_kwh"] = night_charge
    updated_at = str(plan.get("updated_at") or "").strip()
    if updated_at:
        merged["plan_updated_at"] = updated_at
    return merged


def _build_dashboard_warnings(
    *,
    latest_schedule: dict[str, Any],
    battery_daily: list[dict[str, Any]],
    energy_daily: list[dict[str, Any]],
    end_date_iso: str,
) -> list[dict[str, Any]]:
    return build_dashboard_warnings(
        latest_schedule=latest_schedule,
        battery_daily=battery_daily,
        energy_daily=energy_daily,
        end_date_iso=end_date_iso,
        today_jst_iso=_today_jst_iso(),
    )


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


def _get_global_bounds_postgres(cur: Any) -> tuple[str | None, str | None]:
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
    pv_daily: list[dict[str, Any]],
    cost_daily: list[dict[str, Any]],
    battery_daily: list[dict[str, Any]],
    energy_daily: list[dict[str, Any]] | None = None,
    forecast_hourly: list[dict[str, Any]] | None = None,
    battery_flow_daily: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    all_dates: list[str] = []
    all_dates.extend([str(x.get("date", "")) for x in pv_daily if x.get("date")])
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


def _build_dashboard_slice(
    raw: DashboardRawData,
    *,
    end_date_iso: str,
    window_days: int,
    pv_forecast_diagnostics: dict[str, Any] | None = None,
    daily_review: dict[str, Any] | None = None,
    daily_reviews: list[dict[str, Any]] | None = None,
) -> DashboardSlice:
    meta = _meta_from_data(
        window_days=window_days,
        global_oldest_date=raw.global_oldest,
        global_newest_date=raw.global_newest,
        pv_daily=raw.pv_daily,
        cost_daily=raw.cost_daily,
        battery_daily=raw.battery_daily,
        energy_daily=raw.energy_daily,
        forecast_hourly=raw.forecast_hourly,
        battery_flow_daily=raw.battery_flow_daily,
    )
    return assemble_dashboard_slice(
        raw,
        meta=meta,
        warnings=_build_dashboard_warnings(
            latest_schedule=raw.latest_schedule,
            battery_daily=raw.battery_daily,
            energy_daily=raw.energy_daily,
            end_date_iso=end_date_iso,
        ),
        pv_forecast_diagnostics=pv_forecast_diagnostics,
        daily_review=daily_review,
        daily_reviews=daily_reviews,
    )


def _build_slice_from_query_snapshot(
    snapshot: DashboardQuerySnapshot,
    *,
    window_days: int,
    include_static: bool,
    pv_forecast_diagnostics: dict[str, Any] | None = None,
) -> DashboardSlice:
    """Apply dashboard-wide calculations and API assembly to backend query rows."""
    end_date_iso = snapshot.resolved_end_date
    if end_date_iso is None:
        return _empty_dashboard_slice(
            window_days=window_days,
            schedule=_default_latest_schedule(),
            global_oldest=snapshot.global_oldest_date,
            global_newest=snapshot.global_newest_date,
        )
    end_obj = _to_date_or_none(end_date_iso)
    if end_obj is None:
        return _empty_dashboard_slice(
            window_days=window_days,
            schedule=_default_latest_schedule(),
            global_oldest=snapshot.global_oldest_date,
            global_newest=snapshot.global_newest_date,
        )

    start_date = (end_obj - timedelta(days=max(1, window_days) - 1)).isoformat()
    energy_daily = _build_energy_daily(
        start_date=start_date,
        end_date_iso=end_date_iso,
        pv_daily=snapshot.pv_daily,
        monitoring_daily=snapshot.monitoring_daily,
        forecast_hourly=snapshot.forecast_hourly,
    )
    cost_monthly = _build_cost_monthly(snapshot.all_cost_daily) if include_static else []
    latest_schedule = _default_latest_schedule(plan_date=end_date_iso)
    if include_static:
        latest_schedule = _build_latest_schedule_from_events(
            event_rows=snapshot.settings_events,
            battery_row=snapshot.latest_battery,
            plan_date=end_date_iso,
        )
    raw = DashboardRawData(
        pv_daily=snapshot.pv_daily,
        cost_daily=snapshot.cost_daily,
        cost_monthly=cost_monthly,
        battery_daily=snapshot.battery_daily,
        model_parameters=snapshot.model_parameters,
        battery_flow_daily=snapshot.battery_flow_daily,
        energy_daily=energy_daily,
        forecast_hourly=snapshot.forecast_hourly,
        latest_schedule=latest_schedule,
        global_oldest=snapshot.global_oldest_date,
        global_newest=snapshot.global_newest_date,
    )
    diagnostics = pv_forecast_diagnostics
    if diagnostics is None:
        diagnostics = _read_latest_pv_forecast_diagnostics() if include_static else {}
    return _build_dashboard_slice(
        raw,
        end_date_iso=end_date_iso,
        window_days=window_days,
        pv_forecast_diagnostics=diagnostics,
    )


# readable-code-audit: skip STRUCT-04 — this adapter assembles one complete dashboard snapshot and must keep SQLite queries transactionally consistent
def _load_sqlite_slice(
    db_path: Path,
    *,
    end_date: str | None,
    window_days: int,
    include_static: bool,
) -> DashboardSlice:
    empty_schedule = _default_latest_schedule()
    if not db_path.exists():
        return _empty_dashboard_slice(window_days=window_days, schedule=empty_schedule)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        global_oldest, global_newest = _get_global_bounds_sqlite(conn)
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

        pv_daily = []
        if _sqlite_table_exists(conn, "sunshine_daily"):
            pv_daily = _rows_to_dicts(
                conn.execute(
                    """
                    SELECT date, forecast_temp_c, actual_temp_c,
                           forecast_pv_total_kwh, forecast_pv_morning_kwh,
                           forecast_pv_midday_kwh, forecast_pv_evening_kwh,
                           forecast_pv_calibration_factor
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
                    SELECT date, setting_soc_target_percent, night_charge_kwh,
                           pv_charge_end_soc_percent, pv_charge_end_at,
                           end_of_day_soc_percent, settings_run_id, source_doc_id,
                           source_status, source_profile, plan_quality_status,
                           plan_should_apply, updated_at
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
        energy_daily = _build_energy_daily(
            start_date=start_date,
            end_date_iso=end_date_iso,
            pv_daily=pv_daily,
            monitoring_daily=monitoring_daily,
            forecast_hourly=forecast_hourly,
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
                        SELECT event_id, run_id, slot, profile, status, detail_json, source_doc_id, recorded_at
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
                    SELECT *
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
            pv_forecast_diagnostics=_read_latest_pv_forecast_diagnostics() if include_static else {},
        )
    finally:
        conn.close()


@dataclass(frozen=True)
class SQLiteDashboardRepository:
    db_path: Path

    def load_dashboard(self, request: DashboardLoadRequest) -> DashboardSlice:
        return _load_sqlite_slice(
            self.db_path,
            end_date=request.end_date,
            window_days=request.window_days,
            include_static=request.include_static,
        )


# readable-code-audit: skip STRUCT-04 — this adapter delegates PostgreSQL-specific queries without changing the shared slice assembler

def _load_postgres_slice(
    *,
    end_date: str | None,
    window_days: int,
    include_static: bool,
) -> DashboardSlice:
    from app.dashboard.postgres_repository import load_postgres_slice

    return load_postgres_slice(
        end_date=end_date,
        window_days=window_days,
        include_static=include_static,
    )


@dataclass(frozen=True)
class PostgresDashboardRepository:
    def load_dashboard(self, request: DashboardLoadRequest) -> DashboardSlice:
        return _load_postgres_slice(
            end_date=request.end_date,
            window_days=request.window_days,
            include_static=request.include_static,
        )

def _load_firestore_slice(
    *,
    end_date: str | None,
    window_days: int,
    include_static: bool,
) -> DashboardSlice:
    from app.dashboard.firestore_repository import load_firestore_slice

    return load_firestore_slice(
        end_date=end_date,
        window_days=window_days,
        include_static=include_static,
        client_factory=_open_dashboard_firestore_client,
        bounds_reader=_get_global_bounds_firestore,
        rows_reader=_firestore_rows_between,
        monitoring_reader=_firestore_monitoring_daily,
        hourly_reader=_firestore_forecast_hourly_between,
    )


class FirestoreDashboardRepository:
    def load_dashboard(self, request: DashboardLoadRequest) -> DashboardSlice:
        return _load_firestore_slice(end_date=request.end_date, window_days=request.window_days, include_static=request.include_static)

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
        return PostgresDashboardRepository().load_dashboard(
            DashboardLoadRequest(end_date=end_date, window_days=days, include_static=include_static)
        )
    if backend == "firestore":
        project_id, database_id = _dashboard_firestore_config()
        key = (project_id, database_id, end_date, days, include_static)
        cached = _FIRESTORE_SLICE_CACHE.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < _FIRESTORE_DASHBOARD_CACHE_SECONDS:
            return cached[1]
        sliced = FirestoreDashboardRepository().load_dashboard(
            DashboardLoadRequest(end_date=end_date, window_days=days, include_static=include_static)
        )
        _FIRESTORE_SLICE_CACHE[key] = (time.monotonic(), sliced)
        return sliced
    return SQLiteDashboardRepository(db_path).load_dashboard(
        DashboardLoadRequest(end_date=end_date, window_days=days, include_static=include_static)
    )


def load_dashboard_data(db_path: Path) -> DashboardData:
    # Backward compatible full load API.
    return load_dashboard_slice(db_path, end_date=None, window_days=365, include_static=True).data


# Keep the historical private test/import boundary while Firestore ownership lives in its adapter.
from app.dashboard.firestore_repository import (
    _billing_usage_summary,
    _dashboard_firestore_config,
    _daily_metric_is_complete,
    _firestore_bounds,
    _firestore_forecast_hourly_between,
    _firestore_monitoring_daily,
    _firestore_rows_between,
    _nested_dict,
    _open_dashboard_firestore_client,
    _review_candidate_dates,
)


def _get_global_bounds_firestore(client: Any) -> tuple[str | None, str | None]:
    candidates: list[str | None] = []
    for collection_name, field_name in (
        ("sunshine_daily", "date"),
        ("cost_daily", "date"),
        ("battery_daily_metrics", "date"),
        ("forecast_hourly", "date"),
        ("monitoring_samples", "ts"),
    ):
        try:
            oldest, newest = _firestore_bounds(client, collection_name, field_name)
        except Exception:
            continue
        candidates.extend([oldest, newest])
    return _pick_min_max_dates(candidates)


def clear_dashboard_cache() -> None:
    _FIRESTORE_SLICE_CACHE.clear()
    from app.dashboard.firestore_repository import clear_dashboard_cache as clear_firestore_cache

    clear_firestore_cache()
