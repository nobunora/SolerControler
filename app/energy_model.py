"""Compatibility exports for energy-plan calculations.

New code should import from :mod:`app.energy_plan.energy_model`.
"""

from app.energy_plan.energy_model import (
    DaytimeSocOptimizationResult,
    EnergyModelCoefficients,
    NightChargeInputs,
    NightChargeResult,
    compute_night_charge_target,
    effective_capacity_kwh,
    fit_coefficients_from_csv,
    forecast_pv_energy_kwh,
    optimize_target_soc_for_daytime,
    to_dict,
)

__all__ = [
    "DaytimeSocOptimizationResult", "EnergyModelCoefficients", "NightChargeInputs",
    "NightChargeResult", "compute_night_charge_target", "effective_capacity_kwh",
    "fit_coefficients_from_csv", "forecast_pv_energy_kwh", "optimize_target_soc_for_daytime",
    "to_dict",
]
