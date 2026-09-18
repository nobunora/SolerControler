from __future__ import annotations

from datetime import datetime

import pytest

from app.energy_plan.night_schedule import (
    build_planned_night_charge_schedule,
    forecast_anchor_minute,
)
from app.energy_plan.soc_projection import simulate_night_charge_soc
from app.operations.domain import extract_battery_daily_from_summary, extract_hourly_forecast_from_plan


def test_03_anchor_uses_latest_target_day_soc_and_starts_immediately() -> None:
    rows = [
        {"dt": datetime(2026, 9, 18, 0, 30), "soc": 38.0},
        {"dt": datetime(2026, 9, 18, 2, 30), "soc": 42.0},
    ]

    anchor = forecast_anchor_minute(rows, target_date="2026-09-18")
    schedule = build_planned_night_charge_schedule(
        required_charge_kwh=4.0,
        estimated_charge_power_kw=4.0,
        anchor_minute=anchor,
        charge_end_time="07:00",
    )

    assert anchor == 3 * 60
    assert schedule.start_time == "03:00"
    assert schedule.end_time == "04:00"
    assert schedule.hourly_grid_charge_kwh[3] == pytest.approx(4.0)
    assert schedule.deliverable_charge_kwh == pytest.approx(4.0)
    assert schedule.unmet_charge_kwh == pytest.approx(0.0)


def test_night_soc_forecast_uses_same_planned_grid_charge() -> None:
    schedule = build_planned_night_charge_schedule(
        required_charge_kwh=4.0,
        estimated_charge_power_kw=4.0,
        anchor_minute=3 * 60,
        charge_end_time="07:00",
    )

    soc = simulate_night_charge_soc(
        current_soc_percent=42.0,
        capacity_kwh=10.0,
        charge_efficiency=0.9,
        anchor_hour=3,
        hourly_grid_charge_kwh=schedule.hourly_grid_charge_kwh,
    )

    assert soc[3] == pytest.approx(42.0)
    assert soc[4] == pytest.approx(78.0)
    assert soc[7] == pytest.approx(78.0)


def test_unreachable_target_is_not_fabricated_at_07() -> None:
    schedule = build_planned_night_charge_schedule(
        required_charge_kwh=20.0,
        estimated_charge_power_kw=4.0,
        anchor_minute=3 * 60,
        charge_end_time="07:00",
    )

    assert schedule.start_time == "03:00"
    assert schedule.end_time == "07:00"
    assert schedule.deliverable_charge_kwh == pytest.approx(16.0)
    assert schedule.unmet_charge_kwh == pytest.approx(4.0)
    assert schedule.limitation_reason == "insufficient_remaining_charge_window"


def test_hourly_plan_extraction_carries_canonical_soc_and_grid_charge() -> None:
    plan = {
        "forecast": {
            "date": "2026-09-18",
            "hourly_weather": [{"hour": hour} for hour in range(24)],
        },
        "daytime_soc_optimization": {
            "hourly_pv_forecast_kwh": {str(hour): 0.0 for hour in range(24)},
            "hourly_load_forecast_kwh": {str(hour): 0.2 for hour in range(24)},
        },
        "result": {
            "hourly_soc_forecast_percent": {"3": 42.0, "4": 78.0, "7": 78.0},
            "hourly_grid_charge_forecast_kwh": {"3": 4.0},
        },
    }

    rows = extract_hourly_forecast_from_plan(plan)
    row03 = next(row for row in rows if row["hour"] == 3)
    row04 = next(row for row in rows if row["hour"] == 4)

    assert row03["forecast_soc_percent"] == pytest.approx(42.0)
    assert row03["forecast_grid_charge_kwh"] == pytest.approx(4.0)
    assert row04["forecast_soc_percent"] == pytest.approx(78.0)


def test_final_plan_target_never_falls_back_to_device_raw_candidate() -> None:
    summary = {
        "night_charge_plan": {
            "forecast_date": "2026-09-18",
            "target_soc_7_percent_raw": 50.0,
            "required_night_charge_kwh": 9.93,
        }
    }
    plan = {
        "forecast": {"date": "2026-09-18"},
        "plan_quality": {"should_apply": True},
        "result": {
            "target_soc_7_percent": 100.0,
            "required_night_charge_kwh": 9.93,
        },
    }

    row = extract_battery_daily_from_summary(summary=summary, night_plan=plan)

    assert row is not None
    assert row["target_soc"] == pytest.approx(100.0)


def test_rejected_plan_fails_closed_instead_of_using_device_raw_candidate() -> None:
    summary = {
        "night_charge_plan": {
            "forecast_date": "2026-09-18",
            "target_soc_7_percent_raw": 50.0,
            "required_night_charge_kwh": 9.93,
        }
    }
    plan = {
        "forecast": {"date": "2026-09-18"},
        "plan_quality": {"should_apply": False},
        "result": {
            "target_soc_7_percent": 100.0,
            "required_night_charge_kwh": 9.93,
        },
    }

    row = extract_battery_daily_from_summary(summary=summary, night_plan=plan)

    assert row is not None
    assert row["target_soc"] is None
    assert row["night_charge_kwh"] is None
