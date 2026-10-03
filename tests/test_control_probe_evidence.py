from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_control_release_scripts_parse_before_cloud_actions() -> None:
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command",
         "$tokens=$null; $errors=$null; "
         "foreach ($file in @('deploy_control_jobs_image_only.ps1', "
         "'run_control_postdeploy_live_probe.ps1', 'assert_control_probe_evidence.ps1', "
         "'run_extended_control_probe_from_env.ps1')) { "
         "[void][System.Management.Automation.Language.Parser]::ParseFile("
         "(Join-Path $PWD scripts $file), [ref]$tokens, [ref]$errors); "
         "if ($errors.Count) { throw $errors[0] } }"],
        text=True, capture_output=True, cwd=ROOT, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _proof() -> dict:
    evidence = {"status": "passed", "restore_verified": True, "hold_seconds": 60}
    for phase, values, candidates in (
        ("forced", {"batteryOperatingMode": "3"}, ["BatteryOperatingMode"]),
        ("green", {"batteryOperatingMode": "1"}, ["BatteryOperatingMode"]),
    ):
        evidence.update({
            f"{phase}_proof": "passed",
            f"{phase}_operation_id": "test-operation",
            f"{phase}_candidate_maps_fetched": candidates,
            f"{phase}_changed_fields": ["batteryOperatingMode"],
            f"{phase}_readback_fields": list(values),
            f"{phase}_requested": values.copy(),
            f"{phase}_observed": values.copy(),
        })
    return {"status": "passed", "roundtrip_forced_proof": "passed",
            "roundtrip_green_proof": "passed", "roundtrip_restore_verified": True,
            "settings_roundtrip_evidence": evidence}


@pytest.mark.parametrize("fault", [None, "missing", "unknown", "restore", "mismatch", "candidate", "missing_zero"])
def test_device_evidence_gate(fault: str | None) -> None:
    proof = _proof()
    evidence = proof["settings_roundtrip_evidence"]
    if fault == "missing":
        proof = None
    elif fault == "unknown":
        evidence["mutation_outcome"] = "unknown"
    elif fault == "restore":
        evidence["restore_verified"] = False
    elif fault == "mismatch":
        evidence["green_observed"]["batteryOperatingMode"] = "5"
    elif fault == "candidate":
        evidence["forced_candidate_maps_fetched"].append("SocChargeMode")
    elif fault == "missing_zero":
        del evidence["green_observed"]["batteryOperatingMode"]
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command",
         "$proof = [Console]::In.ReadToEnd() | ConvertFrom-Json -AsHashtable; "
         "& ./scripts/assert_control_probe_evidence.ps1 -Proof $proof"],
        input=json.dumps(proof), text=True, capture_output=True, cwd=ROOT, check=False,
    )
    assert (result.returncode == 0) == (fault is None), result.stdout + result.stderr


@pytest.mark.parametrize('fault', [None, 'missing', 'restore', 'archive', 'soc', 'standby', 'unrelated'])
def test_extended_controller_evidence_gate(fault):
    proof = _proof()
    controller = {
        'status': 'passed', 'target_reached': True, 'target_soc': 41,
        'restore_verified': True, 'restored_field_count': 14, 'storage_scope': 'control_live_probes',
        'generated_plan': {'status': 'verified', 'detail_sha256': 'a' * 64, 'raw_gzip_base64': 'encoded'},
        'monitor_plan': {'status': 'verified', 'detail_sha256': 'b' * 64, 'raw_gzip_base64': 'encoded'},
        'writes': [{'requested': mode, 'observed': mode, 'changed_fields': ['batteryOperatingMode']} for mode in ['3', '5']],
        'readings': [{'soc': 40, 'source': 'realtime'}, {'soc': 41, 'source': 'realtime'}],
    }
    proof['controller_probe'] = controller
    if fault == 'missing':
        del proof['controller_probe']
    elif fault == 'restore':
        controller['restore_verified'] = False
    elif fault == 'archive':
        controller['monitor_plan']['detail_sha256'] = None
    elif fault == 'soc':
        controller['readings'][-1]['source'] = 'csv'
    elif fault == 'standby':
        controller['writes'][-1]['observed'] = '3'
    elif fault == 'unrelated':
        controller['writes'][-1]['changed_fields'].append('socChargeMode')
    result = subprocess.run(
        ['pwsh', '-NoProfile', '-Command',
         '$proof = [Console]::In.ReadToEnd() | ConvertFrom-Json -AsHashtable; '
         '& ./scripts/assert_control_probe_evidence.ps1 -Proof $proof -RequireController'],
        input=json.dumps(proof), text=True, capture_output=True, cwd=ROOT, check=False,
    )
    assert (result.returncode == 0) == (fault is None), result.stdout + result.stderr
