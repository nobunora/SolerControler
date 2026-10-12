"""Local/cloud store cold start, immutable originals, ordering and CAS failure."""

import hashlib
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest
from google.api_core.exceptions import Forbidden, NotFound, PreconditionFailed

from app.forecasting import clear_ghi_store
from app.forecasting.clear_ghi_observations import canonical_bytes
from app.forecasting.pv_clear_ghi import BASIS_VERSION, MODEL_ID, calibration_from_bytes

FINGERPRINT = "a" * 64
AS_OF = datetime(2026, 10, 14, tzinfo=timezone.utc)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _completed(*, stamp=AS_OF, theta=2.0):
    records = []
    for day in ["2026-01-01", "2026-04-01"]:
        hours = [
            {"hour": hour, "actual_halfhour_kwh": [0.4, 0.5],
             "solar_altitude_deg": 20.0, "sunshine_minutes": 60.0,
             "satellite_ghi_w_m2": 500.0, "central_haurwitz_w_m2": 550.0}
            for hour in range(5, 18)
        ]
        actual = {"date": day, "samples": [
            {"hour": row["hour"], "actual_halfhour_kwh": row["actual_halfhour_kwh"]} for row in hours
        ]}
        certification = {"date": day, "model": "satellite_fixture", "rule": "fixture-v1", "hourly": [
            {"time": f"{day}T{hour + 1:02}:00:00+09:00", "shortwave_radiation": 500.0,
             "sunshine_duration": 3600.0} for hour in range(5, 18)
        ]}
        records.append({
            "date": day, "actual_sha256": _sha(canonical_bytes(actual)),
            "certification_sha256": _sha(canonical_bytes(certification)),
            "actual_available_at": "2026-04-02T00:00:00+00:00",
            "certification_available_at": "2026-04-02T00:00:00+00:00",
            "certification_rule_version": "fixture-v1", "certified_clear": True,
            "site_fingerprint": FINGERPRINT, "basis_version": BASIS_VERSION,
            "hours": hours, "actual_source": actual, "certification_source": certification,
        })
    pack = {
        "schema_version": 1, "model_id": MODEL_ID, "basis_version": BASIS_VERSION,
        "site_fingerprint": FINGERPRINT, "terms": ["1", "x", "cos_phi", "sin_phi"],
        "theta4": [theta, 0.1, 0.2, 0.1], "training_dates": [record["date"] for record in records],
        "training_samples": 13 * len(records), "rank": 4, "condition_number": 3.0,
        "training_as_of": stamp.isoformat(), "created_at": stamp.isoformat(), "usable_from": stamp.isoformat(),
        "certification_rule_version": "fixture-v1",
    }
    for source in ("actual", "certification"):
        pack[f"{source}_source_sha256"] = _sha(canonical_bytes([
            {"date": record["date"], "sha256": record[f"{source}_sha256"]} for record in records
        ]))
    raw = canonical_bytes(pack)
    return calibration_from_bytes(raw, sha256=_sha(raw)), records


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name
        self.generation = None

    def reload(self, **kwargs):
        self.bucket.calls.append(("reload", self.name, kwargs))
        if self.bucket.reload_error is not None:
            raise self.bucket.reload_error
        if self.name not in self.bucket.objects:
            raise NotFound("fixture object absent")
        self.generation = self.bucket.objects[self.name][1]

    def download_as_bytes(self, **kwargs):
        self.bucket.calls.append(("download", self.name, kwargs))
        raw, generation = self.bucket.objects[self.name]
        if kwargs.get("if_generation_match") != generation:
            raise PreconditionFailed("fixture read conflict")
        return raw

    def upload_from_string(self, raw, **kwargs):
        self.bucket.calls.append(("upload", self.name, kwargs))
        if self.name.endswith("/current.json") and self.bucket.race_current:
            self.bucket.race_current = False
            old_raw, old_generation = self.bucket.objects[self.name]
            self.bucket.objects[self.name] = old_raw, old_generation + 100
        generation = self.bucket.objects.get(self.name, (None, 0))[1]
        if kwargs["if_generation_match"] != generation:
            raise PreconditionFailed("fixture write conflict")
        self.bucket.counter += 1
        self.generation = self.bucket.counter
        self.bucket.objects[self.name] = raw, self.generation
        if self.bucket.corrupt_original and "/calibrations/" in self.name:
            self.bucket.objects[self.name] = b"corrupt", self.generation


