"""Compatibility exports for dashboard data loading.

New code should import from :mod:`app.dashboard.data`.
"""

from app.dashboard.data import (
    FirestoreDashboardRepository,
    PostgresDashboardRepository, SQLiteDashboardRepository, clear_dashboard_cache,
    load_dashboard_data, load_dashboard_slice,
)
from app.dashboard.models import DashboardData, DashboardRawData, DashboardSlice

__all__ = [
    "DashboardData", "DashboardRawData", "DashboardSlice", "FirestoreDashboardRepository",
    "PostgresDashboardRepository", "SQLiteDashboardRepository", "clear_dashboard_cache",
    "load_dashboard_data", "load_dashboard_slice",
]
