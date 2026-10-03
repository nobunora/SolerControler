"""Explicit daytime integration probe using the unchanged 03 controller.

The logical clock is injected only here. Device operations use real deadlines,
real SOC and real read-back; normal 03 ownership fences are never patched.
"""
from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import time
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from google.cloud import storage

from app.backup.night_plan_archive import night_plan_archive_prefix
from app.backup.plan_snapshot import archive_plan_snapshot, embed_plan_detail
from app.kpnet.client import KpNetClient, KpNetUnknownWriteError
from app.kpnet.config import KpNetConfig
from app.kpnet.profile_builder import _pick_battery_operating_mode_code
from app.kpnet.settings_roundtrip import (
    ROUNDTRIP_SETTING_FIELDS, _apply_and_verify, _assert_preserved_fields,
    _forced_probe_candidate_maps, _read_only_snapshot_after_unknown,
    profile_from_current_settings,
)
from app.operations.firestore import open_firestore
from app.runtime import cloud_job
from app.runtime.command_adapter import _env_float, _env_int
from app.runtime.night_soc_controller import compare_setting_readback
from app.runtime.soc_reading import SocReading, latest_realtime_soc_percent, read_soc_with_fallback


class ProbeFirestore:
    """Use actual Firestore transactions, but never production plan collections."""

    def __init__(self, client: Any, run_id: str) -> None:
        self.client = client
        self.root = client.collection('control_live_probes').document(run_id)

    def collection(self, name: str) -> Any:
        if name not in {'night_plan_decisions', 'night_charge_plans'}:
            raise ValueError('unexpected probe collection')
        return self.root.collection(name)

    def transaction(self, **kwargs: Any) -> Any:
        return self.client.transaction(**kwargs)


class ProbeClock:
    def __init__(self, budget_seconds: int = 300) -> None:
        self.started = time.monotonic()
        self.deadline = self.started + budget_seconds
        self.logical_start = datetime.now(ZoneInfo('Asia/Tokyo')).replace(hour=3, minute=0, second=0)
        self.delays: list[dict[str, float]] = []

    def check(self) -> None:
        actual = datetime.now(ZoneInfo('Asia/Tokyo'))
        minutes = actual.hour * 60 + actual.minute
        if not 435 <= minutes < 1350:
            raise RuntimeError('live controller probe requires 07:15-22:29 JST')
        if time.monotonic() >= self.deadline:
            raise TimeoutError('live controller probe monitoring budget exhausted')

    def now(self, timezone: ZoneInfo) -> datetime:
        return (self.logical_start + timedelta(seconds=time.monotonic() - self.started)).astimezone(timezone)

    def monotonic_seconds(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: int) -> None:
        self.check()
        # Faster sampling is a probe-only constraint, not evidence of normal ETA.
        delay = min(float(seconds), 30.0, max(0.0, self.deadline - time.monotonic()))
        self.delays.append({'controller_requested_seconds': float(seconds), 'probe_sleep_seconds': delay})
        time.sleep(delay)


