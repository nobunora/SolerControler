"""Connect admitted response models to the existing scheduled planning path."""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.energy_plan.battery_dispatch import dispatch_soc
from app.energy_plan.battery_planning import build_planning_battery_response
from app.energy_plan.battery_response import BatterySocResponse
from app.energy_plan.energy_model import compute_night_charge_target, to_dict
from app.energy_plan.hourly_pv_bias import PvBiasDay, PvBiasModel, ReplayErrors, forecast_weather_by_hour, select_hourly_pv_bias
from app.energy_plan.plan_quality import _soc_cost_model_from_env, _to_optional_float
from app.energy_plan.soc_cost import SocCostModel
from app.runtime.forced_charge_monitor import estimate_forced_charge_rate_percent_per_hour

if TYPE_CHECKING:
    from app.energy_plan.workflow import ConsumptionForecastBundle, EnergyModelContext, NightChargePreparation, PvForecastBundle


def _pv_replay(day: PvBiasDay, pv: dict[int, float], cost_model: SocCostModel) -> ReplayErrors:
    """Use each frozen day's response and observed interval-start state.

    Hourly forecast kWh is split equally into its two half-hours; this is an
    explicit evaluation approximation, not a claim of subhour forecast skill.
    """
    if day.battery_response_payload is None:
        raise ValueError("PV validation requires the contemporaneous battery response")
    response = BatterySocResponse.from_payload(day.battery_response_payload)
    rows = sorted(day.actual_rows, key=lambda row: row["dt"])
    soc_errors, buy_errors, fee_errors = [], [], []
    for previous, row in zip(rows, rows[1:]):
        hour = row["dt"].hour
        if not 7 <= hour < 23:
            continue
        if (row["dt"] - previous["dt"]).total_seconds() != 1800:
            raise ValueError("PV validation requires consecutive intervals")
        buy, sell, soc = dispatch_soc(soc=float(previous["soc"]),
            pv_kwh=pv[hour] / 2.0, load_kwh=day.hourly_load_kwh[hour] / 2.0,
            response=response, hour=hour, duration_hours=0.5)
        actual_buy, actual_sell, actual_soc = float(row["buy"]), float(row["sell"]), float(row["soc"])
        if any(not math.isfinite(v) for v in (actual_buy, actual_sell, actual_soc)):
            raise ValueError("PV validation requires finite meter observations")
        fee = cost_model.day_buy_cost_yen(buy) - sell * cost_model.sell_revenue_yen_per_kwh
        actual_fee = cost_model.day_buy_cost_yen(actual_buy) - actual_sell * cost_model.sell_revenue_yen_per_kwh
        soc_errors.append(abs(soc - actual_soc))
        buy_errors.append(abs(buy - actual_buy))
        fee_errors.append(abs(fee - actual_fee))
    if len(soc_errors) != 32:
        raise ValueError("PV validation requires all daytime half-hours and the preceding anchor")
    return ReplayErrors(sum(soc_errors) / 32, sum(buy_errors) / 32, sum(fee_errors) / 32)


