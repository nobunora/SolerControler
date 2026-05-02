from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any

from app.config import AppConfig

HISTORY_COLUMNS = [
    "run_at",
    "dry_run",
    "forecast_hours_12h",
    "latest_soc",
    "avg_soc",
    "total_charge",
    "total_discharge",
    "charge_limit_percent",
    "mode",
    "reason",
    "changed",
    "previous_charge_limit_text",
    "summary_path",
    "csv_path",
]


def _parent_mkdir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_history_csv(csv_path: Path, record: dict[str, Any]) -> None:
    _parent_mkdir(csv_path)
    exists = csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_COLUMNS)
        if not exists:
            writer.writeheader()
        row = {k: record.get(k) for k in HISTORY_COLUMNS}
        writer.writerow(row)


def upsert_history_sqlite(db_path: Path, record: dict[str, Any]) -> None:
    _parent_mkdir(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_history (
                run_at TEXT PRIMARY KEY,
                dry_run INTEGER NOT NULL,
                forecast_hours_12h REAL,
                latest_soc REAL,
                avg_soc REAL,
                total_charge REAL,
                total_discharge REAL,
                charge_limit_percent INTEGER,
                mode TEXT,
                reason TEXT,
                changed INTEGER NOT NULL,
                previous_charge_limit_text TEXT,
                summary_path TEXT,
                csv_path TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO run_history (
                run_at, dry_run, forecast_hours_12h, latest_soc, avg_soc,
                total_charge, total_discharge, charge_limit_percent,
                mode, reason, changed, previous_charge_limit_text,
                summary_path, csv_path
            )
            VALUES (
                :run_at, :dry_run, :forecast_hours_12h, :latest_soc, :avg_soc,
                :total_charge, :total_discharge, :charge_limit_percent,
                :mode, :reason, :changed, :previous_charge_limit_text,
                :summary_path, :csv_path
            )
            ON CONFLICT(run_at) DO UPDATE SET
                dry_run=excluded.dry_run,
                forecast_hours_12h=excluded.forecast_hours_12h,
                latest_soc=excluded.latest_soc,
                avg_soc=excluded.avg_soc,
                total_charge=excluded.total_charge,
                total_discharge=excluded.total_discharge,
                charge_limit_percent=excluded.charge_limit_percent,
                mode=excluded.mode,
                reason=excluded.reason,
                changed=excluded.changed,
                previous_charge_limit_text=excluded.previous_charge_limit_text,
                summary_path=excluded.summary_path,
                csv_path=excluded.csv_path
            """,
            {
                "run_at": record.get("run_at"),
                "dry_run": 1 if record.get("dry_run") else 0,
                "forecast_hours_12h": record.get("forecast_hours_12h"),
                "latest_soc": record.get("latest_soc"),
                "avg_soc": record.get("avg_soc"),
                "total_charge": record.get("total_charge"),
                "total_discharge": record.get("total_discharge"),
                "charge_limit_percent": record.get("charge_limit_percent"),
                "mode": record.get("mode"),
                "reason": record.get("reason"),
                "changed": 1 if record.get("changed") else 0,
                "previous_charge_limit_text": record.get("previous_charge_limit_text"),
                "summary_path": record.get("summary_path"),
                "csv_path": record.get("csv_path"),
            },
        )
        conn.commit()
    finally:
        conn.close()


def persist_history(cfg: AppConfig, record: dict[str, Any]) -> None:
    append_history_csv(cfg.history_csv_path, record)
    if cfg.history_sqlite_enabled:
        upsert_history_sqlite(cfg.history_sqlite_path, record)
