from __future__ import annotations

import csv
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Any

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.forecasting.comfort_load import build_comfort_feature_map as comfort_feature_map
from app.forecasting.comfort_load import predict_hourly_comfort_load
from app.energy_plan.energy_model import forecast_pv_energy_kwh, fit_coefficients_from_csv
from app.energy_plan.workflow import _build_hourly_load_forecast, _build_hourly_pv_forecast, _reshape_hourly_pv_by_weather

# These trailing windows capture both immediate weather and one-day persistence without mixing future data.
WINDOWS = (1, 3, 6, 12, 24)
TRAINING_DAYS = 45
TARGET_HOURS = range(0, 24)
# Match the production daytime control window when comparing PV forecast errors.
PV_HOURS = range(7, 23)


@dataclass(frozen=True)
class HourlyRow:
    ts: datetime
    load_kwh: float
    pv_kwh: float


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            out[key] = value
    return out


def _setting(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    return _parse_env_file(Path(".env")).get(name, default)


def _today_jst() -> date:
    tz_name = _setting("TIMEZONE", "Asia/Tokyo") or "Asia/Tokyo"
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        return datetime.now(timezone(timedelta(hours=9))).date()


def _latest_kpnet_run_dir() -> Path:
    candidates = sorted(
        (p for p in Path("artifacts").glob("*/kpnet_summary.json") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("No kpnet_summary.json files were found under artifacts/")
    return candidates[0].parent


def _load_latest_target_rows(target_date: str) -> list[dict[str, Any]]:
    run_dir = _latest_kpnet_run_dir()
    csv_dir = run_dir / "csv"
    rows: list[dict[str, Any]] = []
    for path in sorted(csv_dir.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if (row.get("年月日") or "").strip().replace("/", "-").startswith(target_date):
                    rows.append(row)
    return rows


def _parse_kpnet_csv_rows(rows: list[dict[str, Any]]) -> list[HourlyRow]:
    out: list[HourlyRow] = []
    for row in rows:
        raw_day = (row.get("年月日") or "").strip()
        raw_time = (row.get("時刻") or "").strip()
        if not raw_day or not raw_time:
            continue
        try:
            dt = datetime.strptime(f"{raw_day} {raw_time}", "%Y/%m/%d %H:%M")
        except ValueError:
            continue
        try:
            load = float((row.get("消費電力量[kWh]") or "0").strip() or 0.0)
            pv = float((row.get("発電電力量[kWh]") or "0").strip() or 0.0)
        except ValueError:
            continue
        out.append(HourlyRow(ts=dt, load_kwh=max(0.0, load), pv_kwh=max(0.0, pv)))
    return out


def _load_monitoring_history(start_date: date, target_date: date) -> list[HourlyRow]:
    db_path = Path("artifacts/solar_monitor.db")
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT ts, pv_kwh, load_kwh
            FROM monitoring_samples
            WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) < ?
            ORDER BY ts
            """,
            (start_date.isoformat(), target_date.isoformat()),
        ).fetchall()
    finally:
        conn.close()
    out: list[HourlyRow] = []
    for row in rows:
        try:
            ts = datetime.fromisoformat(str(row["ts"]))
        except ValueError:
            continue
        out.append(
            HourlyRow(
                ts=ts,
                load_kwh=max(0.0, float(row["load_kwh"] or 0.0)),
                pv_kwh=max(0.0, float(row["pv_kwh"] or 0.0)),
            )
        )
    return out


def _aggregate_hourly(rows: list[HourlyRow]) -> dict[date, dict[int, dict[str, float]]]:
    by_day: dict[date, dict[int, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: {"load": 0.0, "pv": 0.0}))
    for row in rows:
        bucket = by_day[row.ts.date()][row.ts.hour]
        bucket["load"] += row.load_kwh
        bucket["pv"] += row.pv_kwh
    return {day: dict(hours) for day, hours in by_day.items()}


def _daily_from_hourly(hourly: dict[date, dict[int, dict[str, float]]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for day, by_hour in hourly.items():
        load = sum(item["load"] for item in by_hour.values())
        pv = sum(item["pv"] for item in by_hour.values())
        out[day.isoformat()] = {"load_kwh": load, "pv_kwh": pv}
    return out


def _fetch_archive(*, lat: float, lon: float, timezone_name: str, start_date: str, end_date: str) -> tuple[dict[str, dict[int, dict[str, float]]], dict[str, dict[str, Any]]]:
    url = "https://archive-api.open-meteo.com/v1/archive"
    hourly_params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": "temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m",
        "timezone": timezone_name,
    }
    daily_params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "sunshine_duration,temperature_2m_mean,weather_code,precipitation_sum,shortwave_radiation_sum",
        "timezone": timezone_name,
    }
    hourly_resp = requests.get(url, params=hourly_params, timeout=60)
    hourly_resp.raise_for_status()
    daily_resp = requests.get(url, params=daily_params, timeout=60)
    daily_resp.raise_for_status()

    hourly_payload = hourly_resp.json().get("hourly", {})
    times = hourly_payload.get("time", [])
    temps = hourly_payload.get("temperature_2m", [])
    humidity = hourly_payload.get("relative_humidity_2m", [])
    dew_points = hourly_payload.get("dew_point_2m", [])
    winds = hourly_payload.get("wind_speed_10m", [])
    hourly: dict[str, dict[int, dict[str, float]]] = defaultdict(dict)
    for raw_time, raw_temp, raw_humidity, raw_dew, raw_wind in zip(times, temps, humidity, dew_points, winds):
        try:
            dt = datetime.fromisoformat(str(raw_time))
        except ValueError:
            continue
        hourly[dt.date().isoformat()][dt.hour] = {
            "temp_c": float(raw_temp),
            "relative_humidity_percent": float(raw_humidity),
            "dew_point_c": float(raw_dew),
            "wind_speed_10m": max(0.0, float(raw_wind)),
        }

    daily_payload = daily_resp.json().get("daily", {})
    daily_times = daily_payload.get("time", [])
    daily_temp = daily_payload.get("temperature_2m_mean", [])
    daily_sun = daily_payload.get("sunshine_duration", [])
    daily_weather_code = daily_payload.get("weather_code", [])
    daily_precip = daily_payload.get("precipitation_sum", [])
    daily_shortwave = daily_payload.get("shortwave_radiation_sum", [])
    daily: dict[str, dict[str, Any]] = {}
    for idx, raw_day in enumerate(daily_times):
        day = str(raw_day)
        daily[day] = {
            "date": day,
            "temp": float(daily_temp[idx]),
            "weather_code": int(float(daily_weather_code[idx])),
            "sunshine_hours": float(daily_sun[idx]) / 3600.0,
            "precipitation": max(0.0, float(daily_precip[idx])),
            "shortwave_radiation_sum_mj_m2": max(0.0, float(daily_shortwave[idx])),
        }
    return dict(hourly), daily


def _fetch_forecast_shortwave(*, lat: float, lon: float, timezone_name: str, target_date: str) -> dict[str, dict[int, float]]:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": target_date,
        "end_date": target_date,
        "hourly": "shortwave_radiation_w_m2",
        "timezone": timezone_name,
    }
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    hourly_payload = resp.json().get("hourly", {})
    times = hourly_payload.get("time", [])
    shortwave = hourly_payload.get("shortwave_radiation_w_m2", [])
    out: dict[str, dict[int, float]] = defaultdict(dict)
    for raw_time, raw_shortwave in zip(times, shortwave):
        try:
            dt = datetime.fromisoformat(str(raw_time))
        except ValueError:
            continue
        out[dt.date().isoformat()][dt.hour] = max(0.0, float(raw_shortwave))
    return dict(out)


def _hourly_weather_map(hourly_weather_by_day: dict[str, dict[int, dict[str, float]]]) -> dict[datetime, dict[str, float]]:
    out: dict[datetime, dict[str, float]] = {}
    for day, by_hour in hourly_weather_by_day.items():
        for hour, values in by_hour.items():
            out[datetime.fromisoformat(f"{day}T{hour:02d}:00:00")] = values
    return dict(sorted(out.items()))


def _rolling_mean(values: list[float], window: int, index: int) -> float:
    start = max(0, index - window + 1)
    subset = values[start : index + 1]
    return fmean(subset) if subset else 0.0


def _linear_fit(x_rows: list[list[float]], y: list[float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = np.asarray(x_rows, dtype=float)
    y_vec = np.asarray(y, dtype=float)
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std = np.where(std < 1e-9, 1.0, std)
    xz = (x - mean) / std
    x_aug = np.column_stack([np.ones(len(xz)), xz])
    coef, *_ = np.linalg.lstsq(x_aug, y_vec, rcond=None)
    return coef, mean, std


def _predict_linear(coef: np.ndarray, mean: np.ndarray, std: np.ndarray, x_rows: list[list[float]]) -> np.ndarray:
    x = np.asarray(x_rows, dtype=float)
    xz = (x - mean) / std
    return np.maximum(0.0, coef[0] + xz @ coef[1:])


def _r2_score(y_true: list[float], y_pred: np.ndarray) -> float:
    actual = np.asarray(y_true, dtype=float)
    if len(actual) == 0:
        return float("nan")
    residual = float(np.sum((actual - y_pred) ** 2))
    total = float(np.sum((actual - actual.mean()) ** 2))
    return 1.0 - residual / total if total > 0 else float("nan")


def _mae(y_true: list[float], y_pred: np.ndarray) -> float:
    actual = np.asarray(y_true, dtype=float)
    return float(np.mean(np.abs(actual - y_pred))) if len(actual) else float("nan")


def _build_model_rows(
    *,
    timestamps: list[datetime],
    weather_by_ts: dict[datetime, dict[str, float]],
    target_by_ts: dict[datetime, float],
    target_name: str,
) -> tuple[list[list[float]], list[float], list[str]]:
    weather_order = [weather_by_ts[ts] for ts in timestamps if ts in weather_by_ts]
    weather_values = {
        "temp_c": [row["temp_c"] for row in weather_order],
        "relative_humidity_percent": [row["relative_humidity_percent"] for row in weather_order],
        "dew_point_c": [row["dew_point_c"] for row in weather_order],
        "wind_speed_10m": [row["wind_speed_10m"] for row in weather_order],
        "shortwave_radiation_w_m2": [row["shortwave_radiation_w_m2"] for row in weather_order],
    }

    feature_names = list(comfort_feature_map(timestamps[0], weather_by_ts).keys())
    cumulative_names = []
    for metric in ("temp_c", "relative_humidity_percent", "wind_speed_10m", "shortwave_radiation_w_m2"):
        for window in WINDOWS:
            cumulative_names.append(f"{metric}_mean_{window}h")
    interaction_names = [
        "temp_x_humidity_24h",
        "temp_x_wind_24h",
        "humidity_x_wind_24h",
        "shortwave_x_temp_24h",
        "shortwave_x_wind_24h",
    ]
    feature_names.extend(cumulative_names)
    feature_names.extend(interaction_names)

    x_rows: list[list[float]] = []
    y_rows: list[float] = []
    for index, ts in enumerate(timestamps):
        if ts not in weather_by_ts or ts not in target_by_ts:
            continue
        base = comfort_feature_map(ts, weather_by_ts)
        row: list[float] = [float(base[name]) for name in comfort_feature_map(ts, weather_by_ts).keys()]
        for metric in ("temp_c", "relative_humidity_percent", "wind_speed_10m", "shortwave_radiation_w_m2"):
            series = weather_values[metric][: index + 1]
            for window in WINDOWS:
                row.append(_rolling_mean(series, window, len(series) - 1))
        temp24 = row[len(base) + 3]
        humidity24 = row[len(base) + 8]
        wind24 = row[len(base) + 13]
        shortwave24 = row[len(base) + 18]
        row.extend(
            [
                temp24 * humidity24,
                temp24 * wind24,
                humidity24 * wind24,
                shortwave24 * temp24,
                shortwave24 * wind24,
            ]
        )
        x_rows.append(row)
        y_rows.append(target_by_ts[ts])
    return x_rows, y_rows, feature_names


def _group_contributions(
    *,
    coef: np.ndarray,
    mean: np.ndarray,
    std: np.ndarray,
    feature_names: list[str],
    x_row: list[float],
) -> dict[str, float]:
    z = (np.asarray(x_row, dtype=float) - mean) / std
    per_feature = np.abs(coef[1:] * z)
    groups: dict[str, float] = defaultdict(float)
    for name, value in zip(feature_names, per_feature):
        if name.startswith("temp_c_mean_"):
            groups["temperature_windows"] += float(value)
        elif name.startswith("relative_humidity_percent_mean_"):
            groups["humidity_windows"] += float(value)
        elif name.startswith("wind_speed_10m_mean_"):
            groups["wind_windows"] += float(value)
        elif name.startswith("shortwave_radiation_w_m2_mean_"):
            groups["shortwave_windows"] += float(value)
        elif name in {"temp_x_humidity_24h", "temp_x_wind_24h", "humidity_x_wind_24h", "shortwave_x_temp_24h", "shortwave_x_wind_24h"}:
            groups["interactions"] += float(value)
        elif name in {"prevailing_temp", "adaptive_comfort_center", "comfort_delta", "comfort_magnitude", "moist_air_enthalpy", "temp_humidity_interaction", "wind_comfort_exchange", "temp_ewm_3h", "humidity_ewm_3h", "enthalpy_ewm_3h", "comfort_magnitude_ewm_3h", "temp_ewm_12h", "humidity_ewm_12h", "enthalpy_ewm_12h", "comfort_magnitude_ewm_12h", "temp_ewm_24h", "humidity_ewm_24h", "enthalpy_ewm_24h", "comfort_magnitude_ewm_24h"}:
            groups["thermal_context"] += float(value)
        elif name in {"hour_sin", "hour_cos", "weekday_sin", "weekday_cos", "is_weekend"}:
            groups["calendar"] += float(value)
        else:
            groups["current_weather"] += float(value)
    total = sum(groups.values())
    if total <= 0:
        return dict(groups)
    return {name: 100.0 * value / total for name, value in sorted(groups.items())}


def _group_contributions_over_rows(
    *,
    coef: np.ndarray,
    mean: np.ndarray,
    std: np.ndarray,
    feature_names: list[str],
    x_rows: list[list[float]],
) -> dict[str, float]:
    groups: dict[str, float] = defaultdict(float)
    for x_row in x_rows:
        z = (np.asarray(x_row, dtype=float) - mean) / std
        per_feature = np.abs(coef[1:] * z)
        for name, value in zip(feature_names, per_feature):
            if "_x_" in name and "temp" in name and "humidity" in name:
                groups["temp_humidity_interactions"] += float(value)
            elif "_x_" in name and "temp" in name and "wind" in name:
                groups["temp_wind_interactions"] += float(value)
            elif "_x_" in name and "humidity" in name and "wind" in name:
                groups["humidity_wind_interactions"] += float(value)
            elif name.startswith("temp_mean_"):
                groups["temperature_windows"] += float(value)
            elif name.startswith("humidity_mean_"):
                groups["humidity_windows"] += float(value)
            elif name.startswith("wind_mean_"):
                groups["wind_windows"] += float(value)
            elif name in {"prevailing_temp", "adaptive_comfort_center", "comfort_delta", "comfort_magnitude", "moist_air_enthalpy", "temp_humidity_interaction", "wind_comfort_exchange", "temp_ewm_3h", "humidity_ewm_3h", "enthalpy_ewm_3h", "comfort_magnitude_ewm_3h", "temp_ewm_12h", "humidity_ewm_12h", "enthalpy_ewm_12h", "comfort_magnitude_ewm_12h", "temp_ewm_24h", "humidity_ewm_24h", "enthalpy_ewm_24h", "comfort_magnitude_ewm_24h"}:
                groups["thermal_context"] += float(value)
            elif name in {"hour_sin", "hour_cos", "weekday_sin", "weekday_cos", "is_weekend"}:
                groups["calendar"] += float(value)
            else:
                groups["current_weather"] += float(value)
    total = sum(groups.values())
    if total <= 0:
        return dict(groups)
    return {name: 100.0 * value / total for name, value in sorted(groups.items())}


def _subset_score(
    *,
    feature_names: list[str],
    x_rows: list[list[float]],
    y_rows: list[float],
    include_prefixes: tuple[str, ...],
    include_names: tuple[str, ...] = (),
) -> float:
    indexes: list[int] = []
    for idx, name in enumerate(feature_names):
        if name in include_names or any(name.startswith(prefix) for prefix in include_prefixes):
            indexes.append(idx)
    if not indexes:
        return float("nan")
    reduced = [[row[i] for i in indexes] for row in x_rows]
    coef, mean, std = _linear_fit(reduced, y_rows)
    pred = _predict_linear(coef, mean, std, reduced)
    return _r2_score(y_rows, pred)


def _format_float(value: float, digits: int = 3) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    return f"{value:.{digits}f}"


# readable-code-audit: skip STRUCT-04 — this CLI intentionally exposes one linear analysis pipeline from local inputs to one report
def main() -> int:
    target_date = _today_jst().isoformat()
    history_start = (_today_jst() - timedelta(days=TRAINING_DAYS)).isoformat()
    lat = float(_setting("FORECAST_LATITUDE", "35.67452") or "35.67452")
    lon = float(_setting("FORECAST_LONGITUDE", "139.48216") or "139.48216")
    tz_name = _setting("TIMEZONE", "Asia/Tokyo") or "Asia/Tokyo"

    monitoring_rows = _load_monitoring_history(date.fromisoformat(history_start), date.fromisoformat(target_date))
    target_rows = _parse_kpnet_csv_rows(_load_latest_target_rows(target_date))
    combined_rows = monitoring_rows + target_rows
    hourly_history = _aggregate_hourly(combined_rows)
    actual_daily = _daily_from_hourly(hourly_history)

    hourly_weather_by_day, daily_weather_by_day = _fetch_archive(
        lat=lat,
        lon=lon,
        timezone_name=tz_name,
        start_date=history_start,
        end_date=target_date,
    )
    hourly_weather = _hourly_weather_map(hourly_weather_by_day)
    daily_weather = daily_weather_by_day

    target_hourly_actual = hourly_history.get(date.fromisoformat(target_date), {})
    target_hours = sorted(hour for hour in target_hourly_actual if hour in TARGET_HOURS)
    target_load_by_hour = {date.fromisoformat(target_date).replace(day=date.fromisoformat(target_date).day): 0}

    target_load_actual = {datetime.fromisoformat(f"{target_date}T{hour:02d}:00:00"): values["load"] for hour, values in target_hourly_actual.items()}
    target_pv_actual = {datetime.fromisoformat(f"{target_date}T{hour:02d}:00:00"): values["pv"] for hour, values in target_hourly_actual.items()}

    load_forecast = predict_hourly_comfort_load(
        actual_history={
            day.isoformat(): {hour: {"load": values["load"], "pv": values["pv"]} for hour, values in by_hour.items()}
            for day, by_hour in hourly_history.items()
        },
        weather_by_day=hourly_weather_by_day,
        target_date=target_date,
        min_samples=336,
    )
    current_hourly_load = load_forecast.get("hourly_load_kwh", {})
    if not isinstance(current_hourly_load, dict) or not current_hourly_load:
        current_hourly_load = {}
    current_load_pred = {datetime.fromisoformat(f"{target_date}T{int(hour):02d}:00:00"): float(value) for hour, value in current_hourly_load.items()}

    coeff = fit_coefficients_from_csv(sorted(_latest_kpnet_run_dir().joinpath("csv").glob("*.csv")))
    target_daily_weather = daily_weather_by_day.get(target_date, {})
    pv_daily_forecast_total = forecast_pv_energy_kwh(
        sun_hours=float(target_daily_weather.get("sunshine_hours", 0.0)),
        temp_c=float(target_daily_weather.get("temp", 24.0)),
        coeff=coeff,
    )
    base_hourly_pv = _build_hourly_pv_forecast(
        [
            {
                "dt": row.ts,
                "load": row.load_kwh,
                "pv": row.pv_kwh,
            }
            for row in monitoring_rows
        ],
        pv_forecast=None,
        target_date=target_date,
        fallback_total_kwh=pv_daily_forecast_total,
    )
    current_hourly_pv = base_hourly_pv
    pv_rationale = {"method": "historical_profile", "reason": "no_hourly_shortwave_forecast_used"}
    current_pv_pred = {datetime.fromisoformat(f"{target_date}T{hour:02d}:00:00"): value for hour, value in current_hourly_pv.items()}

    # Current-model evaluation.
    observed_load_hours = sorted(ts for ts in target_load_actual if ts.hour in TARGET_HOURS)
    observed_pv_hours = sorted(ts for ts in target_pv_actual if ts.hour in PV_HOURS)
    current_load_pred_list = [current_load_pred.get(ts, 0.0) for ts in observed_load_hours]
    current_load_actual_list = [target_load_actual[ts] for ts in observed_load_hours]
    current_pv_pred_list = [current_pv_pred.get(ts, 0.0) for ts in observed_pv_hours]
    current_pv_actual_list = [target_pv_actual[ts] for ts in observed_pv_hours]

    train_timestamps = sorted(ts for ts in hourly_weather if ts.date().isoformat() < target_date)
    weather_by_ts = hourly_weather
    temp_series = [weather_by_ts[ts]["temp_c"] for ts in train_timestamps if ts in weather_by_ts]
    humidity_series = [weather_by_ts[ts]["relative_humidity_percent"] for ts in train_timestamps if ts in weather_by_ts]
    wind_series = [weather_by_ts[ts]["wind_speed_10m"] for ts in train_timestamps if ts in weather_by_ts]
    load_feature_rows: list[list[float]] = []
    load_targets: list[float] = []
    load_feature_names: list[str] | None = None

    for index, ts in enumerate(train_timestamps):
        day_rows = hourly_history.get(ts.date(), {})
        if ts.hour not in day_rows:
            continue
        actual_row = day_rows[ts.hour]
        current_model_features = comfort_feature_map(ts, weather_by_ts)
        row = [float(current_model_features[name]) for name in current_model_features.keys()]
        row.extend([_rolling_mean(temp_series[: index + 1], window, len(temp_series[: index + 1]) - 1) for window in WINDOWS])
        row.extend([_rolling_mean(humidity_series[: index + 1], window, len(humidity_series[: index + 1]) - 1) for window in WINDOWS])
        row.extend([_rolling_mean(wind_series[: index + 1], window, len(wind_series[: index + 1]) - 1) for window in WINDOWS])
        temp_window_values = [_rolling_mean(temp_series[: index + 1], window, len(temp_series[: index + 1]) - 1) for window in WINDOWS]
        humidity_window_values = [_rolling_mean(humidity_series[: index + 1], window, len(humidity_series[: index + 1]) - 1) for window in WINDOWS]
        wind_window_values = [_rolling_mean(wind_series[: index + 1], window, len(wind_series[: index + 1]) - 1) for window in WINDOWS]
        for temp_value in temp_window_values:
            for humidity_value in humidity_window_values:
                row.append(temp_value * humidity_value)
        for temp_value in temp_window_values:
            for wind_value in wind_window_values:
                row.append(temp_value * wind_value)
        for humidity_value in humidity_window_values:
            for wind_value in wind_window_values:
                row.append(humidity_value * wind_value)
        load_feature_rows.append(row)
        load_targets.append(float(actual_row["load"]))
        if load_feature_names is None:
            load_feature_names = list(current_model_features.keys()) + [
                *(f"temp_mean_{window}h" for window in WINDOWS),
                *(f"humidity_mean_{window}h" for window in WINDOWS),
                *(f"wind_mean_{window}h" for window in WINDOWS),
                *(f"temp_mean_{t}h_x_humidity_mean_{h}h" for t in WINDOWS for h in WINDOWS),
                *(f"temp_mean_{t}h_x_wind_mean_{w}h" for t in WINDOWS for w in WINDOWS),
                *(f"humidity_mean_{h}h_x_wind_mean_{w}h" for h in WINDOWS for w in WINDOWS),
            ]

    if not load_feature_rows:
        raise RuntimeError("Not enough training rows were built for the hourly regression comparison.")

    load_coef, load_mean, load_std = _linear_fit(load_feature_rows, load_targets)
    load_target_rows: list[list[float]] = []
    for ts in observed_load_hours:
        features = comfort_feature_map(ts, weather_by_ts)
        row = [float(features[name]) for name in features.keys()]
        row.extend([_rolling_mean(temp_series, window, len(temp_series) - 1) for window in WINDOWS])
        row.extend([_rolling_mean(humidity_series, window, len(humidity_series) - 1) for window in WINDOWS])
        row.extend([_rolling_mean(wind_series, window, len(wind_series) - 1) for window in WINDOWS])
        temp_window_values = [_rolling_mean(temp_series, window, len(temp_series) - 1) for window in WINDOWS]
        humidity_window_values = [_rolling_mean(humidity_series, window, len(humidity_series) - 1) for window in WINDOWS]
        wind_window_values = [_rolling_mean(wind_series, window, len(wind_series) - 1) for window in WINDOWS]
        for temp_value in temp_window_values:
            for humidity_value in humidity_window_values:
                row.append(temp_value * humidity_value)
        for temp_value in temp_window_values:
            for wind_value in wind_window_values:
                row.append(temp_value * wind_value)
        for humidity_value in humidity_window_values:
            for wind_value in wind_window_values:
                row.append(humidity_value * wind_value)
        load_target_rows.append(row)

    load_reg_pred = _predict_linear(load_coef, load_mean, load_std, load_target_rows)
    load_group_contrib = _group_contributions_over_rows(
        coef=load_coef,
        mean=load_mean,
        std=load_std,
        feature_names=load_feature_names or [],
        x_rows=load_target_rows,
    )
    load_full_r2 = _r2_score(load_targets[-len(load_target_rows):], load_reg_pred)
    load_reg_mae = _mae(current_load_actual_list[: len(load_reg_pred)], np.asarray(load_reg_pred[: len(current_load_actual_list)]))

    output_dir = Path("artifacts") / "analysis"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"hourly_weather_vector_analysis_{target_date}.md"

    load_current_mae = _mae(current_load_actual_list, np.asarray(current_load_pred_list, dtype=float))
    load_current_r2 = _r2_score(current_load_actual_list, np.asarray(current_load_pred_list, dtype=float))
    pv_current_mae = _mae(current_pv_actual_list, np.asarray(current_pv_pred_list, dtype=float))
    pv_current_r2 = _r2_score(current_pv_actual_list, np.asarray(current_pv_pred_list, dtype=float))

    lines = [
        f"# Hourly vector analysis {target_date}",
        "",
        f"- Target date: {target_date}",
        f"- History start: {history_start}",
        f"- Load training rows: {len(load_feature_rows)}",
        f"- Current model: {load_forecast.get('model', 'unknown')}",
        f"- Current model applied: {load_forecast.get('applied', False)}",
        f"- Current model reason: {load_forecast.get('reason', 'unknown')}",
        f"- Current model sample count: {load_forecast.get('sample_count', 0)}",
        f"- PV shape rationale method: {pv_rationale.get('method', pv_rationale.get('reason', 'unknown'))}",
        "",
        "## Current model vs actual",
        "",
        f"- Load MAE: {_format_float(load_current_mae)} kWh/hour",
        f"- Load R²: {_format_float(load_current_r2)}",
        f"- PV MAE: {_format_float(pv_current_mae)} kWh/hour",
        f"- PV R²: {_format_float(pv_current_r2)}",
        "",
        "## Explicit cumulative-window model",
        "",
        f"- Load MAE: {_format_float(load_reg_mae)} kWh/hour",
        f"- Load R²: {_format_float(load_full_r2)}",
        "",
        "## Load contribution share",
        "",
        *(f"- {name}: {_format_float(value)}%" for name, value in sorted(load_group_contrib.items(), key=lambda item: item[1], reverse=True)),
        "",
        "## Notes",
        "",
        "- Rolling windows are trailing means over 1/3/6/12/24 hours, not raw sums, so the scales stay comparable.",
        "- The cumulative model keeps the current comfort features and adds explicit weather memory plus a few interaction terms.",
        "- The contribution shares are standardized absolute coefficient shares for the target-day reference hour, so they are directional guidance rather than causal proof.",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report_path)
    print(json.dumps(
        {
            "report": str(report_path),
            "load_current_mae": load_current_mae,
            "load_current_r2": load_current_r2,
            "load_reg_mae": load_reg_mae,
            "load_reg_r2": load_full_r2,
            "pv_current_mae": pv_current_mae,
            "pv_current_r2": pv_current_r2,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
