from __future__ import annotations

import pytest

from app.kpnet.settings_roundtrip import (
    make_reversible_probe_profile,
    profile_from_current_settings,
    validate_forced_target,
)


def _current() -> dict[str, str]:
    return {
        "batteryOperatingMode": "1",
        "socSafetyMode": "0",
        "socEconomyMode": "0",
        "socContactInput": "0",
        "socChargeMode": "0",
        "chargeStartTimeH": "23",
        "chargeStartTimeM": "0",
        "chargeEndTimeH": "7",
        "chargeEndTimeM": "0",
        "dischargeStartTimeH": "7",
        "dischargeStartTimeM": "0",
        "dischargeEndTimeH": "23",
        "dischargeEndTimeM": "0",
        "agreementAmpere": "50",
    }


def test_target_guard_rejects_unrepresentable_target_before_probe() -> None:
    with pytest.raises(ValueError, match="no candidate"):
        validate_forced_target(
            value_maps={"SocChargeMode": {"0": "0", "50": "50"}}, target_soc_percent=100.0
        )


def test_probe_prefers_standby_mode_and_restore_profile_keeps_snapshot() -> None:
    restore = profile_from_current_settings(_current())
    probe = make_reversible_probe_profile(
        restore_profile=restore,
        value_maps={
            "BatteryOperatingMode": {"0": "待機", "1": "グリーン"},
            "SocChargeMode": {"0": "0", "10": "10"},
        },
    )

    assert probe.name == "post-deploy-standby-probe"
    assert probe.battery_operating_mode == "0"
    assert probe.soc_charge_mode == restore.soc_charge_mode
    assert restore.battery_operating_mode == "1"


def test_probe_falls_back_to_an_alternate_soc_only_when_already_standby() -> None:
    current = _current()
    current["batteryOperatingMode"] = "0"
    restore = profile_from_current_settings(current)

    probe = make_reversible_probe_profile(
        restore_profile=restore,
        value_maps={
            "BatteryOperatingMode": {"0": "待機"},
            "SocChargeMode": {"0": "0", "10": "10"},
        },
    )

    assert probe.name == "post-deploy-soc-probe"
    assert probe.battery_operating_mode == "0"
    assert probe.soc_charge_mode == "10"


def test_live_roundtrip_requires_an_exact_one_minute_hold() -> None:
    from app.kpnet.settings_roundtrip import run_settings_roundtrip

    with pytest.raises(ValueError, match="exactly 60"):
        run_settings_roundtrip(target_soc_percent=50.0, hold_seconds=59)


def test_roundtrip_emits_a_restore_audit_record() -> None:
    source = (
        __import__("app.kpnet.settings_roundtrip", fromlist=["__file__"]).__file__
    )
    assert source is not None
    text = open(source, encoding="utf-8").read()

    assert '"restore_verified": True' in text
    assert "[settings_roundtrip]" in text
