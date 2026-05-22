from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class EnergyModelCoefficients:
    # SOC dynamics
    soc_per_kwh_charge: float
    soc_per_kwh_discharge: float
    soc_drift_per_slot: float
    battery_round_trip_efficiency: float
    battery_usable_capacity_kwh: float

    # PV / self-consumption dynamics
    pv_self_consumption_ratio: float
    pv_direct_use_ratio: float
    pv_to_battery_ratio: float
    pv_kwh_per_sunhour: float
    pv_temp_coeff_per_deg: float

    # Battery degradation / environment placeholders
    battery_temp_coeff_per_deg: float
    battery_cycle_capacity_fade_per_cycle: float


@dataclass(frozen=True)
class NightChargeInputs:
    soc_now_percent: float
    sun_hours_forecast: float
    temp_forecast_c: float
    daytime_load_forecast_kwh: float
    morning_load_forecast_kwh: float
    morning_pv_ratio: float
    midday_surplus_ratio: float
    reserve_soc_percent: float
    cycle_count: float
    battery_temp_c: float
    predicted_pv_kwh_override: float | None = None
    predicted_morning_pv_kwh_override: float | None = None
    predicted_midday_surplus_kwh_override: float | None = None


@dataclass(frozen=True)
class NightChargeResult:
    predicted_pv_kwh: float
    predicted_morning_pv_kwh: float
    predicted_morning_deficit_kwh: float
    predicted_daytime_deficit_kwh: float
    predicted_midday_surplus_kwh: float
    effective_capacity_kwh: float
    target_soc_7_percent: float
    required_night_charge_kwh: float


@dataclass(frozen=True)
class DaytimeSocOptimizationResult:
    target_soc_7_percent: float
    target_energy_kwh: float
    required_night_charge_kwh: float
    predicted_daytime_buy_kwh: float
    predicted_sunset_soc_percent: float
    predicted_sunset_energy_kwh: float


def _read_rows(csv_paths: Iterable[Path]) -> list[dict[str, float | str | datetime]]:
    rows: list[dict[str, float | str | datetime]] = []
    for path in csv_paths:
        raw = path.read_text(encoding="utf-8-sig")
        reader = csv.DictReader(raw.splitlines())
        for row in reader:
            date_text = (row.get("年月日") or "").strip()
            time_text = (row.get("時刻") or "").strip()
            if not date_text or not time_text:
                continue
            dt = datetime.strptime(f"{date_text} {time_text}", "%Y/%m/%d %H:%M")

            def f(key: str) -> float:
                v = (row.get(key) or "").strip()
                return float(v) if v else 0.0

            soc_raw = (row.get("蓄電残量(SOC)[%]") or "").strip()
            soc = float(soc_raw) if soc_raw else float("nan")

            rows.append(
                {
                    "dt": dt,
                    "pv": f("発電電力量[kWh]"),
                    "load": f("消費電力量[kWh]"),
                    "sell": f("売電電力量[kWh]"),
                    "buy": f("買電電力量[kWh]"),
                    "chg": f("充電電力量[kWh]"),
                    "dchg": f("放電電力量[kWh]"),
                    "soc": soc,
                }
            )
    rows.sort(key=lambda x: x["dt"])  # type: ignore[index]
    return rows