class FakeBucket:
    def __init__(self):
        self.objects = {}
        self.counter = 0
        self.calls = []
        self.reload_error = None
        self.race_current = False
        self.corrupt_original = False

    def blob(self, name):
        return FakeBlob(self, name)


class FakeStorage:
    def __init__(self):
        self.value = FakeBucket()

    def bucket(self, name):
        assert name == "test-bucket"
        return self.value


def _cloud(tmp_path):
    storage = FakeStorage()
    store = clear_ghi_store.ClearGhiStore(
        tmp_path, fingerprint=FINGERPRINT, storage=storage, cloud_prefix="gs://test-bucket/archive",
    )
    return store, storage.value


@pytest.mark.parametrize("cloud", [False, True])
def test_cold_start_retry_and_fresh_instance(tmp_path, cloud):
    store, bucket = _cloud(tmp_path) if cloud else (clear_ghi_store.ClearGhiStore(tmp_path, fingerprint=FINGERPRINT), None)
    assert store.read_current() == (None, 0)
    calibration, records = _completed()
    store.publish(calibration, records, expected_generation=0)
    state, generation = store.read_current()
    assert generation > 0
    assert state["calibration_sha256"] == calibration.sha256
    assert state["clear_days"] == records
    assert canonical_bytes(state["calibration_pack"]) == calibration.raw_bytes
    before_count = len(bucket.calls) if cloud else len(list(store.directory.rglob("*.json")))
    store.publish(calibration, records, expected_generation=0)
    assert store.read_current()[1] == generation
    if cloud:
        assert not any(method == "upload" for method, _, _ in bucket.calls[before_count:])
        for _, _, kwargs in bucket.calls:
            assert kwargs["timeout"] == 4 and kwargs["retry"] is None
        original_uploads = [name for method, name, _ in bucket.calls if method == "upload" and "/current.json" not in name]
        assert len(original_uploads) == 6
    else:
        assert len(list(store.directory.rglob("*.json"))) == before_count == 7
        restarted = clear_ghi_store.ClearGhiStore(tmp_path, fingerprint=FINGERPRINT)
        assert restarted.read_current() == (state, generation)
        assert not list(store.directory.glob("*.tmp"))


@pytest.mark.parametrize("cloud", [False, True])
def test_late_updater_and_same_cutoff_conflict_keep_current(tmp_path, cloud):
    store, _ = _cloud(tmp_path) if cloud else (clear_ghi_store.ClearGhiStore(tmp_path, fingerprint=FINGERPRINT), None)
    calibration, records = _completed()
    store.publish(calibration, records, expected_generation=0)
    original, generation = store.read_current()
    for bad, reason in [(_completed(stamp=AS_OF - timedelta(seconds=1))[0], "late updater"),
                        (_completed(theta=2.2)[0], "same cutoff")]:
        with pytest.raises(ValueError, match=reason):
            store.publish(bad, records, expected_generation=generation)
        assert store.read_current() == (original, generation)
    newer, records = _completed(stamp=AS_OF + timedelta(seconds=1))
    store.publish(newer, records, expected_generation=generation)
    assert store.read_current()[0]["calibration_sha256"] == newer.sha256


