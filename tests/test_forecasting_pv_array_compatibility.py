from __future__ import annotations

import app.forecasting.pv_array as canonical
import app.pv_array_forecast as legacy


def test_legacy_pv_array_forecast_exports_canonical_objects() -> None:
    for name in (
        "PVArrayConfig", "PvCalibrationInput", "PvCalibrationPolicy", "build_pv_array_forecast",
        "calibrate_performance_ratio", "calibrate_performance_ratio_for", "fetch_open_meteo_hourly",
        "forecast_pv_arrays", "forecast_pv_arrays_forecast_solar", "load_pv_array_configs",
    ):
        assert getattr(legacy, name) is getattr(canonical, name)
