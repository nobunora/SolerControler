"""Local transactional ledger, isolated from clear-GHI calibration storage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.forecasting.pv_ideal_max import (
    BASIS_VERSION,
    MODEL_ID,
    HourlyPvObservation,
    IdealMaxGeometry,
    IdealMaxPrediction,
    IdealMaxState,
    IdealMaxUpdate,
    exceeds_ideal_max,
    initial_ideal_max_state,
    predict_ideal_max_hour,
    update_ideal_max,
)

_APPLICATION_ID = 0x494D4158
_STATE_KEYS = {"schema_version", "model_id", "basis_version", "fingerprint", "coefficient", "revision", "updated_at"}


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("an aware timestamp is required")
    return value.astimezone(timezone.utc)


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _state_bytes(state: IdealMaxState) -> bytes:
    return _bytes({"schema_version": 1, "model_id": MODEL_ID, "basis_version": BASIS_VERSION,
                   "fingerprint": state.fingerprint, "coefficient": state.coefficient,
                   "revision": state.revision, "updated_at": _utc(state.updated_at).isoformat()})


def _decode_state(raw: bytes, sha256: str, geometry: IdealMaxGeometry) -> IdealMaxState:
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("ideal maximum state checksum mismatch")
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != _STATE_KEYS or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or value["model_id"] != MODEL_ID
            or value["basis_version"] != BASIS_VERSION or value["fingerprint"] != geometry.fingerprint):
        raise ValueError("invalid ideal maximum state schema or identity")
    state = IdealMaxState(value["fingerprint"], value["coefficient"], value["revision"], datetime.fromisoformat(value["updated_at"]))
    if _state_bytes(state) != raw:
        raise ValueError("noncanonical ideal maximum state")
    return state


class IdealMaxStore:
    """Read one current state; update C and its immutable observation atomically.

    Initialization is explicit. Existing empty/corrupt files are never cold starts.
    This local ledger is not a Cloud Run persistence or deployment adapter.
    """

    def __init__(self, directory: Path, geometry: IdealMaxGeometry, *, initialize_at: datetime | None = None) -> None:
        self.geometry = geometry
        self.path = Path(directory) / "pv_ideal_max" / geometry.fingerprint / "ledger.sqlite3"
        missing = not self.path.exists()
        if missing and initialize_at is None:
            raise FileNotFoundError("ideal maximum ledger is absent; explicit initialization is required")
        if missing:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5.0)
        try:
            with self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                tables = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if not tables:
                    if not missing or initialize_at is None:
                        raise ValueError("existing empty ledger is not a cold start")
                    state = initial_ideal_max_state(geometry, as_of=initialize_at)
                    self.connection.execute("CREATE TABLE current_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), payload BLOB NOT NULL, sha256 TEXT NOT NULL, last_received_at TEXT NOT NULL)")
                    self.connection.execute("CREATE TABLE observations (interval_start TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL, payload BLOB NOT NULL)")
                    self.connection.execute("CREATE TABLE predictions (interval_start TEXT NOT NULL, issued_at TEXT NOT NULL, payload BLOB NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(interval_start, issued_at))")
                    raw = _state_bytes(state)
                    self.connection.execute("INSERT INTO current_state VALUES (1, ?, ?, ?)",
                                            (raw, hashlib.sha256(raw).hexdigest(), _utc(initialize_at).isoformat()))
                    self.connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                    self.connection.execute("PRAGMA user_version=1")
                elif (self.connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                      or self.connection.execute("PRAGMA user_version").fetchone()[0] != 1):
                    raise ValueError("unknown ideal maximum ledger format")
                self.read_current()
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> IdealMaxStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def read_current(self) -> IdealMaxState:
        row = self.connection.execute("SELECT payload, sha256 FROM current_state WHERE singleton=1").fetchone()
        if row is None:
            raise ValueError("ideal maximum current state is missing")
        return _decode_state(row[0], row[1], self.geometry)

    def observation_record(self, interval_start: datetime) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT payload FROM observations WHERE interval_start=?", (_utc(interval_start).isoformat(),)).fetchone()
        return json.loads(row[0]) if row is not None else None

    def observations_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Explicit hourly report: indexed range, at most 31 days per request."""
        first, last = _utc(start), _utc(end)
        if not timedelta(0) < last - first <= timedelta(days=31):
            raise ValueError("hourly report range must be positive and at most 31 days")
        rows = self.connection.execute(
            "SELECT payload FROM observations WHERE interval_start>=? AND interval_start<? ORDER BY interval_start",
            (first.isoformat(), last.isoformat())).fetchall()
        return [json.loads(row[0]) for row in rows]

    def predict_hour(self, interval_start: datetime, *, as_of: datetime) -> IdealMaxPrediction:
        """Freeze a prediction separately from later observations and updates."""
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            prediction = predict_ideal_max_hour(interval_start, self.geometry, self.read_current(), as_of=as_of)
            self._save_prediction(prediction)
        return prediction

    def predict_day(self, day: date, *, as_of: datetime) -> tuple[IdealMaxPrediction, ...]:
        """Freeze all 24 hours with one state read and one atomic commit."""
        start = datetime.combine(day, time(), ZoneInfo(self.geometry.timezone_name))
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            state = self.read_current()
            predictions = tuple(predict_ideal_max_hour(start + timedelta(hours=hour), self.geometry, state, as_of=as_of)
                                for hour in range(24))
            for prediction in predictions:
                self._save_prediction(prediction)
        return predictions

    def _save_prediction(self, prediction: IdealMaxPrediction) -> None:
        # Caller owns the transaction, including the state snapshot.
        as_of = prediction.issued_at
        key, issued = _utc(prediction.interval_start).isoformat(), _utc(as_of).isoformat()
        raw = _bytes({"fingerprint": prediction.fingerprint, "interval_start": key,
                      "issued_at": issued, "base_kwh": prediction.base_kwh,
                      "maximum_kwh": prediction.maximum_kwh, "coefficient": prediction.coefficient,
                      "revision": prediction.revision})
        digest = hashlib.sha256(raw).hexdigest()
        previous = self.connection.execute("SELECT payload, sha256 FROM predictions WHERE interval_start=? AND issued_at=?", (key, issued)).fetchone()
        if previous is not None:
            if previous != (raw, digest):
                raise ValueError("a different prediction already exists for the same issue time")
        else:
            self.connection.execute("INSERT INTO predictions VALUES (?, ?, ?, ?)", (key, issued, raw, digest))

    def ingest(self, observation: HourlyPvObservation, *, as_of: datetime,
               expected_revision: int | None = None) -> IdealMaxUpdate | None:
        """Return None for an exact duplicate; reject corrections without rewriting."""
        if _utc(observation.available_at) > _utc(as_of):
            raise ValueError("observation is unavailable at as_of")
        key = _utc(observation.interval_start).isoformat()
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            current = self.read_current()
            if _utc(current.updated_at) > _utc(as_of):
                raise ValueError("current state is unavailable at as_of")
            previous = self.connection.execute("SELECT source_sha256 FROM observations WHERE interval_start=?", (key,)).fetchone()
            if previous is not None:
                if previous[0] != observation.source_sha256:
                    raise ValueError("observation correction requires an explicit separate rebuild")
                return None
            if expected_revision is not None and (type(expected_revision) is not int or expected_revision != current.revision):
                raise ValueError("ideal maximum state revision conflict")
            watermark = self.connection.execute("SELECT last_received_at FROM current_state WHERE singleton=1").fetchone()[0]
            if _utc(observation.available_at) < datetime.fromisoformat(watermark):
                raise ValueError("new observations must be ingested in availability order")
            result = update_ideal_max(current, self.geometry, observation, as_of=as_of)
            prediction_row = self.connection.execute(
                "SELECT payload, sha256 FROM predictions WHERE interval_start=? AND issued_at<=? ORDER BY issued_at DESC LIMIT 1",
                (key, key)).fetchone()
            issued_prediction = None
            if prediction_row is not None:
                raw_prediction, prediction_sha = prediction_row
                if hashlib.sha256(raw_prediction).hexdigest() != prediction_sha:
                    raise ValueError("issued prediction checksum mismatch")
                issued_prediction = json.loads(raw_prediction)
            record = {"interval_start": key, "available_at": _utc(observation.available_at).isoformat(),
                      "source_sha256": observation.source_sha256,
                      "halfhour_kwh": [observation.first_half_kwh, observation.second_half_kwh],
                      "actual_kwh": observation.actual_kwh, "base_kwh": result.base_kwh,
                      "maximum_before_kwh": result.maximum_before_kwh,
                      "coefficient_before": current.coefficient,
                      "coefficient_after": result.state_after.coefficient, "exceeded": result.exceeded,
                      "issued_prediction": issued_prediction,
                      "issued_exceeded": exceeds_ideal_max(observation.actual_kwh, issued_prediction["maximum_kwh"])
                          if issued_prediction is not None else None}
            self.connection.execute("INSERT INTO observations VALUES (?, ?, ?)", (key, observation.source_sha256, _bytes(record)))
            raw = _state_bytes(result.state_after)
            self.connection.execute("UPDATE current_state SET payload=?, sha256=?, last_received_at=? WHERE singleton=1",
                                    (raw, hashlib.sha256(raw).hexdigest(), _utc(observation.available_at).isoformat()))
            # Verify the stored state before committing this transaction.
            if self.read_current() != result.state_after:
                raise ValueError("ideal maximum state readback mismatch")
            return result
