from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.runtime import control_live_probe as probe
from test_kpnet_settings_roundtrip import _current
from test_plan_snapshot_recovery import Firestore, Storage, plan_file


@pytest.fixture
def rig(monkeypatch, tmp_path):
    state = _current()
    state['batteryOperatingMode'] = '1'
    initial = state.copy()
    writes = []

    class Client:
        csrf_setting = 'csrf'
        pcsid = 'pcs'

        def __init__(self, *args, **kwargs):
            self.pending = {}

        def login(self):
            pass

        def open_settings_page(self):
            pass

        def read_current_settings(self):
            return state.copy()

        def candidate_map(self, field, path):
            assert field == 'BatteryOperatingMode'
            return {'0': 'economy', '1': 'green', '3': 'forced charge', '5': 'standby'}

        def confirm_setting(self, payload):
            self.pending = payload
            return True, 'ok', '', '<form/>'

        def write_setting(self, html):
            writes.append(self.pending['batteryOperatingMode'])
            for key, value in self.pending.items():
                if key in state:
                    state[key] = value

        def close(self):
            pass

    db, storage = Firestore(), Storage()
    monkeypatch.setattr(probe.KpNetConfig, 'from_env', lambda: SimpleNamespace(dry_run=False))
    monkeypatch.setattr(probe, 'KpNetClient', Client)
    monkeypatch.setattr(probe, 'open_firestore', lambda: db)
    monkeypatch.setattr(probe, 'ProbeFirestore', lambda client, run_id: client)
    monkeypatch.setattr(probe.storage, 'Client', lambda: storage)
    monkeypatch.setattr(probe, 'night_plan_archive_prefix', lambda: 'gs://test/plans')
    monkeypatch.setattr(probe.ProbeClock, 'check', lambda self: None)
    monkeypatch.setattr(probe.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(probe.cloud_job, '_latest_kpnet_csv_paths', lambda path: [])
    monkeypatch.setenv('ADJUST03_MAX_CONSECUTIVE_SOC_FAILURES', '3')
    path = plan_file(tmp_path)
    def readings(values):
        items = iter(values)
        monkeypatch.setattr(probe, 'latest_realtime_soc_percent', lambda **kwargs: next(items))
    return SimpleNamespace(state=state, initial=initial, writes=writes, db=db, storage=storage,
                           path=path, readings=readings, client=Client)


def test_real_controller_archive_join_and_rising_stop(rig):
    rig.readings([40, 40, 40, 41])
    original = rig.path.read_bytes()
    result = probe.run_controller_probe(rig.path)
    assert result['status'] == 'passed' and result['target_reached'] is True
    assert result['monitor_initial_below_target'] is True
    assert result['restore_verified'] is True
    assert rig.state == rig.initial and rig.writes == ['3', '5', '1']
    assert rig.path.read_bytes() == original
    assert len(rig.db.collection('night_plan_decisions').records) == 2
    assert result['generated_plan']['detail_sha256'] != result['monitor_plan']['detail_sha256']
    assert result['delays'][0]['probe_sleep_seconds'] <= 30


def test_full_soc_proves_immediate_path_without_claiming_rising(rig):
    rig.readings([100, 100])
    result = probe.run_controller_probe(rig.path)
    assert result['scenario'] == 'already_at_target'
    assert result['monitor_initial_below_target'] is False
    assert rig.writes == ['3', '5', '1'] and rig.state == rig.initial


@pytest.mark.parametrize('timeout', [False, True])
def test_explicit_charging_window_fixture_is_restored(rig, monkeypatch, timeout, capsys):
    from datetime import datetime
    class Daytime(datetime):
        @classmethod
        def now(cls, timezone=None):
            return cls(2026, 10, 3, 21, 10, tzinfo=timezone)
    monkeypatch.setattr(probe, 'datetime', Daytime)
    rig.readings([40, 40, 40 if timeout else 41])
    if timeout:
        def expired(self, seconds):
            raise TimeoutError('test budget expired')
        monkeypatch.setattr(probe.ProbeClock, 'sleep', expired)
        with pytest.raises(TimeoutError):
            probe.run_controller_probe(rig.path, charge_window_fixture=True)
        result = json.loads(capsys.readouterr().out.splitlines()[-1])
    else:
        result = probe.run_controller_probe(rig.path, charge_window_fixture=True)
    fixture = result['charging_window_fixture']
    assert fixture['observed']['chargeStartTimeH'] == '21'
    assert fixture['observed']['chargeEndTimeH'] == '22'
    assert fixture['observed']['batteryOperatingMode'] == '5'
    assert fixture['readback_verified'] is True
    assert [row['profile'] for row in result['writes']] == (['forced'] if timeout else ['forced', 'standby'])
    assert rig.writes == (['5', '3', '1'] if timeout else ['5', '3', '5', '1'])
    assert rig.state == rig.initial and result['restore_verified'] is True


def test_monitor_timeout_restores_settings_and_fails(rig, monkeypatch, capsys):
    rig.readings([40, 40, 40])
    def timeout(self, seconds):
        raise TimeoutError('bounded live monitor')
    monkeypatch.setattr(probe.ProbeClock, 'sleep', timeout)
    with pytest.raises(TimeoutError):
        probe.run_controller_probe(rig.path)
    assert rig.writes == ['3', '1'] and rig.state == rig.initial
    proof = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert proof['status'] == 'failed' and proof['restore_verified'] is True


def test_corrupt_archive_prevents_any_device_write(rig, monkeypatch):
    monkeypatch.setattr(probe, 'embed_plan_detail', lambda *args, **kwargs: {'plan_json': '{}'})
    with pytest.raises(RuntimeError, match='raw-byte mismatch'):
        probe.run_controller_probe(rig.path)
    assert rig.writes == []


def test_unknown_write_suppresses_standby_and_restore(rig, monkeypatch, capsys):
    rig.readings([40, 40])
    def unknown(self, html):
        rig.writes.append('unknown')
        raise probe.KpNetUnknownWriteError('ambiguous write')
    monkeypatch.setattr(rig.client, 'write_setting', unknown)
    with pytest.raises(probe.KpNetUnknownWriteError):
        probe.run_controller_probe(rig.path)
    assert rig.writes == ['unknown']
    proof = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert proof['restore_after_failure'] == 'suppressed_unknown_write'
    assert proof['status'] == 'failed'


def test_probe_firestore_routes_every_collection_under_run():
    paths = []
    class Ref:
        def collection(self, name):
            paths.append(name)
            return self
        def document(self, name):
            paths.append(name)
            return self
    db = probe.ProbeFirestore(Ref(), 'test-run')
    db.collection('night_plan_decisions')
    db.collection('night_charge_plans')
    assert paths == ['control_live_probes', 'test-run', 'night_plan_decisions', 'night_charge_plans']
    with pytest.raises(ValueError):
        db.collection('model_parameters')
