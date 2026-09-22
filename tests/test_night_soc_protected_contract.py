from __future__ import annotations

import ast
from pathlib import Path

from app.runtime.night_soc_operational_contract import (
    SLOT03_CLOUD_RUN_MAX_RETRIES,
    SLOT03_PLATFORM_RETRY_DELAY_SECONDS,
    SLOT23_PRESERVED_FIELDS,
)
from app.runtime.night_soc_time_contract import CONTROL_HARD_CUTOFF, FINAL_STANDBY_START_CUTOFF, FORCED_MONITOR_CUTOFF, GREEN_START


ROOT = Path(__file__).resolve().parents[1]


def _local_window(source: str, marker: str, size: int = 4000) -> str:
    start = source.index(marker)
    return source[max(0, start - size):start + size]


def test_independent_time_ownership_contract_is_immutable() -> None:
    assert SLOT03_CLOUD_RUN_MAX_RETRIES == 3
    assert SLOT03_PLATFORM_RETRY_DELAY_SECONDS == 300
    assert "batteryOperatingMode" not in SLOT23_PRESERVED_FIELDS
    assert set(SLOT23_PRESERVED_FIELDS) == {
        "socSafetyMode",
        "socEconomyMode",
        "socContactInput",
        "socChargeMode",
        "chargeStartTimeH",
        "chargeStartTimeM",
        "chargeEndTimeH",
        "chargeEndTimeM",
        "dischargeStartTimeH",
        "dischargeStartTimeM",
        "dischargeEndTimeH",
        "dischargeEndTimeM",
        "agreementAmpere",
        "onPowerOutageMode",
        "onPowerOutageChargePowerW",
    }
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


def test_03_direct_soc_path_has_local_20260906_regression_lock() -> None:
    source = (ROOT / "app/runtime/cloud_job.py").read_text(encoding="utf-8")
    window = _local_window(source, "allow_csv_fallback=False", size=1200)

    assert "HISTORICAL_FAILURE_LOCK" in window
    assert "2026-09-06" in window
    assert "live KP-NET visualization SOC" in window
    assert "変更禁止" in window
    assert "allow_csv_fallback=False" in window
    assert "単発の取得失敗でstandbyへ遷移しない" in source
    assert "正常値を取得したら連続失敗回数を0へ戻す" in source
    assert "test_runner_soc_path_never_uses_delayed_csv_when_realtime_is_unavailable" in window


def test_07_entrypoint_is_ast_limited_to_one_green_call() -> None:
    tree = ast.parse((ROOT / "app/runtime/slot_orchestration.py").read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_day_07")
    calls = [node for node in ast.walk(fn) if isinstance(node, ast.Call)]
    assert len(calls) == 1
    keywords = {key.arg: key.value.value for key in calls[0].keywords if isinstance(key.value, ast.Constant)}
    assert keywords == {"profile": "green", "dynamic_forced_profile": False, "label": "07-green"}


def test_07_green_path_changes_only_operating_mode() -> None:
    workflow = (ROOT / "app/kpnet/workflow.py").read_text(encoding="utf-8")
    start = workflow.index('elif profile == "green":')
    end = workflow.index('elif profile == "economy":', start)
    window = workflow[start:end]

    assert "_mode_only_profile_from_current_settings" in window
    assert 'prefer="green"' in window
    assert "soc_economy_mode=" not in window
    for forbidden in (
        "soc_safety_mode=",
        "soc_contact_input=",
        "soc_charge_mode=",
        "charge_start_h=",
        "charge_end_h=",
        "discharge_start_h=",
        "discharge_end_h=",
        "agreement_ampere=",
    ):
        assert forbidden not in window


def test_07_mode_only_requires_explicit_mode_readback() -> None:
    workflow = (ROOT / "app/kpnet/workflow.py").read_text(encoding="utf-8")
    start = workflow.index("def run_kpnet_mode_only_profile")
    window = workflow[start:]

    assert 'if profile in {"standby", "green", "forced"}' in window
    assert 'name="07-green-mode-only"' in window
    assert "required_readback_fields=required_readback_fields" in window
    assert "candidate_maps_fetched=candidate_maps_fetched" in window
    assert '"message": "kpnet-settings-readback"' in workflow


def test_03_forced_mode_only_changes_only_operating_mode() -> None:
    workflow = (ROOT / "app/kpnet/workflow.py").read_text(encoding="utf-8")
    window = _local_window(workflow, 'elif profile == "forced":', size=900)
    candidate_helper = _local_window(workflow, "def _minimal_03_candidate_maps", size=1200)

    assert "_mode_only_profile_from_current_settings" in window
    assert 'prefer="forced"' in window
    assert "soc_charge_mode=" not in window
    assert "_pick_max_code" not in window
    assert "valueList/socchargemode" not in candidate_helper
