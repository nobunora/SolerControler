from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import app.kpnet.workflow as kpnet_workflow
from app.kpnet.profiles import STANDBY_PROFILE
from app.runtime.cloud_job import (
    ForcedChargeCompletionEstimator,
    _execute_monitor_terminal_transition,
    _finalize_03_exception_with_fail_safe_standby,
    SocReading,
    _adjust03_target_date,
    _assert_manual_handoff_eligible,
    _assert_day_transition_allowed,
    _estimate_forced_charge_minutes,
    _estimate_forced_charge_rate_percent_per_hour,
    _estimate_required_charge_kwh,
    _keep_standby_when_initial_soc_is_unavailable,
    _mask_env_updates,
    _monitor_partial_forced_and_stop,
    _persist_03_monitor_schedule_to_firestore,
    _persist_03_no_charge_decision_to_firestore,
    _read_plan_meta,
    _read_soc_with_fallback,
    _latest_realtime_soc_percent,
    _latest_csv_soc_reading,
    _required_charge_percent_from_plan,
    _run_adjust_03,
    _run_day_07,
    _run_night_23,
    _RunnerMonitorDevicePort,
)
from app.forced_charge import ChargeEffect, ChargeState, ChargeTransition
from app.runtime.plan_persistence import persist_night_soc_execution
from scripts.kpnet_incident_validation import _MemoryFirestore


class _FailSafeDevice:
    def __init__(self, *, standby_fails: bool) -> None:
        self.standby_fails = standby_fails
        self.calls: list[str] = []

    def apply_profile(self, *, profile: str, dynamic_forced_profile: bool, label: str) -> None:
        self.calls.append(label)
        if self.standby_fails:
            raise RuntimeError("standby readback mismatch")


class _FailSafeStatus:
    def __init__(self) -> None:
        self.reasons: list[str] = []

    def persist_stop_reason(self, _plan_meta: dict, reason: str, **_kwargs: object) -> bool:
        self.reasons.append(reason)
        return True

    def persist_schedule(self, **_values: object) -> bool:
        return True

    def persist_no_charge(self, **_values: object) -> bool:
        return True


class _ReplayClock:
    def __init__(self) -> None:
        self.elapsed = 0.0

    def monotonic_seconds(self) -> float:
        return self.elapsed

    def now(self, timezone: ZoneInfo) -> datetime:
        return datetime(2099, 8, 29, 3, 0, tzinfo=timezone) + timedelta(seconds=self.elapsed)

    def sleep(self, seconds: int) -> None:
        self.elapsed += seconds


