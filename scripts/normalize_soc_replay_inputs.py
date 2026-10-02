"""Normalize saved plans for explicitly reconstructed, local SOC experiments."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


def normalize_saved_plan(
    saved: dict[str, Any], *, issued_at: str, pv_variants: dict[str, dict[str, float]],
    grid_step_percent: float, terminal_value_factor: float, legacy_projection_enabled: bool,
) -> dict[str, Any]:
    """Never label a forecast-owner plan as an adopted 03 decision.

    Missing legacy projection flags and grid step are explicit experiment inputs.
    A saved zero/zero peak cost cannot identify an enabled peak penalty rate.
    """
    plan = deepcopy(saved)
    day = plan["forecast"]["date"]
    opt = plan["daytime_soc_optimization"]
    model = opt["cost_model"]
    if not 0 < grid_step_percent <= 10 or terminal_value_factor < 0:
        raise ValueError("invalid_experiment_parameters")
    assumptions = ["conditional day with saved initial SOC and monthly tariff state",
                   "candidate grid reconstructed from saved reserve/cap and explicit step"]
    if "monthly_tariff_projection_enabled" not in model:
        model["monthly_tariff_projection_enabled"] = legacy_projection_enabled
        assumptions.append(f"legacy monthly_tariff_projection_enabled={legacy_projection_enabled}")
    peak = opt["forecast_correction"]["soc_peak_unmet_penalty"]
    if peak.get("enabled") is False:
        policy = {"enabled": False, "target_percent": 0, "rate_yen_per_kwh": 0}
    elif peak.get("enabled") is True and opt["expected_peak_unmet_kwh"] > 0:
        policy = {"enabled": True, "target_percent": peak["target_peak_soc_percent"],
                  "rate_yen_per_kwh": opt["expected_peak_unmet_cost_yen"] / opt["expected_peak_unmet_kwh"]}
    else:
        raise ValueError("enabled_peak_rate_unrecoverable")
    low = plan["inputs"]["reserve_soc_percent"]
    high = max(low, opt["max_target_soc_percent_after_guards"])
    grid = []
    value = low
    while value <= high + 1e-9:
        grid.append(round(min(value, high), 8))
        value += grid_step_percent
    # This is the saved optimizer's inclusive step-loop, not an invented endpoint.
    if len(grid) != opt["evaluated_candidate_count"]:
        raise ValueError("reconstructed_grid_count_mismatch")
    for summary in opt["candidate_summaries"]:
        if not any(abs(summary["target_soc_percent"] - t) < 1e-6 for t in grid):
            raise ValueError("reconstructed_grid_summary_mismatch")
    opt.update(peak_policy=policy, candidate_grid=grid, constraints={"min": low, "max": high}, pv_variants=pv_variants)
    opt["hourly_pv_forecast_kwh"] = pv_variants["baseline"]
    contract = {k: v for k, v in model.items() if k not in {
        "monthly_day_buy_kwh_before_target", "expected_rest_of_month_day_buy_kwh", "charge_efficiency"}}
    # Efficiency is a per-decision battery input, not a tariff/policy version.
    contract["peak_policy"] = policy
    digest = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    plan.update(date=day, issued_at=issued_at, decision_at=issued_at,
                decision_id=f"reconstructed:{day}", adopted_slot="reconstructed_experiment",
                model_version="paired-local-v2", contract_version=digest, price_version=digest,
                terminal_value_yen_per_kwh=terminal_value_factor * model["night_buy_rate_yen_per_kwh"] / model["charge_efficiency"],
                reconstruction_assumptions=assumptions)
    return plan
