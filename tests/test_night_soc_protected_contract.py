"""Non-negotiable regression guard for the 2026-08-28 morning SOC=0 incident."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.runtime.night_soc_controller import effective_target_soc
from app.runtime.night_soc_operational_contract import (
    DAY_TRANSITION_ALLOWED_STATES,
    SLOT03_CLOUD_RUN_MAX_RETRIES,
    SLOT23_PRESERVED_FIELDS,
    is_day_transition_allowed_state,
)
from app.settings.forced_charge import ForcedChargeSettings
from scripts.kpnet_incident_validation import (
    run_manual_handoff_validation,
    run_scheduled_auto_path_validation,
)


ROOT = Path(__file__).resolve().parents[1]


def test_protected_contract_has_documented_locks_at_each_operational_boundary() -> None:
    """Keep explanations beside the boundaries whose innocent edits cause SOC=0."""
    boundaries = (
        ("app/settings/forced_charge.py", "min_target_soc_percent=min(",
            "min_target_soc_percent=min(", "1dd21ae, 2026-08-28 incident evidence"
        ),
        ("app/runtime/slot_orchestration.py", "manual-operation",
            "def _manual_soc_operation_enabled() -> bool:", "1dd21ae, 2026-08-28 incident evidence"
        ),
        ("app/runtime/slot_orchestration.py", "scheduled-monitor",
            "_monitor_partial_forced_and_stop(plan_path)", "1dd21ae, 2026-08-28 incident evidence"
        ),
        ("scripts/deploy_gcp_jobs.ps1", "manual-default",
            '"NIGHT_SOC_MANUAL_OPERATION=false"', "2026-08-28 that made a"
        ),
        ("scripts/kpnet_incident_validation.py", "scheduled-replay",
            "def run_scheduled_auto_path_validation()", "1dd21ae, 2026-08-28 incident evidence"
        ),
        ("app/runtime/night_soc_operational_contract.py", "slot23-fields",
            "SLOT23_PRESERVED_FIELDS", "2026-08-28/29 runtime\n# evidence"
        ),
        ("app/kpnet/workflow.py", "slot23-preserve", "def _preserve_night_soc_fields", "EVIDENCE_20260829_SLOT23_PRESERVE"),
        ("app/kpnet/profile_builder.py", "standby-candidate", "def _pick_battery_operating_mode_code", "EVIDENCE_20260829_STANDBY_CANDIDATE"),
        ("app/runtime/cloud_job.py", "fail-safe-finalizer", "def _finalize_03_exception_with_fail_safe_standby", "EVIDENCE_20260829_FAILSAFE_FINALIZER"),
        ("app/runtime/cloud_job.py", "confirmed-standby", "def _apply_03_confirmed_standby", "EVIDENCE_20260829_CONFIRMED_STANDBY"),
        ("app/runtime/night_soc_operational_contract.py", "day-gate", "DAY_TRANSITION_ALLOWED_STATES", "EVIDENCE_20260829_DAY_GATE"),
        ("scripts/deploy_gcp_jobs.ps1", "job03-retry", "CLOUD_JOB_SLOT=03", "EVIDENCE_20260829_JOB03_RETRY"),
    )
    source_cache: dict[str, str] = {}
    for relative_path, boundary_name, symbol, evidence_marker in boundaries:
        source = source_cache.setdefault(relative_path, (ROOT / relative_path).read_text(encoding="utf-8"))
        symbol_index = source.index(symbol)
        context = source[max(0, symbol_index - 1_500) : symbol_index + 500]
        assert "HISTORICAL_FAILURE_LOCK" in context, f"missing incident lock for {boundary_name}"
        assert evidence_marker in context, f"missing incident evidence for {boundary_name}: {evidence_marker}"


def test_each_20260829_boundary_has_a_unique_local_lock_and_document_row() -> None:
    """One distant/global marker must never satisfy several protected boundaries."""
    boundaries = {
        "EVIDENCE_20260829_SLOT23_PRESERVE": (
            "app/kpnet/workflow.py", "def _preserve_night_soc_fields"
        ),
        "EVIDENCE_20260829_STANDBY_CANDIDATE": (
            "app/kpnet/profile_builder.py", "def _pick_battery_operating_mode_code"
        ),
        "EVIDENCE_20260829_FAILSAFE_FINALIZER": (
            "app/runtime/cloud_job.py", "def _finalize_03_exception_with_fail_safe_standby"
        ),
        "EVIDENCE_20260829_CONFIRMED_STANDBY": (
            "app/runtime/cloud_job.py", "def _apply_03_confirmed_standby"
        ),
        "EVIDENCE_20260829_DAY_GATE": (
            "app/runtime/night_soc_operational_contract.py", "DAY_TRANSITION_ALLOWED_STATES"
        ),
        "EVIDENCE_20260829_JOB03_RETRY": (
            "scripts/deploy_gcp_jobs.ps1",
            'if (-not $SkipJob03Deploy) { Invoke-GCloud run jobs deploy $Job03Name',
        ),
    }
    protection_doc = (
        ROOT / "docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md"
    ).read_text(encoding="utf-8")

    assert len(boundaries) == len(set(boundaries))
    for marker, (relative_path, symbol) in boundaries.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        symbol_index = source.index(symbol)
        local_preceding = source[max(0, symbol_index - 1_200) : symbol_index]
        assert "HISTORICAL_FAILURE_LOCK" in local_preceding, relative_path
        assert marker in local_preceding, f"{marker} is not locally bound to {symbol}"
        assert protection_doc.count(f"(`{marker}`)") == 1, f"missing unique protection row: {marker}"


def test_protected_operational_contract_is_immutable_and_fail_closed() -> None:
    assert "batteryOperatingMode" not in SLOT23_PRESERVED_FIELDS
    assert SLOT03_CLOUD_RUN_MAX_RETRIES == 0
    assert len(SLOT23_PRESERVED_FIELDS) == 12
    assert DAY_TRANSITION_ALLOWED_STATES == frozenset({"STANDBY_ACKED", "COMPLETED_NO_CHARGE", "VERIFIED"})
    assert is_day_transition_allowed_state("STANDBY_ACKED") is True
    assert is_day_transition_allowed_state("STANDBY_UNCONFIRMED") is False


def test_operational_contract_is_io_free_and_exactly_locks_the_20260828_29_boundaries() -> None:
    source = (ROOT / "app" / "runtime" / "night_soc_operational_contract.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]

    assert all(isinstance(node, ast.ImportFrom) and node.module == "typing" for node in imports)
    assert "batteryOperatingMode" not in SLOT23_PRESERVED_FIELDS
    assert type(SLOT23_PRESERVED_FIELDS) is tuple
    assert len(SLOT23_PRESERVED_FIELDS) == 12
    assert type(DAY_TRANSITION_ALLOWED_STATES) is frozenset
    assert DAY_TRANSITION_ALLOWED_STATES == frozenset({"STANDBY_ACKED", "COMPLETED_NO_CHARGE", "VERIFIED"})
    # Names in explanatory lock comments are allowed; imports are the executable
    # dependency boundary and were checked above.
    assert "open(" not in source
    assert "subprocess" not in source
    assert "2026-08-28/29 runtime" in source
    assert "2026-08-29 03:00 forced-reapply failure" in source


def test_protected_sources_keep_23_standby_and_03_retry_locks() -> None:
    workflow = (ROOT / "app" / "kpnet" / "workflow.py").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")
    cloud_job = (ROOT / "app" / "runtime" / "cloud_job.py").read_text(encoding="utf-8")
    contract = (ROOT / "app" / "runtime" / "night_soc_operational_contract.py").read_text(encoding="utf-8")

    assert "SLOT23_PRESERVED_FIELDS" in workflow
    assert 'prefer="standby"' in workflow
    job03_deploy = next(line for line in deploy.splitlines() if "CLOUD_JOB_SLOT=03" in line)
    assert "--max-retries 0" in job03_deploy
    assert str(SLOT03_CLOUD_RUN_MAX_RETRIES) == job03_deploy.split("--max-retries ", 1)[1].split()[0]
    assert "forced_reapply_failed_fail_safe" in cloud_job
    assert "failure_terminal_values" in cloud_job
    assert "STANDBY_UNCONFIRMED" in contract


def test_protected_execution_floor_is_30_and_preserves_higher_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale 0 override cannot turn optimizer plan=0 into executable SOC=0."""
    monkeypatch.setenv("ADJUST03_MIN_TARGET_SOC_PERCENT", "0")

    floor = ForcedChargeSettings.from_env().min_target_soc_percent

    assert floor == 30.0
    assert effective_target_soc(0, floor) == 30.0
    assert effective_target_soc(100, floor) == 100.0


