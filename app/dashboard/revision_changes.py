"""Bounded Firestore metadata bootstrap and resumable native change reads."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import md5
import json
from threading import Event
import time
from typing import Any

# Firestore already depends on grpcio; this environment has no separate stubs.
import grpc  # type: ignore[import-untyped]
from google.cloud.firestore_v1 import _helpers
from google.cloud.firestore_v1.types import BloomFilter, ListenRequest, Target

from app.dashboard.revision_reader import PAGE_SIZE, RevisionReader
from app.dashboard.revision_manifest import timestamp_key, wire_timestamp


SOURCES = {
    "sunshine_daily": (["date"], 3660), "battery_daily_metrics": (["date"], 3660),
    "forecast_hourly": (["date"], 24 * 1096),
    "forecast_hourly_snapshots": (["date"], 24 * 1096 * 8),
    "forecast_hourly_reconstructed": (["date"], 24 * 1096),
    "forecast_plans": (["date"], 3660), "dashboard_daily_metrics": (["date"], 3660),
    "night_charge_plans": (["date", "decision_rationale.forecast_correction.recent_and_analog_hourly_floor.analog_day",
                            "daytime_soc_optimization.forecast_correction.recent_and_analog_hourly_floor.analog_day"], 3660),
    "monitoring_samples": (["ts"], 48 * 1096), "cost_daily": (["date"], 3660),
    "model_parameters": (["name"], 3660), "settings_events": (["recorded_at"], 40),
}


def _absent_names(names: list[str], bloom: BloomFilter) -> list[str]:
    """Return only names the native existence filter proves absent.

    Firestore uses little-endian MD5 halves and unsigned 64-bit double hashing.
    A possible member is never removed: false positives require a full rebuild.
    """
    bitmap, padding, hashes = bytes(bloom.bits.bitmap), bloom.bits.padding, bloom.hash_count
    if not 0 <= padding < 8 or not 0 <= hashes <= 64 or (bitmap and not hashes) or (not bitmap and padding):
        raise RevisionResetRequired("invalid revision existence filter")
    bit_count = len(bitmap) * 8 - padding
    if not bit_count:
        return names
    absent = []
    for name in names:
        digest = md5(name.encode("utf-8"), usedforsecurity=False).digest()
        first, second = int.from_bytes(digest[:8], "little"), int.from_bytes(digest[8:], "little")
        for index in range(hashes):
            bit = ((first + index * second) & ((1 << 64) - 1)) % bit_count
            if not bitmap[bit // 8] & (1 << (bit % 8)):
                absent.append(name)
                break
    return absent


def timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("revision timestamp is unavailable")
    method = getattr(value, "rfc3339", None)
    return str(method()) if method else value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def projected_fields(name: str, values: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in SOURCES[name][0]:
        path = field.split(".")
        value: Any = values
        for part in path:
            if not isinstance(value, dict) or part not in value:
                break
            value = value[part]
        else:
            target = result
            for part in path[:-1]:
                target = target.setdefault(part, {})
            target[path[-1]] = value
    return result


@dataclass
class RevisionState:
    rows: dict[str, dict[str, Any]]
    read_time: str
    resume_token: bytes = b""


class RevisionResetRequired(ValueError):
    pass


class NativeRevisionChanges:
    def __init__(self, client: Any):
        self.client = client
        self.observed_reads = 0

    def query(self, name: str) -> Any:
        query = self.client.collection(name)
        # A bounded native target tracks tail replacements as removals/additions.
        return (query.order_by("recorded_at", direction="DESCENDING").limit(40) if name == "settings_events"
                else query.order_by("__name__"))

    @staticmethod
    def timeout(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("incremental revision deadline exceeded")
        return min(4.0, remaining)

    def bootstrap(self, deadline: float) -> tuple[RevisionState, int]:
        """Initial/recovery scan only, with the existing stable-page semantics."""
        reader = RevisionReader(self.client, lambda: self.timeout(deadline))
        rows: dict[str, dict[str, Any]] = {}
        reads = 1
        for name, (fields, maximum) in SOURCES.items():
            query = self.query(name).select(fields)
            values: dict[str, Any] = {}
            cursor = ""
            while True:
                page = reader.query(query.limit(PAGE_SIZE + 1)) if name != "settings_events" else reader.query(query)
                reads += len(page)
                accepted = page[:PAGE_SIZE]
                for row in accepted:
                    if row.id in values or (name != "settings_events" and row.id <= cursor):
                        raise ValueError("revision bootstrap cursor did not advance")
                    cursor = row.id
                    values[row.id] = {"fields": projected_fields(name, row.to_dict() or {}), "updated": timestamp(row.update_time)}
                if len(values) > maximum:
                    raise ValueError("revision bootstrap source exceeds its bounded limit")
                if len(page) <= PAGE_SIZE:
                    break
                query = query.start_after(accepted[-1])
            rows[name] = values
        return RevisionState(rows, timestamp(reader.read_time)), reads

    def refresh(self, state: RevisionState, deadline: float) -> tuple[RevisionState, dict[str, int]]:
        # A failed/partial stream never changes the caller's manifest or cursor.
        self.observed_reads = 0
        for attempt in range(3):
            try:
                result, stats = self._listen(state, deadline)
                stats["document_reads"] = self.observed_reads
                return result, stats
            except grpc.RpcError as error:
                if attempt == 2 or error.code() not in (
                    grpc.StatusCode.DEADLINE_EXCEEDED, grpc.StatusCode.UNAVAILABLE,
                    grpc.StatusCode.INTERNAL, grpc.StatusCode.RESOURCE_EXHAUSTED,
                ):
                    raise
                print(json.dumps({"event": "dashboard_revision_delta_retry", "attempt": attempt + 1,
                                  "error_type": type(error).__name__}), flush=True)
                time.sleep(min(0.1 * 2 ** attempt, self.timeout(deadline)))
        raise AssertionError("unreachable revision change retry")

    def _listen(self, state: RevisionState, deadline: float) -> tuple[RevisionState, dict[str, int]]:
        rows = {name: dict(values) for name, values in state.rows.items()}
        targets = {index: name for index, name in enumerate(SOURCES, 1)}
        current: set[int] = set()
        stopped = Event()
        stats = {"document_reads": 0, "deleted_documents": 0, "removed_documents": 0,
                 "bloom_removed_documents": 0,
                 "reset_targets": 0, "bounded_reset_targets": 0, "settings_document_reads": 0,
                 "listen_targets": len(targets)}
        requests = []
        for target_id, name in targets.items():
            target = Target(query={"parent": self.client._database_string + "/documents",
                                   "structured_query": self.query(name)._to_protobuf()}, target_id=target_id,
                            expected_count={"value": len(rows[name])})
            if state.resume_token:
                target.resume_token = state.resume_token
            else:
                target.read_time = wire_timestamp(state.read_time)
            requests.append(ListenRequest(database=self.client._database_string, add_target=target))

        def request_stream() -> Any:
            yield from requests
            # Half-closing a bidi call early can discard its initial responses.
            stopped.wait(self.timeout(deadline) + 1)

        # Match the SDK Watch transport: GAPIC listen injects an empty routing
        # header; the transport preserves the database resource-prefix metadata.
        stream = self.client._firestore_api._transport.listen(
            request_stream(), timeout=self.timeout(deadline), metadata=self.client._rpc_metadata)
        try:
            for event in stream:
                self.timeout(deadline)
                kind = event._pb.WhichOneof("response_type")
                if kind == "target_change":
                    change = event.target_change
                    ids = set(change.target_ids) or set(targets)
                    if not ids <= targets.keys() or change.cause.code:
                        raise RevisionResetRequired("revision target was rejected")
                    if change.target_change_type == 2:
                        raise RevisionResetRequired("revision target was removed before consistency")
                    if change.target_change_type == 4:
                        for target_id in ids:
                            rows[targets[target_id]] = {}
                        current -= ids
                        stats["bounded_reset_targets"] += sum(targets[target_id] == "settings_events" for target_id in ids)
                        stats["reset_targets"] += sum(targets[target_id] != "settings_events" for target_id in ids)
                    if change.target_change_type == 3:
                        current |= ids
                    if not change.target_ids and change.read_time and current == set(targets):
                        read_time = timestamp(change.read_time)
                        if timestamp_key(read_time) < timestamp_key(state.read_time):
                            raise ValueError("revision cursor moved backwards")
                        return RevisionState(rows, read_time, bytes(change.resume_token)), stats
                elif kind == "filter":
                    filtered_name = targets.get(event.filter.target_id)
                    if filtered_name is not None and event.filter.count != len(rows[filtered_name]):
                        prefix = self.client._database_string + "/documents/" + filtered_name + "/"
                        names = [prefix + key for key in rows[filtered_name]]
                        absent = (names if event.filter.count == 0 else
                                  _absent_names(names, event.filter.unchanged_names)
                                  if event.filter._pb.HasField("unchanged_names") else [])
                        # Accept only a completely reconciled set. Missing filters
                        # or false positives retain the existing rebuild boundary.
                        if len(rows[filtered_name]) - len(absent) == event.filter.count:
                            for document_name in absent:
                                rows[filtered_name].pop(document_name[len(prefix):])
                            stats["removed_documents"] += len(absent)
                            stats["bloom_removed_documents"] += len(absent)
                    if filtered_name is None or event.filter.count != len(rows[filtered_name]):
                        print(json.dumps({"event": "dashboard_revision_existence_mismatch", "source": filtered_name,
                                          "observed": event.filter.count,
                                          "cached": len(rows[filtered_name]) if filtered_name else None}), flush=True)
                        raise RevisionResetRequired("revision existence count mismatch")
                elif kind in ("document_change", "document_delete", "document_remove"):
                    if kind != "document_delete":
                        self.observed_reads += 1
                    message = getattr(event, kind)
                    document_name = message.document.name if kind == "document_change" else message.document
                    prefix = self.client._database_string + "/documents/"
                    if not document_name.startswith(prefix):
                        raise ValueError("revision document belongs to another database")
                    parts = document_name[len(prefix):].split("/")
                    if len(parts) != 2 or parts[0] not in SOURCES:
                        raise ValueError("revision document is outside its target")
                    name, key = parts
                    target_id = next(index for index, value in targets.items() if value == name)
                    if kind == "document_change" and target_id in message.target_ids:
                        fields = message.document.fields
                        roots = {field.split(".")[0] for field in SOURCES[name][0]}
                        values = _helpers.decode_dict({field: value for field, value in fields.items() if field in roots}, self.client)
                        if not isinstance(values, dict):
                            raise ValueError("revision document metadata is not a map")
                        rows[name][key] = {"fields": projected_fields(name, values), "updated": timestamp(message.document.update_time)}
                        stats["document_reads"] += 1
                        if name == "settings_events":
                            stats["settings_document_reads"] += 1
                    else:
                        rows[name].pop(key, None)
                        stats["deleted_documents" if kind == "document_delete" else "removed_documents"] += 1
                    if len(rows[name]) > SOURCES[name][1]:
                        raise ValueError("revision change source exceeds its bounded limit")
                else:
                    raise ValueError("unknown revision stream response")
            raise RevisionResetRequired("revision stream ended without a consistent snapshot")
        finally:
            stopped.set()
            stream.cancel()
