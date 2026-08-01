"""Compatibility exports for forecast correction.

New code should import from :mod:`app.forecasting.correction`.
"""

from app.forecasting.correction import (
    ForecastCorrectionInput,
    ForecastCorrectionPolicy,
    add_thermal_states,
    build_forecast_correction,
    fetch_hourly_weather,
    load_forecast_hourly_history_from_firestore,
)

__all__ = [
    "ForecastCorrectionInput", "ForecastCorrectionPolicy", "add_thermal_states",
    "build_forecast_correction", "fetch_hourly_weather",
    "load_forecast_hourly_history_from_firestore",
]
