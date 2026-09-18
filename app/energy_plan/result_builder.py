from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.energy_plan import EnergyPlanOutput as EnergyModelOutput, PlanDocumentV1, summarize_hourly_pv as _hourly_pv_totals
from app.energy_plan.energy_model import to_dict
from app.energy_plan.optimization import OptimizationDecision, apply_uncertainty_floor as _apply_uncertainty_floor
from app.energy_plan.plan_quality import _build_plan_quality, _candidate_reason_summary, _decision_cost_breakdown, _to_optional_float
from app.energy_plan.soc_constraints import SocConstraintSet, active_constraint_names as _active_constraint_names
from app.energy_plan.soc_cost import to_plain_dict
from app.energy_plan.night_schedule import (
    NightChargeScheduleSettings,
    build_planned_night_charge_schedule,
    estimate_night_charge_power_kw,
    forecast_anchor_minute,
    resolve_night_charge_end_time,
)
from app.energy_plan.soc_projection import simulate_daytime_soc, simulate_night_charge_soc

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

    final_target_soc = _to_optional_float(result_payload.get("target_soc_7_percent"))
    capacity_kwh = night_charge.result.effective_capacity_kwh
    if final_target_soc is not None and capacity_kwh > 0.0:
        schedule_settings = NightChargeScheduleSettings.from_env()
        anchor_minute = forecast_anchor_minute(context.rows, target_date=context.target_date)
        estimated_charge_power_kw = estimate_night_charge_power_kw(
            context.rows,
            night_window_start=schedule_settings.night_window_start,
            night_window_end=schedule_settings.night_window_end,
            fallback_kw=schedule_settings.fallback_charge_power_kw,
        )
        planned_schedule = build_planned_night_charge_schedule(
            required_charge_kwh=max(
                0.0,
                _to_optional_float(result_payload.get("required_night_charge_kwh")) or 0.0,
            ),
            estimated_charge_power_kw=estimated_charge_power_kw,
            anchor_minute=anchor_minute,
            charge_end_time=resolve_night_charge_end_time(
                schedule_settings.operation_conditions_path,
                default=schedule_settings.night_window_end,
            ),
        )
        night_soc = simulate_night_charge_soc(
            current_soc_percent=context.latest_soc_percent,
            capacity_kwh=capacity_kwh,
            charge_efficiency=context.coefficients.battery_round_trip_efficiency,
            anchor_hour=planned_schedule.anchor_minute // 60,
            hourly_grid_charge_kwh=planned_schedule.hourly_grid_charge_kwh,
        )
        forecast_soc_7 = night_soc.get(7, context.latest_soc_percent)
        daytime_projection = simulate_daytime_soc(
            start_energy_kwh=capacity_kwh * forecast_soc_7 / 100.0,
            capacity_kwh=capacity_kwh,
            hourly_load_kwh=pv_forecast.hourly_load_kwh,
            hourly_pv_kwh=pv_forecast.hourly_pv_kwh,
        )
        hourly_soc = {**night_soc, **daytime_projection.soc_at_hour_start_percent}
        result_payload.update(
            {
                "planned_charge_anchor_time": planned_schedule.anchor_time,
                "planned_charge_start_time": planned_schedule.start_time,
                "planned_charge_end_time": planned_schedule.end_time,
                "planned_charge_power_kw": round(planned_schedule.estimated_charge_power_kw, 4),
                "planned_charge_duration_minutes": planned_schedule.planned_duration_minutes,
                "planned_charge_deliverable_kwh": planned_schedule.deliverable_charge_kwh,
                "planned_charge_unmet_kwh": planned_schedule.unmet_charge_kwh,
                "planned_charge_limitation_reason": planned_schedule.limitation_reason,
                "forecast_soc_7_percent": round(forecast_soc_7, 4),
                "hourly_grid_charge_forecast_kwh": {
                    str(hour): value
                    for hour, value in sorted(planned_schedule.hourly_grid_charge_kwh.items())
                },
                "hourly_soc_forecast_percent": {
                    str(hour): value for hour, value in sorted(hourly_soc.items())
                },
                "hourly_soc_forecast_basis": "energy_plan_planned_schedule",
            }
        )
    optimization_payload = decision.optimization_payload
    cost_payload = decision.cost_optimization_payload
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
            "planned_charge_anchor_time": result_payload.get("planned_charge_anchor_time"),
            "planned_charge_start_time": result_payload.get("planned_charge_start_time"),
            "planned_charge_end_time": result_payload.get("planned_charge_end_time"),
            "planned_charge_limitation_reason": result_payload.get("planned_charge_limitation_reason"),
            "forecast_soc_7_percent": result_payload.get("forecast_soc_7_percent"),
            "hourly_soc_forecast_basis": result_payload.get("hourly_soc_forecast_basis"),
        },
    )
    return EnergyModelOutput(
        document=document,
        output_path=context.config.artifacts_dir / "night_charge_plan.json",
    )


