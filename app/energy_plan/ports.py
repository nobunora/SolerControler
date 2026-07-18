from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from app.energy_model import EnergyModelCoefficients
from app.occupancy_schedule import OccupancyScheduleEvent
from app.energy_plan.weather import WeatherHistoryFetchResult


class HistoricalInputPort(Protocol):
    def locate_csv_paths(self, artifacts_dir: Path) -> list[Path]: ...

    def read_rows(self, csv_paths: list[Path]) -> list[dict[str, Any]]: ...

    def fit_coefficients(self, csv_paths: list[Path]) -> EnergyModelCoefficients: ...

    def build_historical_profile(self, rows: list[dict[str, Any]]) -> dict[str, float]: ...

    def load_occupancy_events(self) -> list[OccupancyScheduleEvent]: ...


class ForecastInputPort(Protocol):
    def load_forecast(self, *, latitude: float, longitude: float, timezone: str) -> dict[str, object]: ...


class WeatherHistoryPort(Protocol):
    def load_history(
        self,
        rows: list[dict[str, Any]],
        *,
        latitude: float,
        longitude: float,
        timezone: str,
    ) -> WeatherHistoryFetchResult: ...
