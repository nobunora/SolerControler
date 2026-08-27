from __future__ import annotations

import pytest

from app.runtime.plan_persistence import acquire_night_soc_lease, can_apply_day_transition


class _Snapshot:
    def __init__(self, *, exists: bool, data: dict | None = None) -> None:
        self.exists = exists
        self._data = data or {}

    def to_dict(self) -> dict:
        return self._data


class _Document:
    def __init__(self) -> None:
        self.payload: dict | None = None

    def set(self, payload: dict, *, merge: bool) -> None:
        self.payload = payload


class _ReadDocument:
    def __init__(self, snapshot: _Snapshot) -> None:
        self.snapshot = snapshot

    def get(self) -> _Snapshot:
        return self.snapshot


class _ReadCollection:
    def __init__(self, document: _ReadDocument) -> None:
        self.document_ref = document

    def document(self, _: str) -> _ReadDocument:
        return self.document_ref


class _ReadClient:
    def __init__(self, snapshot: _Snapshot) -> None:
        self.document_ref = _ReadDocument(snapshot)

    def collection(self, _: str) -> _ReadCollection:
        return _ReadCollection(self.document_ref)


class _Collection:
    def __init__(self, document: _Document) -> None:
        self.document_ref = document

    def document(self, _: str) -> _Document:
        return self.document_ref


class _Transaction:
    def __init__(self, snapshot_result: object, document: _Document) -> None:
        self.snapshot_result = snapshot_result
        self.document = document
        self.begun = False
        self.committed = False
        self._max_attempts = 1
        self._read_only = False
        self._id = b"test-transaction"

    def _begin(self, *, retry_id: bytes | None = None) -> None:
        self.begun = True

    def _clean_up(self) -> None:
        self.begun = False

    def get(self, _: _Document) -> object:
        if not self.begun:
            raise RuntimeError("Transaction not in progress, cannot be used in API requests.")
        return self.snapshot_result

    def set(self, _: _Document, payload: dict, *, merge: bool) -> None:
        self.document.set(payload, merge=merge)

    def _commit(self) -> list[object]:
        if not self.begun:
            raise RuntimeError("Transaction not in progress, cannot be committed.")
        self.committed = True
        return []

    def _rollback(self) -> None:
        self.begun = False

    def commit(self) -> None:
        self._commit()


class _Client:
    def __init__(self, snapshot_result: object) -> None:
        self.document_ref = _Document()
        self.transaction_ref = _Transaction(snapshot_result, self.document_ref)

    def collection(self, _: str) -> _Collection:
        return _Collection(self.document_ref)

    def transaction(self) -> _Transaction:
        return self.transaction_ref


@pytest.mark.parametrize(
    "snapshot_result",
    [
        _Snapshot(exists=False),
        (_snapshot for _snapshot in [_Snapshot(exists=False)]),
        (_snapshot for _snapshot in []),
    ],
    ids=["snapshot", "generator", "empty-generator"],
)
def test_acquire_night_soc_lease_accepts_firestore_get_shapes(snapshot_result: object) -> None:
    client = _Client(snapshot_result)

    acquired = acquire_night_soc_lease(
        plan_meta={"date": "2026-08-21", "plan_id": "plan-1"},
        owner="03-monitor",
        lease_seconds=18000,
        open_firestore=lambda: client,
    )

    assert acquired is True
    assert client.transaction_ref.begun is True
    assert client.transaction_ref.committed is True
    assert client.document_ref.payload is not None
    assert client.document_ref.payload["state"] == "LEASE_ACQUIRED"


def test_acquire_night_soc_lease_rejects_active_owner_from_generator() -> None:
    snapshot = _Snapshot(
        exists=True,
        data={
            "plan_id": "plan-1",
            "owner": "another-owner",
            "lease_expires_at_utc": "9999-12-31T23:59:59+00:00",
        },
    )
    client = _Client((item for item in [snapshot]))

    acquired = acquire_night_soc_lease(
        plan_meta={"date": "2026-08-21", "plan_id": "plan-1"},
        owner="03-monitor",
        lease_seconds=18000,
        open_firestore=lambda: client,
    )

    assert acquired is False
    assert client.transaction_ref.committed is True
    assert client.document_ref.payload is None


def test_day_transition_manual_owner_requires_explicit_allowance() -> None:
    client = _ReadClient(
        _Snapshot(
            exists=True,
            data={
                "state": "MANUAL_OPERATION",
                "owner": "manual",
                "device_write_skipped": True,
                "plan_date": "2026-08-27",
                "plan_id": "2026-08-27-1-dummy",
                "updated_at_utc": "2026-08-27T00:00:00Z",
            },
        )
    )

    assert can_apply_day_transition(
        plan_date="2026-08-27",
        open_firestore=lambda: client,
    ) is False
    assert can_apply_day_transition(
        plan_date="2026-08-27",
        open_firestore=lambda: client,
        allow_manual_owner=True,
        max_handoff_age_seconds=100000000,
    ) is True


@pytest.mark.parametrize(
    "changes",
    [
        {"owner": "03-monitor"},
        {"device_write_skipped": False},
        {"plan_id": "2026-08-26-1-dummy"},
        {"updated_at_utc": "not-a-timestamp"},
    ],
)
def test_day_transition_rejects_invalid_manual_handoff(changes: dict) -> None:
    record = {
        "state": "MANUAL_OPERATION",
        "owner": "manual",
        "device_write_skipped": True,
        "plan_date": "2026-08-27",
        "plan_id": "2026-08-27-1-dummy",
        "updated_at_utc": "2026-08-27T00:00:00Z",
    }
    record.update(changes)
    client = _ReadClient(_Snapshot(exists=True, data=record))

    assert can_apply_day_transition(
        plan_date="2026-08-27",
        open_firestore=lambda: client,
        allow_manual_owner=True,
        expected_plan_id="2026-08-27-1-dummy",
        max_handoff_age_seconds=100000000,
    ) is False
