from __future__ import annotations

import pytest

from app.forecasting.correction_model import _physical_vector_residual_correction


@pytest.mark.parametrize(
    "actual_history",
    [
        {},
        {"2026-09-30": {}},
        {"2026-09-30": {8: {"pv": 4.0}}},
        {"2026-09-30": {7: {"load": 1.0}}},
        {"2026-09-30": {7: {"pv": None}}},
        {"2026-09-30": {7: {"pv": float("nan")}}},
        {"2026-09-30": {7: {"pv": float("inf")}}},
    ],
    ids=["missing-day", "empty-day", "missing-hour", "missing-pv", "null-pv", "nan-pv", "infinite-pv"],
)
def test_matching_weather_without_actual_pv_does_not_correct_forecast(monkeypatch, actual_history) -> None:
    monkeypatch.setenv("PHYSICAL_PV_VECTOR_RESIDUAL_ENABLED", "true")
    corrected, diagnostics = _physical_vector_residual_correction(
        forecast_history={"2026-09-30": {7: {"pv": 1.0, "shortwave": 100.0, "weather_code": 3.0}}},
        actual_history=actual_history,
        forecast={"hourly_weather": [{"hour": 7, "shortwave_radiation_w_m2": 100.0, "weather_code": 3}]},
        hourly_pv={7: 2.0},
    )
    assert corrected == {7: 2.0}
    assert diagnostics["applied"] == []


def test_missing_actual_sample_does_not_dilute_valid_residual(monkeypatch) -> None:
    monkeypatch.setenv("PHYSICAL_PV_VECTOR_RESIDUAL_ENABLED", "true")
    monkeypatch.setenv("PHYSICAL_PV_VECTOR_RESIDUAL_SPREAD_KWH", "0.6")
    corrected, diagnostics = _physical_vector_residual_correction(
        forecast_history={
            "2026-09-29": {7: {"pv": 9.0, "shortwave": 100.0, "weather_code": 3.0}},
            "2026-09-30": {7: {"pv": 1.0, "shortwave": 100.0, "weather_code": 3.0}},
        },
        actual_history={"2026-09-30": {7: {"pv": 4.0}}},
        forecast={"hourly_weather": [{"hour": 7, "shortwave_radiation_w_m2": 100.0, "weather_code": 3}]},
        hourly_pv={7: 2.0},
    )
    assert corrected[7] == pytest.approx(2.5)
    assert diagnostics["applied"][0]["count"] == 1


def test_measured_zero_pv_remains_a_valid_residual_sample(monkeypatch) -> None:
    monkeypatch.setenv("PHYSICAL_PV_VECTOR_RESIDUAL_ENABLED", "true")
    monkeypatch.setenv("PHYSICAL_PV_VECTOR_RESIDUAL_SPREAD_KWH", "0.6")
    corrected, diagnostics = _physical_vector_residual_correction(
        forecast_history={"2026-09-30": {7: {"pv": 1.0, "shortwave": 100.0, "weather_code": 3.0}}},
        actual_history={"2026-09-30": {7: {"pv": 0.0}}},
        forecast={"hourly_weather": [{"hour": 7, "shortwave_radiation_w_m2": 100.0, "weather_code": 3}]},
        hourly_pv={7: 2.0},
    )
    assert corrected[7] == pytest.approx(2.0 - 1.0 / 6.0)
    assert diagnostics["applied"][0]["count"] == 1
    assert diagnostics["applied"][0]["residual_kwh"] == -1.0
