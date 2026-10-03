"""Immutable, read-back verified plan snapshots, independent of device control."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from google.api_core.exceptions import PreconditionFailed

from app.backup.night_plan_archive import (
    _parse_gs_uri, build_night_plan_firestore_document, night_plan_archive_prefix,
)


def archive_plan_snapshot(
    path: Path, *, storage: Any, firestore: Any, source: str,
    prefix: str | None = None,
) -> dict[str, Any]:
    """Keep exact original bytes; never label a generated plan as device-applied."""
    raw = path.read_bytes()
    plan = json.loads(raw)
    day = date.fromisoformat(plan["forecast"]["date"]).isoformat()
    sha = hashlib.sha256(raw).hexdigest()
    decision_id = f"{day}--{sha}"
    archive_prefix = prefix or night_plan_archive_prefix()
    if not archive_prefix:
        raise ValueError("night plan archive prefix missing")
    bucket, base = _parse_gs_uri(archive_prefix)
    name = f"{base.rstrip('/')}/decisions/{day}/{sha}.json.gz".lstrip('/')
    blob = storage.bucket(bucket).blob(name)
    blob.content_encoding = "gzip"
    try:
        blob.upload_from_string(gzip.compress(raw, mtime=0), content_type="application/json",
                                if_generation_match=0, timeout=4, retry=None)
    except PreconditionFailed:
        # An identical retry reuses the object; content is still verified below.
        pass
    stored = blob.download_as_bytes(raw_download=True, timeout=4, retry=None)
    if gzip.decompress(stored) != raw:
        raise ValueError("plan snapshot read-back mismatch")
    now = datetime.now(timezone.utc).isoformat()
    archive = {"detail_uri": f"gs://{bucket}/{name}", "generation": str(blob.generation or ""), "archived_at": now}
    doc = build_night_plan_firestore_document(plan, source=source, updated_at=now, archive_info=archive)
    doc.update({"decision_id": decision_id, "detail_sha256": sha, "detail_size_bytes": len(raw),
                "detail_gzip_size_bytes": len(stored), "generated_at": plan.get("generated_at"),
                "record_status": "generated", "plan_json": None})
    model = (plan.get("daytime_soc_optimization") or {}).get("cost_model")
    doc["recorder_source_revision"] = os.getenv("PLAN_SOURCE_REVISION") or None
    doc["source_revision"] = doc["recorder_source_revision"] if source == "adjust03-generated" else None
    doc["cost_model_sha256"] = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":")).encode()).hexdigest() if model is not None else None
    # Immutable raw bytes remain recoverable if an index update fails.
    firestore.collection("night_plan_decisions").document(decision_id).set(doc, timeout=4, retry=None)
    # Compatibility read models point to the same verified immutable object.
    collection = firestore.collection("night_charge_plans")
    collection.document(day).set(doc, timeout=4, retry=None)
    # Preserve the existing inline latest read path; dashboard bootstrap must
    # not add a GCS round trip merely because immutable archival was enabled.
    collection.document("latest").set({**doc, "plan_json": raw.decode("utf-8")}, timeout=4, retry=None)
    return {"decision_id": decision_id, "date": day, "detail_sha256": sha, "status": "verified"}


def restore_snapshot_payload(snapshot: dict[str, Any], destination: Path) -> list[Path]:
    """Restore embedded detail bytes locally without Firestore/GCS access."""
    destination.mkdir(parents=True, exist_ok=True)
    generations = [entry["snapshot"] for entry in snapshot.get("generations", [])] if snapshot.get("backup_type") == "data_generations" else [snapshot]
    plans: dict[str, bytes] = {}
    for generation in generations:
        for name in ("night_plan_decisions", "night_charge_plans"):
            for row in generation.get("collections", {}).get(name, []):
                text = row.get("plan_json")
                if not isinstance(text, str) or not text:
                    raise ValueError("backup missing embedded plan detail")
                raw = text.encode("utf-8")
                sha = hashlib.sha256(raw).hexdigest()
                if sha != row.get("detail_sha256"):
                    raise ValueError("backup detail checksum mismatch")
                plan = json.loads(text)
                day = date.fromisoformat(plan["forecast"]["date"]).isoformat()
                plans[f"{day}--{sha}.json"] = raw
    result = []
    for name, raw in plans.items():
        path = destination / name
        if path.exists() and path.read_bytes() != raw:
            raise ValueError("restore destination conflicts with existing data")
        path.write_bytes(raw)
        result.append(path)
    return result


def embed_plan_detail(doc: dict[str, Any], *, storage: Any) -> dict[str, Any]:
    """Make the backup self-contained; fail instead of archiving a broken URI."""
    text = doc.get("plan_json")
    if isinstance(text, str) and text:
        raw = text.encode("utf-8")
    else:
        bucket, name = _parse_gs_uri(str(doc.get("detail_gcs_uri") or doc.get("detail_uri") or ""))
        blob = storage.bucket(bucket).blob(name)
        raw = blob.download_as_bytes(raw_download=True, timeout=30)
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    sha = hashlib.sha256(raw).hexdigest()
    if doc.get("detail_sha256") and sha != doc["detail_sha256"]:
        raise ValueError("backup source checksum mismatch")
    if not isinstance(json.loads(raw), dict):
        raise ValueError("backup source is not a plan")
    return {**doc, "plan_json": raw.decode("utf-8"), "detail_sha256": sha}
