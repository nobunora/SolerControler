from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.dashboard.models import DashboardRawData, DashboardSlice
from app.dashboard.repositories import DashboardLoadRequest
from app.dashboard.schedule import _build_latest_schedule_from_events, _default_latest_schedule
from app.dashboard.aggregation import _build_cost_monthly, _build_energy_daily
from app.dashboard.slice_assembler import (
    build_dashboard_slice as _build_dashboard_slice,
    empty_dashboard_slice as _empty_dashboard_slice,
    read_latest_pv_forecast_diagnostics as _read_latest_pv_forecast_diagnostics,
)
from app.dashboard.repository_support import pick_min_max_dates as _pick_min_max_dates, rows_to_dicts as _rows_to_dicts, to_date_or_none as _to_date_or_none


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

# readable-code-audit: skip STRUCT-04 — this adapter mirrors the SQLite snapshot contract while preserving PostgreSQL-specific queries and fallback handling
def load_postgres_slice(
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
            return _empty_dashboard_slice(window_days=window_days, schedule=empty_schedule)
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

            cur.execute(
                """
                SELECT date, forecast_temp_c, actual_temp_c,
                       forecast_pv_total_kwh, forecast_pv_morning_kwh,
                       forecast_pv_midday_kwh, forecast_pv_evening_kwh,
                       forecast_pv_calibration_factor
                FROM sunshine_daily
                WHERE date >= %s AND date <= %s
                ORDER BY date
                """,
                (start_date, end_date_iso),
            )
            pv_daily = _rows_to_dicts(cur.fetchall())

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
                SELECT date, setting_soc_target_percent, night_charge_kwh,
                       pv_charge_end_soc_percent, pv_charge_end_at,
                       end_of_day_soc_percent, settings_run_id, source_doc_id,
                       source_status, source_profile, plan_quality_status,
                       plan_should_apply, updated_at
                FROM battery_daily_metrics
                WHERE date >= %s AND date <= %s
                ORDER BY date
                """,
                (start_date, end_date_iso),
            )
            battery_daily = _rows_to_dicts(cur.fetchall())

            cur.execute(
                """
                WITH hourly_actuals AS (
                    SELECT substring(ts,1,10) AS date,
                           EXTRACT(HOUR FROM CAST(ts AS timestamp))::integer AS hour,
                           CASE WHEN COUNT(*) = 2 AND COUNT(DISTINCT substring(ts,15,2)) = 2
                                AND COUNT(CASE WHEN load_kwh >= 0 AND load_kwh < 'Infinity'::double precision
                                    AND substring(ts,15,2) IN ('00','30') AND substring(ts,18,2) = '00' THEN 1 END) = 2
                                THEN SUM(load_kwh) END AS actual_load_kwh,
                           CASE WHEN COUNT(*) = 2 AND COUNT(DISTINCT substring(ts,15,2)) = 2
                                AND COUNT(CASE WHEN pv_kwh >= 0 AND pv_kwh < 'Infinity'::double precision
                                    AND substring(ts,15,2) IN ('00','30') AND substring(ts,18,2) = '00' THEN 1 END) = 2
                                THEN SUM(pv_kwh) END AS actual_pv_kwh,
                           MIN(ts) AS first_sample_at,
                           MAX(ts) AS latest_sample_at
                    FROM monitoring_samples
                    WHERE substring(ts,1,10) >= %s AND substring(ts,1,10) <= %s
                    GROUP BY substring(ts,1,10), EXTRACT(HOUR FROM CAST(ts AS timestamp))::integer
                ), hourly_soc AS (
                    SELECT date, hour,
                           MAX(CASE WHEN opening_row_number = 1 THEN soc_percent END) AS opening_soc_percent,
                           MAX(CASE WHEN latest_row_number = 1 THEN soc_percent END) AS actual_soc_percent
                    FROM (
                        SELECT substring(ts,1,10) AS date,
                               EXTRACT(HOUR FROM CAST(ts AS timestamp))::integer AS hour,
                               soc_percent,
                               ROW_NUMBER() OVER (
                                   PARTITION BY substring(ts,1,10), EXTRACT(HOUR FROM CAST(ts AS timestamp))::integer
                                   ORDER BY ts ASC
                               ) AS opening_row_number,
                               ROW_NUMBER() OVER (
                                   PARTITION BY substring(ts,1,10), EXTRACT(HOUR FROM CAST(ts AS timestamp))::integer
                                   ORDER BY ts DESC
                               ) AS latest_row_number
                        FROM monitoring_samples
                        WHERE substring(ts,1,10) >= %s AND substring(ts,1,10) <= %s
                          AND soc_percent IS NOT NULL
                          AND soc_percent >= 0 AND soc_percent <= 100
                    ) ranked
                    GROUP BY date, hour
                ), display_hours AS (
                    SELECT date, hour FROM forecast_hourly WHERE date >= %s AND date <= %s
                    UNION SELECT date, hour FROM hourly_actuals
                )
                SELECT dh.date, dh.hour, fh.forecast_pv_kwh, fh.forecast_load_kwh,
                       fh.forecast_charge_kwh, ah.actual_load_kwh, ah.actual_pv_kwh, hs.actual_soc_percent,
                       hs.opening_soc_percent,
                       ah.first_sample_at, ah.latest_sample_at, fh.source, fh.updated_at
                FROM display_hours dh
                LEFT JOIN forecast_hourly fh ON fh.date = dh.date AND fh.hour = dh.hour
                LEFT JOIN hourly_actuals ah ON ah.date = dh.date AND ah.hour = dh.hour
                LEFT JOIN hourly_soc hs ON hs.date = dh.date AND hs.hour = dh.hour
                WHERE dh.date >= %s AND dh.date <= %s
                ORDER BY dh.date, dh.hour
                """,
                (start_date, end_date_iso, start_date, end_date_iso,
                 start_date, end_date_iso, start_date, end_date_iso),
            )
            forecast_hourly = _rows_to_dicts(cur.fetchall())

            history_start = (start_obj - timedelta(days=14)).isoformat()
            cur.execute(
                """
                SELECT substring(ts,1,10) AS date,
                       CASE WHEN COUNT(CASE WHEN pv_kwh >= 0 AND pv_kwh < 'Infinity'::double precision THEN 1 END) = COUNT(*)
                            THEN SUM(pv_kwh) END AS actual_pv_kwh,
                       CASE WHEN COUNT(CASE WHEN load_kwh >= 0 AND load_kwh < 'Infinity'::double precision THEN 1 END) = COUNT(*)
                            THEN SUM(load_kwh) END AS actual_load_kwh
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
                       CASE WHEN COUNT(CASE WHEN charge_kwh >= 0 AND charge_kwh < 'Infinity'::double precision THEN 1 END) = COUNT(*)
                            THEN SUM(charge_kwh) END AS charge_kwh,
                       CASE WHEN COUNT(CASE WHEN discharge_kwh >= 0 AND discharge_kwh < 'Infinity'::double precision THEN 1 END) = COUNT(*)
                            THEN SUM(discharge_kwh) END AS discharge_kwh
                FROM monitoring_samples
                WHERE substring(ts,1,10) >= %s AND substring(ts,1,10) <= %s
                GROUP BY substring(ts,1,10)
                ORDER BY date
                """,
                (start_date, end_date_iso),
            )
            battery_flow_daily = _rows_to_dicts(cur.fetchall())

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
                    SELECT event_id, run_id, slot, profile, status, detail_json, source_doc_id, recorded_at
                    FROM settings_events
                    ORDER BY recorded_at DESC, event_id DESC
                    LIMIT 40
                    """
                )
                latest_events = _rows_to_dicts(cur.fetchall())

                cur.execute(
                    """
                    SELECT *
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
class PostgresDashboardRepository:
    def load_dashboard(self, request: DashboardLoadRequest) -> DashboardSlice:
        return load_postgres_slice(
            end_date=request.end_date,
            window_days=request.window_days,
            include_static=request.include_static,
        )



