from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WeeklyBackupResult:
    created: bool
    path: Path | None
    reason: str


def _week_key(now_utc: datetime) -> str:
    y, w, _ = now_utc.isocalendar()
    return f"{y}-W{w:02d}"


def _backup_due(*, now_utc: datetime, weekday: int) -> bool:
    return now_utc.weekday() == weekday


def _fetch_rows(cursor, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
            continue
        if hasattr(row, "keys"):
            out.append({k: row[k] for k in row.keys()})
            continue
        if isinstance(row, tuple):
            out.append({f"c{i}": v for i, v in enumerate(row)})
            continue
        out.append({"value": row})
    return out


def create_weekly_diff_backup(
    conn,
    *,
    backend: str,
    out_dir: Path,
    now_utc: datetime,
    weekday: int = 5,
    force: bool = False,
) -> WeeklyBackupResult:
    if not force and not _backup_due(now_utc=now_utc, weekday=weekday):
        return WeeklyBackupResult(created=False, path=None, reason="weekday-skip")

    wk = _week_key(now_utc)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"weekly-diff-{wk}.json"
    if out_path.exists() and not force:
        return WeeklyBackupResult(created=False, path=out_path, reason="already-exists")

    since = (now_utc - timedelta(days=7)).replace(microsecond=0).isoformat() + "Z"
    tables = {
        "monitoring_samples": ("SELECT * FROM monitoring_samples WHERE ingested_at >= {p}", (since,)),
        "sunshine_daily": ("SELECT * FROM sunshine_daily WHERE updated_at >= {p}", (since,)),
        "settings_events": ("SELECT * FROM settings_events WHERE recorded_at >= {p}", (since,)),
        "cost_daily": ("SELECT * FROM cost_daily WHERE updated_at >= {p}", (since,)),
        "battery_daily_metrics": ("SELECT * FROM battery_daily_metrics WHERE updated_at >= {p}", (since,)),
        "model_parameters": ("SELECT * FROM model_parameters WHERE updated_at >= {p}", (since,)),
        "pipeline_runs": ("SELECT * FROM pipeline_runs WHERE recorded_at >= {p}", (since,)),
    }
    placeholder = "?" if backend == "sqlite" else "%s"

    payload: dict[str, Any] = {
        "schema_version": 1,
        "backend": backend,
        "week_key": wk,
        "captured_at": now_utc.replace(microsecond=0).isoformat() + "Z",
        "since": since,
        "tables": {},
    }
    cur = conn.cursor()
    try:
        for table, (sql_tmpl, params) in tables.items():
            sql = sql_tmpl.format(p=placeholder)
            payload["tables"][table] = _fetch_rows(cur, sql, params)
    finally:
        try:
            cur.close()
        except Exception:
            pass

    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return WeeklyBackupResult(created=True, path=out_path, reason="created")
