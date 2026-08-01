from __future__ import annotations

import app.forecast_correction as legacy
import app.forecasting.correction as canonical


def test_legacy_correction_exports_canonical_objects() -> None:
    for name in (
        "ForecastCorrectionInput", "ForecastCorrectionPolicy", "add_thermal_states",
        "build_forecast_correction", "fetch_hourly_weather",
        "load_forecast_hourly_history_from_firestore",
    ):
        assert getattr(legacy, name) is getattr(canonical, name)
