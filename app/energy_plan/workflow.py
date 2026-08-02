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
    build_forecast_correction,
)
from app.forecasting.correction_history_io import _load_forecast_hourly_history
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
from app.energy_plan.monthly_projection import (
    billing_period_for_target as _billing_period_for_target,
    expected_rest_of_month_day_buy_kwh as _expected_rest_of_month_day_buy_kwh,
    monthly_day_buy_kwh_before_target as _monthly_day_buy_kwh_before_target,
)
from app.energy_plan.plan_quality import (
    _annotate_pv_headroom_guard_policy,
    _build_plan_quality,
    _candidate_reason_summary,
    _decision_cost_breakdown,
    _env_bool,
    _env_float,
    _env_float_clamped,
    _list_value,
    _physical_pv_uncertainty_from_diagnostics,
    _pv_uncertainty_from_forecast,
    _selected_pv_uncertainty,
    _soc_cap_or_unbounded,
    _soc_cost_model_from_env,
    _soc_decision_target_features,
    _to_optional_float,
    _to_optional_int,
    _uses_physical_pv_forecast,
)
from app.energy_plan.pv_selection import _build_selected_pv_forecast
from app.energy_plan.result_builder import _build_energy_model_output
from app.energy_plan.optimization import LegacyOptimizationDecision, OptimizationDecision


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
    from app.energy_plan.optimization import apply_uncertainty_floor

    return apply_uncertainty_floor(uncertainty)


def _sigma_buckets_for_cost_optimizer() -> tuple[SigmaBucket, ...]:
    from app.energy_plan.optimization import sigma_buckets

    return sigma_buckets()


def _load_scenarios_for_cost_optimizer(
    forecast_correction: dict[str, object] | None = None,
) -> tuple[ForecastScenario, ...] | None:
    from app.energy_plan.optimization import load_scenarios

    return load_scenarios(forecast_correction)


def _weather_upside_probability_for_cost_optimizer(forecast: dict[str, object]) -> float:
    from app.energy_plan.optimization import weather_upside_probability

    return weather_upside_probability(forecast)


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
    from app.energy_plan.optimization import paired_scenarios

    return paired_scenarios(forecast_correction)


# readable-code-audit: skip STRUCT-04 — candidate selection and PV provenance must remain coupled in the generated plan
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
    from app.energy_plan.optimization import run_legacy_optimizer

    return run_legacy_optimizer(
        capacity_kwh=night_charge.result.effective_capacity_kwh, soc_now_percent=context.latest_soc_percent,
        reserve_soc_percent=night_charge.inputs.reserve_soc_percent, battery_round_trip_efficiency=context.coefficients.battery_round_trip_efficiency,
        hourly_load_kwh=pv_forecast.hourly_load_kwh, hourly_pv_kwh=pv_forecast.hourly_pv_kwh, sunset_hour=pv_forecast.sunset_hour,
        soc_step_percent=config.daytime_soc_step_percent, target_peak_soc_percent=config.daytime_target_peak_soc_percent,
        buy_tolerance_kwh=config.daytime_buy_tolerance_kwh, sell_tolerance_kwh=config.daytime_sell_tolerance_kwh,
        max_target_soc_percent=constraints.max_target_soc_percent, morning_headroom=constraints.morning_headroom,
        daytime_net_surplus=constraints.daytime_net_surplus, historical_soc_gain=constraints.historical_soc_gain,
        hourly_weather_shape=pv_forecast.hourly_weather_shape, physical_diagnostics=pv_forecast.physical_diagnostics,
    )


# readable-code-audit: skip STRUCT-04 — this orchestration function keeps model inputs, candidate evaluation, and persisted decision metadata on one snapshot
def _run_soc_optimization(
    context: EnergyModelContext,
    night_charge: NightChargePreparation,
    pv_forecast: PvForecastBundle,
    constraints: SocConstraintSet,
    legacy: LegacyOptimizationDecision,
) -> OptimizationDecision:
    from app.energy_plan.optimization import run_current_optimizer

    return run_current_optimizer(
        context,
        night_charge,
        pv_forecast,
        constraints,
        legacy,
        optimize_request=optimize_soc_request,
        prior_loader=load_soc_decision_prior_from_firestore,
        target_features_builder=_soc_decision_target_features,
    )

# readable-code-audit: skip STRUCT-04 — output sections share one model snapshot and published provenance metadata
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
