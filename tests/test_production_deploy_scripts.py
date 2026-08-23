from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ENV_KEYS = {
    "GCP_PROJECT_ID",
    "GCP_REGION",
    "GCP_SCHEDULER_REGION",
    "GCP_RUNNER_REPOSITORY",
    "GCP_RUNNER_IMAGE_NAME",
    "GCP_DASHBOARD_REPOSITORY",
    "GCP_DASHBOARD_IMAGE_NAME",
    "GCP_DASHBOARD_SERVICE",
    "GCP_RUN_SERVICE_ACCOUNT",
    "GCP_RUN_SERVICE_ACCOUNT_NAME",
    "DATA_BACKEND",
    "FIRESTORE_PROJECT_ID",
    "FIRESTORE_DATABASE_ID",
    "DRIVE_BACKUP_FOLDER_ID",
    "SHEETS_SPREADSHEET_ID",
    "SHEETS_SHARE_EMAIL",
    "NIGHT_PLAN_ARCHIVE_GCS_PREFIX",
    "KP_MONITOR_USERNAME_SECRET",
    "KP_MONITOR_PASSWORD_SECRET",
    "DASHBOARD_BASIC_USER",
    "DASHBOARD_BASIC_PASSWORD",
    "DASHBOARD_SESSION_SECRET",
}


def _env_example_keys() -> set[str]:
    keys: set[str] = set()
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            keys.add(raw.split("=", 1)[0].strip())
    return keys


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            key, value = raw.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def test_env_example_documents_every_required_production_setting() -> None:
    assert REQUIRED_ENV_KEYS <= _env_example_keys()


def test_agent_instructions_require_canonical_production_scripts() -> None:
    instructions = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "## Production Operations (Mandatory)" in instructions
    assert "deploy_production_from_env.ps1 -ValidateOnly" in instructions
    assert "run_kpnet_import_from_env.ps1" in instructions
    assert "run_drive_backup_cloud_from_env.ps1" in instructions
    assert "run_cloud_job_from_env.ps1" in instructions
    assert "run_kpnet_soc_gap_report.ps1 -SkipDownload" in instructions
    assert "python scripts/security_check.py" in instructions
    assert "docs/current/ops/PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md" in instructions
    assert "Before every production deployment" in instructions


def test_agent_instructions_keep_ruff_as_lint_only() -> None:
    instructions = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "Ruffはlint（`python -m ruff check .`）だけに使用する。" in instructions
    assert "formatterの実行、format checkの追加、Ruffによる一括整形は行わない。" in instructions


def test_production_deployment_runbook_documents_safe_resume_and_verification() -> None:
    runbook = (ROOT / "docs" / "current" / "ops" / "PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "-ValidateOnly",
        "-SkipPreRelease",
        "-SkipJobBuild",
        "-SkipJob23Deploy",
        "-SkipJob03Deploy",
        "-SkipJob07Deploy",
        "-SkipJobDeploy",
        "scripts/run_cloud_job_from_env.ps1 -Slot 07 -DryRun",
        "Completed=True",
    ):
        assert required in runbook


def test_manual_actual_import_cannot_overwrite_production_plan() -> None:
    script = (ROOT / "scripts" / "run_kpnet_import_from_env.ps1").read_text(
        encoding="utf-8"
    )

    assert "$env:DATA_PIPELINE_INCLUDE_NIGHT_PLAN = 'false'" in script


def test_plan_refresh_cloud_job_mode_is_limited_to_slot_03() -> None:
    script = (ROOT / "scripts" / "run_cloud_job_from_env.ps1").read_text(
        encoding="utf-8"
    )

    assert "[switch]$PlanRefreshOnly" in script
    assert "-PlanRefreshOnly requires -Slot 03" in script
    assert "--args=--plan-refresh-only" in script


def test_cloud_settings_roundtrip_job_is_explicit_and_can_run_at_any_time() -> None:
    runner = (ROOT / "scripts" / "run_cloud_job_from_env.ps1").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")
    runtime = (ROOT / "app" / "runtime" / "cloud_job.py").read_text(encoding="utf-8")

    assert "'settings-roundtrip'" in runner
    assert "Live settings round-trip requires -TestExecution." in runner
    assert "[ValidateRange(50, 50)]" in runner
    assert "[double]$SettingsRoundTripTargetSoc = 50" in runner
    assert "SETTINGS_ROUNDTRIP_TARGET_SOC=$SettingsRoundTripTargetSoc,DRY_RUN=false" in runner
    assert "solar-battery-settings-roundtrip" in deploy
    assert "--task-timeout 600 --max-retries 0" in deploy
    assert "CLOUD_JOB_SLOT=settings-roundtrip" in deploy
    assert "run_settings_roundtrip(target_soc_percent=target_soc)" in runtime


