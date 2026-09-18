from __future__ import annotations

from typing import Any

import pytest

from app.kpnet.settings_roundtrip import (
    _economy_probe_candidate_maps,
    _forced_probe_candidate_maps,
    make_economy_probe_profile,
    make_forced_probe_profile,
    profile_from_current_settings,
)


def _current() -> dict[str, str]:
    return {
        "batteryOperatingMode": "5",
        "socSafetyMode": "20",
        "socEconomyMode": "10",
        "socContactInput": "30",
        "socChargeMode": "50",
        "chargeStartTimeH": "23",
        "chargeStartTimeM": "0",
        "chargeEndTimeH": "7",
        "chargeEndTimeM": "0",
        "dischargeStartTimeH": "7",
        "dischargeStartTimeM": "0",
        "dischargeEndTimeH": "23",
        "dischargeEndTimeM": "0",
        "agreementAmpere": "50",
        "onPowerOutageMode": "1",
        "onPowerOutageChargePowerW": "65535",
    }


class _CandidateClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def candidate_map(self, field: str, _path: str) -> dict[str, str]:
        self.calls.append(field)
        if field == "BatteryOperatingMode":
            return {
                "0": "economy",
                "1": "green",
                "3": "forced charge",
                "5": "standby",
            }
        if field == "SocEconomyMode":
            return {"0": "0%", "10": "10%", "20": "20%"}
        raise AssertionError(f"unexpected candidate fetch: {field}")


def test_forced_probe_fetches_only_battery_operating_mode() -> None:
    client = _CandidateClient()

    maps = _forced_probe_candidate_maps(client)

    assert client.calls == ["BatteryOperatingMode"]
    assert maps["SocChargeMode"] == {}
    assert maps["SocEconomyMode"] == {}


def test_economy_probe_fetches_only_mode_and_economy_soc() -> None:
    client = _CandidateClient()

    maps = _economy_probe_candidate_maps(client)

    assert client.calls == ["BatteryOperatingMode", "SocEconomyMode"]
    assert maps["SocChargeMode"] == {}
    assert maps["SocEconomyMode"]["0"] == "0%"


def test_probe_profiles_preserve_unrelated_settings() -> None:
    current = _current()
    base = profile_from_current_settings(current)
    client = _CandidateClient()

    forced = make_forced_probe_profile(
        current_profile=base,
        value_maps=_forced_probe_candidate_maps(client),
    )
    assert forced.battery_operating_mode == "3"
    assert forced.soc_charge_mode == current["socChargeMode"]
    assert forced.soc_economy_mode == current["socEconomyMode"]

    forced_snapshot = dict(current)
    forced_snapshot["batteryOperatingMode"] = "3"
    economy_base = profile_from_current_settings(forced_snapshot)
    economy = make_economy_probe_profile(
        current_profile=economy_base,
        value_maps=_economy_probe_candidate_maps(client),
    )
    assert economy.battery_operating_mode == "0"
    assert economy.soc_economy_mode == "0"
    assert economy.soc_charge_mode == current["socChargeMode"]
    assert economy.soc_safety_mode == current["socSafetyMode"]


def test_live_roundtrip_requires_an_exact_one_minute_hold() -> None:
    from app.kpnet.settings_roundtrip import run_settings_roundtrip

    with pytest.raises(ValueError, match="exactly 60"):
        run_settings_roundtrip(hold_seconds=59)


def test_live_roundtrip_rejects_window_mutation() -> None:
    from app.kpnet.settings_roundtrip import run_settings_roundtrip

    with pytest.raises(ValueError, match="must not alter"):
        run_settings_roundtrip(test_charge_start_hhmm="23:59")


