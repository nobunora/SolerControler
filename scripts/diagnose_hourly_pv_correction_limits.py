"""Diagnose how much predictable signal exists in hourly PV forecast residuals.

This script is intentionally offline-only. It reads the de-identified frozen
snapshot committed for the hourly-vector review and never imports production
forecasting, persistence, Firestore, or deployment code.

The experiment answers three questions:

1. Is the baseline error dominated by a stable bias that post-processing can
   remove, or by volatile residuals that do not repeat from day to day?
2. How predictive are prior residuals selected by the current weather-vector
   rule, especially in August where the review correction regressed?
3. Do shorter lookbacks, recency weighting, continuous shortwave similarity,
   normalized residuals, or limited hour pooling materially change the result?
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "docs/current/product/evidence/hourly_vector_dataset_2026-08-23.csv"
DEFAULT_OUTPUT = ROOT / "artifacts/analysis/hourly_pv_correction_diagnostic.json"
EPS_KWH = 0.05
LOOKBACKS = (7, 14, 21, 30, 45)
RECENCY_HALF_LIVES = (None, 7.0, 14.0)
PERIODS = {
    "all": (date(2026, 6, 1), date(2026, 8, 23)),
    "july_plus": (date(2026, 7, 1), date(2026, 8, 23)),
    "august": (date(2026, 8, 1), date(2026, 8, 23)),
    "recent_14d": (date(2026, 8, 10), date(2026, 8, 23)),
}


@dataclass(frozen=True)
class Row:
    day: date
    hour: int
    actual: float
    forecast: float
    shortwave: float
    weather_class: str

    @property
    def additive_residual(self) -> float:
        return self.actual - self.forecast

    @property
    def log_residual(self) -> float:
        return math.log((self.actual + EPS_KWH) / (self.forecast + EPS_KWH))


@dataclass(frozen=True)
class Candidate:
    row: Row
    age_days: int
    distance: float


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _mae(pairs: Iterable[tuple[float, float]]) -> float:
    items = list(pairs)
    return _mean(abs(predicted - observed) for predicted, observed in items)


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mean_x = _mean(xs)
    mean_y = _mean(ys)
    dx = [value - mean_x for value in xs]
    dy = [value - mean_y for value in ys]
    denom = math.sqrt(sum(value * value for value in dx) * sum(value * value for value in dy))
    if denom <= 0.0:
        return None
    return sum(left * right for left, right in zip(dx, dy)) / denom


def _weighted_median(values_and_weights: Sequence[tuple[float, float]]) -> float:
    ordered = sorted((value, max(0.0, weight)) for value, weight in values_and_weights)
    total = sum(weight for _, weight in ordered)
    if total <= 0.0:
        return median(value for value, _ in ordered) if ordered else 0.0
    threshold = total / 2.0
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def load_rows(path: Path) -> tuple[dict[tuple[date, int], Row], dict[str, Any]]:
    rows: dict[tuple[date, int], Row] = {}
    counts: dict[str, Any] = {
        "csv_rows": 0,
        "evaluable_positive_forecast_rows": 0,
        "positive_forecast_with_nonpositive_shortwave": 0,
    }
    first_valid_shortwave_day: date | None = None
    with path.open(encoding="utf-8", newline="") as handle:
        for item in csv.DictReader(handle):
            counts["csv_rows"] += 1
            if not item.get("actual_pv_kwh") or not item.get("forecast_pv_kwh"):
                continue
            forecast = float(item["forecast_pv_kwh"])
            if forecast <= 0.0:
                continue
            actual = float(item["actual_pv_kwh"])
            shortwave = float(item.get("shortwave_w_m2") or 0.0)
            day = date.fromisoformat(item["date"])
            row = Row(
                day=day,
                hour=int(item["hour"]),
                actual=actual,
                forecast=forecast,
                shortwave=shortwave,
                weather_class=item.get("weather_class") or "",
            )
            rows[(day, row.hour)] = row
            counts["evaluable_positive_forecast_rows"] += 1
            if shortwave <= 0.0:
                counts["positive_forecast_with_nonpositive_shortwave"] += 1
            elif first_valid_shortwave_day is None or day < first_valid_shortwave_day:
                first_valid_shortwave_day = day
    counts["first_valid_positive_shortwave_day"] = (
        first_valid_shortwave_day.isoformat() if first_valid_shortwave_day else None
    )
    return rows, counts


def target_rows(rows: dict[tuple[date, int], Row], start: date, end: date) -> list[Row]:
    return sorted(
        (
            row
            for row in rows.values()
            if start <= row.day <= end and 7 <= row.hour <= 22 and row.forecast > 0.0
        ),
        key=lambda row: (row.day, row.hour),
    )


def candidate_rows(
    rows: dict[tuple[date, int], Row],
    target: Row,
    *,
    lookback_days: int,
    similarity: str,
    pool_hours: int,
    top_k: int = 5,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    if target.shortwave <= 0.0:
        return candidates
    for age in range(1, lookback_days + 1):
        prior_day = target.day - timedelta(days=age)
        for hour in range(max(0, target.hour - pool_hours), min(23, target.hour + pool_hours) + 1):
            prior = rows.get((prior_day, hour))
            if prior is None or prior.forecast <= 0.0 or prior.shortwave <= 0.0:
                continue
            log_sw_distance = abs(math.log(prior.shortwave / target.shortwave))
            if similarity == "current_hard":
                if hour != target.hour:
                    continue
                if prior.weather_class != target.weather_class:
                    continue
                if not (0.7 * target.shortwave <= prior.shortwave <= 1.3 * target.shortwave):
                    continue
                distance = log_sw_distance
            elif similarity == "continuous_sw":
                distance = log_sw_distance + 0.20 * abs(hour - target.hour)
            elif similarity == "continuous_sw_class":
                mismatch = 0.0 if prior.weather_class == target.weather_class else 0.45
                distance = log_sw_distance + mismatch + 0.20 * abs(hour - target.hour)
            else:
                raise ValueError(f"unknown similarity: {similarity}")
            candidates.append(Candidate(row=prior, age_days=age, distance=distance))
    if similarity.startswith("continuous"):
        candidates.sort(key=lambda item: (item.distance, item.age_days, item.row.day))
        return candidates[:top_k]
    return candidates


def candidate_weight(candidate: Candidate, half_life_days: float | None) -> float:
    recency = 1.0
    if half_life_days is not None:
        recency = math.exp(-math.log(2.0) * candidate.age_days / half_life_days)
    similarity = math.exp(-0.5 * (candidate.distance / 0.35) ** 2)
    return recency * similarity


def predict_variant(
    target: Row,
    candidates: Sequence[Candidate],
    *,
    residual_kind: str,
    half_life_days: float | None,
    min_candidates: int,
) -> float:
    if len(candidates) < min_candidates:
        return target.forecast
    if residual_kind == "additive":
        center = _weighted_median(
            [
                (candidate.row.additive_residual, candidate_weight(candidate, half_life_days))
                for candidate in candidates
            ]
        )
        return max(0.0, target.forecast + center)
    if residual_kind == "log_ratio":
        center = _weighted_median(
            [
                (candidate.row.log_residual, candidate_weight(candidate, half_life_days))
                for candidate in candidates
            ]
        )
        return max(0.0, (target.forecast + EPS_KWH) * math.exp(center) - EPS_KWH)
    raise ValueError(f"unknown residual kind: {residual_kind}")


def production_like_prediction(
    target: Row,
    candidates: Sequence[Candidate],
    *,
    spread_kwh: float = 0.6,
) -> float:
    if not candidates:
        return target.forecast
    residuals = [candidate.row.additive_residual for candidate in candidates]
    center = median(residuals)
    variance = (
        spread_kwh**2
        if len(residuals) == 1
        else sum((value - center) ** 2 for value in residuals) / len(residuals)
    )
    weight = (len(residuals) / (len(residuals) + 2.0)) * (
        spread_kwh**2 / (spread_kwh**2 + variance)
    )
    return max(0.0, target.forecast + weight * center)


def baseline_diagnostics(rows: dict[tuple[date, int], Row]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for name, (start, end) in PERIODS.items():
        period_rows = target_rows(rows, start, end)
        residuals = [row.additive_residual for row in period_rows]
        log_residuals = [row.log_residual for row in period_rows]
        mae = _mean(abs(value) for value in residuals)
        output[name] = {
            "samples": len(period_rows),
            "mae_kwh": mae,
            "mean_actual_minus_forecast_kwh": _mean(residuals),
            "median_actual_minus_forecast_kwh": median(residuals) if residuals else 0.0,
            "abs_mean_bias_share_of_mae": abs(_mean(residuals)) / mae if mae else 0.0,
            "mean_log_actual_over_forecast": _mean(log_residuals),
            "median_log_actual_over_forecast": median(log_residuals) if log_residuals else 0.0,
        }
    return output


def lag_diagnostics(
    rows: dict[tuple[date, int], Row],
    start: date,
    end: date,
) -> dict[str, Any]:
    current = target_rows(rows, start, end)
    additive: dict[str, Any] = {}
    normalized: dict[str, Any] = {}
    for lag in (1, 2, 3, 7, 14):
        left_add: list[float] = []
        right_add: list[float] = []
        left_log: list[float] = []
        right_log: list[float] = []
        for row in current:
            prior = rows.get((row.day - timedelta(days=lag), row.hour))
            if prior is None or prior.forecast <= 0.0:
                continue
            left_add.append(row.additive_residual)
            right_add.append(prior.additive_residual)
            left_log.append(row.log_residual)
            right_log.append(prior.log_residual)
        additive[str(lag)] = {"pairs": len(left_add), "pearson": _pearson(left_add, right_add)}
        normalized[str(lag)] = {"pairs": len(left_log), "pearson": _pearson(left_log, right_log)}
    return {"additive": additive, "log_ratio": normalized}


def analog_signal_diagnostics(
    rows: dict[tuple[date, int], Row],
    *,
    start: date,
    end: date,
    lookback_days: int,
    similarity: str,
) -> dict[str, Any]:
    actual_add: list[float] = []
    predicted_add: list[float] = []
    actual_log: list[float] = []
    predicted_log: list[float] = []
    sign_matches = 0
    targets = target_rows(rows, start, end)
    for target in targets:
        candidates = candidate_rows(
            rows,
            target,
            lookback_days=lookback_days,
            similarity=similarity,
            pool_hours=0,
        )
        if len(candidates) < 2:
            continue
        prior_add = median(candidate.row.additive_residual for candidate in candidates)
        prior_log = median(candidate.row.log_residual for candidate in candidates)
        actual_add.append(target.additive_residual)
        predicted_add.append(prior_add)
        actual_log.append(target.log_residual)
        predicted_log.append(prior_log)
        if (prior_add >= 0.0) == (target.additive_residual >= 0.0):
            sign_matches += 1
    return {
        "targets": len(targets),
        "hits": len(actual_add),
        "coverage": len(actual_add) / len(targets) if targets else 0.0,
        "additive_residual_pearson": _pearson(actual_add, predicted_add),
        "log_residual_pearson": _pearson(actual_log, predicted_log),
        "additive_sign_agreement": sign_matches / len(actual_add) if actual_add else 0.0,
    }


def _variant_result(
    name: str,
    predictions: Sequence[tuple[Row, float]],
    baseline_by_period: dict[str, float],
) -> dict[str, Any]:
    periods: dict[str, Any] = {}
    for period_name, (start, end) in PERIODS.items():
        selected = [
            (prediction, row.actual)
            for row, prediction in predictions
            if start <= row.day <= end
        ]
        mae = _mae(selected)
        baseline = baseline_by_period[period_name]
        periods[period_name] = {
            "samples": len(selected),
            "mae_kwh": mae,
            "baseline_mae_kwh": baseline,
            "improvement_percent": 100.0 * (baseline - mae) / baseline if baseline else 0.0,
        }
    return {"name": name, "periods": periods}


def evaluate_variants(rows: dict[tuple[date, int], Row]) -> dict[str, Any]:
    all_targets = target_rows(rows, PERIODS["all"][0], PERIODS["all"][1])
    baseline_by_period = {
        name: _mae(
            (row.forecast, row.actual)
            for row in all_targets
            if start <= row.day <= end
        )
        for name, (start, end) in PERIODS.items()
    }
    variants: list[dict[str, Any]] = []

    production_predictions: list[tuple[Row, float]] = []
    for target in all_targets:
        candidates = candidate_rows(
            rows,
            target,
            lookback_days=45,
            similarity="current_hard",
            pool_hours=0,
        )
        production_predictions.append((target, production_like_prediction(target, candidates)))
    variants.append(
        _variant_result("production_like_45d", production_predictions, baseline_by_period)
    )

    for lookback in LOOKBACKS:
        for similarity in ("current_hard", "continuous_sw", "continuous_sw_class"):
            for residual_kind in ("additive", "log_ratio"):
                pool_options = (
                    (0, 1)
                    if residual_kind == "log_ratio" and similarity.startswith("continuous")
                    else (0,)
                )
                for pool_hours in pool_options:
                    for half_life in RECENCY_HALF_LIVES:
                        predictions: list[tuple[Row, float]] = []
                        for target in all_targets:
                            candidates = candidate_rows(
                                rows,
                                target,
                                lookback_days=lookback,
                                similarity=similarity,
                                pool_hours=pool_hours,
                            )
                            prediction = predict_variant(
                                target,
                                candidates,
                                residual_kind=residual_kind,
                                half_life_days=half_life,
                                min_candidates=2,
                            )
                            predictions.append((target, prediction))
                        recency_label = (
                            "none" if half_life is None else f"hl{int(half_life)}d"
                        )
                        name = (
                            f"lb{lookback}_{similarity}_{residual_kind}_"
                            f"pool{pool_hours}_{recency_label}"
                        )
                        variants.append(_variant_result(name, predictions, baseline_by_period))

    variants.sort(key=lambda item: item["periods"]["all"]["mae_kwh"])
    robust = sorted(
        variants,
        key=lambda item: (
            -min(
                item["periods"]["august"]["improvement_percent"],
                item["periods"]["recent_14d"]["improvement_percent"],
            ),
            item["periods"]["all"]["mae_kwh"],
        ),
    )
    return {
        "variant_count": len(variants),
        "best_all_period": variants[:10],
        "best_recent_robustness": robust[:10],
        "all_variants": variants,
    }


def build_result(
    rows: dict[tuple[date, int], Row],
    dataset_meta: dict[str, Any],
) -> dict[str, Any]:
    analog: dict[str, Any] = {}
    for period_name in ("all", "august", "recent_14d"):
        start, end = PERIODS[period_name]
        for lookback in (7, 14, 30, 45):
            for similarity in ("current_hard", "continuous_sw", "continuous_sw_class"):
                key = f"{period_name}_lb{lookback}_{similarity}"
                analog[key] = analog_signal_diagnostics(
                    rows,
                    start=start,
                    end=end,
                    lookback_days=lookback,
                    similarity=similarity,
                )
    variants = evaluate_variants(rows)
    return {
        "dataset": dataset_meta,
        "baseline": baseline_diagnostics(rows),
        "same_hour_residual_lag_correlation": {
            name: lag_diagnostics(rows, start, end)
            for name, (start, end) in PERIODS.items()
        },
        "analog_signal": analog,
        "variant_search": variants,
    }


def compact_summary(result: dict[str, Any]) -> dict[str, Any]:
    baseline = result["baseline"]
    analog = result["analog_signal"]
    search = result["variant_search"]
    return {
        "dataset": result["dataset"],
        "baseline": baseline,
        "lag1": {
            period: result["same_hour_residual_lag_correlation"][period]["additive"]["1"]
            for period in ("all", "august", "recent_14d")
        },
        "current_hard_signal": {
            key: analog[key]
            for key in (
                "all_lb45_current_hard",
                "august_lb45_current_hard",
                "recent_14d_lb45_current_hard",
                "august_lb14_current_hard",
                "recent_14d_lb14_current_hard",
            )
        },
        "best_all_period": search["best_all_period"][:5],
        "best_recent_robustness": search["best_recent_robustness"][:5],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows, dataset_meta = load_rows(args.dataset)
    result = build_result(rows, dataset_meta)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "DIAGNOSTIC_SUMMARY_JSON="
        + json.dumps(compact_summary(result), sort_keys=True, separators=(",", ":"))
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
