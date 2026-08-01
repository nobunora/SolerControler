"""Compatibility exports for the PV-array forecast model.

New code should import from :mod:`app.forecasting.pv_array`.
"""

from app.forecasting.pv_array import (
    PVArrayConfig,
    PvCalibrationInput,
    PvCalibrationPolicy,
    build_pv_array_forecast,
    calibrate_performance_ratio,
    calibrate_performance_ratio_for,
    fetch_open_meteo_hourly,
    forecast_pv_arrays,
    forecast_pv_arrays_forecast_solar,
    load_pv_array_configs,
)

__all__ = [
    "PVArrayConfig", "PvCalibrationInput", "PvCalibrationPolicy", "build_pv_array_forecast",
    "calibrate_performance_ratio", "calibrate_performance_ratio_for", "fetch_open_meteo_hourly",
    "forecast_pv_arrays", "forecast_pv_arrays_forecast_solar", "load_pv_array_configs",
]
