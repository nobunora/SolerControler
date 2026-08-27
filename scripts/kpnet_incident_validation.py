"""Validate the manual hand-off path and, explicitly, the live KP-NET write path.

The manual hand-off stage is intentionally an in-memory simulation.  It cannot
write a production plan or Firestore document.  The live stage delegates to
the protected reversible settings round-trip, which snapshots every controlled
setting, writes a 50% forced-charge candidate, holds it for exactly 60 seconds,
and restores/read-backs the snapshot.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from tempfile import TemporaryDirectory
from unittest.mock import patch
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.kpnet.profiles import GREEN_MODE_PROFILE
from app.kpnet.settings_roundtrip import ROUNDTRIP_SETTING_FIELDS, run_settings_roundtrip
import app.runtime.cloud_job as cloud_job
import app.runtime.slot_orchestration as slot_orchestration
from app.runtime.plan_persistence import (
    can_apply_day_transition,
    persist_night_soc_execution,
)


class _MemorySnapshot:
    def __init__(self, values: dict[str, Any] | None) -> None:
        self.exists = values is not None
        self._values = values or {}

    def to_dict(self) -> dict[str, Any]:
        return dict(self._values)


class _MemoryDocument:
    def __init__(self, store: dict[str, dict[str, Any]], key: str) -> None:
        self._store = store
        self._key = key

    def get(self) -> _MemorySnapshot:
        return _MemorySnapshot(self._store.get(self._key))

    def set(self, values: dict[str, Any], merge: bool = False) -> None:
        if merge:
            current = self._store.setdefault(self._key, {})
            current.update(values)
        else:
            self._store[self._key] = dict(values)


class _MemoryCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, key: str) -> _MemoryDocument:
        return _MemoryDocument(self._store, key)


class _MemoryFirestore:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> _MemoryCollection:
        return _MemoryCollection(self.collections.setdefault(name, {}))


def run_manual_handoff_validation() -> dict[str, object]:
    """Exercise MANUAL_OPERATION -> 07 gate -> green selection without I/O."""
    store = _MemoryFirestore()
    plan_meta = {
        "date": "2099-01-01",
        "plan_id": "2099-01-01-1-dummy-manual-handoff-validation",
        "target_soc_7_percent": 80.0,
    }
    opener = lambda: store
    persisted = persist_night_soc_execution(
        plan_meta=plan_meta,
        state="MANUAL_OPERATION",
        owner="manual",
        open_firestore=opener,
        device_write_skipped=True,
    )
    gate_allows_manual = can_apply_day_transition(
        plan_date=str(plan_meta["date"]),
        open_firestore=opener,
        allow_manual_owner=True,
    )
    gate_rejects_implicit_manual = not can_apply_day_transition(
        plan_date=str(plan_meta["date"]),
        open_firestore=opener,
        allow_manual_owner=False,
    )
    green_path = {
        "profile": GREEN_MODE_PROFILE.name,
        "battery_operating_mode": GREEN_MODE_PROFILE.battery_operating_mode,
        "charge_start_hhmm": f"{int(GREEN_MODE_PROFILE.charge_start_h):02d}:{int(GREEN_MODE_PROFILE.charge_start_m):02d}",
        "discharge_start_hhmm": f"{int(GREEN_MODE_PROFILE.discharge_start_h):02d}:{int(GREEN_MODE_PROFILE.discharge_start_m):02d}",
    }
    settings_calls: list[dict[str, object]] = []
    with patch.dict(
        "os.environ",
        {
            "NIGHT_SOC_CONTROL_MODE": "enforce",
            "NIGHT_SOC_MANUAL_OPERATION": "true",
            "DRY_RUN": "false",
            "KP_NIGHT_PLAN_PATH": "artifacts/night_charge_plan.json",
        },
        clear=False,
    ), patch.object(cloud_job, "_adjust03_target_date", lambda: str(plan_meta["date"])), patch.object(
        cloud_job, "_open_firestore_for_plan", lambda: store
    ), patch.object(cloud_job, "_read_plan_meta", lambda _path: plan_meta), patch.object(
        cloud_job,
        "_run_settings_profile_with_retry",
        lambda **kwargs: settings_calls.append(kwargs),
    ):
        getattr(cloud_job, "_run_day_07")()
    green_writer_called = settings_calls == [
        {"profile": "green", "dynamic_forced_profile": False, "label": "07-green"}
    ]
    passed = (
        persisted
        and gate_allows_manual
        and gate_rejects_implicit_manual
        and green_path["profile"] == "green-mode"
        and green_writer_called
    )
    return {
        "status": "passed" if passed else "failed",
        "storage": "in-memory-only",
        "production_records_written": False,
        "dummy_plan_date": plan_meta["date"],
        "handoff_state": "MANUAL_OPERATION",
        "persisted": persisted,
        "gate_allows_explicit_manual": gate_allows_manual,
        "gate_rejects_implicit_manual": gate_rejects_implicit_manual,
        "green_path": green_path,
        "green_writer_called": green_writer_called,
    }


def run_scheduled_auto_path_validation() -> dict[str, object]:
    """Replay the normal 23:00 -> 03:00 -> 07:00 path without external I/O.

    HISTORICAL_FAILURE_LOCK (1dd21ae, 2026-08-28 incident evidence): a live,
    reversible settings round-trip proves only mutation/read-back/restore.  It
    cannot prove the scheduler selected the automatic 23:00 standby and 03:00
    monitor routes.  When the production manual default was true, that missing
    coverage allowed plan=100%, actual=0%, charge=0kWh.  This replay is a
    mandatory release-validation stage: do not remove it, downgrade a failure
    to warning, or run the live round-trip first.  It must prove 23->03->07,
    exactly one monitor call, a durable terminal state, and green only after
    that state.  Separate monitor/read-back and real-device round-trip tests
    cover the external boundaries that this no-I/O replay deliberately stubs.
    """
    store = _MemoryFirestore()
    plan_meta = {
        "date": "2099-01-01",
        "plan_id": "2099-01-01-1-dummy-scheduled-path-validation",
        "target_soc_7_percent": 100.0,
    }
    settings_calls: list[dict[str, object]] = []
    monitor_calls: list[Path] = []
    terminal_states: list[str] = []

    def complete_monitor(plan_path: Path) -> None:
        monitor_calls.append(plan_path)
        persisted = persist_night_soc_execution(
            plan_meta=plan_meta,
            state="STANDBY_ACKED",
            owner="03-monitor",
            open_firestore=lambda: store,
        )
        if not persisted:
            raise RuntimeError("scheduled auto-path terminal state could not be persisted")
        terminal_states.append("STANDBY_ACKED")

    with TemporaryDirectory(prefix="kpnet-scheduled-auto-path-") as temp_dir:
        plan_path = Path(temp_dir) / "night_charge_plan.json"
        plan_path.write_text("{}", encoding="utf-8")
        with patch.dict(
            "os.environ",
            {
                "NIGHT_SOC_MANUAL_OPERATION": "false",
                "NIGHT_SOC_CONTROL_MODE": "enforce",
                "DRY_RUN": "false",
                "KP_NIGHT_PLAN_PATH": str(plan_path),
                "ADJUST03_REGENERATE_PLAN": "false",
            },
            clear=False,
        ), patch.object(cloud_job, "_open_firestore_for_plan", lambda: store), patch.object(
            cloud_job, "_adjust03_target_date", lambda: str(plan_meta["date"])
        ), patch.object(cloud_job, "_read_plan_meta", lambda _path: plan_meta), patch.object(
            cloud_job,
            "_run_settings_profile_with_retry",
            lambda **kwargs: settings_calls.append(kwargs),
        ), patch.object(cloud_job, "_run_csv_with_retry", lambda **_kwargs: None), patch.object(
            cloud_job, "_persist_previous_day_soc_feedback", lambda **_kwargs: None
        ), patch.object(cloud_job, "_latest_kpnet_csv_paths", lambda _path: []), patch.object(
            cloud_job, "_ensure_night_plan_available", lambda _path: True
        ), patch.object(cloud_job, "_run_db_pipeline_slot", lambda *_args, **_kwargs: None), patch.object(
            cloud_job, "_monitor_partial_forced_and_stop", complete_monitor
        ), patch(
            "app.runtime.slot_orchestration._run_optional_04_exports_and_backups", lambda: None
        ):
            slot_orchestration._run_night_23()
            slot_orchestration._run_adjust_03()
            slot_orchestration._run_day_07()

    expected_settings_calls = [
        {"profile": "standby", "dynamic_forced_profile": False, "label": "23-settings-standby"},
        {"profile": "green", "dynamic_forced_profile": False, "label": "07-green"},
    ]
    passed = (
        settings_calls == expected_settings_calls
        and len(monitor_calls) == 1
        and terminal_states == ["STANDBY_ACKED"]
    )
    return {
        "status": "passed" if passed else "failed",
        "storage": "in-memory-only",
        "production_records_written": False,
        "dummy_plan_date": plan_meta["date"],
        "manual_operation_enabled": False,
        "monitor_call_count": len(monitor_calls),
        "terminal_state": terminal_states[-1] if terminal_states else None,
        "settings_calls": settings_calls,
    }


def _write_result(path: Path, result: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-soc", type=float, default=50.0)
    parser.add_argument("--test-charge-start")
    parser.add_argument("--test-charge-end")
    parser.add_argument("--result-path", type=Path)
    parser.add_argument("--test-execution", action="store_true")
    args = parser.parse_args()
    if not args.test_execution:
        raise RuntimeError("refusing live setting mutation without --test-execution")
    if args.target_soc != 50.0:
        raise ValueError("incident validation only permits the device maximum SocChargeMode target of 50")

    result: dict[str, object] = {
        "schema_version": 1,
        "started_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "failed",
        "target_soc_percent": args.target_soc,
        "hold_seconds": 60,
        "snapshot_fields": list(ROUNDTRIP_SETTING_FIELDS),
        "snapshot_field_count": len(ROUNDTRIP_SETTING_FIELDS),
    }
    handoff = run_manual_handoff_validation()
    result["manual_handoff"] = handoff
    if handoff["status"] != "passed":
        result["failure_stage"] = "manual_handoff"
    else:
        scheduled_auto_path = run_scheduled_auto_path_validation()
        result["scheduled_auto_path"] = scheduled_auto_path
        if scheduled_auto_path["status"] != "passed":
            result["failure_stage"] = "scheduled_auto_path"
        else:
            try:
                live = run_settings_roundtrip(
                    target_soc_percent=args.target_soc,
                    test_charge_start_hhmm=args.test_charge_start,
                    test_charge_end_hhmm=args.test_charge_end,
                )
                result["live_device_roundtrip"] = live
                result["status"] = "passed" if live.get("status") == "passed" else "failed"
                if result["status"] != "passed":
                    result["failure_stage"] = "live_device_roundtrip"
            except Exception as exc:
                live_failure = {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                }
                summary = getattr(exc, "summary", None)
                if isinstance(summary, dict):
                    live_failure.update(summary)
                result["live_device_roundtrip"] = live_failure
                result["failure_stage"] = "live_device_roundtrip"
    result["completed_at_utc"] = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    output_path = args.result_path or Path("artifacts/validation/kpnet-incident-validation.json")
    _write_result(output_path, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
