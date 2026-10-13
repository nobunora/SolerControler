import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import summarize_production_validation as summary


@pytest.fixture
def evidence(tmp_path):
    proof = {"source_commit": "a" * 40, "runtime_plan_source_revision": "a" * 40,
             "fixture_sha256": "b" * 64, "image_digest": "sha256:" + "c" * 64,
             "backend_tier": "production_server", "stage": "all", "status": "PASS",
             "sdk_versions": {"google-auth": "2.60.0", "private_identity": "PRIVATE"},
             "cases": [{"name": f"case-{index}", "status": "PASS"} for index in range(12)]}
    proof_path = tmp_path / "proof.json"
    state = {"kind": "separated_runtime_deployment", "schema_version": 1,
             "repository_commit": "a" * 40, "adapter_fixture_sha256": "b" * 64,
             "image_digests": {"planner": "sha256:" + "c" * 64}, "status": "failed",
             "stages": {name: {"status": "not_started"} for name in summary.STAGES}}
    for name in ("preflight", "parity", "inventory", "build", "planner"):
        state["stages"][name]["status"] = "success"
    state["stages"]["calculation"]["status"] = "failed"
    state["stages"]["build"]["proof_path"] = str(proof_path)
    state_path = tmp_path / "state.json"
    def save():
        proof_path.write_text(json.dumps(proof), encoding="utf-8")
        state["stages"]["build"]["proof_sha256"] = hashlib.sha256(proof_path.read_bytes()).hexdigest()
        state_path.write_text(json.dumps(state), encoding="utf-8")
    save()
    return state_path, state, proof, proof_path, save


def test_native_sdk_pass_is_separate_from_incomplete_deployment_and_device_test(evidence):
    path, state, proof, proof_path, save = evidence
    before = path.read_bytes(), proof_path.read_bytes()
    result = summary.summarize(path)
    assert result["deployment"] == "FAIL" and result["server_sdk"]["status"] == "PASS"
    assert result["server_sdk"]["cases_passed"] == result["server_sdk"]["cases_total"] == 12
    assert result["stages"]["control"] == result["stages"]["web"] == "NOT_RUN"
    assert result["soc_setting_test"] == result["browser_test"] == "NOT_EVALUATED"
    assert result["cloud_writes"] == 0 and "PRIVATE" not in json.dumps(result)
    assert before == (path.read_bytes(), proof_path.read_bytes())


@pytest.mark.parametrize("defect", ["file_sha", "source_commit", "fixture_sha256",
                                    "image_digest", "backend_tier", "stage", "case_failed", "case_duplicate", "case_missing",
                                    "empty_identity"])
def test_unmatched_native_proof_never_passes(evidence, defect):
    path, state, proof, proof_path, save = evidence
    if defect == "file_sha":
        proof_path.write_text(proof_path.read_text() + " ")
    elif defect == "case_failed":
        proof["cases"][0]["status"] = "FAIL"
        save()
    elif defect == "case_duplicate":
        proof["cases"][0]["name"] = proof["cases"][1]["name"]
        save()
    elif defect == "case_missing":
        proof["cases"] = []
        save()
    elif defect == "empty_identity":
        state.pop("adapter_fixture_sha256")
        proof.pop("fixture_sha256")
        save()
    else:
        proof[defect] = "mismatch"
        save()
    assert summary.summarize(path)["server_sdk"]["status"] == "FAIL"


def test_complete_requires_every_stage_and_native_receipt(evidence):
    path, state, proof, proof_path, save = evidence
    state["status"] = "complete"
    save()
    assert summary.summarize(path)["deployment"] == "INCONCLUSIVE"
    for stage in state["stages"].values():
        stage["status"] = "success"
    save()
    assert summary.summarize(path)["deployment"] == "PASS"
    completed = subprocess.run([sys.executable, str(Path(summary.__file__)), "--state", str(path)],
                               capture_output=True, text=True)
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["server_sdk"]["status"] == "PASS"


def test_stale_runtime_revision_is_reported_and_blocks_overall_success(evidence):
    path, state, proof, proof_path, save = evidence
    proof["runtime_plan_source_revision"] = "d" * 40
    state["status"] = "complete"
    for stage in state["stages"].values():
        stage["status"] = "success"
    save()
    result = summary.summarize(path)
    assert result["server_sdk"]["status"] == "PASS"
    assert result["server_sdk"]["runtime_revision_matches"] is False
    completed = subprocess.run([sys.executable, str(Path(summary.__file__)), "--state", str(path)],
                               capture_output=True, text=True)
    assert completed.returncode == 1


def test_not_run_preflight_and_missing_receipt_are_not_server_sdk_pass(evidence):
    path, state, proof, proof_path, save = evidence
    state["stages"]["build"].pop("proof_path")
    state["stages"]["build"]["status"] = "not_started"
    save()
    assert summary.summarize(path)["server_sdk"]["status"] == "NOT_RUN"
    state["stages"]["build"]["status"] = "success"
    save()
    assert summary.summarize(path)["server_sdk"]["status"] == "INCONCLUSIVE"


def test_failed_repair_receipt_is_reported_without_private_values(evidence, tmp_path):
    path, state, proof, proof_path, save = evidence
    repair = tmp_path / "repair.json"
    journal = Path(str(repair) + ".d") / "journal.json"
    journal.parent.mkdir()
    journal.write_text(json.dumps({"stages": {"history": {"status": "verified", "secret": "PRIVATE"},
                                             "verify": {"status": "failed", "path": "PRIVATE"}}}))
    state["monitoring_repair_state_path"] = str(repair)
    save()
    result = summary.summarize(path)
    assert result["repair_stages"] == {"history": "PASS", "verify": "FAIL"}
    assert result["failed_repair_stages"] == ["verify"] and "PRIVATE" not in json.dumps(result)


def test_bad_or_missing_evidence_fails_safely_without_messages(tmp_path):
    path = tmp_path / "PRIVATE.json"
    for content in (None, "invalid PRIVATE", json.dumps({"kind": "wrong", "secret": "PRIVATE"})):
        if content is not None:
            path.write_text(content)
        completed = subprocess.run([sys.executable, str(Path(summary.__file__)), "--state", str(path)],
                                   capture_output=True, text=True)
        assert completed.returncode == 2 and not completed.stderr
        assert "PRIVATE" not in completed.stdout
        assert json.loads(completed.stdout)["deployment"] == "INCONCLUSIVE"
