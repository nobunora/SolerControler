from __future__ import annotations

import json
import time
import base64
import copy
import gzip
import hashlib
from datetime import datetime, timedelta
from types import SimpleNamespace

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


def test_closed_calendar_cost_resume_ignores_positive_current_day_owned_by_normal_producer(prepared_repair):
    client, cfg, path = apply_fixture(prepared_repair)
    state = repair._load(path)
    assert state["cost_end_ts"] == "2026-10-13T00:00:00"
    first = repair.regenerate_derived(client=client, cfg=cfg, state_path=path, stage="cost")
    closed = {key: dict(value) for key, value in client.data.items() if key.startswith("cost_daily/")}
    client.put("monitoring_samples/2026-10-13T00:00:00", {
        "ts": "2026-10-13T00:00:00", "load_kwh": 10.0, "buy_kwh": 1.0,
    })
    # The ordinary producer owns the open day and starts October from the
    # corrected preceding cost baseline; repair retains its closed-day scope.
    assert producer.recalc_cost_daily_from(client, start_date="2026-10-01", end_ts="2026-10-14T00:00:00",
        updated_at="normal-producer", deadline=time.monotonic() + 60, **state["policy"]) == 1
    assert client.data["cost_daily/2026-10-13"]["self_consumption_kwh"] == 9.0
    commits = client.commit_attempts
    client.writes.clear()
    repeated = repair.regenerate_derived(client=client, cfg=cfg, state_path=path, stage="cost")
    assert first["status"] == repeated["status"] == "verified"
    assert first["source_after_sha256"] == repeated["source_after_sha256"]
    assert repeated["reason_code"] == "verified_existing_output"
    assert all(client.data[key] == value for key, value in closed.items())
    assert client.writes == [] and client.commit_attempts == commits


def test_failed_stage_records_bounded_safe_active_exception_and_preserves_primary(tmp_path):
    path = tmp_path / "repair.json"
    secret = "PRIVATE_FAILURE_MESSAGE_AND_LOCAL"
    namespace = {"secret": secret}
    exec(compile("def fail(depth):\n    private_local = secret\n    if depth:\n        return fail(depth - 1)\n    raise ValueError(private_local)\n",
                 "C:\\PRIVATE_RESOURCE_PATH\\provider_read.py", "exec"), namespace)
    receipt = {"status": "failed", "reason_code": "ValueError", "finished_at": "fixed", "units": [{"event": "commit_ack"}]}
    primary = []
    with pytest.raises(ValueError, match=secret) as caught:
        try:
            namespace["fail"](20)
        except ValueError as error:
            primary.append(error)
            saved = repair._save_stage(path, "history", receipt)
            assert error.__traceback__ is not None
            raise
    assert caught.value is primary[0] and str(caught.value) == secret
    frames = saved["failure_frames"]
    assert len(frames) == 8
    assert all(set(frame) == {"type", "file", "function", "line"} for frame in frames)
    assert all(frame == {"type": "ValueError", "file": "provider_read.py", "function": "fail", "line": frame["line"]}
               for frame in frames)
    assert frames[-1]["line"] == 5
    assert saved["units"] == receipt["units"] and "failure_frames" not in receipt
    assert saved["receipt_sha256"] == repair._sha({key: value for key, value in saved.items() if key != "receipt_sha256"})
    journal_raw = (repair._directory(path) / "journal.json").read_text(encoding="utf-8")
    assert secret not in journal_raw and "PRIVATE_RESOURCE_PATH" not in journal_raw
    assert repair._journal(path)["stages"]["history"] == saved


def test_failed_stage_without_active_exception_records_unknown_frames(tmp_path):
    saved = repair._save_stage(tmp_path / "repair.json", "verify", {
        "status": "failed", "reason_code": "ValueError", "finished_at": "fixed",
    })
    assert saved["failure_frames"] == []
    assert saved["receipt_sha256"] == repair._sha({key: value for key, value in saved.items() if key != "receipt_sha256"})


def test_success_stage_receipt_and_hash_unchanged_during_active_exception(tmp_path):
    receipt = {"status": "verified", "finished_at": "fixed", "reason_code": "verified_existing_output"}
    expected = {**receipt, "receipt_sha256": repair._sha(receipt)}
    try:
        raise ValueError("unrelated active diagnostic")
    except ValueError:
        saved = repair._save_stage(tmp_path / "repair.json", "cost", receipt)
    assert saved == expected
    assert repair._journal(tmp_path / "repair.json")["stages"]["cost"] == expected


