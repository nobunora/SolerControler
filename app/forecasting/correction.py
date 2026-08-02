from __future__ import annotations

"""Forecast correction layer for the night-charge planner.

This module keeps the tunable, data-driven forecast adjustments out of
energy_model_main.py. The stable contract is simple: take raw hourly PV/load
forecasts and return corrected hourly forecasts plus a human-readable rationale
that can be persisted for later validation.
"""

import os
from dataclasses import dataclass
from typing import Any

import requests

from app.forecasting.comfort_load import predict_hourly_comfort_load
from app.forecasting.correction_calculations import actual_hourly_totals_by_day as _actual_hourly_totals_by_day
from app.forecasting.correction_history_io import (
    _load_forecast_hourly_history,
    load_forecast_hourly_history_from_firestore,
)
from app.forecasting.correction_weather import fetch_hourly_weather as _fetch_hourly_weather
from app.configuration.environment import env_bool, env_float, env_float_clamped
from app.forecasting.correction_model import (
    add_thermal_states,
    calculate_forecast_correction,
    _correct_hourly_pv,
    _evening_temperature_correction,
    _paired_forecast_error_scenarios,
    _physical_vector_residual_correction,
    _target_weather_from_forecast as _model_target_weather_from_forecast,
    _temperature_features_for_day,
    _temperature_hourly_multipliers,
    _temperature_training_samples,
)


def fetch_hourly_weather(**kwargs: Any) -> dict[str, dict[int, dict[str, float]]]:
    """Preserve the correction module's HTTP monkeypatch boundary."""
    return _fetch_hourly_weather(**kwargs, http_get=requests.get)


def _target_weather_from_forecast(
    forecast: dict[str, object],
    *,
    target_date: str,
    latitude: float,
    longitude: float,
    timezone: str,
) -> dict[int, dict[str, float]]:
    """Preserve the correction module's weather monkeypatch boundary."""
    return _model_target_weather_from_forecast(
        forecast,
        target_date=target_date,
        latitude=latitude,
        longitude=longitude,
        timezone=timezone,
        weather_fetch=fetch_hourly_weather,
    )

__all__ = [
    "ForecastCorrectionInput",
    "ForecastCorrectionPolicy",
    "add_thermal_states",
    "build_forecast_correction",
    "fetch_hourly_weather",
    "load_forecast_hourly_history_from_firestore",
]


@dataclass(frozen=True)
class ForecastCorrectionInput:
    rows: list[dict[str, Any]]
    hourly_load_forecast: dict[int, float]
    hourly_pv_forecast: dict[int, float]
    target_date: str
    latitude: float
    longitude: float
    timezone: str
    forecast: dict[str, object]


@dataclass(frozen=True)
class ForecastCorrectionPolicy:
    enabled: bool
    skip_pv_correction: bool
    allow_load_safety_floor: bool
    pv_ewma_alpha: float
    pv_ratio_min: float
    pv_ratio_max: float
    load_ewma_alpha: float
    load_ratio_min: float
    load_ratio_max: float

    @classmethod
    def from_env(
        cls,
        *,
        skip_pv_correction: bool = False,
        allow_load_safety_floor: bool = True,
    ) -> "ForecastCorrectionPolicy":
        pv_min = max(0.0, env_float("PV_RATIO_EWMA_MIN", default=0.9))
        load_min = max(0.0, env_float("LOAD_RATIO_EWMA_MIN", default=0.7))
        return cls(
            enabled=env_bool("FORECAST_CORRECTION_ENABLED", default=True),
            skip_pv_correction=skip_pv_correction,
            allow_load_safety_floor=allow_load_safety_floor,
            pv_ewma_alpha=env_float_clamped("PV_RATIO_EWMA_ALPHA", 0.2, min_val=0.0, max_val=1.0),
            pv_ratio_min=pv_min,
            pv_ratio_max=max(pv_min, env_float("PV_RATIO_EWMA_MAX", default=1.35)),
            load_ewma_alpha=env_float_clamped(
                "LOAD_RATIO_EWMA_ALPHA", 0.5, min_val=0.0, max_val=1.0
            ),
            load_ratio_min=load_min,
            load_ratio_max=max(load_min, env_float("LOAD_RATIO_EWMA_MAX", default=1.8)),
        )


def build_forecast_correction(
    correction_input: ForecastCorrectionInput,
    policy: ForecastCorrectionPolicy,
) -> dict[str, object]:
    """Coordinate correction ports while the model module owns calculations."""
    if not policy.enabled:
        return {
            "enabled": False,
            "hourly_load_kwh": correction_input.hourly_load_forecast,
            "hourly_pv_kwh": correction_input.hourly_pv_forecast,
            "rationale": {"enabled": False, "reason": "disabled"},
        }
    return calculate_forecast_correction(
        rows=correction_input.rows,
        hourly_load_forecast=correction_input.hourly_load_forecast,
        hourly_pv_forecast=correction_input.hourly_pv_forecast,
        target_date=correction_input.target_date,
        latitude=correction_input.latitude,
        longitude=correction_input.longitude,
        timezone=correction_input.timezone,
        forecast=correction_input.forecast,
        skip_pv_correction=policy.skip_pv_correction,
        pv_ewma_alpha=policy.pv_ewma_alpha,
        pv_ratio_min=policy.pv_ratio_min,
        pv_ratio_max=policy.pv_ratio_max,
        load_ewma_alpha=policy.load_ewma_alpha,
        load_ratio_min=policy.load_ratio_min,
        load_ratio_max=policy.load_ratio_max,
        weather_fetch=fetch_hourly_weather,
        history_loader=_load_forecast_hourly_history,
        temperature_corrector=_evening_temperature_correction,
        physical_corrector=_physical_vector_residual_correction,
        comfort_predictor=predict_hourly_comfort_load,
    )


def _build_forecast_correction(
    *,
    rows: list[dict[str, Any]],
    hourly_load_forecast: dict[int, float],
    hourly_pv_forecast: dict[int, float],
    target_date: str,
    lat: float,
    lon: float,
    timezone: str,
    forecast: dict[str, object],
    skip_pv_correction: bool = False,
    allow_load_safety_floor: bool = True,
) -> dict[str, object]:
    """Backward-compatible adapter for callers that still pass individual values."""
    return build_forecast_correction(
        ForecastCorrectionInput(
            rows=rows,
            hourly_load_forecast=hourly_load_forecast,
            hourly_pv_forecast=hourly_pv_forecast,
            target_date=target_date,
            latitude=lat,
            longitude=lon,
            timezone=timezone,
            forecast=forecast,
        ),
        ForecastCorrectionPolicy.from_env(
            skip_pv_correction=skip_pv_correction,
            allow_load_safety_floor=allow_load_safety_floor,
        ),
    )
