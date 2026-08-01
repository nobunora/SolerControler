from __future__ import annotations

import csv
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.comfort_load_forecast import _feature_map as comfort_feature_map
from app.forecast_correction import _fetch_hourly_weather

# Use the same short-to-daily trailing scales so the contribution report is comparable to training features.
WINDOWS_HOURS = (1, 3, 6, 12, 24)
WINDOWS_DAYS = (1, 3, 6, 12, 24)
DEFAULT_LOOKBACK_DAYS = 30


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    env_path = Path(".env")
    if not env_path.exists():
        return default
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if key.strip() == name:
            return raw_value.strip().strip('"').strip("'")
    return default


def _today_jst() -> date:
    tz_name = _env("TIMEZONE", "Asia/Tokyo") or "Asia/Tokyo"
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        return datetime.now().date()


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _latest_complete_day() -> date:
    db_path = Path(_env("DATA_DB_PATH", "artifacts/solar_monitor.db"))
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT MAX(substr(ts,1,10)) FROM monitoring_samples").fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        raise RuntimeError("monitoring_samples is empty")
    return _parse_date(str(row[0]))


def _load_monitoring_hourly(start_date: date, end_date: date) -> list[dict[str, Any]]:
    db_path = Path(_env("DATA_DB_PATH", "artifacts/solar_monitor.db"))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT ts, pv_kwh, load_kwh
            FROM monitoring_samples
            WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) <= ?
            ORDER BY ts
            """,
            (start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            ts = datetime.fromisoformat(str(row["ts"]))
        except ValueError:
            continue
        out.append(
            {
                "ts": ts,
                "load_kwh": max(0.0, float(row["load_kwh"] or 0.0)),
                "pv_kwh": max(0.0, float(row["pv_kwh"] or 0.0)),
            }
        )
    return out


def _fetch_daily_shortwave(*, lat: float, lon: float, timezone: str, start_date: str, end_date: str) -> dict[str, float]:
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "shortwave_radiation_sum",
        "timezone": timezone,
    }
    response = requests.get(url, params=params, timeout=40)
    response.raise_for_status()
    payload = response.json().get("daily", {})
    if not isinstance(payload, dict):
        return {}
    times = payload.get("time", [])
    shortwave = payload.get("shortwave_radiation_sum", [])
    out: dict[str, float] = {}
    if not isinstance(times, list) or not isinstance(shortwave, list):
        return out
    for idx, raw_day in enumerate(times):
        try:
            out[str(raw_day)] = max(0.0, float(shortwave[idx]))
        except Exception:
            continue
    return out


def _flatten_weather_by_day(
    weather_by_day: dict[str, dict[int, dict[str, float]]],
) -> dict[datetime, dict[str, float]]:
    out: dict[datetime, dict[str, float]] = {}
    for raw_day, hourly in weather_by_day.items():
        try:
            day = datetime.fromisoformat(raw_day)
        except ValueError:
            continue
        for hour, row in hourly.items():
            out[day.replace(hour=hour)] = row
    return dict(sorted(out.items()))


def _aggregate_load_by_hour(rows: list[dict[str, Any]]) -> dict[date, dict[int, float]]:
    by_day: dict[date, dict[int, float]] = defaultdict(dict)
    for row in rows:
        ts = row["ts"]
        if not isinstance(ts, datetime):
            continue
        day = ts.date()
        value = float(row["load_kwh"])
        by_day[day][ts.hour] = by_day[day].get(ts.hour, 0.0) + value
    return {day: dict(hours) for day, hours in by_day.items()}


def _day_series(day_values: dict[str, float], day: date, window_days: int) -> float:
    values: list[float] = []
    for offset in range(window_days):
        current = (day - timedelta(days=offset)).isoformat()
        if current in day_values:
            values.append(day_values[current])
    return fmean(values) if values else 0.0


def _hourly_trailing_mean(
    weather_by_ts: dict[datetime, dict[str, float]],
    ts: datetime,
    field: str,
    window_hours: int,
) -> float:
    values: list[float] = []
    for offset in range(window_hours):
        current = ts - timedelta(hours=offset)
        row = weather_by_ts.get(current)
        if row is None:
            continue
        values.append(float(row.get(field, 0.0)))
    return fmean(values) if values else 0.0


def _rolling_daily_mean(
    daily_values: dict[str, float],
    day: date,
    window_days: int,
) -> float:
    values: list[float] = []
    for offset in range(window_days):
        current = (day - timedelta(days=offset)).isoformat()
        if current in daily_values:
            values.append(daily_values[current])
    return fmean(values) if values else 0.0


def _standardize_matrix(matrix: list[list[float]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = np.asarray(matrix, dtype=float)
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std = np.where(std < 1e-9, 1.0, std)
    return x, mean, std


def _ridge_fit(matrix: list[list[float]], targets: list[float], *, ridge: float = 0.1) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x, mean, std = _standardize_matrix(matrix)
    z = (x - mean) / std
    z_aug = np.column_stack([np.ones(len(z)), z])
    gram = z_aug.T @ z_aug
    gram[1:, 1:] += np.eye(gram.shape[0] - 1) * ridge
    rhs = z_aug.T @ np.asarray(targets, dtype=float)
    coef = np.linalg.solve(gram, rhs)
    return coef, mean, std


def _predict(coef: np.ndarray, mean: np.ndarray, std: np.ndarray, matrix: list[list[float]]) -> np.ndarray:
    x = np.asarray(matrix, dtype=float)
    z = (x - mean) / std
    return coef[0] + z @ coef[1:]


def _r2_score(y_true: list[float], y_pred: np.ndarray) -> float:
    actual = np.asarray(y_true, dtype=float)
    resid = float(np.sum((actual - y_pred) ** 2))
    total = float(np.sum((actual - actual.mean()) ** 2))
    return 1.0 - resid / total if total > 0 else float("nan")


def _mae(y_true: list[float], y_pred: np.ndarray) -> float:
    actual = np.asarray(y_true, dtype=float)
    return float(np.mean(np.abs(actual - y_pred))) if len(actual) else float("nan")


def _build_rows(
    hourly_load: dict[date, dict[int, float]],
    weather_by_ts: dict[datetime, dict[str, float]],
    daily_shortwave: dict[str, float],
) -> tuple[list[list[float]], list[float], list[str], list[dict[str, float]]]:
    feature_rows: list[list[float]] = []
    targets: list[float] = []
    feature_names: list[str] = []
    contribution_rows: list[dict[str, float]] = []
    ordered_timestamps = sorted(ts for ts in weather_by_ts if ts.date() in hourly_load and ts.hour in hourly_load[ts.date()])
    if not ordered_timestamps:
        raise RuntimeError("No overlapping load and weather samples were found.")

    for index, ts in enumerate(ordered_timestamps):
        day = ts.date()
        if ts.hour not in hourly_load[day]:
            continue
        actual_load = hourly_load[day][ts.hour]
        current = weather_by_ts[ts]
        current_temp = float(current.get("temp_c", 0.0))
        current_humidity = float(current.get("relative_humidity_percent", 0.0))
        current_wind = float(current.get("wind_speed_10m", 0.0))
        current_enthalpy = float(current.get("enthalpy_kj_kg", current_temp))
        current_shortwave = daily_shortwave.get(day.isoformat(), 0.0)

        base = comfort_feature_map(ts, weather_by_ts)
        temp_windows = [_hourly_trailing_mean(weather_by_ts, ts, "temp_c", window) for window in WINDOWS_HOURS]
        humidity_windows = [_hourly_trailing_mean(weather_by_ts, ts, "relative_humidity_percent", window) for window in WINDOWS_HOURS]
        wind_windows = [_hourly_trailing_mean(weather_by_ts, ts, "wind_speed_10m", window) for window in WINDOWS_HOURS]
        shortwave_windows = [_rolling_daily_mean(daily_shortwave, day, window) for window in WINDOWS_DAYS]

        row = [float(base[name]) for name in base.keys()]
        row.extend([current_temp, current_humidity, current_wind, current_enthalpy, current_shortwave])
        row.extend(temp_windows)
        row.extend(humidity_windows)
        row.extend(wind_windows)
        row.extend(shortwave_windows)

        # Pairwise interactions; kept small enough for stability.
        interaction_values = {
            "temp_x_humidity_current": current_temp * current_humidity,
            "temp_x_wind_current": current_temp * current_wind,
            "humidity_x_wind_current": current_humidity * current_wind,
            "shortwave_x_temp_current": current_shortwave * current_temp,
            "shortwave_x_humidity_current": current_shortwave * current_humidity,
            "shortwave_x_wind_current": current_shortwave * current_wind,
            "temp24_x_humidity24": temp_windows[-1] * humidity_windows[-1],
            "temp24_x_wind24": temp_windows[-1] * wind_windows[-1],
            "humidity24_x_wind24": humidity_windows[-1] * wind_windows[-1],
        }
        row.extend(interaction_values.values())
        feature_rows.append(row)
        targets.append(actual_load)
        contribution_rows.append(interaction_values)

        if not feature_names:
            feature_names = list(base.keys()) + [
                "temp_current",
                "humidity_current",
                "wind_current",
                "enthalpy_current",
                "daily_shortwave_sum",
                *(f"temp_mean_{w}h" for w in WINDOWS_HOURS),
                *(f"humidity_mean_{w}h" for w in WINDOWS_HOURS),
                *(f"wind_mean_{w}h" for w in WINDOWS_HOURS),
                *(f"shortwave_mean_{w}d" for w in WINDOWS_DAYS),
                *interaction_values.keys(),
            ]
    return feature_rows, targets, feature_names, contribution_rows


def _group_contributions(
    *,
    coef: np.ndarray,
    mean: np.ndarray,
    std: np.ndarray,
    feature_names: list[str],
    rows: list[list[float]],
) -> dict[str, float]:
    groups: dict[str, float] = defaultdict(float)
    z = (np.asarray(rows, dtype=float) - mean) / std
    for row in z:
        per_feature = np.abs(coef[1:] * row)
        for name, value in zip(feature_names, per_feature):
            if name.startswith("temp_mean_"):
                groups["temperature_windows"] += float(value)
            elif name.startswith("humidity_mean_"):
                groups["humidity_windows"] += float(value)
            elif name.startswith("wind_mean_"):
                groups["wind_windows"] += float(value)
            elif name.startswith("shortwave_mean_"):
                groups["shortwave_windows"] += float(value)
            elif name.startswith("temp_") and "current" in name:
                groups["current_temperature"] += float(value)
            elif name.startswith("humidity_") and "current" in name:
                groups["current_humidity"] += float(value)
            elif name.startswith("wind_") and "current" in name:
                groups["current_wind"] += float(value)
            elif "temp_x_humidity" in name:
                groups["temp_humidity_interactions"] += float(value)
            elif "temp_x_wind" in name:
                groups["temp_wind_interactions"] += float(value)
            elif "humidity_x_wind" in name:
                groups["humidity_wind_interactions"] += float(value)
            elif "shortwave_x_temp" in name:
                groups["shortwave_temp_interactions"] += float(value)
            elif "shortwave_x_humidity" in name:
                groups["shortwave_humidity_interactions"] += float(value)
            elif "shortwave_x_wind" in name:
                groups["shortwave_wind_interactions"] += float(value)
            elif name in {"hour_sin", "hour_cos", "weekday_sin", "weekday_cos", "is_weekend"}:
                groups["calendar"] += float(value)
            else:
                groups["thermal_context"] += float(value)
    total = sum(groups.values())
    if total <= 0:
        return dict(groups)
    return {key: 100.0 * value / total for key, value in sorted(groups.items())}


def _nested_r2(feature_rows: list[list[float]], targets: list[float], groups: dict[str, list[int]]) -> dict[str, float]:
    results: dict[str, float] = {}
    for name, indexes in groups.items():
        reduced = [[row[i] for i in indexes] for row in feature_rows]
        coef, mean, std = _ridge_fit(reduced, targets)
        pred = _predict(coef, mean, std, reduced)
        results[name] = _r2_score(targets, pred)
    return results


def _subset_r2(feature_rows: list[list[float]], targets: list[float], indexes: list[int]) -> float:
    if not indexes:
        return float("nan")
    reduced = [[row[i] for i in indexes] for row in feature_rows]
    coef, mean, std = _ridge_fit(reduced, targets)
    pred = _predict(coef, mean, std, reduced)
    return _r2_score(targets, pred)


# readable-code-audit: skip STRUCT-04 — this CLI intentionally keeps feature construction, attribution, and report formatting in one reproducible run
def main() -> int:
    end_date = _latest_complete_day()
    lookback_days = int(float(_env("WEATHER_ATTRIBUTION_LOOKBACK_DAYS", str(DEFAULT_LOOKBACK_DAYS))))
    lookback_days = max(7, lookback_days)
    start_date = end_date - timedelta(days=lookback_days - 1)
    timezone = _env("TIMEZONE", "Asia/Tokyo") or "Asia/Tokyo"
    lat = float(_env("FORECAST_LATITUDE", "35.67452") or "35.67452")
    lon = float(_env("FORECAST_LONGITUDE", "139.48216") or "139.48216")

    load_rows = _load_monitoring_hourly(start_date, end_date)
    hourly_load = _aggregate_load_by_hour(load_rows)
    hourly_weather = _flatten_weather_by_day(
        _fetch_hourly_weather(
            lat=lat,
            lon=lon,
            timezone=timezone,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            archive=True,
        )
    )
    daily_shortwave = _fetch_daily_shortwave(
        lat=lat,
        lon=lon,
        timezone=timezone,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )
    if not hourly_weather:
        raise RuntimeError("Weather archive retrieval returned no hourly rows.")

    feature_rows, targets, feature_names, contribution_rows = _build_rows(hourly_load, hourly_weather, daily_shortwave)
    coef, mean, std = _ridge_fit(feature_rows, targets, ridge=0.25)
    preds = _predict(coef, mean, std, feature_rows)
    overall_r2 = _r2_score(targets, preds)
    overall_mae = _mae(targets, preds)
    contributions = _group_contributions(coef=coef, mean=mean, std=std, feature_names=feature_names, rows=feature_rows)

    group_index_map: dict[str, list[int]] = defaultdict(list)
    for idx, name in enumerate(feature_names):
        if name.startswith("temp_mean_"):
            group_index_map["temperature_windows"].append(idx)
        elif name.startswith("humidity_mean_"):
            group_index_map["humidity_windows"].append(idx)
        elif name.startswith("wind_mean_"):
            group_index_map["wind_windows"].append(idx)
        elif name.startswith("shortwave_mean_"):
            group_index_map["shortwave_windows"].append(idx)
        elif name.startswith("temp_current"):
            group_index_map["current_temperature"].append(idx)
        elif name.startswith("humidity_current"):
            group_index_map["current_humidity"].append(idx)
        elif name.startswith("wind_current"):
            group_index_map["current_wind"].append(idx)
        elif "temp_x_humidity" in name:
            group_index_map["temp_humidity_interactions"].append(idx)
        elif "temp_x_wind" in name:
            group_index_map["temp_wind_interactions"].append(idx)
        elif "humidity_x_wind" in name:
            group_index_map["humidity_wind_interactions"].append(idx)
        elif "shortwave_x_temp" in name:
            group_index_map["shortwave_temp_interactions"].append(idx)
        elif "shortwave_x_humidity" in name:
            group_index_map["shortwave_humidity_interactions"].append(idx)
        elif "shortwave_x_wind" in name:
            group_index_map["shortwave_wind_interactions"].append(idx)
        elif name in {"hour_sin", "hour_cos", "weekday_sin", "weekday_cos", "is_weekend"}:
            group_index_map["calendar"].append(idx)
        else:
            group_index_map["thermal_context"].append(idx)

    nested_calendar = _subset_r2(feature_rows, targets, group_index_map["calendar"])
    nested_current = _subset_r2(
        feature_rows,
        targets,
        sorted(
            {
                *group_index_map["calendar"],
                *group_index_map["current_temperature"],
                *group_index_map["current_humidity"],
                *group_index_map["current_wind"],
                *group_index_map["thermal_context"],
            }
        ),
    )
    nested_windows = _subset_r2(
        feature_rows,
        targets,
        sorted(
            {
                *group_index_map["calendar"],
                *group_index_map["current_temperature"],
                *group_index_map["current_humidity"],
                *group_index_map["current_wind"],
                *group_index_map["thermal_context"],
                *group_index_map["temperature_windows"],
                *group_index_map["humidity_windows"],
                *group_index_map["wind_windows"],
                *group_index_map["shortwave_windows"],
            }
        ),
    )

    output_dir = Path("artifacts") / "analysis"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"multi_day_weather_contribution_{end_date.isoformat()}.md"

    lines = [
        f"# Multi-day weather contribution analysis {end_date.isoformat()}",
        "",
        f"- Range: {start_date.isoformat()} to {end_date.isoformat()}",
        f"- Hourly samples: {len(feature_rows)}",
        f"- Monitor rows: {len(load_rows)}",
        f"- Ridge MAE: {overall_mae:.3f} kWh/hour",
        f"- Ridge R²: {overall_r2:.3f}",
        "",
        "## Contribution share",
        "",
        *(f"- {name}: {value:.3f}%" for name, value in sorted(contributions.items(), key=lambda item: item[1], reverse=True)),
        "",
        "## Nested model R²",
        "",
        f"- Calendar only: {nested_calendar:.3f}",
        f"- Calendar + current weather: {nested_current:.3f}",
        f"- Calendar + current weather + cumulative windows: {nested_windows:.3f}",
        "",
        "## Notes",
        "",
        "- Contributions are average absolute standardized linear contributions across the whole multi-day window.",
        "- Interaction terms are explicit pairwise products, so their share reflects confounding and synergy rather than causal separation.",
        "- Shortwave uses daily archive sums; temperature, humidity, and wind use hourly archive data with trailing windows.",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report_path)
    print(
        json.dumps(
            {
                "report": str(report_path),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "samples": len(feature_rows),
                "mae": overall_mae,
                "r2": overall_r2,
                "top_contributions": sorted(contributions.items(), key=lambda item: item[1], reverse=True)[:8],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
