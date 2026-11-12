"""Reconcile one proven no-data failure; never rerun an import or touch active."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from app.operations.firestore import open_firestore

REQUIRED_CHECKS = {"release_prerequisites", "working_tree_clean", "git_diff_check", "env_is_ignored_and_unstaged",
                   "local_backup", "security_check", "production_validate_only", "pre_release_integration"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def eligible(row: dict[str, Any], completed_at: str) -> bool:
    return (row.get("status") == "failed" and row.get("stage") == "actual_save"
            and row.get("actuals_saved", "missing") is None
            and isinstance(row.get("completed_at"), str)
            and datetime.fromisoformat(row["completed_at"]) == datetime.fromisoformat(completed_at))


def verify_evidence(execution: str, receipt: dict[str, Any], native: dict[str, Any], logs: dict[str, Any]) -> None:
    status = native.get("status", {})
    terminal = [c for c in status.get("conditions", []) if c.get("type") == "Completed"]
    if (not re.fullmatch(r"solar-actual-import-[a-z0-9]+", execution)
            or native.get("metadata", {}).get("name") != execution
            or native.get("spec", {}).get("taskCount") != 1 or status.get("failedCount") != 1
            or len(terminal) != 1 or terminal[0].get("status") != "False"
            or terminal[0].get("reason") != "NonZeroExitCode" or not status.get("completionTime")):
        raise ValueError("exact execution has no proven failed terminal state")
    if logs.get("nextPageToken"):
        raise ValueError("log evidence exceeds its bounded read")
    matches = []
    for entry in logs.get("entries", []):
        labels = entry.get("labels")
        if not isinstance(labels, dict) or labels.get("run.googleapis.com/execution_name") != execution:
            continue
        text = entry.get("textPayload", "")
        if ("in save_date_actuals" in text
                and text.rstrip().endswith("ValueError: requested date has no actual measurements")):
            stamp = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
            completed = datetime.fromisoformat(receipt["completed_at"])
            if abs((stamp - completed).total_seconds()) <= 10:
                matches.append(entry)
    if len(matches) != 1:
        raise ValueError("one exact no-data traceback is required")


def read_evidence(execution: str, completed_at: str) -> tuple[dict[str, Any], dict[str, Any]]:
    project, region = os.environ["GCP_PROJECT_ID"], os.environ["GCP_REGION"]
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    factory: Any = AuthorizedSession
    with factory(credentials, max_refresh_attempts=0) as session:
        response = session.get(f"https://{region}-run.googleapis.com/apis/run.googleapis.com/v1/"
                               f"namespaces/{project}/executions/{execution}", timeout=20)
        response.raise_for_status()
        native = response.json()
        completed = datetime.fromisoformat(completed_at)
        if completed.utcoffset() is None:
            raise ValueError("failure evidence requires an aware completion time")
        start, end = ((completed + timedelta(seconds=offset)).isoformat() for offset in (-10, 10))
        response = session.post("https://logging.googleapis.com/v2/entries:list", timeout=20, json={
            "resourceNames": [f"projects/{project}"], "orderBy": "timestamp desc", "pageSize": 50,
            "filter": ('resource.type="cloud_run_job" AND labels."run.googleapis.com/execution_name"="' + execution
                       + '" AND severity>=ERROR AND timestamp>="' + start + '" AND timestamp<="' + end + '"')})
        response.raise_for_status()
        return native, response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "apply", "verify"), required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--target-date")
    parser.add_argument("--completed-at")
    parser.add_argument("--reviewed-sha")
    parser.add_argument("--preflight", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    state_path = args.state.resolve()
    if not state_path.is_relative_to(root / "artifacts/deployment_state"):
        raise ValueError("private state must stay below artifacts/deployment_state")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    client = open_firestore()
    collection = client.collection("dashboard_actual_import_runs")
    if args.stage == "prepare":
        if state_path.exists() or not args.target_date or not args.completed_at:
            raise ValueError("prepare requires a new state and an exact target/time")
        rows = list(collection.where(filter=FieldFilter("target_date", "==", args.target_date)).limit(21).stream(timeout=4, retry=None))
        candidates = [r for r in rows if r.id != "active" and eligible(r.to_dict() or {}, args.completed_at)]
        if len(rows) > 20 or len(candidates) != 1:
            raise ValueError("one bounded exact failed receipt is required")
        row = candidates[0]
        receipt = row.to_dict() or {}
        if not re.fullmatch(r"solar-actual-import-[a-z0-9]+", row.id):
            raise ValueError("invalid execution identity")
        native, logs = read_evidence(row.id, receipt["completed_at"])
        verify_evidence(row.id, receipt, native, logs)
        state = {"status": "prepared", "repository_commit": commit, "execution": row.id,
                 "receipt": receipt, "native": native, "logs": logs}
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        print(json.dumps({"status": "prepared", "reviewed_state_sha256": digest(state)}))
        return 0
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state["repository_commit"] != commit:
        raise ValueError("repair source commit changed")
    reference = collection.document(state["execution"])
    if args.stage == "apply":
        if state["status"] != "prepared" or digest(state) != args.reviewed_sha or args.preflight is None:
            raise ValueError("apply requires an unattempted reviewed state")
        preflight = json.loads(args.preflight.read_text(encoding="utf-8-sig"))
        checks = preflight.get("checks", [])
        if (preflight.get("kind") != "production_deployment_preflight"
                or preflight.get("repository_commit") != commit or preflight.get("status") != "passed"
                or not REQUIRED_CHECKS <= {c.get("name") for c in checks}
                or not checks or any(c.get("status") != "success" for c in checks)):
            raise ValueError("same-commit mandatory preflight is required")
        verify_evidence(state["execution"], state["receipt"], state["native"], state["logs"])
        state["status"] = "applying"
        state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        @firestore.transactional
        def reconcile(transaction: Any) -> None:
            current = reference.get(transaction=transaction, timeout=4, retry=None).to_dict() or {}
            if digest(current) != digest(state["receipt"]):
                raise ValueError("receipt changed after evidence capture")
            transaction.update(reference, {"actuals_saved": False, "reason": "verified_no_actual_measurements"})

        reconcile(client.transaction(max_attempts=1))
    current = reference.get(timeout=4, retry=None).to_dict() or {}
    expected = {**state["receipt"], "actuals_saved": False, "reason": "verified_no_actual_measurements"}
    if digest(current) != digest(expected):
        raise ValueError("reconciliation readback mismatch")
    state["status"] = "verified"
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": "verified", "receipt_updates": 1, "actual_data_writes": 0, "active_updates": 0}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
