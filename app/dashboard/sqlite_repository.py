from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.dashboard.repositories import DashboardLoadRequest, DashboardQuerySnapshot
from app.dashboard.models import DashboardSlice
from app.dashboard.aggregation import _monitoring_history_start
from app.dashboard.slice_assembler import build_slice_from_query_snapshot
from app.dashboard.repository_support import pick_min_max_dates as _pick_min_max_dates, rows_to_dicts as _rows_to_dicts, to_date_or_none as _to_date_or_none


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
            # HISTORICAL_FAILURE_LOCK: preserve the same run/issued-at evidence as
            # Firestore; flattening a validation replica must not erase provenance.
            identity_fields = ("forecast_run_id", "forecast_issued_at")
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(forecast_hourly)")}
            identity_sql = "".join(f", fh.{name}" for name in identity_fields if name in columns)
            plan_fields = ("forecast_soc_percent", "forecast_grid_charge_kwh")
            plan_sql = "".join(
                f", fh.{name}" if name in columns else f", NULL AS {name}" for name in plan_fields
            )
            forecast_hourly = _rows_to_dicts(conn.execute(f"""
                WITH hourly_actuals AS (
                    SELECT substr(ts,1,10) AS date, CAST(strftime('%H', ts) AS INTEGER) AS hour,
                           CASE WHEN COUNT(*) = 2 AND COUNT(DISTINCT substr(ts,15,2)) = 2
                                AND COUNT(CASE WHEN typeof(load_kwh) IN ('integer','real') AND load_kwh >= 0
                                    AND load_kwh < 1e999 AND substr(ts,15,2) IN ('00','30')
                                    AND substr(ts,18,2) = '00' THEN 1 END) = 2
                                THEN SUM(load_kwh) END AS actual_load_kwh,
                           CASE WHEN COUNT(*) = 2 AND COUNT(DISTINCT substr(ts,15,2)) = 2
                                AND COUNT(CASE WHEN typeof(pv_kwh) IN ('integer','real') AND pv_kwh >= 0
                                    AND pv_kwh < 1e999 AND substr(ts,15,2) IN ('00','30')
                                    AND substr(ts,18,2) = '00' THEN 1 END) = 2
                                THEN SUM(pv_kwh) END AS actual_pv_kwh,
                           MIN(ts) AS first_sample_at,
                           MAX(ts) AS latest_sample_at
                    FROM monitoring_samples
                    WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) <= ?
                    GROUP BY substr(ts,1,10), CAST(strftime('%H', ts) AS INTEGER)
                ), hourly_soc AS (
                    SELECT date, hour,
                           MAX(CASE WHEN opening_row_number = 1 THEN soc_percent END) AS opening_soc_percent,
                           MAX(CASE WHEN latest_row_number = 1 THEN soc_percent END) AS actual_soc_percent
                    FROM (
                        SELECT substr(ts,1,10) AS date,
                               CAST(strftime('%H', ts) AS INTEGER) AS hour,
                               soc_percent,
                               ROW_NUMBER() OVER (
                                   PARTITION BY substr(ts,1,10), CAST(strftime('%H', ts) AS INTEGER)
                                   ORDER BY ts ASC
                               ) AS opening_row_number,
                               ROW_NUMBER() OVER (
                                   PARTITION BY substr(ts,1,10), CAST(strftime('%H', ts) AS INTEGER)
                                   ORDER BY ts DESC
                               ) AS latest_row_number
                        FROM monitoring_samples
                        WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) <= ?
                          AND soc_percent IS NOT NULL
                          AND soc_percent >= 0 AND soc_percent <= 100
                    )
                    GROUP BY date, hour
                ), display_hours AS (
                    SELECT date, hour FROM forecast_hourly WHERE date >= ? AND date <= ?
                    UNION SELECT date, hour FROM hourly_actuals
                )
                SELECT dh.date, dh.hour, fh.forecast_pv_kwh, fh.forecast_load_kwh,
                       fh.forecast_charge_kwh, ah.actual_load_kwh, ah.actual_pv_kwh, hs.actual_soc_percent,
                       hs.opening_soc_percent,
                       ah.first_sample_at, ah.latest_sample_at, fh.source, fh.updated_at{identity_sql}{plan_sql},
                       fh.forecast_reconstruction_id, fh.forecast_reconstructed_at,
                       fh.forecast_reconstruction_model_version, fh.forecast_reconstruction_basis
                FROM display_hours dh
                LEFT JOIN forecast_hourly fh ON fh.date = dh.date AND fh.hour = dh.hour
                LEFT JOIN hourly_actuals ah ON ah.date = dh.date AND ah.hour = dh.hour
                LEFT JOIN hourly_soc hs ON hs.date = dh.date AND hs.hour = dh.hour
                WHERE dh.date >= ? AND dh.date <= ? ORDER BY dh.date, dh.hour
                """, (start_date, end_date_iso, start_date, end_date_iso,
                      start_date, end_date_iso, start_date, end_date_iso)).fetchall())
            for row in forecast_hourly:
                for name in identity_fields:
                    if row.get(name) is None:
                        row.pop(name, None)
        history_start = _monitoring_history_start(start_obj, include_static=request.include_static)
        monitoring_daily: list[dict[str, Any]] = []
        battery_flow_daily: list[dict[str, Any]] = []
        if _sqlite_table_exists(conn, "monitoring_samples"):
            raw_monitoring_daily = _rows_to_dicts(conn.execute("""
                SELECT substr(ts,1,10) AS date,
                       CASE WHEN COUNT(CASE WHEN typeof(pv_kwh) IN ('integer','real') AND pv_kwh >= 0 AND pv_kwh < 1e999 THEN 1 END) = COUNT(*)
                            THEN SUM(pv_kwh) END AS actual_pv_kwh,
                       CASE WHEN COUNT(CASE WHEN typeof(load_kwh) IN ('integer','real') AND load_kwh >= 0 AND load_kwh < 1e999 THEN 1 END) = COUNT(*)
                            THEN SUM(load_kwh) END AS actual_load_kwh,
                       CASE WHEN COUNT(CASE WHEN typeof(charge_kwh) IN ('integer','real') AND charge_kwh >= 0 AND charge_kwh < 1e999 THEN 1 END) = COUNT(*)
                            THEN SUM(charge_kwh) END AS charge_kwh,
                       CASE WHEN COUNT(CASE WHEN typeof(discharge_kwh) IN ('integer','real') AND discharge_kwh >= 0 AND discharge_kwh < 1e999 THEN 1 END) = COUNT(*)
                            THEN SUM(discharge_kwh) END AS discharge_kwh
                FROM monitoring_samples WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) <= ?
                GROUP BY substr(ts,1,10) ORDER BY date
                """, (history_start, end_date_iso)).fetchall())
            authoritative_daily: list[dict[str, Any]] = []
            if _sqlite_table_exists(conn, "dashboard_daily_metrics"):
                authoritative_daily = _rows_to_dicts(conn.execute("""
                    SELECT date, actual_pv_kwh, actual_load_kwh, charge_kwh, discharge_kwh
                    FROM dashboard_daily_metrics
                    WHERE date >= ? AND date <= ? ORDER BY date
                    """, (history_start, end_date_iso)).fetchall())
            raw_by_date = {str(row["date"]): row for row in raw_monitoring_daily}
            authoritative_by_date = {str(row["date"]): row for row in authoritative_daily}
            monitoring_daily = []
            for day in sorted(set(raw_by_date) | set(authoritative_by_date)):
                authoritative = authoritative_by_date.get(day, {})
                raw = raw_by_date.get(day, {})
                monitoring_daily.append({
                    "date": day,
                    "actual_pv_kwh": authoritative.get("actual_pv_kwh") if authoritative.get("actual_pv_kwh") is not None else raw.get("actual_pv_kwh"),
                    "actual_load_kwh": authoritative.get("actual_load_kwh") if authoritative.get("actual_load_kwh") is not None else raw.get("actual_load_kwh"),
                    "charge_kwh": authoritative.get("charge_kwh") if authoritative.get("charge_kwh") is not None else raw.get("charge_kwh"),
                    "discharge_kwh": authoritative.get("discharge_kwh") if authoritative.get("discharge_kwh") is not None else raw.get("discharge_kwh"),
                })
            battery_flow_daily = [
                {
                    "date": row["date"],
                    "charge_kwh": row.get("charge_kwh"),
                    "discharge_kwh": row.get("discharge_kwh"),
                }
                for row in monitoring_daily
                if start_date <= str(row.get("date", "")) <= end_date_iso
            ]

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


def load_sqlite_slice(db_path: Path, *, end_date: str | None, window_days: int, include_static: bool) -> DashboardSlice:
    request = DashboardLoadRequest(end_date=end_date, window_days=window_days, include_static=include_static)
    return build_slice_from_query_snapshot(load_sqlite_query_snapshot(db_path, request), window_days=window_days, include_static=include_static)


@dataclass(frozen=True)
class SQLiteDashboardRepository:
    db_path: Path

    def load_dashboard(self, request: DashboardLoadRequest) -> DashboardSlice:
        return load_sqlite_slice(self.db_path, end_date=request.end_date, window_days=request.window_days, include_static=request.include_static)
