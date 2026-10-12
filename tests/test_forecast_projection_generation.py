from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_v1 import Client
from google.cloud.firestore_v1 import _helpers
from google.cloud.firestore_v1.types import BatchGetDocumentsResponse, BeginTransactionResponse, CommitResponse, Document, WriteResult

from app.operations.forecast_persistence import persist_forecast_only_plan, project_forecast_plan
from app.operations.firestore import project_model_parameters, upsert_model_parameters_from_plan
from app.separated_runtime import planner
from app.separated_runtime.plan import PublishedPlan


class SdkTransport:
    """Real Firestore SDK request serialization, local RPC responses only."""

    def __init__(self):
        self.documents = {}
        self.requests = []
        self.stamp = datetime(2026, 10, 11, 3, tzinfo=timezone.utc)

    def begin_transaction(self, request, **kwargs):
        self.requests.append(("begin", request))
        return BeginTransactionResponse(transaction=b"local-transaction")

    def batch_get_documents(self, request, **kwargs):
        self.requests.append(("read", request))
        for name in request["documents"]:
            if name in self.documents:
                yield BatchGetDocumentsResponse(found=Document(name=name, fields=_helpers.encode_dict(self.documents[name]), update_time=self.stamp), read_time=self.stamp)
            else:
                yield BatchGetDocumentsResponse(missing=name, read_time=self.stamp)

    def commit(self, request, **kwargs):
        self.requests.append(("commit", request))
        for write in request["writes"]:
            assert write.update.name
            payload = _helpers.decode_dict(write.update.fields, None)
            self.documents[write.update.name] = {**self.documents.get(write.update.name, {}), **copy.deepcopy(payload)}
        return CommitResponse(write_results=[WriteResult(update_time=self.stamp) for _ in request["writes"]], commit_time=self.stamp)

    def rollback(self, request, **kwargs):
        self.requests.append(("rollback", request))


@pytest.fixture
def sdk_client():
    client = Client(project="publication-local", credentials=AnonymousCredentials())
    transport = SdkTransport()
    client._firestore_api_internal = transport
    return client, transport


def original(*, generated="2026-10-11T02:00:00Z", day="2026-10-11", pv=0.1):
    return json.dumps({
        "generated_at": generated, "forecast": {"date": day, "hourly_weather": [{"hour": hour} for hour in range(24)]},
        "result": {"target_soc_7_percent": 40, "required_night_charge_kwh": 2, "effective_capacity_kwh": 10},
        "coefficients": {"load_temperature": 0.25},
        "daytime_soc_optimization": {"hourly_pv_forecast_kwh": {str(hour): pv for hour in range(24)},
                                    "hourly_load_forecast_kwh": {str(hour): 0.2 for hour in range(24)}},
    }, sort_keys=True).encode()


def bind(raw):
    return PublishedPlan.from_bytes(raw, target_date=json.loads(raw)["forecast"]["date"], charge_rate_info={"percent_per_hour": 20})


def private_file(tmp_path, raw):
    directory = tmp_path / "forecast-plan-test-run"
    directory.mkdir(exist_ok=True)
    path = directory / "night_charge_plan.json"
    path.write_bytes(raw)
    return path


def test_real_sdk_projection_transaction_binds_24_values_and_same_capacity(tmp_path, sdk_client):
    client, transport = sdk_client
    raw = original()
    plan = bind(raw)
    path = private_file(tmp_path, raw)
    assert project_forecast_plan(client, raw=raw, plan=plan, private_plan_path=path, timezone_name="Asia/Tokyo") == 24
    saved = client.collection("forecast_plans").document(plan.target_date)._document_path
    data = transport.documents[saved]
    assert data["raw_plan_sha256"] == plan.sha256
    assert data["control_version_id"] == plan.version_id
    assert data["planned_capacity_kwh"] == 10
    assert set(data["planned_hourly_soc_percent"]) == {str(hour) for hour in range(7, 24)}
    transaction_commits = [request for kind, request in transport.requests if kind == "commit" and request.get("transaction")]
    assert len(transaction_commits) == 1
    assert len(transaction_commits[0]["writes"]) == 26
    assert all("control_plan_current" not in write.update.name for write in transaction_commits[0]["writes"])


