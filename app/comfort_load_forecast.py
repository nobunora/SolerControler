"""Compatibility exports for the comfort-load forecast model.

New code should import from :mod:`app.forecasting.comfort_load`.
"""

from app.forecasting.comfort_load import (
    ADAPTIVE_LOOKBACK_HOURS,
    FEATURE_NAMES,
    MODEL_NAME,
    THERMAL_HALF_LIVES,
    build_comfort_feature_map,
    predict_hourly_comfort_load,
)

__all__ = [
    "ADAPTIVE_LOOKBACK_HOURS", "FEATURE_NAMES", "MODEL_NAME", "THERMAL_HALF_LIVES",
    "build_comfort_feature_map", "predict_hourly_comfort_load",
]
