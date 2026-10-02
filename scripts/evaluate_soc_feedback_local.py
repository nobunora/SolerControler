"""Strict local feedback replay; missing adopted plans never enter learning."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.energy_plan.decision_feedback import build_soc_decision_feedback, build_soc_decision_prior
from app.energy_plan.soc_cost import SocCostModel


def plan_exclusion(plan: dict[str, Any], target_date: str) -> str | None:
    """Require explicit provenance; old parser defaults cannot prove completeness."""
    required = ("decision_id", "issued_at", "model_version", "contract_version", "price_version", "adopted_slot")
    if any(not plan.get(key) for key in required):
        return "plan_provenance_missing"
    if plan["adopted_slot"] != "03" or plan.get("date") != target_date:
        return "adopted_plan_missing"
    try:
        issue = datetime.fromisoformat(plan["issued_at"].replace("Z", "+00:00"))
        if issue.utcoffset() is None or issue.astimezone(ZoneInfo("Asia/Tokyo")).date().isoformat() != target_date:
            return "issue_invalid"
    except (ValueError, TypeError):
        return "issue_invalid"
    optimization = plan.get("daytime_soc_optimization", {})
    cost = optimization.get("cost_model", {})
    if not isinstance(cost, dict) or any(field.name not in cost for field in fields(SocCostModel)):
        return "cost_model_incomplete"
    for name in ("hourly_pv_forecast_kwh", "hourly_load_forecast_kwh"):
        series = optimization.get(name, {})
        if not isinstance(series, dict) or {str(k) for k in series} != {str(h) for h in range(24)}:
            return "forecast_incomplete"
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in series.values()):
            return "forecast_invalid"
    for block, name in (("inputs", "soc_now_percent"), ("result", "target_soc_7_percent"), ("result", "effective_capacity_kwh")):
        value = plan.get(block, {}).get(name)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return "decision_input_missing"
    if not optimization.get("constraints") or not optimization.get("candidate_grid"):
        return "constraints_or_grid_missing"
    grid = optimization["candidate_grid"]
    if not isinstance(grid, list) or any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 100 for v in grid):
        return "candidate_grid_invalid"
    peak = optimization.get("forecast_correction", {}).get("soc_peak_unmet_penalty", {}).get("target_peak_soc_percent")
    if not isinstance(peak, (int, float)) or not math.isfinite(peak):
        return "peak_policy_missing"
    for field in ("expected_peak_unmet_kwh", "expected_peak_unmet_cost_yen"):
        value = optimization.get(field)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            return "peak_policy_missing"
    return None


def replay(plans: list[dict[str, Any]], csv_paths: list[Path]) -> dict[str, Any]:
    """Rebuild all dependent outputs on each invocation, preserving original inputs."""
    actual_version = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(csv_paths))).hexdigest()
    outputs: list[dict[str, Any]] = []
    documents: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for plan in sorted(plans, key=lambda p: str(p.get("date", ""))):
        day = str(plan.get("date", ""))
        reason = plan_exclusion(plan, day)
        if reason:
            outputs.append({"date": day, "eligible": False, "reason": reason})
            continue
        identity = str(plan["decision_id"])
        if identity in seen:
            raise ValueError("duplicate adopted decision")
        seen.add(identity)
        partition = tuple(str(plan[key]) for key in ("model_version", "contract_version", "price_version"))
        history = documents.setdefault(partition, [])
        labels: list[str] = []
        invalid = False
        for path in csv_paths:
            try:
                raw = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                raw = path.read_text(encoding="cp932")
            for row in csv.DictReader(raw.splitlines()):
                if row.get("年月日", "").strip().replace("/", "-") != day:
                    continue
                labels.append(row.get("時刻", "").strip())
                for field in ("発電電力量[kWh]", "消費電力量[kWh]"):
                    try:
                        value = float(row[field])
                        invalid |= not math.isfinite(value) or value < 0
                    except (KeyError, TypeError, ValueError):
                        invalid = True
        expected = [f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 30)]
        if invalid or sorted(labels) != expected:
            outputs.append({"date": day, "eligible": False, "reason": "actual_incomplete"})
            continue
        prior = build_soc_decision_prior(feedback_docs=history, target_date=day)
        feedback = build_soc_decision_feedback(plan=plan, csv_paths=csv_paths, target_date=day,
                                               created_at=(datetime.fromisoformat(day) + timedelta(days=1)).replace(tzinfo=ZoneInfo("Asia/Tokyo")).isoformat(),
                                               min_rows=32, step_percent=1.0)
        # The existing simulator counts only 07:00-22:30 (32 intervals).
        # All 48 source intervals were separately validated above.
        if feedback is None or feedback["actual_summary"]["row_count"] != 32:
            outputs.append({"date": day, "eligible": False, "reason": "actual_incomplete"})
            continue
        grid = set(plan["daytime_soc_optimization"]["candidate_grid"])
        points = [point for point in feedback["points"] if point["target_soc_percent"] in grid]
        if {point["target_soc_percent"] for point in points} != grid:
            outputs.append({"date": day, "eligible": False, "reason": "candidate_grid_not_replayable"})
            continue
        best = min(points, key=lambda point: (point["objective_yen"], point["target_soc_percent"]))
        minimum = best["objective_yen"]
        feedback["best_target_soc_percent"] = best["target_soc_percent"]
        feedback["min_objective_yen"] = minimum
        feedback["points"] = [{**point, "regret_yen": round(max(0.0, point["objective_yen"] - minimum), 4)} for point in points]
        history.append(feedback)
        outputs.append({"date": day, "decision_id": identity, "actual_version": actual_version,
                        "eligible": True, "prior": prior, "feedback": feedback,
                        "created_at_basis": "reconstructed_day_complete_boundary", "validated_source_intervals": 48})
    return {"schema_version": 1, "production_connected": False, "actual_version": actual_version,
            "basis": "retrospective one-hour model without power limits", "records": outputs}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plans", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--csv", type=Path, action="append", required=True)
    args = parser.parse_args()
    result = replay(json.loads(args.plans.read_text(encoding="utf-8-sig")), args.csv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output.exists():
        digest = hashlib.sha256(args.output.read_bytes()).hexdigest()[:16]
        args.output.with_suffix(f".previous-{digest}.json").write_bytes(args.output.read_bytes())
    args.output.write_text(payload, encoding="utf-8")
    print(json.dumps({"days": len(result["records"]), "eligible": sum(r["eligible"] for r in result["records"]), "production_connected": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
