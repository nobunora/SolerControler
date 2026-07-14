from pathlib import Path
import json

import pytest

from app.dashboard_data import (
    DashboardRawData,
    _build_dashboard_warnings,
    _build_latest_schedule_from_events,
    _build_dashboard_slice,
    _get_global_bounds_firestore,
    clear_dashboard_cache,
    load_dashboard_slice,
)
from app.operations_db import ensure_schema, open_db


def test_dashboard_slice_includes_energy_daily(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO sunshine_daily(date, forecast_pv_total_kwh, source, updated_at)
            VALUES ('2026-05-02', 5.7, 'test', '2026-05-02T00:00:00')
            """
        )
        conn.executemany(
            """
            INSERT INTO monitoring_samples(ts, pv_kwh, load_kwh, ingested_at)
            VALUES (?, ?, ?, '2026-05-02T00:00:00')
            """,
            [
                ("2026-05-01T07:00:00", 1.0, 0.8),
                ("2026-05-01T07:30:00", 1.0, 1.2),
                ("2026-05-02T07:00:00", 2.0, 1.5),
                ("2026-05-02T07:30:00", 3.0, 2.5),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    sliced = load_dashboard_slice(db_path, end_date="2026-05-02", window_days=2, include_static=True)
    row = next(x for x in sliced.data.energy_daily if x["date"] == "2026-05-02")

    assert row["forecast_pv_kwh"] == pytest.approx(5.7)
    assert row["actual_pv_kwh"] == pytest.approx(5.0)
    assert row["forecast_load_kwh"] == pytest.approx(2.0)
    assert row["actual_load_kwh"] == pytest.approx(4.0)


def test_dashboard_uses_pv_array_forecast_when_present(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO sunshine_daily(
                date, forecast_pv_total_kwh, source, updated_at
            )
            VALUES ('2026-05-02', 8.4, 'test', '2026-05-02T00:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO monitoring_samples(ts, pv_kwh, load_kwh, ingested_at)
            VALUES ('2026-05-02T07:00:00', 2.0, 1.5, '2026-05-02T00:00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()

    sliced = load_dashboard_slice(db_path, end_date="2026-05-02", window_days=1, include_static=True)
    row = next(x for x in sliced.data.energy_daily if x["date"] == "2026-05-02")

    assert row["forecast_pv_kwh"] == pytest.approx(8.4)


def test_dashboard_slice_includes_hourly_forecast(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        conn.executemany(
            """
            INSERT INTO forecast_hourly(
                date, hour, forecast_pv_kwh, forecast_load_kwh, forecast_charge_kwh, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'test', '2026-05-02T00:00:00')
            """,
            [
                ("2026-05-02", 7, 1.2, 0.8, 0.4),
                ("2026-05-02", 8, 0.3, 0.9, 0.0),
            ],
        )
        conn.executemany(
            """
            INSERT INTO monitoring_samples(ts, load_kwh, ingested_at)
            VALUES (?, ?, '2026-05-02T00:00:00')
            """,
            [
                ("2026-05-02T07:00:00", 0.4),
                ("2026-05-02T07:30:00", 0.5),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    sliced = load_dashboard_slice(db_path, end_date="2026-05-02", window_days=1, include_static=True)

    assert [row["hour"] for row in sliced.data.forecast_hourly] == [7, 8]
    assert sliced.data.forecast_hourly[0]["forecast_charge_kwh"] == pytest.approx(0.4)
    assert sliced.data.forecast_hourly[0]["actual_load_kwh"] == pytest.approx(0.9)
    assert sliced.data.forecast_hourly[0]["latest_sample_at"] == "2026-05-02T07:30:00"
    assert sliced.data.forecast_hourly[1]["actual_load_kwh"] is None


def test_dashboard_slice_includes_battery_flow_daily(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        conn.executemany(
            """
            INSERT INTO monitoring_samples(ts, charge_kwh, discharge_kwh, ingested_at)
            VALUES (?, ?, ?, '2026-05-02T00:00:00')
            """,
            [
                ("2026-05-02T01:00:00", 1.2, 0.0),
                ("2026-05-02T07:00:00", 0.4, 0.7),
                ("2026-05-02T18:00:00", 0.0, 1.1),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    sliced = load_dashboard_slice(db_path, end_date="2026-05-02", window_days=1, include_static=True)

    assert sliced.data.battery_flow_daily == [
        {"date": "2026-05-02", "charge_kwh": pytest.approx(1.6), "discharge_kwh": pytest.approx(1.8)}
    ]


def test_dashboard_monthly_cost_uses_configurable_close_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_AGGREGATION_CLOSE_DAY", "14")
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        conn.executemany(
            """
            INSERT INTO cost_daily(date, self_consumption_kwh, savings_yen, cumulative_kwh, cumulative_yen, updated_at)
            VALUES (?, ?, ?, ?, ?, '2026-05-15T00:00:00')
            """,
            [
                ("2026-04-14", 1.0, 10.0, 1.0, 10.0),
                ("2026-04-15", 2.0, 20.0, 3.0, 30.0),
                ("2026-05-14", 3.0, 30.0, 6.0, 60.0),
                ("2026-05-15", 4.0, 40.0, 10.0, 100.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    sliced = load_dashboard_slice(db_path, end_date="2026-05-15", window_days=40, include_static=True)
    by_month = {row["month"]: row for row in sliced.data.cost_monthly}

    assert by_month["2026-04"]["self_consumption_kwh"] == pytest.approx(1.0)
    assert by_month["2026-05"]["period_start"] == "2026-04-15"
    assert by_month["2026-05"]["period_end"] == "2026-05-14"
    assert by_month["2026-05"]["self_consumption_kwh"] == pytest.approx(5.0)
    assert by_month["2026-06"]["self_consumption_kwh"] == pytest.approx(4.0)
    assert sliced.meta["aggregation_close_day"] == 14


def test_dashboard_prefers_03_monitor_schedule_over_estimated_start(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO battery_daily_metrics(date, setting_soc_target_percent, night_charge_kwh, updated_at)
            VALUES ('2026-06-03', 79, 7.68, '2026-06-02T14:03:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO settings_events(
                run_id, slot, profile, status, changed_fields_json, detail_json, source_doc_id, recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-06-03-03-monitor-schedule",
                "03",
                "forced-monitor",
                "forced-started",
                "[]",
                json.dumps(
                    {
                        "plan_date": "2026-06-03",
                        "charge_start_time": "02:43",
                        "charge_end_time": "07:00",
                        "soc_charge_mode": "79",
                        "battery_operating_mode": "forced",
                        "estimated_charge_power_kw": 1.8,
                        "schedule_source": "03-monitor",
                    },
                    separators=(",", ":"),
                ),
                "2026-06-03-03-monitor-schedule",
                "2026-06-03T00:06:00Z",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    sliced = load_dashboard_slice(db_path, end_date="2026-06-03", window_days=1, include_static=True)
    schedule = sliced.data.latest_schedule

    assert schedule["charge_start_time"] == "02:43"
    assert schedule["charge_end_time"] == "07:00"
    assert schedule["status"] == "forced-started"
    assert schedule["schedule_source"] == "03-monitor"


def test_latest_schedule_does_not_complete_from_different_plan_date() -> None:
    schedule = _build_latest_schedule_from_events(
        event_rows=[
            {
                "run_id": "old-run",
                "slot": "03",
                "profile": "night-green",
                "status": "applied",
                "detail_json": json.dumps({"plan_date": "2026-06-02", "charge_end_time": "06:00"}),
                "source_doc_id": "old-doc",
                "recorded_at": "2026-06-02T03:00:00Z",
            },
            {
                "run_id": "failed-current",
                "slot": "03",
                "profile": "night-green",
                "status": "confirm-failed",
                "detail_json": json.dumps({"plan_date": "2026-06-03", "charge_end_time": "07:00"}),
                "source_doc_id": "failed-doc",
                "recorded_at": "2026-06-03T03:00:00Z",
            },
        ],
        battery_row=None,
        plan_date="2026-06-03",
    )

    assert schedule["charge_end_time"] == "07:00"
    assert schedule["status"] == "confirm-failed"
    assert schedule["settings_completed"] is False
    assert schedule["settings_completed_status"] is None


def test_firestore_global_bounds_combine_sources_and_ignore_failures(monkeypatch) -> None:
    bounds = {
        ("sunshine_daily", "date"): (None, None),
        ("cost_daily", "date"): ("2026-01-01", "2026-05-01"),
        ("battery_daily_metrics", "date"): ("2026-02-01", "2026-07-14"),
        ("forecast_hourly", "date"): ("2026-03-01", "2026-07-15"),
        ("monitoring_samples", "ts"): ("2025-12-01", "2026-07-13"),
    }

    def fake_bounds(_client, collection_name, field_name="date"):
        if collection_name == "forecast_hourly":
            raise RuntimeError("index unavailable")
        return bounds[(collection_name, field_name)]

    monkeypatch.setattr("app.dashboard_data._firestore_bounds", fake_bounds)

    assert _get_global_bounds_firestore(object()) == ("2025-12-01", "2026-07-14")


def test_firestore_slice_cache_isolated_by_connection_and_clear(monkeypatch, tmp_path) -> None:
    calls: list[str] = []

    def fake_load(**_kwargs):
        value = f"slice-{len(calls)}"
        calls.append(value)
        return value

    monkeypatch.setenv("DATA_BACKEND", "firestore")
    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "project-a")
    monkeypatch.setenv("FIRESTORE_DATABASE_ID", "database-a")
    monkeypatch.setattr("app.dashboard_data._load_firestore_slice", fake_load)
    clear_dashboard_cache()

    first = load_dashboard_slice(tmp_path / "unused", end_date="2026-07-14")
    assert load_dashboard_slice(tmp_path / "unused", end_date="2026-07-14") is first
    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "project-b")
    assert load_dashboard_slice(tmp_path / "unused", end_date="2026-07-14") != first
    monkeypatch.setenv("FIRESTORE_DATABASE_ID", "database-b")
    assert load_dashboard_slice(tmp_path / "unused", end_date="2026-07-14") == "slice-2"
    clear_dashboard_cache()
    assert load_dashboard_slice(tmp_path / "unused", end_date="2026-07-14") == "slice-3"
    assert len(calls) == 4


@pytest.mark.parametrize("backend", ["sqlite", "postgres", "firestore"])
def test_dashboard_raw_data_build_has_backend_parity(backend: str) -> None:
    raw = DashboardRawData(
        pv_daily=[{"date": "2026-07-14", "actual_kwh": 4.0}],
        cost_daily=[{"date": "2026-07-14", "savings_yen": 100.0}],
        cost_monthly=[{"month": "2026-07", "savings_yen": 100.0}],
        battery_daily=[{"date": "2026-07-14", "setting_soc_target_percent": 80.0}],
        model_parameters=[],
        battery_flow_daily=[{"date": "2026-07-14", "charge_kwh": 1.0}],
        energy_daily=[{"date": "2026-07-14", "actual_pv_kwh": 4.0}],
        forecast_hourly=[{"date": "2026-07-14", "hour": 12, "forecast_pv_kwh": 1.0}],
        latest_schedule={"plan_date": "2026-07-14", "status": "applied", "settings_completed": True},
        global_oldest="2026-07-14",
        global_newest="2026-07-14",
    )

    sliced = _build_dashboard_slice(raw, end_date_iso="2026-07-14", window_days=31)

    assert sliced.data.energy_daily == raw.energy_daily, backend
    assert sliced.data.latest_schedule == raw.latest_schedule, backend
    assert sliced.data.cost_monthly == raw.cost_monthly, backend
    assert sliced.data.forecast_hourly == raw.forecast_hourly, backend
    assert sliced.meta["global_oldest_date"] == "2026-07-14", backend


def test_latest_schedule_keeps_newest_monitor_event_and_same_run_completion() -> None:
    schedule = _build_latest_schedule_from_events(
        event_rows=[
            {
                "run_id": "new-run",
                "slot": "03",
                "profile": "night-green",
                "status": "applied",
                "detail_json": json.dumps(
                    {"plan_date": "2026-06-03", "charge_end_time": "07:00", "schedule_source": "03-monitor"}
                ),
                "source_doc_id": "new-doc",
                "recorded_at": "2026-06-03T03:10:00Z",
            },
            {
                "run_id": "old-run",
                "slot": "03",
                "profile": "night-green",
                "status": "applied",
                "detail_json": json.dumps(
                    {"plan_date": "2026-06-03", "charge_end_time": "06:00", "schedule_source": "03-monitor"}
                ),
                "source_doc_id": "old-doc",
                "recorded_at": "2026-06-03T03:00:00Z",
            },
        ],
        battery_row=None,
        plan_date="2026-06-03",
    )

    assert schedule["charge_end_time"] == "07:00"
    assert schedule["recorded_at"] == "2026-06-03T03:10:00Z"
    assert schedule["settings_completed_run_id"] == "new-run"


def test_latest_schedule_does_not_mix_completion_from_different_run() -> None:
    schedule = _build_latest_schedule_from_events(
        event_rows=[
            {
                "run_id": "new-run",
                "status": "confirm-failed",
                "detail_json": json.dumps(
                    {"plan_date": "2026-06-03", "charge_end_time": "07:00", "schedule_source": "03-monitor"}
                ),
                "recorded_at": "2026-06-03T03:10:00Z",
            },
            {
                "run_id": "old-run",
                "status": "applied",
                "detail_json": json.dumps(
                    {"plan_date": "2026-06-03", "charge_end_time": "06:00", "schedule_source": "03-monitor"}
                ),
                "recorded_at": "2026-06-03T03:00:00Z",
            },
        ],
        battery_row=None,
        plan_date="2026-06-03",
    )

    assert schedule["charge_end_time"] == "07:00"
    assert schedule["settings_completed"] is False


def test_latest_schedule_prioritizes_monitor_then_no_charge() -> None:
    base_row = {"run_id": "run", "status": "applied", "recorded_at": "2026-06-03T03:00:00Z"}
    schedule = _build_latest_schedule_from_events(
        event_rows=[
            {**base_row, "detail_json": json.dumps({"plan_date": "2026-06-03", "charge_end_time": "05:00"})},
            {
                **base_row,
                "detail_json": json.dumps(
                    {"plan_date": "2026-06-03", "charge_end_time": "06:00", "schedule_source": "03-no-charge"}
                ),
            },
            {
                **base_row,
                "detail_json": json.dumps(
                    {"plan_date": "2026-06-03", "charge_end_time": "07:00", "schedule_source": "03-monitor"}
                ),
            },
        ],
        battery_row=None,
        plan_date="2026-06-03",
    )
    no_monitor_schedule = _build_latest_schedule_from_events(
        event_rows=[
            {**base_row, "detail_json": json.dumps({"plan_date": "2026-06-03", "charge_end_time": "05:00"})},
            {
                **base_row,
                "detail_json": json.dumps(
                    {"plan_date": "2026-06-03", "charge_end_time": "06:00", "schedule_source": "03-no-charge"}
                ),
            },
        ],
        battery_row=None,
        plan_date="2026-06-03",
    )

    assert schedule["charge_end_time"] == "07:00"
    assert no_monitor_schedule["charge_end_time"] == "06:00"


def test_latest_schedule_uses_battery_metric_provenance_for_completion() -> None:
    schedule = _build_latest_schedule_from_events(
        event_rows=[],
        battery_row={
            "date": "2026-06-03",
            "setting_soc_target_percent": 80,
            "night_charge_kwh": 0,
            "source_status": "applied",
            "source_profile": "night-green",
            "settings_run_id": "settings-run",
            "source_doc_id": "settings-run-03-00-night-green",
            "updated_at": "2026-06-03T03:10:00Z",
        },
        plan_date="2026-06-03",
    )

    assert schedule["settings_completed"] is True
    assert schedule["settings_completed_status"] == "applied"
    assert schedule["settings_completed_profile"] == "night-green"
    assert schedule["settings_completed_run_id"] == "settings-run"
    assert schedule["settings_completed_source_doc_id"] == "settings-run-03-00-night-green"


def test_latest_schedule_treats_no_charge_decision_as_completed() -> None:
    schedule = _build_latest_schedule_from_events(
        event_rows=[
            {
                "run_id": "2026-07-09-03-no-charge",
                "slot": "03",
                "profile": "standby",
                "status": "skipped-no-charge",
                "detail_json": json.dumps(
                    {
                        "plan_date": "2026-07-09",
                        "charge_end_time": "07:00",
                        "schedule_source": "03-no-charge",
                    }
                ),
                "source_doc_id": "2026-07-09-03-no-charge",
                "recorded_at": "2026-07-08T19:04:00Z",
            }
        ],
        battery_row=None,
        plan_date="2026-07-09",
    )

    assert schedule["settings_completed"] is True
    assert schedule["settings_completed_status"] == "skipped-no-charge"
    assert schedule["schedule_source"] == "03-no-charge"


def test_dashboard_warnings_do_not_mix_previous_battery_day_with_latest_plan() -> None:
    warnings = _build_dashboard_warnings(
        latest_schedule={
            "plan_date": "2026-07-09",
            "status": "skipped-no-charge",
            "settings_completed": True,
            "settings_completed_status": "skipped-no-charge",
            "schedule_source": "03-no-charge",
        },
        battery_daily=[
            {
                "date": "2026-07-08",
                "setting_soc_target_percent": 2.0,
                "night_charge_kwh": 0.194,
            }
        ],
        energy_daily=[],
        end_date_iso="2026-07-09",
    )

    assert {row["code"] for row in warnings}.isdisjoint(
        {"monitor_schedule_missing", "settings_completion_unconfirmed"}
    )


def test_latest_schedule_ignores_battery_metric_from_different_plan_date() -> None:
    schedule = _build_latest_schedule_from_events(
        event_rows=[
            {
                "run_id": "legacy-run",
                "slot": "03",
                "profile": "night-green",
                "status": "applied",
                "detail_json": json.dumps({"charge_end_time": "06:00"}),
                "source_doc_id": "legacy-doc",
                "recorded_at": "2026-05-03T03:00:00Z",
            },
        ],
        battery_row={
            "date": "2026-05-03",
            "setting_soc_target_percent": 80,
            "night_charge_kwh": 4.2,
            "source_status": "applied",
            "source_profile": "night-green",
            "settings_run_id": "legacy-run",
            "source_doc_id": "legacy-doc",
            "updated_at": "2026-05-03T03:10:00Z",
        },
        plan_date="2026-06-30",
    )

    assert schedule["settings_completed"] is False
    assert schedule["settings_completed_status"] is None
    assert schedule["charge_start_time"] is None
    assert schedule["status"] == "fallback-default"


def test_dashboard_warns_when_soc_target_is_unreached_without_false_schedule_warning(tmp_path: Path) -> None:
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO battery_daily_metrics(
                date, setting_soc_target_percent, night_charge_kwh, pv_charge_end_soc_percent, pv_charge_end_at, updated_at
            )
            VALUES ('2026-06-03', 80, 4.2, 49, '2026-06-03T15:30:00', '2026-06-03T23:10:00')
            """
        )
        conn.execute(
            """
            INSERT INTO monitoring_samples(ts, pv_kwh, load_kwh, ingested_at)
            VALUES ('2026-06-03T07:00:00', 1.0, 0.8, '2026-06-03T23:10:00')
            """
        )
        conn.commit()
    finally:
        conn.close()

    sliced = load_dashboard_slice(db_path, end_date="2026-06-03", window_days=1, include_static=True)
    codes = {row["code"] for row in sliced.data.dashboard_warnings}

    assert "soc_target_unreached" in codes
    assert "monitor_schedule_missing" not in codes


def test_dashboard_does_not_warn_stale_csv_for_today_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.dashboard_data._today_jst_iso", lambda: "2026-06-05")
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO sunshine_daily(date, source, updated_at)
            VALUES ('2026-06-05', 'test', '2026-06-04T23:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO monitoring_samples(ts, pv_kwh, load_kwh, ingested_at)
            VALUES ('2026-06-04T07:00:00', 1.0, 0.8, '2026-06-04T23:10:00')
            """
        )
        conn.commit()
    finally:
        conn.close()

    sliced = load_dashboard_slice(db_path, end_date="2026-06-05", window_days=2, include_static=True)
    codes = {row["code"] for row in sliced.data.dashboard_warnings}

    assert "csv_actual_stale" not in codes