def test_live_roundtrip_proves_forced_then_economy_then_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.kpnet.settings_roundtrip as roundtrip

    initial = _current()
    current = dict(initial)
    candidate_calls: list[str] = []
    mutation_payloads: list[dict[str, str]] = []

    class FakeClient:
        csrf_setting = "csrf"
        pcsid = "pcsid"
        operation_id = "op-0"

        def __init__(self, _cfg: object) -> None:
            self.pending: dict[str, str] = {}

        def login(self) -> None:
            pass

        def open_settings_page(self) -> None:
            pass

        def read_current_settings(self) -> dict[str, str]:
            return dict(current)

        def candidate_map(self, field: str, _path: str) -> dict[str, str]:
            candidate_calls.append(field)
            if field == "BatteryOperatingMode":
                return {
                    "0": "economy",
                    "1": "green",
                    "3": "forced charge",
                    "5": "standby",
                }
            if field == "SocEconomyMode":
                return {"0": "0%", "10": "10%", "20": "20%"}
            raise AssertionError(f"unexpected candidate fetch: {field}")

        def confirm_setting(
            self, payload: dict[str, str]
        ) -> tuple[bool, str, str, str]:
            self.pending = dict(payload)
            return True, "ok", "", "<form></form>"

        def write_setting(self, _confirm_html: str) -> dict[str, Any]:
            mutation_payloads.append(dict(self.pending))
            for key, value in self.pending.items():
                if key in current:
                    current[key] = str(value)
            self.operation_id = f"op-{len(mutation_payloads)}"
            return {"changed": True}

        def close(self) -> None:
            pass

        def logout(self) -> None:
            pass

    monkeypatch.setattr(
        roundtrip.KpNetConfig,
        "from_env",
        lambda: type("Cfg", (), {"dry_run": False})(),
    )
    monkeypatch.setattr(roundtrip, "KpNetClient", FakeClient)
    monkeypatch.setattr(roundtrip.time, "sleep", lambda _seconds: None)

    summary = roundtrip.run_settings_roundtrip()

    assert summary["status"] == "passed"
    assert summary["forced_proof"] == "passed"
    assert summary["economy_proof"] == "passed"
    assert summary["restore_verified"] is True
    assert summary["forced_changed_fields"] == ["batteryOperatingMode"]
    assert summary["economy_changed_fields"] == [
        "batteryOperatingMode",
        "socEconomyMode",
    ]
    assert summary["economy_readback_fields"] == [
        "batteryOperatingMode",
        "socEconomyMode",
    ]
    assert candidate_calls == [
        "BatteryOperatingMode",
        "BatteryOperatingMode",
        "SocEconomyMode",
    ]
    assert len(mutation_payloads) == 3
    assert current == initial


def test_unknown_forced_write_never_issues_economy_or_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.kpnet.settings_roundtrip as roundtrip

    initial = _current()
    read_count = 0
    apply_count = 0

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
            nonlocal read_count
            read_count += 1
            observed = dict(initial)
            if read_count > 1:
                observed["batteryOperatingMode"] = "3"
            return observed

        def candidate_map(self, field: str, _path: str) -> dict[str, str]:
            if field != "BatteryOperatingMode":
                raise AssertionError(f"unexpected candidate fetch after UNKNOWN: {field}")
            return {
                "0": "economy",
                "1": "green",
                "3": "forced charge",
                "5": "standby",
            }

        def close(self) -> None:
            pass

        def logout(self) -> None:
            pass

    def fake_apply(**_kwargs: object) -> tuple[dict[str, Any], list[str], dict[str, str]]:
        nonlocal apply_count
        apply_count += 1
        raise roundtrip.KpNetUnknownWriteError("ambiguous provider write")

    monkeypatch.setattr(
        roundtrip.KpNetConfig,
        "from_env",
        lambda: type("Cfg", (), {"dry_run": False})(),
    )
    monkeypatch.setattr(roundtrip, "KpNetClient", FakeClient)
    monkeypatch.setattr(roundtrip, "_apply_and_verify", fake_apply)

    with pytest.raises(roundtrip.SettingsRoundtripError) as raised:
        roundtrip.run_settings_roundtrip()

    assert apply_count == 1
    assert raised.value.summary["failed_phase"] == "forced_write"
    assert raised.value.summary["mutation_outcome"] == "unknown"
    assert raised.value.summary["restore_after_failure"] == "suppressed_unknown_write"
    assert raised.value.summary["economy_proof"] == "not_started"


def test_roundtrip_emits_restore_and_dual_proof_audit_fields() -> None:
    import app.kpnet.settings_roundtrip as roundtrip

    source = roundtrip.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()

    assert '"forced_proof"' in text
    assert '"economy_proof"' in text
    assert '"restore_verified": True' in text
    assert "[settings_roundtrip]" in text
