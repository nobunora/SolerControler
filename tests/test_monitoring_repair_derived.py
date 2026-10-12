from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import pytest

from app.operations import firestore as producer
from app.operations import monitoring_repair as repair
from test_monitoring_repair_command import RepairClient, prepared_repair  # noqa: F401


def apply_fixture(prepared_repair):
    client, cfg, path, result = prepared_repair
    assert repair.apply_source_repair(client=client, state_path=path,
        reviewed_state_sha256=result["reviewed_state_sha256"])["status"] == "verified"
    client.writes.clear()
    return client, cfg, path


def test_daily_repairs_persisted_metrics_and_next_night_window(prepared_repair):
    client, cfg, path = apply_fixture(prepared_repair)
    receipt = repair.regenerate_derived(client=client, cfg=cfg, state_path=path, stage="daily")
    assert receipt["status"] == "verified"
    day = client.data["dashboard_daily_metrics/2026-09-20"]
    assert day["actual_load_kwh"] == 96 and day["buy_kwh"] == 48 and day["day_buy_kwh"] == 32
    assert day["sample_count"] == 48
    assert client.data["dashboard_daily_metrics/2026-09-21"]["review_night_charge_kwh"] == pytest.approx(0.9)
    assert all(path.startswith(("dashboard_daily_metrics/", "battery_daily_metrics/")) for path in client.writes)


def test_cost_month_scope_retry_and_hand_calculated_self_consumption(prepared_repair):
    client, cfg, path = apply_fixture(prepared_repair)
    receipt = repair.regenerate_derived(client=client, cfg=cfg, state_path=path, stage="cost")
    assert receipt["status"] == "verified"
    assert client.data["cost_daily/2026-09-20"]["self_consumption_kwh"] == 48
    old = dict(client.data["cost_daily/2026-09-20"])
    client.data["cost_daily/2026-09-20"]["savings_yen"] = -1
    retry = repair.regenerate_derived(client=client, cfg=cfg, state_path=path, stage="cost")
    assert retry["status"] == "verified" and client.data["cost_daily/2026-09-20"] == old
    assert all(path.startswith("cost_daily/") for path in client.writes)


@pytest.mark.parametrize("stage", ["daily", "cost"])
def test_lost_commit_ack_readback_preserves_committed_output(prepared_repair, stage):
    client, cfg, path = apply_fixture(prepared_repair)
    client.lose_ack = True
    receipt = repair.regenerate_derived(client=client, cfg=cfg, state_path=path, stage=stage)
    assert receipt["status"] == "verified" and receipt["reason_code"] == "TimeoutError"
    assert any(unit["event"] == "commit_unknown" for unit in receipt["units"])


def test_source_drift_regenerates_once_then_stops(prepared_repair):
    client, cfg, path = apply_fixture(prepared_repair)
    original = producer.recalc_cost_daily_from
    calls = []
    def drift(*args, **kwargs):
        result = original(*args, **kwargs)
        target = "monitoring_samples/2026-09-20T18:00:00"
        client.versions[target] += timedelta(seconds=1)
        calls.append(result)
        return result
    from unittest.mock import patch
    with patch.object(producer, "recalc_cost_daily_from", drift):
        receipt = repair.regenerate_derived(client=client, cfg=cfg, state_path=path, stage="cost")
    assert receipt["status"] == "failed" and receipt["reason_code"] == "source_drift" and len(calls) == 2
    assert client.data["cost_daily/2026-09-20"]


def test_partial_stream_timeout_prevents_every_commit():
    client = RepairClient()
    client.put("monitoring_samples/2026-09-20T00:00:00", {"ts": "2026-09-20T00:00:00", "load_kwh": 2, "buy_kwh": 1})
    with pytest.raises(TimeoutError):
        producer.recalc_cost_daily_from(client, start_date="2026-09-01", end_ts="2026-09-21T00:00:00",
            day_rate_yen_per_kwh=31, updated_at="fixed", deadline=time.monotonic() - 1)
    assert client.writes == [] and client.commit_attempts == 0


