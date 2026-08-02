from __future__ import annotations

import os
from typing import Any, cast

from app.energy_plan.soc_cost import PvForecastUncertainty, SocCostModel
def _to_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return None


def _soc_cap_or_unbounded(value: object) -> float:
    cap = _to_optional_float(value)
    return 100.0 if cap is None else cap


def _to_optional_int(value: object) -> int | None:
    as_float = _to_optional_float(value)
    if as_float is None:
        return None
    return int(as_float)


# readable-code-audit: skip DUP-01 — unknown non-empty values intentionally mean false here, unlike the shared helper which returns its default for unknown values.
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


# readable-code-audit: skip DUP-01 — malformed numeric planning settings must fall back locally instead of raising like the strict shared parser.
def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# readable-code-audit: skip DUP-01 — this clamp deliberately keeps Energy Plan's malformed-value fallback behavior from `_env_float`.
def _env_float_clamped(name: str, default: float, *, min_value: float, max_value: float) -> float:
    value = _env_float(name, default)
    return max(min_value, min(max_value, value))


def _pv_uncertainty_from_forecast(pv_forecast: dict[str, object] | None) -> PvForecastUncertainty:
    """Return the PV error distribution used by the SOC cost optimizer."""

    default_mean = _env_float("PV_FORECAST_ERROR_RATIO_MEAN", 1.0)
    default_std = max(0.0, _env_float("PV_FORECAST_ERROR_RATIO_STD", 0.30))
    default = PvForecastUncertainty(
        mean_multiplier=max(0.0, default_mean),
        std_multiplier=default_std,
        variance_multiplier=default_std * default_std,
        sample_count=0,
        source="env_default",
    )
    if not isinstance(pv_forecast, dict):
        return default
    calibration = pv_forecast.get("calibration")
    if not isinstance(calibration, dict):
        return default
    distribution = calibration.get("forecast_error_distribution")
    if not isinstance(distribution, dict):
        return default

    min_samples = int(_env_float("PV_FORECAST_ERROR_MIN_SAMPLE_DAYS", 5.0))
    sample_count = int(_to_optional_float(distribution.get("sample_count")) or 0)
    if sample_count < min_samples:
        return PvForecastUncertainty(
            mean_multiplier=default.mean_multiplier,
            std_multiplier=default.std_multiplier,
            variance_multiplier=default.variance_multiplier,
            sample_count=sample_count,
            source=f"{distribution.get('source') or 'calibration'}:insufficient_samples",
        )

    mean = _to_optional_float(distribution.get("mean_multiplier"))
    std = _to_optional_float(distribution.get("std_multiplier"))
    variance = _to_optional_float(distribution.get("variance_multiplier"))
    if mean is None or std is None:
        return default
    std = max(0.0, std)
    if variance is None:
        variance = std * std
    return PvForecastUncertainty(
        mean_multiplier=max(0.0, mean),
        std_multiplier=std,
        variance_multiplier=max(0.0, variance),
        sample_count=sample_count,
        source=str(distribution.get("source") or "calibration"),
    )


def _physical_pv_uncertainty_from_diagnostics(diagnostics: dict[str, object]) -> PvForecastUncertainty:
    data_quality = diagnostics.get("data_quality")
    sample_count = 0
    if isinstance(data_quality, dict):
        sample_count = int(_to_optional_float(data_quality.get("global_days")) or 0)
    std = max(0.0, _env_float("PHYSICAL_PV_FORECAST_ERROR_RATIO_STD", _env_float("PV_FORECAST_ERROR_RATIO_STD", 0.30)))
    method = str(diagnostics.get("selected_method") or "physical")
    return PvForecastUncertainty(
        mean_multiplier=1.0,
        std_multiplier=std,
        variance_multiplier=std * std,
        sample_count=sample_count,
        source=f"{method}_neutral_mean",
    )


def _selected_pv_uncertainty(
    *,
    physical_pv_selected: bool,
    physical_pv_diagnostics: dict[str, object],
    pv_array_forecast: dict[str, object] | None,
) -> PvForecastUncertainty:
    if physical_pv_selected:
        return _physical_pv_uncertainty_from_diagnostics(physical_pv_diagnostics)
    return _pv_uncertainty_from_forecast(pv_array_forecast if isinstance(pv_array_forecast, dict) else None)


def _soc_decision_target_features(
    *,
    forecast: dict[str, object],
    hourly_load_forecast: dict[int, float],
    hourly_pv_forecast: dict[int, float],
    final_pv_forecast_source: str,
) -> dict[str, object]:
    return {
        "forecast_pv_kwh": round(sum(max(0.0, value) for value in hourly_pv_forecast.values()), 4),
        "forecast_load_kwh": round(
            sum(max(0.0, value) for hour, value in hourly_load_forecast.items() if 7 <= int(hour) < 23),
            4,
        ),
        "forecast_shortwave_radiation_sum_mj_m2": _to_optional_float(
            forecast.get("shortwave_radiation_sum_mj_m2")
        ),
        "forecast_temp_c": _to_optional_float(forecast.get("temp_c")),
        "weather_class": forecast.get("weather_class"),
        "final_pv_forecast_source": final_pv_forecast_source,
    }


