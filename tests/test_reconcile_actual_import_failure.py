import copy
import json
from types import SimpleNamespace

import pytest

from scripts import reconcile_actual_import_failure as repair

EXECUTION = "solar-actual-import-test1"
STAMP = "2026-11-10T15:11:17+00:00"
COMMIT = "a" * 40


def test_evidence_reads_are_exact_and_bounded(monkeypatch):
    _, native, logs = evidence()
    calls = []

    class Session:
        def __init__(self, credentials, **kwargs):
            assert kwargs == {"max_refresh_attempts": 0}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: native)

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: logs)

    monkeypatch.setenv("GCP_PROJECT_ID", "example")
    monkeypatch.setenv("GCP_REGION", "asia-northeast1")
    monkeypatch.setattr(repair.google.auth, "default", lambda **_: (object(), None))
    monkeypatch.setattr(repair, "AuthorizedSession", Session)
    assert repair.read_evidence(EXECUTION, STAMP) == (native, logs)
    assert len(calls) == 2
    query = calls[1][1]["json"]
    assert query["pageSize"] == 50
    assert 'severity>=ERROR' in query["filter"]
    assert 'timestamp>="2026-11-10T15:11:07+00:00"' in query["filter"]
    assert 'timestamp<="2026-11-10T15:11:27+00:00"' in query["filter"]


def evidence():
    receipt = {"status": "failed", "stage": "actual_save", "actuals_saved": None,
               "target_date": "2026-11-11", "completed_at": STAMP}
    native = {"metadata": {"name": EXECUTION}, "spec": {"taskCount": 1}, "status": {
        "failedCount": 1, "completionTime": STAMP,
        "conditions": [{"type": "Completed", "status": "False", "reason": "NonZeroExitCode"}]}}
    logs = {"entries": [{"labels": None}, {"labels": {"run.googleapis.com/execution_name": EXECUTION},
        "timestamp": STAMP, "textPayload": "in save_date_actuals\nValueError: requested date has no actual measurements"}]}
    return receipt, native, logs


def test_only_exact_terminal_no_data_failure_is_eligible():
    receipt, native, logs = evidence()
    assert repair.eligible(receipt, STAMP)
    repair.verify_evidence(EXECUTION, receipt, native, logs)
    for field, value in [("actuals_saved", False), ("stage", "display_publication"), ("status", "running")]:
        assert not repair.eligible({**receipt, field: value}, STAMP)
    for broken in (dict(native, metadata={"name": "other"}), dict(native, status={})):
        with pytest.raises(ValueError):
            repair.verify_evidence(EXECUTION, receipt, broken, logs)
    for broken in ({"entries": []}, {**logs, "nextPageToken": "more"}, {"entries": logs["entries"] * 2}):
        with pytest.raises(ValueError):
            repair.verify_evidence(EXECUTION, receipt, native, broken)
    wrong = copy.deepcopy(logs)
    wrong["entries"][1]["textPayload"] = "in save_date_actuals\nTimeoutError: write outcome unknown"
    with pytest.raises(ValueError):
        repair.verify_evidence(EXECUTION, receipt, native, wrong)


@pytest.mark.parametrize("scenario", ["success", "changed_receipt", "commit_timeout", "incomplete_gate"])
def test_one_receipt_cas_unknown_outcome_verify_and_no_active_write(monkeypatch, tmp_path, scenario):
    root = tmp_path / "repo"
    folder = root / "artifacts/deployment_state"
    folder.mkdir(parents=True)
    monkeypatch.setattr(repair, "__file__", str(root / "scripts/repair.py"))
    monkeypatch.setattr(repair.subprocess, "check_output", lambda *_a, **_k: COMMIT)
    receipt, native, logs = evidence()
    state = {"status": "prepared", "repository_commit": COMMIT, "execution": EXECUTION,
             "receipt": receipt, "native": native, "logs": logs}
    state_path = folder / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    preflight = {"kind": "production_deployment_preflight", "repository_commit": COMMIT, "status": "passed",
                 "checks": [{"name": name, "status": "success"} for name in repair.REQUIRED_CHECKS]}
    if scenario == "incomplete_gate":
        preflight["checks"].pop()
    preflight_path = folder / "gate.json"
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")
    current = copy.deepcopy(receipt)
    if scenario == "changed_receipt":
        current["reason"] = "changed"
    updates = []
    reference = SimpleNamespace(get=lambda **_: SimpleNamespace(to_dict=lambda: current))

    def document(name):
        assert name == EXECUTION  # The shared active receipt must never be touched.
        return reference

    def update(ref, fields):
        assert ref is reference
        updates.append(fields)
        current.update(fields)
        if scenario == "commit_timeout":
            raise TimeoutError("outcome unknown after commit")

    transaction = SimpleNamespace(update=update)
    client = SimpleNamespace(collection=lambda name: SimpleNamespace(document=document),
                             transaction=lambda **_: transaction)
    monkeypatch.setattr(repair, "open_firestore", lambda: client)
    monkeypatch.setattr(repair.firestore, "transactional", lambda f: f)
    arguments = ["repair", "--stage", "apply", "--state", str(state_path), "--reviewed-sha", repair.digest(state),
                 "--preflight", str(preflight_path)]
    monkeypatch.setattr(repair.sys, "argv", arguments)
    if scenario in ("changed_receipt", "incomplete_gate"):
        with pytest.raises(ValueError):
            repair.main()
        assert updates == []
    elif scenario == "commit_timeout":
        with pytest.raises(TimeoutError):
            repair.main()
        with pytest.raises(ValueError, match="unattempted"):
            repair.main()
        monkeypatch.setattr(repair.sys, "argv", ["repair", "--stage", "verify", "--state", str(state_path)])
        assert repair.main() == 0
        assert len(updates) == 1
    else:
        assert repair.main() == 0
        assert updates == [{"actuals_saved": False, "reason": "verified_no_actual_measurements"}]
