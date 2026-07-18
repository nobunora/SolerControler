from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.dashboard.models import DashboardSlice


@dataclass(frozen=True)
class DashboardLoadRequest:
    end_date: str | None
    window_days: int
    include_static: bool


class DashboardRepository(Protocol):
    def load_dashboard(self, request: DashboardLoadRequest) -> DashboardSlice: ...
