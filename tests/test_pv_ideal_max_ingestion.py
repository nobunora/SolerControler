"""Production CSV -> shared C -> preserved issued-hour evaluation."""

import csv
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import PreconditionFailed

from app.forecasting import pv_ideal_max as model
from app.forecasting.pv_ideal_max_store import IdealMaxStore
from app.operations import pv_ideal_max as ingestion
from app.operations.pv_ideal_max_evaluation import evaluate_ideal_max_hours

START = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


@pytest.fixture
def geometry(monkeypatch):
    monkeypatch.setattr(model, "ideal_max_base", lambda *_: 1.0)
    return model.IdealMaxGeometry(35, 135, "UTC", (model.IdealMaxArray(180, 20, 2, 0.8, 1),))


def write_csv(tmp_path, values=("0.6", "0.6")):
    path = tmp_path / "actual.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["年月日", "時刻", "発電電力量[kWh]", "買電電力量[kWh]", "売電電力量[kWh]"])
        writer.writerows([["2026/10/30", "12:00", values[0], "0", "0"], ["2026/10/30", "12:30", values[1], "0", "0"]])
    return path


def test_csv_ingest_scores_original_issued_value_and_duplicate_keeps_it(tmp_path, geometry):
    path = write_csv(tmp_path)
    with IdealMaxStore(tmp_path / "state", geometry, initialize_at=START) as store:
        store.predict_hour(START, as_of=START)
        result = ingestion.ingest_ideal_max_csvs(store, [path], as_of=START + timedelta(hours=1), issue_clock=lambda: START + timedelta(hours=1, seconds=5))
        assert result["new_hours"] == result["evaluated_issued_hours"] == result["issued_exceedances"] == 1
        assert result["coefficient"] == 1.2
        assert datetime.fromisoformat(result["issued_at"]) == START + timedelta(hours=1, seconds=5)
        repeated = ingestion.ingest_ideal_max_csvs(store, [path], as_of=START + timedelta(hours=2), issue_clock=lambda: START + timedelta(hours=2))
        assert repeated["duplicate_hours"] == 1 and repeated["new_hours"] == 0
        report = evaluate_ideal_max_hours(store, START, START + timedelta(hours=2), as_of=START + timedelta(hours=2))
        assert report["evaluated_hours"] == report["exceedances"] == report["pending_actual_hours"] == 1
        assert report["hourly"][0]["issued_maximum_kwh"] == 1
        assert report["hourly"][0]["actual_kwh"] == 1.2
        assert report["hourly"][1]["actual_kwh"] is None
        assert len(report["hourly"]) == 2
        uncertain = evaluate_ideal_max_hours(store, START, START + timedelta(hours=2),
                                              as_of=START + timedelta(hours=2), issued_not_before=START + timedelta(seconds=1))
        assert uncertain["evaluated_hours"] == 0 and uncertain["unverified_issue_hours"] == 1
        assert store.observation_record(START)["issued_prediction"]["maximum_kwh"] == 1


def test_received_history_without_issued_maximum_is_not_predictive_evidence(tmp_path, geometry):
    path = write_csv(tmp_path, ("0", "0"))
    with IdealMaxStore(tmp_path / "state", geometry, initialize_at=START) as store:
        result = ingestion.ingest_ideal_max_csvs(store, [path], as_of=START + timedelta(hours=1), issue_clock=lambda: START + timedelta(hours=1))
        assert result["actual_without_issued_prediction"] == 1
        report = evaluate_ideal_max_hours(store, START, START + timedelta(hours=2), as_of=START + timedelta(hours=1))
        assert report["status"] == "NO_PREISSUED_MAXIMUM"
        assert report["evaluated_hours"] == 0 and report["no_preissued_hours"] == 1
        assert report["future_hours"] == 1
        with pytest.raises(ValueError, match="31 days"):
            evaluate_ideal_max_hours(store, START, START + timedelta(days=32), as_of=START)


def test_seed_predictions_before_initialization_are_not_certified(tmp_path, geometry):
    path = write_csv(tmp_path)
    with IdealMaxStore(tmp_path / "state", geometry, initialize_at=START) as store:
        store.predict_hour(START, as_of=START)
        result = ingestion.ingest_ideal_max_csvs(store, [path], as_of=START + timedelta(hours=1),
            issue_clock=lambda: START + timedelta(hours=1), issued_not_before=START + timedelta(minutes=5))
        assert result["new_hours"] == result["unverified_issue_hours"] == 1
        assert result["evaluated_issued_hours"] == 0
        assert result["actual_without_issued_prediction"] == 0
        assert result["coefficient"] == 1.2


