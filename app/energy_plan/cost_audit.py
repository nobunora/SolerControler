"""Read-only financial and policy breakdown of an existing SOC candidate."""

from __future__ import annotations

from dataclasses import replace

from app.energy_plan.soc_cost import SocCandidate, SocCostModel


def candidate_cost_breakdown(candidate: SocCandidate, model: SocCostModel) -> dict[str, float | str]:
    """Do not reprice or alter optimization; disclose projected tariff assumptions."""
    tariff = replace(model, day_buy_penalty_factor=1.0)
    day_tariff = tariff.day_buy_cost_yen(candidate.expected_day_buy_kwh)
    revenue_mode = model.export_value_mode.strip().lower() == "revenue"
    revenue = candidate.expected_sell_opportunity_cost_yen if revenue_mode else 0.0
    export_policy = 0.0 if revenue_mode else candidate.expected_sell_opportunity_cost_yen
    financial = candidate.night_charge_cost_yen + day_tariff + revenue
    return {
        "night_import_yen": candidate.night_charge_cost_yen,
        "projected_day_tariff_yen": day_tariff,
        "export_revenue_cost_yen": revenue,
        "export_policy_yen": export_policy,
        "day_import_policy_yen": candidate.expected_day_buy_cost_yen - day_tariff,
        "peak_policy_yen": candidate.expected_peak_unmet_cost_yen,
        "monthly_policy_yen": candidate.expected_monthly_tier_landing_penalty_yen,
        "learning_prior_yen": candidate.decision_prior_cost_yen,
        "projected_financial_yen": financial,
        "total_objective_yen": candidate.total_expected_cost_yen,
        "financial_basis": "saved tariff projection; not an observed invoice",
    }