def test_real_sdk_same_projection_is_idempotent_and_older_conflicting_generation_is_rejected(tmp_path, sdk_client):
    client, transport = sdk_client
    raw = original()
    plan = bind(raw)
    path = private_file(tmp_path, raw)
    project_forecast_plan(client, raw=raw, plan=plan, private_plan_path=path, timezone_name="Asia/Tokyo")
    before = copy.deepcopy(transport.documents)
    assert project_forecast_plan(client, raw=raw, plan=plan, private_plan_path=path, timezone_name="Asia/Tokyo") == 0
    assert transport.documents == before
    for candidate in (original(generated="2026-10-11T01:00:00Z"), original(pv=0.4)):
        with pytest.raises(ValueError, match="older|conflicting"):
            project_forecast_plan(client, raw=candidate, plan=bind(candidate), private_plan_path=private_file(tmp_path, candidate), timezone_name="Asia/Tokyo")
        assert transport.documents[client.collection("forecast_plans").document(plan.target_date)._document_path] == before[client.collection("forecast_plans").document(plan.target_date)._document_path]


def test_parameters_legacy_migration_then_retry_never_adds_a_second_sample(sdk_client):
    client, transport = sdk_client
    ref = client.collection("model_parameters").document("load_temperature")
    transport.documents[ref._document_path] = {"name": "load_temperature", "mean_value": 0.25, "sample_count": 5}
    raw, plan = original(), bind(original())
    project_model_parameters(client, raw=raw, plan=plan, updated_at="2026-10-11T03:00:00Z")
    assert transport.documents[ref._document_path]["sample_count"] == 6
    project_model_parameters(client, raw=raw, plan=plan, updated_at="2026-10-11T04:00:00Z")
    assert transport.documents[ref._document_path]["sample_count"] == 6
    assert transport.documents[ref._document_path]["variance"] == abs(0.25) * 0.05
    old = original(generated="2026-10-10T02:00:00Z", day="2026-10-10")
    with pytest.raises(ValueError, match="older"):
        project_model_parameters(client, raw=old, plan=bind(old), updated_at="2026-10-11T05:00:00Z")
    assert transport.documents[ref._document_path]["sample_count"] == 6


def test_shared_path_and_private_tamper_fail_before_projection(tmp_path, sdk_client):
    client, transport = sdk_client
    raw = original()
    shared = tmp_path / "night_charge_plan.json"
    shared.write_bytes(raw)
    with pytest.raises(ValueError, match="private"):
        project_forecast_plan(client, raw=raw, plan=bind(raw), private_plan_path=shared, timezone_name="Asia/Tokyo")
    path = private_file(tmp_path, raw + b" ")
    with pytest.raises(ValueError, match="checksum"):
        project_forecast_plan(client, raw=raw, plan=bind(raw), private_plan_path=path, timezone_name="Asia/Tokyo")
    assert not transport.documents


def test_private_original_tamper_during_legacy_snapshot_call_stops_mutable_projection(tmp_path, sdk_client, monkeypatch):
    client, transport = sdk_client
    raw = original()
    path = private_file(tmp_path, raw)
    def snapshot_then_tamper(*args, **kwargs):
        kwargs["night_plan_path"].write_bytes(raw + b" ")
        return 24
    monkeypatch.setattr("app.operations.forecast_persistence.persist_forecast_snapshots", snapshot_then_tamper)
    with pytest.raises(ValueError, match="changed during snapshot persistence"):
        project_forecast_plan(client, raw=raw, plan=bind(raw), private_plan_path=path, timezone_name="Asia/Tokyo")
    assert not transport.documents


def test_legacy_same_raw_adoption_binds_identity_without_another_parameter_sample(tmp_path, sdk_client):
    client, transport = sdk_client
    raw, plan = original(), bind(original())
    path = private_file(tmp_path, raw)
    persist_forecast_only_plan(client, plan_path=path, target_date=plan.target_date, timezone_name="Asia/Tokyo")
    upsert_model_parameters_from_plan(client, night_plan_path=path, updated_at="2026-10-11T03:00:00Z")
    forecast_ref = client.collection("forecast_plans").document(plan.target_date)._document_path
    parameter_ref = client.collection("model_parameters").document("load_temperature")._document_path
    assert transport.documents[forecast_ref]["control_version_id"] == ""
    count = transport.documents[parameter_ref]["sample_count"]
    project_forecast_plan(client, raw=raw, plan=plan, private_plan_path=path, timezone_name="Asia/Tokyo")
    project_model_parameters(client, raw=raw, plan=plan, updated_at="2026-10-11T04:00:00Z")
    assert transport.documents[forecast_ref]["control_version_id"] == plan.version_id
    assert transport.documents[parameter_ref]["control_version_id"] == plan.version_id
    assert transport.documents[parameter_ref]["sample_count"] == count
    # Compatibility wrappers retrying this same raw retain the adopted binding.
    persist_forecast_only_plan(client, plan_path=path, target_date=plan.target_date, timezone_name="Asia/Tokyo")
    upsert_model_parameters_from_plan(client, night_plan_path=path, updated_at="2026-10-11T05:00:00Z")
    assert transport.documents[forecast_ref]["control_version_id"] == plan.version_id
    assert transport.documents[parameter_ref]["sample_count"] == count


