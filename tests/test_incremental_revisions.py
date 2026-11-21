from copy import deepcopy
import itertools
import json
import time
import gzip
import hashlib
from types import SimpleNamespace

import grpc
import pytest
from google.cloud.firestore_v1.types import ListenResponse

from app.dashboard import snapshots
from app.dashboard.incremental_revisions import IncrementalRevisionClient, decode_state, encode_state
from app.dashboard.revision_changes import NativeRevisionChanges, RevisionResetRequired, RevisionState, SOURCES, projected_fields, timestamp
from app.dashboard.revision_manifest import ManifestClient, parse_timestamp, wire_timestamp
from app.dashboard.revision_reader import RevisionSnapshot
from test_dashboard_epoch_parallel import ObservedClient

OLD = '2030-01-01T00:00:00Z'
NEW = '2030-01-01T00:00:01.000000123Z'
DB = 'projects/test-owned/databases/test-owned'


def baseline():
    native = ObservedClient()
    state = RevisionState({name: {row.id: {'fields': projected_fields(name, row.to_dict()), 'updated': timestamp(row.update_time)}
                          for row in native.rows.get(name, [])} for name in SOURCES}, OLD)
    return native, state


def barrier():
    return [ListenResponse(target_change={'target_change_type': 3, 'target_ids': list(range(1, len(SOURCES) + 1)), 'read_time': wire_timestamp(NEW)}),
            ListenResponse(target_change={'target_change_type': 0, 'read_time': wire_timestamp(NEW), 'resume_token': b'new-token'})]


def update(name='monitoring_samples', key='0', value='2026-04-17T00:00:00'):
    field = SOURCES[name][0][0]
    return ListenResponse(document_change={'document': {'name': DB + '/documents/' + name + '/' + key,
        'fields': {field: {'string_value': value}}, 'update_time': wire_timestamp(NEW)},
        'target_ids': [list(SOURCES).index(name) + 1]})


class Stream:
    def __init__(self, events):
        self.events = events
        self.cancelled = False

    def __iter__(self):
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event

    def cancel(self):
        self.cancelled = True


class ChangeClient:
    _database_string = DB
    _rpc_metadata = ()

    def __init__(self, streams):
        self.streams, self.requests = [], []
        self.events = iter(streams)
        self._firestore_api = SimpleNamespace(_transport=SimpleNamespace(listen=self.listen))

    def collection(self, name):
        class Query:
            def order_by(self, *_, **__):
                return self

            def limit(self, *_):
                return self

            def _to_protobuf(self):
                return {'from_': [{'collection_id': name}]}
        return Query()

    def listen(self, requests, *, timeout, metadata):
        assert 0 < timeout <= 4
        self.requests.append(list(itertools.islice(requests, len(SOURCES))))
        stream = Stream(next(self.events))
        self.streams.append(stream)
        return stream


@pytest.mark.parametrize('view', [dict(history_scope=False), dict(history_scope=True),
    dict(history_scope=True, history_compare_before='2026-09-10'),
    dict(history_scope=True, excluded_dates=frozenset({'2026-10-13'}))])
def test_manifest_preserves_native_epoch_every_component(view):
    native, state = baseline()
    expected = snapshots._read_dashboard_source_epoch(native, **view)
    assert snapshots._read_dashboard_source_epoch(ManifestClient(state.rows, OLD), **view) == expected


@pytest.mark.parametrize('kind', ['update', 'insert', 'delete', 'remove'])
def test_resume_detects_old_date_changes_without_reading_unchanged_documents(kind):
    _, state = baseline()
    before = deepcopy(state.rows)
    key = 'late-old-date' if kind == 'insert' else '0'
    if kind in ('update', 'insert'):
        events = [update(key=key)]
    else:
        events = [ListenResponse(**{'document_' + kind: {'document': DB + '/documents/monitoring_samples/' + key,
            'removed_target_ids': [list(SOURCES).index('monitoring_samples') + 1], 'read_time': wire_timestamp(NEW)}})]
    client = ChangeClient([events + barrier()])
    result, stats = NativeRevisionChanges(client).refresh(state, time.monotonic() + 10)
    assert state.rows == before and state.read_time == OLD
    if kind in ('delete', 'remove'):
        assert key not in result.rows['monitoring_samples']
    else:
        assert result.rows['monitoring_samples'][key]['updated'] == NEW
    assert stats['document_reads'] <= 1
    assert all(not stream.events[-1:] == [] and stream.cancelled for stream in client.streams)
    assert result.resume_token == b'new-token'
    assert all(request.add_target.read_time == parse_timestamp(OLD) for request in client.requests[0])


