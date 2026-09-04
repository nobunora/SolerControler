from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


# HISTORICAL_FAILURE_LOCK (2026-09-04): dashboard-only deployment must not
# mutate inverter settings merely to validate an unrelated dashboard revision.
# Runner/full deployments retain the protected 50% settings round-trip.


def test_dashboard_only_deployment_records_settings_roundtrip_as_not_applicable() -> None:
    script = (ROOT / "scripts" / "deploy_production_from_env.ps1").read_text(encoding="utf-8")

    assert "if ($resolvedScope -eq 'dashboard')" in script
    assert "$roundTripStage.status = 'skipped_not_applicable'" in script
    assert "Skip stage: settings_roundtrip (not applicable to dashboard-only deployment)" in script
    assert "Invoke-DeploymentStage -Name 'settings_roundtrip' -Skip:$false" in script
    assert "-Slot settings-roundtrip" in script
    assert "-SettingsRoundTripTargetSoc $SettingsRoundTripTargetSoc -TestExecution" in script


def test_dashboard_only_runbook_is_explicitly_non_control() -> None:
    runbook = (ROOT / "docs" / "current" / "ops" / "PRODUCTION_DEPLOYMENT_RUNBOOK_JA.md").read_text(
        encoding="utf-8"
    )

    assert "### 2.0 dashboard-only の非制御デプロイ契約" in runbook
    assert "-DeploymentScope dashboard" in runbook
    assert "settings_roundtrip.status` を `skipped_not_applicable`" in runbook
    assert "runner/fullではsettings round-tripを必須" in runbook
    assert "browserで履歴forecast-vs-actualと予想SOCが表示される" in runbook


def test_protected_regions_keep_roundtrip_for_runner_full_only() -> None:
    protected = (
        ROOT / "docs" / "current" / "agent" / "PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md"
    ).read_text(encoding="utf-8")

    assert "runner/fullではsettings round-tripを必須" in protected
    assert "dashboard-onlyではrunner/control経路を変更しないため実機round-tripを起動せず" in protected
    assert "`skipped_not_applicable`" in protected
