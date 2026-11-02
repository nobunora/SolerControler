from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
import json
from zoneinfo import ZoneInfo

import pytest

from app.energy_plan.battery_response import BatterySocResponse
from app.energy_plan.empirical_planning import prepare_empirical_planning
from app.energy_plan.energy_model import (
    NightChargeInputs, compute_night_charge_target, fit_coefficients_from_csv, to_dict,
)
from app.energy_plan.optimization import OptimizationDecision
from app.energy_plan.result_builder import _build_energy_model_output
from app.energy_plan.soc_constraints import SocConstraintSet
from app.energy_plan.soc_cost import PvForecastUncertainty, SocCostModel
from app.energy_plan.workflow import (
    ConsumptionForecastBundle, EnergyModelConfig, EnergyModelContext,
    NightChargePreparation, PvForecastBundle, _read_rows,
)
from app.forecasting.consumption import ConsumptionForecast
from app.operations.forecast_snapshot import build_forecast_snapshot_rows


AS_OF = datetime(2026, 11, 3, 2, 30, tzinfo=ZoneInfo("Asia/Tokyo"))


@pytest.fixture
def runtime_inputs(tmp_path, monkeypatch):
    csv = tmp_path / "actual.csv"
    csv.write_text(
        "年月日,時刻,発電電力量[kWh],消費電力量[kWh],売電電力量[kWh],買電電力量[kWh],充電電力量[kWh],放電電力量[kWh],蓄電残量(SOC)[%]\n"
        "2026/11/02,06:00,0,0,0,0,0,0,50\n"
        "2026/11/02,06:30,0,0,0,1,1,0,58\n"
        "2026/11/02,07:00,0,1,0,0,0,1,46\n"
        "2026/11/02,07:30,0,0,0,0,0,0,46\n", encoding="utf-8-sig",
    )
    coefficients = fit_coefficients_from_csv([csv])
    config = EnergyModelConfig(
        artifacts_dir=tmp_path, latitude=35., longitude=139., timezone="Asia/Tokyo",
        consumption_min_training_days=7, consumption_fallback_window_days=7,
        reserve_soc_percent=0., cycle_count=0., battery_temp_c=13.8,
        pv_midday_load_fraction=0.5, daytime_soc_step_percent=1.,
        daytime_target_peak_soc_percent=99., daytime_buy_tolerance_kwh=0.05,
        daytime_sell_tolerance_kwh=0.1, cost_optimization_enabled=True,
        cost_respect_morning_headroom_cap=False, cost_soc_step_percent=1.,
        cost_weather_upside_z=0., cost_min_pv_multiplier=0., cost_max_pv_multiplier=1.,
    )
    rows = [{**row, "available_at": "2026-11-02T08:00:00+09:00"} for row in _read_rows([csv])]
    context = EnergyModelContext(
        config, [csv], rows, coefficients,
        {"morning_pv_ratio": 0.2, "midday_surplus_ratio": 0.2},
        {"date": "2026-11-03", "source": "test", "temp_c": 13.8,
         "hourly_weather": [{"hour": h, "temp_c": 13.8, "weather_code": 3} for h in range(24)]},
        "2026-11-03", 46., [], billing_rows=[*rows, {
            **rows[-1], "dt": datetime(2026, 11, 3, 2), "soc": 46.,
            "available_at": "2026-11-03T02:01:00+09:00",
        }],
    )
    daily = ConsumptionForecast(date(2026, 11, 3), 1.5, 8., "test", 4, [])
    consumption = ConsumptionForecastBundle(daily, daily, {}, None)
    pv_values = {h: 0.1 if 7 <= h < 17 else 0. for h in range(24)}
    maximum = {h: value * 2 for h, value in pv_values.items()}
    trace = {"model_id": "physical_pv_ideal_max_global_v1", "revision": "immutable-test",
             "hourly_maximum_kwh": {str(h): v for h, v in maximum.items()},
             "hourly_uncorrected_pv_kwh": {str(h): v for h, v in pv_values.items()},
             "hourly_weather_class": {str(h): "overcast" for h in range(24)}}
    pv = PvForecastBundle(None, {h: 0.5 for h in range(24)}, pv_values, {}, {}, {},
                          "test", "test", PvForecastUncertainty(1., 0., 0., 0, "test"), 17,
                          ideal_max_provenance=trace, hourly_pv_max_kwh=maximum)
    inputs = NightChargeInputs(46., 0., 13.8, 8., 1.5, 0.2, 0.2, 0., 0., 13.8,
                               predicted_pv_kwh_override=1.)
    result = compute_night_charge_target(coefficients, inputs)
    night = NightChargePreparation(None, inputs, result, to_dict(result), {"kwh": 0.}, {"kwh": 0.}, 0.)
    import app.energy_plan.empirical_planning as stage
    import app.energy_plan.financial_contract as contract
    cost = SocCostModel(39.1, 15., coefficients.battery_round_trip_efficiency, 0.)
    monkeypatch.setattr(stage, "_soc_cost_model_from_env", lambda **kwargs: cost)
    monkeypatch.setattr(contract, "apply_financial_contract", lambda cost, target: cost)
    return context, consumption, pv, night


