from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.energy_plan.night_soc_projection import build_night_soc_projection, latest_night_soc_observation
from app.energy_plan.night_charge_capacity import simulate_night_charge_capacity
from app.operations.domain import extract_hourly_forecast_from_plan
from app.operations.forecast_snapshot import build_forecast_snapshot_rows
from app.operations.forecast_persistence import _forecast_projection_values


def projection(**overrides):
    arguments = {
        "target_date": "2026-10-17", "timezone_name": "Asia/Tokyo",
        "generated_at": datetime(2026, 10, 17, 2, 30, tzinfo=ZoneInfo("Asia/Tokyo")),
        "rows": [{"dt": datetime(2026, 10, 17, 2), "soc": 0.0}],
        "target_soc": 39.0, "soc_per_kwh_charge": 39 / 3.684,
        "rate_info": {"percent_per_hour": 40.0, "source": "csv", "sample_count": 14},
    }
    arguments.update(overrides)
    return build_night_soc_projection(**arguments)


def test_october_17_charge_reaches_target_by_four_not_seven():
    result = projection()
    assert result["status"] == "ready"
    assert result["hourly_soc_percent"] == {"3": 0.0, "4": 39.0, "5": 39.0, "6": 39.0}
    assert result["hourly_grid_charge_kwh"]["3"] == 3.684
    assert result["hourly_grid_charge_kwh"]["4"] == 0.0
    assert result["target_reached_at"] == "2026-10-17T03:58:30+09:00"
    assert result["initial_soc_observed_at"] == "2026-10-17T02:30:00+09:00"


@pytest.mark.parametrize("soc", [None, float("nan"), float("inf"), -1, 101])
def test_missing_or_invalid_soc_is_not_zero(soc):
    result = projection(rows=[{"dt": datetime(2026, 10, 17, 2), "soc": soc}])
    assert result["status"] == "unavailable"
    assert result["hourly_soc_percent"] == {}


def test_daytime_rerun_cannot_invent_last_night_forecast():
    result = projection(generated_at=datetime(2026, 10, 17, 9, tzinfo=ZoneInfo("Asia/Tokyo")))
    assert result["reason"] == "night_window_already_elapsed"
    assert result["hourly_soc_percent"] == {}


def test_after_start_rerun_is_not_an_original_night_plan():
    result = projection(generated_at=datetime(2026, 10, 17, 3, 1, tzinfo=ZoneInfo("Asia/Tokyo")))
    assert result["reason"] == "night_plan_generated_after_start"


def test_old_or_future_observations_do_not_anchor_night():
    for stamp in (datetime(2026, 10, 16, 22), datetime(2026, 10, 17, 3)):
        assert projection(rows=[{"dt": stamp, "soc": 0}])["status"] == "unavailable"


def test_start_soc_excludes_a_csv_label_whose_effective_time_is_in_the_future():
    rows = [{"dt": datetime(2026, 10, 17, 1, 30), "soc": 0},
            {"dt": datetime(2026, 10, 17, 2), "soc": 80}]
    observed = latest_night_soc_observation(rows=rows, target_date="2026-10-17",
        timezone_name="Asia/Tokyo", generated_at=datetime(2026, 10, 17, 2, 15, tzinfo=ZoneInfo("Asia/Tokyo")))
    assert observed is not None
    assert observed[0].isoformat() == "2026-10-17T02:00:00+09:00"
    assert observed[1] == 0


def test_target_already_met_never_forecasts_discharge_or_more_charging():
    result = projection(rows=[{"dt": datetime(2026, 10, 17, 2), "soc": 50}])
    assert set(result["hourly_soc_percent"].values()) == {50}
    assert set(result["hourly_grid_charge_kwh"].values()) == {0}


def test_slow_charge_stops_at_cutoff_with_target_unreachable():
    result = projection(target_soc=100, rate_info={"percent_per_hour": 10, "source": "fixture"})
    assert result["target_reachable"] is False
    assert result["target_reached_at"] is None
    assert result["charge_stop_at"] == "2026-10-17T06:45:00+09:00"
    assert result["hourly_soc_percent"]["6"] == 30
    assert sum(result["hourly_grid_charge_kwh"].values()) == pytest.approx(3.684 * 37.5 / 39, abs=0.0002)


