from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scripts import kpnet_soc_cycle_probe as probe


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch):
    now_seconds = [0.0]
    monkeypatch.setenv("SOC_CYCLE_PROBE_AUTHORIZED", "true")
    monkeypatch.setattr(probe.time, "monotonic", lambda: now_seconds[0])
    monkeypatch.setattr(probe.time, "sleep", lambda delay: now_seconds.__setitem__(0, now_seconds[0] + delay))

    class Daytime:
        @staticmethod
        def now(zone):
            return datetime(2026, 10, 7, 19, 0, tzinfo=ZoneInfo("Asia/Tokyo"))

    monkeypatch.setattr(probe, "datetime", Daytime)
    monkeypatch.setattr(probe.KpNetConfig, "from_env", lambda: SimpleNamespace(dry_run=False))
    plan = SimpleNamespace(producer_source_revision="test-source", raw_json='{"test":true}')
    monkeypatch.setattr(probe, "FirestorePlanStore", lambda: SimpleNamespace(fetch=lambda day: plan))
    monkeypatch.setattr(probe, "_validate_current_plan", lambda plan, now, require_evidence: plan)
    current = {field: "50" for field in probe.ROUNDTRIP_SETTING_FIELDS}
    current["batteryOperatingMode"] = "1"
    current.update({"chargeStartTimeH": "23", "chargeStartTimeM": "0", "chargeEndTimeH": "7", "chargeEndTimeM": "0"})
    reads = [34.0, 34.0, 35.0]
    writes = []

    class Client:
        def __init__(self, cfg, deadline_monotonic):
            self.deadline_monotonic = deadline_monotonic

        def login(self):
            pass

        def open_settings_page(self):
            pass

        def read_current_settings(self):
            return dict(current)

        def logout(self):
            pass

    monkeypatch.setattr(probe, "KpNetClient", Client)
    monkeypatch.setattr(probe, "latest_realtime_soc_reading", lambda **kwargs: SimpleNamespace(value_percent=reads.pop(0) if reads else None))
    monkeypatch.setattr(probe, "_forced_probe_candidate_maps", lambda client: {"BatteryOperatingMode": {"1": "グリーン", "3": "強制充電", "5": "待機"}})

    def apply(**kwargs):
        profile = kwargs["profile"]
        mode = profile.battery_operating_mode
        writes.append(mode)
        changed = [] if mode == current["batteryOperatingMode"] else ["batteryOperatingMode"]
        current["batteryOperatingMode"] = mode
        for field, attr in (("chargeStartTimeH", "charge_start_h"), ("chargeStartTimeM", "charge_start_m"),
                            ("chargeEndTimeH", "charge_end_h"), ("chargeEndTimeM", "charge_end_m")):
            value = getattr(profile, attr)
            if current[field] != value:
                current[field] = value
                changed.append(field)
        return dict(current), changed, {"batteryOperatingMode": mode}

    monkeypatch.setattr(probe, "_apply_and_verify", apply)
    return reads, writes, current, now_seconds


def test_realtime_one_point_reaches_target_then_stops_and_restores(harness):
    _, writes, _, _ = harness
    summary = probe.run_soc_cycle_probe()
    assert summary["status"] == "passed"
    assert summary["initial_soc_percent"] == 34
    assert summary["target_soc_percent"] == 35
    assert summary["soc_increase_percent"] == 1
    assert summary["stop_reason"] == "target_reached"
    assert writes == ["3", "5", "1"]
    assert summary["restore_verified"] is True


def test_unknown_initial_soc_causes_no_setting_mutation(harness):
    reads, writes, _, _ = harness
    reads[:] = [None]
    summary = probe.run_soc_cycle_probe()
    assert summary["status"] == "failed"
    assert summary["failed_phase"] == "initial_soc"
    assert writes == []


def test_no_soc_headroom_causes_no_setting_mutation(harness):
    reads, writes, _, _ = harness
    reads[:] = [100]
    summary = probe.run_soc_cycle_probe()
    assert summary["status"] == "failed"
    assert writes == []


def test_three_missing_monitor_reads_stop_and_restore_without_pass(harness):
    reads, writes, _, _ = harness
    reads[:] = [34, None, None, None]
    summary = probe.run_soc_cycle_probe()
    assert summary["status"] == "inconclusive"
    assert summary["stop_reason"] == "soc_unavailable"
    assert writes == ["3", "5", "1"]
    assert summary["restore_verified"] is True


def test_soc_timeout_stops_and_restores_without_pass(harness):
    reads, writes, _, _ = harness
    reads[:] = [34] * 50
    summary = probe.run_soc_cycle_probe()
    assert summary["status"] == "inconclusive"
    assert summary["stop_reason"] == "probe_timeout"
    assert writes == ["3", "5", "1"]


def test_unknown_write_suppresses_stop_and_restore(harness, monkeypatch):
    _, writes, _, _ = harness

    def uncertain(**kwargs):
        writes.append("unknown")
        raise probe.KpNetUnknownWriteError("uncertain SET")

    monkeypatch.setattr(probe, "_apply_and_verify", uncertain)
    summary = probe.run_soc_cycle_probe()
    assert summary["status"] == "failed"
    assert summary["mutation_outcome"] == "unknown"
    assert summary["restore_after_failure"] == "suppressed_unknown_write"
    assert writes == ["unknown"]


def test_active_forced_mode_is_preserved(harness):
    _, writes, current, _ = harness
    current["batteryOperatingMode"] = "3"
    summary = probe.run_soc_cycle_probe()
    assert summary["status"] == "failed"
    assert writes == []


def test_unauthorized_probe_is_rejected_before_any_network(harness, monkeypatch):
    monkeypatch.delenv("SOC_CYCLE_PROBE_AUTHORIZED")
    with pytest.raises(RuntimeError, match="one-shot authorization"):
        probe.run_soc_cycle_probe()


def test_temporary_daytime_charge_window_is_explicit_and_exactly_restored(harness, monkeypatch):
    monkeypatch.setenv("SOC_CYCLE_PROBE_TEMPORARY_WINDOW", "true")
    _, writes, current, _ = harness
    summary = probe.run_soc_cycle_probe()
    assert summary["status"] == "passed"
    assert summary["temporary_charge_window"] == {"start_hour": 19, "end_hour": 20}
    assert set(summary["forced_changed_fields"]) == {"batteryOperatingMode", "chargeStartTimeH", "chargeEndTimeH"}
    assert current["chargeStartTimeH"] == "23" and current["chargeEndTimeH"] == "7"
    assert writes == ["3", "5", "1"]
    assert summary["restore_verified"] is True


def test_real_zero_soc_is_valid_and_stops_after_one_point(harness):
    reads, writes, _, _ = harness
    reads[:] = [0, 0, 1]
    summary = probe.run_soc_cycle_probe()
    assert summary["status"] == "passed"
    assert summary["initial_soc_percent"] == 0
    assert summary["target_soc_percent"] == 1
    assert writes == ["3", "5", "1"]
