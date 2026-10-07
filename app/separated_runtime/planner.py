"""Calculation owner: reuse the canonical forecast and publication workflow."""
from __future__ import annotations

from pathlib import Path
import os
from datetime import datetime, timezone

from google.cloud import storage

from app.runtime import forecast_job
from app.backup.plan_snapshot import archive_plan_snapshot
from app.dashboard.snapshots import dashboard_snapshot_prefix, write_dashboard_snapshots
from app.operations.firestore import open_firestore, upsert_model_parameters_from_plan
from app.kpnet.monitoring_history import find_latest_kpnet_csv_paths
from app.runtime.forced_charge_monitor import estimate_forced_charge_rate_percent_per_hour
from app.separated_runtime.plan import PublishedPlan
from app.separated_runtime.plan_store import FirestorePlanStore


def main() -> int:
    # Downloaded CSVs are model inputs; this role does not produce KPI PNGs.
    os.environ["KP_CSV_PLOT_ENABLED"] = "false"
    result = forecast_job.main(publish_read_models=False)
    if result != 0:
        return result
    artifacts = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
    path = Path(os.getenv("KP_NIGHT_PLAN_PATH", str(artifacts / "night_charge_plan.json")))
    rate = estimate_forced_charge_rate_percent_per_hour(find_latest_kpnet_csv_paths(artifacts))
    plan = PublishedPlan.from_bytes(path.read_bytes(), target_date=forecast_job._target_date(), charge_rate_info=rate,
                                   producer_source_revision=os.environ.get("PLAN_SOURCE_REVISION", ""), inputs_verified=True)
    client = open_firestore()
    # HISTORICAL_FAILURE_LOCK: the planner owns BOTH the exact immutable
    # decision archive and the legacy latest/day dashboard projections. Moving
    # generation out of 03 must never freeze the dashboard's latest plan.
    archive_plan_snapshot(path, storage=storage.Client(), firestore=client, source="planner-generated")
    upsert_model_parameters_from_plan(client, night_plan_path=path, updated_at=datetime.now(timezone.utc).isoformat())
    FirestorePlanStore().publish(plan)
    if dashboard_snapshot_prefix():
        write_dashboard_snapshots(Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db")))
    print("Published validated control plan", flush=True)
    return 0
