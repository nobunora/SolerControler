"""Non-negotiable regression guard for the 2026-08-28 morning SOC=0 incident."""

from __future__ import annotations

from pathlib import Path

from app.runtime.night_soc_controller import effective_target_soc
from app.settings.forced_charge import ForcedChargeSettings
from scripts.kpnet_incident_validation import (
    run_manual_handoff_validation,
    run_scheduled_auto_path_validation,
)


ROOT = Path(__file__).resolve().parents[1]


def test_protected_contract_has_documented_locks_at_each_operational_boundary() -> None:
    """Keep explanations beside the boundaries whose innocent edits cause SOC=0."""
    boundaries = {
        "app/settings/forced_charge.py": "min_target_soc_percent=min(",
        "app/runtime/slot_orchestration.py": "def _manual_soc_operation_enabled() -> bool:",
        "app/runtime/slot_orchestration.py#monitor": "_monitor_partial_forced_and_stop(plan_path)",
        "scripts/deploy_gcp_jobs.ps1": '"NIGHT_SOC_MANUAL_OPERATION=false"',
        "scripts/kpnet_incident_validation.py": "def run_scheduled_auto_path_validation()",
    }
    source_cache: dict[str, str] = {}
    for key, symbol in boundaries.items():
        relative_path = key.split("#", maxsplit=1)[0]
        source = source_cache.setdefault(relative_path, (ROOT / relative_path).read_text(encoding="utf-8"))
        symbol_index = source.rindex(symbol) if key.endswith("#monitor") else source.index(symbol)
        context = source[max(0, symbol_index - 2_000) : symbol_index + 2_000]
        assert "HISTORICAL_FAILURE_LOCK" in context, f"missing incident lock for {key}"
        assert "1dd21ae, 2026-08-28 incident evidence" in context, f"missing incident evidence for {key}"


def test_protected_execution_floor_is_30_and_preserves_higher_plan(monkeypatch) -> None:
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
