from __future__ import annotations

import app.energy_model as legacy
import app.energy_plan.energy_model as canonical


def test_legacy_energy_model_exports_canonical_objects() -> None:
    for name in (
        "DaytimeSocOptimizationResult", "EnergyModelCoefficients", "NightChargeInputs",
        "NightChargeResult", "compute_night_charge_target", "effective_capacity_kwh",
        "fit_coefficients_from_csv", "forecast_pv_energy_kwh", "optimize_target_soc_for_daytime",
        "to_dict",
    ):
        assert getattr(legacy, name) is getattr(canonical, name)
