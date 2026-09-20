from __future__ import annotations

from app.operations.monitoring_audit import audit_monitoring_documents


def _payload(ts: str, *, pv: float = 1.0):
    return {
        "ts": ts,
        "pv_kwh": pv,
        "load_kwh": 2.0,
        "sell_kwh": 0.0,
        "buy_kwh": 1.0,
        "charge_kwh": 0.2,
        "discharge_kwh": 0.0,
        "soc_percent": 50.0,
    }


def test_audit_reports_canonical_documents_without_deletions() -> None:
    ts = "2026-09-20T14:30:00"
    result = audit_monitoring_documents([(ts, _payload(ts))])

    assert result.canonical == 1
    assert result.deletable_ids == ()
    assert result.duplicate_same_groups == 0
    assert result.duplicate_conflict_groups == 0


def test_audit_marks_only_exact_duplicate_with_canonical_id_as_deletable() -> None:
    ts = "2026-09-20T14:30:00"
    result = audit_monitoring_documents(
        [
            (ts, _payload(ts)),
            ("legacy-copy", _payload(ts)),
        ]
    )

    assert result.duplicate_same_groups == 1
    assert result.duplicate_same_docs == 1
    assert result.id_payload_mismatch == 1
    assert result.deletable_ids == ("legacy-copy",)


def test_audit_never_marks_conflicting_duplicate_as_deletable() -> None:
    ts = "2026-09-20T14:30:00"
    result = audit_monitoring_documents(
        [
            (ts, _payload(ts, pv=1.0)),
            ("legacy-copy", _payload(ts, pv=2.0)),
        ]
    )

    assert result.duplicate_conflict_groups == 1
    assert result.conflict_timestamps == (ts,)
    assert result.deletable_ids == ()


def test_audit_does_not_delete_exact_duplicates_without_unique_canonical_id() -> None:
    ts = "2026-09-20T14:30:00"
    result = audit_monitoring_documents(
        [
            ("legacy-a", _payload(ts)),
            ("legacy-b", _payload(ts)),
        ]
    )

    assert result.duplicate_same_groups == 1
    assert result.deletable_ids == ()


def test_audit_reports_invalid_timestamp_without_deletion() -> None:
    result = audit_monitoring_documents(
        [("bad-id", _payload("not-a-timestamp"))]
    )

    assert result.invalid_timestamp == 1
    assert result.deletable_ids == ()
