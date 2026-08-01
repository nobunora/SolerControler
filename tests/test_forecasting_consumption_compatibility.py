from __future__ import annotations

import app.consumption_forecast as legacy
import app.forecasting.consumption as canonical


def test_legacy_consumption_forecast_exports_canonical_objects() -> None:
    for name in (
        "ConsumptionForecast", "ConsumptionForecaster", "DailyWeatherFeatures",
        "LoadObservation", "forecast_daily_consumption",
    ):
        assert getattr(legacy, name) is getattr(canonical, name)