def test_newer_standalone_original_cannot_keep_an_older_adopted_identity(tmp_path, sdk_client):
    client, transport = sdk_client
    raw, plan = original(), bind(original())
    path = private_file(tmp_path, raw)
    project_forecast_plan(client, raw=raw, plan=plan, private_plan_path=path, timezone_name="Asia/Tokyo")
    project_model_parameters(client, raw=raw, plan=plan, updated_at="2026-10-11T03:00:00Z")
    new_raw = original(generated="2026-10-11T04:00:00Z", pv=0.3)
    path.write_bytes(new_raw)
    persist_forecast_only_plan(client, plan_path=path, target_date=plan.target_date, timezone_name="Asia/Tokyo")
    upsert_model_parameters_from_plan(client, night_plan_path=path, updated_at="2026-10-11T04:00:00Z")
    forecast_ref = client.collection("forecast_plans").document(plan.target_date)._document_path
    parameter_ref = client.collection("model_parameters").document("load_temperature")._document_path
    assert transport.documents[forecast_ref]["control_version_id"] == ""
    assert transport.documents[parameter_ref]["control_version_id"] == ""
    assert transport.documents[parameter_ref]["sample_count"] == 2
    before = copy.deepcopy(transport.documents)
    with pytest.raises(ValueError, match="older"):
        project_forecast_plan(client, raw=raw, plan=plan, private_plan_path=private_file(tmp_path, raw), timezone_name="Asia/Tokyo")
    assert transport.documents == before


def test_planner_rejection_has_no_mutable_projection(tmp_path, monkeypatch):
    path = private_file(tmp_path, original())
    calls = []
    monkeypatch.setattr(planner.forecast_job, "generate_forecast_plan", lambda: path)
    monkeypatch.setattr(planner.forecast_job, "_target_date", lambda: "2026-10-11")
    monkeypatch.setattr(planner, "open_firestore", lambda: object())
    monkeypatch.setattr(planner.storage, "Client", lambda: object())
    monkeypatch.setattr(planner, "estimate_forced_charge_rate_percent_per_hour", lambda *_: {"percent_per_hour": 20})
    monkeypatch.setattr(planner, "archive_plan_original", lambda raw, **kw: calls.append("immutable") or ({"status": "verified"}, {}))
    def reject(plan):
        calls.append("control")
        raise ValueError("rejected")
    monkeypatch.setattr(planner, "FirestorePlanStore", lambda: SimpleNamespace(publish=reject))
    monkeypatch.setattr(planner, "project_forecast_plan", lambda *a, **kw: pytest.fail("candidate must not project"))
    with pytest.raises(ValueError, match="rejected"):
        planner.main()
    assert calls == ["immutable", "control"]


def test_projection_failure_does_not_republish_or_report_control_failure(tmp_path, monkeypatch, capsys):
    path = private_file(tmp_path, original())
    calls = []
    monkeypatch.setattr(planner.forecast_job, "generate_forecast_plan", lambda: path)
    monkeypatch.setattr(planner.forecast_job, "_target_date", lambda: "2026-10-11")
    monkeypatch.setattr(planner, "open_firestore", lambda: object())
    monkeypatch.setattr(planner.storage, "Client", lambda: object())
    monkeypatch.setattr(planner, "estimate_forced_charge_rate_percent_per_hour", lambda *_: {"percent_per_hour": 20})
    monkeypatch.setattr(planner, "archive_plan_original", lambda raw, **kw: ({"status": "verified"}, {}))
    monkeypatch.setattr(planner, "FirestorePlanStore", lambda: SimpleNamespace(publish=lambda plan: calls.append(plan.version_id)))
    def fail(*args, **kwargs):
        raise RuntimeError("projection unavailable")
    monkeypatch.setattr(planner, "project_forecast_plan", fail)
    assert planner.main() == 0
    assert len(calls) == 1
    assert '"event": "plan_projection_failed"' in capsys.readouterr().out
