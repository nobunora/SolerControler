from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Any

from google.cloud import firestore


TABLES = {
    "monitoring_samples": "ts",
    "sunshine_daily": "date",
    "settings_events": "event_id",
    "cost_daily": "date",
    "battery_daily_metrics": "date",
    "model_parameters": "name",
    "pipeline_runs": "run_key",
}


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cur = conn.execute(f"SELECT * FROM {table}")
    return [dict(r) for r in cur.fetchall()]


def _doc_id(row: dict[str, Any], key_col: str) -> str:
    v = row.get(key_col)
    if v is None:
        return ""
    return str(v)


def migrate(sqlite_path: Path, *, project_id: str | None, database_id: str) -> None:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"sqlite not found: {sqlite_path}")
    client = firestore.Client(project=project_id, database=database_id) if project_id else firestore.Client(database=database_id)
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        for table, key_col in TABLES.items():
            rows = _rows(conn, table)
            if not rows:
                print(f"[migrate] {table}: 0 rows")
                continue
            batch = client.batch()
            count = 0
            for row in rows:
                doc_id = _doc_id(row, key_col)
                if not doc_id:
                    continue
                ref = client.collection(table).document(doc_id)
                payload = {k: row[k] for k in row.keys()}
                batch.set(ref, payload, merge=True)
                count += 1
                if count % 450 == 0:
                    batch.commit()
                    batch = client.batch()
            if count % 450 != 0:
                batch.commit()
            print(f"[migrate] {table}: {count} docs")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default="artifacts/solar_monitor.db")
    parser.add_argument("--project-id", default=os.getenv("FIRESTORE_PROJECT_ID", "").strip() or None)
    parser.add_argument("--database-id", default=os.getenv("FIRESTORE_DATABASE_ID", "(default)"))
    args = parser.parse_args()
    migrate(Path(args.sqlite), project_id=args.project_id, database_id=args.database_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

