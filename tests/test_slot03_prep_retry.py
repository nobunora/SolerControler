from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.runtime import slot_orchestration


def test_missing_plan_after_csv_failure_attempts_standby_then_raises_for_platform_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path = tmp_path / "missing-plan.json"
    calls: list[str] = []

    def fake_cloud_call(name: str, *args: object, **kwargs: object) -> object:
        calls.append(name)
        if name == "_night_plan_path":
            return plan_path
        if name == "_before_03_external_io":
            return None
        if name == "_run_csv_with_retry":
            raise RuntimeError("csv unavailable")
        if name == "_run_03_prep_fail_safe_standby":
            return True
        raise AssertionError(f"unexpected cloud call: {name}")

    monkeypatch.setattr(slot_orchestration, "_cloud_call", fake_cloud_call)

    with pytest.raises(RuntimeError, match="csv unavailable"):
        slot_orchestration._run_adjust_03()

    assert calls == [
        "_night_plan_path",
        "_before_03_external_io",
        "_run_csv_with_retry",
        "_run_03_prep_fail_safe_standby",
    ]
    line = next(
        value
        for value in capsys.readouterr().out.splitlines()
        if '"message":"03-prep-terminal-audit"' in value
    )
    payload = json.loads(line)
    assert payload["usable_plan_exists"] is False
    assert payload["standby_attempted"] is True
    assert payload["standby_outcome"] == "success"
    assert payload["platform_retry"] == "eligible"


def test_existing_local_plan_can_still_continue_after_csv_prep_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "night_charge_plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    calls: list[str] = []

    def fake_cloud_call(name: str, *args: object, **kwargs: object) -> object:
        calls.append(name)
        if name == "_night_plan_path":
            return plan_path
        if name == "_before_03_external_io":
            return None
        if name == "_run_csv_with_retry":
            raise RuntimeError("csv unavailable")
        if name == "_monitor_partial_forced_and_stop":
            return None
        raise AssertionError(f"unexpected cloud call: {name}")

    monkeypatch.setattr(slot_orchestration, "_cloud_call", fake_cloud_call)

    slot_orchestration._run_adjust_03()

    assert "_run_03_prep_fail_safe_standby" not in calls
    assert calls[-1] == "_monitor_partial_forced_and_stop"
