"""PV-only training, separate from complete battery/financial replay evidence."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.energy_plan.hourly_pv_bias import (
    LOOKBACK_DAYS, MIN_BIN_DAYS, MIN_VALIDATION_DAYS, PRIOR_PSEUDO_DAYS,
    PvBiasDay, PvBiasModel, ReplayErrors, _actual_availability, _apply, _number, _stamp,
    forecast_weather_by_hour,
)
from app.forecasting.pv_clear_ghi import clear_ghi_hour, normalize_radiation_intervals
from app.forecasting.pv_ideal_max import IdealMaxGeometry, ideal_max_base

MODEL_ID = "reforecast_hour_weather_bias_v2"


def coefficient_at(history: list[tuple[datetime, float]], stamp: datetime) -> float | None:
    values = [(t, c) for t, c in history if t <= stamp]
    # A batch may share a receipt timestamp; native capability only increases.
    return max(values, key=lambda x: (x[0], x[1]))[1] if values else None


@dataclass(frozen=True)
class PvTrainingDay:
    day: date
    issued_at: datetime
    basis: dict[int, float]
    radiation_factor: dict[int, float]
    weather: dict[int, str]
    actual: dict[int, float]
    available: dict[int, datetime]
    source_sha256: str


def reforecast_origin(plan: dict[str, Any], *, geometry: IdealMaxGeometry) -> tuple[dict[int, float], dict[int, float], dict[int, str]]:
    """Reuse saved weather only; date-specific solar geometry is not a residual."""
    forecast = plan.get("forecast", {})
    day = date.fromisoformat(str(forecast["date"]))
    weather = forecast_weather_by_hour(forecast)
    old_rows = {r.get("hour"): r for r in forecast.get("hourly_weather", []) if isinstance(r, dict)}
    raw = forecast.get("pv_radiation_rows")
    radiation = normalize_radiation_intervals(tuple(raw), target_date=day, timezone=geometry.timezone_name)[0] if isinstance(raw, list) else (None,) * 24
    basis, factors = {}, {}
    for hour in range(24):
        basis[hour] = ideal_max_base(day, hour, geometry)
        clear = clear_ghi_hour(day, hour, latitude=geometry.latitude, longitude=geometry.longitude, timezone=geometry.timezone_name)
        if clear == 0:
            factors[hour] = 0.
            continue
        value = radiation[hour]
        if value is None:
            legacy = _number(old_rows.get(hour + 1, {}).get("shortwave_radiation_w_m2"))
            value = legacy / 1000 if legacy is not None else None
        if value is not None and math.isfinite(value) and value >= 0:
            factors[hour] = min(value / clear, 1.)
    return basis, factors, weather


def collect_training_days(origins: list[dict[str, Any]], actual_rows: list[dict[str, Any]], *,
                          geometry: IdealMaxGeometry, target_date: str, as_of: datetime) -> tuple[tuple[PvTrainingDay, ...], dict[str, int]]:
    if as_of.utcoffset() is None:
        raise ValueError("PV training requires aware as_of")
    zone, target = ZoneInfo(geometry.timezone_name), date.fromisoformat(target_date)
    slots: dict[tuple[date, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in actual_rows:
        dt = row.get("dt")
        if not isinstance(dt, datetime):
            continue
        local = dt.replace(tzinfo=zone) if dt.utcoffset() is None else dt.astimezone(zone)
        if local.minute in (0, 30) and not local.second and not local.microsecond:
            slots[(local.date(), local.hour, local.minute)].append(row)
    counts: dict[str, int] = defaultdict(int)
    days = []
    seen: set[date] = set()
    for origin in sorted(origins, key=lambda x: _stamp(x.get("issued_at")) or as_of):
        issue, acquired = _stamp(origin.get("issued_at")), _stamp(origin.get("available_at"))
        if issue is None or acquired is None or acquired > as_of:
            counts["origin_unavailable"] += 1
            continue
        ds = str(origin.get("date", ""))
        try:
            day = date.fromisoformat(ds)
        except ValueError:
            counts["invalid_origin_date"] += 1
            continue
        if not (0 < (target - day).days <= LOOKBACK_DAYS and day < as_of.astimezone(zone).date()):
            continue
        if issue > datetime.combine(day, datetime.min.time(), zone) + timedelta(hours=7) or day in seen:
            counts["late_or_duplicate_origin"] += 1
            continue
        try:
            basis, factors, weather = reforecast_origin(origin["plan"], geometry=geometry)
        except (KeyError, TypeError, ValueError, OverflowError):
            counts["invalid_saved_weather"] += 1
            continue
        if not any(basis[h] > 0 for h in factors):
            counts["missing_saved_daylight_weather"] += 1
            continue
        actual, available = {}, {}
        for hour in range(24):
            pair = [slots.get((day, hour, minute), []) for minute in (0, 30)]
            if hour not in factors or any(len(group) != 1 for group in pair):
                counts["missing_or_duplicate_hour"] += 1
                continue
            values = [_number(group[0].get("pv")) for group in pair]
            receipts = [_actual_availability(group[0]) for group in pair]
            starts = [datetime.combine(day, datetime.min.time(), zone) + timedelta(hours=hour, minutes=m) for m in (0, 30)]
            if any(x is None for x in values) or any(t is None or t > as_of or t < start for t, start in zip(receipts, starts)):
                counts["invalid_or_unavailable_actual"] += 1
                continue
            actual[hour] = math.fsum(float(x) for x in values if x is not None)
            available[hour] = max([acquired, *(t for t in receipts if t is not None)])
        if actual:
            seen.add(day)
            days.append(PvTrainingDay(day, issue, basis, factors, weather, actual, available, str(origin["sha256"])))
    counts["accepted_days"] = len(days)
    counts["accepted_hours"] = sum(len(d.actual) for d in days)
    return tuple(sorted(days, key=lambda d: d.day)), dict(counts)


def _vectors(day: PvTrainingDay, coefficient: float) -> tuple[dict[int, float], dict[int, float]]:
    maximum = {h: day.basis[h] * coefficient for h in range(24)}
    return {h: maximum[h] * day.radiation_factor[h] for h in day.radiation_factor}, maximum


def fit_training_bias(days: tuple[PvTrainingDay, ...], *, coefficient: float, as_of: datetime,
                      before: date) -> tuple[dict[tuple[int, str], float], dict[str, int]]:
    samples: dict[tuple[int, str], dict[date, float]] = defaultdict(dict)
    for day in days:
        if day.day >= before or (before - day.day).days > LOOKBACK_DAYS:
            continue
        pv, _ = _vectors(day, coefficient)
        for hour, actual in day.actual.items():
            cls = day.weather.get(hour, "unknown")
            if day.available[hour] <= as_of and cls != "unknown" and hour in pv:
                samples[(hour, cls)][day.day] = actual - pv[hour]
    deltas, counts = {}, {}
    for key, values in samples.items():
        n = len(values)
        counts[f"{key[0]}:{key[1]}"] = n
        if n >= MIN_BIN_DAYS:
            deltas[key] = math.fsum(values.values()) / (n + PRIOR_PSEUDO_DAYS)
    return deltas, counts


def select_training_bias(*, days: tuple[PvTrainingDay, ...], coefficients: list[tuple[datetime, float]],
                         as_of: datetime, target_date: str, forecast_pv: dict[int, float], maximum: dict[int, float],
                         weather: dict[int, str], economic_days: tuple[PvBiasDay, ...],
                         evaluate_day: Callable[[PvBiasDay, dict[int, float]], ReplayErrors] | None,
                         previous_model: PvBiasModel | None = None) -> tuple[dict[int, float], dict[str, Any], PvBiasModel | None]:
    if as_of.utcoffset() is None or any(t.utcoffset() is None or not math.isfinite(c) or c < 1 for t, c in coefficients):
        raise ValueError("PV training requires finite, timestamped capability history")
    current = coefficient_at(coefficients, as_of)
    if current is None:
        raise ValueError("capability history is unavailable at as_of")
    target = date.fromisoformat(target_date)
    eligible = tuple(d for d in days if d.day < target and (target - d.day).days <= LOOKBACK_DAYS)
    if len({d.day for d in eligible}) != len(eligible):
        raise ValueError("duplicate PV training days")
    deltas, counts = fit_training_bias(eligible, coefficient=current, as_of=as_of, before=target)
    pv_base: list[float] = []
    pv_candidate: list[float] = []
    validation_dates: list[str] = []
    base_economics, candidate_economics = [], []
    economic_by_day = {d.day: d for d in economic_days}
    rejected = 0
    for day in eligible:
        coefficient = coefficient_at(coefficients, day.issued_at)
        if coefficient is None:
            continue
        rolling, _ = fit_training_bias(eligible, coefficient=coefficient, as_of=day.issued_at, before=day.day)
        baseline, limits = _vectors(day, coefficient)
        hours = [h for h in range(7, 23) if h in day.actual and h in baseline and day.available[h] <= as_of]
        if not rolling or not hours:
            continue
        # A missing weather interval never becomes a fabricated validation value.
        candidate = {h: min(limits[h], max(0., baseline[h] + rolling.get((h, day.weather.get(h, "unknown")), 0.))) for h in baseline}
        pv_base.extend(baseline[h] - day.actual[h] for h in hours)
        pv_candidate.extend(candidate[h] - day.actual[h] for h in hours)
        validation_dates.append(day.day.isoformat())
        economic = economic_by_day.get(day.day)
        if economic is not None and len(baseline) == 24 and evaluate_day is not None:
            try:
                frozen = replace(economic, hourly_forecast_pv_kwh=baseline, hourly_maximum_kwh=limits)
                a, b = evaluate_day(frozen, baseline), evaluate_day(frozen, candidate)
                if not a.valid() or not b.valid():
                    raise ValueError("invalid economic replay")
                base_economics.append(a)
                candidate_economics.append(b)
            except (KeyError, ValueError, ArithmeticError, TypeError):
                rejected += 1
    def mae(values: list[float]) -> float:
        return math.fsum(abs(x) for x in values) / len(values) if values else math.inf
    def bias(values: list[float]) -> float:
        return abs(math.fsum(values) / len(values)) if values else math.inf
    pv_pass = len(validation_dates) >= MIN_VALIDATION_DAYS and mae(pv_candidate) < mae(pv_base) - 1e-9 and bias(pv_candidate) <= bias(pv_base) + 1e-9
    means = {}
    if base_economics:
        for key in ("soc_mae", "buy_mae", "fee_mae"):
            means[key] = {"baseline": math.fsum(getattr(e, key) for e in base_economics) / len(base_economics),
                          "candidate": math.fsum(getattr(e, key) for e in candidate_economics) / len(candidate_economics)}
    # PV validation is independent of full battery evidence. All available complete
    # financial/SOC replays must remain non-worse; an already-perfect SOC may tie.
    economic_pass = bool(means) and rejected == 0 and all(v["candidate"] <= v["baseline"] + 1e-9 for v in means.values())
    digest = hashlib.sha256(json.dumps({"model": MODEL_ID, "sources": [d.source_sha256 for d in eligible],
                                      "coefficient": current}, sort_keys=True).encode()).hexdigest()
    admitted = bool(deltas) and pv_pass and economic_pass
    selected = PvBiasModel(deltas, tuple(d.day.isoformat() for d in eligible), as_of, digest) if admitted else None
    status = "accepted" if admitted else "neutral"
    if selected is None and previous_model is not None:
        if (previous_model.validated_at <= as_of and previous_model.training_days
                and max(previous_model.training_days) < target_date
                and min(previous_model.training_days) >= (target - timedelta(days=LOOKBACK_DAYS)).isoformat()
                and all(math.isfinite(x) for x in previous_model.deltas.values())):
            selected, status = previous_model, "retained_previous"
    diagnostics = {"model_id": MODEL_ID, "status": status, "enabled": selected is not None,
                   "minimum_bin_days": MIN_BIN_DAYS, "prior_pseudo_days": PRIOR_PSEUDO_DAYS,
                   "lookback_days": LOOKBACK_DAYS, "training_days": [d.day.isoformat() for d in eligible],
                   "bin_days": counts, "validation_days": validation_dates, "validation_hours": len(pv_base),
                   "pv_validation": {"baseline_mae": mae(pv_base) if pv_base else None, "candidate_mae": mae(pv_candidate) if pv_candidate else None},
                   "economic_validation_days": len(base_economics), "validation_errors": means, "rejected_folds": rejected,
                   "reason": "forward_metrics_passed" if admitted else "insufficient_forward_folds" if not pv_pass else "economic_replay_unavailable_or_regressed",
                   "source_sha256": digest, "as_of": as_of.isoformat(),
                   "uncorrected_hourly_pv_kwh": {str(h): x for h, x in forecast_pv.items()},
                   "hourly_deltas_kwh": {f"{h}:{c}": x for (h, c), x in (selected.deltas if selected else {}).items()}}
    return _apply(forecast_pv, maximum, weather, selected.deltas if selected else {}), diagnostics, selected
