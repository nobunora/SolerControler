"""Compatibility repository objects for dashboard backend selection."""

from __future__ import annotations

from dataclasses import dataclass

from app.dashboard.models import DashboardSlice
from app.dashboard.repositories import DashboardLoadRequest


@dataclass(frozen=True)
class PostgresDashboardRepository:
    def load_dashboard(self, request: DashboardLoadRequest) -> DashboardSlice:
        from app.dashboard.data import _load_postgres_slice
        return _load_postgres_slice(end_date=request.end_date, window_days=request.window_days, include_static=request.include_static)


@dataclass(frozen=True)
class FirestoreDashboardRepository:
    def load_dashboard(self, request: DashboardLoadRequest) -> DashboardSlice:
        from app.dashboard.data import _load_firestore_slice
        return _load_firestore_slice(end_date=request.end_date, window_days=request.window_days, include_static=request.include_static)
