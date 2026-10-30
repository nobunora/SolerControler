"""Persistence, original predictions and atomic monotone updates."""

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.forecasting import pv_ideal_max as model
from app.forecasting.pv_ideal_max_store import IdealMaxStore

START = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


@pytest.fixture
def geometry(monkeypatch):
    monkeypatch.setattr(model, "ideal_max_base", lambda *_: 1.0)
    return model.IdealMaxGeometry(35, 135, "UTC", (model.IdealMaxArray(180, 20, 2, 0.8, 1),))


def observed(actual=1.1, start=START, available=None):
    return model.HourlyPvObservation(start, actual / 2, actual / 2, available or start + timedelta(hours=1))


def test_daily_predictions_use_one_state_and_remain_immutable(tmp_path, geometry):
    with IdealMaxStore(tmp_path, geometry, initialize_at=START) as store:
        queries = []
        store.connection.set_trace_callback(queries.append)
        predictions = store.predict_day(START.date(), as_of=START)
        assert len(predictions) == 24
        assert {p.coefficient for p in predictions} == {1.0}
        assert sum(q.startswith("SELECT payload, sha256 FROM current_state") for q in queries) == 1
        assert store.predict_day(START.date(), as_of=START) == predictions
        issued_later = START + timedelta(hours=2)
        store.predict_day(START.date(), as_of=issued_later)
        store.ingest(observed(), as_of=START + timedelta(hours=1))
        with pytest.raises(ValueError, match="different prediction"):
            store.predict_day(START.date(), as_of=issued_later)
        assert store.connection.execute("SELECT count(*) FROM predictions WHERE issued_at=?", (START.isoformat(),)).fetchone()[0] == 24


def test_explicit_initialization_restart_duplicate_and_separate_namespace(tmp_path, geometry):
    legacy = tmp_path / "clear_ghi/current.json"
    legacy.parent.mkdir()
    legacy.write_bytes(b"legacy")
    with pytest.raises(FileNotFoundError):
        IdealMaxStore(tmp_path, geometry)
    with IdealMaxStore(tmp_path, geometry, initialize_at=START) as store:
        store.predict_hour(START, as_of=START)
        first = store.ingest(observed(), as_of=START + timedelta(hours=1))
        assert first.state_after.coefficient == 1.1
        original = store.observation_record(START)
        assert original["issued_prediction"]["maximum_kwh"] == 1
        assert original["issued_exceeded"] is True
    with IdealMaxStore(tmp_path, geometry, initialize_at=START) as restarted:
        assert restarted.read_current() == first.state_after
        assert restarted.ingest(observed(), as_of=START + timedelta(days=2), expected_revision=0) is None
        assert restarted.observation_record(START) == original
        assert restarted.read_current() == first.state_after
    assert legacy.read_bytes() == b"legacy"


def test_corrections_and_revision_conflicts_do_not_replace_state(tmp_path, geometry):
    with IdealMaxStore(tmp_path, geometry, initialize_at=START) as store:
        store.ingest(observed(), as_of=START + timedelta(hours=1))
        before = store.read_current()
        with pytest.raises(ValueError, match="correction"):
            store.ingest(observed(1.3), as_of=START + timedelta(hours=1))
        with pytest.raises(ValueError, match="revision conflict"):
            store.ingest(observed(1.4, START + timedelta(hours=1)), as_of=START + timedelta(hours=2), expected_revision=0)
        assert store.read_current() == before
        assert store.observation_record(START + timedelta(hours=1)) is None


def test_late_actual_keeps_issued_prediction_even_if_current_model_covers_it(tmp_path, geometry):
    with IdealMaxStore(tmp_path, geometry, initialize_at=START) as store:
        store.predict_hour(START, as_of=START)
        store.ingest(observed(1.4, START + timedelta(hours=1)), as_of=START + timedelta(hours=2))
        late = observed(1.2, available=START + timedelta(hours=3))
        result = store.ingest(late, as_of=late.available_at)
        assert not result.exceeded
        record = store.observation_record(START)
        assert record["issued_prediction"]["maximum_kwh"] == 1
        assert record["issued_exceeded"] is True
        assert record["maximum_before_kwh"] == 1.4
        assert store.read_current().coefficient == 1.4


def test_two_writers_read_latest_state_and_never_lower_it(tmp_path, geometry):
    with IdealMaxStore(tmp_path, geometry, initialize_at=START) as first:
        with IdealMaxStore(tmp_path, geometry) as second:
            first.ingest(observed(1.4), as_of=START + timedelta(hours=1))
            second.ingest(observed(1.2, START + timedelta(hours=1)), as_of=START + timedelta(hours=2))
            assert first.read_current().coefficient == second.read_current().coefficient == 1.4


def test_failed_write_rolls_back_observation_and_coefficient(tmp_path, geometry):
    with IdealMaxStore(tmp_path, geometry, initialize_at=START) as store:
        before = store.read_current()
        store.connection.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON current_state BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            store.ingest(observed(), as_of=START + timedelta(hours=1))
        assert store.read_current() == before
        assert store.observation_record(START) is None


@pytest.mark.parametrize("fault", ["checksum", "model", "missing", "empty"])
def test_corrupt_existing_state_is_not_a_cold_start(tmp_path, geometry, fault):
    with IdealMaxStore(tmp_path, geometry, initialize_at=START) as store:
        path = store.path
        if fault == "checksum":
            store.connection.execute("UPDATE current_state SET sha256='broken'")
        elif fault == "model":
            payload = json.loads(store.connection.execute("SELECT payload FROM current_state").fetchone()[0])
            payload["model_id"] = "old_model"
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            store.connection.execute("UPDATE current_state SET payload=?, sha256=?", (raw, hashlib.sha256(raw).hexdigest()))
        elif fault == "missing":
            store.connection.execute("DELETE FROM current_state")
        store.connection.commit()
    if fault == "empty":
        path.write_bytes(b"")
    with pytest.raises(ValueError):
        IdealMaxStore(tmp_path, geometry, initialize_at=START)


def test_zero_base_error_cannot_publish_a_fallback(tmp_path, geometry, monkeypatch):
    monkeypatch.setattr(model, "ideal_max_base", lambda *_: 0.0)
    with IdealMaxStore(tmp_path, geometry, initialize_at=START) as store:
        before = store.read_current()
        with pytest.raises(ValueError, match="zero-base"):
            store.ingest(observed(0.1), as_of=START + timedelta(hours=1))
        assert store.read_current() == before
        assert store.observation_record(START) is None


def test_prediction_identity_is_immutable_and_future_issue_is_not_backfilled(tmp_path, geometry):
    with IdealMaxStore(tmp_path, geometry, initialize_at=START) as store:
        first = store.predict_hour(START, as_of=START)
        assert store.predict_hour(START, as_of=START) == first
        store.predict_hour(START - timedelta(hours=1), as_of=START)
        store.ingest(observed(1.1, START - timedelta(hours=1), START + timedelta(hours=1)), as_of=START + timedelta(hours=1))
        assert store.observation_record(START - timedelta(hours=1))["issued_prediction"] is None


def test_normal_state_read_does_not_scan_history(tmp_path, geometry):
    with IdealMaxStore(tmp_path, geometry, initialize_at=START) as store:
        queries = []
        store.connection.set_trace_callback(queries.append)
        store.read_current()
        assert len(queries) == 1
        assert "observations" not in queries[0]