class ProbeDevice:
    def __init__(self, client: KpNetClient, initial: dict[str, Any], clock: ProbeClock) -> None:
        self.client, self.initial, self.clock = client, initial, clock
        self.unknown_write = False
        self.readings: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []

    def prepare_charging_window(self) -> dict[str, Any]:
        """Explicit physical fixture: standby while enabling the current hour.

        This is never called by a scheduled owner or the 60-second roundtrip.
        The caller retains the original snapshot for final restoration.
        """
        self.clock.check()
        hour = datetime.now(ZoneInfo('Asia/Tokyo')).hour
        if hour >= 22:
            raise RuntimeError('charging-window fixture must finish before the 23 owner')
        fields = ('batteryOperatingMode', 'chargeStartTimeH', 'chargeStartTimeM', 'chargeEndTimeH', 'chargeEndTimeM')
        current = self.client.read_current_settings()
        maps = _forced_probe_candidate_maps(self.client)
        fixture = replace(profile_from_current_settings(current), name='live-probe-charge-window',
                          battery_operating_mode=_pick_battery_operating_mode_code(maps['BatteryOperatingMode'], prefer='standby'),
                          charge_start_h=str(hour), charge_start_m='0', charge_end_h=str(hour + 1), charge_end_m='0')
        try:
            observed, changed, requested = _apply_and_verify(
                client=self.client, current=current, value_maps=maps, profile=fixture,
                required_readback_fields=ROUNDTRIP_SETTING_FIELDS,
            )
        except KpNetUnknownWriteError:
            self.unknown_write = True
            raise
        _assert_preserved_fields(baseline=self.initial, observed=observed, allowed_changes=set(fields), phase='charging window fixture')
        self.initial = observed
        return {'changed_fields': changed, 'requested': {key: requested[key] for key in fields},
                'observed': {key: str(observed[key]) for key in fields}, 'readback_verified': True}

    def read_soc(self, csv_paths: list[Path]) -> SocReading:
        self.clock.check()
        deadline = min(self.clock.deadline, time.monotonic() + 60)
        reading = read_soc_with_fallback(
            [], latest_realtime=lambda: latest_realtime_soc_percent(deadline_monotonic=deadline),
            latest_csv=lambda _paths: (None, None), env_int=_env_int,
            env_float=_env_float, deadline_monotonic=deadline,
            allow_realtime=True, allow_csv_fallback=False,
        )
        self.readings.append({'soc': reading.value_percent, 'source': reading.source,
                              'observed_at': reading.observed_at.isoformat() if reading.observed_at else None})
        return reading

    def apply_profile(self, *, profile: str, dynamic_forced_profile: bool, label: str) -> None:
        if self.unknown_write:
            raise RuntimeError('probe suppresses subsequent mutation after UNKNOWN_WRITE')
        if profile not in {'forced', 'standby'} or dynamic_forced_profile != (profile == 'forced'):
            raise ValueError('invalid probe controller profile')
        self.clock.check()
        self.client.deadline_monotonic = self.clock.deadline
        current = self.client.read_current_settings()
        maps = _forced_probe_candidate_maps(self.client)
        target = replace(profile_from_current_settings(current), name=f'live-controller-{profile}',
                         battery_operating_mode=_pick_battery_operating_mode_code(maps['BatteryOperatingMode'], prefer=profile))
        try:
            observed, changed, requested = _apply_and_verify(
                client=self.client, current=current, value_maps=maps, profile=target,
                required_readback_fields=('batteryOperatingMode',),
            )
        except KpNetUnknownWriteError:
            self.unknown_write = True
            raise
        _assert_preserved_fields(baseline=self.initial, observed=observed,
                                 allowed_changes={'batteryOperatingMode'}, phase=label)
        self.writes.append({'profile': profile, 'requested': requested['batteryOperatingMode'],
                            'observed': str(observed['batteryOperatingMode']), 'changed_fields': changed})


def verify_archive(path: Path, *, db: Any, storage_client: Any, prefix: str) -> dict[str, Any]:
    result = archive_plan_snapshot(path, storage=storage_client, firestore=db,
                                   source='control-live-probe', prefix=prefix)
    doc = db.collection('night_plan_decisions').document(result['decision_id']).get(timeout=4, retry=None).to_dict()
    restored = embed_plan_detail(doc, storage=storage_client)['plan_json'].encode('utf-8')
    if restored != path.read_bytes():
        raise RuntimeError('probe plan/index/raw-byte mismatch')
    latest = db.collection('night_charge_plans').document('latest').get(timeout=4, retry=None).to_dict()
    if latest['detail_sha256'] != result['detail_sha256']:
        raise RuntimeError('probe latest index mismatch')
    # The wrapper materializes these exact read-back bytes locally, including on
    # a later device failure. No bucket/account identifiers enter this evidence.
    result['raw_gzip_base64'] = base64.b64encode(gzip.compress(restored, mtime=0)).decode('ascii')
    return result


