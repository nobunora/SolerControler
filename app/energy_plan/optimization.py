"""Scenario preparation shared by Energy Plan SOC optimizers."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any

from app.energy_plan.energy_model import DaytimeSocOptimizationResult
from app.energy_plan.energy_model import optimize_target_soc_for_daytime, to_dict
from app.energy_plan.soc_cost import DEFAULT_SIGMA_BUCKETS, ForecastScenario, PvForecastUncertainty, SigmaBucket


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