def test_runbook_fixes_single_build_cache_resume_and_roundtrip_rules() -> None:
    runbook = (ROOT / "docs" / "current" / "ops" / "PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "一つのcommitへ固定",
        "同一commitでrunnerを複数回ビルドしない",
        "-SkipInlineSmokeTest",
        "Cloud Runの再試行は0回",
        "同じ状態ファイルの`-Resume`",
    ):
        assert required in runbook


def test_dashboard_cloudbuild_requires_an_explicit_image_substitution() -> None:
    config = (ROOT / "cloudbuild.dashboard.yaml").read_text(encoding="utf-8")

    assert config.count("${_DASHBOARD_IMAGE}") == 4
    assert "codrivernavi-web" not in config


def test_deploy_script_rejects_implicit_empty_backup_destinations() -> None:
    script = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")

    assert "Drive backup is enabled, but DRIVE_BACKUP_FOLDER_ID is empty" in script
    assert "Sheets export is enabled, but SHEETS_SPREADSHEET_ID is empty" in script


def test_production_export_cost_configuration_matches_inactive_contract() -> None:
    script = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")

    assert '"SOC_EXPORT_CONTRACT_STATUS=inactive"' in script
    assert '"SOC_EXPORT_VALUE_MODE=neutral"' in script
    assert '"SOC_SELL_REVENUE_YEN_PER_KWH=0"' in script


def test_production_adjust03_starts_at_three_and_holds_standby_until_seven() -> None:
    script = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")

    assert '-SchedulerName "solar-battery-run-03" -Schedule "0 3 * * *"' in script
    assert "ADJUST03_FORCE_MONITOR_CUTOFF_HHMM=07:00" in script
    assert "ADJUST03_POST_CHARGE_HOLD_PROFILE=standby" in script


def test_job_deploy_uses_isolated_gcloud_python_and_absolute_build_source() -> None:
    script = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")

    assert 'platform\\bundledpython\\python.exe' in script
    assert 'lib\\gcloud.py' in script
    assert "[System.Diagnostics.ProcessStartInfo]::new()" in script
    assert "$process.WaitForExit()" in script
    assert "$gcloudExitCode = $process.ExitCode" in script
    assert "$process.StandardOutput.ReadToEndAsync()" in script
    assert "Write-Output $stdout.TrimEnd()" in script
    assert "cmd.exe /d /c gcloud.cmd" not in script
    assert "builds submit --config $runnerBuildConfig" in script
    assert "--substitutions \"_RUNNER_IMAGE=$image\"" in script


def test_production_disables_fixed_weather_upside_scenario() -> None:
    script = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")

    assert '"SOC_COST_WEATHER_UPSIDE_SCENARIO_ENABLED=false"' in script


def test_production_enables_smoothed_paired_pv_load_scenarios() -> None:
    script = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")

    assert '"SOC_COST_PAIRED_SCENARIOS_ENABLED=true"' in script


def test_high_level_wrapper_supports_resuming_individual_job_deploys() -> None:
    script = (ROOT / "scripts" / "deploy_production_from_env.ps1").read_text(encoding="utf-8")

    for slot in ("23", "03", "07"):
        assert f"[switch]$SkipJob{slot}Deploy" in script
        assert f"if ($SkipJob{slot}Deploy) {{ $jobDeployArgs.SkipJob{slot}Deploy = $true }}" in script


def test_example_preserves_production_soc_and_export_safety_settings() -> None:
    values = _env_example_values()

    assert values["NIGHT_RESERVE_SOC_PERCENT"] == "0"
    assert values["SOC_EXPORT_CONTRACT_STATUS"] == "inactive"
    assert values["SOC_EXPORT_VALUE_MODE"] == "neutral"
    assert values["SOC_SELL_REVENUE_YEN_PER_KWH"] == "0"


def test_production_uses_previous_billing_period_without_heuristic_tier_penalties() -> None:
    values = _env_example_values()
    script = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")

    assert values["SOC_MONTHLY_TARIFF_PROJECTION_ENABLED"] == "true"
    assert values["SOC_TIER1_CROSSING_PENALTY_YEN_PER_KWH"] == "0"
    assert values["SOC_TIER2_EXTRA_PENALTY_YEN_PER_KWH"] == "0"
    assert values["SOC_TIER3_EXTRA_PENALTY_YEN_PER_KWH"] == "0"
    assert '"SOC_MONTHLY_TARIFF_PROJECTION_ENABLED=true"' in script
    assert '"SOC_MONTHLY_TIER_LANDING_ENABLED=false"' in script
    assert '"SOC_TIER3_EXTRA_PENALTY_YEN_PER_KWH=0"' in script
    assert 'ADJUST03_MIN_TARGET_SOC_PERCENT=0' in script