def test_cloud_cas_race_propagates_without_retry(tmp_path):
    store, bucket = _cloud(tmp_path)
    calibration, records = _completed()
    store.publish(calibration, records, expected_generation=0)
    original, generation = store.read_current()
    bucket.calls.clear()
    bucket.race_current = True
    newer, records = _completed(stamp=AS_OF + timedelta(seconds=1))
    with pytest.raises(PreconditionFailed):
        store.publish(newer, records, expected_generation=generation)
    assert store.read_current()[0] == original
    assert sum(method == "upload" and name.endswith("/current.json") for method, name, _ in bucket.calls) == 1


def test_local_crash_before_replace_keeps_current(tmp_path, monkeypatch):
    store = clear_ghi_store.ClearGhiStore(tmp_path, fingerprint=FINGERPRINT)
    calibration, records = _completed()
    store.publish(calibration, records, expected_generation=0)
    original, generation = store.read_current()
    newer, records = _completed(stamp=AS_OF + timedelta(seconds=1))

    def crash(*args):
        raise OSError("fixture replace interruption")

    monkeypatch.setattr(clear_ghi_store.os, "replace", crash)
    with pytest.raises(OSError, match="interruption"):
        store.publish(newer, records, expected_generation=generation)
    assert store.read_current() == (original, generation)
    assert not list(store.directory.glob("*.tmp"))


def test_cloud_original_readback_mismatch_never_publishes_current(tmp_path):
    store, bucket = _cloud(tmp_path)
    bucket.corrupt_original = True
    calibration, records = _completed()
    with pytest.raises(ValueError, match="read-back mismatch"):
        store.publish(calibration, records, expected_generation=0)
    assert store.read_current() == (None, 0)
    assert not any(method == "upload" and name.endswith("/current.json") for method, name, _ in bucket.calls)


def test_interrupted_local_original_is_not_partial_or_current(tmp_path, monkeypatch):
    store = clear_ghi_store.ClearGhiStore(tmp_path, fingerprint=FINGERPRINT)
    calibration, records = _completed()
    original_link = clear_ghi_store.os.link

    def crash(*args):
        raise OSError("fixture original interruption")

    monkeypatch.setattr(clear_ghi_store.os, "link", crash)
    with pytest.raises(OSError, match="original interruption"):
        store.publish(calibration, records, expected_generation=0)
    assert store.read_current() == (None, 0)
    assert not list(store.directory.rglob("*.json"))
    assert not list(store.directory.rglob("*.tmp"))
    monkeypatch.setattr(clear_ghi_store.os, "link", original_link)
    store.publish(calibration, records, expected_generation=0)
    assert store.read_current()[0]["calibration_sha256"] == calibration.sha256


@pytest.mark.parametrize("cloud", [False, True])
def test_corrupt_current_is_not_a_cold_start(tmp_path, cloud):
    store, bucket = _cloud(tmp_path) if cloud else (clear_ghi_store.ClearGhiStore(tmp_path, fingerprint=FINGERPRINT), None)
    if cloud:
        bucket.objects[f"archive/clear_ghi/{FINGERPRINT}/current.json"] = b"invalid", 1
    else:
        store.directory.mkdir(parents=True)
        (store.directory / "current.json").write_bytes(b"invalid")
    with pytest.raises(ValueError):
        store.read_current()


def test_cloud_permission_failure_propagates(tmp_path):
    store, bucket = _cloud(tmp_path)
    bucket.reload_error = Forbidden("fixture denied")
    with pytest.raises(Forbidden):
        store.read_current()


@pytest.mark.parametrize("fault", ["site", "source_hash", "hour_pair", "duplicate_day", "availability", "training_missing"])
def test_invalid_provenance_fails_before_publication(tmp_path, fault):
    store, bucket = _cloud(tmp_path)
    calibration, records = _completed()
    records = deepcopy(records)
    if fault == "site":
        records[0]["site_fingerprint"] = "b" * 64
    elif fault == "source_hash":
        records[0]["actual_source"]["samples"][0]["actual_halfhour_kwh"][0] = 999.0
    elif fault == "hour_pair":
        records[0]["hours"][0]["actual_halfhour_kwh"] = [None, 0.5]
    elif fault == "duplicate_day":
        records.append(deepcopy(records[0]))
    elif fault == "availability":
        records[0]["actual_available_at"] = (AS_OF + timedelta(seconds=1)).isoformat()
    elif fault == "training_missing":
        records.pop(0)
    with pytest.raises(ValueError):
        store.publish(calibration, records, expected_generation=0)
    assert bucket.calls == []
    assert store.read_current() == (None, 0)


