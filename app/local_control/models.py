from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class ForecastResult:
    hours_12h: float
    captured_at: datetime
    source_text: str


@dataclass(frozen=True)
class MonitoringMetrics:
    row_count: int
    latest_soc: Optional[float]
    avg_soc: Optional[float]
    total_charge: float
    total_discharge: float


@dataclass(frozen=True)
class DesiredBatterySetting:
    charge_limit_percent: int
    mode: str
    reason: str


@dataclass(frozen=True)
class ApplyResult:
    changed: bool
    previous_charge_limit_text: Optional[str]
