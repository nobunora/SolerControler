from copy import deepcopy
from dataclasses import asdict
from unittest.mock import patch

import pytest

from app.energy_plan.local_replay import evaluate_replay_day
from app.energy_plan.soc_cost import SocCostModel
from app.forecasting.hourly_residual_candidate import evaluate_hourly_residuals
from scripts.evaluate_soc_feedback_local import plan_exclusion
from scripts.normalize_soc_replay_inputs import normalize_saved_plan


def plan():
    zero = {str(h): 0.0 for h in range(7, 23)}
    return {"date": "2026-08-01", "decision_id": "d", "issued_at": "2026-08-01T03:00:00+09:00",
            "decision_at": "2026-08-01T03:00:00+09:00", "adopted_slot": "03", "model_version": "v",
            "contract_version": "c", "price_version": "p", "terminal_value_yen_per_kwh": 0,
            "inputs": {"soc_now_percent": 0, "expected_overnight_discharge_kwh": 0},
            "result": {"effective_capacity_kwh": 8, "target_soc_7_percent": 50},
            "daytime_soc_optimization": {"cost_model": asdict(SocCostModel(40, 10, 1, .75, export_value_mode="neutral")),
                "candidate_grid": [0, 50, 100], "constraints": {"min": 0, "max": 100},
                "peak_policy": {"enabled": False, "target_percent": 0, "rate_yen_per_kwh": 0},
                "forecast_scenarios": [{"label": "point", "probability": 1, "pv_multiplier": 1, "load_multiplier": 1}],
                "hourly_pv_forecast_kwh": zero, "hourly_load_forecast_kwh": dict(zero)}}


def actual(load=0):
    return {"pv": {h: 0 for h in range(7, 23)}, "load": {h: load if h == 7 else 0 for h in range(7, 23)}}


def test_selection_uses_forecast_and_prior_but_not_current_actual():
    p = plan()
    prior = {"applied": True, "weight": 1, "max_penalty_yen": 200, "regret_yen_by_soc": {0: 200, 50: 0, 100: 0}}
    with patch("app.energy_plan.local_replay.build_soc_decision_prior", return_value=prior) as loader:
        low = evaluate_replay_day(p, actual(), [])
        high = evaluate_replay_day(p, actual(10), [])
    for name in low["comparisons"]:
        assert low["comparisons"][name]["selected"] == high["comparisons"][name]["selected"]
    assert low["comparisons"]["baseline:plain"]["selected"]["target_soc_percent"] == 0
    assert low["comparisons"]["baseline:learned"]["selected"]["target_soc_percent"] == 50
    assert loader.call_args.kwargs["target_features"] == {"forecast_pv_kwh": 0, "forecast_load_kwh": 0}
    p["daytime_soc_optimization"]["hourly_load_forecast_kwh"]["7"] = 4
    assert evaluate_replay_day(p, actual(), [])["comparisons"]["baseline:plain"]["selected"]["target_soc_percent"] == 50


def test_forecast_actual_identity_and_monthly_policy():
    p = plan(); opt = p["daytime_soc_optimization"]
    opt["hourly_load_forecast_kwh"]["7"] = 4
    opt["cost_model"].update(monthly_tier_landing_enabled=True, tier1_underuse_penalty_yen_per_kwh=2)
    r = evaluate_replay_day(p, actual(4), [])["comparisons"]["baseline:plain"]
    assert r["selected"] == r["realized"]
    assert r["realized"]["costs"]["monthly_policy_yen"] > 0
    c = r["realized"]["costs"]
    assert c["total_objective_yen"] == pytest.approx(sum(c[k] for k in (
        "projected_financial_yen", "export_policy_yen", "day_import_policy_yen", "peak_policy_yen", "monthly_policy_yen", "learning_prior_yen")))


def test_reachable_energy_and_terminal_value():
    p = plan(); p["inputs"]["soc_now_percent"] = 80
    r = evaluate_replay_day(p, actual(), [])
    assert r["excluded_unreachable_candidates"] == 2
    score = r["comparisons"]["baseline:plain"]["realized"]
    assert score["end_energy_kwh"] == pytest.approx(6.4 + score["night_charge_kwh"])
    p["terminal_value_yen_per_kwh"] = 10
    valued = evaluate_replay_day(p, actual(), [])["comparisons"]["baseline:plain"]["realized"]
    assert valued["financial_net_terminal_yen"] == pytest.approx(score["financial_net_terminal_yen"] - 80)


@pytest.mark.parametrize("mutation,reason", [
    (lambda p: p.update(issued_at="2026-08-01T23:59:00+09:00"), "issue_invalid"),
    (lambda p: p["daytime_soc_optimization"]["cost_model"].update(day_buy_rate_yen_per_kwh="invalid"), "cost_model_invalid"),
    (lambda p: p["daytime_soc_optimization"]["constraints"].update(max=40), "candidate_grid_invalid"),
])
def test_semantic_validation(mutation, reason):
    p = plan(); assert plan_exclusion(p, p["date"]) is None
    mutation(p); assert plan_exclusion(p, p["date"]) == reason


def test_residual_timezone_equivalence():
    r = {"date": "2026-08-01", "hour": 7, "q": 1, "actual": 2, "issued_at": "2026-07-31T18:00:00Z",
         "target_at": "2026-08-01T07:00:00+09:00", "baseline_version": "v", "kind": "pv", "basis": "reconstructed",
         "actual_interval_minutes": [0, 30]}
    utc = deepcopy(r); utc["target_at"] = "2026-07-31T22:00:00Z"
    assert evaluate_hourly_residuals([r], alpha=.2)["metrics"] == evaluate_hourly_residuals([utc], alpha=.2)["metrics"]


def saved_plan():
    p = plan(); p["forecast"] = {"date": p["date"]}; p["inputs"]["reserve_soc_percent"] = 0
    o = p["daytime_soc_optimization"]
    o.update(max_target_soc_percent_after_guards=100, evaluated_candidate_count=101,
             candidate_summaries=[{"target_soc_percent": 50}],
             forecast_correction={"soc_peak_unmet_penalty": {"enabled": False}},
             expected_peak_unmet_kwh=0, expected_peak_unmet_cost_yen=0)
    return p


def test_normalization_preserves_disabled_policy_and_battery_state_is_not_tariff_version():
    p = saved_plan()
    kwargs = {"issued_at": p["issued_at"], "pv_variants": {"baseline": p["daytime_soc_optimization"]["hourly_pv_forecast_kwh"]},
              "grid_step_percent": 1, "terminal_value_factor": 1, "legacy_projection_enabled": False}
    first = normalize_saved_plan(p, **kwargs)
    p["daytime_soc_optimization"]["cost_model"]["charge_efficiency"] = .9
    second = normalize_saved_plan(p, **kwargs)
    assert first["contract_version"] == second["contract_version"]
    assert first["adopted_slot"] == "reconstructed_experiment"
    assert first["daytime_soc_optimization"]["peak_policy"] == {"enabled": False, "target_percent": 0, "rate_yen_per_kwh": 0}
    assert plan_exclusion(first, first["date"]) is None


def test_normalization_does_not_invent_zero_zero_peak_rate():
    p = saved_plan(); p["daytime_soc_optimization"]["forecast_correction"]["soc_peak_unmet_penalty"]["enabled"] = True
    with pytest.raises(ValueError, match="enabled_peak_rate_unrecoverable"):
        normalize_saved_plan(p, issued_at=p["issued_at"], pv_variants={}, grid_step_percent=1,
                             terminal_value_factor=0, legacy_projection_enabled=False)
