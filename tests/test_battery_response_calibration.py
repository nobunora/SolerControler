from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.energy_plan.battery_calibration import calibrate_battery_response
from app.energy_plan.battery_planning import build_planning_battery_response
from app.energy_plan.battery_replay_validation import make_battery_validation_evaluator
from app.energy_plan.battery_response import BatterySocResponse
from app.energy_plan.soc_cost import SocCostModel
from app.energy_plan.energy_model import fit_coefficients_from_rows


def _observations(source: str = "observed_outdoor") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    previous = 50.0
    for index in range(30 * 48):
        dt = datetime(2026, 9, 1) + timedelta(minutes=30 * index)
        temperature = 20.0 + 8.0 * math.sin(index / 48.0 * 0.7) + 2.0 * math.sin(dt.hour / 24 * math.tau)
        charge_response = 10.0 + 0.05 * (temperature - 20)
        discharge_response = 12.0 + 0.08 * (temperature - 20)
        soc = 50.0 + 4.0 * math.sin(index * 0.4)
        delta = soc - previous + 0.01
        charge = max(0, delta) / charge_response
        discharge = max(0, -delta) / discharge_response
        rows.append(dict(dt=dt, available_at=dt + timedelta(minutes=30), soc=soc, chg=charge, dchg=discharge, temperature_c=temperature,
                         temperature_source=source, pv=1.0 + charge, load=1.0 + discharge, buy=0.0, sell=0.0))
        previous = soc
    return rows


def _financial_pass(candidate: BatterySocResponse, baseline: BatterySocResponse, rows: list[dict[str, Any]]) -> dict[str, float]:
    assert rows and "candidate_charge_soc_per_kwh" in rows[0]
    return dict(candidate_buy_mae=0.1, baseline_buy_mae=0.1, candidate_fee_mae=1.0, baseline_fee_mae=1.0)


def _calibrate(**kwargs: Any) -> Any:
    arguments: dict[str, Any] = dict(rows=_observations(), baseline=BatterySocResponse(9.0, 14.0, 0.03),
                                     as_of=datetime(2026, 10, 1), forecast_temperatures={0: 12.0, 1: 28.0},
                                     validation_evaluator=_financial_pass)
    arguments.update(kwargs)
    return calibrate_battery_response(**arguments)


def test_response_preserves_asymmetric_ac_coefficients_and_json_roundtrip() -> None:
    response = BatterySocResponse(10, 12, 0.02, {3: 9.8}, {3: 12.2})
    reconstructed = BatterySocResponse.from_payload(response.to_payload())
    assert reconstructed == response
    assert reconstructed.charge_coefficient(3) == 9.8
    assert reconstructed.discharge_coefficient(4) == 12


@pytest.mark.parametrize("charge,discharge,drift", [(12, 10, 0), (math.nan, 12, 0), (10, math.inf, 0), (10, 12, -1)])
def test_response_rejects_nonphysical_or_nonfinite_values(charge: float, discharge: float, drift: float) -> None:
    with pytest.raises(ValueError):
        BatterySocResponse(charge, discharge, drift)


@pytest.mark.parametrize("field", ["charge_soc_per_kwh", "discharge_soc_per_kwh", "drift_soc_per_hour", "hourly_charge_soc_per_kwh", "hourly_discharge_soc_per_kwh"])
@pytest.mark.parametrize("deserialize", [False, True])
def test_boolean_is_not_a_physical_response_number(field: str, deserialize: bool) -> None:
    values: dict[str, Any] = dict(charge_soc_per_kwh=0.5, discharge_soc_per_kwh=12, drift_soc_per_hour=0)
    values[field] = {"3" if deserialize else 3: True} if field.startswith("hourly_") else True
    with pytest.raises(ValueError, match="boolean"):
        if deserialize:
            BatterySocResponse.from_payload(values)
        else:
            BatterySocResponse(**values)


def test_calibration_adopts_observed_temperature_only_after_independent_holdout() -> None:
    result = _calibrate()
    assert result.diagnostics["adopted"] is True
    model = result.diagnostics["model"]
    assert model["temperature_source"] == "observed_outdoor"
    assert model["charge_temperature_slope"] == pytest.approx(0.05, abs=0.001)
    assert model["discharge_temperature_slope"] == pytest.approx(0.08, abs=0.001)
    assert model["drift_soc_per_hour"] == pytest.approx(0.02, abs=0.001)
    assert model["iterations"] <= 200
    assert result.response.charge_coefficient(0) < result.response.charge_coefficient(1)
    assert result.diagnostics["candidate_soc_mae"] < result.diagnostics["baseline_soc_mae"]
    assert result.diagnostics["holdout_start"] == "2026-09-25"


