from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from app.dashboard.models import DashboardData, DashboardRawData as DashboardRawData, DashboardSlice
from app.dashboard.repositories import DashboardLoadRequest, DashboardQuerySnapshot
from app.dashboard.slice_assembler import (
    build_dashboard_slice as _build_dashboard_slice,  # noqa: F401
    build_slice_from_query_snapshot as _assemble_query_snapshot,
    empty_dashboard_slice as _empty_dashboard_slice,  # noqa: F401
    merge_latest_plan_into_schedule as _merge_latest_plan_into_schedule,  # noqa: F401
    read_latest_pv_forecast_diagnostics as _read_latest_pv_forecast_diagnostics,
)
from app.dashboard.sqlite_repository import SQLiteDashboardRepository, load_sqlite_query_snapshot
from app.dashboard.postgres_repository import PostgresDashboardRepository
from app.dashboard.firestore_repository import FirestoreDashboardRepository
from app.dashboard.history_reconstruction import (
    firestore_forecast_hourly_with_reconstruction,
    get_global_bounds_firestore_with_reconstruction,
)
from app.dashboard.aggregation import (
    _accounting_month_label,
    _accounting_period_bounds,
    _aggregation_close_day,
    _build_cost_monthly,
    _date_add_iso,
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
        bounds_reader=get_global_bounds_firestore_with_reconstruction,
        rows_reader=_firestore_rows_between,
        monitoring_reader=_firestore_monitoring_daily,
        hourly_reader=firestore_forecast_hourly_with_reconstruction,
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
        project_id, database_id = _dashboard_firestore_config()
        key = (project_id, database_id, end_date, days, include_static)
        cached = _FIRESTORE_SLICE_CACHE.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < _FIRESTORE_DASHBOARD_CACHE_SECONDS:
            return cached[1]
        sliced = _load_firestore_slice(end_date=end_date, window_days=days, include_static=include_static)
        _FIRESTORE_SLICE_CACHE[key] = (time.monotonic(), sliced)
        return sliced
    return _load_sqlite_slice(db_path, end_date=end_date, window_days=days, include_static=include_static)


def load_dashboard_data(db_path: Path) -> DashboardData:
    # Backward compatible full load API.
    return load_dashboard_slice(db_path, end_date=None, window_days=365, include_static=True).data


# Keep the historical private test/import boundary while Firestore ownership lives in its adapter.
from app.dashboard.firestore_repository import (
    _billing_usage_summary as _billing_usage_summary,
    _dashboard_firestore_config,
    _daily_metric_is_complete as _daily_metric_is_complete,
    _firestore_bounds,
    _firestore_forecast_hourly_between,
    _firestore_monitoring_daily,
    _firestore_rows_between,
    _get_global_bounds_firestore as _repository_global_bounds_firestore,
    _open_dashboard_firestore_client,
    _review_candidate_dates as _review_candidate_dates,
)


def _get_global_bounds_firestore(client: Any) -> tuple[str | None, str | None]:
    """Compatibility port that keeps legacy test injection at the data boundary."""
    return _repository_global_bounds_firestore(client, bounds_reader=_firestore_bounds)


def clear_dashboard_cache() -> None:
    _FIRESTORE_SLICE_CACHE.clear()
    from app.dashboard.firestore_repository import clear_dashboard_cache as clear_firestore_cache

    clear_firestore_cache()
