from __future__ import annotations

import csv
import json
import math
import os
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Iterable, cast
from zoneinfo import ZoneInfo

import requests

from app.forecasting.consumption import ConsumptionForecast, forecast_daily_consumption
from app.energy_plan import (
    EnergyPlanOutput as EnergyModelOutput,
    ForecastInputPort,
    ForecastSettings,
    HistoricalInputPort,
    HistoricalInputSettings,
    PlanDocumentV1,
    WeatherHistoryFetchResult,
    WeatherHistoryPort,
    build_historical_profile as _historical_profile,
    coerce_hourly_energy as _coerce_hourly_float_dict,
    estimate_sunset_hour as _estimate_sunset_hour,
    summarize_hourly_pv as _hourly_pv_totals,
)
from app.energy_plan.energy_model import (
    DaytimeSocOptimizationResult,
    EnergyModelCoefficients,
    NightChargeInputs,
    NightChargeResult,
    compute_night_charge_target,
    fit_coefficients_from_csv,
    optimize_target_soc_for_daytime,
    to_dict,
)
from app.forecasting.occupancy import (
    OccupancyAdjustment,
    OccupancyScheduleEvent,
    apply_occupancy_schedule,
    filter_training_load_rows,
    load_occupancy_events_from_env,
)
from app.forecasting.pv_array import build_pv_array_forecast, load_pv_array_configs
from app.energy_plan.soc_cost import (
    DEFAULT_SIGMA_BUCKETS,
    ForecastScenario,
    PvForecastUncertainty,
    SocOptimizationRequest,
    SocCostModel,
    SigmaBucket,
    optimize_soc_request,
    to_plain_dict,
)
from app.forecasting.correction import (
    ForecastCorrectionInput,
    ForecastCorrectionPolicy,
    _build_forecast_correction,
    _load_forecast_hourly_history,
    build_forecast_correction,
)
from app.forecasting.pv_physical import build_physical_pv_candidate
from app.energy_plan.decision_feedback import load_soc_decision_prior_from_firestore
from app.configuration.environment import load_dotenv_if_present
from app.kpnet.monitoring_history import find_latest_kpnet_csv_paths
from app.energy_plan.weather_history import (
    archive_weather_history,
    forecast_weather_row,
    hourly_weather_records_from_open_meteo,
    hourly_weather_summary,
    weather_archive_cache_path,
    weather_class,
)
from app.energy_plan.forecast_inputs import (
    build_hourly_load_forecast,
    build_hourly_pv_forecast,
    load_rows_for_consumption_forecast,
    pv_forecast_totals,
    reshape_hourly_pv_by_weather,
)
from app.energy_plan.soc_constraints import (
    SocConstraint,
    SocConstraintSet,
    active_constraint_names as _active_constraint_names,
    morning_pv_headroom_guard,
)


@dataclass(frozen=True)
class EnergyModelConfig:
    artifacts_dir: Path
    latitude: float
    longitude: float
    timezone: str
    consumption_min_training_days: int
    consumption_fallback_window_days: int
    reserve_soc_percent: float
    cycle_count: float
    battery_temp_c: float | None
    pv_midday_load_fraction: float
    daytime_soc_step_percent: float
    daytime_target_peak_soc_percent: float
    daytime_buy_tolerance_kwh: float
    daytime_sell_tolerance_kwh: float
    cost_optimization_enabled: bool
    cost_respect_morning_headroom_cap: bool
    cost_soc_step_percent: float
    cost_weather_upside_z: float
    cost_min_pv_multiplier: float
    cost_max_pv_multiplier: float

    @classmethod
    def from_env(cls) -> "EnergyModelConfig":
        load_dotenv_if_present()
        forecast_settings = ForecastSettings.from_env()
        historical_settings = HistoricalInputSettings.from_env()
        battery_temp = (
            float(os.environ["BATTERY_TEMP_C"])
            if "BATTERY_TEMP_C" in os.environ
            else None
        )
        daytime_soc_step = float(
            os.getenv("DAYTIME_SOC_OPT_STEP_PERCENT", "1.0").strip() or "1.0"
        )
        return cls(
            artifacts_dir=historical_settings.artifacts_dir,
            latitude=forecast_settings.latitude,
            longitude=forecast_settings.longitude,
            timezone=forecast_settings.timezone,
            consumption_min_training_days=historical_settings.min_training_days,
            consumption_fallback_window_days=historical_settings.fallback_window_days,
            reserve_soc_percent=float(os.getenv("NIGHT_RESERVE_SOC_PERCENT", "30")),
            cycle_count=float(os.getenv("BATTERY_CYCLE_COUNT", "0")),
            battery_temp_c=battery_temp,
            pv_midday_load_fraction=(
                _to_optional_float(os.getenv("PV_MIDDAY_LOAD_FRACTION", "").strip())
                or (6.0 / 13.0)
            ),
            daytime_soc_step_percent=daytime_soc_step,
            daytime_target_peak_soc_percent=float(
                os.getenv("DAYTIME_TARGET_PEAK_SOC_PERCENT", "99.0").strip() or "99.0"
            ),
            daytime_buy_tolerance_kwh=float(
                os.getenv("DAYTIME_BUY_TOLERANCE_KWH", "0.05").strip() or "0.05"
            ),
            daytime_sell_tolerance_kwh=float(
                os.getenv("DAYTIME_SELL_TOLERANCE_KWH", "0.10").strip() or "0.10"
            ),
            cost_optimization_enabled=_env_bool(
                "DAYTIME_SOC_COST_OPTIMIZATION_ENABLED", True
            ),
            cost_respect_morning_headroom_cap=_env_bool(
                "SOC_COST_RESPECT_MORNING_HEADROOM_CAP", True
            ),
            cost_soc_step_percent=_env_float("SOC_COST_OPT_STEP_PERCENT", daytime_soc_step),
            cost_weather_upside_z=_env_float("SOC_COST_WEATHER_UPSIDE_SCENARIO_Z", 3.5),
            cost_min_pv_multiplier=_env_float("SOC_COST_MIN_PV_MULTIPLIER", 0.0),
            cost_max_pv_multiplier=_env_float("SOC_COST_MAX_PV_MULTIPLIER", 3.0),
        )


@dataclass(frozen=True)
class EnergyModelContext:
    config: EnergyModelConfig
    csv_paths: list[Path]
    rows: list[dict[str, Any]]
    coefficients: EnergyModelCoefficients
    historical_profile: dict[str, float]
    forecast: dict[str, object]
    target_date: str
    latest_soc_percent: float
    occupancy_events: list[OccupancyScheduleEvent]


class _DefaultHistoricalInputPort:
    def locate_csv_paths(self, artifacts_dir: Path) -> list[Path]:
        return _csv_paths_from_env_or_latest(artifacts_dir)

    def read_rows(self, csv_paths: list[Path]) -> list[dict[str, Any]]:
        return _read_rows(csv_paths)

    def fit_coefficients(self, csv_paths: list[Path]) -> EnergyModelCoefficients:
        return fit_coefficients_from_csv(csv_paths)

    def build_historical_profile(self, rows: list[dict[str, Any]]) -> dict[str, float]:
        return _historical_profile(rows)

    def load_occupancy_events(self) -> list[OccupancyScheduleEvent]:
        return load_occupancy_events_from_env()


