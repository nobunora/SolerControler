from __future__ import annotations

import csv
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

from app.soc_cost_optimizer import SocCostModel
from app.utils import env_bool, env_float, parse_csv_float, to_float


MODEL_VERSION = 1


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result


def _nested(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return default
        current = current.get(part)
    return default if current is None else current


def _actual_hourly_from_csvs(csv_paths: list[Path], *, target_date: str) -> dict[str, Any]:
    pv_by_hour: dict[int, float] = {}
    load_by_hour: dict[int, float] = {}
    row_count = 0
    latest_hhmm = ""
    for csv_path in csv_paths:
        if not csv_path.exists():
            continue
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            try:
                sample = handle.read(4096)
                handle.seek(0)
                dialect = csv.Sniffer().sniff(sample)
            except csv.Error:
                dialect = csv.excel
            for row in csv.DictReader(handle, dialect=dialect):
                raw_day = str(row.get("年月日") or "").strip().replace("/", "-")
                if raw_day != target_date:
                    continue
                hhmm = str(row.get("時刻") or "").strip()
                if ":" not in hhmm:
                    continue
                try:
                    hour = int(hhmm.split(":", 1)[0])
                except ValueError:
                    continue
                if hour < 7 or hour >= 23:
                    continue
                pv_by_hour[hour] = pv_by_hour.get(hour, 0.0) + float(
                    parse_csv_float(row.get("発電電力量[kWh]"), default=0.0) or 0.0
                )
                load_by_hour[hour] = load_by_hour.get(hour, 0.0) + float(
                    parse_csv_float(row.get("消費電力量[kWh]"), default=0.0) or 0.0
                )
                row_count += 1
                latest_hhmm = max(latest_hhmm, hhmm)
    return {
        "pv_by_hour": pv_by_hour,
        "load_by_hour": load_by_hour,
        "row_count": row_count,
        "active_hours": sorted(set(pv_by_hour) | set(load_by_hour)),
        "latest_hhmm": latest_hhmm,
    }


def _cost_model_from_plan(plan: dict[str, Any]) -> SocCostModel:
    source = _nested(plan, "daytime_soc_optimization.cost_model", {})
    if not isinstance(source, dict):
        source = {}
    return SocCostModel(
        day_buy_rate_yen_per_kwh=_finite_float(source.get("day_buy_rate_yen_per_kwh"), 39.10),
        night_buy_rate_yen_per_kwh=_finite_float(source.get("night_buy_rate_yen_per_kwh"), 28.85),
        charge_efficiency=max(0.01, _finite_float(source.get("charge_efficiency"), 0.93)),
        sell_value_ratio=_finite_float(source.get("sell_value_ratio"), 0.75),
        day_buy_penalty_factor=_finite_float(source.get("day_buy_penalty_factor"), 1.0),
        sell_opportunity_loss_yen_per_kwh_override=(
            to_float(source.get("sell_opportunity_loss_yen_per_kwh_override"))
        ),
        export_value_mode=str(source.get("export_value_mode") or "opportunity"),
        sell_revenue_yen_per_kwh=_finite_float(source.get("sell_revenue_yen_per_kwh"), 0.0),
        tariff_mode=str(source.get("tariff_mode") or "flat"),
        monthly_day_buy_kwh_before_target=_finite_float(source.get("monthly_day_buy_kwh_before_target"), 0.0),
        day_tier1_upper_kwh=_finite_float(source.get("day_tier1_upper_kwh"), 90.0),
        day_tier2_upper_kwh=_finite_float(source.get("day_tier2_upper_kwh"), 230.0),
        day_tier1_rate_yen_per_kwh=_finite_float(source.get("day_tier1_rate_yen_per_kwh"), 31.80),
        day_tier2_rate_yen_per_kwh=_finite_float(source.get("day_tier2_rate_yen_per_kwh"), 39.10),
        day_tier3_rate_yen_per_kwh=_finite_float(source.get("day_tier3_rate_yen_per_kwh"), 43.62),
        monthly_tier_landing_enabled=bool(source.get("monthly_tier_landing_enabled", False)),
        expected_rest_of_month_day_buy_kwh=_finite_float(source.get("expected_rest_of_month_day_buy_kwh"), 0.0),
        tier1_underuse_penalty_yen_per_kwh=_finite_float(source.get("tier1_underuse_penalty_yen_per_kwh"), 0.0),
        tier1_crossing_penalty_yen_per_kwh=_finite_float(source.get("tier1_crossing_penalty_yen_per_kwh"), 30.0),
        tier2_extra_penalty_yen_per_kwh=_finite_float(source.get("tier2_extra_penalty_yen_per_kwh"), 8.0),
        tier3_extra_penalty_yen_per_kwh=_finite_float(source.get("tier3_extra_penalty_yen_per_kwh"), 20.0),
    )


def _simulate_actual_day(
    *,
    target_soc_percent: float,
    capacity_kwh: float,
    soc_now_percent: float,
    hourly_pv_kwh: dict[int, float],
    hourly_load_kwh: dict[int, float],
    cost_model: SocCostModel,
    peak_target_soc_percent: float,
    peak_unmet_rate_yen_per_kwh: float,
) -> dict[str, float]:
    target_soc = max(0.0, min(100.0, target_soc_percent))
    target_energy = capacity_kwh * target_soc / 100.0
    current_energy = capacity_kwh * max(0.0, min(100.0, soc_now_percent)) / 100.0
    required_charge_kwh = max(0.0, (target_energy - current_energy) / max(0.01, cost_model.charge_efficiency))
    energy = max(0.0, min(capacity_kwh, target_energy))
    buy_kwh = 0.0
    sell_kwh = 0.0
    max_energy = energy
    soc_18 = 0.0
    soc_21 = 0.0
    for hour in range(7, 23):
        net = max(0.0, hourly_pv_kwh.get(hour, 0.0)) - max(0.0, hourly_load_kwh.get(hour, 0.0))
        if net >= 0.0:
            charge = min(capacity_kwh - energy, net)
            energy += charge
            sell_kwh += max(0.0, net - charge)
        else:
            need = -net
            discharge = min(energy, need)
            energy -= discharge
            buy_kwh += max(0.0, need - discharge)
        max_energy = max(max_energy, energy)
        soc = 100.0 * energy / capacity_kwh if capacity_kwh > 0.0 else 0.0
        if hour == 18:
            soc_18 = soc
        if hour == 21:
            soc_21 = soc

    max_soc = 100.0 * max_energy / capacity_kwh if capacity_kwh > 0.0 else 0.0
    end_soc = 100.0 * energy / capacity_kwh if capacity_kwh > 0.0 else 0.0
    peak_unmet_kwh = max(0.0, peak_target_soc_percent - max_soc) * capacity_kwh / 100.0
    objective_yen = (
        required_charge_kwh * cost_model.night_buy_rate_yen_per_kwh
        + cost_model.day_buy_cost_yen(buy_kwh)
        + sell_kwh * cost_model.sell_opportunity_loss_yen_per_kwh
        + peak_unmet_kwh * max(0.0, peak_unmet_rate_yen_per_kwh)
    )
    return {
        "target_soc_percent": target_soc,
        "required_night_charge_kwh": required_charge_kwh,
        "day_buy_kwh": buy_kwh,
        "sell_kwh": sell_kwh,
        "max_soc_percent": max_soc,
        "soc_18_percent": soc_18,
        "soc_21_percent": soc_21,
        "end_soc_percent": end_soc,
        "peak_unmet_kwh": peak_unmet_kwh,
        "objective_yen": objective_yen,
    }


def build_soc_decision_feedback(
    *,
    plan: dict[str, Any],
    csv_paths: list[Path],
    target_date: str,
    created_at: str | None = None,
    min_rows: int | None = None,
    step_percent: float | None = None,
) -> dict[str, Any] | None:
    """Build the realized target-SOC regret curve for one completed day."""

    capacity = _finite_float(_nested(plan, "result.effective_capacity_kwh"), 0.0)
    if capacity <= 0.0:
        return None
    actual = _actual_hourly_from_csvs(csv_paths, target_date=target_date)
    rows = int(actual["row_count"])
    if rows < (min_rows if min_rows is not None else int(env_float("SOC_DECISION_FEEDBACK_MIN_ROWS", default=24.0))):
        return None
    active_hours = list(actual["active_hours"])
    if len(active_hours) < 8:
        return None

    cost_model = _cost_model_from_plan(plan)
    soc_now = _finite_float(_nested(plan, "inputs.soc_now_percent"), 0.0)
    peak_target = _finite_float(
        _nested(plan, "daytime_soc_optimization.forecast_correction.soc_peak_unmet_penalty.target_peak_soc_percent"),
        95.0,
    )
    expected_peak_unmet = _finite_float(_nested(plan, "daytime_soc_optimization.expected_peak_unmet_kwh"), 0.0)
    expected_peak_cost = _finite_float(_nested(plan, "daytime_soc_optimization.expected_peak_unmet_cost_yen"), 0.0)
    peak_rate = expected_peak_cost / expected_peak_unmet if expected_peak_unmet > 0.0 else cost_model.day_buy_rate_yen_per_kwh
    step = max(
        1.0,
        min(
            20.0,
            step_percent if step_percent is not None else env_float("SOC_DECISION_FEEDBACK_STEP_PERCENT", default=1.0),
        ),
    )

    targets: list[float] = []
    cursor = 0.0
    while cursor <= 100.0 + 1e-9:
        targets.append(round(min(100.0, cursor), 4))
        cursor += step
    if targets[-1] < 100.0:
        targets.append(100.0)

    points = [
        _simulate_actual_day(
            target_soc_percent=target,
            capacity_kwh=capacity,
            soc_now_percent=soc_now,
            hourly_pv_kwh=actual["pv_by_hour"],
            hourly_load_kwh=actual["load_by_hour"],
            cost_model=cost_model,
            peak_target_soc_percent=peak_target,
            peak_unmet_rate_yen_per_kwh=peak_rate,
        )
        for target in targets
    ]
    best = min(points, key=lambda item: (item["objective_yen"], item["target_soc_percent"]))
    min_objective = float(best["objective_yen"])
    point_payload = [
        {
            **{key: round(float(value), 4) for key, value in point.items()},
            "regret_yen": round(max(0.0, float(point["objective_yen"]) - min_objective), 4),
        }
        for point in points
    ]
    return {
        "date": target_date,
        "model_version": MODEL_VERSION,
        "source": "kpnet_actual_soc_counterfactual",
        "created_at": created_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "capacity_kwh": round(capacity, 4),
        "soc_now_percent": round(soc_now, 4),
        "best_target_soc_percent": round(float(best["target_soc_percent"]), 4),
        "min_objective_yen": round(min_objective, 4),
        "points": point_payload,
        "actual_summary": {
            "row_count": rows,
            "active_hours": active_hours,
            "latest_hhmm": actual["latest_hhmm"],
            "pv_kwh": round(sum(actual["pv_by_hour"].values()), 4),
            "load_kwh": round(sum(actual["load_by_hour"].values()), 4),
        },
        "plan_reference": {
            "target_soc_7_percent": to_float(_nested(plan, "result.target_soc_7_percent")),
            "required_night_charge_kwh": to_float(_nested(plan, "result.required_night_charge_kwh")),
            "final_predicted_pv_kwh": to_float(_nested(plan, "result.final_predicted_pv_kwh")),
            "expected_day_buy_kwh": to_float(_nested(plan, "daytime_soc_optimization.expected_day_buy_kwh")),
            "expected_sell_kwh": to_float(_nested(plan, "daytime_soc_optimization.expected_sell_kwh")),
        },
    }


def _regret_map_from_doc(doc: dict[str, Any]) -> dict[float, float]:
    out: dict[float, float] = {}
    points = doc.get("points")
    if not isinstance(points, list):
        return out
    for item in points:
        if not isinstance(item, dict):
            continue
        target = to_float(item.get("target_soc_percent"))
        regret = to_float(item.get("regret_yen"))
        if target is None or regret is None:
            continue
        out[round(max(0.0, min(100.0, target)), 4)] = max(0.0, regret)
    return out


def build_soc_decision_prior(
    feedback_docs: list[dict[str, Any]],
    *,
    target_date: str,
) -> dict[str, Any]:
    if not env_bool("SOC_DECISION_FEEDBACK_ENABLED", default=True):
        return {"enabled": False, "applied": False, "reason": "disabled"}
    try:
        target_day = date.fromisoformat(target_date)
    except ValueError:
        return {"enabled": True, "applied": False, "reason": "invalid_target_date"}

    lookback_days = max(1, int(env_float("SOC_DECISION_FEEDBACK_LOOKBACK_DAYS", default=7.0)))
    min_day = target_day - timedelta(days=lookback_days)
    usable: list[dict[str, Any]] = []
    for doc in feedback_docs:
        day_text = str(doc.get("date") or "").strip()
        try:
            day = date.fromisoformat(day_text)
        except ValueError:
            continue
        if min_day <= day < target_day and _regret_map_from_doc(doc):
            usable.append(doc)
    usable.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
    if not usable:
        return {"enabled": True, "applied": False, "reason": "no_recent_feedback"}

    decay = max(0.1, min(1.0, env_float("SOC_DECISION_FEEDBACK_RECENCY_DECAY", default=0.75)))
    weighted: dict[float, tuple[float, float]] = {}
    best_targets: list[float] = []
    source_dates: list[str] = []
    for index, doc in enumerate(usable):
        weight = decay ** index
        source_dates.append(str(doc.get("date") or ""))
        best = to_float(doc.get("best_target_soc_percent"))
        if best is not None:
            best_targets.append(best)
        for target, regret in _regret_map_from_doc(doc).items():
            total, total_weight = weighted.get(target, (0.0, 0.0))
            weighted[target] = (total + regret * weight, total_weight + weight)

    if not weighted:
        return {"enabled": True, "applied": False, "reason": "empty_regret_curve"}

    confidence_days = max(0.1, env_float("SOC_DECISION_FEEDBACK_CONFIDENCE_DAYS", default=2.0))
    confidence = len(usable) / (len(usable) + confidence_days)
    base_weight = max(0.0, env_float("SOC_DECISION_FEEDBACK_WEIGHT", default=0.35))
    applied_weight = base_weight * confidence
    max_penalty_yen = max(0.0, env_float("SOC_DECISION_FEEDBACK_MAX_PENALTY_YEN", default=30.0))
    return {
        "enabled": True,
        "applied": applied_weight > 0.0 and max_penalty_yen > 0.0,
        "method": "recent_realized_soc_regret_curve",
        "sample_count": len(usable),
        "confidence": round(confidence, 6),
        "weight": round(applied_weight, 6),
        "base_weight": round(base_weight, 6),
        "max_penalty_yen": round(max_penalty_yen, 4),
        "target_date": target_date,
        "source_dates": source_dates,
        "best_target_soc_percent_median": round(float(median(best_targets)), 4) if best_targets else None,
        "regret_yen_by_soc": {
            str(int(target) if abs(target - round(target)) < 1e-9 else target): round(total / total_weight, 4)
            for target, (total, total_weight) in sorted(weighted.items())
            if total_weight > 0.0
        },
    }


def load_soc_decision_prior_from_firestore(*, target_date: str) -> dict[str, Any]:
    if not env_bool("SOC_DECISION_FEEDBACK_ENABLED", default=True):
        return {"enabled": False, "applied": False, "reason": "disabled"}
    backend = os.getenv("DATA_BACKEND", "").strip().lower()
    project_id = os.getenv("FIRESTORE_PROJECT_ID", "").strip()
    if backend != "firestore" and not project_id:
        return {"enabled": True, "applied": False, "reason": "firestore_not_configured"}
    try:
        from google.cloud import firestore

        database_id = os.getenv("FIRESTORE_DATABASE_ID", "").strip() or "(default)"
        client = firestore.Client(project=project_id, database=database_id) if project_id else firestore.Client(database=database_id)
        docs = [doc.to_dict() or {} for doc in client.collection("soc_decision_feedback").stream()]
    except Exception as exc:
        return {"enabled": True, "applied": False, "reason": f"firestore_error:{exc}"}
    return build_soc_decision_prior(docs, target_date=target_date)
