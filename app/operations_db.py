from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from app.utils import env, env_float, load_dotenv_if_present, to_float, to_int


def _extract_hourly_forecast_from_plan(data: dict[str, Any]) -> list[dict[str, Any]]:
    forecast = data.get("forecast", {})
    forecast_date = str(forecast.get("date", "")).strip() if isinstance(forecast, dict) else ""
    if not forecast_date:
        return []
    optimization = data.get("daytime_soc_optimization", {})
    if not isinstance(optimization, dict):
        return []
    pv_by_hour = optimization.get("hourly_pv_forecast_kwh", {})
    load_by_hour = optimization.get("hourly_load_forecast_kwh", {})
    if not isinstance(pv_by_hour, dict):
        pv_by_hour = {}
    if not isinstance(load_by_hour, dict):
        load_by_hour = {}
    weather_by_hour: dict[int, dict[str, Any]] = {}
    hourly_weather = forecast.get("hourly_weather", []) if isinstance(forecast, dict) else []
    if isinstance(hourly_weather, list):
        for item in hourly_weather:
            if not isinstance(item, dict):
                continue
            hour = to_int(item.get("hour"))
            if hour is None or hour < 0 or hour > 23:
                continue
            weather_by_hour[hour] = item

    hours: set[int] = set()
    for source in (pv_by_hour, load_by_hour):
        for raw_hour in source:
            try:
                hour = int(raw_hour)
            except (TypeError, ValueError):
                continue
            if 0 <= hour <= 23:
                hours.add(hour)
    hours.update(weather_by_hour)

    rows: list[dict[str, Any]] = []
    for hour in sorted(hours):
        pv_kwh = to_float(pv_by_hour.get(str(hour), pv_by_hour.get(hour))) or 0.0
        load_kwh = to_float(load_by_hour.get(str(hour), load_by_hour.get(hour))) or 0.0
        weather = weather_by_hour.get(hour, {})
        rows.append(
            {
                "date": forecast_date,
                "hour": hour,
                "forecast_pv_kwh": round(max(0.0, pv_kwh), 4),
                "forecast_load_kwh": round(max(0.0, load_kwh), 4),
                "forecast_charge_kwh": round(max(0.0, pv_kwh - load_kwh), 4),
                "forecast_weather_code": to_int(weather.get("weather_code")),
                "forecast_precipitation_mm": to_float(weather.get("precipitation_mm")),
                "forecast_precipitation_probability": to_float(weather.get("precipitation_probability")),
                "forecast_cloud_cover": to_float(weather.get("cloud_cover")),
                "forecast_shortwave_radiation_w_m2": to_float(weather.get("shortwave_radiation_w_m2")),
            }
        )
    return rows


def _safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _parse_hhmm_to_minute(*, value: str, name: str) -> int:
    text = value.strip()
    try:
        hour_str, minute_str = text.split(":", 1)
        hour = int(hour_str)
        minute = int(minute_str)
    except Exception as exc:  # pragma: no cover - defensive parse
        raise ValueError(f"{name} must be HH:MM but got: {value}") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"{name} must be HH:MM but got: {value}")
    return hour * 60 + minute


def _is_within_window(minute_of_day: int, *, start_minute: int, end_minute: int) -> bool:
    if start_minute == end_minute:
        return True
    if start_minute < end_minute:
        return start_minute <= minute_of_day < end_minute
    return minute_of_day >= start_minute or minute_of_day < end_minute


def _tiered_day_cost(
    day_kwh: float,
    *,
    tier1_upper_kwh: float,
    tier2_upper_kwh: float,
    rate_tier1_yen: float,
    rate_tier2_yen: float,
    rate_tier3_yen: float,
) -> float:
    kwh = max(0.0, float(day_kwh))
    t1 = max(0.0, float(tier1_upper_kwh))
    t2 = max(t1, float(tier2_upper_kwh))
    b1 = min(kwh, t1)
    b2 = min(max(kwh - t1, 0.0), t2 - t1)
    b3 = max(kwh - t2, 0.0)
    return b1 * rate_tier1_yen + b2 * rate_tier2_yen + b3 * rate_tier3_yen


def _tiered_day_increment_cost(
    *,
    previous_kwh: float,
    delta_kwh: float,
    tier1_upper_kwh: float,
    tier2_upper_kwh: float,
    rate_tier1_yen: float,
    rate_tier2_yen: float,
    rate_tier3_yen: float,
) -> float:
    prev = max(0.0, float(previous_kwh))
    delta = max(0.0, float(delta_kwh))
    return _tiered_day_cost(
        prev + delta,
        tier1_upper_kwh=tier1_upper_kwh,
        tier2_upper_kwh=tier2_upper_kwh,
        rate_tier1_yen=rate_tier1_yen,
        rate_tier2_yen=rate_tier2_yen,
        rate_tier3_yen=rate_tier3_yen,
    ) - _tiered_day_cost(
        prev,
        tier1_upper_kwh=tier1_upper_kwh,
        tier2_upper_kwh=tier2_upper_kwh,
        rate_tier1_yen=rate_tier1_yen,
        rate_tier2_yen=rate_tier2_yen,
        rate_tier3_yen=rate_tier3_yen,
    )


