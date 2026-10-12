from __future__ import annotations

import ast
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_v1 import Client, types
import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'tests/g07/firestore_server_cases.py'


def load_harness():
    spec = importlib.util.spec_from_file_location('native_firestore_case_regression', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sdk_client():
    client = Client(project='test-only', database='test-only', credentials=AnonymousCredentials())
    reference = client.document('forecast_hourly_snapshots/owned-harness-test')
    calls = []

    def batch_get_documents(*, request, **kwargs):
        calls.append(('get', kwargs))
        return iter([types.BatchGetDocumentsResponse(missing=reference._document_path)])

    def commit(*, request, **kwargs):
        calls.append(('commit', kwargs))
        return types.CommitResponse(commit_time=datetime.now(timezone.utc))

    api = SimpleNamespace(batch_get_documents=batch_get_documents, commit=commit)
    client._firestore_api_internal = api
    return client, reference, api, calls


def test_actual_sdk_document_get_accepts_recorded_stream_iterator_without_network():
    module = load_harness()
    client, reference, api, calls = sdk_client()
    recorder = module.RpcRecorder(api)
    client._firestore_api_internal = recorder
    snapshot = reference.get(timeout=4, retry=None)
    assert snapshot.exists is False
    assert [name for name, _ in calls] == ['get']
    assert calls[0][1]['timeout'] == 4 and calls[0][1]['retry'] is None
    assert recorder.calls[0]['method'] == 'batch_get_documents'
    assert recorder.calls[0]['retry_disabled'] and recorder.calls[0]['timeout'] == 4
    client._firestore_api_internal = api
    client.close()


def test_actual_cleanup_restores_original_rpc_before_delete_commit_and_retains_primary():
    module = load_harness()
    client, reference, api, calls = sdk_client()
    recorder = module.RpcRecorder(api)
    recorder.drop_commit_ack = True
    client._firestore_api_internal = recorder
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    run = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run')
    guarded = next(node for node in run.body if isinstance(node, ast.Try) and node.finalbody)
    cleanup = compile(ast.Module(body=guarded.finalbody, type_ignores=[]), str(SOURCE), 'exec')
    cases = []
    environment = {'client': client, 'references': [reference],
                   'versions': {reference.path: datetime.now(timezone.utc)},
                   'cases': cases, 'RpcRecorder': module.RpcRecorder}
    with pytest.raises(TypeError, match='primary SDK operation failed'):
        try:
            raise TypeError('primary SDK operation failed')
        finally:
            exec(cleanup, environment)
    assert client._firestore_api_internal is api
    assert [name for name, _ in calls] == ['commit', 'get']
    assert all(kwargs['timeout'] == 4 and kwargs['retry'] is None for _, kwargs in calls)
    assert recorder.calls == [] and recorder.drop_commit_ack is True
    assert cases == [{'name': 'firestore_owned_cleanup_native_absence', 'status': 'PASS', 'documents': 1}]


def test_ack_loss_injection_still_occurs_after_delegated_commit():
    module = load_harness()
    _, _, api, calls = sdk_client()
    recorder = module.RpcRecorder(api)
    recorder.drop_commit_ack = True
    with pytest.raises(TimeoutError, match='after actual commit'):
        recorder.commit(request={'writes': []}, timeout=4, retry=None)
    assert [name for name, _ in calls] == ['commit']
    assert recorder.drop_commit_ack is False
    assert recorder.calls[0]['method'] == 'commit'
