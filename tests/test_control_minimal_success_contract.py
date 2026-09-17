from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import requests

import app.kpnet.settings_roundtrip as settings_roundtrip
import app.kpnet.workflow as workflow
import app.runtime.forced_charge_monitor as forced_charge_monitor
from app.kpnet.client import KpNetClient


def test_apply_settings_profile_verifies_only_fields_changed_by_this_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    payload = {
        "batteryOperatingMode": "3",
        "socChargeMode": "50",
        # Full provider forms contain unrelated values too. This one is
        # intentionally different in readback and must not fail this SET.
        "chargeStartTimeH": "23",
    }
    monkeypatch.setattr(
        workflow,
        "_build_payload",
        lambda **_kwargs: (payload, ["batteryOperatingMode", "socChargeMode"]),
    )

    class FakeClient:
        csrf_setting = "csrf"
        pcsid = "pcsid"
        deadline_monotonic = None
        operation_id = "operation"

        def confirm_setting(self, _payload: dict[str, str]) -> tuple[bool, str, str, str]:
            return True, "ok", "", "<form></form>"

        def write_setting(self, _confirm_html: str) -> dict[str, Any]:
            return {"changed": True}

        def read_current_settings(self) -> dict[str, Any]:
            return {
                "batteryOperatingMode": "3",
                "socChargeMode": "50",
                "chargeStartTimeH": "22",  # unrelated drift
            }

    summary: dict[str, Any] = {"setting_results": [], "night_soc": {}}
    result = workflow._apply_settings_profile(
        client=FakeClient(),
        cfg=SimpleNamespace(dry_run=False),
        run_dir=tmp_path,
        summary=summary,
        current={
            "batteryOperatingMode": "1",
            "socChargeMode": "0",
            "chargeStartTimeH": "22",
        },
        value_maps={},
        profile=SimpleNamespace(name="minimal-change"),
    )

    assert result["batteryOperatingMode"] == "3"
    assert result["socChargeMode"] == "50"
    setting_result = summary["setting_results"][0]
    assert setting_result["status"] == "applied"
    assert setting_result["readback_fields"] == ["batteryOperatingMode", "socChargeMode"]
    assert setting_result["readback_match"] is True
    assert setting_result["readback_mismatch_fields"] == []


def test_minimal_03_candidate_maps_fetch_only_required_candidates() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def candidate_map(self, candidate_type: str, _path: str) -> dict[str, str]:
            self.calls.append(candidate_type)
            return {"3": "forced", "50": "50%"}

    standby_client = FakeClient()
    standby_maps = workflow._minimal_03_candidate_maps(
        standby_client,
        include_soc_charge=False,
    )
    assert standby_client.calls == ["BatteryOperatingMode"]
    assert standby_maps["SocChargeMode"] == {}

    forced_client = FakeClient()
    forced_maps = workflow._minimal_03_candidate_maps(
        forced_client,
        include_soc_charge=True,
    )
    assert forced_client.calls == ["BatteryOperatingMode", "SocChargeMode"]
    assert forced_maps["SocChargeMode"]


def test_probe_candidate_maps_fetch_only_mode_and_soc_charge() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def candidate_map(self, candidate_type: str, _path: str) -> dict[str, str]:
            self.calls.append(candidate_type)
            return {"3": "強制充電", "50": "50%"}

    client = FakeClient()
    maps = settings_roundtrip._probe_candidate_maps(client)  # type: ignore[arg-type]

    assert client.calls == ["BatteryOperatingMode", "SocChargeMode"]
    assert maps["SocSafetyMode"] == {}
    assert maps["AgreementAmpere"] == {}


def test_roundtrip_mutation_readback_ignores_unrelated_field_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "batteryOperatingMode": "3",
        "socChargeMode": "50",
        "chargeStartTimeH": "23",
    }
    monkeypatch.setattr(
        settings_roundtrip,
        "_build_payload",
        lambda **_kwargs: (payload, ["batteryOperatingMode", "socChargeMode"]),
    )

    class FakeClient:
        csrf_setting = "csrf"
        pcsid = "pcsid"

        def confirm_setting(self, _payload: dict[str, str]) -> tuple[bool, str, str, str]:
            return True, "ok", "", "<form></form>"

        def write_setting(self, _confirm_html: str) -> dict[str, Any]:
            return {"changed": True}

        def read_current_settings(self) -> dict[str, Any]:
            return {
                "batteryOperatingMode": "3",
                "socChargeMode": "50",
                "chargeStartTimeH": "22",
            }

    readback, changed = settings_roundtrip._apply_and_verify(
        client=FakeClient(),  # type: ignore[arg-type]
        current={"batteryOperatingMode": "1", "socChargeMode": "0", "chargeStartTimeH": "22"},
        value_maps={},
        profile=SimpleNamespace(name="probe"),  # type: ignore[arg-type]
    )

    assert changed == ["batteryOperatingMode", "socChargeMode"]
    assert readback["chargeStartTimeH"] == "22"


def test_write_setting_ignores_post_write_housekeeping_failure() -> None:
    client = object.__new__(KpNetClient)
    client.operation_id = "operation"
    client.csrf_setting = "csrf"
    client._extract_form_data = lambda _html: ({"_csrf": "csrf"}, "csrf")  # type: ignore[method-assign]
    client._json_object = lambda _response, *, operation: {  # type: ignore[method-assign]
        "data": {"communicationSequenceno": "1", "value": "v"}
    }
    client._poll_json = lambda *_args, **_kwargs: {"status": 1}  # type: ignore[method-assign]
    emitted: list[dict[str, Any]] = []
    client._emit_http_event = lambda **kwargs: emitted.append(kwargs)  # type: ignore[method-assign]

    def fake_post(path: str, **_kwargs: Any) -> object:
        if path == "remotesetting/pcssetting/write/request":
            return object()
        raise requests.HTTPError("housekeeping failed")

    client._post = fake_post  # type: ignore[method-assign]

    result = client.write_setting("<form></form>")

    assert result["changed"] is True
    assert result["housekeeping"] == {
        "complete": "ignored:HTTPError",
        "device_detail": "ignored:HTTPError",
    }
    assert any(event.get("classification") == "ignored_housekeeping_failure" for event in emitted)


def test_charge_rate_estimation_failure_falls_back_instead_of_aborting_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_history(_paths: list[object]):
        raise RuntimeError("broken history")

    monkeypatch.setattr(forced_charge_monitor, "iter_charge_soc_points", broken_history)
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_RATE_FALLBACK_PERCENT_PER_HOUR", "35")
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_RATE_MIN_PERCENT_PER_HOUR", "25")
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_RATE_MAX_PERCENT_PER_HOUR", "50")

    result = forced_charge_monitor.estimate_forced_charge_rate_percent_per_hour([])

    assert result["percent_per_hour"] == 35.0
    assert result["sample_count"] == 0
    assert str(result["source"]).startswith("fallback-forced-charge-soc-rate:")


def test_invalid_optional_rate_tuning_uses_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_RATE_FALLBACK_PERCENT_PER_HOUR", "not-a-number")
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_RATE_MIN_PERCENT_PER_HOUR", "nan")
    monkeypatch.setenv("ADJUST03_FORCE_CHARGE_RATE_MAX_PERCENT_PER_HOUR", "inf")
    monkeypatch.setattr(forced_charge_monitor, "iter_charge_soc_points", lambda _paths: iter(()))

    result = forced_charge_monitor.estimate_forced_charge_rate_percent_per_hour([])

    assert result["percent_per_hour"] == 35.0
    assert result["source"] == "fallback-forced-charge-soc-rate"
