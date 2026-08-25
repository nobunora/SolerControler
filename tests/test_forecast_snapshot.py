from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.operations import sqlite as sqlite_ops
from app.operations.forecast_snapshot import (
    build_forecast_snapshot_rows,
    persist_forecast_snapshots,
    select_latest_snapshot_before,
)


def _plan(*, pv_kwh: float = 1.2, issued_at: str | None = "2026-08-26T00:00:00+09:00") -> dict:
    plan = {
        "forecast": {
            "date": "2026-08-27",
            "source": "open-meteo",
            "model_version": "weather-v1",
            "hourly_weather": [
                {
                    "hour": 10,
                    "weather_code": 2,
                    "cloud_cover": 35,
                    "shortwave_radiation_w_m2": 500,
                    "temp_c": 31.5,
                    "relative_humidity_percent": 68.0,
                    "dew_point_c": 24.9,
                    "wind_speed_10m": 3.4,
                }
            ],
        },
        "pv_array_forecast": {
            "source": "ensemble-forecast-solar-open-meteo",
            "provider": "ensemble",
            "model_version": "pv-v2",
            "calibration": {"effective_factor": 0.93},
            "hourly": [
                {
                    "time": "2026-08-27T10:00+09:00",
                    "total_kwh": 1.15,
                    "forecast_solar_kwh": 1.2,
                    "open_meteo_kwh": 1.05,
                    "ensemble_method": "midday_blend",
                }
            ],
            "provider_forecasts": {
                "forecast_solar": {
                    "hourly": [
                        {
                            "time": "2026-08-27T10:00+09:00",
                            "total_kwh": 1.2,
                        }
                    ]
                },
                "open_meteo": {
                    "hourly": [
                        {
                            "time": "2026-08-27T10:00+09:00",
                            "total_kwh": 1.05,
                            "roof_gti_w_m2": 510,
                            "temp_c": 31.5,
                        }
                    ]
                },
            },
        },
        "result": {"final_pv_forecast_source": "physical_pv_forecast"},
        "daytime_soc_optimization": {
            "hourly_pv_forecast_kwh": {"10": pv_kwh},
            "hourly_load_forecast_kwh": {"10": 0.4},
        },
    }
    if issued_at is not None:
        plan["issued_at"] = issued_at
    return plan


def _write_plan(path: Path, plan: dict) -> None:
    path.write_text(json.dumps(plan), encoding="utf-8")


