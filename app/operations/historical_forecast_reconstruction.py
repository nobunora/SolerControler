"""Persist later historical forecast reconstructions without corrupting original evidence.

This module is deliberately separate from forecast_hourly_snapshots and forecast_hourly.
A reconstruction is a later estimate built from historical inputs; it is never an
original contemporaneous forecast and must keep that semantic distinction end to end.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.backup.night_plan_archive import read_plan_file
from app.operations.domain import extract_hourly_forecast_from_plan
from app.parsing.numbers import to_float

RECONSTRUCTED_FORECAST_COLLECTION = "forecast_hourly_reconstructed"
RECONSTRUCTION_METADATA_COLLECTION = "forecast_reconstructions"
RECONSTRUCTED_FORECAST_SOURCE = "historical_reconstructed_estimate"
ALLOWED_RECONSTRUCTION_BASES = {
    "historical_archive",
    "historical_model_replay",
    "legacy_model_replay",
}
_EXPECTED_HOURS = set(range(24))


def _normalized_hourly_rows(data: dict[str, Any], *, target_date: str) -> list[dict[str, Any]]:
    forecast_value = data.get("forecast")
    forecast = forecast_value if isinstance(forecast_value, dict) else {}
    forecast_date = str(forecast.get("date") or "").strip()
    if forecast_date != target_date:
        raise ValueError("reconstruction plan forecast.date does not match target date")

    rows = extract_hourly_forecast_from_plan(data)
    if len(rows) != 24:
        raise ValueError("reconstruction must contain exactly 24 hourly rows")
    hours: set[int] = set()
    normalized: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        if str(row.get("date") or "") != target_date:
            raise ValueError("reconstruction rows must all match the target date")
        try:
            hour = int(row.get("hour"))
        except (TypeError, ValueError) as exc:
            raise ValueError("reconstruction row hour is invalid") from exc
        hours.add(hour)
        if to_float(row.get("forecast_pv_kwh")) is None:
            raise ValueError("reconstruction row is missing forecast_pv_kwh")
        if to_float(row.get("forecast_load_kwh")) is None:
            raise ValueError("reconstruction row is missing forecast_load_kwh")
        normalized.append(row)
    if hours != _EXPECTED_HOURS:
        raise ValueError("reconstruction rows must contain unique hours 0 through 23")
    normalized.sort(key=lambda row: int(row["hour"]))
    return normalized


def _source_plan_sha256(plan_path: Path) -> str:
    return hashlib.sha256(plan_path.read_bytes()).hexdigest()


def _reconstruction_id(
    *,
    target_date: str,
    rows: list[dict[str, Any]],
    model_version: str,
    basis: str,
    input_provenance: str,
    source_plan_sha256: str,
) -> str:
    identity = {
        "target_date": target_date,
        "rows": rows,
        "model_version": model_version,
        "basis": basis,
        "input_provenance": input_provenance,
        "source_plan_sha256": source_plan_sha256,
    }
    return hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:24]


def build_reconstructed_forecast_rows(
    *,
    plan_path: Path,
    target_date: str,
    reconstruction_model_version: str,
    reconstruction_basis: str,
    input_provenance: str,
    reconstructed_at: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    model_version = reconstruction_model_version.strip()
    basis = reconstruction_basis.strip()
    provenance = input_provenance.strip()
    if not model_version:
        raise ValueError("reconstruction_model_version is required")
    if basis not in ALLOWED_RECONSTRUCTION_BASES:
        raise ValueError("reconstruction_basis is not an allowed historical basis")
    if not provenance:
        raise ValueError("input_provenance is required")

    data = read_plan_file(plan_path)
    hourly_rows = _normalized_hourly_rows(data, target_date=target_date)
    plan_sha = _source_plan_sha256(plan_path)
    reconstruction_id = _reconstruction_id(
        target_date=target_date,
        rows=hourly_rows,
        model_version=model_version,
        basis=basis,
        input_provenance=provenance,
        source_plan_sha256=plan_sha,
    )
    reconstructed_at_value = reconstructed_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    try:
        parsed = datetime.fromisoformat(reconstructed_at_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("reconstructed_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("reconstructed_at must include a timezone")

    rows: list[dict[str, Any]] = []
    for hourly in hourly_rows:
        row = dict(hourly)
        # Never persist fields that could make a later replay look contemporaneous.
        row.pop("issued_at", None)
        row.pop("forecast_issued_at", None)
        row.pop("forecast_run_id", None)
        rows.append(
            {
                **row,
                "source": RECONSTRUCTED_FORECAST_SOURCE,
                "is_reconstructed": True,
                "forecast_reconstruction_id": reconstruction_id,
                "forecast_reconstructed_at": reconstructed_at_value,
                "forecast_reconstruction_model_version": model_version,
                "forecast_reconstruction_basis": basis,
                "forecast_reconstruction_input_provenance": provenance,
                "source_plan_sha256": plan_sha,
            }
        )

    pv_total = sum(float(to_float(row.get("forecast_pv_kwh")) or 0.0) for row in rows)
    load_total = sum(float(to_float(row.get("forecast_load_kwh")) or 0.0) for row in rows)
    summary = {
        "forecast_reconstruction_id": reconstruction_id,
        "date": target_date,
        "hourly_row_count": 24,
        "forecast_pv_total_kwh": pv_total,
        "forecast_load_total_kwh": load_total,
        "source": RECONSTRUCTED_FORECAST_SOURCE,
        "is_reconstructed": True,
        "forecast_reconstructed_at": reconstructed_at_value,
        "forecast_reconstruction_model_version": model_version,
        "forecast_reconstruction_basis": basis,
        "forecast_reconstruction_input_provenance": provenance,
        "source_plan_sha256": plan_sha,
    }
    return rows, summary


def preview_reconstructed_forecast_plan(
    *,
    plan_path: Path,
    target_date: str,
    reconstruction_model_version: str,
    reconstruction_basis: str,
    input_provenance: str,
    reconstructed_at: str | None = None,
) -> dict[str, Any]:
    _, summary = build_reconstructed_forecast_rows(
        plan_path=plan_path,
        target_date=target_date,
        reconstruction_model_version=reconstruction_model_version,
        reconstruction_basis=reconstruction_basis,
        input_provenance=input_provenance,
        reconstructed_at=reconstructed_at,
    )
    return summary


def persist_reconstructed_forecast_plan(
    client: Any,
    *,
    plan_path: Path,
    target_date: str,
    reconstruction_model_version: str,
    reconstruction_basis: str,
    input_provenance: str,
    reconstructed_at: str | None = None,
) -> int:
    """Persist one complete reconstruction run into the reconstruction namespace only.

    Returns 24 for a newly inserted reconstruction or 0 when the deterministic
    reconstruction id already exists. No mutable/original forecast collection is touched.
    """
    rows, summary = build_reconstructed_forecast_rows(
        plan_path=plan_path,
        target_date=target_date,
        reconstruction_model_version=reconstruction_model_version,
        reconstruction_basis=reconstruction_basis,
        input_provenance=input_provenance,
        reconstructed_at=reconstructed_at,
    )
    reconstruction_id = str(summary["forecast_reconstruction_id"])
    metadata_ref = client.collection(RECONSTRUCTION_METADATA_COLLECTION).document(
        reconstruction_id
    )
    existing = metadata_ref.get()
    if getattr(existing, "exists", False):
        return 0

    batch = client.batch()
    for row in rows:
        hour = int(row["hour"])
        document_id = f"{reconstruction_id}-{target_date}-{hour:02d}"
        batch.set(
            client.collection(RECONSTRUCTED_FORECAST_COLLECTION).document(document_id),
            row,
            merge=False,
        )
    batch.set(metadata_ref, summary, merge=False)
    batch.commit()
    return len(rows)
