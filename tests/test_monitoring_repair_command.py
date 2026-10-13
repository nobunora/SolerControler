from __future__ import annotations

import copy
import importlib.util
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1.types import Document

from app.operations import monitoring_repair as repair
from app.operations.sqlite import PipelineConfig
from test_incremental_monitoring_backends import _write_csv


def test_prepare_cli_pins_closed_calendar_end_in_configured_timezone(prepared_repair, monkeypatch):
    from pathlib import Path

    client, cfg, path, _ = prepared_repair
    cfg = replace(cfg, timezone="Asia/Tokyo")
    script = Path(__file__).resolve().parents[1] / "scripts/repair_monitoring_source.py"
    spec = importlib.util.spec_from_file_location("repair_closed_calendar_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = []

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            # UTC is still October 12; the configured JST calendar is October 13.
            return datetime(2026, 10, 12, 15, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(module, "datetime", FixedClock)
    monkeypatch.setattr(module, "PipelineConfig", SimpleNamespace(from_env=lambda: cfg))
    monkeypatch.setattr(module, "open_firestore", lambda: client)

    def prepare(**kwargs):
        captured.append(kwargs)
        return {"status": "prepared", "candidate_count": 0}

    monkeypatch.setattr(module, "prepare_source_repair", prepare)
    assert module.main(["--state-path", str(path.parent / "fresh-cli.json"), "--stage", "prepare",
                        "--csv-run-dirs", str(path.parent)]) == 0
    assert len(captured) == 1 and captured[0]["cost_end_ts"] == "2026-10-13T00:00:00"
    assert client.writes == [] and client.commit_attempts == 0


class RepairRef:
    def __init__(self, client, path):
        self.client, self.path = client, path
        self.id = path.split("/", 1)[1]
        self._document_path = "projects/test-project/databases/(default)/documents/" + path

    def get(self, **kwargs):
        if kwargs:
            assert 0 < kwargs["timeout"] <= 4 and kwargs["retry"] is None
        return RepairSnapshot(self)


class RepairSnapshot:
    def __init__(self, ref):
        self.reference, self.id = ref, ref.id
        self.exists = ref.path in ref.client.data
        self.value = copy.deepcopy(ref.client.data.get(ref.path))
        self.update_time = ref.client.versions.get(ref.path)
        self.create_time = datetime(2026, 9, 20, tzinfo=timezone.utc) if self.exists else None

    def to_dict(self):
        return copy.deepcopy(self.value)


class RepairQuery:
    def __init__(self, client, name, filters=(), cap=None, descending=False):
        self.client, self.name, self.filters, self.cap, self.descending = client, name, filters, cap, descending

    def where(self, field, operator, value):
        return RepairQuery(self.client, self.name, (*self.filters, (field, operator, value)), self.cap, self.descending)

    def order_by(self, field, direction=None):
        return RepairQuery(self.client, self.name, self.filters, self.cap, direction == "DESCENDING")

    def limit(self, cap):
        return RepairQuery(self.client, self.name, self.filters, cap, self.descending)

    def document(self, key):
        return RepairRef(self.client, self.name + "/" + key)

    def stream(self, **kwargs):
        if kwargs:
            assert 0 < kwargs["timeout"] <= 4 and kwargs["retry"] is None
        self.client.read_calls.append((self.name, kwargs))
        rows = []
        for path, value in self.client.data.items():
            if not path.startswith(self.name + "/"):
                continue
            if all((str(value.get(field, "")) >= candidate if operator == ">=" else
                    str(value.get(field, "")) < candidate if operator == "<" else str(value.get(field, "")) <= candidate)
                   for field, operator, candidate in self.filters):
                rows.append(RepairSnapshot(RepairRef(self.client, path)))
        rows.sort(key=lambda row: row.id, reverse=self.descending)
        return iter(rows if self.cap is None else rows[:self.cap])


class RepairBatch:
    def __init__(self, client):
        self.client, self.pending = client, []

    def create(self, ref, value):
        self.pending.append((ref, copy.deepcopy(value), True))

    def set(self, ref, value, merge):
        self.pending.append((ref, copy.deepcopy(value), False))

    def commit(self, **kwargs):
        if kwargs:
            assert 0 < kwargs["timeout"] <= 4 and kwargs["retry"] is None
        self.client.commit_attempts += 1
        if self.client.before_commit:
            hook, self.client.before_commit = self.client.before_commit, None
            hook(self.client)
        if any(create and ref.path in self.client.data for ref, value, create in self.pending):
            raise AlreadyExists("concurrent create")
        if self.client.fail_before:
            raise TimeoutError("commit outcome unavailable")
        for ref, value, create in self.pending:
            saved = {} if create else dict(self.client.data.get(ref.path, {}))
            saved.update(value)
            self.client.data[ref.path] = saved
            self.client.versions[ref.path] = datetime(2026, 10, 12, tzinfo=timezone.utc) + timedelta(seconds=self.client.commit_attempts)
            self.client.writes.append(ref.path)
        if self.client.lose_ack:
            raise TimeoutError("ACK lost")
        return [SimpleNamespace(update_time=self.client.versions[ref.path]) for ref, value, create in self.pending]


class RepairClient:
    project = "test-project"
    _database_string = "projects/test-project/databases/(default)"

    def __init__(self):
        self.data, self.versions = {}, {}
        self.writes, self.read_calls = [], []
        self.commit_attempts = 0
        self.lose_ack = self.fail_before = False
        self.before_commit = None

    def collection(self, name):
        return RepairQuery(self, name)

    def document(self, path):
        return RepairRef(self, path)

    def get_all(self, refs, **kwargs):
        assert 0 < kwargs["timeout"] <= 4 and kwargs["retry"] is None
        return iter(RepairSnapshot(ref) for ref in refs)

    def batch(self):
        return RepairBatch(self)

    def put(self, path, value):
        self.data[path] = value
        self.versions[path] = datetime(2026, 9, 21, tzinfo=timezone.utc)


@pytest.fixture
def prepared_repair(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "firestore")
    monkeypatch.setattr("app.backup.night_plan_archive.night_plan_archive_prefix", lambda: "")
    cfg = replace(PipelineConfig.from_env(), artifacts_dir=tmp_path)
    csv = _write_csv(tmp_path / "september.csv", [f"2026/09/20,{index//2:02d}:{index%2*30:02d},1,2,0,1,0.2,0,50" for index in range(48)])
    (tmp_path / "kpnet_summary.json").write_text(json.dumps({"csv_downloads": [{"path": str(csv)}]}), encoding="utf-8")
    client = RepairClient()
    from app.operations.monitoring_sync import prepare_monitoring_csvs
    rows = sorted(prepare_monitoring_csvs([csv], window=None).rows, key=lambda row: row["ts"])
    for row in rows[:35]:
        payload = {key: value for key, value in row.items() if key != "_source_csv"}
        payload.update(source_csv="legacy.csv", ingested_at="legacy", foreign=b"unknown-field")
        client.put("monitoring_samples/" + row["ts"], payload)
    client.put("monitoring_samples/2026-09-21T00:00:00", {"ts": "2026-09-21T00:00:00", "pv_kwh": 0.0,
        "load_kwh": 1.0, "buy_kwh": 1.0, "sell_kwh": 0.0, "charge_kwh": 0.5, "discharge_kwh": 0.0, "soc_percent": 55.0})
    path = tmp_path / "repair.private.json"
    result = repair.prepare_source_repair(client=client, cfg=cfg, cost_end_ts="2026-10-13T00:00:00",
        csv_run_dirs=(tmp_path,), target_dates=(date(2026, 9, 20),), state_path=path)
    (repair._directory(path) / "forecast-intent.json").write_text(json.dumps({"prepared_sha256": result["reviewed_state_sha256"], "target_date": "2026-10-13"}))
    return client, cfg, path, result


def test_prepare_pins_full_payload_timestamp_and_native_unknown_fields(prepared_repair):
    client, cfg, path, result = prepared_repair
    state = repair._load(path, client)
    assert client.writes == [] and client.commit_attempts == 0
    assert len(state["candidates"]) == 13
    assert all(row["payload"]["ingested_at"] == state["prepared_at"] for row in state["candidates"])
    assert repair._sha(state["candidates"]) == state["candidate_sha256"]
    preimage = json.loads((repair._directory(path) / "preimage.json").read_bytes())
    native = next(value for value in preimage.values() if value["exists"])
    import base64
    proto = Document.deserialize(base64.b64decode(native["document_proto"]))
    assert proto.fields["foreign"].bytes_value == b"unknown-field"
    repair._save_stage(path, "example", {"status": "failed"})
    assert repair._load(path)["reviewed_state_sha256"] == result["reviewed_state_sha256"]


def test_fresh_verified_48_source_manifest_has_no_cloud_write_and_pins_calibration(prepared_repair):
    client, cfg, old_path, result = prepared_repair
    assert repair.apply_source_repair(client=client, state_path=old_path,
        reviewed_state_sha256=result["reviewed_state_sha256"])["status"] == "verified"
    old_manifest = old_path.read_bytes()
    native = {key: repair._native(RepairSnapshot(RepairRef(client, key)))
              for key in client.data if key.startswith("monitoring_samples/")}
    fresh_path = old_path.parent / "fresh-repair.private.json"
    prepared = repair.prepare_source_repair(client=client, cfg=cfg, cost_end_ts="2026-10-14T00:00:00",
        csv_run_dirs=(old_path.parent,), target_dates=(date(2026, 9, 20),), state_path=fresh_path)
    state = repair._load(fresh_path)
    assert prepared["candidate_count"] == 0 and len(state["existing"]) == 48
    repair._atomic_json(repair._directory(fresh_path) / "forecast-intent.json", {
        "prepared_sha256": prepared["reviewed_state_sha256"], "target_date": "2026-10-13",
    })
    commits = client.commit_attempts
    client.writes.clear()
    applied = repair.apply_source_repair(client=client, state_path=fresh_path,
        reviewed_state_sha256=prepared["reviewed_state_sha256"])
    assert applied["status"] == "verified" and applied["raw_writes"] == 0
    assert applied["calibration_preimage_sha256"] == state["calibration_preimage_sha256"]
    assert (repair._directory(fresh_path) / "previous-calibration-plan.json").exists()
    repeated = repair.apply_source_repair(client=client, state_path=fresh_path,
        reviewed_state_sha256=prepared["reviewed_state_sha256"])
    assert repeated["calibration_preimage_sha256"] == state["calibration_preimage_sha256"]
    assert client.writes == [] and client.commit_attempts == commits
    assert old_path.read_bytes() == old_manifest
    assert {key: repair._native(RepairSnapshot(RepairRef(client, key))) for key in native} == native


@pytest.mark.parametrize("failure", ["missing_one", "different_value", "boolean_value"])
def test_fresh_verified_manifest_rejects_partial_or_different_source(prepared_repair, failure):
    client, cfg, old_path, result = prepared_repair
    repair.apply_source_repair(client=client, state_path=old_path, reviewed_state_sha256=result["reviewed_state_sha256"])
    target = "monitoring_samples/2026-09-20T00:00:00"
    if failure == "missing_one":
        del client.data[target]
    elif failure == "different_value":
        client.data[target]["soc_percent"] = 51.0
    else:
        client.data[target]["pv_kwh"] = True
    commits = client.commit_attempts
    client.writes.clear()
    with pytest.raises(ValueError):
        repair.prepare_source_repair(client=client, cfg=cfg, cost_end_ts="2026-10-14T00:00:00",
            csv_run_dirs=(old_path.parent,), target_dates=(date(2026, 9, 20),), state_path=old_path.parent / "fresh-rejected.json")
    assert client.writes == [] and client.commit_attempts == commits


def test_atomic_create_13_and_same_input_retry_no_raw_rewrite(prepared_repair):
    client, cfg, path, result = prepared_repair
    previous = copy.deepcopy(client.data)
    receipt = repair.apply_source_repair(client=client, state_path=path, reviewed_state_sha256=result["reviewed_state_sha256"])
    assert receipt["status"] == "verified" and len(client.writes) == 13 and client.commit_attempts == 1
    assert all(client.data[key] == value for key, value in previous.items())
    receipt = repair.apply_source_repair(client=client, state_path=path, reviewed_state_sha256=result["reviewed_state_sha256"])
    assert receipt["status"] == "verified" and receipt["raw_writes"] == 0
    assert len(client.writes) == 13 and client.commit_attempts == 1


def test_review_sha_and_csv_mutation_prevent_write(prepared_repair):
    client, cfg, path, result = prepared_repair
    with pytest.raises(ValueError, match="SHA mismatch"):
        repair.apply_source_repair(client=client, state_path=path, reviewed_state_sha256="0" * 64)
    csv = path.parent / "september.csv"
    csv.write_bytes(csv.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="CSV evidence changed"):
        repair.apply_source_repair(client=client, state_path=path, reviewed_state_sha256=result["reviewed_state_sha256"])
    assert client.writes == []


@pytest.mark.parametrize("lost,before,status", [(True, False, "verified"), (False, True, "not_committed")])
def test_unknown_commit_is_resolved_by_readback(prepared_repair, lost, before, status):
    client, cfg, path, result = prepared_repair
    client.lose_ack, client.fail_before = lost, before
    receipt = repair.apply_source_repair(client=client, state_path=path, reviewed_state_sha256=result["reviewed_state_sha256"])
    assert receipt["status"] == status and client.commit_attempts == 1


def test_concurrent_create_rejects_whole_batch_without_overwrite(prepared_repair):
    client, cfg, path, result = prepared_repair
    state = repair._load(path)
    missing = state["candidates"][0]
    client.before_commit = lambda active: active.put("monitoring_samples/" + missing["document_id"], {"ts": missing["document_id"], "foreign": "other-writer"})
    receipt = repair.apply_source_repair(client=client, state_path=path, reviewed_state_sha256=result["reviewed_state_sha256"])
    assert receipt["status"] == "conflict" and client.writes == []
    assert len(client.data) == 37


def test_existing_35_guard_is_aftercheck_not_an_atomic_read_guard(prepared_repair):
    client, cfg, path, result = prepared_repair
    existing_path = next(iter(client.data))
    def mutate(active):
        active.data[existing_path]["foreign"] = b"concurrent-correction"
        active.versions[existing_path] += timedelta(seconds=1)
    client.before_commit = mutate
    receipt = repair.apply_source_repair(client=client, state_path=path, reviewed_state_sha256=result["reviewed_state_sha256"])
    assert receipt["status"] == "committed_drift" and len(client.writes) == 13
    assert client.data[existing_path]["foreign"] == b"concurrent-correction"


@pytest.mark.parametrize("invalid", ["-1", "NaN", "true", ""])
def test_prepare_rejects_invalid_energy_instead_of_inventing_zero(prepared_repair, invalid):
    client, cfg, path, result = prepared_repair
    csv = path.parent / "september.csv"
    text = csv.read_text(encoding="utf-8-sig")
    text = text.replace("2026/09/20,17:30,1,2,0,1,0.2,0,50", f"2026/09/20,17:30,{invalid},2,0,1,0.2,0,50")
    csv.write_text(text, encoding="utf-8-sig")
    with pytest.raises(ValueError, match="invalid source"):
        repair.prepare_source_repair(client=client, cfg=cfg, cost_end_ts="2026-10-13T00:00:00",
            csv_run_dirs=(path.parent,), target_dates=(date(2026, 9, 20),), state_path=path.parent / "second.private.json")
    assert client.writes == []


def test_real_sdk_batch_serializes_13_exists_false_writes_and_shared_timeout(prepared_repair, monkeypatch):
    from google.auth.credentials import AnonymousCredentials
    from google.cloud.firestore_v1 import Client, DocumentSnapshot
    from google.cloud.firestore_v1.types import WriteResult
    client, cfg, path, result = prepared_repair
    native = Client(project="test-project", credentials=AnonymousCredentials())
    def get_all(refs, **kwargs):
        assert kwargs["retry"] is None and 0 < kwargs["timeout"] <= 4
        for ref in refs:
            fake = client.document(ref.path).get()
            yield DocumentSnapshot(ref, fake.to_dict(), fake.exists, None, fake.create_time, fake.update_time)
    monkeypatch.setattr(native, "get_all", get_all)
    calls = []
    def commit(request, **kwargs):
        assert kwargs["retry"] is None and 0 < kwargs["timeout"] <= 4
        calls.append(request)
        for write in request["writes"]:
            assert write.current_document.exists is False
            document = write.update
            decoded = repair._helpers.decode_dict(document.fields, native)
            relative = document.name.split("/documents/", 1)[1]
            client.put(relative, decoded)
        stamp = datetime(2026, 10, 12, tzinfo=timezone.utc)
        return SimpleNamespace(write_results=[WriteResult(update_time=stamp) for _ in request["writes"]], commit_time=stamp)
    monkeypatch.setattr(native, "_firestore_api_internal", SimpleNamespace(commit=commit))
    receipt = repair.apply_source_repair(client=native, state_path=path, reviewed_state_sha256=result["reviewed_state_sha256"])
    assert receipt["status"] == "verified" and len(calls) == 1 and len(calls[0]["writes"]) == 13


@pytest.mark.parametrize("exists", [False, True])
def test_prepare_reads_configured_native_current_once_even_for_legacy_plan(prepared_repair, monkeypatch, exists):
    from app.forecasting.clear_ghi_store import ClearGhiStore
    from test_clear_ghi_store import FakeStorage, FINGERPRINT, _completed
    from app.separated_runtime.plan import PublishedPlan
    client, cfg, old_path, _ = prepared_repair
    storage = FakeStorage()
    prefix = "gs://test-bucket/archive"
    store = ClearGhiStore(cfg.artifacts_dir, fingerprint=FINGERPRINT, storage=storage, cloud_prefix=prefix)
    if exists:
        calibration, records = _completed()
        store.publish(calibration, records, expected_generation=0)
    storage.bucket("test-bucket").calls.clear()
    monkeypatch.setattr("app.backup.night_plan_archive.night_plan_archive_prefix", lambda: prefix)
    monkeypatch.setattr("google.cloud.storage.Client", lambda: storage)
    monkeypatch.setattr("app.forecasting.clear_ghi_observations.site_fingerprint", lambda **kwargs: FINGERPRINT)
    state_path = old_path.parent / "native.private.json"
    result = repair.prepare_source_repair(client=client, cfg=cfg, cost_end_ts="2026-10-13T00:00:00",
        csv_run_dirs=(old_path.parent,), target_dates=(date(2026, 9, 20),), state_path=state_path)
    state = repair._load(state_path, client)
    preimage = json.loads((repair._directory(state_path) / "calibration-preimage.json").read_bytes())
    assert preimage["status"] == ("verified" if exists else "verified_absent")
    assert preimage["root"]["backend"] == "gcs" and state["calibration_root"]["site_fingerprint"] == FINGERPRINT
    reads = [call for call in storage.bucket("test-bucket").calls if call[0] == "reload" and call[1].endswith("/current.json")]
    assert len(reads) == 1 and reads[0][2]["retry"] is None and reads[0][2]["timeout"] <= 4
    original = {"generated_at": "2026-10-12T02:00:00Z", "forecast": {"date": "2026-10-12"},
        "result": {"target_soc_7_percent": 40, "required_night_charge_kwh": 2, "effective_capacity_kwh": 10},
        "daytime_soc_optimization": {"hourly_pv_forecast_kwh": {str(hour): .1 for hour in range(24)},
                                     "hourly_load_forecast_kwh": {str(hour): .2 for hour in range(24)}}}
    previous = PublishedPlan.from_bytes(json.dumps(original).encode(), target_date="2026-10-12", charge_rate_info={"percent_per_hour": 20})
    client.put("control_plan_versions/" + previous.version_id, {"payload": json.dumps(previous.to_document())})
    (repair._directory(state_path) / "forecast-intent.json").write_text(json.dumps({"prepared_sha256": result["reviewed_state_sha256"],
        "target_date": "2026-10-13", "previous_control_version_id": previous.version_id, "previous_raw_plan_sha256": previous.sha256}))
    repair._pin_calibration_preimage(client, state_path, state)
    previous_receipt = json.loads((repair._directory(state_path) / "previous-calibration-plan.json").read_bytes())
    assert previous_receipt["status"] == "unavailable" and previous_receipt["reason_code"] == "legacy_plan_has_no_calibration_binding"
    assert json.loads((repair._directory(state_path) / "calibration-preimage.json").read_bytes()) == preimage
    assert len([call for call in storage.bucket("test-bucket").calls if call[0] == "reload" and call[1].endswith("/current.json")]) == 1