def test_stale_local_token_cannot_replace_current(tmp_path):
    store = clear_ghi_store.ClearGhiStore(tmp_path, fingerprint=FINGERPRINT)
    calibration, records = _completed()
    store.publish(calibration, records, expected_generation=0)
    original = store.read_current()
    newer, records = _completed(stamp=AS_OF + timedelta(seconds=1))
    with pytest.raises(ValueError, match="generation conflict"):
        store.publish(newer, records, expected_generation=0)
    assert store.read_current() == original


def test_new_pack_with_same_sources_does_not_repeat_original_io(tmp_path):
    store, bucket = _cloud(tmp_path)
    calibration, records = _completed()
    store.publish(calibration, records, expected_generation=0)
    _, generation = store.read_current()
    bucket.calls.clear()
    newer, records = _completed(stamp=AS_OF + timedelta(seconds=1))
    store.publish(newer, records, expected_generation=generation)
    assert len(bucket.calls) == 9
    state_hash = store.published_state_sha256
    assert state_hash is not None
    assert bucket.objects[f"archive/clear_ghi/{FINGERPRINT}/states/{state_hash}.json"][0] == canonical_bytes(store.read_current()[0])
    assert not any("/originals/" in name for _, name, _ in bucket.calls)
    assert sum(method == "upload" for method, _, _ in bucket.calls) == 3


@pytest.mark.parametrize("cloud", [False, True])
@pytest.mark.parametrize("corrupt", [False, True])
def test_explicit_recovery_from_selected_originals(tmp_path, cloud, corrupt):
    store, bucket = _cloud(tmp_path) if cloud else (clear_ghi_store.ClearGhiStore(tmp_path, fingerprint=FINGERPRINT), None)
    calibration, records = _completed()
    store.publish(calibration, records, expected_generation=0)
    state, _ = store.read_current()
    state_sha = store.published_state_sha256
    assert state_sha == _sha(canonical_bytes(state))
    expected_generation = 0
    if cloud:
        current_name = f"archive/clear_ghi/{FINGERPRINT}/current.json"
        if corrupt:
            expected_generation = 900
            bucket.objects[current_name] = b"corrupt", expected_generation
        else:
            del bucket.objects[current_name]
    else:
        current = store.directory / "current.json"
        if corrupt:
            current.write_bytes(b"corrupt")
            expected_generation = int(_sha(b"corrupt"), 16)
        else:
            current.unlink()
    assert store.read_recovery_generation() == expected_generation
    store.restore_current(state_sha, expected_generation=expected_generation,
                          target_date=date(2026, 10, 15), as_of=AS_OF + timedelta(days=1))
    restored, _ = store.read_current()
    assert restored == state
    assert restored["clear_days"] == records


