from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import time as time_module
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.kpnet.config import KpNetConfig
from app.kpnet import workflow
from app.runtime import cloud_job
from app.runtime.night_soc_time_contract import may_start_03_io, may_start_final_standby, must_stop_forced_monitoring
from app.runtime.soc_reading import SocReading
from app.runtime.slot_orchestration import _run_day_07, _run_night_23


class _Clock:
    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def monotonic(self) -> float:
        return self.value

    def time(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class _Response:
    def __init__(self, *, text: str = "", payload: dict[str, Any] | None = None) -> None:
        self.text = text
        self._payload = payload or {}
        self.headers: dict[str, str] = {}
        self.content = b""

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _Session:
    def __init__(self, clock: _Clock, *, request_seconds: float = 1.0) -> None:
        self.headers: dict[str, str] = {}
        self.clock = clock
        self.request_seconds = request_seconds
        self.requests: list[tuple[float, str, str, float]] = []
        self.current = _current_settings()
        self.confirmed: dict[str, str] = {}
        self.confirm_payloads: list[dict[str, str]] = []

    def get(self, url: str, *, timeout: float, **_kwargs: Any) -> _Response:
        return self._request("GET", url, None, timeout)

    def post(self, url: str, *, data: dict[str, Any] | None = None, timeout: float, **_kwargs: Any) -> _Response:
        return self._request("POST", url, data, timeout)

    def _request(self, method: str, url: str, data: dict[str, Any] | None, timeout: float) -> _Response:
        at = self.clock.monotonic()
        self.requests.append((at, method, url, timeout))
        self.clock.sleep(self.request_seconds)
        if url.endswith("/login"):
            return _Response(text='<input name="_csrf" value="top">')
        if url.endswith("/simplevisualization/enduser"):
            return _Response(text='<title>top</title><input name="_csrf" value="top">')
        if url.endswith("/gwpcsmanage"):
            return _Response(text='<form action="/settingcontrol/remotesetting/pcsselect/pcs"><button name="pcsid" value="pcs"></button></form>')
        if url.endswith("/pcssetting"):
            return _Response(text='<input name="_csrf" value="settings">')
        if url.endswith("/read/request") or url.endswith("/read/request/candidate") or url.endswith("/write/request"):
            return _Response(payload={"data": {"communicationSequenceno": "sequence", "value": "value"}})
        if "/read/response" in url or "/write/response" in url:
            return _Response(payload={"status": 1, "data": self.current})
        if "/valueList/" in url:
            return _Response(payload={"data": [{"code": "0", "value": "economy"}, {"code": "1", "value": "green"}, {"code": "3", "value": "forced"}, {"code": "5", "value": "standby"}]})
        if url.endswith("/batterysetting"):
            self.confirmed = {str(key): str(value) for key, value in (data or {}).items()}
            self.confirm_payloads.append(self.confirmed)
            return _Response(text=_confirmation_form(self.confirmed))
        if url.endswith("/pcssettingcomplete/"):
            self.current.update(self.confirmed)
        return _Response()


def _current_settings() -> dict[str, str]:
    return {
        "batteryOperatingMode": "1", "socSafetyMode": "0", "socEconomyMode": "0", "socContactInput": "0", "socChargeMode": "0",
        "chargeStartTimeH": "23", "chargeStartTimeM": "0", "chargeEndTimeH": "7", "chargeEndTimeM": "0",
        "dischargeStartTimeH": "7", "dischargeStartTimeM": "0", "dischargeEndTimeH": "23", "dischargeEndTimeM": "0",
        "agreementAmpere": "50", "onPowerOutageMode": "1", "onPowerOutageChargePowerW": "65535",
    }


def _confirmation_form(values: dict[str, str]) -> str:
    inputs = "".join(f'<input name="{key}" value="{value}">' for key, value in values.items())
    return f'<form id="itemForm">{inputs}<input name="_csrf" value="confirm"></form><button id="pcs-input-complete"></button>'


@pytest.fixture
def real_mode_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[_Clock, _Session, KpNetConfig]:
    cfg = replace(_config(tmp_path), dry_run=False)
    clock = _Clock()
    session = _Session(clock)
    monkeypatch.setattr(workflow.KpNetConfig, "from_env", staticmethod(lambda: cfg))
    monkeypatch.setattr(workflow, "load_dotenv_if_present", lambda: None)
    monkeypatch.setattr(workflow, "_setup_logging", lambda: None)
    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)
    monkeypatch.setattr(time_module, "time", clock.time)
    monkeypatch.setattr(time_module, "sleep", clock.sleep)
    monkeypatch.setattr("app.kpnet.client.requests.Session", lambda: session)
    return clock, session, cfg


def _config(tmp_path: Path) -> KpNetConfig:
    return KpNetConfig(
        base_url="https://example.test/settingcontrol", username="user", password="pass", dry_run=False, timeout_sec=60.0,
        csv_output_format="x", csv_aggr_type="x", csv_target_months=[], download_latest_month=False, workflow_mode="settings",
        settings_sequence="forced-only", force_settings_profile="standby", dynamic_forced_profile=False, dynamic_mode_switch_by_time=False,
        night_plan_path=tmp_path / "plan.json", default_charge_power_kw=1.8, green_mode_max_charge_percent=50.0,
        night_charge_window_start="23:00", night_charge_window_end="07:00", day_discharge_window_start="07:00", day_discharge_window_end="23:00",
        operation_conditions_path=tmp_path / "operation_conditions.json", timezone_name="Asia/Tokyo", use_har_credentials=False,
        har_path=tmp_path / "none.har", artifacts_dir=tmp_path, enforce_https=True, allowed_hosts=["example.test"],
    )


