"""One-shot actual-only worker. Never generate, adopt or rewrite a forecast."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

from google.cloud import firestore
from dataclasses import replace

from app.dashboard.date_snapshots import capture_date_refresh, publish_date_refresh
from app.kpnet.monitoring_history import find_latest_kpnet_csv_paths
from app.operations import firestore as source
from app.operations.monitoring_sync import monitoring_sync_window
from app.operations.sqlite import PipelineConfig
from app.runtime.command_adapter import _run


def _claim(client: Any, execution: str) -> bool:
    lock = client.collection("dashboard_actual_import_runs").document("active")

    @firestore.transactional
    def claim(transaction: Any) -> bool:
        current = lock.get(transaction=transaction, timeout=4, retry=None).to_dict() or {}
        # A killed worker stays unresolved until read-only reconciliation. There
        # is no expiry that can start another import while the first is unknown.
        if current.get("status") == "running":
            return False
        transaction.set(lock, {"execution": execution, "status": "running"})
        return True

    return bool(claim(client.transaction(max_attempts=1)))


def main() -> int:
    execution = os.environ["CLOUD_RUN_EXECUTION"]
    # This absolute JST date is fixed in the authenticated request. Never replace
    # it with the worker's date when queueing/download crosses midnight.
    target_date = date.fromisoformat(os.environ["ACTUAL_IMPORT_TARGET_DATE"]).isoformat()
    if target_date > datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat():
        raise ValueError("future actual import is forbidden")
    client = source.open_firestore()
    receipt = client.collection("dashboard_actual_import_runs").document(execution)
    if not _claim(client, execution):
        receipt.set({"status": "busy", "reason": "another_import_is_unresolved"}, timeout=4, retry=None)
        return 1
    receipt.set({"status": "running", "target_date": target_date}, timeout=4, retry=None)
    status = "failed"
    stage = "baseline_verification"
    actuals_saved: bool | None = False
    pending_dates: list[str] = []
    try:
        captured = capture_date_refresh(client, target_date)
        stage = "actual_download"
        root = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="actual-import-", dir=root) as directory:
            overrides = {
                "ARTIFACTS_DIR": directory,
                "KP_WORKFLOW_MODE": "csv", "KP_CSV_PLOT_ENABLED": "false",
                "KP_DOWNLOAD_LATEST_MONTH": "false", "KP_CSV_TARGET_MONTHS": target_date[:7],
            }
            _run([sys.executable, "kpnet_main.py"], overrides, timeout_seconds=240)
            stage = "actual_save"
            actuals_saved = None
            receipt.set({"status": "running", "stage": stage, "actuals_saved": None}, timeout=4, retry=None)
            pending_dates = save_date_actuals(client, target_date, Path(directory))
            actuals_saved = True
        stage = "display_publication"
        receipt.set({"status": "running", "stage": stage, "actuals_saved": True}, timeout=4, retry=None)
        publish_date_refresh(client, target_date, captured)
        status = "partial" if pending_dates else "succeeded"
        return 0
    finally:
        result = {"status": status, "stage": stage, "actuals_saved": actuals_saved,
                  "target_date": target_date, "pending_dates": pending_dates,
                  "completed_at": datetime.now(timezone.utc).isoformat()}
        receipt.set(result, timeout=4, retry=None)
        client.collection("dashboard_actual_import_runs").document("active").set(
            {"execution": execution, **result}, timeout=4, retry=None)


def save_date_actuals(client: Any, target_date: str, directory: Path) -> list[str]:
    cfg = PipelineConfig.from_env()
    now = datetime.now(timezone.utc).isoformat()
    paths = find_latest_kpnet_csv_paths(directory)
    if not paths:
        raise ValueError("no actual CSV was downloaded")
    day = date.fromisoformat(target_date)
    window = monitoring_sync_window(datetime.combine(day, time.min, tzinfo=ZoneInfo("Asia/Tokyo")), days=1)
    observed_until = datetime.now(ZoneInfo("Asia/Tokyo")).replace(tzinfo=None).isoformat()
    window = replace(window, end_ts=min(window.end_ts, observed_until))
    changes = source.sync_monitoring_csvs(client, csv_paths=paths, ingested_at=now, window=window)
    if not changes.materialization_dates:
        raise ValueError("requested date has no actual measurements")
    source.recalc_monitoring_daily_metrics(client, calendar_dates={target_date}, dashboard_affected_dates={target_date}, updated_at=now)
    month_start = day.replace(day=1).isoformat()
    cost_options = {name: getattr(cfg, name) for name in (
        "night8_day_start_hhmm", "night8_day_end_hhmm", "night8_day_tier1_upper_kwh", "night8_day_tier2_upper_kwh",
        "night8_day_rate_tier1_yen", "night8_day_rate_tier2_yen", "night8_day_rate_tier3_yen", "night8_night_rate_yen")}
    source.recalc_cost_daily_from(client, start_date=month_start, end_ts=window.end_ts, updated_at=now,
                                 day_rate_yen_per_kwh=cfg.day_rate_yen_per_kwh, tariff_mode=cfg.cost_tariff_mode,
                                 write_dates={target_date}, **cost_options)
    # A correction can invalidate later cumulative figures/night-window totals.
    # Report those dependencies; never regenerate an unrequested day implicitly.
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    return [(day + timedelta(days=offset)).isoformat() for offset in range(1, (today - day).days + 1)] if changes.changed_count else []


if __name__ == "__main__":
    raise SystemExit(main())
