"""Immutable ideal-max ledger snapshots and a generation-guarded GCS pointer."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.api_core.exceptions import NotFound, PreconditionFailed

from app.forecasting.pv_ideal_max import IdealMaxGeometry
from app.forecasting.pv_ideal_max_store import IdealMaxStore


class IdealMaxCloudArchive:
    """A checkout always uses a fresh local directory; no stale local fallback.

    Readers download one pointer and its exact snapshot generation. Writers keep
    immutable snapshots and replace the pointer only by compare-and-swap.
    """

    def __init__(self, storage: Any, prefix: str, geometry: IdealMaxGeometry, *, deadline: float | None = None) -> None:
        match = re.fullmatch(r"gs://([^/]+)(?:/(.*))?", prefix.rstrip("/"))
        if match is None:
            raise ValueError("ideal maximum archive requires a gs URI")
        self.bucket = storage.bucket(match.group(1))
        self.prefix = "/".join(p for p in (match.group(2), "pv_ideal_max", geometry.fingerprint) if p)
        self.geometry = geometry
        self.deadline = time.monotonic() + 60 if deadline is None else deadline
        if not math.isfinite(self.deadline):
            raise ValueError("finite archive deadline required")
        self.generation = 0
        self.original: dict[str, Any] | None = None

    def _timeout(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("ideal maximum archive deadline exceeded")
        return min(4.0, remaining)

    def _blob(self, suffix: str) -> Any:
        return self.bucket.blob(f"{self.prefix}/{suffix}")

    def checkout(self, directory: Path) -> IdealMaxStore | None:
        if (directory / "pv_ideal_max").exists():
            raise ValueError("cloud checkout requires a fresh local directory")
        pointer = self._blob("current.json")
        try:
            pointer.reload(timeout=self._timeout(), retry=None)
        except NotFound:
            self.generation, self.original = 0, None
            return None
        generation = int(pointer.generation)
        raw = pointer.download_as_bytes(if_generation_match=generation, timeout=self._timeout(), retry=None)
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {"schema_version", "fingerprint", "sha256", "coefficient", "revision", "initialized_at"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["fingerprint"] != self.geometry.fingerprint
                or not isinstance(value["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None):
            raise ValueError("invalid ideal maximum archive pointer")
        initialized_at = datetime.fromisoformat(value["initialized_at"])
        if initialized_at.tzinfo is None or initialized_at.utcoffset() is None:
            raise ValueError("archive initialization requires an aware timestamp")
        snapshot = self._blob(f"snapshots/{value['sha256']}.sqlite.gz")
        snapshot.reload(timeout=self._timeout(), retry=None)
        packed = snapshot.download_as_bytes(if_generation_match=int(snapshot.generation), timeout=self._timeout(), retry=None)
        ledger = gzip.decompress(packed)
        if hashlib.sha256(ledger).hexdigest() != value["sha256"]:
            raise ValueError("ideal maximum archive snapshot checksum mismatch")
        path = directory / "pv_ideal_max" / self.geometry.fingerprint / "ledger.sqlite3"
        path.parent.mkdir(parents=True)
        path.write_bytes(ledger)
        store = IdealMaxStore(directory, self.geometry)
        try:
            state = store.read_current()
            if state.coefficient != value["coefficient"] or state.revision != value["revision"]:
                raise ValueError("archive pointer and ledger state disagree")
            if store.connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("invalid ideal maximum SQLite snapshot")
        except BaseException:
            store.close()
            raise
        self.generation, self.original = generation, value
        return store

    def publish(self, store: IdealMaxStore, *, initialize: bool = False) -> dict[str, Any]:
        if store.geometry.fingerprint != self.geometry.fingerprint:
            raise ValueError("archive and ledger geometry disagree")
        if self.generation == 0 and not initialize:
            raise ValueError("explicit cloud initialization required")
        state = store.read_current()
        if self.original is not None and (state.coefficient < self.original["coefficient"]
                                         or state.revision < self.original["revision"]):
            raise ValueError("cloud publication cannot forget learned state")
        with tempfile.TemporaryDirectory(prefix="ideal-max-backup-") as temporary:
            snapshot_path = Path(temporary) / "ledger.sqlite3"
            destination = sqlite3.connect(snapshot_path)
            try:
                store.connection.backup(destination)
            finally:
                destination.close()
            ledger = snapshot_path.read_bytes()
        digest = hashlib.sha256(ledger).hexdigest()
        snapshot = self._blob(f"snapshots/{digest}.sqlite.gz")
        packed = gzip.compress(ledger, mtime=0)
        try:
            snapshot.upload_from_string(packed, content_type="application/gzip", if_generation_match=0,
                                        timeout=self._timeout(), retry=None)
        except PreconditionFailed:
            snapshot.reload(timeout=self._timeout(), retry=None)
        verified = snapshot.download_as_bytes(if_generation_match=int(snapshot.generation), timeout=self._timeout(), retry=None)
        if verified != packed:
            raise ValueError("archive snapshot readback mismatch")
        pointer = {"schema_version": 1, "fingerprint": self.geometry.fingerprint, "sha256": digest,
                   "coefficient": state.coefficient, "revision": state.revision,
                   "initialized_at": self.original["initialized_at"] if self.original is not None else datetime.now(timezone.utc).isoformat()}
        raw = json.dumps(pointer, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        current = self._blob("current.json")
        # No retries: a competing writer or an uncertain response must reconcile.
        current.upload_from_string(raw, content_type="application/json", if_generation_match=self.generation,
                                   timeout=self._timeout(), retry=None)
        generation = int(current.generation)
        if current.download_as_bytes(if_generation_match=generation, timeout=self._timeout(), retry=None) != raw:
            raise ValueError("archive pointer readback mismatch")
        self.generation, self.original = generation, pointer
        return {"sha256": digest, "generation": generation, "snapshot_bytes": len(packed),
                "coefficient": state.coefficient, "revision": state.revision}
