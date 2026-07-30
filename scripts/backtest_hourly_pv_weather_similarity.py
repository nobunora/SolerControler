"""Backtest hourly PV corrections using only saved forecast-time inputs.

This is an analysis utility.  It does not alter forecasts, plans, or cloud data.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Any

from google.cloud import firestore


def number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def weather_class(code: Any) -> str:
    value = int(number(code))
    if value <= 3:
        return "clear"
    if 45 <= value <= 48:
        return "fog"
    if 51 <= value <= 67:
        return "rain"
    if 71 <= value <= 77:
        return "snow"
    if 80 <= value <= 99:
        return "shower"
    return "other"


def actuals(db_path: str) -> dict[tuple[str, int], float]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT substr(ts, 1, 10), CAST(substr(ts, 12, 2) AS INTEGER),
                      SUM(COALESCE(pv_kwh, 0))
               FROM monitoring_samples GROUP BY 1, 2"""
        ).fetchall()
    finally:
        conn.close()
    return {(str(day), int(hour)): number(pv) for day, hour, pv in rows}


def forecasts() -> dict[str, dict[int, dict[str, float | str]]]:
    project = os.environ.get("FIRESTORE_PROJECT_ID", "").strip() or None
    database = os.environ.get("FIRESTORE_DATABASE_ID", "(default)").strip() or "(default)"
    client = firestore.Client(project=project, database=database) if project else firestore.Client(database=database)
    out: dict[str, dict[int, dict[str, float | str]]] = defaultdict(dict)
    for doc in client.collection("forecast_hourly").stream():
        row = doc.to_dict() or {}
        day, hour = str(row.get("date", "")), int(number(row.get("hour")))
        if not day or not 0 <= hour <= 23:
            continue
        out[day][hour] = {
            "pv": max(0.0, number(row.get("forecast_pv_kwh"))),
            "shortwave": max(0.0, number(row.get("forecast_shortwave_radiation_w_m2"))),
            "class": weather_class(row.get("forecast_weather_code")),
        }
    return dict(out)


def clipped_median(values: list[float]) -> float:
    return min(1.5, max(0.5, median(values)))


def mae(pairs: list[tuple[float, float]]) -> float:
    return sum(abs(predicted - actual) for predicted, actual in pairs) / len(pairs) if pairs else 0.0


