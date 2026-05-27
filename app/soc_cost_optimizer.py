from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PvForecastUncertainty:
    """PV forecast error model, expressed as multipliers around the base forecast."""

    mean_multiplier: float
    std_multiplier: float
    variance_multiplier: float
    sample_count: int
    source: str


@dataclass(frozen=True)
class SigmaBucket:
    """One representative probability bucket of a normal-like forecast error distribution."""

    label: str
    probability: float
    z_value: float


@dataclass(frozen=True)
class SocCostModel:
    """Prices used to compare grid charging, daytime grid import, and wasted PV headroom."""

    day_buy_rate_yen_per_kwh: float
    night_buy_rate_yen_per_kwh: float
    charge_efficiency: float
    sell_value_ratio: float
    day_buy_penalty_factor: float = 1.0

    @property
    def night_effective_rate_yen_per_kwh(self) -> float:
        return self.night_buy_rate_yen_per_kwh / max(0.01, self.charge_efficiency)

    @property
    def sell_opportunity_loss_yen_per_kwh(self) -> float:
        # Exported PV is not worthless, but it is less valuable than PV stored for later use.
        sell_credit = self.night_effective_rate_yen_per_kwh * max(0.0, min(1.0, self.sell_value_ratio))
        return max(0.0, self.night_effective_rate_yen_per_kwh - sell_credit)


@dataclass(frozen=True)
class ScenarioReplay:
    label: str
    probability: float
    pv_multiplier: float
    buy_kwh: float
    sell_kwh: float
    max_soc_percent: float
    first_full_hour: int | None
    end_soc_percent: float
    day_buy_cost_yen: float
    sell_opportunity_cost_yen: float


@dataclass(frozen=True)
class SocCandidate:
    target_soc_percent: float
    target_energy_kwh: float
    required_night_charge_kwh: float
    night_charge_cost_yen: float
    expected_day_buy_kwh: float
    expected_sell_kwh: float
    expected_day_buy_cost_yen: float
    expected_sell_opportunity_cost_yen: float
    total_expected_cost_yen: float
    scenario_replays: tuple[ScenarioReplay, ...]


@dataclass(frozen=True)
class SocCostOptimizationResult:
    target_soc_7_percent: float
    target_energy_kwh: float
    required_night_charge_kwh: float
    night_charge_cost_yen: float
    expected_day_buy_kwh: float
    expected_sell_kwh: float
    expected_day_buy_cost_yen: float
    expected_sell_opportunity_cost_yen: float
    total_expected_cost_yen: float
    selected_candidate: SocCandidate
    evaluated_candidate_count: int
    uncertainty: PvForecastUncertainty
    cost_model: SocCostModel
    sigma_buckets: tuple[SigmaBucket, ...]


DEFAULT_SIGMA_BUCKETS: tuple[SigmaBucket, ...] = (
    SigmaBucket("<-2sigma", 0.0228, -2.0),
    SigmaBucket("-2_to_-1sigma", 0.1359, -1.5),
    SigmaBucket("-1_to_0sigma", 0.3413, -0.5),
    SigmaBucket("0_to_+1sigma", 0.3413, 0.5),
    SigmaBucket("+1_to_+2sigma", 0.1359, 1.5),
    SigmaBucket(">+2sigma", 0.0228, 2.0),
)


def to_plain_dict(obj) -> dict:
    """Dataclass-to-dict helper kept here so payload creation stays readable."""

    return asdict(obj)


def _bounded_soc(value: float) -> float:
    return max(0.0, min(100.0, value))


def _pv_multiplier_for_bucket(
    *,
    uncertainty: PvForecastUncertainty,
    bucket: SigmaBucket,
    min_multiplier: float,
    max_multiplier: float,
) -> float:
    raw = uncertainty.mean_multiplier + bucket.z_value * uncertainty.std_multiplier
    return max(min_multiplier, min(max_multiplier, raw))


def _simulate_day(
    *,
    start_energy_kwh: float,
    capacity_kwh: float,
    hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float],
    pv_multiplier: float,
) -> tuple[float, float, float, int | None, float]:
    """Replay 07:00-23:00 for one PV scenario and one starting SOC."""

    energy = max(0.0, min(capacity_kwh, start_energy_kwh))
    buy_kwh = 0.0
    sell_kwh = 0.0
    max_energy = energy
    first_full_hour: int | None = None

    for hour in range(7, 23):
        load = max(0.0, hourly_load_kwh.get(hour, 0.0))
        pv = max(0.0, hourly_pv_kwh.get(hour, 0.0)) * max(0.0, pv_multiplier)
        net = pv - load
        if net >= 0:
            charge = min(capacity_kwh - energy, net)
            energy += charge
            sell_kwh += max(0.0, net - charge)
        else:
            need = -net
            discharge = min(energy, need)
            energy -= discharge
            buy_kwh += max(0.0, need - discharge)

        max_energy = max(max_energy, energy)
        if first_full_hour is None and capacity_kwh > 0 and energy >= capacity_kwh * 0.999:
            first_full_hour = hour

    max_soc = (100.0 * max_energy / capacity_kwh) if capacity_kwh > 0 else 0.0
    end_soc = (100.0 * energy / capacity_kwh) if capacity_kwh > 0 else 0.0
    return buy_kwh, sell_kwh, _bounded_soc(max_soc), first_full_hour, _bounded_soc(end_soc)


