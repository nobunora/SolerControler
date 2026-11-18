from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import DeadlineExceeded, PermissionDenied

from app.dashboard.revision_reader import RevisionReader
from app.operations.actual_publication_resume import decode_checkpoint, encode_checkpoint


def reader():
    stamp = datetime(2026, 11, 18, tzinfo=timezone.utc)
    anchor = SimpleNamespace(get=lambda **_: SimpleNamespace(read_time=stamp))
    client = SimpleNamespace(collection=lambda _: SimpleNamespace(document=lambda _: anchor))
    return RevisionReader(client, lambda: 4.0)


def test_partial_stream_is_discarded_and_retried_at_same_snapshot(monkeypatch):
    monkeypatch.setattr('app.dashboard.revision_reader.time.sleep', lambda _: None)
    session = reader()
    calls = []

    def stream(**kwargs):
        calls.append(kwargs)
        yield 'first'
        if len(calls) == 1:
            raise DeadlineExceeded('partial transport loss')
        yield 'second'

    assert session.query(SimpleNamespace(stream=stream)) == ['first', 'second']
    assert len(calls) == 2
    assert calls[0]['read_time'] == calls[1]['read_time'] == session.read_time
    assert calls[0]['retry'] is not None
    assert not calls[0]['retry']._predicate(DeadlineExceeded('no hidden retry'))


def test_authentication_failure_is_not_retried():
    calls = []

    def stream(**_):
        calls.append(1)
        raise PermissionDenied('not transient')

    with pytest.raises(PermissionDenied):
        reader().query(SimpleNamespace(stream=stream))
    assert calls == [1]


def test_transient_failures_have_finite_attempts(monkeypatch):
    monkeypatch.setattr('app.dashboard.revision_reader.time.sleep', lambda _: None)
    calls = []

    def stream(**_):
        calls.append(1)
        raise DeadlineExceeded('still unavailable')

    with pytest.raises(DeadlineExceeded):
        reader().query(SimpleNamespace(stream=stream))
    assert len(calls) == 3


def test_checkpoint_retains_exact_payload_and_rejects_wrong_scope_or_hash():
    captured = {'payloads': {'bootstrap': {'zero': 0}, 'history': {}}, 'identity': {'raw_plan_sha256': 'original'},
                'outside_epoch': {'old': 'revision'}, 'target_plan_version': None, 'storage': object()}
    checkpoint = encode_checkpoint(captured, '2026-11-18')
    restored = decode_checkpoint(checkpoint, '2026-11-18')
    assert restored == {key: value for key, value in captured.items() if key != 'storage'}
    with pytest.raises(ValueError, match='target'):
        decode_checkpoint(checkpoint, '2026-11-19')
    with pytest.raises(ValueError, match='checksum'):
        decode_checkpoint({**checkpoint, 'sha256': '0' * 64}, '2026-11-18')


def test_committed_actuals_resume_publication_without_download_or_save(monkeypatch, tmp_path):
    from app.operations import actual_import as worker

    rows, downloads, saves, publications = {}, [], [], []

    class Document:
        def __init__(self, key):
            self.key = key

        def get(self, **_):
            return SimpleNamespace(to_dict=lambda: dict(rows.get(self.key, {})))

        def set(self, value, **_):
            rows[self.key] = dict(value)

    client = SimpleNamespace(collection=lambda _: SimpleNamespace(document=Document),
        transaction=lambda **_: SimpleNamespace(set=lambda doc, value: doc.set(value)))
    monkeypatch.setattr(worker.source, 'open_firestore', lambda: client)
    monkeypatch.setattr(worker.firestore, 'transactional', lambda callback: callback)
    monkeypatch.setattr('google.cloud.storage.Client', lambda: object())
    monkeypatch.setenv('ACTUAL_IMPORT_TARGET_DATE', '2026-11-03')
    monkeypatch.setenv('ARTIFACTS_DIR', str(tmp_path))
    monkeypatch.setattr(worker, 'capture_date_refresh', lambda *_: {
        'payloads': {'bootstrap': {}, 'history': {}}, 'identity': {},
        'outside_epoch': {}, 'target_plan_version': None, 'storage': object()})
    monkeypatch.setattr(worker, '_run', lambda *_args, **_kw: downloads.append(1))
    monkeypatch.setattr(worker, 'review_missing_actuals', lambda *_: {'status': 'checked'})
    monkeypatch.setattr(worker, 'save_date_actuals', lambda *_: saves.append(1) or [])

    def publish(*_):
        publications.append(1)
        if len(publications) == 1:
            raise DeadlineExceeded('saved actuals; display read failed')

    monkeypatch.setattr(worker, 'publish_date_refresh', publish)
    monkeypatch.setenv('CLOUD_RUN_EXECUTION', 'solar-actual-import-first')
    with pytest.raises(DeadlineExceeded):
        worker.main()
    assert rows['solar-actual-import-first']['actuals_saved'] is True
    monkeypatch.setenv('CLOUD_RUN_EXECUTION', 'solar-actual-import-second')
    assert worker.main() == 0
    assert downloads == [1] and saves == [1] and publications == [1, 1]
    assert rows['solar-actual-import-first']['status'] == 'failed'
    assert rows['solar-actual-import-second']['status'] == 'succeeded'
    assert 'publication_resume' not in rows['active']


def test_unknown_actual_commit_blocks_claim_even_after_worker_failure(monkeypatch):
    from app.operations import actual_import as worker

    monkeypatch.setattr(worker.firestore, 'transactional', lambda callback: callback)
    writes = []
    document = SimpleNamespace(get=lambda **_: SimpleNamespace(to_dict=lambda: {
        'status': 'failed', 'stage': 'actual_save', 'actuals_saved': None}))
    client = SimpleNamespace(collection=lambda _: SimpleNamespace(document=lambda _: document),
        transaction=lambda **_: SimpleNamespace(set=lambda *args: writes.append(args)))
    assert worker._claim(client, 'new-execution') is False
    assert writes == []
