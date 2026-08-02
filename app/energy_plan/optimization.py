"""Scenario preparation shared by Energy Plan SOC optimizers."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Callable, TYPE_CHECKING

from app.energy_plan.energy_model import DaytimeSocOptimizationResult
from app.energy_plan.energy_model import optimize_target_soc_for_daytime, to_dict
from app.energy_plan.decision_feedback import load_soc_decision_prior_from_firestore
from app.energy_plan.soc_cost import SocCostModel, SocOptimizationRequest, optimize_soc_request, to_plain_dict
from app.energy_plan.soc_cost import DEFAULT_SIGMA_BUCKETS, ForecastScenario, PvForecastUncertainty, SigmaBucket

if TYPE_CHECKING:
    from app.energy_plan.soc_constraints import SocConstraintSet
    from app.energy_plan.workflow import EnergyModelContext, NightChargePreparation, PvForecastBundle


@dataclass(frozen=True)
class LegacyOptimizationDecision:
    result: DaytimeSocOptimizationResult | None
    payload: dict[str, object] | None


@dataclass
class OptimizationDecision:
    result_payload: dict[str, Any]
    optimization_payload: dict[str, object] | None
    cost_optimization_payload: dict[str, object] | None


def run_legacy_optimizer(
    *, capacity_kwh: float, soc_now_percent: float, reserve_soc_percent: float,
    battery_round_trip_efficiency: float, hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float], sunset_hour: int, soc_step_percent: float,
    target_peak_soc_percent: float, buy_tolerance_kwh: float, sell_tolerance_kwh: float,
    max_target_soc_percent: float, morning_headroom: dict[str, object],
    daytime_net_surplus: dict[str, object], historical_soc_gain: dict[str, object],
    hourly_weather_shape: dict[str, object], physical_diagnostics: dict[str, object],
) -> LegacyOptimizationDecision:
    result = optimize_target_soc_for_daytime(
        effective_capacity_kwh_value=capacity_kwh, soc_now_percent=soc_now_percent,
        reserve_soc_percent=reserve_soc_percent, battery_round_trip_efficiency=battery_round_trip_efficiency,
        hourly_load_kwh=hourly_load_kwh, hourly_pv_kwh=hourly_pv_kwh, sunset_hour=sunset_hour,
        soc_step_percent=soc_step_percent, target_peak_soc_percent=target_peak_soc_percent,
        buy_tolerance_kwh=buy_tolerance_kwh, sell_tolerance_kwh=sell_tolerance_kwh,
        max_target_soc_percent=max_target_soc_percent,
    )
    if result is None:
        return LegacyOptimizationDecision(None, None)
    payload: dict[str, object] = {**to_dict(result), "objective": "avoid_daytime_buy_and_sell_then_peak_soc_near_target", "target_peak_soc_percent": target_peak_soc_percent, "buy_tolerance_kwh": buy_tolerance_kwh, "sell_tolerance_kwh": sell_tolerance_kwh, "target_soc_7_percent_after_peak_objective": result.target_soc_7_percent, "required_night_charge_kwh_after_peak_objective": result.required_night_charge_kwh, "legacy_pv_headroom_cap": {"applied": False, "reason": "replaced_by_peak_soc_objective"}, "morning_pv_headroom_guard": morning_headroom, "daytime_net_surplus_headroom_guard": daytime_net_surplus, "historical_daytime_soc_gain_guard": historical_soc_gain, "sunset_hour": sunset_hour, "hourly_weather_pv_shape": hourly_weather_shape, "pv_physical_forecast": physical_diagnostics, "hourly_load_forecast_kwh": {str(key): round(value, 4) for key, value in sorted(hourly_load_kwh.items())}, "hourly_pv_forecast_kwh": {str(key): round(value, 4) for key, value in sorted(hourly_pv_kwh.items())}}
    return LegacyOptimizationDecision(result, payload)


def cost_max_target_soc(
    *, respect_morning_headroom: bool, apply_pv_headroom_caps: bool,
    morning_headroom: dict[str, object], daytime_net_surplus: dict[str, object],
    historical_soc_gain: dict[str, object],
) -> float:
    """Return the optimizer's SOC ceiling after applicable safety guards."""
    maximum = 100.0
    if respect_morning_headroom and apply_pv_headroom_caps:
        maximum = _cap_or_unbounded(morning_headroom.get("cap_target_soc_percent"))
    for guard in (daytime_net_surplus, historical_soc_gain):
        if apply_pv_headroom_caps and guard.get("applied"):
            maximum = min(maximum, _cap_or_unbounded(guard.get("cap_target_soc_percent")))
    return maximum