def test_restart_uses_token_and_checksum_codec_retains_nanoseconds():
    _, state = baseline()
    state.rows['monitoring_samples']['0']['updated'] = NEW
    state.read_time, state.resume_token = NEW, b'saved-token'
    restored = decode_state(encode_state(state, 'owned'), 'owned')
    client = ChangeClient([barrier()])
    result, stats = NativeRevisionChanges(client).refresh(restored, time.monotonic() + 10)
    assert stats['document_reads'] == 0 and result.rows == state.rows
    assert all(request.add_target.resume_token == b'saved-token' for request in client.requests[0])
    assert getattr(ManifestClient(result.rows, NEW).rows['monitoring_samples']['0'].update_time, 'nanosecond') == 123
    with pytest.raises(ValueError, match='identity'):
        decode_state(encode_state(state, 'owned'), 'foreign')


def test_partial_stream_is_discarded_and_retry_starts_from_previous_cursor(monkeypatch):
    class Lost(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE
    monkeypatch.setattr('app.dashboard.revision_changes.time.sleep', lambda _: None)
    _, state = baseline()
    client = ChangeClient([[update(key='partial'), Lost()], [update(key='complete')] + barrier()])
    result, stats = NativeRevisionChanges(client).refresh(state, time.monotonic() + 10)
    assert 'partial' not in result.rows['monitoring_samples'] and 'complete' in result.rows['monitoring_samples']
    assert stats['document_reads'] == 2 and state.read_time == OLD
    assert all(stream.cancelled for stream in client.streams)
    assert all(requests[0].add_target.read_time == parse_timestamp(OLD) for requests in client.requests)


def test_current_without_global_consistency_never_advances_cursor():
    _, state = baseline()
    client = ChangeClient([[update(), barrier()[0]]])
    with pytest.raises(RevisionResetRequired):
        NativeRevisionChanges(client).refresh(state, time.monotonic() + 10)
    assert state.read_time == OLD and client.streams[0].cancelled


def test_existence_mismatch_rejects_incomplete_cached_set():
    _, state = baseline()
    client = ChangeClient([[ListenResponse(filter={'target_id': 1, 'count': 999})] + barrier()])
    with pytest.raises(RevisionResetRequired, match='existence'):
        NativeRevisionChanges(client).refresh(state, time.monotonic() + 10)
    assert state.read_time == OLD


def test_reset_discards_deleted_rows_before_accepting_new_initial_set():
    _, state = baseline()
    client = ChangeClient([[ListenResponse(target_change={'target_change_type': 4, 'target_ids': [9]}), update(key='new')] + barrier()])
    result, stats = NativeRevisionChanges(client).refresh(state, time.monotonic() + 10)
    assert set(result.rows['monitoring_samples']) == {'new'} and stats['reset_targets'] == 1


def test_bounded_settings_refresh_is_separate_from_history_reset():
    _, state = baseline()
    target = list(SOURCES).index('settings_events') + 1
    client = ChangeClient([[ListenResponse(target_change={'target_change_type': 4, 'target_ids': [target]}),
                           update('settings_events', 'setting', '2026-10-13T00:00:00')] + barrier()])
    result, stats = NativeRevisionChanges(client).refresh(state, time.monotonic() + 10)
    assert len(result.rows['settings_events']) == 1
    assert stats['reset_targets'] == 0 and stats['bounded_reset_targets'] == 1 and stats['settings_document_reads'] == 1


def test_submicrosecond_cursor_and_document_order_are_preserved():
    _, state = baseline()
    state.read_time = NEW
    client = ChangeClient([barrier()])
    NativeRevisionChanges(client).refresh(state, time.monotonic() + 10)
    assert getattr(client.requests[0][0].add_target.read_time, 'nanosecond') == 123
    state.read_time = NEW.replace('123Z', '122Z')
    state.rows['monitoring_samples']['0']['updated'] = NEW
    with pytest.raises(ValueError, match='newer'):
        decode_state(encode_state(state, 'owned'), 'owned')


def test_expired_delta_deadline_starts_no_native_stream():
    _, state = baseline()
    client = ChangeClient([])
    with pytest.raises(RuntimeError, match='deadline'):
        NativeRevisionChanges(client).refresh(state, time.monotonic() - 1)
    assert client.requests == []


def test_new_stage_refreshes_but_adjacent_views_reuse_same_consistent_point(capsys):
    native, state = baseline()
    client = IncrementalRevisionClient(native)
    calls = []

    def view(deadline):
        calls.append(1)
        return ManifestClient(state.rows, OLD), {'document_reads': 1, 'mode': 'delta', 'listen_targets': 12}

    client.metadata_view = view
    group = RevisionSnapshot()
    snapshots._read_dashboard_source_epoch(client, history_scope=True, revision_snapshot=group)
    snapshots._read_dashboard_source_epoch(client, history_scope=False, revision_snapshot=group)
    snapshots._read_dashboard_source_epoch(client, history_scope=True)
    assert len(calls) == 2
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event['document_reads'] for event in events] == [1, 0, 1]
    assert events[1]['mode'] == 'same_snapshot'


