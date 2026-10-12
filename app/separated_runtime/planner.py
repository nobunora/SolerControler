"""Calculation owner: reuse the canonical forecast and publication workflow."""
from __future__ import annotations

from pathlib import Path
import os
import json
from datetime import datetime, timezone

from google.cloud import storage

from app.runtime import forecast_job
from app.backup.plan_snapshot import archive_plan_original, project_plan_read_models
from app.dashboard.snapshots import dashboard_snapshot_prefix, write_dashboard_snapshots
from app.operations.firestore import open_firestore, project_model_parameters
from app.operations.forecast_persistence import project_forecast_plan
from app.kpnet.monitoring_history import find_latest_kpnet_csv_paths
from app.runtime.forced_charge_monitor import estimate_forced_charge_rate_percent_per_hour
from app.separated_runtime.plan import PublishedPlan
from app.separated_runtime.plan_store import FirestorePlanStore


def main() -> int:
    # Downloaded CSVs are model inputs; this role does not produce KPI PNGs.
    os.environ["KP_CSV_PLOT_ENABLED"] = "false"
    path = forecast_job.generate_forecast_plan()
    artifacts = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
    rate = estimate_forced_charge_rate_percent_per_hour(find_latest_kpnet_csv_paths(artifacts))
    raw = path.read_bytes()
    plan = PublishedPlan.from_bytes(raw, target_date=forecast_job._target_date(), charge_rate_info=rate,
                                   producer_source_revision=os.environ.get("PLAN_SOURCE_REVISION", ""), inputs_verified=True)
    client = open_firestore()
    # HISTORICAL_FAILURE_LOCK: the planner owns BOTH the exact immutable
    # decision archive and the legacy latest/day dashboard projections. Moving
    # generation out of 03 must never freeze the dashboard's latest plan.
    _, archived_doc = archive_plan_original(raw, storage=storage.Client(), firestore=client, source="planner-generated")
    try:
        FirestorePlanStore().publish(plan)
    except Exception as error:
        print(json.dumps({"event": "plan_publication_rejected", "stage": "control_cas",
                          "target_date": plan.target_date, "control_version_id": plan.version_id,
                          "raw_plan_sha256": plan.sha256, "reason_code": type(error).__name__}), flush=True)
        raise
    stage = "forecast_projection"
    try:
        project_forecast_plan(client, raw=raw, plan=plan, private_plan_path=path, timezone_name="Asia/Tokyo")
        stage = "plan_projection"
        project_plan_read_models(archived_doc, raw, firestore=client)
        stage = "parameters_projection"
        project_model_parameters(client, raw=raw, plan=plan, updated_at=datetime.now(timezone.utc).isoformat())
        stage = "dashboard_snapshot"
        if dashboard_snapshot_prefix():
            write_dashboard_snapshots(Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db")), published_plan=plan)
    except Exception as error:
        # Adoption succeeded. Retrying/reapplying control to fix display would
        # confuse publication with projection; retain the adopted original.
        print(json.dumps({"event": "plan_projection_failed", "stage": stage,
                          "target_date": plan.target_date, "control_version_id": plan.version_id,
                          "raw_plan_sha256": plan.sha256, "reason_code": type(error).__name__}), flush=True)
    print("Published validated control plan", flush=True)
    return 0
