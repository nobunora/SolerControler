"""Independent schedule-slot entry points."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _cloud_call(name: str, *args: Any, **kwargs: Any) -> Any:
    from app.runtime import cloud_job

    return getattr(cloud_job, name)(*args, **kwargs)


# HISTORICAL_FAILURE_LOCK (2026-08-29 user-authorized time ownership): do not
# add plan, CSV, forecast, Firestore, manual-mode, or profile-env branching to
# this slot.  23:00 must perform exactly one standby candidate/read-back write;
# adding a skip makes the physical battery retain green/forced mode overnight.
# Guarded by test_slot23_is_unconditional_standby_without_cross_slot_dependencies.
def _run_night_23() -> None:
    _cloud_call(
        "_run_settings_profile_with_retry",
        profile="standby",
        dynamic_forced_profile=False,
        label="23-standby",
    )


# HISTORICAL_FAILURE_LOCK (2026-08-29 user-authorized time ownership): 03:00 is
# standalone.  Do not add lease/owner/manual hand-off/day-gate writes or Drive/
# Sheets tail work.  Those cross-slot operations can run into 07:00 ownership
# and previously blocked green after a harmless 03 failure.  Guarded by
# test_slot03_has_no_cross_slot_or_export_tail_dependencies.
def _run_adjust_03(*, plan_refresh_only: bool = False) -> None:
    plan_path = Path(_cloud_call("_night_plan_path"))
    try:
        _cloud_call("_before_03_external_io")
        _cloud_call("_run_csv_with_retry", label="03-initial-csv")
        available = _cloud_call("_ensure_night_plan_available", plan_path)
    except Exception:
        available = plan_path.exists()
        if not available:
            _cloud_call("_run_03_prep_fail_safe_standby")
            return
    if not available:
        raise RuntimeError(f"night charge plan not found: {plan_path}")
    if plan_refresh_only:
        return
    _cloud_call("_monitor_partial_forced_and_stop", plan_path)


# HISTORICAL_FAILURE_LOCK (2026-08-29 user-authorized time ownership): do not
# add Firestore, plan, lease, owner, SOC, manual-mode, or terminal-state checks
# before this call.  At 07:00 this job owns the device and must issue exactly
# one green candidate/read-back write regardless of every 03 outcome.  A gate
# recreates the observed SOC=100 but non-green physical state.  Guarded by
# test_slot07_is_unconditional_green_without_cross_slot_dependencies.
def _run_day_07() -> None:
    _cloud_call(
        "_run_settings_profile_with_retry",
        profile="green",
        dynamic_forced_profile=False,
        label="07-green",
    )
