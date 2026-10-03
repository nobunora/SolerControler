from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from google.api_core.exceptions import PreconditionFailed

from app.backup.plan_snapshot import archive_plan_snapshot, embed_plan_detail, restore_snapshot_payload
from app.backup.drive import build_firestore_snapshot
from app.runtime import cloud_job


class Blob:
    generation = '1'

    def __init__(self):
        self.payload = None

    def upload_from_string(self, payload, **kwargs):
        assert kwargs['if_generation_match'] == 0
        assert kwargs['retry'] is None
        if self.payload is not None:
            raise PreconditionFailed('exists')
        self.payload = payload

    def download_as_bytes(self, **kwargs):
        assert kwargs['raw_download'] is True
        return self.payload


class Storage:
    def __init__(self):
        self.blobs = {}

    def bucket(self, _name):
        return self

    def blob(self, name):
        return self.blobs.setdefault(name, Blob())


class Document:
    def __init__(self, records, key):
        self.records = records
        self.id = key

    def set(self, doc, **_kwargs):
        self.records[self.id] = dict(doc)

    def to_dict(self):
        return self.records[self.id]


class Collection:
    def __init__(self):
        self.records = {}

    def document(self, key):
        return Document(self.records, key)

    def stream(self):
        return [self.document(k) for k in self.records]


class Firestore:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, Collection())


def plan_file(tmp_path, target=45):
    path = tmp_path/'plan.json'
    path.write_text(json.dumps({'forecast': {'date': '2026-10-03'},
                               'generated_at': '2026-10-02T18:01:00Z',
                               'inputs': {'soc_now_percent': 10},
                               'result': {'target_soc_7_percent': target}}), encoding='utf-8')
    return path


def test_versions_retry_backup_and_offline_restore(tmp_path):
    storage, db = Storage(), Firestore()
    path = plan_file(tmp_path)
    first_bytes = path.read_bytes()
    kwargs = dict(storage=storage, firestore=db, source='adjust03-generated', prefix='gs://test/plans')
    first = archive_plan_snapshot(path, **kwargs)
    assert archive_plan_snapshot(path, **kwargs) == first
    path = plan_file(tmp_path, 60)
    second_bytes = path.read_bytes()
    second = archive_plan_snapshot(path, **kwargs)
    assert first['decision_id'] != second['decision_id']
    assert len(storage.blobs) == 2
    snapshot = build_firestore_snapshot(db, storage_client=storage)
    assert snapshot['counts']['night_plan_decisions'] == 2
    assert all(r['record_status'] == 'generated' for r in snapshot['collections']['night_plan_decisions'])
    assert all(r['plan_json'] for r in snapshot['collections']['night_plan_decisions'])
    # Restore works with no storage client and retains exact original bytes.
    restored = restore_snapshot_payload(snapshot, tmp_path/'restored')
    assert {p.read_bytes() for p in restored} == {first_bytes, second_bytes}
    cumulative = {'backup_type': 'data_generations', 'generations': [{'snapshot': snapshot}]}
    assert len(restore_snapshot_payload(cumulative, tmp_path/'cumulative')) == 2
    snapshot['collections']['night_plan_decisions'][0]['plan_json'] += ' '
    with pytest.raises(ValueError, match='checksum'):
        restore_snapshot_payload(snapshot, tmp_path/'bad')
    assert not list((tmp_path/'bad').glob('*.json'))


def test_corrupt_existing_object_is_not_indexed(tmp_path):
    storage, db = Storage(), Firestore()
    path = plan_file(tmp_path)
    kwargs = dict(storage=storage, firestore=db, source='test', prefix='gs://test/plans')
    archive_plan_snapshot(path, **kwargs)
    next(iter(storage.blobs.values())).payload = gzip.compress(b'{}')
    db.collections.clear()
    with pytest.raises(ValueError, match='read-back mismatch'):
        archive_plan_snapshot(path, **kwargs)
    assert not db.collections


def test_backup_rejects_bad_inline_hash():
    with pytest.raises(ValueError, match='checksum'):
        embed_plan_detail({'plan_json': '{}', 'detail_sha256': 'wrong'}, storage=Storage())


def test_archive_failure_and_time_budget_do_not_gate_control(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('DATA_BACKEND', 'firestore')
    monkeypatch.setattr(cloud_job, '_tokyo_now', lambda: datetime(2026, 10, 3, 3, 0, tzinfo=ZoneInfo('Asia/Tokyo')))
    calls = []
    def fail(command, **kwargs):
        calls.append((command, kwargs))
        raise TimeoutError('storage unavailable')
    monkeypatch.setattr(cloud_job, '_run', fail)
    cloud_job._archive_generated_plan(plan_file(tmp_path))
    assert len(calls) == 1 and calls[0][1]['timeout_seconds'] == 20
    assert '"status": "failed"' in capsys.readouterr().out
    monkeypatch.setattr(cloud_job, '_tokyo_now', lambda: datetime(2026, 10, 3, 6, 44, 30, tzinfo=ZoneInfo('Asia/Tokyo')))
    cloud_job._archive_generated_plan(tmp_path/'plan.json')
    assert len(calls) == 1
    assert 'skipped_time_budget' in capsys.readouterr().out


def test_generation_entrypoint_records_archive(monkeypatch, tmp_path):
    path = plan_file(tmp_path)
    calls = []
    monkeypatch.setenv('ADJUST03_REGENERATE_PLAN', 'true')
    monkeypatch.setattr(cloud_job, '_before_03_external_io', lambda: None)
    monkeypatch.setattr(cloud_job, '_run', lambda *args, **kwargs: None)
    monkeypatch.setattr(cloud_job, '_archive_generated_plan', lambda p: calls.append(p))
    assert cloud_job._ensure_night_plan_available(path)
    assert calls == [path]


def test_drive_scheduler_is_independent_and_not_deleted_when_enabled():
    source = (Path(__file__).resolve().parents[1]/'scripts/deploy_gcp_jobs.ps1').read_text(encoding='utf-8')
    assert 'Upsert-SchedulerRunJob -SchedulerName $DriveBackupSchedulerName' in source
    assert 'if (-not $driveBackupFolderResolved) { Delete-RunJobIfExists -Name $DriveBackupJobName }' in source
    assert '--args "scripts/backup_drive.py,--mode,data"' in source
