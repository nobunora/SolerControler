"""Independent maximum ledger update after successful monitoring ingestion."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.backup.night_plan_archive import night_plan_archive_prefix
from app.energy_plan.workflow import EnergyModelConfig, _read_rows
from app.forecasting.pv_array import load_pv_array_configs
from app.forecasting.pv_ideal_max import BASIS_VERSION, MODEL_ID, IdealMaxArray, IdealMaxGeometry, calculate_ideal_max
from app.forecasting.clear_ghi_observations import site_fingerprint
from app.forecasting.pv_ideal_max_cloud import IdealMaxCloudArchive
from app.forecasting.pv_ideal_max_store import IdealMaxStore
from app.operations.pv_ideal_max_csv import hourly_observations


def ideal_max_archive_prefix() -> str:
    """Keep the maximum ledger reachable when a child disables plan archives."""
    return os.getenv("PV_IDEAL_MAX_GCS_PREFIX", "").strip() or night_plan_archive_prefix()


def configured_ideal_max_geometry() -> IdealMaxGeometry:
    cfg = EnergyModelConfig.from_env()
    return IdealMaxGeometry(cfg.latitude, cfg.longitude, cfg.timezone, tuple(
        IdealMaxArray(a.azimuth_deg, a.tilt_deg, a.capacity_kw, a.performance_ratio, a.shading_factor)
        for a in load_pv_array_configs()))


def load_ideal_max_for_forecast(target_date: date, *, as_of: datetime,
                                artifacts_dir: Path) -> tuple[dict[int, float], dict[str, Any]] | None:
    """Read the current ledger without issuing predictions or publishing state."""
    prefix = ideal_max_archive_prefix()
    if not prefix and not (artifacts_dir / "pv_ideal_max").exists():
        return None
    geometry = configured_ideal_max_geometry()

    def read(store: IdealMaxStore, snapshot_sha: str | None) -> tuple[dict[int, float], dict[str, Any]]:
        state = store.read_current()
        pv = dict(enumerate(calculate_ideal_max(target_date, geometry, state, as_of=as_of)))
        pack = {"model_id": MODEL_ID, "basis_version": BASIS_VERSION, "geometry_fingerprint": geometry.fingerprint,
                "coefficient": state.coefficient, "revision": state.revision,
                "training_as_of": state.updated_at.isoformat(), "snapshot_sha256": snapshot_sha}
        raw = json.dumps(pack, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        trace = {"schema_version": 1, "model_id": "physical_pv_ideal_max_global_v1", "basis_version": BASIS_VERSION,
                 "site_fingerprint": site_fingerprint(latitude=geometry.latitude, longitude=geometry.longitude,
                    timezone_name=geometry.timezone_name, arrays=[asdict(a) for a in load_pv_array_configs()]),
                 "target_date": target_date.isoformat(), "as_of": as_of.isoformat(),
                 "retrieved_at": datetime.now(timezone.utc).isoformat(), "provider_issued_at": None,
                 "calibration_sha256": hashlib.sha256(raw).hexdigest(), "calibration_pack": pack,
                 "coefficient": state.coefficient, "revision": state.revision,
                 "hourly_pv_kwh": {str(h): value for h, value in pv.items()},
                 "weather_adjustment_applied": False}
        return pv, trace

    if prefix:
        from google.cloud.storage import Client
        archive = IdealMaxCloudArchive(Client(), prefix, geometry)
        with tempfile.TemporaryDirectory(prefix="ideal-max-forecast-") as temporary:
            store = archive.checkout(Path(temporary))
            if store is None:
                return None
            with store:
                return read(store, archive.original["sha256"] if archive.original is not None else None)
    if not (artifacts_dir / "pv_ideal_max" / geometry.fingerprint / "ledger.sqlite3").exists():
        return None
    with IdealMaxStore(artifacts_dir, geometry) as store:
        return read(store, None)


def ingest_ideal_max_csvs(store: IdealMaxStore, csv_paths: list[Path], *, as_of: datetime,
                         issue_clock: Callable[[], datetime] | None = None,
                         issued_not_before: datetime | None = None) -> dict[str, Any]:
    observations, excluded = hourly_observations(_read_rows(csv_paths), zone=ZoneInfo(store.geometry.timezone_name),
                                                as_of=as_of, hours=(0, 24), replay=False)
    records = []
    duplicates = 0
    for observation in observations:
        result = store.ingest(observation, as_of=as_of)
        if result is None:
            duplicates += 1
        else:
            records.append(store.observation_record(observation.interval_start))
    day = as_of.astimezone(ZoneInfo(store.geometry.timezone_name)).date()
    # Actuals are committed first. These are newly issued values, never backdated.
    issued_at = issue_clock() if issue_clock is not None else datetime.now(timezone.utc)
    for target in (day, day + timedelta(days=1)):
        store.predict_day(target, as_of=issued_at)
    issued_records = [r for r in records if r is not None and r["issued_prediction"] is not None]
    evaluated = [r for r in issued_records if issued_not_before is None
                 or (datetime.fromisoformat(r["interval_start"]) >= issued_not_before
                     and datetime.fromisoformat(r["issued_prediction"]["issued_at"]) >= issued_not_before)]
    exceeded = [r for r in evaluated if r["issued_exceeded"]]
    state = store.read_current()
    return {"status": "updated", "received_at": as_of.isoformat(), "issued_at": issued_at.isoformat(), "new_hours": len(records),
            "duplicate_hours": duplicates, "excluded": excluded,
            "evaluated_issued_hours": len(evaluated), "actual_without_issued_prediction": len(records) - len(issued_records),
            "unverified_issue_hours": len(issued_records) - len(evaluated),
            "issued_exceedances": len(exceeded),
            "issued_excess_kwh": sum(r["actual_kwh"] - r["issued_prediction"]["maximum_kwh"] for r in exceeded),
            "coefficient": state.coefficient, "revision": state.revision, "hourly": records}


def update_ideal_max_after_import(csv_paths: list[Path], *, artifacts_dir: Path, as_of: datetime) -> dict[str, Any]:
    """Only an explicitly initialized ledger is active; absence is observable.

    Cloud mode never accepts a container's stale local copy. A failed CAS is
    terminal for this run, while the already completed raw import remains safe.
    """
    prefix = ideal_max_archive_prefix()
    if not prefix and not (artifacts_dir / "pv_ideal_max").exists():
        return {"status": "uninitialized", "backend": "local"}
    geometry = configured_ideal_max_geometry()
    if prefix:
        from google.cloud.storage import Client
        archive = IdealMaxCloudArchive(Client(), prefix, geometry)
        with tempfile.TemporaryDirectory(prefix="ideal-max-checkout-") as temporary:
            store = archive.checkout(Path(temporary))
            if store is None:
                return {"status": "uninitialized", "backend": "gcs"}
            with store:
                if archive.original is None:
                    raise ValueError("cloud checkout lacks initialization provenance")
                result = ingest_ideal_max_csvs(store, csv_paths, as_of=as_of,
                                               issued_not_before=datetime.fromisoformat(archive.original["initialized_at"]))
                result["archive"] = archive.publish(store)
    else:
        if not (artifacts_dir / "pv_ideal_max" / geometry.fingerprint / "ledger.sqlite3").exists():
            return {"status": "uninitialized", "backend": "local"}
        with IdealMaxStore(artifacts_dir, geometry) as store:
            result = ingest_ideal_max_csvs(store, csv_paths, as_of=as_of)
    report_dir = artifacts_dir / "pv_ideal_max_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(result, sort_keys=True, allow_nan=False).encode()
    path = report_dir / f"{hashlib.sha256(raw).hexdigest()}.json"
    if not path.exists():
        path.write_bytes(raw)
    return result
