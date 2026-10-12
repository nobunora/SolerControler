"""Calculation owner: reuse the canonical forecast and publication workflow."""
from __future__ import annotations

from pathlib import Path
import os
import json
import time
import hashlib
import gzip
import tempfile
from typing import Any
from urllib.parse import urlparse
from datetime import datetime, timezone

import google.cloud.storage as storage

from app.runtime import forecast_job
from app.backup.plan_snapshot import archive_plan_original, project_plan_read_models
from app.dashboard.snapshots import dashboard_snapshot_prefix, write_dashboard_snapshots
from app.operations.firestore import open_firestore, project_model_parameters
from app.operations.forecast_persistence import project_forecast_plan, _projection_timeout
from app.operations.forecast_snapshot import build_forecast_snapshot_rows
from app.kpnet.monitoring_history import find_latest_kpnet_csv_paths
from app.runtime.forced_charge_monitor import estimate_forced_charge_rate_percent_per_hour
from app.separated_runtime.plan import PublishedPlan
from app.separated_runtime.plan_store import FirestorePlanStore


def _private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("private immutable receipt conflict")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(raw)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        os.link(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_forecast_intent(path: Path, *, prepared_sha256: str, source_commit: str, planner_digest: str) -> None:
    """Record the pre-execution reference; this never changes remote state."""
    target = forecast_job._target_date()
    client = open_firestore()
    snapshot = client.collection("control_plan_current").document(target).get(timeout=4, retry=None)
    old = PublishedPlan.from_document(json.loads(snapshot.to_dict()["payload"]), target_date=target) if snapshot.exists else None
    value = {"schema_version": 1, "prepared_sha256": prepared_sha256, "source_commit": source_commit,
             "planner_digest": planner_digest, "command": "python", "args": ["planner_job_main.py"],
             "target_date": target, "previous_control_version_id": old.version_id if old else None,
             "previous_raw_plan_sha256": old.sha256 if old else None,
             "previous_update_time": snapshot.update_time.isoformat() if snapshot.exists else None}
    if path.exists():
        saved = json.loads(path.read_bytes())
        if any(saved.get(key) != value[key] for key in ("prepared_sha256", "source_commit", "planner_digest", "target_date")):
            raise ValueError("forecast intent identity changed")
        return
    _private_json(path, value)


def capture_adopted_forecast_receipt(execution_path: Path, intent_path: Path, receipt_path: Path) -> None:
    """Bind one exact execution to native adopted objects, or stop UNKNOWN."""
    execution = json.loads(execution_path.read_bytes())
    intent = json.loads(intent_path.read_bytes())
    name = str(execution.get("execution_name") or "").rsplit("/", 1)[-1]
    if not name:
        raise ValueError("forecast execution identity is unknown")
    identities = set()
    for record in execution.get("logs", []):
        if record.get("labels", {}).get("run.googleapis.com/execution_name") != name:
            continue
        event = record.get("jsonPayload", {})
        if event.get("event") in {"plan_publication_succeeded", "plan_publication_unknown", "plan_publication_rejected", "plan_projection_failed"}:
            identities.add((event.get("target_date"), event.get("control_version_id"), event.get("raw_plan_sha256")))
    if len(identities) != 1:
        raise ValueError("forecast candidate is unknown or ambiguous")
    target, version, sha = identities.pop()
    if target != intent["target_date"] or not isinstance(version, str) or not isinstance(sha, str):
        raise ValueError("forecast candidate does not match intent")
    client = open_firestore()
    current = client.collection("control_plan_current").document(target).get(timeout=4, retry=None).to_dict() or {}
    original = client.collection("control_plan_versions").document(version).get(timeout=4, retry=None).to_dict() or {}
    if current.get("payload") != original.get("payload") or not original.get("payload"):
        raise ValueError("forecast candidate is not the native adopted version")
    plan = PublishedPlan.from_document(json.loads(original["payload"]), target_date=target)
    if plan.version_id != version or plan.sha256 != sha or plan.producer_source_revision != intent["source_commit"]:
        raise ValueError("adopted forecast provenance mismatch")
    decision_id = f"{target}--{sha}"
    decision = client.collection("night_plan_decisions").document(decision_id).get(timeout=4, retry=None).to_dict() or {}
    raw = plan.raw_json.encode()
    data = json.loads(raw)
    if decision.get("detail_sha256") != sha or decision.get("generated_at") != data["generated_at"]:
        raise ValueError("adopted forecast archive identity mismatch")
    uri = urlparse(str(decision.get("detail_gcs_uri") or decision.get("detail_uri") or ""))
    if uri.scheme != "gs" or not uri.netloc:
        raise ValueError("adopted original URI is missing")
    blob = storage.Client().bucket(uri.netloc).blob(uri.path.lstrip("/"))
    blob.reload(timeout=4, retry=None)
    generation = int(blob.generation)
    saved_raw = blob.download_as_bytes(raw_download=True, if_generation_match=generation, timeout=4, retry=None)
    if gzip.decompress(saved_raw) != raw:
        raise ValueError("adopted archive original checksum mismatch")
    original_path = receipt_path.parent / "forecast-plan-adopted" / "plan.json"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    if original_path.exists() and original_path.read_bytes() != raw:
        raise ValueError("adopted private original conflict")
    if not original_path.exists():
        original_path.write_bytes(raw)
    rows = build_forecast_snapshot_rows(data, ingested_at=decision["updated_at"], timezone="Asia/Tokyo")
    if len(rows) != 24:
        raise ValueError("adopted forecast does not contain complete hourly evidence")
    trace = (data.get("decision_rationale") or {}).get("pv_clear_ghi") or {}
    pack = trace.get("calibration_pack")
    pack_sha = hashlib.sha256(json.dumps(pack, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest() if pack else None
    receipt = {"schema_version": 1, "status": "verified", "prepared_sha256": intent["prepared_sha256"],
               "execution_name": execution["execution_name"], "source_commit": intent["source_commit"],
               "execution_receipt_path": str(execution_path.resolve()),
               "execution_receipt_sha256": hashlib.sha256(execution_path.read_bytes()).hexdigest(),
               "planner_digest": intent["planner_digest"], "target_date": target, "generated_at": data["generated_at"],
               "control_version_id": version, "raw_plan_sha256": sha, "forecast_run_id": rows[0]["forecast_run_id"],
               "decision_id": decision_id, "detail_sha256": sha, "archive_generation": generation,
               "calibration_sha256": pack_sha, "original_path": str(original_path.resolve())}
    _private_json(receipt_path, receipt)


def recover_adopted_forecast_projection(receipt_path: Path) -> None:
    """Resume projections from one adopted original; never regenerate or adopt."""
    receipt = json.loads(receipt_path.read_bytes())
    client = open_firestore()
    target = receipt["target_date"]
    document = client.collection("control_plan_versions").document(receipt["control_version_id"]).get(timeout=4, retry=None).to_dict() or {}
    current = client.collection("control_plan_current").document(target).get(timeout=4, retry=None).to_dict() or {}
    if document.get("payload") != current.get("payload"):
        raise ValueError("adopted forecast was superseded")
    plan = PublishedPlan.from_document(json.loads(document["payload"]), target_date=target)
    path = Path(receipt["original_path"])
    raw = path.read_bytes()
    if plan.sha256 != receipt["raw_plan_sha256"] or plan.version_id != receipt["control_version_id"] or raw != plan.raw_json.encode():
        raise ValueError("adopted forecast receipt checksum mismatch")
    decision = client.collection("night_plan_decisions").document(receipt["decision_id"]).get(timeout=4, retry=None).to_dict() or {}
    deadline = time.monotonic() + 60
    project_forecast_plan(client, raw=raw, plan=plan, private_plan_path=path, timezone_name="Asia/Tokyo", deadline=deadline)
    project_plan_read_models(decision, raw, firestore=client, deadline=deadline)
    project_model_parameters(client, raw=raw, plan=plan, updated_at=datetime.now(timezone.utc).isoformat(), deadline=deadline)
    # Only an incomplete bootstrap needs publication; an existing same-raw
    # artifact is retained, including its reader generation and cache identity.
    prefix = dashboard_snapshot_prefix()
    if prefix:
        from app.dashboard.snapshots import snapshot_gcs_uri, _validate_publication_payload
        uri = urlparse(snapshot_gcs_uri("bootstrap"))
        blob = storage.Client().bucket(uri.netloc).blob(uri.path.lstrip("/"))
        valid = False
        try:
            blob.reload(timeout=_projection_timeout(deadline), retry=None)
            metadata = blob.metadata or {}
            if metadata.get("raw_plan_sha256") == plan.sha256 and metadata.get("control_version_id") == plan.version_id:
                payload = blob.download_as_bytes(raw_download=True, if_generation_match=int(blob.generation),
                                                 timeout=_projection_timeout(deadline), retry=None)
                _validate_publication_payload(json.loads(gzip.decompress(payload)), plan)
                valid = True
        except ValueError:
            pass
        except Exception as error:
            if type(error).__name__ != "NotFound":
                raise
        if not valid:
            _projection_timeout(deadline)
            write_dashboard_snapshots(Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db")), published_plan=plan)


def main() -> int:
    # Downloaded CSVs are model inputs; this role does not produce KPI PNGs.
    os.environ["KP_CSV_PLOT_ENABLED"] = "false"
    path = forecast_job.generate_forecast_plan()
    artifacts = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
    rate = estimate_forced_charge_rate_percent_per_hour(find_latest_kpnet_csv_paths(artifacts))
    raw = path.read_bytes()
    plan = PublishedPlan.from_bytes(raw, target_date=forecast_job._target_date(), charge_rate_info=rate,
                                   producer_source_revision=os.environ.get("PLAN_SOURCE_REVISION", ""), inputs_verified=True)
    client = open_firestore()
    # HISTORICAL_FAILURE_LOCK: the planner owns BOTH the exact immutable
    # decision archive and the legacy latest/day dashboard projections. Moving
    # generation out of 03 must never freeze the dashboard's latest plan.
    _, archived_doc = archive_plan_original(raw, storage=storage.Client(), firestore=client, source="planner-generated")
    try:
        FirestorePlanStore().publish(plan)
    except Exception as error:
        event = "plan_publication_unknown" if any("acknowledgement is unknown" in note
                                                  for note in getattr(error, "__notes__", ())) else "plan_publication_rejected"
        print(json.dumps({"event": event, "stage": "control_cas",
                          "target_date": plan.target_date, "control_version_id": plan.version_id,
                          "raw_plan_sha256": plan.sha256, "reason_code": type(error).__name__}), flush=True)
        raise
    print(json.dumps({"event": "plan_publication_succeeded", "stage": "control_cas",
                      "target_date": plan.target_date, "control_version_id": plan.version_id,
                      "raw_plan_sha256": plan.sha256}), flush=True)
    projection_deadline = time.monotonic() + 60.0
    stage = "forecast_projection"
    try:
        project_forecast_plan(client, raw=raw, plan=plan, private_plan_path=path, timezone_name="Asia/Tokyo",
                              deadline=projection_deadline)
        stage = "plan_projection"
        project_plan_read_models(archived_doc, raw, firestore=client, deadline=projection_deadline)
        stage = "parameters_projection"
        project_model_parameters(client, raw=raw, plan=plan, updated_at=datetime.now(timezone.utc).isoformat(),
                                 deadline=projection_deadline)
        stage = "dashboard_snapshot"
        if dashboard_snapshot_prefix():
            write_dashboard_snapshots(Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db")), published_plan=plan)
    except Exception as error:
        # Adoption succeeded. Retrying/reapplying control to fix display would
        # confuse publication with projection; retain the adopted original.
        print(json.dumps({"event": "plan_projection_failed", "stage": stage,
                          "target_date": plan.target_date, "control_version_id": plan.version_id,
                          "raw_plan_sha256": plan.sha256, "reason_code": type(error).__name__}), flush=True)
    print("Published validated control plan", flush=True)
    return 0
