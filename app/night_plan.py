from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class NightPlanResult:
    target_soc_7_percent: float
    required_night_charge_kwh: float
    predicted_midday_surplus_kwh: float | None


@dataclass(frozen=True)
class NightPlan:
    forecast_date: date
    result: NightPlanResult
    should_apply: bool | None
    raw: dict[str, Any]


def _finite_float(source: dict[str, Any], key: str, *, required: bool) -> float | None:
    if key not in source:
        if required:
            raise ValueError(f"night plan is missing result.{key}")
        return None
    try:
        value = float(source[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"night plan result.{key} must be numeric") from exc
    if not math.isfinite(value):
        raise ValueError(f"night plan result.{key} must be finite")
    return value


def parse_night_plan(raw: dict[str, Any]) -> NightPlan:
    forecast = raw.get("forecast")
    result = raw.get("result")
    quality = raw.get("plan_quality")
    if not isinstance(forecast, dict) or not isinstance(result, dict):
        raise ValueError("night plan forecast and result must be objects")
    try:
        forecast_date = date.fromisoformat(str(forecast["date"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("night plan forecast.date must be YYYY-MM-DD") from exc
    target = _finite_float(result, "target_soc_7_percent", required=True)
    required = _finite_float(result, "required_night_charge_kwh", required=True)
    surplus = _finite_float(result, "predicted_midday_surplus_kwh", required=False)
    assert target is not None and required is not None
    if not 0.0 <= target <= 100.0 or required < 0.0:
        raise ValueError("night plan target SOC or required charge is out of range")
    should_apply = quality.get("should_apply") if isinstance(quality, dict) else None
    if should_apply is not None and not isinstance(should_apply, bool):
        raise ValueError("night plan plan_quality.should_apply must be boolean")
    return NightPlan(
        forecast_date=forecast_date,
        result=NightPlanResult(target, required, surplus),
        should_apply=should_apply,
        raw=raw,
    )


def read_night_plan(path: Path) -> NightPlan:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("night plan root must be an object")
    return parse_night_plan(raw)

