from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.operations.forecast_persistence import persist_forecast_only_plan


class _Doc:
    def __init__(self, client: "_Client", collection: str, doc_id: str) -> None:
        self.client = client
        self.collection_name = collection
        self.id = doc_id
        self.reference = self

    @property
    def exists(self) -> bool:
        return self.id in self.client.data.setdefault(self.collection_name, {})

    def get(self) -> "_Doc":
        return self

    def to_dict(self) -> dict:
        return dict(self.client.data.setdefault(self.collection_name, {}).get(self.id, {}))


class _Query:
    def __init__(self, client: "_Client", collection: str, date: str) -> None:
        self.client = client
        self.collection_name = collection
        self.date = date

    def stream(self) -> list[_Doc]:
        return [
            _Doc(self.client, self.collection_name, doc_id)
            for doc_id, row in self.client.data.setdefault(self.collection_name, {}).items()
            if row.get("date") == self.date
        ]


class _Collection:
    def __init__(self, client: "_Client", name: str) -> None:
        self.client = client
        self.name = name

    def document(self, doc_id: str) -> _Doc:
        return _Doc(self.client, self.name, doc_id)

    def where(self, field: str, operator: str, value: str) -> _Query:
        assert (field, operator) == ("date", "==")
        return _Query(self.client, self.name, value)


class _Batch:
    def __init__(self, client: "_Client") -> None:
        self.client = client
        self.pending: list[tuple[str, _Doc, dict | None]] = []

    def delete(self, doc: _Doc) -> None:
        self.pending.append(("delete", doc, None))

    def set(self, doc: _Doc, payload: dict, merge: bool = False) -> None:
        self.pending.append(("set", doc, dict(payload)))

    def create(self, doc: _Doc, payload: dict) -> None:
        self.pending.append(("create", doc, dict(payload)))

    def commit(self) -> None:
        for action, doc, payload in self.pending:
            rows = self.client.data.setdefault(doc.collection_name, {})
            if action == "delete":
                rows.pop(doc.id, None)
            elif action == "create":
                assert doc.id not in rows
                rows[doc.id] = payload or {}
            else:
                rows[doc.id] = payload or {}


class _Client:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, dict]] = {}

    def collection(self, name: str) -> _Collection:
        return _Collection(self, name)

    def batch(self) -> _Batch:
        return _Batch(self)


def _plan(*, date: str = "2026-09-03", issued_at: str = "2026-09-03T02:30:00+09:00") -> dict:
    return {
        "generated_at": issued_at,
        "forecast": {"date": date, "sun_hours": 4.0, "temp_c": 25.0, "weather_code": 1, "hourly_weather": [{"hour": hour} for hour in range(24)]},
        "daytime_soc_optimization": {"hourly_pv_forecast_kwh": {str(hour): 0.1 for hour in range(24)}, "hourly_load_forecast_kwh": {str(hour): 0.2 for hour in range(24)}},
        "pv_array_forecast": {"source": "test", "totals": {"total_kwh": 2.4, "morning_kwh": 0.8, "midday_kwh": 1.2, "evening_kwh": 0.4}},
        "result": {
            "final_pv_forecast_source": "test",
            "target_soc_7_percent": 67.5,
            "required_night_charge_kwh": 3.25,
        },
    }


def _write_plan(tmp_path: Path, plan: dict) -> Path:
    path = tmp_path / "night_charge_plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def test_valid_forecast_persists_only_forecast_collections(tmp_path: Path) -> None:
    client = _Client()
    persisted = persist_forecast_only_plan(client, plan_path=_write_plan(tmp_path, _plan()), target_date="2026-09-03", timezone_name="Asia/Tokyo", recorded_at="2026-09-02T17:30:00Z")

    assert persisted == 24
    assert len(client.data["forecast_hourly"]) == 24
    assert len(client.data["forecast_hourly_snapshots"]) == 24
    assert client.data["sunshine_daily"]["2026-09-03"]["forecast_pv_total_kwh"] == pytest.approx(2.4)
    forecast_plan = client.data["forecast_plans"]["2026-09-03"]
    assert forecast_plan["hourly_row_count"] == 24
    assert forecast_plan["planned_target_soc_percent"] == pytest.approx(67.5)
    assert forecast_plan["planned_night_charge_kwh"] == pytest.approx(3.25)
    assert "night_charge_plans" not in client.data


def test_invalid_forecast_never_deletes_existing_mutable_rows(tmp_path: Path) -> None:
    client = _Client()
    client.data["forecast_hourly"] = {"existing": {"date": "2026-09-03", "hour": 12, "forecast_pv_kwh": 9.0}}
    invalid = _plan()
    invalid["daytime_soc_optimization"]["hourly_pv_forecast_kwh"].pop("23")
    invalid["daytime_soc_optimization"]["hourly_load_forecast_kwh"].pop("23")
    invalid["forecast"]["hourly_weather"] = invalid["forecast"]["hourly_weather"][:-1]

    with pytest.raises(ValueError, match="hours 0 through 23"):
        persist_forecast_only_plan(client, plan_path=_write_plan(tmp_path, invalid), target_date="2026-09-03", timezone_name="Asia/Tokyo")

    assert client.data["forecast_hourly"]["existing"]["forecast_pv_kwh"] == 9.0


def test_target_date_mismatch_refuses_persistence(tmp_path: Path) -> None:
    client = _Client()

    with pytest.raises(ValueError, match="target date"):
        persist_forecast_only_plan(client, plan_path=_write_plan(tmp_path, _plan(date="2026-09-04")), target_date="2026-09-03", timezone_name="Asia/Tokyo")

    assert client.data == {}


def test_snapshot_is_idempotent_and_new_vintage_updates_mutable_rows(tmp_path: Path) -> None:
    client = _Client(); path = _write_plan(tmp_path, _plan())
    assert persist_forecast_only_plan(client, plan_path=path, target_date="2026-09-03", timezone_name="Asia/Tokyo") == 24
    assert persist_forecast_only_plan(client, plan_path=path, target_date="2026-09-03", timezone_name="Asia/Tokyo") == 0
    path = _write_plan(tmp_path, _plan(issued_at="2026-09-03T02:31:00+09:00"))
    assert persist_forecast_only_plan(client, plan_path=path, target_date="2026-09-03", timezone_name="Asia/Tokyo") == 24
    assert len(client.data["forecast_hourly_snapshots"]) == 48
    assert len(client.data["forecast_hourly"]) == 24
