from __future__ import annotations

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from datetime import datetime, timedelta
from threading import Thread
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.dashboard import actual_import
from app.dashboard.server import Handler
from app.operations import actual_import as worker


@pytest.fixture(autouse=True)
def fixed_date(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")).astimezone(tz)

    monkeypatch.setattr(worker, "datetime", FixedDateTime)
    monkeypatch.setattr(actual_import, "datetime", FixedDateTime)


def test_web_calls_only_fixed_import_job_with_date_only_overrides(monkeypatch):
    monkeypatch.setenv("DASHBOARD_ACTUAL_IMPORT_JOB", "projects/example/locations/asia-northeast1/jobs/solar-actual-import")
    monkeypatch.setattr(actual_import.google.auth, "default", lambda **_: (object(), None))
    calls = []

    class Session:
        def __init__(self, credentials, **kwargs):
            assert kwargs == {"max_refresh_attempts": 0}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"metadata": {"name": "solar-actual-import-abc12"}})

    monkeypatch.setattr(actual_import, "AuthorizedSession", Session)
    assert actual_import.start_actual_import("2026-11-03")["execution"] == "solar-actual-import-abc12"
    assert len(calls) == 1
    assert calls[0][1] == {"json": {"overrides": {"containerOverrides": [{"env": [
        {"name": "ACTUAL_IMPORT_TARGET_DATE", "value": "2026-11-03"}]}]}}, "timeout": 20, "allow_redirects": False}
    assert calls[0][0].endswith("/jobs/solar-actual-import:run")


def test_unknown_submission_is_not_retried(monkeypatch):
    monkeypatch.setenv("DASHBOARD_ACTUAL_IMPORT_JOB", "projects/example/locations/asia-northeast1/jobs/solar-actual-import")
    monkeypatch.setattr(actual_import.google.auth, "default", lambda **_: (object(), None))
    calls = []

    class Session:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, *_args, **_kwargs):
            calls.append(1)
            raise TimeoutError

    monkeypatch.setattr(actual_import, "AuthorizedSession", Session)
    with pytest.raises(TimeoutError):
        actual_import.start_actual_import()
    assert len(calls) == 1


