from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, TypeVar, cast
from zoneinfo import ZoneInfo

from app.forced_charge import (
    ChargeMonitorProgress,
    ChargeObservation,
    ChargePolicy,
    ChargeReapplyPolicy,
    ChargeDemand,
    ChargeState,
    ChargeTransition,
    MonitorClock,
    MonitorDevicePort,
    MonitorStatusPort,
    decide_transition,
    requires_forced_charge,
)
from app.kpnet.settings_roundtrip import run_settings_roundtrip
from app.runtime import plan_persistence
from app.runtime import soc_reading
from app.runtime import forced_charge_monitor
from app.runtime import night_soc_controller
from app.runtime.forced_charge_monitor import ForcedChargeCompletionEstimator
from app.runtime.soc_reading import SocReading
from app.settings.forced_charge import ForcedChargeSettings
from app.kpnet.monitoring_history import find_latest_kpnet_csv_paths
from app.runtime.schedule import (
    _adjust03_target_date as _adjust03_target_date,
    _hhmm_after_delay,
    _seconds_until_cutoff,
)
from app.runtime.command_adapter import (
    _env_float,
    _env_int,
    _mask_env_updates as _mask_env_updates,
    _run,
    _run_operation_with_retry,
    _run_optional as _run_optional,  # noqa: F401
)
from app.runtime.adjust03_plan import (
    _attempt_03_fail_safe_standby,
    _ensure_night_plan_available,  # noqa: F401
    _run_db_pipeline_slot,  # noqa: F401
    _run_03_settings_profile_with_db,
)
from app.runtime.slot_orchestration import (
    _run_adjust_03,
    _run_day_07,
    _run_night_23,
    _run_optional_04_exports_and_backups as _run_optional_04_exports_and_backups,  # noqa: F401
)


_SECRET_KEYWORDS = ("password", "passwd", "secret", "token", "key")
_T = TypeVar("_T")


class _SystemMonitorClock:
    def monotonic_seconds(self) -> float:
        return time.time()

    def now(self, timezone: ZoneInfo) -> datetime:
        return datetime.now(timezone)

    def sleep(self, seconds: int) -> None:
        time.sleep(seconds)


class _RunnerMonitorDevicePort:
    def __init__(self) -> None:
        self.plan_meta: dict[str, Any] | None = None

    def read_soc(self, csv_paths: list[Path]) -> SocReading:
        return _read_soc_with_fallback(csv_paths)

    def apply_profile(self, *, profile: str, dynamic_forced_profile: bool, label: str) -> None:
        # HISTORICAL_FAILURE_LOCK (d1d7792): readback_match acknowledges the
        # settings write only; it is not evidence that forced charging started.
        _run_03_settings_profile_with_db(
            profile=profile,
            dynamic_forced_profile=dynamic_forced_profile,
            label=label,
        )
        if self.plan_meta is not None:
            values: dict[str, Any] = {
                "readback_match": True,
                "writer": label,
                "competing_writes": 0,
            }
            if profile == "standby":
                values["standby_confirmed_at_utc"] = (
                    datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z")
                )
            _persist_night_soc_execution(self.plan_meta, "SETTINGS_ACKED", **values)


class _RunnerMonitorStatusPort:
    def persist_stop_reason(
        self,
        plan_meta: dict[str, Any],
        reason: str,
        *,
        soc_reading: SocReading | None = None,
    ) -> bool:
        if soc_reading is None:
            return _persist_03_monitor_stop_reason(plan_meta, reason)
        return _persist_03_monitor_stop_reason(plan_meta, reason, soc_reading=soc_reading)

    def persist_schedule(self, **values: Any) -> bool:
        return _persist_03_monitor_schedule_to_firestore(**values)

    def persist_no_charge(self, **values: Any) -> bool:
        return _persist_03_no_charge_decision_to_firestore(**values)