def test_no_financial_evidence_cannot_promote_a_better_soc_fit() -> None:
    result = _calibrate(validation_evaluator=None)
    assert not result.diagnostics["adopted"]
    assert result.diagnostics["reason"] == "financial_validation_missing"
    assert result.response == BatterySocResponse(9.0, 14.0, 0.03)


def test_worse_financial_prediction_retains_baseline() -> None:
    def worse(*args: Any) -> dict[str, float]:
        return dict(candidate_buy_mae=0.2, baseline_buy_mae=0.1, candidate_fee_mae=0.5, baseline_fee_mae=1.0)

    result = _calibrate(validation_evaluator=worse)
    assert result.diagnostics["reason"] == "financial_holdout_worsened"
    assert not result.diagnostics["adopted"]


def test_forecast_temperature_is_not_observational_training() -> None:
    result = _calibrate(rows=_observations("forecast_outdoor"))
    model = result.diagnostics["candidate_model"]
    assert model["temperature_source"] is None
    assert model["charge_temperature_slope"] == 0
    assert model["discharge_temperature_slope"] == 0


def test_future_rows_cannot_change_training_or_holdout() -> None:
    original = _calibrate()
    future = dict(_observations()[-1], dt=datetime(2026, 10, 1, 0, 30), soc=7.0, chg=9999.0)
    changed = _calibrate(rows=_observations() + [future])
    assert original.diagnostics["input_sha256"] == changed.diagnostics["input_sha256"]
    assert original.diagnostics["training_sha256"] == changed.diagnostics["training_sha256"]
    assert original.response == changed.response


def test_candidate_and_reference_fits_do_not_use_holdout_or_runtime_fitted_prior() -> None:
    first = _calibrate()
    changed_rows = [dict(row, temperature_c=45.0) if row["dt"].day >= 25 else row for row in _observations()]
    changed = _calibrate(rows=changed_rows, baseline=BatterySocResponse(8.0, 15.0, 0.04))
    assert first.diagnostics["candidate_model"] == changed.diagnostics["candidate_model"]
    assert first.diagnostics["chronological_reference_model"] == changed.diagnostics["chronological_reference_model"]
    assert first.diagnostics["training_sha256"] == changed.diagnostics["training_sha256"]


def test_missing_or_future_availability_cannot_be_used_for_training() -> None:
    missing = [{key: value for key, value in row.items() if key != "available_at"} for row in _observations()]
    assert _calibrate(rows=missing).diagnostics["reason"] == "insufficient_prior_dates"
    future = [dict(row, updated_at=datetime(2026, 10, 2)) for row in _observations()]
    assert _calibrate(rows=future).diagnostics["reason"] == "insufficient_prior_dates"


@pytest.mark.parametrize("receipt", ["2026-10-02T00:00:00", "invalid-receipt"])
def test_nested_weather_receipts_must_all_be_valid_and_prior(receipt: str) -> None:
    rows = [dict(row, temperature_available_at=datetime(2026, 9, 30),
                 temperature_receipts={"available_at": receipt}) for row in _observations()]
    assert _calibrate(rows=rows).diagnostics["reason"] == "insufficient_prior_dates"


def test_adopted_previous_model_is_reprojected_and_retained_without_new_evidence() -> None:
    first = _calibrate()
    next_result = _calibrate(rows=[], previous_payload=first.diagnostics,
                             as_of=datetime(2026, 10, 2), forecast_temperatures={0: 28.0})
    assert not next_result.diagnostics["adopted"]
    assert next_result.diagnostics["model"] == first.diagnostics["model"]
    assert next_result.response.charge_coefficient(0) > first.response.charge_coefficient(0)


def test_future_previous_payload_cannot_be_adopted() -> None:
    previous = dict(as_of="2026-10-03T00:00:00", response=BatterySocResponse(10, 12).to_payload())
    result = _calibrate(rows=[], previous_payload=previous)
    assert result.diagnostics["previous_model_rejected"] == "invalid_previous_payload"
    assert result.response == BatterySocResponse(9, 14, 0.03)


def test_unadopted_previous_baseline_does_not_freeze_current_empirical_fit() -> None:
    previous = dict(as_of="2026-09-30T00:00:00", adopted=False, model=None,
                    response=BatterySocResponse(10, 12).to_payload())
    result = _calibrate(rows=[], previous_payload=previous)
    assert result.response == BatterySocResponse(9, 14, 0.03)


