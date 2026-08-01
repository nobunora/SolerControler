"""Compatibility exports for the consumption forecast model.

New code should import from :mod:`app.forecasting.consumption`.
"""

from app.forecasting.consumption import (
    ConsumptionForecast,
    ConsumptionForecaster,
    DailyWeatherFeatures,
    LoadObservation,
    forecast_daily_consumption,
)

__all__ = [
    "ConsumptionForecast",
    "ConsumptionForecaster",
    "DailyWeatherFeatures",
    "LoadObservation",
    "forecast_daily_consumption",
]
