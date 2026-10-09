from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from pathlib import Path
import json
import time as time_module
from zoneinfo import ZoneInfo

import pytest

from app.runtime import cloud_job
from app.runtime.soc_reading import SocReading, latest_realtime_soc_reading, read_realtime_soc_with_retry


JST = ZoneInfo("Asia/Tokyo")


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def test_runner_allocates_one_deadline_and_threads_it_to_realtime_client(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    client_deadlines: list[float] = []
    session_requests: list[tuple[str, float]] = []
    monkeypatch.setattr(cloud_job, "_tokyo_now", lambda: datetime(2099, 1, 1, 3, 0, tzinfo=JST))
    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)

    class Session:
        def post(self, _url: str, *, timeout: float) -> None:
            session_requests.append(("post", timeout))

    class Client:
        def __init__(self, *, deadline_monotonic: float) -> None:
            client_deadlines.append(deadline_monotonic)
            self.deadline_monotonic = deadline_monotonic
            self.session = Session()

        def login(self) -> None:
            return None

        def read_soc(self):
            remaining = self.deadline_monotonic - clock.monotonic()
            if remaining <= 0:
                raise TimeoutError("fake deadline expired before request")
            self.session.post("https://fake/realtime", timeout=min(30.0, remaining))
            return SimpleNamespace(value_percent=42.0, measured_at=datetime(2099, 1, 1, 3, 0, tzinfo=JST), retrieved_at=datetime(2099, 1, 1, 3, 0, 5, tzinfo=JST))

        def close(self) -> None:
            return None

    monkeypatch.setattr("app.runtime.soc_reading.KpNetSocClient", Client)

    reading = cloud_job._RunnerMonitorDevicePort().read_soc([])

    assert reading.value_percent == 42.0
    assert reading.source == "realtime"
    assert client_deadlines == [60.0]
    assert session_requests == [("post", 30.0)]
    assert reading.observed_at == datetime(2099, 1, 1, 3, 0, tzinfo=JST)
    assert reading.retrieved_at == datetime(2099, 1, 1, 3, 0, 5, tzinfo=JST)


def test_runner_soc_path_never_uses_delayed_csv_when_realtime_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    monkeypatch.setattr(cloud_job, "_tokyo_now", lambda: datetime(2099, 1, 1, 3, 0, tzinfo=JST))
    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)
    monkeypatch.setenv("ADJUST03_REALTIME_SOC_RETRY_ATTEMPTS", "1")
    monkeypatch.setattr(cloud_job, "latest_realtime_soc_reading", lambda **_kwargs: None)

    def delayed_csv(*_args, **_kwargs):
        raise AssertionError("CSV must not be opened")

    monkeypatch.setattr(Path, "open", delayed_csv)

    reading = cloud_job._RunnerMonitorDevicePort().read_soc([])

    assert reading.value_percent is None
    assert reading.source == "unavailable"


def test_runner_064459_skips_realtime_without_using_delayed_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    client_created: list[object] = []
    now = datetime(2099, 1, 1, 6, 44, 59, tzinfo=JST)
    monkeypatch.setattr(cloud_job, "_tokyo_now", lambda: now)
    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)

    class Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            client_created.append(self)

    monkeypatch.setattr("app.runtime.soc_reading.KpNetSocClient", Client)
    def delayed_csv(*_args, **_kwargs):
        raise AssertionError("03 SOC control must not read delayed CSV")

    monkeypatch.setattr(Path, "open", delayed_csv)

    reading = cloud_job._RunnerMonitorDevicePort().read_soc([])

    assert reading.value_percent is None
    assert reading.source == "unavailable"
    assert client_created == []


