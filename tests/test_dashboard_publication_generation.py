from __future__ import annotations

import copy
import base64
import gzip
import hashlib
import json
import io
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.auth.credentials import AnonymousCredentials
from google.api_core.datetime_helpers import DatetimeWithNanoseconds
from google.cloud.storage import Client as StorageClient

from app.dashboard import snapshots
from app.dashboard.history_reconstruction import _forecast_plan_metadata_by_date, _with_forecast_plan_metadata
from app.dashboard.slice_assembler import read_latest_pv_forecast_diagnostics
from app.operations.forecast_persistence import _forecast_projection_values
from app.separated_runtime.plan import PublishedPlan
from test_dashboard_new100_diagnostics import MODEL, model_document


class RevisionDocument:
    def __init__(self, key, value, revision):
        self.id, self.value, self.update_time = key, value, revision
        self.exists = value is not None

    def to_dict(self):
        return copy.deepcopy(self.value)

    def get(self, **kwargs):
        assert kwargs["timeout"] <= 4 and kwargs["retry"] is None
        return self


class RevisionQuery:
    def __init__(self, rows, filters=(), field=None, cap=None, tail=False, after=None):
        self.rows, self.filters, self.field, self.cap, self.tail = rows, filters, field, cap, tail
        self.after = after

    def select(self, fields):
        return self

    def where(self, field, operator, value):
        return RevisionQuery(self.rows, self.filters + ((field, operator, value),), self.field, self.cap, self.tail, self.after)

    def order_by(self, field):
        return RevisionQuery(self.rows, self.filters, self.field if field == "__name__" else field, self.cap, self.tail, self.after)

    def limit(self, count):
        return RevisionQuery(self.rows, self.filters, self.field, count, False, self.after)

    def start_after(self, snapshot):
        return RevisionQuery(self.rows, self.filters, self.field, self.cap, self.tail,
                             (str(snapshot.value[self.field]), snapshot.id))

    def limit_to_last(self, count):
        return RevisionQuery(self.rows, self.filters, self.field, count, True)

    def stream(self, **kwargs):
        if kwargs:
            assert kwargs["timeout"] <= 4 and kwargs["retry"] is None
        return iter(self._selected())

    def get(self, **kwargs):
        assert kwargs["timeout"] <= 4 and kwargs["retry"] is None
        return self._selected()

    def document(self, key):
        return next((row for row in self.rows if row.id == key), RevisionDocument(key, None, None))

    def _selected(self):
        selected = []
        for row in self.rows:
            if all((str(row.value.get(field, "")) >= value if operator == ">=" else
                    str(row.value.get(field, "")) <= value if operator == "<=" else str(row.value.get(field, "")) < value)
                   for field, operator, value in self.filters):
                selected.append(row)
        if self.field:
            selected.sort(key=lambda row: (str(row.value.get(self.field, "")), row.id))
        if self.after is not None:
            selected = [row for row in selected if (str(row.value[self.field]), row.id) > self.after]
        if self.cap is not None:
            selected = selected[-self.cap:] if self.tail else selected[:self.cap]
        return selected


class RevisionClient:
    def __init__(self):
        stamp = datetime(2026, 10, 11, 3, tzinfo=timezone.utc)
        self.rows = {
            "cost_daily": [RevisionDocument("2024-01-01", {"date": "2024-01-01"}, stamp)],
            "forecast_hourly": [RevisionDocument("2026-10-11-00", {"date": "2026-10-11"}, stamp)],
            "night_charge_plans": [RevisionDocument("latest", {"date": "2026-10-11"}, stamp),
                                   RevisionDocument("2026-10-11", {"date": "2026-10-11"}, stamp)],
        }

    def collection(self, name):
        return RevisionQuery(self.rows.get(name, []))

    def get_all(self, refs, **kwargs):
        return iter(refs)


class PublicationBlob:
    def __init__(self):
        self.generation = None
        self.metadata = {}
        self.previous = None
        self.uploads = []
        self.conflicts = 0

    def reload(self, **kwargs):
        if self.previous is None:
            raise NotFound("no artifact")
        self.generation, self.metadata = self.previous

    def upload_from_string(self, payload, **kwargs):
        self.uploads.append((payload, kwargs, dict(self.metadata)))
        if self.conflicts:
            self.conflicts -= 1
            raise PreconditionFailed("concurrent writer")
        self.previous = (str(int(self.generation or 0) + 1), dict(self.metadata))


def storage_for(blob):
    return SimpleNamespace(bucket=lambda *_: SimpleNamespace(blob=lambda *_: blob))


def identity():
    return {"target_date": "2026-10-11", "generated_at": "2026-10-11T02:00:00Z",
            "control_version_id": "version-1", "raw_plan_sha256": "sha-1"}