@dataclass(frozen=True)
class PipelineConfig:
    data_backend: str
    site_id: str
    db_path: Path
    artifacts_dir: Path
    slot: str
    timezone: str
    day_rate_yen_per_kwh: float
    cost_tariff_mode: str
    night8_day_start_hhmm: str
    night8_day_end_hhmm: str
    night8_day_tier1_upper_kwh: float
    night8_day_tier2_upper_kwh: float
    night8_day_rate_tier1_yen: float
    night8_day_rate_tier2_yen: float
    night8_day_rate_tier3_yen: float
    night8_night_rate_yen: float
    storage_gcs_db_uri: str
    storage_gcs_daily_prefix: str
    storage_sync_enabled: bool
    write_only_slot_23: bool
    weekly_backup_enabled: bool
    weekly_backup_weekday: int
    weekly_backup_dir: Path

    @staticmethod
    def from_env() -> "PipelineConfig":
        load_dotenv_if_present()
        slot_raw = env("CLOUD_JOB_SLOT", default="").strip().lower()
        if slot_raw in {"23", "night", "night23"}:
            slot = "23"
        elif slot_raw in {"7", "07", "day", "day07"}:
            slot = "07"
        else:
            slot = slot_raw or "unknown"

        sync_enabled = env("DATA_DB_SYNC_ENABLED", default="false").strip().lower() in {"1", "true", "yes", "on"}
        write_only_23 = env("DATA_DB_WRITE_ONLY_23", default="true").strip().lower() in {"1", "true", "yes", "on"}
        weekly_backup_enabled = env("DATA_WEEKLY_BACKUP_ENABLED", default="true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        weekly_backup_weekday = int(env("DATA_WEEKLY_BACKUP_WEEKDAY", default="5"))
        cost_tariff_mode = env("COST_TARIFF_MODE", default="night8_tiered").strip().lower() or "night8_tiered"
        if cost_tariff_mode not in {"flat", "night8_tiered"}:
            cost_tariff_mode = "night8_tiered"
        day_rate = env_float("DAY_RATE_YEN_PER_KWH", default=31.0)
        return PipelineConfig(
            data_backend=env("DATA_BACKEND", default="sqlite").strip().lower(),
            site_id=env("SITE_ID", default="fuchu-home").strip() or "fuchu-home",
            db_path=Path(env("DATA_DB_PATH", default="artifacts/solar_monitor.db")),
            artifacts_dir=Path(env("ARTIFACTS_DIR", default="artifacts")),
            slot=slot,
            timezone=env("TIMEZONE", default="Asia/Tokyo"),
            day_rate_yen_per_kwh=day_rate,
            cost_tariff_mode=cost_tariff_mode,
            night8_day_start_hhmm=env("NIGHT8_DAY_START_HHMM", default="07:00").strip() or "07:00",
            night8_day_end_hhmm=env("NIGHT8_DAY_END_HHMM", default="23:00").strip() or "23:00",
            night8_day_tier1_upper_kwh=env_float("NIGHT8_DAY_TIER1_UPPER_KWH", default=90.0),
            night8_day_tier2_upper_kwh=env_float("NIGHT8_DAY_TIER2_UPPER_KWH", default=230.0),
            night8_day_rate_tier1_yen=env_float("NIGHT8_DAY_RATE_TIER1_YEN", default=31.80),
            night8_day_rate_tier2_yen=env_float("NIGHT8_DAY_RATE_TIER2_YEN", default=39.10),
            night8_day_rate_tier3_yen=env_float("NIGHT8_DAY_RATE_TIER3_YEN", default=43.62),
            night8_night_rate_yen=env_float("NIGHT8_NIGHT_RATE_YEN", default=28.85),
            storage_gcs_db_uri=env("DATA_GCS_DB_URI", default=""),
            storage_gcs_daily_prefix=env("DATA_GCS_DAILY_PREFIX", default=""),
            storage_sync_enabled=sync_enabled,
            write_only_slot_23=write_only_23,
            weekly_backup_enabled=weekly_backup_enabled,
            weekly_backup_weekday=weekly_backup_weekday,
            weekly_backup_dir=Path(env("DATA_WEEKLY_BACKUP_DIR", default="artifacts/backups/weekly")),
        )


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        CREATE TABLE IF NOT EXISTS monitoring_samples (
            ts TEXT PRIMARY KEY,
            pv_kwh REAL,
            load_kwh REAL,
            sell_kwh REAL,
            buy_kwh REAL,
            charge_kwh REAL,
            discharge_kwh REAL,
            soc_percent REAL,
            source_csv TEXT,
            ingested_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sunshine_daily (
            date TEXT PRIMARY KEY,
            forecast_hours REAL,
            actual_hours REAL,
            forecast_temp_c REAL,
            actual_temp_c REAL,
            source TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            slot TEXT NOT NULL,
            profile TEXT NOT NULL,
            status TEXT NOT NULL,
            changed_fields_json TEXT,
            detail_json TEXT,
            source_doc_id TEXT,
            recorded_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cost_daily (
            date TEXT PRIMARY KEY,
            self_consumption_kwh REAL NOT NULL,
            savings_yen REAL NOT NULL,
            cumulative_kwh REAL NOT NULL,
            cumulative_yen REAL NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS battery_daily_metrics (
            date TEXT PRIMARY KEY,
            setting_soc_target_percent REAL,
            night_charge_kwh REAL,
            pv_max_charge_kwh REAL,
            pv_charge_end_soc_percent REAL,
            pv_charge_end_at TEXT,
            end_of_day_soc_percent REAL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS model_parameters (
            name TEXT PRIMARY KEY,
            mean_value REAL NOT NULL,
            variance REAL NOT NULL,
            sample_count INTEGER NOT NULL,
            hit_rate REAL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_key TEXT PRIMARY KEY,
            slot TEXT NOT NULL,
            csv_run_id TEXT,
            settings_run_id TEXT,
            csv_rows_upserted INTEGER NOT NULL,
            recorded_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS forecast_hourly (
            date TEXT NOT NULL,
            hour INTEGER NOT NULL,
            forecast_pv_kwh REAL,
            forecast_load_kwh REAL,
            forecast_charge_kwh REAL,
            forecast_weather_code INTEGER,
            forecast_precipitation_mm REAL,
            forecast_precipitation_probability REAL,
            forecast_cloud_cover REAL,
            forecast_shortwave_radiation_w_m2 REAL,
            source TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(date, hour)
        );
        """
    )
    _ensure_sqlite_columns(
        conn,
        "sunshine_daily",
        {
            "forecast_weather_code": "INTEGER",
            "actual_weather_code": "INTEGER",
            "forecast_precipitation_sum_mm": "REAL",
            "forecast_precipitation_probability_mean": "REAL",
            "actual_precipitation_sum_mm": "REAL",
            "forecast_shortwave_radiation_sum_mj_m2": "REAL",
            "actual_shortwave_radiation_sum_mj_m2": "REAL",
            "forecast_pv_total_kwh": "REAL",
            "forecast_pv_morning_kwh": "REAL",
            "forecast_pv_midday_kwh": "REAL",
            "forecast_pv_evening_kwh": "REAL",
            "forecast_pv_calibration_factor": "REAL",
        },
    )
    _ensure_sqlite_columns(
        conn,
        "forecast_hourly",
        {
            "forecast_weather_code": "INTEGER",
            "forecast_precipitation_mm": "REAL",
            "forecast_precipitation_probability": "REAL",
            "forecast_cloud_cover": "REAL",
            "forecast_shortwave_radiation_w_m2": "REAL",
        },
    )
    _ensure_sqlite_columns(
        conn,
        "battery_daily_metrics",
        {
            "pv_charge_end_soc_percent": "REAL",
            "pv_charge_end_at": "TEXT",
        },
    )
    _ensure_sqlite_columns(
        conn,
        "settings_events",
        {
            "source_doc_id": "TEXT",
        },
    )
    conn.commit()


def _ensure_sqlite_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _latest_run_dirs(artifacts_dir: Path) -> list[Path]:
    run_dirs = [p for p in artifacts_dir.glob("*") if p.is_dir() and len(p.name) >= 15 and p.name[:8].isdigit()]
    run_dirs.sort(key=lambda p: p.name, reverse=True)
    return run_dirs


def _read_summary(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_if_exists(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _extract_battery_daily_from_summary(
    *,
    summary: dict[str, Any],
    night_plan: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(summary, dict):
        return None
    np_summary = summary.get("night_charge_plan", {})
    if not isinstance(np_summary, dict):
        np_summary = {}
    np_root = night_plan if isinstance(night_plan, dict) else {}
    np_result = np_root.get("result", {}) if isinstance(np_root.get("result", {}), dict) else {}
    np_forecast = np_root.get("forecast", {}) if isinstance(np_root.get("forecast", {}), dict) else {}
    prefer_night_plan = env("DATA_PREFER_NIGHT_PLAN_METRICS", default="false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    date = str(np_summary.get("forecast_date") or np_forecast.get("date") or "").strip()
    if not date:
        return None

    target_soc = to_float(np_result.get("target_soc_7_percent")) if prefer_night_plan else None
    if target_soc is None:
        target_soc = to_float(np_summary.get("target_soc_7_percent_raw"))
    if target_soc is None:
        target_soc = to_float(np_result.get("target_soc_7_percent"))

    night_charge_kwh = to_float(np_result.get("required_night_charge_kwh")) if prefer_night_plan else None
    if night_charge_kwh is None:
        night_charge_kwh = to_float(np_summary.get("required_night_charge_kwh"))
    if night_charge_kwh is None:
        night_charge_kwh = to_float(np_result.get("required_night_charge_kwh"))

    pv_max_charge_kwh = to_float(np_result.get("predicted_midday_surplus_kwh")) if prefer_night_plan else None
    if pv_max_charge_kwh is None:
        pv_max_charge_kwh = to_float(np_summary.get("predicted_midday_surplus_kwh"))
    if pv_max_charge_kwh is None:
        pv_max_charge_kwh = to_float(np_result.get("predicted_midday_surplus_kwh"))

    return {
        "date": date,
        "target_soc": target_soc,
        "night_charge_kwh": night_charge_kwh,
        "pv_max_charge_kwh": pv_max_charge_kwh,
        # Actual PV-charge-end SOC must come from monitoring CSV samples.
        "pv_charge_end_soc": None,
        "pv_charge_end_at": None,
    }


def find_latest_csv_and_settings_runs(artifacts_dir: Path) -> tuple[Path | None, Path | None]:
    latest_csv: Path | None = None
    latest_settings: Path | None = None
    for run_dir in _latest_run_dirs(artifacts_dir):
        summary_path = run_dir / "kpnet_summary.json"
        if not summary_path.exists():
            continue
        summary = _read_summary(summary_path)
        if latest_csv is None and summary.get("csv_downloads"):
            latest_csv = run_dir
        if latest_settings is None and summary.get("setting_results"):
            latest_settings = run_dir
        if latest_csv is not None and latest_settings is not None:
            break
    return latest_csv, latest_settings


def _iter_monitoring_rows(csv_path: Path):
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_text = (row.get("年月日") or "").strip()
            time_text = (row.get("時刻") or "").strip()
            if not date_text or not time_text:
                continue
            try:
                dt = datetime.strptime(f"{date_text} {time_text}", "%Y/%m/%d %H:%M")
            except ValueError:
                continue
            ts = dt.isoformat()
            yield {
                "ts": ts,
                "pv_kwh": to_float(row.get("発電電力量[kWh]")),
                "load_kwh": to_float(row.get("消費電力量[kWh]")),
                "sell_kwh": to_float(row.get("売電電力量[kWh]")),
                "buy_kwh": to_float(row.get("買電電力量[kWh]")),
                "charge_kwh": to_float(row.get("充電電力量[kWh]")),
                "discharge_kwh": to_float(row.get("放電電力量[kWh]")),
                "soc_percent": to_float(row.get("蓄電残量(SOC)[%]")),
            }


def ingest_monitoring_csvs(
    conn: sqlite3.Connection,
    *,
    csv_paths: list[Path],
    ingested_at: str,
) -> int:
    upserted = 0
    for csv_path in csv_paths:
        for row in _iter_monitoring_rows(csv_path):
            conn.execute(
                """
                INSERT INTO monitoring_samples (
                    ts, pv_kwh, load_kwh, sell_kwh, buy_kwh, charge_kwh, discharge_kwh, soc_percent, source_csv, ingested_at
                ) VALUES (
                    :ts, :pv_kwh, :load_kwh, :sell_kwh, :buy_kwh, :charge_kwh, :discharge_kwh, :soc_percent, :source_csv, :ingested_at
                )
                ON CONFLICT(ts) DO UPDATE SET
                    pv_kwh=excluded.pv_kwh,
                    load_kwh=excluded.load_kwh,
                    sell_kwh=excluded.sell_kwh,
                    buy_kwh=excluded.buy_kwh,
                    charge_kwh=excluded.charge_kwh,
                    discharge_kwh=excluded.discharge_kwh,
                    soc_percent=excluded.soc_percent,
                    source_csv=excluded.source_csv,
                    ingested_at=excluded.ingested_at
                """,
                {
                    **row,
                    "source_csv": str(csv_path),
                    "ingested_at": ingested_at,
                },
            )
            upserted += 1
    conn.commit()
    return upserted


def _first_float(values: Any) -> float | None:
    if not values:
        return None
    try:
        value = values[0]
    except Exception:
        return None
    return to_float(value)


def _first_int(values: Any) -> int | None:
    value = _first_float(values)
    if value is None:
        return None
    return int(value)


def _fetch_open_meteo_daily_actual(*, lat: float, lon: float, date_ymd: str, timezone: str) -> dict[str, float | int | None]:
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": date_ymd,
        "end_date": date_ymd,
        "daily": "sunshine_duration,temperature_2m_mean,weather_code,precipitation_sum,shortwave_radiation_sum",
        "timezone": timezone,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    obj = resp.json()
    daily = obj.get("daily", {})
    sunshine_s = _first_float(daily.get("sunshine_duration", []))
    return {
        "actual_hours": (sunshine_s / 3600.0) if sunshine_s is not None else None,
        "actual_temp_c": _first_float(daily.get("temperature_2m_mean", [])),
        "actual_weather_code": _first_int(daily.get("weather_code", [])),
        "actual_precipitation_sum_mm": _first_float(daily.get("precipitation_sum", [])),
        "actual_shortwave_radiation_sum_mj_m2": _first_float(daily.get("shortwave_radiation_sum", [])),
    }


def _fetch_open_meteo_today_actual(*, lat: float, lon: float, date_ymd: str, timezone: str) -> tuple[float | None, float | None]:
    actual = _fetch_open_meteo_daily_actual(lat=lat, lon=lon, date_ymd=date_ymd, timezone=timezone)
    return (
        to_float(actual.get("actual_hours")),
        to_float(actual.get("actual_temp_c")),
    )


def ingest_sunshine_from_night_plan(
    conn: sqlite3.Connection,
    *,
    night_plan_path: Path,
    timezone: str,
    ingested_at: str,
) -> None:
    if not night_plan_path.exists():
        return
    data = json.loads(night_plan_path.read_text(encoding="utf-8"))
    forecast = data.get("forecast", {})
    forecast_date = str(forecast.get("date", "")).strip()
    tomorrow_hours = forecast.get("sun_hours")
    tomorrow_temp = forecast.get("temp_c")
    tomorrow_weather_code = forecast.get("weather_code")
    tomorrow_precip_sum = forecast.get("precipitation_sum_mm")
    tomorrow_precip_probability = forecast.get("precipitation_probability_mean")
    tomorrow_shortwave = forecast.get("shortwave_radiation_sum_mj_m2")
    pv_forecast = data.get("pv_array_forecast", {})
    pv_totals = pv_forecast.get("totals", {}) if isinstance(pv_forecast, dict) else {}
    pv_calibration = pv_forecast.get("calibration", {}) if isinstance(pv_forecast, dict) else {}
    forecast_source = str(
        (pv_forecast.get("source") if isinstance(pv_forecast, dict) else None)
        or forecast.get("source")
        or "forecast"
    )
    lat = float(env("FORECAST_LATITUDE", default="35.67452"))
    lon = float(env("FORECAST_LONGITUDE", default="139.48216"))

    if forecast_date:
        conn.execute(
            """
            INSERT INTO sunshine_daily (
                date, forecast_hours, actual_hours, forecast_temp_c, actual_temp_c,
                forecast_weather_code, actual_weather_code,
                forecast_precipitation_sum_mm, forecast_precipitation_probability_mean, actual_precipitation_sum_mm,
                forecast_shortwave_radiation_sum_mj_m2, actual_shortwave_radiation_sum_mj_m2,
                forecast_pv_total_kwh, forecast_pv_morning_kwh, forecast_pv_midday_kwh,
                forecast_pv_evening_kwh, forecast_pv_calibration_factor,
                source, updated_at
            )
            VALUES (?, ?, NULL, ?, NULL, ?, NULL, ?, ?, NULL, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                forecast_hours=excluded.forecast_hours,
                forecast_temp_c=excluded.forecast_temp_c,
                forecast_weather_code=excluded.forecast_weather_code,
                forecast_precipitation_sum_mm=excluded.forecast_precipitation_sum_mm,
                forecast_precipitation_probability_mean=excluded.forecast_precipitation_probability_mean,
                forecast_shortwave_radiation_sum_mj_m2=excluded.forecast_shortwave_radiation_sum_mj_m2,
                forecast_pv_total_kwh=excluded.forecast_pv_total_kwh,
                forecast_pv_morning_kwh=excluded.forecast_pv_morning_kwh,
                forecast_pv_midday_kwh=excluded.forecast_pv_midday_kwh,
                forecast_pv_evening_kwh=excluded.forecast_pv_evening_kwh,
                forecast_pv_calibration_factor=excluded.forecast_pv_calibration_factor,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (
                forecast_date,
                float(tomorrow_hours) if tomorrow_hours is not None else None,
                float(tomorrow_temp) if tomorrow_temp is not None else None,
                to_int(tomorrow_weather_code),
                to_float(tomorrow_precip_sum),
                to_float(tomorrow_precip_probability),
                to_float(tomorrow_shortwave),
                to_float(pv_totals.get("total_kwh") if isinstance(pv_totals, dict) else None),
                to_float(pv_totals.get("morning_kwh") if isinstance(pv_totals, dict) else None),
                to_float(pv_totals.get("midday_kwh") if isinstance(pv_totals, dict) else None),
                to_float(pv_totals.get("evening_kwh") if isinstance(pv_totals, dict) else None),
                to_float(
                    (
                        pv_calibration.get("effective_factor")
                        if isinstance(pv_calibration, dict)
                        else None
                    )
                    or (pv_calibration.get("factor") if isinstance(pv_calibration, dict) else None)
                ),
                forecast_source,
                ingested_at,
            ),
        )
        hourly_rows = _extract_hourly_forecast_from_plan(data)
        conn.execute("DELETE FROM forecast_hourly WHERE date = ?", (forecast_date,))
        conn.executemany(
            """
            INSERT INTO forecast_hourly (
                date, hour, forecast_pv_kwh, forecast_load_kwh, forecast_charge_kwh,
                forecast_weather_code, forecast_precipitation_mm, forecast_precipitation_probability,
                forecast_cloud_cover, forecast_shortwave_radiation_w_m2,
                source, updated_at
            )
            VALUES (
                :date, :hour, :forecast_pv_kwh, :forecast_load_kwh, :forecast_charge_kwh,
                :forecast_weather_code, :forecast_precipitation_mm, :forecast_precipitation_probability,
                :forecast_cloud_cover, :forecast_shortwave_radiation_w_m2,
                :source, :updated_at
            )
            ON CONFLICT(date, hour) DO UPDATE SET
                forecast_pv_kwh=excluded.forecast_pv_kwh,
                forecast_load_kwh=excluded.forecast_load_kwh,
                forecast_charge_kwh=excluded.forecast_charge_kwh,
                forecast_weather_code=excluded.forecast_weather_code,
                forecast_precipitation_mm=excluded.forecast_precipitation_mm,
                forecast_precipitation_probability=excluded.forecast_precipitation_probability,
                forecast_cloud_cover=excluded.forecast_cloud_cover,
                forecast_shortwave_radiation_w_m2=excluded.forecast_shortwave_radiation_w_m2,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            [
                {
                    **row,
                    "source": "night-charge-plan-hourly",
                    "updated_at": ingested_at,
                }
                for row in hourly_rows
            ],
        )

    today_date = datetime.now().date().isoformat()
    actual_weather: dict[str, float | int | None] = {}
    try:
        actual_weather = _fetch_open_meteo_daily_actual(
            lat=lat,
            lon=lon,
            date_ymd=today_date,
            timezone=timezone,
        )
    except Exception:
        actual_weather = {}

    if any(actual_weather.get(key) is not None for key in actual_weather):
        conn.execute(
            """
            INSERT INTO sunshine_daily (
                date, forecast_hours, actual_hours, forecast_temp_c, actual_temp_c,
                forecast_weather_code, actual_weather_code,
                forecast_precipitation_sum_mm, forecast_precipitation_probability_mean, actual_precipitation_sum_mm,
                forecast_shortwave_radiation_sum_mj_m2, actual_shortwave_radiation_sum_mj_m2,
                source, updated_at
            )
            VALUES (?, NULL, ?, NULL, ?, NULL, ?, NULL, NULL, ?, NULL, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                actual_hours=excluded.actual_hours,
                actual_temp_c=excluded.actual_temp_c,
                actual_weather_code=excluded.actual_weather_code,
                actual_precipitation_sum_mm=excluded.actual_precipitation_sum_mm,
                actual_shortwave_radiation_sum_mj_m2=excluded.actual_shortwave_radiation_sum_mj_m2,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (
                today_date,
                to_float(actual_weather.get("actual_hours")),
                to_float(actual_weather.get("actual_temp_c")),
                to_int(actual_weather.get("actual_weather_code")),
                to_float(actual_weather.get("actual_precipitation_sum_mm")),
                to_float(actual_weather.get("actual_shortwave_radiation_sum_mj_m2")),
                "open-meteo-archive",
                ingested_at,
            ),
        )
    conn.commit()


def ingest_settings_summary(
    conn: sqlite3.Connection,
    *,
    settings_summary_path: Path,
    slot: str,
    ingested_at: str,
) -> None:
    if not settings_summary_path.exists():
        return
    summary = _read_summary(settings_summary_path)
    run_id = str(summary.get("run_id", settings_summary_path.parent.name))
    settings_results = summary.get("setting_results", [])
    for idx, item in enumerate(settings_results):
        profile = str(item.get("profile", "unknown"))
        status = str(item.get("status", "unknown"))
        changed_fields = item.get("changed_fields", [])
        conn.execute(
            """
            INSERT INTO settings_events (
                run_id, slot, profile, status, changed_fields_json, detail_json, source_doc_id, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                slot,
                profile,
                status,
                _safe_json(changed_fields),
                _safe_json(item),
                f"{run_id}-{slot}-{idx:02d}-{profile}",
                ingested_at,
            ),
        )
    conn.commit()


def recalc_cost_daily(
    conn: sqlite3.Connection,
    *,
    day_rate_yen_per_kwh: float,
    updated_at: str,
    tariff_mode: str = "flat",
    night8_day_start_hhmm: str = "07:00",
    night8_day_end_hhmm: str = "23:00",
    night8_day_tier1_upper_kwh: float = 90.0,
    night8_day_tier2_upper_kwh: float = 230.0,
    night8_day_rate_tier1_yen: float = 31.80,
    night8_day_rate_tier2_yen: float = 39.10,
    night8_day_rate_tier3_yen: float = 43.62,
    night8_night_rate_yen: float = 28.85,
) -> None:
    mode = (tariff_mode or "flat").strip().lower()
    if mode == "flat":
        rows = conn.execute(
            """
            SELECT substr(ts, 1, 10) AS day,
                   COALESCE(SUM(MAX(0, COALESCE(load_kwh,0) - COALESCE(buy_kwh,0))), 0) AS self_kwh
            FROM monitoring_samples
            GROUP BY substr(ts, 1, 10)
            ORDER BY day
            """
        ).fetchall()

        cumulative_kwh = 0.0
        cumulative_yen = 0.0
        for row in rows:
            day = str(row["day"])
            self_kwh = float(row["self_kwh"] or 0.0)
            yen = self_kwh * day_rate_yen_per_kwh
            cumulative_kwh += self_kwh
            cumulative_yen += yen
            conn.execute(
                """
                INSERT INTO cost_daily (date, self_consumption_kwh, savings_yen, cumulative_kwh, cumulative_yen, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    self_consumption_kwh=excluded.self_consumption_kwh,
                    savings_yen=excluded.savings_yen,
                    cumulative_kwh=excluded.cumulative_kwh,
                    cumulative_yen=excluded.cumulative_yen,
                    updated_at=excluded.updated_at
                """,
                (day, self_kwh, yen, cumulative_kwh, cumulative_yen, updated_at),
            )
        conn.commit()
        return

    if mode != "night8_tiered":
        raise ValueError(f"unsupported tariff_mode: {tariff_mode}")

    day_start_minute = _parse_hhmm_to_minute(value=night8_day_start_hhmm, name="NIGHT8_DAY_START_HHMM")
    day_end_minute = _parse_hhmm_to_minute(value=night8_day_end_hhmm, name="NIGHT8_DAY_END_HHMM")
    sample_rows = conn.execute(
        """
        SELECT ts, COALESCE(load_kwh, 0) AS load_kwh, COALESCE(buy_kwh, 0) AS buy_kwh
        FROM monitoring_samples
        ORDER BY ts
        """
    ).fetchall()

    day_metrics: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "self_total_kwh": 0.0,
            "self_day_kwh": 0.0,
            "self_night_kwh": 0.0,
            "buy_day_kwh": 0.0,
            "buy_night_kwh": 0.0,
        }
    )
    for row in sample_rows:
        ts_text = str(row["ts"] or "").strip()
        if not ts_text:
            continue
        try:
            ts = datetime.fromisoformat(ts_text)
        except ValueError:
            continue

        day = ts.date().isoformat()
        load_kwh = max(0.0, float(row["load_kwh"] or 0.0))
        buy_kwh = max(0.0, float(row["buy_kwh"] or 0.0))
        self_kwh = max(0.0, load_kwh - buy_kwh)
        minute_of_day = ts.hour * 60 + ts.minute
        is_day_window = _is_within_window(
            minute_of_day,
            start_minute=day_start_minute,
            end_minute=day_end_minute,
        )
        metrics = day_metrics[day]
        metrics["self_total_kwh"] += self_kwh
        if is_day_window:
            metrics["self_day_kwh"] += self_kwh
            metrics["buy_day_kwh"] += buy_kwh
        else:
            metrics["self_night_kwh"] += self_kwh
            metrics["buy_night_kwh"] += buy_kwh

    sorted_days = sorted(day_metrics.keys())
    by_month: dict[str, list[str]] = defaultdict(list)
    for day in sorted_days:
        by_month[day[:7]].append(day)

    daily_savings: dict[str, float] = {}
    for month in sorted(by_month.keys()):
        cumulative_actual_day_kwh = 0.0
        cumulative_counterfactual_day_kwh = 0.0
        for day in by_month[month]:
            metrics = day_metrics[day]
            actual_day_buy_kwh = metrics["buy_day_kwh"]
            actual_night_buy_kwh = metrics["buy_night_kwh"]
            counterfactual_day_buy_kwh = actual_day_buy_kwh + metrics["self_day_kwh"]
            counterfactual_night_buy_kwh = actual_night_buy_kwh + metrics["self_night_kwh"]

            actual_day_cost = _tiered_day_increment_cost(
                previous_kwh=cumulative_actual_day_kwh,
                delta_kwh=actual_day_buy_kwh,
                tier1_upper_kwh=night8_day_tier1_upper_kwh,
                tier2_upper_kwh=night8_day_tier2_upper_kwh,
                rate_tier1_yen=night8_day_rate_tier1_yen,
                rate_tier2_yen=night8_day_rate_tier2_yen,
                rate_tier3_yen=night8_day_rate_tier3_yen,
            )
            counterfactual_day_cost = _tiered_day_increment_cost(
                previous_kwh=cumulative_counterfactual_day_kwh,
                delta_kwh=counterfactual_day_buy_kwh,
                tier1_upper_kwh=night8_day_tier1_upper_kwh,
                tier2_upper_kwh=night8_day_tier2_upper_kwh,
                rate_tier1_yen=night8_day_rate_tier1_yen,
                rate_tier2_yen=night8_day_rate_tier2_yen,
                rate_tier3_yen=night8_day_rate_tier3_yen,
            )
            actual_night_cost = actual_night_buy_kwh * night8_night_rate_yen
            counterfactual_night_cost = counterfactual_night_buy_kwh * night8_night_rate_yen
            daily_savings[day] = (
                counterfactual_day_cost
                + counterfactual_night_cost
                - actual_day_cost
                - actual_night_cost
            )

            cumulative_actual_day_kwh += actual_day_buy_kwh
            cumulative_counterfactual_day_kwh += counterfactual_day_buy_kwh

    cumulative_kwh = 0.0
    cumulative_yen = 0.0
    for day in sorted_days:
        metrics = day_metrics[day]
        self_kwh = float(metrics["self_total_kwh"])
        yen = float(daily_savings.get(day, 0.0))
        cumulative_kwh += self_kwh
        cumulative_yen += yen
        conn.execute(
            """
            INSERT INTO cost_daily (date, self_consumption_kwh, savings_yen, cumulative_kwh, cumulative_yen, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                self_consumption_kwh=excluded.self_consumption_kwh,
                savings_yen=excluded.savings_yen,
                cumulative_kwh=excluded.cumulative_kwh,
                cumulative_yen=excluded.cumulative_yen,
                updated_at=excluded.updated_at
            """,
            (day, self_kwh, yen, cumulative_kwh, cumulative_yen, updated_at),
        )
    conn.commit()


def upsert_battery_daily_metrics(
    conn: sqlite3.Connection,
    *,
    summary_path: Path,
    updated_at: str,
    night_plan_path: Path | None = None,
) -> None:
    if not summary_path.exists():
        return
    summary = _read_summary(summary_path)
    night_plan = _read_json_if_exists(night_plan_path)
    metrics = _extract_battery_daily_from_summary(summary=summary, night_plan=night_plan)
    if metrics is None:
        return
    date = str(metrics["date"])
    target_soc = metrics["target_soc"]
    night_charge_kwh = metrics["night_charge_kwh"]
    pv_max_charge_kwh = metrics["pv_max_charge_kwh"]
    pv_charge_end_soc = metrics["pv_charge_end_soc"]
    pv_charge_end_at = metrics["pv_charge_end_at"]
    conn.execute(
        """
        INSERT INTO battery_daily_metrics (
            date, setting_soc_target_percent, night_charge_kwh, pv_max_charge_kwh,
            pv_charge_end_soc_percent, pv_charge_end_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            setting_soc_target_percent=excluded.setting_soc_target_percent,
            night_charge_kwh=excluded.night_charge_kwh,
            pv_max_charge_kwh=excluded.pv_max_charge_kwh,
            pv_charge_end_soc_percent=excluded.pv_charge_end_soc_percent,
            pv_charge_end_at=excluded.pv_charge_end_at,
            updated_at=excluded.updated_at
        """,
        (date, target_soc, night_charge_kwh, pv_max_charge_kwh, pv_charge_end_soc, pv_charge_end_at, updated_at),
    )
    conn.commit()


def recalc_battery_pv_charge_end_soc(conn: sqlite3.Connection, *, updated_at: str) -> int:
    rows = conn.execute(
        """
        SELECT day, ts, soc_percent
        FROM (
            SELECT
                substr(ts, 1, 10) AS day,
                ts,
                soc_percent,
                ROW_NUMBER() OVER (
                    PARTITION BY substr(ts, 1, 10)
                    ORDER BY ts DESC
                ) AS rn
            FROM monitoring_samples
            WHERE soc_percent IS NOT NULL
              AND COALESCE(pv_kwh, 0) > 0
              AND COALESCE(charge_kwh, 0) > 0
        ) ranked
        WHERE rn = 1
        """
    ).fetchall()
    if not rows:
        return 0

    updated = 0
    for row in rows:
        day = str(row["day"])
        ts = str(row["ts"])
        soc = to_float(row["soc_percent"])
        if soc is None:
            continue
        cur = conn.execute(
            """
            UPDATE battery_daily_metrics
            SET pv_charge_end_soc_percent = ?, pv_charge_end_at = ?, updated_at = ?
            WHERE date = ?
            """,
            (soc, ts, updated_at, day),
        )
        updated += int(cur.rowcount or 0)
    conn.commit()
    return updated


def recalc_battery_end_of_day_soc(conn: sqlite3.Connection, *, updated_at: str) -> int:
    # Backward-compatible wrapper. The metric is now PV-charge-end SOC.
    return recalc_battery_pv_charge_end_soc(conn, updated_at=updated_at)


def upsert_model_parameters_from_plan(conn: sqlite3.Connection, *, night_plan_path: Path, updated_at: str) -> None:
    if not night_plan_path.exists():
        return
    data = json.loads(night_plan_path.read_text(encoding="utf-8"))
    coeff = data.get("coefficients", {})
    if not isinstance(coeff, dict):
        return
    for name, value in coeff.items():
        try:
            mean = float(value)
        except (TypeError, ValueError):
            continue
        variance = abs(mean) * 0.05
        conn.execute(
            """
            INSERT INTO model_parameters (name, mean_value, variance, sample_count, hit_rate, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                mean_value=excluded.mean_value,
                variance=excluded.variance,
                sample_count=model_parameters.sample_count + 1,
                updated_at=excluded.updated_at
            """,
            (name, mean, variance, 1, None, updated_at),
        )
    conn.commit()


def recalc_model_hit_rates(conn: sqlite3.Connection, *, updated_at: str) -> float | None:
    rows = conn.execute(
        """
        SELECT forecast_hours, actual_hours
        FROM sunshine_daily
        WHERE forecast_hours IS NOT NULL
          AND actual_hours IS NOT NULL
        """
    ).fetchall()
    if not rows:
        return None

    smape_values: list[float] = []
    for row in rows:
        fh = to_float(row["forecast_hours"])
        ah = to_float(row["actual_hours"])
        if fh is None or ah is None:
            continue
        # Use sMAPE-like normalization to avoid low-actual-day over-penalty.
        denom = max((abs(ah) + abs(fh)) / 2.0, 0.5)
        smape = abs(ah - fh) / denom
        smape_values.append(min(smape, 2.0))

    if not smape_values:
        return None

    mean_smape = sum(smape_values) / len(smape_values)
    hit_rate = max(0.0, min(1.0, 1.0 - (mean_smape / 2.0)))
    conn.execute(
        """
        UPDATE model_parameters
        SET hit_rate = ?, updated_at = ?
        """,
        (hit_rate, updated_at),
    )
    conn.commit()
    return hit_rate