# readable-code-audit: skip STRUCT-04 — tariff and optimizer settings are read together to create one internally consistent cost model
def _soc_cost_model_from_env(
    battery_round_trip_efficiency: float,
    monthly_day_buy_kwh_before_target: float = 0.0,
    expected_rest_of_month_day_buy_kwh: float = 0.0,
) -> SocCostModel:
    from app.energy_plan.optimization import soc_cost_model_from_env

    return soc_cost_model_from_env(
        battery_round_trip_efficiency=battery_round_trip_efficiency,
        monthly_day_buy_kwh_before_target=monthly_day_buy_kwh_before_target,
        expected_rest_of_month_day_buy_kwh=expected_rest_of_month_day_buy_kwh,
    )
def _build_plan_quality(
    *,
    forecast: dict[str, object],
    optimization_payload: dict[str, object] | None,
    result_payload: dict[str, object],
) -> dict[str, object]:
    reasons: list[str] = []
    source = str(forecast.get("source") or "")
    status = "normal"
    should_apply = True
    conservative = False

    if source == "date-only-fallback":
        status = "forecast_fallback"
        conservative = True
        reasons.append("daily_forecast_api_failed")
    elif source == "env-override":
        reasons.append("forecast_env_override")

    if forecast.get("daily_forecast_error"):
        status = "forecast_fallback"
        conservative = True
        reasons.append("daily_forecast_error_present")

    if not forecast.get("date"):
        status = "partial_data"
        should_apply = False
        conservative = True
        reasons.append("missing_forecast_date")

    if result_payload.get("target_soc_7_percent") is None:
        status = "unsafe_to_apply"
        should_apply = False
        conservative = True
        reasons.append("missing_target_soc")

    if optimization_payload is None:
        reasons.append("cost_optimizer_unavailable_or_legacy_selected")

    return {
        "status": status,
        "should_apply": should_apply,
        "conservative": conservative,
        "source": source or "unknown",
        "reasons": reasons or ["all_required_inputs_available"],
    }


def _uses_physical_pv_forecast(physical_pv_diagnostics: dict[str, object]) -> bool:
    method = str(physical_pv_diagnostics.get("selected_method") or "").strip().lower()
    return method.startswith("physical_")


def _annotate_pv_headroom_guard_policy(
    guard: dict[str, object],
    *,
    apply_caps: bool,
    selected_method: str,
) -> dict[str, object]:
    out = dict(guard)
    out["enforced_as_target_cap"] = bool(apply_caps and guard.get("applied"))
    out["enforcement_policy"] = "existing_forecast_only"
    out["pv_forecast_selected_method"] = selected_method or "unknown"
    if guard.get("applied") and not apply_caps:
        out["enforcement_skip_reason"] = "physical_pv_selected"
    return out


def _candidate_reason_summary(optimization_payload: dict[str, object] | None) -> list[dict[str, object]]:
    if not isinstance(optimization_payload, dict):
        return []
    summaries = optimization_payload.get("candidate_summaries")
    if not isinstance(summaries, (list, tuple)):
        return []
    out: list[dict[str, object]] = []
    for item in summaries:
        if not isinstance(item, dict):
            continue
        if item.get("rejection_reason") == "selected":
            continue
        out.append(
            {
                "target_soc_percent": item.get("target_soc_percent"),
                "reason": item.get("rejection_reason"),
                "total_expected_cost_yen": item.get("total_expected_cost_yen"),
                "expected_day_buy_kwh": item.get("expected_day_buy_kwh"),
                "expected_sell_kwh": item.get("expected_sell_kwh"),
                "expected_peak_unmet_kwh": item.get("expected_peak_unmet_kwh"),
                "monthly_tier_landing_penalty_yen": item.get(
                    "expected_monthly_tier_landing_penalty_yen"
                ),
                "decision_prior_cost_yen": item.get("decision_prior_cost_yen"),
            }
        )
        if len(out) >= 3:
            break
    return out


def _decision_cost_breakdown(optimization_payload: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(optimization_payload, dict):
        return {}
    return {
        "night_charge_yen": optimization_payload.get("night_charge_cost_yen"),
        "expected_day_buy_yen": optimization_payload.get("expected_day_buy_cost_yen"),
        "expected_sell_loss_yen": optimization_payload.get("expected_sell_opportunity_cost_yen"),
        "expected_peak_unmet_yen": optimization_payload.get("expected_peak_unmet_cost_yen"),
        "monthly_tier_landing_penalty_yen": optimization_payload.get(
            "expected_monthly_tier_landing_penalty_yen"
        ),
        "decision_prior_yen": optimization_payload.get("decision_prior_cost_yen"),
        "total_expected_yen": optimization_payload.get("total_expected_cost_yen"),
    }


def _list_value(values: object, index: int) -> object | None:
    if not isinstance(values, list) or index >= len(values):
        return None
    return cast(object, values[index])


