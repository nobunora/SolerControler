from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def recovery():
    path = Path(__file__).resolve().parents[1] / "scripts/local_recovery.py"
    spec = importlib.util.spec_from_file_location("local_recovery_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(path: Path, value: bytes = b"new", *, complete: bool = True) -> None:
    path.mkdir(parents=True)
    (path / "data").write_bytes(value)
    stages = {name: {"status": "success", "result": {}} for name in
              ("local", "firestore", "firestore_settings", "storage", "configuration", "secrets", "drive", "logs", "registry")}
    stages["firestore"]["result"]["read_time"] = "2026-11-08T00:00:00Z"
    manifest = {"status": "complete" if complete else "incomplete", "gaps": [], "stages": stages,
                "files": {"data": {"sha256": hashlib.sha256(value).hexdigest()}}}
    (path / "manifest.private.json").write_text(json.dumps(manifest))
    (path / "summary.safe.json").write_text(json.dumps({"status": manifest["status"], "gaps": []}))


def test_success_replaces_latest_and_retains_exactly_one_complete_copy(recovery, tmp_path):
    root = recovery.owned_root(tmp_path)
    snapshot(root / "latest", b"old")
    snapshot(root / "refresh.pending")
    assert recovery.promote(root)["retained_complete_backups"] == 1
    assert (root / "latest/data").read_bytes() == b"new"
    assert not (root / "latest.previous").exists()
    assert not (root / "refresh.pending").exists()


@pytest.mark.parametrize("failure", ["corrupt", "incomplete", "missing_stage", "path_escape"])
def test_failed_candidate_never_replaces_latest(recovery, tmp_path, failure):
    root = recovery.owned_root(tmp_path)
    snapshot(root / "latest", b"old")
    candidate = root / "refresh.pending"
    snapshot(candidate)
    manifest_path = candidate / "manifest.private.json"
    manifest = json.loads(manifest_path.read_text())
    if failure == "corrupt":
        (candidate / "data").write_bytes(b"corrupt")
    elif failure == "incomplete":
        manifest["status"] = "incomplete"
    elif failure == "missing_stage":
        del manifest["stages"]["registry"]
    else:
        manifest["files"]["../outside"] = {"sha256": "unused"}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        recovery.promote(root)
    assert (root / "latest/data").read_bytes() == b"old"


def test_interrupted_rename_recovers_last_verified_copy(recovery, tmp_path):
    root = recovery.owned_root(tmp_path)
    snapshot(root / "latest.previous", b"old")
    snapshot(root / "refresh.pending", complete=False)
    recovery.reconcile_publication(root)
    assert (root / "latest/data").read_bytes() == b"old"
    assert not (root / "latest.previous").exists()


def test_unowned_directory_and_arbitrary_deletion_are_rejected(recovery, tmp_path):
    (tmp_path / "unrelated.txt").write_text("keep")
    with pytest.raises(ValueError, match="nonempty"):
        recovery.owned_root(tmp_path)
    with pytest.raises(ValueError, match="outside managed"):
        recovery.remove_owned(tmp_path, "../other")
    assert (tmp_path / "unrelated.txt").read_text() == "keep"


def test_local_environment_drops_all_production_credentials(recovery, tmp_path, monkeypatch):
    for key in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_API_KEY", "DASHBOARD_ACTUAL_IMPORT_JOB", "KP_MONITOR_PASSWORD", "GCP_PROJECT_ID"):
        monkeypatch.setenv(key, "must-not-escape")
    env = recovery.local_environment(tmp_path, 8781, 8780)
    assert "must-not-escape" not in env.values()
    assert env["FIRESTORE_EMULATOR_HOST"] == "127.0.0.1:8781"
    assert env["GOOGLE_CLOUD_PROJECT"].startswith("demo-")
    assert env["DASHBOARD_ACTUAL_IMPORT_JOB"] == ""
    assert env["DATA_BACKEND"] == "firestore"


def test_reference_remapping_preserves_values_and_nested_types(recovery):
    source = {"mapValue": {"fields": {
        "ref": {"referenceValue": "projects/original/databases/(default)/documents/a/b"},
        "literal": {"stringValue": "projects/original/databases/(default)/documents/a/b"},
        "large": {"integerValue": "9007199254740993"},
        "bytes": {"bytesValue": "AP8="},
        "null": {"nullValue": None},
    }}}
    mapped = recovery.remap_references(source, "original")
    fields = mapped["mapValue"]["fields"]
    assert fields["ref"]["referenceValue"].startswith("projects/demo-")
    assert fields["literal"] == source["mapValue"]["fields"]["literal"]
    assert fields["large"]["integerValue"] == "9007199254740993"
    assert recovery.canonical_document({"x": source}) != recovery.canonical_document({"x": mapped})


def test_partial_preview_never_becomes_a_complete_backup(recovery, tmp_path):
    root = recovery.owned_root(tmp_path)
    candidate = root / "refresh.pending"
    snapshot(candidate, complete=False)
    path = candidate / "manifest.private.json"
    manifest = json.loads(path.read_text())
    digest = hashlib.sha256(b"new").hexdigest()
    for stage in manifest["stages"].values():
        stage["files"] = {"data": digest}
    manifest["stages"]["drive"]["status"] = "partial"
    path.write_text(json.dumps(manifest))
    assert recovery.verify_preview(candidate)["status"] == "incomplete"
    with pytest.raises(ValueError, match="Incomplete"):
        recovery.promote(root)
    assert not (root / "latest").exists()
    (candidate / "data").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        recovery.verify_preview(candidate)


def test_drive_exclusion_requires_both_scope_and_explicit_receipt(recovery, tmp_path):
    path = tmp_path / "capture"
    snapshot(path)
    manifest_path = path / "manifest.private.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["stages"]["drive"] = {"status": "excluded", "reason": "user_scope_exclusion"}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        recovery.verify_snapshot(path)
    manifest["selection_policy"] = {"drive": "excluded_by_user"}
    manifest_path.write_text(json.dumps(manifest))
    assert recovery.verify_snapshot(path)["status"] == "complete"
    del manifest["stages"]["drive"]["reason"]
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Drive exclusion"):
        recovery.verify_snapshot(path)