class _DefaultForecastInputPort:
    def load_forecast(self, *, latitude: float, longitude: float, timezone: str) -> dict[str, object]:
        return _forecast_from_env_or_api(lat=latitude, lon=longitude, timezone=timezone)


class _DefaultWeatherHistoryPort:
    def load_history(
        self,
        rows: list[dict[str, Any]],
        *,
        latitude: float,
        longitude: float,
        timezone: str,
    ) -> WeatherHistoryFetchResult:
        return _archive_weather_history(rows, lat=latitude, lon=longitude, timezone=timezone)


@dataclass(frozen=True)
class ConsumptionForecastBundle:
    daily: ConsumptionForecast
    base_daily: ConsumptionForecast
    training_diagnostics: dict[str, object]
    occupancy_adjustment: OccupancyAdjustment | None


@dataclass
class NightChargePreparation:
    pv_array_forecast: dict[str, object] | None
    inputs: NightChargeInputs
    result: NightChargeResult
    result_payload: dict[str, Any]
    monthly_day_buy_before_target: dict[str, object]
    expected_rest_of_month_day_buy: dict[str, object]
    expected_overnight_discharge_kwh: float


@dataclass
class PvForecastBundle:
    array_forecast: dict[str, object] | None
    hourly_load_kwh: dict[int, float]
    hourly_pv_kwh: dict[int, float]
    hourly_weather_shape: dict[str, object]
    physical_diagnostics: dict[str, object]
    correction: dict[str, object]
    selected_method: str
    source: str
    uncertainty: PvForecastUncertainty
    sunset_hour: int


@dataclass(frozen=True)
class LegacyOptimizationDecision:
    result: DaytimeSocOptimizationResult | None
    payload: dict[str, object] | None


@dataclass
class OptimizationDecision:
    result_payload: dict[str, Any]
    optimization_payload: dict[str, object] | None
    cost_optimization_payload: dict[str, object] | None


def _latest_kpnet_csv_paths(artifacts_dir: Path) -> list[Path]:
    csv_paths = find_latest_kpnet_csv_paths(artifacts_dir)
    if csv_paths:
        return csv_paths
    raise RuntimeError("artifacts配下にCSVが見つかりませんでした")


def _csv_paths_from_env_or_latest(artifacts_dir: Path) -> list[Path]:
    explicit_dir = os.getenv("ENERGY_MODEL_CSV_DIR", "").strip()
    if explicit_dir:
        csv_dir = Path(explicit_dir)
        csvs = sorted(csv_dir.glob("*.csv"))
        if csvs:
            return csvs
        raise RuntimeError(f"ENERGY_MODEL_CSV_DIR にCSVが見つかりません: {csv_dir}")

    explicit_list = os.getenv("ENERGY_MODEL_CSV_PATHS", "").strip()
    if explicit_list:
        csvs = [Path(p.strip()) for p in explicit_list.split(",") if p.strip()]
        existing = [p for p in csvs if p.exists()]
        if existing:
            return existing
        raise RuntimeError("ENERGY_MODEL_CSV_PATHS のCSVが見つかりませんでした")

    return _latest_kpnet_csv_paths(artifacts_dir)


