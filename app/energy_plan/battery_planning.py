"""Join ingested, provenance-labelled observations to forecast battery response."""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any

from app.energy_plan.battery_calibration import BatteryCalibrationResult, calibrate_battery_response
from app.energy_plan.battery_replay_validation import make_battery_validation_evaluator
from app.energy_plan.battery_response import BatterySocResponse
from app.energy_plan.energy_model import EnergyModelCoefficients
from app.energy_plan.soc_cost import SocCostModel


def build_planning_battery_response(
    *, rows: list[dict[str, Any]], coefficients: EnergyModelCoefficients,
    capacity_kwh: float, forecast: dict[str, Any], as_of: datetime,
    cost_model: SocCostModel, previous_payload: dict[str, Any] | None = None,
    weather_observations: list[dict[str, Any]] | None = None,
) -> BatteryCalibrationResult:
    """Use AC-side empirical constants; no theoretical temperature multiplier.

    A supplied nonthermal capacity adjustment (for example degradation) scales
    both SOC responses equally. History temperatures train only when each row
    explicitly labels its observation source. Configured/forecast temperatures
    are never re-labelled as historical battery measurements.
    """
    if not isfinite(capacity_kwh) or capacity_kwh <= 0:
        raise ValueError("planning battery capacity must be finite and positive")
    if not isfinite(coefficients.battery_usable_capacity_kwh) or coefficients.battery_usable_capacity_kwh <= 0:
        raise ValueError("empirical battery capacity must be finite and positive")
    scale = coefficients.battery_usable_capacity_kwh / capacity_kwh
    baseline = BatterySocResponse(
        coefficients.soc_per_kwh_charge * scale,
        coefficients.soc_per_kwh_discharge * scale,
        max(0.0, -coefficients.soc_drift_per_slot * 2.0),
    )
    observations: list[dict[str, Any]] = []
    weather_by_date: dict[str, tuple[float, datetime, dict[str, str]]] = {}
    for weather in weather_observations or []:
        if weather.get("temperature_source", weather.get("source")) != "observed_outdoor":
            continue
        if weather.get("available_at") is None and weather.get("retrieved_at") is None:
            continue
        try:
            temperature = float(weather["temp"])
            receipt_values = {key: str(weather[key]) for key in ("available_at", "retrieved_at", "updated_at") if weather.get(key) is not None}
            receipts = [datetime.fromisoformat(value) for value in receipt_values.values()]
            normalized_receipts = [receipt.replace(tzinfo=as_of.tzinfo) if receipt.tzinfo is None else receipt.astimezone(as_of.tzinfo) if as_of.tzinfo is not None else receipt.replace(tzinfo=None) for receipt in receipts]
            date_key = str(weather["date"])[:10]
            actual_date = datetime.fromisoformat(date_key).date()
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        if not isfinite(temperature) or not normalized_receipts or any(receipt > as_of for receipt in normalized_receipts) or actual_date >= as_of.date():
            continue
        weather_by_date[date_key] = (temperature, max(normalized_receipts), receipt_values)
    for row in rows:
        observation = dict(row)
        if observation.get("temperature_source") not in ("measured_battery", "observed_outdoor"):
            observation.pop("temperature_c", None)
            observation.pop("temperature_source", None)
            timestamp = observation.get("dt")
            date_key = timestamp.date().isoformat() if isinstance(timestamp, datetime) else str(timestamp)[:10]
            if date_key in weather_by_date:
                temperature, receipt, receipt_values = weather_by_date[date_key]
                observation.update(temperature_c=temperature, temperature_source="observed_outdoor",
                                   temperature_resolution="daily_mean", temperature_available_at=receipt,
                                   temperature_receipts=receipt_values)
        observations.append(observation)
    temperatures: dict[int, float] = {}
    hourly_weather = forecast.get("hourly_weather")
    if isinstance(hourly_weather, list):
        for weather in hourly_weather:
            if not isinstance(weather, dict):
                continue
            try:
                hour, temperature = int(weather["hour"]), float(weather["temp_c"])
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
            if 0 <= hour < 24 and isfinite(temperature):
                temperatures[hour] = temperature
    daily_temperature: float | None
    try:
        daily_temperature = float(forecast["temp_c"])
        if not isfinite(daily_temperature):
            daily_temperature = None
    except (KeyError, ValueError, TypeError, OverflowError):
        daily_temperature = None
    result = calibrate_battery_response(
        rows=observations, baseline=baseline, as_of=as_of,
        forecast_temperatures=temperatures, forecast_temperature_source="forecast_outdoor",
        previous_payload=previous_payload, validation_evaluator=make_battery_validation_evaluator(cost_model),
        forecast_daily_temperature_c=daily_temperature,
    )
    diagnostics = dict(result.diagnostics)
    diagnostics.update(
        baseline_capacity_scale=scale,
        temperature_prediction_source="forecast_outdoor",
        forecast_temperature_hours=sorted(temperatures),
        forecast_daily_temperature_c=daily_temperature,
        observed_outdoor_daily_dates=len(weather_by_date),
        financial_validation_scope="current_planning_contract_revaluation_prediction_error_not_invoices_or_savings",
    )
    return BatteryCalibrationResult(result.response, diagnostics)
