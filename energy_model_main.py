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

from app.consumption_forecast import ConsumptionForecast, forecast_daily_consumption
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
from app.energy_model import (
    DaytimeSocOptimizationResult,
    EnergyModelCoefficients,
    NightChargeInputs,
    NightChargeResult,
    compute_night_charge_target,
    fit_coefficients_from_csv,
    optimize_target_soc_for_daytime,
    to_dict,
)
from app.occupancy_schedule import (
    OccupancyAdjustment,
    OccupancyScheduleEvent,
    apply_occupancy_schedule,
    filter_training_load_rows,
    load_occupancy_events_from_env,
)
from app.pv_array_forecast import build_pv_array_forecast, load_pv_array_configs
from app.soc_cost_optimizer import (
    DEFAULT_SIGMA_BUCKETS,
    ForecastScenario,
    PvForecastUncertainty,
    SocOptimizationRequest,
    SocCostModel,
    SigmaBucket,
    optimize_soc_request,
    to_plain_dict,
)
from app.forecast_correction import (
    ForecastCorrectionInput,
    ForecastCorrectionPolicy,
    _build_forecast_correction,
    _load_forecast_hourly_history,
    build_forecast_correction,
)
from app.pv_physical_forecast import build_physical_pv_candidate
from app.soc_decision_feedback import load_soc_decision_prior_from_firestore


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
        _load_dotenv_if_present()
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
            reserve_soc_percent=float(os.getenv("NIGHT_RESERVE_SOC_PERCENT", "0")),
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
class SocConstraint:
    name: str
    applied: bool
    cap_target_soc_percent: float | None
    reason: str
    evidence: dict[str, object]


@dataclass
class SocConstraintSet:
    reserve_soc_percent: float
    max_target_soc_percent: float
    apply_pv_headroom_caps: bool
    active_constraints: list[SocConstraint]
    morning_headroom: dict[str, object]
    daytime_net_surplus: dict[str, object]
    historical_soc_gain: dict[str, object]


@dataclass(frozen=True)
class LegacyOptimizationDecision:
    result: DaytimeSocOptimizationResult | None
    payload: dict[str, object] | None


@dataclass
class OptimizationDecision:
    result_payload: dict[str, Any]
    optimization_payload: dict[str, object] | None
    cost_optimization_payload: dict[str, object] | None