def _read_rows(csv_paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in csv_paths:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            required_financial_columns = (
                "買電電力量[kWh]",
                "売電電力量[kWh]",
            )
            fieldnames = set(reader.fieldnames or ())
            missing_columns = [
                column for column in required_financial_columns if column not in fieldnames
            ]
            if missing_columns:
                raise RuntimeError(
                    "required financial CSV columns are missing "
                    f"({', '.join(missing_columns)}): {path}"
                )
            for row in reader:
                d = (row.get("年月日") or "").strip()
                t = (row.get("時刻") or "").strip()
                if not d or not t:
                    continue
                dt = datetime.strptime(f"{d} {t}", "%Y/%m/%d %H:%M")

                def fv(key: str) -> float:
                    v = (row.get(key) or "").strip()
                    return float(v) if v else 0.0

                soc_raw = (row.get("蓄電残量(SOC)[%]") or "").strip()
                soc = float(soc_raw) if soc_raw else float("nan")
                rows.append(
                    {
                        "dt": dt,
                        "load": fv("消費電力量[kWh]"),
                        "pv": fv("発電電力量[kWh]"),
                        "sell": fv("売電電力量[kWh]"),
                        "buy": fv("買電電力量[kWh]"),
                        "charge": fv("充電電力量[kWh]"),
                        "discharge": fv("放電電力量[kWh]"),
                        "soc": soc,
                    }
                )
    rows.sort(key=lambda x: x["dt"] if isinstance(x.get("dt"), datetime) else datetime.min)
    return rows


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


def _weather_class(weather_code: int | None) -> str:
    """Compatibility seam for tests importing the former workflow helper."""
    return weather_class(weather_code)


def _fetch_open_meteo_previous_day1_forecast(
    *,
    lat: float,
    lon: float,
    timezone: str,
    target_date: str,
) -> dict[str, object]:
    model = os.getenv("OPEN_METEO_PREVIOUS_RUNS_MODEL", "jma_seamless").strip() or "jma_seamless"
    suffix = "_previous_day1"
    url = "https://previous-runs-api.open-meteo.com/v1/forecast"
    params: dict[str, str | float] = {
        "latitude": lat,
        "longitude": lon,
        "timezone": timezone,
        "start_date": target_date,
        "end_date": target_date,
        "models": model,
        "hourly": (
            "weather_code_previous_day1,precipitation_previous_day1,"
            "precipitation_probability_previous_day1,cloud_cover_previous_day1,"
            "shortwave_radiation_previous_day1,temperature_2m_previous_day1,"
            "relative_humidity_2m_previous_day1,dew_point_2m_previous_day1,"
            "wind_speed_10m_previous_day1"
        ),
    }
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    hourly = resp.json().get("hourly", {})
    if not isinstance(hourly, dict):
        raise RuntimeError("Open-Meteo previous runs response has no hourly data")
    hourly_weather = hourly_weather_records_from_open_meteo(hourly, target_date=target_date, suffix=suffix)
    if not hourly_weather:
        raise RuntimeError(f"Open-Meteo previous runs hourly forecast is empty: {target_date}")
    summary = hourly_weather_summary(
        hourly_weather,
        rain_probability_threshold=_env_float("HOURLY_WEATHER_RAIN_PROBABILITY_THRESHOLD", 70.0),
        rain_mm_threshold=_env_float("HOURLY_WEATHER_RAIN_MM_THRESHOLD", 0.1),
        low_shortwave_threshold=_env_float("HOURLY_WEATHER_LOW_SHORTWAVE_W_M2", 120.0),
    )
    dominant_code = _to_optional_int(summary.get("dominant_weather_code_7_17"))
    shortwave_sum_wh = _to_optional_float(summary.get("shortwave_sum_9_15_wh_m2")) or 0.0
    return {
        "date": target_date,
        "sun_hours": None,
        "temp_c": _to_optional_float(summary.get("mean_temp_c_7_17")),
        "weather_code": dominant_code,
        "weather_class": _weather_class(dominant_code),
        "precipitation_sum_mm": sum(
            _to_optional_float(row.get("precipitation_mm")) or 0.0 for row in hourly_weather
        ),
        "precipitation_probability_mean": None,
        "shortwave_radiation_sum_mj_m2": shortwave_sum_wh * 3600.0 / 1_000_000.0,
        "hourly_weather": hourly_weather,
        "hourly_weather_summary": summary,
        "historical_forecast": {
            "enabled": True,
            "source": "open-meteo-previous-runs-day1",
            "model": model,
            "endpoint": "previous-runs-api.open-meteo.com",
        },
    }


# readable-code-audit: skip STRUCT-04 — provider response normalization and date selection must retain one weather-request contract
def _forecast_for_date(lat: float, lon: float, timezone: str, *, target_date: str | None = None) -> dict[str, object]:
    url = "https://api.open-meteo.com/v1/forecast"
    params: dict[str, str | float | int] = {
        "latitude": lat,
        "longitude": lon,
        "hourly": (
            "weather_code,precipitation,precipitation_probability,cloud_cover,shortwave_radiation,"
            "temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m"
        ),
        "daily": (
            "sunshine_duration,temperature_2m_mean,weather_code,"
            "precipitation_sum,precipitation_probability_mean,shortwave_radiation_sum"
        ),
        "timezone": timezone,
        "forecast_days": 7,
    }
    attempts = max(1, int(os.getenv("FORECAST_API_RETRIES", "4").strip() or "4"))
    backoff_seconds = max(0.0, float(os.getenv("FORECAST_API_RETRY_BACKOFF_SECONDS", "5").strip() or "5"))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(url, params=params, timeout=20)
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= attempts:
                raise
            sleep_seconds = min(60.0, backoff_seconds * (2 ** (attempt - 1)))
            print(
                f"[energy_model] forecast API failed attempt={attempt}/{attempts}: {exc}; retry in {sleep_seconds:.1f}s",
                flush=True,
            )
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    else:
        raise RuntimeError("forecast API request failed") from last_error
    obj = resp.json()
    times = obj["daily"]["time"]
    sunshine = obj["daily"]["sunshine_duration"]
    temp = obj["daily"]["temperature_2m_mean"]
    if len(times) < 2:
        raise RuntimeError("翌日予報を取得できませんでした")
    daily = obj.get("daily", {})
    hourly = obj.get("hourly", {})
    target_index = 1
    if target_date:
        try:
            target_index = times.index(target_date)
        except ValueError as exc:
            raise RuntimeError(f"指定日の予報を取得できませんでした: {target_date}") from exc
    weather_code = _to_optional_int(_list_value(daily.get("weather_code"), target_index))
    forecast_date = str(times[target_index])
    hourly_weather = hourly_weather_records_from_open_meteo(hourly, target_date=forecast_date) if isinstance(hourly, dict) else []
    return {
        "date": forecast_date,
        "sun_hours": (_to_optional_float(_list_value(sunshine, target_index)) or 0.0) / 3600.0,
        "temp_c": _to_optional_float(_list_value(temp, target_index)) or 0.0,
        "weather_code": weather_code,
        "weather_class": _weather_class(weather_code),
        "precipitation_sum_mm": _to_optional_float(_list_value(daily.get("precipitation_sum"), target_index)),
        "precipitation_probability_mean": _to_optional_float(
            _list_value(daily.get("precipitation_probability_mean"), target_index)
        ),
        "shortwave_radiation_sum_mj_m2": _to_optional_float(
            _list_value(daily.get("shortwave_radiation_sum"), target_index)
        ),
        "hourly_weather": hourly_weather,
        "hourly_weather_summary": hourly_weather_summary(
            hourly_weather,
            rain_probability_threshold=_env_float("HOURLY_WEATHER_RAIN_PROBABILITY_THRESHOLD", 70.0),
            rain_mm_threshold=_env_float("HOURLY_WEATHER_RAIN_MM_THRESHOLD", 0.1),
            low_shortwave_threshold=_env_float("HOURLY_WEATHER_LOW_SHORTWAVE_W_M2", 120.0),
        ),
    }


# readable-code-audit: skip STRUCT-04 — environment override and provider fallback are one forecast-source selection boundary
def _forecast_from_env_or_api(*, lat: float, lon: float, timezone: str) -> dict[str, object]:
    date_override = os.getenv("FORECAST_DATE_OVERRIDE", "").strip()
    sun_override = os.getenv("FORECAST_SUN_HOURS_OVERRIDE", "").strip()
    if sun_override:
        date_override = date_override or datetime.now().date().isoformat()
        temp_override = os.getenv("FORECAST_TEMP_C_OVERRIDE", "").strip() or "20"
        weather_code = _to_optional_int(os.getenv("FORECAST_WEATHER_CODE_OVERRIDE", "").strip() or None)
        forecast = {
            "date": date_override,
            "sun_hours": float(sun_override),
            "temp_c": float(temp_override),
            "weather_code": weather_code,
            "weather_class": _weather_class(weather_code),
            "precipitation_sum_mm": _to_optional_float(os.getenv("FORECAST_PRECIPITATION_SUM_MM_OVERRIDE", "").strip() or None),
            "precipitation_probability_mean": _to_optional_float(
                os.getenv("FORECAST_PRECIPITATION_PROBABILITY_MEAN_OVERRIDE", "").strip() or None
            ),
            "shortwave_radiation_sum_mj_m2": _to_optional_float(
                os.getenv("FORECAST_SHORTWAVE_RADIATION_SUM_MJ_M2_OVERRIDE", "").strip() or None
            ),
            "hourly_weather": [],
            "hourly_weather_summary": {},
            "source": "env-override",
        }
        if _env_bool("OPEN_METEO_PREVIOUS_DAY1_FORECAST_ENABLED", False):
            try:
                previous = _fetch_open_meteo_previous_day1_forecast(
                    lat=lat,
                    lon=lon,
                    timezone=timezone,
                    target_date=date_override,
                )
                for key in (
                    "weather_code",
                    "weather_class",
                    "precipitation_sum_mm",
                    "precipitation_probability_mean",
                    "shortwave_radiation_sum_mj_m2",
                    "hourly_weather",
                    "hourly_weather_summary",
                    "historical_forecast",
                ):
                    if previous.get(key) is not None:
                        forecast[key] = previous[key]
                if previous.get("temp_c") is not None and not os.getenv("FORECAST_TEMP_C_OVERRIDE", "").strip():
                    forecast["temp_c"] = previous["temp_c"]
                forecast["source"] = "env-override+open-meteo-previous-runs-day1"
            except Exception as exc:
                forecast["historical_forecast"] = {
                    "enabled": True,
                    "source": "open-meteo-previous-runs-day1",
                    "error": str(exc),
                }
        return forecast
    try:
        forecast = _forecast_for_date(lat=lat, lon=lon, timezone=timezone, target_date=date_override or None)
        forecast["source"] = "open-meteo-forecast"
        return forecast
    except Exception as exc:
        if date_override:
            fallback_date = date_override
        else:
            try:
                fallback_date = (datetime.now(ZoneInfo(timezone)).date() + timedelta(days=1)).isoformat()
            except Exception:
                fallback_date = (datetime.now().date() + timedelta(days=1)).isoformat()
        print(
            f"[energy_model] daily forecast API failed; continue with date-only fallback for PV providers: {exc}",
            flush=True,
        )
        return {
            "date": fallback_date,
            "sun_hours": 0.0,
            "temp_c": 20.0,
            "weather_code": None,
            "weather_class": "unknown",
            "precipitation_sum_mm": None,
            "precipitation_probability_mean": None,
            "shortwave_radiation_sum_mj_m2": None,
            "hourly_weather": [],
            "hourly_weather_summary": {},
            "source": "date-only-fallback",
            "daily_forecast_error": str(exc),
        }


def _archive_weather_history(
    rows: list[dict[str, Any]],
    *,
    lat: float,
    lon: float,
    timezone: str,
) -> WeatherHistoryFetchResult:
    # Preserve the workflow injection point while weather_history owns archive I/O and cache behavior.
    return archive_weather_history(
        rows,
        lat=lat,
        lon=lon,
        timezone=timezone,
        cache_path=weather_archive_cache_path(),
        chunk_days=max(1, int(_env_float("WEATHER_ARCHIVE_CHUNK_DAYS", 14.0))),
        timeout_seconds=max(1.0, _env_float("WEATHER_ARCHIVE_TIMEOUT_SECONDS", 30.0)),
    )


def _archive_weather_rows(
    rows: list[dict[str, Any]],
    *,
    lat: float,
    lon: float,
    timezone: str,
) -> list[dict[str, object]]:
    return archive_weather_history(
        rows,
        lat=lat,
        lon=lon,
        timezone=timezone,
        cache_path=weather_archive_cache_path(),
        chunk_days=max(1, int(_env_float("WEATHER_ARCHIVE_CHUNK_DAYS", 14.0))),
        timeout_seconds=max(1.0, _env_float("WEATHER_ARCHIVE_TIMEOUT_SECONDS", 30.0)),
    ).rows


def _consumption_forecast_to_dict(forecast: ConsumptionForecast) -> dict[str, object]:
    return {
        "target_date": forecast.target_date.isoformat(),
        "morning_load_kwh": forecast.morning_load_kwh,
        "daytime_load_kwh": forecast.daytime_load_kwh,
        "source": forecast.source,
        "sample_count": forecast.sample_count,
        "features": forecast.features,
    }


def _occupancy_adjustment_to_dict(adjustment: OccupancyAdjustment | None) -> dict[str, object] | None:
    if adjustment is None:
        return None
    return dict(adjustment.to_dict())


def _build_pv_forecast_or_disabled(
    *,
    rows: list[dict[str, Any]],
    target_date: str,
    lat: float,
    lon: float,
    timezone: str,
    target_weather_class: str | None,
    target_sun_hours: float | None,
    target_precipitation_sum_mm: float | None,
) -> dict[str, object] | None:
    if not _env_bool("PV_ARRAY_FORECAST_ENABLED", True):
        return {"enabled": False, "source": "disabled"}
    arrays = load_pv_array_configs()
    if not arrays:
        return {"enabled": False, "source": "no_pv_array_config"}
    try:
        result = build_pv_array_forecast(
            arrays=arrays,
            rows=rows,
            target_date=target_date,
            lat=lat,
            lon=lon,
            timezone=timezone,
            target_weather_class=target_weather_class,
            target_sun_hours=target_sun_hours,
            target_precipitation_sum_mm=target_precipitation_sum_mm,
        )
        return dict(result) if result is not None else {"enabled": False, "source": "pv_array_forecast_empty"}
    except Exception as exc:
        return {"enabled": False, "source": "pv_array_forecast_failed", "error": str(exc)}


def _morning_pv_headroom_guard(
    *,
    hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float],
    effective_capacity_kwh_value: float,
    reserve_soc_percent: float,
) -> dict[str, object]:
    return morning_pv_headroom_guard(
        hourly_load_kwh=hourly_load_kwh,
        hourly_pv_kwh=hourly_pv_kwh,
        effective_capacity_kwh=effective_capacity_kwh_value,
        reserve_soc_percent=reserve_soc_percent,
        enabled=_env_bool("MORNING_PV_HEADROOM_GUARD_ENABLED", True),
        guard_ratio=_env_float_clamped(
            "MORNING_PV_HEADROOM_GUARD_RATIO", 0.50, min_value=0.0, max_value=1.0
        ),
        min_guard_kwh=max(0.0, _env_float("MORNING_PV_HEADROOM_GUARD_MIN_KWH", 0.20)),
    )


