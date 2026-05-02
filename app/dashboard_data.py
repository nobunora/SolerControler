from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DashboardData:
    sunshine_daily: list[dict[str, Any]]
    cost_daily: list[dict[str, Any]]
    cost_monthly: list[dict[str, Any]]
    battery_daily: list[dict[str, Any]]
    model_parameters: list[dict[str, Any]]


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


def _get_global_bounds_sqlite(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    candidates: list[str | None] = []
    for table in ("sunshine_daily", "cost_daily", "battery_daily_metrics"):
        row = conn.execute(f"SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM {table}").fetchone()
        candidates.extend([row["min_date"], row["max_date"]])
    return _pick_min_max_dates(candidates)


def _get_global_bounds_postgres(cur) -> tuple[str | None, str | None]:
    candidates: list[str | None] = []
    for table in ("sunshine_daily", "cost_daily", "battery_daily_metrics"):
        cur.execute(f"SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM {table}")
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
) -> dict[str, Any]:
    all_dates: list[str] = []
    all_dates.extend([str(x.get("date", "")) for x in sunshine_daily if x.get("date")])
    all_dates.extend([str(x.get("date", "")) for x in cost_daily if x.get("date")])
    all_dates.extend([str(x.get("date", "")) for x in battery_daily if x.get("date")])
    oldest_loaded = min(all_dates) if all_dates else None
    newest_loaded = max(all_dates) if all_dates else None
    has_more_before = False
    if global_oldest_date and oldest_loaded:
        has_more_before = global_oldest_date < oldest_loaded
    return {
        "window_days": window_days,
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
    if not db_path.exists():
        return DashboardSlice(
            data=DashboardData([], [], [], [], []),
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
                data=DashboardData([], [], [], [], []),
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
                data=DashboardData([], [], [], [], []),
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

        sunshine = _rows_to_dicts(
            conn.execute(
                """
                SELECT date, forecast_hours, actual_hours, forecast_temp_c, actual_temp_c
                FROM sunshine_daily
                WHERE date >= ? AND date <= ?
                ORDER BY date
                """,
                (start_date, end_date_iso),
            ).fetchall()
        )
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
        battery_daily = _rows_to_dicts(
            conn.execute(
                """
                SELECT date, setting_soc_target_percent, night_charge_kwh, pv_max_charge_kwh, end_of_day_soc_percent
                FROM battery_daily_metrics
                WHERE date >= ? AND date <= ?
                ORDER BY date
                """,
                (start_date, end_date_iso),
            ).fetchall()
        )

        cost_monthly: list[dict[str, Any]] = []
        params: list[dict[str, Any]] = []
        if include_static:
            cost_monthly = _rows_to_dicts(
                conn.execute(
                    """
                    SELECT substr(date,1,7) AS month,
                           SUM(self_consumption_kwh) AS self_consumption_kwh,
                           SUM(savings_yen) AS savings_yen
                    FROM cost_daily
                    GROUP BY substr(date,1,7)
                    ORDER BY month
                    """
                ).fetchall()
            )
            params = _rows_to_dicts(
                conn.execute(
                    """
                    SELECT name, mean_value, variance, sample_count, hit_rate
                    FROM model_parameters
                    ORDER BY name
                    """
                ).fetchall()
            )

        meta = _meta_from_data(
            window_days=window_days,
            global_oldest_date=global_oldest,
            global_newest_date=global_newest,
            sunshine_daily=sunshine,
            cost_daily=cost_daily,
            battery_daily=battery_daily,
        )
        return DashboardSlice(
            data=DashboardData(
                sunshine_daily=sunshine,
                cost_daily=cost_daily,
                cost_monthly=cost_monthly,
                battery_daily=battery_daily,
                model_parameters=params,
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
                data=DashboardData([], [], [], [], []),
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
                    data=DashboardData([], [], [], [], []),
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
                    data=DashboardData([], [], [], [], []),
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
                SELECT date, forecast_hours, actual_hours, forecast_temp_c, actual_temp_c
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
                SELECT date, setting_soc_target_percent, night_charge_kwh, pv_max_charge_kwh, end_of_day_soc_percent
                FROM battery_daily_metrics
                WHERE date >= %s AND date <= %s
                ORDER BY date
                """,
                (start_date, end_date_iso),
            )
            battery_daily = _rows_to_dicts(cur.fetchall())

            cost_monthly: list[dict[str, Any]] = []
            params: list[dict[str, Any]] = []
            if include_static:
                cur.execute(
                    """
                    SELECT substring(date,1,7) AS month,
                           SUM(self_consumption_kwh) AS self_consumption_kwh,
                           SUM(savings_yen) AS savings_yen
                    FROM cost_daily
                    GROUP BY substring(date,1,7)
                    ORDER BY month
                    """
                )
                cost_monthly = _rows_to_dicts(cur.fetchall())

                cur.execute(
                    """
                    SELECT name, mean_value, variance, sample_count, hit_rate
                    FROM model_parameters
                    ORDER BY name
                    """
                )
                params = _rows_to_dicts(cur.fetchall())

            meta = _meta_from_data(
                window_days=window_days,
                global_oldest_date=global_oldest,
                global_newest_date=global_newest,
                sunshine_daily=sunshine,
                cost_daily=cost_daily,
                battery_daily=battery_daily,
            )
            return DashboardSlice(
                data=DashboardData(
                    sunshine_daily=sunshine,
                    cost_daily=cost_daily,
                    cost_monthly=cost_monthly,
                    battery_daily=battery_daily,
                    model_parameters=params,
                ),
                meta=meta,
            )
    finally:
        conn.close()


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
    return _load_sqlite_slice(db_path, end_date=end_date, window_days=days, include_static=include_static)


def load_dashboard_data(db_path: Path) -> DashboardData:
    # Backward compatible full load API.
    return load_dashboard_slice(db_path, end_date=None, window_days=365, include_static=True).data
