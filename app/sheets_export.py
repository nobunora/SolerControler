from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any


SHEETS_SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _norm(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return str(v)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SheetsExportConfig:
    enabled: bool
    slot_only: str
    timezone: str
    spreadsheet_id: str
    spreadsheet_title: str
    share_email: str
    backend: str
    sqlite_db_path: Path

    @staticmethod
    def from_env() -> "SheetsExportConfig":
        return SheetsExportConfig(
            enabled=_env_bool("SHEETS_EXPORT_ENABLED", False),
            slot_only=(os.getenv("SHEETS_EXPORT_SLOT_ONLY", "23").strip() or "23"),
            timezone=(os.getenv("SHEETS_EXPORT_TIMEZONE", "Asia/Tokyo").strip() or "Asia/Tokyo"),
            spreadsheet_id=os.getenv("SHEETS_SPREADSHEET_ID", "").strip(),
            spreadsheet_title=(os.getenv("SHEETS_SPREADSHEET_TITLE", "SolarController Backup").strip() or "SolarController Backup"),
            share_email=os.getenv("SHEETS_SHARE_EMAIL", "").strip(),
            backend=(os.getenv("DATA_BACKEND", "sqlite").strip().lower() or "sqlite"),
            sqlite_db_path=Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db")),
        )


def _today_jst_str(tz_name: str) -> str:
    return datetime.now(ZoneInfo(tz_name)).date().isoformat()


def _google_services():
    import google.auth
    from googleapiclient.discovery import build

    creds, _ = google.auth.default(scopes=SHEETS_SCOPE)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    return sheets, drive


def _ensure_spreadsheet(sheets, drive, *, spreadsheet_id: str, title: str) -> str:
    if spreadsheet_id:
        return spreadsheet_id
    try:
        created = (
            sheets.spreadsheets()
            .create(body={"properties": {"title": title}}, fields="spreadsheetId,spreadsheetUrl")
            .execute()
        )
        sid = str(created.get("spreadsheetId", "")).strip()
        surl = str(created.get("spreadsheetUrl", "")).strip()
        print(f"[sheets_export] created by sheets.create spreadsheet_id={sid} url={surl}")
        return sid
    except Exception as exc:
        print(f"[sheets_export] sheets.create failed: {exc}; fallback to drive.files.create")
        created = (
            drive.files()
            .create(
                body={"name": title, "mimeType": "application/vnd.google-apps.spreadsheet"},
                fields="id,webViewLink",
            )
            .execute()
        )
        sid = str(created.get("id", "")).strip()
        surl = str(created.get("webViewLink", "")).strip()
        print(f"[sheets_export] created by drive.files.create spreadsheet_id={sid} url={surl}")
        return sid


def _share_spreadsheet(drive, *, spreadsheet_id: str, email: str) -> None:
    if not email:
        return
    permission = {"type": "user", "role": "writer", "emailAddress": email}
    try:
        drive.permissions().create(
            fileId=spreadsheet_id,
            body=permission,
            sendNotificationEmail=False,
            fields="id",
        ).execute()
        print(f"[sheets_export] shared spreadsheet with {email}")
    except Exception as exc:
        print(f"[sheets_export] share skipped/failed for {email}: {exc}")


def _ensure_sheet_tabs(sheets, spreadsheet_id: str, titles: list[str]) -> None:
    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title").execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    reqs = []
    for t in titles:
        if t not in existing:
            reqs.append({"addSheet": {"properties": {"title": t}}})
    if reqs:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": reqs},
        ).execute()


def _read_meta_map(sheets, spreadsheet_id: str) -> dict[str, str]:
    try:
        res = (
            sheets.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range="meta!A:B")
            .execute()
        )
    except Exception:
        return {}
    out: dict[str, str] = {}
    for row in res.get("values", []):
        if len(row) >= 2:
            out[str(row[0])] = str(row[1])
    return out


def _write_sheet_table(sheets, spreadsheet_id: str, tab: str, headers: list[str], rows: list[dict[str, Any]]) -> None:
    values = [headers]
    for row in rows:
        values.append([_norm(row.get(h)) for h in headers])
    sheets.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{tab}!A:ZZ",
        body={},
    ).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{tab}!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()


def _update_meta(sheets, spreadsheet_id: str, *, last_export_date_jst: str, exported_at_utc: str, slot: str) -> None:
    values = [
        ["key", "value"],
        ["last_export_date_jst", last_export_date_jst],
        ["last_exported_at_utc", exported_at_utc],
        ["last_slot", slot],
    ]
    sheets.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range="meta!A:B", body={}).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="meta!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()


def _load_sqlite_tables(db_path: Path) -> dict[str, list[dict[str, Any]]]:
    if not db_path.exists():
        return {
            "sunshine_daily": [],
            "cost_daily": [],
            "battery_daily_metrics": [],
            "model_parameters": [],
            "settings_events": [],
            "pipeline_runs": [],
        }
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        def q(sql: str) -> list[dict[str, Any]]:
            return [dict(r) for r in conn.execute(sql).fetchall()]

        return {
            "sunshine_daily": q("SELECT * FROM sunshine_daily ORDER BY date"),
            "cost_daily": q("SELECT * FROM cost_daily ORDER BY date"),
            "battery_daily_metrics": q("SELECT * FROM battery_daily_metrics ORDER BY date"),
            "model_parameters": q("SELECT * FROM model_parameters ORDER BY name"),
            "settings_events": q("SELECT * FROM settings_events ORDER BY recorded_at"),
            "pipeline_runs": q("SELECT * FROM pipeline_runs ORDER BY recorded_at"),
        }
    finally:
        conn.close()


