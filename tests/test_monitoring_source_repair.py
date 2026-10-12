from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.operations import firestore as firestore_ops
from app.operations import sqlite as sqlite_ops
from app.operations import workflow
from test_incremental_monitoring_backends import (
    _Collection,
    _FirestoreClient,
    _Query,
    _monitoring_payload,
    _write_csv,
)


DAY = "2026-09-20"
NOW = "2026-09-20T07:00:00Z"
ENERGY_FIELDS = ("pv_kwh", "load_kwh", "buy_kwh", "sell_kwh", "charge_kwh", "discharge_kwh")
DAILY_FIELDS = ("actual_pv_kwh", "actual_load_kwh", *ENERGY_FIELDS[2:])


class _FullCollection(_Collection):
    def stream(self):
        return _Query(self.client, self.name).stream()


class _FullClient(_FirestoreClient):
    """Only the explicitly requested historical full materializer may scan raw."""

    def collection(self, name: str) -> _FullCollection:
        return _FullCollection(self, name)


def _materialize(client: _FirestoreClient, *, full: bool = False, day: str = DAY) -> None:
    if full:
        firestore_ops.recalc_dashboard_daily_metrics(client, updated_at=NOW)
    else:
        firestore_ops.recalc_monitoring_daily_metrics(
            client, calendar_dates={day}, dashboard_affected_dates={day}, updated_at=NOW,
        )


def _day_rows(*, date: str = "2026/09/20") -> list[str]:
    return [f"{date},{slot // 2:02d}:{30 * (slot % 2):02d},1,2,0,0,0,0,50" for slot in range(48)]


def _run_dir(tmp_path: Path, name: str, rows: list[str]) -> Path:
    run = tmp_path / name
    run.mkdir()
    csv = _write_csv(run / "monitor.csv", rows)
    (run / "kpnet_summary.json").write_text(
        json.dumps({"csv_downloads": [{"path": str(csv)}]}), encoding="utf-8",
    )
    return run


@pytest.fixture
def workflow_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _FirestoreClient()
    completed: set[str] = set()
    cost_calls: list[dict[str, Any]] = []
    refresh_calls: list[Any] = []
    monkeypatch.delenv("DATA_MONITORING_FULL_BACKFILL", raising=False)
    monkeypatch.setattr(firestore_ops, "open_firestore", lambda: client)
    monkeypatch.setattr(firestore_ops, "ensure_schema", lambda _client: None)
    monkeypatch.setattr(firestore_ops, "pipeline_run_exists", lambda _client, *, run_key: run_key in completed)
    monkeypatch.setattr(firestore_ops, "upsert_pipeline_run", lambda _client, **kw: completed.add(kw["run_key"]))
    monkeypatch.setattr(firestore_ops, "recalc_model_hit_rates", lambda *a, **kw: None)

    def cost(_client, **kwargs):
        cost_calls.append(kwargs)
        return 0

    monkeypatch.setattr(firestore_ops, "recalc_cost_daily_from", cost)
    monkeypatch.setattr(workflow, "_refresh_dashboard_snapshots", refresh_calls.append)

    def forbidden(*args, **kwargs):
        raise AssertionError("actual-only repair must not invoke forecast/settings/backup producers")

    for symbol in ("ingest_sunshine_from_night_plan", "upsert_model_parameters_from_plan", "ingest_settings_summary"):
        monkeypatch.setattr(firestore_ops, symbol, forbidden)
    monkeypatch.setattr(workflow, "persist_forecast_snapshots", forbidden)
    monkeypatch.setattr(workflow, "create_weekly_diff_backup", forbidden)
    cfg = cast(sqlite_ops.PipelineConfig, SimpleNamespace(
        site_id="test-site", slot="manual-csv", artifacts_dir=tmp_path, timezone="Asia/Tokyo",
        day_rate_yen_per_kwh=31.0, cost_tariff_mode="flat",
        night8_day_start_hhmm="07:00", night8_day_end_hhmm="23:00",
        night8_day_tier1_upper_kwh=90.0, night8_day_tier2_upper_kwh=230.0,
        night8_day_rate_tier1_yen=31.8, night8_day_rate_tier2_yen=39.1,
        night8_day_rate_tier3_yen=43.62, night8_night_rate_yen=28.85,
    ))

    def ingest(run: Path, *, now: str = NOW) -> None:
        workflow._ingest_firestore(cfg, csv_run_dir=run, settings_run_dir=None, now_iso=now, include_night_plan=False)

    return client, ingest, cost_calls, refresh_calls