@pytest.mark.parametrize('failure', [None, 'unknown_ack'])
def test_checkpoint_uses_original_generation_once_even_when_ack_is_unknown(failure, capsys):
    native, state = baseline()
    client = IncrementalRevisionClient(native)
    client.state, client.identity, client.generation = state, 'owned', 17
    writes = []

    def upload(body, **kwargs):
        writes.append((body, kwargs))
        if failure:
            raise TimeoutError('unknown acknowledgement')

    client.blob = SimpleNamespace(upload_from_string=upload)
    client.save_checkpoint()
    client.save_checkpoint()
    assert len(writes) == 1 and writes[0][1]['if_generation_match'] == 17
    event = json.loads(capsys.readouterr().out)
    assert event['event'] == ('dashboard_revision_checkpoint_save_failed' if failure else 'dashboard_revision_checkpoint_saved')


@pytest.mark.parametrize('corrupt', [False, True])
def test_restart_loads_generation_pinned_checkpoint_or_explicitly_rebuilds(monkeypatch, corrupt):
    native, state = baseline()
    native._database_string = DB
    client = IncrementalRevisionClient(native)
    identity = hashlib.sha256(DB.encode()).hexdigest()
    raw = encode_state(state, identity)
    downloads, full_reads = [], []
    blob = SimpleNamespace(generation=17, size=10, metadata={'sha256': 'wrong' if corrupt else hashlib.sha256(raw).hexdigest()},
                           reload=lambda **_: None)
    def download(**kwargs):
        downloads.append(kwargs)
        return gzip.compress(raw)
    blob.download_as_bytes = download
    monkeypatch.setenv('DASHBOARD_SNAPSHOT_GCS_PREFIX', 'gs://test-owned/dashboard')
    monkeypatch.setattr('google.cloud.storage.Client', lambda: SimpleNamespace(bucket=lambda _: SimpleNamespace(blob=lambda _: blob)))
    def bootstrap(deadline):
        full_reads.append(1)
        return state, 99
    client.changes.bootstrap = bootstrap
    client.changes.refresh = lambda value, deadline: (value, {'document_reads': 0, 'reset_targets': 0})
    view, stats = client.metadata_view()
    assert view.rows['monitoring_samples']
    assert downloads[0]['if_generation_match'] == 17 and downloads[0]['raw_download'] is True
    assert len(full_reads) == int(corrupt)
    assert stats['mode'] == ('bootstrap' if corrupt else 'delta')


@pytest.mark.parametrize('failure', [None, 'partial', 'no_data', 'unknown_save', 'publication', 'receipt', 'active'])
def test_worker_advances_checkpoint_only_after_known_successful_terminal_writes(monkeypatch, tmp_path, failure):
    from app.operations import actual_import as worker
    rows, advances = {}, []

    class Document:
        def __init__(self, key):
            self.key = key

        def get(self, **_):
            return SimpleNamespace(to_dict=lambda: dict(rows.get(self.key, {})))

        def set(self, value, **_):
            terminal = value.get('status') in ('succeeded', 'partial')
            if terminal and ((failure == 'receipt' and self.key != 'active') or (failure == 'active' and self.key == 'active')):
                raise TimeoutError('terminal acknowledgement unknown')
            rows[self.key] = dict(value)

    native = SimpleNamespace(collection=lambda _: SimpleNamespace(document=Document),
        transaction=lambda **_: SimpleNamespace(set=lambda ref, value: ref.set(value)))
    monkeypatch.setattr(worker.source, 'open_firestore', lambda: native)
    monkeypatch.setattr(worker.firestore, 'transactional', lambda callback: callback)
    monkeypatch.setenv('CLOUD_RUN_EXECUTION', 'owned-import')
    monkeypatch.setenv('ACTUAL_IMPORT_TARGET_DATE', '2026-11-03')
    monkeypatch.setenv('ARTIFACTS_DIR', str(tmp_path))
    monkeypatch.setattr(worker, 'capture_date_refresh', lambda *_: {
        'payloads': {'bootstrap': {}, 'history': {}}, 'identity': {}, 'outside_epoch': {},
        'target_plan_version': None, 'storage': object()})
    monkeypatch.setattr(worker, '_run', lambda *_, **__: None)
    monkeypatch.setattr(worker, 'review_missing_actuals', lambda *_: {'status': 'checked'})
    monkeypatch.setattr(worker.IncrementalRevisionClient, 'save_checkpoint', lambda _: advances.append(1))

    def save(*_):
        if failure == 'no_data':
            raise worker.NoActualMeasurementsError('none')
        if failure == 'unknown_save':
            raise TimeoutError('unknown write')
        return ['2026-11-04'] if failure == 'partial' else []

    def publish(*_):
        if failure == 'publication':
            raise ValueError('source changed')

    monkeypatch.setattr(worker, 'save_date_actuals', save)
    monkeypatch.setattr(worker, 'publish_date_refresh', publish)
    if failure in (None, 'partial'):
        assert worker.main() == 0 and advances == [1]
        assert rows['active']['status'] in ('succeeded', 'partial')
    else:
        with pytest.raises((ValueError, TimeoutError)):
            worker.main()
        assert advances == []
