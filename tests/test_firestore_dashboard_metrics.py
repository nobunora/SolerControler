from __future__ import annotations

from typing import Any

import pytest

from app.firestore_ops import recalc_dashboard_daily_metrics


class _Snapshot:
    def __init__(self, row: dict[str, Any]) -> None:
        self.id = str(row["ts"])
        self._row = row

    def to_dict(self) -> dict[str, Any]:
        return dict(self._row)


class _Collection:
    def __init__(self, client: "_Client", name: str) -> None:
        self.client = client
        self.name = name

    def stream(self) -> list[_Snapshot]:
        return [_Snapshot(row) for row in self.client.rows] if self.name == "monitoring_samples" else []

    def document(self, document_id: str) -> tuple[str, str]:
        return self.name, document_id


class _Batch:
    def __init__(self, client: "_Client") -> None:
        self.client = client

    def set(self, ref: tuple[str, str], value: dict[str, Any], *, merge: bool) -> None:
        del merge
        self.client.writes[ref] = value

    def commit(self) -> None:
        return None


class _Client:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.writes: dict[tuple[str, str], dict[str, Any]] = {}

    def collection(self, name: str) -> _Collection:
        return _Collection(self, name)

    def batch(self) -> _Batch:
        return _Batch(self)


def test_dashboard_metrics_materialize_review_night_window_and_morning_soc() -> None:
    client = _Client(
        [
            {"ts": "2026-07-15T23:00:00", "charge_kwh": 1.0, "buy_kwh": 2.0, "soc_percent": 20},
            {"ts": "2026-07-16T00:00:00", "charge_kwh": 2.0, "buy_kwh": 3.0, "soc_percent": 30},
            {"ts": "2026-07-16T07:00:00", "buy_kwh": 4.0, "soc_percent": 80},
            {"ts": "2026-07-16T22:30:00", "buy_kwh": 5.0, "soc_percent": 40},
            {"ts": "2026-07-16T23:00:00", "charge_kwh": 6.0, "soc_percent": 35},
        ]
    )

    assert recalc_dashboard_daily_metrics(client, updated_at="2026-07-17T00:00:00Z") == 2
    metrics = client.writes[("dashboard_daily_metrics", "2026-07-16")]

    assert metrics["review_night_charge_kwh"] == pytest.approx(3.0)
    assert metrics["day_buy_kwh"] == pytest.approx(9.0)
    assert metrics["night_buy_kwh"] == pytest.approx(3.0)
    assert metrics["morning_soc_percent"] == pytest.approx(80.0)
    assert metrics["day_soc_max_percent"] == pytest.approx(80.0)
    assert metrics["first_sample_at"] == "2026-07-16T00:00:00"
    assert metrics["latest_sample_at"] == "2026-07-16T23:00:00"
    assert metrics["sample_count"] == pytest.approx(4.0)
