"""Dedicated non-control daily forecast job."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.operations.firestore import open_firestore
from app.operations.forecast_persistence import persist_forecast_only_plan
from app.runtime.command_adapter import _run


def _target_date() -> str:
    return datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()


def main() -> int:
    target_date = _target_date()
    artifacts_dir = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
    plan_path = Path(os.getenv("KP_NIGHT_PLAN_PATH", str(artifacts_dir / "night_charge_plan.json")))
    _run([sys.executable, "kpnet_main.py"], {"KP_WORKFLOW_MODE": "csv"}, timeout_seconds=240)
    _run(
        [sys.executable, "energy_model_main.py"],
        {"FORECAST_DATE_OVERRIDE": target_date},
        timeout_seconds=240,
    )
    snapshot_count = persist_forecast_only_plan(
        open_firestore(),
        plan_path=plan_path,
        target_date=target_date,
        timezone_name="Asia/Tokyo",
    )
    print(
        f"[forecast_job] persisted target_date={target_date} immutable_snapshot_rows={snapshot_count}",
        flush=True,
    )
    return 0