@pytest.mark.parametrize("remaining, allowed", [(299.0, False), (300.0, True)])
def test_mode_only_real_client_requires_full_io_and_release_budget(real_mode_only: tuple[_Clock, _Session, KpNetConfig], remaining: float, allowed: bool) -> None:
    clock, session, _cfg = real_mode_only
    if not allowed:
        with pytest.raises(TimeoutError, match="240s I/O plus 60s"):
            workflow.run_kpnet_mode_only_profile(profile="standby", deadline_monotonic=remaining)
        assert session.requests == []
        return
    assert workflow.run_kpnet_mode_only_profile(profile="standby", deadline_monotonic=remaining) == 0
    assert session.current["batteryOperatingMode"] == "5"
    assert any(path.endswith("/write/request") for _at, _method, path, _timeout in session.requests)
    assert all(at < 240.0 for at, _method, path, _timeout in session.requests if not path.endswith("/logout"))
    logout = [at for at, _method, path, _timeout in session.requests if path.endswith("/logout")]
    assert len(logout) == 1
    assert 0.0 < logout[0] < 240.0
    assert logout[0] <= remaining
    assert all(timeout > 0 for _at, _method, _path, timeout in session.requests)


def test_mode_only_real_client_stops_requests_at_io_deadline_and_only_releases_in_tail(real_mode_only: tuple[_Clock, _Session, KpNetConfig]) -> None:
    _clock, session, _cfg = real_mode_only
    session.request_seconds = 80.0
    assert workflow.run_kpnet_mode_only_profile(profile="green", deadline_monotonic=300.0) == 1
    assert session.current["batteryOperatingMode"] == "1"
    assert all(at < 240.0 for at, _method, path, _timeout in session.requests if not path.endswith("/logout"))
    assert [at for at, _method, path, _timeout in session.requests if path.endswith("/logout")] == [240.0]


def test_mode_only_operation_cap_is_independent_of_large_hard_cutoff(real_mode_only: tuple[_Clock, _Session, KpNetConfig]) -> None:
    clock, session, _cfg = real_mode_only
    assert workflow.run_kpnet_mode_only_profile(profile="green", deadline_monotonic=14_100.0) == 0
    assert all(at < 240.0 for at, _method, path, _timeout in session.requests if not path.endswith("/logout"))
    logout = [at for at, _method, path, _timeout in session.requests if path.endswith("/logout")]
    assert len(logout) == 1
    assert logout[0] < 300.0
    assert clock.monotonic() <= 300.0


@pytest.mark.parametrize(
    ("hour", "minute", "second", "io_allowed", "monitor_stopped", "standby_allowed"),
    [
        (6, 44, 59, True, False, True), (6, 45, 0, True, True, True),
        (6, 49, 59, True, True, True), (6, 50, 0, True, True, False),
        (6, 54, 59, True, True, False), (6, 55, 0, False, True, False),
        (7, 0, 0, False, True, False),
    ],
)
def test_03_wall_clock_fences_are_exact(
    hour: int, minute: int, second: int, io_allowed: bool, monitor_stopped: bool, standby_allowed: bool,
) -> None:
    now = datetime(2099, 1, 1, hour, minute, second, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert may_start_03_io(now) is io_allowed
    assert must_stop_forced_monitoring(now) is monitor_stopped
    assert may_start_final_standby(now) is standby_allowed


def test_slot23_07_and_mode_only_ignore_plan_firestore_manual_and_lease_failures(
    monkeypatch: pytest.MonkeyPatch, real_mode_only: tuple[_Clock, _Session, KpNetConfig],
) -> None:
    _clock, session, _cfg = real_mode_only
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("cross-slot dependency was touched")

    for name in ("_night_plan_path", "_ensure_night_plan_available", "_run_csv_with_retry", "_before_03_external_io"):
        monkeypatch.setattr(cloud_job, name, forbidden)
    for name in ("load_night_charge_plan", "_load_operation_conditions"):
        monkeypatch.setattr(workflow, name, forbidden)
    monkeypatch.setattr(cloud_job, "run_kpnet_mode_only_profile", workflow.run_kpnet_mode_only_profile)
    _run_night_23(); _run_day_07()
    assert [payload["batteryOperatingMode"] for payload in session.confirm_payloads] == ["5", "1"]
    assert len([path for _at, _method, path, _timeout in session.requests if path.endswith("/write/request")]) == 2
    assert len([path for _at, _method, path, _timeout in session.requests if path.endswith("/read/request")]) == 4


def test_03_readback_failure_still_leaves_07_green_independent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class AtThree:
        def now(self, timezone: ZoneInfo) -> datetime:
            return datetime(2099, 1, 1, 3, tzinfo=timezone)

        def monotonic_seconds(self) -> float:
            return 0.0

        def sleep(self, _seconds: int) -> None:
            return None

    class FailedForcedDevice:
        def read_soc(self, _paths: list[Path]) -> SocReading:
            return SocReading(20.0, "fake", None, datetime(2099, 1, 1, tzinfo=ZoneInfo("Asia/Tokyo")))

        def apply_profile(self, *, profile: str, **_kwargs: Any) -> None:
            if profile == "forced":
                raise RuntimeError("forced readback mismatch")

    writes: list[str] = []
    monkeypatch.setattr(cloud_job, "_run_settings_profile_with_retry", lambda *, profile, **_kwargs: writes.append(profile))
    plan = tmp_path / "plan.json"; plan.write_text('{"result":{"target_soc_7_percent":80,"required_night_charge_kwh":1,"effective_capacity_kwh":10}}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="forced"):
        cloud_job._monitor_partial_forced_and_stop(plan, clock=AtThree(), device_port=FailedForcedDevice())
    _run_day_07()
    assert writes == ["green"]
