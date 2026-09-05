"""Independent 23:00, 03:00, and 07:00 Cloud Job control paths."""
# mypy: disable-error-code=no-untyped-call
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.forced_charge import MonitorClock, MonitorDevicePort
from app.kpnet.monitoring_history import find_latest_kpnet_csv_paths
from app.kpnet.settings_roundtrip import run_settings_roundtrip
from app.kpnet.workflow import run_kpnet_mode_only_profile
from app.runtime.command_adapter import _env_float, _env_int, _run, _run_operation_with_retry
from app.runtime.forced_charge_monitor import ForcedChargeCompletionEstimator, estimate_forced_charge_rate_percent_per_hour
from app.runtime.night_soc_time_contract import SOC_OPERATION_MAX_SECONDS, may_start_final_standby, must_stop_forced_monitoring, may_start_03_io, seconds_until_control_cutoff, seconds_until_forced_monitor_cutoff
from app.runtime.soc_reading import SocReading, latest_csv_soc_reading, latest_realtime_soc_percent, read_soc_with_fallback
from app.settings.forced_charge import ForcedChargeSettings


class _SystemMonitorClock:
    def monotonic_seconds(self) -> float: return time.monotonic()
    def now(self, timezone: ZoneInfo) -> datetime: return datetime.now(timezone)
    def sleep(self, seconds: int) -> None: time.sleep(seconds)


def _tokyo_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Tokyo"))


# HISTORICAL_FAILURE_LOCK (2026-08-29 user-authorized time ownership): do not
# relax this 06:55 check or start a new external 03 operation after it.  A
# delayed 03 write can otherwise overwrite the 07:00 green owner. Guarded by
# test_cloud_job_runner.py::test_03_hard_fence_controls_all_device_io.
def _before_03_external_io(*, now: datetime | None = None) -> None:
    if not may_start_03_io(now or _tokyo_now()):
        raise RuntimeError("03 ownership ended at 06:55 JST; no external I/O is allowed")


def _night_plan_path() -> Path:
    return Path(os.getenv("KP_NIGHT_PLAN_PATH", "artifacts/night_charge_plan.json"))


def _latest_kpnet_csv_paths(artifacts_dir: Path) -> list[Path]:
    return find_latest_kpnet_csv_paths(artifacts_dir)


def _run_csv_with_retry(*, label: str) -> None:
    _before_03_external_io()
    operation_deadline = time.monotonic() + seconds_until_control_cutoff(_tokyo_now())
    def operation() -> None:
        _before_03_external_io()
        _run([sys.executable, "kpnet_main.py"], {"KP_WORKFLOW_MODE": "csv"}, timeout_seconds=min(240, seconds_until_control_cutoff(_tokyo_now())), deadline_monotonic=operation_deadline)
    _run_operation_with_retry(operation, label=label, deadline_monotonic=operation_deadline)


