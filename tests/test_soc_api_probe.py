from dataclasses import replace
from datetime import datetime

import pytest

from app.kpnet.soc_api import SocApiError
from app.runtime.soc_reading import SocReading
from scripts import kpnet_soc_api_probe as probe


@pytest.fixture
def bounded_clock(monkeypatch):
    seconds = [0.0]
    sleeps = []
    retry = probe.read_realtime_soc_with_retry
    monkeypatch.setattr(probe.time, "monotonic", lambda: seconds[0])

    def sleep(delay):
        sleeps.append(delay)
        seconds[0] += delay

    def run_retry(**kwargs):
        return retry(sleep=sleep, **kwargs)

    monkeypatch.setattr(probe, "read_realtime_soc_with_retry", run_retry)
    return seconds, sleeps


def fresh_reading():
    measured = datetime.fromisoformat("2026-10-13T23:45:33+09:00")
    retrieved = datetime.fromisoformat("2026-10-13T23:45:40+09:00")
    return SocReading(14, "realtime", None, measured, retrieved)


@pytest.mark.parametrize("failures", [0, 1, 2])
def test_probe_recovers_with_one_shared_deadline(bounded_clock, monkeypatch, failures, capsys):
    seconds, sleeps = bounded_clock
    calls = []

    def read(*, deadline_monotonic):
        calls.append((seconds[0], deadline_monotonic))
        if len(calls) <= failures:
            raise SocApiError("private-provider-detail")
        return fresh_reading()

    monkeypatch.setattr(probe, "latest_realtime_soc_reading", read)
    result = probe.run_probe()
    assert result["status"] == "passed"
    assert result["soc_percent"] == 14
    assert result["device_measured_at"] == "2026-10-13T23:45:33+09:00"
    assert result["retrieved_at"] == "2026-10-13T23:45:40+09:00"
    assert len(calls) == failures + 1
    assert all(deadline == 60 for _, deadline in calls)
    assert all(start < deadline for start, deadline in calls)
    assert len(sleeps) == failures
    assert seconds[0] < 60
    assert "private-provider-detail" not in capsys.readouterr().out


@pytest.mark.parametrize("consume_budget", [False, True])
def test_probe_exhaustion_remains_failure(bounded_clock, monkeypatch, consume_budget, capsys):
    seconds, sleeps = bounded_clock
    calls = []

    def unavailable(*, deadline_monotonic):
        calls.append(deadline_monotonic)
        if consume_budget:
            seconds[0] = deadline_monotonic
        raise SocApiError("private-provider-detail")

    monkeypatch.setattr(probe, "latest_realtime_soc_reading", unavailable)
    with pytest.raises(RuntimeError, match="no timestamped measurement"):
        probe.run_probe()
    assert calls == ([60] if consume_budget else [60, 60, 60])
    assert len(sleeps) == (0 if consume_budget else 2)
    assert seconds[0] <= 60
    output = capsys.readouterr().out
    assert '"status": "passed"' not in output
    assert "private-provider-detail" not in output


@pytest.mark.parametrize("missing", ["value_percent", "observed_at", "retrieved_at"])
def test_probe_incomplete_measurement_is_not_accepted(bounded_clock, monkeypatch, missing):
    reading = replace(fresh_reading(), **{missing: None})
    monkeypatch.setattr(probe, "latest_realtime_soc_reading", lambda **kwargs: reading)
    with pytest.raises(RuntimeError, match="no timestamped measurement"):
        probe.run_probe()
