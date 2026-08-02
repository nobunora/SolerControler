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
from app.dashboard.service import merge_forecast_hourly_actuals
from app.dashboard.slice_assembler import (
    build_dashboard_slice as _build_dashboard_slice,
    build_slice_from_query_snapshot as _assemble_query_snapshot,
    empty_dashboard_slice as _empty_dashboard_slice,
    extract_pv_forecast_diagnostics as _extract_pv_forecast_diagnostics,
    read_latest_pv_forecast_diagnostics as _read_latest_pv_forecast_diagnostics,
)
from app.dashboard.sqlite_repository import SQLiteDashboardRepository, load_sqlite_query_snapshot
from app.dashboard.backend_repositories import FirestoreDashboardRepository, PostgresDashboardRepository
from app.domain.tariff import tiered_day_cost
from app.parsing.numbers import to_float
from app.dashboard.aggregation import (
    _accounting_month_label,
    _accounting_period_bounds,
    _aggregation_close_day,
    _build_cost_monthly,
    _date_add_iso,
    _forecast_pv_kwh,
    _rolling_load_forecast,
    _today_jst_iso,
    _build_energy_daily,
)


_FIRESTORE_CLIENTS: dict[tuple[str | None, str], Any] = {}
_FIRESTORE_SLICE_CACHE: dict[
    tuple[str | None, str, str | None, int, bool], tuple[float, "DashboardSlice"]
] = {}
_FIRESTORE_DASHBOARD_CACHE_SECONDS = 120.0

__all__ = [
    "DashboardData",
    "DashboardSlice",
    "FirestoreDashboardRepository",
    "PostgresDashboardRepository",
    "SQLiteDashboardRepository",
    "clear_dashboard_cache",
    "load_dashboard_data",
    "load_dashboard_slice",
    "_accounting_month_label",
    "_accounting_period_bounds",
    "_aggregation_close_day",
    "_build_cost_monthly",
    "_build_energy_daily",
    "_date_add_iso",
    "_rolling_load_forecast",
    "_today_jst_iso",
]


def _build_slice_from_query_snapshot(
    snapshot: DashboardQuerySnapshot,
    *,
    window_days: int,
    include_static: bool,
    pv_forecast_diagnostics: dict[str, Any] | None = None,
    today_jst_iso: str | None = None,
) -> DashboardSlice:
    """Compatibility boundary for tests and callers that patch data-module ports."""
    diagnostics = pv_forecast_diagnostics
    if diagnostics is None:
        diagnostics = _read_latest_pv_forecast_diagnostics() if include_static else {}
    return _assemble_query_snapshot(
        snapshot,
        window_days=window_days,
        include_static=include_static,
        pv_forecast_diagnostics=diagnostics,
        today_jst_iso=today_jst_iso or _today_jst_iso(),
    )


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


def _read_latest_pv_forecast_diagnostics_from_firestore(client: Any) -> dict[str, Any]:
    try:
        snap = client.collection("night_charge_plans").document("latest").get()
    except Exception:
        return {}
    if not snap.exists:
        return {}
    row = snap.to_dict() or {}
    return _extract_pv_forecast_diagnostics(row)


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


def _load_sqlite_slice(
    db_path: Path, *, end_date: str | None, window_days: int, include_static: bool
) -> DashboardSlice:
    request = DashboardLoadRequest(end_date=end_date, window_days=window_days, include_static=include_static)
    return _build_slice_from_query_snapshot(
        load_sqlite_query_snapshot(db_path, request),
        window_days=window_days,
        include_static=include_static,
        today_jst_iso=_today_jst_iso(),
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
    return _load_sqlite_slice(db_path, end_date=end_date, window_days=days, include_static=include_static)


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
