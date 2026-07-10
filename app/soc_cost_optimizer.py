from __future__ import annotations

from dataclasses import asdict, dataclass

from app.constants import SOCBounds


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
class ForecastScenario:
    """Readable forecast scenario for joint PV/load evaluation."""

    label: str
    probability: float
    pv_multiplier: float
    load_multiplier: float


@dataclass(frozen=True)
class SocCostModel:
    """Prices used to compare grid charging, daytime grid import, and wasted PV headroom."""

    day_buy_rate_yen_per_kwh: float
    night_buy_rate_yen_per_kwh: float
    charge_efficiency: float
    sell_value_ratio: float
    day_buy_penalty_factor: float = 1.0
    sell_opportunity_loss_yen_per_kwh_override: float | None = None
    export_value_mode: str = "opportunity"
    sell_revenue_yen_per_kwh: float = 0.0
    tariff_mode: str = "flat"
    monthly_day_buy_kwh_before_target: float = 0.0
    day_tier1_upper_kwh: float = 90.0
    day_tier2_upper_kwh: float = 230.0
    day_tier1_rate_yen_per_kwh: float = 31.80
    day_tier2_rate_yen_per_kwh: float = 39.10
    day_tier3_rate_yen_per_kwh: float = 43.62
    monthly_tier_landing_enabled: bool = False
    expected_rest_of_month_day_buy_kwh: float = 0.0
    tier1_underuse_penalty_yen_per_kwh: float = 0.0
    tier1_crossing_penalty_yen_per_kwh: float = 30.0
    tier2_extra_penalty_yen_per_kwh: float = 8.0
    tier3_extra_penalty_yen_per_kwh: float = 20.0

    @property
    def night_effective_rate_yen_per_kwh(self) -> float:
        return self.night_buy_rate_yen_per_kwh / max(0.01, self.charge_efficiency)

    @property
    def sell_opportunity_loss_yen_per_kwh(self) -> float:
        mode = (self.export_value_mode or "opportunity").strip().lower()
        if mode == "neutral":
            return 0.0
        if mode == "revenue":
            return -max(0.0, self.sell_revenue_yen_per_kwh)
        if mode == "penalty":
            if self.sell_opportunity_loss_yen_per_kwh_override is not None:
                return max(0.0, self.sell_opportunity_loss_yen_per_kwh_override)
            return max(0.0, self.day_buy_rate_yen_per_kwh)

        # Opportunity mode: exported PV is not worthless, but it is less valuable
        # than PV stored for later use.
        if self.sell_opportunity_loss_yen_per_kwh_override is not None:
            return max(0.0, self.sell_opportunity_loss_yen_per_kwh_override)
        sell_credit = self.night_effective_rate_yen_per_kwh * max(0.0, min(1.0, self.sell_value_ratio))
        return max(0.0, self.night_effective_rate_yen_per_kwh - sell_credit)

    def day_buy_cost_yen(self, buy_kwh: float) -> float:
        buy = max(0.0, buy_kwh)
        if (self.tariff_mode or "flat").strip().lower() != "night8_tiered":
            return buy * self.day_buy_rate_yen_per_kwh * self.day_buy_penalty_factor
        return _tiered_day_increment_cost(
            previous_kwh=self.monthly_day_buy_kwh_before_target,
            delta_kwh=buy,
            tier1_upper_kwh=self.day_tier1_upper_kwh,
            tier2_upper_kwh=self.day_tier2_upper_kwh,
            rate_tier1_yen=self.day_tier1_rate_yen_per_kwh,
            rate_tier2_yen=self.day_tier2_rate_yen_per_kwh,
            rate_tier3_yen=self.day_tier3_rate_yen_per_kwh,
        ) * self.day_buy_penalty_factor

    def monthly_tier_landing_penalty_yen(self, candidate_day_buy_kwh: float) -> float:
        if not self.monthly_tier_landing_enabled:
            return 0.0
        base_kwh = max(0.0, self.monthly_day_buy_kwh_before_target)
        rest_kwh = max(0.0, self.expected_rest_of_month_day_buy_kwh)
        candidate_kwh = max(0.0, candidate_day_buy_kwh)
        projected_before_candidate = base_kwh + rest_kwh
        projected_total = projected_before_candidate + candidate_kwh
        t1 = max(0.0, self.day_tier1_upper_kwh)
        t2 = max(t1, self.day_tier2_upper_kwh)

        penalty = 0.0
        if projected_before_candidate <= t1:
            underuse = max(0.0, t1 - projected_total)
            crossing = max(0.0, projected_total - t1)
            penalty += underuse * max(0.0, self.tier1_underuse_penalty_yen_per_kwh)
            penalty += crossing * max(0.0, self.tier1_crossing_penalty_yen_per_kwh)
        elif projected_before_candidate <= t2:
            tier2_extra = max(0.0, min(projected_total, t2) - projected_before_candidate)
            tier3_crossing = max(0.0, projected_total - t2)
            penalty += tier2_extra * max(0.0, self.tier2_extra_penalty_yen_per_kwh)
            penalty += tier3_crossing * max(0.0, self.tier3_extra_penalty_yen_per_kwh)
        else:
            penalty += candidate_kwh * max(0.0, self.tier3_extra_penalty_yen_per_kwh)
        return penalty


