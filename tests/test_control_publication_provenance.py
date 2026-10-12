from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.separated_runtime.plan import PublishedPlan
from app.separated_runtime.plan_store import FirestorePlanStore


def published(*, generated="2026-10-11T02:00:00Z", issued="2026-10-11T01:00:00Z", retrieved="2026-10-11T01:10:00Z", trained="2026-10-10T00:00:00Z", site="site-a"):
    trace = {
        "model_id": "physical_clear_ghi_annual1", "basis_version": "basis-1", "site_fingerprint": site,
        "provider_issued_at": issued, "retrieved_at": retrieved,
        "calibration_pack": {"training_as_of": trained},
    }
    raw = json.dumps({
        "generated_at": generated, "forecast": {"date": "2026-10-11"},
        "result": {"target_soc_7_percent": 40, "required_night_charge_kwh": 2, "effective_capacity_kwh": 10},
        "decision_rationale": {"pv_clear_ghi": trace},
    }).encode()
    return PublishedPlan.from_bytes(raw, target_date="2026-10-11", charge_rate_info={"percent_per_hour": 20})


def stored(plan):
    return {"fields": {"payload": {"stringValue": json.dumps(plan.to_document())}}, "updateTime": "old-time"}


@pytest.mark.parametrize("changes", [
    {"issued": "2026-10-11T00:00:00Z"}, {"issued": None},
    {"trained": "2026-10-09T00:00:00Z"}, {"site": "different-site"},
])
def test_new_generation_cannot_replace_fresher_provenance(monkeypatch, changes):
    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "local-test")
    old = published()
    new = published(generated="2026-10-11T03:00:00Z", **changes)
    session = SimpleNamespace(
        get=lambda *a, **kw: SimpleNamespace(status_code=200, json=lambda: stored(old)),
        post=lambda *a, **kw: pytest.fail("rejected provenance must not be committed"),
    )
    with pytest.raises(ValueError):
        FirestorePlanStore(session=session).publish(new)


def test_provenance_is_rechecked_after_cas_conflict(monkeypatch):
    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "local-test")
    incoming = published(generated="2026-10-11T04:00:00Z")
    replies = iter([stored(published()), stored(published(issued="2026-10-11T02:00:00Z"))])
    writes = []
    session = SimpleNamespace(
        get=lambda *a, **kw: SimpleNamespace(status_code=200, json=lambda: next(replies)),
        post=lambda *a, **kw: writes.append(kw) or SimpleNamespace(status_code=409, json=lambda: {}),
    )
    with pytest.raises(ValueError):
        FirestorePlanStore(session=session).publish(incoming)
    assert len(writes) == 1


def test_unknown_provider_compares_retrieval_without_claiming_provider_freshness(monkeypatch, capsys):
    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "local-test")
    old = published(issued=None)
    new = published(generated="2026-10-11T03:00:00Z", issued=None, retrieved="2026-10-11T01:20:00Z")
    session = SimpleNamespace(
        get=lambda *a, **kw: SimpleNamespace(status_code=200, json=lambda: stored(old)),
        post=lambda *a, **kw: SimpleNamespace(status_code=200, json=lambda: {}),
    )
    FirestorePlanStore(session=session).publish(new)
    assert "provider_freshness_unknown" in capsys.readouterr().out


def test_control_publication_deadline_prevents_later_cas_attempt(monkeypatch):
    import time
    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "local-test")
    calls = []
    clock = [time.monotonic()]
    monkeypatch.setattr("app.separated_runtime.plan_store.time.monotonic", lambda: clock[0])
    def get(*args, **kwargs):
        calls.append(("read", kwargs))
        return SimpleNamespace(status_code=404)
    def post(*args, **kwargs):
        calls.append(("commit", kwargs))
        clock[0] += 61
        return SimpleNamespace(status_code=409, json=lambda: {})
    with pytest.raises(TimeoutError):
        FirestorePlanStore(session=SimpleNamespace(get=get, post=post)).publish(published())
    assert [kind for kind, _ in calls] == ["read", "commit"]
    assert all(kwargs["max_allowed_time"] <= 60 for _, kwargs in calls)


def test_control_commit_lost_ack_is_distinct_from_server_rejection(monkeypatch):
    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "local-test")
    def lost(*args, **kwargs):
        raise TimeoutError("response lost")
    session = SimpleNamespace(get=lambda *args, **kwargs: SimpleNamespace(status_code=404), post=lost)
    with pytest.raises(TimeoutError) as error:
        FirestorePlanStore(session=session).publish(published())
    assert any("acknowledgement is unknown" in note for note in error.value.__notes__)