@pytest.fixture
def calibration_readback_case(prepared_repair):
    from test_clear_ghi_store import FINGERPRINT, _completed
    client, cfg, path = apply_fixture(prepared_repair)
    calibration, templates = _completed()
    state = repair._load(path)
    pack = json.loads(calibration.raw_bytes)
    records = []
    for index in range(17):
        record = copy.deepcopy(templates[0])
        day = f"2026-05-{index + 1:02}"
        record["date"] = record["actual_source"]["date"] = record["certification_source"]["date"] = day
        record["actual_available_at"] = record["certification_available_at"] = "2026-05-18T00:00:00+00:00"
        for hour, row in zip(range(5, 18), record["certification_source"]["hourly"]):
            row["time"] = f"{day}T{hour + 1:02}:00:00+09:00"
        records.append(record)
    pack.update(training_as_of="2026-10-12T00:00:00+00:00", created_at="2026-10-12T00:00:00+00:00", usable_from="2026-10-12T00:00:00+00:00")
    actual = {"date": "2026-09-20", "samples": [{"hour": hour, "actual_halfhour_kwh": [
        float(client.data[f"monitoring_samples/2026-09-20T{hour:02}:{minute:02}:00"]["pv_kwh"]) for minute in (0, 30)
    ]} for hour in range(5, 18)]}
    event = {"event": "pv_calibration_observations", "retained_observation_gaps": [],
             "excluded_dates": {"satellite_hour_missing": ["2026-09-20"]},
             "checked_actual_sha256_by_date": {"2026-09-20": repair._sha(actual)}}
    case = SimpleNamespace(client=client, cfg=cfg, path=path, state=state, pack=pack, records=records,
                           events=[event], actual=actual, plan=SimpleNamespace(target_date="2026-10-13", version_id="test-version"))
    state["calibration_root"] = {"site_fingerprint": FINGERPRINT, "cloud_prefix": None, "local_directory": str(cfg.artifacts_dir)}
    def save():
        for record in records:
            record["certification_source"]["date"] = record["date"]
            for sample, row in zip(record["actual_source"]["samples"], record["hours"]):
                row["actual_halfhour_kwh"] = sample["actual_halfhour_kwh"]
            for hour, row in zip(range(5, 18), record["certification_source"]["hourly"]):
                row["time"] = f"{record['date']}T{hour + 1:02}:00:00+09:00"
            for source in ("actual", "certification"):
                record[f"{source}_sha256"] = repair._sha(record[f"{source}_source"])
                original = cfg.artifacts_dir / "clear_ghi" / FINGERPRINT / "originals" / source / record["date"] / (record[f"{source}_sha256"] + ".json")
                original.parent.mkdir(parents=True, exist_ok=True)
                original.write_bytes(repair._canonical(record[f"{source}_source"]))
        pack.update(training_dates=[record["date"] for record in records], training_samples=len(records) * 13)
        for source in ("actual", "certification"):
            pack[f"{source}_source_sha256"] = repair._sha([{"date": record["date"], "sha256": record[f"{source}_sha256"]} for record in records])
        current = {"schema_version": 1, "model_id": pack["model_id"], "basis_version": pack["basis_version"],
                   "site_fingerprint": FINGERPRINT, "calibration_pack": pack, "calibration_sha256": repair._sha(pack),
                   "training_as_of": pack["training_as_of"], "clear_days": records}
        preimage_path = repair._directory(path) / "calibration-preimage.json"
        preimage_path.write_bytes(repair._canonical({"current": current, "generation": 1, "state_sha256": repair._sha(current)}))
        state["calibration_preimage_sha256"] = hashlib.sha256(preimage_path.read_bytes()).hexdigest()
        state["reviewed_state_sha256"] = repair._sha({key: value for key, value in state.items() if key != "reviewed_state_sha256"})
        path.write_bytes(repair._canonical(state))
        repair._save_stage(path, "source", {**repair._journal(path)["stages"]["source"], "calibration_preimage_sha256": state["calibration_preimage_sha256"]})
        execution_path = repair._directory(path) / "execution.json"
        execution_path.write_bytes(repair._canonical({"logs": [{"jsonPayload": event} for event in case.events]}))
        (repair._directory(path) / "adopted-forecast.json").write_bytes(repair._canonical({
            "execution_receipt_path": str(execution_path), "execution_receipt_sha256": hashlib.sha256(execution_path.read_bytes()).hexdigest(),
            "calibration_sha256": repair._sha(pack),
        }))
        case.plan.raw_json = json.dumps({"generated_at": "2026-10-13T00:00:00+00:00", "decision_rationale": {
            "pv_clear_ghi": {"calibration_pack": pack, "calibration_sha256": repair._sha(pack)},
        }})
        case.plan.sha256 = hashlib.sha256(case.plan.raw_json.encode()).hexdigest()
        (repair._directory(path) / "forecast-intent.json").write_bytes(repair._canonical({"prepared_sha256": state["reviewed_state_sha256"], "target_date": case.plan.target_date}))
    case.save = save
    save()
    client.writes.clear()
    return case


