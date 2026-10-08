from datetime import datetime

import pytest

from app.forecasting.correction_calculations import actual_hourly_totals_by_day, daily_pairs_for_ratio
from app.forecasting.correction_model import _physical_vector_residual_correction


@pytest.fixture(autouse=True)
def residual_settings(monkeypatch):
    monkeypatch.setenv("PHYSICAL_PV_VECTOR_RESIDUAL_ENABLED", "true")
    monkeypatch.setenv("PHYSICAL_PV_VECTOR_RESIDUAL_SPREAD_KWH", "0.6")


def correct(actual_history, forecast_history=None):
    return _physical_vector_residual_correction(
        forecast_history=forecast_history or {"2026-10-05": {
            10: {"pv": 1.0, "weather_code": 3}, 11: {"shortwave": 500.0},
        }},
        actual_history=actual_history,
        forecast={"hourly_weather": [
            {"hour": 10, "weather_code": 3},
            {"hour": 11, "shortwave_radiation_w_m2": 500.0},
        ]},
        hourly_pv={10: 1.0},
    )


@pytest.mark.parametrize("actual_history", [
    {}, {"2026-10-05": {}}, {"2026-10-05": {10: {}}},
    *[{"2026-10-05": {10: {"pv": value}}} for value in
      (None, float("nan"), float("inf"), float("-inf"), "", "unavailable", -0.1)],
], ids=["missing-day", "missing-hour", "missing-pv", "null", "nan", "inf", "negative-inf", "empty", "invalid", "negative"])
def test_missing_or_invalid_actual_is_not_a_zero_residual_sample(actual_history):
    corrected, metadata = correct(actual_history)
    assert corrected == {10: 1.0}
    assert metadata["applied"] == []
    assert metadata["excluded_actual_samples"] == 1


@pytest.mark.parametrize("actual,expected", [(0.0, 5 / 6), (0.5, 11 / 12), (1.5, 13 / 12)])
def test_measured_zero_and_valid_negative_residual_are_preserved(actual, expected):
    corrected, metadata = correct({"2026-10-05": {10: {"pv": actual}}})
    assert corrected[10] == pytest.approx(expected)
    assert metadata["applied"][0]["count"] == 1


def test_unmatched_history_cannot_change_residual_or_confidence():
    valid_history = {"2026-10-05": {
        10: {"pv": 1.0, "weather_code": 3}, 11: {"shortwave": 500.0},
    }}
    extended = {**valid_history, **{
        f"2026-09-{day:02}": {
            10: {"pv": 1.0, "weather_code": 3}, 11: {"shortwave": 500.0},
        }
        for day in range(1, 25)
    }}
    actual = {"2026-10-05": {10: {"pv": 1.5}}}
    corrected, metadata = correct(actual, extended)
    clean_corrected, clean_metadata = correct(actual, valid_history)
    assert corrected == clean_corrected
    assert metadata["applied"] == clean_metadata["applied"]
    assert metadata["excluded_actual_samples"] == 24


@pytest.mark.parametrize("invalid", [None, float("nan"), float("inf"), "", "unavailable", -0.1])
@pytest.mark.parametrize("invalid_first", [False, True])
def test_partial_hour_with_invalid_pv_is_excluded_before_residual_learning(invalid, invalid_first):
    values = [invalid, 0.5] if invalid_first else [0.5, invalid]
    rows = [{"dt": datetime(2026, 10, 5, 10, minute), "pv": pv, "load": 0.2}
            for minute, pv in zip((0, 30), values)]
    history = actual_hourly_totals_by_day(rows, target_date="2026-10-06")
    assert "pv" not in history["2026-10-05"][10]
    assert history["2026-10-05"][10]["load"] == pytest.approx(0.4)
    assert correct(history)[1]["applied"] == []


def test_measured_zero_survives_hourly_aggregation_and_residual_learning():
    history = actual_hourly_totals_by_day([
        {"dt": datetime(2026, 10, 5, 10, 0), "pv": 0.0, "load": 0.2},
        {"dt": datetime(2026, 10, 5, 10, 30), "pv": 0.0, "load": 0.2},
    ], target_date="2026-10-06")
    assert history["2026-10-05"][10]["pv"] == 0.0
    assert correct(history)[0][10] == pytest.approx(5 / 6)


def test_daily_pv_training_does_not_reintroduce_missing_hour_as_zero():
    forecast = {"2026-10-05": {10: {"pv": 1.0}, 11: {"pv": 1.0}}}
    actual = {"2026-10-05": {10: {}, 11: {"pv": 1.0}}}
    assert daily_pairs_for_ratio(forecast_history=forecast, actual_history=actual, key="pv") == []
    actual["2026-10-05"][10]["pv"] = 0.0
    assert daily_pairs_for_ratio(forecast_history=forecast, actual_history=actual, key="pv") == [
        ("2026-10-05", 2.0, 1.0)
    ]
