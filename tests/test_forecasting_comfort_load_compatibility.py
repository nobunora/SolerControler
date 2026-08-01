from __future__ import annotations

import app.comfort_load_forecast as legacy
import app.forecasting.comfort_load as canonical


def test_legacy_comfort_load_exports_canonical_objects() -> None:
    for name in (
        "ADAPTIVE_LOOKBACK_HOURS", "FEATURE_NAMES", "MODEL_NAME", "THERMAL_HALF_LIVES",
        "build_comfort_feature_map", "predict_hourly_comfort_load",
    ):
        assert getattr(legacy, name) is getattr(canonical, name)