@pytest.mark.parametrize("count", [8506, 8526])
def test_paged_monitoring_revision_includes_all_raw_and_boundary_corrections(count, capsys):
    client = RevisionClient()
    stamp = datetime(2026, 10, 11, 3, tzinfo=timezone.utc)
    start = datetime(2026, 4, 17)
    client.rows["monitoring_samples"] = [RevisionDocument(str(index), {"ts": (start + timedelta(minutes=30*index)).isoformat()}, stamp)
                                          for index in range(count)]
    before = snapshots._read_dashboard_source_epoch(client, history_scope=True)
    assert int(before["monitoring_samples"].split("|")[1]) == count
    event = json.loads(capsys.readouterr().out.strip())
    # All payload revisions, duplicated bounds, and two page sentinels count.
    assert event["document_reads"] >= count + 2
    client.rows["monitoring_samples"][3659].update_time += timedelta(seconds=1)
    after = snapshots._read_dashboard_source_epoch(client, history_scope=True)
    assert after["monitoring_samples"] != before["monitoring_samples"]
    snapshots._check_source_epoch_order(before, after)


def test_paged_budget_rejects_raw_above_maximum_without_publication():
    client = RevisionClient()
    stamp = datetime(2026, 10, 11, 3, tzinfo=timezone.utc)
    start = datetime(2023, 10, 11)
    client.rows["monitoring_samples"] = [RevisionDocument(str(index), {"ts": (start + timedelta(minutes=30*index)).isoformat()}, stamp)
                                          for index in range(52609)]
    with pytest.raises(ValueError, match="52608"):
        snapshots._read_dashboard_source_epoch(client, history_scope=True)


def test_paged_budget_rejects_nonadvancing_cursor(monkeypatch):
    client = RevisionClient()
    stamp = datetime(2026, 10, 11, 3, tzinfo=timezone.utc)
    start = datetime(2026, 4, 17)
    client.rows["monitoring_samples"] = [RevisionDocument(str(index), {"ts": (start + timedelta(minutes=30*index)).isoformat()}, stamp)
                                          for index in range(3661)]
    monkeypatch.setattr(RevisionQuery, "start_after", lambda self, snapshot: self)
    with pytest.raises(ValueError, match="cursor did not advance"):
        snapshots._read_dashboard_source_epoch(client, history_scope=True)


def test_upload_detects_post_upload_source_drift_without_verified_receipt(monkeypatch):
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "gs://test/snapshots")
    blob = PublicationBlob()
    epoch = {"cost_daily": "2026-10-11T00:00:00+00:00|1|before"}
    epochs = iter([epoch, {"cost_daily": "2026-10-11T00:00:01+00:00|1|after"}])
    monkeypatch.setattr(snapshots, "_read_dashboard_source_epoch", lambda *args, **kwargs: next(epochs))
    with pytest.raises(ValueError, match="changed after upload"):
        snapshots._upload_artifact(snapshots.artifact_from_payload(snapshots.BOOTSTRAP_KIND, {"test": 1}),
            storage_client=storage_for(blob), publication_identity=identity(), source_epoch=epoch, source_client=object())
    assert len(blob.uploads) == 1


def test_cold_precomputed_download_has_finite_sdk_timeout_and_no_retry(monkeypatch):
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "gs://test/snapshots")
    artifact = snapshots.artifact_from_payload(snapshots.BOOTSTRAP_KIND, {"data": 1})
    calls = []
    def download(**kwargs):
        calls.append(kwargs)
        return artifact.gzip_bytes
    blob = SimpleNamespace(download_as_bytes=download)
    loaded = snapshots.load_precomputed_snapshot(snapshots.BOOTSTRAP_KIND, storage_client=storage_for(blob), use_cache=False)
    assert loaded.raw == artifact.raw and calls == [{"timeout": 4, "retry": None}]


def test_old_cost_correction_participates_in_source_epoch_and_cannot_regress():
    client = RevisionClient()
    before = snapshots._read_dashboard_source_epoch(client, history_scope=False)
    client.rows["cost_daily"][0].update_time += timedelta(seconds=1)
    after = snapshots._read_dashboard_source_epoch(client, history_scope=False)
    assert before["cost_daily"] != after["cost_daily"]
    snapshots._check_source_epoch_order(before, after)
    with pytest.raises(ValueError, match="regress"):
        snapshots._check_source_epoch_order(after, before)


def test_cost_revision_sentinel_never_silently_truncates():
    client = RevisionClient()
    stamp = datetime(2026, 10, 11, 3, tzinfo=timezone.utc)
    client.rows["cost_daily"] = [RevisionDocument(str(day), {"date": (date(2000, 1, 1) + timedelta(days=day)).isoformat()}, stamp) for day in range(3661)]
    with pytest.raises(ValueError, match="3660"):
        snapshots._read_dashboard_source_epoch(client, history_scope=False)


