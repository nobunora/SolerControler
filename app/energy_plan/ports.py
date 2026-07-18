from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from app.energy_model import EnergyModelCoefficients
from app.occupancy_schedule import OccupancyScheduleEvent


class HistoricalInputPort(Protocol):
    def locate_csv_paths(self, artifacts_dir: Path) -> list[Path]: ...

    def read_rows(self, csv_paths: list[Path]) -> list[dict[str, Any]]: ...

    def fit_coefficients(self, csv_paths: list[Path]) -> EnergyModelCoefficients: ...

    def build_historical_profile(self, rows: list[dict[str, Any]]) -> dict[str, float]: ...

    def load_occupancy_events(self) -> list[OccupancyScheduleEvent]: ...


class ForecastInputPort(Protocol):
    def load_forecast(self, *, latitude: float, longitude: float, timezone: str) -> dict[str, object]: ...
