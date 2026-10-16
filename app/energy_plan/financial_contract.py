"""Date-bound export contract and conservative carried-energy valuation."""
from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import date
from pathlib import Path

from app.energy_plan.soc_cost import SocCostModel

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "config" / "energy_financial_contract.json"


def apply_financial_contract(model: SocCostModel, target_date: str) -> SocCostModel:
    """Use the forecast date, never the process clock, for contract anniversaries."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    target = date.fromisoformat(target_date)
    exact_start = contract.get("export_start_date")
    start = date.fromisoformat(exact_start or f"{contract['export_start_month']}-01")
    # February 29 contracts renew on February 28 in non-leap years.
    def anniversary(years: int) -> date:
        try:
            return start.replace(year=start.year + years)
        except ValueError:
            return date(start.year + years, 2, 28)

    if not exact_start and target.year in {start.year, start.year + 4, start.year + 10} and target.month == start.month:
        raise ValueError("Exact export start date is required in a contract transition month")
    if target < start:
        return model
    if target >= anniversary(10):
        raise ValueError("Export contract expired; a reviewed successor tariff is required")
    rate_key = "export_years_1_to_4_yen_per_kwh" if target < anniversary(4) else "export_years_5_to_10_yen_per_kwh"
    rate = float(contract[rate_key])
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("Export contract rate must be finite and positive")
    if not math.isfinite(model.night_buy_rate_yen_per_kwh) or model.night_buy_rate_yen_per_kwh < 0:
        raise ValueError("Night purchase rate must be finite and nonnegative")
    tiers = (model.day_tier1_rate_yen_per_kwh, model.day_tier2_rate_yen_per_kwh, model.day_tier3_rate_yen_per_kwh)
    if any(not math.isfinite(value) or value < 0 for value in tiers):
        raise ValueError("Purchase tariff rates must be finite and nonnegative")
    if not (0 < model.day_tier1_upper_kwh < model.day_tier2_upper_kwh < math.inf):
        raise ValueError("Purchase tariff thresholds must be finite and increasing")
    if not math.isfinite(model.charge_efficiency) or not 0 < model.charge_efficiency <= 1:
        raise ValueError("Usable charging efficiency must be in (0, 1]")
    return replace(
        model, export_value_mode="revenue", sell_revenue_yen_per_kwh=rate,
        tariff_mode="night8_tiered", monthly_tariff_projection_enabled=True,
        day_buy_penalty_factor=1.0, monthly_tier_landing_enabled=False,
        terminal_energy_value_yen_per_kwh=model.night_buy_rate_yen_per_kwh,
        financial_contract_start_date=start.isoformat() if exact_start else "",
        financial_contract_start_month=start.strftime("%Y-%m"),
        financial_contract_start_date_confirmed=bool(exact_start),
        financial_contract_rate_end_date=(anniversary(4) if target < anniversary(4) else anniversary(10)).isoformat(),
        financial_objective="net_purchase_minus_export_and_carried_energy",
    )
