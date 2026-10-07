from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_durable_stage_only_skips_success_and_records_failure(tmp_path):
    # Execute the actual stage functions, not the wrapper's top-level cloud calls.
    harness = r'''
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Parse failed' }
foreach ($name in @('Save-State', 'Invoke-Stage')) {
    $function = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    Invoke-Expression $function.Extent.Text
}
$stateRoot = $args[1]; $StatePath = Join-Path $stateRoot 'state.json'
$state = [ordered]@{ status = 'running'; stages = [ordered]@{ ready = @{status='success'}; interrupted = @{status='running'}; failed = @{status='failed'} } }
$global:LASTEXITCODE = 0; $script:executions = 0
Invoke-Stage 'ready' { throw 'Success stage must not repeat' }
Invoke-Stage 'interrupted' { $script:executions++ }
if ($script:executions -ne 1 -or $state.stages.interrupted.status -ne 'success') { throw 'Running checkpoint skipped' }
try { Invoke-Stage 'failed' { throw 'Expected local failure' } } catch { }
$saved = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json -AsHashtable
if ($saved.status -ne 'failed' -or $saved.stages.failed.status -ne 'failed') { throw 'Failure was not durable' }
if (Test-Path -LiteralPath "$StatePath.part") { throw 'Atomic state write was not completed' }
'''
    path = tmp_path / "stage-test.ps1"
    path.write_text(harness, encoding="utf-8")
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(path),
                             str(ROOT / "scripts/deploy_separated_runtime.ps1"), str(tmp_path)],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr


def test_role_context_excludes_recovery_and_secret_files():
    ignored = (ROOT / ".gcloudignore-separated").read_text().splitlines()
    for required in (".env", ".env.*", "artifacts/", ".git", "tests/", "docs/", "__pycache__/"):
        assert required in ignored
    planner = (ROOT / "Dockerfile.planner").read_text()
    assert "drive.v3.json" in planner and "sheets.v4.json" in planner
    assert "RUN pip install" in planner and "&& python" in planner


def test_separated_release_retains_live_probe_gate_before_control_changes():
    wrapper = (ROOT / "scripts/deploy_separated_runtime.ps1").read_text()
    assert wrapper.index("Invoke-Stage 'calculation'") < wrapper.index("Invoke-Stage 'control'")
    assert "-ExpectedCommit $commit -SkipBuild -SeparatedRuntime" in wrapper
    assert "if ($state.stages.control.status -eq 'running')" in wrapper
    assert "run_cloud_job_from_env.ps1') -Slot 07 -DryRun" in wrapper
    control = (ROOT / "scripts/deploy_control_jobs_image_only.ps1").read_text()
    assert control.index("run_control_postdeploy_live_probe.ps1") < control.index("run jobs update $jobName")
    assert "@roleProbeArgs" in control and "@restoreEntryArgs" in control


def test_separated_backup_reuses_the_deployed_immutable_planner():
    backup = (ROOT / "scripts/run_drive_backup_cloud_from_env.ps1").read_text()
    assert "run jobs describe $ScheduledJobName" in backup
    assert "Scheduled backup does not reference a verified immutable planner image" in backup
    assert backup.index("$expectedPackage") < backup.index("'run', 'jobs', 'deploy'")
    release = (ROOT / "scripts/deploy_production_from_env.ps1").read_text()
    assert "if ($DeploymentScope -notin @('auto', 'full'))" in release
