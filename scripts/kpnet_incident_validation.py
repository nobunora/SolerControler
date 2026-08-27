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
