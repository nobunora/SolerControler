from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.operations_db import ensure_schema, ingest_monitoring_csvs, open_db
from app.weekly_backup import create_weekly_diff_backup


def test_create_weekly_diff_backup(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        csv_path = tmp_path / "m.csv"
        csv_path.write_text(
            "\n".join(
                [
                    "年月日,時刻,発電電力量[kWh],消費電力量[kWh],売電電力量[kWh],放電電力量[kWh],充電電力量[kWh],蓄電残量(SOC)[%]",
                    "2026/05/01,07:00,1.2,0.8,0.1,0.2,0.5,55",
                ]
            ),
            encoding="utf-8",
        )
        ingest_monitoring_csvs(conn, csv_paths=[csv_path], ingested_at="2026-05-02T00:00:00Z")
        result = create_weekly_diff_backup(
            conn,
            backend="sqlite",
            out_dir=tmp_path / "backups",
            now_utc=datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc),
            force=True,
        )
        assert result.created is True
        assert result.path is not None
        payload = json.loads(result.path.read_text(encoding="utf-8"))
        assert "tables" in payload
        assert "monitoring_samples" in payload["tables"]
        assert len(payload["tables"]["monitoring_samples"]) == 1
    finally:
        conn.close()
