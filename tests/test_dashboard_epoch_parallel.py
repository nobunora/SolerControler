from __future__ import annotations

from collections import Counter
from concurrent.futures import CancelledError, Future
import copy
from datetime import datetime, timedelta, timezone
import json
import threading
import time
from types import SimpleNamespace

import pytest

from app.dashboard import snapshots
from test_dashboard_publication_generation import (
    PublicationBlob, RevisionClient, RevisionDocument, RevisionQuery, identity, storage_for,
)


class ObservedQuery(RevisionQuery):
    def __init__(self, client, name, rows, filters=(), field=None, cap=None, tail=False, after=None, mask=()):
        super().__init__(rows, filters, field, cap, tail, after)
        self.client, self.name, self.mask = client, name, mask

    def clone(self, **updates):
        values = dict(filters=self.filters, field=self.field, cap=self.cap, tail=self.tail, after=self.after, mask=self.mask)
        values.update(updates)
        return ObservedQuery(self.client, self.name, self.rows, **values)

    def select(self, fields):
        return self.clone(mask=tuple(fields))

    def where(self, field, operator, value):
        return self.clone(filters=(*self.filters, (field, operator, value)))

    def order_by(self, field):
        return self.clone(field=self.field if field == '__name__' else field)

    def limit(self, count):
        return self.clone(cap=count, tail=False)

    def limit_to_last(self, count):
        return self.clone(cap=count, tail=True)

    def start_after(self, snapshot):
        return self.clone(after=(str(snapshot.value[self.field]), snapshot.id))

    def stream(self, **kwargs):
        return iter(self.client.read(self, kwargs))

    def get(self, **kwargs):
        return self.client.read(self, kwargs)


class ObservedClient(RevisionClient):
    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.calls, self.active, self.peak, self.documents, self.payload_bytes = [], 0, 0, 0, 0
        self.per_source, self.monitoring_peak = Counter(), 0
        self.hook = None
        stamp = datetime(2026, 10, 11, 3, tzinfo=timezone.utc)
        for name in ('sunshine_daily', 'battery_daily_metrics', 'forecast_hourly', 'forecast_hourly_snapshots',
                     'forecast_hourly_reconstructed', 'forecast_plans', 'dashboard_daily_metrics', 'night_charge_plans'):
            self.rows[name] = [RevisionDocument(day + '-source', {'date': day}, stamp)
                               for day in ('2026-04-17', '2026-10-13')]
        self.rows['night_charge_plans'][-1].value['decision_rationale'] = {
            'forecast_correction': {'recent_and_analog_hourly_floor': {'analog_day': '2026-03-01'}}}
        self.rows['night_charge_plans'].extend([
            RevisionDocument('2026-03-01', {'date': '2026-03-01'}, stamp),
            RevisionDocument('latest', {'date': '2026-10-13'}, stamp)])
        self.rows['model_parameters'] = [RevisionDocument('pv', {'name': 'pv'}, stamp)]
        self.rows['settings_events'] = [RevisionDocument('setting', {'recorded_at': '2026-10-13T00:00:00'}, stamp)]
        self.rows['cost_daily'] = [RevisionDocument('2026-04-17', {'date': '2026-04-17'}, stamp)]
        start = datetime(2026, 4, 17)
        self.rows['monitoring_samples'] = [RevisionDocument(str(index), {
            'ts': (start + timedelta(minutes=30 * index)).isoformat()}, stamp) for index in range(3661)]

    def collection(self, name):
        return ObservedQuery(self, name, self.rows.get(name, []))

    def read(self, query, kwargs):
        assert 0 < kwargs['timeout'] <= 4 and kwargs['retry'] is None
        shape = (query.name, query.filters, query.field, query.cap, query.tail, query.after, query.mask)
        with self.lock:
            self.calls.append(shape)
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.per_source[query.name] += 1
            if query.name == 'monitoring_samples':
                self.monitoring_peak = max(self.monitoring_peak, self.per_source[query.name])
        try:
            if self.hook:
                self.hook(query)
            rows = copy.deepcopy(query._selected())
            encoded = sum(len(json.dumps(row.to_dict(), sort_keys=True).encode()) for row in rows)
            with self.lock:
                self.documents += len(rows)
                self.payload_bytes += encoded
            return rows
        finally:
            with self.lock:
                self.active -= 1
                self.per_source[query.name] -= 1

    def get_all(self, refs, **kwargs):
        assert 0 < kwargs['timeout'] <= 4 and kwargs['retry'] is None and kwargs['field_paths'] == ['date']
        ids = tuple(ref.id for ref in refs)
        if '2026-03-01' in ids:
            assert any(call[0] == 'night_charge_plans' and call[1] for call in self.calls)
        with self.lock:
            self.calls.append(('get_all', ids, tuple(kwargs['field_paths'])))
            self.documents += sum(ref.exists for ref in refs)
            self.payload_bytes += sum(len(json.dumps(ref.to_dict(), sort_keys=True).encode()) for ref in refs if ref.exists)
        return iter(copy.deepcopy(refs))