def _load_dotenv_if_present(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            len(value) >= 2
            and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'"))
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _latest_kpnet_csv_paths(artifacts_dir: Path) -> list[Path]:
    run_dirs = [p for p in artifacts_dir.glob("*") if p.is_dir() and p.name[:8].isdigit()]
    run_dirs.sort(key=lambda p: p.name, reverse=True)
    for run_dir in run_dirs:
        csv_dir = run_dir / "csv"
        csvs = sorted(csv_dir.glob("*.csv"))
        if csvs:
            return csvs
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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


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


def _active_constraint_names(
    *,
    morning_headroom_guard: dict[str, object],
    daytime_net_surplus_headroom_guard: dict[str, object],
    historical_soc_gain_guard: dict[str, object],
    respect_morning_headroom_guard: bool,
) -> list[str]:
    active = ["reserve_soc"]
    morning_enforced = morning_headroom_guard.get("enforced_as_target_cap", morning_headroom_guard.get("applied"))
    daytime_enforced = daytime_net_surplus_headroom_guard.get(
        "enforced_as_target_cap",
        daytime_net_surplus_headroom_guard.get("applied"),
    )
    historical_enforced = historical_soc_gain_guard.get(
        "enforced_as_target_cap",
        historical_soc_gain_guard.get("applied"),
    )
    if respect_morning_headroom_guard and morning_enforced:
        active.append("morning_pv_headroom_guard")
    if daytime_enforced:
        active.append("daytime_net_surplus_headroom_guard")
    if historical_enforced:
        active.append("historical_daytime_soc_gain_guard")
    return active


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
    if weather_code is None:
        return "unknown"
    if weather_code == 0:
        return "clear"
    if 1 <= weather_code <= 3:
        return "cloudy"
    if weather_code in {45, 48}:
        return "fog"
    if 51 <= weather_code <= 67 or 80 <= weather_code <= 82:
        return "rain"
    if 71 <= weather_code <= 77 or 85 <= weather_code <= 86:
        return "snow"
    if 95 <= weather_code <= 99:
        return "storm"
    return "other"


def _hourly_weather_summary(hourly_weather: list[dict[str, object]]) -> dict[str, object]:
    daytime = [
        row for row in hourly_weather
        if isinstance(row, dict) and 7 <= (_to_optional_int(row.get("hour")) or -1) < 18
    ]
    solar = [
        row for row in hourly_weather
        if isinstance(row, dict) and 9 <= (_to_optional_int(row.get("hour")) or -1) < 16
    ]
    rain_probability_threshold = _env_float("HOURLY_WEATHER_RAIN_PROBABILITY_THRESHOLD", 70.0)
    rain_mm_threshold = _env_float("HOURLY_WEATHER_RAIN_MM_THRESHOLD", 0.1)
    low_shortwave_threshold = _env_float("HOURLY_WEATHER_LOW_SHORTWAVE_W_M2", 120.0)

    rain_hours = 0
    low_shortwave_hours = 0
    shortwave_sum = 0.0
    weather_codes: list[int] = []
    temp_values: list[float] = []
    for row in daytime:
        code = _to_optional_int(row.get("weather_code"))
        if code is not None:
            weather_codes.append(code)
        temp = _to_optional_float(row.get("temp_c"))
        if temp is not None:
            temp_values.append(temp)
        precip = _to_optional_float(row.get("precipitation_mm")) or 0.0
        precip_probability = _to_optional_float(row.get("precipitation_probability"))
        if (
            _weather_class(code) in {"rain", "storm"}
            or precip >= rain_mm_threshold
            or (precip_probability is not None and precip_probability >= rain_probability_threshold)
        ):
            rain_hours += 1
    for row in solar:
        shortwave = _to_optional_float(row.get("shortwave_radiation_w_m2")) or 0.0
        shortwave_sum += shortwave
        if shortwave <= low_shortwave_threshold:
            low_shortwave_hours += 1

    dominant_code = None
    if weather_codes:
        dominant_code = max(set(weather_codes), key=weather_codes.count)
    return {
        "daytime_hour_count": len(daytime),
        "solar_hour_count": len(solar),
        "rain_hours_7_17": rain_hours,
        "low_shortwave_hours_9_15": low_shortwave_hours,
        "shortwave_sum_9_15_wh_m2": round(shortwave_sum, 3),
        "dominant_weather_code_7_17": dominant_code,
        "dominant_weather_class_7_17": _weather_class(dominant_code),
        "mean_temp_c_7_17": round(sum(temp_values) / len(temp_values), 3) if temp_values else None,
    }


def _hourly_weather_records_from_open_meteo(
    hourly: dict[str, object],
    *,
    target_date: str,
    suffix: str = "",
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    times = hourly.get("time", [])
    if not isinstance(times, list):
        return out
    for idx, raw_time in enumerate(times):
        time_text = str(raw_time)
        if not time_text.startswith(f"{target_date}T"):
            continue
        try:
            hour = int(time_text.split("T", 1)[1].split(":", 1)[0])
        except (IndexError, ValueError):
            continue
        weather_code = _to_optional_int(_list_value(hourly.get(f"weather_code{suffix}"), idx))
        out.append(
            {
                "time": time_text,
                "hour": hour,
                "weather_code": weather_code,
                "weather_class": _weather_class(weather_code),
                "precipitation_mm": _to_optional_float(_list_value(hourly.get(f"precipitation{suffix}"), idx)),
                "precipitation_probability": _to_optional_float(
                    _list_value(hourly.get(f"precipitation_probability{suffix}"), idx)
                ),
                "cloud_cover": _to_optional_float(_list_value(hourly.get(f"cloud_cover{suffix}"), idx)),
                "shortwave_radiation_w_m2": _to_optional_float(
                    _list_value(hourly.get(f"shortwave_radiation{suffix}"), idx)
                ),
                "temp_c": _to_optional_float(_list_value(hourly.get(f"temperature_2m{suffix}"), idx)),
                "relative_humidity_percent": _to_optional_float(
                    _list_value(hourly.get(f"relative_humidity_2m{suffix}"), idx)
                ),
                "dew_point_c": _to_optional_float(_list_value(hourly.get(f"dew_point_2m{suffix}"), idx)),
            }
        )
    return out


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
            "relative_humidity_2m_previous_day1,dew_point_2m_previous_day1"
        ),
    }
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    hourly = resp.json().get("hourly", {})
    if not isinstance(hourly, dict):
        raise RuntimeError("Open-Meteo previous runs response has no hourly data")
    hourly_weather = _hourly_weather_records_from_open_meteo(hourly, target_date=target_date, suffix=suffix)
    if not hourly_weather:
        raise RuntimeError(f"Open-Meteo previous runs hourly forecast is empty: {target_date}")
    summary = _hourly_weather_summary(hourly_weather)
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


