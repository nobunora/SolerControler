"""Explicit one-time display repair; never write forecast originals or monitoring DB."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.cloud import storage  # noqa: E402
from app.dashboard import snapshots  # noqa: E402
from app.dashboard.data import clear_dashboard_cache  # noqa: E402
from app.dashboard.date_snapshots import _assert_current_plan  # noqa: E402
from app.operations.firestore import open_firestore  # noqa: E402
from app.separated_runtime.plan import PublishedPlan  # noqa: E402


def _save(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _read(remote: Any, kind: str) -> tuple[bytes, dict[str, str], str]:
    bucket, name = snapshots._parse_gs_uri(snapshots.snapshot_gcs_uri(kind))
    blob = remote.bucket(bucket).blob(name)
    blob.reload(timeout=4, retry=None)
    raw = gzip.decompress(blob.download_as_bytes(raw_download=True, if_generation_match=int(blob.generation), timeout=4, retry=None))
    metadata = blob.metadata or {}
    if hashlib.sha256(raw).hexdigest() != metadata.get("sha256"):
        raise ValueError("display repair checksum mismatch")
    return raw, metadata, str(blob.generation)


def prepare(client: Any, remote: Any, path: Path) -> dict[str, Any]:
    if path.exists():
        raise ValueError("repair state exists; verify it, never overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    before: dict[str, dict[str, Any]] = {}
    identity = {}
    for kind in (snapshots.BOOTSTRAP_KIND, snapshots.HISTORY_KIND):
        raw, metadata, generation = _read(remote, kind)
        before[kind] = {"raw": raw.decode(), "metadata": metadata, "generation": generation}
        if kind == snapshots.BOOTSTRAP_KIND:
            identity = {key: metadata[key] for key in ("target_date", "generated_at", "control_version_id", "raw_plan_sha256")}
    _assert_current_plan(client, identity)
    saved = client.collection("control_plan_versions").document(identity["control_version_id"]).get(timeout=4, retry=None).to_dict() or {}
    plan = PublishedPlan.from_document(json.loads(saved["payload"]), target_date=identity["target_date"])
    if plan.version_id != identity["control_version_id"] or plan.sha256 != identity["raw_plan_sha256"]:
        raise ValueError("repair original mismatch")
    clear_dashboard_cache()
    epoch = snapshots._read_dashboard_source_epoch(client, history_scope=True)
    payloads = snapshots.build_dashboard_snapshot_payloads(Path("artifacts/solar_monitor.db"), existing_history=None)
    snapshots._validate_publication_payload(payloads[snapshots.BOOTSTRAP_KIND], plan)
    if snapshots._read_dashboard_source_epoch(client, history_scope=True) != epoch:
        raise ValueError("repair source changed during preparation")
    _assert_current_plan(client, identity)
    # Keep existing original-bound diagnostics; this repair changes display data only.
    payloads[snapshots.BOOTSTRAP_KIND]["pv_forecast_diagnostics"] = json.loads(before[snapshots.BOOTSTRAP_KIND]["raw"]).get("pv_forecast_diagnostics", {})
    prepared = {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "identity": identity, "epoch": epoch, "before": before, "payloads": payloads}
    _save(path, {"status": "prepared", "prepared": prepared})
    return prepared


def _prepared_sha(prepared: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(prepared, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify(client: Any, remote: Any, prepared: dict[str, Any]) -> None:
    _assert_current_plan(client, prepared["identity"])
    if snapshots._read_dashboard_source_epoch(client, history_scope=True) != prepared["epoch"]:
        raise ValueError("repair source no longer matches the prepared generation")
    encoded = base64.b64encode(gzip.compress(json.dumps(prepared["epoch"], sort_keys=True, separators=(",", ":")).encode(), mtime=0)).decode()
    for kind, payload in prepared["payloads"].items():
        raw, metadata, _ = _read(remote, kind)
        expected = snapshots.artifact_from_payload(kind, payload)
        if raw != expected.raw or metadata.get("source_epoch") != encoded or any(metadata.get(key) != value for key, value in prepared["identity"].items()):
            raise ValueError("display repair readback mismatch; no automatic rewrite")
    _assert_current_plan(client, prepared["identity"])


def apply(client: Any, remote: Any, path: Path, reviewed_sha: str) -> None:
    state = json.loads(path.read_bytes())
    prepared = state["prepared"]
    if state["status"] != "prepared" or _prepared_sha(prepared) != reviewed_sha:
        raise ValueError("repair state is unreviewed or already attempted; use verify")
    if snapshots._read_dashboard_source_epoch(client, history_scope=True) != prepared["epoch"]:
        raise ValueError("repair source changed before write")
    _assert_current_plan(client, prepared["identity"])
    for kind, before in prepared["before"].items():
        raw, metadata, generation = _read(remote, kind)
        if generation != before["generation"] or raw.decode() != before["raw"] or metadata != before["metadata"]:
            raise ValueError("display baseline changed before repair")
    state["status"] = "running"
    _save(path, state)
    for kind, payload in prepared["payloads"].items():
        _assert_current_plan(client, prepared["identity"])
        snapshots._upload_artifact(snapshots.artifact_from_payload(kind, payload), storage_client=remote,
                                   publication_identity=prepared["identity"], source_epoch=prepared["epoch"],
                                   source_client=client, history_scope=True)
    verify(client, remote, prepared)
    state["status"] = "verified"
    _save(path, state)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("prepare", "apply", "verify"), required=True)
    parser.add_argument("--state-path", type=Path, required=True)
    parser.add_argument("--reviewed-state-sha256", default="")
    args = parser.parse_args()
    root = Path("artifacts/deployment_state").resolve()
    if not args.state_path.resolve().is_relative_to(root):
        raise ValueError("repair state must remain private under artifacts/deployment_state")
    client, remote = open_firestore(), storage.Client()
    if args.stage == "prepare":
        prepared = prepare(client, remote, args.state_path)
        print(json.dumps({"status": "prepared", "reviewed_state_sha256": _prepared_sha(prepared)}))
    elif args.stage == "apply":
        apply(client, remote, args.state_path, args.reviewed_state_sha256)
        print('{"status":"verified"}')
    else:
        state = json.loads(args.state_path.read_bytes())
        if args.reviewed_state_sha256 != _prepared_sha(state["prepared"]):
            raise ValueError("repair verification identity mismatch")
        verify(client, remote, state["prepared"])
        print('{"status":"verified_read_only"}')


if __name__ == "__main__":
    main()