# readable-code-audit: skip STRUCT-04 — historical selection and headroom decision must use the same daytime energy snapshot
def _daytime_net_surplus_headroom_guard(
    *,
    hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float],
    forecast: dict[str, object],
    effective_capacity_kwh_value: float,
    reserve_soc_percent: float,
) -> dict[str, object]:
    from app.energy_plan.soc_constraints import daytime_net_surplus_headroom_guard

    return daytime_net_surplus_headroom_guard(
        hourly_load_kwh=hourly_load_kwh,
        hourly_pv_kwh=hourly_pv_kwh,
        forecast=forecast,
        effective_capacity_kwh=effective_capacity_kwh_value,
        reserve_soc_percent=reserve_soc_percent,
    )


# readable-code-audit: skip STRUCT-04 — history selection and the resulting guard must use one model-input snapshot to avoid approving a plan from mismatched dates
def _historical_daytime_soc_gain_guard(
    rows: list[dict[str, Any]],
    *,
    reserve_soc_percent: float,
    target_date: str,
) -> dict[str, object]:
    from app.energy_plan.soc_constraints import historical_daytime_soc_gain_guard

    return historical_daytime_soc_gain_guard(
        rows,
        reserve_soc_percent=reserve_soc_percent,
        target_date=target_date,
    )


def _apply_uncertainty_floor(uncertainty: PvForecastUncertainty) -> PvForecastUncertainty:
    floor = max(0.0, _env_float("SOC_COST_PV_UNCERTAINTY_STD_FLOOR", 0.30))
    std = max(uncertainty.std_multiplier, floor)
    return PvForecastUncertainty(
        mean_multiplier=uncertainty.mean_multiplier,
        std_multiplier=std,
        variance_multiplier=std * std,
        sample_count=uncertainty.sample_count,
        source=uncertainty.source if std == uncertainty.std_multiplier else f"{uncertainty.source}+std_floor",
    )


