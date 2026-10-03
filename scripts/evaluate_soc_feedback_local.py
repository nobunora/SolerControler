"""Strict local feedback replay; missing adopted plans never enter learning."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.energy_plan.local_replay import evaluate_replay_day
from app.energy_plan.soc_cost import SocCostModel


def finite_nonnegative(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def valid_scenarios(scenarios: Any) -> bool:
    if not isinstance(scenarios, list) or not scenarios:
        return False
    if any(not isinstance(s, dict) or set(s) != {"label", "probability", "pv_multiplier", "load_multiplier"}
           or not isinstance(s["label"], str) or not s["label"]
           or any(not finite_nonnegative(s[k]) for k in ("probability", "pv_multiplier", "load_multiplier")) for s in scenarios):
        return False
    return bool(abs(sum(s["probability"] for s in scenarios) - 1) <= 1e-6)


def plan_exclusion(plan: dict[str, Any], target_date: str) -> str | None:
    """Require explicit provenance; old parser defaults cannot prove completeness."""
    required = ("decision_id", "issued_at", "model_version", "contract_version", "price_version", "adopted_slot")
    if any(not plan.get(key) for key in required):
        return "plan_provenance_missing"
    if plan["adopted_slot"] not in {"03", "reconstructed_experiment"} or plan.get("date") != target_date:
        return "adopted_plan_missing"
    try:
        issue = datetime.fromisoformat(plan["issued_at"].replace("Z", "+00:00"))
        boundary = datetime.fromisoformat(target_date).replace(hour=7, tzinfo=ZoneInfo("Asia/Tokyo"))
        decision = datetime.fromisoformat(plan["decision_at"].replace("Z", "+00:00"))
        if issue.utcoffset() is None or decision.utcoffset() is None or issue > decision or decision >= boundary:
            return "issue_invalid"
    except (KeyError, ValueError, TypeError):
        return "issue_invalid"
    optimization = plan.get("daytime_soc_optimization", {})
    cost = optimization.get("cost_model", {})
    if not isinstance(cost, dict) or any(field.name not in cost for field in fields(SocCostModel)):
        return "cost_model_incomplete"
    if set(cost) != {field.name for field in fields(SocCostModel)}:
        return "cost_model_invalid"
    for key, value in cost.items():
        if key in {"export_value_mode", "tariff_mode"}:
            continue
        if key in {"monthly_tariff_projection_enabled", "monthly_tier_landing_enabled"}:
            if type(value) is not bool:
                return "cost_model_invalid"
        elif value is None and key == "sell_opportunity_loss_yen_per_kwh_override":
            continue
        elif not finite_nonnegative(value):
            return "cost_model_invalid"
    if (cost["export_value_mode"] not in {"neutral", "penalty", "revenue", "opportunity"}
            or cost["tariff_mode"] not in {"flat", "night8_tiered"}
            or not 0 < cost["charge_efficiency"] <= 1
            or cost["day_tier2_upper_kwh"] < cost["day_tier1_upper_kwh"]):
        return "cost_model_invalid"
    for name in ("hourly_pv_forecast_kwh", "hourly_load_forecast_kwh"):
        series = optimization.get(name, {})
        if not isinstance(series, dict) or not {str(h) for h in range(7, 23)} <= {str(k) for k in series}:
            return "forecast_incomplete"
        if any(not finite_nonnegative(v) for v in series.values()):
            return "forecast_invalid"
    for block, name in (("inputs", "soc_now_percent"), ("result", "target_soc_7_percent"), ("result", "effective_capacity_kwh")):
        value = plan.get(block, {}).get(name)
        if not finite_nonnegative(value):
            return "decision_input_missing"
    if (plan["result"]["effective_capacity_kwh"] <= 0 or plan["inputs"]["soc_now_percent"] > 100
            or plan["result"]["target_soc_7_percent"] > 100
            or not finite_nonnegative(plan["inputs"].get("expected_overnight_discharge_kwh"))
            or not finite_nonnegative(plan.get("terminal_value_yen_per_kwh"))):
        return "decision_input_missing"
    if not optimization.get("constraints") or not optimization.get("candidate_grid"):
        return "constraints_or_grid_missing"
    grid = optimization["candidate_grid"]
    if not isinstance(grid, list) or any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 100 for v in grid):
        return "candidate_grid_invalid"
    bounds = optimization["constraints"]
    if (not isinstance(bounds, dict) or not finite_nonnegative(bounds.get("min"))
            or not finite_nonnegative(bounds.get("max")) or bounds["max"] > 100
            or any(not bounds["min"] <= v <= bounds["max"] for v in grid) or len(set(grid)) != len(grid)):
        return "candidate_grid_invalid"
    peak = optimization.get("peak_policy", {})
    if (type(peak.get("enabled")) is not bool or not finite_nonnegative(peak.get("target_percent"))
            or peak["target_percent"] > 100 or not finite_nonnegative(peak.get("rate_yen_per_kwh"))):
        return "peak_policy_missing"
    scenarios = optimization.get("forecast_scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        return "scenarios_missing"
    if not valid_scenarios(scenarios):
        return "scenarios_invalid"
    variant_scenarios = optimization.get("forecast_scenarios_by_variant")
    if variant_scenarios is not None:
        if (not isinstance(variant_scenarios, dict) or set(variant_scenarios) != set(optimization.get("pv_variants", {"baseline": None}))
                or any(not valid_scenarios(s) for s in variant_scenarios.values())):
            return "variant_scenarios_invalid"
    for series in optimization.get("pv_variants", {}).values():
        if (not isinstance(series, dict) or set(map(str, series)) != set(map(str, optimization["hourly_pv_forecast_kwh"]))
                or any(not finite_nonnegative(v) for v in series.values())):
            return "forecast_invalid"
    return None


def replay(plans: list[dict[str, Any]], csv_paths: list[Path]) -> dict[str, Any]:
    """Rebuild all dependent outputs on each invocation, preserving original inputs."""
    actual_version = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(csv_paths))).hexdigest()
    outputs: list[dict[str, Any]] = []
    documents: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    seen: set[str] = set()
    seen_days: set[tuple[tuple[str, ...], str]] = set()
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
        partition = (str(plan["model_version"]), str(plan["contract_version"]), str(plan["price_version"]))
        if (partition, day) in seen_days:
            raise ValueError("duplicate target date within model/contract/price partition")
        seen_days.add((partition, day))
        history = documents.setdefault(partition, [])
        labels: list[str] = []
        actual: dict[str, dict[int, float]] = {"pv": {}, "load": {}}
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
                for kind, field in (("pv", "発電電力量[kWh]"), ("load", "消費電力量[kWh]")):
                    try:
                        value = float(row[field])
                        invalid |= not math.isfinite(value) or value < 0
                        hour = int(labels[-1].split(":")[0])
                        if 7 <= hour < 23:
                            actual[kind][hour] = actual[kind].get(hour, 0) + value
                    except (KeyError, TypeError, ValueError):
                        invalid = True
        expected = [f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 30)]
        if invalid or sorted(labels) != expected:
            outputs.append({"date": day, "eligible": False, "reason": "actual_incomplete"})
            continue
        try:
            evaluated = evaluate_replay_day(plan, actual, history)
        except ValueError as error:
            if str(error) != "no_reachable_candidate":
                raise
            outputs.append({"date": day, "eligible": False, "reason": str(error)})
            continue
        history.append(evaluated["feedback"])
        outputs.append({"date": day, "decision_id": identity, "actual_version": actual_version,
                        "eligible": True, **evaluated,
                        "created_at_basis": "reconstructed_day_complete_boundary", "validated_source_intervals": 48})
    return {"schema_version": 2, "production_connected": False, "actual_version": actual_version,
            "basis": "paired conditional-day one-hour model without power limits; not a continuous bill replay", "records": outputs}


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
