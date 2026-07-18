from __future__ import annotations

from pathlib import Path

import pytest

from app.energy_plan import ForecastSettings, HistoricalInputSettings


def test_energy_plan_focused_settings_preserve_defaults(monkeypatch) -> None:
    for key in (
        "FORECAST_LATITUDE",
        "FORECAST_LONGITUDE",
        "TIMEZONE",
        "ARTIFACTS_DIR",
        "CONSUMPTION_MODEL_MIN_TRAINING_DAYS",
        "CONSUMPTION_MODEL_FALLBACK_WINDOW_DAYS",
    ):
        monkeypatch.delenv(key, raising=False)

    assert ForecastSettings.from_env() == ForecastSettings(35.67452, 139.48216, "Asia/Tokyo")
    assert HistoricalInputSettings.from_env() == HistoricalInputSettings(Path("artifacts"), 45, 14)


def test_forecast_settings_preserve_invalid_numeric_failure(monkeypatch) -> None:
    monkeypatch.setenv("FORECAST_LATITUDE", "invalid")

    with pytest.raises(ValueError):
        ForecastSettings.from_env()