def _sigma_buckets_for_cost_optimizer() -> tuple[SigmaBucket, ...]:
    if not _env_bool("SOC_COST_UPSIDE_SCENARIO_ENABLED", False):
        return DEFAULT_SIGMA_BUCKETS
    upside_probability = _env_float_clamped("SOC_COST_UPSIDE_SCENARIO_PROBABILITY", 0.08, min_value=0.0, max_value=0.5)
    upside_z = _env_float("SOC_COST_UPSIDE_SCENARIO_Z", 3.0)
    if upside_probability <= 0:
        return DEFAULT_SIGMA_BUCKETS
    base_sum = sum(max(0.0, b.probability) for b in DEFAULT_SIGMA_BUCKETS) or 1.0
    remaining = max(0.0, 1.0 - upside_probability)
    base = tuple(
        SigmaBucket(b.label, max(0.0, b.probability) / base_sum * remaining, b.z_value)
        for b in DEFAULT_SIGMA_BUCKETS
    )
    return base + (SigmaBucket("pv_upside_guard", upside_probability, upside_z),)


def _load_scenarios_for_cost_optimizer(
    forecast_correction: dict[str, object] | None = None,
) -> tuple[ForecastScenario, ...] | None:
    if not _env_bool("SOC_COST_LOAD_SCENARIOS_ENABLED", True):
        return None
    adaptive = (forecast_correction or {}).get("load_scenarios")
    if isinstance(adaptive, list):
        scenarios: list[ForecastScenario] = []
        for item in adaptive:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            probability = _to_optional_float(item.get("probability"))
            multiplier = _to_optional_float(item.get("multiplier"))
            if not label or probability is None or probability <= 0.0 or multiplier is None or multiplier <= 0.0:
                continue
            scenarios.append(ForecastScenario(label, probability, 1.0, multiplier))
        if scenarios:
            return tuple(scenarios)
    low_probability = _env_float_clamped("SOC_COST_LOAD_LOW_PROBABILITY", 0.20, min_value=0.0, max_value=1.0)
    high_probability = _env_float_clamped("SOC_COST_LOAD_HIGH_PROBABILITY", 0.20, min_value=0.0, max_value=1.0)
    mid_probability = max(0.0, 1.0 - low_probability - high_probability)
    return (
        ForecastScenario("load_low", low_probability, 1.0, _env_float("SOC_COST_LOAD_LOW_MULTIPLIER", 0.82)),
        ForecastScenario("load_mid", mid_probability, 1.0, _env_float("SOC_COST_LOAD_MID_MULTIPLIER", 1.00)),
        ForecastScenario("load_high", high_probability, 1.0, _env_float("SOC_COST_LOAD_HIGH_MULTIPLIER", 1.18)),
    )


def _weather_upside_probability_for_cost_optimizer(forecast: dict[str, object]) -> float:
    if not _env_bool("SOC_COST_WEATHER_UPSIDE_SCENARIO_ENABLED", True):
        return 0.0
    weather_class = str(forecast.get("weather_class") or "").strip().lower()
    if weather_class not in {"cloudy", "rain", "rainy"}:
        return 0.0
    return _env_float_clamped("SOC_COST_WEATHER_UPSIDE_SCENARIO_PROBABILITY", 0.12, min_value=0.0, max_value=0.5)


def _estimate_midday_surplus_from_pv_forecast(
    *,
    pv_forecast: dict[str, object] | None,
    consumption_forecast: ConsumptionForecast,
) -> float | None:
    totals = pv_forecast_totals(pv_forecast)
    midday_pv = _to_optional_float(totals.get("midday_kwh"))
    if midday_pv is None:
        return None
    non_morning_load = max(
        0.0,
        consumption_forecast.daytime_load_kwh - consumption_forecast.morning_load_kwh,
    )
    # Midday is 10:00-16:00. The remaining daytime load window is 10:00-23:00.
    default_fraction = 6.0 / 13.0
    midday_load_fraction = _to_optional_float(os.getenv("PV_MIDDAY_LOAD_FRACTION", "").strip())
    if midday_load_fraction is None:
        midday_load_fraction = default_fraction
    midday_load_fraction = max(0.0, min(1.0, midday_load_fraction))
    estimated_midday_load = non_morning_load * midday_load_fraction
    net_surplus = max(0.0, midday_pv - estimated_midday_load)
    return float(net_surplus)


def _parse_hhmm(value: str, *, default: str) -> dt_time:
    text = (value or default).strip() or default
    try:
        hh, mm = text.split(":", 1)
        return dt_time(hour=max(0, min(23, int(hh))), minute=max(0, min(59, int(mm))))
    except (TypeError, ValueError):
        hh, mm = default.split(":", 1)
        return dt_time(hour=int(hh), minute=int(mm))


def _clock_minutes(value: dt_time) -> int:
    return value.hour * 60 + value.minute


def _is_within_window(minute_of_day: int, *, start_minute: int, end_minute: int) -> bool:
    if start_minute == end_minute:
        return True
    if start_minute < end_minute:
        return start_minute <= minute_of_day < end_minute
    return minute_of_day >= start_minute or minute_of_day < end_minute


def _billing_period_for_target(target_day: date) -> tuple[date, date, int]:
    raw = os.getenv(
        "SOC_MONTHLY_TIER_CLOSE_DAY",
        os.getenv("DASHBOARD_AGGREGATION_CLOSE_DAY", "14"),
    ).strip()
    try:
        close_day = max(1, min(28, int(raw)))
    except ValueError:
        close_day = 14

    if target_day.day <= close_day:
        period_end = target_day.replace(day=close_day)
        previous_month_end = period_end.replace(day=1) - timedelta(days=1)
        period_start = previous_month_end.replace(day=close_day + 1)
    else:
        next_month = (target_day.replace(day=28) + timedelta(days=4)).replace(day=1)
        period_start = target_day.replace(day=close_day + 1)
        period_end = next_month.replace(day=close_day)
    return period_start, period_end, close_day


def _monthly_day_buy_kwh_before_target(
    rows: list[dict[str, Any]],
    *,
    target_date: str,
) -> dict[str, object]:
    try:
        target_day = date.fromisoformat(target_date)
    except ValueError:
        return {"kwh": 0.0, "source": "invalid_target_date", "target_date": target_date}
    day_start = _parse_hhmm(os.getenv("NIGHT8_DAY_START_HHMM", "07:00"), default="07:00")
    day_end = _parse_hhmm(os.getenv("NIGHT8_DAY_END_HHMM", "23:00"), default="23:00")
    start_minute = _clock_minutes(day_start)
    end_minute = _clock_minutes(day_end)
    period_start, period_end, close_day = _billing_period_for_target(target_day)
    total = 0.0
    sample_days: set[str] = set()
    for row in rows:
        dt = row.get("dt")
        if not isinstance(dt, datetime):
            continue
        row_day = dt.date()
        if row_day < period_start or row_day >= target_day or row_day > period_end:
            continue
        minute = dt.hour * 60 + dt.minute
        if not _is_within_window(minute, start_minute=start_minute, end_minute=end_minute):
            continue
        total += max(0.0, float(row.get("buy", 0.0) or 0.0))
        sample_days.add(row_day.isoformat())
    return {
        "kwh": round(total, 4),
        "source": "csv_month_to_target_daytime_buy",
        "target_date": target_date,
        "billing_period_start": period_start.isoformat(),
        "billing_period_end": period_end.isoformat(),
        "billing_close_day": close_day,
        "day_window": f"{day_start.strftime('%H:%M')}-{day_end.strftime('%H:%M')}",
        "sample_day_count": len(sample_days),
        "sample_days": sorted(sample_days)[-10:],
    }


