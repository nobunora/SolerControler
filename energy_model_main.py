from __future__ import annotations

import csv
import json
import os
import statistics
import time
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import requests

from app.consumption_forecast import ConsumptionForecast, forecast_daily_consumption
from app.energy_model import (
    DaytimeSocOptimizationResult,
    NightChargeInputs,
    compute_night_charge_target,
    fit_coefficients_from_csv,
    optimize_target_soc_for_daytime,
    to_dict,
)
from app.occupancy_schedule import (
    apply_occupancy_schedule,
    filter_training_load_rows,
    load_occupancy_events_from_env,
)
from app.pv_array_forecast import build_pv_array_forecast, load_pv_array_configs
from app.soc_cost_optimizer import (
    DEFAULT_SIGMA_BUCKETS,
    ForecastScenario,
    PvForecastUncertainty,
    SocCostModel,
    SigmaBucket,
    optimize_soc_by_expected_cost,
    to_plain_dict,
)
from app.forecast_correction import _build_forecast_correction, _risk_adjusted_peak_penalty


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


def _read_rows(csv_paths: Iterable[Path]) -> list[dict[str, float | datetime]]:
    rows: list[dict[str, float | datetime]] = []
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
    rows.sort(key=lambda x: x["dt"])  # type: ignore[index]
    return rows


def _historical_profile(rows: list[dict[str, float | datetime]]) -> dict[str, float]:
    by_day: dict[str, dict[str, float]] = {}
    for r in rows:
        dt = r["dt"]
        assert isinstance(dt, datetime)
        day = dt.date().isoformat()
        d = by_day.setdefault(day, {"day_load": 0.0, "morning_load": 0.0, "day_pv": 0.0, "morning_pv": 0.0})
        h = dt.hour
        load = float(r["load"])
        pv = float(r["pv"])
        if 7 <= h < 23:
            d["day_load"] += load
            d["day_pv"] += pv
        if 7 <= h < 10:
            d["morning_load"] += load
            d["morning_pv"] += pv

    days = list(by_day.values())
    if not days:
        raise RuntimeError("日次集計対象データがありません")

    avg_day_load = sum(d["day_load"] for d in days) / len(days)
    avg_morning_load = sum(d["morning_load"] for d in days) / len(days)
    sum_day_pv = sum(d["day_pv"] for d in days)
    sum_morning_pv = sum(d["morning_pv"] for d in days)
    morning_pv_ratio = (sum_morning_pv / sum_day_pv) if sum_day_pv > 0 else 0.25

    # 日中余剰比率 (max(0, pv-load) はここでは直接ないので実務初期値)
    midday_surplus_ratio = 0.375
    return {
        "avg_day_load_kwh": avg_day_load,
        "avg_morning_load_kwh": avg_morning_load,
        "morning_pv_ratio": morning_pv_ratio,
        "midday_surplus_ratio": midday_surplus_ratio,
    }


def _to_optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_optional_int(value) -> int | None:
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


def _soc_cost_model_from_env(*, battery_round_trip_efficiency: float) -> SocCostModel:
    """Prices intentionally live in one place so the objective is easy to audit."""

    day_rate = _env_float(
        "SOC_COST_DAY_BUY_RATE_YEN_PER_KWH",
        _env_float("NIGHT8_DAY_RATE_TIER2_YEN", _env_float("DAY_RATE_YEN_PER_KWH", 39.10)),
    )
    night_rate = _env_float("SOC_COST_NIGHT_RATE_YEN_PER_KWH", _env_float("NIGHT8_NIGHT_RATE_YEN", 31.0))
    sell_value_ratio = _env_float_clamped("SOC_COST_SELL_VALUE_RATIO", 0.0, min_value=0.0, max_value=1.0)
    day_buy_penalty = max(0.0, _env_float("SOC_COST_DAY_BUY_PENALTY_FACTOR", 1.0))
    charge_efficiency = _env_float(
        "SOC_COST_USABLE_CHARGE_EFFICIENCY",
        _env_float("SOC_COST_CHARGE_EFFICIENCY", battery_round_trip_efficiency),
    )
    sell_loss_override = _env_float("SOC_COST_SELL_OPPORTUNITY_LOSS_YEN_PER_KWH", 38.75)
    return SocCostModel(
        day_buy_rate_yen_per_kwh=max(0.0, day_rate),
        night_buy_rate_yen_per_kwh=max(0.0, night_rate),
        charge_efficiency=max(0.01, charge_efficiency),
        sell_value_ratio=sell_value_ratio,
        day_buy_penalty_factor=day_buy_penalty,
        sell_opportunity_loss_yen_per_kwh_override=max(0.0, sell_loss_override),
    )