def _ensure_night_plan_available(plan_path: Path) -> bool:
    _before_03_external_io()
    # Regenerate locally only.  03 has no Firestore fallback/persistence path.
    if plan_path.exists() and os.getenv("ADJUST03_REGENERATE_PLAN", "true").lower() not in {"1", "true", "yes", "on"}:
        return True
    started = time.monotonic()
    deadline = started + seconds_until_control_cutoff(_tokyo_now())
    try:
        _run([sys.executable, "energy_model_main.py"], {"FORECAST_DATE_OVERRIDE": _tokyo_now().date().isoformat()}, timeout_seconds=min(240, seconds_until_control_cutoff(_tokyo_now())), deadline_monotonic=deadline)
    except Exception as error:
        outcome = "timeout" if isinstance(error, TimeoutError) else "error"
        print(
            "[cloud_job_runner] 03-prep "
            + json.dumps(
                {
                    "stage": "plan_generation",
                    "outcome": outcome,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "exception_type": type(error).__name__,
                    "usable_plan_exists": plan_path.exists(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            flush=True,
        )
        raise
    available = plan_path.exists()
    print(
        "[cloud_job_runner] 03-prep "
        + json.dumps(
            {
                "stage": "plan_generation",
                "outcome": "success" if available else "no_plan",
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "exception_type": None,
                "usable_plan_exists": available,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    return available


def _read_plan_meta(path: Path) -> dict[str, Any]:
    raw_bytes = path.read_bytes()
    raw = json.loads(raw_bytes)
    result = raw.get("result", {})
    from app.energy_plan.night_plan import build_night_plan_provenance
    from app.runtime.night_soc_controller import make_plan_snapshot
    try:
        snapshot = make_plan_snapshot(raw)
    except (TypeError, ValueError):
        snapshot = None

    return {
        "plan_date": None if snapshot is None else snapshot.plan_date,
        "plan_revision": None if snapshot is None else snapshot.revision,
        "plan_id": None if snapshot is None else snapshot.plan_id,
        "plan_hash": None if snapshot is None else snapshot.content_hash,
        "target_soc_7_percent": float(result["target_soc_7_percent"]),
        "required_night_charge_kwh": float(result.get("required_night_charge_kwh", 0.0)),
        "effective_capacity_kwh": float(result.get("effective_capacity_kwh", 0.0)),
        "provenance": build_night_plan_provenance(
            raw, plan_sha256=hashlib.sha256(raw_bytes).hexdigest()
        ),
    }


class _RunnerMonitorDevicePort:
    """KP-NET port: exactly one mode-only write/read-back per invocation."""
    def read_soc(self, csv_paths: list[Path]) -> SocReading:
        _before_03_external_io()
        now = _tokyo_now()
        if must_stop_forced_monitoring(now):
            return SocReading(None, "unavailable", "03 SOC cutoff reached", None)
        operation_start = time.monotonic()
        monitor_cutoff = operation_start + seconds_until_forced_monitor_cutoff(now)
        deadline = min(monitor_cutoff, operation_start + SOC_OPERATION_MAX_SECONDS)
        return read_soc_with_fallback(
            csv_paths,
            latest_realtime=lambda: latest_realtime_soc_percent(deadline_monotonic=deadline),
            latest_csv=latest_csv_soc_reading,
            env_int=lambda name, default: _env_int(name, default),
            env_float=lambda name, default: _env_float(name, default),
            deadline_monotonic=deadline,
            allow_realtime=deadline - operation_start >= SOC_OPERATION_MAX_SECONDS,
        )

    def apply_profile(self, *, profile: str, dynamic_forced_profile: bool, label: str) -> None:
        _before_03_external_io()
        if profile not in {"forced", "standby"}:
            raise ValueError(f"unsupported 03 profile: {profile}")
        if dynamic_forced_profile != (profile == "forced"):
            raise ValueError("03 profile/dynamic flag mismatch")
        # No retry and no DB/Firestore pipeline: a mismatch must remain visible.
        deadline = time.monotonic() + seconds_until_control_cutoff(_tokyo_now())
        if run_kpnet_mode_only_profile(profile=profile, deadline_monotonic=deadline) != 0:
            raise RuntimeError(f"KP-NET mode-only read-back failed for {label}")


def _emit_03_terminal_audit(
    plan: dict[str, Any], *, stop_reason: str, latest: SocReading | None,
    standby_attempted: bool, standby_outcome: str,
) -> None:
    """Emit one secret-free terminal record; logging must never affect control."""
    try:
        reading = latest
        source = None if reading is None else reading.source
        if source not in {"realtime", "csv", "fake", "unavailable", "unknown"}:
            source = "unknown"
        payload = {
            "message": "03-terminal-audit",
            "terminal_event_type": "03_monitor_terminal",
            "plan_date": plan.get("plan_date"), "plan_id": plan.get("plan_id"),
            "plan_revision": plan.get("plan_revision"), "plan_hash": plan.get("plan_hash"),
            "target_soc_percent": plan.get("target_soc_7_percent"),
            "required_night_charge_kwh": plan.get("required_night_charge_kwh"),
            "stop_reason": stop_reason,
            "last_soc_percent": None if reading is None else reading.value_percent,
            "last_soc_source": source,
            "last_soc_observed_at": None if reading is None or reading.observed_at is None else reading.observed_at.isoformat(),
            "standby_attempted": standby_attempted, "standby_outcome": standby_outcome,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), flush=True)
    except Exception:
        return


# HISTORICAL_FAILURE_LOCK (2026-08-29 user-authorized time ownership): this is
# the sole public 03 controller.  Do not add forced reapply, lease, terminal
# persistence, Firestore, DB, or manual hand-off.  The sequence is forced=3,
# immediate SOC check, then standby=5 on target/failure before 06:55; a forced
# read-back mismatch gets one standby attempt and preserves the original error.
# Guarded by test_03_mismatch_is_not_reapplied_and_does_not_gate_07.
def _monitor_partial_forced_and_stop(plan_path: Path, *, clock: MonitorClock | None = None, device_port: MonitorDevicePort | None = None, status_port: object | None = None) -> None:
    del status_port
    clock = clock or _SystemMonitorClock(); device = device_port or _RunnerMonitorDevicePort()
    zone = ZoneInfo("Asia/Tokyo")
    now = lambda: clock.now(zone)
    if not may_start_03_io(now()) or not plan_path.exists(): return
    plan = _read_plan_meta(plan_path); target = max(0.0, min(100.0, plan["target_soc_7_percent"])); settings = ForcedChargeSettings.from_env()
    print(
        "[cloud_job_runner] 03-plan-provenance "
        + json.dumps(plan["provenance"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        flush=True,
    )
    print(f"[cloud_job_runner] 03-monitor contract target={target:.2f}% exact_target_stop=true configured_stop_margin={settings.stop_soc_margin_percent:.2f}% applied_stop_margin=0.00%", flush=True)
    paths = _latest_kpnet_csv_paths(Path(os.getenv("ARTIFACTS_DIR", "artifacts")))
    standby_attempted = False
    standby_outcome = "not_attempted"
    failure_phase = "initial_soc"
    def standby(label: str) -> None:
        nonlocal standby_attempted, standby_outcome
        if not may_start_final_standby(now()): return
        standby_attempted = True
        try:
            device.apply_profile(profile="standby", dynamic_forced_profile=False, label=label)
            standby_outcome = "success"
        except Exception:
            standby_outcome = "failed"
            raise
    def log_soc(reading: SocReading) -> None:
        latest = reading.value_percent
        action = "soc_unavailable" if latest is None else "target_reached" if latest >= target else "continue"
        value = "none" if latest is None else f"{latest:.2f}%"
        observed_at = "none" if reading.observed_at is None else reading.observed_at.isoformat()
        print(f"[cloud_job_runner] 03-monitor soc value={value} source={reading.source} observed_at={observed_at} target={target:.2f}% action={action}", flush=True)
    try:
        initial = device.read_soc(paths); latest = initial.value_percent
        log_soc(initial)
        # Literal contract: every configured target begins the forced/readback path.
        # No new KP settings write may start after 06:50: reserve the final
        # five minutes for an already-running operation to terminate cleanly.
        if not may_start_03_io(now()) or must_stop_forced_monitoring(now()):
            _emit_03_terminal_audit(plan, stop_reason="monitor_cutoff", latest=initial, standby_attempted=standby_attempted, standby_outcome=standby_outcome)
            return
        failure_phase = "forced"
        device.apply_profile(profile="forced", dynamic_forced_profile=True, label="03-forced-start")
        failure_phase = "monitor"
        if latest is None:
            print(f"[cloud_job_runner] 03-monitor stop reason=soc_unavailable target={target:.2f}%", flush=True)
            standby("03-immediate-standby"); _emit_03_terminal_audit(plan, stop_reason="soc_unavailable", latest=initial, standby_attempted=standby_attempted, standby_outcome=standby_outcome); return
        if latest >= target:
            print(f"[cloud_job_runner] 03-monitor stop reason=target_reached latest={latest:.2f}% target={target:.2f}%", flush=True)
            standby("03-immediate-standby"); _emit_03_terminal_audit(plan, stop_reason="target_reached", latest=initial, standby_attempted=standby_attempted, standby_outcome=standby_outcome); return
        if must_stop_forced_monitoring(now()):
            standby("03-immediate-standby")
            _emit_03_terminal_audit(plan, stop_reason="monitor_cutoff", latest=initial, standby_attempted=standby_attempted, standby_outcome=standby_outcome)
            return
    except Exception as error:
        try: standby("03-forced-error-standby")
        except Exception: print("[cloud_job_runner] 03 standby failure", flush=True)
        if failure_phase == "forced":
            stop_reason = "forced_failure"
        elif standby_outcome == "failed":
            stop_reason = "standby_failure"
        else:
            stop_reason = "monitor_failure"
        _emit_03_terminal_audit(plan, stop_reason=stop_reason, latest=initial if "initial" in locals() else None, standby_attempted=standby_attempted, standby_outcome=standby_outcome)
        raise error
    # Empirical 14-day CSV trend/EWMA selects ETA; env fallback is used only by
    # that estimator when no valid charged interval exists.
    latest_reading = initial
    try:
        rate_info = estimate_forced_charge_rate_percent_per_hour(paths)
        estimator = ForcedChargeCompletionEstimator(rate_percent_per_hour=float(rate_info["percent_per_hour"]), confirm_before_minutes=settings.completion_confirm_before_minutes)
        while may_start_03_io(now()) and not must_stop_forced_monitoring(now()):
            reading = device.read_soc(paths); latest = reading.value_percent
            latest_reading = reading
            log_soc(reading)
            if latest is None:
                print(f"[cloud_job_runner] 03-monitor stop reason=soc_unavailable target={target:.2f}%", flush=True)
                standby("03-target-reached-standby"); _emit_03_terminal_audit(plan, stop_reason="soc_unavailable", latest=reading, standby_attempted=standby_attempted, standby_outcome=standby_outcome); return
            if latest >= target:
                print(f"[cloud_job_runner] 03-monitor stop reason=target_reached latest={latest:.2f}% target={target:.2f}%", flush=True)
                standby("03-target-reached-standby"); _emit_03_terminal_audit(plan, stop_reason="target_reached", latest=reading, standby_attempted=standby_attempted, standby_outcome=standby_outcome); return
            delay = estimator.next_check_seconds(target_soc=target, latest_soc=latest, fallback_poll_seconds=settings.poll_interval_seconds, cutoff_seconds=seconds_until_control_cutoff(now()))
            if delay <= 0: break
            clock.sleep(delay)
        print(f"[cloud_job_runner] 03-monitor stop reason=monitor_cutoff target={target:.2f}%", flush=True)
        standby("03-monitor-cutoff-standby")
        _emit_03_terminal_audit(plan, stop_reason="monitor_cutoff", latest=latest_reading, standby_attempted=standby_attempted, standby_outcome=standby_outcome)
    except Exception:
        _emit_03_terminal_audit(plan, stop_reason="standby_failure" if standby_outcome == "failed" else "monitor_failure", latest=latest_reading, standby_attempted=standby_attempted, standby_outcome=standby_outcome)
        raise


def _run_settings_profile_with_retry(*, profile: str, dynamic_forced_profile: bool, label: str | None = None) -> None:
    # 23/07 use mode-only flow, exactly one write; retain name for slot adaptor.
    if run_kpnet_mode_only_profile(profile=profile) != 0:
        raise RuntimeError(f"KP-NET mode-only read-back failed for {label or profile}")


def _run_03_prep_fail_safe_standby() -> bool:
    """One bounded local standby only while its full 300-second window remains."""
    if not may_start_final_standby(_tokyo_now()):
        return False
    _RunnerMonitorDevicePort().apply_profile(profile="standby", dynamic_forced_profile=False, label="03-prep-failed-standby")
    return True


def main() -> int:
    from app.runtime.slot_orchestration import _run_adjust_03, _run_day_07, _run_night_23
    slot = os.getenv("CLOUD_JOB_SLOT", "").strip().lower()
    if slot in {"23", "night", "night23"}: _run_night_23()
    elif slot in {"3", "03", "adjust", "adjust03"}: _run_adjust_03(plan_refresh_only="--plan-refresh-only" in sys.argv[1:])
    elif slot in {"7", "07", "day", "day07"}: _run_day_07()
    elif slot in {"settings-roundtrip", "settings_roundtrip"}:
        target_soc = _env_float("SETTINGS_ROUNDTRIP_TARGET_SOC", 50.0)
        run_settings_roundtrip(target_soc_percent=target_soc)
    else: raise RuntimeError("CLOUD_JOB_SLOT must be 23, 03, 07, or settings-roundtrip")
    return 0


if __name__ == "__main__": raise SystemExit(main())