def _tiered_day_cost(
    day_kwh: float,
    *,
    tier1_upper_kwh: float,
    tier2_upper_kwh: float,
    rate_tier1_yen: float,
    rate_tier2_yen: float,
    rate_tier3_yen: float,
) -> float:
    kwh = max(0.0, float(day_kwh))
    t1 = max(0.0, float(tier1_upper_kwh))
    t2 = max(t1, float(tier2_upper_kwh))
    b1 = min(kwh, t1)
    b2 = min(max(kwh - t1, 0.0), t2 - t1)
    b3 = max(kwh - t2, 0.0)
    return b1 * rate_tier1_yen + b2 * rate_tier2_yen + b3 * rate_tier3_yen


def _tiered_day_increment_cost(
    *,
    previous_kwh: float,
    delta_kwh: float,
    tier1_upper_kwh: float,
    tier2_upper_kwh: float,
    rate_tier1_yen: float,
    rate_tier2_yen: float,
    rate_tier3_yen: float,
) -> float:
    prev = max(0.0, float(previous_kwh))
    delta = max(0.0, float(delta_kwh))
    return _tiered_day_cost(
        prev + delta,
        tier1_upper_kwh=tier1_upper_kwh,
        tier2_upper_kwh=tier2_upper_kwh,
        rate_tier1_yen=rate_tier1_yen,
        rate_tier2_yen=rate_tier2_yen,
        rate_tier3_yen=rate_tier3_yen,
    ) - _tiered_day_cost(
        prev,
        tier1_upper_kwh=tier1_upper_kwh,
        tier2_upper_kwh=tier2_upper_kwh,
        rate_tier1_yen=rate_tier1_yen,
        rate_tier2_yen=rate_tier2_yen,
        rate_tier3_yen=rate_tier3_yen,
    )


@dataclass(frozen=True)
class ScenarioReplay:
    label: str
    probability: float
    pv_multiplier: float
    load_multiplier: float
    buy_kwh: float
    sell_kwh: float
    max_soc_percent: float
    first_full_hour: int | None
    end_soc_percent: float
    day_buy_cost_yen: float
    sell_opportunity_cost_yen: float
    peak_unmet_kwh: float = 0.0
    peak_unmet_cost_yen: float = 0.0


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
    expected_peak_unmet_kwh: float
    expected_peak_unmet_cost_yen: float
    expected_monthly_tier_landing_penalty_yen: float
    decision_prior_cost_yen: float
    total_expected_cost_yen: float
    scenario_replays: tuple[ScenarioReplay, ...]


@dataclass(frozen=True)
class SocCandidateSummary:
    target_soc_percent: float
    total_expected_cost_yen: float
    required_night_charge_kwh: float
    expected_day_buy_kwh: float
    expected_sell_kwh: float
    expected_peak_unmet_kwh: float
    expected_monthly_tier_landing_penalty_yen: float
    decision_prior_cost_yen: float
    rejection_reason: str


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
    expected_peak_unmet_kwh: float
    expected_peak_unmet_cost_yen: float
    expected_monthly_tier_landing_penalty_yen: float
    decision_prior_cost_yen: float
    expected_day_buy_kwh_risk: float
    expected_sell_kwh_risk: float
    worst_case_day_buy_kwh: float
    worst_case_sell_kwh: float
    buy_risk: bool
    sell_risk: bool
    total_expected_cost_yen: float
    selected_candidate: SocCandidate
    candidate_summaries: tuple[SocCandidateSummary, ...]
    evaluated_candidate_count: int
    uncertainty: PvForecastUncertainty
    cost_model: SocCostModel
    sigma_buckets: tuple[SigmaBucket, ...]
    forecast_scenarios: tuple[ForecastScenario, ...]


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
    return SOCBounds.clamp(value)


