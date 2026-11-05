from datetime import date, datetime, timedelta
from math import nan

import pytest

from app.forecasting.correction_model import calculate_load_forecast_correction
from app.forecasting.hourly_load_template import (
    HOURS, apply_hourly_load_template, complete_hourly_load_history,
)


def history():
    target = date(2026, 11, 3)
    return {(target - timedelta(days=age)).isoformat(): {h: float(age) for h in HOURS}
            for age in range(-1, 70)}


def test_holiday_median_strict_prior_and_no_change_to_other_hours():
    baseline = dict.fromkeys(range(24), 1.0)
    result, proof = apply_hourly_load_template(baseline, history=history(), target_date="2026-11-03")
    assert proof["dates"] == ["2026-11-01", "2026-10-31", "2026-10-25", "2026-10-24"]
    assert result[12] == 6.0
    assert all(result[h] == 1.0 for h in range(3))
    assert baseline == dict.fromkeys(range(24), 1.0)


def test_one_hour_change_does_not_renormalize_other_hours():
    actual = history()
    baseline = dict.fromkeys(range(24), 1.0)
    before, _ = apply_hourly_load_template(baseline, history=actual, target_date="2026-11-03")
    for values in actual.values():
        values[12] += 100
    after, _ = apply_hourly_load_template(baseline, history=actual, target_date="2026-11-03")
    assert after[12] == before[12] + 100
    assert after[13] == before[13]


@pytest.mark.parametrize("bad", [None, nan, -1.0])
def test_bad_interval_never_becomes_zero_and_cannot_be_revived(bad):
    stamp = datetime(2026, 11, 2, 12)
    rows = [{"dt": stamp, "load": 1.0}, {"dt": stamp.replace(minute=30), "load": bad},
            {"dt": stamp.replace(minute=30), "load": 1.0}]
    assert complete_hourly_load_history(rows, target_date="2026-11-03") == {}


def test_valid_zero_duplicate_missing_conflict_and_future():
    stamp = datetime(2026, 11, 2, 12)
    rows = [{"dt": stamp, "load": 0.0}, {"dt": stamp.replace(minute=30), "load": 0.0}]
    assert complete_hourly_load_history(rows + rows, target_date="2026-11-03") == {"2026-11-02": {12: 0.0}}
    assert complete_hourly_load_history(rows[:1], target_date="2026-11-03") == {}
    assert complete_hourly_load_history(rows, target_date="2026-11-02") == {}
    assert complete_hourly_load_history(rows + [{"dt": stamp, "load": 1.0}], target_date="2026-11-03") == {}


def test_sparse_or_expired_history_and_unknown_calendar_use_baseline():
    baseline = dict.fromkeys(range(24), 1.0)
    for target, actual, reason in [
        ("2026-11-03", {}, "insufficient_compatible_history"),
        ("2027-02-03", history(), "insufficient_compatible_history"),
        ("2028-02-03", history(), "holiday_calendar_year_unavailable"),
    ]:
        result, proof = apply_hourly_load_template(baseline, history=actual, target_date=target)
        assert result == baseline
        assert proof["reason"] == reason
        assert proof["applied"] is False


def test_missing_hour_excludes_whole_day_and_zero_day_is_valid():
    actual = history()
    actual["2026-11-01"].pop(12)
    actual["2026-10-31"] = dict.fromkeys(HOURS, 0.0)
    _, proof = apply_hourly_load_template(dict.fromkeys(range(24), 1.0), history=actual, target_date="2026-11-03")
    assert "2026-11-01" not in proof["dates"]
    assert "2026-10-31" in proof["dates"]


@pytest.mark.parametrize("comfort_applied", [True, False])
def test_integration_preserves_hgb_instead_of_replacing_with_actual_median(monkeypatch, comfort_applied):
    monkeypatch.setenv("LOAD_COMFORT_MODEL_ENABLED", "true")
    rows = [{"dt": datetime.fromisoformat(day).replace(hour=h, minute=m), "load": value / 2, "pv": 0.0}
            for day, values in history().items() for h, value in values.items() for m in (0, 30)]
    baseline = dict.fromkeys(range(24), 1.0)
    hgb = {**baseline, 12: 1.75}
    result = calculate_load_forecast_correction(
        rows=rows, hourly_load_forecast=baseline, target_date="2026-11-03",
        latitude=35.0, longitude=139.0, timezone="Asia/Tokyo", forecast={},
        load_ewma_alpha=0.5, load_ratio_min=0.7, load_ratio_max=1.8,
        weather_fetch=lambda **kwargs: {}, history_loader=lambda **kwargs: ({}, "test"),
        temperature_corrector=lambda **kwargs: {"multiplier": 1.0},
        comfort_predictor=lambda **kwargs: {
            "applied": comfort_applied, "hourly_load_kwh": hgb, "confidence": 0.5,
        },
    )
    assert result["hourly_load_kwh"][12] == (1.75 if comfort_applied else 1.0)
    assert result["hourly_load_kwh"][0] == 1.0
    assert result["rationale"]["corrected_hourly_load_forecast_kwh"]["12"] == (1.75 if comfort_applied else 1.0)
    assert result["rationale"]["comfort_load_model"]["hourly_load_kwh"]["12"] == 1.75
    assert "hourly_actual_template" not in result["rationale"]["comfort_load_model"]
