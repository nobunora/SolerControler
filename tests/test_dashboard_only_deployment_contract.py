from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


# HISTORICAL_FAILURE_LOCK (2026-09-04): non-control deployment scopes must not
# mutate inverter settings merely to validate dashboard/forecast-only changes.
# Runner/full deployments retain the protected 50% settings round-trip.


def test_non_control_scopes_record_settings_roundtrip_as_not_applicable() -> None:
    script = (ROOT / "scripts" / "deploy_production_from_env.ps1").read_text(encoding="utf-8")

    assert "[ValidateSet('auto', 'full', 'runner', 'forecast', 'dashboard', 'control-readonly')]" in script
    assert "if ($resolvedScope -eq 'dashboard')" in script
    assert "if ($resolvedScope -eq 'forecast')" in script
    assert "if ($resolvedScope -in @('dashboard', 'forecast', 'control-readonly'))" in script
    assert "$roundTripStage.status = 'skipped_not_applicable'" in script
    assert "Skip stage: settings_roundtrip (not applicable to $resolvedScope deployment)" in script
    assert "Invoke-DeploymentStage -Name 'settings_roundtrip' -Skip:$false" in script
    assert "-Slot settings-roundtrip" in script
    assert "-SettingsRoundTripTargetSoc $SettingsRoundTripTargetSoc -TestExecution" in script


def test_forecast_scope_updates_only_forecast_job_revision() -> None:
    script = (ROOT / "scripts" / "deploy_production_from_env.ps1").read_text(encoding="utf-8")

    assert "$SkipJob23Deploy = $true" in script
    assert "$SkipJob03Deploy = $true" in script
    assert "$SkipJob07Deploy = $true" in script
    assert "$SkipForecastJobDeploy = $false" in script
    assert "$SkipForecastSchedulerDeploy = $true" in script
    assert "$SkipSettingsRoundTripJobDeploy = $true" in script
    assert "$SkipInlineSmokeTest = $true" in script


def test_non_control_scope_runbook_is_explicit() -> None:
    runbook = (ROOT / "docs" / "current" / "ops" / "PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md").read_text(
        encoding="utf-8"
    )

    assert "### 2.0 非制御scope: dashboard-only / forecast-only" in runbook
    assert "-DeploymentScope dashboard" in runbook
    assert "-DeploymentScope forecast" in runbook
    assert "settings_roundtrip.status` を `skipped_not_applicable`" in runbook
    assert "`runner`または`full` scopeでは" in runbook
    assert "settings round-tripを従来どおり必須" in runbook
    assert "control-readonly" in runbook
    assert "23/03/07 Schedulerはdeploy前後のschedule/time-zone/targetが一致" in runbook
    assert "browserで履歴forecast-vs-actualと予想SOCが表示される" in runbook


def test_protected_regions_keep_roundtrip_for_runner_full_only() -> None:
    protected = (
        ROOT / "docs" / "current" / "agent" / "PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md"
    ).read_text(encoding="utf-8")

    assert "runner/fullではsettings round-tripを必須" in protected
    assert "control-readonly" in protected
    assert "`skipped_not_applicable`" in protected


def test_dashboard_deploy_preserves_runtime_identity_and_grants_read_only_snapshot_access() -> None:
    script = (ROOT / "scripts" / "deploy_production_from_env.ps1").read_text(encoding="utf-8")

    assert "value(spec.template.spec.serviceAccountName)" in script
    assert "roles/storage.objectViewer" in script
    assert "Dashboard Cloud Run service account could not be resolved." in script
    assert "--service-account (Get-RequiredProductionEnv 'GCP_RUN_SERVICE_ACCOUNT')" not in script

