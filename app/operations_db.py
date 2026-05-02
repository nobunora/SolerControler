from __future__ import annotations

import csv
import json
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


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


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    return float(raw)


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


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
        _load_dotenv_if_present()
        slot_raw = _env("CLOUD_JOB_SLOT", "").strip().lower()
        if slot_raw in {"23", "night", "night23"}:
            slot = "23"
        elif slot_raw in {"7", "07", "day", "day07"}:
            slot = "07"
        else:
            slot = slot_raw or "unknown"

        sync_enabled = _env("DATA_DB_SYNC_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        write_only_23 = _env("DATA_DB_WRITE_ONLY_23", "true").strip().lower() in {"1", "true", "yes", "on"}
        weekly_backup_enabled = _env("DATA_WEEKLY_BACKUP_ENABLED", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        weekly_backup_weekday = int(_env("DATA_WEEKLY_BACKUP_WEEKDAY", "5"))
        cost_tariff_mode = _env("COST_TARIFF_MODE", "night8_tiered").strip().lower() or "night8_tiered"
        if cost_tariff_mode not in {"flat", "night8_tiered"}:
            cost_tariff_mode = "night8_tiered"
        day_rate = _env_float("DAY_RATE_YEN_PER_KWH", 31.0)
        return PipelineConfig(
            data_backend=_env("DATA_BACKEND", "sqlite").strip().lower(),
            site_id=_env("SITE_ID", "fuchu-home").strip() or "fuchu-home",
            db_path=Path(_env("DATA_DB_PATH", "artifacts/solar_monitor.db")),
            artifacts_dir=Path(_env("ARTIFACTS_DIR", "artifacts")),
            slot=slot,
            timezone=_env("TIMEZONE", "Asia/Tokyo"),
            day_rate_yen_per_kwh=day_rate,
            cost_tariff_mode=cost_tariff_mode,
            night8_day_start_hhmm=_env("NIGHT8_DAY_START_HHMM", "07:00").strip() or "07:00",
            night8_day_end_hhmm=_env("NIGHT8_DAY_END_HHMM", "23:00").strip() or "23:00",
            night8_day_tier1_upper_kwh=_env_float("NIGHT8_DAY_TIER1_UPPER_KWH", 90.0),
            night8_day_tier2_upper_kwh=_env_float("NIGHT8_DAY_TIER2_UPPER_KWH", 230.0),
            night8_day_rate_tier1_yen=_env_float("NIGHT8_DAY_RATE_TIER1_YEN", 31.80),
            night8_day_rate_tier2_yen=_env_float("NIGHT8_DAY_RATE_TIER2_YEN", 39.10),
            night8_day_rate_tier3_yen=_env_float("NIGHT8_DAY_RATE_TIER3_YEN", 43.62),
            night8_night_rate_yen=_env_float("NIGHT8_NIGHT_RATE_YEN", 28.85),
            storage_gcs_db_uri=_env("DATA_GCS_DB_URI", ""),
            storage_gcs_daily_prefix=_env("DATA_GCS_DAILY_PREFIX", ""),
            storage_sync_enabled=sync_enabled,
            write_only_slot_23=write_only_23,
            weekly_backup_enabled=weekly_backup_enabled,
            weekly_backup_weekday=weekly_backup_weekday,
            weekly_backup_dir=Path(_env("DATA_WEEKLY_BACKUP_DIR", "artifacts/backups/weekly")),
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
        """
    )
    conn.commit()


def _latest_run_dirs(artifacts_dir: Path) -> list[Path]:
    run_dirs = [p for p in artifacts_dir.glob("*") if p.is_dir() and len(p.name) >= 15 and p.name[:8].isdigit()]
    run_dirs.sort(key=lambda p: p.name, reverse=True)
    return run_dirs


def _read_summary(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
                "pv_kwh": _to_float(row.get("発電電力量[kWh]")),
                "load_kwh": _to_float(row.get("消費電力量[kWh]")),
                "sell_kwh": _to_float(row.get("売電電力量[kWh]")),
                "buy_kwh": _to_float(row.get("買電電力量[kWh]")),
                "charge_kwh": _to_float(row.get("充電電力量[kWh]")),
                "discharge_kwh": _to_float(row.get("放電電力量[kWh]")),
                "soc_percent": _to_float(row.get("蓄電残量(SOC)[%]")),
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


def _fetch_open_meteo_today_actual(*, lat: float, lon: float, date_ymd: str, timezone: str) -> tuple[float | None, float | None]:
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": date_ymd,
        "end_date": date_ymd,
        "daily": "sunshine_duration,temperature_2m_mean",
        "timezone": timezone,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    obj = resp.json()
    daily = obj.get("daily", {})
    sunshine = daily.get("sunshine_duration", [])
    temp = daily.get("temperature_2m_mean", [])
    hours = None
    temp_c = None
    if sunshine:
        try:
            hours = float(sunshine[0] or 0) / 3600.0
        except (TypeError, ValueError):
            hours = None
    if temp:
        try:
            temp_c = float(temp[0])
        except (TypeError, ValueError):
            temp_c = None
    return hours, temp_c


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
    lat = float(_env("FORECAST_LATITUDE", "35.67452"))
    lon = float(_env("FORECAST_LONGITUDE", "139.48216"))

    if forecast_date:
        conn.execute(
            """
            INSERT INTO sunshine_daily (date, forecast_hours, actual_hours, forecast_temp_c, actual_temp_c, source, updated_at)
            VALUES (?, ?, NULL, ?, NULL, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                forecast_hours=excluded.forecast_hours,
                forecast_temp_c=excluded.forecast_temp_c,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (
                forecast_date,
                float(tomorrow_hours) if tomorrow_hours is not None else None,
                float(tomorrow_temp) if tomorrow_temp is not None else None,
                "open-meteo-forecast",
                ingested_at,
            ),
        )

    today_date = datetime.now().date().isoformat()
    actual_hours = None
    actual_temp = None
    try:
        actual_hours, actual_temp = _fetch_open_meteo_today_actual(
            lat=lat,
            lon=lon,
            date_ymd=today_date,
            timezone=timezone,
        )
    except Exception:
        actual_hours, actual_temp = None, None

    if actual_hours is not None or actual_temp is not None:
        conn.execute(
            """
            INSERT INTO sunshine_daily (date, forecast_hours, actual_hours, forecast_temp_c, actual_temp_c, source, updated_at)
            VALUES (?, NULL, ?, NULL, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                actual_hours=excluded.actual_hours,
                actual_temp_c=excluded.actual_temp_c,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (
                today_date,
                actual_hours,
                actual_temp,
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
    for item in settings_results:
        profile = str(item.get("profile", "unknown"))
        status = str(item.get("status", "unknown"))
        changed_fields = item.get("changed_fields", [])
        conn.execute(
            """
            INSERT INTO settings_events (
                run_id, slot, profile, status, changed_fields_json, detail_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                slot,
                profile,
                status,
                _safe_json(changed_fields),
                _safe_json(item),
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


def upsert_battery_daily_metrics(conn: sqlite3.Connection, *, summary_path: Path, updated_at: str) -> None:
    if not summary_path.exists():
        return
    summary = _read_summary(summary_path)
    night_plan = summary.get("night_charge_plan", {}) if isinstance(summary, dict) else {}
    if not isinstance(night_plan, dict):
        return
    date = str(night_plan.get("forecast_date", "")).strip()
    if not date:
        return
    target_soc = _to_float(str(night_plan.get("target_soc_7_percent_raw", "")))
    night_charge_kwh = _to_float(str(night_plan.get("required_night_charge_kwh", "")))
    pv_max_charge_kwh = _to_float(str(night_plan.get("predicted_midday_surplus_kwh", "")))
    end_of_day_soc = _to_float(str(night_plan.get("target_soc_7_percent_raw", "")))
    conn.execute(
        """
        INSERT INTO battery_daily_metrics (
            date, setting_soc_target_percent, night_charge_kwh, pv_max_charge_kwh, end_of_day_soc_percent, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            setting_soc_target_percent=excluded.setting_soc_target_percent,
            night_charge_kwh=excluded.night_charge_kwh,
            pv_max_charge_kwh=excluded.pv_max_charge_kwh,
            end_of_day_soc_percent=excluded.end_of_day_soc_percent,
            updated_at=excluded.updated_at
        """,
        (date, target_soc, night_charge_kwh, pv_max_charge_kwh, end_of_day_soc, updated_at),
    )
    conn.commit()


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
