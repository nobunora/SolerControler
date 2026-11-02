"""Prediction-error validation under the saved planning contract, not bills."""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any

from app.energy_plan.battery_calibration import ValidationEvaluator
from app.energy_plan.battery_response import BatterySocResponse
from app.energy_plan.soc_cost import SocCostModel


def _energy(row: dict[str, Any], key: str) -> float:
    value = float(row[key])
    if not isfinite(value) or value < 0:
        raise ValueError(f"missing or invalid actual {key}")
    return value


def make_battery_validation_evaluator(cost_model: SocCostModel) -> ValidationEvaluator:
    """Return daytime observed-state grid/cash MAE comparison.

    Beginning SOC is measured for every interval, so this isolates response
    error rather than allowing a recursively drifting SOC to hide PV/load
    errors. Forced night charging is excluded. Each cash value reuses the same
    saved contract valuation; these are not historical invoices or savings.
    """
    if not isfinite(cost_model.sell_revenue_yen_per_kwh) or cost_model.sell_revenue_yen_per_kwh < 0:
        raise ValueError("invalid saved export revenue")

    def evaluate(candidate: BatterySocResponse, baseline: BatterySocResponse, rows: list[dict[str, Any]]) -> dict[str, float]:
        errors: dict[str, list[float]] = {key: [] for key in ("candidate_buy_mae", "baseline_buy_mae", "candidate_fee_mae", "baseline_fee_mae")}
        for row in rows:
            timestamp = row.get("dt")
            if not isinstance(timestamp, datetime):
                raise ValueError("holdout timestamp missing")
            if not 7 <= timestamp.hour < 23:
                continue
            pv, load = _energy(row, "pv"), _energy(row, "load")
            actual_buy, actual_sell = _energy(row, "buy"), _energy(row, "sell")
            soc, duration = _energy(row, "soc_start"), _energy(row, "duration_hours")
            if soc > 100 or duration <= 0:
                raise ValueError("invalid measured SOC or interval duration")
            actual_fee = cost_model.day_buy_cost_yen(actual_buy) - actual_sell * cost_model.sell_revenue_yen_per_kwh
            for label, response in (("candidate", candidate), ("baseline", baseline)):
                charge_coefficient = _energy(row, f"{label}_charge_soc_per_kwh")
                discharge_coefficient = _energy(row, f"{label}_discharge_soc_per_kwh")
                if not 0 < charge_coefficient <= discharge_coefficient:
                    raise ValueError("invalid holdout response coefficients")
                start_soc = max(0.0, soc - response.drift_soc_per_hour * duration)
                deficit, surplus = max(0.0, load - pv), max(0.0, pv - load)
                discharge = min(deficit, start_soc / discharge_coefficient)
                charge = min(surplus, (100.0 - start_soc) / charge_coefficient)
                buy, sell = deficit - discharge, surplus - charge
                fee = cost_model.day_buy_cost_yen(buy) - sell * cost_model.sell_revenue_yen_per_kwh
                if not isfinite(fee) or not isfinite(actual_fee):
                    raise ValueError("nonfinite saved contract evaluation")
                errors[f"{label}_buy_mae"].append(abs(buy - actual_buy))
                errors[f"{label}_fee_mae"].append(abs(fee - actual_fee))
        if not errors["candidate_buy_mae"]:
            raise ValueError("no valid daytime meter holdout")
        return {key: sum(values) / len(values) for key, values in errors.items()}

    return evaluate
