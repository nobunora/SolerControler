from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.backup.night_plan_archive import build_night_plan_firestore_document
from app.operations.battery_calibration_state import load_previous_battery_calibration


AS_OF = datetime.fromisoformat("2026-11-03T02:30:00+09:00")


def _payload():
    return {"forecast": {"date": "2026-11-02"}, "decision_rationale": {
        "battery_calibration": {"as_of": "2026-11-02T02:30:00+09:00", "response": {"schema_version": 1}},
        "hourly_pv_bias_model": {"validated_at": "2026-11-02T02:30:00+09:00", "deltas": {}},
    }}


def _document(payload):
    return build_night_plan_firestore_document(
        payload, source="test", updated_at="2026-11-02T02:31:00+09:00", force_inline_detail=True,
    )


class ExactReader:
    def __init__(self, document):
        self.document_value = document
        self.calls = []

    def collection(self, name):
        assert name == "night_charge_plans"
        return self

    def document(self, identity):
        assert identity == "2026-11-02"
        return self

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(exists=self.document_value is not None, to_dict=lambda: self.document_value)


def test_previous_calibration_exact_read_verified_by_canonical_sha(monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "firestore")
    payload = _payload()
    reader = ExactReader(_document(payload))
    state = load_previous_battery_calibration(target_date="2026-11-03", as_of=AS_OF,
                                              client_factory=lambda: reader)
    assert state == payload["decision_rationale"]
    assert reader.calls == [{"timeout": 4.0, "retry": None}]


@pytest.mark.parametrize("case", ["missing", "missing_sha", "bad_sha", "wrong_doc_date", "future_receipt",
                                  "wrong_source_date", "future_model", "future_payload", "naive_as_of"])
def test_previous_calibration_rejects_unverified_or_future_state(monkeypatch, case):
    monkeypatch.setenv("DATA_BACKEND", "firestore")
    payload = _payload()
    if case == "wrong_source_date":
        payload["forecast"]["date"] = "2026-11-01"
    if case == "future_model":
        payload["decision_rationale"]["battery_calibration"]["as_of"] = "2026-11-04T00:00:00+09:00"
    if case == "future_payload":
        payload["generated_at"] = "2026-11-04T00:00:00+09:00"
    document = _document(payload)
    if case == "missing_sha":
        document.pop("detail_sha256")
    if case == "bad_sha":
        document["detail_sha256"] = "0" * 64
    if case == "wrong_doc_date":
        document["date"] = "2026-11-01"
    if case == "wrong_source_date":
        document["date"] = "2026-11-02"
    if case == "future_receipt":
        document["updated_at"] = "2026-11-04T00:00:00+09:00"
    reader = ExactReader(None if case == "missing" else document)
    as_of = AS_OF.replace(tzinfo=None) if case == "naive_as_of" else AS_OF
    assert load_previous_battery_calibration(target_date="2026-11-03", as_of=as_of,
                                            client_factory=lambda: reader) is None
    assert len(reader.calls) <= 1


def test_non_firestore_does_not_construct_client(monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "sqlite")

    def forbidden():
        raise AssertionError("non-Firestore must not contact cloud")

    assert load_previous_battery_calibration(target_date="2026-11-03", as_of=AS_OF,
                                            client_factory=forbidden) is None


def test_blob_read_is_bounded_and_uses_canonical_loader(monkeypatch):
    import json

    monkeypatch.setenv("DATA_BACKEND", "firestore")
    payload = _payload()
    document = deepcopy(_document(payload))
    document["plan_json"] = None
    document["detail_gcs_uri"] = "gs://test-bucket/previous.json"
    reads = []

    class Storage:
        def bucket(self, name):
            assert name == "test-bucket"
            return self

        def blob(self, name):
            assert name == "previous.json"
            return self

        def download_as_bytes(self, **kwargs):
            reads.append(kwargs)
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

    assert load_previous_battery_calibration(
        target_date="2026-11-03", as_of=AS_OF, client_factory=lambda: ExactReader(document),
        storage_client=Storage(),
    ) == payload["decision_rationale"]
    assert reads == [{"timeout": 4.0, "retry": None}]


def test_backend_failure_logs_only_exception_type(monkeypatch, caplog):
    monkeypatch.setenv("DATA_BACKEND", "firestore")

    def unavailable():
        raise RuntimeError("secret-resource-value")

    assert load_previous_battery_calibration(target_date="2026-11-03", as_of=AS_OF,
                                            client_factory=unavailable) is None
    assert "RuntimeError" in caplog.text
    assert "secret-resource-value" not in caplog.text
