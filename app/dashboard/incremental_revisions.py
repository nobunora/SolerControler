"""Actual-import revision checkpoint owner; ordinary database calls stay native."""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import math
import time
from typing import Any

from google.api_core.exceptions import NotFound

from app.dashboard.revision_changes import NativeRevisionChanges, RevisionResetRequired, RevisionState, SOURCES
from app.dashboard.revision_manifest import ManifestClient, timestamp_key
from app.dashboard.revision_reader import RevisionSnapshot

MAX_COMPRESSED_BYTES = 16 * 1024 * 1024
MAX_RAW_BYTES = 128 * 1024 * 1024


def encode_state(state: RevisionState, identity: str) -> bytes:
    return json.dumps({"schema": 1, "identity": identity, "read_time": state.read_time,
                       "resume_token": base64.b64encode(state.resume_token).decode("ascii"), "rows": state.rows},
                      sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def decode_state(raw: bytes, identity: str) -> RevisionState:
    if len(raw) > MAX_RAW_BYTES:
        raise ValueError("revision checkpoint is too large")
    value = json.loads(raw)
    if value.get("schema") != 1 or value.get("identity") != identity or set(value["rows"]) != set(SOURCES):
        raise ValueError("revision checkpoint identity or schema mismatch")
    read_time = timestamp_key(value["read_time"])
    for name, rows in value["rows"].items():
        if not isinstance(rows, dict) or len(rows) > SOURCES[name][1]:
            raise ValueError("revision checkpoint source limit exceeded")
        for key, row in rows.items():
            if not isinstance(key, str) or not key or "/" in key or not isinstance(row["fields"], dict):
                raise ValueError("invalid revision checkpoint document")
            if timestamp_key(row["updated"]) > read_time:
                raise ValueError("revision checkpoint document is newer than its cursor")
    return RevisionState(value["rows"], value["read_time"], base64.b64decode(value["resume_token"], validate=True))


class IncrementalRevisionClient:
    """Explicit worker-only adapter. No persistent listener or writer interception.

    Each fresh verification advances to a global consistent native Listen point.
    Only the worker's known successful terminal receipt permits save_checkpoint.
    """

    def __init__(self, native: Any):
        self.native = native
        self.changes = NativeRevisionChanges(native)
        self.state: RevisionState | None = None
        self.loaded = False
        self.blob: Any = None
        self.generation = 0
        self.identity = ""
        self.checkpoint_attempted = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.native, name)

    def _load(self) -> str:
        from google.cloud import storage
        from app.dashboard.snapshots import _parse_gs_uri, dashboard_snapshot_prefix

        self.loaded = True
        self.identity = hashlib.sha256(self.native._database_string.encode()).hexdigest()
        bucket, name = _parse_gs_uri(dashboard_snapshot_prefix().rstrip("/") + "/revision-checkpoint.json.gz")
        self.blob = storage.Client().bucket(bucket).blob(name)
        try:
            self.blob.reload(timeout=4, retry=None)
        except NotFound:
            return "missing_checkpoint"
        self.generation = int(self.blob.generation)
        if self.blob.size > MAX_COMPRESSED_BYTES:
            raise ValueError("compressed revision checkpoint is too large")
        compressed = self.blob.download_as_bytes(raw_download=True, if_generation_match=self.generation, timeout=4, retry=None)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            raw = stream.read(MAX_RAW_BYTES + 1)
        if hashlib.sha256(raw).hexdigest() != (self.blob.metadata or {}).get("sha256"):
            raise ValueError("revision checkpoint checksum mismatch")
        self.state = decode_state(raw, self.identity)
        return "last_success"

    def metadata_view(self, deadline: float | None = None) -> tuple[ManifestClient, dict[str, Any]]:
        deadline = time.monotonic() + 60 if deadline is None else deadline
        if not math.isfinite(deadline):
            raise ValueError("incremental revision requires a finite deadline")
        origin = "in_process"
        if not self.loaded:
            try:
                origin = self._load()
            except Exception as error:
                origin = "invalid_checkpoint"
                print(json.dumps({"event": "dashboard_revision_checkpoint_rebuild", "reason": origin,
                                  "error_type": type(error).__name__}), flush=True)
        reads = 0
        mode = "delta"
        if self.state is None:
            self.state, reads = self.changes.bootstrap(deadline)
            mode = "bootstrap"
        try:
            state, stats = self.changes.refresh(self.state, deadline)
        except RevisionResetRequired as error:
            # Existence mismatches/expired positions never certify a partial map.
            print(json.dumps({"event": "dashboard_revision_checkpoint_rebuild", "reason": str(error)}), flush=True)
            self.state, bootstrap_reads = self.changes.bootstrap(deadline)
            reads += bootstrap_reads
            state, stats = self.changes.refresh(self.state, deadline)
            mode = "rebuild"
        self.state = state
        stats["document_reads"] += reads
        if stats["reset_targets"]:
            mode = "reset"
        return ManifestClient(state.rows, state.read_time), {**stats, "mode": mode, "checkpoint_origin": origin}

    def read_epoch(self, *, history_scope: bool, deadline: float | None, history_compare_before: str | None,
                   excluded_dates: frozenset[str], revision_snapshot: RevisionSnapshot | None) -> dict[str, str]:
        from app.dashboard.snapshots import _read_dashboard_source_epoch

        deadline = time.monotonic() + 60 if deadline is None else deadline
        try:
            view = revision_snapshot.source_client if revision_snapshot is not None else None
            if view is None:
                view, stats = self.metadata_view(deadline)
                if revision_snapshot is not None:
                    revision_snapshot.source_client = view
            else:
                stats = {"document_reads": 0, "mode": "same_snapshot", "listen_targets": 0,
                         "deleted_documents": 0, "removed_documents": 0, "reset_targets": 0,
                         "bounded_reset_targets": 0, "settings_document_reads": 0,
                         "checkpoint_origin": "in_process"}
            before = view.documents_read
            epoch = _read_dashboard_source_epoch(view, history_scope=history_scope, deadline=deadline,
                history_compare_before=history_compare_before, excluded_dates=excluded_dates, emit_metrics=False)
            logical = view.documents_read - before
            print(json.dumps({"event": "dashboard_source_revisions_read", "stage": "snapshot_producer",
                              "history_scope": history_scope, "components": len(epoch),
                              "cached_documents": max(0, logical - stats["document_reads"]),
                              "logical_document_reads": logical, **stats}), flush=True)
            return epoch
        except Exception as error:
            self.state = None
            if revision_snapshot is not None:
                revision_snapshot.source_client = None
            print(json.dumps({"event": "dashboard_revision_full_fallback", "error_type": type(error).__name__}), flush=True)
            return _read_dashboard_source_epoch(self.native, history_scope=history_scope, deadline=deadline,
                history_compare_before=history_compare_before, excluded_dates=excluded_dates,
                revision_snapshot=revision_snapshot)

    def save_checkpoint(self) -> None:
        if self.state is None or self.blob is None or self.checkpoint_attempted:
            return
        self.checkpoint_attempted = True
        try:
            raw = encode_state(self.state, self.identity)
            if len(raw) > MAX_RAW_BYTES:
                raise ValueError("revision checkpoint exceeds its raw size budget")
            compressed = gzip.compress(raw, mtime=0)
            if len(compressed) > MAX_COMPRESSED_BYTES:
                raise ValueError("revision checkpoint exceeds its compressed size budget")
            self.blob.metadata = {"sha256": hashlib.sha256(raw).hexdigest(), "revision_schema": "1"}
            self.blob.content_encoding = "gzip"
            self.blob.cache_control = "private, no-store"
            self.blob.upload_from_string(compressed, content_type="application/json", if_generation_match=self.generation,
                                         timeout=4, retry=None)
            print(json.dumps({"event": "dashboard_revision_checkpoint_saved", "read_time": self.state.read_time,
                              "documents": sum(map(len, self.state.rows.values())), "compressed_bytes": len(compressed)}), flush=True)
        except Exception as error:
            # A cache/CAS/unknown acknowledgement never undoes a known successful
            # import. Do not retry this write; next time load its native generation.
            print(json.dumps({"event": "dashboard_revision_checkpoint_save_failed", "error_type": type(error).__name__}), flush=True)

    def metadata_for_review(self) -> Any:
        try:
            view, stats = self.metadata_view(time.monotonic() + 4)
            print(json.dumps({"event": "dashboard_revision_delta_review", **stats}), flush=True)
            return view
        except Exception as error:
            print(json.dumps({"event": "dashboard_revision_missing_review_fallback", "error_type": type(error).__name__}), flush=True)
            return self.native