def fit_coefficients_from_csv(csv_paths: Iterable[Path]) -> EnergyModelCoefficients:
    rows = _read_rows(csv_paths)
    if len(rows) < 3:
        raise ValueError("CSV件数が不足しているため係数推定できません")

    sum_pv = sum(float(r["pv"]) for r in rows)
    sum_sell = sum(float(r["sell"]) for r in rows)
    sum_chg = sum(float(r["chg"]) for r in rows)

    pv_self = max(0.0, sum_pv - sum_sell)
    pv_direct = max(0.0, pv_self - sum_chg)

    x: list[list[float]] = []
    y: list[float] = []
    for i in range(1, len(rows)):
        s0 = float(rows[i - 1]["soc"])
        s1 = float(rows[i]["soc"])
        if np.isnan(s0) or np.isnan(s1):
            continue
        x.append([float(rows[i]["chg"]), float(rows[i]["dchg"]), 1.0])
        y.append(s1 - s0)

    if len(x) < 3:
        raise ValueError("SOC変化データが不足しているため係数推定できません")

    X = np.asarray(x, dtype=float)
    Y = np.asarray(y, dtype=float)
    coef, *_ = np.linalg.lstsq(X, Y, rcond=None)

    soc_per_kwh_charge = float(coef[0])
    soc_per_kwh_discharge = float(-coef[1])
    soc_drift_per_slot = float(coef[2])

    if soc_per_kwh_charge <= 0 or soc_per_kwh_discharge <= 0:
        raise ValueError("推定係数が不正です")

    eta = float(np.sqrt(soc_per_kwh_charge / soc_per_kwh_discharge))
    cap = 100.0 * eta / soc_per_kwh_charge

    # 初期値: sunshine 1時間あたり発電係数(暫定)
    # 実運用では翌日予報との実測誤差で継続フィッティングする
    pv_kwh_per_sunhour = 1.45

    return EnergyModelCoefficients(
        soc_per_kwh_charge=soc_per_kwh_charge,
        soc_per_kwh_discharge=soc_per_kwh_discharge,
        soc_drift_per_slot=soc_drift_per_slot,
        battery_round_trip_efficiency=eta,
        battery_usable_capacity_kwh=cap,
        pv_self_consumption_ratio=(pv_self / sum_pv) if sum_pv > 0 else 0.0,
        pv_direct_use_ratio=(pv_direct / sum_pv) if sum_pv > 0 else 0.0,
        pv_to_battery_ratio=(sum_chg / sum_pv) if sum_pv > 0 else 0.0,
        pv_kwh_per_sunhour=pv_kwh_per_sunhour,
        # 温度係数は初期値(将来フィッティング前提)
        pv_temp_coeff_per_deg=-0.0035,
        battery_temp_coeff_per_deg=-0.0050,
        battery_cycle_capacity_fade_per_cycle=0.00030,
    )


def forecast_pv_energy_kwh(
    sun_hours: float,
    temp_c: float,
    coeff: EnergyModelCoefficients,
    temp_ref_c: float = 25.0,
) -> float:
    factor = 1.0 + coeff.pv_temp_coeff_per_deg * (temp_c - temp_ref_c)
    return max(0.0, coeff.pv_kwh_per_sunhour * sun_hours * max(0.0, factor))


def effective_capacity_kwh(
    coeff: EnergyModelCoefficients,
    cycle_count: float,
    battery_temp_c: float,
    temp_ref_c: float = 25.0,
) -> float:
    cycle_factor = max(0.6, 1.0 - coeff.battery_cycle_capacity_fade_per_cycle * cycle_count)
    temp_factor = max(0.7, 1.0 + coeff.battery_temp_coeff_per_deg * (battery_temp_c - temp_ref_c))
    return coeff.battery_usable_capacity_kwh * cycle_factor * temp_factor


def _simulate_daytime(
    *,
    start_energy_kwh: float,
    capacity_kwh: float,
    hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float],
    sunset_hour: int,
) -> tuple[float, float]:
    energy = max(0.0, min(capacity_kwh, start_energy_kwh))
    buy_kwh = 0.0
    sunset_energy_kwh = energy
    hours = sorted(set(hourly_load_kwh.keys()) | set(hourly_pv_kwh.keys()))
    for hour in hours:
        if hour < 7 or hour >= 23:
            continue
        load = max(0.0, hourly_load_kwh.get(hour, 0.0))
        pv = max(0.0, hourly_pv_kwh.get(hour, 0.0))
        net = load - pv
        if net >= 0:
            discharge = min(energy, net)
            energy -= discharge
            buy_kwh += max(0.0, net - discharge)
        else:
            charge = min(capacity_kwh - energy, -net)
            energy += charge
        if hour <= sunset_hour:
            sunset_energy_kwh = energy
    return buy_kwh, sunset_energy_kwh


