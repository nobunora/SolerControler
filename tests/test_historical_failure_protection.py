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
        preceding = source[max(0, symbol_index - 500) : symbol_index]
        assert LOCK_MARKER in preceding, relative_path


def test_protected_document_retains_historical_failure_commits() -> None:
    document = (
        ROOT
        / "docs"
        / "current"
        / "agent"
        / "PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md"
    ).read_text(encoding="utf-8")

    for commit in ("d1d7792", "0a804f4", "5e46ff8", "4af0d59", "541cd60", "ee84e43"):
        assert commit in document