def test_snapshot_builder_records_issue_lead_provenance_and_physical_evidence() -> None:
    rows = build_forecast_snapshot_rows(
        _plan(),
        ingested_at="2026-08-25T15:05:00Z",
        timezone="Asia/Tokyo",
        source_run_key="site:23:csv:settings-a",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["issued_at"] == "2026-08-26T00:00:00+09:00"
    assert row["issued_at_source"] == "plan"
    assert row["target_at"] == "2026-08-27T10:00:00+09:00"
    assert row["lead_minutes"] == 2040
    assert row["forecast_provider"] == "open-meteo"
    assert row["pv_input_source"] == "ensemble-forecast-solar-open-meteo"
    assert row["pv_provider"] == "ensemble"
    assert row["pv_model_source"] == "physical_pv_forecast"
    assert row["pv_model_version"] == "pv-v2"
    assert row["weather_model_version"] == "weather-v1"
    assert row["forecast_pv_calibration_factor"] == pytest.approx(0.93)
    assert row["physical_pv_kwh"] == pytest.approx(1.15)
    detail = json.loads(row["pv_forecast_detail_json"])
    assert detail["selected"]["ensemble_method"] == "midday_blend"
    assert detail["providers"]["forecast_solar"]["total_kwh"] == pytest.approx(1.2)
    assert detail["providers"]["open_meteo"]["roof_gti_w_m2"] == 510
    assert json.loads(row["quality_flags_json"]) == []


def test_snapshot_builder_marks_fallback_issue_time_and_invalid_shortwave() -> None:
    plan = _plan(issued_at=None)
    plan["forecast"]["hourly_weather"][0]["shortwave_radiation_w_m2"] = 0

    row = build_forecast_snapshot_rows(
        plan,
        ingested_at="2026-08-26T00:10:00Z",
        timezone="Asia/Tokyo",
    )[0]

    assert row["issued_at"] == "2026-08-26T00:10:00Z"
    assert row["issued_at_source"] == "ingested_at_fallback"
    assert row["lead_minutes"] == 1490
    assert set(json.loads(row["quality_flags_json"])) == {
        "issued_at_ingested_fallback",
        "nonpositive_shortwave_with_positive_pv",
    }


def test_snapshot_builder_marks_missing_physical_stage_evidence() -> None:
    plan = _plan()
    plan["pv_array_forecast"]["hourly"] = []
    plan["pv_array_forecast"]["provider_forecasts"] = {}

    row = build_forecast_snapshot_rows(
        plan,
        ingested_at="2026-08-25T15:05:00Z",
        timezone="Asia/Tokyo",
    )[0]

    assert row["physical_pv_kwh"] is None
    assert json.loads(row["pv_forecast_detail_json"]) == {}
    assert "missing_physical_pv_detail" in json.loads(row["quality_flags_json"])


def test_sqlite_keeps_multiple_vintages_while_latest_contract_still_overwrites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sqlite_ops, "_fetch_open_meteo_daily_actual", lambda **_kwargs: {})
    conn = sqlite_ops.open_db(tmp_path / "solar.db")
    plan_path = tmp_path / "night_charge_plan.json"
    try:
        sqlite_ops.ensure_schema(conn)

        first = _plan(pv_kwh=1.2, issued_at="2026-08-26T00:00:00+09:00")
        _write_plan(plan_path, first)
        sqlite_ops.ingest_sunshine_from_night_plan(
            conn,
            night_plan_path=plan_path,
            timezone="Asia/Tokyo",
            ingested_at="2026-08-25T15:00:00Z",
        )
        assert persist_forecast_snapshots(
            conn,
            backend="sqlite",
            night_plan_path=plan_path,
            timezone="Asia/Tokyo",
            ingested_at="2026-08-25T15:00:00Z",
            source_run_key="run-a",
        ) == 1

        second = _plan(pv_kwh=1.6, issued_at="2026-08-26T02:00:00+09:00")
        second["pv_array_forecast"]["hourly"][0]["total_kwh"] = 1.5
        _write_plan(plan_path, second)
        sqlite_ops.ingest_sunshine_from_night_plan(
            conn,
            night_plan_path=plan_path,
            timezone="Asia/Tokyo",
            ingested_at="2026-08-25T17:00:00Z",
        )
        assert persist_forecast_snapshots(
            conn,
            backend="sqlite",
            night_plan_path=plan_path,
            timezone="Asia/Tokyo",
            ingested_at="2026-08-25T17:00:00Z",
            source_run_key="run-b",
        ) == 1

        latest = conn.execute(
            "SELECT forecast_pv_kwh FROM forecast_hourly WHERE date='2026-08-27' AND hour=10"
        ).fetchone()
        snapshots = conn.execute(
            """
            SELECT issued_at, forecast_pv_kwh, physical_pv_kwh
            FROM forecast_hourly_snapshots
            WHERE date='2026-08-27' AND hour=10
            ORDER BY issued_at
            """
        ).fetchall()

        assert latest["forecast_pv_kwh"] == pytest.approx(1.6)
        assert [(row["issued_at"], row["forecast_pv_kwh"], row["physical_pv_kwh"]) for row in snapshots] == [
            ("2026-08-26T00:00:00+09:00", pytest.approx(1.2), pytest.approx(1.15)),
            ("2026-08-26T02:00:00+09:00", pytest.approx(1.6), pytest.approx(1.5)),
        ]
        assert persist_forecast_snapshots(
            conn,
            backend="sqlite",
            night_plan_path=plan_path,
            timezone="Asia/Tokyo",
            ingested_at="2026-08-25T17:00:00Z",
            source_run_key="run-b",
        ) == 0
        assert conn.execute("SELECT COUNT(*) FROM forecast_hourly_snapshots").fetchone()[0] == 2
    finally:
        conn.close()