def _to_float_or_none(value: object) -> float | None:
    try:
        result = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _read_plan_meta(plan_path: Path) -> dict[str, float | str | None]:
    obj = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"night plan root must be an object: {plan_path}")
    forecast = obj.get("forecast", {})
    result = obj.get("result", {})
    inputs = obj.get("inputs", {})
    plan_quality = obj.get("plan_quality", {})
    if not isinstance(forecast, dict):
        raise RuntimeError(f"night plan forecast must be an object: {plan_path}")
    if not isinstance(result, dict):
        raise RuntimeError(f"night plan result must be an object: {plan_path}")
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, dict):
        raise RuntimeError(f"night plan inputs must be an object: {plan_path}")
    if isinstance(plan_quality, dict) and plan_quality.get("should_apply") is False:
        raise RuntimeError(f"night plan is not safe to apply: plan_quality={plan_quality}")
    forecast_date = str(forecast.get("date", "")).strip()
    if not forecast_date:
        raise RuntimeError(f"night plan forecast.date is missing: {plan_path}")
    target_soc = _required_plan_float(
        result,
        key="target_soc_7_percent",
        min_value=0.0,
        max_value=100.0,
        plan_path=plan_path,
    )
    required_kwh = _required_plan_float(
        result,
        key="required_night_charge_kwh",
        min_value=0.0,
        plan_path=plan_path,
    )
    snapshot = night_soc_controller.make_plan_snapshot(obj)
    return {
        "date": forecast_date,
        "sun_hours": _to_float_or_none(forecast.get("sun_hours", 0.0)) or 0.0,
        "temp_c": _to_float_or_none(forecast.get("temp_c", 0.0)) or 0.0,
        "target_soc_7_percent": target_soc,
        "required_night_charge_kwh": required_kwh,
        "soc_now_percent": _to_float_or_none(inputs.get("soc_now_percent")),
        "effective_capacity_kwh": _to_float_or_none(result.get("effective_capacity_kwh")),
        "plan_id": snapshot.plan_id,
        "plan_revision": snapshot.revision,
        "plan_hash": snapshot.content_hash,
        "generated_at_utc": snapshot.generated_at_utc,
    }