def apply_uncertainty_floor(uncertainty: PvForecastUncertainty) -> PvForecastUncertainty:
    floor = max(0.0, _env_float("SOC_COST_PV_UNCERTAINTY_STD_FLOOR", 0.30))
    std = max(uncertainty.std_multiplier, floor)
    return PvForecastUncertainty(uncertainty.mean_multiplier, std, std * std, uncertainty.sample_count, uncertainty.source if std == uncertainty.std_multiplier else f"{uncertainty.source}+std_floor")


def sigma_buckets() -> tuple[SigmaBucket, ...]:
    if not _env_bool("SOC_COST_UPSIDE_SCENARIO_ENABLED", False):
        return DEFAULT_SIGMA_BUCKETS
    probability = _env_float_clamped("SOC_COST_UPSIDE_SCENARIO_PROBABILITY", 0.08, min_value=0.0, max_value=0.5)
    if probability <= 0:
        return DEFAULT_SIGMA_BUCKETS
    total = sum(max(0.0, bucket.probability) for bucket in DEFAULT_SIGMA_BUCKETS) or 1.0
    base = tuple(SigmaBucket(bucket.label, max(0.0, bucket.probability) / total * (1.0 - probability), bucket.z_value) for bucket in DEFAULT_SIGMA_BUCKETS)
    return base + (SigmaBucket("pv_upside_guard", probability, _env_float("SOC_COST_UPSIDE_SCENARIO_Z", 3.0)),)


def weather_upside_probability(forecast: dict[str, object]) -> float:
    if not _env_bool("SOC_COST_WEATHER_UPSIDE_SCENARIO_ENABLED", True):
        return 0.0
    if str(forecast.get("weather_class") or "").strip().lower() not in {"cloudy", "rain", "rainy"}:
        return 0.0
    return _env_float_clamped("SOC_COST_WEATHER_UPSIDE_SCENARIO_PROBABILITY", 0.12, min_value=0.0, max_value=0.5)


def load_scenarios(forecast_correction: dict[str, object] | None = None) -> tuple[ForecastScenario, ...] | None:
    if not _env_bool("SOC_COST_LOAD_SCENARIOS_ENABLED", True):
        return None
    adaptive = (forecast_correction or {}).get("load_scenarios")
    if isinstance(adaptive, list):
        scenarios: list[ForecastScenario] = []
        for item in adaptive:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            probability = _optional_float(item.get("probability"))
            multiplier = _optional_float(item.get("multiplier"))
            if label and probability is not None and probability > 0 and multiplier is not None and multiplier > 0:
                scenarios.append(ForecastScenario(label, probability, 1.0, multiplier))
        if scenarios:
            return tuple(scenarios)
    low = _env_float_clamped("SOC_COST_LOAD_LOW_PROBABILITY", 0.20, min_value=0.0, max_value=1.0)
    high = _env_float_clamped("SOC_COST_LOAD_HIGH_PROBABILITY", 0.20, min_value=0.0, max_value=1.0)
    return (
        ForecastScenario("load_low", low, 1.0, _env_float("SOC_COST_LOAD_LOW_MULTIPLIER", 0.82)),
        ForecastScenario("load_mid", max(0.0, 1.0 - low - high), 1.0, _env_float("SOC_COST_LOAD_MID_MULTIPLIER", 1.00)),
        ForecastScenario("load_high", high, 1.0, _env_float("SOC_COST_LOAD_HIGH_MULTIPLIER", 1.18)),
    )


