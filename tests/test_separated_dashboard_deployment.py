from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deploy_production_from_env.ps1"
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is unavailable")


def _run(tmp_path: Path, code: str, **environment: str) -> subprocess.CompletedProcess[str]:
    runner = tmp_path / "probe.ps1"
    runner.write_text("$ErrorActionPreference='Stop'\n" + code, encoding="utf-8")
    return subprocess.run(
        [PWSH or "pwsh", "-NoProfile", "-File", str(runner)],
        capture_output=True, text=True, encoding="utf-8", check=False,
        env={**os.environ, "TEST_DEPLOY_SCRIPT": str(SCRIPT), **environment},
        timeout=30,
    )


@pytest.mark.parametrize(("layout", "explicit", "scope", "expected"), [
    ("separated", False, "dashboard", "dashboard"),
    ("legacy", True, "dashboard", "dashboard"),
    ("legacy", False, "dashboard", "dashboard"),
    ("separated", False, "auto", "full"),
    ("separated", False, "full", "full"),
    ("separated", False, "forecast", "reject"),
    ("separated", False, "runner", "reject"),
    ("separated", False, "control-readonly", "reject"),
])
def test_separated_scope_never_expands_dashboard_into_control(
    tmp_path: Path, layout: str, explicit: bool, scope: str, expected: str,
) -> None:
    (tmp_path / "deploy_separated_runtime.ps1").write_text("Write-Output 'full'", encoding="utf-8")
    completed = _run(tmp_path, r"""
function Get-ProductionEnv { param($Name, $Default) return $env:TEST_LAYOUT }
$SeparatedRuntime = [bool]::Parse($env:TEST_EXPLICIT)
$DeploymentScope = $env:TEST_SCOPE
$StatePath = 'test-state.json'
$Resume = $false
$ApprovedCalculationPatchSha256 = ''
$AllowOutOfWindowLiveProbe = $false
$MonitoringRepairStatePath = ''
$ReviewedRepairStateSha256 = ''
$AdapterProofFixturePath = ''
$source = Get-Content -Raw -LiteralPath $env:TEST_DEPLOY_SCRIPT
$start = $source.IndexOf('$useSeparatedRuntime =')
$end = $source.IndexOf('$artifactsRoot =', $start)
$route = $source.Substring($start, $end - $start)
# Execute the actual routing in this file's scope, preserving its early return.
$routePath = Join-Path $PSScriptRoot 'route.ps1'
Set-Content -LiteralPath $routePath -Value ($route + "`nWrite-Output 'dashboard'") -Encoding utf8
& $routePath
""", TEST_LAYOUT=layout, TEST_EXPLICIT=str(explicit), TEST_SCOPE=scope)
    if expected == "reject":
        assert completed.returncode != 0
        assert "requires auto/full scope" in completed.stderr
    else:
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == expected


FAKE_CLOUD = r"""
Add-Content -LiteralPath $env:TEST_CALLS -Value (ConvertTo-Json -InputObject @($args) -Compress)
$global:LASTEXITCODE = 0
if ($args[0] -eq 'builds') {
    if ($env:TEST_FAILURE -eq 'build') { $global:LASTEXITCODE = 1 }
} elseif ($args[0] -eq 'artifacts') {
    if ($env:TEST_FAILURE -eq 'digest') { 'invalid' } else { 'sha256:' + ('b' * 64) }
} elseif ($args[2] -eq 'update') {
    if ($env:TEST_FAILURE -eq 'update') { $global:LASTEXITCODE = 1 }
} elseif ($args[-1] -ne 'json') {
    'test-region-docker.pkg.dev/test-project/web/web@sha256:' + ('c' * 64)
} else {
    $ready = if ($env:TEST_FAILURE -eq 'ready') { 'False' } else { 'True' }
    $percent = if ($env:TEST_FAILURE -eq 'traffic') { 0 } else { 100 }
    $image = 'test-region-docker.pkg.dev/test-project/web/web@sha256:' + ('b' * 64)
    if ($env:TEST_FAILURE -eq 'image') { $image = 'wrong-image' }
    $revision = if ($env:TEST_FAILURE -eq 'revision') { 'old-revision' } else { 'new-revision' }
    @{ spec = @{ template = @{ spec = @{ containers = @(@{image=$image}) } } };
       status = @{latestCreatedRevisionName='new-revision';latestReadyRevisionName=$revision;
       traffic=@(@{revisionName='new-revision';percent=$percent});conditions=@(@{type='Ready';status=$ready})} } | ConvertTo-Json -Depth 8
}
"""


@pytest.mark.parametrize("failure", ["", "build", "digest", "update", "ready", "traffic", "image", "revision"])
def test_separated_web_action_is_image_only_and_fails_closed(tmp_path: Path, failure: str) -> None:
    (tmp_path / "fake_cloud.ps1").write_text(FAKE_CLOUD, encoding="utf-8")
    calls_path = tmp_path / "calls.jsonl"
    completed = _run(tmp_path, r"""
$useSeparatedRuntime = $true
$region = 'test-region'
$projectId = 'test-project'
$dashboardRepository = 'web'
$dashboardImageName = 'web'
$dashboardService = 'test-dashboard'
$state = @{ repository_commit = ('a' * 40) }
$gcloud = Join-Path $PSScriptRoot 'fake_cloud.ps1'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:TEST_DEPLOY_SCRIPT, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Deployment syntax errors' }
$command = $ast.FindAll({ param($node)
    $node -is [System.Management.Automation.Language.CommandAst] -and
    $node.GetCommandName() -eq 'Invoke-DeploymentStage' -and
    @($node.CommandElements | Where-Object { $_.Extent.Text -eq "'dashboard'" }).Count
}, $true)[0]
$action = @($command.CommandElements | Where-Object { $_ -is [System.Management.Automation.Language.ScriptBlockExpressionAst] })[0]
& $action.ScriptBlock.GetScriptBlock()
""", TEST_CALLS=str(calls_path), TEST_FAILURE=failure)
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8-sig").splitlines()]
    if failure:
        assert completed.returncode != 0
    else:
        assert completed.returncode == 0, completed.stderr
        assert [call[:3] for call in calls] == [
            ["run", "services", "describe"], ["builds", "submit", "--config"],
            ["artifacts", "docker", "images"], ["run", "services", "update"],
            ["run", "services", "describe"],
        ]
    builds = [call for call in calls if call[0] == "builds"]
    assert len(builds) == 1
    assert "cloudbuild.web.yaml" in builds[0] and ".gcloudignore-separated" in builds[0]
    assert any(":git-" + "a" * 40 in arg for arg in builds[0])
    updates = [call for call in calls if call[:3] == ["run", "services", "update"]]
    assert len(updates) == (0 if failure in {"build", "digest"} else 1)
    for update in updates:
        assert update[update.index("--image") + 1].endswith("@sha256:" + "b" * 64)
        assert not any("env" in arg or "secret" in arg or "account" in arg for arg in update)
    assert all(call[0] in {"run", "builds", "artifacts"} for call in calls)
    assert all(call[1] == "services" for call in calls if call[0] == "run")


def test_separated_web_build_has_no_control_or_planner_images() -> None:
    config = (ROOT / "cloudbuild.web.yaml").read_text(encoding="utf-8")
    assert "Dockerfile.web" in config and "${_WEB_CACHE_IMAGE}" in config
    assert "images: ['${_WEB_IMAGE}']" in config
    assert "Dockerfile.planner" not in config and "Dockerfile.control" not in config