def test_native_nanosecond_revision_and_source_delete_cannot_be_hidden(capsys):
    client = RevisionClient()
    row = client.rows["cost_daily"][0]
    row.update_time = DatetimeWithNanoseconds(2026, 10, 11, 3, nanosecond=123456001, tzinfo=timezone.utc)
    before = snapshots._read_dashboard_source_epoch(client, history_scope=False)
    row.update_time = DatetimeWithNanoseconds(2026, 10, 11, 3, nanosecond=123456002, tzinfo=timezone.utc)
    after = snapshots._read_dashboard_source_epoch(client, history_scope=False)
    assert before["cost_daily"] != after["cost_daily"]
    snapshots._check_source_epoch_order(before, after)
    client.rows["cost_daily"] = []
    deleted = snapshots._read_dashboard_source_epoch(client, history_scope=False)
    with pytest.raises(ValueError, match="missing"):
        snapshots._check_source_epoch_order(after, deleted)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event["document_reads"] for event in events] == [9, 9, 6]
    assert all(event["event"] == "dashboard_source_revisions_read" for event in events)


def test_history_revision_scope_includes_old_corrected_daily_and_analog_plan():
    client = RevisionClient()
    stamp = datetime(2026, 10, 11, 3, tzinfo=timezone.utc)
    client.rows["sunshine_daily"] = [RevisionDocument("2024-01-01", {"date": "2024-01-01"}, stamp)]
    client.rows["night_charge_plans"][1].value["decision_rationale"] = {
        "forecast_correction": {"recent_and_analog_hourly_floor": {"analog_day": "2024-01-01"}}}
    client.rows["night_charge_plans"].append(RevisionDocument("2024-01-01", {"date": "2024-01-01"}, stamp))
    recent = snapshots._read_dashboard_source_epoch(client, history_scope=False)
    assert "analog_plans" in recent
    assert "sunshine_daily" not in recent
    history = snapshots._read_dashboard_source_epoch(client, history_scope=True)
    client.rows["sunshine_daily"][0].update_time += timedelta(seconds=1)
    corrected = snapshots._read_dashboard_source_epoch(client, history_scope=True)
    assert history["sunshine_daily"] != corrected["sunshine_daily"]
    assert corrected["scope/sunshine_daily"] == "2024-01-01"


def test_revision_deadline_expires_before_storage_read():
    with pytest.raises(RuntimeError, match="deadline"):
        snapshots._read_dashboard_source_epoch(RevisionClient(), history_scope=False, deadline=0)


def test_delete_with_another_new_revision_still_retains_previous_asset():
    old = {"cost_daily": "2026-10-11T03:00:00.000000000+00:00|2|old-digest"}
    new = {"cost_daily": "2026-10-11T04:00:00.000000000+00:00|1|new-digest"}
    with pytest.raises(ValueError, match="regress"):
        snapshots._check_source_epoch_order(old, new)


def test_producer_clears_cache_before_materializing_and_reports_compressed_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "")
    monkeypatch.setenv("NIGHT_PLAN_ARCHIVE_GCS_PREFIX", "")
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_LOCAL_DIR", str(tmp_path))
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_FULL_HISTORY_REBUILD", "false")
    calls = []
    monkeypatch.setattr(snapshots, "clear_dashboard_cache", lambda: calls.append("clear"))
    monkeypatch.setattr(snapshots, "load_dashboard_slice", lambda *args, **kwargs: calls.append(kwargs) or object())
    monkeypatch.setattr(snapshots, "build_bootstrap_payload", lambda value: {"meta": {}, "forecast_hourly": []})
    result = snapshots.write_dashboard_snapshots(tmp_path / "unused.db")
    assert calls == ["clear", {"end_date": None, "window_days": 31, "include_static": True}]
    assert set(result) == {"bootstrap"}
    assert result["bootstrap"]["gzip_bytes"] == (tmp_path / "bootstrap.json.gz").stat().st_size
    assert result["bootstrap"]["raw_bytes"] > 0


def adopted_payload(*, day="2026-10-11", generated="2026-10-11T02:00:00Z"):
    original = {"generated_at": generated, "forecast": {"date": day},
                "result": {"target_soc_7_percent": 40, "required_night_charge_kwh": 2, "effective_capacity_kwh": 10},
                "daytime_soc_optimization": {"hourly_pv_forecast_kwh": {str(hour): 0.1 for hour in range(24)},
                                             "hourly_load_forecast_kwh": {str(hour): 0.2 for hour in range(24)}}}
    raw = json.dumps(original).encode()
    plan = PublishedPlan.from_bytes(raw, target_date=day, charge_rate_info={"percent_per_hour": 20})
    writes = _forecast_projection_values(original, target_date=plan.target_date, timezone_name="Asia/Tokyo", now=original["generated_at"])
    client = RevisionClient()
    client.rows["forecast_plans"] = [RevisionDocument(key, value, datetime(2026, 10, 11, 3, tzinfo=timezone.utc))
                                     for collection, key, value in writes if collection == "forecast_plans"]
    metadata = _forecast_plan_metadata_by_date(client, start_date=plan.target_date, end_date_iso=plan.target_date)
    rows = _with_forecast_plan_metadata([value for collection, _, value in writes if collection == "forecast_hourly"], metadata)
    payload = {"forecast_hourly": rows,
               "latest_schedule": {"plan_date": plan.target_date, "planned_target_soc_percent": 40}}
    return plan, payload


