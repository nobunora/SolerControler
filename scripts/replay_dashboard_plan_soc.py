from __future__ import annotations

import argparse
import json
import os

from app.operations.dashboard_plan_soc_replay import replay_missing_plan_soc_firestore


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay missing dashboard configured-SOC history from retained forecast_plans evidence."
    )
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write missing-only battery_daily_metrics fields. Without this flag the command is read-only.",
    )
    args = parser.parse_args()

    from google.cloud import firestore

    project_id = os.getenv("FIRESTORE_PROJECT_ID", "").strip() or None
    database_id = os.getenv("FIRESTORE_DATABASE_ID", "").strip() or "(default)"
    client = (
        firestore.Client(project=project_id, database=database_id)
        if project_id
        else firestore.Client(database=database_id)
    )
    report = replay_missing_plan_soc_firestore(
        client,
        start_date=args.start_date,
        end_date=args.end_date,
        apply=bool(args.apply),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
