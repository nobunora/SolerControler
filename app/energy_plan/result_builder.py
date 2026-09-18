from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.energy_plan import EnergyPlanOutput as EnergyModelOutput, PlanDocumentV1, summarize_hourly_pv as _hourly_pv_totals
from app.energy_plan.energy_model import to_dict
from app.energy_plan.optimization import OptimizationDecision, apply_uncertainty_floor as _apply_uncertainty_floor
from app.energy_plan.plan_quality import _build_plan_quality, _candidate_reason_summary, _decision_cost_breakdown, _to_optional_float
from app.energy_plan.soc_constraints import SocConstraintSet, active_constraint_names as _active_constraint_names
from app.energy_plan.soc_cost import to_plain_dict
from app.energy_plan.soc_projection import (
    allocate_hourly_grid_charge,
    build_hourly_soc_projection,
    build_planned_night_charge_schedule,
    estimate_charge_power_kw_from_csv,
    resolve_planned_charge_end_time,
)

if TYPE_CHECKING:
    from app.energy_plan.workflow import ConsumptionForecastBundle, EnergyModelContext, NightChargePreparation, PvForecastBundle
def _build_energy_model_output(
    context: EnergyModelContext,
    consumption: ConsumptionForecastBundle,
    night_charge: NightChargePreparation,
    pv_forecast: PvForecastBundle,
    constraints: SocConstraintSet,
    decision: OptimizationDecision,
) -> EnergyModelOutput:
    from app.energy_plan.workflow import (
        _consumption_forecast_to_dict,
        _occupancy_adjustment_to_dict,
    )

    coefficients: dict[str, Any] = to_dict(context.coefficients)
    array_forecast = night_charge.pv_array_forecast
    if isinstance(array_forecast, dict) and array_forecast.get("enabled"):
        calibration = array_forecast.get("calibration", {})
        arrays = array_forecast.get("arrays", [])
        if isinstance(calibration, dict):
            factor = _to_optional_float(calibration.get("effective_factor"))
            if factor is None:
                factor = _to_optional_float(calibration.get("factor"))
            if factor is not None:
                coefficients["pv_array_calibration_factor"] = factor
        if isinstance(arrays, list):
            coefficients["pv_array_total_capacity_kw"] = sum(
                _to_optional_float(array.get("capacity_kw")) or 0.0
                for array in arrays
                if isinstance(array, dict)
            )
    uncertainty = pv_forecast.uncertainty
    coefficients["pv_forecast_error_ratio_mean"] = uncertainty.mean_multiplier
    coefficients["pv_forecast_error_ratio_std"] = uncertainty.std_multiplier
    coefficients["pv_forecast_error_ratio_variance"] = uncertainty.variance_multiplier
    coefficients["pv_forecast_error_ratio_sample_count"] = float(uncertainty.sample_count)
    physical_scales = pv_forecast.physical_diagnostics.get("scales")
    if isinstance(physical_scales, dict):
        radiation_scale = _to_optional_float(physical_scales.get("radiation_scale"))
        global_bias_scale = _to_optional_float(physical_scales.get("global_bias_scale"))
        if radiation_scale is not None:
            coefficients["physical_pv_radiation_scale"] = radiation_scale
        if global_bias_scale is not None:
            coefficients["physical_pv_global_bias_scale"] = global_bias_scale

    result_payload = decision.result_payload
    final_pv_totals = _hourly_pv_totals(pv_forecast.hourly_pv_kwh)
    result_payload["final_predicted_pv_kwh"] = final_pv_totals["total_kwh"]
    result_payload["final_predicted_morning_pv_kwh"] = final_pv_totals["morning_kwh"]
    result_payload["final_predicted_midday_pv_kwh"] = final_pv_totals["midday_kwh"]
    result_payload["final_predicted_evening_pv_kwh"] = final_pv_totals["evening_kwh"]
    result_payload["final_pv_forecast_source"] = pv_forecast.source
    optimization_payload = decision.optimization_payload
    cost_payload = decision.cost_optimization_payload

    night_window_start = os.getenv("KP_NIGHT_CHARGE_WINDOW_START", "23:00").strip() or "23:00"
    night_window_end = os.getenv("KP_NIGHT_CHARGE_WINDOW_END", "07:00").strip() or "07:00"
    conditions_path = Path(
        os.getenv("KP_OPERATION_CONDITIONS_PATH", "config/operation_conditions.json").strip()
        or "config/operation_conditions.json"
    )
    fallback_charge_power_kw = float(os.getenv("KP_DEFAULT_CHARGE_POWER_KW", "4.0").strip() or "4.0")
    estimated_charge_power_kw = estimate_charge_power_kw_from_csv(
        context.csv_paths,
        night_window_start=night_window_start,
        night_window_end=night_window_end,
        fallback_kw=fallback_charge_power_kw,
    )
    planned_charge_end_time = resolve_planned_charge_end_time(
        conditions_path=conditions_path,
        default_hhmm=night_window_end,
    )
    planned_schedule = build_planned_night_charge_schedule(
        required_night_charge_kwh=float(result_payload.get("required_night_charge_kwh") or 0.0),
        estimated_charge_power_kw=estimated_charge_power_kw,
        charge_end_time=planned_charge_end_time,
        not_before_minute=3 * 60,
    )
    hourly_grid_charge = allocate_hourly_grid_charge(
        schedule=planned_schedule,
        required_night_charge_kwh=float(result_payload.get("required_night_charge_kwh") or 0.0),
    )
    soc_risk = (
        optimization_payload.get("soc_cost_risk", {})
        if isinstance(optimization_payload, dict)
        else {}
    )
    optimized_charge_efficiency = (
        _to_optional_float(soc_risk.get("charge_efficiency"))
        if isinstance(soc_risk, dict)
        else None
    )
    charge_efficiency = (
        optimized_charge_efficiency
        if optimized_charge_efficiency is not None
        else float(context.coefficients.battery_round_trip_efficiency)
    )
    hourly_soc_forecast = build_hourly_soc_projection(
        anchor_hour=3,
        anchor_soc_percent=context.latest_soc_percent,
        capacity_kwh=night_charge.result.effective_capacity_kwh,
        charge_efficiency=charge_efficiency,
        hourly_grid_charge_kwh=hourly_grid_charge,
        hourly_load_kwh=pv_forecast.hourly_load_kwh,
        hourly_pv_kwh=pv_forecast.hourly_pv_kwh,
    )
    result_payload.update(
        {
            "planned_charge_start_time": planned_schedule.charge_start_time,
            "planned_charge_end_time": planned_schedule.charge_end_time,
            "planned_charge_power_kw": planned_schedule.estimated_charge_power_kw,
            "planned_charge_duration_minutes": planned_schedule.planned_charge_duration_minutes,
            "planned_charge_duration_clipped": planned_schedule.duration_clipped_to_available_window,
            "soc_forecast_anchor_hour": 3,
            "soc_forecast_anchor_percent": context.latest_soc_percent,
            "hourly_grid_charge_forecast_kwh": {
                str(hour): round(value, 4) for hour, value in sorted(hourly_grid_charge.items())
            },
            "hourly_soc_forecast_percent": {
                str(hour): (round(value, 1) if value is not None else None)
                for hour, value in sorted(hourly_soc_forecast.items())
            },
            "soc_forecast_source": "energy-plan-single-source",
        }
    )
    plan_quality = _build_plan_quality(
        forecast=context.forecast,
        optimization_payload=optimization_payload,
        result_payload=result_payload,
    )
    active_constraints = _active_constraint_names(
        morning_headroom_guard=constraints.morning_headroom,
        daytime_net_surplus_headroom_guard=constraints.daytime_net_surplus,
        historical_soc_gain_guard=constraints.historical_soc_gain,
        respect_morning_headroom_guard=(
            bool(optimization_payload.get("respect_morning_headroom_guard"))
            if isinstance(optimization_payload, dict)
            else True
        ),
    )
    objective = (
        "minimize_night_charge_plus_day_buy_plus_sell_loss_plus_peak_unmet_plus_monthly_tier_plus_decision_prior_cost"
        if cost_payload is not None
        else "legacy_peak_soc_objective"
    )
    document = PlanDocumentV1(
        csv_paths=[str(path) for path in context.csv_paths],
        plan_quality=plan_quality,
        forecast=context.forecast,
        pv_array_forecast=array_forecast,
        historical_profile=context.historical_profile,
        consumption_forecast=_consumption_forecast_to_dict(consumption.daily),
        base_consumption_forecast=_consumption_forecast_to_dict(consumption.base_daily),
        weather_history=consumption.training_diagnostics,
        occupancy_adjustment=_occupancy_adjustment_to_dict(consumption.occupancy_adjustment),
        coefficients=coefficients,
        inputs=to_dict(night_charge.inputs),
        result=result_payload,
        daytime_soc_optimization=optimization_payload,
        decision_rationale={
            "plan_quality": plan_quality,
            "objective": objective,
            "selected_reason": (
                "lowest_total_cost_with_active_constraints"
                if cost_payload is not None
                else "legacy_peak_soc_objective_fallback"
            ),
            "active_constraints": active_constraints,
            "rejected_candidates": _candidate_reason_summary(optimization_payload),
            "cost_breakdown_yen": _decision_cost_breakdown(optimization_payload),
            "historical_daytime_soc_gain_guard": constraints.historical_soc_gain,
            "morning_pv_headroom_guard": constraints.morning_headroom,
            "daytime_net_surplus_headroom_guard": constraints.daytime_net_surplus,
            "hourly_weather_pv_shape": pv_forecast.hourly_weather_shape,
            "pv_physical_forecast": pv_forecast.physical_diagnostics,
            "forecast_correction": pv_forecast.correction.get("rationale", {}),
            "soc_decision_feedback_prior": (
                cost_payload.get("soc_decision_feedback_prior", {})
                if isinstance(cost_payload, dict)
                else {}
            ),
            "final_pv_forecast": {
                **final_pv_totals,
                "source": result_payload["final_pv_forecast_source"],
                "legacy_result_predicted_pv_kwh": result_payload.get("predicted_pv_kwh"),
            },
            "pv_uncertainty": to_plain_dict(_apply_uncertainty_floor(uncertainty)),
            "raw_target_soc_7_percent": result_payload.get("target_soc_7_percent_base"),
            "final_target_soc_7_percent": result_payload.get("target_soc_7_percent"),
            "final_required_night_charge_kwh": result_payload.get("required_night_charge_kwh"),
        },
    )
    return EnergyModelOutput(
        document=document,
        output_path=context.config.artifacts_dir / "night_charge_plan.json",
    )