@pytest.mark.parametrize("field", ["forecast_pv_kwh", "forecast_load_kwh", "forecast_run_id", "forecast_target_soc_percent", "forecast_capacity_kwh", "forecast_soc_percent"])
def test_fixed_adopted_payload_validates_then_each_mixed_generation_field_is_rejected(field):
    plan, payload = adopted_payload()
    snapshots._validate_publication_payload(payload, plan)
    payload["forecast_hourly"][7][field] = "wrong-generation" if field == "forecast_run_id" else 99
    with pytest.raises(ValueError, match="generation mismatch"):
        snapshots._validate_publication_payload(payload, plan)


def new100_publication(monkeypatch, *, invalid=False):
    plan, _ = adopted_payload()
    document = json.loads(plan.raw_json)
    document["decision_rationale"] = model_document()["decision_rationale"]
    document["result"]["final_pv_forecast_source"] = MODEL
    if invalid:
        document["decision_rationale"]["pv_clear_ghi"]["calibration_pack"]["training_samples"] = 220
    plan = PublishedPlan.from_bytes(json.dumps(document).encode(), target_date=plan.target_date, charge_rate_info=plan.charge_rate_info)
    writes = _forecast_projection_values(document, target_date=plan.target_date, timezone_name="Asia/Tokyo", now=document["generated_at"])
    client, blob = RevisionClient(), PublicationBlob()
    client.rows["forecast_plans"] = [RevisionDocument(key, value, datetime(2026, 10, 11, 3, tzinfo=timezone.utc))
                                     for collection, key, value in writes if collection == "forecast_plans"]
    metadata = _forecast_plan_metadata_by_date(client, start_date=plan.target_date, end_date_iso=plan.target_date)
    payload = {"meta": {}, "forecast_hourly": _with_forecast_plan_metadata([value for collection, _, value in writes if collection == "forecast_hourly"], metadata),
               "latest_schedule": {"plan_date": plan.target_date, "planned_target_soc_percent": 40},
               "pv_forecast_diagnostics": {"plan_date": "2099-01-01", "physical": {"selected_method": "physical_global"}}}
    monkeypatch.setenv("DATA_BACKEND", "firestore")
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "gs://test/dashboard")
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_FULL_HISTORY_REBUILD", "false")
    loads = []
    monkeypatch.setattr(snapshots, "load_dashboard_slice", lambda *args, **kwargs: loads.append(kwargs) or object())
    monkeypatch.setattr(snapshots, "build_bootstrap_payload", lambda value: copy.deepcopy(payload))
    return plan, client, blob, payload, loads


def test_producer_stamps_same_raw_summary_after_validation_without_additional_load(tmp_path, monkeypatch, record_property):
    plan, client, blob, payload, loads = new100_publication(monkeypatch)
    baseline = snapshots.artifact_from_payload("bootstrap", payload)
    result = snapshots.write_dashboard_snapshots(tmp_path / "unused.db", storage_client=storage_for(blob),
                                                 published_plan=plan, source_client=client)
    public = json.loads(gzip.decompress(blob.uploads[0][0]))
    summary = public["pv_forecast_diagnostics"]["pv_model"]
    assert summary["status"] == "valid" and summary["raw_plan_sha256"] == plan.sha256
    assert summary["control_version_id"] == plan.version_id and summary["plan_date"] == plan.target_date
    assert summary["forecast_run_id"] == payload["forecast_hourly"][0]["forecast_run_id"]
    assert public["pv_forecast_diagnostics"]["physical"]["selected_method"] == MODEL
    assert public["forecast_hourly"] == payload["forecast_hourly"] and public["latest_schedule"] == payload["latest_schedule"]
    assert loads == [{"end_date": None, "window_days": 31, "include_static": True}]
    assert "PRIVATE-DO-NOT-TRANSFER" not in json.dumps(public)
    assert 0 < result["bootstrap"]["raw_bytes"] - len(baseline.raw) < 4096
    record_property("d01_raw_added_bytes", result["bootstrap"]["raw_bytes"] - len(baseline.raw))
    record_property("d01_gzip_added_bytes", result["bootstrap"]["gzip_bytes"] - len(baseline.gzip_bytes))
    record_property("d01_bootstrap_loader_calls", len(loads))