@pytest.mark.parametrize("invalid", [None, float("nan"), float("inf"), -float("inf"), True, False, -1.0])
@pytest.mark.parametrize("full", [False, True], ids=["bounded", "full"])
def test_persisted_invalid_energy_stays_unknown_and_never_becomes_zero(invalid: Any, full: bool) -> None:
    ts = f"{DAY}T00:00:00"
    bad = {**_monitoring_payload(ts), **dict.fromkeys(ENERGY_FIELDS, invalid)}
    good_ts = f"{DAY}T00:30:00"
    client = _FullClient({"monitoring_samples": {ts: bad, good_ts: _monitoring_payload(good_ts)}})
    _materialize(client, full=full)
    metrics = client.data["dashboard_daily_metrics"][DAY]
    assert all(metrics[field] is None for field in DAILY_FIELDS)
    assert metrics["night_buy_kwh"] is None
    assert metrics["review_night_charge_kwh"] is None
    assert metrics["sample_count"] == 2
    assert client.data["monitoring_samples"][ts] == bad
    assert not [w for w in client.writes if w[0] == "monitoring_samples"]


@pytest.mark.parametrize("full", [False, True], ids=["bounded", "full"])
def test_observed_zero_is_preserved_but_absent_night_observation_is_unknown(full: bool) -> None:
    noon = f"{DAY}T12:00:00"
    client = _FullClient({"monitoring_samples": {noon: {**_monitoring_payload(noon), **dict.fromkeys(ENERGY_FIELDS, 0.0)}}})
    _materialize(client, full=full)
    metrics = client.data["dashboard_daily_metrics"][DAY]
    assert all(metrics[field] == 0.0 for field in DAILY_FIELDS)
    assert metrics["day_buy_kwh"] == 0.0
    assert metrics["night_buy_kwh"] is None
    assert metrics["review_night_charge_kwh"] is None
    midnight = f"{DAY}T00:00:00"
    client.data["monitoring_samples"][midnight] = {**_monitoring_payload(midnight), **dict.fromkeys(ENERGY_FIELDS, 0.0)}
    _materialize(client, full=full)
    metrics = client.data["dashboard_daily_metrics"][DAY]
    assert metrics["review_night_charge_kwh"] == metrics["day_buy_kwh"] == metrics["night_buy_kwh"] == 0.0


@pytest.mark.parametrize("full", [False, True], ids=["bounded", "full"])
def test_night_only_observation_does_not_fabricate_day_purchase_zero(full: bool) -> None:
    ts = f"{DAY}T00:00:00"
    client = _FullClient({"monitoring_samples": {ts: {**_monitoring_payload(ts), "buy_kwh": 0.0}}})
    _materialize(client, full=full)
    metrics = client.data["dashboard_daily_metrics"][DAY]
    assert metrics["buy_kwh"] == metrics["night_buy_kwh"] == 0.0
    assert metrics["day_buy_kwh"] is None


def test_full_day_csv_measured_zero_is_preserved_for_both_purchase_windows(
    tmp_path: Path, workflow_fixture,
) -> None:
    client, ingest, _cost, _refresh = workflow_fixture
    ingest(_run_dir(tmp_path, "csv-observed-zero", _day_rows()))
    metrics = client.data["dashboard_daily_metrics"][DAY]
    assert metrics["sample_count"] == 48
    assert metrics["buy_kwh"] == metrics["day_buy_kwh"] == metrics["night_buy_kwh"] == 0.0
    assert metrics["review_night_charge_kwh"] == 0.0


@pytest.mark.parametrize("csv_value", ["", "NaN", "inf", "-1", "true"])
def test_real_csv_missing_energy_reaches_persisted_daily_without_fabricated_zero(
    tmp_path: Path, workflow_fixture, csv_value: str,
) -> None:
    client, ingest, _cost, _refresh = workflow_fixture
    rows = _day_rows()
    rows[24] = f"2026/09/20,12:00,{csv_value},2,0,0,0,0,50"
    ingest(_run_dir(tmp_path, "csv-invalid", rows))
    metrics = client.data["dashboard_daily_metrics"][DAY]
    assert metrics["actual_pv_kwh"] is None
    assert metrics["actual_load_kwh"] == 96.0
    assert metrics["sample_count"] == 48
    assert metrics["buy_kwh"] == metrics["sell_kwh"] == metrics["review_night_charge_kwh"] == 0.0
    raw = client.data["monitoring_samples"][f"{DAY}T12:00:00"]["pv_kwh"]
    assert raw == (-1.0 if csv_value == "-1" else None)


