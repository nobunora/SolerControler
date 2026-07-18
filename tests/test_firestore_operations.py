from __future__ import annotations

from app.firestore_ops import recalc_cost_daily


class _Document:
    def __init__(self, document_id: str, payload=None) -> None:
        self.id = document_id
        self.payload = payload or {}

    def to_dict(self):
        return dict(self.payload)


class _Collection:
    def __init__(self, name: str, documents: list[_Document]) -> None:
        self.name = name
        self.documents = documents

    def order_by(self, _field: str):
        return self

    def stream(self):
        return iter(self.documents)

    def document(self, document_id: str) -> _Document:
        return _Document(document_id)


class _Batch:
    def __init__(self, owner) -> None:
        self.owner = owner

    def set(self, document: _Document, payload, *, merge: bool) -> None:
        self.owner.writes.append((document.id, dict(payload), merge))

    def commit(self) -> None:
        self.owner.commits += 1


class _Client:
    def __init__(self) -> None:
        self.writes = []
        self.commits = 0
        self.monitoring = [
            _Document("2026-05-01T07:00:00+09:00", {"load_kwh": 2.0, "buy_kwh": 0.5})
        ]

    def collection(self, name: str) -> _Collection:
        return _Collection(name, self.monitoring if name == "monitoring_samples" else [])

    def batch(self) -> _Batch:
        return _Batch(self)


def test_firestore_daily_cost_maps_domain_result_to_existing_document() -> None:
    client = _Client()

    recalc_cost_daily(
        client,
        day_rate_yen_per_kwh=31.0,
        updated_at="2026-05-02T00:00:00Z",
    )

    assert client.writes == [
        (
            "2026-05-01",
            {
                "date": "2026-05-01",
                "self_consumption_kwh": 1.5,
                "savings_yen": 46.5,
                "cumulative_kwh": 1.5,
                "cumulative_yen": 46.5,
                "updated_at": "2026-05-02T00:00:00Z",
            },
            True,
        )
    ]
    assert client.commits == 1
