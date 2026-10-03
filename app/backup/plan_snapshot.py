"""Immutable, read-back verified plan snapshots, independent of device control."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from google.api_core.exceptions import AlreadyExists, PreconditionFailed
from google.cloud.firestore_v1 import transactional

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
    doc["recorder_image_digest"] = os.getenv("PLAN_IMAGE_DIGEST") or None
    doc["image_digest"] = doc["recorder_image_digest"] if source == "adjust03-generated" else None
    doc["cost_model_sha256"] = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":")).encode()).hexdigest() if model is not None else None
    # Immutable raw bytes remain recoverable if an index update fails.
    decision = firestore.collection("night_plan_decisions").document(decision_id)
    try:
        decision.create(doc, timeout=4, retry=None)
    except AlreadyExists:
        recorded = decision.get(timeout=4, retry=None).to_dict() or {}
        if recorded.get("detail_sha256") != sha:
            raise ValueError("immutable decision index checksum mismatch")
        # Reusing the same original must not replace its first recorded origin.
        doc = recorded
    # Compatibility read models point to the same verified immutable object.
    _update_plan_read_models(firestore, doc, raw)
    return {"decision_id": decision_id, "date": day, "detail_sha256": sha, "status": "verified"}


def _plan_order(doc: dict[str, Any]) -> tuple[str, datetime]:
    day = date.fromisoformat(doc["date"]).isoformat()
    # Legacy summaries retain updated_at even without a generated_at field.
    stamp = doc.get("generated_at") or doc.get("updated_at")
    issued = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    if issued.utcoffset() is None:
        raise ValueError("plan ordering requires a timezone")
    return day, issued


def _update_plan_read_models(firestore: Any, doc: dict[str, Any], raw: bytes) -> None:
    collection = firestore.collection("night_charge_plans")
    refs = [collection.document(doc["date"]), collection.document("latest")]

    @transactional
    def update(transaction: Any) -> None:
        # All reads precede writes. A concurrent newer plan invalidates the
        # transaction instead of allowing a stale retry to overwrite it.
        existing = [ref.get(transaction=transaction, timeout=3, retry=None) for ref in refs]
        for ref, old in zip(refs, existing):
            previous = old.to_dict() or {}
            if previous and _plan_order(previous) >= _plan_order(doc):
                continue
            value = {**doc, "plan_json": raw.decode("utf-8")} if ref.id == "latest" else doc
            transaction.set(ref, value, merge=True)

    update(firestore.transaction(max_attempts=1))


def restore_snapshot_payload(snapshot: dict[str, Any], destination: Path) -> list[Path]:
    """Restore embedded detail bytes locally without Firestore/GCS access."""
    destination.mkdir(parents=True, exist_ok=True)
    if not isinstance(snapshot, dict):
        raise ValueError("backup root must be an object")
    generations = [snapshot]
    if snapshot.get("backup_type") == "data_generations":
        entries = snapshot.get("generations")
        if not isinstance(entries, list) or any(not isinstance(entry, dict) or not isinstance(entry.get("snapshot"), dict) for entry in entries):
            raise ValueError("invalid backup generations")
        generations = [entry["snapshot"] for entry in entries]
    plans: dict[str, bytes] = {}
    for generation in generations:
        if not isinstance(generation.get("collections"), dict):
            raise ValueError("backup collections missing")
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
    if not plans:
        raise ValueError("backup contains no restorable plan details")
    targets = [(destination / name, raw) for name, raw in plans.items()]
    for path, raw in targets:
        if path.exists() and path.read_bytes() != raw:
            raise ValueError("restore destination conflicts with existing data")
    result = []
    for path, raw in targets:
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