class _ForcedReapplyReplayDevice:
    def __init__(self, *, standby_fails: bool) -> None:
        self.standby_fails = standby_fails
        self.forced_calls = 0
        self.calls: list[str] = []
        observed_at = datetime(2099, 8, 29, 3, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.readings = iter(
            [SocReading(20.0, "realtime", None, observed_at), SocReading(20.0, "realtime", None, observed_at)]
        )

    def read_soc(self, _csv_paths: list[Path]) -> SocReading:
        return next(self.readings)

    def apply_profile(self, *, profile: str, dynamic_forced_profile: bool, label: str) -> None:
        self.calls.append(label)
        if profile == "forced":
            self.forced_calls += 1
            if self.forced_calls == 2:
                raise RuntimeError("forced reapply readback mismatch")
        elif self.standby_fails:
            raise RuntimeError("standby readback mismatch")


def _run_forced_reapply_failure_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, standby_fails: bool
) -> tuple[_MemoryFirestore, dict[str, object], _ForcedReapplyReplayDevice, list[dict[str, object]]]:
    """Use the real monitor, persistence layer, and 07 gate; no device/subprocess I/O."""
    store = _MemoryFirestore()
    plan_meta: dict[str, object] = {
        "date": "2099-08-29", "plan_id": "2099-08-29-1-reapply", "required_night_charge_kwh": 1.0,
        "target_soc_7_percent": 80.0, "effective_capacity_kwh": 10.0,
    }
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    green_calls: list[dict[str, object]] = []
    device = _ForcedReapplyReplayDevice(standby_fails=standby_fails)
    monkeypatch.setenv("NIGHT_SOC_CONTROL_MODE", "enforce")
    monkeypatch.setenv("NIGHT_SOC_MANUAL_OPERATION", "false")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("ADJUST03_FORCE_REAPPLY_AFTER_POLLS", "1")
    monkeypatch.setattr("app.runtime.cloud_job._open_firestore_for_plan", lambda: store)
    monkeypatch.setattr("app.runtime.cloud_job._acquire_night_soc_lease", lambda _meta: True)
    monkeypatch.setattr("app.runtime.cloud_job._read_plan_meta", lambda _path: plan_meta)
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _path: [])
    monkeypatch.setattr("app.runtime.cloud_job._seconds_until_cutoff", lambda **_kwargs: 3600)
    monkeypatch.setattr("app.runtime.cloud_job._persist_03_monitor_schedule_to_firestore", lambda **_kwargs: True)
    monkeypatch.setattr("app.runtime.cloud_job._persist_03_monitor_stop_reason", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("app.runtime.cloud_job._adjust03_target_date", lambda: plan_meta["date"])
    monkeypatch.setattr("app.runtime.cloud_job._run_settings_profile_with_retry", lambda **values: green_calls.append(values))

    with pytest.raises(RuntimeError, match="forced reapply readback mismatch"):
        _monitor_partial_forced_and_stop(
            plan_path, clock=_ReplayClock(), device_port=device, status_port=_FailSafeStatus()
        )
    return store, plan_meta, device, green_calls


def test_monitor_reapply_failure_persists_ack_then_actual_07_runs_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, plan_meta, device, green_calls = _run_forced_reapply_failure_replay(
        monkeypatch, tmp_path, standby_fails=False
    )

    record = store.collections["night_soc_execution"][str(plan_meta["date"])]
    assert record["state"] == "STANDBY_ACKED"
    assert record["terminal_state"] == "failed_command"
    assert record["stop_reason"] == "forced_reapply_failed_fail_safe"
    assert device.calls == ["03-forced-start", "03-forced-reapply", "03-forced-reapply-failed-standby"]

    _run_day_07()
    assert green_calls == [{"profile": "green", "dynamic_forced_profile": False, "label": "07-green"}]


def test_monitor_reapply_failure_with_unconfirmed_standby_blocks_actual_07(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, plan_meta, device, green_calls = _run_forced_reapply_failure_replay(
        monkeypatch, tmp_path, standby_fails=True
    )

    record = store.collections["night_soc_execution"][str(plan_meta["date"])]
    assert record["state"] == "STANDBY_UNCONFIRMED"
    assert device.calls == ["03-forced-start", "03-forced-reapply", "03-forced-reapply-failed-standby"]
    with pytest.raises(RuntimeError, match="07:00 day transition blocked"):
        _run_day_07()
    assert green_calls == []


def test_confirmed_standby_persistence_failure_blocks_07_after_device_readback(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    device = _FailSafeDevice(standby_fails=False)
    monkeypatch.setattr("app.runtime.cloud_job._persist_night_soc_execution", lambda *_, **__: False)

    with pytest.raises(RuntimeError, match="terminal night SOC execution persistence failed"):
        _execute_monitor_terminal_transition(
            {"date": "2099-08-29", "plan_id": "2099-08-29-1-persist"},
            ChargeTransition(ChargeState.STOPPING, (ChargeEffect.SET_STANDBY,), "target", ChargeState.COMPLETED_TARGET),
            device_port=device,
            status_port=_FailSafeStatus(),
        )
    assert device.calls == ["03-target-standby"]


@pytest.mark.parametrize(("readback", "expected_attempts"), [("5", 1), ("1", 3)])
def test_runner_monitor_port_uses_adjust03_and_cloud_retry_for_kpnet_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, readback: str, expected_attempts: int
) -> None:
    """Keep the production port -> adjustment -> retry -> KP-NET workflow boundary intact."""
    attempts: list[str] = []

    class FakeKpNetClient:
        csrf_setting = "csrf"
        pcsid = "pcsid"

        def confirm_setting(self, _payload: dict[str, str]) -> tuple[bool, str, str, str]:
            return True, "confirmed", "", "<html>confirmed</html>"

        def write_setting(self, _confirm_html: str) -> dict[str, bool]:
            return {"changed": True}

        def read_current_settings(self) -> dict[str, str]:
            return {"batteryOperatingMode": readback}

    def fake_run_settings_profile(*, profile: str, dynamic_forced_profile: bool) -> None:
        assert profile == "standby"
        assert dynamic_forced_profile is False
        attempts.append(profile)
        summary: dict[str, object] = {"setting_results": []}
        kpnet_workflow._apply_settings_profile(
            client=FakeKpNetClient(),
            cfg=SimpleNamespace(dry_run=False),
            run_dir=tmp_path,
            summary=summary,
            current={"batteryOperatingMode": "1"},
            value_maps={},
            profile=STANDBY_PROFILE,
        )

    monkeypatch.setenv("NIGHT_SOC_READBACK_REQUIRED", "true")
    monkeypatch.setenv("KP_SETTINGS_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("KP_SETTINGS_RETRY_DELAY_SECONDS", "0")
    monkeypatch.setattr(
        "app.kpnet.workflow._build_payload", lambda **_kwargs: ({"batteryOperatingMode": "5"}, ["batteryOperatingMode"])
    )
    monkeypatch.setattr("app.runtime.cloud_job._run_settings_profile", fake_run_settings_profile)
    monkeypatch.setattr("app.runtime.cloud_job._run_db_pipeline_slot", lambda *_args, **_kwargs: None)

    port = _RunnerMonitorDevicePort()
    if readback == "5":
        port.apply_profile(profile="standby", dynamic_forced_profile=False, label="test-standby")
    else:
        with pytest.raises(RuntimeError, match="read-back mismatch"):
            port.apply_profile(profile="standby", dynamic_forced_profile=False, label="test-standby")
    assert attempts == ["standby"] * expected_attempts


@pytest.mark.parametrize(
    "reason",
    [
        "initial_soc_unavailable",
        "forced_start_failed_fail_safe",
        "forced_reapply_failed_fail_safe",
        "monitor_exception_fail_safe",
        "target_standby_failed",
        "cutoff_standby_failed",
        "sensor_standby_failed",
    ],
)
def test_fail_safe_terminal_matrix_never_claims_ack_without_standby_readback(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    persisted: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "app.runtime.cloud_job._persist_night_soc_execution",
        lambda _meta, state, **values: persisted.append((state, values)) or True,
    )
    plan_meta = {"date": "2026-08-29", "plan_id": "2026-08-29-plan"}
    status = _FailSafeStatus()

    _finalize_03_exception_with_fail_safe_standby(
        plan_meta, label="test-standby", reason=reason, primary_error=RuntimeError("primary"),
        device=_FailSafeDevice(standby_fails=False), status=status,
    )
    assert persisted == [("STANDBY_ACKED", {"terminal_state": "failed_command", "stop_reason": reason, "primary_error": "RuntimeError('primary')"})]
    assert status.reasons == [reason]

    persisted.clear()
    _finalize_03_exception_with_fail_safe_standby(
        plan_meta, label="test-standby", reason=reason, primary_error=RuntimeError("primary"),
        device=_FailSafeDevice(standby_fails=True), status=status,
    )
    assert persisted[0][0] == "STANDBY_UNCONFIRMED"
    assert persisted[0][1]["terminal_state"] == "standby_unconfirmed"
    assert "STANDBY_ACKED" not in [state for state, _values in persisted]


def test_real_failure_recovery_persists_standby_then_allows_07_green(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay forced-reapply failure recovery through persistence and the actual 07 gate."""
    store = _MemoryFirestore()
    plan_meta = {"date": "2099-08-29", "plan_id": "2099-08-29-1-fail-safe"}
    settings_calls: list[dict[str, object]] = []
    monkeypatch.setenv("NIGHT_SOC_CONTROL_MODE", "enforce")
    monkeypatch.setenv("NIGHT_SOC_MANUAL_OPERATION", "false")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setattr("app.runtime.cloud_job._open_firestore_for_plan", lambda: store)
    monkeypatch.setattr("app.runtime.cloud_job._adjust03_target_date", lambda: plan_meta["date"])
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_settings_profile_with_retry",
        lambda **values: settings_calls.append(values),
    )

    _run_night_23()
    _finalize_03_exception_with_fail_safe_standby(
        plan_meta,
        label="03-forced-reapply-failed-standby",
        reason="forced_reapply_failed_fail_safe",
        primary_error=RuntimeError("forced readback mismatch"),
        device=_FailSafeDevice(standby_fails=False),
        status=_FailSafeStatus(),
    )
    _run_day_07()

    record = store.collections["night_soc_execution"][plan_meta["date"]]
    assert record["state"] == "STANDBY_ACKED"
    assert record["terminal_state"] == "failed_command"
    assert record["stop_reason"] == "forced_reapply_failed_fail_safe"
    assert settings_calls == [
        {"profile": "standby", "dynamic_forced_profile": False, "label": "23-settings-standby"},
        {"profile": "green", "dynamic_forced_profile": False, "label": "07-green"},
    ]


def test_mask_env_updates_hides_secrets() -> None:
    masked = _mask_env_updates(
        {
            "KP_MONITOR_PASSWORD": "plain-password",
            "API_TOKEN": "plain-token",
            "KP_WORKFLOW_MODE": "settings",
        }
    )
    assert masked["KP_MONITOR_PASSWORD"] == "***"
    assert masked["API_TOKEN"] == "***"
    assert masked["KP_WORKFLOW_MODE"] == "settings"


def test_mask_env_updates_none() -> None:
    assert _mask_env_updates(None) == {}


def test_day_transition_dry_run_does_not_require_night_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIGHT_SOC_CONTROL_MODE", "enforce")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setattr(
        "app.runtime.cloud_job.plan_persistence.can_apply_day_transition",
        lambda **_: pytest.fail("dry-run must not query the execution lease"),
    )

    _assert_day_transition_allowed()


def test_day_transition_accepts_explicit_manual_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIGHT_SOC_CONTROL_MODE", "enforce")
    monkeypatch.setenv("NIGHT_SOC_MANUAL_OPERATION", "true")
    monkeypatch.setattr(
        "app.runtime.cloud_job.plan_persistence.can_apply_day_transition",
        lambda **kwargs: kwargs["allow_manual_owner"] is True,
    )

    _assert_day_transition_allowed()


def test_manual_handoff_postcheck_uses_exact_firestore_record_even_in_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _MemoryFirestore()
    plan_meta = {
        "date": "2026-08-28",
        "plan_id": "2026-08-28-1-manual-handoff",
        "target_soc_7_percent": 80.0,
    }
    assert persist_night_soc_execution(
        plan_meta=plan_meta,
        state="MANUAL_OPERATION",
        owner="manual",
        device_write_skipped=True,
        open_firestore=lambda: store,
    ) is True
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setattr("app.runtime.cloud_job._open_firestore_for_plan", lambda: store)

    _assert_manual_handoff_eligible(plan_meta)

    store.collections["night_soc_execution"]["2026-08-28"]["owner"] = "03-monitor"
    with pytest.raises(RuntimeError, match="manual operation hand-off is not eligible"):
        _assert_manual_handoff_eligible(plan_meta)

    store.collections["night_soc_execution"]["2026-08-28"].update(
        {"owner": "manual", "plan_id": "2026-08-28-2-different-plan"}
    )
    with pytest.raises(RuntimeError, match="manual operation hand-off is not eligible"):
        _assert_manual_handoff_eligible(plan_meta)


def test_manual_soc_operation_skips_03_device_writes_and_records_terminal_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    persisted: list[tuple[dict[str, object], str, dict[str, object]]] = []
    optional_runs: list[bool] = []
    monkeypatch.setenv("NIGHT_SOC_MANUAL_OPERATION", "true")
    monkeypatch.setenv("ADJUST03_REGENERATE_PLAN", "false")
    monkeypatch.setenv("KP_NIGHT_PLAN_PATH", str(plan_path))
    monkeypatch.setattr("app.runtime.cloud_job._run_csv_with_retry", lambda **_: None)
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr("app.runtime.cloud_job._persist_previous_day_soc_feedback", lambda **_: None)
    monkeypatch.setattr("app.runtime.cloud_job._ensure_night_plan_available", lambda _: True)
    monkeypatch.setattr("app.runtime.cloud_job._run_db_pipeline_slot", lambda *_, **__: None)
    monkeypatch.setattr(
        "app.runtime.slot_orchestration._run_optional_04_exports_and_backups",
        lambda: optional_runs.append(True),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._read_plan_meta",
        lambda _: {"date": "2026-08-27", "plan_id": "plan-1", "target_soc_7_percent": 80.0},
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._monitor_partial_forced_and_stop",
        lambda *_: pytest.fail("manual operation must not write through the 03 monitor"),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._persist_night_soc_execution",
        lambda plan, state, **values: persisted.append((plan, state, values)) or True,
    )
    handoff_checks: list[bool] = []
    monkeypatch.setattr(
        "app.runtime.cloud_job._assert_manual_handoff_eligible",
        lambda _plan: handoff_checks.append(True),
    )

    _run_adjust_03()

    assert persisted == [
        (
            {"date": "2026-08-27", "plan_id": "plan-1", "target_soc_7_percent": 80.0},
            "MANUAL_OPERATION",
            {"owner": "manual", "device_write_skipped": True},
        )
    ]
    assert handoff_checks == [True]
    assert optional_runs == [True]


def test_manual_soc_operation_fails_when_handoff_persistence_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    optional_runs: list[bool] = []
    monkeypatch.setenv("NIGHT_SOC_MANUAL_OPERATION", "true")
    monkeypatch.setenv("ADJUST03_REGENERATE_PLAN", "false")
    monkeypatch.setenv("KP_NIGHT_PLAN_PATH", str(plan_path))
    monkeypatch.setattr("app.runtime.cloud_job._run_csv_with_retry", lambda **_: None)
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr("app.runtime.cloud_job._persist_previous_day_soc_feedback", lambda **_: None)
    monkeypatch.setattr("app.runtime.cloud_job._ensure_night_plan_available", lambda _: True)
    monkeypatch.setattr("app.runtime.cloud_job._run_db_pipeline_slot", lambda *_, **__: None)
    monkeypatch.setattr("app.runtime.cloud_job._read_plan_meta", lambda _: {"date": "2026-08-27", "plan_id": "plan-1"})
    monkeypatch.setattr(
        "app.runtime.slot_orchestration._run_optional_04_exports_and_backups",
        lambda: optional_runs.append(True),
    )
    monkeypatch.setattr("app.runtime.cloud_job._persist_night_soc_execution", lambda *_, **__: False)

    with pytest.raises(RuntimeError, match="manual operation hand-off could not be persisted"):
        _run_adjust_03()

    assert optional_runs == []


def test_manual_soc_operation_fails_when_persisted_handoff_is_not_eligible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    optional_runs: list[bool] = []
    monkeypatch.setenv("NIGHT_SOC_MANUAL_OPERATION", "true")
    monkeypatch.setenv("ADJUST03_REGENERATE_PLAN", "false")
    monkeypatch.setenv("KP_NIGHT_PLAN_PATH", str(plan_path))
    monkeypatch.setattr("app.runtime.cloud_job._run_csv_with_retry", lambda **_: None)
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr("app.runtime.cloud_job._persist_previous_day_soc_feedback", lambda **_: None)
    monkeypatch.setattr("app.runtime.cloud_job._ensure_night_plan_available", lambda _: True)
    monkeypatch.setattr("app.runtime.cloud_job._run_db_pipeline_slot", lambda *_, **__: None)
    monkeypatch.setattr(
        "app.runtime.cloud_job._read_plan_meta",
        lambda _: {"date": "2026-08-27", "plan_id": "2026-08-27-1", "target_soc_7_percent": 80.0},
    )
    monkeypatch.setattr("app.runtime.cloud_job._persist_night_soc_execution", lambda *_, **__: True)
    monkeypatch.setattr(
        "app.runtime.slot_orchestration._run_optional_04_exports_and_backups",
        lambda: optional_runs.append(True),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._assert_manual_handoff_eligible",
        lambda _plan: (_ for _ in ()).throw(RuntimeError("manual operation hand-off is not eligible")),
    )

    with pytest.raises(RuntimeError, match="manual operation hand-off is not eligible"):
        _run_adjust_03()

    assert optional_runs == []


def test_scheduled_auto_sequence_keeps_device_control_and_reaches_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    settings_calls: list[dict[str, object]] = []
    monitor_calls: list[Path] = []
    gate_calls: list[dict[str, object]] = []
    monkeypatch.setenv("NIGHT_SOC_MANUAL_OPERATION", "false")
    monkeypatch.setenv("NIGHT_SOC_CONTROL_MODE", "enforce")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("ADJUST03_REGENERATE_PLAN", "false")
    monkeypatch.setenv("KP_NIGHT_PLAN_PATH", str(plan_path))
    monkeypatch.delenv("NIGHT23_SETTINGS_PROFILE", raising=False)
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_settings_profile_with_retry",
        lambda **kwargs: settings_calls.append(kwargs),
    )
    monkeypatch.setattr("app.runtime.cloud_job._run_csv_with_retry", lambda **_: None)
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr("app.runtime.cloud_job._persist_previous_day_soc_feedback", lambda **_: None)
    monkeypatch.setattr("app.runtime.cloud_job._ensure_night_plan_available", lambda _: True)
    monkeypatch.setattr("app.runtime.cloud_job._run_db_pipeline_slot", lambda *_, **__: None)
    monkeypatch.setattr(
        "app.runtime.cloud_job._monitor_partial_forced_and_stop",
        lambda path: monitor_calls.append(path),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job.plan_persistence.can_apply_day_transition",
        lambda **kwargs: gate_calls.append(kwargs) or True,
    )

    _run_night_23()
    _run_adjust_03()
    _run_day_07()

    assert settings_calls == [
        {"profile": "standby", "dynamic_forced_profile": False, "label": "23-settings-standby"},
        {"profile": "green", "dynamic_forced_profile": False, "label": "07-green"},
    ]
    assert monitor_calls == [plan_path]
    assert gate_calls and gate_calls[0]["allow_manual_owner"] is False


def test_manual_soc_operation_skips_23_device_write(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIGHT_SOC_MANUAL_OPERATION", "true")
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_settings_profile_with_retry",
        lambda **_: pytest.fail("manual operation must not write through the 23 slot"),
    )

    _run_night_23()


def test_03_readback_failure_leaves_07_transition_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NIGHT_SOC_MANUAL_OPERATION", "false")
    monkeypatch.setenv("NIGHT_SOC_CONTROL_MODE", "enforce")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("ADJUST03_REGENERATE_PLAN", "false")
    monkeypatch.setenv("KP_NIGHT_PLAN_PATH", str(plan_path))
    monkeypatch.setattr("app.runtime.cloud_job._run_csv_with_retry", lambda **_: None)
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr("app.runtime.cloud_job._persist_previous_day_soc_feedback", lambda **_: None)
    monkeypatch.setattr("app.runtime.cloud_job._ensure_night_plan_available", lambda _: True)
    monkeypatch.setattr("app.runtime.cloud_job._run_db_pipeline_slot", lambda *_, **__: None)
    def fail_monitor(*_: object) -> None:
        raise RuntimeError(
            "KP-NET settings read-back mismatch for profile=night-green: batteryOperatingMode"
        )

    monkeypatch.setattr("app.runtime.cloud_job._monitor_partial_forced_and_stop", fail_monitor)
    with pytest.raises(RuntimeError, match="batteryOperatingMode"):
        _run_adjust_03()

    monkeypatch.setattr(
        "app.runtime.cloud_job.plan_persistence.can_apply_day_transition",
        lambda **_: False,
    )
    with pytest.raises(RuntimeError, match="07:00 day transition blocked"):
        _assert_day_transition_allowed()


def test_enforced_monitor_fails_closed_when_night_lease_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NIGHT_SOC_CONTROL_MODE", "enforce")
    monkeypatch.setattr(
        "app.runtime.cloud_job._read_plan_meta",
        lambda _: {"date": "2026-08-21", "plan_id": "plan-1", "target_soc_7_percent": 80.0},
    )
    monkeypatch.setattr("app.runtime.cloud_job._acquire_night_soc_lease", lambda _meta: False)
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_settings_profile",
        lambda **_: pytest.fail("lease failure must not change battery settings"),
    )

    with pytest.raises(RuntimeError, match="night SOC lease could not be acquired"):
        _monitor_partial_forced_and_stop(plan_path)


def test_required_charge_percent_from_plan_uses_soc_delta() -> None:
    pct = _required_charge_percent_from_plan(
        {
            "target_soc_7_percent": 80.0,
            "soc_now_percent": 25.0,
            "effective_capacity_kwh": 9.0,
            "required_night_charge_kwh": 2.0,
        }
    )
    assert pct == 55.0


def test_read_plan_meta_rejects_missing_target(tmp_path: Path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text(
        """
        {
          "forecast": {"date": "2026-05-27"},
          "result": {"required_night_charge_kwh": 0.0}
        }
        """.strip(),
        encoding="utf-8",
    )

    try:
        _read_plan_meta(plan_path)
    except RuntimeError as exc:
        assert "target_soc_7_percent" in str(exc)
    else:
        raise AssertionError("missing target_soc_7_percent must be rejected")


def test_read_plan_meta_rejects_plan_quality_should_apply_false(tmp_path: Path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text(
        """
        {
          "forecast": {"date": "2026-05-27"},
          "plan_quality": {"should_apply": false},
          "result": {
            "target_soc_7_percent": 40.0,
            "required_night_charge_kwh": 0.0
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    try:
        _read_plan_meta(plan_path)
    except RuntimeError as exc:
        assert "not safe to apply" in str(exc)
    else:
        raise AssertionError("plan_quality.should_apply=false must be rejected")


def test_estimate_required_charge_kwh_uses_latest_soc(monkeypatch) -> None:
    monkeypatch.setenv("KP_NIGHT_CHARGE_EFFICIENCY", "0.9")
    required = _estimate_required_charge_kwh(
        plan_meta={
            "target_soc_7_percent": 100.0,
            "soc_now_percent": 0.0,
            "effective_capacity_kwh": 9.0,
            "required_night_charge_kwh": 9.0,
        },
        latest_soc_percent=60.0,
    )
    assert required == 4.0


def test_estimate_forced_charge_minutes_uses_empirical_soc_rate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_SAMPLE_MIN_KWH", "1.2")
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_RATE_MIN_PERCENT_PER_HOUR", "25")
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_RATE_MAX_PERCENT_PER_HOUR", "50")
    csv_path = tmp_path / "kp.csv"
    csv_path.write_text(
        "\n".join(
            [
                "年月日,時刻,蓄電残量(SOC)[%],充電電力量[kWh]",
                "2026/06/03,02:30,0,0",
                "2026/06/03,03:00,21,2.01",
                "2026/06/03,03:30,42,2.00",
            ]
        ),
        encoding="utf-8-sig",
    )

    minutes, info = _estimate_forced_charge_minutes(
        plan_meta={"target_soc_7_percent": 80.0, "soc_now_percent": 0.0},
        latest_soc_percent=0.0,
        csv_paths=[csv_path],
    )

    assert minutes == 115
    assert info["source"] == "csv-14d-degradation-trend-ewma-soc-rate"
    assert info["sample_count"] == 1
    assert info["interval_sample_count"] == 2
    assert info["percent_per_hour"] == 42.0


def test_forced_charge_rate_tracks_recent_14_day_degradation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_SAMPLE_MIN_KWH", "1.2")
    csv_path = tmp_path / "kp.csv"
    lines = ["年月日,時刻,蓄電残量(SOC)[%],充電電力量[kWh]"]
    for offset, daily_gain in enumerate([20, 19, 18, 17, 16, 15]):
        day = 10 + offset
        lines.extend(
            [
                f"2026/07/{day:02d},02:30,0,0",
                f"2026/07/{day:02d},03:00,{daily_gain},2.0",
                f"2026/07/{day:02d},03:30,{daily_gain * 2},2.0",
            ]
        )
    csv_path.write_text("\n".join(lines), encoding="utf-8-sig")

    info = _estimate_forced_charge_rate_percent_per_hour([csv_path])

    assert info["source"] == "csv-14d-degradation-trend-ewma-soc-rate"
    assert info["sample_count"] == 6
    assert float(info["raw_percent_per_hour"]) < 35.0


def test_forced_charge_completion_estimator_checks_before_predicted_completion() -> None:
    estimator = ForcedChargeCompletionEstimator(rate_percent_per_hour=40.0, confirm_before_minutes=5)

    assert estimator.remaining_minutes(target_soc=80.0, latest_soc=60.0) == 30
    assert estimator.next_check_seconds(
        target_soc=80.0,
        latest_soc=60.0,
        fallback_poll_seconds=3600,
        cutoff_seconds=7200,
    ) == 25 * 60


def test_forced_charge_completion_estimator_caps_to_poll_and_cutoff() -> None:
    estimator = ForcedChargeCompletionEstimator(rate_percent_per_hour=20.0, confirm_before_minutes=5)

    assert estimator.next_check_seconds(
        target_soc=90.0,
        latest_soc=10.0,
        fallback_poll_seconds=180,
        cutoff_seconds=120,
    ) == 120


def test_adjust03_target_date_uses_current_day(monkeypatch) -> None:
    monkeypatch.delenv("FORECAST_DATE_OVERRIDE", raising=False)
    monkeypatch.setenv("TIMEZONE", "Asia/Tokyo")
    now = datetime(2026, 5, 27, 3, 10, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert _adjust03_target_date(now=now) == "2026-05-27"


def test_monitor_forced_charge_clamps_zero_override_to_protected_minimum(
    monkeypatch,
    tmp_path,
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    settings_calls: list[dict[str, object]] = []
    cutoff_seconds = iter([3600, 0])
    monkeypatch.setenv("ADJUST03_MIN_TARGET_SOC_PERCENT", "0")

    monkeypatch.setattr(
        "app.runtime.cloud_job._read_plan_meta",
        lambda _: {"required_night_charge_kwh": 0.0, "target_soc_7_percent": 0.0, "effective_capacity_kwh": 10.0},
    )
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr(
        "app.runtime.cloud_job._read_soc_with_fallback",
        lambda _: SocReading(0.0, "realtime", None, None),
    )
    monkeypatch.setattr("app.runtime.cloud_job._seconds_until_cutoff", lambda **_: next(cutoff_seconds))
    monkeypatch.setattr("app.runtime.cloud_job._persist_night_soc_execution", lambda *_, **__: True)
    monkeypatch.setattr("app.runtime.cloud_job._persist_03_monitor_schedule_to_firestore", lambda **_: True)
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_03_settings_profile_with_db",
        lambda **kwargs: settings_calls.append(kwargs),
    )

    _monitor_partial_forced_and_stop(plan_path)

    assert settings_calls == [
        {"profile": "forced", "dynamic_forced_profile": True, "label": "03-forced-start"},
        {"profile": "standby", "dynamic_forced_profile": False, "label": "03-no-window-standby"},
    ]


def test_monitor_uses_minimum_target_and_invokes_forced_write_readback_at_low_soc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    settings_calls: list[dict[str, object]] = []
    cutoff_seconds = iter([3600, 0])

    monkeypatch.setenv("ADJUST03_MIN_TARGET_SOC_PERCENT", "30")
    monkeypatch.setattr(
        "app.runtime.cloud_job._read_plan_meta",
        lambda _: {
            "date": "2026-08-28",
            "plan_id": "2026-08-28-1-low-soc",
            "required_night_charge_kwh": 0.0,
            "target_soc_7_percent": 0.0,
            "effective_capacity_kwh": 10.0,
        },
    )
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr(
        "app.runtime.cloud_job._read_soc_with_fallback",
        lambda _: SocReading(0.0, "realtime", None, None),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._seconds_until_cutoff",
        lambda **_kwargs: next(cutoff_seconds),
    )
    monkeypatch.setattr("app.runtime.cloud_job._persist_night_soc_execution", lambda *_, **__: True)
    monkeypatch.setattr("app.runtime.cloud_job._persist_03_monitor_schedule_to_firestore", lambda **_: True)
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_03_settings_profile_with_db",
        lambda **kwargs: settings_calls.append(kwargs),
    )

    _monitor_partial_forced_and_stop(plan_path)

    assert settings_calls == [
        {"profile": "forced", "dynamic_forced_profile": True, "label": "03-forced-start"},
        {"profile": "standby", "dynamic_forced_profile": False, "label": "03-no-window-standby"},
    ]


def test_monitor_partial_forced_keeps_standby_when_charge_not_needed(
    monkeypatch,
    tmp_path,
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ADJUST03_MIN_TARGET_SOC_PERCENT", "0")

    monkeypatch.setattr(
        "app.runtime.cloud_job._read_plan_meta",
        lambda _: {
            "required_night_charge_kwh": 0.2,
            "target_soc_7_percent": 2.0,
            "effective_capacity_kwh": 10.0,
        },
    )
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr("app.runtime.cloud_job._persist_night_soc_execution", lambda *_, **__: True)
    monkeypatch.setattr("app.runtime.cloud_job._latest_realtime_soc_percent", lambda: 40.0)
    persisted: list[dict] = []
    monkeypatch.setattr(
        "app.runtime.cloud_job._persist_03_no_charge_decision_to_firestore",
        lambda **kwargs: persisted.append(kwargs) or True,
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_settings_profile",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no KP-NET setting change expected")),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_db_pipeline_slot",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no settings ingestion expected")),
    )

    _monitor_partial_forced_and_stop(plan_path)

    assert persisted == [
        {
            "plan_meta": {
                "required_night_charge_kwh": 0.2,
                "target_soc_7_percent": 30.0,
                "effective_capacity_kwh": 10.0,
                "planned_target_soc_7_percent": 2.0,
            },
            "target_soc": 30.0,
            "latest_soc": 40.0,
            "soc_source": "realtime",
            "required_kwh": 0.0,
        }
    ]


def test_read_soc_with_fallback_uses_realtime(monkeypatch) -> None:
    monkeypatch.setattr("app.runtime.cloud_job._latest_realtime_soc_percent", lambda: 42.0)

    reading = _read_soc_with_fallback([])

    assert reading.value_percent == 42.0
    assert reading.source == "realtime"


@pytest.mark.parametrize("value", ["0", "38", "100"])
def test_latest_csv_soc_reading_accepts_valid_values(tmp_path, value: str) -> None:
    csv_path = tmp_path / "soc.csv"
    csv_path.write_text(
        f"年月日,時刻,蓄電残量(SOC)[%]\n2026/07/14,03:00,{value}\n",
        encoding="utf-8-sig",
    )

    reading, observed_at = _latest_csv_soc_reading([csv_path])

    assert reading == float(value)
    assert observed_at == datetime(2026, 7, 14, 3, 0)


@pytest.mark.parametrize("value", ["-1", "101", "780", "NaN", "Infinity", "-Infinity"])
def test_latest_csv_soc_reading_rejects_invalid_values(tmp_path, value: str) -> None:
    csv_path = tmp_path / "soc.csv"
    csv_path.write_text(
        f"年月日,時刻,蓄電残量(SOC)[%]\n2026/07/14,03:00,{value}\n",
        encoding="utf-8-sig",
    )

    assert _latest_csv_soc_reading([csv_path]) == (None, None)


def test_latest_csv_soc_reading_skips_newer_invalid_row(tmp_path) -> None:
    csv_path = tmp_path / "soc.csv"
    csv_path.write_text(
        "年月日,時刻,蓄電残量(SOC)[%]\n"
        "2026/07/14,02:55,38\n"
        "2026/07/14,03:00,780\n",
        encoding="utf-8-sig",
    )

    assert _latest_csv_soc_reading([csv_path]) == (38.0, datetime(2026, 7, 14, 2, 55))


def test_realtime_soc_returns_value_when_logout_fails(monkeypatch) -> None:
    monkeypatch.setenv("KP_MONITOR_USERNAME", "test-user")
    monkeypatch.setenv("KP_MONITOR_PASSWORD", "test-password")
    monkeypatch.setattr("app.kpnet.workflow.KpNetClient.login", lambda self: None)
    monkeypatch.setattr("app.kpnet.workflow.KpNetClient.read_realtime_soc_percent", lambda self: 47.0)
    monkeypatch.setattr(
        "app.kpnet.workflow.KpNetClient.logout",
        lambda self: (_ for _ in ()).throw(RuntimeError("logout failed")),
    )

    assert _latest_realtime_soc_percent() == 47.0


def test_realtime_soc_preserves_read_failure_when_logout_also_fails(monkeypatch) -> None:
    monkeypatch.setenv("KP_MONITOR_USERNAME", "test-user")
    monkeypatch.setenv("KP_MONITOR_PASSWORD", "test-password")
    monkeypatch.setattr("app.kpnet.workflow.KpNetClient.login", lambda self: None)
    monkeypatch.setattr(
        "app.kpnet.workflow.KpNetClient.read_realtime_soc_percent",
        lambda self: (_ for _ in ()).throw(ValueError("read failed")),
    )
    monkeypatch.setattr(
        "app.kpnet.workflow.KpNetClient.logout",
        lambda self: (_ for _ in ()).throw(RuntimeError("logout failed")),
    )

    with pytest.raises(ValueError, match="read failed"):
        _latest_realtime_soc_percent()


def test_realtime_soc_does_not_logout_after_login_failure(monkeypatch) -> None:
    logout_calls: list[bool] = []
    monkeypatch.setenv("KP_MONITOR_USERNAME", "test-user")
    monkeypatch.setenv("KP_MONITOR_PASSWORD", "test-password")
    monkeypatch.setattr(
        "app.kpnet.workflow.KpNetClient.login",
        lambda self: (_ for _ in ()).throw(RuntimeError("login failed")),
    )
    monkeypatch.setattr("app.kpnet.workflow.KpNetClient.logout", lambda self: logout_calls.append(True))

    with pytest.raises(RuntimeError, match="login failed"):
        _latest_realtime_soc_percent()

    assert logout_calls == []


def test_read_soc_with_fallback_uses_fresh_csv(monkeypatch) -> None:
    # CSV timestamps are naive Asia/Tokyo wall-clock values, independent of the test runner timezone.
    now = datetime.now(ZoneInfo("Asia/Tokyo")).replace(tzinfo=None)
    monkeypatch.setenv("ADJUST03_REALTIME_SOC_RETRY_ATTEMPTS", "1")
    monkeypatch.setattr(
        "app.runtime.cloud_job._latest_realtime_soc_percent",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr("app.runtime.cloud_job._latest_csv_soc_reading", lambda _paths: (38.0, now))

    reading = _read_soc_with_fallback([])

    assert reading.value_percent == 38.0
    assert reading.source == "csv"
    assert "offline" in str(reading.error)


def test_read_soc_with_fallback_rejects_stale_csv(monkeypatch) -> None:
    monkeypatch.setenv("ADJUST03_REALTIME_SOC_RETRY_ATTEMPTS", "1")
    monkeypatch.setenv("ADJUST03_CSV_SOC_MAX_AGE_MINUTES", "60")
    monkeypatch.setattr(
        "app.runtime.cloud_job._latest_realtime_soc_percent",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._latest_csv_soc_reading",
        lambda _paths: (38.0, datetime.now() - timedelta(hours=2)),
    )

    reading = _read_soc_with_fallback([])

    assert reading.value_percent is None
    assert reading.source == "unavailable"
    assert "stale" in str(reading.error)


def test_monitor_partial_forced_starts_immediately_then_switches_standby_at_cutoff(
    monkeypatch,
    tmp_path,
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, bool]] = []
    sleeps: list[int] = []
    cutoff_values = iter([3600, 3600])
    time_values = iter([0.0, 0.0, 0.0, 4000.0])

    monkeypatch.setattr(
        "app.runtime.cloud_job._read_plan_meta",
        lambda _: {"required_night_charge_kwh": 1.0, "target_soc_7_percent": 25.0, "effective_capacity_kwh": 10.0},
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._latest_kpnet_csv_paths",
        lambda _: [],
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._latest_realtime_soc_percent",
        lambda: 20.0,
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._seconds_until_cutoff",
        lambda **kwargs: next(cutoff_values),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_csv_with_retry",
        lambda *, label="kpnet-csv": (_ for _ in ()).throw(AssertionError("03 monitor must not fetch CSV")),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job.time.time",
        lambda: next(time_values),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_settings_profile",
        lambda *, profile, dynamic_forced_profile: calls.append((profile, dynamic_forced_profile)),
    )
    monkeypatch.setattr("app.runtime.cloud_job._persist_night_soc_execution", lambda *_, **__: True)

    _monitor_partial_forced_and_stop(plan_path)

    assert sleeps == [180]
    assert calls == [("forced", True), ("standby", False)]


@pytest.mark.parametrize(
    "terminal",
    [ChargeState.COMPLETED_CUTOFF, ChargeState.FAILED_TIMEOUT],
)
def test_monitor_terminal_executor_preserves_timeout_persistence_reason(
    monkeypatch, terminal: ChargeState
) -> None:
    calls: list[str] = []
    reasons: list[str] = []
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_03_settings_profile_with_db",
        lambda *, profile, dynamic_forced_profile, label: calls.append(label),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._persist_03_monitor_stop_reason",
        lambda _meta, reason: reasons.append(reason) or True,
    )
    monkeypatch.setattr("app.runtime.cloud_job._persist_night_soc_execution", lambda *_, **__: True)

    handled = _execute_monitor_terminal_transition(
        {},
        ChargeTransition(
            ChargeState.STOPPING,
            (ChargeEffect.SET_STANDBY,),
            "cutoff_reached" if terminal is ChargeState.COMPLETED_CUTOFF else "runtime_limit",
            terminal,
        ),
    )

    assert handled is True
    assert calls == ["03-timer-standby"]
    assert reasons == ["monitor_timeout"]


def test_monitor_terminal_executor_persists_sensor_reason_when_standby_fails(monkeypatch) -> None:
    reasons: list[str] = []
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_03_settings_profile_with_db",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("standby failed")),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._persist_03_monitor_stop_reason",
        lambda _meta, reason: reasons.append(reason) or True,
    )

    with pytest.raises(RuntimeError, match="standby failed"):
        _execute_monitor_terminal_transition(
            {},
            ChargeTransition(
                ChargeState.STOPPING,
                (ChargeEffect.SET_STANDBY,),
                "sensor_failure_limit",
                ChargeState.FAILED_SENSOR,
            ),
        )

    assert reasons == ["soc_unavailable_fail_safe"]


def test_monitor_stops_safely_after_consecutive_soc_failures(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, bool]] = []
    reasons: list[str] = []
    readings = iter(
        [
            SocReading(None, "unavailable", "offline", None),
            SocReading(None, "unavailable", "offline", None),
            SocReading(None, "unavailable", "offline", None),
        ]
    )
    monkeypatch.setenv("ADJUST03_MAX_CONSECUTIVE_SOC_FAILURES", "2")
    monkeypatch.setenv("ADJUST03_ALLOW_FORCED_START_WITHOUT_SOC", "true")
    monkeypatch.setattr(
        "app.runtime.cloud_job._read_plan_meta",
        lambda _: {
            "date": "2026-07-14",
            "required_night_charge_kwh": 1.0,
            "target_soc_7_percent": 80.0,
            "effective_capacity_kwh": 10.0,
        },
    )
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr("app.runtime.cloud_job._read_soc_with_fallback", lambda _: next(readings))
    monkeypatch.setattr("app.runtime.cloud_job._seconds_until_cutoff", lambda **kwargs: 3600)
    monkeypatch.setattr("app.runtime.cloud_job.time.time", lambda: 0.0)
    monkeypatch.setattr("app.runtime.cloud_job.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_settings_profile",
        lambda *, profile, dynamic_forced_profile: calls.append((profile, dynamic_forced_profile)),
    )
    monkeypatch.setattr("app.runtime.cloud_job._run_db_pipeline_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.runtime.cloud_job._persist_03_monitor_schedule_to_firestore", lambda **kwargs: True)
    monkeypatch.setattr("app.runtime.cloud_job._persist_night_soc_execution", lambda *_, **__: True)
    monkeypatch.setattr(
        "app.runtime.cloud_job._persist_03_monitor_stop_reason",
        lambda _plan, reason: reasons.append(reason) or True,
    )

    _monitor_partial_forced_and_stop(plan_path)

    assert calls == [("forced", True), ("standby", False)]
    assert reasons == ["soc_unavailable_fail_safe"]


