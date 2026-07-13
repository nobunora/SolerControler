from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.operations_db import (
    _extract_battery_daily_from_summary,
    _extract_final_pv_source_from_plan,
    _extract_final_pv_totals_from_plan,
    _extract_hourly_forecast_from_plan,
    _fetch_open_meteo_today_actual,
    _is_within_window,
    _iter_monitoring_rows,
    _parse_hhmm_to_minute,
    _read_json_if_exists,
    _read_summary,
    _safe_json,
    _tiered_day_increment_cost,
)
from app.utils import env, to_float


def _conninfo_from_env() -> str:
    # DATABASE_URL があれば優先。なければ標準的な PG* 変数を組み立てる。
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return database_url
    host = os.getenv("PGHOST", "").strip()
    dbname = os.getenv("PGDATABASE", "").strip()
    user = os.getenv("PGUSER", "").strip()
    password = os.getenv("PGPASSWORD", "").strip()
    port = int(os.getenv("PGPORT", "5432"))
    sslmode = os.getenv("PGSSLMODE", "prefer").strip() or "prefer"
    connect_timeout = int(os.getenv("PGCONNECT_TIMEOUT", "10"))
    if not host or not dbname or not user or not password:
        raise RuntimeError("PostgreSQL接続情報が不足しています。PGHOST/PGDATABASE/PGUSER/PGPASSWORD を設定してください。")
    return make_conninfo(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        sslmode=sslmode,
        connect_timeout=connect_timeout,
    )


def open_postgres():
    conn = psycopg.connect(_conninfo_from_env(), row_factory=dict_row)
    return conn


