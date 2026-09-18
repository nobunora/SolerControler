from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from app.dashboard.schedule import _build_latest_schedule_from_events
from app.energy_plan.soc_cost import _simulate_day, simulate_daytime_soc_replay
from app.energy_plan.soc_projection import (
    allocate_hourly_grid_charge,
    build_hourly_soc_projection,
    build_planned_night_charge_schedule,
)
from app.operations.domain import (
    extract_battery_daily_from_summary,
    extract_hourly_forecast_from_plan,
)
from app.operations.sqlite import ensure_schema


def test_daytime_hourly_replay_is_the_same_engine_as_optimizer_summary() -> None:
    kwargs = {
        "start_energy_kwh": 5.0,
        "capacity_kwh": 10.0,
        "hourly_load_kwh": {7: 1.0, 8: 3.0},
        "hourly_pv_kwh": {7: 3.0, 8: 0.0},
        "pv_multiplier": 1.0,
        "load_multiplier": 1.0,
    }
    replay = simulate_daytime_soc_replay(**kwargs)
    summary = _simulate_day(**kwargs)

    assert replay.hourly_soc_percent[7] == pytest.approx(50.0)
    assert replay.hourly_soc_percent[8] == pytest.approx(70.0)
    assert summary[0] == pytest.approx(replay.buy_kwh)
    assert summary[1] == pytest.approx(replay.sell_kwh)
    assert summary[2] == pytest.approx(replay.max_soc_percent)
    assert summary[3] == replay.first_full_hour
    assert summary[4] == pytest.approx(replay.end_soc_percent)


def test_03_anchor_generates_night_soc_and_grid_charge_without_07_actual() -> None:
    schedule = build_planned_night_charge_schedule(
        required_night_charge_kwh=8.0,
        estimated_charge_power_kw=4.0,
        charge_end_time="07:00",
        not_before_minute=3 * 60,
    )
    grid = allocate_hourly_grid_charge(
        schedule=schedule,
        required_night_charge_kwh=8.0,
    )
    soc = build_hourly_soc_projection(
        anchor_hour=3,
        anchor_soc_percent=50.0,
        capacity_kwh=10.0,
        charge_efficiency=1.0,
        hourly_grid_charge_kwh=grid,
        hourly_load_kwh={hour: 0.0 for hour in range(24)},
        hourly_pv_kwh={hour: 0.0 for hour in range(24)},
    )

    assert schedule.charge_start_time == "05:00"
    assert schedule.charge_end_time == "07:00"
    assert sum(grid.values()) == pytest.approx(8.0)
    assert soc[0] is None and soc[1] is None and soc[2] is None
    assert soc[3] == pytest.approx(50.0)
    assert soc[5] == pytest.approx(50.0)
    assert soc[6] == pytest.approx(90.0)
    assert soc[7] == pytest.approx(100.0)


def test_planner_schedule_fails_closed_to_available_03_to_07_window() -> None:
    schedule = build_planned_night_charge_schedule(
        required_night_charge_kwh=20.0,
        estimated_charge_power_kw=4.0,
        charge_end_time="07:00",
        not_before_minute=3 * 60,
    )
    grid = allocate_hourly_grid_charge(
        schedule=schedule,
        required_night_charge_kwh=20.0,
    )

    assert schedule.charge_start_time == "03:00"
    assert schedule.planned_charge_duration_minutes == 240
    assert schedule.duration_clipped_to_available_window is True
    assert sum(grid.values()) == pytest.approx(16.0)