def test_monitor_exception_after_forced_start_attempts_fail_safe_standby(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    profiles: list[str] = []
    stop_reasons: list[str] = []
    readings = iter([SocReading(20.0, "realtime", None, None), RuntimeError("sensor transport failed")])

    monkeypatch.setattr(
        "app.runtime.cloud_job._read_plan_meta",
        lambda _: {
            "required_night_charge_kwh": 1.0,
            "target_soc_7_percent": 80.0,
            "effective_capacity_kwh": 10.0,
        },
    )
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _: [])

    def read_soc(_paths):
        value = next(readings)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("app.runtime.cloud_job._read_soc_with_fallback", read_soc)
    monkeypatch.setattr("app.runtime.cloud_job._seconds_until_cutoff", lambda **kwargs: 3600)
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_03_settings_profile_with_db",
        lambda *, profile, dynamic_forced_profile, label: profiles.append(profile),
    )
    monkeypatch.setattr("app.runtime.cloud_job._persist_03_monitor_schedule_to_firestore", lambda **kwargs: True)
    monkeypatch.setattr(
        "app.runtime.cloud_job._persist_03_monitor_stop_reason",
        lambda _meta, reason, **kwargs: stop_reasons.append(reason) or True,
    )

    with pytest.raises(RuntimeError, match="sensor transport failed"):
        _monitor_partial_forced_and_stop(plan_path)

    assert profiles == ["forced", "standby"]
    assert stop_reasons[-1] == "monitor_exception_fail_safe"


