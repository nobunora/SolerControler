from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db_sync import sync_firestore_to_sqlite, sync_sqlite_to_firestore


def main() -> int:
    parser = argparse.ArgumentParser(description="Keep Firestore and local SQLite aligned for validation runs.")
    parser.add_argument(
        "--direction",
        choices=("firestore-to-sqlite", "sqlite-to-firestore", "both"),
        default="firestore-to-sqlite",
        help="Default is firestore-to-sqlite so validation always sees the latest remote data first.",
    )
    parser.add_argument("--sqlite", default=os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db"))
    parser.add_argument("--project-id", default=os.getenv("FIRESTORE_PROJECT_ID", "").strip() or None)
    parser.add_argument("--database-id", default=os.getenv("FIRESTORE_DATABASE_ID", "(default)"))
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite)
    if args.direction in {"firestore-to-sqlite", "both"}:
        counts = sync_firestore_to_sqlite(
            sqlite_path=sqlite_path,
            project_id=args.project_id,
            database_id=args.database_id,
        )
        print(
            "[sync_validation_state] firestore->sqlite "
            + ", ".join(f"{table}={count}" for table, count in sorted(counts.items()))
        )

    if args.direction in {"sqlite-to-firestore", "both"}:
        counts = sync_sqlite_to_firestore(
            sqlite_path=sqlite_path,
            project_id=args.project_id,
            database_id=args.database_id,
        )
        print(
            "[sync_validation_state] sqlite->firestore "
            + ", ".join(f"{table}={count}" for table, count in sorted(counts.items()))
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
