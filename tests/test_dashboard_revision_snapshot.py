"""Same-time views reuse revisions; mutation and publication boundaries do not."""
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from app.dashboard import snapshots
from app.dashboard.revision_reader import RevisionReader, RevisionSnapshot
from test_dashboard_epoch_parallel import ObservedClient
from test_dashboard_publication_generation import RevisionDocument


VIEWS = [
    {"history_scope": True, "history_compare_before": "2026-09-10"},
    {"history_scope": False},
    {"history_scope": True, "excluded_dates": frozenset({"2026-10-13"})},
]


def test_adjacent_views_preserve_every_epoch_component_and_reduce_native_reads(capsys):
    original, optimized = ObservedClient(), ObservedClient()
    expected = [snapshots._read_dashboard_source_epoch(original, **view) for view in VIEWS]
    original_events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    session = RevisionSnapshot()
    actual = [snapshots._read_dashboard_source_epoch(optimized, revision_snapshot=session, **view) for view in VIEWS]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert actual == expected
    assert optimized.documents < original.documents * 0.55
    assert sum(event["document_reads"] for event in events) < sum(event["document_reads"] for event in original_events) * 0.55
    assert events[0]["cached_documents"] == 0
    assert events[1]["cached_documents"] > 0 and events[2]["cached_documents"] > 3661
    assert optimized.payload_bytes < original.payload_bytes


@pytest.mark.parametrize("change", ["correction", "delete", "insert"])
def test_new_stage_detects_old_date_changes_even_outside_24_hours(change):
    client = ObservedClient()
    before = snapshots._read_dashboard_source_epoch(client, history_scope=True, revision_snapshot=RevisionSnapshot())
    if change == "correction":
        client.rows["monitoring_samples"][0].update_time += timedelta(microseconds=1)
    elif change == "delete":
        client.rows["monitoring_samples"].pop(0)
    else:
        row = client.rows["monitoring_samples"][0]
        client.rows["monitoring_samples"].append(RevisionDocument("old-late-arrival", dict(row.value), row.update_time))
    after = snapshots._read_dashboard_source_epoch(client, history_scope=True, revision_snapshot=RevisionSnapshot())
    assert before["monitoring_samples"] != after["monitoring_samples"]


def test_cached_night_plan_pages_keep_analog_dependencies_after_first_page():
    client = ObservedClient()
    stamp = client.rows["night_charge_plans"][0].update_time
    for row in client.rows["night_charge_plans"]:
        row.value.pop("decision_rationale", None)
    # One native unpaged result becomes multiple local pages in another view.
    client.rows["night_charge_plans"].extend(
        RevisionDocument(f"plan-{index}", {"date": "2026-10-13"}, stamp) for index in range(520))
    client.rows["night_charge_plans"][-1].value["decision_rationale"] = {
        "forecast_correction": {"recent_and_analog_hourly_floor": {"analog_day": "2026-03-01"}}}
    session = RevisionSnapshot()
    snapshots._read_dashboard_source_epoch(client, history_scope=True, revision_snapshot=session)
    assert snapshots._read_dashboard_source_epoch(client, history_scope=False, revision_snapshot=session) == (
        snapshots._read_dashboard_source_epoch(client, history_scope=False))


def test_snapshot_narrower_range_cannot_hide_a_wider_query_or_upper_boundary():
    session = RevisionSnapshot()
    client = ObservedClient()
    recent = snapshots._read_dashboard_source_epoch(client, history_scope=False, revision_snapshot=session)
    full = snapshots._read_dashboard_source_epoch(client, history_scope=True, revision_snapshot=session)
    assert full == snapshots._read_dashboard_source_epoch(client, history_scope=True)
    assert full["monitoring_samples"] != recent.get("monitoring_samples")
    assert session.find("monitoring_samples", "ts", "1900-01-01", "2100-01-01", inclusive=False) is None


def test_adjacent_views_share_native_read_time_and_new_stage_acquires_another_anchor():
    calls = []
    stamp = ObservedClient().rows["night_charge_plans"][0].read_time

    def anchor(**_):
        calls.append(1)
        return SimpleNamespace(read_time=stamp + timedelta(seconds=len(calls)))

    client = SimpleNamespace(collection=lambda _: SimpleNamespace(document=lambda _: SimpleNamespace(get=anchor)))
    session = RevisionSnapshot()
    first = RevisionReader(client, lambda: 4, session)
    second = RevisionReader(client, lambda: 4, session)
    assert first.read_time == second.read_time and len(calls) == 1
    third = RevisionReader(client, lambda: 4, RevisionSnapshot())
    assert third.read_time > first.read_time and len(calls) == 2


def test_upload_checks_do_not_receive_materialization_snapshot(monkeypatch):
    from app.dashboard import date_snapshots

    sessions, uploads = [], []
    monkeypatch.setattr(date_snapshots, "_assert_refresh_plans", lambda *_: None)
    monkeypatch.setattr(date_snapshots, "_outside_epoch", lambda *_, **__: {})
    monkeypatch.setattr(date_snapshots, "clear_dashboard_cache", lambda: None)
    monkeypatch.setattr(date_snapshots, "load_dashboard_slice", lambda *_, **__: object())
    monkeypatch.setattr(date_snapshots, "_payload_from_slice", lambda _: {})
    monkeypatch.setattr(date_snapshots.PublishedPlan, "from_document", lambda *_, **__: SimpleNamespace(sha256="original"))
    monkeypatch.setattr(date_snapshots, "_validate_publication_payload", lambda *_: None)

    def read(*_, revision_snapshot, **__):
        sessions.append(revision_snapshot)
        return {"revision": "unchanged"}

    monkeypatch.setattr(date_snapshots, "_read_dashboard_source_epoch", read)
    monkeypatch.setattr(date_snapshots, "_upload_artifact", lambda *_, **kwargs: uploads.append(kwargs))
    client = SimpleNamespace(collection=lambda _: SimpleNamespace(document=lambda _: SimpleNamespace(
        get=lambda **_: SimpleNamespace(to_dict=lambda: {"payload": "{}"}))))
    captured = {"outside_epoch": {}, "storage": object(), "identity": {
        "target_date": "2026-11-03", "control_version_id": "current", "raw_plan_sha256": "original"},
        "payloads": {"bootstrap": {}, "history": {}}}
    date_snapshots.publish_date_refresh(client, "2026-11-03", captured)
    date_snapshots.publish_date_refresh(client, "2026-11-03", captured)
    assert sessions[0] is sessions[1] and sessions[2] is sessions[3] and sessions[0] is not sessions[2]
    assert len(uploads) == 4 and all("revision_snapshot" not in kwargs for kwargs in uploads)
