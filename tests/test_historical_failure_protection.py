from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_user_authorized_20260829_time_ownership_replaces_cross_slot_gate() -> None:
    doc = (ROOT / "docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md").read_text(encoding="utf-8")
    assert "2026-08-29 利用者承認" in doc
    assert "06:45 realtime監視停止・06:50最終standby開始停止・06:55 I/O停止" in doc


def test_legacy_cross_slot_gate_has_no_scheduled_entrypoint() -> None:
    source = (ROOT / "app/runtime/slot_orchestration.py").read_text(encoding="utf-8")
    assert "_assert_day_transition_allowed" not in source
    assert "_manual_soc_operation_enabled" not in source
    assert "_run_optional_04_exports_and_backups" not in source


def test_protection_document_retires_obsolete_cross_slot_contract_symbols() -> None:
    doc = (ROOT / "docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md").read_text(encoding="utf-8")
    for obsolete in ("manual opt-in", "07 green gate", "_apply_03_confirmed_standby", "30%実行床"):
        assert obsolete not in doc
    assert "device read-back" in doc
    assert "時刻所有権" in doc


def test_retired_architecture_document_points_only_to_current_independent_slot_contract() -> None:
    doc = (ROOT / "docs/current/architecture/NIGHT_SOC_SINGLE_OWNER_IMPLEMENTATION_SPEC_JA.md").read_text(encoding="utf-8")
    protection = (ROOT / "docs/current/agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md").read_text(encoding="utf-8")

    assert "退役した歴史的設計" in doc
    assert "2026-08-29の利用者承認" in doc
    assert "app/runtime/night_soc_time_contract.py" in doc
    assert "app/runtime/night_soc_time_contract.py" in protection
    for retired_directive in ("NIGHT_SOC_MANUAL_OPERATION", "ADJUST03_MIN_TARGET_SOC_PERCENT", "LEASE_ACQUIRED"):
        assert retired_directive not in doc