def _forecast_for_date(lat: float, lon: float, timezone: str, *, target_date: str | None = None) -> dict[str, object]:
    url = "https://api.open-meteo.com/v1/forecast"
    params: dict[str, str | float | int] = {
        "latitude": lat,
        "longitude": lon,
        "hourly": (
            "weather_code,precipitation,precipitation_probability,cloud_cover,shortwave_radiation,"
            "temperature_2m,relative_humidity_2m,dew_point_2m"
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
    hourly_weather = _hourly_weather_records_from_open_meteo(hourly, target_date=forecast_date) if isinstance(hourly, dict) else []
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
        "hourly_weather_summary": _hourly_weather_summary(hourly_weather),
    }


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


def _weather_archive_cache_path() -> Path:
    return Path(os.getenv("WEATHER_ARCHIVE_CACHE_PATH", "artifacts/weather_archive_cache.json"))


def _load_weather_archive_cache(path: Path) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    if not path.exists():
        return {}, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("rows", {}) if isinstance(payload, dict) else {}
        if not isinstance(rows, dict):
            raise ValueError("cache rows must be an object")
        return {
            str(day): dict(row)
            for day, row in rows.items()
            if isinstance(row, dict)
        }, []
    except Exception as exc:
        return {}, [{
            "stage": "cache_read",
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }]


def _save_weather_archive_cache(path: Path, rows_by_date: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": 1, "rows": rows_by_date}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return []
    except Exception as exc:
        return [{
            "stage": "cache_write",
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }]


def _consecutive_date_chunks(days: list[date], *, chunk_days: int) -> list[list[date]]:
    chunks: list[list[date]] = []
    for day in days:
        if not chunks or len(chunks[-1]) >= chunk_days or day != chunks[-1][-1] + timedelta(days=1):
            chunks.append([day])
        else:
            chunks[-1].append(day)
    return chunks


def _weather_rows_from_daily(daily: object) -> list[dict[str, object]]:
    if not isinstance(daily, dict):
        raise ValueError("daily weather payload must be an object")
    times = daily.get("time", [])
    if not isinstance(times, list):
        raise ValueError("daily.time must be a list")
    out: list[dict[str, object]] = []
    for idx, raw_day in enumerate(times):
        try:
            date.fromisoformat(str(raw_day))
        except ValueError:
            continue
        weather_code = _to_optional_int(_list_value(daily.get("weather_code"), idx))
        sunshine_s = _to_optional_float(_list_value(daily.get("sunshine_duration"), idx))
        mean_temperature = _to_optional_float(_list_value(daily.get("temperature_2m_mean"), idx))
        if mean_temperature is None:
            continue
        out.append(
            {
                "date": str(raw_day),
                "temp": mean_temperature,
                "weather_code": weather_code if weather_code is not None else "unknown",
                "sunshine_hours": (sunshine_s / 3600.0) if sunshine_s is not None else 0.0,
                "precipitation": _to_optional_float(_list_value(daily.get("precipitation_sum"), idx)) or 0.0,
                "shortwave_radiation_sum_mj_m2": _to_optional_float(
                    _list_value(daily.get("shortwave_radiation_sum"), idx)
                )
                or 0.0,
            }
        )
    return out


def _archive_weather_history(
    rows: list[dict[str, Any]],
    *,
    lat: float,
    lon: float,
    timezone: str,
) -> WeatherHistoryFetchResult:
    dates = sorted(
        {
            r["dt"].date()
            for r in rows
            if isinstance(r.get("dt"), datetime)
        }
    )
    if not dates:
        return WeatherHistoryFetchResult([], [], [], [], [], [], [])
    requested_days = [dates[0] + timedelta(days=offset) for offset in range((dates[-1] - dates[0]).days + 1)]
    requested_dates = [day.isoformat() for day in requested_days]
    cache_path = _weather_archive_cache_path()
    cached_rows, errors = _load_weather_archive_cache(cache_path)
    rows_by_date = {
        day: cached_rows[day]
        for day in requested_dates
        if day in cached_rows
    }
    cache_hit_dates = sorted(rows_by_date)
    missing_days = [day for day in requested_days if day.isoformat() not in rows_by_date]
    chunk_days = max(1, int(_env_float("WEATHER_ARCHIVE_CHUNK_DAYS", 14.0)))
    url = "https://archive-api.open-meteo.com/v1/archive"
    requested_periods: list[dict[str, object]] = []
    timeout_seconds = max(1.0, _env_float("WEATHER_ARCHIVE_TIMEOUT_SECONDS", 30.0))
    for chunk in _consecutive_date_chunks(missing_days, chunk_days=chunk_days):
        params: dict[str, str | float] = {
            "latitude": lat,
            "longitude": lon,
            "start_date": chunk[0].isoformat(),
            "end_date": chunk[-1].isoformat(),
            "daily": "sunshine_duration,temperature_2m_mean,weather_code,precipitation_sum,shortwave_radiation_sum",
            "timezone": timezone,
        }
        period: dict[str, object] = {
            "start_date": params["start_date"],
            "end_date": params["end_date"],
            "requested_day_count": len(chunk),
        }
        resp: object | None = None
        try:
            resp = requests.get(url, params=params, timeout=timeout_seconds)
            period["http_status"] = getattr(resp, "status_code", None)
            resp.raise_for_status()
            payload = resp.json()
            fetched_rows = _weather_rows_from_daily(payload.get("daily") if isinstance(payload, dict) else None)
            allowed_dates = {day.isoformat() for day in chunk}
            for weather_row in fetched_rows:
                weather_date = str(weather_row["date"])
                if weather_date in allowed_dates:
                    rows_by_date[weather_date] = weather_row
            period["received_day_count"] = sum(1 for day in allowed_dates if day in rows_by_date)
        except Exception as exc:
            period["received_day_count"] = 0
            errors.append({
                "stage": "http_fetch",
                "start_date": str(params["start_date"]),
                "end_date": str(params["end_date"]),
                "http_status": getattr(resp, "status_code", None),
                "exception_type": type(exc).__name__,
                "message": str(exc),
            })
        requested_periods.append(period)

    received_dates = sorted(day for day in requested_dates if day in rows_by_date)
    missing_dates = sorted(set(requested_dates) - set(received_dates))
    if received_dates and set(received_dates) != set(cache_hit_dates):
        errors.extend(_save_weather_archive_cache(cache_path, {**cached_rows, **rows_by_date}))
    return WeatherHistoryFetchResult(
        rows=[rows_by_date[day] for day in received_dates],
        requested_dates=requested_dates,
        received_dates=received_dates,
        missing_dates=missing_dates,
        errors=errors,
        cache_hit_dates=cache_hit_dates,
        requested_periods=requested_periods,
    )


def _archive_weather_rows(
    rows: list[dict[str, Any]],
    *,
    lat: float,
    lon: float,
    timezone: str,
) -> list[dict[str, object]]:
    return _archive_weather_history(rows, lat=lat, lon=lon, timezone=timezone).rows


def _forecast_weather_row(forecast: dict[str, object]) -> dict[str, object]:
    precip = _to_optional_float(forecast.get("precipitation_sum_mm"))
    if precip is None:
        # Probability is not the same unit as precipitation, but this keeps a weak rain signal
        # for fallback forecast APIs that do not return a daily precipitation sum.
        probability = _to_optional_float(forecast.get("precipitation_probability_mean"))
        precip = (probability / 100.0) if probability is not None else 0.0
    return {
        "date": forecast["date"],
        "temp": _to_optional_float(forecast.get("temp_c")) or 0.0,
        "weather_code": forecast.get("weather_code") if forecast.get("weather_code") is not None else "unknown",
        "sunshine_hours": _to_optional_float(forecast.get("sun_hours")) or 0.0,
        "precipitation": precip,
    }


def _load_rows_for_consumption_forecast(rows: list[dict[str, Any]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in rows:
        dt = row.get("dt")
        if not isinstance(dt, datetime):
            continue
        out.append({"dt": dt, "load": float(row.get("load", 0.0) or 0.0)})
    return out


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


def _pv_forecast_totals(pv_forecast: dict[str, object] | None) -> dict[str, object]:
    if not pv_forecast or not pv_forecast.get("enabled"):
        return {}
    totals = pv_forecast.get("totals", {})
    return totals if isinstance(totals, dict) else {}


def _hourly_pv_kwh_from_forecast(
    pv_forecast: dict[str, object] | None,
    *,
    target_date: str,
) -> dict[int, float]:
    out: dict[int, float] = {}
    if not isinstance(pv_forecast, dict) or not pv_forecast.get("enabled"):
        return out
    hourly = pv_forecast.get("hourly", [])
    if not isinstance(hourly, list):
        return out
    for row in hourly:
        if not isinstance(row, dict):
            continue
        raw_time = str(row.get("time") or "").strip()
        if not raw_time:
            continue
        try:
            dt = datetime.fromisoformat(raw_time)
        except ValueError:
            continue
        if dt.date().isoformat() != target_date:
            continue
        if dt.hour < 7 or dt.hour >= 23:
            continue
        kwh = _to_optional_float(row.get("total_kwh")) or 0.0
        out[dt.hour] = out.get(dt.hour, 0.0) + max(0.0, kwh)
    return out


def _historical_hourly_profile(
    rows: list[dict[str, Any]],
    *,
    key: str,
    start_hour: int,
    end_hour_exclusive: int,
) -> dict[int, float]:
    values_by_day_hour: dict[tuple[date, int], dict[int, float]] = {}
    for row in rows:
        dt = row.get("dt")
        if not isinstance(dt, datetime):
            continue
        if dt.hour < start_hour or dt.hour >= end_hour_exclusive:
            continue
        if dt.minute not in {0, 30}:
            continue
        val = max(0.0, float(row.get(key, 0.0) or 0.0))
        values_by_day_hour.setdefault((dt.date(), dt.hour), {})[dt.minute] = val

    complete_values_by_hour: dict[int, list[float]] = {}
    for (_, hour), interval_values in values_by_day_hour.items():
        if set(interval_values) != {0, 30}:
            continue
        complete_values_by_hour.setdefault(hour, []).append(sum(interval_values.values()))

    out: dict[int, float] = {}
    for hour in range(start_hour, end_hour_exclusive):
        values = complete_values_by_hour.get(hour, [])
        out[hour] = statistics.mean(values) if values else 0.0
    return out


def _normalize_profile(profile: dict[int, float], *, hours: list[int]) -> dict[int, float]:
    values = {h: max(0.0, profile.get(h, 0.0)) for h in hours}
    total = sum(values.values())
    if total <= 0:
        uniform = 1.0 / len(hours) if hours else 0.0
        return {h: uniform for h in hours}
    return {h: values[h] / total for h in hours}


def _build_hourly_load_forecast(
    rows: list[dict[str, Any]],
    *,
    daytime_load_kwh: float,
    morning_load_kwh: float,
    overnight_load_by_hour: dict[int, float] | None = None,
) -> dict[int, float]:
    overnight_hours = [0, 1, 2, 3, 4, 5, 6, 23]
    morning_hours = [7, 8, 9]
    daytime_rest_hours = list(range(10, 23))
    early_overnight_profile = _historical_hourly_profile(rows, key="load", start_hour=0, end_hour_exclusive=7)
    late_overnight_profile = _historical_hourly_profile(rows, key="load", start_hour=23, end_hour_exclusive=24)
    morning_profile_raw = _historical_hourly_profile(rows, key="load", start_hour=7, end_hour_exclusive=10)
    rest_profile_raw = _historical_hourly_profile(rows, key="load", start_hour=10, end_hour_exclusive=23)
    morning_profile = _normalize_profile(morning_profile_raw, hours=morning_hours)
    rest_profile = _normalize_profile(rest_profile_raw, hours=daytime_rest_hours)

    morning_total = max(0.0, morning_load_kwh)
    daytime_total = max(0.0, daytime_load_kwh)
    rest_total = max(0.0, daytime_total - morning_total)

    out: dict[int, float] = {}
    for h in overnight_hours:
        out[h] = early_overnight_profile.get(h, late_overnight_profile.get(h, 0.0))
    for h in morning_hours:
        out[h] = morning_total * morning_profile[h]
    for h in daytime_rest_hours:
        out[h] = rest_total * rest_profile[h]
    if overnight_load_by_hour:
        for h, value in overnight_load_by_hour.items():
            if 0 <= int(h) <= 23:
                out[int(h)] = max(0.0, float(value or 0.0))
    return out


def _build_hourly_pv_forecast(
    rows: list[dict[str, Any]],
    *,
    pv_forecast: dict[str, object] | None,
    target_date: str,
    fallback_total_kwh: float,
) -> dict[int, float]:
    from_forecast = _hourly_pv_kwh_from_forecast(pv_forecast, target_date=target_date)
    if from_forecast and sum(max(0.0, value) for value in from_forecast.values()) > 0:
        return from_forecast

    hours = list(range(7, 23))
    pv_profile_raw = _historical_hourly_profile(rows, key="pv", start_hour=7, end_hour_exclusive=23)
    pv_profile = _normalize_profile(pv_profile_raw, hours=hours)
    total = max(0.0, fallback_total_kwh)
    return {h: total * pv_profile[h] for h in hours}


def _reshape_hourly_pv_by_weather(
    hourly_pv_kwh: dict[int, float],
    forecast: dict[str, object],
) -> tuple[dict[int, float], dict[str, object]]:
    if not _env_bool("HOURLY_WEATHER_PV_SHAPE_ENABLED", True):
        return hourly_pv_kwh, {"enabled": False, "reason": "disabled"}
    hourly_weather = forecast.get("hourly_weather")
    if not isinstance(hourly_weather, list):
        return hourly_pv_kwh, {"enabled": False, "reason": "no_hourly_weather"}

    weights: dict[int, float] = {}
    for row in hourly_weather:
        if not isinstance(row, dict):
            continue
        hour = _to_optional_int(row.get("hour"))
        if hour is None or hour < 7 or hour >= 23:
            continue
        shortwave = _to_optional_float(row.get("shortwave_radiation_w_m2"))
        if shortwave is None:
            continue
        weights[hour] = max(0.0, shortwave)
    if not weights or sum(weights.values()) <= 0:
        return hourly_pv_kwh, {"enabled": False, "reason": "no_positive_shortwave"}

    original_total = sum(max(0.0, value) for value in hourly_pv_kwh.values())
    if original_total <= 0:
        return hourly_pv_kwh, {"enabled": False, "reason": "no_hourly_pv_total"}

    total_weight = sum(weights.values())
    reshaped = {hour: original_total * (weights.get(hour, 0.0) / total_weight) for hour in range(7, 23)}
    blend = _env_float_clamped("HOURLY_WEATHER_PV_SHAPE_BLEND", 0.75, min_value=0.0, max_value=1.0)
    out: dict[int, float] = {}
    for hour in range(7, 23):
        original = max(0.0, hourly_pv_kwh.get(hour, 0.0))
        out[hour] = original * (1.0 - blend) + reshaped.get(hour, 0.0) * blend
    return out, {
        "enabled": True,
        "method": "blend_existing_pv_shape_with_hourly_shortwave",
        "blend": blend,
        "source": forecast.get("source"),
        "original_total_kwh": round(original_total, 4),
        "reshaped_total_kwh": round(sum(out.values()), 4),
        "shortwave_hours": sorted(weights),
    }


def _morning_pv_headroom_guard(
    *,
    hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float],
    effective_capacity_kwh_value: float,
    reserve_soc_percent: float,
) -> dict[str, object]:
    enabled = _env_bool("MORNING_PV_HEADROOM_GUARD_ENABLED", True)
    hours = [7, 8, 9]
    morning_pv = sum(max(0.0, hourly_pv_kwh.get(hour, 0.0)) for hour in hours)
    morning_load = sum(max(0.0, hourly_load_kwh.get(hour, 0.0)) for hour in hours)
    morning_deficit = max(0.0, morning_load - morning_pv)
    capacity = max(0.0, effective_capacity_kwh_value)
    guard_ratio = max(
        0.0,
        min(1.0, float(os.getenv("MORNING_PV_HEADROOM_GUARD_RATIO", "0.50").strip() or "0.50")),
    )
    min_guard_kwh = max(
        0.0,
        float(os.getenv("MORNING_PV_HEADROOM_GUARD_MIN_KWH", "0.20").strip() or "0.20"),
    )
    guard_headroom = max(0.0, morning_pv * guard_ratio - morning_deficit)
    applied = enabled and capacity > 0 and guard_headroom >= min_guard_kwh
    cap_target_soc = 100.0
    if applied:
        cap_target_soc = max(
            reserve_soc_percent,
            100.0 - (guard_headroom / capacity * 100.0),
        )
    return {
        "enabled": enabled,
        "applied": applied,
        "hours": hours,
        "guard_ratio": guard_ratio,
        "min_guard_kwh": min_guard_kwh,
        "morning_pv_kwh": morning_pv,
        "morning_load_kwh": morning_load,
        "morning_deficit_kwh": morning_deficit,
        "guard_headroom_kwh": guard_headroom,
        "cap_target_soc_percent": max(0.0, min(100.0, cap_target_soc)),
    }


def _daytime_net_surplus_headroom_guard(
    *,
    hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float],
    forecast: dict[str, object],
    effective_capacity_kwh_value: float,
    reserve_soc_percent: float,
) -> dict[str, object]:
    enabled = _env_bool("DAYTIME_NET_SURPLUS_HEADROOM_GUARD_ENABLED", True)
    hours = list(range(7, 18))
    solar_hours = list(range(9, 16))
    net_by_hour = {
        hour: max(0.0, hourly_pv_kwh.get(hour, 0.0) - hourly_load_kwh.get(hour, 0.0))
        for hour in hours
    }
    expected_surplus = sum(net_by_hour.values())
    solar_surplus = sum(net_by_hour.get(hour, 0.0) for hour in solar_hours)
    min_surplus = _env_float("DAYTIME_NET_SURPLUS_HEADROOM_MIN_KWH", 1.0)
    guard_ratio = _env_float_clamped("DAYTIME_NET_SURPLUS_HEADROOM_RATIO", 0.65, min_value=0.0, max_value=1.0)
    max_guard_kwh = _env_float("DAYTIME_NET_SURPLUS_HEADROOM_MAX_KWH", 6.0)
    min_solar_surplus_share = _env_float_clamped(
        "DAYTIME_NET_SURPLUS_HEADROOM_MIN_SOLAR_SHARE",
        0.55,
        min_value=0.0,
        max_value=1.0,
    )
    summary = forecast.get("hourly_weather_summary")
    rain_hours = 0
    low_shortwave_hours = 0
    if isinstance(summary, dict):
        rain_hours = int(_to_optional_float(summary.get("rain_hours_7_17")) or 0)
        low_shortwave_hours = int(_to_optional_float(summary.get("low_shortwave_hours_9_15")) or 0)
    rain_relax_hours = int(_env_float("DAYTIME_NET_SURPLUS_HEADROOM_RAIN_RELAX_HOURS", 7.0))
    low_shortwave_relax_hours = int(_env_float("DAYTIME_NET_SURPLUS_HEADROOM_LOW_SHORTWAVE_RELAX_HOURS", 5.0))
    solar_share = solar_surplus / expected_surplus if expected_surplus > 0 else 0.0
    rainy_or_low_radiation = rain_hours >= rain_relax_hours or low_shortwave_hours >= low_shortwave_relax_hours
    usable_surplus = min(max_guard_kwh, expected_surplus * guard_ratio)
    capacity = max(0.0, effective_capacity_kwh_value)
    applied = bool(
        enabled
        and capacity > 0
        and expected_surplus >= min_surplus
        and solar_share >= min_solar_surplus_share
        and not rainy_or_low_radiation
    )
    cap_target_soc = 100.0
    if applied:
        cap_target_soc = max(reserve_soc_percent, 100.0 - (usable_surplus / capacity * 100.0))

    if not enabled:
        reason = "disabled"
    elif expected_surplus < min_surplus:
        reason = "insufficient_net_surplus"
    elif solar_share < min_solar_surplus_share:
        reason = "surplus_not_concentrated_in_solar_hours"
    elif rainy_or_low_radiation:
        reason = "rain_or_low_radiation_relaxed"
    else:
        reason = "ok"

    return {
        "enabled": enabled,
        "applied": applied,
        "reason": reason,
        "hours": hours,
        "solar_hours": solar_hours,
        "expected_net_surplus_kwh": round(expected_surplus, 4),
        "solar_net_surplus_kwh": round(solar_surplus, 4),
        "solar_surplus_share": round(solar_share, 4),
        "guard_ratio": guard_ratio,
        "min_surplus_kwh": min_surplus,
        "max_guard_kwh": max_guard_kwh,
        "usable_headroom_kwh": round(usable_surplus if applied else 0.0, 4),
        "cap_target_soc_percent": round(max(0.0, min(100.0, cap_target_soc)), 3),
        "rain_hours_7_17": rain_hours,
        "low_shortwave_hours_9_15": low_shortwave_hours,
        "net_surplus_by_hour_kwh": {str(hour): round(net_by_hour[hour], 4) for hour in hours},
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    p = max(0.0, min(100.0, percentile)) / 100.0
    pos = (len(ordered) - 1) * p
    lo = int(pos)
    hi = min(len(ordered) - 1, lo + 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _historical_daytime_soc_gain_guard(
    rows: list[dict[str, Any]],
    *,
    reserve_soc_percent: float,
    target_date: str,
) -> dict[str, object]:
    """Cap morning SOC using observed PV-driven SOC gain.

    This is a guardrail, not the main optimizer. It prevents low PV forecasts
    from selecting a near-full morning SOC while the historical record still
    says some daytime charging headroom should be preserved.
    """

    enabled = _env_bool("HISTORICAL_DAYTIME_SOC_GAIN_GUARD_ENABLED", True)
    percentile = _env_float_clamped("HISTORICAL_DAYTIME_SOC_GAIN_PERCENTILE", 25.0, min_value=0.0, max_value=100.0)
    floor_percent = _env_float_clamped("HISTORICAL_DAYTIME_SOC_GAIN_FLOOR_PERCENT", 15.0, min_value=0.0, max_value=100.0)
    min_days = max(1, int(_env_float("HISTORICAL_DAYTIME_SOC_GAIN_MIN_DAYS", 5.0)))
    long_term_days = max(1, int(_env_float("HISTORICAL_DAYTIME_SOC_GAIN_LONG_TERM_DAYS", 180.0)))
    recent_days = max(1, int(_env_float("HISTORICAL_DAYTIME_SOC_GAIN_RECENT_DAYS", 30.0)))
    max_morning_soc = _env_float_clamped("HISTORICAL_DAYTIME_SOC_GAIN_MAX_MORNING_SOC", 70.0, min_value=0.0, max_value=100.0)
    full_soc_threshold = _env_float_clamped("HISTORICAL_DAYTIME_SOC_GAIN_FULL_SOC_THRESHOLD", 98.0, min_value=0.0, max_value=100.0)
    min_pv_kwh = max(0.0, _env_float("HISTORICAL_DAYTIME_SOC_GAIN_MIN_PV_KWH", 0.1))
    min_samples = max(1, int(_env_float("HISTORICAL_DAYTIME_SOC_GAIN_MIN_SAMPLES", 30.0)))

    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        dt = row.get("dt")
        if not isinstance(dt, datetime):
            continue
        by_day.setdefault(dt.date().isoformat(), []).append(row)

    candidates: list[dict[str, object]] = []
    excluded = {
        "incomplete": 0,
        "low_pv": 0,
        "missing_morning_soc": 0,
        "full_soc_clipped": 0,
        "high_morning_soc": 0,
        "future_or_target_day": 0,
    }

    for day, day_rows in sorted(by_day.items()):
        if day >= target_date:
            excluded["future_or_target_day"] += 1
            continue
        day_rows = sorted(
            day_rows,
            key=lambda x: x["dt"] if isinstance(x.get("dt"), datetime) else datetime.min,
        )
        if len(day_rows) < min_samples:
            excluded["incomplete"] += 1
            continue
        last_dt = day_rows[-1]["dt"]
        if not isinstance(last_dt, datetime) or last_dt.hour < 18:
            excluded["incomplete"] += 1
            continue

        total_pv = sum(float(r.get("pv", 0.0) or 0.0) for r in day_rows)
        if total_pv < min_pv_kwh:
            excluded["low_pv"] += 1
            continue

        morning_rows = [
            r for r in day_rows
            if isinstance(r.get("dt"), datetime) and 6 <= r["dt"].hour <= 8
        ]
        if not morning_rows:
            excluded["missing_morning_soc"] += 1
            continue
        morning = min(
            morning_rows,
            key=lambda r: abs(
                ((r["dt"].hour * 60 + r["dt"].minute) - 420)
                if isinstance(r.get("dt"), datetime)
                else 10_000
            ),
        )
        morning_soc = _to_optional_float(morning.get("soc"))
        if morning_soc is None or morning_soc != morning_soc:
            excluded["missing_morning_soc"] += 1
            continue

        day_soc_values: list[float] = []
        for row in day_rows:
            row_dt = row.get("dt")
            if not isinstance(row_dt, datetime) or not 5 <= row_dt.hour <= 18:
                continue
            soc_value = _to_optional_float(row.get("soc"))
            if soc_value is not None and soc_value == soc_value:
                day_soc_values.append(soc_value)
        if not day_soc_values:
            excluded["incomplete"] += 1
            continue
        max_soc = max(day_soc_values)
        if max_soc >= full_soc_threshold:
            excluded["full_soc_clipped"] += 1
            continue
        if morning_soc > max_morning_soc:
            excluded["high_morning_soc"] += 1
            continue

        gain = max(0.0, max_soc - morning_soc)
        candidates.append(
            {
                "date": day,
                "morning_soc_percent": round(morning_soc, 3),
                "max_daytime_soc_percent": round(max_soc, 3),
                "daytime_soc_gain_percent": round(gain, 3),
                "pv_kwh": round(total_pv, 4),
            }
        )

    if len(candidates) >= long_term_days:
        selected = candidates[-recent_days:]
        source_window = f"recent_{recent_days}_days"
    else:
        selected = candidates
        source_window = "all_available_until_180_days"

    gains = [_to_optional_float(x.get("daytime_soc_gain_percent")) or 0.0 for x in selected]
    percentile_gain = _percentile(gains, percentile)
    applied = bool(enabled and percentile_gain is not None and len(gains) >= min_days)
    guard_gain = max(floor_percent, percentile_gain or 0.0) if applied else 0.0
    cap_target_soc = 100.0
    if applied:
        cap_target_soc = max(0.0, min(100.0, 100.0 - guard_gain))
        cap_target_soc = max(reserve_soc_percent, cap_target_soc)

    return {
        "enabled": enabled,
        "applied": applied,
        "reason": "ok" if applied else ("disabled" if not enabled else "insufficient_history"),
        "target_date": target_date,
        "source_window": source_window,
        "sample_count": len(gains),
        "total_candidate_days": len(candidates),
        "percentile": percentile,
        "percentile_gain_percent": round(percentile_gain, 3) if percentile_gain is not None else None,
        "floor_percent": floor_percent,
        "guard_gain_percent": round(guard_gain, 3),
        "cap_target_soc_percent": round(cap_target_soc, 3),
        "reserve_soc_percent": reserve_soc_percent,
        "selection_rules": {
            "max_morning_soc_percent": max_morning_soc,
            "full_soc_threshold_percent": full_soc_threshold,
            "min_pv_kwh": min_pv_kwh,
            "min_samples_per_day": min_samples,
        },
        "excluded_counts": excluded,
        "lowest_days": sorted(
            selected,
            key=lambda x: _to_optional_float(x.get("daytime_soc_gain_percent")) or 0.0,
        )[:8],
    }


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
    totals = _pv_forecast_totals(pv_forecast)
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
    load_rows = _load_rows_for_consumption_forecast(context.rows)
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
        weather_row=_forecast_weather_row(context.forecast),
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
    pv_totals = _pv_forecast_totals(pv_array_forecast)
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


def _build_selected_pv_forecast(
    context: EnergyModelContext,
    consumption: ConsumptionForecastBundle,
    night_charge: NightChargePreparation,
) -> PvForecastBundle:
    config = context.config
    pv_array_forecast = night_charge.pv_array_forecast
    raw_hourly_load = _build_hourly_load_forecast(
        context.rows,
        daytime_load_kwh=consumption.daily.daytime_load_kwh,
        morning_load_kwh=consumption.daily.morning_load_kwh,
        overnight_load_by_hour=None,
    )
    raw_hourly_pv = _build_hourly_pv_forecast(
        context.rows,
        pv_forecast=pv_array_forecast,
        target_date=context.target_date,
        fallback_total_kwh=night_charge.result.predicted_pv_kwh,
    )
    raw_hourly_pv, hourly_weather_shape = _reshape_hourly_pv_by_weather(
        raw_hourly_pv,
        context.forecast,
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
    active = [
        SocConstraint(
            name=name,
            applied=bool(guard.get("applied")),
            cap_target_soc_percent=_to_optional_float(guard.get("cap_target_soc_percent")),
            reason=str(guard.get("reason") or ""),
            evidence=dict(guard),
        )
        for (name, _), guard in zip(raw_guards, annotated)
        if guard.get("applied")
    ]
    max_target_soc = 100.0
    if apply_caps:
        for guard in annotated:
            if guard.get("applied") or guard is annotated[0]:
                max_target_soc = min(
                    max_target_soc,
                    _soc_cap_or_unbounded(guard.get("cap_target_soc_percent")),
                )
    return SocConstraintSet(
        reserve_soc_percent=reserve_soc,
        max_target_soc_percent=max_target_soc,
        apply_pv_headroom_caps=apply_caps,
        active_constraints=active,
        morning_headroom=annotated[0],
        daytime_net_surplus=annotated[1],
        historical_soc_gain=annotated[2],
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
                    "scenario_method": "pv_sigma_x_load_scenarios_with_weather_upside",
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
