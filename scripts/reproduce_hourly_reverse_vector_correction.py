"""Reproduce the hourly weather-vector correction comparison.

The default input is a de-identified, repository-tracked snapshot. Use
``--live`` only to replay against the configured Firestore and local SQLite
sources. The experiment is read-only except for its JSON result file.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.backtest_hourly_pv_weather_similarity as base  # noqa: E402

DEFAULT_DATASET = ROOT / "docs/current/product/evidence/hourly_vector_dataset_2026-08-23.csv"
DEFAULT_OUTPUT = ROOT / "artifacts/analysis/hourly_reverse_vector_correction_results.json"
MULTIPLIERS = (-1.0, -0.5, 0.0, 0.5, 1.0, 1.5)
METHODS = ("baseline", "raw", "sign", "multiplier", "error_residual")


def export_snapshot(
    path: Path,
    actuals: dict[tuple[str, int], float],
    forecasts: dict[str, dict[int, dict[str, Any]]],
) -> None:
    """Export only the de-identified fields required by this experiment."""
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted(set(actuals) | {(day, hour) for day, hours in forecasts.items() for hour in hours})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("date", "hour", "actual_pv_kwh", "forecast_pv_kwh", "shortwave_w_m2", "weather_class"),
            lineterminator="\n",
        )
        writer.writeheader()
        for day, hour in keys:
            forecast = forecasts.get(day, {}).get(hour)
            writer.writerow(
                {
                    "date": day,
                    "hour": hour,
                    "actual_pv_kwh": actuals.get((day, hour), ""),
                    "forecast_pv_kwh": forecast["pv"] if forecast else "",
                    "shortwave_w_m2": forecast["shortwave"] if forecast else "",
                    "weather_class": forecast["class"] if forecast else "",
                }
            )


def load_snapshot(
    path: Path,
) -> tuple[dict[tuple[str, int], float], dict[str, dict[int, dict[str, Any]]]]:
    actuals: dict[tuple[str, int], float] = {}
    forecasts: dict[str, dict[int, dict[str, Any]]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row["date"], int(row["hour"]))
            if row["actual_pv_kwh"]:
                actuals[key] = float(row["actual_pv_kwh"])
            if row["forecast_pv_kwh"]:
                forecasts.setdefault(row["date"], {})[key[1]] = {
                    "pv": float(row["forecast_pv_kwh"]),
                    "shortwave": float(row["shortwave_w_m2"]),
                    "class": row["weather_class"],
                }
    return actuals, forecasts


def _mae(rows: Iterable[tuple[float, float]]) -> float:
    pairs = list(rows)
    return sum(abs(predicted - observed) for predicted, observed in pairs) / len(pairs) if pairs else 0.0


def replay(
    args: argparse.Namespace,
    actuals: dict[tuple[str, int], float],
    forecasts: dict[str, dict[int, dict[str, Any]]],
) -> dict[str, Any]:
    candidate_cache: dict[tuple[str, int], list[tuple[date, float, float, float]]] = {}

    def candidates(day: date, hour: int) -> list[tuple[date, float, float, float]]:
        key = (day.isoformat(), hour)
        if key in candidate_cache:
            return candidate_cache[key]
        target = forecasts.get(day.isoformat(), {}).get(hour)
        rows: list[tuple[date, float, float, float]] = []
        if target:
            target_shortwave = base.number(target["shortwave"])
            for offset in range(1, args.lookback_days + 1):
                prior_day = day - timedelta(days=offset)
                prior = forecasts.get(prior_day.isoformat(), {}).get(hour)
                observed = actuals.get((prior_day.isoformat(), hour))
                if not prior or observed is None:
                    continue
                prior_pv = base.number(prior["pv"])
                prior_shortwave = base.number(prior["shortwave"])
                if (
                    prior_pv > 0
                    and prior["class"] == target["class"]
                    and target_shortwave > 0
                    and 0.7 * target_shortwave <= prior_shortwave <= 1.3 * target_shortwave
                ):
                    rows.append((prior_day, observed, prior_pv, observed - prior_pv))
        candidate_cache[key] = rows
        return rows

    def raw_correction(day: date, hour: int) -> float:
        rows = candidates(day, hour)
        return median(row[3] for row in rows) if len(rows) >= args.min_candidates else 0.0

    def historical_hits(day: date, hour: int) -> list[tuple[date, float, float, float]]:
        rows = []
        for offset in range(1, args.lookback_days + 1):
            prior_day = day - timedelta(days=offset)
            forecast = forecasts.get(prior_day.isoformat(), {}).get(hour)
            observed = actuals.get((prior_day.isoformat(), hour))
            if not forecast or observed is None or base.number(forecast["pv"]) <= 0:
                continue
            if len(candidates(prior_day, hour)) >= args.min_candidates:
                rows.append((prior_day, observed, base.number(forecast["pv"]), raw_correction(prior_day, hour)))
        return rows

    def evaluate_period(start: date, end: date) -> dict[str, Any]:
        results: dict[str, list[tuple[float, float]]] = {method: [] for method in METHODS}
        hourly: dict[int, dict[str, list[tuple[float, float]]]] = {
            hour: {method: [] for method in METHODS} for hour in range(7, 23)
        }
        hourly_hits = {hour: 0 for hour in range(7, 23)}
        multiplier_counts: dict[str, int] = {}
        hit_count = 0
        day = start
        while day <= end:
            for hour in range(7, 23):
                target = forecasts.get(day.isoformat(), {}).get(hour)
                observed = actuals.get((day.isoformat(), hour))
                if not target or observed is None or base.number(target["pv"]) <= 0:
                    continue
                forecast = base.number(target["pv"])
                current_candidates = candidates(day, hour)
                hit = len(current_candidates) >= args.min_candidates
                correction = median(row[3] for row in current_candidates) if hit else 0.0
                corrections = {method: correction for method in METHODS if method != "baseline"}
                history = historical_hits(day, hour)
                if hit and len(history) >= args.sign_history_min:
                    sign_scores = [
                        (_mae((max(0.0, pv + sign * prior_c), actual) for _, actual, pv, prior_c in history), sign)
                        for sign in (1.0, -1.0)
                    ]
                    corrections["sign"] = min(sign_scores, key=lambda item: item[0])[1] * correction
                    multiplier_scores = [
                        (_mae((max(0.0, pv + factor * prior_c), actual) for _, actual, pv, prior_c in history), factor)
                        for factor in MULTIPLIERS
                    ]
                    factor = min(multiplier_scores, key=lambda item: item[0])[1]
                    corrections["multiplier"] = factor * correction
                    multiplier_counts[str(factor)] = multiplier_counts.get(str(factor), 0) + 1
                    corrections["error_residual"] = correction + median(
                        actual - (pv + prior_c) for _, actual, pv, prior_c in history
                    )
                if hit:
                    hit_count += 1
                    hourly_hits[hour] += 1
                predictions = {"baseline": forecast}
                predictions.update(
                    {method: max(0.0, forecast + value) for method, value in corrections.items()}
                )
                for method, prediction in predictions.items():
                    pair = (prediction, observed)
                    results[method].append(pair)
                    hourly[hour][method].append(pair)
            day += timedelta(days=1)
        sample_count = len(results["baseline"])
        return {
            "samples": sample_count,
            "hits": hit_count,
            "coverage": hit_count / sample_count if sample_count else 0.0,
            "mae_kwh": {method: _mae(rows) for method, rows in results.items()},
            "multiplier_selection_counts": multiplier_counts,
            "hourly": {
                str(hour): {
                    "samples": len(hourly[hour]["baseline"]),
                    "hits": hourly_hits[hour],
                    "mae_kwh": {method: _mae(rows) for method, rows in hourly[hour].items()},
                }
                for hour in range(7, 23)
            },
        }

    starts = (date(2026, 7, 1), date(2026, 8, 1), date(2026, 8, 10))
    periods = {"all": (args.start, args.end)}
    periods.update({f"{start.isoformat()}..{args.end.isoformat()}": (max(args.start, start), args.end) for start in starts})
    return {
        "parameters": {
            "start": args.start.isoformat(),
            "end": args.end.isoformat(),
            "lookback_days": args.lookback_days,
            "min_candidates": args.min_candidates,
            "sign_history_min": args.sign_history_min,
            "input": "live" if args.live else str(args.dataset),
        },
        "periods": {
            name: evaluate_period(start, end)
            for name, (start, end) in periods.items()
            if start <= end
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 6, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 8, 23))
    parser.add_argument("--lookback-days", type=int, default=45)
    parser.add_argument("--min-candidates", type=int, default=2)
    parser.add_argument("--sign-history-min", type=int, default=7)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--db-path", default="artifacts/solar_monitor.db")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--live", action="store_true", help="Read configured Firestore and local SQLite instead of the snapshot.")
    parser.add_argument("--export-dataset", type=Path, help="With --live, export a de-identified snapshot to this path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.live:
        actuals, forecasts = base.actuals(args.db_path), base.forecasts()
        if args.export_dataset:
            export_snapshot(args.export_dataset, actuals, forecasts)
    else:
        actuals, forecasts = load_snapshot(args.dataset)
    result = replay(args, actuals, forecasts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
