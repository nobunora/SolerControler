"""Independent maximum ledger update after successful monitoring ingestion."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.backup.night_plan_archive import night_plan_archive_prefix
from app.energy_plan.workflow import EnergyModelConfig, _read_rows
from app.forecasting.pv_array import load_pv_array_configs
from app.forecasting.pv_ideal_max import IdealMaxArray, IdealMaxGeometry
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
                 or datetime.fromisoformat(r["interval_start"]) >= issued_not_before]
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