def test_invalid_display_metadata_retains_pv_load_soc_and_emits_only_reason(tmp_path, monkeypatch, capsys):
    plan, client, blob, payload, _ = new100_publication(monkeypatch, invalid=True)
    snapshots.write_dashboard_snapshots(tmp_path / "unused.db", storage_client=storage_for(blob), published_plan=plan, source_client=client)
    public = json.loads(gzip.decompress(blob.uploads[0][0]))
    assert public["forecast_hourly"] == payload["forecast_hourly"]
    summary = public["pv_forecast_diagnostics"]["pv_model"]
    assert summary["status"] == "invalid" and summary["reason_code"] == "display_metadata_invalid"
    assert "theta4" not in summary
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    event = next(event for event in events if event["event"] == "dashboard_pv_model_summary_unavailable")
    assert set(event) == {"event", "stage", "target_date", "model_id", "reason_code"}
    assert "PRIVATE" not in json.dumps(event)


def test_producer_does_not_stamp_summary_for_same_date_wrong_run(tmp_path, monkeypatch):
    plan, client, blob, payload, _ = new100_publication(monkeypatch)
    for row in payload["forecast_hourly"]:
        row["forecast_run_id"] = "same-date-different-run"
    monkeypatch.setattr(snapshots, "extract_pv_forecast_diagnostics", lambda *args, **kwargs: pytest.fail("validation must precede summary"))
    with pytest.raises(ValueError, match="forecast generation mismatch"):
        snapshots.write_dashboard_snapshots(tmp_path / "unused.db", storage_client=storage_for(blob), published_plan=plan, source_client=client)
    assert blob.uploads == []


def history_producer(monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "firestore")
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "gs://test/dashboard")
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_FULL_HISTORY_REBUILD", "false")
    client, blob = RevisionClient(), PublicationBlob()
    previous = snapshots._read_dashboard_source_epoch(client, history_scope=True)
    history = {"meta": {"snapshot_kind": "history", "snapshot_schema_version": 1},
               "cost_daily": [{"date": "2024-01-01", "savings_yen": 1}]}
    monkeypatch.setattr(snapshots, "_read_history_publication", lambda **kwargs: (copy.deepcopy(history), previous))
    plan, payload = adopted_payload(generated="2026-10-11T04:00:00Z")
    calls = []
    def build(db_path, *, existing_history):
        calls.append(existing_history)
        value = copy.deepcopy(history if existing_history is None else existing_history)
        if existing_history is None:
            value["cost_daily"][0]["savings_yen"] = client.rows["cost_daily"][0].value.get("savings_yen", 1)
        return {"bootstrap": copy.deepcopy(payload), "history": value}
    monkeypatch.setattr(snapshots, "build_dashboard_snapshot_payloads", build)
    return client, blob, previous, history, plan, payload, calls, build


def test_repair_history_writer_updates_history_only_and_pins_native_readback(tmp_path, monkeypatch):
    client, blob, previous, history, plan, payload, calls, build = history_producer(monkeypatch)
    downloads = []
    def download(**kwargs):
        downloads.append(kwargs)
        assert kwargs["if_generation_match"] == int(blob.previous[0])
        assert kwargs["retry"] is None and 0 < kwargs["timeout"] <= 4
        return blob.uploads[-1][0]
    blob.download_as_bytes = download
    result = snapshots.write_dashboard_history_snapshot(tmp_path / "unused.db", published_plan=plan,
        source_client=client, storage_client=storage_for(blob))
    assert result["status"] == "verified" and result["generation"] == "1"
    assert result["publication_identity"]["raw_plan_sha256"] == plan.sha256
    assert len(blob.uploads) == 1 and calls == [None] and len(downloads) == 1
    published = json.loads(gzip.decompress(blob.uploads[0][0]))
    assert published["meta"]["snapshot_kind"] == "history"
    assert not (tmp_path / "bootstrap.json.gz").exists()


def test_repair_history_mixed_generation_prevents_every_upload(tmp_path, monkeypatch):
    client, blob, previous, history, plan, payload, calls, build = history_producer(monkeypatch)
    payload["forecast_hourly"][7]["forecast_run_id"] = "different-run"
    with pytest.raises(ValueError, match="generation mismatch"):
        snapshots.write_dashboard_history_snapshot(tmp_path / "unused.db", published_plan=plan,
            source_client=client, storage_client=storage_for(blob))
    assert blob.uploads == []


