from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.dashboard.repositories import DashboardLoadRequest
from app.dashboard.sqlite_repository import load_sqlite_query_snapshot
from app.operations import sqlite as storage
from app.operations.sync import TABLE_SPECS, _insert_sqlite_row, _sqlite_upsert_row


@pytest.mark.parametrize("upsert", [False, True])
def test_sync_preserves_night_plan_values_and_vintage(tmp_path: Path, upsert: bool) -> None:
    path = tmp_path / "solar.db"
    with storage.open_db(path) as conn:
        storage.ensure_schema(conn)
        row = {"date": "2026-10-17", "hour": 3, "forecast_soc_percent": 0.0,
               "forecast_grid_charge_kwh": 3.684, "forecast_run_id": "original-03",
               "forecast_issued_at": "2026-10-16T18:00:00Z", "updated_at": "2026-10-16T18:01:00Z"}
        _insert_sqlite_row(conn, "forecast_hourly", row)
        if upsert:
            _sqlite_upsert_row(conn, "forecast_hourly", {**row, "forecast_grid_charge_kwh": 0.0})
    snapshot = load_sqlite_query_snapshot(path, DashboardLoadRequest("2026-10-17", 1, False))
    actual = snapshot.forecast_hourly[0]
    assert actual["forecast_soc_percent"] == 0.0
    assert actual["forecast_grid_charge_kwh"] == (0.0 if upsert else 3.684)
    assert actual["forecast_run_id"] == "original-03"
    assert actual["forecast_issued_at"] == row["forecast_issued_at"]
    assert {"forecast_soc_percent", "forecast_grid_charge_kwh"} <= set(TABLE_SPECS["forecast_hourly"]["columns"])


def test_legacy_sqlite_read_is_null_then_schema_migrates(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with storage.open_db(path) as conn:
        storage.ensure_schema(conn)
        for column in ("forecast_soc_percent", "forecast_grid_charge_kwh"):
            conn.execute(f"ALTER TABLE forecast_hourly DROP COLUMN {column}")
        conn.execute("INSERT INTO forecast_hourly (date, hour, updated_at) VALUES ('2026-10-17', 3, 'original')")
    snapshot = load_sqlite_query_snapshot(path, DashboardLoadRequest("2026-10-17", 1, False))
    assert snapshot.forecast_hourly[0]["forecast_soc_percent"] is None
    assert snapshot.forecast_hourly[0]["forecast_grid_charge_kwh"] is None
    with storage.open_db(path) as conn:
        storage.ensure_schema(conn)
        row = conn.execute("SELECT forecast_soc_percent, forecast_grid_charge_kwh FROM forecast_hourly").fetchone()
        assert tuple(row) == (None, None)


def test_plan_ingestion_preserves_night_projection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "_fetch_open_meteo_daily_actual", lambda **_kwargs: {})
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({
        "forecast": {"date": "2026-10-17"},
        "daytime_soc_optimization": {"hourly_pv_forecast_kwh": {"3": 0, "4": 0},
                                     "hourly_load_forecast_kwh": {"3": 0.1, "4": 0.1}},
        "decision_rationale": {"night_soc_projection": {
            "target_date": "2026-10-17", "status": "ready",
            "hourly_soc_percent": {"3": 0, "4": 39},
            "hourly_grid_charge_kwh": {"3": 3.684, "4": 0},
        }},
    }), encoding="utf-8")
    with storage.open_db(tmp_path / "solar.db") as conn:
        storage.ensure_schema(conn)
        storage.ingest_sunshine_from_night_plan(conn, night_plan_path=path,
            timezone="Asia/Tokyo", ingested_at="2026-10-16T18:00:00Z")
        rows = conn.execute("SELECT hour, forecast_soc_percent, forecast_grid_charge_kwh FROM forecast_hourly ORDER BY hour").fetchall()
        assert [tuple(row) for row in rows] == [(3, 0.0, 3.684), (4, 39.0, 0.0)]
