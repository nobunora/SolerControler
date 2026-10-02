from dataclasses import asdict

import pytest

from app.energy_plan.cost_audit import candidate_cost_breakdown
from app.energy_plan.soc_cost import SocCandidate, SocCostModel
from scripts.evaluate_soc_feedback_local import plan_exclusion, replay


def test_missing_adopted_plan_never_learns():
    result = replay([{"date": "2026-10-01", "result": {"target_soc_7_percent": 97}}], [])
    assert result["records"][0]["reason"] == "plan_provenance_missing"
    assert not result["production_connected"]


def test_complete_schema_does_not_accept_forecast_owner_as_adopted():
    plan = {"date": "2026-10-01", "decision_id": "one", "issued_at": "2026-10-01T03:00:00+09:00", "decision_at": "2026-10-01T03:00:00+09:00",
            "model_version": "v1", "contract_version": "inactive", "price_version": "p1", "adopted_slot": "02:35"}
    assert plan_exclusion(plan, plan["date"]) == "adopted_plan_missing"
    plan["adopted_slot"] = "03"
    assert plan_exclusion(plan, plan["date"]) == "cost_model_incomplete"
    plan["daytime_soc_optimization"] = {"cost_model": asdict(SocCostModel(39, 28, .93, .75))}
    assert plan_exclusion(plan, plan["date"]) == "forecast_incomplete"


@pytest.mark.parametrize("mode,export", [("neutral", 0), ("penalty", 39), ("revenue", -10)])
def test_financial_and_policy_breakdown(mode, export):
    model = SocCostModel(39, 28, .93, .75, export_value_mode=mode, sell_revenue_yen_per_kwh=10)
    candidate = SocCandidate(target_soc_percent=50, target_energy_kwh=4, required_night_charge_kwh=2,
                             night_charge_cost_yen=56, expected_day_buy_kwh=1, expected_sell_kwh=1,
                             expected_day_buy_cost_yen=39, expected_sell_opportunity_cost_yen=export,
                             expected_peak_unmet_kwh=0, expected_peak_unmet_cost_yen=0,
                             expected_monthly_tier_landing_penalty_yen=0, decision_prior_cost_yen=0,
                             total_expected_cost_yen=95 + export, scenario_replays=())
    result = candidate_cost_breakdown(candidate, model)
    assert result["projected_financial_yen"] == 85 if mode == "revenue" else result["projected_financial_yen"] == 95
    assert result["export_policy_yen"] == (39 if mode == "penalty" else 0)


def test_complete_plan_is_idempotent_and_corrected_actual_rebuilds(tmp_path):
    model = asdict(SocCostModel(39, 28, .93, .75, export_value_mode="neutral"))
    plan = {"date": "2026-10-01", "decision_id": "one", "issued_at": "2026-09-30T18:00:00Z", "decision_at": "2026-09-30T18:00:00Z",
            "model_version": "v1", "contract_version": "inactive", "price_version": "p1", "adopted_slot": "03",
            "terminal_value_yen_per_kwh": 0,
            "inputs": {"soc_now_percent": 0, "expected_overnight_discharge_kwh": 0}, "result": {"effective_capacity_kwh": 8, "target_soc_7_percent": 50},
            "daytime_soc_optimization": {"cost_model": model, "constraints": {"min": 0, "max": 100},
                "peak_policy": {"enabled": False, "target_percent": 0, "rate_yen_per_kwh": 0},
                "forecast_scenarios": [{"label": "point", "probability": 1, "pv_multiplier": 1, "load_multiplier": 1}],
                "expected_peak_unmet_kwh": 0, "expected_peak_unmet_cost_yen": 0,
                "candidate_grid": list(range(101)), "hourly_pv_forecast_kwh": {str(h): 0 for h in range(24)},
                "hourly_load_forecast_kwh": {str(h): 1 for h in range(24)}}}
    csv_path = tmp_path / "actual.csv"
    header = "年月日,時刻,発電電力量[kWh],消費電力量[kWh]"
    rows = [f"2026/10/01,{hour:02d}:{minute:02d},0,1" for hour in range(24) for minute in (0, 30)]
    csv_path.write_text("\n".join([header, *rows]), encoding="utf-8-sig")
    first = replay([plan], [csv_path])
    assert first["records"][0]["eligible"]
    assert first["records"][0]["actual_summary"]["load_kwh"] == 32
    assert first["records"][0]["actual_summary"]["pv_kwh"] == 0
    assert first == replay([plan], [csv_path])
    csv_path.write_text("\n".join([header, *[r.replace(",0,1", ",0,2") for r in rows]]), encoding="utf-8-sig")
    corrected = replay([plan], [csv_path])
    assert first["actual_version"] != corrected["actual_version"]
    assert first["records"][0]["feedback"] != corrected["records"][0]["feedback"]