@pytest.mark.parametrize("same_run", [False, True], ids=["new-directory", "same-completed-directory"])
def test_unchanged_csv_retry_repairs_daily_without_raw_rewrite_or_cost_recalculation(
    tmp_path: Path, workflow_fixture, same_run: bool,
) -> None:
    client, ingest, cost_calls, _refresh = workflow_fixture
    first = _run_dir(tmp_path, "csv-first", _day_rows())
    ingest(first)
    assert len([w for w in client.writes if w[0] == "monitoring_samples"]) == 48
    original_raw = {ts: dict(row) for ts, row in client.data["monitoring_samples"].items()}
    client.data["dashboard_daily_metrics"][DAY].update(actual_pv_kwh=999, actual_load_kwh=0)
    client.writes.clear()
    cost_calls.clear()
    retry = first if same_run else _run_dir(tmp_path, "csv-retry", _day_rows())
    ingest(retry, now="2026-09-20T08:00:00Z")
    metrics = client.data["dashboard_daily_metrics"][DAY]
    assert metrics["actual_pv_kwh"] == 48.0
    assert metrics["actual_load_kwh"] == 96.0
    assert metrics["sample_count"] == 48
    assert metrics["updated_at"] == "2026-09-20T08:00:00Z"
    assert client.data["monitoring_samples"] == original_raw
    assert not [w for w in client.writes if w[0] == "monitoring_samples"]
    assert any(w[0] == "dashboard_daily_metrics" for w in client.writes)
    assert cost_calls == []
    assert all(filters for name, filters in client.query_calls if name == "monitoring_samples")


@pytest.mark.parametrize("same_run", [False, True], ids=["new-directory", "same-completed-directory"])
def test_full_backfill_materializes_all_csv_dates_including_unchanged_history(
    tmp_path: Path, workflow_fixture, monkeypatch: pytest.MonkeyPatch, same_run: bool,
) -> None:
    client, ingest, cost_calls, _refresh = workflow_fixture
    monkeypatch.setenv("DATA_MONITORING_FULL_BACKFILL", "true")
    rows = _day_rows(date="2026/05/01") + _day_rows()
    first = _run_dir(tmp_path, "csv-backfill", rows)
    ingest(first)
    client.data["dashboard_daily_metrics"]["2026-05-01"]["actual_pv_kwh"] = 999
    client.data["dashboard_daily_metrics"][DAY]["actual_load_kwh"] = 0
    client.writes.clear()
    cost_calls.clear()
    retry = first if same_run else _run_dir(tmp_path, "csv-backfill-retry", rows)
    ingest(retry)
    assert client.data["dashboard_daily_metrics"]["2026-05-01"]["actual_pv_kwh"] == 48.0
    assert client.data["dashboard_daily_metrics"][DAY]["actual_load_kwh"] == 96.0
    assert {w[1] for w in client.writes if w[0] == "dashboard_daily_metrics"} == {"2026-05-01", DAY}
    assert not [w for w in client.writes if w[0] == "monitoring_samples"]
    assert cost_calls == []
    assert all(filters for name, filters in client.query_calls if name == "monitoring_samples")


def test_csv_23_hour_correction_repairs_next_day_night_window_only_from_saved_source(
    tmp_path: Path, workflow_fixture,
) -> None:
    client, ingest, _cost, _refresh = workflow_fixture
    original = ["2026/09/19,23:00,0,0,0,0,1,0,50", "2026/09/20,00:00,0,0,0,0,2,0,50"]
    ingest(_run_dir(tmp_path, "csv-night", original))
    assert client.data["dashboard_daily_metrics"][DAY]["review_night_charge_kwh"] == 3.0
    client.writes.clear()
    ingest(_run_dir(tmp_path, "csv-correction", ["2026/09/19,23:00,0,0,0,0,4,0,50"]))
    assert client.data["dashboard_daily_metrics"][DAY]["review_night_charge_kwh"] == 6.0
    assert client.data["dashboard_daily_metrics"]["2026-09-19"]["charge_kwh"] == 4.0
    assert client.data["dashboard_daily_metrics"][DAY]["charge_kwh"] == 2.0
    assert [w[1] for w in client.writes if w[0] == "monitoring_samples"] == ["2026-09-19T23:00:00"]


@pytest.mark.parametrize("conflict", [False, True], ids=["identical", "conflicting"])
def test_csv_duplicates_do_not_inflate_persisted_sample_count_or_energy(
    tmp_path: Path, workflow_fixture, conflict: bool,
) -> None:
    client, ingest, _cost, _refresh = workflow_fixture
    rows = _day_rows()
    rows.append("2026/09/20,12:00,9,2,0,0,0,0,50" if conflict else rows[24])
    ingest(_run_dir(tmp_path, "csv-duplicates", rows))
    expected = 47 if conflict else 48
    metrics = client.data["dashboard_daily_metrics"][DAY]
    assert metrics["sample_count"] == expected
    assert metrics["actual_pv_kwh"] == float(expected)
    assert metrics["actual_load_kwh"] == float(expected * 2)
    assert len(client.data["monitoring_samples"]) == expected
    assert (f"{DAY}T12:00:00" in client.data["monitoring_samples"]) == (not conflict)