def test_forced_start_failure_attempts_fail_safe_standby(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    profiles: list[str] = []

    monkeypatch.setattr(
        "app.runtime.cloud_job._read_plan_meta",
        lambda _: {
            "required_night_charge_kwh": 1.0,
            "target_soc_7_percent": 80.0,
            "effective_capacity_kwh": 10.0,
        },
    )
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr(
        "app.runtime.cloud_job._read_soc_with_fallback",
        lambda _paths: SocReading(20.0, "realtime", None, None),
    )
    monkeypatch.setattr("app.runtime.cloud_job._seconds_until_cutoff", lambda **kwargs: 3600)
    monkeypatch.setattr("app.runtime.cloud_job._persist_03_monitor_schedule_to_firestore", lambda **kwargs: True)

    def apply_profile(*, profile, dynamic_forced_profile, label):
        profiles.append(profile)
        if profile == "forced":
            raise RuntimeError("write confirmation failed")

    monkeypatch.setattr("app.runtime.cloud_job._run_03_settings_profile_with_db", apply_profile)
    monkeypatch.setattr("app.runtime.cloud_job._persist_03_monitor_stop_reason", lambda *args, **kwargs: True)

    with pytest.raises(RuntimeError, match="write confirmation failed"):
        _monitor_partial_forced_and_stop(plan_path)

    assert profiles == ["forced", "standby"]


def test_forced_reapply_failure_attempts_fail_safe_standby(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    profiles: list[str] = []
    stop_reasons: list[str] = []

    monkeypatch.setenv("ADJUST03_FORCE_REAPPLY_AFTER_POLLS", "1")
    monkeypatch.setattr(
        "app.runtime.cloud_job._read_plan_meta",
        lambda _: {
            "required_night_charge_kwh": 1.0,
            "target_soc_7_percent": 80.0,
            "effective_capacity_kwh": 10.0,
        },
    )
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr(
        "app.runtime.cloud_job._read_soc_with_fallback",
        lambda _paths: SocReading(20.0, "realtime", None, None),
    )
    monkeypatch.setattr("app.runtime.cloud_job._seconds_until_cutoff", lambda **kwargs: 3600)
    monkeypatch.setattr("app.runtime.cloud_job._persist_03_monitor_schedule_to_firestore", lambda **kwargs: True)
    monkeypatch.setattr(
        "app.runtime.cloud_job._persist_03_monitor_stop_reason",
        lambda _meta, reason, **kwargs: stop_reasons.append(reason) or True,
    )

    forced_calls = 0

    def apply_profile(*, profile, dynamic_forced_profile, label):
        nonlocal forced_calls
        profiles.append(profile)
        if profile == "forced":
            forced_calls += 1
            if forced_calls == 2:
                raise RuntimeError("reapply confirmation failed")

    monkeypatch.setattr("app.runtime.cloud_job._run_03_settings_profile_with_db", apply_profile)

    with pytest.raises(RuntimeError, match="reapply confirmation failed"):
        _monitor_partial_forced_and_stop(plan_path)

    assert profiles == ["forced", "forced", "standby"]
    assert stop_reasons[-1] == "forced_reapply_failed_fail_safe"


def test_monitor_keeps_standby_when_initial_soc_is_unavailable(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, bool]] = []
    persisted: list[tuple[str, SocReading | None]] = []
    reading = SocReading(None, "unavailable", "realtime offline; CSV SOC unavailable", None)
    monkeypatch.delenv("ADJUST03_ALLOW_FORCED_START_WITHOUT_SOC", raising=False)
    monkeypatch.setattr(
        "app.runtime.cloud_job._read_plan_meta",
        lambda _: {
            "date": "2026-07-14",
            "required_night_charge_kwh": 1.0,
            "target_soc_7_percent": 80.0,
            "effective_capacity_kwh": 10.0,
        },
    )
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr("app.runtime.cloud_job._persist_night_soc_execution", lambda *_, **__: True)
    monkeypatch.setattr("app.runtime.cloud_job._read_soc_with_fallback", lambda _: reading)
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_settings_profile",
        lambda *, profile, dynamic_forced_profile: calls.append((profile, dynamic_forced_profile)),
    )
    monkeypatch.setattr("app.runtime.cloud_job._run_db_pipeline_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "app.runtime.cloud_job._persist_03_monitor_stop_reason",
        lambda _plan, reason, *, soc_reading=None: persisted.append((reason, soc_reading)) or True,
    )

    _monitor_partial_forced_and_stop(plan_path)

    assert calls == [("standby", False)]
    assert persisted == [("initial_soc_unavailable", reading)]


