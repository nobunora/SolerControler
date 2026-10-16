from dataclasses import replace
import json

import pytest

from app.energy_plan import financial_contract
from app.energy_plan.financial_contract import apply_financial_contract
from app.energy_plan.plan_quality import _decision_cost_breakdown
from app.energy_plan.soc_cost import ForecastScenario, PvForecastUncertainty, SocCostModel, evaluate_soc_candidate, to_plain_dict


@pytest.fixture
def contract(monkeypatch, tmp_path):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps({"export_start_date": "2026-09-15",
                               "export_years_1_to_4_yen_per_kwh": 24,
                               "export_years_5_to_10_yen_per_kwh": 8.3}))
    monkeypatch.setattr(financial_contract, "CONTRACT_PATH", path)
    return path


def model():
    return SocCostModel(39.1, 28.85, .9, 0, export_value_mode="neutral")


@pytest.mark.parametrize("day,rate,end", [
    ("2026-09-15",24,"2030-09-15"), ("2030-09-14",24,"2030-09-15"),
    ("2030-09-15",8.3,"2036-09-15"), ("2036-09-14",8.3,"2036-09-15"),
])
def test_rate_uses_target_date_and_exclusive_anniversary(contract, day, rate, end):
    result = apply_financial_contract(model(), day)
    assert result.sell_opportunity_loss_yen_per_kwh == -rate
    assert result.financial_contract_rate_end_date == end
    assert result.export_value_mode == "revenue"


def test_historical_and_expired_dates_are_not_assigned_current_rate(contract):
    old = model()
    assert apply_financial_contract(old, "2026-09-14") is old
    with pytest.raises(ValueError, match="expired"):
        apply_financial_contract(old, "2036-09-15")


@pytest.mark.parametrize("rate", [float('nan'), float('inf'), -1, 0])
def test_invalid_export_price_stops_planning(contract, rate):
    data = json.loads(contract.read_text())
    data['export_years_1_to_4_yen_per_kwh'] = rate
    contract.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="finite and positive"):
        apply_financial_contract(model(), "2026-10-16")


def candidate(cost_model, *, target=0., load=None, pv=None):
    return evaluate_soc_candidate(target_soc_percent=target, soc_now_percent=0., capacity_kwh=10.,
        hourly_load_kwh=load or {}, hourly_pv_kwh=pv or {},
        uncertainty=PvForecastUncertainty(1.,0.,0.,1,"test"), cost_model=cost_model,
        joint_scenarios=(ForecastScenario("base",1.,1.,1.),))


def test_sales_are_income_and_terminal_energy_is_not_exported_twice(contract):
    cost_model = apply_financial_contract(model(), "2026-10-16")
    result = candidate(cost_model, pv={12:12.})
    assert result.expected_sell_kwh == pytest.approx(2.)
    assert result.expected_sell_opportunity_cost_yen == pytest.approx(-48.)
    assert result.expected_terminal_energy_credit_yen == pytest.approx(288.5)
    assert result.total_expected_cost_yen == pytest.approx(-336.5)
    no_sales_value = candidate(replace(cost_model,sell_revenue_yen_per_kwh=0),pv={12:12.})
    assert no_sales_value.total_expected_cost_yen-result.total_expected_cost_yen == pytest.approx(48.)
    breakdown = _decision_cost_breakdown(to_plain_dict(result))
    assert sum(v for k,v in breakdown.items() if k!='total_expected_yen') == pytest.approx(result.total_expected_cost_yen)


def test_terminal_value_cannot_make_grid_charge_and_hold_profitable(contract):
    cost_model = apply_financial_contract(model(), "2026-10-16")
    empty = candidate(cost_model)
    full = candidate(cost_model,target=100)
    assert full.total_expected_cost_yen > empty.total_expected_cost_yen
    assert full.expected_terminal_energy_credit_yen <= full.night_charge_cost_yen
    assert candidate(replace(cost_model,terminal_energy_value_yen_per_kwh=999),target=100).total_expected_cost_yen == full.total_expected_cost_yen


@pytest.mark.parametrize("before,expected", [(89.,110.),(229.,126.34)])
def test_tier_crossings_and_purchase_direction(contract,before,expected):
    cost_model = apply_financial_contract(replace(model(),monthly_day_buy_kwh_before_target=before),"2026-10-16")
    result = candidate(cost_model,load={18:3.})
    assert result.expected_day_buy_cost_yen == pytest.approx(expected)
    assert result.total_expected_cost_yen > candidate(cost_model).total_expected_cost_yen


def test_projected_month_is_included_without_extra_penalty(contract):
    cost_model = apply_financial_contract(replace(model(),monthly_day_buy_kwh_before_target=5.517,
        expected_rest_of_month_day_buy_kwh=184.651,day_buy_penalty_factor=2.,monthly_tier_landing_enabled=True),"2026-10-16")
    assert cost_model.day_buy_cost_yen(3.) == pytest.approx(3*39.1)
    assert cost_model.monthly_tier_landing_penalty_yen(3.) == 0


def test_contract_config_is_deployable():
    data = json.loads(financial_contract.CONTRACT_PATH.read_text())
    assert data['export_start_month'] == '2026-09'
    assert data['export_years_1_to_4_yen_per_kwh'] == 24
    assert data['export_years_5_to_10_yen_per_kwh'] == 8.3


def test_month_only_start_does_not_invent_day_or_switch_rate_early(contract):
    data = json.loads(contract.read_text())
    data.update(export_start_date=None,export_start_month='2026-09')
    contract.write_text(json.dumps(data))
    current = apply_financial_contract(model(), '2026-10-16')
    assert current.sell_revenue_yen_per_kwh == 24
    assert current.financial_contract_start_date == ''
    assert current.financial_contract_start_date_confirmed is False
    assert apply_financial_contract(model(),'2030-08-31').sell_revenue_yen_per_kwh == 24
    assert apply_financial_contract(model(),'2030-10-01').sell_revenue_yen_per_kwh == 8.3
    for day in ['2026-09-15','2030-09-01','2030-09-30','2036-09-01']:
        with pytest.raises(ValueError,match='Exact export start date'):
            apply_financial_contract(model(),day)