def run_controller_probe(plan_path: Path, *, charge_window_fixture: bool = False) -> dict[str, Any]:
    cfg = KpNetConfig.from_env()
    if cfg.dry_run:
        raise RuntimeError('live controller probe requires DRY_RUN=false')
    clock = ProbeClock()
    clock.check()
    client = KpNetClient(cfg, deadline_monotonic=time.monotonic() + 180)
    initial: dict[str, Any] | None = None
    device: ProbeDevice | None = None
    summary: dict[str, Any] = {'message': 'controller-live-probe', 'status': 'failed',
                               'restore_verified': False, 'logical_clock': 'injected_03',
                               'storage_scope': 'control_live_probes', 'monitor_budget_seconds': 300}
    try:
        prefix = night_plan_archive_prefix()
        if not prefix:
            raise RuntimeError('probe archive prefix missing')
        run_id = uuid4().hex
        summary['probe_run_id'] = run_id
        db = ProbeFirestore(open_firestore(), run_id)
        storage_client = storage.Client()
        probe_prefix = f'{prefix.rstrip("/")}/live-probes/{run_id}'
        summary['generated_plan'] = verify_archive(plan_path, db=db, storage_client=storage_client, prefix=probe_prefix)
        client.login()
        client.open_settings_page()
        initial = client.read_current_settings()
        profile_from_current_settings(initial)  # Validate all restore fields before mutation.
        device = ProbeDevice(client, initial, clock)
        reading = device.read_soc([])
        if reading.value_percent is None:
            raise RuntimeError('live SOC unavailable before probe')
        initial_soc = reading.value_percent
        target = min(100.0, initial_soc + 1.0)
        summary.update({'initial_soc': initial_soc, 'target_soc': target,
                        'scenario': 'rising_soc' if initial_soc < target else 'already_at_target'})
        plan = json.loads(plan_path.read_bytes())
        plan['live_probe'] = {'generated_plan_sha256': summary['generated_plan']['detail_sha256'],
                              'original_target_soc': plan['result']['target_soc_7_percent'],
                              'target_override': target, 'purpose': 'bounded real SOC stop verification'}
        plan['result']['target_soc_7_percent'] = target
        plan['generated_at'] = datetime.now(ZoneInfo('UTC')).isoformat()
        probe_path = plan_path.with_name('live_probe_plan.json')
        probe_path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
        summary['monitor_plan'] = verify_archive(probe_path, db=db, storage_client=storage_client, prefix=probe_prefix)
        if charge_window_fixture:
            client.deadline_monotonic = time.monotonic() + 180
            summary['charging_window_fixture'] = device.prepare_charging_window()
        clock = ProbeClock()  # Budget starts after preparation, before any forced write.
        device.clock = clock
        summary['readings'] = device.readings
        summary['writes'] = device.writes
        summary['delays'] = clock.delays
        cloud_job._monitor_partial_forced_and_stop(probe_path, clock=clock, device_port=device)
        if hashlib.sha256(probe_path.read_bytes()).hexdigest() != summary['monitor_plan']['detail_sha256']:
            raise RuntimeError('monitor used a modified plan')
        if [item['profile'] for item in device.writes] != ['forced', 'standby']:
            raise RuntimeError('controller did not prove forced then standby')
        if not device.readings or device.readings[-1]['soc'] is None or device.readings[-1]['soc'] < target:
            raise RuntimeError('controller did not reach real target SOC')
        summary['target_reached'] = True
        summary['monitor_initial_below_target'] = device.readings[1]['soc'] is not None and device.readings[1]['soc'] < target
        summary['status'] = 'passed'
        return summary
    except Exception as exc:
        summary['error_type'] = type(exc).__name__
        raise
    finally:
        try:
            if initial is not None:
                client.deadline_monotonic = time.monotonic() + 180
                if device is not None and device.unknown_write:
                    summary['mutation_outcome'] = 'unknown'
                    _read_only_snapshot_after_unknown(client=client, initial=initial, summary=summary)
                else:
                    current = client.read_current_settings()
                    maps = _forced_probe_candidate_maps(client)
                    restored, _, _ = _apply_and_verify(client=client, current=current, value_maps=maps,
                                                       profile=profile_from_current_settings(initial), require_change=False)
                    matched, mismatches = compare_setting_readback(initial, restored, ROUNDTRIP_SETTING_FIELDS)
                    summary['restore_verified'] = matched
                    summary['restored_field_count'] = len(ROUNDTRIP_SETTING_FIELDS)
                    if not matched:
                        raise RuntimeError(f'probe restoration mismatch: {mismatches}')
        except Exception as exc:
            summary['status'] = 'failed'
            summary['restore_error_type'] = type(exc).__name__
            raise
        finally:
            try:
                client.close()
            finally:
                if not summary['restore_verified']:
                    summary['status'] = 'failed'
                print(json.dumps(summary, ensure_ascii=False), flush=True)