def _load_postgres_tables() -> dict[str, list[dict[str, Any]]]:
    from app.postgres_ops import open_postgres

    conn = open_postgres()
    try:
        with conn.cursor() as cur:
            def q(sql: str) -> list[dict[str, Any]]:
                cur.execute(sql)
                return list(cur.fetchall())

            return {
                "sunshine_daily": q("SELECT * FROM sunshine_daily ORDER BY date"),
                "cost_daily": q("SELECT * FROM cost_daily ORDER BY date"),
                "battery_daily_metrics": q("SELECT * FROM battery_daily_metrics ORDER BY date"),
                "model_parameters": q("SELECT * FROM model_parameters ORDER BY name"),
                "settings_events": q("SELECT * FROM settings_events ORDER BY recorded_at"),
                "pipeline_runs": q("SELECT * FROM pipeline_runs ORDER BY recorded_at"),
            }
    finally:
        conn.close()


def _load_firestore_tables() -> dict[str, list[dict[str, Any]]]:
    from app.firestore_ops import open_firestore

    client = open_firestore()
    def read(col: str, order_by: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for doc in client.collection(col).order_by(order_by).stream():
            row = doc.to_dict() or {}
            out.append(row)
        return out

    return {
        "sunshine_daily": read("sunshine_daily", "date"),
        "cost_daily": read("cost_daily", "date"),
        "battery_daily_metrics": read("battery_daily_metrics", "date"),
        "model_parameters": read("model_parameters", "name"),
        "settings_events": read("settings_events", "recorded_at"),
        "pipeline_runs": read("pipeline_runs", "recorded_at"),
    }


def _load_tables(cfg: SheetsExportConfig) -> dict[str, list[dict[str, Any]]]:
    if cfg.backend == "postgres":
        return _load_postgres_tables()
    if cfg.backend == "firestore":
        return _load_firestore_tables()
    return _load_sqlite_tables(cfg.sqlite_db_path)


def run_export(*, slot: str) -> int:
    cfg = SheetsExportConfig.from_env()
    if not cfg.enabled:
        print("[sheets_export] disabled")
        return 0
    if cfg.slot_only and slot != cfg.slot_only:
        print(f"[sheets_export] skip: slot={slot} target={cfg.slot_only}")
        return 0
    now_utc = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    today_jst = _today_jst_str(cfg.timezone)
    sheets, drive = _google_services()
    spreadsheet_id = _ensure_spreadsheet(
        sheets,
        drive,
        spreadsheet_id=cfg.spreadsheet_id,
        title=cfg.spreadsheet_title,
    )
    _ensure_sheet_tabs(
        sheets,
        spreadsheet_id,
        [
            "meta",
            "sunshine_daily",
            "cost_daily",
            "battery_daily_metrics",
            "model_parameters",
            "settings_events",
            "pipeline_runs",
        ],
    )
    _share_spreadsheet(drive, spreadsheet_id=spreadsheet_id, email=cfg.share_email)

    meta = _read_meta_map(sheets, spreadsheet_id)
    if meta.get("last_export_date_jst", "") == today_jst:
        print(f"[sheets_export] already exported today ({today_jst})")
        print(f"[sheets_export] spreadsheet_id={spreadsheet_id}")
        return 0

    tables = _load_tables(cfg)
    specs: list[tuple[str, str, list[str]]] = [
        (
            "sunshine_daily",
            "sunshine_daily",
            [
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
                "source",
                "updated_at",
            ],
        ),
        ("cost_daily", "cost_daily", ["date", "self_consumption_kwh", "savings_yen", "cumulative_kwh", "cumulative_yen", "updated_at"]),
        ("battery_daily_metrics", "battery_daily_metrics", ["date", "setting_soc_target_percent", "night_charge_kwh", "pv_max_charge_kwh", "end_of_day_soc_percent", "updated_at"]),
        ("model_parameters", "model_parameters", ["name", "mean_value", "variance", "sample_count", "hit_rate", "updated_at"]),
        ("settings_events", "settings_events", ["event_id", "run_id", "slot", "profile", "status", "changed_fields_json", "detail_json", "recorded_at"]),
        ("pipeline_runs", "pipeline_runs", ["run_key", "slot", "csv_run_id", "settings_run_id", "csv_rows_upserted", "recorded_at"]),
    ]
    for table_key, tab, headers in specs:
        _write_sheet_table(sheets, spreadsheet_id, tab, headers, tables.get(table_key, []))

    _update_meta(
        sheets,
        spreadsheet_id,
        last_export_date_jst=today_jst,
        exported_at_utc=now_utc,
        slot=slot,
    )
    print(f"[sheets_export] done spreadsheet_id={spreadsheet_id}")
    return 0