def _pv_multiplier_for_bucket(
    *,
    uncertainty: PvForecastUncertainty,
    bucket: SigmaBucket,
    min_multiplier: float,
    max_multiplier: float,
) -> float:
    raw = uncertainty.mean_multiplier + bucket.z_value * uncertainty.std_multiplier
    return max(min_multiplier, min(max_multiplier, raw))


def _build_forecast_scenarios(
    *,
    uncertainty: PvForecastUncertainty,
    sigma_buckets: tuple[SigmaBucket, ...],
    load_scenarios: tuple[ForecastScenario, ...] | None,
    min_pv_multiplier: float,
    max_pv_multiplier: float,
    weather_upside_probability: float,
    weather_upside_z: float,
) -> tuple[ForecastScenario, ...]:
    pv_scenarios: list[ForecastScenario] = []
    upside_probability = max(0.0, min(1.0, weather_upside_probability))
    if sigma_buckets:
        for bucket in sigma_buckets:
            base_probability = max(0.0, bucket.probability) * (1.0 - upside_probability)
            pv_scenarios.append(
                ForecastScenario(
                    label=bucket.label,
                    probability=base_probability,
                    pv_multiplier=_pv_multiplier_for_bucket(
                        uncertainty=uncertainty,
                        bucket=bucket,
                        min_multiplier=min_pv_multiplier,
                        max_multiplier=max_pv_multiplier,
                    ),
                    load_multiplier=1.0,
                )
            )
            if upside_probability > 0.0:
                upside_multiplier = max(
                    min_pv_multiplier,
                    min(max_pv_multiplier, uncertainty.mean_multiplier + weather_upside_z * uncertainty.std_multiplier),
                )
                pv_scenarios.append(
                    ForecastScenario(
                        label=f"{bucket.label}+upside",
                        probability=max(0.0, bucket.probability) * upside_probability,
                        pv_multiplier=upside_multiplier,
                        load_multiplier=1.0,
                    )
                )
    else:
        pv_scenarios.append(
            ForecastScenario(
                label="pv_base",
                probability=1.0,
                pv_multiplier=max(min_pv_multiplier, min(max_pv_multiplier, uncertainty.mean_multiplier)),
                load_multiplier=1.0,
            )
        )

    load_scenarios = load_scenarios or (
        ForecastScenario("load_mid", 1.0, 1.0, 1.0),
    )
    joint: list[ForecastScenario] = []
    for pv_scenario in pv_scenarios:
        for load_scenario in load_scenarios:
            joint.append(
                ForecastScenario(
                    label=f"{pv_scenario.label} x {load_scenario.label}",
                    probability=max(0.0, pv_scenario.probability) * max(0.0, load_scenario.probability),
                    pv_multiplier=pv_scenario.pv_multiplier,
                    load_multiplier=load_scenario.load_multiplier,
                )
            )
    total_probability = sum(s.probability for s in joint)
    if total_probability <= 0.0:
        return (ForecastScenario("fallback", 1.0, 1.0, 1.0),)
    return tuple(
        ForecastScenario(
            label=scenario.label,
            probability=scenario.probability / total_probability,
            pv_multiplier=scenario.pv_multiplier,
            load_multiplier=scenario.load_multiplier,
        )
        for scenario in joint
    )


def _simulate_day(
    *,
    start_energy_kwh: float,
    capacity_kwh: float,
    hourly_load_kwh: dict[int, float],
    hourly_pv_kwh: dict[int, float],
    pv_multiplier: float,
    load_multiplier: float,
) -> tuple[float, float, float, int | None, float]:
    """Replay 07:00-23:00 for one PV scenario and one starting SOC."""

    energy = max(0.0, min(capacity_kwh, start_energy_kwh))
    buy_kwh = 0.0
    sell_kwh = 0.0
    max_energy = energy
    first_full_hour: int | None = None

    for hour in range(7, 23):
        load = max(0.0, hourly_load_kwh.get(hour, 0.0)) * max(0.0, load_multiplier)
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