def test_protected_production_default_is_automatic_not_manual() -> None:
    deploy = (ROOT / "scripts" / "deploy_gcp_jobs.ps1").read_text(encoding="utf-8")

    assert '"NIGHT_SOC_MANUAL_OPERATION=false"' in deploy
    assert '"NIGHT_SOC_MANUAL_OPERATION=true"' not in deploy
    assert "ADJUST03_MIN_TARGET_SOC_PERCENT=30" in deploy


def test_protected_manual_is_opt_in_and_scheduled_path_reaches_terminal_gate_and_green() -> None:
    """Exercise both ownership modes through real orchestration code with fake Firestore only."""
    manual = run_manual_handoff_validation()
    scheduled = run_scheduled_auto_path_validation()

    assert manual["status"] == "passed"
    assert manual["gate_allows_explicit_manual"] is True
    assert manual["gate_rejects_implicit_manual"] is True
    assert scheduled["status"] == "passed"
    assert scheduled["manual_operation_enabled"] is False
    assert scheduled["monitor_call_count"] == 1
    assert scheduled["terminal_state"] == "STANDBY_ACKED"
    assert scheduled["settings_calls"] == [
        {"profile": "standby", "dynamic_forced_profile": False, "label": "23-settings-standby"},
        {"profile": "green", "dynamic_forced_profile": False, "label": "07-green"},
    ]
