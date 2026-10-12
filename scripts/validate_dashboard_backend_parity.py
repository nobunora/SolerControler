from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.dashboard.data import clear_dashboard_cache, load_dashboard_slice
from scripts.validate_forecast_display_evidence import (
    _business_errors,
    _empty_window_result,
    load_forecast_evidence_for_window,
    validate_forecast_window_evidence,
)


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


def partition_forecast_metadata(
    *, sqlite_rows: list[dict[str, Any]], firestore_rows: list[dict[str, Any]],
    day_results: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Exclude unsupported SQL fields only after proving the exact FS vintage."""
    left = [dict(row) for row in sqlite_rows]
    right = [dict(row) for row in firestore_rows]
    left_by_key, errors = _index_rows(left, backend="sqlite")
    right_by_key, right_errors = _index_rows(right, backend="firestore")
    errors.extend(right_errors)
    for key in left_by_key.keys() & right_by_key.keys():
        left_row, right_row = left_by_key[key], right_by_key[key]
        proof = day_results.get(key[0], {})
        identity = proof.get("forecast_identity", {})
        for field in ("forecast_capacity_kwh", "forecast_soc_percent"):
            if left_row.get(field) is not None or right_row.get(field) is None:
                continue
            valid = (proof.get("status") == "PASS" and proof.get("original_verified") is True
                     and proof.get("capacity_verified") is True
                     and (field != "forecast_soc_percent" or proof.get("soc_points_verified") == 17)
                     and identity.get("forecast_run_id") == right_row.get("forecast_run_id")
                     and identity.get("forecast_issued_at") == right_row.get("forecast_issued_at"))
            if not valid:
                errors.append(f"date={key[0]} {field}: unverified original")
                continue
            left_row.pop(field, None)
            right_row.pop(field, None)
    return left, right, errors


def _evidence_clients() -> tuple[Any, Any]:
    from google.cloud import storage
    from app.operations.firestore import open_firestore

    return open_firestore(), storage.Client()


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare dashboard API rows from SQLite and Firestore.")
    parser.add_argument("--sqlite", default=os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db"))
    parser.add_argument("--end-date", default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--window-days", type=int, default=31)
    parser.add_argument("--original-dir", type=Path)
    args = parser.parse_args()
    result = _empty_window_result()
    result.update(status="FAIL", required_evidence_status="FAIL",
                  common_parity_errors=dict.fromkeys(DATASETS, 0), common_parity_status="FAIL")
    previous_backend = os.environ.get("DATA_BACKEND")
    try:
        end = date.fromisoformat(args.end_date)
        if not 1 <= args.window_days <= 31:
            raise ValueError("invalid_window")
        start_date = (end - timedelta(days=args.window_days - 1)).isoformat()
        result.update(start_date=start_date, end_date=end.isoformat())
        db_path = Path(args.sqlite)
        deadline = time.monotonic() + 60.0
        client, storage = _evidence_clients()
        failures: list[str] = []
        total_reads, total_bytes = 0, 0
        for attempt in range(2):
            if time.monotonic() >= deadline:
                result.update(status="INCONCLUSIVE", required_evidence_status="INCONCLUSIVE",
                              failure_reasons=["coordinator:deadline"])
                break
            os.environ["DATA_BACKEND"] = "sqlite"
            clear_dashboard_cache()
            sqlite_data = load_dashboard_slice(db_path, end_date=args.end_date,
                                               window_days=args.window_days, include_static=False).data
            os.environ["DATA_BACKEND"] = "firestore"
            clear_dashboard_cache()
            firestore_data = load_dashboard_slice(db_path, end_date=args.end_date,
                                                  window_days=args.window_days, include_static=False).data
            evidence = load_forecast_evidence_for_window(client=client, storage=storage, start_date=start_date,
                end_date=args.end_date, selected_hourly=firestore_data.forecast_hourly,
                original_dir=args.original_dir, deadline=deadline)
            result = validate_forecast_window_evidence(start_date=start_date, end_date=args.end_date,
                evidence_by_date=evidence, displayed_hourly=firestore_data.forecast_hourly,
                displayed_battery_daily=firestore_data.battery_daily)
            total_reads += result["read_count"]
            total_bytes += result["read_bytes"]
            result.update(read_count=total_reads, read_bytes=total_bytes)
            drift = any("snapshot_drift" in item["failure_reasons"] for item in result["days"].values())
            if drift and attempt == 0:
                continue
            failures = []
            counts: dict[str, int] = {}
            for dataset in DATASETS:
                sqlite_rows = getattr(sqlite_data, dataset)
                firestore_rows = getattr(firestore_data, dataset)
                extra: list[str] = []
                if dataset in {"forecast_hourly", "battery_daily"}:
                    extra.extend(_business_errors(sqlite_rows))
                if dataset == "forecast_hourly":
                    sqlite_rows, firestore_rows, partition_errors = partition_forecast_metadata(
                        sqlite_rows=sqlite_rows, firestore_rows=firestore_rows, day_results=result["days"])
                    extra.extend(partition_errors)
                errors = extra + compare_rows(sqlite_rows, firestore_rows, ignored_fields=IGNORED_FIELDS.get(dataset))
                counts[dataset] = len(errors)
                print(f"[dashboard-parity] {dataset}: sqlite={len(sqlite_rows)}, "
                      f"firestore={len(firestore_rows)}, errors={len(errors)}")
                failures.extend(f"{dataset}: {error}" for error in errors[:10])
            result["common_parity_errors"] = counts
            result["common_parity_status"] = "FAIL" if failures else "PASS"
            if failures:
                result["status"] = "FAIL"
            if time.monotonic() >= deadline and result["required_evidence_status"] == "PASS":
                result["required_evidence_status"] = "INCONCLUSIVE"
                if result["status"] != "FAIL":
                    result["status"] = "INCONCLUSIVE"
                result["failure_reasons"].append("coordinator:deadline")
            break
        result.setdefault("common_parity_errors", dict.fromkeys(DATASETS, 0))
        result.setdefault("common_parity_status", "FAIL")
        if failures:
            print("Dashboard backend parity failed:", file=sys.stderr)
            for failure in failures:
                print(f"- {failure}", file=sys.stderr)
        if result["common_parity_status"] == "PASS" and result["required_evidence_status"] == "PASS":
            print(f"Dashboard backend parity passed through {args.end_date}.")
    except Exception:
        # SDK/credential and native payload errors can include secret identifiers.
        # Keep the public failure constant; exceptions never become a PASS.
        result.update(status="FAIL", required_evidence_status="FAIL",
                      failure_reasons=["coordinator:invalid_or_unavailable_evidence"])
    finally:
        if previous_backend is None:
            os.environ.pop("DATA_BACKEND", None)
        else:
            os.environ["DATA_BACKEND"] = previous_backend
        clear_dashboard_cache()
    print("[forecast-evidence] " + json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    if result["common_parity_status"] != "PASS" or result["required_evidence_status"] == "FAIL":
        return 1
    return {"PASS": 0, "INCONCLUSIVE": 2}[result["required_evidence_status"]]


if __name__ == "__main__":
    raise SystemExit(main())
