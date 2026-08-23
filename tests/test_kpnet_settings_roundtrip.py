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


def test_target_guard_uses_the_maximum_device_candidate_for_a_higher_plan_target() -> None:
    guard = validate_forced_target(
        value_maps={"SocChargeMode": {"0": "0", "50": "50"}}, target_soc_percent=100.0
    )

    assert guard.device_soc_code == "50"
    assert guard.stop_threshold_percent == pytest.approx(100.0)


def test_probe_sets_forced_charge_with_the_supported_50_percent_candidate() -> None:
    restore = profile_from_current_settings(_current())
    probe = make_reversible_probe_profile(
        restore_profile=restore,
        value_maps={
            "BatteryOperatingMode": {"0": "待機", "1": "グリーン", "3": "強制充電"},
            "SocChargeMode": {"0": "0", "50": "50"},
        },
        target_soc_percent=50,
    )

    assert probe.name == "post-deploy-forced-charge-50-probe"
    assert probe.battery_operating_mode == "3"
    assert probe.soc_charge_mode == "50"
    assert restore.battery_operating_mode == "1"


def test_probe_requires_a_forced_charge_mode_candidate() -> None:
    with pytest.raises(RuntimeError, match="forced-charge"):
        make_reversible_probe_profile(
            restore_profile=profile_from_current_settings(_current()),
            value_maps={
                "BatteryOperatingMode": {"0": "待機", "1": "グリーン"},
                "SocChargeMode": {"0": "0", "50": "50"},
            },
            target_soc_percent=50,
        )


def test_live_roundtrip_requires_an_exact_one_minute_hold() -> None:
    from app.kpnet.settings_roundtrip import run_settings_roundtrip

    with pytest.raises(ValueError, match="exactly 60"):
        run_settings_roundtrip(target_soc_percent=50.0, hold_seconds=59)


def test_live_roundtrip_applies_forced_charge_50_then_restores_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.kpnet.settings_roundtrip as roundtrip

    current = _current()
    applied_profiles = []

    class FakeClient:
        csrf_setting = "csrf"
        pcsid = "pcsid"

        def __init__(self, _cfg: object) -> None:
            pass

        def login(self) -> None:
            pass

        def open_settings_page(self) -> None:
            pass

        def read_current_settings(self) -> dict[str, str]:
            return dict(current)

        def collect_candidate_maps(self) -> dict[str, dict[str, str]]:
            return {
                "BatteryOperatingMode": {"0": "待機", "1": "グリーン", "3": "強制充電"},
                "SocChargeMode": {"0": "0", "50": "50"},
            }

        def logout(self) -> None:
            pass

    def fake_apply(**kwargs: object) -> tuple[dict[str, str], list[str]]:
        profile = kwargs["profile"]
        applied_profiles.append(profile)
        return dict(current), ["batteryOperatingMode", "socChargeMode"]

    monkeypatch.setattr(roundtrip.KpNetConfig, "from_env", lambda: type("Cfg", (), {"dry_run": False})())
    monkeypatch.setattr(roundtrip, "KpNetClient", FakeClient)
    monkeypatch.setattr(roundtrip, "_apply_and_verify", fake_apply)
    monkeypatch.setattr(roundtrip.time, "sleep", lambda _seconds: None)

    summary = roundtrip.run_settings_roundtrip(target_soc_percent=50.0)

    assert summary["status"] == "passed"
    assert [profile.name for profile in applied_profiles] == [
        "post-deploy-forced-charge-50-probe",
        "post-deploy-restore",
    ]
    assert applied_profiles[0].battery_operating_mode == "3"
    assert applied_profiles[0].soc_charge_mode == "50"
    assert summary["restore_verified"] is True


def test_roundtrip_emits_a_restore_audit_record() -> None:
    source = (
        __import__("app.kpnet.settings_roundtrip", fromlist=["__file__"]).__file__
    )
    assert source is not None
    text = open(source, encoding="utf-8").read()

    assert '"restore_verified": True' in text
    assert "[settings_roundtrip]" in text
