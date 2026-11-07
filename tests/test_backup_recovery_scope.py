from __future__ import annotations

import gzip
import hashlib
import json

import pytest

from scripts.backup_recovery_scope import decode_fields, object_references, pointer_references
from scripts.backup_complete_remote import Backup, ReadFailure


def test_typed_database_and_embedded_json_keep_exact_generations():
    decoded = decode_fields({"detail": {"mapValue": {"fields": {
        "detail_uri": {"stringValue": "gs://bucket/history/old.json"},
        "detail_generation": {"integerValue": "9007199254740993"}}}}})
    refs = object_references(decoded, reason="database")
    assert [(r.name, r.generation) for r in refs] == [("history/old.json", "9007199254740993")]
    assert object_references(json.dumps(decoded), reason="database") == refs
    assert object_references({"name": "NIGHT_PLAN_ARCHIVE_GCS_PREFIX", "value": "gs://bucket/plans"}, reason="config") == []


def test_fixed_path_pointer_requires_its_exact_content_addressed_ledger():
    refs = pointer_references("bucket", "plans/pv_ideal_max/geometry/current.json", {"sha256": "a" * 64})
    assert refs[0].name == "plans/pv_ideal_max/geometry/snapshots/" + "a" * 64 + ".sqlite.gz"
    assert refs[0].decoded_sha256 == "a" * 64
    with pytest.raises(ValueError, match="pointer"):
        pointer_references("bucket", "pv_ideal_max/g/current.json", {"sha256": "bad"})


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("SOLAR_BACKUP_READ_TOKEN", "synthetic")
    return Backup(tmp_path, required_only=True, exclude_drive=True)


def install_catalog(backup, monkeypatch, values, live_keys, soft_keys=()):
    def meta(key):
        name, generation = key
        return {"name": name, "generation": generation, "size": str(len(values[key]))}
    live = [meta(key) for key in live_keys]
    soft = [meta(key) for key in soft_keys]
    versions = [meta(key) for key in values if key not in soft_keys]
    def items(url, key, **kwargs):
        if url.endswith("/b?project=test-project"):
            return [{"name": "bucket"}]
        params = kwargs["params"]
        return soft if params.get("softDeleted") else versions if params.get("versions") else live
    copied = []
    def download(url, path, **kwargs):
        from urllib.parse import unquote
        name = unquote(url.rsplit("/o/", 1)[1])
        key = (name, str(kwargs["params"]["generation"]))
        copied.append(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(values[key])
    monkeypatch.setattr(backup, "items", items)
    monkeypatch.setattr(backup, "optional", lambda *args, **kwargs: {})
    monkeypatch.setattr(backup, "download", download)
    return copied


def test_only_unreferenced_old_generations_are_excluded_and_cycles_terminate(scoped, monkeypatch):
    values = {("a.json", "2"): b'{"uri":"gs://bucket/b.json"}',
              ("b.json", "2"): b'{"detail_uri":"gs://bucket/a.json","detail_generation":"1"}',
              ("a.json", "1"): b'{"uri":"gs://bucket/b.json"}',
              ("gone.json", "1"): b"old"}
    copied = install_catalog(scoped, monkeypatch, values, [("a.json", "2"), ("b.json", "2")], [("gone.json", "1")])
    result = scoped.storage()
    assert result["readable_generations"] == 3 and result["excluded_generations"] == 1
    assert len(copied) == 3 and ("a.json", "1") in copied
    assert not scoped.manifest["gaps"]


def test_required_soft_deleted_generation_is_missing_not_excluded(scoped, monkeypatch):
    values = {("current.json", "2"): b'{"detail_uri":"gs://bucket/old.json","detail_generation":"1"}',
              ("old.json", "1"): b"required"}
    install_catalog(scoped, monkeypatch, values, [("current.json", "2")], [("old.json", "1")])
    result = scoped.storage()
    assert result["missing_required"] == 1 and result["excluded_generations"] == 0
    assert scoped.manifest["gaps"][0]["category"] == "required_gcs_object_missing"


def test_pointer_ledger_content_is_verified_even_if_already_selected(scoped, monkeypatch):
    digest = hashlib.sha256(b"correct ledger").hexdigest()
    pointer = "pv_ideal_max/g/current.json"
    ledger = "pv_ideal_max/g/snapshots/" + digest + ".sqlite.gz"
    values = {(pointer, "1"): json.dumps({"sha256": digest}).encode(), (ledger, "1"): gzip.compress(b"corrupt ledger")}
    install_catalog(scoped, monkeypatch, values, list(values))
    with pytest.raises(ReadFailure, match="content checksum"):
        scoped.storage()


def test_user_excluded_drive_is_never_contacted_or_counted_as_success(scoped, monkeypatch):
    for name in ("local", "firestore", "firestore_settings", "storage", "configuration", "secrets", "logs", "registry"):
        monkeypatch.setattr(scoped, name, lambda: {})
    monkeypatch.setattr(scoped, "drive", lambda: pytest.fail("Drive is outside the approved scope"))
    assert scoped.run() == 0
    assert scoped.manifest["stages"]["drive"]["status"] == "excluded"
    assert scoped.manifest["excluded"]["drive"]["reason"] == "user_scope_exclusion"


def test_resume_cannot_reinterpret_a_legacy_failed_capture(scoped):
    with pytest.raises(ReadFailure, match="policy mismatch"):
        Backup(scoped.output)


def test_scoped_seed_does_not_copy_unused_images(scoped, tmp_path):
    source = tmp_path / "seed"
    relative = "registry/oci/blobs/sha256/" + "a" * 64
    blob = source / relative
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"old unused image")
    (source / "manifest.private.json").write_text(json.dumps({"project": "test-project", "files": {
        relative: {"sha256": hashlib.sha256(blob.read_bytes()).hexdigest()}}}))
    scoped.seed_immutable_files(source)
    assert not (scoped.output / relative).exists()


@pytest.mark.parametrize('matches',[False,True])
def test_missing_generation_never_uses_same_name_without_original_sha_proof(scoped,monkeypatch,matches):
    original=b'{"original":true}'
    digest=hashlib.sha256(original).hexdigest()
    values={("index.json","2"):json.dumps({'detail_uri':'gs://bucket/old.json.gz','detail_generation':'1','detail_sha256':digest}).encode(),
            ("old.json.gz","2"):gzip.compress(original if matches else b'{"original":false}')}
    install_catalog(scoped,monkeypatch,values,list(values))
    result=scoped.storage()
    assert result['missing_required']==(0 if matches else 1)
    assert result['verified_generation_equivalents']==(1 if matches else 0)
    if matches:
        entry=next(iter(scoped.manifest['generation_equivalents'].values()))
        assert entry['requested_generation']=='1' and entry['captured_generation']=='2'
        assert entry['original_generation_fetched'] is False