def test_real_stage_uses_measured_response_neutral_temperature_and_immutable_capacity(runtime_inputs, monkeypatch):
    import app.operations.battery_calibration_state as state

    monkeypatch.setattr(state, "load_previous_battery_calibration", lambda **kwargs:
                        pytest.fail("explicit injected history must not read cloud"))
    original, consumption, pv, night = runtime_inputs
    capability = deepcopy(pv.hourly_pv_max_kwh)
    trace = deepcopy(pv.ideal_max_provenance)
    context, selected, prepared = prepare_empirical_planning(
        original, consumption, pv, night, as_of=AS_OF, load_previous=False,
    )
    assert context.battery_response.charge_soc_per_kwh == pytest.approx(8.)
    assert context.battery_response.discharge_soc_per_kwh == pytest.approx(12.)
    assert context.battery_calibration["reason"] == "insufficient_prior_dates"
    assert context.battery_calibration["model"] is None
    assert prepared.result.effective_capacity_kwh == pytest.approx(original.coefficients.battery_usable_capacity_kwh)
    assert selected.hourly_pv_kwh == pv.hourly_pv_kwh
    assert selected.hourly_pv_max_kwh == capability
    assert pv.ideal_max_provenance == trace  # caller's source ledger was not mutated
    assert selected.ideal_max_provenance["revision"] == "immutable-test"
    assert prepared.result.required_night_charge_kwh == pytest.approx(
        max(0., prepared.result.target_soc_7_percent - original.latest_soc_percent) / 8.)


@pytest.mark.parametrize("load_previous", [False, True])
def test_real_stage_retains_previous_response_without_new_training(runtime_inputs, monkeypatch, load_previous):
    import app.operations.battery_calibration_state as state

    context, consumption, pv, night = runtime_inputs
    response = BatterySocResponse(9., 13.)
    prior = {"battery_calibration": {"as_of": "2026-11-02T02:30:00+09:00", "adopted": True,
                                     "response": response.to_payload()}}
    context = replace(context, previous_calibration_state=prior)
    calls = []

    def reader(**kwargs):
        calls.append(kwargs)
        return prior

    monkeypatch.setattr(state, "load_previous_battery_calibration", reader)
    selected, _, prepared = prepare_empirical_planning(context, consumption, pv, night,
                                                      as_of=AS_OF, load_previous=load_previous)
    assert selected.battery_response == response
    assert selected.battery_calibration["reason"] == "insufficient_prior_dates"
    assert selected.previous_calibration_state == prior
    assert len(calls) == int(load_previous)
    if calls:
        assert calls[0] == {"target_date": "2026-11-03", "as_of": AS_OF}
    assert prepared.result.required_night_charge_kwh == pytest.approx(
        max(0., prepared.result.target_soc_7_percent - context.latest_soc_percent) / 9.)


def test_real_stage_response_survives_plan_document_and_hourly_snapshot(runtime_inputs, monkeypatch):
    import app.energy_plan.result_builder as builder

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return AS_OF.astimezone(tz)

    monkeypatch.setattr(builder, "datetime", Frozen)
    context, consumption, pv, night = runtime_inputs
    context, pv, night = prepare_empirical_planning(context, consumption, pv, night,
                                                   as_of=AS_OF, load_previous=False)
    result = {**night.result_payload, "hourly_pv_forecast_kwh": {str(h): v for h, v in pv.hourly_pv_kwh.items()}}
    optimization = {"hourly_pv_forecast_kwh": {str(h): v for h, v in pv.hourly_pv_kwh.items()},
                    "hourly_load_forecast_kwh": {str(h): v for h, v in pv.hourly_load_kwh.items()}}
    output = _build_energy_model_output(
        context, consumption, night, pv, SocConstraintSet(0., 100., False, [], {}, {}, {}),
        OptimizationDecision(result, optimization, {}),
    )
    document = output.document.to_payload()
    rationale = document["decision_rationale"]
    assert rationale["battery_response"] == context.battery_response.to_payload()
    assert rationale["battery_calibration"] == context.battery_calibration
    projection = rationale["night_soc_projection"]
    assert projection["status"] == "ready"
    snapshots = build_forecast_snapshot_rows(document, ingested_at=AS_OF.isoformat(), timezone="Asia/Tokyo")
    snapshot7 = next(row for row in snapshots if row["hour"] == 7)
    frozen_response = json.loads(snapshot7["pv_forecast_detail_json"])["hourly_pv_bias"]["battery_response"]
    assert frozen_response == context.battery_response.to_payload()
    assert json.loads(snapshot7["pv_forecast_detail_json"])["hourly_pv_bias"]["battery_response_as_of"] == AS_OF.isoformat()
    for row in snapshots:
        if row["hour"] < 7:
            assert row["forecast_soc_percent"] == projection["hourly_soc_percent"].get(str(row["hour"]))
