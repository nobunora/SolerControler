from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.energy_plan.battery_dispatch import dispatch_soc, night_charge_ac_kwh
from app.energy_plan.battery_response import BatterySocResponse
from app.energy_plan.energy_model import fit_coefficients_from_csv, effective_capacity_kwh
from app.energy_plan.night_soc_projection import build_night_soc_projection
from app.energy_plan.soc_cost import (
    ForecastScenario, PvForecastUncertainty, SocCostModel,
    evaluate_soc_candidate, optimize_soc_by_expected_cost, simulate_daytime_soc_percent,
)


def test_csv_empirical_response_prices_ac_energy_without_symmetric_losses(tmp_path: Path):
    csv = tmp_path / "actual.csv"
    csv.write_text(
        "年月日,時刻,発電電力量[kWh],消費電力量[kWh],売電電力量[kWh],買電電力量[kWh],充電電力量[kWh],放電電力量[kWh],蓄電残量(SOC)[%]\n"
        "2026/11/01,06:00,0,0,0,0,0,0,50\n"
        "2026/11/01,06:30,0,0,0,1,1,0,58\n"
        "2026/11/01,07:00,0,1,0,0,0,1,46\n"
        "2026/11/01,07:30,0,0,0,0,0,0,46\n",
        encoding="utf-8-sig",
    )
    coeff = fit_coefficients_from_csv([csv])
    assert coeff.soc_per_kwh_charge == pytest.approx(8)
    assert coeff.soc_per_kwh_discharge == pytest.approx(12)
    assert coeff.battery_temp_coeff_per_deg == 0
    assert effective_capacity_kwh(coeff, 0, 13.8) == pytest.approx(coeff.battery_usable_capacity_kwh)
    response = BatterySocResponse(coeff.soc_per_kwh_charge, coeff.soc_per_kwh_discharge)
    parameters = dict(capacity_kwh=coeff.battery_usable_capacity_kwh, soc_now_percent=0,
        hourly_load_kwh={7: 2., 18: 8.}, hourly_pv_kwh={}, battery_response=response,
        uncertainty=PvForecastUncertainty(1, 0, 0, 0, "exact"),
        cost_model=SocCostModel(39.1, 28.85, coeff.battery_round_trip_efficiency, 0),
        joint_scenarios=(ForecastScenario("exact", 1, 1, 1),))
    candidate = evaluate_soc_candidate(target_soc_percent=100, **parameters)
    assert candidate.required_night_charge_kwh == pytest.approx(100 / 8)
    assert candidate.expected_day_buy_kwh == pytest.approx(10 - 100 / 12)
    # With this measured response night-delivered energy costs more than day
    # imports: money must win over eliminating daytime purchases.
    selected = optimize_soc_by_expected_cost(reserve_soc_percent=0, **parameters)
    assert selected is not None
    assert selected.target_soc_7_percent == 0


def test_night_cost_and_saved_hourly_charging_share_temperature_response():
    response = BatterySocResponse(10, 12, hourly_charge_soc_per_kwh={3: 8, 4: 9, 5: 10})
    zone = ZoneInfo("Asia/Tokyo")
    projection = build_night_soc_projection(target_date="2026-11-03", timezone_name="Asia/Tokyo",
        generated_at=datetime(2026, 11, 3, 2, 30, tzinfo=zone),
        rows=[{"dt": datetime(2026, 11, 3, 2), "soc": 0}], target_soc=100,
        soc_per_kwh_charge=10, rate_info={"percent_per_hour": 40}, battery_response=response)
    required = night_charge_ac_kwh(initial_soc=0, target_soc=100, response=response, percent_per_hour=40)
    assert required == pytest.approx(40 / 8 + 40 / 9 + 20 / 10)
    assert sum(projection["hourly_grid_charge_kwh"].values()) == pytest.approx(required, abs=0.0002)


def test_day_soc_curve_and_candidate_use_same_empirical_dispatch():
    response = BatterySocResponse(10, 12.5)
    curve = simulate_daytime_soc_percent(target_soc_percent=100, capacity_kwh=10,
        hourly_load_kwh={7: 4, 8: 5}, hourly_pv_kwh={}, battery_response=response)
    assert curve[8] == pytest.approx(50)
    assert curve[9] == 0
    buy, sell, soc = dispatch_soc(soc=100, pv_kwh=0, load_kwh=9, response=response, hour=7)
    assert (buy, sell, soc) == (1, 0, 0)


def test_terminal_inventory_credit_uses_deliverable_ac_not_internal_energy():
    response = BatterySocResponse(10, 12.5)
    candidate = evaluate_soc_candidate(target_soc_percent=100, soc_now_percent=100,
        capacity_kwh=10, hourly_load_kwh={}, hourly_pv_kwh={}, battery_response=response,
        uncertainty=PvForecastUncertainty(1, 0, 0, 0, "exact"),
        cost_model=SocCostModel(39.1, 28.85, 1, 0, terminal_energy_value_yen_per_kwh=28.85),
        joint_scenarios=(ForecastScenario("exact", 1, 1, 1),))
    assert candidate.expected_terminal_energy_credit_yen == pytest.approx(8 * 28.85)


def test_nonfinite_night_rate_rejected_instead_of_nonterminating_replay():
    with pytest.raises(ValueError):
        night_charge_ac_kwh(initial_soc=0, target_soc=100,
            response=BatterySocResponse(10, 12), percent_per_hour=float("nan"))