def test_hourly_plan_rows_carry_canonical_soc_and_grid_charge() -> None:
    plan = {
        "forecast": {"date": "2026-09-18"},
        "daytime_soc_optimization": {
            "hourly_pv_forecast_kwh": {"3": 0.0, "7": 1.2},
            "hourly_load_forecast_kwh": {"3": 0.3, "7": 0.8},
        },
        "result": {
            "hourly_soc_forecast_percent": {"3": 42.0, "7": 78.0},
            "hourly_grid_charge_forecast_kwh": {"3": 1.5, "7": 0.0},
        },
    }

    rows = extract_hourly_forecast_from_plan(plan)
    by_hour = {row["hour"]: row for row in rows}

    assert by_hour[3]["forecast_soc_percent"] == pytest.approx(42.0)
    assert by_hour[3]["forecast_grid_charge_kwh"] == pytest.approx(1.5)
    assert by_hour[7]["forecast_soc_percent"] == pytest.approx(78.0)


def test_device_soc_mode_50_and_final_target_100_remain_separate() -> None:
    row = {
        "event_id": "evt",
        "run_id": "run",
        "status": "applied",
        "recorded_at": "2026-09-18T03:00:00+09:00",
        "detail_json": {
            "plan_date": "2026-09-18",
            "soc_charge_mode": "50",
            "charge_start_time": "04:00",
            "charge_end_time": "07:00",
            "schedule_source": "03-monitor",
        },
    }
    schedule = _build_latest_schedule_from_events(
        event_rows=[row],
        battery_row={
            "date": "2026-09-18",
            "setting_soc_target_percent": 100.0,
            "source_status": "applied",
        },
        plan_date="2026-09-18",
    )

    assert schedule["soc_charge_mode"] == "50"
    assert schedule["planned_target_soc_percent"] == pytest.approx(100.0)


def test_final_plan_target_wins_over_raw_summary_target() -> None:
    row = extract_battery_daily_from_summary(
        summary={
            "run_id": "run",
            "night_charge_plan": {
                "forecast_date": "2026-09-18",
                "target_soc_7_percent_raw": 50.0,
                "required_night_charge_kwh": 2.0,
            },
        },
        night_plan={
            "forecast": {"date": "2026-09-18"},
            "plan_quality": {"should_apply": True},
            "result": {
                "target_soc_7_percent": 100.0,
                "required_night_charge_kwh": 9.93,
            },
        },
    )

    assert row is not None
    assert row["target_soc"] == pytest.approx(100.0)
    assert row["night_charge_kwh"] == pytest.approx(9.93)


def test_rejected_plan_does_not_fall_back_to_raw_target() -> None:
    row = extract_battery_daily_from_summary(
        summary={
            "run_id": "run",
            "night_charge_plan": {
                "forecast_date": "2026-09-18",
                "target_soc_7_percent_raw": 50.0,
                "required_night_charge_kwh": 2.0,
            },
        },
        night_plan={
            "forecast": {"date": "2026-09-18"},
            "plan_quality": {"should_apply": False},
            "result": {
                "target_soc_7_percent": 100.0,
                "required_night_charge_kwh": 9.93,
            },
        },
    )

    assert row is not None
    assert row["target_soc"] is None
    assert row["night_charge_kwh"] is None


def test_dashboard_has_no_soc_prediction_engine() -> None:
    root = Path(__file__).resolve().parents[1]
    dashboard = (root / "static" / "dashboard.js").read_text(encoding="utf-8")
    calculations = (root / "static" / "dashboard_calculations.js").read_text(encoding="utf-8")

    assert "forecast_soc_percent" in dashboard
    assert "forecast_grid_charge_kwh" in dashboard
    assert "estimateHourlyForecastSoc" not in dashboard
    assert "forecastSocFromLatestActual" not in dashboard
    assert "forecastSocFromLatestActual" not in calculations
    assert "allocateNightGridCharge" not in calculations


def test_existing_sqlite_forecast_table_migrates_new_hourly_columns() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE forecast_hourly (
            date TEXT NOT NULL,
            hour INTEGER NOT NULL,
            forecast_pv_kwh REAL,
            forecast_load_kwh REAL,
            forecast_charge_kwh REAL,
            source TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(date, hour)
        )
        """
    )

    ensure_schema(conn)

    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(forecast_hourly)").fetchall()
    }
    assert "forecast_grid_charge_kwh" in columns
    assert "forecast_soc_percent" in columns