def paired_scenarios(forecast_correction: dict[str, object] | None = None) -> tuple[ForecastScenario, ...] | None:
    if not _env_bool("SOC_COST_PAIRED_SCENARIOS_ENABLED", True):
        return None
    paired = (forecast_correction or {}).get("paired_scenarios")
    if not isinstance(paired, list):
        return None
    scenarios: list[ForecastScenario] = []
    for item in paired:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        probability = _optional_float(item.get("probability"))
        pv_multiplier = _optional_float(item.get("pv_multiplier"))
        load_multiplier = _optional_float(item.get("load_multiplier"))
        if label and probability is not None and probability > 0 and pv_multiplier is not None and pv_multiplier > 0 and load_multiplier is not None and load_multiplier > 0:
            scenarios.append(ForecastScenario(label, probability, pv_multiplier, load_multiplier))
    return tuple(scenarios) if len(scenarios) >= 3 else None




def soc_cost_model_from_env(
    *,
    battery_round_trip_efficiency: float,
    monthly_day_buy_kwh_before_target: float = 0.0,
    expected_rest_of_month_day_buy_kwh: float = 0.0,
) -> SocCostModel:
    """Prices intentionally live in one place so the objective is easy to audit."""

    day_rate = _env_float(
        "SOC_COST_DAY_BUY_RATE_YEN_PER_KWH",
        _env_float("NIGHT8_DAY_RATE_TIER2_YEN", _env_float("DAY_RATE_YEN_PER_KWH", 39.10)),
    )
    night_rate = _env_float("SOC_COST_NIGHT_RATE_YEN_PER_KWH", _env_float("NIGHT8_NIGHT_RATE_YEN", 31.0))
    sell_value_ratio = _env_float_clamped("SOC_COST_SELL_VALUE_RATIO", 0.0, min_value=0.0, max_value=1.0)
    day_buy_penalty = max(0.0, _env_float("SOC_COST_DAY_BUY_PENALTY_FACTOR", 1.0))
    export_value_mode = os.getenv("SOC_EXPORT_VALUE_MODE", "penalty").strip().lower() or "penalty"
    sell_revenue = max(0.0, _env_float("SOC_SELL_REVENUE_YEN_PER_KWH", 0.0))
    export_contract_status = os.getenv("SOC_EXPORT_CONTRACT_STATUS", "").strip().lower()
    valid_contract_statuses = {"active", "inactive", "unknown"}
    if export_contract_status not in valid_contract_statuses:
        raise RuntimeError(
            "SOC_EXPORT_CONTRACT_STATUS must be active, inactive, or unknown"
        )
    if export_contract_status == "active" and export_value_mode != "revenue":
        raise RuntimeError(
            "SOC_EXPORT_VALUE_MODE must be revenue when SOC_EXPORT_CONTRACT_STATUS is active"
        )
    if export_contract_status == "inactive" and export_value_mode not in {"penalty", "neutral"}:
        raise RuntimeError(
            "SOC_EXPORT_VALUE_MODE must be penalty or neutral when SOC_EXPORT_CONTRACT_STATUS is inactive"
        )
    if export_contract_status == "unknown" and export_value_mode != "neutral":
        raise RuntimeError(
            "SOC_EXPORT_VALUE_MODE must be neutral when SOC_EXPORT_CONTRACT_STATUS is unknown"
        )
    if export_value_mode == "revenue" and sell_revenue <= 0:
        raise RuntimeError(
            "SOC_SELL_REVENUE_YEN_PER_KWH must be positive when SOC_EXPORT_VALUE_MODE is revenue"
        )
    charge_efficiency = _env_float(
        "SOC_COST_USABLE_CHARGE_EFFICIENCY",
        _env_float("SOC_COST_CHARGE_EFFICIENCY", battery_round_trip_efficiency),
    )
    sell_loss_raw = os.getenv("SOC_COST_SELL_OPPORTUNITY_LOSS_YEN_PER_KWH", "").strip()
    sell_loss_override = (
        _env_float("SOC_COST_SELL_OPPORTUNITY_LOSS_YEN_PER_KWH", 0.0)
        if sell_loss_raw
        else _env_float("SOC_EXPORT_PENALTY_YEN_PER_KWH", max(0.0, day_rate))
        if export_value_mode == "penalty"
        else None
    )
    tariff_mode = os.getenv("COST_TARIFF_MODE", "night8_tiered").strip().lower() or "night8_tiered"
    if not _env_bool("SOC_TIERED_DAY_BUY_COST_ENABLED", True):
        tariff_mode = "flat"
    return SocCostModel(
        day_buy_rate_yen_per_kwh=max(0.0, day_rate),
        night_buy_rate_yen_per_kwh=max(0.0, night_rate),
        charge_efficiency=max(0.01, charge_efficiency),
        sell_value_ratio=sell_value_ratio,
        day_buy_penalty_factor=day_buy_penalty,
        sell_opportunity_loss_yen_per_kwh_override=(
            max(0.0, sell_loss_override) if sell_loss_override is not None else None
        ),
        export_value_mode=export_value_mode,
        sell_revenue_yen_per_kwh=sell_revenue,
        tariff_mode=tariff_mode,
        monthly_day_buy_kwh_before_target=max(
            0.0,
            _env_float("SOC_MONTHLY_DAY_BUY_KWH_BEFORE_TARGET", monthly_day_buy_kwh_before_target),
        ),
        day_tier1_upper_kwh=_env_float("NIGHT8_DAY_TIER1_UPPER_KWH", 90.0),
        day_tier2_upper_kwh=_env_float("NIGHT8_DAY_TIER2_UPPER_KWH", 230.0),
        day_tier1_rate_yen_per_kwh=_env_float("NIGHT8_DAY_RATE_TIER1_YEN", 31.80),
        day_tier2_rate_yen_per_kwh=_env_float("NIGHT8_DAY_RATE_TIER2_YEN", 39.10),
        day_tier3_rate_yen_per_kwh=_env_float("NIGHT8_DAY_RATE_TIER3_YEN", 43.62),
        monthly_tier_landing_enabled=_env_bool("SOC_MONTHLY_TIER_LANDING_ENABLED", False),
        expected_rest_of_month_day_buy_kwh=max(
            0.0,
            _env_float("SOC_EXPECTED_REST_OF_MONTH_DAY_BUY_KWH", expected_rest_of_month_day_buy_kwh),
        ),
        tier1_underuse_penalty_yen_per_kwh=max(
            0.0,
            _env_float("SOC_TIER1_UNDERUSE_PENALTY_YEN_PER_KWH", 0.2),
        ),
        tier1_crossing_penalty_yen_per_kwh=max(
            0.0,
            _env_float("SOC_TIER1_CROSSING_PENALTY_YEN_PER_KWH", 30.0),
        ),
        tier2_extra_penalty_yen_per_kwh=max(
            0.0,
            _env_float("SOC_TIER2_EXTRA_PENALTY_YEN_PER_KWH", 8.0),
        ),
        tier3_extra_penalty_yen_per_kwh=max(
            0.0,
            _env_float("SOC_TIER3_EXTRA_PENALTY_YEN_PER_KWH", 20.0),
        ),
    )



