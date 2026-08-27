from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_MARKER = "HISTORICAL_FAILURE_LOCK"


def test_historical_failure_protection_document_and_agent_rule_exist() -> None:
    document = (
        ROOT
        / "docs"
        / "current"
        / "agent"
        / "PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md"
    )
    instructions = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert document.exists()
    assert "Historical Failure Protected Regions (Mandatory)" in instructions
    assert (
        "docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md" in instructions
    )


def test_protected_runtime_boundaries_keep_explicit_lock_comments() -> None:
    protected_markers = {
        "app/runtime/night_soc_controller.py": "def build_device_soc_guard(",
        "app/kpnet/profile_builder.py": "def _build_dynamic_forced_profile(",
        "app/runtime/cloud_job.py": "def _monitor_partial_forced_and_stop(",
        "app/runtime/slot_orchestration.py": "def _manual_soc_operation_enabled() -> bool:",
        "app/runtime/plan_persistence.py": "def acquire_night_soc_lease(",
        "app/forecasting/pv_physical.py": "HOURS = range(7, 23)",
        "app/energy_plan/monthly_projection.py": "def previous_billing_period_for_target(",
        "app/kpnet/settings_roundtrip.py": "def run_settings_roundtrip(",
        "scripts/deploy_production_from_env.ps1": "function Get-DeploymentStageRecord {",
        "scripts/deploy_gcp_jobs.ps1": '--max-retries 0 --set-env-vars "$commonEnvArg,CLOUD_JOB_SLOT=settings-roundtrip',
    }

    for relative_path, symbol in protected_markers.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        symbol_index = source.index(symbol)
        preceding = source[max(0, symbol_index - 2_000) : symbol_index]
        assert LOCK_MARKER in preceding, relative_path


def test_protected_document_retains_historical_failure_commits() -> None:
    document = (
        ROOT
        / "docs"
        / "current"
        / "agent"
        / "PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md"
    ).read_text(encoding="utf-8")

    for commit in (
        "d1d7792",
        "0a804f4",
        "5e46ff8",
        "4af0d59",
        "541cd60",
        "ee84e43",
        "1dd21ae",
    ):
        assert commit in document


def test_night_soc_incident_production_contract_keeps_automatic_default_and_floor() -> None:
    """Lock the deployed values whose reversal reproduced the 2026-08-28 SOC=0 path."""
    deploy = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")

    assert '"NIGHT_SOC_MANUAL_OPERATION=false"' in deploy
    assert "ADJUST03_MIN_TARGET_SOC_PERCENT=30" in deploy
    assert "ADJUST03_MIN_TARGET_SOC_PERCENT=0" not in deploy
    assert "HISTORICAL_FAILURE_LOCK (1dd21ae, 2026-08-28 incident evidence)" in deploy


def test_incident_validation_locks_scheduled_replay_before_live_roundtrip() -> None:
    script = (ROOT / "scripts" / "kpnet_incident_validation.py").read_text(encoding="utf-8")

    scheduled_index = script.index("scheduled_auto_path = run_scheduled_auto_path_validation()")
    live_index = script.index("live = run_settings_roundtrip(")
    assert scheduled_index < live_index
    assert 'result["failure_stage"] = "scheduled_auto_path"' in script
    assert "HISTORICAL_FAILURE_LOCK (1dd21ae, 2026-08-28 incident evidence)" in script