@pytest.mark.parametrize("explicit_plan", [False, True])
def test_adopted_new_plan_normally_updates_history_incrementally(tmp_path, monkeypatch, explicit_plan):
    client, blob, previous, history, plan, _, calls, _ = history_producer(monkeypatch)
    client.rows["forecast_hourly"][0].update_time += timedelta(seconds=1)
    if not explicit_plan:
        stamp = datetime(2026, 10, 11, 4, tzinfo=timezone.utc)
        client.rows["forecast_plans"] = [RevisionDocument(plan.target_date, {"date": plan.target_date,
                                                                          "control_version_id": plan.version_id, "raw_plan_sha256": plan.sha256}, stamp)]
        client.rows["control_plan_versions"] = [RevisionDocument(plan.version_id, {"payload": json.dumps(plan.to_document())}, stamp)]
    result = snapshots.write_dashboard_snapshots(tmp_path / "unused.db", update_history=True, published_plan=plan if explicit_plan else None,
                                                 source_client=client, storage_client=storage_for(blob))
    assert result["history"]["rebuild_mode"] == "incremental"
    assert calls == [history]
    assert len(blob.uploads) == 2
    epoch = json.loads(gzip.decompress(base64.b64decode(blob.previous[1]["source_epoch"])))
    assert epoch["compare_retained/cost_daily"] == previous["retained/cost_daily"]


@pytest.mark.parametrize("legacy", [False, True])
def test_past_actual_correction_or_legacy_history_uses_bounded_existing_rebuild(tmp_path, monkeypatch, legacy):
    client, blob, previous, _, plan, _, calls, _ = history_producer(monkeypatch)
    if legacy:
        previous.clear()
    else:
        client.rows["cost_daily"][0].value["savings_yen"] = 2
        client.rows["cost_daily"][0].update_time += timedelta(seconds=1)
    result = snapshots.write_dashboard_snapshots(tmp_path / "unused.db", update_history=True, published_plan=plan,
                                                 source_client=client, storage_client=storage_for(blob))
    assert result["history"]["rebuild_mode"] == "full"
    assert calls == [None]
    restored = json.loads(gzip.decompress(blob.uploads[-1][0]))
    assert restored["cost_daily"][0]["savings_yen"] == (1 if legacy else 2)


def test_history_boundary_advance_refreshes_newly_retained_day(tmp_path, monkeypatch):
    client, blob, _, _, _, payload, calls, _ = history_producer(monkeypatch)
    plan, new_payload = adopted_payload(day="2026-10-12", generated="2026-10-12T04:00:00Z")
    payload.clear()
    payload.update(new_payload)
    client.rows["forecast_hourly"][0].value["date"] = plan.target_date
    client.rows["forecast_hourly"][0].update_time += timedelta(days=1)
    edge = {"meta": {}, "cost_daily": [{"date": "2026-09-11", "savings_yen": 3}]}
    reads = []
    monkeypatch.setattr(snapshots, "load_dashboard_slice", lambda *args, **kwargs: reads.append(kwargs) or object())
    monkeypatch.setattr(snapshots, "_payload_from_slice", lambda value: edge)
    result = snapshots.write_dashboard_snapshots(tmp_path / "unused.db", update_history=True, published_plan=plan,
                                                 source_client=client, storage_client=storage_for(blob))
    assert result["history"]["rebuild_mode"] == "incremental"
    assert reads == [{"end_date": "2026-09-11", "window_days": 1, "include_static": False}]
    assert any(row["date"] == "2026-09-11" and row["savings_yen"] == 3 for row in calls[0]["cost_daily"])


def test_history_correction_then_materialization_drift_retains_existing_assets(tmp_path, monkeypatch):
    client, blob, _, _, plan, _, _, build = history_producer(monkeypatch)
    previous = ("5", {"dashboard_snapshot_schema": "1"})
    blob.previous = copy.deepcopy(previous)
    client.rows["cost_daily"][0].update_time += timedelta(seconds=1)
    def build_with_drift(*args, **kwargs):
        value = build(*args, **kwargs)
        client.rows["cost_daily"][0].update_time += timedelta(seconds=1)
        return value
    monkeypatch.setattr(snapshots, "build_dashboard_snapshot_payloads", build_with_drift)
    with pytest.raises(ValueError, match="during materialization"):
        snapshots.write_dashboard_snapshots(tmp_path / "unused.db", update_history=True, published_plan=plan,
                                           source_client=client, storage_client=storage_for(blob))
    assert blob.uploads == []
    assert blob.previous == previous


def test_history_full_rebuild_source_budget_never_publishes_partial_history(tmp_path, monkeypatch):
    client, blob, previous, _, plan, _, calls, _ = history_producer(monkeypatch)
    previous.clear()
    stamp = datetime(2026, 10, 11, 3, tzinfo=timezone.utc)
    client.rows["cost_daily"] = [RevisionDocument(str(day), {"date": (date(2000, 1, 1) + timedelta(days=day)).isoformat()}, stamp) for day in range(3661)]
    with pytest.raises(ValueError, match="3660"):
        snapshots.write_dashboard_snapshots(tmp_path / "unused.db", update_history=True, published_plan=plan,
                                           source_client=client, storage_client=storage_for(blob))
    assert calls == [] and blob.uploads == []


