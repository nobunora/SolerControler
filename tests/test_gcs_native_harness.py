from __future__ import annotations

import importlib.util
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage
import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'tests/g07/gcs_server_cases.py'


@pytest.mark.parametrize('failed_readback_generation', [None, 1, 2, 3, 4])
def test_actual_storage_sdk_ack_generations_survive_cas_restore_and_exact_cleanup(monkeypatch, failed_readback_generation):
    spec = importlib.util.spec_from_file_location('native_gcs_case_regression', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_client = storage.Client
    live = {}
    calls = []
    acknowledgements = []
    sequence = [0]
    lock = Lock()

    def client_factory(*, project):
        return original_client(project=project, credentials=AnonymousCredentials())

    def selected(path, query):
        if path not in live:
            raise NotFound('test object absent')
        current = live[path]
        if query.get('generation') is not None and int(query['generation']) != current['generation']:
            raise NotFound('old generation is not retained')
        if query.get('ifGenerationMatch') is not None and int(query['ifGenerationMatch']) != current['generation']:
            raise PreconditionFailed('generation mismatch')
        return current

    def get_resource(client, path, *, query_params, timeout, retry, **kwargs):
        with lock:
            calls.append(('get', path, dict(query_params), timeout, retry))
            current = selected(path, query_params)
            if current['generation'] == failed_readback_generation:
                raise RuntimeError('test metadata readback failure')
            return dict(current['properties'])

    def delete_resource(client, path, *, query_params, timeout, retry, **kwargs):
        with lock:
            current = selected(path, query_params)
            assert int(query_params['generation']) == int(query_params['ifGenerationMatch']) == current['generation']
            calls.append(('delete', path, dict(query_params), timeout, retry))
            del live[path]

    def do_upload(blob, client, file_obj, content_type, size, num_retries, predefined_acl,
                  if_generation_match, if_generation_not_match, if_metageneration_match,
                  if_metageneration_not_match, *, timeout, checksum, retry, command):
        payload = file_obj.read()
        with lock:
            actual = live.get(blob.path, {}).get('generation', 0)
            calls.append(('upload', blob.path, {'ifGenerationMatch': if_generation_match}, timeout, retry))
            if if_generation_match != actual:
                raise PreconditionFailed('generation mismatch')
            sequence[0] += 1
            generation = sequence[0]
            properties = {'name': blob.name, 'bucket': blob.bucket.name, 'generation': str(generation),
                          'metageneration': '1', 'size': str(len(payload))}
            live[blob.path] = {'generation': generation, 'payload': payload, 'properties': properties}
            acknowledgements.append((blob.path, generation, payload))
            return properties

    def do_download(blob, transport, file_obj, download_url, headers, start, end, raw_download, *, timeout, retry, **kwargs):
        query = {key: values[0] for key, values in parse_qs(urlsplit(download_url).query).items()}
        with lock:
            calls.append(('download', blob.path, query, timeout, retry))
            current = selected(blob.path, query)
            assert int(query['generation']) == int(query['ifGenerationMatch']) == current['generation']
            file_obj.write(current['payload'])

    monkeypatch.setattr(storage, 'Client', client_factory)
    monkeypatch.setattr(original_client, '_get_resource', get_resource)
    monkeypatch.setattr(original_client, '_delete_resource', delete_resource)
    monkeypatch.setattr(storage.Blob, '_do_upload', do_upload)
    monkeypatch.setattr(storage.Blob, '_do_download', do_download)
    operation = 'owned-sdk-generation-regression'
    fixture = {'project_id': 'test-only', 'bucket': 'test-only-bucket', 'kind': 'production_server',
               'operation_id': operation, 'owned_object_names': [f'adapter-proof/{operation}/current', f'adapter-proof/{operation}/checkpoint']}
    if failed_readback_generation is not None:
        with pytest.raises(RuntimeError, match='metadata readback failure'):
            module.run(fixture)
        assert live == {}
        deletes = [call for call in calls if call[0] == 'delete']
        latest_ack = dict((path, generation) for path, generation, _ in acknowledgements)
        assert {call[1]: int(call[2]['ifGenerationMatch']) for call in deletes} == latest_ack
        assert all(call[3] == 4 and call[4] is None for call in calls)
        return
    cases = module.run(fixture)
    assert live == {}
    # Anonymous credentials do not prove a native ADC refresh.
    assert cases[0]['status'] == 'INCONCLUSIVE'
    assert all(case['status'] == 'PASS' for case in cases[1:])
    raw = operation.encode()
    assert acknowledgements[0][2] == raw
    assert acknowledgements[1][2] in (raw + b'-a', raw + b'-b')
    assert acknowledgements[2][2] == acknowledgements[3][2] == raw
    deletes = [call for call in calls if call[0] == 'delete']
    assert [int(call[2]['ifGenerationMatch']) for call in deletes] == [acknowledgements[3][1], acknowledgements[2][1]]
    assert all(call[3] == 4 and call[4] is None for call in calls)
    assert sum(call[0] == 'upload' for call in calls) == 6  # includes two rejected conditional writes
