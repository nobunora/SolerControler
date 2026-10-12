"""Dedicated non-control daily forecast job."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.operations.firestore import open_firestore
from app.dashboard.snapshots import dashboard_snapshot_prefix, write_dashboard_snapshots
from app.operations.forecast_persistence import persist_forecast_only_plan
from app.operations.forecast_recovery import recover_missing_forecast_snapshots
from app.runtime.command_adapter import _run


def _target_date() -> str:
    return datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()


def generate_forecast_plan() -> Path:
    """Generate into a private run directory; do not publish forecast projections."""
    target_date = _target_date()
    artifacts_dir = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    plan_path = Path(tempfile.mkdtemp(prefix="forecast-plan-", dir=artifacts_dir)) / "night_charge_plan.json"
    # HISTORICAL_FAILURE_LOCK: recover prior saved forecasts before refreshing
    # actuals/bootstrap; a successful new day must not leave yesterday missing.
    client = open_firestore()
    recovery = recover_missing_forecast_snapshots(
        client, target_date=target_date,
        recorded_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    print(f"[forecast_job] forecast recovery {recovery}", flush=True)
    _run([sys.executable, "kpnet_main.py"], {"KP_WORKFLOW_MODE": "csv"}, timeout_seconds=240)
    _run(
        [sys.executable, "db_pipeline_main.py"],
        {
            "DATA_PIPELINE_INCLUDE_SETTINGS": "false",
            "DATA_PIPELINE_INCLUDE_NIGHT_PLAN": "false",
            "DASHBOARD_SNAPSHOT_GCS_PREFIX": "",
            "NIGHT_PLAN_ARCHIVE_GCS_PREFIX": "",
        },
        timeout_seconds=240,
    )
    _run(
        [sys.executable, "energy_model_main.py"],
        {"FORECAST_DATE_OVERRIDE": target_date, "KP_NIGHT_PLAN_PATH": str(plan_path)},
        timeout_seconds=240,
    )
    if not plan_path.is_file():
        raise ValueError("forecast generation did not produce its private plan")
    return plan_path


def main(*, publish_read_models: bool = True) -> int:
    plan_path = generate_forecast_plan()
    target_date = _target_date()
    client = open_firestore()
    snapshot_count = persist_forecast_only_plan(
        client,
        plan_path=plan_path,
        target_date=target_date,
        timezone_name="Asia/Tokyo",
    )
    print(
        f"[forecast_job] persisted target_date={target_date} immutable_snapshot_rows={snapshot_count}",
        flush=True,
    )
    # Legacy callers still receive the configured path. Publication uses the
    # private original; this compatibility copy is never reread by the planner.
    artifacts_dir = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
    destination = Path(os.getenv("KP_NIGHT_PLAN_PATH", str(artifacts_dir / "night_charge_plan.json")))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix="plan-", suffix=".part", delete=False) as temporary:
        temporary.write(plan_path.read_bytes())
        temporary_path = Path(temporary.name)
    temporary_path.replace(destination)
    if publish_read_models and dashboard_snapshot_prefix():
        snapshot_result = write_dashboard_snapshots(
            Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db"))
        )
        for kind, info in snapshot_result.items():
            rebuild_mode = info.get("rebuild_mode")
            mode_suffix = f" rebuild_mode={rebuild_mode}" if rebuild_mode else ""
            print(
                "[forecast_job] dashboard snapshot "
                f"kind={kind} raw_bytes={info['raw_bytes']} gzip_bytes={info['gzip_bytes']}"
                f"{mode_suffix}",
                flush=True,
            )
    return 0