@pytest.mark.parametrize("conflict", [False, True])
def test_history_producer_pins_payload_and_metadata_to_one_gcs_generation(monkeypatch, conflict):
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "gs://test/dashboard")
    history = {"meta": {"snapshot_kind": "history", "snapshot_schema_version": 1}}
    epoch = {"history_retained_before": "2026-09-11"}
    calls = []
    def download(**kwargs):
        calls.append(kwargs)
        if conflict:
            raise PreconditionFailed("history changed")
        return gzip.compress(json.dumps(history).encode())
    blob = SimpleNamespace(generation="7", metadata={"source_epoch": base64.b64encode(gzip.compress(json.dumps(epoch).encode())).decode()},
                           reload=lambda **kwargs: None, download_as_bytes=download)
    if conflict:
        with pytest.raises(PreconditionFailed):
            snapshots._read_history_publication(storage_client=storage_for(blob))
    else:
        assert snapshots._read_history_publication(storage_client=storage_for(blob)) == (history, epoch)
    assert calls == [{"if_generation_match": 7, "timeout": 4, "retry": None}]


def test_real_gcs_sdk_history_read_serializes_generation_precondition(monkeypatch):
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "gs://test/dashboard")
    history = {"meta": {"snapshot_kind": "history", "snapshot_schema_version": 1}}
    raw = gzip.compress(json.dumps(history).encode())
    epoch = {"history_retained_before": "2026-09-11"}
    encoded = base64.b64encode(gzip.compress(json.dumps(epoch).encode())).decode()
    requests_seen = []
    def request(method, url, **kwargs):
        requests_seen.append((method, url))
        response = requests.Response()
        response.status_code = 200
        response._content = raw if "alt=media" in url else json.dumps({"name": "dashboard/history.json.gz", "generation": "7", "metadata": {"source_epoch": encoded}}).encode()
        response.headers = {"content-type": "application/gzip" if "alt=media" in url else "application/json"}
        response.raw = requests.packages.urllib3.response.HTTPResponse(body=io.BytesIO(response._content), headers=response.headers, preload_content=False)
        return response
    client = StorageClient(project="publication-local", credentials=AnonymousCredentials(), _http=SimpleNamespace(request=request, is_mtls=False))
    assert snapshots._read_history_publication(storage_client=client) == (history, epoch)
    assert len(requests_seen) == 2
    assert "ifGenerationMatch=7" in requests_seen[1][1]


def test_gcs_cas_keeps_payload_and_rechecks_source_on_every_retry(monkeypatch):
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "gs://test/dashboard")
    client, blob = RevisionClient(), PublicationBlob()
    epoch = snapshots._read_dashboard_source_epoch(client, history_scope=False)
    blob.conflicts = 1
    artifact = snapshots.artifact_from_payload("bootstrap", {"meta": {}, "forecast_hourly": []})
    snapshots._upload_artifact(artifact, storage_client=storage_for(blob), publication_identity=identity(), source_epoch=epoch, source_client=client)
    assert len(blob.uploads) == 2
    assert all(payload == artifact.gzip_bytes for payload, _, _ in blob.uploads)
    assert all(kwargs["if_generation_match"] == 0 and kwargs["retry"] is None for _, kwargs, _ in blob.uploads)
    assert len(blob.previous[1]["source_epoch"]) < 8192
    with pytest.raises(ValueError, match="adopted"):
        snapshots._upload_artifact(artifact, storage_client=storage_for(blob))


def test_legacy_asset_first_migration_and_three_conflicts_retain_existing_generation(monkeypatch):
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "gs://test/dashboard")
    client, blob = RevisionClient(), PublicationBlob()
    epoch = snapshots._read_dashboard_source_epoch(client, history_scope=False)
    blob.previous = ("5", {"dashboard_snapshot_schema": "1"})
    artifact = snapshots.artifact_from_payload("bootstrap", {"meta": {}})
    snapshots._upload_artifact(artifact, storage_client=storage_for(blob), publication_identity=identity(), source_epoch=epoch, source_client=client)
    assert blob.uploads[0][1]["if_generation_match"] == 5
    assert blob.previous[1]["control_version_id"] == "version-1"
    previous = copy.deepcopy(blob.previous)
    blob.uploads = []
    blob.conflicts = 3
    with pytest.raises(RuntimeError, match="did not converge"):
        snapshots._upload_artifact(artifact, storage_client=storage_for(blob), publication_identity=identity(), source_epoch=epoch, source_client=client)
    assert len(blob.uploads) == 3
    assert blob.previous == previous