def optimize_target_soc_for_daytime(
    *,
    effective_capacity_kwh_value: float,
    soc_now_percent: float,
    reserve_soc_percent: float,
    battery_round_trip_efficiency: float,
    hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float],
    sunset_hour: int,
    soc_step_percent: float = 1.0,
) -> DaytimeSocOptimizationResult | None:
    cap = max(0.0, effective_capacity_kwh_value)
    if cap <= 0:
        return None
    hours = [h for h in sorted(set(hourly_load_kwh.keys()) | set(hourly_pv_kwh.keys())) if 7 <= h < 23]
    if not hours:
        return None

    reserve_soc = max(0.0, min(100.0, reserve_soc_percent))
    soc_now = max(0.0, min(100.0, soc_now_percent))
    step = min(10.0, max(0.5, soc_step_percent))
    eta_ch = max(0.7, battery_round_trip_efficiency)

    best_target_soc = reserve_soc
    best_target_energy = cap * reserve_soc / 100.0
    best_buy = float("inf")
    best_sunset_energy = -1.0

    cursor = reserve_soc
    while cursor <= 100.0 + 1e-9:
        target_soc = min(100.0, cursor)
        start_energy = cap * target_soc / 100.0
        buy_kwh, sunset_energy = _simulate_daytime(
            start_energy_kwh=start_energy,
            capacity_kwh=cap,
            hourly_load_kwh=hourly_load_kwh,
            hourly_pv_kwh=hourly_pv_kwh,
            sunset_hour=sunset_hour,
        )
        better = False
        if buy_kwh < best_buy - 1e-9:
            better = True
        elif abs(buy_kwh - best_buy) <= 1e-9:
            if sunset_energy > best_sunset_energy + 1e-9:
                better = True
            elif abs(sunset_energy - best_sunset_energy) <= 1e-9 and start_energy < best_target_energy - 1e-9:
                better = True

        if better:
            best_target_soc = target_soc
            best_target_energy = start_energy
            best_buy = buy_kwh
            best_sunset_energy = sunset_energy
        cursor += step

    e_now = cap * soc_now / 100.0
    required_night_charge_kwh = max(0.0, (best_target_energy - e_now) / eta_ch)
    sunset_soc = 100.0 * best_sunset_energy / cap if cap > 0 else 0.0
    return DaytimeSocOptimizationResult(
        target_soc_7_percent=max(0.0, min(100.0, best_target_soc)),
        target_energy_kwh=best_target_energy,
        required_night_charge_kwh=required_night_charge_kwh,
        predicted_daytime_buy_kwh=max(0.0, best_buy),
        predicted_sunset_soc_percent=max(0.0, min(100.0, sunset_soc)),
        predicted_sunset_energy_kwh=max(0.0, best_sunset_energy),
    )


def compute_night_charge_target(
    coeff: EnergyModelCoefficients,
    inp: NightChargeInputs,
) -> NightChargeResult:
    cap_eff = effective_capacity_kwh(
        coeff=coeff,
        cycle_count=inp.cycle_count,
        battery_temp_c=inp.battery_temp_c,
    )
    e_now = cap_eff * inp.soc_now_percent / 100.0
    e_reserve = cap_eff * inp.reserve_soc_percent / 100.0

    if inp.predicted_pv_kwh_override is None:
        e_pv = forecast_pv_energy_kwh(
            sun_hours=inp.sun_hours_forecast,
            temp_c=inp.temp_forecast_c,
            coeff=coeff,
        )
    else:
        e_pv = max(0.0, inp.predicted_pv_kwh_override)

    if inp.predicted_morning_pv_kwh_override is None:
        e_morning_pv = max(0.0, e_pv * inp.morning_pv_ratio)
    else:
        e_morning_pv = max(0.0, inp.predicted_morning_pv_kwh_override)
    e_morning_def = max(0.0, inp.morning_load_forecast_kwh - e_morning_pv)
    e_daytime_def = max(0.0, inp.daytime_load_forecast_kwh - e_pv)
    if inp.predicted_midday_surplus_kwh_override is None:
        e_midday_surplus = max(0.0, e_pv * inp.midday_surplus_ratio)
    else:
        e_midday_surplus = max(0.0, inp.predicted_midday_surplus_kwh_override)

    # 7時時点の目標エネルギー:
    # - 下限: 早朝不足 + 予備, かつ日中の総不足分もカバー
    # - 上限: 昼の余剰PVを受けるヘッドルームを確保
    e_lower = min(
        cap_eff,
        max(
            e_reserve,
            e_morning_def + e_reserve,
            e_daytime_def + e_reserve,
        ),
    )
    e_upper = max(e_lower, cap_eff - e_midday_surplus)
    e_target = min(e_lower, e_upper) if e_lower <= e_upper else e_lower

    # 夜間に系統から充電する必要量（放電禁止前提）
    eta_ch = max(0.7, coeff.battery_round_trip_efficiency)
    e_night_charge = max(0.0, (e_target - e_now) / eta_ch)

    target_soc = max(0.0, min(100.0, 100.0 * e_target / cap_eff if cap_eff > 0 else 0.0))
    return NightChargeResult(
        predicted_pv_kwh=e_pv,
        predicted_morning_pv_kwh=e_morning_pv,
        predicted_morning_deficit_kwh=e_morning_def,
        predicted_daytime_deficit_kwh=e_daytime_def,
        predicted_midday_surplus_kwh=e_midday_surplus,
        effective_capacity_kwh=cap_eff,
        target_soc_7_percent=target_soc,
        required_night_charge_kwh=e_night_charge,
    )


def to_dict(
    obj: EnergyModelCoefficients | NightChargeInputs | NightChargeResult | DaytimeSocOptimizationResult,
) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for key, value in asdict(obj).items():
        out[key] = None if value is None else float(value)
    return out
