"""Cloud restart, immutable backups, CAS races and corruption fail closed."""

import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from google.api_core.exceptions import NotFound, PreconditionFailed

from app.forecasting import pv_ideal_max as model
from app.forecasting.pv_ideal_max_cloud import IdealMaxCloudArchive
from app.forecasting.pv_ideal_max_store import IdealMaxStore

START = datetime(2026, 10, 30, tzinfo=timezone.utc)


class FakeBlob:
    def __init__(self, storage, name):
        self.storage, self.name, self.generation = storage, name, None

    def reload(self, **kwargs):
        assert kwargs["retry"] is None and 0 < kwargs["timeout"] <= 4
        if self.name not in self.storage.objects:
            raise NotFound("missing")
        self.generation = self.storage.objects[self.name][0]

    def download_as_bytes(self, **kwargs):
        self.reload(timeout=kwargs["timeout"], retry=kwargs["retry"])
        if kwargs["if_generation_match"] != self.generation:
            raise PreconditionFailed("changed")
        return self.storage.objects[self.name][1]

    def upload_from_string(self, raw, **kwargs):
        assert kwargs["retry"] is None and 0 < kwargs["timeout"] <= 4
        current = self.storage.objects.get(self.name, (0, b""))[0]
        if kwargs["if_generation_match"] != current:
            raise PreconditionFailed("changed")
        self.storage.counter += 1
        self.generation = self.storage.counter
        self.storage.objects[self.name] = (self.generation, raw)


class FakeStorage:
    def __init__(self):
        self.objects, self.counter = {}, 0

    def bucket(self, _name):
        return self

    def blob(self, name):
        return FakeBlob(self, name)


@pytest.fixture
def geometry(monkeypatch):
    monkeypatch.setattr(model, "ideal_max_base", lambda *_: 1.0)
    return model.IdealMaxGeometry(35, 135, "UTC", (model.IdealMaxArray(180, 20, 2, 0.8, 1),))


def seed(tmp_path, geometry, storage):
    archive = IdealMaxCloudArchive(storage, "gs://test/old-archive", geometry)
    with IdealMaxStore(tmp_path / "seed", geometry, initialize_at=START) as store:
        store.predict_day(START.date(), as_of=START)
        archive.publish(store, initialize=True)
    return archive


def test_restart_restores_state_and_original_predictions(tmp_path, geometry):
    storage = FakeStorage()
    archive = seed(tmp_path, geometry, storage)
    initialized_at = archive.original["initialized_at"]
    with archive.checkout(tmp_path / "first") as store:
        store.ingest(model.HourlyPvObservation(START, 0.6, 0.6, START + timedelta(hours=1)), as_of=START + timedelta(hours=1))
        receipt = archive.publish(store)
    fresh = IdealMaxCloudArchive(storage, "gs://test/old-archive", geometry)
    with fresh.checkout(tmp_path / "restarted") as restored:
        assert restored.read_current().coefficient == 1.2
        assert restored.observation_record(START)["issued_prediction"]["maximum_kwh"] == 1
        assert restored.observation_record(START)["issued_exceeded"] is True
        assert fresh.original["initialized_at"] == initialized_at
    assert receipt["snapshot_bytes"] > 0
    assert len([name for name in storage.objects if "/snapshots/" in name]) == 2
    assert all("/pv_ideal_max/" in name for name in storage.objects)


def test_competing_writers_cannot_lose_state_or_history(tmp_path, geometry):
    storage = FakeStorage()
    seed(tmp_path, geometry, storage)
    a = IdealMaxCloudArchive(storage, "gs://test/old-archive", geometry)
    b = IdealMaxCloudArchive(storage, "gs://test/old-archive", geometry)
    with a.checkout(tmp_path / "a") as first, b.checkout(tmp_path / "b") as second:
        first.ingest(model.HourlyPvObservation(START, 0.7, 0.7, START + timedelta(hours=1)), as_of=START + timedelta(hours=1))
        second.ingest(model.HourlyPvObservation(START, 0.6, 0.6, START + timedelta(hours=1)), as_of=START + timedelta(hours=1))
        a.publish(first)
        with pytest.raises(PreconditionFailed):
            b.publish(second)
    with a.checkout(tmp_path / "current") as current:
        assert current.read_current().coefficient == 1.4
        assert current.observation_record(START)["actual_kwh"] == 1.4


@pytest.mark.parametrize("fault", ["pointer", "snapshot", "state"])
def test_corrupt_cloud_never_uses_stale_local_or_initializes(tmp_path, geometry, fault):
    storage = FakeStorage()
    archive = seed(tmp_path, geometry, storage)
    pointer_name = f"{archive.prefix}/current.json"
    generation, raw = storage.objects[pointer_name]
    pointer = json.loads(raw)
    if fault == "pointer":
        storage.objects[pointer_name] = (generation, b"{}")
    elif fault == "state":
        pointer["coefficient"] = 100
        storage.objects[pointer_name] = (generation, json.dumps(pointer).encode())
    else:
        name = f"{archive.prefix}/snapshots/{pointer['sha256']}.sqlite.gz"
        storage.objects[name] = (storage.objects[name][0], gzip.compress(b"wrong"))
    with pytest.raises(ValueError):
        archive.checkout(tmp_path / "damaged")


def test_absent_cloud_requires_explicit_init_and_existing_cloud_cannot_reset(tmp_path, geometry):
    storage = FakeStorage()
    archive = IdealMaxCloudArchive(storage, "gs://test/old-archive", geometry)
    assert archive.checkout(tmp_path / "absent") is None
    with IdealMaxStore(tmp_path / "local", geometry, initialize_at=START) as store:
        with pytest.raises(ValueError, match="explicit"):
            archive.publish(store)
        archive.publish(store, initialize=True)
        another = IdealMaxCloudArchive(storage, "gs://test/old-archive", geometry)
        with pytest.raises(PreconditionFailed):
            another.publish(store, initialize=True)
        with pytest.raises(TimeoutError):
            IdealMaxCloudArchive(storage, "gs://test/old-archive", geometry, deadline=0).checkout(tmp_path / "timeout")


def test_checkout_rejects_existing_local_cache(tmp_path, geometry):
    (tmp_path / "pv_ideal_max").mkdir()
    with pytest.raises(ValueError, match="fresh"):
        IdealMaxCloudArchive(FakeStorage(), "gs://test", geometry).checkout(Path(tmp_path))
