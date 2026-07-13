from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from app import operations_db as sqlite_ops
from app.firestore_ops import open_firestore


TABLE_SPECS: dict[str, dict[str, Any]] = {
    "monitoring_samples": {
        "key_cols": ("ts",),
        "columns": (
            "ts",
            "pv_kwh",
            "load_kwh",
            "sell_kwh",
            "buy_kwh",
            "charge_kwh",
            "discharge_kwh",
            "soc_percent",
            "source_csv",
            "ingested_at",
        ),
    },
    "sunshine_daily": {
        "key_cols": ("date",),
        "columns": (
            "date",
            "forecast_hours",
            "actual_hours",
            "forecast_temp_c",
            "actual_temp_c",
            "forecast_weather_code",
            "actual_weather_code",
            "forecast_precipitation_sum_mm",
            "forecast_precipitation_probability_mean",
            "actual_precipitation_sum_mm",
            "forecast_shortwave_radiation_sum_mj_m2",
            "actual_shortwave_radiation_sum_mj_m2",
            "forecast_pv_total_kwh",
            "forecast_pv_morning_kwh",
            "forecast_pv_midday_kwh",
            "forecast_pv_evening_kwh",
            "forecast_pv_calibration_factor",
            "source",
            "updated_at",
        ),
    },
    "settings_events": {
        "key_cols": ("event_id",),
        "columns": (
            "event_id",
            "run_id",
            "slot",
            "profile",
            "status",
            "changed_fields_json",
            "detail_json",
            "source_doc_id",
            "recorded_at",
        ),
    },
    "cost_daily": {
        "key_cols": ("date",),
        "columns": ("date", "self_consumption_kwh", "savings_yen", "cumulative_kwh", "cumulative_yen", "updated_at"),
    },
    "battery_daily_metrics": {
        "key_cols": ("date",),
        "columns": (
            "date",
            "setting_soc_target_percent",
            "night_charge_kwh",
            "pv_charge_end_soc_percent",
            "pv_charge_end_at",
            "end_of_day_soc_percent",
            "settings_run_id",
            "source_doc_id",
            "source_status",
            "source_profile",
            "plan_quality_status",
            "plan_should_apply",
            "updated_at",
        ),
    },
    "model_parameters": {
        "key_cols": ("name",),
        "columns": ("name", "mean_value", "variance", "sample_count", "hit_rate", "updated_at"),
    },
    "pipeline_runs": {
        "key_cols": ("run_key",),
        "columns": ("run_key", "slot", "csv_run_id", "settings_run_id", "csv_rows_upserted", "recorded_at"),
    },
    "forecast_hourly": {
        "key_cols": ("date", "hour"),
        "columns": (
            "date",
            "hour",
            "forecast_pv_kwh",
            "forecast_load_kwh",
            "forecast_charge_kwh",
            "forecast_weather_code",
            "forecast_precipitation_mm",
            "forecast_precipitation_probability",
            "forecast_cloud_cover",
            "forecast_shortwave_radiation_w_m2",
            "source",
            "updated_at",
        ),
    },
}

JSON_TEXT_COLUMNS = {
    "settings_events": {"changed_fields_json", "detail_json"},
}


