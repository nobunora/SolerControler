from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.runtime import cloud_job
from app.runtime.soc_reading import SocReading
from app.separated_runtime import control, planner
from app.separated_runtime.plan import PublishedPlan
from app.separated_runtime.plan_store import FirestorePlanStore

JST = ZoneInfo("Asia/Tokyo")
RATE = {"percent_per_hour": 20.0, "source": "saved-csv"}


def raw_plan(target=80, day="2099-01-01", generated="2098-12-31T17:30:00Z"):
    return json.dumps({"forecast": {"date": day, "hourly_weather": [{"hour": 10, "pv": 1.25}]},
                       "generated_at": generated,
                       "result": {"target_soc_7_percent": target, "required_night_charge_kwh": 7.2,
                                  "effective_capacity_kwh": 10}}, ensure_ascii=False, indent=2).encode()


class Clock:
    def __init__(self, at=None):
        self.at = at or datetime(2099, 1, 1, 3, tzinfo=JST)

    def now(self, _zone):
        return self.at

    def sleep(self, seconds):
        self.at += timedelta(seconds=seconds)

    def monotonic_seconds(self):
        return 0.0


class Device:
    def __init__(self, values):
        self.values = iter(values)
        self.events = []

    def read_soc(self, _paths):
        value = next(self.values)
        self.events.append(("soc", value))
        return SocReading(value, "fake", None, None)

    def apply_profile(self, *, profile, dynamic_forced_profile, label):
        self.events.append(("mode", profile, dynamic_forced_profile))


def test_handoff_keeps_exact_plan_bytes_and_values(tmp_path):
    raw = raw_plan()
    plan = PublishedPlan.from_bytes(raw, target_date="2099-01-01", charge_rate_info=RATE)
    decoded = PublishedPlan.from_document(json.loads(json.dumps(plan.to_document())), target_date="2099-01-01")
    path = tmp_path / "plan.json"
    decoded.restore(path)
    assert path.read_bytes() == raw
    assert decoded.sha256 == hashlib.sha256(raw).hexdigest()
    assert json.loads(decoded.raw_json)["result"] == json.loads(raw)["result"]


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), -1, 101])
def test_invalid_target_never_becomes_zero(value):
    with pytest.raises((ValueError, TypeError)):
        PublishedPlan.from_bytes(raw_plan(value), target_date="2099-01-01", charge_rate_info=RATE)


def test_stale_or_corrupt_handoff_is_rejected():
    plan = PublishedPlan.from_bytes(raw_plan(), target_date="2099-01-01", charge_rate_info=RATE)
    with pytest.raises(ValueError, match="stale"):
        PublishedPlan.from_document(plan.to_document(), target_date="2099-01-02")
    document = plan.to_document()
    document["raw_json"] = document["raw_json"].replace("1.25", "9.25")
    with pytest.raises(ValueError, match="checksum"):
        PublishedPlan.from_document(document, target_date="2099-01-01")


@pytest.mark.parametrize("target,values", [(0, [0]), (30, [0, 30]), (80, [79, 80]),
                                         (100, [93, 99, 100]), (94, [20, 60, 94]),
                                         (80, [20, None, 30, 80]), (80, [20, None, None, None])])
def test_new_worker_has_same_monitor_trace_as_legacy(tmp_path, monkeypatch, target, values):
    raw = raw_plan(target)
    plan = PublishedPlan.from_bytes(raw, target_date="2099-01-01", charge_rate_info=RATE)
    old_path = tmp_path / "old.json"
    old_path.write_bytes(raw)
    old = Device(values)
    monkeypatch.setattr(cloud_job, "estimate_forced_charge_rate_percent_per_hour", lambda _paths: RATE)
    cloud_job._monitor_partial_forced_and_stop(old_path, clock=Clock(), device_port=old)
    new = Device(values)
    monkeypatch.setattr(cloud_job, "_night_plan_path", lambda: tmp_path / "new.json")
    control.run_slot("03", store=SimpleNamespace(fetch=lambda *a, **kw: plan), clock=Clock(), device_port=new)
    assert new.events == old.events
    assert (tmp_path / "new.json").read_bytes() == raw


@pytest.mark.parametrize("slot,expected", [("23", "standby"), ("07", "green")])
def test_23_and_07_never_acquire_a_plan_or_depend_on_database(monkeypatch, slot, expected):
    calls = []
    monkeypatch.setattr(cloud_job, "_run_settings_profile_with_retry", lambda **kw: calls.append(kw))
    monkeypatch.setattr(control, "FirestorePlanStore", lambda: pytest.fail("database must not be opened"))
    control.run_slot(slot)
    assert len(calls) == 1 and calls[0]["profile"] == expected


def test_missing_plan_attempts_standby_and_does_not_force(monkeypatch):
    calls = []
    monkeypatch.setattr(cloud_job, "_run_03_prep_fail_safe_standby", lambda: calls.append("standby"))
    monkeypatch.setattr(cloud_job, "_monitor_partial_forced_and_stop", lambda *a, **kw: pytest.fail("no plan"))
    def missing(*args, **kwargs):
        raise RuntimeError("missing")
    with pytest.raises(RuntimeError, match="missing"):
        control.run_slot("03", store=SimpleNamespace(fetch=missing), clock=Clock())
    assert calls == ["standby"]


def test_fetch_cannot_cross_03_hard_deadline(monkeypatch):
    calls = []
    monkeypatch.setattr(cloud_job, "_run_03_prep_fail_safe_standby", lambda: calls.append("standby"))
    store = SimpleNamespace(fetch=lambda *a, **kw: pytest.fail("must not start a late request"))
    with pytest.raises(RuntimeError, match="time budget"):
        control.run_slot("03", store=store, clock=Clock(datetime(2099, 1, 1, 6, 54, tzinfo=JST)))
    assert calls == ["standby"]


