"""Historical forecast reconstruction read contract for the dashboard.

Original mutable/snapshot forecast evidence always wins. Later model replays live in a
separate Firestore namespace and are exposed with explicit reconstruction provenance;
they must never masquerade as contemporaneous forecast snapshots.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.dashboard.aggregation import _date_add_iso
from app.dashboard.firestore_repository import (
    _contiguous_date_ranges,
    _firestore_bounds,
    _firestore_forecast_hourly_between,
    _get_global_bounds_firestore,
)
from app.dashboard.repository_support import pick_min_max_dates as _pick_min_max_dates
from app.dashboard.service import merge_forecast_hourly_actuals
from app.parsing.numbers import to_float

RECONSTRUCTED_FORECAST_COLLECTION = "forecast_hourly_reconstructed"
RECONSTRUCTED_FORECAST_SOURCE = "historical_reconstructed_estimate"
_EXPECTED_HOURS = set(range(24))


def _parse_reconstructed_at(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _complete_reconstruction_run(rows: list[dict[str, Any]]) -> bool:
    if len(rows) != 24:
        return False
    hours: set[int] = set()
    for row in rows:
        raw_hour = row.get("hour")
        if not isinstance(raw_hour, (str, int, float)):
            return False
        try:
            hour = int(raw_hour)
        except (TypeError, ValueError):
            return False
        hours.add(hour)
        if to_float(row.get("forecast_pv_kwh")) is None:
            return False
        if to_float(row.get("forecast_load_kwh")) is None:
            return False
        if row.get("is_reconstructed") is not True:
            return False
        if str(row.get("source") or "") != RECONSTRUCTED_FORECAST_SOURCE:
            return False
    return hours == _EXPECTED_HOURS


def _forecast_plan_metadata_by_date(
    client: Any,
    *,
    start_date: str,
    end_date_iso: str,
) -> dict[str, dict[str, float]]:
    """Read forecast-only display metadata without consulting control-plan documents."""
    try:
        query = (
            client.collection("forecast_plans")
            .where("date", ">=", start_date)
            .where("date", "<=", end_date_iso)
        )
        documents = query.stream()
    except Exception:
        return {}

    by_date: dict[str, dict[str, float]] = {}
    for document in documents:
        row = document.to_dict() or {}
        target_date = str(row.get("date") or "")
        if not target_date:
            continue
        metadata: dict[str, float] = {}
        target_soc = to_float(row.get("planned_target_soc_percent"))
        night_charge = to_float(row.get("planned_night_charge_kwh"))
        if target_soc is not None and 0.0 <= target_soc <= 100.0:
            metadata["forecast_target_soc_percent"] = target_soc
        if night_charge is not None and night_charge >= 0.0:
            metadata["forecast_night_charge_kwh"] = night_charge
        if metadata:
            by_date[target_date] = metadata
    return by_date


def _with_forecast_plan_metadata(
    rows: list[dict[str, Any]],
    metadata_by_date: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    if not metadata_by_date:
        return rows
    enriched: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        metadata = metadata_by_date.get(str(item.get("date") or ""))
        if metadata:
            for key, value in metadata.items():
                item.setdefault(key, value)
        enriched.append(item)
    return enriched


def _selected_reconstructed_rows_between(
    client: Any,
    *,
    start_date: str,
    end_date_iso: str,
    original_dates: set[str],
) -> list[dict[str, Any]]:
    runs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    query = (
        client.collection(RECONSTRUCTED_FORECAST_COLLECTION)
        .where("date", ">=", start_date)
        .where("date", "<=", end_date_iso)
    )
    for doc in query.stream():
        row = doc.to_dict() or {}
        target_date = str(row.get("date") or "")
        reconstruction_id = str(row.get("forecast_reconstruction_id") or "")
        if not target_date or not reconstruction_id or target_date in original_dates:
            continue
        runs.setdefault((target_date, reconstruction_id), []).append(row)

    selected: list[dict[str, Any]] = []
    for target_date in sorted({day for day, _ in runs}):
        candidates: list[tuple[datetime, str, list[dict[str, Any]]]] = []
        for (day, reconstruction_id), rows in runs.items():
            if day != target_date or not _complete_reconstruction_run(rows):
                continue
            reconstructed_at_values = {
                str(row.get("forecast_reconstructed_at") or "").strip() for row in rows
            }
            model_versions = {
                str(row.get("forecast_reconstruction_model_version") or "").strip()
                for row in rows
            }
            if len(reconstructed_at_values) != 1 or len(model_versions) != 1:
                continue
            reconstructed_at = _parse_reconstructed_at(next(iter(reconstructed_at_values)))
            if reconstructed_at is None or not next(iter(model_versions)):
                continue
            candidates.append((reconstructed_at, reconstruction_id, rows))
        if not candidates:
            continue
        reconstructed_at, reconstruction_id, rows = max(
            candidates,
            key=lambda item: (item[0], item[1]),
        )
        for row in rows:
            item = dict(row)
            # A later reconstruction is intentionally not an original forecast vintage.
            item.pop("issued_at", None)
            item.pop("forecast_issued_at", None)
            item.pop("forecast_run_id", None)
            item["source"] = RECONSTRUCTED_FORECAST_SOURCE
            item["is_reconstructed"] = True
            item["forecast_reconstruction_id"] = reconstruction_id
            item["forecast_reconstructed_at"] = reconstructed_at.isoformat()
            selected.append(item)
    selected.sort(key=lambda row: (str(row.get("date") or ""), int(row.get("hour") or 0)))
    return selected


def _monitoring_rows_for_reconstructed_dates(
    client: Any,
    *,
    dates: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for range_start, range_end in _contiguous_date_ranges(dates):
        end_next = _date_add_iso(range_end, 1) or range_end
        query = (
            client.collection("monitoring_samples")
            .where("ts", ">=", range_start)
            .where("ts", "<", end_next)
            .order_by("ts")
        )
        for doc in query.stream():
            row = doc.to_dict() or {}
            rows.append(
                {
                    "ts": row.get("ts", doc.id),
                    "load_kwh": row.get("load_kwh"),
                    "soc_percent": row.get("soc_percent"),
                }
            )
    return rows


def firestore_forecast_hourly_with_reconstruction(
    client: Any,
    *,
    start_date: str,
    end_date_iso: str,
) -> list[dict[str, Any]]:
    """Return original forecast evidence, falling back to explicit reconstructions.

    The original reader already enforces complete mutable rows and one eligible immutable
    snapshot run. Reconstructed rows are considered only for dates absent from that
    original-evidence result. Forecast-only SOC metadata is joined by date strictly for
    dashboard display; no control-plan collection is read or modified here.
    """
    original_rows = _firestore_forecast_hourly_between(
        client,
        start_date=start_date,
        end_date_iso=end_date_iso,
    )
    metadata_by_date = _forecast_plan_metadata_by_date(
        client,
        start_date=start_date,
        end_date_iso=end_date_iso,
    )
    original_rows = _with_forecast_plan_metadata(original_rows, metadata_by_date)
    original_dates = {str(row.get("date")) for row in original_rows if row.get("date")}
    reconstructed_rows = _selected_reconstructed_rows_between(
        client,
        start_date=start_date,
        end_date_iso=end_date_iso,
        original_dates=original_dates,
    )
    if not reconstructed_rows:
        return original_rows

    reconstructed_rows = _with_forecast_plan_metadata(reconstructed_rows, metadata_by_date)
    reconstructed_dates = {
        str(row.get("date")) for row in reconstructed_rows if row.get("date")
    }
    monitoring_rows = _monitoring_rows_for_reconstructed_dates(
        client,
        dates=reconstructed_dates,
    )
    reconstructed_with_actuals = merge_forecast_hourly_actuals(
        reconstructed_rows,
        monitoring_rows,
    )
    merged = [*original_rows, *reconstructed_with_actuals]
    merged.sort(key=lambda row: (str(row.get("date") or ""), int(row.get("hour") or 0)))
    return merged


def get_global_bounds_firestore_with_reconstruction(
    client: Any,
    *,
    original_bounds: tuple[str | None, str | None] | None = None,
) -> tuple[str | None, str | None]:
    """Extend dashboard date bounds without changing the original collection contract."""
    candidates: list[str | None] = [*(original_bounds or _get_global_bounds_firestore(client))]
    try:
        candidates.extend(_firestore_bounds(client, RECONSTRUCTED_FORECAST_COLLECTION, "date"))
    except Exception:
        pass
    return _pick_min_max_dates(candidates)
