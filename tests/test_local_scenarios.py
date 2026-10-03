from copy import deepcopy

import pytest

from app.energy_plan.local_scenarios import recalibrate_scenarios


def observation(day="2026-08-01"):
    return {"date": day, "pv_forecasts": {"baseline": 2, "half": 4},
            "load_forecast": 10, "actual_pv": 6, "actual_load": 20}


def test_past_only_joint_pairing_and_variant_denominator():
    past = observation()
    today = observation("2026-08-02")
    result = recalibrate_scenarios([past, today], target_date="2026-08-02", variants=("baseline", "half"))
    today["actual_pv"] = 999999
    assert result == recalibrate_scenarios([today, past], target_date="2026-08-02", variants=("baseline", "half"))
    assert result["source_dates"] == ["2026-08-01"]
    baseline = result["scenarios_by_variant"]["baseline"]
    half = result["scenarios_by_variant"]["half"]
    assert baseline[1]["pv_multiplier"] == 3
    assert half[1]["pv_multiplier"] == 1.5
    assert half[1]["load_multiplier"] == baseline[1]["load_multiplier"] == 2
    assert sum(s["probability"] for s in baseline) == pytest.approx(1)


def test_zero_actual_valid_missing_or_zero_forecast_excluded_for_all_variants():
    rows = [observation("2026-08-01"), observation("2026-08-02"), observation("2026-08-03")]
    rows[0]["actual_pv"] = 0
    rows[1]["pv_forecasts"]["half"] = 0
    rows[2]["actual_load"] = None
    r = recalibrate_scenarios(rows, target_date="2026-08-04", variants=("baseline", "half"))
    assert r["source_dates"] == ["2026-08-01"]
    assert len(r["excluded"]) == 2
    assert r["scenarios_by_variant"]["half"][1]["pv_multiplier"] == 0


def test_window_cold_start_duplicates_and_clipping():
    old = observation("2026-07-01")
    r = recalibrate_scenarios([old], target_date="2026-08-01", variants=("baseline", "half"))
    assert r["fallback"] == "point_prior_only"
    new = observation(); new["actual_pv"] = 8
    r = recalibrate_scenarios([new], target_date="2026-08-02", variants=("baseline", "half"))
    assert r["clipped_pv_dates"]["baseline"] == ["2026-08-01"]
    with pytest.raises(ValueError, match="duplicate"):
        recalibrate_scenarios([new, deepcopy(new)], target_date="2026-08-02", variants=("baseline", "half"))
