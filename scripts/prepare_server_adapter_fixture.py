"""Prepare private, exact-owned SDK proof inputs locally; never contact a server."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
from typing import Any
from urllib.parse import urlparse
import uuid

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "fd022e7a94abc40d8190b639722d9f5471042d07"
CALCULATION_PATHS = ("app/energy_model", "app/forecasting", "app/energy_plan",
                     "app/operations/forecast_persistence.py", "app/operations/forecast_recovery.py")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def git(repo: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=repo, text=True,
                                   encoding="utf-8", stderr=subprocess.DEVNULL).strip()


def prepare(repo: Path, expected_commit: str, approval_note: str, output: Path | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{40}", expected_commit) or git(repo, "rev-parse", "HEAD") != expected_commit:
        raise ValueError("Preparation requires the exact intended commit")
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Preparation requires a clean worktree")
    if not approval_note.strip() or len(approval_note) > 2000:
        raise ValueError("An explicit authorization note is required; preparation does not grant approval")
    if os.environ.get("DATA_BACKEND") != "firestore" or os.environ.get("FIRESTORE_EMULATOR_HOST"):
        raise ValueError("Production proof requires the configured Firestore backend without an emulator")
    project, database = (os.environ.get(name, "").strip()
                         for name in ("FIRESTORE_PROJECT_ID", "FIRESTORE_DATABASE_ID"))
    archive = urlparse(os.environ.get("NIGHT_PLAN_ARCHIVE_GCS_PREFIX", ""))
    if not project or not database or archive.scheme != "gs" or not archive.netloc or archive.query or archive.fragment or archive.username:
        raise ValueError("Explicit configured Firestore and archive identities are required")
    operation = "server-proof-" + uuid.uuid4().hex
    proof_root = (repo / "artifacts/adapter_proof").resolve()
    destination = (output or proof_root / operation).resolve()
    if not proof_root.is_relative_to(repo.resolve()) or not destination.is_relative_to(proof_root) or destination == proof_root:
        raise ValueError("Private proof output must stay below artifacts/adapter_proof")
    if destination.exists():
        raise FileExistsError("A prepared proof directory cannot be reused or overwritten")
    ignored = subprocess.run(["git", "check-ignore", "--quiet", str(destination.relative_to(repo.resolve()))],
                             cwd=repo, capture_output=True, check=False)
    if ignored.returncode != 0:
        raise ValueError("Private proof output must be Git-ignored")
    paths = [f"forecast_hourly_snapshots/{operation}-{suffix}" for suffix in ("a", "b")]
    prefix = f"adapter-proof/{operation}/"
    names = [f"{prefix}{suffix}.json" for suffix in ("a", "b")]
    approval = {"user_instruction": approval_note, "source_commit": expected_commit, "operation_id": operation,
                "scope": "dedicated SDK probe; exact future documents and objects; restore and cleanup",
                "physical_setting_writes": 0, "project_id": project, "database_id": database,
                "bucket": archive.netloc, "owned_document_paths": paths, "owned_object_names": names}
    fixture = {"schema_version": 1, "kind": "production_server", "source_commit": expected_commit,
               "operation_id": operation, "backend": "firestore", "project_id": project,
               "database_id": database, "bucket": archive.netloc, "prefix": prefix,
               "target_date": (date.today() + timedelta(days=732)).isoformat(),
               "owned_document_paths": paths, "owned_object_names": names,
               "approval_receipt_sha256": hashlib.sha256(canonical(approval)).hexdigest()}
    fixture["fixture_sha256"] = hashlib.sha256(canonical(fixture)).hexdigest()
    # Reuse the actual proof schema without importing SDK cases or running them.
    runner = runpy.run_path(str(ROOT / "tests/g07/adapter_proof.py"))
    runner["validate_fixture"](fixture)
    patch = subprocess.check_output(["git", "diff", "--no-ext-diff", "--no-color", "--binary", BASELINE,
                                     "--", *CALCULATION_PATHS], cwd=repo, text=True, encoding="utf-8",
                                    stderr=subprocess.DEVNULL)
    patch_sha = hashlib.sha256(patch.encode("utf-8")).hexdigest() if patch else ""
    # Check again before exposing inputs, in case the worktree changed during preparation.
    if git(repo, "rev-parse", "HEAD") != expected_commit or git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Repository changed during preparation")
    destination.mkdir(parents=True, exist_ok=False)
    for name, content in (("approval.private.json", canonical(approval)), ("fixture.private.json", canonical(fixture)),
                          ("calculation-patch-sha256.txt", patch_sha.encode("ascii"))):
        with (destination / name).open("xb") as handle:
            handle.write(content)
    return {"status": "PREPARED_LOCAL_ONLY", "source_commit": expected_commit,
            "prepared_at": datetime.now(timezone.utc).isoformat(),
            "fixture_path": str(destination / "fixture.private.json"), "fixture_sha256": fixture["fixture_sha256"],
            "calculation_patch_sha256": patch_sha, "owned_document_count": 2, "owned_object_count": 2,
            "cloud_writes": 0, "physical_setting_writes": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--approval-note", required=True)
    parser.add_argument("--output-directory", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(ROOT, args.expected_commit, args.approval_note, args.output_directory)))
    except Exception as error:
        # Identifiers and approval text belong only in the private prepared files.
        print(json.dumps({"status": "FAILED_PREPARATION", "error_type": type(error).__name__, "cloud_writes": 0}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
