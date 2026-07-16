"""Dashboard domain models and backend-neutral assembly."""

from app.dashboard.models import DashboardData, DashboardRawData, DashboardSlice
from app.dashboard.service import assemble_dashboard_slice

__all__ = ["DashboardData", "DashboardRawData", "DashboardSlice", "assemble_dashboard_slice"]
