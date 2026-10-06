from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.dashboard import firestore_repository as repository
from app.dashboard.aggregation import _build_energy_daily
from app.operations.forecast_persistence import persist_forecast_only_plan
from app.operations.forecast_recovery import recover_missing_forecast_snapshots

DAY = "2026-10-06"


class Document:
    def __init__(self, client, collection, key):
        self.client, self.collection, self.id = client, collection, key
        self.reference = self

    @property
    def exists(self):
        return self.id in self.client.data.get(self.collection, {})

    def get(self):
        return self

    def to_dict(self):
        return copy.deepcopy(self.client.data.get(self.collection, {}).get(self.id, {}))


class Collection:
    def __init__(self, client, name, filters=()):
        self.client, self.name, self.filters = client, name, filters

    def document(self, key):
        return Document(self.client, self.name, key)

    def where(self, field, operator, value):
        return Collection(self.client, self.name, (*self.filters, (field, operator, value)))

    def order_by(self, *args, **kwargs):
        return self

    def stream(self):
        self.client.reads.append((self.name, self.filters))
        for key, row in self.client.data.get(self.name, {}).items():
            if all(
                str(row.get(field, "")) >= value if operator == ">=" else
                str(row.get(field, "")) <= value if operator == "<=" else
                str(row.get(field, "")) < value if operator == "<" else row.get(field) == value
                for field, operator, value in self.filters
            ):
                yield self.document(key)


class Batch:
    def __init__(self, client):
        self.client, self.actions = client, []

    def delete(self, ref):
        self.actions.append(("delete", ref, None))

    def set(self, ref, payload, merge=False):
        self.actions.append(("set", ref, payload))

    def create(self, ref, payload):
        self.actions.append(("create", ref, payload))

    def commit(self):
        for action, ref, payload in self.actions:
            rows = self.client.data.setdefault(ref.collection, {})
            if action == "delete":
                rows.pop(ref.id, None)
            else:
                if action == "create":
                    assert ref.id not in rows
                rows[ref.id] = copy.deepcopy(payload)
        self.client.writes += len(self.actions)


class Client:
    def __init__(self, plan):
        self.data = {"night_charge_plans": {DAY: {"plan_json": json.dumps(plan), "result": {"final_predicted_pv_kwh": 999.0}}}}
        self.reads, self.plan_reads, self.writes = [], [], 0

    def collection(self, name):
        return Collection(self, name)

    def get_all(self, refs):
        self.plan_reads.extend(ref.id for ref in refs)
        return iter(refs)

    def batch(self):
        return Batch(self)


def plan():
    return {
        "generated_at": "2026-10-06T03:03:19+09:00",
        "forecast": {"date": DAY, "hourly_weather": [{"hour": hour} for hour in range(24)]},
        "daytime_soc_optimization": {
            "hourly_pv_forecast_kwh": {str(hour): 0.1 for hour in range(24)},
            "hourly_load_forecast_kwh": {str(hour): 0.2 for hour in range(24)},
        },
        "pv_array_forecast": {"totals": {"total_kwh": 999.0}},
    }


def recover(client):
    return recover_missing_forecast_snapshots(
        client, target_date="2026-10-07", recorded_at="2026-10-07T02:30:00+09:00", lookback_days=1,
    )