def test_sqlite_migrates_snapshot_table_created_by_phase0_v1(tmp_path: Path) -> None:
    conn = sqlite_ops.open_db(tmp_path / "legacy-snapshot.db")
    plan_path = tmp_path / "night_charge_plan.json"
    _write_plan(plan_path, _plan())
    try:
        conn.executescript(
            """
            CREATE TABLE forecast_hourly_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                forecast_run_id TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                issued_at_source TEXT NOT NULL,
                target_at TEXT NOT NULL,
                lead_minutes INTEGER NOT NULL,
                date TEXT NOT NULL,
                hour INTEGER NOT NULL,
                forecast_pv_kwh REAL,
                forecast_load_kwh REAL,
                forecast_charge_kwh REAL,
                forecast_weather_code INTEGER,
                forecast_precipitation_mm REAL,
                forecast_precipitation_probability REAL,
                forecast_cloud_cover REAL,
                forecast_shortwave_radiation_w_m2 REAL,
                forecast_temp_c REAL,
                forecast_relative_humidity_percent REAL,
                forecast_dew_point_c REAL,
                forecast_wind_speed_10m REAL,
                forecast_provider TEXT,
                pv_input_source TEXT,
                pv_model_source TEXT,
                pv_model_version TEXT,
                weather_model_version TEXT,
                forecast_pv_calibration_factor REAL,
                quality_flags_json TEXT NOT NULL,
                source TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            """
        )

        assert persist_forecast_snapshots(
            conn,
            backend="sqlite",
            night_plan_path=plan_path,
            timezone="Asia/Tokyo",
            ingested_at="2026-08-25T15:00:00Z",
            source_run_key="run-after-v1",
        ) == 1

        columns = {row[1] for row in conn.execute("PRAGMA table_info(forecast_hourly_snapshots)").fetchall()}
        assert {"pv_provider", "physical_pv_kwh", "pv_forecast_detail_json"} <= columns
        stored = conn.execute(
            "SELECT pv_provider, physical_pv_kwh, pv_forecast_detail_json FROM forecast_hourly_snapshots"
        ).fetchone()
        assert stored["pv_provider"] == "ensemble"
        assert stored["physical_pv_kwh"] == pytest.approx(1.15)
        assert json.loads(stored["pv_forecast_detail_json"])["providers"]["open_meteo"]["roof_gti_w_m2"] == 510
    finally:
        conn.close()


def test_snapshot_reader_never_selects_a_future_issued_forecast() -> None:
    rows = [
        {"date": "2026-08-27", "hour": 10, "issued_at": "2026-08-26T00:00:00+09:00", "value": "old"},
        {"date": "2026-08-27", "hour": 10, "issued_at": "2026-08-26T02:00:00+09:00", "value": "future"},
        {"date": "2026-08-27", "hour": 11, "issued_at": "2026-08-26T00:30:00+09:00", "value": "other-hour"},
        {"date": "2026-08-27", "hour": "bad", "issued_at": "2026-08-26T00:45:00+09:00", "value": "bad-hour"},
    ]

    selected = select_latest_snapshot_before(
        rows,
        target_date="2026-08-27",
        hour=10,
        cutoff_at="2026-08-26T01:00:00+09:00",
        timezone="Asia/Tokyo",
    )

    assert selected is not None
    assert selected["value"] == "old"


class _Snapshot:
    def __init__(self, exists: bool) -> None:
        self.exists = exists


class _Document:
    def __init__(self, owner, document_id: str) -> None:
        self.owner = owner
        self.id = document_id

    def get(self) -> _Snapshot:
        return _Snapshot(self.id in self.owner.documents)


class _Collection:
    def __init__(self, owner) -> None:
        self.owner = owner

    def document(self, document_id: str) -> _Document:
        return _Document(self.owner, document_id)


class _Batch:
    def __init__(self, owner) -> None:
        self.owner = owner
        self.pending: list[tuple[_Document, dict]] = []

    def create(self, document: _Document, payload: dict) -> None:
        self.pending.append((document, dict(payload)))

    def commit(self) -> None:
        for document, payload in self.pending:
            assert document.id not in self.owner.documents
            self.owner.documents[document.id] = payload


class _FirestoreClient:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    def collection(self, name: str) -> _Collection:
        assert name == "forecast_hourly_snapshots"
        return _Collection(self)

    def batch(self) -> _Batch:
        return _Batch(self)


def test_firestore_snapshot_store_is_append_only_and_idempotent(tmp_path: Path) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    _write_plan(plan_path, _plan())
    client = _FirestoreClient()

    first = persist_forecast_snapshots(
        client,
        backend="firestore",
        night_plan_path=plan_path,
        timezone="Asia/Tokyo",
        ingested_at="2026-08-25T15:00:00Z",
        source_run_key="run-a",
    )
    repeated = persist_forecast_snapshots(
        client,
        backend="firestore",
        night_plan_path=plan_path,
        timezone="Asia/Tokyo",
        ingested_at="2026-08-25T15:05:00Z",
        source_run_key="run-a",
    )
    second = persist_forecast_snapshots(
        client,
        backend="firestore",
        night_plan_path=plan_path,
        timezone="Asia/Tokyo",
        ingested_at="2026-08-25T15:10:00Z",
        source_run_key="run-b",
    )

    assert (first, repeated, second) == (1, 0, 1)
    assert len(client.documents) == 2
    assert all(document["quality_flags"] == [] for document in client.documents.values())
    assert all(document["pv_provider"] == "ensemble" for document in client.documents.values())
    assert all(document["physical_pv_kwh"] == pytest.approx(1.15) for document in client.documents.values())
    assert all(
        document["pv_forecast_detail"]["providers"]["open_meteo"]["roof_gti_w_m2"] == 510
        for document in client.documents.values()
    )