@pytest.mark.parametrize("issue_minutes,verified", [(-20, False), (-10, True), (-5, True)])
def test_post_initialization_hour_requires_a_post_initialization_issue(tmp_path, geometry, issue_minutes, verified):
    path = write_csv(tmp_path)
    boundary = START - timedelta(minutes=10)
    issued_at = START + timedelta(minutes=issue_minutes)
    with IdealMaxStore(tmp_path / "state", geometry, initialize_at=START - timedelta(hours=1)) as store:
        store.predict_hour(START, as_of=issued_at)
        result = ingestion.ingest_ideal_max_csvs(store, [path], as_of=START + timedelta(hours=1),
            issue_clock=lambda: START + timedelta(hours=1), issued_not_before=boundary)
        assert result["new_hours"] == 1
        assert result["evaluated_issued_hours"] == int(verified)
        assert result["unverified_issue_hours"] == int(not verified)
        assert result["issued_exceedances"] == int(verified)
        assert result["coefficient"] == 1.2
        record = store.observation_record(START)
        assert record["exceeded"] is True
        assert record["issued_prediction"]["issued_at"] == issued_at.isoformat()
        report = evaluate_ideal_max_hours(store, START, START + timedelta(hours=1),
            as_of=START + timedelta(hours=1), issued_not_before=boundary)
        assert report["evaluated_hours"] == int(verified)
        assert report["unverified_issue_hours"] == int(not verified)
        assert report["hourly"][0]["status"] == ("EXCEEDED" if verified else "UNVERIFIED_ISSUE_TIME")


@pytest.mark.parametrize("backend", ["sqlite", "postgres", "firestore"])
def test_pipeline_calls_maximum_update_after_backend_success_including_duplicate_import(monkeypatch, tmp_path, backend):
    from app.operations import workflow
    cfg = SimpleNamespace(artifacts_dir=tmp_path, slot="manual-csv", write_only_slot_23=False,
                          data_backend=backend, storage_sync_enabled=False)
    monkeypatch.setattr(workflow.sqlite_ops.PipelineConfig, "from_env", lambda: cfg)
    monkeypatch.setattr(workflow.sqlite_ops, "find_latest_csv_and_settings_runs", lambda _: (tmp_path, None))
    events = []
    monkeypatch.setattr(workflow, f"_ingest_{backend}", lambda *_args, **_kwargs: events.append("raw_import_finished"))
    monkeypatch.setattr(workflow, "_update_ideal_max_after_csv", lambda *_args: events.append("maximum_updated"))
    assert workflow.main() == 0
    assert events == ["raw_import_finished", "maximum_updated"]
    events.clear()
    assert workflow.main() == 0
    assert events == ["raw_import_finished", "maximum_updated"]


def test_inactive_local_feature_does_not_load_forecast_configuration_or_cloud(monkeypatch, tmp_path):
    monkeypatch.setattr(ingestion, "night_plan_archive_prefix", lambda: "")
    monkeypatch.setattr(ingestion, "configured_ideal_max_geometry", lambda: pytest.fail("uninitialized feature"))
    assert ingestion.update_ideal_max_after_import([], artifacts_dir=tmp_path, as_of=START)["status"] == "uninitialized"


@pytest.mark.parametrize("backend", ["sqlite", "postgres", "firestore"])
@pytest.mark.parametrize("error_type", [TimeoutError, ValueError, OSError, PreconditionFailed])
def test_optional_ledger_failure_does_not_fail_completed_import(monkeypatch, tmp_path, capsys, backend, error_type):
    from app.operations import workflow
    (tmp_path / "kpnet_summary.json").write_text("{}", encoding="utf-8")
    cfg = SimpleNamespace(artifacts_dir=tmp_path, slot="manual-csv", write_only_slot_23=False,
                          data_backend=backend, storage_sync_enabled=False)
    monkeypatch.setattr(workflow.sqlite_ops.PipelineConfig, "from_env", lambda: cfg)
    monkeypatch.setattr(workflow.sqlite_ops, "find_latest_csv_and_settings_runs", lambda _: (tmp_path, None))
    events = []
    monkeypatch.setattr(workflow, f"_ingest_{backend}", lambda *_args, **_kwargs: events.append("raw_import_finished"))

    def fail_ledger(*_args, **_kwargs):
        events.append("ledger_failed")
        raise error_type("private storage identifier must not appear in public logs")

    monkeypatch.setattr(workflow, "update_ideal_max_after_import", fail_ledger)
    assert workflow.main() == 0
    assert events == ["raw_import_finished", "ledger_failed"]
    output = capsys.readouterr().out
    event = json.loads(next(line for line in output.splitlines() if '"pv_ideal_max_import"' in line))
    assert event == {"event": "pv_ideal_max_import", "severity": "ERROR", "status": "failed", "error_type": error_type.__name__}
    assert "private storage identifier" not in output


@pytest.mark.parametrize("backend", ["sqlite", "postgres", "firestore"])
def test_primary_import_failure_still_stops_and_never_calls_ledger(monkeypatch, tmp_path, backend):
    from app.operations import workflow
    cfg = SimpleNamespace(artifacts_dir=tmp_path, slot="manual-csv", write_only_slot_23=False,
                          data_backend=backend, storage_sync_enabled=False)
    monkeypatch.setattr(workflow.sqlite_ops.PipelineConfig, "from_env", lambda: cfg)
    monkeypatch.setattr(workflow.sqlite_ops, "find_latest_csv_and_settings_runs", lambda _: (tmp_path, None))

    def fail_import(*_args, **_kwargs):
        raise ValueError("primary import failed")

    monkeypatch.setattr(workflow, f"_ingest_{backend}", fail_import)
    monkeypatch.setattr(workflow, "update_ideal_max_after_import", lambda *_args, **_kwargs: pytest.fail("ledger must not run"))
    with pytest.raises(ValueError, match="primary import failed"):
        workflow.main()