def _previous_pv_model(payload: Any) -> PvBiasModel | None:
    if not isinstance(payload, dict):
        return None
    try:
        return PvBiasModel(
            {(int(row["hour"]), str(row["weather_class"])): float(row["delta_kwh"])
             for row in payload["deltas"]},
            tuple(str(day) for day in payload["training_days"]),
            datetime.fromisoformat(payload["validated_at"]), str(payload["source_sha256"]),
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def prepare_empirical_planning(
    context: EnergyModelContext, consumption: ConsumptionForecastBundle,
    pv: PvForecastBundle, night: NightChargePreparation, *, as_of: datetime,
    load_previous: bool,
) -> tuple[EnergyModelContext, PvForecastBundle, NightChargePreparation]:
    from app.energy_plan import estimate_sunset_hour
    from app.energy_plan.clear_ghi_adapter import _prepare_night_charge_from_hourly
    from app.energy_plan.financial_contract import apply_financial_contract

    if as_of.utcoffset() is None:
        raise ValueError("empirical planning requires an aware as_of")
    as_of = as_of.astimezone(ZoneInfo(context.config.timezone))
    previous = context.previous_calibration_state
    if load_previous:
        from app.operations.battery_calibration_state import load_previous_battery_calibration

        previous = load_previous_battery_calibration(target_date=context.target_date, as_of=as_of) or {}
    cost = apply_financial_contract(_soc_cost_model_from_env(
        battery_round_trip_efficiency=context.coefficients.battery_round_trip_efficiency,
        monthly_day_buy_kwh_before_target=_to_optional_float(night.monthly_day_buy_before_target.get("kwh")) or 0.0,
        expected_rest_of_month_day_buy_kwh=_to_optional_float(night.expected_rest_of_month_day_buy.get("kwh")) or 0.0,
    ), context.target_date)
    weather_rows = consumption.training_diagnostics.get("rows")
    calibration = build_planning_battery_response(
        rows=context.battery_training_rows if context.battery_training_rows is not None else context.rows,
        coefficients=context.coefficients, capacity_kwh=night.result.effective_capacity_kwh,
        forecast=context.forecast, as_of=as_of, cost_model=cost,
        previous_payload=previous.get("battery_calibration"),
        weather_observations=weather_rows if isinstance(weather_rows, list) else None,
    )
    context = replace(context, battery_response=calibration.response,
                      battery_calibration=calibration.diagnostics, previous_calibration_state=previous)
    if pv.ideal_max_provenance is not None and pv.hourly_pv_max_kwh is not None:
        if pv.hourly_pv_training_days is not None:
            from app.energy_plan.pv_bias_training import MODEL_ID as TRAINING_MODEL_ID, select_training_bias
            saved = previous.get("hourly_pv_bias_model")
            fingerprint = pv.ideal_max_provenance.get("calibration_pack", {}).get("geometry_fingerprint")
            compatible = (isinstance(saved, dict) and saved.get("model_id") == TRAINING_MODEL_ID
                          and saved.get("geometry_fingerprint") == fingerprint
                          and saved.get("calibration_sha256") == pv.ideal_max_provenance.get("calibration_sha256"))
            corrected, diagnostics, model = select_training_bias(
                days=pv.hourly_pv_training_days, coefficients=pv.pv_coefficient_history,
                as_of=as_of, target_date=context.target_date, forecast_pv=pv.hourly_pv_kwh,
                maximum=pv.hourly_pv_max_kwh, weather=forecast_weather_by_hour(context.forecast),
                economic_days=pv.hourly_pv_bias_days, evaluate_day=lambda day, values: _pv_replay(day, values, cost),
                previous_model=_previous_pv_model(saved) if compatible else None)
        else:
            saved_pv_model = previous.get("hourly_pv_bias_model")
            if pv.ideal_max_provenance.get("model_id") == "physical_pv_ideal_max_local_v1":
                if (not isinstance(saved_pv_model, dict)
                        or saved_pv_model.get("geometry_fingerprint") != pv.ideal_max_provenance.get("calibration_pack", {}).get("geometry_fingerprint")
                        or saved_pv_model.get("calibration_sha256") != pv.ideal_max_provenance.get("calibration_sha256")):
                    saved_pv_model = None
            corrected, diagnostics, model = select_hourly_pv_bias(
                days=pv.hourly_pv_bias_days, as_of=as_of, target_date=context.target_date,
                forecast_pv=pv.hourly_pv_kwh, maximum=pv.hourly_pv_max_kwh,
                weather_by_hour=forecast_weather_by_hour(context.forecast),
                evaluate_day=lambda day, values: _pv_replay(day, values, cost),
                previous_model=_previous_pv_model(saved_pv_model),
            )
        diagnostics["evaluation"] = "frozen_response_observed_start_soc_uniform_halfhour_current_contract_revaluation"
        old_bias = pv.ideal_max_provenance.get("hourly_pv_bias", {})
        diagnostics["coverage"] = old_bias.get("coverage", {}) if isinstance(old_bias, dict) else {}
        model_payload = None if model is None else {
            "deltas": [{"hour": hour, "weather_class": weather, "delta_kwh": value}
                       for (hour, weather), value in sorted(model.deltas.items())],
            "training_days": list(model.training_days), "validated_at": model.validated_at.isoformat(),
            "source_sha256": model.source_sha256,
            "model_id": diagnostics["model_id"],
            "geometry_fingerprint": pv.ideal_max_provenance.get("calibration_pack", {}).get("geometry_fingerprint"),
            "calibration_sha256": pv.ideal_max_provenance.get("calibration_sha256"),
        }
        trace = {**pv.ideal_max_provenance, "hourly_pv_bias": diagnostics,
                 "hourly_pv_bias_model": model_payload,
                 "final_hourly_factors": {str(h): corrected[h] / pv.hourly_pv_max_kwh[h]
                                          if pv.hourly_pv_max_kwh[h] > 0 else 0.0 for h in range(24)},
                 "hourly_pv_kwh": {str(h): value for h, value in corrected.items()}}
        pv = replace(pv, hourly_pv_kwh=corrected, sunset_hour=estimate_sunset_hour(corrected),
                     ideal_max_provenance=trace)
        night = _prepare_night_charge_from_hourly(context, consumption, pv)
    rate = estimate_forced_charge_rate_percent_per_hour(context.csv_paths)
    raw_rate = rate.get("percent_per_hour")
    percent_per_hour = float(raw_rate) if isinstance(raw_rate, (int, float)) else None
    night.result = compute_night_charge_target(context.coefficients, night.inputs,
        battery_response=calibration.response, night_charge_rate_percent_per_hour=percent_per_hour)
    night.result_payload = to_dict(night.result)
    night.charge_rate_info = rate
    return context, pv, night
