from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.dashboard_data import clear_dashboard_cache, load_dashboard_slice


DATASETS = (
    "pv_daily",
    "cost_daily",
    "battery_daily",
    "battery_flow_daily",
    "energy_daily",
    "forecast_hourly",
)
IGNORED_FIELDS = {"battery_daily": {"updated_at"}}


def _same_value(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
    return str(left) == str(right)


def compare_rows(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    *,
    ignored_fields: set[str] | None = None,
) -> list[str]:
    ignored = ignored_fields or set()
    errors: list[str] = []
    if len(left) != len(right):
        return [f"row count differs: sqlite={len(left)}, firestore={len(right)}"]
    for index, (left_row, right_row) in enumerate(zip(left, right)):
        left_fields = set(left_row) - ignored
        right_fields = set(right_row) - ignored
        identity = f"date={left_row.get('date')}, hour={left_row.get('hour')}"
        if left_fields != right_fields:
            errors.append(
                f"row {index} ({identity}) fields differ: "
                f"sqlite_only={sorted(left_fields - right_fields)}, "
                f"firestore_only={sorted(right_fields - left_fields)}"
            )
            continue
        differing = [
            field
            for field in sorted(left_fields)
            if not _same_value(left_row.get(field), right_row.get(field))
        ]
        if differing:
            errors.append(f"row {index} ({identity}) values differ: {differing}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare dashboard API rows from SQLite and Firestore.")
    parser.add_argument("--sqlite", default=os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db"))
    parser.add_argument("--end-date", default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--window-days", type=int, default=31)
    args = parser.parse_args()

    db_path = Path(args.sqlite)
    os.environ["DATA_BACKEND"] = "sqlite"
    sqlite_data = load_dashboard_slice(
        db_path,
        end_date=args.end_date,
        window_days=args.window_days,
        include_static=False,
    ).data

    os.environ["DATA_BACKEND"] = "firestore"
    clear_dashboard_cache()
    firestore_data = load_dashboard_slice(
        db_path,
        end_date=args.end_date,
        window_days=args.window_days,
        include_static=False,
    ).data

    failures: list[str] = []
    for dataset in DATASETS:
        sqlite_rows = getattr(sqlite_data, dataset)
        firestore_rows = getattr(firestore_data, dataset)
        errors = compare_rows(
            sqlite_rows,
            firestore_rows,
            ignored_fields=IGNORED_FIELDS.get(dataset),
        )
        print(
            f"[dashboard-parity] {dataset}: "
            f"sqlite={len(sqlite_rows)}, firestore={len(firestore_rows)}, errors={len(errors)}"
        )
        failures.extend(f"{dataset}: {error}" for error in errors[:10])

    if failures:
        print("Dashboard backend parity failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"Dashboard backend parity passed through {args.end_date}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