def test_calibration_readback_preserves_unselected_satellite_gap_as_pending(calibration_readback_case):
    case = calibration_readback_case
    assert len(case.pack["training_dates"]) == 17 and case.pack["training_samples"] == 221
    originals = {path: path.read_bytes() for path in (case.cfg.artifacts_dir / "clear_ghi").rglob("*.json")}
    result = repair._calibration_readback(case.client, case.cfg, case.path, case.state, case.plan)
    assert result["dates"] == {"2026-09-20": "pending_satellite_hour_missing"}
    assert result["calibration_sha256"] == repair._sha(case.pack)
    assert case.client.writes == [] and all(path.read_bytes() == raw for path, raw in originals.items())


@pytest.mark.parametrize("failure", ["selected", "selected_other_day", "retained", "retained_other_day", "missing_event", "missing_hash", "wrong_hash", "split_event", "actual_missing", "actual_bool", "actual_nan"])
def test_calibration_readback_rejects_selected_or_unproven_gap(calibration_readback_case, failure):
    case = calibration_readback_case
    event = case.events[0]
    if failure == "selected":
        record = copy.deepcopy(case.records[0])
        record["date"] = "2026-09-20"
        record["actual_source"] = case.actual
        case.records.append(record)
    elif failure == "selected_other_day":
        event["excluded_dates"]["satellite_hour_missing"].append(case.records[0]["date"])
    elif failure == "retained":
        event["retained_observation_gaps"] = ["2026-09-20"]
    elif failure == "retained_other_day":
        event["retained_observation_gaps"] = [case.records[0]["date"]]
    elif failure == "missing_event":
        case.events.clear()
    elif failure == "missing_hash":
        event["checked_actual_sha256_by_date"].clear()
    elif failure == "wrong_hash":
        event["checked_actual_sha256_by_date"]["2026-09-20"] = "0" * 64
    elif failure == "split_event":
        event["checked_actual_sha256_by_date"].clear()
        case.events.append({"event": "pv_calibration_observations", "checked_actual_sha256_by_date": {"2026-09-20": repair._sha(case.actual)}})
    else:
        case.client.data["monitoring_samples/2026-09-20T05:00:00"]["pv_kwh"] = {"actual_missing": None, "actual_bool": True, "actual_nan": float("nan")}[failure]
    case.save()
    with pytest.raises(ValueError):
        repair._calibration_readback(case.client, case.cfg, case.path, case.state, case.plan)
    assert case.client.writes == [] and "verify" not in repair._journal(case.path)["stages"]


@pytest.mark.parametrize("failure", ["execution_sha", "pack_sha", "site", "preimage", "basis", "cohort", "original", "old_source", "candidate_source"])
def test_calibration_readback_pending_does_not_bypass_pack_or_source_guards(calibration_readback_case, failure):
    case = calibration_readback_case
    if failure in {"preimage", "basis", "cohort", "original"}:
        preimage_path = repair._directory(case.path) / "calibration-preimage.json"
        preimage = json.loads(preimage_path.read_bytes())
        if failure == "basis":
            preimage["current"]["calibration_pack"]["basis_version"] = "wrong"
        elif failure == "cohort":
            preimage["current"]["calibration_pack"]["training_samples"] += 1
        elif failure == "original":
            preimage["current"]["clear_days"][0]["actual_source"]["samples"][0]["actual_halfhour_kwh"][0] += 1
        else:
            preimage["generation"] += 1
        preimage_path.write_bytes(repair._canonical(preimage))
        with pytest.raises(ValueError, match="preimage"):
            repair._load(case.path, case.client)
    elif failure in {"old_source", "candidate_source"}:
        ts = next(iter(case.state["existing"])) if failure == "old_source" else case.state["candidates"][0]["document_id"]
        case.client.data["monitoring_samples/" + ts]["pv_kwh"] += 1
        assert repair.verify_source_repair(client=case.client, state_path=case.path)["status"] != "verified"
    else:
        if failure == "execution_sha":
            (repair._directory(case.path) / "execution.json").write_bytes(b"{}")
        elif failure == "pack_sha":
            raw = json.loads(case.plan.raw_json)
            raw["decision_rationale"]["pv_clear_ghi"]["calibration_sha256"] = "0" * 64
            case.plan.raw_json = json.dumps(raw)
        else:
            case.state["calibration_root"]["site_fingerprint"] = "0" * 64
        with pytest.raises(ValueError):
            repair._calibration_readback(case.client, case.cfg, case.path, case.state, case.plan)
    assert case.client.writes == []