def run(*, start: date, end: date, lookback_days: int, min_candidates: int, actual: dict[tuple[str, int], float], history: dict[str, dict[int, dict[str, float | str]]], residual_alpha: float) -> dict[str, Any]:
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    baseline: list[tuple[float, float]] = []
    ewma_pairs: list[tuple[float, float]] = []
    similarity_pairs: list[tuple[float, float]] = []
    combined_pairs: list[tuple[float, float]] = []
    hourly_residual_pairs: list[tuple[float, float]] = []
    vector_residual_pairs: list[tuple[float, float]] = []
    coverage: list[dict[str, Any]] = []
    ewma_ratio = 1.0
    residual_by_hour = {hour: 0.0 for hour in range(24)}
    for day in days:
        key = day.isoformat()
        current = history.get(key, {})
        prior_days = [(day - timedelta(days=offset)).isoformat() for offset in range(1, lookback_days + 1)]
        day_baseline: list[tuple[float, float]] = []
        day_baseline_by_hour: list[tuple[int, float, float]] = []
        day_ewma: list[tuple[float, float]] = []
        day_similarity: list[tuple[float, float]] = []
        day_combined: list[tuple[float, float]] = []
        day_hourly_residual: list[tuple[float, float]] = []
        day_vector_residual: list[tuple[float, float]] = []
        matched_hours = 0
        for hour in range(7, 23):
            target = current.get(hour)
            actual_pv = actual.get((key, hour))
            if not target or actual_pv is None:
                continue
            base = number(target["pv"])
            if base <= 0:
                continue
            ratios: list[float] = []
            residuals: list[float] = []
            shortwave = number(target["shortwave"])
            for prior_key in prior_days:
                prior = history.get(prior_key, {}).get(hour)
                prior_actual = actual.get((prior_key, hour))
                if not prior or prior_actual is None or number(prior["pv"]) <= 0:
                    continue
                prior_shortwave = number(prior["shortwave"])
                if prior["class"] != target["class"] or shortwave <= 0 or prior_shortwave <= 0:
                    continue
                if not 0.7 * shortwave <= prior_shortwave <= 1.3 * shortwave:
                    continue
                ratios.append(prior_actual / number(prior["pv"]))
                residuals.append(prior_actual - number(prior["pv"]))
            similarity_ratio = clipped_median(ratios) if len(ratios) >= min_candidates else 1.0
            vector_residual = median(residuals) if len(residuals) >= min_candidates else 0.0
            if ratios and len(ratios) >= min_candidates:
                matched_hours += 1
            day_baseline.append((base, actual_pv))
            day_baseline_by_hour.append((hour, base, actual_pv))
            day_ewma.append((base * ewma_ratio, actual_pv))
            day_similarity.append((base * similarity_ratio, actual_pv))
            day_combined.append((base * ewma_ratio * similarity_ratio, actual_pv))
            day_hourly_residual.append((max(0.0, base + residual_by_hour[hour]), actual_pv))
            day_vector_residual.append((max(0.0, base + vector_residual), actual_pv))
        if len(day_baseline) >= 8:
            baseline.extend(day_baseline)
            ewma_pairs.extend(day_ewma)
            similarity_pairs.extend(day_similarity)
            combined_pairs.extend(day_combined)
            hourly_residual_pairs.extend(day_hourly_residual)
            vector_residual_pairs.extend(day_vector_residual)
            coverage.append(
                {
                    "date": key,
                    "matched_hours": matched_hours,
                    "evaluated_hours": len(day_baseline),
                    "mae_kwh": {
                        "baseline": round(mae(day_baseline), 4),
                        "ewma": round(mae(day_ewma), 4),
                        "weather_similarity": round(mae(day_similarity), 4),
                        "combined": round(mae(day_combined), 4),
                        "hourly_residual": round(mae(day_hourly_residual), 4),
                        "vector_residual": round(mae(day_vector_residual), 4),
                    },
                }
            )
            ratios = [actual_value / predicted for predicted, actual_value in day_baseline if predicted > 0]
            if ratios:
                ewma_ratio = 0.2 * clipped_median(ratios) + 0.8 * ewma_ratio
            for hour, predicted, actual_value in day_baseline_by_hour:
                residual_by_hour[hour] = residual_alpha * (actual_value - predicted) + (1.0 - residual_alpha) * residual_by_hour[hour]
    return {
        "days": len(coverage),
        "hourly_samples": len(baseline),
        "mae_kwh": {
            "baseline": round(mae(baseline), 4),
            "ewma": round(mae(ewma_pairs), 4),
            "weather_similarity": round(mae(similarity_pairs), 4),
            "combined": round(mae(combined_pairs), 4),
            "hourly_residual": round(mae(hourly_residual_pairs), 4),
            "vector_residual": round(mae(vector_residual_pairs), 4),
        },
        "coverage": coverage,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-06-01")
    parser.add_argument("--end", default="2026-07-28")
    parser.add_argument("--lookback-days", type=int, default=45)
    parser.add_argument("--min-candidates", type=int, default=3)
    parser.add_argument("--db-path", default="artifacts/solar_monitor.db")
    parser.add_argument("--residual-alpha", type=float, default=1.0)
    args = parser.parse_args()
    result = run(start=date.fromisoformat(args.start), end=date.fromisoformat(args.end), lookback_days=args.lookback_days, min_candidates=args.min_candidates, actual=actuals(args.db_path), history=forecasts(), residual_alpha=args.residual_alpha)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
