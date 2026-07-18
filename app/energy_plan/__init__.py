"""Typed orchestration boundary for energy plan generation."""

from app.energy_plan.models import PlanDocumentV1
from app.energy_plan.output import EnergyPlanOutput
from app.energy_plan.ports import ForecastInputPort, HistoricalInputPort, WeatherHistoryPort
from app.energy_plan.weather import WeatherHistoryFetchResult
from app.energy_plan.settings import ForecastSettings, HistoricalInputSettings
from app.energy_plan.historical import build_historical_profile
from app.energy_plan.forecast import coerce_hourly_energy, estimate_sunset_hour, summarize_hourly_pv

__all__ = [
    "PlanDocumentV1",
    "EnergyPlanOutput",
    "ForecastInputPort",
    "HistoricalInputPort",
    "WeatherHistoryFetchResult",
    "WeatherHistoryPort",
    "ForecastSettings",
    "HistoricalInputSettings",
    "build_historical_profile",
    "coerce_hourly_energy",
    "estimate_sunset_hour",
    "summarize_hourly_pv",
]
