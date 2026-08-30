from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.runtime.cloud_job import _monitor_partial_forced_and_stop
from app.runtime.soc_reading import SocReading
from app.runtime.slot_orchestration import _run_day_07, _run_night_23
from app.runtime.slot_orchestration import _run_adjust_03

JST = ZoneInfo("Asia/Tokyo")


class _Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at
        self.elapsed = 0.0

    def now(self, _timezone: ZoneInfo) -> datetime:
        return self.at

    def monotonic_seconds(self) -> float:
        return self.elapsed

    def sleep(self, seconds: int) -> None:
        self.elapsed += seconds
        self.at += timedelta(seconds=seconds)


class _Device:
    def __init__(self, soc: list[float | None], *, fail_forced: bool = False) -> None:
        self.soc = iter(soc)
        self.fail_forced = fail_forced
        self.calls: list[str] = []
        self.soc_read_count = 0

    def read_soc(self, _paths: list[Path]) -> SocReading:
        self.soc_read_count += 1
        return SocReading(next(self.soc), "fake", None, datetime(2099, 1, 1, tzinfo=JST))

    def apply_profile(self, *, profile: str, dynamic_forced_profile: bool, label: str) -> None:
        self.calls.append(profile)
        if profile == "forced" and self.fail_forced:
            raise RuntimeError("KP-NET forced readback mismatch")


def _plan(path: Path, target: float, required_kwh: float = 1.0) -> Path:
    path.write_text(json.dumps({"forecast": {"date": "2099-01-01"}, "result": {
        "target_soc_7_percent": target, "required_night_charge_kwh": required_kwh,
        "effective_capacity_kwh": 10.0}}), encoding="utf-8")
    return path


@pytest.mark.parametrize("target", [0, 30, 50, 80, 100])
def test_03_targets_are_continuous(tmp_path: Path, target: float) -> None:
    device = _Device([0.0, float(target)])
    _monitor_partial_forced_and_stop(
        _plan(tmp_path / "plan.json", target), clock=_Clock(datetime(2099, 1, 1, 3, tzinfo=JST)), device_port=device
    )
    assert device.calls == ["forced", "standby"]


def test_03_target_100_does_not_stop_at_93_or_99_even_with_legacy_margin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT", "7")
    device = _Device([93.0, 99.0, 100.0])
    _monitor_partial_forced_and_stop(
        _plan(tmp_path / "plan.json", 100), clock=_Clock(datetime(2099, 1, 1, 3, tzinfo=JST)), device_port=device
    )
    assert device.calls.count("forced") == 1
    assert device.calls.count("standby") == 1
    assert device.soc_read_count == 3


def test_03_target_80_does_not_stop_at_79_with_legacy_margin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT", "7")
    device = _Device([79.0, 80.0])
    _monitor_partial_forced_and_stop(
        _plan(tmp_path / "plan.json", 80), clock=_Clock(datetime(2099, 1, 1, 3, tzinfo=JST)), device_port=device
    )
    assert device.calls.count("forced") == 1
    assert device.calls.count("standby") == 1
    assert device.soc_read_count == 2


def test_03_target_stop_log_records_target_source_and_reason(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    device = _Device([100.0])
    _monitor_partial_forced_and_stop(
        _plan(tmp_path / "plan.json", 100), clock=_Clock(datetime(2099, 1, 1, 3, tzinfo=JST)), device_port=device
    )
    stdout = capsys.readouterr().out
    assert "exact_target_stop=true" in stdout
    assert "target=100.00%" in stdout
    assert "source=fake" in stdout
    assert "reason=target_reached" in stdout
    assert "latest=100.00%" in stdout


def test_03_mismatch_is_not_reapplied_and_does_not_gate_07(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    device = _Device([20.0], fail_forced=True)
    with pytest.raises(RuntimeError, match="readback mismatch"):
        _monitor_partial_forced_and_stop(
            _plan(tmp_path / "plan.json", 80), clock=_Clock(datetime(2099, 1, 1, 3, tzinfo=JST)), device_port=device
        )
    assert device.calls == ["forced", "standby"]
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("app.runtime.cloud_job._run_settings_profile_with_retry", lambda **kwargs: calls.append(kwargs))
    _run_day_07()
    assert calls == [{"profile": "green", "dynamic_forced_profile": False, "label": "07-green"}]


@pytest.mark.parametrize("at, expected", [(datetime(2099, 1, 1, 6, 54, 59, tzinfo=JST), []), (datetime(2099, 1, 1, 6, 55, tzinfo=JST), [])])
def test_03_hard_fence_controls_all_device_io(tmp_path: Path, at: datetime, expected: list[str]) -> None:
    device = _Device([90.0])
    _monitor_partial_forced_and_stop(_plan(tmp_path / "plan.json", 80), clock=_Clock(at), device_port=device)
    assert device.calls == expected


def test_slot23_and_07_are_one_unconditional_profile_write(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("app.runtime.cloud_job._run_settings_profile_with_retry", lambda **kwargs: calls.append(kwargs))
    _run_night_23(); _run_day_07()
    assert [call["profile"] for call in calls] == ["standby", "green"]


def test_03_prep_failure_standby_then_independent_07_green(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    writes: list[dict[str, object]] = []
    monkeypatch.setattr("app.runtime.cloud_job._night_plan_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr("app.runtime.cloud_job._before_03_external_io", lambda: None)
    monkeypatch.setattr("app.runtime.cloud_job._run_csv_with_retry", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("csv failed")))
    monkeypatch.setattr("app.runtime.cloud_job._run_03_prep_fail_safe_standby", lambda: writes.append({"profile": "standby"}))
    monkeypatch.setattr("app.runtime.cloud_job._run_settings_profile_with_retry", lambda **kwargs: writes.append(kwargs))

    _run_adjust_03(); _run_day_07()

    assert [call["profile"] for call in writes] == ["standby", "green"]


def test_slot07_has_no_cross_slot_import_or_call() -> None:
    source = Path("app/runtime/slot_orchestration.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_day_07")
    body = ast.unparse(fn)
    for forbidden in ("firestore", "plan", "lease", "owner", "manual", "soc", "terminal"):
        assert forbidden not in body.lower()


def test_deploy_job03_time_ownership_semantics() -> None:
    source = Path("scripts/deploy_gcp_jobs.ps1").read_text(encoding="utf-8")
    line = next(value for value in source.splitlines() if "run jobs deploy $Job03Name" in value)
    assert "--task-timeout 14100" in line and "--max-retries 0" in line
    assert "ADJUST03_FORCE_MONITOR_CUTOFF_HHMM" not in line