def ensure_schema(conn) -> None:
    ddl = [
        """
        CREATE TABLE IF NOT EXISTS monitoring_samples (
            ts TEXT PRIMARY KEY,
            pv_kwh DOUBLE PRECISION,
            load_kwh DOUBLE PRECISION,
            sell_kwh DOUBLE PRECISION,
            buy_kwh DOUBLE PRECISION,
            charge_kwh DOUBLE PRECISION,
            discharge_kwh DOUBLE PRECISION,
            soc_percent DOUBLE PRECISION,
            source_csv TEXT,
            ingested_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sunshine_daily (
            date TEXT PRIMARY KEY,
            forecast_hours DOUBLE PRECISION,
            actual_hours DOUBLE PRECISION,
            forecast_temp_c DOUBLE PRECISION,
            actual_temp_c DOUBLE PRECISION,
            forecast_pv_total_kwh DOUBLE PRECISION,
            forecast_pv_morning_kwh DOUBLE PRECISION,
            forecast_pv_midday_kwh DOUBLE PRECISION,
            forecast_pv_evening_kwh DOUBLE PRECISION,
            forecast_pv_calibration_factor DOUBLE PRECISION,
            source TEXT,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS settings_events (
            event_id BIGSERIAL PRIMARY KEY,
            run_id TEXT NOT NULL,
            slot TEXT NOT NULL,
            profile TEXT NOT NULL,
            status TEXT NOT NULL,
            changed_fields_json JSONB,
            detail_json JSONB,
            source_doc_id TEXT,
            recorded_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS cost_daily (
            date TEXT PRIMARY KEY,
            self_consumption_kwh DOUBLE PRECISION NOT NULL,
            savings_yen DOUBLE PRECISION NOT NULL,
            cumulative_kwh DOUBLE PRECISION NOT NULL,
            cumulative_yen DOUBLE PRECISION NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS battery_daily_metrics (
            date TEXT PRIMARY KEY,
            setting_soc_target_percent DOUBLE PRECISION,
            night_charge_kwh DOUBLE PRECISION,
            pv_charge_end_soc_percent DOUBLE PRECISION,
            pv_charge_end_at TEXT,
            end_of_day_soc_percent DOUBLE PRECISION,
            settings_run_id TEXT,
            source_doc_id TEXT,
            source_status TEXT,
            source_profile TEXT,
            plan_quality_status TEXT,
            plan_should_apply BOOLEAN,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS model_parameters (
            name TEXT PRIMARY KEY,
            mean_value DOUBLE PRECISION NOT NULL,
            variance DOUBLE PRECISION NOT NULL,
            sample_count INTEGER NOT NULL,
            hit_rate DOUBLE PRECISION,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_key TEXT PRIMARY KEY,
            slot TEXT NOT NULL,
            csv_run_id TEXT,
            settings_run_id TEXT,
            csv_rows_upserted INTEGER NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS forecast_hourly (
            date TEXT NOT NULL,
            hour INTEGER NOT NULL,
            forecast_pv_kwh DOUBLE PRECISION,
            forecast_load_kwh DOUBLE PRECISION,
            forecast_charge_kwh DOUBLE PRECISION,
            forecast_weather_code INTEGER,
            forecast_precipitation_mm DOUBLE PRECISION,
            forecast_precipitation_probability DOUBLE PRECISION,
            forecast_cloud_cover DOUBLE PRECISION,
            forecast_shortwave_radiation_w_m2 DOUBLE PRECISION,
            source TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(date, hour)
        )
        """,
    ]
    with conn.cursor() as cur:
        for sql in ddl:
            cur.execute(sql)
        for column_sql in [
            "ALTER TABLE sunshine_daily ADD COLUMN IF NOT EXISTS forecast_pv_total_kwh DOUBLE PRECISION",
            "ALTER TABLE sunshine_daily ADD COLUMN IF NOT EXISTS forecast_pv_morning_kwh DOUBLE PRECISION",
            "ALTER TABLE sunshine_daily ADD COLUMN IF NOT EXISTS forecast_pv_midday_kwh DOUBLE PRECISION",
            "ALTER TABLE sunshine_daily ADD COLUMN IF NOT EXISTS forecast_pv_evening_kwh DOUBLE PRECISION",
            "ALTER TABLE sunshine_daily ADD COLUMN IF NOT EXISTS forecast_pv_calibration_factor DOUBLE PRECISION",
            "ALTER TABLE forecast_hourly ADD COLUMN IF NOT EXISTS forecast_weather_code INTEGER",
            "ALTER TABLE forecast_hourly ADD COLUMN IF NOT EXISTS forecast_precipitation_mm DOUBLE PRECISION",
            "ALTER TABLE forecast_hourly ADD COLUMN IF NOT EXISTS forecast_precipitation_probability DOUBLE PRECISION",
            "ALTER TABLE forecast_hourly ADD COLUMN IF NOT EXISTS forecast_cloud_cover DOUBLE PRECISION",
            "ALTER TABLE forecast_hourly ADD COLUMN IF NOT EXISTS forecast_shortwave_radiation_w_m2 DOUBLE PRECISION",
            "ALTER TABLE battery_daily_metrics ADD COLUMN IF NOT EXISTS pv_charge_end_soc_percent DOUBLE PRECISION",
            "ALTER TABLE battery_daily_metrics ADD COLUMN IF NOT EXISTS pv_charge_end_at TEXT",
            "ALTER TABLE battery_daily_metrics ADD COLUMN IF NOT EXISTS settings_run_id TEXT",
            "ALTER TABLE battery_daily_metrics ADD COLUMN IF NOT EXISTS source_doc_id TEXT",
            "ALTER TABLE battery_daily_metrics ADD COLUMN IF NOT EXISTS source_status TEXT",
            "ALTER TABLE battery_daily_metrics ADD COLUMN IF NOT EXISTS source_profile TEXT",
            "ALTER TABLE battery_daily_metrics ADD COLUMN IF NOT EXISTS plan_quality_status TEXT",
            "ALTER TABLE battery_daily_metrics ADD COLUMN IF NOT EXISTS plan_should_apply BOOLEAN",
            "ALTER TABLE settings_events ADD COLUMN IF NOT EXISTS source_doc_id TEXT",
        ]:
            cur.execute(column_sql)
    conn.commit()


