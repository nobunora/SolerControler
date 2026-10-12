"""Explicit post-deployment SOC-setting proof, separate from scheduled modes."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
import time
from typing import Any

from app.kpnet.client import KpNetClient, KpNetUnknownWriteError
from app.kpnet.config import KpNetConfig
from app.kpnet.settings_roundtrip import (
    ROUNDTRIP_SETTING_FIELDS, SettingsRoundtripError, _apply_and_verify,
    _assert_preserved_fields, _read_only_snapshot_after_unknown,
    _sparse_candidate_maps, profile_from_current_settings,
)
from app.runtime.night_soc_controller import compare_setting_readback


def run_soc_setting_probe(*, target_soc_percent: float = 30.0, hold_seconds: int = 60) -> dict[str, object]:
    if isinstance(target_soc_percent, bool) or not math.isfinite(target_soc_percent) or target_soc_percent not in (30, 50):
        raise ValueError('SOC probe requires a supported 30 or 50 percent candidate')
    if isinstance(hold_seconds, bool) or hold_seconds != 60:
        raise ValueError('SOC probe hold must be exactly 60 seconds')
    cfg = KpNetConfig.from_env()
    if cfg.dry_run:
        raise RuntimeError('explicit SOC setting proof requires DRY_RUN=false')
    client = KpNetClient(cfg)
    summary: dict[str, object] = {'message': 'post-deploy-soc-setting-probe', 'status': 'failed',
                                  'hold_seconds': 60, 'requested_target_soc_percent': target_soc_percent,
                                  'restore_verified': False}
    initial: dict[str, Any] | None = None
    maps: dict[str, dict[str, str]] | None = None
    unknown = False
    phase = 'initial_read'
    try:
        client.login()
        client.open_settings_page()
        initial = client.read_current_settings()
        restore = profile_from_current_settings(initial)
        snapshot = {field: str(initial[field]) for field in ROUNDTRIP_SETTING_FIELDS}
        summary['initial_soc_code'] = snapshot['socChargeMode']
        summary['snapshot_hash'] = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        phase = 'candidate_read'
        values = client.candidate_map('SocChargeMode', 'remotesetting/pcssetting/valueList/socchargemode')
        selected = str(int(target_soc_percent))
        if selected == str(initial['socChargeMode']):
            selected = '50' if selected == '30' else '30'
        if selected not in values:
            raise RuntimeError('different supported SOC-setting candidate unavailable')
        maps = _sparse_candidate_maps(battery_operating_mode={})
        maps['SocChargeMode'] = values
        profile = replace(restore, name='post-deploy-soc-setting-proof', soc_charge_mode=selected)
        summary['applied_target_soc_percent'] = int(selected)
        phase = 'soc_write'
        observed, changed, _ = _apply_and_verify(client=client, current=initial, value_maps=maps,
                                                profile=profile, required_readback_fields=('socChargeMode',))
        if changed != ['socChargeMode']:
            raise RuntimeError('SOC probe changed fields outside its one-field scope')
        _assert_preserved_fields(baseline=initial, observed=observed, allowed_changes={'socChargeMode'}, phase=phase)
        summary.update(changed_fields=changed, requested_soc_code=selected, observed_soc_code=str(observed['socChargeMode']))
        phase = 'hold'
        started = time.monotonic()
        time.sleep(60)
        elapsed = time.monotonic() - started
        if elapsed < 60:
            time.sleep(60 - elapsed)
        summary['hold_elapsed_seconds'] = time.monotonic() - started
        observed = client.read_current_settings()
        _assert_preserved_fields(baseline=initial, observed=observed, allowed_changes={'socChargeMode'}, phase=phase)
        if str(observed.get('socChargeMode')) != selected:
            raise RuntimeError('SOC setting changed during the hold interval')
        phase = 'restore_write'
        restored, _, _ = _apply_and_verify(client=client, current=observed, value_maps=maps,
                                          profile=restore, require_change=False)
        matches, _ = compare_setting_readback(initial, restored, ROUNDTRIP_SETTING_FIELDS)
        if not matches:
            raise RuntimeError('SOC probe restoration read-back mismatch')
        summary.update(status='passed', restore_verified=True, restored_soc_code=str(restored['socChargeMode']))
        return summary
    except KpNetUnknownWriteError as exc:
        unknown = True
        summary.update(error_type=type(exc).__name__, failed_phase=phase, mutation_outcome='unknown')
        raise SettingsRoundtripError('SOC probe write outcome is unknown', summary=summary) from exc
    except Exception as exc:
        summary.update(error_type=type(exc).__name__, failed_phase=phase)
        raise SettingsRoundtripError('SOC setting proof failed', summary=summary) from exc
    finally:
        if initial is not None and not summary['restore_verified']:
            if unknown:
                _read_only_snapshot_after_unknown(client=client, initial=initial, summary=summary)
            elif maps is not None and phase not in ('initial_read', 'candidate_read'):
                try:
                    latest = client.read_current_settings()
                    _assert_preserved_fields(baseline=initial, observed=latest, allowed_changes={'socChargeMode'}, phase='cleanup')
                    restored, _, _ = _apply_and_verify(client=client, current=latest, value_maps=maps,
                        profile=profile_from_current_settings(initial), require_change=False)
                    matches, _ = compare_setting_readback(initial, restored, ROUNDTRIP_SETTING_FIELDS)
                    summary['restore_verified'] = matches
                    summary['restore_after_failure'] = 'passed' if matches else 'failed'
                except KpNetUnknownWriteError:
                    summary['restore_after_failure'] = 'unknown_write'
                    _read_only_snapshot_after_unknown(client=client, initial=initial, summary=summary)
                except Exception as cleanup_error:
                    summary['restore_after_failure'] = 'failed'
                    summary['restore_error_type'] = type(cleanup_error).__name__
        try:
            client.close()
        except Exception as close_error:
            summary['close_error_type'] = type(close_error).__name__
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True), flush=True)
