from __future__ import annotations

from datetime import datetime
import time as time_module
from zoneinfo import ZoneInfo

import pytest

from app.runtime import cloud_job
from app.runtime.soc_reading import latest_realtime_soc_percent, read_soc_with_fallback


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
        def get(self, _url: str, *, timeout: float) -> None:
            session_requests.append(("get", timeout))

    class Client:
        def __init__(self, _cfg: object, *, deadline_monotonic: float) -> None:
            client_deadlines.append(deadline_monotonic)
            self.deadline_monotonic = deadline_monotonic
            self.session = Session()

        def login(self) -> None:
            return None

        def read_realtime_soc_percent(self) -> float:
            remaining = self.deadline_monotonic - clock.monotonic()
            if remaining <= 0:
                raise TimeoutError("fake deadline expired before request")
            self.session.get("https://fake/realtime", timeout=min(30.0, remaining))
            return 42.0

        def logout(self) -> None:
            return None

    monkeypatch.setattr("app.kpnet.client.KpNetClient", Client)
    monkeypatch.setattr("app.kpnet.workflow.KpNetConfig.from_env", staticmethod(lambda: object()))

    reading = cloud_job._RunnerMonitorDevicePort().read_soc([])

    assert reading.value_percent == 42.0
    assert reading.source == "realtime"
    assert client_deadlines == [60.0]
    assert session_requests == [("get", 30.0)]


def test_runner_064459_skips_realtime_and_uses_csv_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    client_created: list[object] = []
    now = datetime(2099, 1, 1, 6, 44, 59, tzinfo=JST)
    monkeypatch.setattr(cloud_job, "_tokyo_now", lambda: now)
    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)

    class Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            client_created.append(self)

    monkeypatch.setattr("app.kpnet.client.KpNetClient", Client)
    monkeypatch.setattr(cloud_job, "latest_csv_soc_reading", lambda _paths: (35.0, datetime.now(JST).replace(tzinfo=None)))

    reading = cloud_job._RunnerMonitorDevicePort().read_soc([])

    assert reading.value_percent == 35.0
    assert reading.source == "csv"
    assert client_created == []


def test_soc_retry_sleep_is_clamped_to_monitor_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    calls: list[str] = []
    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)

    def realtime() -> float | None:
        calls.append("realtime")
        raise RuntimeError("offline")

    result = read_soc_with_fallback(
        [],
        latest_realtime=realtime,
        latest_csv=lambda _paths: (None, None),
        env_int=lambda _name, default: 3 if "ATTEMPTS" in _name else default,
        env_float=lambda _name, default: 10.0,
        sleep=clock.sleep,
        deadline_monotonic=1.0,
    )

    assert calls == ["realtime"]
    assert clock.sleeps == [1.0]
    assert result.source == "unavailable"
    assert "SOC deadline expired" in (result.error or "")


def test_soc_realtime_uses_the_given_deadline_without_a_second_60_second_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    calls: list[str] = []
    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)

    def realtime() -> float | None:
        calls.append("realtime")
        clock.value += 60.0
        raise RuntimeError("offline")

    result = read_soc_with_fallback(
        [], latest_realtime=realtime, latest_csv=lambda _paths: (None, None),
        env_int=lambda _name, default: 3 if "ATTEMPTS" in _name else default,
        env_float=lambda _name, default: 10.0, sleep=clock.sleep, deadline_monotonic=10_000.0,
    )

    assert calls == ["realtime", "realtime", "realtime"]
    assert clock.sleeps == [10.0, 10.0]
    assert "offline" in (result.error or "")


def test_latest_realtime_client_receives_the_given_absolute_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    deadlines: list[float] = []

    class Client:
        def __init__(self, _cfg: object, *, deadline_monotonic: float) -> None:
            deadlines.append(deadline_monotonic)

        def login(self) -> None:
            return None

        def read_realtime_soc_percent(self) -> float:
            return 42.0

        def logout(self) -> None:
            return None

    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)
    monkeypatch.setattr("app.kpnet.client.KpNetClient", Client)
    monkeypatch.setattr("app.kpnet.workflow.KpNetConfig.from_env", staticmethod(lambda: object()))

    assert latest_realtime_soc_percent(deadline_monotonic=10_000.0) == 42.0
    assert deadlines == [10_000.0]


def test_expired_deadline_starts_no_realtime_retry_sleep_or_request(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    requests: list[str] = []
    monkeypatch.setattr(time_module, "monotonic", clock.monotonic)

    def realtime() -> float | None:
        requests.append("request")
        return 42.0

    result = read_soc_with_fallback(
        [],
        latest_realtime=realtime,
        latest_csv=lambda _paths: (None, None),
        env_int=lambda _name, default: 3 if "ATTEMPTS" in _name else default,
        env_float=lambda _name, default: 2.0,
        sleep=clock.sleep,
        deadline_monotonic=0.0,
    )

    assert result.source == "unavailable"
    assert requests == []
    assert clock.sleeps == []
