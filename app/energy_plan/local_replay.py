"""Offline paired SOC decisions using the planner's cost model for both paths."""
from __future__ import annotations

from typing import Any

from app.energy_plan.cost_audit import candidate_cost_breakdown
from app.energy_plan.decision_feedback import build_soc_decision_prior
from app.energy_plan.soc_cost import (
    ForecastScenario, PvForecastUncertainty, SocCostModel, evaluate_soc_candidate,
)


def evaluate_replay_day(
    plan: dict[str, Any], actual: dict[str, dict[int, float]],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Select using forecasts/past feedback, then score against completed actuals.

    Each day is conditional on the saved morning energy and tariff state; totals
    are not a continuous-month bill simulation. No production state is written.
    """
    opt = plan["daytime_soc_optimization"]
    model = SocCostModel(**opt["cost_model"])
    capacity = plan["result"]["effective_capacity_kwh"]
    overnight = plan["inputs"]["expected_overnight_discharge_kwh"]
    morning = max(0.0, plan["inputs"]["soc_now_percent"] * capacity / 100 - overnight)
    # A charging-only action cannot lower morning energy to a smaller target.
    grid = [v for v in opt["candidate_grid"] if v * capacity / 100 >= morning - 1e-9]
    if not grid:
        raise ValueError("no_reachable_candidate")
    peak = opt["peak_policy"]
    terminal_rate = plan["terminal_value_yen_per_kwh"]
    point = (ForecastScenario("point", 1.0, 1.0, 1.0),)
    scenarios = tuple(ForecastScenario(**s) for s in opt["forecast_scenarios"])
    uncertainty = PvForecastUncertainty(1, 0, 0, 0, "explicit_replay_scenarios")

    def evaluate(target, pv, load, scenario_set, prior=None):
        prior = prior or {}
        candidate = evaluate_soc_candidate(
            target_soc_percent=target, soc_now_percent=plan["inputs"]["soc_now_percent"],
            capacity_kwh=capacity, hourly_pv_kwh=pv, hourly_load_kwh=load,
            uncertainty=uncertainty, cost_model=model, joint_scenarios=scenario_set,
            expected_overnight_discharge_kwh=overnight,
            peak_soc_target_percent=peak["target_percent"] if peak["enabled"] else None,
            peak_soc_unmet_penalty_yen_per_kwh=peak["rate_yen_per_kwh"] if peak["enabled"] else 0,
            peak_soc_unmet_penalty_factor=1,
            decision_prior_regret_yen_by_soc=prior.get("regret_yen_by_soc"),
            decision_prior_weight=prior.get("weight", 0) if prior.get("applied") else 0,
            decision_prior_max_penalty_yen=prior.get("max_penalty_yen", 0),
        )
        end_energy = sum(s.probability * s.end_soc_percent * capacity / 100 for s in candidate.scenario_replays)
        costs = candidate_cost_breakdown(candidate, model)
        return {
            "target_soc_percent": target, "objective_yen": candidate.total_expected_cost_yen - terminal_rate * end_energy,
            "financial_net_terminal_yen": costs["projected_financial_yen"] - terminal_rate * end_energy,
            "end_energy_kwh": end_energy, "terminal_credit_yen": terminal_rate * end_energy,
            "buy_kwh": candidate.expected_day_buy_kwh, "sell_kwh": candidate.expected_sell_kwh,
            "night_charge_kwh": candidate.required_night_charge_kwh, "costs": costs,
        }

    forecast_load = {int(h): v for h, v in opt["hourly_load_forecast_kwh"].items()}
    variants = opt.get("pv_variants", {"baseline": opt["hourly_pv_forecast_kwh"]})
    # No current actual is passed to selection or prior construction.
    selections = {}
    for name, raw_pv in variants.items():
        pv = {int(h): v for h, v in raw_pv.items()}
        features = {"forecast_pv_kwh": sum(pv.get(h, 0) for h in range(7, 23)),
                    "forecast_load_kwh": sum(forecast_load[h] for h in range(7, 23))}
        prior = build_soc_decision_prior(history, target_date=plan["date"], target_features=features)
        for learning in (False, True):
            candidates = [evaluate(t, pv, forecast_load, scenarios, prior if learning else None) for t in grid]
            selected = min(candidates, key=lambda r: (r["objective_yen"], r["target_soc_percent"]))
            selections[f"{name}:{'learned' if learning else 'plain'}"] = {
                "selected": selected, "prior": prior if learning else {"applied": False}}

    realized = {t: evaluate(t, actual["pv"], actual["load"], point) for t in grid}
    best = min(realized.values(), key=lambda r: (r["objective_yen"], r["target_soc_percent"]))
    for selection in selections.values():
        score = realized[selection["selected"]["target_soc_percent"]]
        selection["realized"] = score
        selection["regret_yen"] = max(0, score["objective_yen"] - best["objective_yen"])
    feedback = {
        "date": plan["date"], "model_version": plan["model_version"],
        "best_target_soc_percent": best["target_soc_percent"],
        "decision_features": {"actual_pv_kwh": sum(actual["pv"].values()), "actual_load_kwh": sum(actual["load"].values())},
        "points": [{"target_soc_percent": t, "regret_yen": max(0, r["objective_yen"] - best["objective_yen"])}
                   for t, r in realized.items()],
    }
    return {"comparisons": selections, "feedback": feedback,
            "actual_summary": {"pv_kwh": sum(actual["pv"].values()), "load_kwh": sum(actual["load"].values()),
                               "start_hour": 7, "end_hour_exclusive": 23},
            "hindsight_best": best, "reachable_candidates": len(grid),
            "excluded_unreachable_candidates": len(opt["candidate_grid"]) - len(grid)}
