from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SECRET = 'PRIVATE_TOKEN_MUST_NOT_APPEAR'


@pytest.mark.parametrize('scenario', ['cleanup_context_and_cause', 'cycle', 'long_chain'])
def test_actual_cli_failure_chain_is_bounded_safe_and_still_fails(tmp_path, scenario):
    operation = 'owned-diagnostic-operation'
    fixture = {'schema_version': 1, 'source_commit': '1' * 40, 'operation_id': operation,
               'kind': 'production_server', 'project_id': 'test-only-project', 'database_id': '(default)',
               'bucket': 'test-only-bucket', 'prefix': f'adapter-proof/{operation}/', 'target_date': '2099-01-01',
               'approval_receipt_sha256': '2' * 64,
               'owned_document_paths': [f'forecast_hourly_snapshots/{operation}-a', f'forecast_hourly_snapshots/{operation}-b'],
               'owned_object_names': [f'adapter-proof/{operation}/a', f'adapter-proof/{operation}/b']}
    fixture['fixture_sha256'] = hashlib.sha256(json.dumps(fixture, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    fixture_path = tmp_path / 'fixture.json'
    fixture_path.write_text(json.dumps(fixture), encoding='utf-8')
    module = tmp_path / 'firestore_server_cases.py'
    if scenario == 'cleanup_context_and_cause':
        code = f'''def first_sdk_operation(depth=40):
    if depth:
        return first_sdk_operation(depth-1)
    raise TimeoutError({SECRET!r})
def cleanup_delete():
    error = TimeoutError({('https://private.invalid/' + SECRET)!r})
    error.__cause__ = ValueError({SECRET!r})
    raise error
def run(fixture):
    try:
        first_sdk_operation()
    finally:
        cleanup_delete()
'''
    elif scenario == 'cycle':
        code = f'''def run(fixture):
    error = TimeoutError({SECRET!r})
    error.__cause__ = error
    raise error
'''
    else:
        code = f'''def run(fixture):
    root = TimeoutError({SECRET!r})
    current = root
    for unused in range(20):
        following = TimeoutError({SECRET!r})
        current.__cause__ = following
        current = following
    raise root
'''
    # The traceback has a Windows private directory even if CI runs on POSIX.
    module.write_text('exec(compile(' + repr(code) + ',' + repr('C:\\' + SECRET + '\\sdk_operation.py') + ',"exec"))\n', encoding='utf-8')
    (tmp_path / 'gcs_server_cases.py').write_text('def run(fixture):\n    raise AssertionError("unexpected GCS operation")\n', encoding='utf-8')
    launcher = tmp_path / 'run_cli.py'
    launcher.write_text('''import importlib.metadata,runpy,sys
expected={"google-cloud-firestore":"2.34.0","google-auth":"2.60.0","google-cloud-storage":"2.19.0","grpcio":"1.84.0"}
importlib.metadata.version=lambda name:expected[name]
sys.path.insert(0,sys.argv[1])
sys.argv=sys.argv[2:]
runpy.run_path(sys.argv[0],run_name="__main__")
''', encoding='utf-8')
    receipt = tmp_path / 'receipt.json'
    result = subprocess.run([sys.executable, str(launcher), str(tmp_path), str(ROOT / 'tests/g07/adapter_proof.py'),
                             '--fixture', str(fixture_path), '--stage', 'all', '--receipt', str(receipt),
                             '--image-digest', 'sha256:' + 'a' * 64], capture_output=True, text=True,
                            encoding='utf-8', timeout=10)
    assert result.returncode == 1, result.stderr
    summary = json.loads(result.stdout)
    body = json.loads(receipt.read_text(encoding='utf-8'))
    detail_text = (tmp_path / 'receipt.json.d/failure.private.json').read_text(encoding='utf-8')
    detail = json.loads(detail_text)
    assert summary['status'] == body['status'] == 'FAIL'
    assert summary['cases'] == 0
    assert summary['failure_chain'] == body['failure_chain'] == detail['failure_chain']
    assert 1 <= len(summary['failure_chain']) <= 8
    for node in summary['failure_chain']:
        assert set(node) == {'exception_type', 'category', 'relation', 'parent', 'frames'}
        assert len(node['frames']) <= 32
        for frame in node['frames']:
            assert set(frame) == {'file', 'function', 'line'}
            assert '/' not in frame['file'] and '\\' not in frame['file']
    for text in (result.stdout, result.stderr, receipt.read_text(encoding='utf-8'), detail_text):
        assert SECRET not in text
        assert str(tmp_path) not in text
        assert 'https://private.invalid' not in text
    if scenario == 'cleanup_context_and_cause':
        assert [item['relation'] for item in summary['failure_chain']] == ['root', 'cause', 'context']
        root, cause, context = summary['failure_chain']
        assert root['category'] == context['category'] == 'TRANSPORT'
        assert cause['category'] == 'VALIDATION'
        assert root['frames'][-1]['function'] == 'cleanup_delete'
        assert context['frames'][-1]['function'] == 'first_sdk_operation'
        assert len(context['frames']) == 32
    elif scenario == 'cycle':
        assert len(summary['failure_chain']) == 1
        assert summary['failure_chain_truncated'] is False
    else:
        assert len(summary['failure_chain']) == 8
        assert summary['failure_chain_truncated'] is True
