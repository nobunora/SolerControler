"""Apply forecast radiation to the learned capability without changing its ledger."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, cast

from app.energy_plan import estimate_sunset_hour
from app.energy_plan.clear_ghi_adapter import _valid_hourly
from app.energy_plan.forecast_inputs import build_hourly_load_forecast
from app.energy_plan.plan_quality import _selected_pv_uncertainty
from app.forecasting.correction import ForecastCorrectionPolicy, build_load_forecast_correction
from app.forecasting.pv_clear_ghi import clear_ghi_hour, normalize_radiation_intervals, _repair_short_gaps

if TYPE_CHECKING:
    from app.energy_plan.workflow import ConsumptionForecastBundle, EnergyModelContext, PvForecastBundle


def _weather_adjusted_pv(context: EnergyModelContext, maximum: dict[int, float], *, as_of: datetime
                         ) -> tuple[dict[int, float], dict[str, Any]]:
    forecast, config = context.forecast, context.config
    source = forecast.get("pv_radiation_rows")
    retrieved = forecast.get("pv_forecast_retrieved_at")
    digest = forecast.get("pv_forecast_input_sha256")
    if (not isinstance(source, list) or any(not isinstance(row, dict) for row in source)
            or not isinstance(retrieved, str) or not isinstance(digest, str)
            or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
        raise ValueError("ideal maximum forecast requires timestamped radiation provenance")
    retrieved_at = datetime.fromisoformat(retrieved)
    if as_of.utcoffset() is None or retrieved_at.utcoffset() is None or retrieved_at > as_of:
        raise ValueError("ideal maximum weather input is unavailable at as_of")
    issued = forecast.get("pv_provider_issued_at")
    if issued is not None:
        if not isinstance(issued, str):
            raise ValueError("invalid weather issuance provenance")
        issued_at = datetime.fromisoformat(issued)
        if issued_at.utcoffset() is None or issued_at > retrieved_at:
            raise ValueError("weather issuance is later than retrieval")
    day = date.fromisoformat(context.target_date)
    clear = tuple(clear_ghi_hour(day, h, latitude=config.latitude, longitude=config.longitude,
                                timezone=config.timezone) for h in range(24))
    energy, source_times = normalize_radiation_intervals(tuple(source), target_date=day, timezone=config.timezone)
    energy, repaired = _repair_short_gaps(clear, energy)
    factors = {h: min(cast(float, energy[h]) / clear[h], 1.0) if clear[h] > 0 else 0.0 for h in range(24)}
    pv = {h: maximum[h] * factors[h] for h in range(24)}
    return pv, {"method": "forecast_ghi_over_clear_ghi", "forecast_sha256": digest,
        "retrieved_at": retrieved, "provider_issued_at": issued,
        "source_times": [stamp.isoformat() if stamp is not None else None for stamp in source_times],
        "repaired_hours": list(repaired), "hourly_factors": {str(h): v for h, v in factors.items()}}


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
    maximum, trace = loaded
    maximum = _valid_hourly(maximum)
    hourly_pv, weather = _weather_adjusted_pv(context, maximum, as_of=as_of)
    trace = {**trace, "weather_adjustment_applied": True,
             "capability_retrieved_at": trace.get("retrieved_at"),
             "retrieved_at": weather["retrieved_at"], "provider_issued_at": weather["provider_issued_at"],
             "forecast_sha256": weather["forecast_sha256"],
             "hourly_maximum_kwh": {str(h): v for h, v in maximum.items()},
             "hourly_pv_kwh": {str(h): v for h, v in hourly_pv.items()}, "weather_forecast": weather}
    raw_load = build_hourly_load_forecast(context.rows, daytime_load_kwh=consumption.daily.daytime_load_kwh,
        morning_load_kwh=consumption.daily.morning_load_kwh, overnight_load_by_hour=None)
    correction = build_load_forecast_correction(rows=context.consumption_rows, hourly_load_forecast=raw_load,
        target_date=context.target_date, latitude=context.config.latitude, longitude=context.config.longitude,
        timezone=context.config.timezone, forecast=context.forecast,
        policy=ForecastCorrectionPolicy.from_env(allow_load_safety_floor=consumption.occupancy_adjustment is None))
    diagnostics: dict[str, object] = {"enabled": True, "selected_method": trace["model_id"],
        "basis_version": trace["basis_version"], "calibration_sha256": trace["calibration_sha256"],
        "coefficient": trace["coefficient"], "revision": trace["revision"], "weather_adjustment_applied": True,
        "forecast_sha256": weather["forecast_sha256"], "repaired_hours": weather["repaired_hours"]}
    print(json.dumps({"event": "pv_ideal_max_forecast", "status": "selected",
        "target_date": context.target_date, "daytime_pv_kwh": sum(hourly_pv[h] for h in range(7, 23)),
        "coefficient": trace["coefficient"], "revision": trace["revision"]}), flush=True)
    return PvForecastBundle(array_forecast=None, hourly_load_kwh=_valid_hourly(correction.get("hourly_load_kwh")),
        hourly_pv_kwh=hourly_pv, hourly_weather_shape={"enabled": True, **weather},
        physical_diagnostics=diagnostics, correction=correction, selected_method=str(trace["model_id"]),
        source=str(trace["model_id"]), uncertainty=_selected_pv_uncertainty(physical_pv_selected=True,
            physical_pv_diagnostics=diagnostics, pv_array_forecast=None),
        sunset_hour=estimate_sunset_hour(hourly_pv), ideal_max_provenance=trace, hourly_pv_max_kwh=maximum)