def _expected_rest_of_month_day_buy_kwh(
    rows: list[dict[str, Any]],
    *,
    target_date: str,
) -> dict[str, object]:
    try:
        target_day = date.fromisoformat(target_date)
    except ValueError:
        return {"kwh": 0.0, "source": "invalid_target_date", "target_date": target_date}

    lookback_days = max(1, int(_env_float("SOC_MONTHLY_TIER_RECENT_DAYS", 7.0)))
    day_start = _parse_hhmm(os.getenv("NIGHT8_DAY_START_HHMM", "07:00"), default="07:00")
    day_end = _parse_hhmm(os.getenv("NIGHT8_DAY_END_HHMM", "23:00"), default="23:00")
    start_minute = _clock_minutes(day_start)
    end_minute = _clock_minutes(day_end)
    period_start, period_end, close_day = _billing_period_for_target(target_day)
    daily: dict[date, float] = {}
    for row in rows:
        dt = row.get("dt")
        if not isinstance(dt, datetime):
            continue
        row_day = dt.date()
        if row_day < period_start or row_day >= target_day:
            continue
        minute = dt.hour * 60 + dt.minute
        if not _is_within_window(minute, start_minute=start_minute, end_minute=end_minute):
            continue
        daily[row_day] = daily.get(row_day, 0.0) + max(0.0, float(row.get("buy", 0.0) or 0.0))

    recent_days = sorted(daily)[-lookback_days:]
    recent_values = [daily[day] for day in recent_days]
    avg = statistics.mean(recent_values) if recent_values else 0.0
    remaining_days_after_target = max(0, (period_end - target_day).days)
    expected = avg * remaining_days_after_target
    return {
        "kwh": round(expected, 4),
        "source": "recent_daytime_buy_average",
        "target_date": target_date,
        "billing_period_start": period_start.isoformat(),
        "billing_period_end": period_end.isoformat(),
        "billing_close_day": close_day,
        "day_window": f"{day_start.strftime('%H:%M')}-{day_end.strftime('%H:%M')}",
        "lookback_days": lookback_days,
        "sample_day_count": len(recent_days),
        "recent_daily_avg_kwh": round(avg, 4),
        "remaining_days_after_target": remaining_days_after_target,
        "sample_days": [day.isoformat() for day in recent_days],
    }


def _load_execution_context(
    config: EnergyModelConfig,
    *,
    historical_input: HistoricalInputPort | None = None,
    forecast_input: ForecastInputPort | None = None,
) -> EnergyModelContext:
    history = historical_input or _DefaultHistoricalInputPort()
    forecast_source = forecast_input or _DefaultForecastInputPort()
    csv_paths = history.locate_csv_paths(config.artifacts_dir)
    rows = history.read_rows(csv_paths)
    coefficients = history.fit_coefficients(csv_paths)
    historical_profile = history.build_historical_profile(rows)
    forecast = forecast_source.load_forecast(
        latitude=config.latitude,
        longitude=config.longitude,
        timezone=config.timezone,
    )
    target_date = str(forecast["date"])
    latest_soc = (
        float(rows[-1]["soc"])
        if rows and rows[-1]["soc"] == rows[-1]["soc"]
        else 30.0
    )
    return EnergyModelContext(
        config=config,
        csv_paths=csv_paths,
        rows=rows,
        coefficients=coefficients,
        historical_profile=historical_profile,
        forecast=forecast,
        target_date=target_date,
        latest_soc_percent=latest_soc,
        occupancy_events=history.load_occupancy_events(),
    )


def _build_consumption_forecasts(
    context: EnergyModelContext,
    *,
    weather_history_port: WeatherHistoryPort | None = None,
) -> ConsumptionForecastBundle:
    config = context.config
    weather_source = weather_history_port or _DefaultWeatherHistoryPort()
    load_rows = load_rows_for_consumption_forecast(context.rows)
    training_rows = filter_training_load_rows(load_rows, context.occupancy_events)
    weather_history = weather_source.load_history(
        context.rows,
        latitude=config.latitude,
        longitude=config.longitude,
        timezone=config.timezone,
    )
    base_forecast = forecast_daily_consumption(
        training_rows,
        weather_history.rows,
        context.target_date,
        weather_row=forecast_weather_row(context.forecast),
        min_training_days=config.consumption_min_training_days,
        fallback_window=config.consumption_fallback_window_days,
    )
    consumption_history_dates = {
        value.date().isoformat()
        for row in training_rows
        if isinstance((value := row.get("dt")), datetime)
    }
    joined_training_dates = consumption_history_dates & set(weather_history.received_dates)
    diagnostics: dict[str, object] = {
        **asdict(weather_history),
        "rows": None,
        "requested_start_date": (
            weather_history.requested_dates[0] if weather_history.requested_dates else None
        ),
        "requested_end_date": (
            weather_history.requested_dates[-1] if weather_history.requested_dates else None
        ),
        "requested_day_count": len(weather_history.requested_dates),
        "received_day_count": len(weather_history.received_dates),
        "consumption_history_day_count": len(consumption_history_dates),
        "joined_training_day_count": len(joined_training_dates),
        "join_coverage_ratio": (
            round(len(joined_training_dates) / len(consumption_history_dates), 6)
            if consumption_history_dates
            else 0.0
        ),
        "fallback_reason": (
            None
            if base_forecast.source == "hist_gradient_boosting"
            else "weather_history_unavailable"
            if not weather_history.received_dates
            else "insufficient_joined_training_history"
            if len(joined_training_dates) < config.consumption_min_training_days
            else "consumption_model_fallback"
        ),
    }
    forecast, occupancy_adjustment = apply_occupancy_schedule(
        base_forecast,
        context.occupancy_events,
    )
    return ConsumptionForecastBundle(
        daily=forecast,
        base_daily=base_forecast,
        training_diagnostics=diagnostics,
        occupancy_adjustment=occupancy_adjustment,
    )


