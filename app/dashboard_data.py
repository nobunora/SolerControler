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


def _load_firestore_slice(
    *,
    end_date: str | None,
    window_days: int,
    include_static: bool,
) -> DashboardSlice:
    from google.cloud import firestore

    project_id = os.getenv("FIRESTORE_PROJECT_ID", "").strip() or None
    database_id = os.getenv("FIRESTORE_DATABASE_ID", "").strip() or "(default)"
    client = firestore.Client(project=project_id, database=database_id) if project_id else firestore.Client(database=database_id)

    b1 = _firestore_bounds(client, "sunshine_daily")
    b2 = _firestore_bounds(client, "cost_daily")
    b3 = _firestore_bounds(client, "battery_daily_metrics")
    global_oldest, global_newest = _pick_min_max_dates([b1[0], b1[1], b2[0], b2[1], b3[0], b3[1]])
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

    sunshine = _firestore_rows_between(
        client,
        collection_name="sunshine_daily",
        start_date=start_date,
        end_date_iso=end_date_iso,
        fields=["forecast_hours", "actual_hours", "forecast_temp_c", "actual_temp_c"],
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
        fields=["setting_soc_target_percent", "night_charge_kwh", "pv_max_charge_kwh", "end_of_day_soc_percent"],
    )

    cost_monthly: list[dict[str, Any]] = []
    params: list[dict[str, Any]] = []
    if include_static:
        month_map: dict[str, dict[str, float]] = {}
        for doc in client.collection("cost_daily").order_by("date").stream():
            row = doc.to_dict() or {}
            d = str(row.get("date", doc.id))
            month = d[:7]
            acc = month_map.setdefault(month, {"self_consumption_kwh": 0.0, "savings_yen": 0.0})
            acc["self_consumption_kwh"] += float(row.get("self_consumption_kwh") or 0.0)
            acc["savings_yen"] += float(row.get("savings_yen") or 0.0)
        cost_monthly = [
            {"month": m, "self_consumption_kwh": v["self_consumption_kwh"], "savings_yen": v["savings_yen"]}
            for m, v in sorted(month_map.items())
        ]
        for doc in client.collection("model_parameters").order_by("name").stream():
            row = doc.to_dict() or {}
            params.append(
                {
                    "name": row.get("name", doc.id),
                    "mean_value": row.get("mean_value"),
                    "variance": row.get("variance"),
                    "sample_count": row.get("sample_count"),
                    "hit_rate": row.get("hit_rate"),
                }
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