class InlineExecutor:
    def __init__(self, **kwargs):
        assert kwargs['max_workers'] == 4

    def submit(self, callback, reader):
        future = Future()
        try:
            future.set_result(callback(reader))
        except BaseException as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, *, wait, cancel_futures):
        assert wait and cancel_futures


def epoch_threads():
    return [thread for thread in threading.enumerate() if thread.name.startswith('dashboard-epoch')]


@pytest.mark.parametrize('history_scope', [False, True])
def test_parallel_epoch_preserves_all_components_requests_bytes_and_dependencies(monkeypatch, capsys, history_scope):
    serial, parallel = ObservedClient(), ObservedClient()
    with monkeypatch.context() as patch:
        patch.setattr(snapshots, 'ThreadPoolExecutor', InlineExecutor)
        expected = snapshots._read_dashboard_source_epoch(serial, history_scope=history_scope)
    # Force multiple overlapping SDK reads while preserving immutable results.
    parallel.hook = lambda query: time.sleep(0.005)
    observed = snapshots._read_dashboard_source_epoch(parallel, history_scope=history_scope)
    assert observed == expected
    if history_scope:
        assert len(observed) == 42
    assert Counter(map(repr, serial.calls)) == Counter(map(repr, parallel.calls))
    assert serial.documents == parallel.documents and serial.payload_bytes == parallel.payload_bytes
    assert 1 < parallel.peak <= 4 and parallel.monitoring_peak == 1
    assert 'analog_plans' in observed and not epoch_threads()
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[0] == events[1]


@pytest.mark.parametrize('cancel_finishes_first', [False, True])
def test_worker_failure_keeps_original_error_stops_second_bound_rpc_and_never_puts(monkeypatch, cancel_finishes_first):
    monkeypatch.setenv('DASHBOARD_SNAPSHOT_GCS_PREFIX', 'gs://owned-test/dashboard')
    client, blob = ObservedClient(), PublicationBlob()
    barrier = threading.Barrier(4)
    release = threading.Event()
    original = OSError('owned worker failure')
    if cancel_finishes_first:
        cancelled = threading.Event()
        set_exception = Future.set_exception

        def delayed_original(future, error):
            if error is original:
                assert cancelled.wait(2)
            set_exception(future, error)
            if isinstance(error, CancelledError):
                cancelled.set()

        monkeypatch.setattr(Future, 'set_exception', delayed_original)

    def fail(query):
        barrier.wait(timeout=2)
        if query.name == 'cost_daily':
            release.set()
            raise original
        assert release.wait(2)
        time.sleep(0.02)

    client.hook = fail
    with pytest.raises(OSError) as caught:
        snapshots._upload_artifact(snapshots.artifact_from_payload(snapshots.HISTORY_KIND, {}),
            storage_client=storage_for(blob), publication_identity=identity(),
            source_epoch={'cost_daily': 'unreached'}, source_client=client, history_scope=True)
    assert caught.value is original
    assert len(client.calls) == 4 and client.active == 0 and not epoch_threads()
    assert blob.uploads == []


def test_expired_shared_deadline_cancels_queued_and_followup_reads(monkeypatch):
    client = ObservedClient()
    clock = [0.0]
    monkeypatch.setattr(snapshots, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    barrier = threading.Barrier(4)

    def expire(query):
        barrier.wait(timeout=2)
        clock[0] = 11.0
        time.sleep(0.01)

    client.hook = expire
    with pytest.raises(RuntimeError, match='deadline'):
        snapshots._read_dashboard_source_epoch(client, history_scope=True, deadline=10.0)
    assert len(client.calls) == 4 and client.active == 0 and not epoch_threads()


def test_main_wait_interruption_sets_stop_before_workers_start_second_rpc(monkeypatch):
    client = ObservedClient()
    ready, release = threading.Event(), threading.Event()
    original = KeyboardInterrupt('owned main interruption')

    def block(query):
        if client.active == 4:
            ready.set()
        assert release.wait(2)
        time.sleep(0.02)

    def interrupted_wait(*args, **kwargs):
        assert ready.wait(2)
        release.set()
        raise original

    client.hook = block
    monkeypatch.setattr(snapshots, 'wait', interrupted_wait)
    with pytest.raises(KeyboardInterrupt) as caught:
        snapshots._read_dashboard_source_epoch(client, history_scope=True)
    assert caught.value is original
    assert len(client.calls) == 4 and client.active == 0 and not epoch_threads()
