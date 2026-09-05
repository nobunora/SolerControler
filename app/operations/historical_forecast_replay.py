"""Build auditable historical forecast replays without invoking control paths.

A replay is a later model estimate. It must never be presented as an original
contemporaneous forecast. The builder deliberately accepts the full available
history and enforces its own target-date cutoff so callers cannot accidentally
leak target-day or future measurements into training/calibration.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, cast
from zoneinfo import ZoneInfo

import requests

from app.energy_plan.forecast_inputs import (
    build_hourly_load_forecast,
    load_rows_for_consumption_forecast,
)
from app.energy_plan.settings import ForecastSettings, HistoricalInputSettings
from app.energy_plan.weather import WeatherHistoryFetchResult
from app.energy_plan.weather_history import (
    archive_weather_history,
    weather_class,
)
from app.forecasting.consumption import forecast_daily_consumption
from app.forecasting.pv_array import PVArrayConfig, load_pv_array_configs
from app.forecasting.pv_array_calibration import (
    PvCalibrationInput,
    PvCalibrationPolicy,
    calibrate_performance_ratio_for,
)
from app.parsing.numbers import parse_csv_float, to_float


REPLAY_BASIS = "historical_model_replay"
REPLAY_SOURCE = "historical_reconstructed_estimate"
REPLAY_MODEL_VERSION = "historical-forecast-replay-v1"
SINGLE_RUN_ENDPOINT = "https://single-runs-api.open-meteo.com/v1/forecast"
DEFAULT_SINGLE_RUN_MODEL = "jma_msm"

HttpGet = Callable[..., Any]
WeatherHistoryLoader = Callable[..., WeatherHistoryFetchResult]
CalibrationBuilder = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class HistoricalReplayResult:
    plan: dict[str, Any]
    forecast_hash: str
    eligible_history_row_count: int
    history_start: str | None
    history_end: str | None


def _target_day(target_date: str) -> date:
    try:
        return date.fromisoformat(target_date)
    except ValueError as exc:
        raise ValueError("target_date must be YYYY-MM-DD") from exc


def default_single_run_for_target(target_date: str) -> str:
    """Return a conservative JMA MSM run known to be available before 02:30 JST.

    JMA MSM runs every three hours. D-1 12:00 UTC is 21:00 JST on the previous
    day and therefore leaves several hours for model publication before the
    02:30 JST forecast-owner window.
    """
    run_day = _target_day(target_date) - timedelta(days=1)
    return f"{run_day.isoformat()}T12:00"


def filter_pre_target_history(
    rows: list[dict[str, Any]],
    *,
    target_date: str,
) -> list[dict[str, Any]]:
    """Keep only measured rows strictly before target-date midnight (local data time)."""
    target = _target_day(target_date)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        timestamp = row.get("dt")
        if not isinstance(timestamp, datetime):
            continue
        if timestamp.date() < target:
            filtered.append(row)
    filtered.sort(key=lambda row: row["dt"])
    return filtered


def _history_bounds(rows: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    timestamps: list[datetime] = []
    for row in rows:
        timestamp = row.get("dt")
        if isinstance(timestamp, datetime):
            timestamps.append(timestamp)
    if not timestamps:
        return None, None
    return min(timestamps).isoformat(), max(timestamps).isoformat()


def _response_payload(response: Any, *, label: str) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} returned a non-object JSON payload")
    return payload


def _single_run_params(
    *,
    settings: ForecastSettings,
    target_date: str,
    model: str,
    run: str,
    hourly: str,
    tilt: float | None = None,
    azimuth: float | None = None,
) -> dict[str, str | float]:
    params: dict[str, str | float] = {
        "latitude": settings.latitude,
        "longitude": settings.longitude,
        "timezone": settings.timezone,
        "start_date": target_date,
        "end_date": target_date,
        "models": model,
        "run": run,
        "hourly": hourly,
    }
    if tilt is not None:
        params["tilt"] = tilt
    if azimuth is not None:
        params["azimuth"] = azimuth
    return params


def _target_hour_indexes(hourly: dict[str, Any], *, target_date: str) -> list[tuple[int, int, str]]:
    times = hourly.get("time")
    if not isinstance(times, list):
        raise RuntimeError("single-runs response hourly.time is missing")
    indexed: list[tuple[int, int, str]] = []
    seen_hours: set[int] = set()
    for index, raw_time in enumerate(times):
        text = str(raw_time)
        if not text.startswith(f"{target_date}T"):
            continue
        try:
            hour = int(text.split("T", 1)[1].split(":", 1)[0])
        except (IndexError, ValueError) as exc:
            raise RuntimeError(f"single-runs response has invalid time: {text}") from exc
        if hour in seen_hours:
            raise RuntimeError(f"single-runs response contains duplicate target hour: {hour}")
        seen_hours.add(hour)
        indexed.append((index, hour, text))
    if seen_hours != set(range(24)) or len(indexed) != 24:
        raise RuntimeError(
            f"single-runs target date must contain exactly 24 unique hours: {target_date}"
        )
    indexed.sort(key=lambda item: item[1])
    return indexed


def _list_value(values: object, index: int) -> object | None:
    if not isinstance(values, list) or index >= len(values):
        return None
    return cast(object, values[index])


def fetch_single_run_weather(
    *,
    settings: ForecastSettings,
    target_date: str,
    model: str,
    run: str,
    http_get: HttpGet = requests.get,
) -> dict[str, Any]:
    hourly_variables = (
        "temperature_2m,relative_humidity_2m,dew_point_2m,precipitation,"
        "weather_code,cloud_cover,shortwave_radiation,sunshine_duration,wind_speed_10m"
    )
    response = http_get(
        SINGLE_RUN_ENDPOINT,
        params=_single_run_params(
            settings=settings,
            target_date=target_date,
            model=model,
            run=run,
            hourly=hourly_variables,
        ),
        timeout=30,
    )
    payload = _response_payload(response, label="Open-Meteo Single Runs weather")
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise RuntimeError("single-runs weather response has no hourly object")
    indexes = _target_hour_indexes(hourly, target_date=target_date)

    normalized: list[dict[str, object]] = []
    daytime_codes: list[int] = []
    daytime_temps: list[float] = []
    precipitation_sum = 0.0
    sunshine_seconds = 0.0
    shortwave_sum_wh_m2 = 0.0
    for index, hour, text in indexes:
        code_raw = to_float(_list_value(hourly.get("weather_code"), index))
        code = int(code_raw) if code_raw is not None else None
        temp = to_float(_list_value(hourly.get("temperature_2m"), index))
        precipitation = to_float(_list_value(hourly.get("precipitation"), index))
        shortwave = to_float(_list_value(hourly.get("shortwave_radiation"), index))
        sunshine = to_float(_list_value(hourly.get("sunshine_duration"), index))
        if 7 <= hour < 18:
            if code is not None:
                daytime_codes.append(code)
            if temp is not None:
                daytime_temps.append(temp)
        precipitation_sum += max(0.0, precipitation or 0.0)
        sunshine_seconds += max(0.0, sunshine or 0.0)
        shortwave_sum_wh_m2 += max(0.0, shortwave or 0.0)
        normalized.append(
            {
                "time": text,
                "hour": hour,
                "weather_code": code,
                "weather_class": weather_class(code),
                "precipitation_mm": precipitation,
                "cloud_cover": to_float(_list_value(hourly.get("cloud_cover"), index)),
                "shortwave_radiation_w_m2": shortwave,
                "sunshine_duration_seconds": sunshine,
                "temp_c": temp,
                "relative_humidity_percent": to_float(
                    _list_value(hourly.get("relative_humidity_2m"), index)
                ),
                "dew_point_c": to_float(_list_value(hourly.get("dew_point_2m"), index)),
                "wind_speed_10m": to_float(_list_value(hourly.get("wind_speed_10m"), index)),
            }
        )

    dominant_code = (
        max(set(daytime_codes), key=daytime_codes.count) if daytime_codes else None
    )
    mean_temp = (
        sum(daytime_temps) / len(daytime_temps) if daytime_temps else None
    )
    return {
        "date": target_date,
        "sun_hours": sunshine_seconds / 3600.0,
        "temp_c": mean_temp,
        "weather_code": dominant_code,
        "weather_class": weather_class(dominant_code),
        "precipitation_sum_mm": precipitation_sum,
        "shortwave_radiation_sum_mj_m2": shortwave_sum_wh_m2 * 3600.0 / 1_000_000.0,
        "hourly_weather": normalized,
        "source": "open-meteo-single-runs",
        "historical_forecast": {
            "provider": "open-meteo",
            "endpoint_family": "single-runs-api",
            "endpoint": "single-runs-api.open-meteo.com",
            "model": model,
            "run": run,
            "run_timezone": "UTC",
            "target_timezone": settings.timezone,
            "target_hourly_count": 24,
        },
    }


def _normalize_weather_class(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"clear", "sunny"}:
        return "clear"
    if text in {"cloud", "cloudy", "overcast"}:
        return "cloudy"
    if text in {"rain", "rainy", "drizzle", "showers"}:
        return "rain"
    if text in {"storm", "thunder", "thunderstorm"}:
        return "storm"
    return text or "unknown"


def _effective_calibration_factor(
    calibration: dict[str, Any],
    *,
    forecast: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    """Apply the same target-weather calibration semantics as the normal PV path."""
    base_factor = parse_csv_float(calibration.get("factor"), default=1.0)
    target_class = _normalize_weather_class(forecast.get("weather_class"))
    multiplier = 1.0
    strategy = "base"
    adjustments = calibration.get("weather_adjustments")
    if isinstance(adjustments, dict):
        entry = adjustments.get(target_class)
        if isinstance(entry, dict):
            multiplier = parse_csv_float(entry.get("multiplier"), default=1.0)
            strategy = "class_multiplier"
    effective = max(0.0, base_factor * multiplier)

    regression = calibration.get("weather_regression")
    if (
        target_class in {"cloudy", "rain"}
        and isinstance(regression, dict)
        and str(regression.get("status")) == "fitted"
    ):
        coefficients = regression.get("coefficients")
        sun_hours = to_float(forecast.get("sun_hours"))
        precipitation = to_float(forecast.get("precipitation_sum_mm"))
        if isinstance(coefficients, dict) and sun_hours is not None and precipitation is not None:
            intercept = parse_csv_float(coefficients.get("intercept"), default=base_factor)
            coef_sun = parse_csv_float(coefficients.get("sunshine_hours"), default=0.0)
            coef_precip = parse_csv_float(coefficients.get("precipitation_sum_mm"), default=0.0)
            blend = max(0.0, min(1.0, parse_csv_float(regression.get("blend"), default=0.1)))
            minimum = parse_csv_float(regression.get("min_factor"), default=0.2)
            maximum = parse_csv_float(regression.get("max_factor"), default=5.0)
            if minimum > maximum:
                minimum, maximum = maximum, minimum
            regression_factor = max(
                minimum,
                min(maximum, intercept + coef_sun * sun_hours + coef_precip * precipitation),
            )
            effective = max(0.0, base_factor * (1.0 - blend) + regression_factor * blend)
            multiplier = effective / base_factor if base_factor > 0 else 1.0
            strategy = "regression_blend"

    return effective, {
        "base_factor": base_factor,
        "target_weather_class": target_class,
        "weather_multiplier": multiplier,
        "effective_factor": effective,
        "adjustment_strategy": strategy,
        "calibration_source": calibration.get("source"),
        "calibration_sample_days": calibration.get("sample_days"),
    }


def fetch_single_run_pv_forecast(
    *,
    rows: list[dict[str, Any]],
    arrays: list[PVArrayConfig],
    settings: ForecastSettings,
    target_date: str,
    forecast: dict[str, Any],
    model: str,
    run: str,
    http_get: HttpGet = requests.get,
    calibration_builder: CalibrationBuilder = calibrate_performance_ratio_for,
) -> tuple[dict[int, float], dict[str, Any]]:
    if not arrays:
        raise RuntimeError("historical replay requires at least one PV array configuration")
    calibration = calibration_builder(
        PvCalibrationInput(
            arrays=arrays,
            rows=rows,
            target_date=target_date,
            latitude=settings.latitude,
            longitude=settings.longitude,
            timezone=settings.timezone,
        ),
        PvCalibrationPolicy.from_env(),
        http_get=http_get,
    )
    effective_factor, calibration_summary = _effective_calibration_factor(
        calibration,
        forecast=forecast,
    )

    hourly_total = {hour: 0.0 for hour in range(24)}
    array_summaries: list[dict[str, Any]] = []
    for array in arrays:
        response = http_get(
            SINGLE_RUN_ENDPOINT,
            params=_single_run_params(
                settings=settings,
                target_date=target_date,
                model=model,
                run=run,
                hourly="global_tilted_irradiance,temperature_2m",
                tilt=array.tilt_deg,
                azimuth=array.azimuth_deg,
            ),
            timeout=30,
        )
        payload = _response_payload(response, label=f"Open-Meteo Single Runs PV {array.name}")
        hourly = payload.get("hourly")
        if not isinstance(hourly, dict):
            raise RuntimeError(f"single-runs PV response has no hourly object: {array.name}")
        indexes = _target_hour_indexes(hourly, target_date=target_date)
        array_total = 0.0
        for index, hour, _ in indexes:
            gti = max(
                0.0,
                to_float(_list_value(hourly.get("global_tilted_irradiance"), index)) or 0.0,
            )
            temp_c = to_float(_list_value(hourly.get("temperature_2m"), index))
            if temp_c is None:
                temp_c = 25.0
            temp_factor = max(0.0, 1.0 + array.temp_coeff_per_deg * (temp_c - 25.0))
            kwh = max(
                0.0,
                array.capacity_kw
                * (gti / 1000.0)
                * array.performance_ratio
                * array.shading_factor
                * effective_factor
                * temp_factor,
            )
            hourly_total[hour] += kwh
            array_total += kwh
        array_summaries.append(
            {
                "name": array.name,
                "tilt_deg": array.tilt_deg,
                "azimuth_deg": array.azimuth_deg,
                "capacity_kw": array.capacity_kw,
                "forecast_kwh": round(array_total, 6),
            }
        )

    return hourly_total, {
        "provider": "open-meteo",
        "endpoint_family": "single-runs-api",
        "model": model,
        "run": run,
        "target_date": target_date,
        "target_timezone": settings.timezone,
        "calibration": calibration_summary,
        "arrays": array_summaries,
    }


def _forecast_weather_row(forecast: dict[str, Any]) -> dict[str, object]:
    return {
        "date": forecast["date"],
        "temp": to_float(forecast.get("temp_c")) or 0.0,
        "weather_code": forecast.get("weather_code")
        if forecast.get("weather_code") is not None
        else "unknown",
        "sunshine_hours": to_float(forecast.get("sun_hours")) or 0.0,
        "precipitation": to_float(forecast.get("precipitation_sum_mm")) or 0.0,
    }


def _complete_hour_map(values: dict[int, float]) -> dict[str, float]:
    return {str(hour): round(max(0.0, float(values.get(hour, 0.0))), 6) for hour in range(24)}


def replay_forecast_hash(plan: dict[str, Any]) -> str:
    optimization = plan.get("daytime_soc_optimization")
    if not isinstance(optimization, dict):
        raise ValueError("replay plan has no daytime_soc_optimization")
    identity = {
        "forecast": plan.get("forecast"),
        "hourly_pv_forecast_kwh": optimization.get("hourly_pv_forecast_kwh"),
        "hourly_load_forecast_kwh": optimization.get("hourly_load_forecast_kwh"),
    }
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def build_historical_replay_plan(
    rows: list[dict[str, Any]],
    *,
    target_date: str,
    forecast_settings: ForecastSettings | None = None,
    historical_settings: HistoricalInputSettings | None = None,
    arrays: list[PVArrayConfig] | None = None,
    model: str = DEFAULT_SINGLE_RUN_MODEL,
    run: str | None = None,
    http_get: HttpGet = requests.get,
    weather_history_loader: WeatherHistoryLoader = archive_weather_history,
    calibration_builder: CalibrationBuilder = calibrate_performance_ratio_for,
) -> HistoricalReplayResult:
    settings = forecast_settings or ForecastSettings.from_env()
    history_settings = historical_settings or HistoricalInputSettings.from_env()
    selected_arrays = arrays if arrays is not None else load_pv_array_configs()
    selected_run = run or default_single_run_for_target(target_date)
    eligible_rows = filter_pre_target_history(rows, target_date=target_date)
    if not eligible_rows:
        raise RuntimeError(f"historical replay has no pre-target history: {target_date}")
    if any(
        isinstance(row.get("dt"), datetime) and row["dt"].date() >= _target_day(target_date)
        for row in eligible_rows
    ):
        raise AssertionError("historical replay cutoff invariant failed")

    forecast = fetch_single_run_weather(
        settings=settings,
        target_date=target_date,
        model=model,
        run=selected_run,
        http_get=http_get,
    )
    weather_history = weather_history_loader(
        eligible_rows,
        lat=settings.latitude,
        lon=settings.longitude,
        timezone=settings.timezone,
    )
    load_rows = load_rows_for_consumption_forecast(eligible_rows)
    consumption = forecast_daily_consumption(
        load_rows,
        weather_history.rows,
        target_date,
        weather_row=_forecast_weather_row(forecast),
        min_training_days=history_settings.min_training_days,
        fallback_window=history_settings.fallback_window_days,
    )
    hourly_load = build_hourly_load_forecast(
        eligible_rows,
        daytime_load_kwh=consumption.daytime_load_kwh,
        morning_load_kwh=consumption.morning_load_kwh,
    )
    hourly_pv, pv_provenance = fetch_single_run_pv_forecast(
        rows=eligible_rows,
        arrays=selected_arrays,
        settings=settings,
        target_date=target_date,
        forecast=forecast,
        model=model,
        run=selected_run,
        http_get=http_get,
        calibration_builder=calibration_builder,
    )
    history_start, history_end = _history_bounds(eligible_rows)
    cutoff = datetime.combine(
        _target_day(target_date),
        datetime.min.time(),
        tzinfo=ZoneInfo(settings.timezone),
    ).isoformat()
    plan: dict[str, Any] = {
        "forecast": forecast,
        "daytime_soc_optimization": {
            "source": REPLAY_BASIS,
            "hourly_pv_forecast_kwh": _complete_hour_map(hourly_pv),
            "hourly_load_forecast_kwh": _complete_hour_map(hourly_load),
        },
        "historical_replay": {
            "source": REPLAY_SOURCE,
            "basis": REPLAY_BASIS,
            "model_version": REPLAY_MODEL_VERSION,
            "history_cutoff": cutoff,
            "eligible_history_start": history_start,
            "eligible_history_end": history_end,
            "eligible_history_row_count": len(eligible_rows),
            "load_model_source": consumption.source,
            "load_model_sample_count": consumption.sample_count,
            "weather_history_received_day_count": len(weather_history.received_dates),
            "weather_history_missing_day_count": len(weather_history.missing_dates),
            "weather_forecast": forecast.get("historical_forecast", {}),
            "pv_forecast": pv_provenance,
        },
    }
    forecast_hash = replay_forecast_hash(plan)
    plan["historical_replay"]["forecast_hash"] = forecast_hash
    return HistoricalReplayResult(
        plan=plan,
        forecast_hash=forecast_hash,
        eligible_history_row_count=len(eligible_rows),
        history_start=history_start,
        history_end=history_end,
    )


def write_historical_replay_plan(result: HistoricalReplayResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.plan, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