@pytest.mark.parametrize("certified", [False, True])
def test_calibration_readback_keeps_clear_and_not_certified_paths(calibration_readback_case, certified):
    case = calibration_readback_case
    case.events[0]["excluded_dates"] = {} if certified else {"not_certified_clear": ["2026-09-20"]}
    if certified:
        record = copy.deepcopy(case.records[0])
        record["date"] = "2026-09-20"
        record["actual_source"] = case.actual
        record["actual_available_at"] = record["certification_available_at"] = "2026-09-21T00:00:00+00:00"
        case.records.append(record)
    case.save()
    result = repair._calibration_readback(case.client, case.cfg, case.path, case.state, case.plan)
    assert result["dates"] == {"2026-09-20": repair._sha(case.actual) if certified else "not_certified_clear"}
    assert case.client.writes == []


def test_verify_repair_keeps_pending_date_observable(calibration_readback_case, monkeypatch, capsys):
    from app.dashboard import snapshots
    from scripts import repair_monitoring_source as command
    case = calibration_readback_case
    for stage in ("daily", "cost"):
        assert repair.regenerate_derived(client=case.client, cfg=case.cfg, state_path=case.path, stage=stage)["status"] == "verified"
    adopted_sha = "b" * 64
    repair._save_stage(case.path, "history", {"status": "verified", "adopted_receipt_sha256": adopted_sha})
    monkeypatch.setattr(repair, "_read_adopted_plan", lambda *args: (case.plan, adopted_sha))
    identity = {"target_date": case.plan.target_date, "generated_at": json.loads(case.plan.raw_json)["generated_at"],
                "control_version_id": case.plan.version_id, "raw_plan_sha256": case.plan.sha256}
    epoch = {"fixture": "stable"}
    metadata = {**identity, "sha256": hashlib.sha256(b"{}").hexdigest(), "source_epoch": base64.b64encode(gzip.compress(json.dumps(epoch).encode())).decode()}
    blob = SimpleNamespace(generation=1, metadata=metadata, reload=lambda **kwargs: None, download_as_bytes=lambda **kwargs: b"fixture-gzip")
    monkeypatch.setattr("google.cloud.storage.Client", lambda: SimpleNamespace(bucket=lambda _: SimpleNamespace(blob=lambda _: blob)))
    monkeypatch.setattr(snapshots, "snapshot_gcs_uri", lambda kind: "gs://test-bucket/" + kind)
    monkeypatch.setattr(snapshots, "_artifact_from_gzip", lambda *args: SimpleNamespace(raw=b"{}"))
    monkeypatch.setattr(snapshots, "_read_dashboard_source_epoch", lambda *args, **kwargs: epoch)
    validated = []
    monkeypatch.setattr(snapshots, "_validate_publication_payload", lambda payload, plan: validated.append(plan))
    case.client.writes.clear()
    result = repair.verify_repair(client=case.client, cfg=case.cfg, state_path=case.path)
    assert result["calibration"]["dates"] == {"2026-09-20": "pending_satellite_hour_missing"}
    assert repair._journal(case.path)["stages"]["verify"] == result
    assert result["receipt_sha256"] == repair._sha({key: value for key, value in result.items() if key != "receipt_sha256"})
    assert validated == [case.plan] and case.client.writes == []
    monkeypatch.setattr(command, "open_firestore", lambda: case.client)
    assert command.main(["--state-path", str(case.path), "--stage", "verify"]) == 0
    output = capsys.readouterr().out
    stdout = json.loads(output.strip().splitlines()[-1])
    assert stdout == {"status": "verified"}
    assert "pending_satellite_hour_missing" not in output
    assert "calibration" not in stdout and "pending_satellite_hour_missing" in json.dumps(repair._journal(case.path))