@pytest.mark.parametrize("fault", ["state", "pack", "source", "wrong_site", "not_available", "generation"])
def test_recovery_rejects_invalid_selected_originals(tmp_path, fault):
    store, bucket = _cloud(tmp_path)
    calibration, records = _completed()
    store.publish(calibration, records, expected_generation=0)
    state, _ = store.read_current()
    state_sha = _sha(canonical_bytes(state))
    base = f"archive/clear_ghi/{FINGERPRINT}/"
    del bucket.objects[base + "current.json"]
    if fault in {"state", "pack", "source"}:
        relative = {"state": f"states/{state_sha}.json", "pack": f"calibrations/{calibration.sha256}.json",
                    "source": f"originals/actual/{records[0]['date']}/{records[0]['actual_sha256']}.json"}[fault]
        raw, generation = bucket.objects[base + relative]
        bucket.objects[base + relative] = b"corrupt", generation
    elif fault == "wrong_site":
        store.fingerprint = "b" * 64
    bucket.calls.clear()
    with pytest.raises(ValueError):
        store.restore_current(state_sha, expected_generation=1 if fault == "generation" else 0,
                              target_date=date(2026, 10, 15),
                              as_of=AS_OF - timedelta(seconds=1) if fault == "not_available" else AS_OF + timedelta(days=1))
    assert not any(method == "upload" for method, _, _ in bucket.calls)
    assert base + "current.json" not in bucket.objects


def test_recovery_does_not_replace_valid_current(tmp_path):
    store, bucket = _cloud(tmp_path)
    calibration, records = _completed()
    store.publish(calibration, records, expected_generation=0)
    old_state, generation = store.read_current()
    newer, records = _completed(stamp=AS_OF + timedelta(seconds=1))
    store.publish(newer, records, expected_generation=generation)
    current, generation = store.read_current()
    with pytest.raises(ValueError, match="valid current"):
        store.restore_current(_sha(canonical_bytes(old_state)), expected_generation=generation,
                              target_date=date(2026, 10, 15), as_of=AS_OF + timedelta(days=1))
    assert store.read_current() == (current, generation)


def test_shared_deadline_shrinks_timeouts_and_stops_before_current(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(clear_ghi_store.time, "monotonic", lambda: clock[0])
    store, bucket = _cloud(tmp_path)
    store.deadline = 10.0
    calibration, records = _completed()
    original_upload = FakeBlob.upload_from_string
    original_reload = FakeBlob.reload
    original_download = FakeBlob.download_as_bytes

    def elapsed(method):
        def call(self, *args, **kwargs):
            assert 0 < kwargs["timeout"] <= min(4, 10 - clock[0])
            assert kwargs["retry"] is None
            clock[0] += min(3.0, kwargs["timeout"])
            return method(self, *args, **kwargs)
        return call

    monkeypatch.setattr(FakeBlob, "upload_from_string", elapsed(original_upload))
    monkeypatch.setattr(FakeBlob, "reload", elapsed(original_reload))
    monkeypatch.setattr(FakeBlob, "download_as_bytes", elapsed(original_download))
    with pytest.raises(TimeoutError, match="deadline"):
        store.publish(calibration, records, expected_generation=0)
    assert clock[0] == 10.0
    assert not any(method == "upload" and name.endswith("/current.json") for method, name, _ in bucket.calls)
    with pytest.raises(TimeoutError):
        store.check_deadline()
    assert len(bucket.calls) == 4


def test_revised_certified_membership_keeps_retired_immutable_originals(tmp_path):
    store, bucket = _cloud(tmp_path)
    calibration, records = _completed()
    extra = deepcopy(records[-1])
    extra["date"] = "2026-04-02"
    for source in ("actual", "certification"):
        extra[f"{source}_source"]["date"] = extra["date"]
        extra[f"{source}_sha256"] = _sha(canonical_bytes(extra[f"{source}_source"]))
    store.publish(calibration, records + [extra], expected_generation=0)
    _, generation = store.read_current()
    newer, records = _completed(stamp=AS_OF + timedelta(seconds=1))
    bucket.calls.clear()
    store.publish(newer, records, expected_generation=generation)
    assert store.read_current()[0]["clear_days"] == records
    assert not any("/originals/" in name for _, name, _ in bucket.calls)
    for source in ("actual", "certification"):
        name = f"archive/clear_ghi/{FINGERPRINT}/originals/{source}/{extra['date']}/{extra[f'{source}_sha256']}.json"
        assert bucket.objects[name][0] == canonical_bytes(extra[f"{source}_source"])
