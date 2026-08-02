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
def _run_night_23() -> None:
    # 23:00 is only a mode-control guard. Forecast/data work is centralized in
    # the 03:00 controller, which still has enough time to reach 100% if needed.
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
    _monitor_partial_forced_and_stop(plan_path)
    _run_optional_04_exports_and_backups()


def _run_day_07() -> None:
    # 07:00 実行:
    # 日中運用向けにグリーンモード設定のみ登録
    _run_settings_profile_with_retry(profile="green", dynamic_forced_profile=False, label="07-green")


