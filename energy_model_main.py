from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime, timedelta
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
    )
    result = compute_night_charge_target(coeff, inp)
    result_payload = to_dict(result)

    hourly_load_forecast = _build_hourly_load_forecast(
        rows,
        daytime_load_kwh=consumption_forecast.daytime_load_kwh,
        morning_load_kwh=consumption_forecast.morning_load_kwh,
    )
    hourly_pv_forecast = _build_hourly_pv_forecast(
        rows,
        pv_forecast=pv_array_forecast if isinstance(pv_array_forecast, dict) else None,
        target_date=tomorrow_date,
        fallback_total_kwh=result.predicted_pv_kwh,
    )
    sunset_hour = _estimate_sunset_hour(hourly_pv_forecast)
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
    )
    optimization_payload: dict[str, object] | None = None
    if daytime_optimization is not None:
        optimized_target_soc = daytime_optimization.target_soc_7_percent
        optimized_required_kwh = daytime_optimization.required_night_charge_kwh

        result_payload["target_soc_7_percent_base"] = result_payload.get("target_soc_7_percent")
        result_payload["required_night_charge_kwh_base"] = result_payload.get("required_night_charge_kwh")
        result_payload["target_soc_7_percent"] = optimized_target_soc
        result_payload["required_night_charge_kwh"] = optimized_required_kwh
        optimization_payload = {
            **to_dict(daytime_optimization),
            "objective": "avoid_daytime_buy_and_sell_then_peak_soc_near_target",
            "target_peak_soc_percent": float(os.getenv("DAYTIME_TARGET_PEAK_SOC_PERCENT", "99.0").strip() or "99.0"),
            "buy_tolerance_kwh": float(os.getenv("DAYTIME_BUY_TOLERANCE_KWH", "0.05").strip() or "0.05"),
            "sell_tolerance_kwh": float(os.getenv("DAYTIME_SELL_TOLERANCE_KWH", "0.10").strip() or "0.10"),
            "target_soc_7_percent_after_peak_objective": optimized_target_soc,
            "required_night_charge_kwh_after_peak_objective": optimized_required_kwh,
            "legacy_pv_headroom_cap": {"applied": False, "reason": "replaced_by_peak_soc_objective"},
            "sunset_hour": sunset_hour,
            "hourly_load_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(hourly_load_forecast.items())},
            "hourly_pv_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(hourly_pv_forecast.items())},
        }

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
    }
    out = artifacts_dir / "night_charge_plan.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