def test_production_deploy_supports_non_mutating_validation() -> None:
    script = (ROOT / "scripts" / "deploy_production_from_env.ps1").read_text(
        encoding="utf-8"
    )

    assert "[switch]$ValidateOnly" in script
    assert "'check_production_env.ps1') -CheckCloud" in script
    assert "No deployment was performed" in script


def test_production_deploy_skips_duplicate_legacy_capacity_subprocess() -> None:
    script = (ROOT / "scripts" / "deploy_production_from_env.ps1").read_text(
        encoding="utf-8"
    )

    assert "SkipCapacityCheck = $true" in script
    assert "SkipIamSetup = $true" in script
    assert "SkipSecretSetup = $true" in script


def test_production_deploy_auto_scope_skips_irrelevant_cloud_work() -> None:
    script = (ROOT / "scripts" / "deploy_production_from_env.ps1").read_text(
        encoding="utf-8"
    )

    assert "[ValidateSet('auto', 'full', 'runner', 'dashboard')]" in script
    assert "function Resolve-DeploymentScope" in script
    assert "Get-LastCompletedDeploymentCommit" in script
    assert "No deployable runner or dashboard source changed" in script
    assert "$SkipDashboardBuild = $true" in script
    assert "$SkipJobBuild = $true" in script
    assert "$SkipKpNetImport = $true" in script
    assert "$SkipDriveBackup = $true" in script


def test_production_deploy_splats_named_job_arguments() -> None:
    script = (ROOT / "scripts" / "deploy_production_from_env.ps1").read_text(
        encoding="utf-8"
    )

    assert "$jobDeployArgs = @{" in script
    assert "DataBackend = Get-RequiredProductionEnv 'DATA_BACKEND'" in script
    assert "@jobDeployArgs" in script


def test_production_deploy_has_fast_verified_smoke_path_and_safe_stage_details() -> None:
    script = (ROOT / "scripts" / "deploy_production_from_env.ps1").read_text(
        encoding="utf-8"
    )

    assert "[switch]$SkipInlineSmokeTest" in script
    assert "if (-not $SkipInlineSmokeTest) { $jobDeployArgs.RunSmokeTest = $true }" in script
    assert "error_detail = $null" in script
    assert "Get-SafeErrorDetail" in script
    assert "Where-Object { $_ -notmatch '(?i)password|secret|token|authorization|credential' }" in script
    assert "--ignore-file $dashboardIgnoreFile" in script


def test_every_production_deploy_runs_the_50_percent_forced_charge_roundtrip() -> None:
    script = (ROOT / "scripts" / "deploy_production_from_env.ps1").read_text(
        encoding="utf-8"
    )
    wrapper = (ROOT / "scripts" / "run_kpnet_settings_roundtrip_from_env.ps1").read_text(
        encoding="utf-8"
    )

    assert "[ValidateRange(50, 50)]" in script
    assert "[double]$SettingsRoundTripTargetSoc = 50" in script
    assert "-Name 'settings_roundtrip'" in script
    assert "run_cloud_job_from_env.ps1" in script
    assert "-Slot settings-roundtrip" in script
    assert "-TestExecution" in script
    assert "Invoke-DeploymentStage -Name 'settings_roundtrip' -Skip:$false" in script
    assert "secrets versions access latest" in wrapper
    assert "Remove-Item Env:KP_MONITOR_USERNAME" in wrapper


def test_deployment_stage_state_uses_explicit_dictionary_or_json_lookup() -> None:
    script = (ROOT / "scripts" / "deploy_production_from_env.ps1").read_text(
        encoding="utf-8"
    )

    assert "function Get-DeploymentStageRecord" in script
    assert "$state.stages[$Name]" in script
    assert "$state.stages.PSObject.Properties[$Name]" in script
    assert "Get-DeploymentStageRecord -Name $Name" in script


def test_runner_build_uses_explicit_cache_and_narrow_context() -> None:
    script = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")
    build = (ROOT / "cloudbuild.runner.yaml").read_text(encoding="utf-8")
    ignore = (ROOT / ".gcloudignore-runner").read_text(encoding="utf-8")

    assert "cloudbuild.runner.yaml" in script
    assert ".gcloudignore-runner" in script
    assert "--ignore-file $runnerIgnoreFile" in script
    assert "--cache-from" in build
    assert "allowFailure: true" in build
    assert "tests/" in ignore
    assert "artifacts/" in ignore