def _json_text(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _parse_json_text(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    text = str(value).strip()
    if not text:
        return text
    if text[0] not in "[{":
        return text
    try:
        return json.loads(text)
    except Exception:
        return text


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _row_value(row: dict[str, Any], key: str) -> Any:
    value = row.get(key)
    if key in {"hour"}:
        return _as_int(value)
    if (
        key.endswith("_kwh")
        or key.endswith("_percent")
        or key.endswith("_yen")
        or key.endswith("_hours")
        or key.endswith("_temp_c")
        or key.endswith("_mm")
        or key.endswith("_probability")
        or key.endswith("_cover")
        or key.endswith("_w_m2")
    ):
        return _as_float(value)
    if key in {"sample_count", "csv_rows_upserted", "forecast_weather_code", "actual_weather_code"}:
        return _as_int(value)
    if key in JSON_TEXT_COLUMNS.get("settings_events", set()):
        return _json_text(value)
    return value


def _sqlite_path(sqlite_path: Path | None) -> Path:
    if sqlite_path is not None:
        return sqlite_path
    return Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db"))


def _open_firestore_client(*, project_id: str | None, database_id: str):
    if project_id:
        from google.cloud import firestore

        return firestore.Client(project=project_id, database=database_id)
    return open_firestore()


def _clear_tables(conn: sqlite3.Connection, table_names: list[str]) -> None:
    for table in table_names:
        conn.execute(f"DELETE FROM {table}")


def _insert_sqlite_row(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    columns = TABLE_SPECS[table]["columns"]
    values = [_row_value(row, col) for col in columns if col != "event_id"]
    if table == "settings_events":
        conn.execute(
            """
            INSERT INTO settings_events (
                run_id, slot, profile, status, changed_fields_json, detail_json, source_doc_id, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("run_id"),
                row.get("slot"),
                row.get("profile"),
                row.get("status"),
                _json_text(row.get("changed_fields_json")),
                _json_text(row.get("detail_json")),
                row.get("source_doc_id") or row.get("event_id"),
                row.get("recorded_at"),
            ),
        )
        return
    if table == "forecast_hourly":
        conn.execute(
            """
            INSERT INTO forecast_hourly (
                date, hour, forecast_pv_kwh, forecast_load_kwh, forecast_charge_kwh,
                forecast_weather_code, forecast_precipitation_mm, forecast_precipitation_probability,
                forecast_cloud_cover, forecast_shortwave_radiation_w_m2,
                source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("date"),
                _as_int(row.get("hour")),
                _as_float(row.get("forecast_pv_kwh")),
                _as_float(row.get("forecast_load_kwh")),
                _as_float(row.get("forecast_charge_kwh")),
                _as_int(row.get("forecast_weather_code")),
                _as_float(row.get("forecast_precipitation_mm")),
                _as_float(row.get("forecast_precipitation_probability")),
                _as_float(row.get("forecast_cloud_cover")),
                _as_float(row.get("forecast_shortwave_radiation_w_m2")),
                row.get("source"),
                row.get("updated_at"),
            ),
        )
        return

    cols = [col for col in columns if col != "event_id"]
    placeholders = ", ".join(["?"] * len(cols))
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
        values,
    )


def _sqlite_upsert_row(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    spec = TABLE_SPECS[table]
    columns = [col for col in spec["columns"] if col != "event_id"]
    key_cols = spec["key_cols"]
    if table == "settings_events":
        doc_id = str(row.get("source_doc_id") or row.get("event_id") or "").strip()
        if not doc_id:
            return
        conn.execute(
            """
            INSERT INTO settings_events (
                run_id, slot, profile, status, changed_fields_json, detail_json, source_doc_id, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("run_id"),
                row.get("slot"),
                row.get("profile"),
                row.get("status"),
                _json_text(row.get("changed_fields_json")),
                _json_text(row.get("detail_json")),
                doc_id,
                row.get("recorded_at"),
            ),
        )
        return

    if table == "forecast_hourly":
        conn.execute(
            """
            INSERT INTO forecast_hourly (
                date, hour, forecast_pv_kwh, forecast_load_kwh, forecast_charge_kwh,
                forecast_weather_code, forecast_precipitation_mm, forecast_precipitation_probability,
                forecast_cloud_cover, forecast_shortwave_radiation_w_m2,
                source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            (
                row.get("date"),
                _as_int(row.get("hour")),
                _as_float(row.get("forecast_pv_kwh")),
                _as_float(row.get("forecast_load_kwh")),
                _as_float(row.get("forecast_charge_kwh")),
                _as_int(row.get("forecast_weather_code")),
                _as_float(row.get("forecast_precipitation_mm")),
                _as_float(row.get("forecast_precipitation_probability")),
                _as_float(row.get("forecast_cloud_cover")),
                _as_float(row.get("forecast_shortwave_radiation_w_m2")),
                row.get("source"),
                row.get("updated_at"),
            ),
        )
        return

    insert_cols = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    updates = ", ".join([f"{col}=excluded.{col}" for col in columns if col not in key_cols])
    conn.execute(
        f"""
        INSERT INTO {table} ({insert_cols})
        VALUES ({placeholders})
        ON CONFLICT({", ".join(key_cols)}) DO UPDATE SET
            {updates}
        """,
        [_row_value(row, col) for col in columns],
    )


def sync_firestore_to_sqlite(*, sqlite_path: Path | None = None, project_id: str | None = None, database_id: str = "(default)") -> dict[str, int]:
    path = _sqlite_path(sqlite_path)
    client = _open_firestore_client(project_id=project_id, database_id=database_id)
    conn = sqlite_ops.open_db(path)
    sqlite_ops.ensure_schema(conn)
    counts: dict[str, int] = {}
    try:
        with conn:
            _clear_tables(conn, list(TABLE_SPECS.keys()))
            for table_name in TABLE_SPECS:
                if table_name == "settings_events":
                    docs = client.collection(table_name).order_by("recorded_at").stream()
                elif table_name == "forecast_hourly":
                    docs = client.collection(table_name).order_by("date").stream()
                else:
                    docs = client.collection(table_name).stream()
                count = 0
                for doc in docs:
                    row = dict(doc.to_dict() or {})
                    if table_name == "settings_events":
                        row.setdefault("event_id", doc.id)
                        row.setdefault("source_doc_id", doc.id)
                    elif table_name == "forecast_hourly":
                        row.setdefault("date", row.get("date") or doc.id[:10])
                        if row.get("hour") is None and isinstance(doc.id, str) and "-" in doc.id:
                            try:
                                row["hour"] = int(doc.id.rsplit("-", 1)[-1])
                            except ValueError:
                                pass
                    else:
                        key_cols = TABLE_SPECS[table_name]["key_cols"]
                        if len(key_cols) == 1:
                            row.setdefault(key_cols[0], doc.id)
                    _insert_sqlite_row(conn, table_name, row)
                    count += 1
                counts[table_name] = count
    finally:
        conn.close()
    return counts


def sync_sqlite_to_firestore(*, sqlite_path: Path | None = None, project_id: str | None = None, database_id: str = "(default)") -> dict[str, int]:
    path = _sqlite_path(sqlite_path)
    if not path.exists():
        raise FileNotFoundError(f"sqlite not found: {path}")
    client = _open_firestore_client(project_id=project_id, database_id=database_id)
    conn = sqlite_ops.open_db(path)
    sqlite_ops.ensure_schema(conn)
    counts: dict[str, int] = {}
    try:
        with conn:
            for table_name, spec in TABLE_SPECS.items():
                rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table_name}").fetchall()]
                batch = client.batch()
                batch_count = 0
                count = 0
                for row in rows:
                    if table_name == "settings_events":
                        doc_id = str(row.get("source_doc_id") or row.get("event_id") or "").strip()
                        if not doc_id:
                            continue
                        payload = {
                            "event_id": doc_id,
                            "run_id": row.get("run_id"),
                            "slot": row.get("slot"),
                            "profile": row.get("profile"),
                            "status": row.get("status"),
                            "changed_fields_json": _parse_json_text(row.get("changed_fields_json")),
                            "detail_json": _parse_json_text(row.get("detail_json")),
                            "source_doc_id": doc_id,
                            "recorded_at": row.get("recorded_at"),
                        }
                    else:
                        doc_id = str(row.get(spec["key_cols"][0]) if len(spec["key_cols"]) == 1 else "-".join(str(row.get(k)) for k in spec["key_cols"]))
                        payload = {}
                        for col in spec["columns"]:
                            if col == "event_id":
                                continue
                            if col in {"changed_fields_json", "detail_json"}:
                                payload[col] = _parse_json_text(row.get(col))
                            else:
                                payload[col] = row.get(col)
                    ref = client.collection(table_name).document(doc_id)
                    batch.set(ref, payload, merge=True)
                    batch_count += 1
                    count += 1
                    if batch_count >= 450:
                        batch.commit()
                        batch = client.batch()
                        batch_count = 0
                if batch_count > 0:
                    batch.commit()
                counts[table_name] = count
    finally:
        conn.close()
    return counts