def test_initial_soc_unavailable_helper_applies_standby_before_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    reading = SocReading(None, "unavailable", "offline", None)

    class Device:
        def apply_profile(self, **kwargs) -> None:
            assert kwargs == {
                "profile": "standby",
                "dynamic_forced_profile": False,
                "label": "03-initial-soc-unavailable-standby",
            }
            events.append("standby")

    class Status:
        def persist_stop_reason(self, plan_meta, reason, *, soc_reading) -> None:
            assert plan_meta == {"date": "2026-07-14"}
            assert reason == "initial_soc_unavailable"
            assert soc_reading is reading
            events.append("persisted")

    monkeypatch.setattr("app.runtime.cloud_job._persist_night_soc_execution", lambda *_, **__: True)

    _keep_standby_when_initial_soc_is_unavailable(
        plan_meta={"date": "2026-07-14"}, device=Device(), status=Status(), soc_reading=reading
    )

    assert events == ["standby", "persisted"]


def test_run_night_23_only_applies_standby_mode(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    monkeypatch.delenv("NIGHT23_SETTINGS_PROFILE", raising=False)
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_settings_profile_with_retry",
        lambda *, profile, dynamic_forced_profile, label: calls.append((profile, dynamic_forced_profile)),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_csv_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("23:00 must not fetch CSV")),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("23:00 must not run forecasts")),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_db_pipeline_slot",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("23:00 must not run data pipeline")),
    )

    _run_night_23()

    assert calls == [("standby", False)]