def test_source_change_after_payload_fixing_prevents_upload(monkeypatch):
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "gs://test/dashboard")
    client, blob = RevisionClient(), PublicationBlob()
    epoch = snapshots._read_dashboard_source_epoch(client, history_scope=False)
    client.rows["cost_daily"][0].update_time += timedelta(seconds=1)
    artifact = snapshots.artifact_from_payload("bootstrap", {"meta": {}})
    with pytest.raises(ValueError, match="revisions changed"):
        snapshots._upload_artifact(artifact, storage_client=storage_for(blob), publication_identity=identity(), source_epoch=epoch, source_client=client)
    assert blob.uploads == []


def test_revision_recheck_consumes_the_same_upload_deadline_budget(monkeypatch):
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "gs://test/dashboard")
    clock = [0.0]
    monkeypatch.setattr(snapshots, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    epoch, blob = {"cost_daily": "revision"}, PublicationBlob()
    def recheck(*args, **kwargs):
        assert kwargs["deadline"] == 24.0
        clock[0] = 23.0
        return epoch
    monkeypatch.setattr(snapshots, "_read_dashboard_source_epoch", recheck)
    timeouts = []
    original_reload = blob.reload
    def reload(**kwargs):
        timeouts.append(kwargs["timeout"])
        return original_reload(**kwargs)
    blob.reload = reload
    snapshots._upload_artifact(snapshots.artifact_from_payload("bootstrap", {"meta": {}}), storage_client=storage_for(blob),
                               publication_identity=identity(), source_epoch=epoch, source_client=object())
    assert timeouts == [1.0]
    assert blob.uploads[0][1]["timeout"] == 1.0


def test_source_change_during_cas_conflict_keeps_existing_asset(monkeypatch):
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "gs://test/dashboard")
    client = RevisionClient()
    epoch = snapshots._read_dashboard_source_epoch(client, history_scope=False)
    blob = PublicationBlob()
    blob.previous = ("5", {"schema_version": "1"})
    blob.conflicts = 1
    artifact = snapshots.artifact_from_payload("bootstrap", {"meta": {}})
    # Change happens in the failed upload transport, before the next source read.
    original_upload = blob.upload_from_string
    def upload(payload, **kwargs):
        try:
            original_upload(payload, **kwargs)
        finally:
            client.rows["cost_daily"][0].update_time += timedelta(seconds=1)
    blob.upload_from_string = upload
    with pytest.raises(ValueError, match="revisions changed"):
        snapshots._upload_artifact(artifact, storage_client=storage_for(blob), publication_identity=identity(), source_epoch=epoch, source_client=client)
    assert len(blob.uploads) == 1
    assert blob.previous == ("5", {"schema_version": "1"})


def test_metadata_budget_is_enforced_before_gcs_io(monkeypatch):
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "gs://test/dashboard")
    epoch = {str(number): hashlib.sha256(str(number).encode()).hexdigest() for number in range(1000)}
    blob = PublicationBlob()
    with pytest.raises(ValueError, match="8 KiB"):
        snapshots._upload_artifact(snapshots.artifact_from_payload("bootstrap", {"meta": {}}), storage_client=storage_for(blob), publication_identity=identity(), source_epoch=epoch)
    assert blob.uploads == []


def test_real_gcs_sdk_serializes_generation_precondition(monkeypatch):
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "gs://test/dashboard")
    requests_seen = []
    def request(method, url, **kwargs):
        requests_seen.append((method, url, kwargs))
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"name": "dashboard/bootstrap.json.gz", "generation": "1", "size": "5"}).encode()
        response.headers = {}
        return response
    http = SimpleNamespace(request=request, is_mtls=False)
    client = StorageClient(project="publication-local", credentials=AnonymousCredentials(), _http=http)
    def missing(*args, **kwargs):
        raise NotFound("not yet published")
    monkeypatch.setattr(client._connection, "api_request", missing)
    artifact = snapshots.artifact_from_payload("bootstrap", {"meta": {}})
    snapshots._upload_artifact(artifact, storage_client=client)
    assert len(requests_seen) == 1
    assert "ifGenerationMatch=0" in requests_seen[0][1]
    assert artifact.gzip_bytes in requests_seen[0][2]["data"]


@pytest.mark.parametrize("text,reason", [("{broken", "invalid_json"), ("[]", "non_object")])
def test_local_diagnostic_failures_are_observable_without_body(tmp_path, monkeypatch, capsys, text, reason):
    path = tmp_path / "plan.json"
    path.write_text(text)
    monkeypatch.setenv("NIGHT_CHARGE_PLAN_PATH", str(path))
    assert read_latest_pv_forecast_diagnostics() == {}
    output = capsys.readouterr().out
    assert reason in output
    assert text not in output
