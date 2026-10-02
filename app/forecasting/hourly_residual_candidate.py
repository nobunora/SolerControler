"""Offline additive residual candidate; never selected by the production planner."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any


def evaluate_hourly_residuals(records: list[dict[str, Any]], *, alpha: float) -> dict[str, Any]:
    """Walk forward with prior-day state, partitioned by baseline version and kind.

    Callers must supply verified complete half-hour pairs and frozen/reconstructed
    baseline provenance. A reconstructed run is never prospective adoption evidence.
    """
    if not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError("alpha must be finite and between zero and one")
    seen: set[tuple[str, str, str, int]] = set()
    state: dict[tuple[str, str, int], float] = {}
    output: list[dict[str, Any]] = []
    for row in sorted(records, key=lambda item: (item["date"], item["hour"])):
        day = date.fromisoformat(row["date"])
        hour = int(row["hour"])
        version = row.get("baseline_version")
        kind = row.get("kind")
        reason = None
        try:
            issue = datetime.fromisoformat(row["issued_at"].replace("Z", "+00:00"))
            target = datetime.fromisoformat(row["target_at"].replace("Z", "+00:00"))
            if issue.utcoffset() is None or target.utcoffset() is None or issue >= target:
                reason = "issue_not_before_target"
            elif target.date() != day or target.hour != hour:
                reason = "target_mismatch"
        except (KeyError, TypeError, ValueError):
            reason = "issue_or_target_missing"
        if not version or kind not in {"pv", "load"} or not 0 <= hour <= 23:
            reason = "version_or_kind_missing"
        if row.get("basis") not in {"reconstructed", "prospective"}:
            reason = "basis_missing"
        q, actual = row.get("q"), row.get("actual")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in (q, actual)):
            reason = "nonfinite_or_missing"
        if row.get("actual_interval_minutes") != [0, 30]:
            reason = "incomplete_actual_hour"
        identity = (str(version), str(kind), row["date"], hour)
        if identity in seen:
            raise ValueError("duplicate target hour: select one frozen issue before evaluation")
        seen.add(identity)
        if reason:
            output.append({**row, "eligible": False, "excluded_reason": reason})
            continue
        key = (str(version) + ":" + row["basis"], str(kind), hour)
        prior = state.get(key, 0.0)
        candidate = max(0.0, q + prior)
        residual = actual - q
        state[key] = (1 - alpha) * prior + alpha * residual if key in state else residual
        output.append({**row, "eligible": True, "prior_bias": prior, "candidate": candidate,
                       "residual": residual, "updated_bias": state[key]})
    valid = [r for r in output if r["eligible"]]
    metrics = {}
    for name in ("q", "candidate"):
        errors = [r[name] - r["actual"] for r in valid]
        daily: dict[str, float] = {}
        for row in valid:
            daily[row["date"]] = daily.get(row["date"], 0.0) + row[name] - row["actual"]
        longest = streak = 0
        previous: date | None = None
        for day, error in sorted(daily.items()):
            current = date.fromisoformat(day)
            consecutive = previous is not None and (current - previous).days == 1
            streak = (streak + 1 if consecutive else 1) if error < 0 else 0
            longest = max(longest, streak)
            previous = current
        metrics[name] = {"mae": sum(abs(e) for e in errors) / len(errors) if errors else None,
                         "rmse": math.sqrt(sum(e * e for e in errors) / len(errors)) if errors else None,
                         "bias": sum(errors) / len(errors) if errors else None,
                         "longest_underprediction_days": longest, "daily_error": daily}
    return {"schema_version": 1, "alpha": alpha, "hours": len(valid),
            "days": len({r["date"] for r in valid}), "metrics": metrics, "records": output,
            "production_connected": False,
            "prospective_days": len({r["date"] for r in valid if r["basis"] == "prospective"})}
