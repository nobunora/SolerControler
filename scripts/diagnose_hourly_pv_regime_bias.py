"""Compare simple recent same-hour bias filters with weather-vector analogs.

This companion diagnostic intentionally ignores weather-vector similarity. It
asks whether recent forecast residuals contain a broad regime bias that is more
predictable than residuals selected by the current analog rule.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any

import diagnose_hourly_pv_correction_limits as core

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts/analysis/hourly_pv_regime_bias_diagnostic.json"
LOOKBACKS = (3, 7, 14, 21, 30, 45)
HALF_LIVES = (None, 3.0, 7.0, 14.0)


def history(
    target: core.Row,
    rows: dict[tuple[date, int], core.Row],
    lookback: int,
) -> list[tuple[core.Row, int]]:
    values: list[tuple[core.Row, int]] = []
    for age in range(1, lookback + 1):
        prior = rows.get((target.day - timedelta(days=age), target.hour))
        if prior is not None and prior.forecast > 0.0:
            values.append((prior, age))
    return values


def weighted_location(
    values: list[tuple[float, int]],
    half_life: float | None,
) -> float:
    if not values:
        return 0.0
    if half_life is None:
        return median(value for value, _ in values)
    weighted = [
        (value, math.exp(-math.log(2.0) * age / half_life))
        for value, age in values
    ]
    return core._weighted_median(weighted)


def predict(
    target: core.Row,
    rows: dict[tuple[date, int], core.Row],
    *,
    lookback: int,
    half_life: float | None,
    residual_kind: str,
) -> float:
    prior = history(target, rows, lookback)
    if len(prior) < 2:
        return target.forecast
    if residual_kind == "additive":
        center = weighted_location(
            [(row.additive_residual, age) for row, age in prior],
            half_life,
        )
        return max(0.0, target.forecast + center)
    if residual_kind == "log_ratio":
        center = weighted_location(
            [(row.log_residual, age) for row, age in prior],
            half_life,
        )
        return max(
            0.0,
            (target.forecast + core.EPS_KWH) * math.exp(center) - core.EPS_KWH,
        )
    raise ValueError(residual_kind)


def evaluate(rows: dict[tuple[date, int], core.Row]) -> dict[str, Any]:
    targets = core.target_rows(
        rows,
        core.PERIODS["all"][0],
        core.PERIODS["all"][1],
    )
    baseline = {
        name: core._mae(
            (row.forecast, row.actual)
            for row in targets
            if start <= row.day <= end
        )
        for name, (start, end) in core.PERIODS.items()
    }
    variants: list[dict[str, Any]] = []
    for lookback in LOOKBACKS:
        for half_life in HALF_LIVES:
            for residual_kind in ("additive", "log_ratio"):
                predictions = [
                    (
                        target,
                        predict(
                            target,
                            rows,
                            lookback=lookback,
                            half_life=half_life,
                            residual_kind=residual_kind,
                        ),
                    )
                    for target in targets
                ]
                recency = "none" if half_life is None else f"hl{int(half_life)}d"
                variants.append(
                    core._variant_result(
                        f"same_hour_bias_lb{lookback}_{residual_kind}_{recency}",
                        predictions,
                        baseline,
                    )
                )
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
    august = sorted(
        variants,
        key=lambda item: item["periods"]["august"]["mae_kwh"],
    )
    recent = sorted(
        variants,
        key=lambda item: item["periods"]["recent_14d"]["mae_kwh"],
    )
    return {
        "variant_count": len(variants),
        "best_all": variants[:10],
        "best_august": august[:10],
        "best_recent_14d": recent[:10],
        "best_recent_robustness": robust[:10],
        "all_variants": variants,
    }


def compact(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "variant_count": result["variant_count"],
        "best_all": result["best_all"][:5],
        "best_august": result["best_august"][:5],
        "best_recent_14d": result["best_recent_14d"][:5],
        "best_recent_robustness": result["best_recent_robustness"][:5],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=core.DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows, _ = core.load_rows(args.dataset)
    result = evaluate(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "REGIME_BIAS_SUMMARY_JSON="
        + json.dumps(compact(result), sort_keys=True, separators=(",", ":"))
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
