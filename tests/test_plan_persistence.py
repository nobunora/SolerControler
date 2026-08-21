from __future__ import annotations

import pytest

from app.runtime.plan_persistence import acquire_night_soc_lease


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


class _Collection:
    def __init__(self, document: _Document) -> None:
        self.document_ref = document

    def document(self, _: str) -> _Document:
        return self.document_ref


class _Transaction:
    def __init__(self, snapshot_result: object, document: _Document) -> None:
        self.snapshot_result = snapshot_result
        self.document = document
        self.committed = False

    def get(self, _: _Document) -> object:
        return self.snapshot_result

    def set(self, _: _Document, payload: dict, *, merge: bool) -> None:
        self.document.set(payload, merge=merge)

    def commit(self) -> None:
        self.committed = True


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
    assert client.transaction_ref.committed is False
    assert client.document_ref.payload is None
