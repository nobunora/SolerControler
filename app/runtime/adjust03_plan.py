from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from app.forced_charge import MonitorDevicePort, MonitorStatusPort
def _cloud_call(name: str, *args: Any, **kwargs: Any) -> Any:
    from app.runtime import cloud_job
    return getattr(cloud_job, name)(*args, **kwargs)


def _run(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_run", *args, **kwargs)
def _run_with_retry(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_run_with_retry", *args, **kwargs)
def _run_settings_profile_with_retry(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_run_settings_profile_with_retry", *args, **kwargs)
def _persist_night_plan_to_firestore(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_persist_night_plan_to_firestore", *args, **kwargs)
def _restore_night_plan_from_firestore(*args: Any, **kwargs: Any) -> Any: return _cloud_call("_restore_night_plan_from_firestore", *args, **kwargs)
def _ensure_night_plan_available(plan_path: Path) -> bool:
    target_date = str(_cloud_call("_adjust03_target_date"))
    regenerate = os.getenv("ADJUST03_REGENERATE_PLAN", "true").strip().lower() in {"1", "true", "yes", "on"}
    if regenerate:
        print(
            f"[cloud_job_runner] 03-plan regenerating target_date={target_date} path={plan_path}",
            flush=True,
        )
        try:
            _run_with_retry(
                [sys.executable, "energy_model_main.py"],
                {"FORECAST_DATE_OVERRIDE": target_date},
                label="03-regenerate-night-plan",
                attempts_env="ADJUST03_PLAN_RETRY_ATTEMPTS",
                delay_env="ADJUST03_PLAN_RETRY_DELAY_SECONDS",
                default_attempts=2,
                default_delay_seconds=30.0,
            )
            _persist_night_plan_to_firestore(plan_path, source="adjust03-regenerated")
            if plan_path.exists() and _night_plan_file_date(plan_path) == target_date:
                return True
        except Exception as exc:
            print(f"[cloud_job_runner] 03-plan regeneration failed; trying fallback plan: {exc}", flush=True)

    if plan_path.exists() and _night_plan_file_date(plan_path) == target_date:
        return True

    if _restore_night_plan_from_firestore(plan_path, target_date=target_date):
        return True

    print(
        f"[cloud_job_runner] 03-plan missing; regenerating target_date={target_date} path={plan_path}",
        flush=True,
    )
    _run_with_retry(
        [sys.executable, "energy_model_main.py"],
        {"FORECAST_DATE_OVERRIDE": target_date},
        label="03-regenerate-night-plan",
        attempts_env="ADJUST03_PLAN_RETRY_ATTEMPTS",
        delay_env="ADJUST03_PLAN_RETRY_DELAY_SECONDS",
        default_attempts=2,
        default_delay_seconds=30.0,
    )
    _persist_night_plan_to_firestore(plan_path, source="adjust03-regenerated")
    return plan_path.exists() and _night_plan_file_date(plan_path) == target_date


def _night_plan_file_date(plan_path: Path) -> str:
    try:
        obj = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    forecast = obj.get("forecast", {})
    if not isinstance(forecast, dict):
        return ""
    return str(forecast.get("date", "")).strip()


def _run_db_pipeline_slot(
    slot: str,
    *,
    include_csv: bool = True,
    include_settings: bool = True,
    extra_env: dict[str, str] | None = None,
) -> None:
    env = {
        "CLOUD_JOB_SLOT": slot,
        "DATA_PIPELINE_INCLUDE_CSV": "true" if include_csv else "false",
        "DATA_PIPELINE_INCLUDE_SETTINGS": "true" if include_settings else "false",
    }
    if extra_env:
        env.update(extra_env)
    _run(
        [sys.executable, "db_pipeline_main.py"],
        env,
    )


def _run_03_settings_profile_with_db(
    *,
    profile: str,
    dynamic_forced_profile: bool,
    label: str,
) -> None:
    _run_settings_profile_with_retry(
        profile=profile,
        dynamic_forced_profile=dynamic_forced_profile,
        label=label,
    )
    _cloud_call(
        "_run_db_pipeline_slot",
        "03",
        include_csv=False,
        include_settings=True,
        extra_env={
            "DATA_DB_WRITE_ONLY_23": "false",
            "DATA_PREFER_NIGHT_PLAN_METRICS": "true",
        },
    )


def _attempt_03_fail_safe_standby(
    plan_meta: dict[str, Any],
    *,
    label: str,
    reason: str,
    device_port: MonitorDevicePort | None = None,
    status_port: MonitorStatusPort | None = None,
) -> None:
    device = device_port or _cloud_call("_RunnerMonitorDevicePort")
    status = status_port or _cloud_call("_RunnerMonitorStatusPort")
    try:
        device.apply_profile(
            profile="standby",
            dynamic_forced_profile=False,
            label=label,
        )
    finally:
        status.persist_stop_reason(plan_meta, reason)


