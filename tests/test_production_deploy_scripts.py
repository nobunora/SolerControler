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


def test_dashboard_cloudbuild_requires_an_explicit_image_substitution() -> None:
    config = (ROOT / "cloudbuild.dashboard.yaml").read_text(encoding="utf-8")

    assert config.count("${_DASHBOARD_IMAGE}") == 2
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
    assert "builds submit --region $Region --tag $image --project $ProjectId $repoRoot" in script


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

    assert values["NIGHT_RESERVE_SOC_PERCENT"] == "30"
    assert values["SOC_EXPORT_CONTRACT_STATUS"] == "inactive"
    assert values["SOC_EXPORT_VALUE_MODE"] == "neutral"
    assert values["SOC_SELL_REVENUE_YEN_PER_KWH"] == "0"


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


def test_production_deploy_splats_named_job_arguments() -> None:
    script = (ROOT / "scripts" / "deploy_production_from_env.ps1").read_text(
        encoding="utf-8"
    )

    assert "$jobDeployArgs = @{" in script
    assert "DataBackend = Get-RequiredProductionEnv 'DATA_BACKEND'" in script
    assert "@jobDeployArgs" in script


def test_cloud_validation_checks_every_production_entrypoint() -> None:
    script = (ROOT / "scripts" / "check_production_env.ps1").read_text(
        encoding="utf-8"
    )

    for slot in ("23", "03", "07"):
        assert f"solar-battery-{slot}" in script
        assert f"solar-battery-run-{slot}" in script
    assert "$ready -ne 'True'" in script
    assert "$state -ne 'ENABLED'" in script


def test_manual_backup_job_name_is_unique_per_execution() -> None:
    script = (ROOT / "scripts" / "run_drive_backup_cloud_from_env.ps1").read_text(
        encoding="utf-8"
    )

    assert "yyyyMMddHHmmss" in script
    assert "$PID" in script


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
