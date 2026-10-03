from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from google.api_core.exceptions import AlreadyExists, PreconditionFailed

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
        self.records[self.id] = {**self.records.get(self.id, {}), **doc} if _kwargs.get('merge') else dict(doc)

    def create(self, doc, **_kwargs):
        if self.id in self.records:
            raise AlreadyExists('exists')
        self.set(doc)

    def get(self, **kwargs):
        if kwargs.get('transaction'):
            assert not kwargs['transaction'].writes
        return self

    def to_dict(self):
        return self.records.get(self.id)


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

    def transaction(self, **kwargs):
        assert kwargs['max_attempts'] == 1
        return Transaction()


class Transaction:
    _max_attempts = 1
    _read_only = False
    _id = b'local-test'

    def __init__(self):
        self.writes = []

    def _clean_up(self):
        self.writes.clear()

    def _begin(self, **kwargs):
        pass

    def set(self, ref, value, **kwargs):
        self.writes.append((ref, value, kwargs))

    def _commit(self):
        for ref, value, kwargs in self.writes:
            ref.set(value, **kwargs)

    def _rollback(self):
        self.writes.clear()


def plan_file(tmp_path, target=45):
    path = tmp_path/'plan.json'
    path.write_text(json.dumps({'forecast': {'date': '2026-10-03'},
                               'generated_at': '2026-10-02T18:01:00Z' if target == 45 else '2026-10-02T18:02:00Z',
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
    assert db.collection('night_charge_plans').records['latest']['plan_json'].encode() == second_bytes
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


def test_legacy_missing_original_does_not_block_other_plan_backup(tmp_path):
    storage, db = Storage(), Firestore()
    path = plan_file(tmp_path)
    archive_plan_snapshot(path, storage=storage, firestore=db, source='adjust03-generated', prefix='gs://test/plans')
    legacy = {'date': '2026-07-12', 'plan_json': None, 'detail_gcs_uri': None, 'detail_sha256': 'original-hash'}
    db.collection('night_charge_plans').records['2026-07-12'] = legacy
    snapshot = build_firestore_snapshot(db, storage_client=storage)
    assert snapshot['plan_details_unavailable'] == 1
    saved = next(r for r in snapshot['collections']['night_charge_plans'] if r['date'] == '2026-07-12')
    assert saved['detail_sha256'] == 'original-hash'
    assert saved['detail_backup_status'] == 'unavailable_legacy'
    restored = restore_snapshot_payload(snapshot, tmp_path/'restored')
    assert [p.read_bytes() for p in restored] == [path.read_bytes()]
    with pytest.raises(ValueError, match='no restorable'):
        restore_snapshot_payload({'collections': {'night_charge_plans': [saved]}}, tmp_path/'legacy-only')


def test_missing_original_of_new_decision_is_still_an_error():
    with pytest.raises(ValueError, match='invalid GCS URI'):
        embed_plan_detail({'decision_id': 'new', 'plan_json': None}, storage=Storage(), allow_legacy_summary=True)


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


def test_reused_plan_does_not_claim_current_model_revision(monkeypatch, tmp_path):
    monkeypatch.setenv('PLAN_SOURCE_REVISION', 'current-revision')
    db = Firestore()
    result = archive_plan_snapshot(plan_file(tmp_path), storage=Storage(), firestore=db,
                                   source='adjust03-reused', prefix='gs://test/plans')
    doc = db.collection('night_plan_decisions').records[result['decision_id']]
    assert doc['source_revision'] is None
    assert doc['recorder_source_revision'] == 'current-revision'


def test_drive_scheduler_is_independent_and_not_deleted_when_enabled():
    source = (Path(__file__).resolve().parents[1]/'scripts/deploy_gcp_jobs.ps1').read_text(encoding='utf-8')
    assert 'Upsert-SchedulerRunJob -SchedulerName $DriveBackupSchedulerName' in source
    assert 'if (-not $driveBackupFolderResolved) { Delete-RunJobIfExists -Name $DriveBackupJobName }' in source
    assert '--args "scripts/backup_drive.py,--mode,data"' in source


def test_retry_keeps_first_provenance_and_latest_does_not_roll_back(monkeypatch, tmp_path):
    storage, db = Storage(), Firestore()
    monkeypatch.setenv('PLAN_SOURCE_REVISION', 'original')
    path = plan_file(tmp_path)
    original = path.read_bytes()
    kwargs = dict(storage=storage, firestore=db, prefix='gs://test/plans')
    first = archive_plan_snapshot(path, source='adjust03-generated', **kwargs)
    old_doc = dict(db.collection('night_plan_decisions').records[first['decision_id']])
    newer = json.loads(original)
    newer['generated_at'] = '2026-10-02T18:05:00Z'
    newer['result']['target_soc_7_percent'] = 65
    path.write_text(json.dumps(newer), encoding='utf-8')
    archive_plan_snapshot(path, source='adjust03-generated', **kwargs)
    expected_latest = dict(db.collection('night_charge_plans').records['latest'])
    path.write_bytes(original)
    monkeypatch.setenv('PLAN_SOURCE_REVISION', 'new-recorder')
    archive_plan_snapshot(path, source='adjust03-reused', **kwargs)
    assert db.collection('night_plan_decisions').records[first['decision_id']] == old_doc
    assert db.collection('night_charge_plans').records['latest'] == expected_latest
    assert db.collection('night_charge_plans').records['2026-10-03']['detail_sha256'] == expected_latest['detail_sha256']


def test_restore_conflicting_destination_does_not_write_other_plans(tmp_path):
    import hashlib
    raw1 = plan_file(tmp_path).read_bytes()
    raw2 = plan_file(tmp_path, 70).read_bytes()
    snapshot = {'collections': {'night_plan_decisions': [
        {'plan_json': r.decode(), 'detail_sha256': hashlib.sha256(r).hexdigest()} for r in (raw1, raw2)]}}
    destination = tmp_path/'restored'
    destination.mkdir()
    conflict = destination/f'2026-10-03--{hashlib.sha256(raw2).hexdigest()}.json'
    conflict.write_bytes(b'keep me')
    with pytest.raises(ValueError, match='conflicts'):
        restore_snapshot_payload(snapshot, destination)
    assert list(destination.iterdir()) == [conflict]
    assert conflict.read_bytes() == b'keep me'


@pytest.mark.parametrize('payload', [{}, {'collections': {}}, {'backup_type': 'data_generations', 'generations': [None]}])
def test_invalid_or_empty_restore_is_not_success(tmp_path, payload):
    with pytest.raises(ValueError):
        restore_snapshot_payload(payload, tmp_path/'invalid')
    assert not list((tmp_path/'invalid').glob('*.json'))


def test_deploy_does_not_claim_checkout_revision_for_reused_image():
    source = (Path(__file__).resolve().parents[1]/'scripts/deploy_gcp_jobs.ps1').read_text(encoding='utf-8')
    assert 'if ($SkipBuild -or $sourceChanges.Count -gt 0) { $planSourceRevision = "" }' in source
    assert '"PLAN_IMAGE_DIGEST=$imageDigest"' in source


def test_transaction_abort_does_not_overwrite_read_models(monkeypatch, tmp_path):
    from google.api_core.exceptions import Aborted
    storage, db = Storage(), Firestore()
    kwargs = dict(storage=storage, firestore=db, source='adjust03-generated', prefix='gs://test/plans')
    archive_plan_snapshot(plan_file(tmp_path), **kwargs)
    original = dict(db.collection('night_charge_plans').records['latest'])
    def abort(_self):
        raise Aborted('concurrent update')
    monkeypatch.setattr(Transaction, '_commit', abort)
    with pytest.raises(ValueError, match='Failed to commit'):
        archive_plan_snapshot(plan_file(tmp_path, 60), **kwargs)
    assert db.collection('night_charge_plans').records['latest'] == original
    assert len(db.collection('night_plan_decisions').records) == 2
