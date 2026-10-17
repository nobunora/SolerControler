from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.energy_plan.night_soc_projection import build_night_soc_projection
from app.operations.domain import extract_hourly_forecast_from_plan
from app.operations.forecast_snapshot import build_forecast_snapshot_rows


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
    assert result["initial_soc_observed_at"] == "2026-10-17T02:00:00+09:00"


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
