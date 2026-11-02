"""One AC-energy/SOC convention shared by planning and frozen replay."""
from __future__ import annotations

import math

from app.energy_plan.battery_response import BatterySocResponse


def dispatch_soc(*, soc: float, pv_kwh: float, load_kwh: float,
                 response: BatterySocResponse, hour: int,
                 duration_hours: float = 1.0) -> tuple[float, float, float]:
    if any(not math.isfinite(v) for v in (soc, pv_kwh, load_kwh, duration_hours)) or duration_hours <= 0:
        raise ValueError("battery replay requires finite state and a positive interval")
    state = max(0.0, min(100.0, soc) - response.drift_soc_per_hour * duration_hours)
    net = max(0.0, pv_kwh) - max(0.0, load_kwh)
    if net >= 0:
        coefficient = response.charge_coefficient(hour)
        charge = min(net, (100.0 - state) / coefficient)
        return 0.0, max(0.0, net - charge), min(100.0, state + coefficient * charge)
    coefficient = response.discharge_coefficient(hour)
    discharge = min(-net, state / coefficient)
    return max(0.0, -net - discharge), 0.0, max(0.0, state - coefficient * discharge)


def night_charge_ac_kwh(*, initial_soc: float, target_soc: float,
                        response: BatterySocResponse,
                        percent_per_hour: float | None = None) -> float:
    """Integrate the same hour-dependent conversion used by the saved night path.

    An unavailable speed uses the 03 coefficient explicitly; it never invents
    an arrival time. Reachability/cutoff remain the existing monitor's contract.
    """
    if any(not math.isfinite(v) for v in (initial_soc, target_soc)):
        raise ValueError("night charge requires finite SOC")
    gain = max(0.0, min(100.0, target_soc) - max(0.0, min(100.0, initial_soc)))
    if percent_per_hour is None:
        return gain / response.charge_coefficient(3)
    if not math.isfinite(percent_per_hour) or percent_per_hour <= 0:
        raise ValueError("night charge rate must be finite and positive")
    energy = 0.0
    for hour in range(3, 7):
        duration = 0.75 if hour == 6 else 1.0
        portion = min(gain, percent_per_hour * duration)
        energy += portion / response.charge_coefficient(hour)
        gain -= portion
    # Required energy is still priced if a target exceeds the monitor window;
    # the independent projection records that target as unreachable.
    return energy + gain / response.charge_coefficient(6)