def test_gcloud_failures_capture_redacted_stderr_without_credentials() -> None:
    script = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")

    assert "$startInfo.RedirectStandardError = $true" in script
    assert "$stderrTask = $process.StandardError.ReadToEndAsync()" in script
    assert "$stderr = $stderrTask.GetAwaiter().GetResult()" in script
    assert "gcloud failed (exit code $gcloudExitCode)" in script
    assert "password|secret|token|authorization|credential" in script


def test_cloud_validation_checks_every_production_entrypoint() -> None:
    script = (ROOT / "scripts" / "check_production_env.ps1").read_text(
        encoding="utf-8"
    )

    for slot in ("23", "03", "07"):
        assert f"solar-battery-{slot}" in script
        assert f"solar-battery-run-{slot}" in script
    assert "$ready -ne 'True'" in script
    assert "$state -ne 'ENABLED'" in script


def test_production_gate_automates_backup_security_and_validation() -> None:
    script = (ROOT / "scripts" / "production_deployment_gate.ps1").read_text(
        encoding="utf-8"
    )

    for required in (
        "working_tree_clean",
        "git_diff_check",
        "env_is_ignored_and_unstaged",
        "backup_local.ps1",
        "security_check.py",
        "deploy_production_from_env.ps1",
        "-ValidateOnly",
        "production_deployment_preflight",
    ):
        assert required in script
    assert "StatePath must remain under artifacts/deployment_state" in script


def test_production_gate_records_safe_failure_details() -> None:
    script = (ROOT / "scripts" / "production_deployment_gate.ps1").read_text(
        encoding="utf-8"
    )

    assert "error_detail = $null" in script
    assert "Get-SafeErrorDetail" in script
    assert "Where-Object { $_ -notmatch '(?i)password|secret|token|authorization|credential' }" in script


def test_deployment_resume_requires_same_commit_and_successful_stages() -> None:
    script = (ROOT / "scripts" / "deploy_production_from_env.ps1").read_text(
        encoding="utf-8"
    )

    assert "[switch]$Resume" in script
    assert "Resume state belongs to a different repository commit." in script
    assert "if ($state.stages.pre_release.status -eq 'success')" in script
    assert "if ($state.stages.jobs.status -eq 'success')" in script
    assert "status = 'running'" in script
    assert "error_code = 'command_failed'" in script
    assert "StatePath must remain under artifacts/deployment_state" in script


def test_smoke_requires_explicit_cloud_run_execution_conditions() -> None:
    script = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")

    assert "Assert-LatestSmokeExecution" in script
    assert "$status = $latest.status" in script
    assert "$conditions = @($status.conditions)" in script
    assert "$status.PSObject.Properties.Name -contains 'failedCount'" in script
    assert "[int]$status.failedCount" in script
    assert "$failedCount = if" in script
    assert "conditions were missing from gcloud output" in script
    for condition in ("Completed", "ResourcesAvailable", "Started", "ContainerReady"):
        assert f"'{condition}'" in script


def test_local_source_backup_excludes_credential_environment_files() -> None:
    script = (ROOT / "scripts" / "backup_local.ps1").read_text(encoding="utf-8")

    assert '".env"' in script
    assert "StartsWith('.env.')" in script
    assert "-ne '.env.example'" in script


def test_manual_backup_job_name_is_unique_per_execution() -> None:
    script = (ROOT / "scripts" / "run_drive_backup_cloud_from_env.ps1").read_text(
        encoding="utf-8"
    )

    assert "yyyyMMddHHmmss" in script
    assert "$PID" in script
    assert "$backupSucceeded = $false" in script
    assert "$backupSucceeded = $true" in script
    assert "temporary job cleanup failed" in script
    assert "exit 0" in script


def test_security_check_compares_sensitive_dotenv_values_with_tracked_files() -> None:
    script = (ROOT / "scripts" / "security_check.py").read_text(encoding="utf-8")

    assert '"git", "ls-files", "-z"' in script
    assert '"DRIVE_BACKUP_FOLDER_ID"' in script
    assert '"SHEETS_SPREADSHEET_ID"' in script
    assert "value.encode(\"utf-8\") in content" in script


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
def test_production_env_loader_rejects_a_missing_required_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GCP_PROJECT_ID=\n", encoding="utf-8")
    helper = ROOT / "scripts" / "production_env.ps1"
    command = (
        f". '{helper}'; Import-ProductionEnv -Path '{env_file}'; "
        "Get-RequiredProductionEnv -Name 'GCP_PROJECT_ID'"
    )

    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "missing or empty" in completed.stderr