def _decision_prior_cost_yen(
    *,
    target_soc_percent: float,
    regret_yen_by_soc: dict[float | str, float] | None,
    weight: float,
    max_penalty_yen: float,
) -> float:
    if not regret_yen_by_soc or weight <= 0.0 or max_penalty_yen <= 0.0:
        return 0.0
    points: list[tuple[float, float]] = []
    for key, value in regret_yen_by_soc.items():
        try:
            target = float(key)
            regret = float(value)
        except (TypeError, ValueError):
            continue
        if 0.0 <= target <= 100.0 and regret >= 0.0:
            points.append((target, regret))
    if not points:
        return 0.0
    points.sort(key=lambda item: item[0])
    target_soc = _bounded_soc(target_soc_percent)
    if target_soc <= points[0][0]:
        regret = points[0][1]
    elif target_soc >= points[-1][0]:
        regret = points[-1][1]
    else:
        regret = 0.0
        for (left_soc, left_regret), (right_soc, right_regret) in zip(points, points[1:]):
            if left_soc <= target_soc <= right_soc:
                if abs(right_soc - left_soc) < 1e-9:
                    regret = min(left_regret, right_regret)
                else:
                    ratio = (target_soc - left_soc) / (right_soc - left_soc)
                    regret = left_regret * (1.0 - ratio) + right_regret * ratio
                break
    return max(0.0, min(max_penalty_yen, regret * weight))


