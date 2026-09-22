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
         "'run_control_postdeploy_live_probe.ps1', 'assert_control_probe_evidence.ps1')) { "
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
