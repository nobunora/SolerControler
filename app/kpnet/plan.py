from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.parsing.numbers import to_float
from app.runtime.night_soc_controller import make_plan_snapshot


@dataclass(frozen=True)
class NightChargePlan:
    plan_path: Path
    forecast_date: str
    required_night_charge_kwh: float
    target_soc_7_percent: float
    soc_now_percent: float | None
    effective_capacity_kwh: float | None
    csv_paths: list[Path]
    plan_id: str = ""
    plan_revision: str = ""
    plan_hash: str = ""
    generated_at_utc: str = ""


def load_night_charge_plan(plan_path: Path) -> NightChargePlan:
    if not plan_path.exists():
        raise RuntimeError(f"夜間充電計画ファイルが見つかりません: {plan_path}")

    raw = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("夜間充電計画のルートがJSON objectではありません")
    result = raw.get("result", {})
    forecast = raw.get("forecast", {})
    inputs = raw.get("inputs", {})
    plan_quality = raw.get("plan_quality", {})
    if not isinstance(result, dict):
        raise RuntimeError("夜間充電計画のresultがJSON objectではありません")
    if not isinstance(forecast, dict):
        raise RuntimeError("夜間充電計画のforecastがJSON objectではありません")
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, dict):
        raise RuntimeError("夜間充電計画のinputsがJSON objectではありません")
    if isinstance(plan_quality, dict) and plan_quality.get("should_apply") is False:
        raise RuntimeError(f"夜間充電計画は適用不可です: plan_quality={plan_quality}")

    required_night_charge_kwh = required_plan_float(
        result,
        key="required_night_charge_kwh",
        min_value=0.0,
        name="result.required_night_charge_kwh",
    )
    target_soc_7_percent = required_plan_float(
        result,
        key="target_soc_7_percent",
        min_value=0.0,
        max_value=100.0,
        name="result.target_soc_7_percent",
    )
    soc_now_percent = to_float(inputs.get("soc_now_percent"))
    effective_capacity_kwh = to_float(result.get("effective_capacity_kwh"))
    forecast_date = str(forecast.get("date", "")).strip()
    if not forecast_date:
        raise RuntimeError("夜間充電計画にforecast.dateが含まれていません")
    csv_paths = [Path(str(path)) for path in raw.get("csv_paths", [])]
    if not csv_paths:
        raise RuntimeError("夜間充電計画にCSVパスが含まれていません")

    snapshot = make_plan_snapshot(raw)
    return NightChargePlan(
        plan_path=plan_path,
        forecast_date=forecast_date,
        required_night_charge_kwh=required_night_charge_kwh,
        target_soc_7_percent=target_soc_7_percent,
        soc_now_percent=soc_now_percent,
        effective_capacity_kwh=effective_capacity_kwh,
        csv_paths=csv_paths,
        plan_id=snapshot.plan_id,
        plan_revision=snapshot.revision,
        plan_hash=snapshot.content_hash,
        generated_at_utc=snapshot.generated_at_utc,
    )


def required_plan_float(
    source: dict[str, Any],
    *,
    key: str,
    name: str,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    if key not in source:
        raise RuntimeError(f"夜間充電計画に{name}が含まれていません")
    try:
        value = float(source[key])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"夜間充電計画の{name}が数値ではありません: {source[key]!r}") from exc
    if not math.isfinite(value):
        raise RuntimeError(f"夜間充電計画の{name}が有限値ではありません: {source[key]!r}")
    if min_value is not None and value < min_value:
        raise RuntimeError(f"夜間充電計画の{name}が下限未満です: {value}")
    if max_value is not None and value > max_value:
        raise RuntimeError(f"夜間充電計画の{name}が上限超過です: {value}")
    return value
