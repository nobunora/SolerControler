"""Faithful display of existing saved samples; no repair writes or interpolation."""

from copy import deepcopy
from pathlib import Path

import pytest

from app.dashboard import history_reconstruction
from app.dashboard.repositories import DashboardLoadRequest
from app.dashboard.service import merge_forecast_hourly_actuals
from app.dashboard.sqlite_repository import load_sqlite_query_snapshot
from app.operations_db import ensure_schema, open_db

DAY = "2026-10-06"


def sample(hour, minute, *, pv=0.2, load=0.3, soc=40.0):
    return {"ts": f"{DAY}T{hour:02}:{minute:02}:00", "pv_kwh": pv,
            "load_kwh": load, "soc_percent": soc}


@pytest.mark.parametrize("bad", [None, True, "0", -1.0, float("nan"), float("inf")])
def test_invalid_halfhour_stays_missing_and_valid_zero_is_kept(bad):
    rows = [sample(6, 0, pv=0.0, load=0.0), sample(6, 30, pv=0.0, load=bad, soc=41.0)]
    before = deepcopy(rows)
    result = merge_forecast_hourly_actuals([], rows)[0]
    assert result["actual_pv_kwh"] == 0.0
    assert result["actual_load_kwh"] is None
    assert result["actual_soc_percent"] == 41.0
    assert result["opening_soc_percent"] == 40.0
    assert result["forecast_pv_kwh"] is None and result["forecast_run_id"] is None
    if not isinstance(bad, float) or bad == bad:
        assert rows == before


def test_missing_halfhour_does_not_turn_into_a_complete_hour():
    result = merge_forecast_hourly_actuals([], [sample(6, 0)])[0]
    assert result["actual_pv_kwh"] is None and result["actual_load_kwh"] is None
    assert result["actual_soc_percent"] == 40.0


def test_duplicate_slot_conflict_is_not_double_counted():
    rows = [sample(6, 0), sample(6, 30), sample(6, 0, pv=9.0)]
    result = merge_forecast_hourly_actuals([], rows)[0]
    assert result["actual_pv_kwh"] is None
    assert result["actual_load_kwh"] == pytest.approx(0.6)


def test_utc_saved_timestamps_join_the_correct_jst_date_and_hour():
    rows = [{"ts": "2026-10-05T15:00:00+00:00", "pv_kwh": 0.0, "load_kwh": 0.4},
            {"ts": "2026-10-05T15:30:00Z", "pv_kwh": 0.0, "load_kwh": 0.6}]
    forecast = [{"date": DAY, "hour": 0, "forecast_pv_kwh": 0.2, "forecast_run_id": "original"}]
    result = merge_forecast_hourly_actuals(forecast, rows)
    assert len(result) == 1
    assert result[0]["date"] == DAY and result[0]["hour"] == 0
    assert result[0]["actual_load_kwh"] == pytest.approx(1.0)
    assert result[0]["forecast_run_id"] == "original"


def test_existing_valid_actual_and_forecast_values_are_preserved():
    forecast = [{"date": DAY, "hour": 6, "forecast_pv_kwh": 3.0,
                 "actual_load_kwh": 4.0, "actual_soc_percent": 0.0}]
    result = merge_forecast_hourly_actuals(forecast, [sample(6, 0), sample(6, 30)])[0]
    assert result["actual_load_kwh"] == 4.0 and result["actual_soc_percent"] == 0.0
    assert result["forecast_pv_kwh"] == 3.0
    assert "actual_pv_kwh" not in forecast[0]


def test_actual_only_rows_do_not_block_saved_reconstructed_forecasts(monkeypatch):
    actual_only = merge_forecast_hourly_actuals([], [sample(6, 0), sample(6, 30)])
    monkeypatch.setattr(history_reconstruction, "_firestore_forecast_hourly_between", lambda *args, **kwargs: actual_only)
    monkeypatch.setattr(history_reconstruction, "_forecast_plan_metadata_by_date", lambda *args, **kwargs: {})

    def selected(*args, **kwargs):
        assert DAY not in kwargs["original_dates"]
        return [{"date": DAY, "hour": hour, "forecast_pv_kwh": 0.5,
                 "forecast_load_kwh": 0.7, "source": "forecast_history_reconstructed"} for hour in range(24)]

    monkeypatch.setattr(history_reconstruction, "_selected_reconstructed_rows_between", selected)
    monkeypatch.setattr(history_reconstruction, "_monitoring_rows_for_reconstructed_dates",
                        lambda *args, **kwargs: [sample(6, 0), sample(6, 30)])
    result = history_reconstruction.firestore_forecast_hourly_with_reconstruction(
        object(), start_date=DAY, end_date_iso=DAY,
    )
    assert len(result) == 24
    assert result[6]["forecast_pv_kwh"] == 0.5
    assert result[6]["actual_pv_kwh"] == pytest.approx(0.4)


def database(tmp_path: Path, samples):
    path = tmp_path / "display.db"
    conn = open_db(path)
    ensure_schema(conn)
    conn.executemany("INSERT INTO monitoring_samples(ts,pv_kwh,load_kwh,charge_kwh,discharge_kwh,soc_percent,ingested_at) VALUES(?,?,?,?,?,?,?)",
                     [(row["ts"], row.get("pv_kwh"), row.get("load_kwh"), 0.0, 0.0,
                       row.get("soc_percent"), f"{DAY}T23:59:00") for row in samples])
    conn.execute("INSERT INTO forecast_hourly(date,hour,forecast_pv_kwh,forecast_load_kwh,forecast_charge_kwh,source,updated_at,forecast_run_id,forecast_issued_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 (DAY, 7, 0.2, 0.3, 0.0, "original", f"{DAY}T03:00:00", "original-run", f"{DAY}T03:00:00"))
    conn.commit()
    conn.close()
    return path


@pytest.mark.parametrize("missing", [False, True])
def test_sqlite_reads_saved_pv_load_and_actual_only_hours_without_zero_fill(tmp_path, monkeypatch, missing):
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    rows = [sample(hour, minute, pv=0.0, load=0.3) for hour in range(24) for minute in (0, 30)]
    if missing:
        rows[-1]["load_kwh"] = None
    path = database(tmp_path, rows)
    before = path.read_bytes()
    result = load_sqlite_query_snapshot(path, DashboardLoadRequest(DAY, 1, False))
    assert path.read_bytes() == before  # Display reads do not repair or materialize storage.
    assert len(result.forecast_hourly) == 24
    hourly = {row["hour"]: row for row in result.forecast_hourly}
    assert hourly[7]["forecast_run_id"] == "original-run"
    assert hourly[6]["forecast_pv_kwh"] is None
    assert hourly[6]["actual_pv_kwh"] == 0.0
    assert hourly[6]["actual_load_kwh"] == pytest.approx(0.6)
    assert (hourly[23]["actual_load_kwh"] is None) == missing
    assert result.monitoring_daily[0]["actual_pv_kwh"] == 0.0
    assert (result.monitoring_daily[0]["actual_load_kwh"] is None) == missing


def test_sqlite_null_pair_is_unknown_with_original_forecast_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    path = database(tmp_path, [sample(7, 0, pv=None, load=None), sample(7, 30, pv=None, load=None)])
    result = load_sqlite_query_snapshot(path, DashboardLoadRequest(DAY, 1, False)).forecast_hourly[0]
    assert result["actual_pv_kwh"] is None and result["actual_load_kwh"] is None
    assert result["forecast_pv_kwh"] == 0.2 and result["forecast_run_id"] == "original-run"
