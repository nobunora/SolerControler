"""Evaluate a strictly causal safety gate for hourly PV residual correction.

The root-cause diagnostics show that historical residual corrections improve the
aggregate period but become non-predictive in August. This experiment asks
whether a day-ahead selector can preserve useful correction regimes and fall
back to the uncorrected physical forecast when recent out-of-sample evidence no
longer supports a correction.

The experiment is offline-only and reads the de-identified frozen snapshot. A
selector for target day D may inspect realized errors only from dates < D.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import diagnose_hourly_pv_correction_limits as core
from scripts import diagnose_hourly_pv_regime_bias as regime

DEFAULT_OUTPUT = ROOT / "artifacts/analysis/hourly_pv_adaptive_gate_diagnostic.json"

MODEL_NAMES = (
    "production_like_45d",
    "same_hour_bias_45d_hl7d",
    "same_hour_bias_45d_hl14d",
)
SELECTOR_WINDOWS = (3, 7, 14, 21)
MARGINS = (0.0, 0.02, 0.05)
GRANULARITIES = ("pooled", "per_hour")


@dataclass(frozen=True)
class PredictionRecord:
    row: core.Row
    predictions: dict[str, float]


def _candidate_predictions(
    target: core.Row,
    rows: dict[tuple[date, int], core.Row],
) -> dict[str, float]:
    hard_candidates = core.candidate_rows(
        rows,
        target,
        lookback_days=45,
        similarity="current_hard",
        pool_hours=0,
    )
    return {
        "baseline": target.forecast,
        "production_like_45d": core.production_like_prediction(
            target,
            hard_candidates,
        ),
        "same_hour_bias_45d_hl7d": regime.predict(
            target,
            rows,
            lookback=45,
            half_life=7.0,
            residual_kind="additive",
        ),
        "same_hour_bias_45d_hl14d": regime.predict(
            target,
            rows,
            lookback=45,
            half_life=14.0,
            residual_kind="additive",
        ),
    }


def build_prediction_records(
    rows: dict[tuple[date, int], core.Row],
) -> list[PredictionRecord]:
    eligible = sorted(
        (
            row
            for row in rows.values()
            if 7 <= row.hour <= 22 and row.forecast > 0.0
        ),
        key=lambda item: (item.day, item.hour),
    )
    return [
        PredictionRecord(row=row, predictions=_candidate_predictions(row, rows))
        for row in eligible
    ]


def _mae_for_model(records: Iterable[PredictionRecord], model: str) -> float:
    pairs = [
        (record.predictions[model], record.row.actual)
        for record in records
        if model in record.predictions
    ]
    return core._mae(pairs)


def _history_for_selector(
    records: list[PredictionRecord],
    *,
    target_day: date,
    target_hour: int,
    window_days: int,
    granularity: str,
) -> list[PredictionRecord]:
    start = target_day - timedelta(days=window_days)
    return [
        record
        for record in records
        if start <= record.row.day < target_day
        and (granularity == "pooled" or record.row.hour == target_hour)
    ]


def select_model(
    history: list[PredictionRecord],
    *,
    margin: float,
    min_distinct_days: int,
) -> tuple[str, dict[str, Any]]:
    distinct_days = len({record.row.day for record in history})
    if distinct_days < min_distinct_days:
        return "baseline", {
            "reason": "insufficient_history",
            "distinct_days": distinct_days,
            "baseline_mae_kwh": None,
            "candidate_mae_kwh": {},
        }

    baseline_mae = _mae_for_model(history, "baseline")
    scores = {model: _mae_for_model(history, model) for model in MODEL_NAMES}
    best_model = min(scores, key=lambda model: scores[model])
    best_mae = scores[best_model]
    threshold = baseline_mae * (1.0 - margin)
    if best_mae < threshold:
        return best_model, {
            "reason": "candidate_passed_gate",
            "distinct_days": distinct_days,
            "baseline_mae_kwh": baseline_mae,
            "candidate_mae_kwh": scores,
        }
    return "baseline", {
        "reason": "baseline_fallback",
        "distinct_days": distinct_days,
        "baseline_mae_kwh": baseline_mae,
        "candidate_mae_kwh": scores,
    }


def evaluate_static_models(
    records: list[PredictionRecord],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for model in ("baseline", *MODEL_NAMES):
        periods: dict[str, Any] = {}
        for period_name, (start, end) in core.PERIODS.items():
            selected = [record for record in records if start <= record.row.day <= end]
            mae = _mae_for_model(selected, model)
            baseline = _mae_for_model(selected, "baseline")
            periods[period_name] = {
                "samples": len(selected),
                "mae_kwh": mae,
                "improvement_percent": (
                    100.0 * (baseline - mae) / baseline if baseline else 0.0
                ),
            }
        output[model] = periods
    return output


def evaluate_selector(
    records: list[PredictionRecord],
    *,
    window_days: int,
    margin: float,
    granularity: str,
) -> dict[str, Any]:
    decisions: list[tuple[PredictionRecord, str, float, str]] = []
    min_distinct_days = min(3, window_days)

    # Select a model once per target day for pooled mode. Per-hour mode selects
    # independently by hour, but still uses only dates strictly before target.
    decision_cache: dict[tuple[date, int | None], tuple[str, dict[str, Any]]] = {}
    for record in records:
        key = (
            record.row.day,
            None if granularity == "pooled" else record.row.hour,
        )
        if key not in decision_cache:
            history = _history_for_selector(
                records,
                target_day=record.row.day,
                target_hour=record.row.hour,
                window_days=window_days,
                granularity=granularity,
            )
            decision_cache[key] = select_model(
                history,
                margin=margin,
                min_distinct_days=min_distinct_days,
            )
        model, meta = decision_cache[key]
        prediction = record.predictions[model]
        decisions.append((record, model, prediction, str(meta["reason"])))

    periods: dict[str, Any] = {}
    for period_name, (start, end) in core.PERIODS.items():
        selected = [item for item in decisions if start <= item[0].row.day <= end]
        mae = core._mae((prediction, record.row.actual) for record, _, prediction, _ in selected)
        baseline = core._mae(
            (record.predictions["baseline"], record.row.actual)
            for record, _, _, _ in selected
        )
        model_counts = Counter(model for _, model, _, _ in selected)
        reason_counts = Counter(reason for _, _, _, reason in selected)
        correction_rows = sum(count for model, count in model_counts.items() if model != "baseline")
        periods[period_name] = {
            "samples": len(selected),
            "mae_kwh": mae,
            "baseline_mae_kwh": baseline,
            "improvement_percent": (
                100.0 * (baseline - mae) / baseline if baseline else 0.0
            ),
            "model_selection_counts": dict(sorted(model_counts.items())),
            "decision_reason_counts": dict(sorted(reason_counts.items())),
            "correction_application_rate": (
                correction_rows / len(selected) if selected else 0.0
            ),
        }

    return {
        "name": (
            f"gate_{granularity}_window{window_days}d_margin{int(margin * 100):02d}pct"
        ),
        "parameters": {
            "window_days": window_days,
            "margin": margin,
            "granularity": granularity,
            "min_distinct_days": min_distinct_days,
        },
        "periods": periods,
    }


def oracle_by_day(records: list[PredictionRecord]) -> dict[str, Any]:
    """Non-causal upper bound: choose the best model after each day's outcomes.

    This is diagnostic only. It estimates whether model switching has enough
    theoretical headroom to justify a causal gate; it is never ranked as a
    deployable candidate.
    """
    by_day: dict[date, list[PredictionRecord]] = {}
    for record in records:
        by_day.setdefault(record.row.day, []).append(record)

    chosen: dict[date, str] = {}
    for day, day_records in by_day.items():
        models = ("baseline", *MODEL_NAMES)
        chosen[day] = min(models, key=lambda model: _mae_for_model(day_records, model))

    periods: dict[str, Any] = {}
    for period_name, (start, end) in core.PERIODS.items():
        selected = [record for record in records if start <= record.row.day <= end]
        mae = core._mae(
            (record.predictions[chosen[record.row.day]], record.row.actual)
            for record in selected
        )
        baseline = _mae_for_model(selected, "baseline")
        periods[period_name] = {
            "samples": len(selected),
            "mae_kwh": mae,
            "baseline_mae_kwh": baseline,
            "improvement_percent": (
                100.0 * (baseline - mae) / baseline if baseline else 0.0
            ),
            "model_day_counts": dict(
                sorted(Counter(chosen[record.row.day] for record in selected).items())
            ),
        }
    return {"name": "noncausal_oracle_by_day", "periods": periods}


def evaluate(rows: dict[tuple[date, int], core.Row]) -> dict[str, Any]:
    records = build_prediction_records(rows)
    target_records = [
        record
        for record in records
        if core.PERIODS["all"][0] <= record.row.day <= core.PERIODS["all"][1]
    ]
    selectors = [
        evaluate_selector(
            records,
            window_days=window_days,
            margin=margin,
            granularity=granularity,
        )
        for granularity in GRANULARITIES
        for window_days in SELECTOR_WINDOWS
        for margin in MARGINS
    ]
    selectors.sort(key=lambda item: item["periods"]["all"]["mae_kwh"])
    robust = sorted(
        selectors,
        key=lambda item: (
            -min(
                item["periods"]["august"]["improvement_percent"],
                item["periods"]["recent_14d"]["improvement_percent"],
            ),
            item["periods"]["all"]["mae_kwh"],
        ),
    )
    noninferior = [
        item
        for item in selectors
        if item["periods"]["august"]["improvement_percent"] >= 0.0
        and item["periods"]["recent_14d"]["improvement_percent"] >= 0.0
    ]
    return {
        "candidate_models": evaluate_static_models(target_records),
        "selector_count": len(selectors),
        "best_all": selectors[:10],
        "best_recent_robustness": robust[:10],
        "recent_noninferior": noninferior[:10],
        "oracle": oracle_by_day(target_records),
        "all_selectors": selectors,
    }


def compact(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_models": result["candidate_models"],
        "selector_count": result["selector_count"],
        "best_all": result["best_all"][:5],
        "best_recent_robustness": result["best_recent_robustness"][:5],
        "recent_noninferior": result["recent_noninferior"][:5],
        "oracle": result["oracle"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=core.DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows, dataset_meta = core.load_rows(args.dataset)
    result = evaluate(rows)
    result["dataset"] = dataset_meta
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "ADAPTIVE_GATE_SUMMARY_JSON="
        + json.dumps(compact(result), sort_keys=True, separators=(",", ":"))
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
