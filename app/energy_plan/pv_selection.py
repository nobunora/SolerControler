from __future__ import annotations

from typing import TYPE_CHECKING

from app.energy_plan import coerce_hourly_energy as _coerce_hourly_float_dict, estimate_sunset_hour as _estimate_sunset_hour
from app.energy_plan.forecast_inputs import build_hourly_load_forecast, build_hourly_pv_forecast, reshape_hourly_pv_by_weather
from app.energy_plan.plan_quality import _env_bool, _env_float_clamped, _selected_pv_uncertainty
from app.forecasting.correction import ForecastCorrectionInput, ForecastCorrectionPolicy, build_forecast_correction
from app.forecasting.correction_history_io import _load_forecast_hourly_history
from app.forecasting.pv_physical import build_physical_pv_candidate

if TYPE_CHECKING:
    from app.energy_plan.workflow import ConsumptionForecastBundle, EnergyModelContext, NightChargePreparation, PvForecastBundle
def _build_selected_pv_forecast(
    context: EnergyModelContext,
    consumption: ConsumptionForecastBundle,
    night_charge: NightChargePreparation,
) -> PvForecastBundle:
    from app.energy_plan.workflow import PvForecastBundle

    config = context.config
    pv_array_forecast = night_charge.pv_array_forecast
    raw_hourly_load = build_hourly_load_forecast(
        context.rows,
        daytime_load_kwh=consumption.daily.daytime_load_kwh,
        morning_load_kwh=consumption.daily.morning_load_kwh,
        overnight_load_by_hour=None,
    )
    raw_hourly_pv = build_hourly_pv_forecast(
        context.rows,
        pv_forecast=pv_array_forecast,
        target_date=context.target_date,
        fallback_total_kwh=night_charge.result.predicted_pv_kwh,
    )
    raw_hourly_pv, hourly_weather_shape = reshape_hourly_pv_by_weather(
        raw_hourly_pv,
        context.forecast,
        enabled=_env_bool("HOURLY_WEATHER_PV_SHAPE_ENABLED", True),
        blend=_env_float_clamped(
            "HOURLY_WEATHER_PV_SHAPE_BLEND",
            0.75,
            min_value=0.0,
            max_value=1.0,
        ),
    )
    physical_history, history_source = _load_forecast_hourly_history(
        target_date=context.target_date
    )
    physical_candidate = build_physical_pv_candidate(
        rows=context.rows,
        forecast_history=physical_history,
        existing_hourly_pv=raw_hourly_pv,
        forecast=context.forecast,
        target_date=context.target_date,
        lat=config.latitude,
        lon=config.longitude,
        timezone=config.timezone,
    )
    physical_diagnostics: dict[str, object] = {
        **physical_candidate.diagnostics,
        "history_source": history_source,
    }
    physical_selected = bool(physical_diagnostics.get("enabled"))
    if physical_selected:
        raw_hourly_pv = physical_candidate.hourly_pv_kwh
    correction = build_forecast_correction(
        ForecastCorrectionInput(
            rows=context.rows,
            hourly_load_forecast=raw_hourly_load,
            hourly_pv_forecast=raw_hourly_pv,
            target_date=context.target_date,
            latitude=config.latitude,
            longitude=config.longitude,
            timezone=config.timezone,
            forecast=context.forecast,
        ),
        ForecastCorrectionPolicy.from_env(
            skip_pv_correction=physical_selected,
            allow_load_safety_floor=consumption.occupancy_adjustment is None,
        ),
    )
    hourly_load = _coerce_hourly_float_dict(correction.get("hourly_load_kwh"))
    hourly_pv = _coerce_hourly_float_dict(correction.get("hourly_pv_kwh"))
    return PvForecastBundle(
        array_forecast=pv_array_forecast,
        hourly_load_kwh=hourly_load,
        hourly_pv_kwh=hourly_pv,
        hourly_weather_shape=hourly_weather_shape,
        physical_diagnostics=physical_diagnostics,
        correction=correction,
        selected_method=str(physical_diagnostics.get("selected_method") or "existing"),
        source="physical_pv_forecast" if physical_selected else "corrected_pv_forecast",
        uncertainty=_selected_pv_uncertainty(
            physical_pv_selected=physical_selected,
            physical_pv_diagnostics=physical_diagnostics,
            pv_array_forecast=pv_array_forecast,
        ),
        sunset_hour=_estimate_sunset_hour(hourly_pv),
    )


# readable-code-audit: skip STRUCT-04 — SOC limits are derived together to preserve their ordering and safety invariant