def test_failed_forecast_day_recovers_original_and_both_consumers_match(monkeypatch):
    client = Client(plan())
    result = recover(client)
    assert result["snapshot_rows"] == 24
    assert client.plan_reads == [DAY]
    rows = repository._firestore_forecast_hourly_between(client, start_date=DAY, end_date_iso=DAY)
    assert len(rows) == 24
    assert {row["forecast_issued_at"] for row in rows} == {"2026-10-06T03:03:19+09:00"}
    assert len({row["forecast_run_id"] for row in rows}) == 1
    energy = _build_energy_daily(
        start_date=DAY, end_date_iso=DAY,
        pv_daily=[{"date": DAY, "forecast_pv_total_kwh": 999.0}],
        monitoring_daily=[{"date": DAY, "actual_pv_kwh": 10.832}], forecast_hourly=rows,
    )
    monkeypatch.setattr(repository, "_review_candidate_dates", lambda *args, **kwargs: [DAY])
    monkeypatch.setattr(repository, "_daily_metric_is_complete", lambda row: True)
    reviews = repository._build_firestore_daily_reviews(
        client, end_date_iso=DAY, energy_daily=energy, battery_daily=[], forecast_hourly=rows,
        daily_metrics=[{"date": DAY}],
    )
    assert energy[0]["forecast_pv_kwh"] == pytest.approx(2.4)
    assert energy[0]["forecast_load_kwh"] == pytest.approx(4.8)
    for field in ("forecast_pv_kwh", "forecast_load_kwh", "forecast_load_source"):
        assert reviews[0][field] == energy[0][field]
    before = copy.deepcopy(client.data)
    assert recover(client)["snapshot_rows"] == 0
    assert client.data == before
    assert "forecast_hourly" not in client.data  # recovery only appends original snapshots


def test_new_day_does_not_delete_recovered_previous_day(tmp_path):
    client = Client(plan())
    recover(client)
    previous = copy.deepcopy(client.data["forecast_hourly_snapshots"])
    today = plan()
    today["forecast"]["date"] = "2026-10-07"
    today["generated_at"] = "2026-10-07T02:30:00+09:00"
    path = tmp_path / "today.json"
    path.write_text(json.dumps(today), encoding="utf-8")
    persist_forecast_only_plan(client, plan_path=path, target_date="2026-10-07", timezone_name="Asia/Tokyo")
    assert all(client.data["forecast_hourly_snapshots"][key] == value for key, value in previous.items())


def test_valid_mutable_forecast_is_preserved_without_loading_plan():
    client = Client(plan())
    client.data["forecast_hourly"] = {
        str(hour): {"date": DAY, "hour": hour, "forecast_pv_kwh": 1.0, "forecast_load_kwh": 2.0}
        for hour in range(24)
    }
    before = copy.deepcopy(client.data)
    assert recover(client)["recovered_days"] == 0
    assert client.plan_reads == []
    assert client.data == before and client.writes == 0


def test_mutable_pv_and_load_use_one_vintage_even_with_stale_daily_pv():
    rows = [{"date": DAY, "hour": hour, "forecast_pv_kwh": 0.1, "forecast_load_kwh": 0.2,
             "source": "forecast-only-hourly", "forecast_run_id": "current"} for hour in range(24)]
    energy = _build_energy_daily(
        start_date=DAY, end_date_iso=DAY, pv_daily=[{"date": DAY, "forecast_pv_total_kwh": 999.0}],
        monitoring_daily=[], forecast_hourly=rows,
    )[0]
    assert energy["forecast_pv_kwh"] == pytest.approx(2.4)
    assert energy["forecast_pv_source"] == energy["forecast_load_source"] == "forecast-only-hourly"
    assert energy["forecast_run_id"] == "current"


@pytest.mark.parametrize("first_hour", [5, 7])
def test_original_model_night_zero_remains_valid(tmp_path, first_hour):
    value = plan()
    value["daytime_soc_optimization"]["hourly_pv_forecast_kwh"] = {str(hour): 0.1 for hour in range(first_hour, 23)}
    client = Client(value)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert persist_forecast_only_plan(client, plan_path=path, target_date=DAY, timezone_name="Asia/Tokyo") == 24
    assert client.data["forecast_hourly"][f"{DAY}-00"]["forecast_pv_kwh"] == 0.0


def test_explicit_vintage_mismatch_cannot_attach_same_timestamp_metadata():
    from app.dashboard.history_reconstruction import _metadata_matches_forecast_row
    assert not _metadata_matches_forecast_row(
        {"forecast_run_id": "selected", "updated_at": "same"},
        {"_forecast_run_id": "other", "_forecast_updated_at": "same"},
    )


