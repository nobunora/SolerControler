from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.dashboard.repositories import DashboardLoadRequest, DashboardQuerySnapshot


def _rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _to_date_or_none(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _pick_min_max_dates(values: list[str | None]) -> tuple[str | None, str | None]:
    dates = [value for value in values if value]
    if not dates:
        return None, None
    return min(dates), max(dates)


def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


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


def _empty_snapshot(
    *,
    global_oldest_date: str | None = None,
    global_newest_date: str | None = None,
) -> DashboardQuerySnapshot:
    return DashboardQuerySnapshot(
        resolved_end_date=None,
        global_oldest_date=global_oldest_date,
        global_newest_date=global_newest_date,
        pv_daily=[],
        cost_daily=[],
        battery_daily=[],
        forecast_hourly=[],
        monitoring_daily=[],
        battery_flow_daily=[],
        all_cost_daily=[],
        model_parameters=[],
        settings_events=[],
        latest_battery=None,
    )


def load_sqlite_query_snapshot(db_path: Path, request: DashboardLoadRequest) -> DashboardQuerySnapshot:
    """Read SQLite-specific dashboard rows without calculating dashboard values."""
    if not db_path.exists():
        return _empty_snapshot()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        global_oldest, global_newest = _get_global_bounds_sqlite(conn)
        end_obj = _to_date_or_none(request.end_date) or _to_date_or_none(global_newest)
        if end_obj is None:
            return _empty_snapshot(
                global_oldest_date=global_oldest,
                global_newest_date=global_newest,
            )

        start_obj = end_obj - timedelta(days=max(1, request.window_days) - 1)
        start_date = start_obj.isoformat()
        end_date_iso = end_obj.isoformat()
        pv_daily: list[dict[str, Any]] = []
        if _sqlite_table_exists(conn, "sunshine_daily"):
            pv_daily = _rows_to_dicts(conn.execute("""
                SELECT date, forecast_temp_c, actual_temp_c,
                       forecast_pv_total_kwh, forecast_pv_morning_kwh,
                       forecast_pv_midday_kwh, forecast_pv_evening_kwh,
                       forecast_pv_calibration_factor
                FROM sunshine_daily WHERE date >= ? AND date <= ? ORDER BY date
                """, (start_date, end_date_iso)).fetchall())
        cost_daily: list[dict[str, Any]] = []
        if _sqlite_table_exists(conn, "cost_daily"):
            cost_daily = _rows_to_dicts(conn.execute("""
                SELECT date, self_consumption_kwh, savings_yen, cumulative_kwh, cumulative_yen
                FROM cost_daily WHERE date >= ? AND date <= ? ORDER BY date
                """, (start_date, end_date_iso)).fetchall())
        battery_daily: list[dict[str, Any]] = []
        if _sqlite_table_exists(conn, "battery_daily_metrics"):
            battery_daily = _rows_to_dicts(conn.execute("""
                SELECT date, setting_soc_target_percent, night_charge_kwh,
                       pv_charge_end_soc_percent, pv_charge_end_at,
                       end_of_day_soc_percent, settings_run_id, source_doc_id,
                       source_status, source_profile, plan_quality_status,
                       plan_should_apply, updated_at
                FROM battery_daily_metrics WHERE date >= ? AND date <= ? ORDER BY date
                """, (start_date, end_date_iso)).fetchall())
        forecast_hourly: list[dict[str, Any]] = []
        if _sqlite_table_exists(conn, "forecast_hourly"):
            forecast_hourly = _rows_to_dicts(conn.execute("""
                SELECT fh.date, fh.hour, fh.forecast_pv_kwh, fh.forecast_load_kwh,
                       fh.forecast_charge_kwh, ah.actual_load_kwh, ah.latest_sample_at,
                       fh.source, fh.updated_at
                FROM forecast_hourly fh
                LEFT JOIN (
                    SELECT substr(ts,1,10) AS date, CAST(strftime('%H', ts) AS INTEGER) AS hour,
                           COALESCE(SUM(COALESCE(load_kwh,0)), 0) AS actual_load_kwh,
                           MAX(ts) AS latest_sample_at
                    FROM monitoring_samples
                    WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) <= ?
                    GROUP BY substr(ts,1,10), CAST(strftime('%H', ts) AS INTEGER)
                ) ah ON ah.date = fh.date AND ah.hour = fh.hour
                WHERE fh.date >= ? AND fh.date <= ? ORDER BY fh.date, fh.hour
                """, (start_date, end_date_iso, start_date, end_date_iso)).fetchall())
        history_start = (start_obj - timedelta(days=14)).isoformat()
        monitoring_daily: list[dict[str, Any]] = []
        battery_flow_daily: list[dict[str, Any]] = []
        if _sqlite_table_exists(conn, "monitoring_samples"):
            monitoring_daily = _rows_to_dicts(conn.execute("""
                SELECT substr(ts,1,10) AS date,
                       COALESCE(SUM(COALESCE(pv_kwh,0)), 0) AS actual_pv_kwh,
                       COALESCE(SUM(COALESCE(load_kwh,0)), 0) AS actual_load_kwh
                FROM monitoring_samples WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) <= ?
                GROUP BY substr(ts,1,10) ORDER BY date
                """, (history_start, end_date_iso)).fetchall())
            battery_flow_daily = _rows_to_dicts(conn.execute("""
                SELECT substr(ts,1,10) AS date,
                       COALESCE(SUM(COALESCE(charge_kwh,0)), 0) AS charge_kwh,
                       COALESCE(SUM(COALESCE(discharge_kwh,0)), 0) AS discharge_kwh
                FROM monitoring_samples WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) <= ?
                GROUP BY substr(ts,1,10) ORDER BY date
                """, (start_date, end_date_iso)).fetchall())

        all_cost_daily: list[dict[str, Any]] = []
        model_parameters: list[dict[str, Any]] = []
        settings_events: list[dict[str, Any]] = []
        latest_battery: dict[str, Any] | None = None
        if request.include_static:
            if _sqlite_table_exists(conn, "cost_daily"):
                all_cost_daily = _rows_to_dicts(conn.execute(
                    "SELECT date, self_consumption_kwh, savings_yen FROM cost_daily ORDER BY date"
                ).fetchall())
            if _sqlite_table_exists(conn, "model_parameters"):
                model_parameters = _rows_to_dicts(conn.execute(
                    "SELECT name, mean_value, variance, sample_count FROM model_parameters ORDER BY name"
                ).fetchall())
            if _sqlite_table_exists(conn, "settings_events"):
                settings_events = _rows_to_dicts(conn.execute("""
                    SELECT event_id, run_id, slot, profile, status, detail_json, source_doc_id, recorded_at
                    FROM settings_events ORDER BY recorded_at DESC, event_id DESC LIMIT 40
                    """).fetchall())
            if _sqlite_table_exists(conn, "battery_daily_metrics"):
                row = conn.execute("SELECT * FROM battery_daily_metrics ORDER BY date DESC LIMIT 1").fetchone()
                latest_battery = dict(row) if row is not None else None

        return DashboardQuerySnapshot(
            resolved_end_date=end_date_iso,
            global_oldest_date=global_oldest,
            global_newest_date=global_newest,
            pv_daily=pv_daily,
            cost_daily=cost_daily,
            battery_daily=battery_daily,
            forecast_hourly=forecast_hourly,
            monitoring_daily=monitoring_daily,
            battery_flow_daily=battery_flow_daily,
            all_cost_daily=all_cost_daily,
            model_parameters=model_parameters,
            settings_events=settings_events,
            latest_battery=latest_battery,
        )
    finally:
        conn.close()