def ingest_monitoring_csvs(
    conn,
    *,
    csv_paths: list[Path],
    ingested_at: str,
) -> int:
    upserted = 0
    with conn.cursor() as cur:
        for csv_path in csv_paths:
            for row in _iter_monitoring_rows(csv_path):
                cur.execute(
                    """
                    INSERT INTO monitoring_samples (
                        ts, pv_kwh, load_kwh, sell_kwh, buy_kwh, charge_kwh, discharge_kwh, soc_percent, source_csv, ingested_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
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
                    (
                        row["ts"],
                        row["pv_kwh"],
                        row["load_kwh"],
                        row["sell_kwh"],
                        row["buy_kwh"],
                        row["charge_kwh"],
                        row["discharge_kwh"],
                        row["soc_percent"],
                        str(csv_path),
                        ingested_at,
                    ),
                )
                upserted += 1
    conn.commit()
    return upserted


def ingest_sunshine_from_night_plan(
    conn,
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
    pv_forecast = data.get("pv_array_forecast", {})
    pv_totals = _extract_final_pv_totals_from_plan(data)
    pv_calibration = pv_forecast.get("calibration", {}) if isinstance(pv_forecast, dict) else {}
    forecast_source = _extract_final_pv_source_from_plan(data)
    lat = float(env("FORECAST_LATITUDE", default="35.67452"))
    lon = float(env("FORECAST_LONGITUDE", default="139.48216"))

    with conn.cursor() as cur:
        if forecast_date:
            cur.execute(
                """
                INSERT INTO sunshine_daily (
                    date, forecast_hours, actual_hours, forecast_temp_c, actual_temp_c,
                    forecast_pv_total_kwh, forecast_pv_morning_kwh, forecast_pv_midday_kwh,
                    forecast_pv_evening_kwh, forecast_pv_calibration_factor,
                    source, updated_at
                )
                VALUES (%s, %s, NULL, %s, NULL, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(date) DO UPDATE SET
                    forecast_hours=excluded.forecast_hours,
                    forecast_temp_c=excluded.forecast_temp_c,
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
            cur.execute("DELETE FROM forecast_hourly WHERE date = %s", (forecast_date,))
            cur.executemany(
                """
                INSERT INTO forecast_hourly (
                    date, hour, forecast_pv_kwh, forecast_load_kwh, forecast_charge_kwh,
                    forecast_weather_code, forecast_precipitation_mm, forecast_precipitation_probability,
                    forecast_cloud_cover, forecast_shortwave_radiation_w_m2,
                    source, updated_at
                )
                VALUES (
                    %(date)s, %(hour)s, %(forecast_pv_kwh)s, %(forecast_load_kwh)s,
                    %(forecast_charge_kwh)s,
                    %(forecast_weather_code)s, %(forecast_precipitation_mm)s,
                    %(forecast_precipitation_probability)s, %(forecast_cloud_cover)s,
                    %(forecast_shortwave_radiation_w_m2)s,
                    %(source)s, %(updated_at)s
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
            cur.execute(
                """
                INSERT INTO sunshine_daily (date, forecast_hours, actual_hours, forecast_temp_c, actual_temp_c, source, updated_at)
                VALUES (%s, NULL, %s, NULL, %s, %s, %s)
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
    conn,
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
    with conn.cursor() as cur:
        for idx, item in enumerate(settings_results):
            profile = str(item.get("profile", "unknown"))
            status = str(item.get("status", "unknown"))
            changed_fields = item.get("changed_fields", [])
            cur.execute(
                """
                INSERT INTO settings_events (
                    run_id, slot, profile, status, changed_fields_json, detail_json, source_doc_id, recorded_at
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
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


def record_planned_day_mode(conn, *, settings_summary_path: Path, recorded_at: str) -> None:
    summary = json.loads(settings_summary_path.read_text(encoding="utf-8"))
    run_id = str(summary.get("run_id", settings_summary_path.parent.name))
    day_plan = summary.get("daytime_mode_plan")
    if not isinstance(day_plan, dict):
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO settings_events (run_id, slot, profile, status, changed_fields_json, detail_json, source_doc_id, recorded_at)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
            """,
            (
                run_id,
                "07",
                "green-mode",
                "planned-from-23",
                "[]",
                json.dumps(day_plan, ensure_ascii=False, separators=(",", ":")),
                f"{run_id}-07-planned-green",
                recorded_at,
            ),
        )
    conn.commit()


def recalc_cost_daily(
    conn,
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
    with conn.cursor() as cur:
        if mode == "flat":
            rows = cur.execute(
                """
                SELECT substring(ts, 1, 10) AS day,
                       COALESCE(SUM(GREATEST(0, COALESCE(load_kwh,0) - COALESCE(buy_kwh,0))), 0) AS self_kwh
                FROM monitoring_samples
                GROUP BY substring(ts, 1, 10)
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
                cur.execute(
                    """
                    INSERT INTO cost_daily (date, self_consumption_kwh, savings_yen, cumulative_kwh, cumulative_yen, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
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
        sample_rows = cur.execute(
            """
            SELECT ts, COALESCE(load_kwh, 0) AS load_kwh, COALESCE(buy_kwh, 0) AS buy_kwh
            FROM monitoring_samples
            ORDER BY ts
            """
        ).fetchall()

        day_metrics: dict[str, dict[str, float]] = {}
        for row in sample_rows:
            ts_text = str(row["ts"] or "").strip()
            if not ts_text:
                continue
            try:
                ts = datetime.fromisoformat(ts_text)
            except ValueError:
                continue
            day = ts.date().isoformat()
            metrics = day_metrics.setdefault(
                day,
                {
                    "self_total_kwh": 0.0,
                    "self_day_kwh": 0.0,
                    "self_night_kwh": 0.0,
                    "buy_day_kwh": 0.0,
                    "buy_night_kwh": 0.0,
                },
            )
            load_kwh = max(0.0, float(row["load_kwh"] or 0.0))
            buy_kwh = max(0.0, float(row["buy_kwh"] or 0.0))
            self_kwh = max(0.0, load_kwh - buy_kwh)
            minute_of_day = ts.hour * 60 + ts.minute
            is_day_window = _is_within_window(
                minute_of_day,
                start_minute=day_start_minute,
                end_minute=day_end_minute,
            )
            metrics["self_total_kwh"] += self_kwh
            if is_day_window:
                metrics["self_day_kwh"] += self_kwh
                metrics["buy_day_kwh"] += buy_kwh
            else:
                metrics["self_night_kwh"] += self_kwh
                metrics["buy_night_kwh"] += buy_kwh

        sorted_days = sorted(day_metrics.keys())
        by_month: dict[str, list[str]] = {}
        for day in sorted_days:
            by_month.setdefault(day[:7], []).append(day)

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
            cur.execute(
                """
                INSERT INTO cost_daily (date, self_consumption_kwh, savings_yen, cumulative_kwh, cumulative_yen, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
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
    conn,
    *,
    summary_path: Path,
    updated_at: str,
    night_plan_path: Path | None = None,
    slot: str | None = None,
) -> None:
    if not summary_path.exists():
        return
    summary = _read_summary(summary_path)
    summary.setdefault("run_id", summary_path.parent.name)
    if slot:
        summary["_metrics_slot"] = slot
    night_plan = _read_json_if_exists(night_plan_path)
    metrics = _extract_battery_daily_from_summary(summary=summary, night_plan=night_plan)
    if metrics is None:
        return
    date = str(metrics["date"])
    target_soc = metrics["target_soc"]
    night_charge_kwh = metrics["night_charge_kwh"]
    pv_charge_end_soc = metrics["pv_charge_end_soc"]
    pv_charge_end_at = metrics["pv_charge_end_at"]
    settings_run_id = metrics["settings_run_id"]
    source_doc_id = metrics["source_doc_id"]
    source_status = metrics["source_status"]
    source_profile = metrics["source_profile"]
    plan_quality_status = metrics["plan_quality_status"]
    plan_should_apply = metrics["plan_should_apply"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO battery_daily_metrics (
                date, setting_soc_target_percent, night_charge_kwh,
                pv_charge_end_soc_percent, pv_charge_end_at,
                settings_run_id, source_doc_id, source_status, source_profile,
                plan_quality_status, plan_should_apply, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(date) DO UPDATE SET
                setting_soc_target_percent=excluded.setting_soc_target_percent,
                night_charge_kwh=excluded.night_charge_kwh,
                pv_charge_end_soc_percent=excluded.pv_charge_end_soc_percent,
                pv_charge_end_at=excluded.pv_charge_end_at,
                settings_run_id=excluded.settings_run_id,
                source_doc_id=excluded.source_doc_id,
                source_status=excluded.source_status,
                source_profile=excluded.source_profile,
                plan_quality_status=excluded.plan_quality_status,
                plan_should_apply=excluded.plan_should_apply,
                updated_at=excluded.updated_at
            """,
            (
                date,
                target_soc,
                night_charge_kwh,
                pv_charge_end_soc,
                pv_charge_end_at,
                settings_run_id,
                source_doc_id,
                source_status,
                source_profile,
                plan_quality_status,
                bool(plan_should_apply) if plan_should_apply is not None else None,
                updated_at,
            ),
        )
    conn.commit()


def recalc_battery_pv_charge_end_soc(conn, *, updated_at: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT day, ts, soc_percent
            FROM (
                SELECT
                    substring(ts, 1, 10) AS day,
                    ts,
                    soc_percent,
                    ROW_NUMBER() OVER (
                        PARTITION BY substring(ts, 1, 10)
                        ORDER BY ts DESC
                    ) AS rn
                FROM monitoring_samples
                WHERE soc_percent IS NOT NULL
                  AND COALESCE(pv_kwh, 0) > 0
                  AND COALESCE(charge_kwh, 0) > 0
            ) ranked
            WHERE rn = 1
            """
        )
        rows = cur.fetchall()
    if not rows:
        return 0

    updated = 0
    with conn.cursor() as cur:
        for row in rows:
            day = str(row["day"])
            ts = str(row["ts"])
            soc_raw = row.get("soc_percent")
            if soc_raw is None:
                continue
            soc = float(soc_raw)
            cur.execute(
                """
                UPDATE battery_daily_metrics
                SET pv_charge_end_soc_percent = %s, pv_charge_end_at = %s, updated_at = %s
                WHERE date = %s
                """,
                (soc, ts, updated_at, day),
            )
            updated += int(cur.rowcount or 0)
    conn.commit()
    return updated


def recalc_battery_end_of_day_soc(conn, *, updated_at: str) -> int:
    # Backward-compatible wrapper. The metric is now PV-charge-end SOC.
    return recalc_battery_pv_charge_end_soc(conn, updated_at=updated_at)


def upsert_model_parameters_from_plan(conn, *, night_plan_path: Path, updated_at: str) -> None:
    if not night_plan_path.exists():
        return
    data = json.loads(night_plan_path.read_text(encoding="utf-8"))
    coeff = data.get("coefficients", {})
    if not isinstance(coeff, dict):
        return
    with conn.cursor() as cur:
        for name, value in coeff.items():
            try:
                mean = float(value)
            except (TypeError, ValueError):
                continue
            variance = abs(mean) * 0.05
            cur.execute(
                """
                INSERT INTO model_parameters (name, mean_value, variance, sample_count, hit_rate, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT(name) DO UPDATE SET
                    mean_value=excluded.mean_value,
                    variance=excluded.variance,
                    sample_count=model_parameters.sample_count + 1,
                    updated_at=excluded.updated_at
                """,
                (name, mean, variance, 1, None, updated_at),
            )
    conn.commit()


def recalc_model_hit_rates(conn, *, updated_at: str) -> float | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT forecast_hours, actual_hours
            FROM sunshine_daily
            WHERE forecast_hours IS NOT NULL
              AND actual_hours IS NOT NULL
            """
        )
        rows = cur.fetchall()
    if not rows:
        return None

    smape_values: list[float] = []
    for row in rows:
        fh = row.get("forecast_hours")
        ah = row.get("actual_hours")
        if fh is None or ah is None:
            continue
        fh_f = float(fh)
        ah_f = float(ah)
        # Use sMAPE-like normalization to avoid low-actual-day over-penalty.
        denom = max((abs(ah_f) + abs(fh_f)) / 2.0, 0.5)
        smape = abs(ah_f - fh_f) / denom
        smape_values.append(min(smape, 2.0))
    if not smape_values:
        return None

    mean_smape = sum(smape_values) / len(smape_values)
    hit_rate = max(0.0, min(1.0, 1.0 - (mean_smape / 2.0)))
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE model_parameters
            SET hit_rate = %s, updated_at = %s
            """,
            (hit_rate, updated_at),
        )
    conn.commit()
    return hit_rate