def test_calendar_outside_normal_calibration_window_stops_without_fit(prepared_repair):
    client, cfg, path = apply_fixture(prepared_repair)
    intent = repair._directory(path) / "forecast-intent.json"
    intent.write_text(json.dumps({"prepared_sha256": repair._load(path)["reviewed_state_sha256"], "target_date": "2027-01-01"}))
    with pytest.raises(ValueError, match="outside normal calibration"):
        repair.regenerate_derived(client=client, cfg=cfg, state_path=path, stage="daily")
    assert client.writes == []


def test_450_commit_boundary_progress_and_partial_ack_do_not_claim_full_scope():
    client = RepairClient()
    begin = datetime(2024, 1, 1)
    for index in range(451):
        ts = (begin + timedelta(days=index)).isoformat()
        client.put("monitoring_samples/" + ts, {"ts": ts, "load_kwh": 2.0, "buy_kwh": 1.0})
    progress = []
    count = producer.recalc_cost_daily_from(client, start_date="2024-01-01", end_ts="2025-04-01T00:00:00",
        day_rate_yen_per_kwh=31, updated_at="fixed", deadline=time.monotonic() + 60, on_progress=progress.append)
    assert count == 451 and client.commit_attempts == 2
    assert [event["document_count"] for event in progress if event["event"] == "unit_prepared"] == [450, 1]
    assert [len(event["acked_update_times"]) for event in progress if event["event"] == "commit_ack"] == [450, 1]


def test_progress_callback_failure_after_ack_keeps_write_and_marks_unknown():
    client = RepairClient()
    client.put("monitoring_samples/2026-09-20T00:00:00", {"ts": "2026-09-20T00:00:00", "load_kwh": 2.0, "buy_kwh": 1.0})
    events = []
    def fail_ack(event):
        events.append(event)
        if event["event"] == "commit_ack":
            raise OSError("journal write failed")
    with pytest.raises(RuntimeError, match="progress is unknown"):
        producer.recalc_cost_daily_from(client, start_date="2026-09-01", end_ts="2026-09-21T00:00:00",
            day_rate_yen_per_kwh=31, updated_at="fixed", on_progress=fail_ack)
    assert "cost_daily/2026-09-20" in client.data
    assert [event["event"] for event in events] == ["unit_prepared", "commit_ack", "progress_unknown"]


def test_cost_tier_boundaries_following_day_october_reset_and_prior_baseline():
    client = RepairClient()
    client.put("cost_daily/2026-08-31", {"date": "2026-08-31", "cumulative_kwh": 100.0, "cumulative_yen": 1000.0})
    for day, buy, load in (("2026-09-01", 90.0, 100.0), ("2026-09-21", 140.0, 150.0), ("2026-10-01", 90.0, 100.0)):
        ts = day + "T07:00:00"
        client.put("monitoring_samples/" + ts, {"ts": ts, "load_kwh": load, "buy_kwh": buy})
    producer.recalc_cost_daily_from(client, start_date="2026-09-01", end_ts="2026-10-02T00:00:00",
        day_rate_yen_per_kwh=31, tariff_mode="night8_tiered", updated_at="fixed",
        deadline=time.monotonic() + 60, on_progress=lambda event: None)
    assert client.data["cost_daily/2026-09-01"]["savings_yen"] == pytest.approx(10 * 39.1)
    assert client.data["cost_daily/2026-09-21"]["savings_yen"] == pytest.approx(130 * 39.1 + 20 * 43.62 - 140 * 39.1)
    october = client.data["cost_daily/2026-10-01"]
    assert october["savings_yen"] == pytest.approx(10 * 39.1)
    assert october["cumulative_kwh"] == 130
    assert october["cumulative_yen"] == pytest.approx(1000 + 391 + 481.4 + 391)


def test_verified_same_source_resume_has_no_extra_derived_commit(prepared_repair):
    client, cfg, path = apply_fixture(prepared_repair)
    first = repair.regenerate_derived(client=client, cfg=cfg, state_path=path, stage="cost")
    commits = client.commit_attempts
    retry = repair.regenerate_derived(client=client, cfg=cfg, state_path=path, stage="cost")
    assert first["status"] == retry["status"] == "verified"
    assert retry["reason_code"] == "verified_existing_output" and client.commit_attempts == commits