def test_grid_headroom_and_cutoff_limit_saved_forecast():
    result = projection(target_soc=100, soc_per_kwh_charge=10,
        rate_info={"percent_per_hour": 40, "source": "fixture"},
        hourly_load_kwh={3: 4, 4: 2, 5: 0, 6: 5},
        hourly_pv_kwh={3: 0, 4: 0, 5: 0, 6: 0})
    assert result["hourly_soc_percent"] == {"3": 0, "4": 10, "5": 40, "6": 80}
    assert result["hourly_grid_charge_kwh"] == {"3": 1, "4": 3, "5": 4, "6": 0}
    assert result["estimated_soc_at_cutoff_percent"] == 80
    assert result["target_reachable"] is False
    assert result["target_reached_at"] is None


def test_night_forecast_refuses_incomplete_power_inputs():
    result = projection(hourly_load_kwh={3: 1}, hourly_pv_kwh={3: 0, 4: 0, 5: 0, 6: 0})
    assert result["status"] == "unavailable"
    assert result["reason"] == "invalid_night_power_forecast"


@pytest.mark.parametrize("elapsed, expected", [(0, 37.5), (1.5, 22.5), (3.75, 0), (8, 0)])
def test_night_capacity_never_reuses_elapsed_charge_time(elapsed, expected):
    schedule = simulate_night_charge_capacity(initial_soc_percent=0, target_soc_percent=100,
        rate_percent_per_hour=10, charge_soc_per_kwh=10,
        hourly_load_kwh={h: 0 for h in range(3, 7)}, hourly_pv_kwh={h: 0 for h in range(3, 7)},
        start_offset_hours=elapsed)
    assert schedule.end_soc_percent == pytest.approx(expected)


def test_saved_projection_connects_0645_arrival_to_07_without_changing_stop_target():
    load = {h: 0.0 for h in range(24)}
    load.update({3: 4, 4: 2, 5: 5, 6: 1})
    pv = {h: 0.0 for h in range(24)}
    night = projection(target_soc=100, soc_per_kwh_charge=10,
        rate_info={"percent_per_hour": 40, "source": "fixture"}, hourly_load_kwh=load, hourly_pv_kwh=pv)
    plan = {"generated_at": "2026-10-16T17:30:00Z", "forecast": {"date": "2026-10-17"},
            "result": {"target_soc_7_percent": 100, "expected_morning_soc_percent": 70,
                       "required_night_charge_kwh": 7, "effective_capacity_kwh": 10},
            "daytime_soc_optimization": {"hourly_load_forecast_kwh": {str(h): v for h, v in load.items()},
                                         "hourly_pv_forecast_kwh": {str(h): v for h, v in pv.items()}},
            "decision_rationale": {"night_soc_projection": night}}
    writes = _forecast_projection_values(plan, target_date="2026-10-17", timezone_name="Asia/Tokyo", now=plan["generated_at"])
    saved = next(value for collection, _, value in writes if collection == "forecast_plans")
    assert saved["planned_target_soc_percent"] == 100
    assert saved["planned_hourly_soc_percent"]["6"] == 40
    assert saved["planned_hourly_soc_percent"]["7"] == 70


def test_saved_night_values_reach_hourly_and_immutable_snapshot_without_recalculation():
    data = {
        "generated_at": "2026-10-16T17:30:00Z",
        "forecast": {"date": "2026-10-17"},
        "daytime_soc_optimization": {
            "hourly_pv_forecast_kwh": {str(h): 0 for h in range(24)},
            "hourly_load_forecast_kwh": {str(h): 0.5 for h in range(24)},
        },
        "decision_rationale": {"night_soc_projection": projection()},
    }
    hourly = extract_hourly_forecast_from_plan(data)
    assert hourly[4]["forecast_soc_percent"] == 39
    assert hourly[3]["forecast_grid_charge_kwh"] == 3.684
    assert hourly[0]["forecast_soc_percent"] is None
    assert "forecast_soc_percent" not in hourly[7]
    snapshots = build_forecast_snapshot_rows(data, ingested_at="2026-10-17T00:00:00Z", timezone="Asia/Tokyo")
    assert snapshots[4]["forecast_soc_percent"] == 39
    assert snapshots[3]["forecast_grid_charge_kwh"] == 3.684
    data["decision_rationale"]["night_soc_projection"]["target_date"] = "2026-10-16"
    assert "forecast_soc_percent" not in extract_hourly_forecast_from_plan(data)[4]