# readable-code-audit: skip STRUCT-04 — forecast inputs, SOC constraints, and charge target are prepared from one plan snapshot
def _prepare_night_charge(
    context: EnergyModelContext,
    consumption: ConsumptionForecastBundle,
) -> NightChargePreparation:
    config = context.config
    forecast = context.forecast
    pv_array_forecast = _build_pv_forecast_or_disabled(
        rows=context.rows,
        target_date=context.target_date,
        lat=config.latitude,
        lon=config.longitude,
        timezone=config.timezone,
        target_weather_class=str(forecast.get("weather_class") or ""),
        target_sun_hours=_to_optional_float(forecast.get("sun_hours")),
        target_precipitation_sum_mm=_to_optional_float(
            forecast.get("precipitation_sum_mm")
        ),
    )
    pv_totals = pv_forecast_totals(pv_array_forecast)
    predicted_pv_total_raw = _to_optional_float(pv_totals.get("total_kwh"))
    predicted_pv_override = predicted_pv_total_raw
    if predicted_pv_override is not None and (
        predicted_pv_override < 0 or not math.isfinite(predicted_pv_override)
    ):
        predicted_pv_override = None
    predicted_morning_pv_override = _to_optional_float(pv_totals.get("morning_kwh"))
    if predicted_morning_pv_override is not None and (
        predicted_morning_pv_override < 0
        or not math.isfinite(predicted_morning_pv_override)
    ):
        predicted_morning_pv_override = None
    predicted_midday_surplus_override = _estimate_midday_surplus_from_pv_forecast(
        pv_forecast=pv_array_forecast,
        consumption_forecast=consumption.daily,
    )
    if predicted_pv_total_raw is not None and (
        predicted_pv_total_raw < 0 or not math.isfinite(predicted_pv_total_raw)
    ):
        predicted_midday_surplus_override = None
    if isinstance(pv_array_forecast, dict) and pv_array_forecast.get("enabled"):
        pv_array_forecast["surplus_estimate"] = {
            "midday_surplus_kwh": predicted_midday_surplus_override,
            "method": "net_midday_surplus_without_safety_floor",
            "midday_load_fraction": config.pv_midday_load_fraction,
        }

    parsed_temp_c = _to_optional_float(forecast.get("temp_c"))
    temp_c = (
        20.0
        if parsed_temp_c is None or not math.isfinite(parsed_temp_c)
        else parsed_temp_c
    )
    expected_overnight_discharge_kwh = 0.0
    monthly_day_buy = _monthly_day_buy_kwh_before_target(
        context.rows,
        target_date=context.target_date,
    )
    expected_rest_of_month = _expected_rest_of_month_day_buy_kwh(
        context.rows,
        target_date=context.target_date,
    )
    inputs = NightChargeInputs(
        soc_now_percent=context.latest_soc_percent,
        sun_hours_forecast=_to_optional_float(forecast.get("sun_hours")) or 0.0,
        temp_forecast_c=temp_c,
        daytime_load_forecast_kwh=consumption.daily.daytime_load_kwh,
        morning_load_forecast_kwh=consumption.daily.morning_load_kwh,
        morning_pv_ratio=context.historical_profile["morning_pv_ratio"],
        midday_surplus_ratio=context.historical_profile["midday_surplus_ratio"],
        reserve_soc_percent=config.reserve_soc_percent,
        cycle_count=config.cycle_count,
        battery_temp_c=config.battery_temp_c if config.battery_temp_c is not None else temp_c,
        predicted_pv_kwh_override=predicted_pv_override,
        predicted_morning_pv_kwh_override=predicted_morning_pv_override,
        predicted_midday_surplus_kwh_override=predicted_midday_surplus_override,
        expected_overnight_discharge_kwh=expected_overnight_discharge_kwh,
    )
    result = compute_night_charge_target(context.coefficients, inputs)
    preparation = NightChargePreparation(
        pv_array_forecast=pv_array_forecast,
        inputs=inputs,
        result=result,
        result_payload=to_dict(result),
        monthly_day_buy_before_target=monthly_day_buy,
        expected_rest_of_month_day_buy=expected_rest_of_month,
        expected_overnight_discharge_kwh=expected_overnight_discharge_kwh,
    )
    return preparation


def _paired_scenarios_for_cost_optimizer(
    forecast_correction: dict[str, object] | None = None,
) -> tuple[ForecastScenario, ...] | None:
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
        probability = _to_optional_float(item.get("probability"))
        pv_multiplier = _to_optional_float(item.get("pv_multiplier"))
        load_multiplier = _to_optional_float(item.get("load_multiplier"))
        if (
            not label
            or probability is None
            or probability <= 0.0
            or pv_multiplier is None
            or pv_multiplier <= 0.0
            or load_multiplier is None
            or load_multiplier <= 0.0
        ):
            continue
        scenarios.append(ForecastScenario(label, probability, pv_multiplier, load_multiplier))
    return tuple(scenarios) if len(scenarios) >= 3 else None


# readable-code-audit: skip STRUCT-04 — candidate selection and PV provenance must remain coupled in the generated plan
def _build_selected_pv_forecast(
    context: EnergyModelContext,
    consumption: ConsumptionForecastBundle,
    night_charge: NightChargePreparation,
) -> PvForecastBundle:
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
def _build_soc_constraints(
    context: EnergyModelContext,
    pv_forecast: PvForecastBundle,
    night_charge: NightChargePreparation,
) -> SocConstraintSet:
    reserve_soc = night_charge.inputs.reserve_soc_percent
    capacity = night_charge.result.effective_capacity_kwh
    raw_guards = [
        (
            "morning_pv_headroom_guard",
            _morning_pv_headroom_guard(
                hourly_load_kwh=pv_forecast.hourly_load_kwh,
                hourly_pv_kwh=pv_forecast.hourly_pv_kwh,
                effective_capacity_kwh_value=capacity,
                reserve_soc_percent=reserve_soc,
            ),
        ),
        (
            "daytime_net_surplus_headroom_guard",
            _daytime_net_surplus_headroom_guard(
                hourly_load_kwh=pv_forecast.hourly_load_kwh,
                hourly_pv_kwh=pv_forecast.hourly_pv_kwh,
                forecast=context.forecast,
                effective_capacity_kwh_value=capacity,
                reserve_soc_percent=reserve_soc,
            ),
        ),
        (
            "historical_daytime_soc_gain_guard",
            _historical_daytime_soc_gain_guard(
                context.rows,
                reserve_soc_percent=reserve_soc,
                target_date=context.target_date,
            ),
        ),
    ]
    apply_caps = not _uses_physical_pv_forecast(pv_forecast.physical_diagnostics)
    annotated = [
        _annotate_pv_headroom_guard_policy(
            guard,
            apply_caps=apply_caps,
            selected_method=pv_forecast.selected_method,
        )
        for _, guard in raw_guards
    ]
    from app.energy_plan.soc_constraints import assemble_constraint_set

    return assemble_constraint_set(
        reserve_soc_percent=reserve_soc,
        apply_pv_headroom_caps=apply_caps,
        raw_guards=raw_guards,
        annotated_guards=annotated,
    )


