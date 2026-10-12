from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.kpnet import soc_setting_probe as probe
from app.kpnet.client import KpNetUnknownWriteError
from app.kpnet.settings_roundtrip import SettingsRoundtripError
from test_kpnet_settings_roundtrip import _current


def setup_client(monkeypatch, *, initial_soc='50', failure=None):
    initial = {**_current(), 'socChargeMode': initial_soc}
    current = dict(initial)
    writes = []
    candidate_calls = []
    sleeps = []

    class Client:
        csrf_setting = 'csrf'
        pcsid = 'device'
        operation_id = 'probe'

        def __init__(self, _cfg):
            self.pending = {}

        def login(self):
            pass

        def open_settings_page(self):
            pass

        def read_current_settings(self):
            return dict(current)

        def candidate_map(self, field, _path):
            candidate_calls.append(field)
            return {'30': '30%', '50': '50%'}

        def confirm_setting(self, payload):
            self.pending = dict(payload)
            return True, '', '', 'form'

        def write_setting(self, _html):
            writes.append(dict(self.pending))
            current.update({k: str(v) for k, v in self.pending.items() if k in current})
            if failure == 'unknown' and len(writes) == 1:
                raise KpNetUnknownWriteError('unknown response')
            if failure == 'known' and len(writes) == 1:
                raise RuntimeError('known response failure')
            return {'changed': True}

        def close(self):
            pass

    clock = [0.0]

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
        if failure == 'other_writer':
            current['batteryOperatingMode'] = '1'

    monkeypatch.setattr(probe, 'KpNetClient', Client)
    monkeypatch.setattr(probe.KpNetConfig, 'from_env', lambda: SimpleNamespace(dry_run=False))
    monkeypatch.setattr(probe.time, 'sleep', sleep)
    monkeypatch.setattr(probe.time, 'monotonic', lambda: clock[0])
    return initial, current, writes, candidate_calls, sleeps


@pytest.mark.parametrize('initial_soc,applied', [('50', 30), ('30', 50)])
def test_soc_only_write_hold_restore(monkeypatch, initial_soc, applied):
    initial, current, writes, candidates, sleeps = setup_client(monkeypatch, initial_soc=initial_soc)
    result = probe.run_soc_setting_probe()
    assert result['status'] == 'passed' and result['restore_verified']
    assert result['applied_target_soc_percent'] == applied
    assert result['changed_fields'] == ['socChargeMode']
    assert result['hold_elapsed_seconds'] == 60
    assert candidates == ['SocChargeMode'] and sleeps == [60]
    assert len(writes) == 2 and current == initial
    for payload in writes:
        assert all(str(payload[k]) == v for k, v in initial.items() if k != 'socChargeMode')


def test_unknown_ack_never_followed_by_write(monkeypatch):
    _, _, writes, _, _ = setup_client(monkeypatch, failure='unknown')
    with pytest.raises(SettingsRoundtripError) as caught:
        probe.run_soc_setting_probe()
    assert len(writes) == 1
    assert caught.value.summary['mutation_outcome'] == 'unknown'


def test_known_failure_restores_only_soc(monkeypatch):
    initial, current, writes, _, _ = setup_client(monkeypatch, failure='known')
    with pytest.raises(SettingsRoundtripError) as caught:
        probe.run_soc_setting_probe()
    assert len(writes) == 2 and current == initial
    assert caught.value.summary['restore_verified']


def test_other_writer_is_not_overwritten(monkeypatch):
    _, current, writes, _, _ = setup_client(monkeypatch, failure='other_writer')
    with pytest.raises(SettingsRoundtripError) as caught:
        probe.run_soc_setting_probe()
    assert len(writes) == 1 and current['batteryOperatingMode'] == '1'
    assert not caught.value.summary['restore_verified']


@pytest.mark.parametrize('arguments', [{'target_soc_percent': 31}, {'target_soc_percent': float('nan')}, {'hold_seconds': 59}])
def test_invalid_contract_fails_before_login(monkeypatch, arguments):
    monkeypatch.setattr(probe.KpNetConfig, 'from_env', lambda: pytest.fail('must not load credentials'))
    with pytest.raises(ValueError):
        probe.run_soc_setting_probe(**arguments)


def test_actual_server_wrapper_requires_deployment_and_exact_execution():
    text = (Path(__file__).parents[1] / 'scripts/run_kpnet_soc_setting_probe_from_env.ps1').read_text(encoding='utf-8')
    assert "state.status -ne 'complete'" in text
    assert 'state.repository_commit -ne $commit' in text
    assert "state.stages.verification.status -ne 'success'" in text
    assert "run.googleapis.com/execution_name" in text
    assert 'Assert-SocIdle $ProbeJobName' in text
    assert 'job_restore_verified' in text
    assert 'SocChargeMode' not in text  # only device module owns settings payloads
    assert 'python .\\scripts' not in text  # physical operation runs on actual server