def test_soc_retry_sleep_is_clamped_to_monitor_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    calls: list[str] = []
    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)

    def realtime() -> SocReading | None:
        calls.append("realtime")
        raise RuntimeError("offline")

    result = read_realtime_soc_with_retry(
        latest_realtime=realtime,
        env_int=lambda _name, default: 3 if "ATTEMPTS" in _name else default,
        env_float=lambda _name, default: 10.0,
        sleep=clock.sleep,
        deadline_monotonic=1.0,
    )

    assert calls == ["realtime", "realtime", "realtime"]
    assert len(clock.sleeps) == 2
    assert sum(clock.sleeps) < 1.0
    assert result.source == "unavailable"
    assert "RuntimeError" in (result.error or "")


def test_soc_default_retries_wait_five_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    calls: list[str] = []
    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)

    def realtime() -> SocReading | None:
        calls.append("realtime")
        return None

    result = read_realtime_soc_with_retry(
        latest_realtime=realtime,
        env_int=lambda _name, default: default,
        env_float=lambda _name, default: default,
        sleep=clock.sleep,
        deadline_monotonic=10_000.0,
    )

    assert calls == ["realtime", "realtime", "realtime"]
    assert clock.sleeps == [300.0, 300.0]
    assert result.source == "unavailable"


def test_soc_failed_read_attempts_emit_secret_free_structured_logs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = _Clock()
    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)

    result = read_realtime_soc_with_retry(
        latest_realtime=lambda: None,
        env_int=lambda _name, default: default,
        env_float=lambda _name, default: default,
        sleep=clock.sleep,
        deadline_monotonic=10_000.0,
    )

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    assert result.source == "unavailable"
    assert events == [
        {"message": "03-soc-read-attempt", "attempt": 1, "max_attempts": 3, "outcome": "no_value", "retry_delay_seconds": 300.0},
        {"message": "03-soc-read-attempt", "attempt": 2, "max_attempts": 3, "outcome": "no_value", "retry_delay_seconds": 300.0},
        {"message": "03-soc-read-attempt", "attempt": 3, "max_attempts": 3, "outcome": "no_value", "retry_delay_seconds": 0.0},
    ]


def test_soc_realtime_uses_the_given_deadline_without_a_second_60_second_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    calls: list[str] = []
    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)

    def realtime() -> SocReading | None:
        calls.append("realtime")
        clock.value += 60.0
        raise RuntimeError("offline")

    result = read_realtime_soc_with_retry(
        latest_realtime=realtime,
        env_int=lambda _name, default: 3 if "ATTEMPTS" in _name else default,
        env_float=lambda _name, default: 10.0, sleep=clock.sleep, deadline_monotonic=10_000.0,
    )

    assert calls == ["realtime", "realtime", "realtime"]
    assert clock.sleeps == [10.0, 10.0]
    assert "RuntimeError" in (result.error or "")


def test_latest_realtime_client_receives_the_given_absolute_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    deadlines: list[float] = []

    class Client:
        def __init__(self, *, deadline_monotonic: float) -> None:
            deadlines.append(deadline_monotonic)

        def login(self) -> None:
            return None

        def read_soc(self):
            return SimpleNamespace(value_percent=42.0, measured_at=datetime(2099, 1, 1, 3, 0, tzinfo=JST), retrieved_at=datetime(2099, 1, 1, 3, 0, 5, tzinfo=JST))

        def close(self) -> None:
            return None

    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)
    monkeypatch.setattr("app.runtime.soc_reading.KpNetSocClient", Client)

    assert latest_realtime_soc_reading(deadline_monotonic=10_000.0).value_percent == 42.0
    assert deadlines == [10_000.0]


def test_expired_deadline_starts_no_realtime_retry_sleep_or_request(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    requests: list[str] = []
    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)

    def realtime() -> SocReading | None:
        requests.append("request")
        return SocReading(42.0, "realtime", None, None)

    result = read_realtime_soc_with_retry(
        latest_realtime=realtime,
        env_int=lambda _name, default: 3 if "ATTEMPTS" in _name else default,
        env_float=lambda _name, default: 2.0,
        sleep=clock.sleep,
        deadline_monotonic=0.0,
    )

    assert result.source == "unavailable"
    assert requests == []
    assert clock.sleeps == []