def _run_legacy_soc_optimization(
    context: EnergyModelContext,
    pv_forecast: PvForecastBundle,
    constraints: SocConstraintSet,
    night_charge: NightChargePreparation,
) -> LegacyOptimizationDecision:
    config = context.config
    result = optimize_target_soc_for_daytime(
        effective_capacity_kwh_value=night_charge.result.effective_capacity_kwh,
        soc_now_percent=context.latest_soc_percent,
        reserve_soc_percent=night_charge.inputs.reserve_soc_percent,
        battery_round_trip_efficiency=context.coefficients.battery_round_trip_efficiency,
        hourly_load_kwh=pv_forecast.hourly_load_kwh,
        hourly_pv_kwh=pv_forecast.hourly_pv_kwh,
        sunset_hour=pv_forecast.sunset_hour,
        soc_step_percent=config.daytime_soc_step_percent,
        target_peak_soc_percent=config.daytime_target_peak_soc_percent,
        buy_tolerance_kwh=config.daytime_buy_tolerance_kwh,
        sell_tolerance_kwh=config.daytime_sell_tolerance_kwh,
        max_target_soc_percent=constraints.max_target_soc_percent,
    )
    if result is None:
        return LegacyOptimizationDecision(result=None, payload=None)
    payload: dict[str, object] = {
        **to_dict(result),
        "objective": "avoid_daytime_buy_and_sell_then_peak_soc_near_target",
        "target_peak_soc_percent": config.daytime_target_peak_soc_percent,
        "buy_tolerance_kwh": config.daytime_buy_tolerance_kwh,
        "sell_tolerance_kwh": config.daytime_sell_tolerance_kwh,
        "target_soc_7_percent_after_peak_objective": result.target_soc_7_percent,
        "required_night_charge_kwh_after_peak_objective": result.required_night_charge_kwh,
        "legacy_pv_headroom_cap": {
            "applied": False,
            "reason": "replaced_by_peak_soc_objective",
        },
        "morning_pv_headroom_guard": constraints.morning_headroom,
        "daytime_net_surplus_headroom_guard": constraints.daytime_net_surplus,
        "historical_daytime_soc_gain_guard": constraints.historical_soc_gain,
        "sunset_hour": pv_forecast.sunset_hour,
        "hourly_weather_pv_shape": pv_forecast.hourly_weather_shape,
        "pv_physical_forecast": pv_forecast.physical_diagnostics,
        "hourly_load_forecast_kwh": {
            str(k): round(v, 4) for k, v in sorted(pv_forecast.hourly_load_kwh.items())
        },
        "hourly_pv_forecast_kwh": {
            str(k): round(v, 4) for k, v in sorted(pv_forecast.hourly_pv_kwh.items())
        },
    }
    return LegacyOptimizationDecision(result=result, payload=payload)


# readable-code-audit: skip STRUCT-04 — this orchestration function keeps model inputs, candidate evaluation, and persisted decision metadata on one snapshot
def _run_soc_optimization(
    context: EnergyModelContext,
    night_charge: NightChargePreparation,
    pv_forecast: PvForecastBundle,
    constraints: SocConstraintSet,
    legacy: LegacyOptimizationDecision,
) -> OptimizationDecision:
    config = context.config
    result_payload = dict(night_charge.result_payload)
    optimization_payload: dict[str, object] | None = None
    cost_payload: dict[str, object] | None = None
    if config.cost_optimization_enabled:
        uncertainty = _apply_uncertainty_floor(pv_forecast.uncertainty)
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
        cost_max_soc = 100.0
        if respect_guard and constraints.apply_pv_headroom_caps:
            cost_max_soc = _soc_cap_or_unbounded(
                constraints.morning_headroom.get("cap_target_soc_percent")
            )
        for guard in (constraints.daytime_net_surplus, constraints.historical_soc_gain):
            if constraints.apply_pv_headroom_caps and guard.get("applied"):
                cost_max_soc = min(
                    cost_max_soc,
                    _soc_cap_or_unbounded(guard.get("cap_target_soc_percent")),
                )
        load_scenarios = _load_scenarios_for_cost_optimizer(pv_forecast.correction)
        paired_scenarios = _paired_scenarios_for_cost_optimizer(pv_forecast.correction)
        weather_upside_probability = _weather_upside_probability_for_cost_optimizer(
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
        prior = load_soc_decision_prior_from_firestore(
            target_date=context.target_date,
            target_features=_soc_decision_target_features(
                forecast=context.forecast,
                hourly_load_forecast=pv_forecast.hourly_load_kwh,
                hourly_pv_forecast=pv_forecast.hourly_pv_kwh,
                final_pv_forecast_source=pv_forecast.source,
            ),
        )
        prior_regret_curve = (
            prior.get("regret_yen_by_soc")
            if isinstance(prior, dict) and prior.get("applied")
            else None
        )
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
        optimized = optimize_soc_request(SocOptimizationRequest(
            capacity_kwh=night_charge.result.effective_capacity_kwh,
            soc_now_percent=context.latest_soc_percent,
            reserve_soc_percent=night_charge.inputs.reserve_soc_percent,
            hourly_load_kwh=pv_forecast.hourly_load_kwh,
            hourly_pv_kwh=pv_forecast.hourly_pv_kwh,
            uncertainty=uncertainty,
            cost_model=cost_model,
            soc_step_percent=config.cost_soc_step_percent,
            max_target_soc_percent=cost_max_soc,
            sigma_buckets=_sigma_buckets_for_cost_optimizer(),
            min_pv_multiplier=config.cost_min_pv_multiplier,
            max_pv_multiplier=config.cost_max_pv_multiplier,
            load_scenarios=load_scenarios,
            joint_scenarios=paired_scenarios,
            weather_upside_probability=weather_upside_probability,
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
                        if paired_scenarios
                        else "pv_sigma_x_load_scenarios_with_weather_upside"
                    ),
                    "weather_upside_probability": weather_upside_probability,
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


# readable-code-audit: skip STRUCT-04 — output sections share one model snapshot and published provenance metadata
def _build_energy_model_output(
    context: EnergyModelContext,
    consumption: ConsumptionForecastBundle,
    night_charge: NightChargePreparation,
    pv_forecast: PvForecastBundle,
    constraints: SocConstraintSet,
    decision: OptimizationDecision,
) -> EnergyModelOutput:
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


def build_energy_plan(
    config: EnergyModelConfig,
    *,
    historical_input: HistoricalInputPort | None = None,
    forecast_input: ForecastInputPort | None = None,
    weather_history_port: WeatherHistoryPort | None = None,
) -> EnergyModelOutput:
    """Coordinate the planning use case without persisting or reporting output."""
    if historical_input is None and forecast_input is None:
        context = _load_execution_context(config)
    else:
        context = _load_execution_context(
            config,
            historical_input=historical_input,
            forecast_input=forecast_input,
        )
    consumption_bundle = (
        _build_consumption_forecasts(context)
        if weather_history_port is None
        else _build_consumption_forecasts(context, weather_history_port=weather_history_port)
    )
    night_charge = _prepare_night_charge(context, consumption_bundle)
    pv_bundle = _build_selected_pv_forecast(context, consumption_bundle, night_charge)
    constraints = _build_soc_constraints(context, pv_bundle, night_charge)
    legacy_decision = _run_legacy_soc_optimization(
        context,
        pv_bundle,
        constraints,
        night_charge,
    )
    decision = _run_soc_optimization(
        context,
        night_charge,
        pv_bundle,
        constraints,
        legacy_decision,
    )
    output = _build_energy_model_output(
        context,
        consumption_bundle,
        night_charge,
        pv_bundle,
        constraints,
        decision,
    )
    return output


def main() -> int:
    config = EnergyModelConfig.from_env()
    output = build_energy_plan(config)
    output.persist()
    print(output.output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
