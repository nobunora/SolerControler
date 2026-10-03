"""Past-only paired error scenarios for reconstructed offline SOC experiments."""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any


def recalibrate_scenarios(
    observations: list[dict[str, Any]], *, target_date: str,
    variants: tuple[str, ...], lookback_days: int = 14, prior_weight: float = 5,
) -> dict[str, Any]:
    """Preserve same-day PV/load dependence and shared sample coverage.

    Observations contain precomputed, past-only predictions for each variant.
    Actual zero is valid; a nonpositive forecast denominator is not a zero error.
    No current/future actual is read, and no coefficient is fitted to the target.
    """
    target = date.fromisoformat(target_date)
    if not variants or len(set(variants)) != len(variants) or lookback_days < 1 or not math.isfinite(prior_weight) or prior_weight <= 0:
        raise ValueError("invalid scenario configuration")
    oldest = target - timedelta(days=lookback_days)
    samples = []
    excluded = []
    seen = set()
    for row in sorted(observations, key=lambda r: r["date"]):
        day = date.fromisoformat(row["date"])
        if not oldest <= day < target:
            continue
        if day in seen:
            raise ValueError("duplicate historical day")
        seen.add(day)
        forecasts = row.get("pv_forecasts", {})
        actuals = [row.get("actual_pv"), row.get("actual_load")]
        denominators = [row.get("load_forecast"), *(forecasts.get(v) for v in variants)]
        valid_actual = all(isinstance(v, (int, float)) and type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in actuals)
        valid_forecast = all(isinstance(v, (int, float)) and type(v) in (int, float) and math.isfinite(v) and v > 0 for v in denominators)
        if not valid_actual or not valid_forecast:
            excluded.append({"date": row["date"], "reason": "invalid_actual" if not valid_actual else "invalid_forecast_denominator"})
            continue
        samples.append(row)
    total_weight = prior_weight + len(samples)
    by_variant = {}
    clipping = {}
    for variant in variants:
        scenarios = [{"label": "recalibrated_prior", "probability": prior_weight / total_weight,
                      "pv_multiplier": 1.0, "load_multiplier": 1.0}]
        clipped_dates = []
        for row in samples:
            ratio = row["actual_pv"] / row["pv_forecasts"][variant]
            # Same 0..3 PV bounds as evaluate_soc_candidate's explicit joint path.
            if ratio > 3:
                clipped_dates.append(row["date"])
            scenarios.append({"label": f"recalibrated_{row['date']}", "probability": 1 / total_weight,
                              "pv_multiplier": min(3.0, ratio), "load_multiplier": row["actual_load"] / row["load_forecast"]})
        by_variant[variant] = scenarios
        clipping[variant] = clipped_dates
    return {"scenarios_by_variant": by_variant, "source_dates": [r["date"] for r in samples],
            "excluded": excluded, "clipped_pv_dates": clipping,
            "lookback_days": lookback_days, "prior_weight": prior_weight,
            "basis": "past-only reconstructed paired daily multipliers",
            "fallback": "point_prior_only" if not samples else None}