def _required_plan_float(
    source: dict[str, Any],
    *,
    key: str,
    plan_path: Path,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    if key not in source:
        raise RuntimeError(f"night plan result.{key} is missing: {plan_path}")
    value = _to_float_or_none(source.get(key))
    if value is None:
        raise RuntimeError(f"night plan result.{key} is not a finite number: {plan_path}")
    if min_value is not None and value < min_value:
        raise RuntimeError(f"night plan result.{key} is below {min_value}: {value}")
    if max_value is not None and value > max_value:
        raise RuntimeError(f"night plan result.{key} is above {max_value}: {value}")
    return value


def _open_firestore_for_plan() -> Any | None:
    backend = os.getenv("DATA_BACKEND", "").strip().lower()
    project_id = os.getenv("FIRESTORE_PROJECT_ID", "").strip()
    if backend != "firestore" and not project_id:
        return None
    try:
        from google.cloud import firestore
    except Exception as exc:
        print(f"[cloud_job_runner] Firestore unavailable for plan persistence: {exc}", flush=True)
        return None
    database_id = os.getenv("FIRESTORE_DATABASE_ID", "").strip() or "(default)"
    if project_id:
        return firestore.Client(project=project_id, database=database_id)
    return firestore.Client(database=database_id)


def _persist_night_plan_to_firestore(plan_path: Path, *, source: str) -> bool:
    return plan_persistence.persist_night_plan_to_firestore(
        plan_path, source=source, open_firestore=_open_firestore_for_plan
    )


def _restore_night_plan_from_firestore(plan_path: Path, *, target_date: str) -> bool:
    return plan_persistence.restore_night_plan_from_firestore(
        plan_path, target_date=target_date, open_firestore=_open_firestore_for_plan
    )


def _persist_previous_day_soc_feedback(*, target_date: str, csv_paths: list[Path]) -> bool:
    return plan_persistence.persist_previous_day_soc_feedback(
        target_date=target_date, csv_paths=csv_paths, open_firestore=_open_firestore_for_plan
    )




def _persist_03_monitor_schedule_to_firestore(
    *,
    plan_meta: dict[str, float | str | None],
    charge_start_time: str,
    charge_end_time: str,
    target_soc: float,
    latest_soc: float | None,
    required_kwh: float,
    estimated_charge_minutes: int,
    default_power_kw: float,
    charge_rate_info: Mapping[str, float | int | str | None] | None = None,
    soc_source: str = "unknown",
    soc_error: str | None = None,
    monitor_start_reason: str = "soc_available",
) -> bool:
    return plan_persistence.persist_03_monitor_schedule_to_firestore(
        plan_meta=plan_meta,
        charge_start_time=charge_start_time,
        charge_end_time=charge_end_time,
        target_soc=target_soc,
        latest_soc=latest_soc,
        required_kwh=required_kwh,
        estimated_charge_minutes=estimated_charge_minutes,
        default_power_kw=default_power_kw,
        charge_rate_info=charge_rate_info,
        soc_source=soc_source,
        soc_error=soc_error,
        monitor_start_reason=monitor_start_reason,
        open_firestore=_open_firestore_for_plan,
    )


def _persist_03_no_charge_decision_to_firestore(
    *,
    plan_meta: dict[str, float | str | None],
    target_soc: float,
    latest_soc: float | None,
    required_kwh: float,
    soc_source: str = "unknown",
) -> bool:
    return plan_persistence.persist_03_no_charge_decision_to_firestore(
        plan_meta=plan_meta,
        target_soc=target_soc,
        latest_soc=latest_soc,
        required_kwh=required_kwh,
        soc_source=soc_source,
        open_firestore=_open_firestore_for_plan,
    )


def _persist_03_monitor_stop_reason(
    plan_meta: dict[str, float | str | None],
    reason: str,
    *,
    soc_reading: SocReading | None = None,
) -> bool:
    return plan_persistence.persist_03_monitor_stop_reason(
        plan_meta,
        reason,
        soc_source=soc_reading.source if soc_reading is not None else None,
        soc_error=soc_reading.error if soc_reading is not None else None,
        open_firestore=_open_firestore_for_plan,
    )


def _persist_night_soc_execution(
    plan_meta: dict[str, Any], state: str, **values: Any
) -> bool:
    return plan_persistence.persist_night_soc_execution(
        plan_meta=plan_meta,
        state=state,
        open_firestore=_open_firestore_for_plan,
        **values,
    )


# HISTORICAL_FAILURE_LOCK (79361c4, f910b98, 0a804f4): 03-monitor must acquire
# the same single-owner lease as the persistence layer; do not bypass the wrapper.
def _acquire_night_soc_lease(plan_meta: dict[str, Any]) -> bool:
    return plan_persistence.acquire_night_soc_lease(
        plan_meta=plan_meta,
        owner="03-monitor",
        lease_seconds=_env_int("NIGHT_SOC_LEASE_SECONDS", 18000, min_value=60),
        open_firestore=_open_firestore_for_plan,
    )


# HISTORICAL_FAILURE_LOCK (d1d7792): the 07:00 transition cannot bypass the
# durable 03-monitor terminal state while single-owner control is enforced.
def _assert_day_transition_allowed() -> None:
    if os.getenv("NIGHT_SOC_CONTROL_MODE", "observe").strip().lower() != "enforce":
        return
    if os.getenv("DRY_RUN", "false").strip().lower() in {"1", "true", "yes", "on"}:
        return
    target_date = _adjust03_target_date()
    if not plan_persistence.can_apply_day_transition(
        plan_date=target_date,
        open_firestore=_open_firestore_for_plan,
    ):
        raise RuntimeError(
            f"07:00 day transition blocked: night SOC owner has not completed plan_date={target_date}"
        )


def _quality_gated_soc_value(reading: SocReading, *, now: datetime) -> float | None:
    if os.getenv("NIGHT_SOC_CONTROL_MODE", "observe").strip().lower() != "enforce":
        return reading.value_percent
    max_age_seconds = _env_float(
        "NIGHT_SOC_MAX_SOC_AGE_SECONDS", 360.0, min_value=0.0
    )
    valid, reason = night_soc_controller.validate_soc_observation(
        reading.value_percent,
        reading.observed_at,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    if not valid:
        print(
            f"[cloud_job_runner] night SOC observation rejected by quality gate: {reason}",
            flush=True,
        )
        return None
    return reading.value_percent


def _required_charge_percent_from_plan(plan_meta: dict[str, float | str | None]) -> float:
    return forced_charge_monitor.required_charge_percent_from_plan(plan_meta)


def _should_keep_standby_without_charge(
    *,
    required_charge_percent: float,
    required_charge_kwh: float,
) -> bool:
    settings = ForcedChargeSettings.from_env()
    return not requires_forced_charge(
        ChargeDemand(required_charge_percent, required_charge_kwh),
        percent_epsilon=settings.no_charge_percent_epsilon,
        kwh_epsilon=settings.no_charge_kwh_epsilon,
    )


def _estimate_required_charge_kwh(
    *,
    plan_meta: dict[str, float | str | None],
    latest_soc_percent: float | None,
) -> float:
    return forced_charge_monitor.estimate_required_charge_kwh(
        plan_meta=plan_meta, latest_soc_percent=latest_soc_percent
    )


def _latest_kpnet_csv_paths(artifacts_dir: Path) -> list[Path]:
    """Keep this seam until Cloud Job tests stop monkeypatching the private name."""
    return find_latest_kpnet_csv_paths(artifacts_dir)


def _latest_csv_soc_reading(csv_paths: list[Path]) -> tuple[float | None, datetime | None]:
    return soc_reading.latest_csv_soc_reading(csv_paths)


def _latest_realtime_soc_percent() -> float | None:
    return soc_reading.latest_realtime_soc_percent()


def _read_soc_with_fallback(csv_paths: list[Path]) -> SocReading:
    return soc_reading.read_soc_with_fallback(
        csv_paths,
        latest_realtime=_latest_realtime_soc_percent,
        latest_csv=_latest_csv_soc_reading,
        env_int=lambda name, default: _env_int(name, default, min_value=1 if "ATTEMPTS" in name else 0),
        env_float=lambda name, default: _env_float(name, default, min_value=0.0),
    )


def _estimate_forced_charge_rate_percent_per_hour(csv_paths: list[Path]) -> dict[str, float | int | str]:
    return forced_charge_monitor.estimate_forced_charge_rate_percent_per_hour(csv_paths)


def _estimate_required_charge_percent_for_schedule(
    *,
    plan_meta: dict[str, float | str | None],
    latest_soc_percent: float | None,
) -> float:
    return forced_charge_monitor.estimate_required_charge_percent_for_schedule(
        plan_meta=plan_meta, latest_soc_percent=latest_soc_percent
    )


def _estimate_forced_charge_minutes(
    *,
    plan_meta: dict[str, float | str | None],
    latest_soc_percent: float | None,
    csv_paths: list[Path],
) -> tuple[int, dict[str, float | int | str]]:
    return forced_charge_monitor.estimate_forced_charge_minutes(
        plan_meta=plan_meta,
        latest_soc_percent=latest_soc_percent,
        csv_paths=csv_paths,
        rate_estimator=_estimate_forced_charge_rate_percent_per_hour,
    )


def _run_settings_profile(*, profile: str, dynamic_forced_profile: bool) -> None:
    _run(
        [sys.executable, "kpnet_main.py"],
        {
            "KP_WORKFLOW_MODE": "settings",
            "KP_FORCE_SETTINGS_PROFILE": profile,
            "KP_DYNAMIC_FORCED_PROFILE": "true" if dynamic_forced_profile else "false",
            "KP_DYNAMIC_MODE_SWITCH_BY_TIME": "false",
            "NIGHT_SOC_WRITER": f"{os.getenv('CLOUD_JOB_SLOT', 'unknown')}:{profile}",
        },
    )


# readable-code-audit: skip DUP-01 — Cloud Job must clamp retry and delay values to safe minima after malformed input fallback.
def _run_with_retry(
    command: Iterable[str],
    env_updates: dict[str, str] | None = None,
    *,
    label: str,
    attempts_env: str = "KP_COMMAND_RETRY_ATTEMPTS",
    delay_env: str = "KP_COMMAND_RETRY_DELAY_SECONDS",
    default_attempts: int = 3,
    default_delay_seconds: float = 20.0,
) -> None:
    _run_operation_with_retry(
        lambda: _run(command, env_updates),
        label=label,
        attempts_env=attempts_env,
        delay_env=delay_env,
        default_attempts=default_attempts,
        default_delay_seconds=default_delay_seconds,
    )


def _run_settings_profile_with_retry(
    *,
    profile: str,
    dynamic_forced_profile: bool,
    label: str | None = None,
) -> None:
    _run_operation_with_retry(
        lambda: _run_settings_profile(profile=profile, dynamic_forced_profile=dynamic_forced_profile),
        label=label or f"settings-profile-{profile}",
        attempts_env="KP_SETTINGS_RETRY_ATTEMPTS",
        delay_env="KP_SETTINGS_RETRY_DELAY_SECONDS",
        default_attempts=3,
        default_delay_seconds=30.0,
    )


def _run_csv_with_retry(*, label: str = "kpnet-csv") -> None:
    _run_with_retry(
        [sys.executable, "kpnet_main.py"],
        {"KP_WORKFLOW_MODE": "csv"},
        label=label,
        attempts_env="KP_CSV_RETRY_ATTEMPTS",
        delay_env="KP_CSV_RETRY_DELAY_SECONDS",
        default_attempts=3,
        default_delay_seconds=20.0,
    )


# readable-code-audit: skip DUP-01 — Cloud Job clamps or defaults invalid schedule input so an automated job always has a safe execution time, unlike Dashboard display parsing.
def _execute_monitor_terminal_transition(
    plan_meta: dict[str, Any],
    transition: ChargeTransition,
    *,
    device_port: MonitorDevicePort | None = None,
    status_port: MonitorStatusPort | None = None,
) -> bool:
    device = device_port or _RunnerMonitorDevicePort()
    status = status_port or _RunnerMonitorStatusPort()
    terminal = transition.terminal_after_stop
    if terminal is None:
        return False
    if terminal is ChargeState.COMPLETED_TARGET:
        print("[cloud_job_runner] 03-monitor target reached. switch to standby profile.", flush=True)
        label = "03-target-standby"
        persisted_reason = transition.reason
    elif terminal is ChargeState.FAILED_SENSOR:
        print("[cloud_job_runner] 03-monitor SOC unavailable; fail-safe standby.", flush=True)
        label = "03-soc-unavailable-standby"
        persisted_reason = "soc_unavailable_fail_safe"
    elif terminal in {ChargeState.COMPLETED_CUTOFF, ChargeState.FAILED_TIMEOUT}:
        print("[cloud_job_runner] 03-monitor timer reached. switch to standby profile.", flush=True)
        label = "03-timer-standby"
        persisted_reason = "monitor_timeout"
    else:
        raise RuntimeError(f"unsupported monitor terminal state: {terminal.value}")
    try:
        device.apply_profile(
            profile="standby",
            dynamic_forced_profile=False,
            label=label,
        )
    finally:
        status.persist_stop_reason(plan_meta, persisted_reason)
        _persist_night_soc_execution(
            plan_meta,
            "STANDBY_ACKED",
            terminal_state=terminal.value,
            stop_reason=persisted_reason,
        )
    return True


def _keep_standby_when_initial_soc_is_unavailable(
    *,
    plan_meta: dict[str, Any],
    device: MonitorDevicePort,
    status: MonitorStatusPort,
    soc_reading: SocReading,
) -> None:
    """Apply safe standby and persist why forced charging did not start."""
    try:
        device.apply_profile(
            profile="standby",
            dynamic_forced_profile=False,
            label="03-initial-soc-unavailable-standby",
        )
    finally:
        status.persist_stop_reason(plan_meta, "initial_soc_unavailable", soc_reading=soc_reading)
        _persist_night_soc_execution(
            plan_meta,
            "SAFE_TERMINATED",
            stop_reason="initial_soc_unavailable",
            soc_source=soc_reading.source,
        )


# HISTORICAL_FAILURE_LOCK (d1d7792): this function owns the complete forced-charge
# lifecycle, including fail-safe standby, target stop, and durable execution state.
# Do not split or reorder those transitions without replaying the morning-SOC failure.
# readable-code-audit: skip STRUCT-04 — Cloud Job must own the complete lifecycle.
def _monitor_partial_forced_and_stop(
    plan_path: Path,
    *,
    clock: MonitorClock | None = None,
    device_port: MonitorDevicePort | None = None,
    status_port: MonitorStatusPort | None = None,
) -> None:
    monitor_clock = clock or _SystemMonitorClock()
    runner_device = None if device_port is not None else _RunnerMonitorDevicePort()
    device = device_port or runner_device
    if device is None:
        raise RuntimeError("night SOC monitor device is unavailable")
    status = status_port or _RunnerMonitorStatusPort()
    forced_charge_settings = ForcedChargeSettings.from_env()
    if not plan_path.exists():
        print(f"[cloud_job_runner] 03-monitor plan missing: {plan_path}", flush=True)
        return

    plan_meta = _read_plan_meta(plan_path)
    if runner_device is not None:
        runner_device.plan_meta = plan_meta
    enforce_single_owner = os.getenv("NIGHT_SOC_CONTROL_MODE", "observe").strip().lower() == "enforce"
    if enforce_single_owner and not _acquire_night_soc_lease(plan_meta):
        raise RuntimeError(f"03:00 night SOC lease could not be acquired: {plan_meta['plan_id']}")
    _persist_night_soc_execution(plan_meta, "PLAN_FROZEN")
    planned_target_soc = max(0.0, float(plan_meta.get("target_soc_7_percent", 0.0) or 0.0))
    target_soc = night_soc_controller.effective_target_soc(
        planned_target_soc, forced_charge_settings.min_target_soc_percent
    )
    if target_soc > planned_target_soc:
        plan_meta = {
            **plan_meta,
            "planned_target_soc_7_percent": planned_target_soc,
            "target_soc_7_percent": target_soc,
        }
        print(
            "[cloud_job_runner] 03-monitor minimum target applied "
            f"planned={planned_target_soc:.2f}% effective={target_soc:.2f}%",
            flush=True,
        )
    required_charge_percent = _required_charge_percent_from_plan(plan_meta)
    artifacts_dir = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
    csv_paths = _latest_kpnet_csv_paths(artifacts_dir)
    soc_reading = device.read_soc(csv_paths)
    latest_soc = _quality_gated_soc_value(
        soc_reading,
        now=monitor_clock.now(ZoneInfo(os.getenv("TIMEZONE", "Asia/Tokyo").strip() or "Asia/Tokyo")),
    )
    print(
        f"[cloud_job_runner] 03-monitor SOC source={soc_reading.source} "
        f"error={soc_reading.error or 'none'}",
        flush=True,
    )
    allow_forced_without_soc = os.getenv(
        "ADJUST03_ALLOW_FORCED_START_WITHOUT_SOC", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if latest_soc is None and not allow_forced_without_soc:
        print("[cloud_job_runner] 03-monitor initial SOC unavailable; keep standby.", flush=True)
        _keep_standby_when_initial_soc_is_unavailable(
            plan_meta=plan_meta, device=device, status=status, soc_reading=soc_reading
        )
        return
    required_kwh = _estimate_required_charge_kwh(plan_meta=plan_meta, latest_soc_percent=latest_soc)
    if latest_soc is not None:
        required_charge_percent = max(0.0, target_soc - latest_soc)
    if not requires_forced_charge(
        ChargeDemand(required_charge_percent, required_kwh),
        percent_epsilon=forced_charge_settings.no_charge_percent_epsilon,
        kwh_epsilon=forced_charge_settings.no_charge_kwh_epsilon,
    ):
        status.persist_no_charge(
            plan_meta=plan_meta,
            target_soc=target_soc,
            latest_soc=latest_soc,
            soc_source=soc_reading.source,
            required_kwh=required_kwh,
        )
        _persist_night_soc_execution(
            plan_meta,
            "COMPLETED_NO_CHARGE",
            latest_soc_percent=latest_soc,
            required_charge_percent=required_charge_percent,
        )
        print(
            "[cloud_job_runner] 03-monitor charge not needed; keep standby until 07:00 green transition. "
            f"required={required_charge_percent:.2f}% required_kwh={required_kwh:.3f} "
            f"target_soc={target_soc:.2f}% latest_soc={latest_soc if latest_soc is not None else 'n/a'}",
            flush=True,
        )
        return
    default_power_kw = float(os.getenv("KP_DEFAULT_CHARGE_POWER_KW", "1.8").strip() or "1.8")
    if default_power_kw <= 0:
        default_power_kw = 1.8
    estimated_charge_minutes, charge_rate_info = _estimate_forced_charge_minutes(
        plan_meta=plan_meta,
        latest_soc_percent=latest_soc,
        csv_paths=csv_paths,
    )
    poll_seconds = forced_charge_settings.poll_interval_seconds
    soc_margin = min(target_soc, forced_charge_settings.stop_soc_margin_percent)
    timezone_name = os.getenv("TIMEZONE", "Asia/Tokyo").strip() or "Asia/Tokyo"
    cutoff_hhmm = forced_charge_settings.cutoff.strftime("%H:%M")
    cutoff_seconds = _seconds_until_cutoff(timezone_name=timezone_name, cutoff_hhmm=cutoff_hhmm)
    if cutoff_seconds <= 0:
        print("[cloud_job_runner] 03-monitor cutoff already reached; keep standby until 07:00 job.", flush=True)
        device.apply_profile(
            profile="standby",
            dynamic_forced_profile=False,
            label="03-cutoff-standby",
        )
        status.persist_stop_reason(plan_meta, "cutoff_reached")
        _persist_night_soc_execution(plan_meta, "STANDBY_ACKED", stop_reason="cutoff_reached")
        return

    charge_start_hhmm = _hhmm_after_delay(timezone_name=timezone_name, delay_seconds=0)
    print(
        "[cloud_job_runner] 03-monitor immediate forced charge "
        f"target_soc={target_soc:.2f}% latest_soc={latest_soc if latest_soc is not None else 'n/a'} "
        f"required={required_kwh:.3f}kWh "
        f"estimated={estimated_charge_minutes}min "
        f"rate={charge_rate_info.get('percent_per_hour')}%/h "
        f"samples={charge_rate_info.get('sample_count')} "
        f"poll={poll_seconds}s cutoff={cutoff_hhmm}",
        flush=True,
    )
    status.persist_schedule(
        plan_meta=plan_meta,
        charge_start_time=charge_start_hhmm,
        charge_end_time=cutoff_hhmm,
        target_soc=target_soc,
        latest_soc=latest_soc,
        soc_source=soc_reading.source,
        soc_error=soc_reading.error,
        monitor_start_reason=("explicit_without_soc" if latest_soc is None else "soc_available"),
        required_kwh=required_kwh,
        estimated_charge_minutes=estimated_charge_minutes,
        default_power_kw=default_power_kw,
        charge_rate_info=charge_rate_info,
    )
    _persist_night_soc_execution(
        plan_meta,
        "CHARGING",
        latest_soc_percent=latest_soc,
        effective_target_soc_percent=target_soc,
        required_charge_percent=required_charge_percent,
        required_kwh=required_kwh,
    )

    try:
        device.apply_profile(profile="forced", dynamic_forced_profile=True, label="03-forced-start")
    except Exception:
        _attempt_03_fail_safe_standby(
            plan_meta,
            label="03-forced-start-failed-standby",
            reason="forced_start_failed_fail_safe",
            device_port=device,
            status_port=status,
        )
        _persist_night_soc_execution(plan_meta, "SAFE_TERMINATED", stop_reason="forced_start_failed_fail_safe")
        raise

    monitor_seconds = _seconds_until_cutoff(timezone_name=timezone_name, cutoff_hhmm=cutoff_hhmm)
    if monitor_seconds <= 0:
        print("[cloud_job_runner] 03-monitor no monitor window after forced-start; switch to standby.", flush=True)
        device.apply_profile(
            profile="standby",
            dynamic_forced_profile=False,
            label="03-no-window-standby",
        )
        _persist_night_soc_execution(plan_meta, "STANDBY_ACKED", stop_reason="no_monitor_window")
        return

    print(
        f"[cloud_job_runner] 03-monitor forced-started monitor={monitor_seconds}s until cutoff={cutoff_hhmm}",
        flush=True,
    )
    started_at = monitor_clock.monotonic_seconds()
    monitor_started_at = monitor_clock.now(ZoneInfo(timezone_name))
    monitor_policy = ChargePolicy(
        target_soc_percent=target_soc,
        cutoff=monitor_started_at + timedelta(seconds=monitor_seconds),
        max_runtime_seconds=float(monitor_seconds),
        max_sensor_failures=forced_charge_settings.max_consecutive_soc_failures,
        hysteresis_percent=soc_margin,
    )
    monitor_progress = ChargeMonitorProgress(previous_soc_percent=latest_soc)
    reapply_policy = ChargeReapplyPolicy(
        enabled=forced_charge_settings.reapply_if_soc_not_increasing,
        after_stagnant_polls=forced_charge_settings.reapply_after_polls,
        min_soc_delta_percent=forced_charge_settings.reapply_min_soc_delta_percent,
    )
    completion_estimator = ForcedChargeCompletionEstimator(
        rate_percent_per_hour=float(charge_rate_info.get("percent_per_hour") or 1.0),
        confirm_before_minutes=forced_charge_settings.completion_confirm_before_minutes,
    )
    while True:
        elapsed_clock_seconds = max(0.0, monitor_clock.monotonic_seconds() - started_at)
        if elapsed_clock_seconds >= monitor_seconds:
            transition = decide_transition(
                ChargeState.MONITORING,
                ChargeObservation(
                    now=monitor_policy.cutoff,
                    soc_percent=None,
                    elapsed_seconds=elapsed_clock_seconds,
                ),
                monitor_policy,
            )
            _execute_monitor_terminal_transition(
                plan_meta, transition, device_port=device, status_port=status
            )
            return
        try:
            soc_reading = device.read_soc(csv_paths)
        except Exception:
            _attempt_03_fail_safe_standby(
                plan_meta,
                label="03-monitor-exception-standby",
                reason="monitor_exception_fail_safe",
                device_port=device,
                status_port=status,
            )
            raise
        latest_soc = _quality_gated_soc_value(
            soc_reading,
            now=monitor_clock.now(ZoneInfo(timezone_name)),
        )
        if soc_reading.error:
            print(
                f"[cloud_job_runner] 03-monitor SOC source={soc_reading.source} error={soc_reading.error}",
                flush=True,
            )
        if latest_soc is not None:
            print(
                f"[cloud_job_runner] 03-monitor latest_soc={latest_soc:.2f}% "
                f"target={target_soc:.2f}% margin={soc_margin:.2f}%",
                flush=True,
            )
        else:
            print("[cloud_job_runner] 03-monitor latest SOC unavailable.", flush=True)

        previous_soc = monitor_progress.previous_soc_percent
        monitor_progress, should_reapply = monitor_progress.observe(
            latest_soc,
            target_soc_percent=target_soc,
            hysteresis_percent=soc_margin,
            reapply_policy=reapply_policy,
        )
        if should_reapply:
            print(
                "[cloud_job_runner] 03-monitor SOC not increasing; reapply forced profile "
                f"latest={latest_soc:.2f}% previous={previous_soc:.2f}%",
                flush=True,
            )
            try:
                device.apply_profile(
                    profile="forced",
                    dynamic_forced_profile=True,
                    label="03-forced-reapply",
                )
            except Exception:
                _attempt_03_fail_safe_standby(
                    plan_meta,
                    label="03-forced-reapply-failed-standby",
                    reason="forced_reapply_failed_fail_safe",
                    device_port=device,
                    status_port=status,
                )
                raise

        observed_at = monitor_clock.now(ZoneInfo(timezone_name))
        transition = decide_transition(
            ChargeState.MONITORING,
            ChargeObservation(
                now=observed_at,
                soc_percent=latest_soc,
                consecutive_sensor_failures=monitor_progress.consecutive_sensor_failures,
                elapsed_seconds=max(0.0, (observed_at - monitor_started_at).total_seconds()),
            ),
            monitor_policy,
        )
        if _execute_monitor_terminal_transition(
            plan_meta, transition, device_port=device, status_port=status
        ):
            return

        remaining = monitor_seconds - int(monitor_clock.monotonic_seconds() - started_at)
        if remaining <= 0:
            continue
        next_check_seconds = completion_estimator.next_check_seconds(
            target_soc=target_soc,
            latest_soc=latest_soc,
            fallback_poll_seconds=poll_seconds,
            cutoff_seconds=remaining,
        )
        if next_check_seconds <= 0:
            timeout_transition = decide_transition(
                ChargeState.MONITORING,
                ChargeObservation(
                    now=monitor_policy.cutoff,
                    soc_percent=None,
                    elapsed_seconds=float(monitor_seconds),
                ),
                monitor_policy,
            )
            _execute_monitor_terminal_transition(
                plan_meta, timeout_transition, device_port=device, status_port=status
            )
            return
        print(
            "[cloud_job_runner] 03-monitor next check "
            f"sleep={next_check_seconds}s remaining_to_cutoff={remaining}s",
            flush=True,
        )
        monitor_clock.sleep(next_check_seconds)

def main() -> int:
    slot = os.getenv("CLOUD_JOB_SLOT", "").strip().lower()
    if slot in {"23", "night", "night23"}:
        _run_night_23()
        return 0
    if slot in {"3", "03", "adjust", "adjust03"}:
        _run_adjust_03(plan_refresh_only="--plan-refresh-only" in sys.argv[1:])
        return 0
    if slot in {"7", "07", "day", "day07"}:
        _run_day_07()
        return 0
    if slot in {"settings-roundtrip", "settings_roundtrip"}:
        target_soc = _env_float("SETTINGS_ROUNDTRIP_TARGET_SOC", 100.0, min_value=0.0)
        if target_soc > 100.0:
            raise RuntimeError("SETTINGS_ROUNDTRIP_TARGET_SOC must be <= 100")
        summary = run_settings_roundtrip(target_soc_percent=target_soc)
        print(f"[cloud_job_runner] settings round-trip passed: {summary}", flush=True)
        return 0
    raise RuntimeError("CLOUD_JOB_SLOT は 23/night, 03/adjust, 07/day, settings-roundtrip のいずれかを指定してください")


if __name__ == "__main__":
    raise SystemExit(main())
