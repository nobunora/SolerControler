"""Explicit stdlib server-proof runner; never collected by ordinary pytest."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback
from datetime import date, timedelta


def canonical_sha(value: dict) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "fixture_sha256"},
                                    sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_fixture(value: dict) -> None:
    if value.get("schema_version") != 1:
        raise ValueError("unsupported fixture schema")
    if not re.fullmatch(r"[a-f0-9]{40}", value.get("source_commit", "")):
        raise ValueError("fixed source commit is absent")
    for key in ("project_id", "database_id", "bucket"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError("explicit server resource identity is absent")
    operation = value.get("operation_id", "")
    if not re.fullmatch(r"[a-z0-9-]{12,80}", operation):
        raise ValueError("operation ownership is absent")
    if canonical_sha(value) != value.get("fixture_sha256"):
        raise ValueError("fixture checksum mismatch")
    if value.get("kind") not in {"emulator", "isolated_cloud", "production_server"}:
        raise ValueError("unsupported fixture kind")
    if value["kind"] != "emulator" and not re.fullmatch(r"[a-f0-9]{64}", value.get("approval_receipt_sha256", "")):
        raise ValueError("real-server approval scope is absent")
    allowed = {"forecast_plans", "forecast_hourly_snapshots", "model_parameters", "control_plan_versions", "night_plan_decisions"}
    paths = value.get("owned_document_paths", [])
    names = value.get("owned_object_names", [])
    if len(paths) < 2 or len(paths) != len(set(paths)) or len(names) < 2 or len(names) != len(set(names)):
        raise ValueError("two distinct exact-owned documents and objects are required")
    if value["kind"] == "production_server":
        allowed = {"forecast_hourly_snapshots"}
        if date.fromisoformat(value.get("target_date", "")) <= date.today() + timedelta(days=366):
            raise ValueError("production synthetic fixture must be outside scheduled history scopes")
    for path in paths:
        parts = path.split("/")
        if len(parts) != 2 or parts[0] not in allowed or not parts[1].startswith(operation + "-"):
            raise ValueError("document is outside operation-owned synthetic scope")
    prefix = value.get("prefix", "")
    if prefix != f"adapter-proof/{operation}/":
        raise ValueError("object prefix is outside operation ownership")
    for name in names:
        if not name.startswith(prefix) or ".." in name.split("/"):
            raise ValueError("object is outside operation ownership")
    if value["kind"] == "emulator":
        endpoint = value.get("endpoint", "")
        if not re.fullmatch(r"(?:127\.0\.0\.1|localhost):\d{2,5}", endpoint):
            raise ValueError("emulator is not loopback-only")
    elif os.environ.get("FIRESTORE_EMULATOR_HOST"):
        raise ValueError("real-server proof cannot use an emulator")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--stage", choices=("preflight", "firestore", "gcs", "restore", "performance", "cleanup", "all"), required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--image-digest", default="")
    args = parser.parse_args()
    started = time.monotonic()
    receipt = {"schema_version": 1, "status": "INCONCLUSIVE", "cases": [], "stage": args.stage}
    code = 2
    try:
        fixture_bytes = args.fixture.read_bytes()
        fixture = json.loads(fixture_bytes)
        validate_fixture(fixture)
        receipt.update(operation_id=fixture["operation_id"], fixture_sha256=fixture["fixture_sha256"],
                       backend_tier=fixture["kind"], image_digest=args.image_digest)
        if args.stage == "preflight":
            receipt["status"] = "PASS"
            code = 0
        else:
            if Path("/app/app").is_dir():
                sys.path.insert(0, "/app")
            expected = {"google-cloud-firestore": "2.34.0", "google-auth": "2.60.0",
                        "google-cloud-storage": "2.19.0", "grpcio": "1.84.0"}
            actual = {name: importlib.metadata.version(name) for name in expected}
            receipt["sdk_versions"] = actual
            receipt["source_commit"] = fixture.get("source_commit")
            receipt["runtime_plan_source_revision"] = os.environ.get("PLAN_SOURCE_REVISION")
            if actual != expected or not re.fullmatch(r"sha256:[a-f0-9]{64}", args.image_digest):
                receipt["reason_code"] = "target_runtime_identity_unproven"
            else:
                import firestore_server_cases
                import gcs_server_cases
                cases = []
                if args.stage in {"firestore", "restore", "performance", "all"}:
                    cases.extend(firestore_server_cases.run(fixture))
                if args.stage in {"gcs", "restore", "performance", "all"}:
                    cases.extend(gcs_server_cases.run(fixture))
                if args.stage == "cleanup":
                    cases.append({"name": "cleanup", "status": "INCONCLUSIVE", "reason_code": "no_owned_native_receipt_selected"})
                receipt["cases"] = cases
                receipt["status"] = "PASS" if cases and all(c["status"] == "PASS" for c in cases) else "INCONCLUSIVE"
                code = 0 if receipt["status"] == "PASS" else 2
            if args.fixture.read_bytes() != fixture_bytes:
                raise ValueError("fixture changed during server proof")
    except Exception as error:
        receipt["status"] = "FAIL"
        receipt["reason_code"] = type(error).__name__
        category = type(error).__name__
        receipt["exception_category"] = (
            "AUTHORIZATION" if category in {"Forbidden", "PermissionDenied", "Unauthenticated", "Unauthorized"}
            else "PRECONDITION" if category in {"FailedPrecondition", "PreconditionFailed", "AlreadyExists", "Conflict"}
            else "TRANSPORT" if category in {"TimeoutError", "DeadlineExceeded", "ServiceUnavailable", "TransportError"}
            else "VALIDATION" if isinstance(error, (ValueError, AssertionError)) else "RUNTIME"
        )
        notes = [note for note in getattr(error, "__notes__", ()) if note.startswith("native proof step=")]
        receipt["failure_step"] = notes[-1] if notes else args.stage
        if args.receipt:
            detail = {"exception_type": category, "category": receipt["exception_category"], "step": receipt["failure_step"],
                      "frames": [{"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                                 for frame in traceback.extract_tb(error.__traceback__)]}
            code_value = getattr(error, "code", None)
            if isinstance(code_value, int):
                detail["status_code"] = int(code_value)
            if isinstance(error, ModuleNotFoundError) and re.fullmatch(r"[A-Za-z0-9_.]+", error.name or ""):
                detail["missing_module"] = error.name
            detail_path = args.receipt.parent / (args.receipt.name + ".d") / "failure.private.json"
            detail_path.parent.mkdir(parents=True, exist_ok=True)
            detail_path.write_text(json.dumps(detail, sort_keys=True), encoding="utf-8")
        code = 1
    receipt["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.receipt.with_suffix(args.receipt.suffix + ".part")
        temporary.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, args.receipt)
    print(json.dumps({"status": receipt["status"], "reason_code": receipt.get("reason_code"), "cases": len(receipt["cases"])}))
    return code


if __name__ == "__main__":
    sys.exit(main())