def response(status, document=None):
    return SimpleNamespace(status_code=status, json=lambda: document or {})


def stored(plan, update_time="2098-12-31T17:30:00Z"):
    return {"fields": {"payload": {"stringValue": json.dumps(plan.to_document())}}, "updateTime": update_time}


def store_with_session(monkeypatch, session):
    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "unit-test")
    return FirestorePlanStore(session=session)


def test_publication_is_atomic_and_protects_the_adopted_version(monkeypatch):
    plan = PublishedPlan.from_bytes(raw_plan(), target_date="2099-01-01", charge_rate_info=RATE)
    posts = []
    session = SimpleNamespace(get=lambda *a, **kw: response(404),
                              post=lambda *a, **kw: posts.append(kw) or response(200))
    store_with_session(monkeypatch, session).publish(plan)
    writes = posts[0]["json"]["writes"]
    assert len(writes) == 2
    assert writes[0]["update"]["fields"] == writes[1]["update"]["fields"]
    assert writes[1]["currentDocument"] == {"exists": False}


def test_delayed_older_publication_does_not_replace_a_newer_plan(monkeypatch):
    old = PublishedPlan.from_bytes(raw_plan(), target_date="2099-01-01", charge_rate_info=RATE)
    new = PublishedPlan.from_bytes(raw_plan(generated="2098-12-31T18:00:00Z"), target_date="2099-01-01", charge_rate_info=RATE)
    session = SimpleNamespace(get=lambda *a, **kw: response(200, stored(new)),
                              post=lambda *a, **kw: pytest.fail("older plan must not be committed"))
    with pytest.raises(ValueError, match="older"):
        store_with_session(monkeypatch, session).publish(old)


def test_concurrent_publication_rechecks_the_pointer(monkeypatch):
    plan = PublishedPlan.from_bytes(raw_plan(), target_date="2099-01-01", charge_rate_info=RATE)
    replies = iter([response(409), response(200)])
    reads = []
    session = SimpleNamespace(get=lambda *a, **kw: reads.append(1) or response(404), post=lambda *a, **kw: next(replies))
    store_with_session(monkeypatch, session).publish(plan)
    assert len(reads) == 2


def test_planner_reuses_canonical_computation_and_publishes_after_success(tmp_path, monkeypatch):
    path = tmp_path / "night.json"
    path.write_bytes(raw_plan())
    monkeypatch.setenv("KP_NIGHT_PLAN_PATH", str(path))
    calls = []
    monkeypatch.setattr(planner.forecast_job, "main", lambda: calls.append("canonical") or 0)
    monkeypatch.setattr(planner.forecast_job, "_target_date", lambda: "2099-01-01")
    monkeypatch.setattr(planner, "estimate_forced_charge_rate_percent_per_hour", lambda _paths: RATE)
    monkeypatch.setattr(planner, "FirestorePlanStore", lambda: SimpleNamespace(publish=lambda plan: calls.append(plan)))
    assert planner.main() == 0
    assert calls[0] == "canonical" and calls[1].raw_json.encode() == path.read_bytes()


def test_control_dependency_list_has_no_scientific_or_backup_packages():
    requirements = Path("requirements-control.txt").read_text()
    assert {line for line in requirements.splitlines() if line and not line.startswith(("#", "-"))} == {
        "requests", "beautifulsoup4", "google-auth"}


@pytest.mark.parametrize("generated", ["2098-12-30T17:30:00Z", "2098-12-31T19:00:00Z"])
def test_stale_and_future_generation_never_reaches_device(monkeypatch, generated):
    plan = PublishedPlan.from_bytes(raw_plan(generated=generated), target_date="2099-01-01", charge_rate_info=RATE)
    monkeypatch.setattr(cloud_job, "_run_03_prep_fail_safe_standby", lambda: None)
    monkeypatch.setattr(cloud_job, "_monitor_partial_forced_and_stop", lambda *a, **kw: pytest.fail("invalid issue time"))
    with pytest.raises(ValueError, match="generation time"):
        control.run_slot("03", store=SimpleNamespace(fetch=lambda *a, **kw: plan), clock=Clock(), device_port=Device([]))


def test_failed_canonical_calculation_does_not_publish(monkeypatch):
    monkeypatch.setattr(planner.forecast_job, "main", lambda: 1)
    monkeypatch.setattr(planner, "FirestorePlanStore", lambda: pytest.fail("failed calculation must not publish"))
    assert planner.main() == 1


@pytest.mark.parametrize("forced,green,restored,expected", [
    ("passed", "passed", True, 0), ("failed", "passed", True, 1),
    ("passed", "failed", True, 1), ("passed", "passed", False, 1)])
def test_probe_requires_both_physical_proofs_and_restoration(monkeypatch, forced, green, restored, expected):
    plan = PublishedPlan.from_bytes(raw_plan(), target_date="2099-01-01", charge_rate_info=RATE,
                                   producer_source_revision="a" * 40, inputs_verified=True)
    monkeypatch.setenv("CLOUD_JOB_SLOT", "settings-roundtrip")
    monkeypatch.setattr(control, "install_unknown_write_guard", lambda: None)
    monkeypatch.setattr(cloud_job, "_tokyo_now", lambda: Clock().at)
    monkeypatch.setattr(control, "FirestorePlanStore", lambda: SimpleNamespace(fetch=lambda *a, **kw: plan))
    monkeypatch.setattr(control, "run_settings_roundtrip", lambda: {
        "status": "passed", "forced_proof": forced, "green_proof": green, "restore_verified": restored})
    assert control.main() == expected
