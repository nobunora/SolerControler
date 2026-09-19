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

from app.dashboard.data import clear_dashboard_cache, load_dashboard_slice


DATASETS = (
    "pv_daily",
    "cost_daily",
    "battery_daily",
    "battery_flow_daily",
    "energy_daily",
    "forecast_hourly",
)
IGNORED_FIELDS = {
    "battery_daily": {"updated_at", "plan_display_source"},
    # SQLite is a flattened validation read model. Reconstruction metadata is
    # retained there for daily aggregation, but original hourly rows expose the
    # nullable schema columns while Firestore omits absent document fields.
    "forecast_hourly": {
        "updated_at",
        "is_reconstructed",
        "forecast_reconstruction_id",
        "forecast_reconstructed_at",
        "forecast_reconstruction_model_version",
        "forecast_reconstruction_basis",
        "forecast_reconstruction_input_provenance",
        "source_plan_sha256",
        "forecast_weather_code",
        "forecast_precipitation_mm",
        "forecast_precipitation_probability",
        "forecast_cloud_cover",
        "forecast_shortwave_radiation_w_m2",
        "forecast_temp_c",
        "forecast_relative_humidity_percent",
        "forecast_dew_point_c",
        "forecast_wind_speed_10m",
        "forecast_target_soc_percent",
        "forecast_night_charge_kwh",
    },
}


def _same_value(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
    return str(left) == str(right)


def _row_identity(row: dict[str, Any]) -> tuple[str, ...] | None:
    day = str(row.get("date") or "").strip()
    if not day:
        return None
    hour = row.get("hour")
    if hour is None or str(hour).strip() == "":
        return (day,)
    try:
        normalized_hour = str(int(float(str(hour))))
    except (TypeError, ValueError):
        normalized_hour = str(hour).strip()
    return (day, normalized_hour)


def _identity_text(identity: tuple[str, ...]) -> str:
    if len(identity) == 1:
        return f"date={identity[0]}"
    return f"date={identity[0]}, hour={identity[1]}"


def _index_rows(
    rows: list[dict[str, Any]],
    *,
    backend: str,
) -> tuple[dict[tuple[str, ...], dict[str, Any]], list[str]]:
    indexed: dict[tuple[str, ...], dict[str, Any]] = {}
    errors: list[str] = []
    for index, row in enumerate(rows):
        identity = _row_identity(row)
        if identity is None:
            errors.append(f"{backend} row {index} has no logical date identity")
            continue
        if identity in indexed:
            errors.append(f"{backend} duplicate row identity: {_identity_text(identity)}")
            continue
        indexed[identity] = row
    return indexed, errors


def _meaningful_fields(row: dict[str, Any], ignored: set[str]) -> set[str]:
    """Treat an omitted Firestore field and a flattened SQLite NULL as equivalent."""
    return {field for field, value in row.items() if field not in ignored and value is not None}


def compare_rows(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    *,
    ignored_fields: set[str] | None = None,
) -> list[str]:
    """Compare backend rows by logical identity rather than provider return order."""
    ignored = ignored_fields or set()
    left_by_key, errors = _index_rows(left, backend="sqlite")
    right_by_key, right_errors = _index_rows(right, backend="firestore")
    errors.extend(right_errors)

    left_keys = set(left_by_key)
    right_keys = set(right_by_key)
    for identity in sorted(left_keys - right_keys):
        errors.append(f"missing from firestore: {_identity_text(identity)}")
    for identity in sorted(right_keys - left_keys):
        errors.append(f"missing from sqlite: {_identity_text(identity)}")

    for identity in sorted(left_keys & right_keys):
        left_row = left_by_key[identity]
        right_row = right_by_key[identity]
        left_fields = _meaningful_fields(left_row, ignored)
        right_fields = _meaningful_fields(right_row, ignored)
        identity_text = _identity_text(identity)
        if left_fields != right_fields:
            errors.append(
                f"{identity_text} fields differ: "
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
            errors.append(f"{identity_text} values differ: {differing}")
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
