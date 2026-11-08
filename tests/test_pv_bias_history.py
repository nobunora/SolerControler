import hashlib
import sqlite3
import time
from datetime import datetime

import pytest

from app.operations.pv_bias_history import _earlier_actuals, _read_original
from app.operations import pv_bias_history as history


def test_inline_original_is_checksum_verified_without_remote_access():
    raw = b'{"forecast":{"date":"2026-10-01"}}'
    doc = {"plan_json": raw.decode(), "detail_sha256": hashlib.sha256(raw).hexdigest()}
    assert _read_original(doc, None, time.monotonic() + 1) == raw
    with pytest.raises(ValueError, match="checksum"):
        _read_original({**doc, "detail_sha256": "0" * 64}, None, time.monotonic() + 1)


def test_original_missing_hash_never_attempts_a_storage_call():
    with pytest.raises(ValueError, match="checksum"):
        _read_original({"detail_uri": "gs://not-a-production-bucket/file"}, None, time.monotonic() + 1)


def test_parallel_original_reads_reserve_total_budget_before_io(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(history, "MAX_ORIGINAL_BYTES", 4)
    monkeypatch.setattr(history, "MAX_TOTAL_BYTES", 8)
    calls = []
    def read(*args):
        calls.append(1)
        return b"data"
    monkeypatch.setattr(history, "_read_original", read)
    budget = history._OriginalReadBudget()
    def attempt(_):
        try:
            return budget.read({}, None, time.monotonic() + 1)
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(10)))
    assert results.count(b"data") == len(calls) == 2
    assert budget.remaining == 0


def test_failed_original_read_keeps_unknown_transfer_reservation(monkeypatch):
    monkeypatch.setattr(history, "MAX_ORIGINAL_BYTES", 4)
    monkeypatch.setattr(history, "MAX_TOTAL_BYTES", 4)
    calls = []
    def read(*args):
        calls.append(1)
        raise TimeoutError
    monkeypatch.setattr(history, "_read_original", read)
    budget = history._OriginalReadBudget()
    with pytest.raises(TimeoutError):
        budget.read({}, None, time.monotonic() + 1)
    with pytest.raises(ValueError, match="budget"):
        budget.read({}, None, time.monotonic() + 1)
    assert calls == [1]


def test_actual_history_extends_only_pv_window_and_is_read_only(tmp_path, monkeypatch):
    path = tmp_path / "history.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE monitoring_samples(ts TEXT,pv_kwh REAL,available_at TEXT)")
        db.executemany("INSERT INTO monitoring_samples VALUES(?,?,?)", [
            ("2024-01-01T10:00:00", 9., "2024-01-02T00:00:00+09:00"),
            ("2026-01-01T10:00:00", 0., "2026-01-02T00:00:00+09:00")])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    monkeypatch.setenv("DATA_DB_PATH", str(path))
    existing = [{"dt": datetime(2026, 10, 1, 10), "pv": 1., "available_at": "2026-10-02T00:00:00+09:00"}]
    values = _earlier_actuals("2026-11-01", existing)
    assert len(values) == 2 and values[0]["pv"] == 0
    assert values[-1] == existing[0]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
