from copy import deepcopy
from datetime import datetime

import pytest

from app.forecasting import correction_history_io as history
from app.operations import sqlite as sqlite_ops


DAY = "2026-10-06"
CUTOFF = datetime.fromisoformat("2026-10-08T02:30:00+09:00")


def rows():
    return [{"date": DAY, "hour": hour, "forecast_pv_kwh": 0.0 if hour < 7 else 1.0,
             "forecast_load_kwh": 0.2, "forecast_shortwave_radiation_w_m2": 0.0 if hour < 7 else 500.0,
             "forecast_weather_code": 3, "forecast_run_id": "original",
             "issued_at": "2026-10-06T03:03:19+09:00",
             "recorded_at": "2026-10-07T09:12:18+09:00",
             "updated_at": "2026-10-06T03:03:19+09:00"} for hour in range(24)]


def select(canonical, snapshots, *, cutoff=CUTOFF, target="2026-10-08"):
    return history._select_calibration_history(canonical, snapshots, target_date=target, as_of=cutoff)


def test_complete_original_fills_gap_and_preserves_zero_without_mutating_inputs():
    saved = rows()
    original = deepcopy(saved)
    result = select([], saved)
    assert len(result[DAY]) == 24
    assert result[DAY][0]["pv"] == 0
    assert result[DAY][10]["pv"] == 1
    assert saved == original


def test_complete_canonical_wins_over_original_snapshot():
    canonical = rows()
    for row in canonical:
        row["forecast_pv_kwh"] = 2.0
    assert select(canonical, rows())[DAY][10]["pv"] == 2


def test_partial_canonical_is_replaced_by_one_complete_original_run():
    assert select(rows()[:-1], rows())[DAY][10]["pv"] == 1


@pytest.mark.parametrize("problem", ["missing_hour", "duplicate_hour", "missing_pv", "missing_sw",
                                   "nan", "negative", "late", "missing_issue", "future_recording",
                                   "mixed_runs"])
def test_invalid_snapshot_cannot_become_calibration_input(problem):
    saved = rows()
    if problem == "missing_hour":
        saved.pop()
    elif problem == "duplicate_hour":
        saved[-1]["hour"] = 0
    elif problem in {"missing_pv", "missing_sw", "nan", "negative"}:
        key = "forecast_shortwave_radiation_w_m2" if problem == "missing_sw" else "forecast_pv_kwh"
        saved[10][key] = {"nan": float("nan"), "negative": -1}.get(problem)
    elif problem == "late":
        for row in saved:
            row["issued_at"] = "2026-10-06T18:00:00+09:00"
    elif problem == "missing_issue":
        saved[10].pop("issued_at")
    elif problem == "future_recording":
        saved[10]["recorded_at"] = "2026-10-09T00:00:00+09:00"
    else:
        saved[10]["forecast_run_id"] = "other"
    assert select([], saved) == {}


def test_recovered_oct6_is_unavailable_to_oct7_early_prediction():
    assert select([], rows(), cutoff=datetime.fromisoformat("2026-10-07T02:30:00+09:00"),
                  target="2026-10-07") == {}


@pytest.mark.parametrize("target", ["2026-10-06", "2026-10-05"])
def test_target_and_future_dates_are_excluded(target):
    assert select(rows(), rows(), target=target) == {}


def test_future_canonical_falls_back_to_available_original():
    canonical = rows()
    canonical[10]["updated_at"] = "2026-10-09T00:00:00+09:00"
    assert select(canonical, rows())[DAY][10]["pv"] == 1


def test_sqlite_reader_uses_snapshot_when_other_canonical_days_exist(tmp_path, monkeypatch):
    path = tmp_path / "history.db"
    conn = sqlite_ops.open_db(path)
    try:
        sqlite_ops.ensure_schema(conn)
        conn.execute("CREATE TABLE forecast_hourly_snapshots (snapshot_id TEXT, forecast_run_id TEXT, "
                     "issued_at TEXT, issued_at_source TEXT, target_at TEXT, lead_minutes INTEGER, date TEXT, "
                     "hour INTEGER, forecast_pv_kwh REAL, forecast_load_kwh REAL, "
                     "forecast_shortwave_radiation_w_m2 REAL, quality_flags_json TEXT, source TEXT, recorded_at TEXT)")
        for row in rows():
            conn.execute("INSERT INTO forecast_hourly (date, hour, forecast_pv_kwh, forecast_load_kwh, "
                         "forecast_shortwave_radiation_w_m2, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                         ("2026-10-05", row["hour"], 2, 0.2, 500, row["updated_at"]))
            conn.execute("INSERT INTO forecast_hourly_snapshots (snapshot_id, forecast_run_id, issued_at, "
                         "issued_at_source, target_at, lead_minutes, date, hour, forecast_pv_kwh, "
                         "forecast_load_kwh, forecast_shortwave_radiation_w_m2, quality_flags_json, source, "
                         "recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (str(row["hour"]), "original", row["issued_at"], "plan", DAY + "T00:00:00+09:00",
                          0, DAY, row["hour"], row["forecast_pv_kwh"], 0.2,
                          row["forecast_shortwave_radiation_w_m2"], "[]", "test", row["recorded_at"]))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setenv("DATA_DB_PATH", str(path))
    result, source = history._load_forecast_hourly_history(target_date="2026-10-08")
    assert source == "sqlite_forecast_hourly"
    assert result["2026-10-05"][10]["pv"] == 2
    assert result[DAY][10]["pv"] == 1


def test_firestore_reader_queries_only_missing_dates_and_never_writes(monkeypatch):
    from google.cloud import firestore

    reads = []
    canonical = rows()
    for row in canonical:
        row["date"] = "2026-10-05"

    class Collection:
        def __init__(self, name):
            self.name = name
            self.filters = []

        def where(self, field, operator, value):
            self.filters.append((field, operator, value))
            return self

        def stream(self):
            reads.append((self.name, self.filters))
            selected = canonical if self.name == "forecast_hourly" else rows()
            return [type("Doc", (), {"to_dict": lambda self, value=row: value})() for row in selected]

    class Client:
        def collection(self, name):
            return Collection(name)

    monkeypatch.setattr(firestore, "Client", lambda **kwargs: Client())
    monkeypatch.setenv("DATA_BACKEND", "firestore")
    monkeypatch.setenv("FORECAST_HOURLY_HISTORY_LOOKBACK_DAYS", "3")
    result = history.load_forecast_hourly_history_from_firestore(target_date="2026-10-08")
    assert result[DAY][10]["pv"] == 1
    assert reads[1] == ("forecast_hourly_snapshots", [("date", "in", [DAY, "2026-10-07"])])