@pytest.mark.parametrize("headers,authorized,expected", [
    ({}, True, 403),
    ({"Content-Type": "application/json", "X-Dashboard-Action": "import-actuals", "Sec-Fetch-Site": "cross-site"}, True, 403),
    ({"Content-Type": "application/json", "X-Dashboard-Action": "import-actuals"}, False, 401),
    ({"Content-Type": "application/json", "X-Dashboard-Action": "import-actuals"}, True, 202),
])
def test_import_http_requires_authorization_and_explicit_action(monkeypatch, headers, authorized, expected):
    monkeypatch.setattr(Handler, "_is_authorized", lambda *_: authorized)
    calls = []
    monkeypatch.setattr("app.dashboard.server.start_actual_import", lambda target: calls.append(target) or {"status": "accepted"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection(*server.server_address)
        connection.request("POST", "/api/dashboard/actual-import", body="{}", headers=headers)
        response = connection.getresponse()
        assert response.status == expected
        assert json.loads(response.read())
        assert len(calls) == (1 if expected == 202 else 0)
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("busy,fail", [(False, False), (False, True), (True, False)])
def test_worker_only_imports_actuals_and_publishes_after_success(monkeypatch, tmp_path, busy, fail):
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", "solar-actual-import-abc12")
    monkeypatch.setenv("ACTUAL_IMPORT_TARGET_DATE", "2026-11-03")
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    writes = []
    client = SimpleNamespace(collection=lambda name: SimpleNamespace(document=lambda key: SimpleNamespace(
        set=lambda value, **_: writes.append((name, key, value)))))
    monkeypatch.setattr(worker.source, "open_firestore", lambda: client)
    monkeypatch.setattr(worker, "_claim", lambda *_: not busy)
    monkeypatch.setattr(worker, "capture_date_refresh", lambda *_: {})
    monkeypatch.setattr(worker, "review_missing_actuals", lambda *_: {"status": "checked", "recoverable_dates": [], "unavailable_dates": []})
    saves = []
    monkeypatch.setattr(worker, "save_date_actuals", lambda *_: saves.append(1) or [])
    commands = []

    def run(command, overrides, **kwargs):
        commands.append((command, overrides))
        if fail:
            raise RuntimeError("synthetic import failure")

    monkeypatch.setattr(worker, "_run", run)
    publications = []
    monkeypatch.setattr(worker, "publish_date_refresh", lambda *args, **kwargs: publications.append(1))
    if fail:
        with pytest.raises(RuntimeError):
            worker.main()
    else:
        assert worker.main() == (1 if busy else 0)
    assert [command[-1] for command, _ in commands] == ([] if busy else ["kpnet_main.py"])
    for _, overrides in commands:
        assert overrides["KP_WORKFLOW_MODE"] == "csv"
        assert overrides["KP_CSV_TARGET_MONTHS"] == "2026-11"
        assert overrides["KP_DOWNLOAD_LATEST_MONTH"] == "false"
    assert len(publications) == (0 if busy or fail else 1)
    assert not any(name in {"forecast_hourly", "forecast_plans", "control_plan_current"} for name, _, _ in writes)


def test_missing_month_review_separates_recoverable_zero_and_provider_gap(tmp_path):
    csv_dir = tmp_path / "20261103-120000" / "csv"
    csv_dir.mkdir(parents=True)
    (csv_dir / "sample.csv").write_text("年月日,時刻,発電電力量[kWh]\n2026/11/01,06:00,0\n", encoding="utf-8-sig")
    missing = {"2026-11-01T06:00:00", "2026-11-02T06:30:00"}
    stamps = [(datetime(2026, 10, 4) + timedelta(minutes=30*i)).isoformat() for i in range(30 * 48)]

    class Query:
        def where(self, *_):
            return self

        def select(self, fields):
            assert fields == ["ts"]
            return self

        def limit(self, count):
            assert count == 1489
            return self

        def stream(self, **kwargs):
            assert kwargs == {"timeout": 4, "retry": None}
            return [SimpleNamespace(to_dict=lambda ts=ts: {"ts": ts}) for ts in stamps if ts not in missing]

    client = SimpleNamespace(collection=lambda name: Query() if name == "monitoring_samples" else None)
    result = worker.review_missing_actuals(client, "2026-11-03", tmp_path)
    assert result == {"status": "checked", "recoverable_dates": ["2026-11-01"], "unavailable_dates": ["2026-11-02"], "unverified_dates": []}


def test_missing_month_review_marks_earlier_month_csv_unverified(tmp_path):
    csv_dir = tmp_path / "20261101-120000" / "csv"
    csv_dir.mkdir(parents=True)
    (csv_dir / "sample.csv").write_text("年月日,時刻,発電電力量[kWh]\n2026/11/01,06:00,0\n", encoding="utf-8-sig")
    missing = "2026-10-31T06:00:00"
    stamps = [(datetime(2026, 10, 2) + timedelta(minutes=30*i)).isoformat() for i in range(30 * 48)]

    class Query:
        def where(self, *_): return self
        def select(self, _): return self
        def limit(self, _): return self
        def stream(self, **_):
            return [SimpleNamespace(to_dict=lambda ts=ts: {"ts": ts}) for ts in stamps if ts != missing]

    result = worker.review_missing_actuals(SimpleNamespace(collection=lambda _: Query()), "2026-11-01", tmp_path)
    assert result == {"status": "checked", "recoverable_dates": [], "unavailable_dates": [],
                      "unverified_dates": ["2026-10-31"]}


def test_retained_night_forecasts_use_existing_read_and_ignore_late_replay():
    from app.dashboard.firestore_repository import _snapshot_forecast_rows_between

    rows = []
    for run, issued, soc, charge in [("night", "2026-11-02T23:00:00+09:00", 0, 1.2),
                                     ("partial", "2026-11-03T02:30:00+09:00", 40, None),
                                     ("late", "2026-11-03T08:31:00+09:00", 99, 9.9)]:
        rows.extend({"date": "2026-11-03", "hour": hour, "forecast_run_id": run, "issued_at": issued,
                     "forecast_pv_kwh": 0, "forecast_load_kwh": 0.2,
                     "forecast_soc_percent": soc if hour < 7 else None,
                     "forecast_grid_charge_kwh": charge if hour < 7 else None} for hour in range(24))
    streams = []

    class Query:
        def where(self, *_):
            return self

        def stream(self):
            streams.append(1)
            return [SimpleNamespace(to_dict=lambda row=row: row) for row in rows]

    client = SimpleNamespace(collection=lambda name: Query())
    retained = {}
    assert _snapshot_forecast_rows_between(client, start_date="2026-11-03", end_date_iso="2026-11-03",
                                           mutable_dates={"2026-11-03"}, retained_night=retained) == []
    assert len(streams) == 1
    assert len(retained) == 7
    assert all(row["retained_forecast_grid_charge_kwh"] == 1.2
               and row["retained_forecast_grid_charge_run_id"] == "night" for row in retained.values())
    assert retained[("2026-11-03", 0)]["retained_forecast_soc_percent"] == 0
    assert retained[("2026-11-03", 3)]["retained_forecast_soc_percent"] == 40
    assert retained[("2026-11-03", 3)]["retained_forecast_soc_run_id"] == "partial"


def test_date_refresh_preserves_other_dates_and_saved_forecasts():
    from app.dashboard.date_snapshots import merge_date_payload

    previous = {"forecast_hourly": [
        {"date": "2026-11-03", "hour": 3, "forecast_soc_percent": None,
         "retained_forecast_soc_percent": 55, "retained_forecast_grid_charge_kwh": 1.2,
         "retained_forecast_run_id": "saved-night", "retained_forecast_issued_at": "2026-11-02T23:00:00+09:00"}],
        "energy_daily": [{"date": "2026-11-02", "actual_load_kwh": 4, "forecast_load_kwh": 5},
                         {"date": "2026-11-03", "actual_load_kwh": 2, "forecast_load_kwh": 6}], "meta": {}}
    fresh = {"forecast_hourly": [{"date": "2026-11-03", "hour": 3, "actual_soc_percent": 70}],
             "energy_daily": [{"date": "2026-11-02", "actual_load_kwh": 999},
                              {"date": "2026-11-03", "actual_load_kwh": 3}]}
    result = merge_date_payload(previous, fresh, "2026-11-03")
    assert result["energy_daily"][0] == previous["energy_daily"][0]
    assert result["energy_daily"][1] == {"date": "2026-11-03", "actual_load_kwh": 3, "forecast_load_kwh": 6}
    assert result["forecast_hourly"][0]["retained_forecast_soc_percent"] == 55
    assert result["forecast_hourly"][0]["retained_forecast_grid_charge_kwh"] == 1.2
    assert result["meta"]["snapshot_updated_dates"] == ["2026-11-03"]
    assert previous["energy_daily"][1]["actual_load_kwh"] == 2


def test_soc_actual_compares_exact_hour_and_does_not_substitute_half_hour():
    from app.dashboard.service import merge_forecast_hourly_actuals

    rows = merge_forecast_hourly_actuals([], [
        {"ts": "2026-11-03T07:00:00", "soc_percent": 0},
        {"ts": "2026-11-03T07:30:00", "soc_percent": 20},
        {"ts": "2026-11-03T08:30:00", "soc_percent": 30},
    ])
    assert rows[0]["actual_soc_at_hour_start_percent"] == 0
    assert rows[0]["actual_soc_percent"] == 20
    assert rows[1]["actual_soc_at_hour_start_percent"] is None


def test_midnight_import_adds_actual_only_day_without_yesterday_forecast():
    from app.dashboard.date_snapshots import merge_date_payload

    previous = {"meta": {"snapshot_kind": "bootstrap"}, "forecast_hourly": [
        {"date": "2026-11-02", "hour": 23, "forecast_soc_percent": 60}]}
    fresh = {"forecast_hourly": [{"date": "2026-11-03", "hour": 0, "actual_soc_percent": 55}]}
    result = merge_date_payload(previous, fresh, "2026-11-03")
    assert result["forecast_hourly"] == [*previous["forecast_hourly"], *fresh["forecast_hourly"]]
    history = merge_date_payload({"meta": {"snapshot_kind": "history"}, "forecast_hourly": []}, fresh, "2026-11-03")
    assert history["forecast_hourly"] == []


def test_adopted_plan_change_stops_import_publication(monkeypatch):
    from app.dashboard import date_snapshots

    saved = SimpleNamespace(version_id="new", sha256="new-sha")
    monkeypatch.setattr(date_snapshots.PublishedPlan, "from_document", lambda *_, **__: saved)
    client = SimpleNamespace(collection=lambda _: SimpleNamespace(document=lambda _: SimpleNamespace(
        get=lambda **_: SimpleNamespace(to_dict=lambda: {"payload": "{}"}))))
    with pytest.raises(ValueError, match="adopted plan changed"):
        date_snapshots._assert_current_plan(client, {"target_date": "2026-11-03", "control_version_id": "old", "raw_plan_sha256": "old-sha"})


def test_new_day_latest_battery_selection_is_not_an_unselected_source_change(monkeypatch):
    from app.dashboard import date_snapshots

    epochs = [{"battery_daily_metrics": "unchanged", "latest_battery": "previous-day"},
              {"battery_daily_metrics": "unchanged"}, {"battery_daily_metrics": "changed"}]
    monkeypatch.setattr(date_snapshots, "_read_dashboard_source_epoch", lambda *_, **__: epochs.pop(0))
    before = date_snapshots._outside_epoch(object(), "2026-11-03")
    assert date_snapshots._outside_epoch(object(), "2026-11-03") == before
    assert date_snapshots._outside_epoch(object(), "2026-11-03") != before


def test_new_day_adoption_is_detected_even_while_yesterday_pointer_stays_unchanged(monkeypatch):
    from app.dashboard import date_snapshots

    identity = {"target_date": "2026-11-02", "control_version_id": "yesterday"}
    monkeypatch.setattr(date_snapshots, "_assert_current_plan", lambda *_: None)
    monkeypatch.setattr(date_snapshots, "_target_plan_version", lambda *_: None)
    captured = {"identity": identity, "target_plan_version": date_snapshots._capture_refresh_plans(object(), "2026-11-03", identity)}
    date_snapshots._assert_refresh_plans(object(), "2026-11-03", captured)
    monkeypatch.setattr(date_snapshots, "_target_plan_version", lambda *_: "new-day-plan")
    with pytest.raises(ValueError, match="target-day plan changed"):
        date_snapshots.publish_date_refresh(object(), "2026-11-03", captured)
    with pytest.raises(ValueError, match="before its display"):
        date_snapshots._capture_refresh_plans(object(), "2026-11-03", identity)


def test_explicit_older_date_keeps_its_plan_binding(monkeypatch):
    from app.dashboard import date_snapshots

    identity = {"target_date": "2026-11-03", "control_version_id": "today"}
    monkeypatch.setattr(date_snapshots, "_assert_current_plan", lambda *_: None)
    monkeypatch.setattr(date_snapshots, "_target_plan_version", lambda *_: "past-plan")
    captured = {"identity": identity, "target_plan_version": date_snapshots._capture_refresh_plans(object(), "2026-11-02", identity)}
    date_snapshots._assert_refresh_plans(object(), "2026-11-02", captured)


def test_date_publication_does_not_roll_back_monthly_totals_to_stale_history(monkeypatch):
    from app.dashboard import date_snapshots
    from app.dashboard.snapshots import _check_source_epoch_order

    monkeypatch.setattr(date_snapshots, "_assert_refresh_plans", lambda *_: None)
    monkeypatch.setattr(date_snapshots, "_outside_epoch", lambda *_: {})
    monkeypatch.setattr(date_snapshots, "clear_dashboard_cache", lambda: None)
    monkeypatch.setattr(date_snapshots, "load_dashboard_slice", lambda *_, **__: object())
    monkeypatch.setattr(date_snapshots, "_payload_from_slice", lambda _: {"cost_daily": [
        {"date": "2026-11-03", "savings_yen": 30, "self_consumption_kwh": 2}]})
    monkeypatch.setattr(date_snapshots.PublishedPlan, "from_document", lambda *_, **__: SimpleNamespace(sha256="original"))
    monkeypatch.setattr(date_snapshots, "_validate_publication_payload", lambda *_: None)
    stamp = "2026-11-03T03:00:00+00:00"
    recent = {"forecast_hourly": f"{stamp}|24|recent", "scope/forecast_hourly": "2026-10-04"}
    full = {"forecast_hourly": f"{stamp}|744|full", "scope/forecast_hourly": "2026-04-17"}
    monkeypatch.setattr(date_snapshots, "_read_dashboard_source_epoch", lambda *_, history_scope, **__: full if history_scope else recent)
    published = {}

    def upload(artifact, *, source_epoch, history_scope, **_):
        assert history_scope == (artifact.kind == "history")
        previous = full if artifact.kind == "history" else recent
        _check_source_epoch_order(previous, source_epoch)
        assert source_epoch == previous
        published[artifact.kind] = json.loads(artifact.raw)

    monkeypatch.setattr(date_snapshots, "_upload_artifact", upload)
    client = SimpleNamespace(collection=lambda _: SimpleNamespace(document=lambda _: SimpleNamespace(
        get=lambda **_: SimpleNamespace(to_dict=lambda: {"payload": "{}"}))))
    captured = {"outside_epoch": {}, "storage": object(), "identity": {
        "target_date": "2026-11-03", "control_version_id": "current", "raw_plan_sha256": "original"}, "payloads": {
        "bootstrap": {"cost_daily": [{"date": "2026-11-02", "savings_yen": 20, "self_consumption_kwh": 1}]},
        "history": {"cost_daily": [{"date": "2026-11-02", "savings_yen": 2, "self_consumption_kwh": 0.1}]}}}
    date_snapshots.publish_date_refresh(client, "2026-11-03", captured)
    assert sum(row["savings_yen"] for row in published["bootstrap"]["cost_monthly"]) == 50
    assert published["history"]["cost_daily"][0]["savings_yen"] == 2  # Unselected date was not regenerated.


def test_requested_date_is_fixed_and_future_dates_are_rejected():
    assert actual_import.resolve_target_date() == "2026-11-03"
    assert actual_import.resolve_target_date("2026-11-02") == "2026-11-02"
    with pytest.raises(ValueError):
        actual_import.resolve_target_date("2026-11-04")


def test_singleton_claim_refuses_an_unresolved_worker(monkeypatch):
    stored = {}

    class Lock:
        def get(self, **_):
            return SimpleNamespace(to_dict=lambda: dict(stored))

    transaction = SimpleNamespace(set=lambda _, row: stored.update(row))
    client = SimpleNamespace(collection=lambda _: SimpleNamespace(document=lambda _: Lock()),
                             transaction=lambda **_: transaction)
    monkeypatch.setattr(worker.firestore, "transactional", lambda function: function)
    assert worker._claim(client, "first") is True
    assert worker._claim(client, "second") is False
    assert stored["execution"] == "first"


def test_csv_write_window_is_exactly_the_explicit_date(tmp_path):
    from app.operations.firestore import sync_monitoring_csvs
    from app.operations.monitoring_sync import monitoring_sync_window

    path = tmp_path / "month.csv"
    path.write_text("年月日,時刻,発電電力量[kWh],消費電力量[kWh],買電電力量[kWh],売電電力量[kWh],蓄電残量(SOC)[%]\n"
                    "2026/11/02,23:30,0,1,1,0,50\n2026/11/03,00:00,0,2,2,0,49\n2026/11/04,00:00,0,3,3,0,48\n", encoding="utf-8")
    writes = []

    class Collection:
        def where(self, *_):
            return self

        def stream(self):
            return []

        def document(self, key):
            return key

    batch = SimpleNamespace(set=lambda ref, payload, **_: writes.append((ref, payload)), commit=lambda: None)
    client = SimpleNamespace(collection=lambda _: Collection(), batch=lambda: batch)
    window = monitoring_sync_window(datetime(2026, 11, 3, tzinfo=ZoneInfo("Asia/Tokyo")), days=1)
    sync_monitoring_csvs(client, csv_paths=[path], ingested_at="2026-11-03T00:00:01Z", window=window)
    assert len(writes) == 1
    assert writes[0][0] == "2026-11-03T00:00:00"
    assert writes[0][1]["buy_kwh"] == 2


def test_batch_display_repair_cannot_repeat_an_unknown_write(tmp_path, monkeypatch):
    from scripts import repair_dashboard_views as repair

    path = tmp_path / "repair.json"
    prepared = {"identity": {}, "epoch": {}, "epochs": {"bootstrap": {}}, "before": {}, "payloads": {"bootstrap": {}}}
    path.write_text(json.dumps({"status": "prepared", "prepared": prepared}), encoding="utf-8")
    monkeypatch.setattr(repair.snapshots, "_read_dashboard_source_epoch", lambda *_, **__: {})
    monkeypatch.setattr(repair, "_assert_current_plan", lambda *_: None)
    writes = []

    def interrupted(*_, **__):
        writes.append(1)
        raise TimeoutError("outcome unknown")

    monkeypatch.setattr(repair.snapshots, "_upload_artifact", interrupted)
    reviewed = repair._prepared_sha(prepared)
    with pytest.raises(TimeoutError):
        repair.apply(object(), object(), path, reviewed)
    assert json.loads(path.read_bytes())["status"] == "running"
    with pytest.raises(ValueError, match="already attempted"):
        repair.apply(object(), object(), path, reviewed)
    assert writes == [1]


def test_batch_display_repair_rejects_source_drift_before_any_write(tmp_path, monkeypatch):
    from scripts import repair_dashboard_views as repair

    path = tmp_path / "repair.json"
    prepared = {"epoch": {"old": "revision"}}
    path.write_text(json.dumps({"status": "prepared", "prepared": prepared}), encoding="utf-8")
    monkeypatch.setattr(repair.snapshots, "_read_dashboard_source_epoch", lambda *_, **__: {"new": "revision"})
    with pytest.raises(ValueError, match="source changed before write"):
        repair.apply(object(), object(), path, repair._prepared_sha(prepared))
    assert json.loads(path.read_bytes())["status"] == "prepared"


def test_batch_display_repair_preserves_each_artifact_native_scope(tmp_path, monkeypatch):
    import base64
    import gzip
    from types import SimpleNamespace
    from scripts import repair_dashboard_views as repair

    stamp = "2026-11-03T03:00:00+00:00"
    recent = {"forecast_hourly": f"{stamp}|24|recent", "scope/forecast_hourly": "2026-10-04"}
    full = {"forecast_hourly": f"{stamp}|744|full", "scope/forecast_hourly": "2026-04-17"}
    identity = {"target_date": "2026-11-03", "generated_at": stamp,
                "control_version_id": "version", "raw_plan_sha256": "sha"}
    def metadata(epoch):
        encoded = base64.b64encode(gzip.compress(json.dumps(epoch, sort_keys=True, separators=(",", ":")).encode(), mtime=0)).decode()
        return {**identity, "source_epoch": encoded}

    stored = {"bootstrap": (b'{}', metadata(recent), "1"), "history": (b'{}', metadata(full), "2")}
    monkeypatch.setattr(repair, "_read", lambda _, kind: stored[kind])
    monkeypatch.setattr(repair, "_assert_current_plan", lambda *_: None)
    monkeypatch.setattr(repair, "clear_dashboard_cache", lambda: None)
    monkeypatch.setattr(repair.PublishedPlan, "from_document", lambda *_, **__: SimpleNamespace(version_id="version", sha256="sha"))
    monkeypatch.setattr(repair.snapshots, "_validate_publication_payload", lambda *_: None)
    monkeypatch.setattr(repair.snapshots, "build_dashboard_snapshot_payloads", lambda *_, **__: {"bootstrap": {}, "history": {}})
    monkeypatch.setattr(repair.snapshots, "_read_dashboard_source_epoch", lambda *_, history_scope, **__: full if history_scope else recent)
    client = SimpleNamespace(collection=lambda _: SimpleNamespace(document=lambda _: SimpleNamespace(
        get=lambda **_: SimpleNamespace(to_dict=lambda: {"payload": "{}"}))))
    path = tmp_path / "repair.json"
    prepared = repair.prepare(client, object(), path)
    assert prepared["epochs"] == {"bootstrap": recent, "history": full}
    calls = []

    def upload(artifact, *, source_epoch, history_scope, **_):
        previous = json.loads(gzip.decompress(base64.b64decode(stored[artifact.kind][1]["source_epoch"])))
        # Exercise the real monotonicity guard that rejected the production
        # full-history epoch on a recent bootstrap with the same max revision.
        repair.snapshots._check_source_epoch_order(previous, source_epoch)
        assert history_scope == (artifact.kind == "history")
        stored[artifact.kind] = (artifact.raw, metadata(source_epoch), "3")
        calls.append(artifact.kind)

    monkeypatch.setattr(repair.snapshots, "_upload_artifact", upload)
    repair.apply(client, object(), path, repair._prepared_sha(prepared))
    assert calls == ["bootstrap", "history"]
    assert json.loads(path.read_bytes())["status"] == "verified"