def _candidate_summary(*, candidate: SocCandidate, best: SocCandidate) -> SocCandidateSummary:
    if abs(candidate.target_soc_percent - best.target_soc_percent) < 1e-9:
        reason = "selected"
    elif candidate.expected_day_buy_kwh > best.expected_day_buy_kwh + 0.05:
        reason = "higher_day_buy_risk"
    elif candidate.expected_sell_kwh > best.expected_sell_kwh + 0.10:
        reason = "higher_sell_loss"
    elif candidate.expected_peak_unmet_kwh > best.expected_peak_unmet_kwh + 0.05:
        reason = "higher_peak_unmet_risk"
    elif (
        candidate.expected_monthly_tier_landing_penalty_yen
        > best.expected_monthly_tier_landing_penalty_yen + 1.0
    ):
        reason = "higher_monthly_tier_risk"
    elif candidate.required_night_charge_kwh > best.required_night_charge_kwh + 0.05:
        reason = "higher_night_charge"
    else:
        reason = "higher_total_cost"
    return SocCandidateSummary(
        target_soc_percent=candidate.target_soc_percent,
        total_expected_cost_yen=candidate.total_expected_cost_yen,
        required_night_charge_kwh=candidate.required_night_charge_kwh,
        expected_day_buy_kwh=candidate.expected_day_buy_kwh,
        expected_sell_kwh=candidate.expected_sell_kwh,
        expected_peak_unmet_kwh=candidate.expected_peak_unmet_kwh,
        expected_monthly_tier_landing_penalty_yen=candidate.expected_monthly_tier_landing_penalty_yen,
        decision_prior_cost_yen=candidate.decision_prior_cost_yen,
        rejection_reason=reason,
    )


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
    load_scenarios: tuple[ForecastScenario, ...] | None = None,
    weather_upside_probability: float = 0.0,
    weather_upside_z: float = 3.5,
    peak_soc_target_percent: float | None = None,
    peak_soc_unmet_penalty_yen_per_kwh: float = 0.0,
    peak_soc_unmet_penalty_factor: float = 1.0,
    expected_overnight_discharge_kwh: float = 0.0,
    decision_prior_regret_yen_by_soc: dict[float | str, float] | None = None,
    decision_prior_weight: float = 0.0,
    decision_prior_max_penalty_yen: float = 0.0,
) -> SocCandidate:
    """Evaluate one SOC target across all sigma buckets."""

    target_soc = _bounded_soc(target_soc_percent)
    target_energy = capacity_kwh * target_soc / 100.0
    current_energy = capacity_kwh * _bounded_soc(soc_now_percent) / 100.0
    projected_morning_energy = max(0.0, current_energy - max(0.0, expected_overnight_discharge_kwh))
    charge_efficiency = max(0.01, cost_model.charge_efficiency)
    required_night_charge_kwh = max(0.0, (target_energy - projected_morning_energy) / charge_efficiency)
    night_cost = required_night_charge_kwh * cost_model.night_buy_rate_yen_per_kwh

    expected_buy = 0.0
    expected_sell = 0.0
    expected_buy_cost = 0.0
    expected_sell_cost = 0.0
    expected_peak_unmet = 0.0
    expected_peak_unmet_cost = 0.0
    replays: list[ScenarioReplay] = []
    peak_target = _bounded_soc(peak_soc_target_percent) if peak_soc_target_percent is not None else None
    peak_penalty = max(0.0, peak_soc_unmet_penalty_yen_per_kwh)

    scenarios = _build_forecast_scenarios(
        uncertainty=uncertainty,
        sigma_buckets=sigma_buckets,
        load_scenarios=load_scenarios,
        min_pv_multiplier=min_pv_multiplier,
        max_pv_multiplier=max_pv_multiplier,
        weather_upside_probability=weather_upside_probability,
        weather_upside_z=weather_upside_z,
    )

    for scenario in scenarios:
        probability = max(0.0, scenario.probability)
        buy_kwh, sell_kwh, max_soc, first_full, end_soc = _simulate_day(
            start_energy_kwh=target_energy,
            capacity_kwh=capacity_kwh,
            hourly_load_kwh=hourly_load_kwh,
            hourly_pv_kwh=hourly_pv_kwh,
            pv_multiplier=scenario.pv_multiplier,
            load_multiplier=scenario.load_multiplier,
        )
        day_buy_cost = cost_model.day_buy_cost_yen(buy_kwh)
        sell_cost = sell_kwh * cost_model.sell_opportunity_loss_yen_per_kwh
        peak_unmet_kwh = 0.0
        peak_unmet_cost = 0.0
        if peak_target is not None and peak_penalty > 0.0:
            peak_unmet_kwh = max(0.0, peak_target - max_soc) * capacity_kwh / 100.0
            peak_unmet_cost = peak_unmet_kwh * peak_penalty * max(0.0, peak_soc_unmet_penalty_factor)
        expected_buy += probability * buy_kwh
        expected_sell += probability * sell_kwh
        expected_buy_cost += probability * day_buy_cost
        expected_sell_cost += probability * sell_cost
        expected_peak_unmet += probability * peak_unmet_kwh
        expected_peak_unmet_cost += probability * peak_unmet_cost
        replays.append(
            ScenarioReplay(
                label=scenario.label,
                probability=probability,
                pv_multiplier=scenario.pv_multiplier,
                load_multiplier=scenario.load_multiplier,
                buy_kwh=buy_kwh,
                sell_kwh=sell_kwh,
                max_soc_percent=max_soc,
                first_full_hour=first_full,
                end_soc_percent=end_soc,
                day_buy_cost_yen=day_buy_cost,
                sell_opportunity_cost_yen=sell_cost,
                peak_unmet_kwh=peak_unmet_kwh,
                peak_unmet_cost_yen=peak_unmet_cost,
            )
        )

    monthly_tier_landing_penalty = cost_model.monthly_tier_landing_penalty_yen(expected_buy)
    decision_prior_cost = _decision_prior_cost_yen(
        target_soc_percent=target_soc,
        regret_yen_by_soc=decision_prior_regret_yen_by_soc,
        weight=max(0.0, decision_prior_weight),
        max_penalty_yen=max(0.0, decision_prior_max_penalty_yen),
    )
    total = (
        night_cost
        + expected_buy_cost
        + expected_sell_cost
        + expected_peak_unmet_cost
        + monthly_tier_landing_penalty
        + decision_prior_cost
    )
    return SocCandidate(
        target_soc_percent=target_soc,
        target_energy_kwh=target_energy,
        required_night_charge_kwh=required_night_charge_kwh,
        night_charge_cost_yen=night_cost,
        expected_day_buy_kwh=expected_buy,
        expected_sell_kwh=expected_sell,
        expected_day_buy_cost_yen=expected_buy_cost,
        expected_sell_opportunity_cost_yen=expected_sell_cost,
        expected_peak_unmet_kwh=expected_peak_unmet,
        expected_peak_unmet_cost_yen=expected_peak_unmet_cost,
        expected_monthly_tier_landing_penalty_yen=monthly_tier_landing_penalty,
        decision_prior_cost_yen=decision_prior_cost,
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
    load_scenarios: tuple[ForecastScenario, ...] | None = None,
    weather_upside_probability: float = 0.0,
    weather_upside_z: float = 3.5,
    peak_soc_target_percent: float | None = None,
    peak_soc_unmet_penalty_yen_per_kwh: float = 0.0,
    peak_soc_unmet_penalty_factor: float = 1.0,
    expected_overnight_discharge_kwh: float = 0.0,
    decision_prior_regret_yen_by_soc: dict[float | str, float] | None = None,
    decision_prior_weight: float = 0.0,
    decision_prior_max_penalty_yen: float = 0.0,
) -> SocCostOptimizationResult | None:
    """Choose the SOC with the lowest expected monetary cost."""

    if capacity_kwh <= 0:
        return None
    if not hourly_load_kwh and not hourly_pv_kwh:
        return None

    start_soc = _bounded_soc(reserve_soc_percent)
    stop_soc = max(start_soc, _bounded_soc(max_target_soc_percent))
    step = max(0.1, min(10.0, soc_step_percent))
    scenarios = _build_forecast_scenarios(
        uncertainty=uncertainty,
        sigma_buckets=sigma_buckets,
        load_scenarios=load_scenarios,
        min_pv_multiplier=min_pv_multiplier,
        max_pv_multiplier=max_pv_multiplier,
        weather_upside_probability=weather_upside_probability,
        weather_upside_z=weather_upside_z,
    )

    best: SocCandidate | None = None
    candidates: list[SocCandidate] = []
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
            load_scenarios=load_scenarios,
            weather_upside_probability=weather_upside_probability,
            weather_upside_z=weather_upside_z,
            peak_soc_target_percent=peak_soc_target_percent,
            peak_soc_unmet_penalty_yen_per_kwh=peak_soc_unmet_penalty_yen_per_kwh,
            peak_soc_unmet_penalty_factor=peak_soc_unmet_penalty_factor,
            expected_overnight_discharge_kwh=expected_overnight_discharge_kwh,
            decision_prior_regret_yen_by_soc=decision_prior_regret_yen_by_soc,
            decision_prior_weight=decision_prior_weight,
            decision_prior_max_penalty_yen=decision_prior_max_penalty_yen,
        )
        candidates.append(candidate)
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
    worst_case_day_buy = max((r.buy_kwh for r in best.scenario_replays), default=0.0)
    worst_case_sell = max((r.sell_kwh for r in best.scenario_replays), default=0.0)
    buy_risk = best.expected_day_buy_kwh > 0.3 or worst_case_day_buy > 1.0
    sell_risk = best.expected_sell_kwh > 0.3 or worst_case_sell > 2.0
    sorted_candidates = sorted(
        candidates,
        key=lambda candidate: (
            candidate.total_expected_cost_yen,
            candidate.expected_day_buy_kwh,
            candidate.expected_sell_kwh,
            candidate.target_soc_percent,
        ),
    )
    summaries: list[SocCandidateSummary] = []
    for candidate in sorted_candidates:
        summaries.append(_candidate_summary(candidate=candidate, best=best))
        if len(summaries) >= 5:
            break
    if all(abs(summary.target_soc_percent - best.target_soc_percent) > 1e-9 for summary in summaries):
        summaries.insert(0, _candidate_summary(candidate=best, best=best))
        summaries = summaries[:5]
    return SocCostOptimizationResult(
        target_soc_7_percent=best.target_soc_percent,
        target_energy_kwh=best.target_energy_kwh,
        required_night_charge_kwh=best.required_night_charge_kwh,
        night_charge_cost_yen=best.night_charge_cost_yen,
        expected_day_buy_kwh=best.expected_day_buy_kwh,
        expected_sell_kwh=best.expected_sell_kwh,
        expected_day_buy_cost_yen=best.expected_day_buy_cost_yen,
        expected_sell_opportunity_cost_yen=best.expected_sell_opportunity_cost_yen,
        expected_peak_unmet_kwh=best.expected_peak_unmet_kwh,
        expected_peak_unmet_cost_yen=best.expected_peak_unmet_cost_yen,
        expected_monthly_tier_landing_penalty_yen=best.expected_monthly_tier_landing_penalty_yen,
        decision_prior_cost_yen=best.decision_prior_cost_yen,
        expected_day_buy_kwh_risk=best.expected_day_buy_kwh,
        expected_sell_kwh_risk=best.expected_sell_kwh,
        worst_case_day_buy_kwh=worst_case_day_buy,
        worst_case_sell_kwh=worst_case_sell,
        buy_risk=buy_risk,
        sell_risk=sell_risk,
        total_expected_cost_yen=best.total_expected_cost_yen,
        selected_candidate=best,
        candidate_summaries=tuple(summaries),
        evaluated_candidate_count=count,
        uncertainty=uncertainty,
        cost_model=cost_model,
        sigma_buckets=sigma_buckets,
        forecast_scenarios=scenarios,
    )
