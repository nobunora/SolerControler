from __future__ import annotations

import ast
from pathlib import Path

from app.runtime.night_soc_operational_contract import SLOT03_CLOUD_RUN_MAX_RETRIES, SLOT23_PRESERVED_FIELDS
from app.runtime.night_soc_time_contract import CONTROL_HARD_CUTOFF, FINAL_STANDBY_START_CUTOFF, FORCED_MONITOR_CUTOFF, GREEN_START


ROOT = Path(__file__).resolve().parents[1]


def _local_window(source: str, marker: str, size: int = 4000) -> str:
    start = source.index(marker)
    return source[max(0, start - size):start + size]


def test_independent_time_ownership_contract_is_immutable() -> None:
    assert SLOT03_CLOUD_RUN_MAX_RETRIES == 0
    assert "batteryOperatingMode" not in SLOT23_PRESERVED_FIELDS
    assert len(SLOT23_PRESERVED_FIELDS) == 12
    assert (FORCED_MONITOR_CUTOFF.hour, FORCED_MONITOR_CUTOFF.minute) == (6, 45)
    assert (FINAL_STANDBY_START_CUTOFF.hour, FINAL_STANDBY_START_CUTOFF.minute) == (6, 50)
    assert (CONTROL_HARD_CUTOFF.hour, CONTROL_HARD_CUTOFF.minute) == (6, 55)
    assert (GREEN_START.hour, GREEN_START.minute) == (7, 0)


def test_protected_boundaries_have_local_20260829_locks() -> None:
    boundaries = (
        ("app/kpnet/workflow.py", "def _preserve_night_soc_fields"),
        ("app/kpnet/profile_builder.py", "def _pick_battery_operating_mode_code"),
        ("app/runtime/slot_orchestration.py", "def _run_night_23"),
        ("app/runtime/slot_orchestration.py", "def _run_adjust_03"),
        ("app/runtime/slot_orchestration.py", "def _run_day_07"),
        ("app/runtime/cloud_job.py", "def _monitor_partial_forced_and_stop("),
        ("app/runtime/night_soc_time_contract.py", "CONTROL_HARD_CUTOFF"),
        ("scripts/deploy_gcp_jobs.ps1", "run jobs deploy $Job03Name"),
    )
    for relative, symbol in boundaries:
        source = (ROOT / relative).read_text(encoding="utf-8")
        window = _local_window(source, symbol)
        assert "HISTORICAL_FAILURE_LOCK" in window
        assert "2026-08-29" in window
        assert "Guarded by" in window or "guarded by" in window


def test_07_entrypoint_is_ast_limited_to_one_green_call() -> None:
    tree = ast.parse((ROOT / "app/runtime/slot_orchestration.py").read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_day_07")
    calls = [node for node in ast.walk(fn) if isinstance(node, ast.Call)]
    assert len(calls) == 1
    keywords = {key.arg: key.value.value for key in calls[0].keywords if isinstance(key.value, ast.Constant)}
    assert keywords == {"profile": "green", "dynamic_forced_profile": False, "label": "07-green"}
