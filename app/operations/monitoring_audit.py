from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from app.operations.monitoring_sync import meaningful_monitoring_payload


@dataclass(frozen=True)
class MonitoringAuditResult:
    total_docs: int
    canonical: int
    duplicate_same_groups: int
    duplicate_same_docs: int
    duplicate_conflict_groups: int
    id_payload_mismatch: int
    invalid_timestamp: int
    deletable_ids: tuple[str, ...]
    conflict_timestamps: tuple[str, ...]


def _valid_timestamp(value: str) -> bool:
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def audit_monitoring_documents(
    documents: Iterable[tuple[str, dict[str, Any]]],
) -> MonitoringAuditResult:
    total_docs = 0
    invalid_timestamp = 0
    id_payload_mismatch = 0
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}

    for doc_id, payload in documents:
        total_docs += 1
        logical_ts = str(payload.get("ts", "")).strip()
        if not logical_ts or not _valid_timestamp(logical_ts):
            invalid_timestamp += 1
            continue
        if doc_id != logical_ts:
            id_payload_mismatch += 1
        groups.setdefault(logical_ts, []).append((doc_id, payload))

    canonical = 0
    duplicate_same_groups = 0
    duplicate_same_docs = 0
    duplicate_conflict_groups = 0
    deletable_ids: list[str] = []
    conflict_timestamps: list[str] = []

    for logical_ts, items in groups.items():
        if len(items) == 1:
            if items[0][0] == logical_ts:
                canonical += 1
            continue

        payloads = {meaningful_monitoring_payload(payload) for _, payload in items}
        if len(payloads) != 1:
            duplicate_conflict_groups += 1
            conflict_timestamps.append(logical_ts)
            continue

        duplicate_same_groups += 1
        duplicate_same_docs += len(items) - 1
        canonical_items = [item for item in items if item[0] == logical_ts]
        if len(canonical_items) == 1:
            deletable_ids.extend(doc_id for doc_id, _ in items if doc_id != logical_ts)

    return MonitoringAuditResult(
        total_docs=total_docs,
        canonical=canonical,
        duplicate_same_groups=duplicate_same_groups,
        duplicate_same_docs=duplicate_same_docs,
        duplicate_conflict_groups=duplicate_conflict_groups,
        id_payload_mismatch=id_payload_mismatch,
        invalid_timestamp=invalid_timestamp,
        deletable_ids=tuple(sorted(deletable_ids)),
        conflict_timestamps=tuple(sorted(conflict_timestamps)),
    )