def _list_value(values, index: int):
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


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


def _forecast_for_date(lat: float, lon: float, timezone: str, *, target_date: str | None = None) -> dict[str, object]:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
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
    target_index = 1
    if target_date:
        try:
            target_index = times.index(target_date)
        except ValueError as exc:
            raise RuntimeError(f"指定日の予報を取得できませんでした: {target_date}") from exc
    weather_code = _to_optional_int(_list_value(daily.get("weather_code"), target_index))
    return {
        "date": times[target_index],
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
    }


def _forecast_from_env_or_api(*, lat: float, lon: float, timezone: str) -> dict[str, object]:
    date_override = os.getenv("FORECAST_DATE_OVERRIDE", "").strip()
    sun_override = os.getenv("FORECAST_SUN_HOURS_OVERRIDE", "").strip()
    if sun_override:
        date_override = date_override or datetime.now().date().isoformat()
        temp_override = os.getenv("FORECAST_TEMP_C_OVERRIDE", "").strip() or "20"
        weather_code = _to_optional_int(os.getenv("FORECAST_WEATHER_CODE_OVERRIDE", "").strip() or None)
        return {
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
            "source": "env-override",
        }
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
            "source": "date-only-fallback",
            "daily_forecast_error": str(exc),
        }


def _archive_weather_rows(
    rows: list[dict[str, float | datetime]],
    *,
    lat: float,
    lon: float,
    timezone: str,
) -> list[dict[str, object]]:
    dates = sorted(
        {
            r["dt"].date()
            for r in rows
            if isinstance(r.get("dt"), datetime)
        }
    )
    if not dates:
        return []
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": dates[0].isoformat(),
        "end_date": dates[-1].isoformat(),
        "daily": "sunshine_duration,temperature_2m_mean,weather_code,precipitation_sum,shortwave_radiation_sum",
        "timezone": timezone,
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
    except Exception:
        return []

    out: list[dict[str, object]] = []
    times = daily.get("time", [])
    for idx, raw_day in enumerate(times if isinstance(times, list) else []):
        weather_code = _to_optional_int(_list_value(daily.get("weather_code"), idx))
        sunshine_s = _to_optional_float(_list_value(daily.get("sunshine_duration"), idx))
        out.append(
            {
                "date": raw_day,
                "temp": _to_optional_float(_list_value(daily.get("temperature_2m_mean"), idx)) or 0.0,
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


def _load_rows_for_consumption_forecast(rows: list[dict[str, float | datetime]]) -> list[dict[str, object]]:
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


def _occupancy_adjustment_to_dict(adjustment) -> dict[str, object] | None:
    if adjustment is None:
        return None
    return adjustment.to_dict()


def _build_pv_forecast_or_disabled(
    *,
    rows: list[dict[str, float | datetime]],
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
        return build_pv_array_forecast(
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
    rows: list[dict[str, float | datetime]],
    *,
    key: str,
    start_hour: int,
    end_hour_exclusive: int,
) -> dict[int, float]:
    sums: dict[int, float] = {}
    counts: dict[int, int] = {}
    for row in rows:
        dt = row.get("dt")
        if not isinstance(dt, datetime):
            continue
        if dt.hour < start_hour or dt.hour >= end_hour_exclusive:
            continue
        val = max(0.0, float(row.get(key, 0.0) or 0.0))
        sums[dt.hour] = sums.get(dt.hour, 0.0) + val
        counts[dt.hour] = counts.get(dt.hour, 0) + 1
    out: dict[int, float] = {}
    for hour in range(start_hour, end_hour_exclusive):
        c = counts.get(hour, 0)
        out[hour] = (sums.get(hour, 0.0) / c) if c > 0 else 0.0
    return out


def _normalize_profile(profile: dict[int, float], *, hours: list[int]) -> dict[int, float]:
    values = {h: max(0.0, profile.get(h, 0.0)) for h in hours}
    total = sum(values.values())
    if total <= 0:
        uniform = 1.0 / len(hours) if hours else 0.0
        return {h: uniform for h in hours}
    return {h: values[h] / total for h in hours}


def _build_hourly_load_forecast(
    rows: list[dict[str, float | datetime]],
    *,
    daytime_load_kwh: float,
    morning_load_kwh: float,
) -> dict[int, float]:
    morning_hours = [7, 8, 9]
    daytime_rest_hours = list(range(10, 23))
    morning_profile_raw = _historical_hourly_profile(rows, key="load", start_hour=7, end_hour_exclusive=10)
    rest_profile_raw = _historical_hourly_profile(rows, key="load", start_hour=10, end_hour_exclusive=23)
    morning_profile = _normalize_profile(morning_profile_raw, hours=morning_hours)
    rest_profile = _normalize_profile(rest_profile_raw, hours=daytime_rest_hours)

    morning_total = max(0.0, morning_load_kwh)
    daytime_total = max(0.0, daytime_load_kwh)
    rest_total = max(0.0, daytime_total - morning_total)

    out: dict[int, float] = {}
    for h in morning_hours:
        out[h] = morning_total * morning_profile[h]
    for h in daytime_rest_hours:
        out[h] = rest_total * rest_profile[h]
    return out


def _build_hourly_pv_forecast(
    rows: list[dict[str, float | datetime]],
    *,
    pv_forecast: dict[str, object] | None,
    target_date: str,
    fallback_total_kwh: float,
) -> dict[int, float]:
    from_forecast = _hourly_pv_kwh_from_forecast(pv_forecast, target_date=target_date)
    if from_forecast:
        return from_forecast

    hours = list(range(7, 23))
    pv_profile_raw = _historical_hourly_profile(rows, key="pv", start_hour=7, end_hour_exclusive=23)
    pv_profile = _normalize_profile(pv_profile_raw, hours=hours)
    total = max(0.0, fallback_total_kwh)
    return {h: total * pv_profile[h] for h in hours}


def _estimate_sunset_hour(hourly_pv_kwh: dict[int, float]) -> int:
    active_hours = [h for h, v in hourly_pv_kwh.items() if h >= 7 and h < 23 and v > 0.03]
    if not active_hours:
        return 18
    return max(active_hours)


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
    rows: list[dict[str, float | datetime]],
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

    by_day: dict[str, list[dict[str, float | datetime]]] = {}
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
        day_rows = sorted(day_rows, key=lambda x: x["dt"])  # type: ignore[index]
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
            if isinstance(r.get("dt"), datetime) and 6 <= r["dt"].hour <= 8  # type: ignore[index]
        ]
        if not morning_rows:
            excluded["missing_morning_soc"] += 1
            continue
        morning = min(
            morning_rows,
            key=lambda r: abs((r["dt"].hour * 60 + r["dt"].minute) - 420),  # type: ignore[index]
        )
        morning_soc = _to_optional_float(morning.get("soc"))
        if morning_soc is None or morning_soc != morning_soc:
            excluded["missing_morning_soc"] += 1
            continue

        day_soc_values = [
            _to_optional_float(r.get("soc"))
            for r in day_rows
            if isinstance(r.get("dt"), datetime) and 5 <= r["dt"].hour <= 18  # type: ignore[index]
        ]
        day_soc_values = [v for v in day_soc_values if v is not None and v == v]
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

    gains = [float(x["daytime_soc_gain_percent"]) for x in selected]
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
        "lowest_days": sorted(selected, key=lambda x: float(x["daytime_soc_gain_percent"]))[:8],
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


def _load_scenarios_for_cost_optimizer() -> tuple[ForecastScenario, ...] | None:
    if not _env_bool("SOC_COST_LOAD_SCENARIOS_ENABLED", True):
        return None
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
    return net_surplus


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


def _estimate_remaining_overnight_discharge_kwh(
    rows: list[dict[str, float | datetime]],
    *,
    target_date: str,
) -> dict[str, object]:
    if not _env_bool("OVERNIGHT_DISCHARGE_GUARD_ENABLED", True):
        return {"enabled": False, "expected_kwh": 0.0, "reason": "disabled"}
    if not rows:
        return {"enabled": True, "expected_kwh": 0.0, "reason": "no_rows"}

    latest_dt = rows[-1].get("dt")
    if not isinstance(latest_dt, datetime):
        return {"enabled": True, "expected_kwh": 0.0, "reason": "missing_latest_dt"}
    try:
        target = date.fromisoformat(target_date)
    except ValueError:
        return {"enabled": True, "expected_kwh": 0.0, "reason": "invalid_target_date"}

    cutoff = _parse_hhmm(os.getenv("OVERNIGHT_DISCHARGE_GUARD_CUTOFF_HHMM", "07:00"), default="07:00")
    latest_clock = latest_dt.time()
    cutoff_minute = _clock_minutes(cutoff)
    if latest_dt.date() == target and _clock_minutes(latest_clock) >= cutoff_minute:
        return {"enabled": True, "expected_kwh": 0.0, "reason": "past_cutoff", "latest_sample_at": latest_dt.isoformat()}

    lookback_days = int(_env_float("OVERNIGHT_DISCHARGE_GUARD_LOOKBACK_DAYS", 21.0))
    min_days = int(_env_float("OVERNIGHT_DISCHARGE_GUARD_MIN_DAYS", 3.0))
    percentile = _env_float_clamped("OVERNIGHT_DISCHARGE_GUARD_PERCENTILE", 75.0, min_value=0.0, max_value=100.0)
    floor_kwh = max(0.0, _env_float("OVERNIGHT_DISCHARGE_GUARD_FLOOR_KWH", 0.0))
    cap_kwh = max(0.0, _env_float("OVERNIGHT_DISCHARGE_GUARD_CAP_KWH", 6.0))

    by_day: dict[str, float] = {}
    lower_bound = target - timedelta(days=max(1, lookback_days))
    latest_minute = _clock_minutes(latest_dt.time())
    for row in rows:
        row_dt = row.get("dt")
        if not isinstance(row_dt, datetime):
            continue
        row_minute = _clock_minutes(row_dt.time())
        candidate_target: date | None = None
        if row_minute > latest_minute:
            candidate_target = row_dt.date() + timedelta(days=1)
        elif row_minute < cutoff_minute:
            candidate_target = row_dt.date()
        if candidate_target is None or not (lower_bound <= candidate_target < target):
            continue
        discharge = max(0.0, float(row.get("discharge", row.get("dchg", 0.0)) or 0.0))
        charge = max(0.0, float(row.get("charge", row.get("chg", 0.0)) or 0.0))
        key = candidate_target.isoformat()
        by_day[key] = by_day.get(key, 0.0) + max(0.0, discharge - charge)

    samples = [value for value in by_day.values() if value > 0.0]
    if len(samples) < min_days:
        return {
            "enabled": True,
            "expected_kwh": floor_kwh,
            "reason": "insufficient_history",
            "sample_count": len(samples),
            "latest_sample_at": latest_dt.isoformat(),
            "cutoff_hhmm": cutoff.strftime("%H:%M"),
        }

    samples.sort()
    if len(samples) == 1:
        estimate = samples[0]
    else:
        estimate = float(statistics.quantiles(samples, n=100, method="inclusive")[max(0, int(percentile) - 1)])
    estimate = min(cap_kwh, max(floor_kwh, estimate))
    return {
        "enabled": True,
        "expected_kwh": estimate,
        "reason": "history_percentile",
        "sample_count": len(samples),
        "percentile": percentile,
        "latest_sample_at": latest_dt.isoformat(),
        "cutoff_hhmm": cutoff.strftime("%H:%M"),
        "sample_days": sorted(by_day)[-min(10, len(by_day)):],
    }


def main() -> int:
    _load_dotenv_if_present()
    artifacts_dir = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
    csv_paths = _csv_paths_from_env_or_latest(artifacts_dir)
    rows = _read_rows(csv_paths)
    coeff = fit_coefficients_from_csv(csv_paths)
    hist = _historical_profile(rows)
    lat = float(os.getenv("FORECAST_LATITUDE", "35.67452"))
    lon = float(os.getenv("FORECAST_LONGITUDE", "139.48216"))
    timezone = os.getenv("TIMEZONE", "Asia/Tokyo")

    forecast = _forecast_from_env_or_api(lat=lat, lon=lon, timezone=timezone)
    tomorrow_date = str(forecast["date"])
    sun_h = float(forecast["sun_hours"])
    temp_c = float(forecast["temp_c"])

    occupancy_events = load_occupancy_events_from_env()
    load_rows_for_forecast = _load_rows_for_consumption_forecast(rows)
    training_load_rows = filter_training_load_rows(load_rows_for_forecast, occupancy_events)
    base_consumption_forecast = forecast_daily_consumption(
        training_load_rows,
        _archive_weather_rows(rows, lat=lat, lon=lon, timezone=timezone),
        tomorrow_date,
        weather_row=_forecast_weather_row(forecast),
        min_training_days=int(os.getenv("CONSUMPTION_MODEL_MIN_TRAINING_DAYS", "45")),
        fallback_window=int(os.getenv("CONSUMPTION_MODEL_FALLBACK_WINDOW_DAYS", "14")),
    )
    consumption_forecast, occupancy_adjustment = apply_occupancy_schedule(
        base_consumption_forecast,
        occupancy_events,
    )
    pv_array_forecast = _build_pv_forecast_or_disabled(
        rows=rows,
        target_date=tomorrow_date,
        lat=lat,
        lon=lon,
        timezone=timezone,
        target_weather_class=str(forecast.get("weather_class") or ""),
        target_sun_hours=_to_optional_float(forecast.get("sun_hours")),
        target_precipitation_sum_mm=_to_optional_float(forecast.get("precipitation_sum_mm")),
    )
    pv_totals = _pv_forecast_totals(pv_array_forecast)
    predicted_pv_override = _to_optional_float(pv_totals.get("total_kwh"))
    predicted_morning_pv_override = _to_optional_float(pv_totals.get("morning_kwh"))
    predicted_midday_surplus_override = _estimate_midday_surplus_from_pv_forecast(
        pv_forecast=pv_array_forecast,
        consumption_forecast=consumption_forecast,
    )
    if isinstance(pv_array_forecast, dict) and pv_array_forecast.get("enabled"):
        pv_array_forecast["surplus_estimate"] = {
            "midday_surplus_kwh": predicted_midday_surplus_override,
            "method": "net_midday_surplus_without_safety_floor",
            "midday_load_fraction": _to_optional_float(os.getenv("PV_MIDDAY_LOAD_FRACTION", "").strip()) or (6.0 / 13.0),
        }

    latest_soc = float(rows[-1]["soc"]) if rows and rows[-1]["soc"] == rows[-1]["soc"] else 30.0
    overnight_discharge_guard = _estimate_remaining_overnight_discharge_kwh(
        rows,
        target_date=tomorrow_date,
    )
    expected_overnight_discharge_kwh = _to_optional_float(overnight_discharge_guard.get("expected_kwh")) or 0.0
    inp = NightChargeInputs(
        soc_now_percent=latest_soc,
        sun_hours_forecast=sun_h,
        temp_forecast_c=temp_c,
        daytime_load_forecast_kwh=consumption_forecast.daytime_load_kwh,
        morning_load_forecast_kwh=consumption_forecast.morning_load_kwh,
        morning_pv_ratio=hist["morning_pv_ratio"],
        midday_surplus_ratio=hist["midday_surplus_ratio"],
        reserve_soc_percent=float(os.getenv("NIGHT_RESERVE_SOC_PERCENT", "0")),
        cycle_count=float(os.getenv("BATTERY_CYCLE_COUNT", "0")),
        battery_temp_c=float(os.getenv("BATTERY_TEMP_C", str(temp_c))),
        predicted_pv_kwh_override=predicted_pv_override,
        predicted_morning_pv_kwh_override=predicted_morning_pv_override,
        predicted_midday_surplus_kwh_override=predicted_midday_surplus_override,
        expected_overnight_discharge_kwh=expected_overnight_discharge_kwh,
    )
    result = compute_night_charge_target(coeff, inp)
    result_payload = to_dict(result)

    raw_hourly_load_forecast = _build_hourly_load_forecast(
        rows,
        daytime_load_kwh=consumption_forecast.daytime_load_kwh,
        morning_load_kwh=consumption_forecast.morning_load_kwh,
    )
    raw_hourly_pv_forecast = _build_hourly_pv_forecast(
        rows,
        pv_forecast=pv_array_forecast if isinstance(pv_array_forecast, dict) else None,
        target_date=tomorrow_date,
        fallback_total_kwh=result.predicted_pv_kwh,
    )
    forecast_correction = _build_forecast_correction(
        rows=rows,
        hourly_load_forecast=raw_hourly_load_forecast,
        hourly_pv_forecast=raw_hourly_pv_forecast,
        target_date=tomorrow_date,
        lat=lat,
        lon=lon,
        timezone=timezone,
        forecast=forecast,
    )
    hourly_load_forecast = forecast_correction["hourly_load_kwh"]  # type: ignore[assignment]
    hourly_pv_forecast = forecast_correction["hourly_pv_kwh"]  # type: ignore[assignment]
    sunset_hour = _estimate_sunset_hour(hourly_pv_forecast)
    morning_headroom_guard = _morning_pv_headroom_guard(
        hourly_load_kwh=hourly_load_forecast,
        hourly_pv_kwh=hourly_pv_forecast,
        effective_capacity_kwh_value=result.effective_capacity_kwh,
        reserve_soc_percent=inp.reserve_soc_percent,
    )
    historical_soc_gain_guard = _historical_daytime_soc_gain_guard(
        rows,
        reserve_soc_percent=inp.reserve_soc_percent,
        target_date=tomorrow_date,
    )
    legacy_max_target_soc = _to_optional_float(morning_headroom_guard.get("cap_target_soc_percent")) or 100.0
    if historical_soc_gain_guard.get("applied"):
        legacy_max_target_soc = min(
            legacy_max_target_soc,
            _to_optional_float(historical_soc_gain_guard.get("cap_target_soc_percent")) or 100.0,
        )
    daytime_optimization: DaytimeSocOptimizationResult | None = optimize_target_soc_for_daytime(
        effective_capacity_kwh_value=result.effective_capacity_kwh,
        soc_now_percent=latest_soc,
        reserve_soc_percent=inp.reserve_soc_percent,
        battery_round_trip_efficiency=coeff.battery_round_trip_efficiency,
        hourly_load_kwh=hourly_load_forecast,
        hourly_pv_kwh=hourly_pv_forecast,
        sunset_hour=sunset_hour,
        soc_step_percent=float(os.getenv("DAYTIME_SOC_OPT_STEP_PERCENT", "1.0").strip() or "1.0"),
        target_peak_soc_percent=float(os.getenv("DAYTIME_TARGET_PEAK_SOC_PERCENT", "99.0").strip() or "99.0"),
        buy_tolerance_kwh=float(os.getenv("DAYTIME_BUY_TOLERANCE_KWH", "0.05").strip() or "0.05"),
        sell_tolerance_kwh=float(os.getenv("DAYTIME_SELL_TOLERANCE_KWH", "0.10").strip() or "0.10"),
        max_target_soc_percent=legacy_max_target_soc,
    )
    optimization_payload: dict[str, object] | None = None
    legacy_optimization_payload: dict[str, object] | None = None
    if daytime_optimization is not None:
        legacy_optimization_payload = {
            **to_dict(daytime_optimization),
            "objective": "avoid_daytime_buy_and_sell_then_peak_soc_near_target",
            "target_peak_soc_percent": float(os.getenv("DAYTIME_TARGET_PEAK_SOC_PERCENT", "99.0").strip() or "99.0"),
            "buy_tolerance_kwh": float(os.getenv("DAYTIME_BUY_TOLERANCE_KWH", "0.05").strip() or "0.05"),
            "sell_tolerance_kwh": float(os.getenv("DAYTIME_SELL_TOLERANCE_KWH", "0.10").strip() or "0.10"),
            "target_soc_7_percent_after_peak_objective": daytime_optimization.target_soc_7_percent,
            "required_night_charge_kwh_after_peak_objective": daytime_optimization.required_night_charge_kwh,
            "legacy_pv_headroom_cap": {"applied": False, "reason": "replaced_by_peak_soc_objective"},
            "morning_pv_headroom_guard": morning_headroom_guard,
            "historical_daytime_soc_gain_guard": historical_soc_gain_guard,
            "sunset_hour": sunset_hour,
            "hourly_load_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(hourly_load_forecast.items())},
            "hourly_pv_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(hourly_pv_forecast.items())},
        }

    cost_optimization_payload: dict[str, object] | None = None
    cost_optimization_enabled = _env_bool("DAYTIME_SOC_COST_OPTIMIZATION_ENABLED", True)
    if cost_optimization_enabled:
        pv_uncertainty = _apply_uncertainty_floor(
            _pv_uncertainty_from_forecast(pv_array_forecast if isinstance(pv_array_forecast, dict) else None)
        )
        cost_model = _soc_cost_model_from_env(
            battery_round_trip_efficiency=coeff.battery_round_trip_efficiency,
        )
        respect_guard = _env_bool("SOC_COST_RESPECT_MORNING_HEADROOM_CAP", False)
        cost_max_soc = 100.0
        if respect_guard:
            cost_max_soc = _to_optional_float(morning_headroom_guard.get("cap_target_soc_percent")) or 100.0
        if historical_soc_gain_guard.get("applied"):
            cost_max_soc = min(
                cost_max_soc,
                _to_optional_float(historical_soc_gain_guard.get("cap_target_soc_percent")) or 100.0,
            )
        sigma_buckets = _sigma_buckets_for_cost_optimizer()
        load_scenarios = _load_scenarios_for_cost_optimizer()
        weather_upside_probability = _weather_upside_probability_for_cost_optimizer(forecast)
        weather_upside_z = _env_float("SOC_COST_WEATHER_UPSIDE_SCENARIO_Z", 3.5)
        peak_penalty = forecast_correction.get("peak_penalty", {})
        peak_target_soc = _to_optional_float(peak_penalty.get("target_peak_soc_percent") if isinstance(peak_penalty, dict) else None)
        peak_penalty_factor = _to_optional_float(peak_penalty.get("applied_factor") if isinstance(peak_penalty, dict) else None) or 0.0
        peak_penalty_rate = cost_model.day_buy_rate_yen_per_kwh * max(0.0, peak_penalty_factor)
        cost_optimization = optimize_soc_by_expected_cost(
            capacity_kwh=result.effective_capacity_kwh,
            soc_now_percent=latest_soc,
            reserve_soc_percent=inp.reserve_soc_percent,
            hourly_load_kwh=hourly_load_forecast,
            hourly_pv_kwh=hourly_pv_forecast,
            uncertainty=pv_uncertainty,
            cost_model=cost_model,
            soc_step_percent=_env_float("SOC_COST_OPT_STEP_PERCENT", _env_float("DAYTIME_SOC_OPT_STEP_PERCENT", 1.0)),
            max_target_soc_percent=cost_max_soc,
            sigma_buckets=sigma_buckets,
            min_pv_multiplier=_env_float("SOC_COST_MIN_PV_MULTIPLIER", 0.0),
            max_pv_multiplier=_env_float("SOC_COST_MAX_PV_MULTIPLIER", 3.0),
            load_scenarios=load_scenarios,
            weather_upside_probability=weather_upside_probability,
            weather_upside_z=weather_upside_z,
            peak_soc_target_percent=peak_target_soc,
            peak_soc_unmet_penalty_yen_per_kwh=peak_penalty_rate,
            expected_overnight_discharge_kwh=expected_overnight_discharge_kwh,
        )
        if cost_optimization is not None:
            cost_optimization_payload = {
                **to_plain_dict(cost_optimization),
                "objective": (
                    "minimize_night_charge_cost_plus_expected_day_buy_cost_plus_expected_sell_opportunity_loss"
                ),
                "morning_pv_headroom_guard": morning_headroom_guard,
                "historical_daytime_soc_gain_guard": historical_soc_gain_guard,
                "respect_morning_headroom_guard": respect_guard,
                "max_target_soc_percent_after_guards": cost_max_soc,
                "forecast_correction": forecast_correction.get("rationale", {}),
                "overnight_discharge_guard": overnight_discharge_guard,
                "soc_cost_risk": {
                    "expected_day_buy_kwh": cost_optimization.expected_day_buy_kwh_risk,
                    "expected_sell_kwh": cost_optimization.expected_sell_kwh_risk,
                    "worst_case_day_buy_kwh": cost_optimization.worst_case_day_buy_kwh,
                    "worst_case_sell_kwh": cost_optimization.worst_case_sell_kwh,
                    "buy_risk": cost_optimization.buy_risk,
                    "sell_risk": cost_optimization.sell_risk,
                    "peak_unmet_penalty_factor": peak_penalty_factor,
                    "sell_opportunity_loss_yen_per_kwh": cost_model.sell_opportunity_loss_yen_per_kwh,
                    "scenario_count": len(cost_optimization.forecast_scenarios),
                    "scenario_method": "pv_sigma_x_load_scenarios_with_weather_upside",
                    "weather_upside_probability": weather_upside_probability,
                    "weather_upside_z": weather_upside_z,
                },
                "hourly_load_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(hourly_load_forecast.items())},
                "hourly_pv_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(hourly_pv_forecast.items())},
                "legacy_peak_objective": legacy_optimization_payload,
            }
            result_payload["target_soc_7_percent_base"] = result_payload.get("target_soc_7_percent")
            result_payload["required_night_charge_kwh_base"] = result_payload.get("required_night_charge_kwh")
            result_payload["target_soc_7_percent"] = cost_optimization.target_soc_7_percent
            result_payload["required_night_charge_kwh"] = cost_optimization.required_night_charge_kwh
            result_payload["target_soc_7_percent_cost_optimized"] = cost_optimization.target_soc_7_percent
            result_payload["required_night_charge_kwh_cost_optimized"] = cost_optimization.required_night_charge_kwh
            result_payload["soc_expected_total_cost_yen"] = cost_optimization.total_expected_cost_yen
            result_payload["soc_expected_day_buy_kwh"] = cost_optimization.expected_day_buy_kwh
            result_payload["soc_expected_sell_kwh"] = cost_optimization.expected_sell_kwh
            result_payload["soc_expected_peak_unmet_kwh"] = cost_optimization.expected_peak_unmet_kwh
            result_payload["soc_expected_peak_unmet_cost_yen"] = cost_optimization.expected_peak_unmet_cost_yen
            optimization_payload = cost_optimization_payload

    if optimization_payload is None and legacy_optimization_payload is not None:
        result_payload["target_soc_7_percent_base"] = result_payload.get("target_soc_7_percent")
        result_payload["required_night_charge_kwh_base"] = result_payload.get("required_night_charge_kwh")
        result_payload["target_soc_7_percent"] = daytime_optimization.target_soc_7_percent
        result_payload["required_night_charge_kwh"] = daytime_optimization.required_night_charge_kwh
        optimization_payload = legacy_optimization_payload

    coefficients = to_dict(coeff)
    if isinstance(pv_array_forecast, dict) and pv_array_forecast.get("enabled"):
        calibration = pv_array_forecast.get("calibration", {})
        arrays = pv_array_forecast.get("arrays", [])
        if isinstance(calibration, dict):
            factor = _to_optional_float(calibration.get("effective_factor"))
            if factor is None:
                factor = _to_optional_float(calibration.get("factor"))
            if factor is not None:
                coefficients["pv_array_calibration_factor"] = factor
        if isinstance(arrays, list):
            total_capacity = sum(_to_optional_float(a.get("capacity_kw")) or 0.0 for a in arrays if isinstance(a, dict))
            coefficients["pv_array_total_capacity_kw"] = total_capacity
    pv_uncertainty_for_payload = _pv_uncertainty_from_forecast(
        pv_array_forecast if isinstance(pv_array_forecast, dict) else None
    )
    coefficients["pv_forecast_error_ratio_mean"] = pv_uncertainty_for_payload.mean_multiplier
    coefficients["pv_forecast_error_ratio_std"] = pv_uncertainty_for_payload.std_multiplier
    coefficients["pv_forecast_error_ratio_variance"] = pv_uncertainty_for_payload.variance_multiplier
    coefficients["pv_forecast_error_ratio_sample_count"] = float(pv_uncertainty_for_payload.sample_count)

    payload = {
        "csv_paths": [str(p) for p in csv_paths],
        "forecast": forecast,
        "pv_array_forecast": pv_array_forecast,
        "historical_profile": hist,
        "consumption_forecast": _consumption_forecast_to_dict(consumption_forecast),
        "base_consumption_forecast": _consumption_forecast_to_dict(base_consumption_forecast),
        "occupancy_adjustment": _occupancy_adjustment_to_dict(occupancy_adjustment),
        "coefficients": coefficients,
        "inputs": to_dict(inp),
        "result": result_payload,
        "daytime_soc_optimization": optimization_payload,
        "decision_rationale": {
            "objective": (
                "minimize_night_charge_cost_plus_expected_day_buy_cost_plus_expected_sell_opportunity_loss"
                if cost_optimization_payload is not None else "legacy_peak_soc_objective"
            ),
            "historical_daytime_soc_gain_guard": historical_soc_gain_guard,
            "morning_pv_headroom_guard": morning_headroom_guard,
            "forecast_correction": forecast_correction.get("rationale", {}),
            "overnight_discharge_guard": overnight_discharge_guard,
            "pv_uncertainty": to_plain_dict(_apply_uncertainty_floor(pv_uncertainty_for_payload)),
            "final_target_soc_7_percent": result_payload.get("target_soc_7_percent"),
            "final_required_night_charge_kwh": result_payload.get("required_night_charge_kwh"),
        },
    }
    out = artifacts_dir / "night_charge_plan.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
