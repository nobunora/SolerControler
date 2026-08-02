from __future__ import annotations

"""Forecast correction layer for the night-charge planner.

This module keeps the tunable, data-driven forecast adjustments out of
energy_model_main.py. The stable contract is simple: take raw hourly PV/load
forecasts and return corrected hourly forecasts plus a human-readable rationale
that can be persisted for later validation.
"""

import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from app.forecasting.comfort_load import ADAPTIVE_LOOKBACK_HOURS, predict_hourly_comfort_load
from app.forecasting.correction_calculations import (
    actual_hourly_totals_by_day as _actual_hourly_totals_by_day,
    clip_float as _clip_float,
    coerce_hourly_values as _coerce_hourly_values,
    daily_pairs_for_ratio as _daily_pairs_for_ratio,
    ewma_ratio_from_daily_pairs as _ewma_ratio_from_daily_pairs,
    weather_class as _weather_class,
)
from app.forecasting.correction_history_io import (
    _forecast_history_start_date,
    _load_forecast_hourly_history,
    _load_forecast_hourly_history_from_sqlite,
    add_thermal_states,
    fetch_hourly_weather,
    load_forecast_hourly_history_from_firestore,
    _moist_air_enthalpy,
)
from app.configuration.environment import env_bool, env_float, env_float_clamped
from app.parsing.numbers import to_float, to_int
from app.forecasting.correction_model import (
    _adaptive_load_scenarios,
    _correct_hourly_pv,
    _evening_temperature_correction,
    _paired_forecast_error_scenarios,
    _physical_vector_residual_correction,
    _target_weather_from_forecast as _model_target_weather_from_forecast,
    _temperature_features_for_day,
    _temperature_hourly_multipliers,
    _temperature_training_samples,
    _temperature_correction_hours,
)
from app.forecasting.correction_result import assemble_forecast_correction_result


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
    rows = correction_input.rows
    hourly_load_forecast = correction_input.hourly_load_forecast
    hourly_pv_forecast = correction_input.hourly_pv_forecast
    target_date = correction_input.target_date
    forecast = correction_input.forecast
    skip_pv_correction = policy.skip_pv_correction
    if not policy.enabled:
        return {
            "enabled": False,
            "hourly_load_kwh": hourly_load_forecast,
            "hourly_pv_kwh": hourly_pv_forecast,
            "rationale": {"enabled": False, "reason": "disabled"},
        }

    forecast_history, history_source = _load_forecast_hourly_history(target_date=target_date)
    actual_history = _actual_hourly_totals_by_day(rows, target_date=target_date)
    pv_alpha = policy.pv_ewma_alpha
    pv_min = policy.pv_ratio_min
    pv_max = policy.pv_ratio_max
    load_alpha = policy.load_ewma_alpha
    load_min = policy.load_ratio_min
    load_max = policy.load_ratio_max

    pv_summary = _ewma_ratio_from_daily_pairs(
        _daily_pairs_for_ratio(forecast_history=forecast_history, actual_history=actual_history, key="pv"),
        alpha=pv_alpha,
    )
    load_summary = _ewma_ratio_from_daily_pairs(
        _daily_pairs_for_ratio(forecast_history=forecast_history, actual_history=actual_history, key="load"),
        alpha=load_alpha,
    )
    pv_ratio_raw = to_float(pv_summary.get("raw_ratio")) or 1.0
    load_ratio_raw = to_float(load_summary.get("raw_ratio")) or 1.0
    pv_ratio = _clip_float(pv_ratio_raw, min_val=pv_min, max_val=pv_max)
    load_ratio = _clip_float(load_ratio_raw, min_val=load_min, max_val=load_max)

    history_dates = sorted(set(forecast_history) & set(actual_history))
    historical_temperature_features: dict[str, dict[str, float | None]] = {}
    all_weather: dict[str, dict[int, dict[str, float]]] = {}
    if history_dates:
        history_weather_start = (
            datetime.fromisoformat(history_dates[0]) - timedelta(hours=ADAPTIVE_LOOKBACK_HOURS)
        ).date().isoformat()
        all_weather = fetch_hourly_weather(
            lat=correction_input.latitude,
            lon=correction_input.longitude,
            timezone=correction_input.timezone,
            start_date=history_weather_start,
            end_date=history_dates[-1],
            archive=True,
        )
    target_weather = _target_weather_from_forecast(
        forecast, target_date=target_date, latitude=correction_input.latitude,
        longitude=correction_input.longitude, timezone=correction_input.timezone,
    )
    all_weather[target_date] = target_weather
    add_thermal_states(all_weather)
    historical_temperature_features = {
        day: _temperature_features_for_day(day, hourly)
        for day, hourly in all_weather.items()
        if day < target_date
    }
    target_weather = all_weather.get(target_date, {})
    target_temps = {hour: float(item.get("temp_c", 24.0)) for hour, item in target_weather.items()}
    target_features = _temperature_features_for_day(target_date, target_weather)
    temperature_correction = _evening_temperature_correction(
        forecast_history=forecast_history,
        actual_history=actual_history,
        historical_temperature_features=historical_temperature_features,
        target_features=target_features,
        load_ratio=load_ratio,
    )
    raw_temperature_multiplier = temperature_correction.get("multiplier")
    temperature_multiplier = (
        float(raw_temperature_multiplier)
        if isinstance(raw_temperature_multiplier, (int, float))
        else 1.0 + (to_float(temperature_correction.get("multiplier_delta")) or 0.0)
    )
    load_scenarios = temperature_correction.get("load_scenarios")
    if not isinstance(load_scenarios, list):
        load_scenarios = _adaptive_load_scenarios([], confidence=0.0)

    corrected_pv, vector_residual, pv_multiplier = _correct_hourly_pv(
        hourly_pv_forecast=hourly_pv_forecast,
        pv_ratio=pv_ratio,
        skip_pv_correction=skip_pv_correction,
        forecast_history=forecast_history,
        actual_history=actual_history,
        forecast=forecast,
    )
    corrected_load: dict[int, float] = {}
    correction_hours = set(_temperature_correction_hours())
    temperature_hourly_multipliers = _temperature_hourly_multipliers(
        hourly_load_forecast=hourly_load_forecast,
        hourly_temperatures=target_temps,
        hourly_weather=target_weather,
        correction_hours=correction_hours,
        total_multiplier=temperature_multiplier,
    )
    temperature_correction["hourly_multipliers"] = {
        str(hour): round(multiplier, 6)
        for hour, multiplier in temperature_hourly_multipliers.items()
    }
    temperature_correction["hourly_shape_method"] = (
        "load_weighted_cooling_degree_distribution_preserving_total"
        if temperature_multiplier > 1.0 and target_temps
        else "uniform_temperature_multiplier"
    )
    for hour, value in hourly_load_forecast.items():
        multiplier = load_ratio * temperature_hourly_multipliers.get(hour, 1.0)
        corrected_load[hour] = max(0.0, value) * max(0.0, multiplier)

    comfort_model_enabled = env_bool("LOAD_COMFORT_MODEL_ENABLED", default=True)
    comfort_model = (
        predict_hourly_comfort_load(
            actual_history=actual_history,
            weather_by_day=all_weather,
            target_date=target_date,
            min_samples=max(24, int(env_float("LOAD_COMFORT_MODEL_MIN_SAMPLES", default=336.0))),
        )
        if comfort_model_enabled
        else {"enabled": False, "applied": False, "reason": "disabled"}
    )
    residual_multipliers = comfort_model.pop("_residual_multipliers", [])
    comfort_hourly = _coerce_hourly_values(comfort_model.get("hourly_load_kwh"))
    if comfort_model.get("applied") is True and len(comfort_hourly) == 24:
        corrected_load = comfort_hourly
        confidence = to_float(comfort_model.get("confidence")) or 0.0
        load_scenarios = _adaptive_load_scenarios(
            residual_multipliers if isinstance(residual_multipliers, list) else [],
            confidence=confidence,
        )
    comfort_model["hourly_load_kwh"] = {
        str(hour): round(value, 4)
        for hour, value in sorted(comfort_hourly.items())
    }

    load_safety_floor = {
        "enabled": False,
        "applied": False,
        "reason": "removed",
        "hourly_floor_kwh": {},
        "applied_hours": [],
    }
    raw_pv_total = sum(max(0.0, value) for value in hourly_pv_forecast.values())
    raw_load_total = sum(max(0.0, value) for value in hourly_load_forecast.values())
    paired_scenarios = _paired_forecast_error_scenarios(
        forecast_history=forecast_history,
        actual_history=actual_history,
        current_pv_correction=(sum(corrected_pv.values()) / raw_pv_total if raw_pv_total > 0.0 else 1.0),
        current_load_correction=(sum(corrected_load.values()) / raw_load_total if raw_load_total > 0.0 else 1.0),
    )
    peak_penalty = {"enabled": False, "applied_factor": 0.0, "reason": "removed"}
    return assemble_forecast_correction_result(
        corrected_load=corrected_load,
        corrected_pv=corrected_pv,
        raw_load=hourly_load_forecast,
        raw_pv=hourly_pv_forecast,
        load_scenarios=load_scenarios,
        paired_scenarios=paired_scenarios,
        peak_penalty=peak_penalty,
        rationale_details={
            "enabled": True,
            "method": "adaptive_comfort_thermal_inertia_hgb_with_existing_fallback",
            "history_source": history_source,
            "history_days": history_dates[-14:],
            "pv_ratio_ewma_raw": round(pv_ratio_raw, 6),
            "pv_ratio_ewma_applied": round(pv_multiplier, 6),
            "pv_ratio_ewma_skipped": bool(skip_pv_correction),
            "pv_ratio_floor": pv_min,
            "pv_ratio_cap": pv_max,
            "pv_ewma_alpha": pv_alpha,
            "pv_sample_count": pv_summary["sample_count"],
            "pv_latest_days": pv_summary["latest_days"],
            "physical_pv_vector_residual": vector_residual,
            "load_ratio_ewma_raw": round(load_ratio_raw, 6),
            "load_ratio_ewma_applied": round(load_ratio, 6),
            "load_ratio_floor": load_min,
            "load_ratio_cap": load_max,
            "load_ewma_alpha": load_alpha,
            "load_sample_count": load_summary["sample_count"],
            "load_latest_days": load_summary["latest_days"],
            "evening_load_temperature": temperature_correction,
            "comfort_load_model": comfort_model,
            "recent_and_analog_hourly_floor": load_safety_floor,
            "load_scenarios": load_scenarios,
            "paired_scenarios": paired_scenarios,
            "soc_peak_unmet_penalty": peak_penalty,
        },
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
