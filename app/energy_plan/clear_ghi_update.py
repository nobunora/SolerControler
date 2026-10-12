"""Non-control calibration acquisition; existing bucket and input history only."""
from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import time
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from google.api_core.exceptions import GoogleAPICallError
from requests import RequestException
from numpy.linalg import LinAlgError

from app.backup.night_plan_archive import night_plan_archive_prefix
from app.forecasting.clear_ghi_observations import fetch_satellite_original, observed_day_records, site_fingerprint, training_records
from app.forecasting.clear_ghi_store import ClearGhiStore
from app.forecasting.pv_array import load_pv_array_configs
from app.forecasting.pv_clear_ghi import BASIS_VERSION, ClearGhiCalibration, calibration_from_bytes, canonical_pack_bytes, fit_clear_ghi_calibration, validate_calibration_for_run

if TYPE_CHECKING:
    from app.energy_plan.workflow import EnergyModelContext

BOOTSTRAP_SAMPLE_LIMIT = 48 * 1096


def _bootstrap_actual_rows(context: EnergyModelContext, *, deadline: float | None = None) -> list[dict[str, Any]]:
    """Explicit bounded bootstrap, outside the seven-day load/PV history contract."""
    raw: list[dict[str, Any]] = []
    remaining = 30.0 if deadline is None else min(30.0, deadline - time.monotonic())
    if remaining <= 0:
        raise ValueError("calibration bootstrap deadline expired")
    if os.getenv("DATA_BACKEND", "").strip().lower() == "firestore":
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        client = firestore.Client(project=os.getenv("FIRESTORE_PROJECT_ID") or None, database=os.getenv("FIRESTORE_DATABASE_ID", "(default)"))
        query = client.collection("monitoring_samples").where(filter=FieldFilter("ts", "<", f"{context.target_date}T00:00:00")).order_by("ts").select(["ts", "pv_kwh"]).limit(BOOTSTRAP_SAMPLE_LIMIT + 1)
        raw = [doc.to_dict() or {} for doc in query.stream(timeout=remaining, retry=None)]
    else:
        path = Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db"))
        if path.exists():
            with sqlite3.connect(path, timeout=min(5.0, remaining)) as connection:
                if deadline is not None:
                    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
                exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='monitoring_samples'").fetchone()
                if exists:
                    raw = [{"ts": ts, "pv_kwh": pv} for ts, pv in connection.execute("SELECT ts,pv_kwh FROM monitoring_samples WHERE ts < ? ORDER BY ts LIMIT ?", (f"{context.target_date}T00:00:00", BOOTSTRAP_SAMPLE_LIMIT + 1))]
    if len(raw) > BOOTSTRAP_SAMPLE_LIMIT:
        raise ValueError("calibration bootstrap exceeds budget; explicit offline historical rebuild required")
    merged = {str(row["dt"]): row for row in context.billing_rows or context.rows}
    for row in raw:
        timestamp = datetime.fromisoformat(str(row["ts"]))
        merged.setdefault(str(timestamp), {"dt": timestamp, "pv": row.get("pv_kwh")})
    return list(merged.values())


def load_calibration_for_context(context: EnergyModelContext) -> tuple[ClearGhiCalibration, datetime]:
    deadline = time.monotonic() + 60.0
    config = context.config
    fingerprint = site_fingerprint(latitude=config.latitude, longitude=config.longitude, timezone_name=config.timezone,
        arrays=[asdict(array) for array in load_pv_array_configs()])
    prefix = night_plan_archive_prefix()
    storage_client: Any = None
    if prefix:
        from google.cloud import storage
        storage_client = storage.Client()
    store = ClearGhiStore(config.artifacts_dir, fingerprint=fingerprint, storage=storage_client, cloud_prefix=prefix or None, deadline=deadline)
    state, generation = store.read_current()
    existing: ClearGhiCalibration | None = None
    cutoff = datetime.now(timezone.utc)
    if state:
        raw = json.dumps(state["calibration_pack"], sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        existing = calibration_from_bytes(raw, sha256=state["calibration_sha256"])
        validate_calibration_for_run(existing, expected_site=fingerprint, expected_basis=BASIS_VERSION,
            target_date=date.fromisoformat(context.target_date), as_of=cutoff)
    try:
        actual_rows = list(context.billing_rows or context.rows) if state else _bootstrap_actual_rows(context, deadline=deadline)
        store.check_deadline()
        actual_at = datetime.now(timezone.utc)
        past = [row["dt"].date() for row in actual_rows if isinstance(row.get("dt"), datetime) and row["dt"].date() < date.fromisoformat(context.target_date)]
        if not past:
            raise ValueError("no prior actual calibration days")
        satellite, observed_at = fetch_satellite_original(start_date=min(past), end_date=max(past),
            latitude=config.latitude, longitude=config.longitude, timezone_name=config.timezone, timeout_seconds=store.remaining_timeout(cap=30.0))
        store.check_deadline()
        records = observed_day_records(actual_rows, satellite, target_date=date.fromisoformat(context.target_date),
            actual_available_at=actual_at, certification_available_at=observed_at,
            latitude=config.latitude, longitude=config.longitude, timezone_name=config.timezone, fingerprint=fingerprint,
            previous=state["clear_days"] if state else [])
        store.check_deadline()
        as_of = datetime.now(timezone.utc)
        if existing is not None and state is not None and records == state["clear_days"]:
            print(json.dumps({"event": "pv_calibration_unchanged", "target_date": context.target_date,
                              "calibration_sha256": existing.sha256, "as_of": as_of.isoformat()}), flush=True)
            return existing, as_of
        calibration = fit_clear_ghi_calibration(training_records(records), target_date=date.fromisoformat(context.target_date), as_of=as_of,
            site={"latitude": config.latitude, "longitude": config.longitude, "timezone": config.timezone, "site_fingerprint": fingerprint}, basis_version=BASIS_VERSION)
        store.check_deadline()
        completed_at = datetime.now(timezone.utc)
        completed_pack = json.loads(calibration.raw_bytes)
        completed_pack.update(created_at=completed_at.isoformat(), usable_from=completed_at.isoformat())
        raw = canonical_pack_bytes(completed_pack)
        calibration = calibration_from_bytes(raw, sha256=hashlib.sha256(raw).hexdigest())
        store.publish(calibration, records, expected_generation=generation)
        run_as_of = datetime.now(timezone.utc)
        print(json.dumps({"event": "pv_calibration_updated", "model_id": calibration.model_id,
            "target_date": context.target_date, "as_of": run_as_of.isoformat(), "calibration_sha256": calibration.sha256,
            "state_sha256": store.published_state_sha256,
            "training_days": len(calibration.training_dates), "training_samples": calibration.training_samples}), flush=True)
        return calibration, run_as_of
    except (ValueError, OSError, sqlite3.Error, RequestException, GoogleAPICallError, LinAlgError) as error:
        if existing is None:
            print(json.dumps({"event": "pv_calibration_failed", "stage": "calibration_update", "target_date": context.target_date, "reason_code": type(error).__name__}), flush=True)
            raise
        print(json.dumps({"event": "pv_calibration_retained", "stage": "calibration_update", "target_date": context.target_date,
            "calibration_sha256": existing.sha256, "reason_code": type(error).__name__}), flush=True)
        run_as_of = datetime.now(timezone.utc)
        validate_calibration_for_run(existing, expected_site=fingerprint, expected_basis=BASIS_VERSION,
            target_date=date.fromisoformat(context.target_date), as_of=run_as_of)
        return existing, run_as_of
