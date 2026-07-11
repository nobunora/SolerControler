from __future__ import annotations

import gzip
import json

from app.night_plan_archive import (
    build_night_plan_firestore_document,
    load_night_plan_detail_from_firestore_doc,
    upload_night_plan_to_gcs,
)


class FakeBlob:
    def __init__(self) -> None:
        self.payload = b""
        self.generation = "1"
        self.cache_control = ""
        self.content_encoding = ""

    def upload_from_string(self, payload: bytes, *, content_type: str) -> None:
        assert content_type == "application/json"
        self.payload = payload

    def download_as_bytes(self) -> bytes:
        return self.payload


class FakeBucket:
    def __init__(self) -> None:
        self.blobs: dict[str, FakeBlob] = {}

    def blob(self, name: str) -> FakeBlob:
        return self.blobs.setdefault(name, FakeBlob())


class FakeStorageClient:
    def __init__(self) -> None:
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket())


def test_night_plan_archive_uploads_gzip_and_builds_summary_doc(monkeypatch) -> None:
    monkeypatch.setenv("NIGHT_PLAN_FIRESTORE_INLINE_DETAIL_DAYS", "0")
    plan = {
        "forecast": {"date": "2026-07-11", "sun_hours": 4.0},
        "result": {"target_soc_7_percent": 56.0, "final_predicted_pv_kwh": 12.3},
        "inputs": {"soc_now_percent": 0.0, "large": "x" * 1000},
        "pv_array_forecast": {"source": "legacy", "totals": {"total_kwh": 8.0}},
    }
    storage = FakeStorageClient()

    archive = upload_night_plan_to_gcs(
        plan,
        forecast_date="2026-07-11",
        prefix_uri="gs://solar-data/night_charge_plans",
        storage_client=storage,
    )
    doc = build_night_plan_firestore_document(
        plan,
        source="test",
        updated_at="2026-07-10T19:00:00Z",
        archive_info=archive,
    )

    assert doc["plan_json"] is None
    assert doc["detail_storage"] == "gcs"
    assert doc["detail_retention_policy"] == "indefinite"
    assert doc["detail_gcs_uri"] == "gs://solar-data/night_charge_plans/2026/07/2026-07-11.json.gz"
    assert doc["inputs_summary"] == {"soc_now_percent": 0.0}
    assert "large" not in json.dumps(doc, ensure_ascii=False)

    blob = storage.bucket("solar-data").blob("night_charge_plans/2026/07/2026-07-11.json.gz")
    assert json.loads(gzip.decompress(blob.payload).decode("utf-8")) == plan


def test_load_night_plan_detail_from_gcs_doc(monkeypatch) -> None:
    monkeypatch.setenv("NIGHT_PLAN_FIRESTORE_INLINE_DETAIL_DAYS", "0")
    plan = {"forecast": {"date": "2026-07-11"}, "result": {"target_soc_7_percent": 56.0}}
    storage = FakeStorageClient()
    archive = upload_night_plan_to_gcs(
        plan,
        forecast_date="2026-07-11",
        prefix_uri="gs://solar-data/archive",
        storage_client=storage,
    )
    doc = build_night_plan_firestore_document(
        plan,
        source="test",
        updated_at="2026-07-10T19:00:00Z",
        archive_info=archive,
    )

    assert load_night_plan_detail_from_firestore_doc(doc, storage_client=storage) == plan
