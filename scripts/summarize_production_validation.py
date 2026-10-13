"""Read saved deployment evidence without cloud calls, secrets, or state mutations."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STAGES = ('preflight', 'parity', 'inventory', 'build', 'planner', 'calculation',
          'soc_api_credentials', 'control', 'web', 'smoke', 'drive_backup', 'verification')


def read_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Evidence file exceeds the bounded reader limit")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Evidence must be a JSON object")
    return value


def status(value: Any) -> str:
    return {"success": "PASS", "verified": "PASS", "passed": "PASS", "complete": "PASS",
            "failed": "FAIL", "not_started": "NOT_RUN"}.get(str(value), "INCONCLUSIVE")


def summarize(path: Path) -> dict[str, Any]:
    state = read_json(path)
    if state.get("kind") != "separated_runtime_deployment" or state.get("schema_version") != 1:
        raise ValueError("Unsupported deployment evidence schema")
    stages = state.get("stages", {})
    commit = state.get("repository_commit", "")
    if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Fixed source identity is absent")
    stage_statuses = {name: status(stages.get(name, {}).get("status")) for name in STAGES}
    deployment_status = status(state.get("status"))
    if deployment_status == "PASS" and any(value != "PASS" for value in stage_statuses.values()):
        deployment_status = "INCONCLUSIVE"
    result: dict[str, Any] = {"deployment": deployment_status,
        "source_commit": commit,
        "stages": stage_statuses,
        "server_sdk": {"status": "INCONCLUSIVE", "scope": "native SDK cases, not device or browser validation"},
        "soc_setting_test": "NOT_EVALUATED", "browser_test": "NOT_EVALUATED", "cloud_writes": 0}
    build = stages.get("build", {})
    proof_name = build.get("proof_path")
    if not proof_name:
        result["server_sdk"]["status"] = "NOT_RUN" if build.get("status") == "not_started" else "INCONCLUSIVE"
    else:
        proof_path = Path(proof_name)
        if not proof_path.is_absolute():
            proof_path = ROOT / proof_path
        proof = read_json(proof_path)
        cases = proof.get("cases", [])
        fixture_sha = state.get("adapter_fixture_sha256", "")
        digest = state.get("image_digests", {}).get("planner", "")
        matches = (isinstance(fixture_sha, str) and re.fullmatch(r"[a-f0-9]{64}", fixture_sha) is not None
                   and isinstance(digest, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", digest) is not None
                   and hashlib.sha256(proof_path.read_bytes()).hexdigest() == build.get("proof_sha256")
                   and proof.get("source_commit") == state.get("repository_commit")
                   and proof.get("fixture_sha256") == state.get("adapter_fixture_sha256")
                   and proof.get("image_digest") == state.get("image_digests", {}).get("planner")
                   and proof.get("backend_tier") == "production_server" and proof.get("stage") == "all")
        unique_cases = (len(cases) == 12 and all(isinstance(entry.get("name"), str) and entry["name"] for entry in cases)
                        and len({entry.get("name") for entry in cases}) == 12)
        sdk_pass = matches and unique_cases and proof.get("status") == "PASS" and all(entry.get("status") == "PASS" for entry in cases)
        result["server_sdk"].update(status="PASS" if sdk_pass else "FAIL", identity_verified=matches,
            runtime_revision_matches=proof.get("runtime_plan_source_revision") == commit,
            cases_passed=sum(entry.get("status") == "PASS" for entry in cases), cases_total=len(cases),
            sdk_versions={name: version for name, version in proof.get("sdk_versions", {}).items()
                          if name in {"google-cloud-firestore", "google-cloud-storage", "google-auth", "grpcio"}
                          and isinstance(version, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version)})
    repair_name = state.get("monitoring_repair_state_path")
    if repair_name:
        repair_path = Path(repair_name)
        if not repair_path.is_absolute():
            repair_path = ROOT / repair_path
        journal_path = Path(str(repair_path) + ".d") / "journal.json"
        if journal_path.exists():
            journal = read_json(journal_path)
            result["repair_stages"] = {name: status(entry.get("status")) for name, entry in journal.get("stages", {}).items()
                                       if name in {"source", "daily", "cost", "history", "verify"}}
            # Never dump journal values: they can include resource identities and private paths.
            result["failed_repair_stages"] = [name for name, value in result["repair_stages"].items() if value == "FAIL"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = summarize(args.state)
    except Exception as error:
        print(json.dumps({"deployment": "INCONCLUSIVE", "error_type": type(error).__name__, "cloud_writes": 0}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if (result["deployment"] == "PASS" and result["server_sdk"]["status"] == "PASS"
                 and result["server_sdk"].get("runtime_revision_matches") is True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