def test_run_adjust_03_regenerates_missing_plan(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    calls: list[tuple[str, dict[str, str]]] = []
    persisted: list[str] = []
    feedback_targets: list[str] = []
    monitored: list[Path] = []

    def fake_run(command, env_updates=None):
        script = list(command)[-1]
        calls.append((script, dict(env_updates or {})))
        if script == "energy_model_main.py":
            plan_path.write_text(
                '{"forecast":{"date":"2026-05-27"},"result":{"target_soc_7_percent":80}}',
                encoding="utf-8",
            )

    monkeypatch.setenv("KP_NIGHT_PLAN_PATH", str(plan_path))
    monkeypatch.setattr("app.runtime.cloud_job._run", fake_run)
    monkeypatch.setattr("app.runtime.cloud_job._adjust03_target_date", lambda: "2026-05-27")
    monkeypatch.setattr("app.runtime.cloud_job._restore_night_plan_from_firestore", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "app.runtime.cloud_job._persist_night_plan_to_firestore",
        lambda _path, *, source: persisted.append(source) or True,
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._persist_previous_day_soc_feedback",
        lambda *, target_date, csv_paths: feedback_targets.append(target_date) or True,
    )
    monkeypatch.setattr("app.runtime.cloud_job._monitor_partial_forced_and_stop", lambda path: monitored.append(path))

    _run_adjust_03()

    assert ("kpnet_main.py", {"KP_WORKFLOW_MODE": "csv"}) in calls
    assert ("energy_model_main.py", {"FORECAST_DATE_OVERRIDE": "2026-05-27"}) in calls
    assert feedback_targets == ["2026-05-27"]
    assert persisted == ["adjust03-regenerated"]
    assert monitored == [plan_path]


def test_run_adjust_03_plan_refresh_skips_device_monitoring(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    calls: list[str] = []

    monkeypatch.setenv("KP_NIGHT_PLAN_PATH", str(plan_path))
    monkeypatch.setattr("app.runtime.cloud_job._run_csv_with_retry", lambda **_: calls.append("csv"))
    monkeypatch.setattr("app.runtime.cloud_job._latest_kpnet_csv_paths", lambda _: [])
    monkeypatch.setattr("app.runtime.cloud_job._persist_previous_day_soc_feedback", lambda **_: True)
    monkeypatch.setattr("app.runtime.cloud_job._ensure_night_plan_available", lambda _: calls.append("plan") or True)
    monkeypatch.setattr("app.runtime.cloud_job._run_db_pipeline_slot", lambda *_, **__: calls.append("db"))
    monkeypatch.setattr(
        "app.runtime.cloud_job._monitor_partial_forced_and_stop",
        lambda _: (_ for _ in ()).throw(AssertionError("plan refresh must not control the device")),
    )
    monkeypatch.setattr(
        "app.runtime.cloud_job._run_optional_04_exports_and_backups",
        lambda: (_ for _ in ()).throw(AssertionError("plan refresh must not run optional side effects")),
    )

    _run_adjust_03(plan_refresh_only=True)

    assert calls == ["csv", "plan", "db"]


def test_persist_03_monitor_schedule_records_dashboard_event(monkeypatch) -> None:
    writes: dict[tuple[str, str], dict] = {}

    class FakeDocument:
        def __init__(self, collection_name: str, document_id: str) -> None:
            self.collection_name = collection_name
            self.document_id = document_id

        def set(self, payload: dict, merge: bool = False) -> None:
            writes[(self.collection_name, self.document_id)] = payload

    class FakeCollection:
        def __init__(self, collection_name: str) -> None:
            self.collection_name = collection_name

        def document(self, document_id: str) -> FakeDocument:
            return FakeDocument(self.collection_name, document_id)

    class FakeClient:
        def collection(self, collection_name: str) -> FakeCollection:
            return FakeCollection(collection_name)

    monkeypatch.setattr("app.runtime.cloud_job._open_firestore_for_plan", lambda: FakeClient())

    persisted = _persist_03_monitor_schedule_to_firestore(
        plan_meta={"date": "2026-06-03"},
        charge_start_time="02:43",
        charge_end_time="07:00",
        target_soc=79.0,
        latest_soc=0.0,
        required_kwh=7.68,
        estimated_charge_minutes=257,
        default_power_kw=1.8,
    )

    assert persisted is True
    event = writes[("settings_events", "2026-06-03-03-monitor-schedule")]
    assert event["slot"] == "03"
    assert event["status"] == "forced-started"
    assert event["detail_json"]["charge_start_time"] == "02:43"
    assert event["detail_json"]["charge_end_time"] == "07:00"
    assert writes[("night_charge_plans", "2026-06-03")]["monitor_schedule"]["schedule_source"] == "03-monitor"


def test_persist_03_no_charge_decision_records_completed_event(monkeypatch) -> None:
    writes: dict[tuple[str, str], dict] = {}

    class FakeDocument:
        def __init__(self, collection_name: str, document_id: str) -> None:
            self.collection_name = collection_name
            self.document_id = document_id

        def set(self, payload: dict, merge: bool = False) -> None:
            writes[(self.collection_name, self.document_id)] = payload

    class FakeCollection:
        def __init__(self, collection_name: str) -> None:
            self.collection_name = collection_name

        def document(self, document_id: str) -> FakeDocument:
            return FakeDocument(self.collection_name, document_id)

    class FakeClient:
        def collection(self, collection_name: str) -> FakeCollection:
            return FakeCollection(collection_name)

    monkeypatch.setattr("app.runtime.cloud_job._open_firestore_for_plan", lambda: FakeClient())

    persisted = _persist_03_no_charge_decision_to_firestore(
        plan_meta={"date": "2026-07-09"},
        target_soc=0.0,
        latest_soc=0.0,
        required_kwh=0.0,
    )

    assert persisted is True
    event = writes[("settings_events", "2026-07-09-03-no-charge")]
    assert event["status"] == "skipped-no-charge"
    assert event["detail_json"]["plan_date"] == "2026-07-09"
    assert event["detail_json"]["schedule_source"] == "03-no-charge"