def test_measured_battery_temperature_slope_does_not_use_outdoor_forecast() -> None:
    result = _calibrate(rows=_observations("measured_battery"))
    assert result.diagnostics["adopted"]
    assert result.diagnostics["model"]["temperature_source"] == "measured_battery"
    assert result.response.charge_coefficient(0) == pytest.approx(result.response.charge_coefficient(1), abs=0.001)


def test_replay_prices_actual_and_predicted_meters_with_same_contract() -> None:
    evaluator = make_battery_validation_evaluator(SocCostModel(30, 15, 1, 1, export_value_mode="revenue", sell_revenue_yen_per_kwh=10))
    response = BatterySocResponse(10, 12)
    row = dict(dt=datetime(2026, 9, 1, 12), pv=0, load=2, buy=1, sell=0, soc_start=12, duration_hours=0.5,
               candidate_charge_soc_per_kwh=10, candidate_discharge_soc_per_kwh=12,
               baseline_charge_soc_per_kwh=10, baseline_discharge_soc_per_kwh=24)
    metrics = evaluator(response, response, [row])
    assert metrics["candidate_buy_mae"] == 0
    assert metrics["candidate_fee_mae"] == 0
    assert metrics["baseline_buy_mae"] == 0.5
    assert metrics["baseline_fee_mae"] == 15


def test_replay_missing_meter_is_unavailable_not_zero() -> None:
    evaluator = make_battery_validation_evaluator(SocCostModel(30, 15, 1, 1))
    with pytest.raises(KeyError):
        evaluator(BatterySocResponse(10, 12), BatterySocResponse(10, 12), [dict(dt=datetime(2026, 9, 1, 12), pv=0, load=1)])


def test_planning_helper_uses_empirical_asymmetric_coefficients_and_neutral_unknown_temperature() -> None:
    observations = _observations("forecast_outdoor")
    coefficients = fit_coefficients_from_rows(observations)
    result = build_planning_battery_response(
        rows=observations, coefficients=coefficients,
        capacity_kwh=coefficients.battery_usable_capacity_kwh,
        forecast={"hourly_weather": [{"hour": 0, "temp_c": -20.0}, {"hour": 1, "temp_c": 40.0}]},
        as_of=datetime(2026, 10, 1), cost_model=SocCostModel(30, 15, 1, 1),
    )
    assert result.diagnostics["temperature_prediction_source"] == "forecast_outdoor"
    assert result.diagnostics["baseline_capacity_scale"] == 1
    assert result.diagnostics.get("candidate_model", {}).get("temperature_source") is None
    assert result.response.charge_soc_per_kwh <= result.response.discharge_soc_per_kwh


def test_planning_helper_joins_receipted_daily_observations_and_uses_daily_forecast() -> None:
    rows = _observations()
    weather: list[dict[str, Any]] = []
    previous_soc = 50.0
    for row in rows:
        temperature = 20 + 8 * math.sin((row["dt"].day - 1) * 0.7)
        delta = row["soc"] - previous_soc + 0.01
        row.update(chg=max(0, delta) / (10 + 0.05 * (temperature - 20)),
                   dchg=max(0, -delta) / (12 + 0.08 * (temperature - 20)))
        row.update(pv=1 + row["chg"], load=1 + row["dchg"])
        previous_soc = row["soc"]
        row.pop("temperature_c")
        row.pop("temperature_source")
        if row["dt"].hour == 0 and row["dt"].minute == 0:
            weather.append(dict(date=row["dt"].date().isoformat(), temp=temperature,
                                temperature_source="observed_outdoor", temperature_resolution="daily_mean",
                                available_at=row["dt"] + timedelta(days=1)))
    coefficients = fit_coefficients_from_rows(rows)
    result = build_planning_battery_response(
        rows=rows, coefficients=coefficients, capacity_kwh=coefficients.battery_usable_capacity_kwh,
        forecast={"temp_c": 12, "hourly_weather": [{"hour": 0, "temp_c": 5}, {"hour": 1, "temp_c": 35}]},
        as_of=datetime(2026, 10, 1), cost_model=SocCostModel(30, 15, 1, 1), weather_observations=weather,
    )
    assert result.diagnostics["adopted"]
    assert result.diagnostics["model"]["temperature_resolution"] == "daily_mean"
    assert result.response.charge_coefficient(0) == pytest.approx(result.response.charge_coefficient(1), abs=0.001)
    assert result.diagnostics["observed_outdoor_daily_dates"] == 30


def test_weather_receipt_does_not_substitute_for_missing_meter_availability() -> None:
    rows = [{key: value for key, value in row.items() if key != "available_at"} for row in _observations()]
    for row in rows:
        row["temperature_available_at"] = datetime(2026, 9, 30)
    assert _calibrate(rows=rows).diagnostics["reason"] == "insufficient_prior_dates"