def run_current_optimizer(
    context: EnergyModelContext,
    night_charge: NightChargePreparation,
    pv_forecast: PvForecastBundle,
    constraints: SocConstraintSet,
    legacy: LegacyOptimizationDecision,
    *,
    optimize_request: Callable[[SocOptimizationRequest], Any] = optimize_soc_request,
    prior_loader: Callable[..., dict[str, object] | None] | None = None,
    target_features_builder: Callable[..., dict[str, object]] | None = None,
) -> OptimizationDecision:
    from app.energy_plan.workflow import (
        _soc_cost_model_from_env,
        _to_optional_float,
        _soc_decision_target_features,
    )
    config = context.config
    result_payload = dict(night_charge.result_payload)
    optimization_payload: dict[str, object] | None = None
    cost_payload: dict[str, object] | None = None
    if config.cost_optimization_enabled:
        uncertainty = apply_uncertainty_floor(pv_forecast.uncertainty)
        cost_model = _soc_cost_model_from_env(
            battery_round_trip_efficiency=context.coefficients.battery_round_trip_efficiency,
            monthly_day_buy_kwh_before_target=(
                _to_optional_float(night_charge.monthly_day_buy_before_target.get("kwh"))
                or 0.0
            ),
            expected_rest_of_month_day_buy_kwh=(
                _to_optional_float(night_charge.expected_rest_of_month_day_buy.get("kwh"))
                or 0.0
            ),
        )
        respect_guard = config.cost_respect_morning_headroom_cap
        from app.energy_plan.optimization import cost_max_target_soc

        cost_max_soc = cost_max_target_soc(
            respect_morning_headroom=respect_guard,
            apply_pv_headroom_caps=constraints.apply_pv_headroom_caps,
            morning_headroom=constraints.morning_headroom,
            daytime_net_surplus=constraints.daytime_net_surplus,
            historical_soc_gain=constraints.historical_soc_gain,
        )
        load_scenario_values = load_scenarios(pv_forecast.correction)
        paired_scenario_values = paired_scenarios(pv_forecast.correction)
        weather_upside_probability_value = weather_upside_probability(
            context.forecast
        )
        peak_penalty = pv_forecast.correction.get("peak_penalty", {})
        peak_target_soc = _to_optional_float(
            peak_penalty.get("target_peak_soc_percent")
            if isinstance(peak_penalty, dict)
            else None
        )
        peak_penalty_factor = (
            _to_optional_float(
                peak_penalty.get("applied_factor")
                if isinstance(peak_penalty, dict)
                else None
            )
            or 0.0
        )
        prior_reader = prior_loader or load_soc_decision_prior_from_firestore
        target_builder = target_features_builder or _soc_decision_target_features
        prior = prior_reader(
            target_date=context.target_date,
            target_features=target_builder(
                forecast=context.forecast,
                hourly_load_forecast=pv_forecast.hourly_load_kwh,
                hourly_pv_forecast=pv_forecast.hourly_pv_kwh,
                final_pv_forecast_source=pv_forecast.source,
            ),
        )
        prior_regret_curve: dict[float | str, float] | None = None
        raw_prior_curve = prior.get("regret_yen_by_soc") if isinstance(prior, dict) else None
        if isinstance(prior, dict) and prior.get("applied") and isinstance(raw_prior_curve, dict):
            prior_regret_curve = {
                key: float(value)
                for key, value in raw_prior_curve.items()
                if isinstance(key, (str, int, float)) and isinstance(value, (int, float)) and not isinstance(value, bool)
            }
        prior_weight = (
            _to_optional_float(prior.get("weight") if isinstance(prior, dict) else None)
            or 0.0
        )
        prior_max_penalty = (
            _to_optional_float(
                prior.get("max_penalty_yen") if isinstance(prior, dict) else None
            )
            or 0.0
        )
        optimized = optimize_request(SocOptimizationRequest(
            capacity_kwh=night_charge.result.effective_capacity_kwh,
            soc_now_percent=context.latest_soc_percent,
            reserve_soc_percent=night_charge.inputs.reserve_soc_percent,
            hourly_load_kwh=pv_forecast.hourly_load_kwh,
            hourly_pv_kwh=pv_forecast.hourly_pv_kwh,
            uncertainty=uncertainty,
            cost_model=cost_model,
            soc_step_percent=config.cost_soc_step_percent,
            max_target_soc_percent=cost_max_soc,
            sigma_buckets=sigma_buckets(),
            min_pv_multiplier=config.cost_min_pv_multiplier,
            max_pv_multiplier=config.cost_max_pv_multiplier,
            load_scenarios=load_scenario_values,
            joint_scenarios=paired_scenario_values,
            weather_upside_probability=weather_upside_probability_value,
            weather_upside_z=config.cost_weather_upside_z,
            peak_soc_target_percent=peak_target_soc,
            peak_soc_unmet_penalty_yen_per_kwh=(
                cost_model.day_buy_rate_yen_per_kwh * max(0.0, peak_penalty_factor)
            ),
            expected_overnight_discharge_kwh=night_charge.expected_overnight_discharge_kwh,
            decision_prior_regret_yen_by_soc=prior_regret_curve,
            decision_prior_weight=prior_weight,
            decision_prior_max_penalty_yen=prior_max_penalty,
        ))
        if optimized is not None:
            cost_payload = {
                **to_plain_dict(optimized),
                "objective": "minimize_night_charge_cost_plus_expected_day_buy_cost_plus_expected_sell_opportunity_loss",
                "morning_pv_headroom_guard": constraints.morning_headroom,
                "daytime_net_surplus_headroom_guard": constraints.daytime_net_surplus,
                "historical_daytime_soc_gain_guard": constraints.historical_soc_gain,
                "respect_morning_headroom_guard": bool(
                    respect_guard and constraints.apply_pv_headroom_caps
                ),
                "pv_headroom_cap_policy": {
                    "apply_caps": constraints.apply_pv_headroom_caps,
                    "reason": (
                        "existing_forecast_selected"
                        if constraints.apply_pv_headroom_caps
                        else "physical_pv_selected"
                    ),
                    "selected_method": pv_forecast.selected_method,
                },
                "max_target_soc_percent_after_guards": cost_max_soc,
                "forecast_correction": pv_forecast.correction.get("rationale", {}),
                "pv_physical_forecast": pv_forecast.physical_diagnostics,
                "hourly_weather_pv_shape": pv_forecast.hourly_weather_shape,
                "soc_decision_feedback_prior": prior,
                "monthly_day_buy_before_target": night_charge.monthly_day_buy_before_target,
                "expected_rest_of_month_day_buy": night_charge.expected_rest_of_month_day_buy,
                "soc_cost_risk": {
                    "expected_day_buy_kwh": optimized.expected_day_buy_kwh_risk,
                    "expected_sell_kwh": optimized.expected_sell_kwh_risk,
                    "worst_case_day_buy_kwh": optimized.worst_case_day_buy_kwh,
                    "worst_case_sell_kwh": optimized.worst_case_sell_kwh,
                    "buy_risk": optimized.buy_risk,
                    "sell_risk": optimized.sell_risk,
                    "peak_unmet_penalty_factor": peak_penalty_factor,
                    "export_value_mode": cost_model.export_value_mode,
                    "sell_revenue_yen_per_kwh": cost_model.sell_revenue_yen_per_kwh,
                    "sell_opportunity_loss_yen_per_kwh": cost_model.sell_opportunity_loss_yen_per_kwh,
                    "tariff_mode": cost_model.tariff_mode,
                    "monthly_day_buy_kwh_before_target": cost_model.monthly_day_buy_kwh_before_target,
                    "expected_rest_of_month_day_buy_kwh": cost_model.expected_rest_of_month_day_buy_kwh,
                    "monthly_tier_landing_enabled": cost_model.monthly_tier_landing_enabled,
                    "monthly_tier_landing_penalty_yen": optimized.expected_monthly_tier_landing_penalty_yen,
                    "projected_monthly_day_buy_kwh": round(
                        cost_model.monthly_day_buy_kwh_before_target
                        + cost_model.expected_rest_of_month_day_buy_kwh
                        + optimized.expected_day_buy_kwh,
                        4,
                    ),
                    "monthly_tier_landing_penalties": {
                        "tier1_underuse_yen_per_kwh": cost_model.tier1_underuse_penalty_yen_per_kwh,
                        "tier1_crossing_yen_per_kwh": cost_model.tier1_crossing_penalty_yen_per_kwh,
                        "tier2_extra_yen_per_kwh": cost_model.tier2_extra_penalty_yen_per_kwh,
                        "tier3_extra_yen_per_kwh": cost_model.tier3_extra_penalty_yen_per_kwh,
                    },
                    "day_buy_tiers": {
                        "tier1_upper_kwh": cost_model.day_tier1_upper_kwh,
                        "tier2_upper_kwh": cost_model.day_tier2_upper_kwh,
                        "tier1_rate_yen_per_kwh": cost_model.day_tier1_rate_yen_per_kwh,
                        "tier2_rate_yen_per_kwh": cost_model.day_tier2_rate_yen_per_kwh,
                        "tier3_rate_yen_per_kwh": cost_model.day_tier3_rate_yen_per_kwh,
                    },
                    "scenario_count": len(optimized.forecast_scenarios),
                    "scenario_method": (
                        "smoothed_paired_pv_load_residuals"
                        if paired_scenario_values
                        else "pv_sigma_x_load_scenarios_with_weather_upside"
                    ),
                    "weather_upside_probability": weather_upside_probability_value,
                    "weather_upside_z": config.cost_weather_upside_z,
                },
                "hourly_load_forecast_kwh": {
                    str(k): round(v, 4)
                    for k, v in sorted(pv_forecast.hourly_load_kwh.items())
                },
                "hourly_pv_forecast_kwh": {
                    str(k): round(v, 4)
                    for k, v in sorted(pv_forecast.hourly_pv_kwh.items())
                },
                "legacy_peak_objective": legacy.payload,
            }
            result_payload["target_soc_7_percent_base"] = result_payload.get(
                "target_soc_7_percent"
            )
            result_payload["required_night_charge_kwh_base"] = result_payload.get(
                "required_night_charge_kwh"
            )
            result_payload.update(
                {
                    "target_soc_7_percent": optimized.target_soc_7_percent,
                    "required_night_charge_kwh": optimized.required_night_charge_kwh,
                    "target_soc_7_percent_cost_optimized": optimized.target_soc_7_percent,
                    "required_night_charge_kwh_cost_optimized": optimized.required_night_charge_kwh,
                    "soc_expected_total_cost_yen": optimized.total_expected_cost_yen,
                    "soc_expected_day_buy_kwh": optimized.expected_day_buy_kwh,
                    "soc_expected_sell_kwh": optimized.expected_sell_kwh,
                    "soc_expected_peak_unmet_kwh": optimized.expected_peak_unmet_kwh,
                    "soc_expected_peak_unmet_cost_yen": optimized.expected_peak_unmet_cost_yen,
                }
            )
            optimization_payload = cost_payload

    if optimization_payload is None and legacy.payload is not None and legacy.result is not None:
        result_payload["target_soc_7_percent_base"] = result_payload.get(
            "target_soc_7_percent"
        )
        result_payload["required_night_charge_kwh_base"] = result_payload.get(
            "required_night_charge_kwh"
        )
        result_payload["target_soc_7_percent"] = legacy.result.target_soc_7_percent
        result_payload["required_night_charge_kwh"] = legacy.result.required_night_charge_kwh
        optimization_payload = legacy.payload
    return OptimizationDecision(
        result_payload=result_payload,
        optimization_payload=optimization_payload,
        cost_optimization_payload=cost_payload,
    )



def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    return default if not raw else raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if math.isfinite(value) else default


def _env_float_clamped(name: str, default: float, *, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, _env_float(name, default)))


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _cap_or_unbounded(value: object) -> float:
    parsed = _optional_float(value)
    return max(0.0, min(100.0, parsed)) if parsed is not None else 100.0