def test_recovery_precedes_actual_refresh_and_current_publication(monkeypatch):
    from app.runtime import forecast_job
    client = Client(plan())
    events = []
    monkeypatch.setattr(forecast_job, "_target_date", lambda: "2026-10-07")
    monkeypatch.setattr(forecast_job, "open_firestore", lambda: client)
    def recovery(_client, **kwargs):
        events.append("recovery")
        return recover(client)
    def run(command, env, **kwargs):
        assert len(client.data["forecast_hourly_snapshots"]) == 24
        events.append(command[1])
    def persist(_client, **kwargs):
        events.append("current_saved")
        return 24
    def publish(path):
        assert events[-1] == "current_saved"
        events.append("publish")
        return {}
    monkeypatch.setattr(forecast_job, "recover_missing_forecast_snapshots", recovery)
    monkeypatch.setattr(forecast_job, "_run", run)
    monkeypatch.setattr(forecast_job, "persist_forecast_only_plan", persist)
    monkeypatch.setattr(forecast_job, "dashboard_snapshot_prefix", lambda: "unit-test")
    monkeypatch.setattr(forecast_job, "write_dashboard_snapshots", publish)
    assert forecast_job.main() == 0
    assert events == ["recovery", "kpnet_main.py", "db_pipeline_main.py", "energy_model_main.py", "current_saved", "publish"]


@pytest.mark.parametrize("problem", ["missing_load", "missing_pv", "nan", "late", "no_issue_time", "wrong_date"])
def test_invalid_archive_never_becomes_original_evidence(problem):
    value = plan()
    if problem == "missing_load":
        value["daytime_soc_optimization"]["hourly_load_forecast_kwh"].pop("12")
    elif problem == "missing_pv":
        value["daytime_soc_optimization"]["hourly_pv_forecast_kwh"].pop("12")
    elif problem == "nan":
        value["daytime_soc_optimization"]["hourly_load_forecast_kwh"]["12"] = float("nan")
    elif problem == "late":
        value["generated_at"] = "2026-10-06T18:00:00+09:00"
    elif problem == "no_issue_time":
        value.pop("generated_at")
    else:
        value["forecast"]["date"] = "2026-10-05"
    client = Client(value)
    assert recover(client)["rejected_days"] == 1
    assert client.writes == 0


def test_corrupt_archive_is_observable_and_does_not_write(monkeypatch, capsys):
    client = Client(plan())
    def corrupt(_doc):
        raise ValueError("checksum mismatch: gs://private-location")
    monkeypatch.setattr("app.operations.forecast_recovery.load_night_plan_detail_from_firestore_doc", corrupt)
    assert recover(client)["rejected_days"] == 1
    assert client.writes == 0
    output = capsys.readouterr().out
    assert "rejected" in output and "ValueError" in output and "private-location" not in output


@pytest.mark.parametrize("field", ["hourly_pv_forecast_kwh", "hourly_load_forecast_kwh"])
def test_missing_value_with_full_weather_cannot_replace_valid_forecast(tmp_path, field):
    client = Client(plan())
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan()), encoding="utf-8")
    persist_forecast_only_plan(client, plan_path=path, target_date=DAY, timezone_name="Asia/Tokyo")
    before = copy.deepcopy(client.data)
    invalid = plan()
    invalid["daytime_soc_optimization"][field].pop("12")
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValueError):
        persist_forecast_only_plan(client, plan_path=path, target_date=DAY, timezone_name="Asia/Tokyo")
    assert client.data == before


def test_source_locks_and_bounded_recovery_remain_present():
    for path in (
        "app/operations/domain.py", "app/operations/forecast_persistence.py",
        "app/operations/forecast_recovery.py", "app/runtime/forecast_job.py",
        "app/dashboard/aggregation.py", "app/dashboard/firestore_repository.py",
        "app/dashboard/history_reconstruction.py",
    ):
        assert "HISTORICAL_FAILURE_LOCK" in Path(path).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="between 1 and 31"):
        recover_missing_forecast_snapshots(Client(plan()), target_date="2026-10-07", recorded_at="", lookback_days=32)
