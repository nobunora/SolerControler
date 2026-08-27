from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def _cloud_call(name: str, *args: Any, **kwargs: Any) -> Any:
    from app.runtime import cloud_job
    return getattr(cloud_job, name)(*args, **kwargs)


def _run_db_pipeline_slot(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_run_db_pipeline_slot", *args, **kwargs)
def _run_optional(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_run_optional", *args, **kwargs)
def _run_csv_with_retry(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_run_csv_with_retry", *args, **kwargs)
def _persist_previous_day_soc_feedback(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_persist_previous_day_soc_feedback", *args, **kwargs)
def _adjust03_target_date(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_adjust03_target_date", *args, **kwargs)
def _latest_kpnet_csv_paths(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_latest_kpnet_csv_paths", *args, **kwargs)
def _ensure_night_plan_available(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_ensure_night_plan_available", *args, **kwargs)
def _monitor_partial_forced_and_stop(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_monitor_partial_forced_and_stop", *args, **kwargs)
def _run_settings_profile_with_retry(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_run_settings_profile_with_retry", *args, **kwargs)
def _assert_manual_handoff_eligible(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_assert_manual_handoff_eligible", *args, **kwargs)
def _assert_day_transition_allowed() -> Any: return _cloud_call("_assert_day_transition_allowed")

# HISTORICAL_FAILURE_LOCK (1dd21ae, 2026-08-28 incident evidence): this is
# an opt-in override only.  If a deployment makes it true by default, 23:00
# skips standby and 03:00 skips the entire monitor/forced-charge lifecycle,
# yielding plan=100%, actual=0%, charge=0kWh and a 07:00 gate without the
# scheduled terminal state.  Keep the false fallback and do not merge this
# predicate into a permissive branch.  The deploy default plus a 23->03->07
# replay are regression-tested; a reversible settings round-trip alone cannot
# prove that scheduler routing reached this function.
def _manual_soc_operation_enabled() -> bool:
    return os.getenv("NIGHT_SOC_MANUAL_OPERATION", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _run_night_23() -> None:
    # 23:00 is only a mode-control guard. Forecast/data work is centralized in
    # the 03:00 controller, which still has enough time to reach 100% if needed.
    if _manual_soc_operation_enabled():
        print(
            "[cloud_job_runner] 23-settings skipped: NIGHT_SOC_MANUAL_OPERATION=true; "
            "battery settings remain under manual control.",
            flush=True,
        )
        return
    profile = os.getenv("NIGHT23_SETTINGS_PROFILE", "standby").strip() or "standby"
    _run_settings_profile_with_retry(
        profile=profile,
        dynamic_forced_profile=False,
        label=f"23-settings-{profile}",
    )


def _run_optional_04_exports_and_backups() -> None:
    _run_optional(
        [sys.executable, "sheets_export_main.py"],
        {
            "CLOUD_JOB_SLOT": "03",
        },
        label="sheets-export",
    )
    if os.getenv("DRIVE_BACKUP_FOLDER_ID", "").strip():
        _run_optional(
            [sys.executable, "scripts/backup_drive.py", "--mode", os.getenv("DRIVE_BACKUP_MODE", "data").strip() or "data"],
            {
                "CLOUD_JOB_SLOT": "03",
            },
            label="drive-backup",
        )
    else:
        print("[cloud_job_runner] drive-backup skipped: DRIVE_BACKUP_FOLDER_ID is empty", flush=True)


def _run_adjust_03(*, plan_refresh_only: bool = False) -> None:
    # 夜間コントローラ:
    # 1) 03:00にCSVを取得して現在SOCを把握
    # 2) 当日分の最新予報を03:00時点で再生成
    # 3) すぐ強制充電を開始し、目標到達または7時まで監視
    _run_csv_with_retry(label="03-initial-csv")
    artifacts_dir = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
    _persist_previous_day_soc_feedback(
        target_date=_adjust03_target_date(),
        csv_paths=_latest_kpnet_csv_paths(artifacts_dir),
    )
    plan_path = Path(os.getenv("KP_NIGHT_PLAN_PATH", "artifacts/night_charge_plan.json"))
    if not _ensure_night_plan_available(plan_path):
        raise RuntimeError(f"night charge plan not found: {plan_path}")
    _run_db_pipeline_slot(
        "03",
        include_csv=True,
        include_settings=False,
        extra_env={
            "DATA_DB_WRITE_ONLY_23": "false",
            "DATA_PREFER_NIGHT_PLAN_METRICS": "true",
        },
    )
    if plan_refresh_only:
        print("[cloud_job_runner] 03-plan refresh completed without device control", flush=True)
        return
    if _manual_soc_operation_enabled():
        # HISTORICAL_FAILURE_LOCK (1dd21ae, 2026-08-28 incident evidence):
        # this branch intentionally replaces the 03 monitor only for an
        # explicit manual operator.  Removing the automatic branch below, or
        # allowing the production default to reach this return, removes lease
        # acquisition, KP-NET forced-charge/read-back, and terminal state.  A
        # MANUAL_OPERATION record is not equivalent to a completed monitor.
        # Keep the postcondition: persistence success alone is insufficient.
        plan_meta = _cloud_call("_read_plan_meta", plan_path)
        persisted = _cloud_call(
            "_persist_night_soc_execution",
            plan_meta,
            "MANUAL_OPERATION",
            owner="manual",
            device_write_skipped=True,
        )
        if not persisted:
            raise RuntimeError(
                "03:00 manual operation hand-off could not be persisted; "
                "07:00 transition remains blocked"
            )
        # This postcondition intentionally has no DRY_RUN/control-mode bypass:
        # exact plan identity, owner, write-skipped marker, and freshness must
        # be readable before the 03:00 manual branch can report success.
        _assert_manual_handoff_eligible(plan_meta)
        _run_optional_04_exports_and_backups()
        print(
            "[cloud_job_runner] 03-monitor skipped: NIGHT_SOC_MANUAL_OPERATION=true; "
            "07:00 hand-off requires settings read-back.",
            flush=True,
        )
        return
    # HISTORICAL_FAILURE_LOCK (d1d7792, 1dd21ae, 2026-08-28 incident evidence):
    # scheduled 03:00 must call the single-owner monitor.  It acquires the
    # Firestore lease, performs forced-charge KP-NET read-back, writes terminal
    # state, and leaves 07:00 safely gated.  Do not replace it with a settings
    # round-trip or omit it after plan refresh: those only prove API mutation,
    # not scheduled routing or morning SOC.  The incident validation replays
    # 23->03->07 and fails release validation if this call is not reached.
    _monitor_partial_forced_and_stop(plan_path)
    _run_optional_04_exports_and_backups()


def _run_day_07() -> None:
    # 07:00 実行:
    # 日中運用向けにグリーンモード設定のみ登録
    _assert_day_transition_allowed()
    _run_settings_profile_with_retry(profile="green", dynamic_forced_profile=False, label="07-green")


