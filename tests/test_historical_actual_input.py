from datetime import datetime
import csv
import hashlib
import sqlite3

from app.energy_plan.workflow import _DefaultHistoricalInputPort
from app.forecasting.actual_history_io import load_historical_actual_rows
from app.forecasting.correction_calculations import actual_hourly_totals_by_day


def _database(tmp_path, monkeypatch):
    path = tmp_path / "actual.db"
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    monkeypatch.setenv("DATA_DB_PATH", str(path))
    monkeypatch.setenv("FORECAST_HOURLY_HISTORY_LOOKBACK_DAYS", "3650")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE monitoring_samples (ts TEXT PRIMARY KEY, pv_kwh REAL, load_kwh REAL, "
            "buy_kwh REAL, sell_kwh REAL, charge_kwh REAL, discharge_kwh REAL, soc_percent REAL)"
        )
        conn.executemany(
            "INSERT INTO monitoring_samples VALUES (?, ?, 1, 0.5, 0.25, 0, 0.5, 10)",
            [("2026-08-01T07:00:00", 0), ("2026-08-01T07:30:00", 2),
             ("2026-08-02T07:00:00", None), ("2026-09-01T00:00:00", 99),
             ("2026-10-03T00:00:00", 99)],
        )
    return path


def test_default_input_adds_old_actuals_without_replacing_csv_or_writing_database(tmp_path, monkeypatch):
    db = _database(tmp_path, monkeypatch)
    csv_path = tmp_path / "recent.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["年月日", "時刻", "発電電力量[kWh]", "消費電力量[kWh]",
                         "買電電力量[kWh]", "売電電力量[kWh]", "充電電力量[kWh]",
                         "放電電力量[kWh]", "蓄電残量(SOC)[%]"])
        writer.writerow(["2026/09/01", "00:00", 3, 4, 2, 1, 0, 1, 20])
        writer.writerow(["2026/10/02", "02:00", 0, 1, 1, 0, 0, 0, 0])
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    rows = _DefaultHistoricalInputPort().read_rows([csv_path])
    hourly = actual_hourly_totals_by_day(rows, target_date="2026-10-02")

    assert len(rows) == 4
    assert hourly["2026-08-01"][7] == {"pv": 2.0, "load": 2.0}
    assert "2026-08-02" not in hourly
    assert rows[-2]["pv"] == 3.0  # CSV wins over the stored 99 kWh.
    assert sum(row["buy"] for row in rows) == 4.0
    assert sum(row["sell"] for row in rows) == 1.5
    assert rows[-1]["soc"] == 0.0
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_history_respects_lookback_and_preserves_measured_zero(tmp_path, monkeypatch):
    _database(tmp_path, monkeypatch)
    recent = [{"dt": datetime(2026, 9, 1)}, {"dt": datetime(2026, 10, 2)}]
    rows = load_historical_actual_rows(recent)
    assert rows[0]["pv"] == 0.0
    monkeypatch.setenv("FORECAST_HOURLY_HISTORY_LOOKBACK_DAYS", "1")
    assert load_historical_actual_rows(recent) == []


def test_firestore_history_uses_bounded_read_only_projected_query(monkeypatch):
    from google.cloud import firestore

    calls = []

    class Document:
        def to_dict(self):
            return {"ts": "2026-08-05T07:00:00", "pv_kwh": 1, "load_kwh": 2,
                    "buy_kwh": 3, "sell_kwh": 4, "charge_kwh": 5,
                    "discharge_kwh": 6, "soc_percent": 7}

    class Query:
        def collection(self, name):
            calls.append(("collection", name))
            return self

        def where(self, *, filter):
            calls.append(("where", filter.field_path, filter.op_string, filter.value))
            return self

        def order_by(self, field):
            calls.append(("order_by", field))
            return self

        def select(self, fields):
            calls.append(("select", fields))
            return self

        def limit(self, count):
            calls.append(("limit", count))
            return self

        def stream(self):
            return iter([Document()])

    monkeypatch.setattr(firestore, "Client", lambda **kwargs: Query())
    monkeypatch.setenv("DATA_BACKEND", "firestore")
    monkeypatch.setenv("FORECAST_HOURLY_HISTORY_LOOKBACK_DAYS", "60")
    rows = load_historical_actual_rows([
        {"dt": datetime(2026, 9, 1)}, {"dt": datetime(2026, 10, 2)},
    ])
    assert rows[0]["pv"] == 1
    assert ("where", "ts", ">=", "2026-08-03T00:00:00") in calls
    assert ("where", "ts", "<", "2026-09-01T00:00:00") in calls
    assert ("limit", 1441) in calls
    assert ("select", ("ts", "pv_kwh", "load_kwh", "buy_kwh", "sell_kwh",
                       "charge_kwh", "discharge_kwh", "soc_percent")) in calls
    # The fixture provides no mutation API, so any write would fail this test.


def test_unavailable_history_is_reported_without_exception_secrets(monkeypatch, capsys):
    import app.forecasting.actual_history_io as history_io

    def fail(**kwargs):
        raise RuntimeError("private connection details")

    monkeypatch.setattr(history_io, "_read_actual_samples", fail)
    monkeypatch.setenv("FORECAST_HOURLY_HISTORY_LOOKBACK_DAYS", "3650")
    assert load_historical_actual_rows([
        {"dt": datetime(2026, 9, 1)}, {"dt": datetime(2026, 10, 2)},
    ]) == []
    log = capsys.readouterr().out
    assert "unavailable=RuntimeError" in log
    assert "private connection details" not in log
