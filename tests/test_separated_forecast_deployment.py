from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is unavailable")

ENV_STUB = r"""
function Import-ProductionEnv {}
function Get-RequiredProductionEnv($Name) {
    switch ($Name) {
        GCP_REGION { 'test-region' }
        GCP_SCHEDULER_REGION { 'test-region' }
        GCP_RUNNER_REPOSITORY { 'runner' }
        GCP_RUNNER_IMAGE_NAME { 'solar' }
        GCP_DASHBOARD_SERVICE { 'web' }
        FIRESTORE_DATABASE_ID { '(default)' }
        NIGHT_PLAN_ARCHIVE_GCS_PREFIX { 'gs://test-bucket/archive' }
        default { 'test-project' }
    }
}
"""
CLOUD_STUB = r"""
Add-Content -LiteralPath $env:TEST_CALLS -Value (ConvertTo-Json -InputObject @($args) -Compress)
$global:LASTEXITCODE = 0
if ($args[0] -eq 'builds') {
    if ($env:TEST_FAILURE -eq 'build') { $global:LASTEXITCODE = 1 }
    return
}
if ($args[0] -eq 'artifacts') {
    if ($env:TEST_FAILURE -eq 'digest') { 'invalid' } else { 'sha256:' + ('b' * 64) }
    return
}
if ($args[0] -eq 'scheduler') {
    @(@{ name='test-scheduler'; schedule='0 3 * * *'; timeZone='Asia/Tokyo' }) | ConvertTo-Json -AsArray
    return
}
if ($args[1] -eq 'services') { @{ spec=@{ unchanged=$true } } | ConvertTo-Json -Depth 5; return }
if ($args[2] -eq 'executions') {
    if ($env:TEST_FAILURE -eq 'active') { '[{"status":{}}]' } else { '[]' }
    return
}
if ($args[2] -eq 'update') {
    if ($env:TEST_FAILURE -eq 'update') { $global:LASTEXITCODE = 1; return }
    Set-Content -LiteralPath $env:TEST_UPDATED -Value 'updated'
    return
}
$changed = Test-Path -LiteralPath $env:TEST_UPDATED
$image = 'old-image@sha256:' + ('c' * 64)
$revision = 'old-revision'
$digest = 'sha256:' + ('c' * 64)
if ($args[3] -eq 'solar-forecast-daily' -and $changed) {
    $image = 'test-region-docker.pkg.dev/test-project/runner/solar-planner@sha256:' + ('b' * 64)
    $revision = 'a' * 40
    $digest = 'sha256:' + ('b' * 64)
}
$entries = @(@{ name='PLAN_SOURCE_REVISION'; value=$revision }, @{ name='PLAN_IMAGE_DIGEST'; value=$digest }, @{ name='KEEP'; value='yes' })
if ($env:TEST_FAILURE -eq 'unexpected' -and $changed) { $entries[2].value = 'no' }
$ready = if ($env:TEST_FAILURE -eq 'ready' -and $changed) { 'False' } else { 'True' }
@{ metadata=@{generation=1}; status=@{conditions=@(@{type='Ready';status=$ready})}; spec=@{ template=@{ spec=@{ template=@{ spec=@{ containers=@(@{
    image=$image; command=@('python'); args=@('planner_job_main.py'); env=$entries
}) } } } } } } | ConvertTo-Json -Depth 15
"""
PROOF_STUB = r"""
param($ImmutablePlannerImage,$FixturePath,$ReceiptPath)
if ($env:TEST_FAILURE -eq 'proof') { throw 'test SDK proof failure' }
$fixture = Get-Content -LiteralPath $FixturePath -Raw | ConvertFrom-Json
@{ status='PASS'; image_digest=($ImmutablePlannerImage -split '@')[-1]; fixture_sha256=$fixture.fixture_sha256 } |
    ConvertTo-Json | Set-Content -LiteralPath $ReceiptPath
$global:LASTEXITCODE = 0
"""


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/deploy_separated_forecast_from_env.ps1", scripts)
    (scripts / "production_env.ps1").write_text(ENV_STUB, encoding="utf-8")
    (scripts / "gcloud.ps1").write_text(CLOUD_STUB, encoding="utf-8")
    (scripts / "run_server_adapter_proof_from_env.ps1").write_text(PROOF_STUB, encoding="utf-8")
    state_root = tmp_path / "artifacts/deployment_state"
    state_root.mkdir(parents=True)
    preflight = {"repository_commit": "a" * 40, "status": "passed",
                 "checks": [{"name": name, "status": "success"} for name in
                            ["working_tree_clean", "git_diff_check", "env_is_ignored_and_unstaged", "local_backup",
                             "security_check", "production_validate_only", "pre_release_integration"]]}
    (state_root / "preflight-test.json").write_text(json.dumps(preflight), encoding="utf-8")
    fixture = {"source_commit": "a" * 40, "backend": "firestore", "project_id": "test-project",
               "database_id": "(default)", "bucket": "test-bucket"}
    fixture["fixture_sha256"] = hashlib.sha256(json.dumps(fixture, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    runner = tmp_path / "run.ps1"
    runner.write_text(r"""
$ErrorActionPreference = 'Stop'
function global:git {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'rev-parse') { 'a' * 40 }
}
& (Join-Path $PSScriptRoot 'scripts/deploy_separated_forecast_from_env.ps1') -StatePath (Join-Path $PSScriptRoot 'artifacts/deployment_state/release.json') -AdapterProofFixturePath (Join-Path $PSScriptRoot 'fixture.json') -Resume:([bool]::Parse($env:TEST_RESUME))
""", encoding="utf-8")
    return runner, state_root / "release.json"


def _run(tmp_path: Path, runner: Path, failure: str = "", resume: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PWSH or "pwsh", "-NoProfile", "-File", str(runner)],
        capture_output=True, text=True, encoding="utf-8", check=False, timeout=30,
        env={**os.environ, "TEST_FAILURE": failure, "TEST_RESUME": str(resume),
             "TEST_CALLS": str(tmp_path / "calls.jsonl"), "TEST_UPDATED": str(tmp_path / "updated.txt")})


@pytest.mark.parametrize("failure", ["", "build", "digest", "proof", "active", "update", "unexpected", "ready"])
def test_forecast_release_is_pinned_and_never_updates_control(tmp_path: Path, failure: str) -> None:
    runner, state_path = _setup(tmp_path)
    result = _run(tmp_path, runner, failure)
    state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8-sig").splitlines()]
    assert state["stages"]["settings_roundtrip"]["status"] == "skipped_not_applicable"
    updates = [call for call in calls if call[:3] == ["run", "jobs", "update"]]
    assert all(call[3] == "solar-forecast-daily" for call in updates)
    assert not any(call[:3] == ["run", "jobs", "execute"] for call in calls)
    assert len([call for call in calls if call[0] == "builds"]) == 1
    assert "cloudbuild.planner.yaml" in next(call for call in calls if call[0] == "builds")
    assert len(updates) == (0 if failure in {"build", "digest", "proof", "active"} else 1)
    if failure:
        assert result.returncode != 0
        assert state["status"] == "failed"
    else:
        assert result.returncode == 0, result.stderr
        assert state["status"] == "complete"
        update = updates[0]
        assert update[update.index("--image") + 1].endswith("@sha256:" + "b" * 64)
        assert "--command" not in update and "--args" not in update


@pytest.mark.parametrize(("failure", "stage"), [("proof", "adapter_proof"), ("digest", "image_pin")])
def test_post_build_failure_resume_reuses_successful_image_build(tmp_path: Path, failure: str, stage: str) -> None:
    runner, state_path = _setup(tmp_path)
    assert _run(tmp_path, runner, failure).returncode != 0
    failed = json.loads(state_path.read_text(encoding="utf-8-sig"))
    assert failed["stages"]["build"]["status"] == "success"
    assert failed["stages"][stage]["status"] == "failed"
    resumed = _run(tmp_path, runner, resume=True)
    assert resumed.returncode == 0, resumed.stderr
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8-sig").splitlines()]
    assert len([call for call in calls if call[0] == "builds"]) == 1


def test_skipped_mandatory_preflight_blocks_all_cloud_calls(tmp_path: Path) -> None:
    runner, _ = _setup(tmp_path)
    path = tmp_path / "artifacts/deployment_state/preflight-test.json"
    preflight = json.loads(path.read_text(encoding="utf-8"))
    preflight["checks"][0]["status"] = "skipped_manual"
    path.write_text(json.dumps(preflight), encoding="utf-8")
    result = _run(tmp_path, runner)
    assert result.returncode != 0
    assert not (tmp_path / "calls.jsonl").exists()
