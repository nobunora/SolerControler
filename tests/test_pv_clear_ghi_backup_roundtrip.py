from __future__ import annotations

import hashlib
import json

import pytest

from app.backup.drive import PLAN_BACKUP_COLLECTIONS, build_firestore_snapshot
from app.backup.plan_snapshot import archive_plan_original, project_plan_read_models, restore_snapshot_payload
from test_plan_snapshot_recovery import Firestore, Storage


def traced_original():
    pack = {"schema_version": 1, "model_id": "physical_clear_ghi_annual1", "basis_version": "basis-test",
            "site_fingerprint": "test-site", "theta4": [1, 0, 0, 0]}
    canonical = json.dumps(pack, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    trace = {"calibration_pack": pack, "calibration_sha256": hashlib.sha256(canonical).hexdigest(),
             "hourly": [{"hour": hour, "q_kwh_m2": 0.1, "c": 1, "f_kwh_m2": 0.1, "pv_kwh": 0.1} for hour in range(24)]}
    return json.dumps({"generated_at": "2026-10-10T18:00:00Z", "forecast": {"date": "2026-10-11"},
                       "result": {"target_soc_7_percent": 40}, "decision_rationale": {"pv_clear_ghi": trace}}, indent=2).encode()


def test_generated_archive_does_not_project_until_explicit_handoff_and_restores_exact_trace(tmp_path):
    raw = traced_original()
    storage, db = Storage(), Firestore()
    receipt, doc = archive_plan_original(raw, storage=storage, firestore=db, source="planner-generated", prefix="gs://test/plans")
    assert receipt["status"] == "verified"
    assert "night_charge_plans" not in db.collections
    assert archive_plan_original(raw, storage=storage, firestore=db, source="planner-generated", prefix="gs://test/plans") == (receipt, doc)
    project_plan_read_models(doc, raw, firestore=db)
    snapshot = build_firestore_snapshot(db, storage_client=storage)
    assert snapshot["schema_version"] == 2
    assert PLAN_BACKUP_COLLECTIONS == ("night_charge_plans", "night_plan_decisions", "forecast_plans", "forecast_hourly_snapshots")
    restored = restore_snapshot_payload(snapshot, tmp_path / "offline")
    assert [path.read_bytes() for path in restored] == [raw]
    trace = json.loads(restored[0].read_bytes())["decision_rationale"]["pv_clear_ghi"]
    pack_bytes = json.dumps(trace["calibration_pack"], sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    assert hashlib.sha256(pack_bytes).hexdigest() == trace["calibration_sha256"]
    assert len(trace["hourly"]) == 24
    assert "clear_ghi_calibration_current" not in snapshot["collections"]


@pytest.mark.parametrize("problem", ["tampered", "missing"])
def test_trace_detail_failure_never_partially_restores(tmp_path, problem):
    raw = traced_original()
    storage, db = Storage(), Firestore()
    _, doc = archive_plan_original(raw, storage=storage, firestore=db, source="planner-generated", prefix="gs://test/plans")
    project_plan_read_models(doc, raw, firestore=db)
    snapshot = build_firestore_snapshot(db, storage_client=storage)
    row = snapshot["collections"]["night_plan_decisions"][0]
    row["plan_json"] = row["plan_json"] + " " if problem == "tampered" else None
    with pytest.raises(ValueError, match="checksum|embedded"):
        restore_snapshot_payload(snapshot, tmp_path / "bad")
    assert not list((tmp_path / "bad").glob("*.json"))
