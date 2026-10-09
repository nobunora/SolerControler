"""Control owner: consume a calculated plan and reuse the protected device core."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.runtime import cloud_job
from app.runtime.night_soc_time_contract import seconds_until_control_cutoff
from app.runtime.kpnet_unknown_guard import KpNetUnknownTaskTerminal, install_unknown_write_guard
from app.kpnet.settings_roundtrip import run_settings_roundtrip
from app.separated_runtime.plan import PublishedPlan
from app.separated_runtime.plan_store import FirestorePlanStore


def _validate_current_plan(plan: PublishedPlan, now: datetime, *, require_evidence: bool) -> PublishedPlan:
    plan = PublishedPlan.from_document(plan.to_document(), target_date=now.date().isoformat())
    issued = datetime.fromisoformat(json.loads(plan.raw_json)["generated_at"].replace("Z", "+00:00"))
    if issued > now + timedelta(minutes=2) or now - issued > timedelta(hours=24):
        raise ValueError("control plan generation time is stale or in the future")
    if require_evidence and (not plan.inputs_verified or not plan.producer_source_revision):
        raise ValueError("control plan has no successful calculation evidence")
    return plan


def run_slot(slot: str, *, store: Any = None, clock: Any = None, device_port: Any = None) -> None:
    # HISTORICAL_FAILURE_LOCK: 23/07 must not acquire a plan, database, lease,
    # owner, or dependency on another slot. Keep the physical mode-only core.
    if slot == "23":
        cloud_job._run_settings_profile_with_retry(profile="standby", dynamic_forced_profile=False, label="23-standby")
        return
    if slot == "07":
        cloud_job._run_settings_profile_with_retry(profile="green", dynamic_forced_profile=False, label="07-green")
        return
    if slot != "03":
        raise ValueError("control slot must be 23, 03, or 07")
    now = cloud_job._tokyo_now() if clock is None else clock.now(ZoneInfo("Asia/Tokyo"))
    cloud_job._before_03_external_io(now=now)
    path = cloud_job._night_plan_path()
    try:
        if store is None and os.getenv("CONTROL_PLAN_FILE"):
            if device_port is None and os.getenv("DRY_RUN", "false").lower() not in {"true", "1", "yes"}:
                raise ValueError("local fixture plans require dry run")
            document = json.loads(Path(os.environ["CONTROL_PLAN_FILE"]).read_text(encoding="utf-8"))
            plan = PublishedPlan.from_document(document, target_date=now.date().isoformat())
        else:
            if seconds_until_control_cutoff(now) < 90:
                raise RuntimeError("not enough 03 time budget to acquire a plan")
            reader = store if store is not None else FirestorePlanStore()
            plan = reader.fetch(now.date().isoformat(), timeout_seconds=min(60, seconds_until_control_cutoff(now)))
        after_read = cloud_job._tokyo_now() if clock is None else clock.now(ZoneInfo("Asia/Tokyo"))
        cloud_job._before_03_external_io(now=after_read)
        plan = _validate_current_plan(plan, after_read, require_evidence=device_port is None)
        plan.restore(path)
    except Exception:
        # A preparation failure is retryable, never a successful no-op or an
        # excuse to consume yesterday's plan. The legacy safe standby is retained.
        cloud_job._run_03_prep_fail_safe_standby()
        raise
    cloud_job._monitor_partial_forced_and_stop(path, clock=clock, device_port=device_port,
                                             charge_rate_info=plan.charge_rate_info)


def main() -> int:
    install_unknown_write_guard()
    slot = os.getenv("CLOUD_JOB_SLOT", "").strip()
    try:
        if slot == "settings-roundtrip":
            now = cloud_job._tokyo_now()
            plan = _validate_current_plan(FirestorePlanStore().fetch(now.date().isoformat()), now,
                                          require_evidence=True)
            # Verify the production SOC path before any device mutation, and again
            # after the mandatory round-trip has restored the original settings.
            from scripts.kpnet_soc_api_probe import run_probe
            soc_before = run_probe()
            evidence = run_settings_roundtrip()
            soc_after = run_probe()
            passed = (evidence.get("status") == "passed" and evidence.get("forced_proof") == "passed"
                      and evidence.get("green_proof") == "passed" and evidence.get("restore_verified") is True)
            summary = {"message": "postdeploy-live-probe", "csv": "passed", "plan": "passed",
                       "calculation_evidence": "published_canonical_workflow",
                       "soc_api": "passed", "soc_api_before": soc_before, "soc_api_after": soc_after,
                       "producer_source_revision": plan.producer_source_revision,
                       "settings_roundtrip": "passed" if passed else "failed", "settings_roundtrip_evidence": evidence,
                       "roundtrip_forced_proof": evidence.get("forced_proof"),
                       "roundtrip_green_proof": evidence.get("green_proof"),
                       "roundtrip_restore_verified": evidence.get("restore_verified"),
                       "status": "passed" if passed else "failed"}
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
            if summary["status"] != "passed":
                return 1
        else:
            if slot == "03":
                cloud_job._wait_for_03_platform_retry()
            run_slot(slot)
    except KpNetUnknownTaskTerminal:
        from cloud_job_runner import _unknown_terminal_exit_code
        code = _unknown_terminal_exit_code()
        print(json.dumps({"message": "kpnet-unknown-task-terminal", "classification": "unknown",
                          "cloud_run_retry_suppressed": code == 0, "exit_code": code}), flush=True)
        return code
    return 0
