from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.operations.firestore import open_firestore
from app.operations.monitoring_audit import audit_monitoring_documents


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit monitoring_samples for legacy duplicate or ID/timestamp inconsistencies."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete only exact duplicate documents when one canonical document ID exactly matches payload ts.",
    )
    args = parser.parse_args()

    client = open_firestore()
    snapshots = list(client.collection("monitoring_samples").stream())
    result = audit_monitoring_documents(
        (snap.id, snap.to_dict() or {}) for snap in snapshots
    )

    deleted = 0
    if args.apply and result.deletable_ids:
        batch = client.batch()
        count = 0
        for doc_id in result.deletable_ids:
            batch.delete(client.collection("monitoring_samples").document(doc_id))
            count += 1
            deleted += 1
            if count >= 450:
                batch.commit()
                batch = client.batch()
                count = 0
        if count:
            batch.commit()

    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "total_docs": result.total_docs,
                "canonical": result.canonical,
                "duplicate_same_groups": result.duplicate_same_groups,
                "duplicate_same_docs": result.duplicate_same_docs,
                "duplicate_conflict_groups": result.duplicate_conflict_groups,
                "id_payload_mismatch": result.id_payload_mismatch,
                "invalid_timestamp": result.invalid_timestamp,
                "deletable_docs": len(result.deletable_ids),
                "deleted_docs": deleted,
                "conflict_timestamps": list(result.conflict_timestamps),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