def evaluate_soc_candidate(
    *,
    target_soc_percent: float,
    soc_now_percent: float,
    capacity_kwh: float,
    hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float],
    uncertainty: PvForecastUncertainty,
    cost_model: SocCostModel,
    sigma_buckets: tuple[SigmaBucket, ...] = DEFAULT_SIGMA_BUCKETS,
    min_pv_multiplier: float = 0.0,
    max_pv_multiplier: float = 3.0,
) -> SocCandidate:
    """Evaluate one SOC target across all sigma buckets."""

    target_soc = _bounded_soc(target_soc_percent)
    target_energy = capacity_kwh * target_soc / 100.0
    current_energy = capacity_kwh * _bounded_soc(soc_now_percent) / 100.0
    charge_efficiency = max(0.01, cost_model.charge_efficiency)
    required_night_charge_kwh = max(0.0, (target_energy - current_energy) / charge_efficiency)
    night_cost = required_night_charge_kwh * cost_model.night_buy_rate_yen_per_kwh

    expected_buy = 0.0
    expected_sell = 0.0
    expected_buy_cost = 0.0
    expected_sell_cost = 0.0
    replays: list[ScenarioReplay] = []

    for bucket in sigma_buckets:
        probability = max(0.0, bucket.probability)
        multiplier = _pv_multiplier_for_bucket(
            uncertainty=uncertainty,
            bucket=bucket,
            min_multiplier=min_pv_multiplier,
            max_multiplier=max_pv_multiplier,
        )
        buy_kwh, sell_kwh, max_soc, first_full, end_soc = _simulate_day(
            start_energy_kwh=target_energy,
            capacity_kwh=capacity_kwh,
            hourly_load_kwh=hourly_load_kwh,
            hourly_pv_kwh=hourly_pv_kwh,
            pv_multiplier=multiplier,
        )
        day_buy_cost = buy_kwh * cost_model.day_buy_rate_yen_per_kwh * cost_model.day_buy_penalty_factor
        sell_cost = sell_kwh * cost_model.sell_opportunity_loss_yen_per_kwh
        expected_buy += probability * buy_kwh
        expected_sell += probability * sell_kwh
        expected_buy_cost += probability * day_buy_cost
        expected_sell_cost += probability * sell_cost
        replays.append(
            ScenarioReplay(
                label=bucket.label,
                probability=probability,
                pv_multiplier=multiplier,
                buy_kwh=buy_kwh,
                sell_kwh=sell_kwh,
                max_soc_percent=max_soc,
                first_full_hour=first_full,
                end_soc_percent=end_soc,
                day_buy_cost_yen=day_buy_cost,
                sell_opportunity_cost_yen=sell_cost,
            )
        )

    total = night_cost + expected_buy_cost + expected_sell_cost
    return SocCandidate(
        target_soc_percent=target_soc,
        target_energy_kwh=target_energy,
        required_night_charge_kwh=required_night_charge_kwh,
        night_charge_cost_yen=night_cost,
        expected_day_buy_kwh=expected_buy,
        expected_sell_kwh=expected_sell,
        expected_day_buy_cost_yen=expected_buy_cost,
        expected_sell_opportunity_cost_yen=expected_sell_cost,
        total_expected_cost_yen=total,
        scenario_replays=tuple(replays),
    )


def optimize_soc_by_expected_cost(
    *,
    capacity_kwh: float,
    soc_now_percent: float,
    reserve_soc_percent: float,
    hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float],
    uncertainty: PvForecastUncertainty,
    cost_model: SocCostModel,
    soc_step_percent: float = 1.0,
    max_target_soc_percent: float = 100.0,
    sigma_buckets: tuple[SigmaBucket, ...] = DEFAULT_SIGMA_BUCKETS,
    min_pv_multiplier: float = 0.0,
    max_pv_multiplier: float = 3.0,
) -> SocCostOptimizationResult | None:
    """Choose the SOC with the lowest expected monetary cost."""

    if capacity_kwh <= 0:
        return None
    if not hourly_load_kwh and not hourly_pv_kwh:
        return None

    start_soc = _bounded_soc(reserve_soc_percent)
    stop_soc = max(start_soc, _bounded_soc(max_target_soc_percent))
    step = max(0.1, min(10.0, soc_step_percent))

    best: SocCandidate | None = None
    count = 0
    cursor = start_soc
    while cursor <= stop_soc + 1e-9:
        candidate = evaluate_soc_candidate(
            target_soc_percent=min(cursor, stop_soc),
            soc_now_percent=soc_now_percent,
            capacity_kwh=capacity_kwh,
            hourly_load_kwh=hourly_load_kwh,
            hourly_pv_kwh=hourly_pv_kwh,
            uncertainty=uncertainty,
            cost_model=cost_model,
            sigma_buckets=sigma_buckets,
            min_pv_multiplier=min_pv_multiplier,
            max_pv_multiplier=max_pv_multiplier,
        )
        count += 1
        if best is None or (
            candidate.total_expected_cost_yen,
            candidate.expected_day_buy_kwh,
            candidate.expected_sell_kwh,
            candidate.target_soc_percent,
        ) < (
            best.total_expected_cost_yen,
            best.expected_day_buy_kwh,
            best.expected_sell_kwh,
            best.target_soc_percent,
        ):
            best = candidate
        cursor += step

    if best is None:
        return None
    return SocCostOptimizationResult(
        target_soc_7_percent=best.target_soc_percent,
        target_energy_kwh=best.target_energy_kwh,
        required_night_charge_kwh=best.required_night_charge_kwh,
        night_charge_cost_yen=best.night_charge_cost_yen,
        expected_day_buy_kwh=best.expected_day_buy_kwh,
        expected_sell_kwh=best.expected_sell_kwh,
        expected_day_buy_cost_yen=best.expected_day_buy_cost_yen,
        expected_sell_opportunity_cost_yen=best.expected_sell_opportunity_cost_yen,
        total_expected_cost_yen=best.total_expected_cost_yen,
        selected_candidate=best,
        evaluated_candidate_count=count,
        uncertainty=uncertainty,
        cost_model=cost_model,
        sigma_buckets=sigma_buckets,
    )
