"""Use the learned clear ceiling as the planning PV basis when available."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import TYPE_CHECKING

from app.energy_plan import estimate_sunset_hour
from app.energy_plan.clear_ghi_adapter import _valid_hourly
from app.energy_plan.forecast_inputs import build_hourly_load_forecast
from app.energy_plan.plan_quality import _selected_pv_uncertainty
from app.forecasting.correction import ForecastCorrectionPolicy, build_load_forecast_correction

if TYPE_CHECKING:
    from app.energy_plan.workflow import ConsumptionForecastBundle, EnergyModelContext, PvForecastBundle


def build_ideal_max_forecast_bundle(context: EnergyModelContext, consumption: ConsumptionForecastBundle,
                                    *, as_of: datetime) -> PvForecastBundle | None:
    from app.energy_plan.workflow import PvForecastBundle
    from app.operations.pv_ideal_max import load_ideal_max_for_forecast

    try:
        loaded = load_ideal_max_for_forecast(date.fromisoformat(context.target_date), as_of=as_of,
                                            artifacts_dir=context.config.artifacts_dir)
    except Exception as exc:
        # An unavailable independent ledger must not block normal forecasting.
        print(json.dumps({"event": "pv_ideal_max_forecast", "severity": "ERROR", "status": "fallback",
                          "error_type": type(exc).__name__}), flush=True)
        return None
    if loaded is None:
        return None
    hourly_pv, trace = loaded
    raw_load = build_hourly_load_forecast(context.rows, daytime_load_kwh=consumption.daily.daytime_load_kwh,
        morning_load_kwh=consumption.daily.morning_load_kwh, overnight_load_by_hour=None)
    correction = build_load_forecast_correction(rows=context.consumption_rows, hourly_load_forecast=raw_load,
        target_date=context.target_date, latitude=context.config.latitude, longitude=context.config.longitude,
        timezone=context.config.timezone, forecast=context.forecast,
        policy=ForecastCorrectionPolicy.from_env(allow_load_safety_floor=consumption.occupancy_adjustment is None))
    diagnostics: dict[str, object] = {"enabled": True, "selected_method": trace["model_id"],
        "basis_version": trace["basis_version"], "calibration_sha256": trace["calibration_sha256"],
        "coefficient": trace["coefficient"], "revision": trace["revision"], "weather_adjustment_applied": False}
    print(json.dumps({"event": "pv_ideal_max_forecast", "status": "selected",
        "target_date": context.target_date, "daytime_pv_kwh": sum(hourly_pv[h] for h in range(7, 23)),
        "coefficient": trace["coefficient"], "revision": trace["revision"]}), flush=True)
    return PvForecastBundle(array_forecast=None, hourly_load_kwh=_valid_hourly(correction.get("hourly_load_kwh")),
        hourly_pv_kwh=_valid_hourly(hourly_pv), hourly_weather_shape={"enabled": False, "reason": "ideal_max_direct"},
        physical_diagnostics=diagnostics, correction=correction, selected_method=str(trace["model_id"]),
        source=str(trace["model_id"]), uncertainty=_selected_pv_uncertainty(physical_pv_selected=True,
            physical_pv_diagnostics=diagnostics, pv_array_forecast=None),
        sunset_hour=estimate_sunset_hour(hourly_pv), ideal_max_provenance=trace)
