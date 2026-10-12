"""Translate the direct clear-GHI model into the existing planning contracts."""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Mapping, cast

from app.energy_plan import estimate_sunset_hour
from app.energy_plan.energy_model import NightChargeInputs, compute_night_charge_target, to_dict
from app.energy_plan.forecast_inputs import build_hourly_load_forecast
from app.energy_plan.plan_quality import _selected_pv_uncertainty, _to_optional_float
from app.energy_plan.monthly_projection import expected_rest_of_month_day_buy_kwh, monthly_day_buy_kwh_before_target
from app.forecasting.correction import ForecastCorrectionPolicy, build_load_forecast_correction
from app.forecasting.pv_clear_ghi import BASIS_VERSION, MODEL_ID, ClearGhiCalibration, forecast_clear_ghi

if TYPE_CHECKING:
    from app.energy_plan.workflow import ConsumptionForecastBundle, EnergyModelContext, NightChargePreparation, PvForecastBundle


def _valid_hourly(value: object) -> dict[int, float]:
    if not isinstance(value, dict) or set(value) != set(range(24)) or any(type(k) is not int for k in value):
        raise ValueError("forecast requires exactly 24 integer hour keys")
    result: dict[int, float] = {}
    for hour, raw in value.items():
        if isinstance(raw, bool) or not isinstance(raw, (float, int)) or not math.isfinite(raw) or raw < 0:
            raise ValueError("forecast requires finite nonnegative hourly energy")
        result[hour] = float(raw)
    return result


def _build_clear_ghi_forecast_bundle(
    context: EnergyModelContext, consumption: ConsumptionForecastBundle, calibration: ClearGhiCalibration, *, as_of: datetime,
) -> PvForecastBundle:
    from app.energy_plan.workflow import PvForecastBundle

    config = context.config
    raw_load = build_hourly_load_forecast(context.rows, daytime_load_kwh=consumption.daily.daytime_load_kwh,
        morning_load_kwh=consumption.daily.morning_load_kwh, overnight_load_by_hour=None)
    correction = build_load_forecast_correction(rows=context.rows, hourly_load_forecast=raw_load,
        target_date=context.target_date, latitude=config.latitude, longitude=config.longitude, timezone=config.timezone,
        forecast=context.forecast, policy=ForecastCorrectionPolicy.from_env(allow_load_safety_floor=consumption.occupancy_adjustment is None))
    hourly_load = _valid_hourly(correction.get("hourly_load_kwh"))
    source = context.forecast.get("pv_radiation_rows")
    retrieved = context.forecast.get("pv_forecast_retrieved_at")
    if not isinstance(source, list) or any(not isinstance(row, dict) for row in source) or not isinstance(retrieved, str):
        raise ValueError("timestamped radiation input/provenance is unavailable")
    issued = context.forecast.get("pv_provider_issued_at")
    if issued is not None and not isinstance(issued, str):
        raise ValueError("invalid provider issuance provenance")
    forecast = forecast_clear_ghi(target_date=date.fromisoformat(context.target_date), as_of=as_of,
        latitude=config.latitude, longitude=config.longitude, timezone=config.timezone, calibration=calibration,
        source_rows=cast(tuple[Mapping[str, object], ...], tuple(source)),
        forecast_hash=str(context.forecast.get("pv_forecast_input_sha256") or ""),
        retrieved_at=datetime.fromisoformat(retrieved), provider_issued_at=datetime.fromisoformat(issued) if issued else None)
    hourly_pv = _valid_hourly(dict(enumerate(forecast.pv_kwh)))
    diagnostics: dict[str, object] = {"enabled": True, "selected_method": MODEL_ID, "basis_version": BASIS_VERSION,
        "calibration_sha256": calibration.sha256, "forecast_sha256": forecast.forecast_hash,
        "repaired_hours": list(forecast.repaired_hours)}
    return PvForecastBundle(array_forecast=None, hourly_load_kwh=hourly_load, hourly_pv_kwh=hourly_pv,
        hourly_weather_shape={"enabled": False, "reason": "clear_ghi_direct"}, physical_diagnostics=diagnostics,
        correction=correction, selected_method=MODEL_ID, source=MODEL_ID,
        uncertainty=_selected_pv_uncertainty(physical_pv_selected=True, physical_pv_diagnostics=diagnostics, pv_array_forecast=None),
        sunset_hour=estimate_sunset_hour(hourly_pv), clear_ghi_forecast=forecast)


def _prepare_night_charge_from_hourly(context: EnergyModelContext, consumption: ConsumptionForecastBundle, pv_forecast: PvForecastBundle) -> NightChargePreparation:
    from app.energy_plan.workflow import NightChargePreparation

    config, forecast = context.config, context.forecast
    pv = _valid_hourly(pv_forecast.hourly_pv_kwh)
    load = _valid_hourly(pv_forecast.hourly_load_kwh)
    parsed_temp_c = _to_optional_float(forecast.get("temp_c"))
    temp_c = 20.0 if parsed_temp_c is None or not math.isfinite(parsed_temp_c) else parsed_temp_c
    expected_overnight_discharge_kwh = 0.0
    billing_rows = context.billing_rows if context.billing_rows is not None else context.rows
    monthly_day_buy = monthly_day_buy_kwh_before_target(billing_rows, target_date=context.target_date)
    expected_rest_of_month = expected_rest_of_month_day_buy_kwh(billing_rows, target_date=context.target_date)
    inputs = NightChargeInputs(soc_now_percent=context.latest_soc_percent,
        sun_hours_forecast=_to_optional_float(forecast.get("sun_hours")) or 0.0, temp_forecast_c=temp_c,
        daytime_load_forecast_kwh=sum(load[h] for h in range(7, 23)), morning_load_forecast_kwh=sum(load[h] for h in range(7, 10)),
        morning_pv_ratio=context.historical_profile["morning_pv_ratio"], midday_surplus_ratio=context.historical_profile["midday_surplus_ratio"],
        reserve_soc_percent=config.reserve_soc_percent, cycle_count=config.cycle_count,
        battery_temp_c=config.battery_temp_c if config.battery_temp_c is not None else temp_c,
        predicted_pv_kwh_override=sum(pv[h] for h in range(7, 23)), predicted_morning_pv_kwh_override=sum(pv[h] for h in range(7, 10)),
        predicted_midday_surplus_kwh_override=max(0.0, sum(pv[h] - load[h] for h in range(10, 16))),
        expected_overnight_discharge_kwh=expected_overnight_discharge_kwh)
    result = compute_night_charge_target(context.coefficients, inputs)
    return NightChargePreparation(pv_array_forecast=None, inputs=inputs, result=result, result_payload=cast(dict[str, Any], to_dict(result)),
        monthly_day_buy_before_target=monthly_day_buy, expected_rest_of_month_day_buy=expected_rest_of_month,
        expected_overnight_discharge_kwh=expected_overnight_discharge_kwh)
