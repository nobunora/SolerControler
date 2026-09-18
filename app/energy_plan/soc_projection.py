from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DaytimeSocProjection:
    soc_at_hour_start_percent: dict[int, float]
    buy_kwh: float
    sell_kwh: float
    max_soc_percent: float
    first_full_hour: int | None
    end_soc_percent: float


def _bounded_soc(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def simulate_night_charge_soc(
    *,
    current_soc_percent: float,
    capacity_kwh: float,
    charge_efficiency: float,
    anchor_hour: int,
    hourly_grid_charge_kwh: dict[int, float],
) -> dict[int, float]:
    if capacity_kwh <= 0.0:
        raise ValueError("capacity_kwh must be positive")
    anchor = max(0, min(7, int(anchor_hour)))
    efficiency = max(0.01, min(1.0, float(charge_efficiency)))
    energy = capacity_kwh * _bounded_soc(current_soc_percent) / 100.0
    result = {anchor: round(_bounded_soc(100.0 * energy / capacity_kwh), 4)}
    for hour in range(anchor, 7):
        grid_charge = max(0.0, float(hourly_grid_charge_kwh.get(hour, 0.0)))
        energy = min(capacity_kwh, energy + grid_charge * efficiency)
        result[hour + 1] = round(_bounded_soc(100.0 * energy / capacity_kwh), 4)
    return result


def simulate_daytime_soc(
    *,
    capacity_kwh: float,
    start_energy_kwh: float | None = None,
    target_soc_7_percent: float | None = None,
    hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float],
    pv_multiplier: float = 1.0,
    load_multiplier: float = 1.0,
) -> DaytimeSocProjection:
    if capacity_kwh <= 0.0:
        raise ValueError("capacity_kwh must be positive")
    if start_energy_kwh is None:
        if target_soc_7_percent is None:
            raise ValueError("start_energy_kwh or target_soc_7_percent is required")
        start_energy_kwh = capacity_kwh * _bounded_soc(target_soc_7_percent) / 100.0

    energy = max(0.0, min(capacity_kwh, float(start_energy_kwh)))
    buy_kwh = 0.0
    sell_kwh = 0.0
    max_energy = energy
    first_full_hour: int | None = None
    soc_by_hour: dict[int, float] = {7: round(_bounded_soc(100.0 * energy / capacity_kwh), 4)}

    for hour in range(7, 23):
        load = max(0.0, float(hourly_load_kwh.get(hour, 0.0))) * max(0.0, load_multiplier)
        pv = max(0.0, float(hourly_pv_kwh.get(hour, 0.0))) * max(0.0, pv_multiplier)
        net = pv - load
        if net >= 0.0:
            charge = min(capacity_kwh - energy, net)
            energy += charge
            sell_kwh += max(0.0, net - charge)
        else:
            need = -net
            discharge = min(energy, need)
            energy -= discharge
            buy_kwh += max(0.0, need - discharge)

        max_energy = max(max_energy, energy)
        if first_full_hour is None and energy >= capacity_kwh * 0.999:
            first_full_hour = hour
        soc_by_hour[hour + 1] = round(_bounded_soc(100.0 * energy / capacity_kwh), 4)

    return DaytimeSocProjection(
        soc_at_hour_start_percent=soc_by_hour,
        buy_kwh=buy_kwh,
        sell_kwh=sell_kwh,
        max_soc_percent=_bounded_soc(100.0 * max_energy / capacity_kwh),
        first_full_hour=first_full_hour,
        end_soc_percent=_bounded_soc(100.0 * energy / capacity_kwh),
    )
