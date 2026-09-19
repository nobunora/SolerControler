from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import cloud_job_runner
from app.kpnet import workflow
from app.runtime.cloud_job import _monitor_partial_forced_and_stop
from app.runtime.kpnet_unknown_guard import (
    KpNetUnknownTaskTerminal,
    guard_unknown_write_terminal,
    install_unknown_write_guard,
)
from app.runtime.soc_reading import SocReading

JST = ZoneInfo("Asia/Tokyo")


class _Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self, _timezone: ZoneInfo) -> datetime:
        return self.at

    def monotonic_seconds(self) -> float:
        return 0.0

    def sleep(self, seconds: int) -> None:
        self.at += timedelta(seconds=seconds)


class _UnknownForcedDevice:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def read_soc(self, _paths: list[Path]) -> SocReading:
        return SocReading(20.0, "fake", None, datetime(2099, 1, 1, tzinfo=JST))

    def apply_profile(self, *, profile: str, dynamic_forced_profile: bool, label: str) -> None:
        del dynamic_forced_profile, label
        self.calls.append(profile)
        if profile == "forced":
            raise KpNetUnknownTaskTerminal("forced write remains unknown")


def _plan(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "forecast": {"date": "2099-01-01"},
                "result": {
                    "target_soc_7_percent": 80.0,
                    "required_night_charge_kwh": 1.0,
                    "effective_capacity_kwh": 10.0,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_guard_promotes_workflow_unknown_to_task_terminal() -> None:
    def original() -> None:
        raise workflow.KpNetUnknownWriteTerminal("still unknown")

    guarded = guard_unknown_write_terminal(original)

    with pytest.raises(KpNetUnknownTaskTerminal):
        guarded()


def test_guard_promotes_failure_after_write_started() -> None:
    class Client:
        def __init__(self) -> None:
            self.write_calls = 0

        def write_setting(self) -> dict[str, bool]:
            self.write_calls += 1
            return {"changed": True}

    client = Client()

    def original(*, client: Client) -> None:
        client.write_setting()
        raise RuntimeError("read-back mismatch")

    guarded = guard_unknown_write_terminal(original)

    with pytest.raises(KpNetUnknownTaskTerminal):
        guarded(client=client)
    assert client.write_calls == 1


def test_guard_keeps_failure_before_write_started_retryable() -> None:
    class Client:
        def __init__(self) -> None:
            self.write_calls = 0

        def write_setting(self) -> None:
            self.write_calls += 1

    client = Client()

    def original(*, client: Client) -> None:
        del client
        raise RuntimeError("pre-write failure")

    guarded = guard_unknown_write_terminal(original)

    with pytest.raises(RuntimeError, match="pre-write failure"):
        guarded(client=client)
    assert client.write_calls == 0


def test_task_terminal_intentionally_bypasses_exception_handlers() -> None:
    assert issubclass(KpNetUnknownTaskTerminal, BaseException)
    assert not issubclass(KpNetUnknownTaskTerminal, Exception)


def test_install_unknown_guard_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    def original(**_kwargs: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(workflow, "_apply_settings_profile", original)
    install_unknown_write_guard()
    once = workflow._apply_settings_profile
    install_unknown_write_guard()

    assert workflow._apply_settings_profile is once
    assert getattr(once, "__kpnet_unknown_task_guard__", False) is True


def test_03_unknown_forced_write_never_attempts_standby(tmp_path: Path) -> None:
    device = _UnknownForcedDevice()

    with pytest.raises(KpNetUnknownTaskTerminal):
        _monitor_partial_forced_and_stop(
            _plan(tmp_path / "plan.json"),
            clock=_Clock(datetime(2099, 1, 1, 3, tzinfo=JST)),
            device_port=device,
        )

    assert device.calls == ["forced"]


@pytest.mark.parametrize("slot", ["23", "03", "07"])
def test_runner_suppresses_platform_retry_only_for_unknown_terminal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    slot: str,
) -> None:
    monkeypatch.setattr(cloud_job_runner, "install_unknown_write_guard", lambda: None)

    def unknown_main() -> int:
        raise KpNetUnknownTaskTerminal("still unknown")

    monkeypatch.setattr(cloud_job_runner, "main", unknown_main)
    monkeypatch.setenv("CLOUD_JOB_SLOT", slot)

    assert cloud_job_runner._run_main() == 0
    record = json.loads(capsys.readouterr().out.strip())
    assert record == {
        "message": "kpnet-unknown-task-terminal",
        "slot": slot,
        "classification": "unknown",
        "platform_retry": "suppressed",
    }


def test_runner_fails_dedicated_probe_for_unknown_terminal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cloud_job_runner, "install_unknown_write_guard", lambda: None)
    monkeypatch.setenv("KP_NET_UNKNOWN_EXIT_ZERO", "false")

    def unknown_main() -> int:
        raise KpNetUnknownTaskTerminal("still unknown")

    monkeypatch.setattr(cloud_job_runner, "main", unknown_main)

    assert cloud_job_runner._run_main() == 1
    record = json.loads(capsys.readouterr().out.strip())
    assert record["classification"] == "unknown"
    assert record["platform_retry"] == "probe-failed"


def test_runner_does_not_suppress_normal_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cloud_job_runner, "install_unknown_write_guard", lambda: None)

    def failed_main() -> int:
        raise RuntimeError("pre-write failure")

    monkeypatch.setattr(cloud_job_runner, "main", failed_main)

    with pytest.raises(RuntimeError, match="pre-write failure"):
        cloud_job_runner._run_main()
