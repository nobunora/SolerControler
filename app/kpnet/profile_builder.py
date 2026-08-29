from __future__ import annotations

import json
import csv
import logging
import math
import os
import re
import statistics
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.domain.constants import SOCBounds, validate_soc_percent
from app.kpnet.monitoring_history import iter_charge_soc_points
from app.kpnet.plan import NightChargePlan, load_night_charge_plan
from app.kpnet.profiles import FORCED_CHARGE_PROFILE, GREEN_MODE_PROFILE, ProfileOverrides
from app.runtime.night_soc_controller import build_device_soc_guard
from app.kpnet.rules import _minutes_to_hm, _night_window_contract, _parse_hhmm
from app.kpnet.rules import _in_time_window
from app.configuration.environment import env
from app.parsing.numbers import to_float
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from app.kpnet.config import KpNetConfig

LOGGER = logging.getLogger(__name__)
def _load_operation_conditions(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"運用条件ファイルが見つかりません: {path}")
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"運用条件ファイル形式が不正です: {path}")
    fixed = obj.get("fixed", [])
    variable = obj.get("variable", [])
    if not isinstance(fixed, list) or not isinstance(variable, list):
        raise RuntimeError(f"運用条件ファイルの fixed/variable が不正です: {path}")
    return obj


def _enabled_sorted_rules(conditions: dict[str, Any], section: str) -> list[dict[str, Any]]:
    rules = conditions.get(section, [])
    out: list[dict[str, Any]] = []
    if not isinstance(rules, list):
        return out
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if not bool(rule.get("enabled", True)):
            continue
        out.append(rule)
    out.sort(key=lambda x: int(x.get("priority", 0)), reverse=True)
    return out


def _variable_rule(conditions: dict[str, Any], rule_id: str) -> dict[str, Any] | None:
    for rule in _enabled_sorted_rules(conditions, "variable"):
        if str(rule.get("id", "")).strip() == rule_id:
            return rule
    return None


def _resolve_hhmm(conditions: dict[str, Any], rule_id: str, key: str, default_hhmm: str) -> tuple[int, int]:
    rule = _variable_rule(conditions, rule_id)
    if rule is None:
        return _parse_hhmm(default_hhmm, name=f"{rule_id}.{key}")
    raw = str(rule.get(key, "")).strip()
    if not raw:
        return _parse_hhmm(default_hhmm, name=f"{rule_id}.{key}")
    return _parse_hhmm(raw, name=f"{rule_id}.{key}")


def _resolve_day_discharge_start_hhmm(
    *,
    cfg: "KpNetConfig",
    conditions: dict[str, Any],
    plan: NightChargePlan | None,
    summary: dict[str, Any],
) -> tuple[int, int]:
    default_hh, default_mm = _parse_hhmm(
        cfg.day_discharge_window_start,
        name="KP_DAY_DISCHARGE_WINDOW_START",
    )
    summary["day_discharge_start_rule"] = {
        "status": "fixed",
        "selected": "default",
        "selected_start": f"{default_hh:02d}:{default_mm:02d}",
        "source": "KP_DAY_DISCHARGE_WINDOW_START",
    }
    return default_hh, default_mm


def _resolve_night_charge_end_hhmm(
    *,
    conditions: dict[str, Any],
    plan: NightChargePlan,
    summary: dict[str, Any],
) -> tuple[int, int]:
    hh, mm = _resolve_hhmm(
        conditions,
        rule_id="night_charge_end_time",
        key="value",
        default_hhmm="07:00",
    )
    summary["night_charge_end_rule"] = {
        "status": "fixed",
        "selected": "base",
        "selected_end": f"{hh:02d}:{mm:02d}",
        "source": "night_charge_end_time",
    }
    return hh, mm


def _apply_fixed_time_rules(
    *,
    start_minute: int,
    end_minute: int,
    window_name: str,
    conditions: dict[str, Any],
    summary: dict[str, Any],
) -> tuple[int, int]:
    fixed_notes: list[dict[str, Any]] = []
    for rule in _enabled_sorted_rules(conditions, "fixed"):
        target = str(rule.get("target", "all")).strip().lower()
        if target not in {"all", window_name}:
            continue
        rule_id = str(rule.get("id", "")).strip()
        priority = int(rule.get("priority", 0))
        if rule_id == "forbid_cross_midnight":
            if start_minute > end_minute:
                min_duration = int(rule.get("min_duration_minutes", 30))
                start_minute = max(0, end_minute - min_duration)
                fixed_notes.append(
                    {
                        "id": rule_id,
                        "priority": priority,
                        "action": f"{window_name} window cross-midnight を補正",
                        "result": {"start_minute": start_minute, "end_minute": end_minute},
                    }
                )
        elif rule_id == "forbid_same_start_end":
            if start_minute == end_minute:
                min_duration = int(rule.get("min_duration_minutes", 30))
                start_minute = max(0, end_minute - min_duration)
                if start_minute == end_minute:
                    end_minute = min(23 * 60 + 59, start_minute + min_duration)
                fixed_notes.append(
                    {
                        "id": rule_id,
                        "priority": priority,
                        "action": f"{window_name} window start=end を補正",
                        "result": {"start_minute": start_minute, "end_minute": end_minute},
                    }
                )

    if start_minute > end_minute:
        raise RuntimeError(f"{window_name} window が0時跨ぎとなり補正不可です")
    if start_minute == end_minute:
        raise RuntimeError(f"{window_name} window の開始/終了が同一となり補正不可です")
    if fixed_notes:
        summary.setdefault("fixed_condition_adjustments", []).extend(fixed_notes)
    return start_minute, end_minute


def _candidate_int_values(value_map: dict[str, str]) -> list[int]:
    values: list[int] = []
    for key in value_map:
        if key.isdigit():
            values.append(int(key))
    if not values:
        raise RuntimeError("候補値を取得できませんでした")
    return sorted(set(values))


def _pick_min_code(value_map: dict[str, str]) -> str:
    return str(_candidate_int_values(value_map)[0])


def _pick_max_code(value_map: dict[str, str]) -> str:
    return str(_candidate_int_values(value_map)[-1])


def _pick_ceil_code(value_map: dict[str, str], target: float) -> str:
    values = _candidate_int_values(value_map)
    for value in values:
        if value >= target:
            return str(value)
    return str(values[-1])


# HISTORICAL_FAILURE_LOCK (EVIDENCE_20260829_STANDBY_CANDIDATE): do not restore
# the old standby=0 fallback or make matching optional. KP-NET maps 0=economy,
# 1=green, 3=forced, and 5=standby; choosing 0/1 instead of candidate 5 breaks
# the standby write/read-back sequence and physically leaves economy/green mode
# when 03:00 and 07:00 depend on confirmed standby. Guarded by
# test_night_soc_protected_contract.py::test_protected_contract_has_documented_locks_at_each_operational_boundary
# and tests/test_kpnet_workflow.py::test_real_kpnet_mode_candidates_map_standby_to_five_not_economy_zero.
def _pick_battery_operating_mode_code(
    value_map: dict[str, str],
    *,
    prefer: str,
) -> str:
    target_keywords: tuple[str, ...]
    prefer_norm = prefer.strip().lower()
    if prefer_norm == "economy":
        target_keywords = ("経済", "economy")
    elif prefer_norm == "green":
        target_keywords = ("グリーン", "green")
    elif prefer_norm == "forced":
        target_keywords = ("強制", "forced")
    elif prefer_norm == "standby":
        target_keywords = ("待機", "standby")
    else:
        raise RuntimeError(f"未知の battery operating mode 指定です: {prefer}")

    for code, label in value_map.items():
        label_text = str(label).strip()
        label_norm = label_text.lower()
        if any(keyword in label_text or keyword in label_norm for keyword in target_keywords):
            return str(code)

    # 既存実装との互換用フォールバック（候補が数字コードの場合のみ）
    if prefer_norm == "economy" and "2" in value_map:
        return "2"
    if prefer_norm == "green" and "1" in value_map:
        return "1"
    if prefer_norm == "forced" and "3" in value_map:
        return "3"
    # HISTORICAL_FAILURE_LOCK (2026-08-29 KP-NET candidate read-back): do not
    # restore the old standby=0 fallback.  The deployed device advertises
    # 0=economy, 1=green, 3=forced, and 5=standby.  A fallback to 0 therefore
    # writes economy while logs claim standby, and 23:00/03:00 cannot provide a
    # trustworthy hand-off to 07:00.  Standby must be selected from its real
    # candidate label; fail closed if the device stops advertising that label.
    # Guarded by test_kpnet_workflow.py candidate-map regressions.

    raise RuntimeError(
        "BatteryOperatingMode の候補から必要なモードを特定できませんでした "
        f"(prefer={prefer}, candidates={value_map})"
    )


def _extract_simple_visualization_soc_percent(html: str) -> float | None:
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.select("table.data_table_bt"):
        if not table.select_one(
            ".fa-battery-full, .fa-battery-three-quarters, .fa-battery-half, "
            ".fa-battery-quarter, .fa-battery-empty"
        ):
            continue
        value_headers = [th for th in table.select("th") if not th.select_one('[class*="fa-battery-"]')]
        soc_column = next(
            (index for index, header in enumerate(value_headers) if "蓄電残量" in header.get_text(" ", strip=True)),
            None,
        )
        if soc_column is None:
            continue
        for row in table.select("tr"):
            cells = row.select("td")
            if soc_column >= len(cells):
                continue
            cell = cells[soc_column]
            if "rb_cell" not in (cell.get("class") or []):
                continue
            raw_value = cell.get_text(" ", strip=True)
            match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*%?\s*", raw_value)
            if match:
                return validate_soc_percent(float(match.group(1)), raw=raw_value)
    return None


_load_night_charge_plan = load_night_charge_plan


def _estimate_charge_power_kw(
    csv_paths: list[Path],
    *,
    night_window_start: tuple[int, int],
    night_window_end: tuple[int, int],
    fallback_kw: float,
) -> float:
    start_minute = night_window_start[0] * 60 + night_window_start[1]
    end_minute = night_window_end[0] * 60 + night_window_end[1]
    charge_kwh_per_30m: list[float] = []

    for csv_path in csv_paths:
        if not csv_path.exists():
            continue
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date_text = (row.get("年月日") or "").strip()
                time_text = (row.get("時刻") or "").strip()
                if not date_text or not time_text:
                    continue
                try:
                    dt = datetime.strptime(f"{date_text} {time_text}", "%Y/%m/%d %H:%M")
                except ValueError:
                    continue

                minute_of_day = dt.hour * 60 + dt.minute
                if not _in_time_window(minute_of_day, start_minute, end_minute):
                    continue

                try:
                    charge_kwh = float((row.get("充電電力量[kWh]") or "0").strip() or "0")
                except ValueError:
                    charge_kwh = 0.0
                if charge_kwh > 0:
                    charge_kwh_per_30m.append(charge_kwh)

    if charge_kwh_per_30m:
        return statistics.median(charge_kwh_per_30m) * 2.0
    return fallback_kw


# readable-code-audit: skip DUP-01 — this median estimates the currently applied KP-NET setting, while Cloud Job forecasts tomorrow's stop time from a 14-day degradation trend.
def _estimate_charge_soc_rate_percent_per_hour(csv_paths: list[Path]) -> dict[str, float | int | str]:
    fallback = float(env("ADJUST03_FORCE_CHARGE_RATE_FALLBACK_PERCENT_PER_HOUR", default="40").strip() or "40")
    min_rate = float(env("ADJUST03_FORCE_CHARGE_RATE_MIN_PERCENT_PER_HOUR", default="25").strip() or "25")
    max_rate = float(env("ADJUST03_FORCE_CHARGE_RATE_MAX_PERCENT_PER_HOUR", default="50").strip() or "50")
    min_charge_kwh = float(env("ADJUST03_FORCE_CHARGE_SAMPLE_MIN_KWH", default="1.2").strip() or "1.2")
    if max_rate < min_rate:
        max_rate = min_rate

    samples: list[float] = []
    previous: tuple[datetime, float, float] | None = None
    for point in iter_charge_soc_points(csv_paths):
        if previous is None:
            previous = point
            continue
        prev_dt, prev_soc, _prev_charge = previous
        dt, soc, charge_kwh = point
        hours = (dt - prev_dt).total_seconds() / 3600.0
        delta_soc = soc - prev_soc
        if 0 < hours <= 2.0 and delta_soc > 0 and charge_kwh >= min_charge_kwh:
            samples.append(delta_soc / hours)
        previous = point

    if samples:
        raw_rate = statistics.median(samples)
        source = "csv-forced-charge-soc-rate"
    else:
        raw_rate = fallback
        source = "fallback-forced-charge-soc-rate"
    rate = max(min_rate, min(max_rate, raw_rate))
    return {
        "percent_per_hour": rate,
        "raw_percent_per_hour": raw_rate,
        "sample_count": len(samples),
        "sample_min_charge_kwh": min_charge_kwh,
        "source": source,
    }


def _required_charge_percent(plan: NightChargePlan) -> float:
    target_soc = max(0.0, plan.target_soc_7_percent)
    soc_now = plan.soc_now_percent
    if soc_now is not None:
        return max(0.0, target_soc - SOCBounds.clamp(soc_now))
    cap = plan.effective_capacity_kwh
    if cap is not None and cap > 0 and plan.required_night_charge_kwh > 0:
        return max(0.0, 100.0 * plan.required_night_charge_kwh / cap)
    return max(0.0, target_soc)


def _pick_night_mode_preference(
    *,
    plan: NightChargePlan,
    green_mode_max_charge_percent: float,
) -> tuple[str, float, bool]:
    required_charge_percent = _required_charge_percent(plan)
    slot = os.getenv("CLOUD_JOB_SLOT", "").strip().lower()
    try:
        no_charge_epsilon = max(0.0, float(env("ADJUST03_NO_CHARGE_PERCENT_EPSILON", default="0.5").strip() or "0.5"))
    except ValueError:
        no_charge_epsilon = 0.5
    # KP green mode has behaved as an absolute SOC ceiling, not just a
    # remaining-charge allowance. If the target SOC itself is above the green
    # ceiling, use forced mode and let the 03 job time/monitor the stop.
    force_charge = (
        (slot in {"3", "03", "adjust", "adjust03"} and required_charge_percent > no_charge_epsilon)
        or plan.target_soc_7_percent > green_mode_max_charge_percent
        or required_charge_percent >= green_mode_max_charge_percent
    )
    return ("forced" if force_charge else "green"), required_charge_percent, force_charge


# HISTORICAL_FAILURE_LOCK (device contract, confirmed 2026-08-23): this is the
# only dynamic forced-profile construction boundary. SocChargeMode is capped at
# 50% by the installed inverter, so a 51..100% planning target must use its
# maximum candidate to activate forced mode. The 03 monitor owns the continuous
# target and standby transition. Do not add a candidate>=target rejection here.
def _build_dynamic_forced_profile(
    cfg: KpNetConfig,
    value_maps: dict[str, dict[str, str]],
    summary: dict[str, Any],
) -> ProfileOverrides:
    plan = _load_night_charge_plan(cfg.night_plan_path)
    conditions = _load_operation_conditions(cfg.operation_conditions_path)

    night_window_start = _parse_hhmm(cfg.night_charge_window_start, name="KP_NIGHT_CHARGE_WINDOW_START")
    night_window_end = _parse_hhmm(cfg.night_charge_window_end, name="KP_NIGHT_CHARGE_WINDOW_END")
    window_contract = _night_window_contract(
        cfg.night_charge_window_start,
        cfg.night_charge_window_end,
    )

    estimated_charge_power_kw = _estimate_charge_power_kw(
        plan.csv_paths,
        night_window_start=night_window_start,
        night_window_end=night_window_end,
        fallback_kw=cfg.default_charge_power_kw,
    )

    required_night_charge_kwh = max(0.0, plan.required_night_charge_kwh)
    target_soc_7_percent = max(0.0, plan.target_soc_7_percent)
    night_mode_preference, required_charge_percent, force_charge_mode = _pick_night_mode_preference(
        plan=plan,
        green_mode_max_charge_percent=cfg.green_mode_max_charge_percent,
    )
    stop_margin_percent = float(os.getenv("ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT", "1.0"))
    soc_guard = build_device_soc_guard(
        value_maps["SocChargeMode"],
        raw_target_soc_percent=target_soc_7_percent,
        stop_margin_percent=stop_margin_percent,
    )
    soc_charge_code = soc_guard.device_soc_code

    duration_minutes_kwh = 0
    if estimated_charge_power_kw > 0 and required_night_charge_kwh > 0:
        duration_minutes_kwh = int(math.ceil(required_night_charge_kwh / estimated_charge_power_kw * 60.0))

    charge_rate_info: dict[str, float | int | str] | None = None
    duration_minutes_soc: int | None = None
    duration_source = "kwh"
    duration_minutes = duration_minutes_kwh
    soc_upper_percent = to_float(soc_charge_code)
    rounded_up_soc_target = (
        soc_upper_percent is not None
        and soc_upper_percent > target_soc_7_percent + 0.01
        and plan.soc_now_percent is not None
        and required_charge_percent > 0
    )
    if rounded_up_soc_target:
        charge_rate_info = _estimate_charge_soc_rate_percent_per_hour(plan.csv_paths)
        rate = max(1.0, float(charge_rate_info["percent_per_hour"]))
        duration_minutes_soc = int(math.ceil(required_charge_percent / rate * 60.0))
        duration_minutes = duration_minutes_soc
        duration_source = "soc-rate-rounded-target"

    # ユーザー要件:
    # - 夜間設定の充電終了は運用条件で決定
    # - 曇り/雨予報時は 07:00 に固定（可変条件ファイルで上書き可）
    # - 0:00 を跨ぐ設定をしない（00:00-終了時刻 の同日内でのみ設定）
    # - 逆算で開始時刻を決定（必要時間 > 6h の場合は 00:00 始まりにクリップ）
    charge_end_h, charge_end_m = _resolve_night_charge_end_hhmm(
        conditions=conditions,
        plan=plan,
        summary=summary,
    )
    charge_end_minute = charge_end_h * 60 + charge_end_m
    window_duration_minutes = charge_end_minute
    requested_duration_minutes = duration_minutes
    duration_clipped = False
    if duration_minutes > window_duration_minutes:
        duration_minutes = window_duration_minutes
        duration_clipped = True

    charge_start_minute = max(0, charge_end_minute - duration_minutes)
    if duration_minutes > 0:
        charge_start_minute, charge_end_minute = _apply_fixed_time_rules(
            start_minute=charge_start_minute,
            end_minute=charge_end_minute,
            window_name="charge",
            conditions=conditions,
            summary=summary,
        )
    charge_start_h, charge_start_m = _minutes_to_hm(charge_start_minute)
    charge_end_h, charge_end_m = _minutes_to_hm(charge_end_minute)
    applied_duration_minutes = max(0, charge_end_minute - charge_start_minute)
    logical_duration_minutes = int(window_contract["logical_window_duration_minutes"])
    truncated_minutes = max(0, logical_duration_minutes - charge_end_minute)
    configured_start_minute = night_window_start[0] * 60 + night_window_start[1]
    if duration_clipped:
        limitation_reason = "requested_duration_exceeds_device_same_day_window"
    elif truncated_minutes > 0:
        limitation_reason = "configured_window_exceeds_device_same_day_window"
    elif requested_duration_minutes > 0 and charge_start_minute < configured_start_minute:
        limitation_reason = "configured_start_not_enforced_by_device_schedule"
    else:
        limitation_reason = "none"
    discharge_start_h, discharge_start_m = _resolve_day_discharge_start_hhmm(
        cfg=cfg,
        conditions=conditions,
        plan=plan,
        summary=summary,
    )
    discharge_end_h, discharge_end_m = _parse_hhmm(
        cfg.day_discharge_window_end,
        name="KP_DAY_DISCHARGE_WINDOW_END",
    )

    night_mode_code = _pick_battery_operating_mode_code(
        value_maps["BatteryOperatingMode"],
        prefer=night_mode_preference,
    )
    night_soc_lower_code = _pick_max_code(value_maps["SocSafetyMode"])
    day_soc_lower_code = _pick_min_code(value_maps["SocEconomyMode"])
    contact_soc_lower_code = _pick_max_code(value_maps["SocContactInput"])
    slot23_guard_applied = os.getenv("CLOUD_JOB_SLOT", "").strip() == "23"
    if slot23_guard_applied:
        contact_soc_lower_code = _pick_ceil_code(value_maps["SocContactInput"], 100.0)
        soc_charge_code = _pick_min_code(value_maps["SocChargeMode"])
        device_soc_ceiling_percent = to_float(value_maps["SocChargeMode"].get(soc_charge_code))
    else:
        device_soc_ceiling_percent = soc_guard.device_soc_ceiling_percent

    summary["night_charge_plan"] = {
        "plan_path": str(plan.plan_path),
        "forecast_date": plan.forecast_date,
        "required_night_charge_kwh": required_night_charge_kwh,
        "required_charge_percent": required_charge_percent,
        "green_mode_max_charge_percent": cfg.green_mode_max_charge_percent,
        "force_charge_mode": force_charge_mode,
        "soc_now_percent": plan.soc_now_percent,
        "effective_capacity_kwh": plan.effective_capacity_kwh,
        "target_soc_7_percent_raw": target_soc_7_percent,
        "plan_id": plan.plan_id,
        "plan_revision": plan.plan_revision,
        "plan_hash": plan.plan_hash,
        "generated_at_utc": plan.generated_at_utc,
        "device_soc_code": soc_charge_code,
        "device_soc_ceiling_percent": device_soc_ceiling_percent,
        "stop_threshold_percent": soc_guard.stop_threshold_percent,
        "estimated_charge_power_kw": estimated_charge_power_kw,
        "duration_minutes_kwh": duration_minutes_kwh,
        "duration_minutes_soc": duration_minutes_soc,
        "duration_source": duration_source,
        "charge_rate_percent_per_hour": (charge_rate_info or {}).get("percent_per_hour"),
        "charge_rate_source": (charge_rate_info or {}).get("source"),
        "charge_rate_sample_count": (charge_rate_info or {}).get("sample_count"),
        "duration_minutes": duration_minutes,
        "duration_clipped_to_window": duration_clipped,
        "no_cross_midnight": True,
        **window_contract,
        "device_schedule_start": f"{charge_start_h:02d}:{charge_start_m:02d}",
        "device_schedule_end": f"{charge_end_h:02d}:{charge_end_m:02d}",
        "device_schedule_duration_minutes": applied_duration_minutes,
        "truncated_minutes": truncated_minutes,
        "requested_charge_duration_minutes": requested_duration_minutes,
        "applied_charge_duration_minutes": applied_duration_minutes,
        "limitation_reason": limitation_reason,
        "fixed_charge_end_time": f"{charge_end_h:02d}:{charge_end_m:02d}",
        "night_window_start": cfg.night_charge_window_start,
        "night_window_end": cfg.night_charge_window_end,
        "charge_start_time": f"{charge_start_h:02d}:{charge_start_m:02d}",
        "charge_end_time": f"{charge_end_h:02d}:{charge_end_m:02d}",
        "soc_safety_mode": night_soc_lower_code,
        "soc_economy_mode": day_soc_lower_code,
        "soc_contact_input": contact_soc_lower_code,
        "soc_charge_mode": soc_charge_code,
        "slot23_discharge_guard": {
            "applied": slot23_guard_applied,
            "reason": "set_target_soc_100_and_charge_upper_0_at_23_to_prevent_night_discharge"
            if slot23_guard_applied else "not_slot_23",
        },
        "battery_operating_mode_preference": night_mode_preference,
        "battery_operating_mode": night_mode_code,
        "day_discharge_window_start": f"{discharge_start_h:02d}:{discharge_start_m:02d}",
        "day_discharge_window_end": f"{discharge_end_h:02d}:{discharge_end_m:02d}",
        "discharge_fixed_window": f"{discharge_start_h:02d}:{discharge_start_m:02d}-{discharge_end_h:02d}:{discharge_end_m:02d}",
        "conditions_source": str(cfg.operation_conditions_path),
    }

    LOGGER.info(
        "Night plan date=%s required=%.3fkWh power=%.3fkW duration=%s/%smin source=%s start=%02d:%02d end=%02d:%02d socTarget=%.1f socUpper=%s",
        plan.forecast_date,
        required_night_charge_kwh,
        estimated_charge_power_kw,
        duration_minutes_kwh,
        duration_minutes,
        duration_source,
        charge_start_h,
        charge_start_m,
        charge_end_h,
        charge_end_m,
        target_soc_7_percent,
        soc_charge_code,
    )
    LOGGER.info(
        "Night window configured=%s-%s logical=%smin crossesMidnight=%s device=%s-%s applied=%smin truncated=%smin limitation=%s",
        window_contract["configured_window_start"],
        window_contract["configured_window_end"],
        logical_duration_minutes,
        window_contract["logical_window_crosses_midnight"],
        f"{charge_start_h:02d}:{charge_start_m:02d}",
        f"{charge_end_h:02d}:{charge_end_m:02d}",
        applied_duration_minutes,
        truncated_minutes,
        limitation_reason,
    )

    return replace(
        FORCED_CHARGE_PROFILE,
        battery_operating_mode=night_mode_code,
        soc_safety_mode=night_soc_lower_code,
        soc_economy_mode=day_soc_lower_code,
        soc_contact_input=contact_soc_lower_code,
        soc_charge_mode=soc_charge_code,
        charge_start_h=str(charge_start_h),
        charge_start_m=str(charge_start_m),
        charge_end_h=str(charge_end_h),
        charge_end_m=str(charge_end_m),
        discharge_start_h=str(discharge_start_h),
        discharge_start_m=str(discharge_start_m),
        discharge_end_h=str(discharge_end_h),
        discharge_end_m=str(discharge_end_m),
    )


# readable-code-audit: skip STRUCT-04 — rule evaluation and profile construction must use one settings version for a device command
def _build_dynamic_green_profile(
    cfg: KpNetConfig,
    value_maps: dict[str, dict[str, str]],
    forced_profile: ProfileOverrides,
    summary: dict[str, Any],
) -> ProfileOverrides:
    conditions = _load_operation_conditions(cfg.operation_conditions_path)
    plan: NightChargePlan | None = None
    try:
        plan = _load_night_charge_plan(cfg.night_plan_path)
    except Exception as exc:
        LOGGER.warning("Night charge plan unavailable while building day profile: %s", exc)

    charge_start_hh, charge_start_mm = _resolve_hhmm(
        conditions,
        rule_id="day_charge_window",
        key="start",
        default_hhmm="00:00",
    )
    charge_end_hh, charge_end_mm = _resolve_hhmm(
        conditions,
        rule_id="day_charge_window",
        key="end",
        default_hhmm="06:00",
    )
    charge_start_minute = charge_start_hh * 60 + charge_start_mm
    charge_end_minute = charge_end_hh * 60 + charge_end_mm
    charge_start_minute, charge_end_minute = _apply_fixed_time_rules(
        start_minute=charge_start_minute,
        end_minute=charge_end_minute,
        window_name="charge",
        conditions=conditions,
        summary=summary,
    )
    charge_start_h, charge_start_m = _minutes_to_hm(charge_start_minute)
    charge_end_h, charge_end_m = _minutes_to_hm(charge_end_minute)
    discharge_start_h, discharge_start_m = _resolve_day_discharge_start_hhmm(
        cfg=cfg,
        conditions=conditions,
        plan=plan,
        summary=summary,
    )
    discharge_end_h, discharge_end_m = _parse_hhmm(
        cfg.day_discharge_window_end,
        name="KP_DAY_DISCHARGE_WINDOW_END",
    )

    # ユーザー要件:
    # - 日中はグリーンモード
    # - SOC下限(安心)は0%
    # - SOC下限(経済/グリーン)は0%
    # - 充電時間帯SOC上限は0%
    # - 放電開始時刻は予報条件ルールで決定
    night_soc_lower_code = _pick_min_code(value_maps["SocSafetyMode"])
    day_soc_lower_code = _pick_min_code(value_maps["SocEconomyMode"])
    contact_soc_lower_code = _pick_min_code(value_maps["SocContactInput"])
    soc_charge_code = _pick_min_code(value_maps["SocChargeMode"])

    summary["daytime_mode_plan"] = {
        "mode": "green",
        "day_charge_window_start": f"{charge_start_h:02d}:{charge_start_m:02d}",
        "day_charge_window_end": f"{charge_end_h:02d}:{charge_end_m:02d}",
        "day_discharge_window_start": f"{discharge_start_h:02d}:{discharge_start_m:02d}",
        "day_discharge_window_end": f"{discharge_end_h:02d}:{discharge_end_m:02d}",
        "discharge_fixed_window": f"{discharge_start_h:02d}:{discharge_start_m:02d}-{discharge_end_h:02d}:{discharge_end_m:02d}",
        "soc_safety_mode": night_soc_lower_code,
        "soc_economy_mode": day_soc_lower_code,
        "soc_contact_input": contact_soc_lower_code,
        "soc_charge_mode": soc_charge_code,
        "conditions_source": str(cfg.operation_conditions_path),
    }

    return replace(
        GREEN_MODE_PROFILE,
        soc_safety_mode=night_soc_lower_code,
        soc_economy_mode=day_soc_lower_code,
        soc_contact_input=contact_soc_lower_code,
        soc_charge_mode=soc_charge_code,
        charge_start_h=str(charge_start_h),
        charge_start_m=str(charge_start_m),
        charge_end_h=str(charge_end_h),
        charge_end_m=str(charge_end_m),
        discharge_start_h=str(discharge_start_h),
        discharge_start_m=str(discharge_start_m),
        discharge_end_h=str(discharge_end_h),
        discharge_end_m=str(discharge_end_m),
    )


def _build_payload(
    csrf_setting: str,
    pcsid: str,
    current: dict[str, Any],
    overrides: ProfileOverrides,
    value_maps: dict[str, dict[str, str]],
) -> tuple[dict[str, str], list[str]]:
    payload = {
        "_csrf": csrf_setting,
        "pcsCategory": "BatterySetting",
        "pcsid": pcsid,
        "batteryOperatingMode": str(overrides.battery_operating_mode),
        "batteryOperatingModename": value_maps["BatteryOperatingMode"].get(
            str(overrides.battery_operating_mode), str(current.get("batteryOperatingModename", ""))
        ),
        "socSafetyMode": str(overrides.soc_safety_mode),
        "socSafetyModename": value_maps["SocSafetyMode"].get(
            str(overrides.soc_safety_mode), str(current.get("socSafetyModename", ""))
        ),
        "socEconomyMode": str(overrides.soc_economy_mode),
        "socEconomyModename": value_maps["SocEconomyMode"].get(
            str(overrides.soc_economy_mode), str(current.get("socEconomyModename", ""))
        ),
        "socContactInput": str(overrides.soc_contact_input),
        "socContactInputname": value_maps["SocContactInput"].get(
            str(overrides.soc_contact_input), str(current.get("socContactInputname", ""))
        ),
        "socChargeMode": str(overrides.soc_charge_mode),
        "socChargeModename": value_maps["SocChargeMode"].get(
            str(overrides.soc_charge_mode), str(current.get("socChargeModename", ""))
        ),
        "onPowerOutageMode": str(overrides.on_power_outage_mode),
        "onPowerOutageChargePowerW": str(overrides.on_power_outage_charge_power_w),
        "onPowerOutageChargePowerWname": value_maps["OnPowerOutageChargePowerW"].get(
            str(overrides.on_power_outage_charge_power_w),
            str(current.get("onPowerOutageChargePowerWname", "")),
        ),
        "dischargeDaySun": str(current.get("dischargeDaySun", "0")),
        "dischargeDayMon": str(current.get("dischargeDayMon", "0")),
        "dischargeDayTue": str(current.get("dischargeDayTue", "0")),
        "dischargeDayWed": str(current.get("dischargeDayWed", "0")),
        "dischargeDayThu": str(current.get("dischargeDayThu", "0")),
        "dischargeDayFri": str(current.get("dischargeDayFri", "0")),
        "dischargeDaySat": str(current.get("dischargeDaySat", "0")),
        "chargeStartTimeH": str(overrides.charge_start_h),
        "chargeStartTimeM": str(overrides.charge_start_m),
        "chargeEndTimeH": str(overrides.charge_end_h),
        "chargeEndTimeM": str(overrides.charge_end_m),
        "dischargeStartTimeH": str(overrides.discharge_start_h),
        "dischargeStartTimeM": str(overrides.discharge_start_m),
        "dischargeEndTimeH": str(overrides.discharge_end_h),
        "dischargeEndTimeM": str(overrides.discharge_end_m),
        "agreementAmpere": str(overrides.agreement_ampere),
        "agreementAmperename": value_maps["AgreementAmpere"].get(
            str(overrides.agreement_ampere), str(current.get("agreementAmperename", ""))
        ),
    }

    changed_fields: list[str] = []
    compare_keys = [
        "batteryOperatingMode",
        "socSafetyMode",
        "socEconomyMode",
        "socContactInput",
        "socChargeMode",
        "onPowerOutageMode",
        "onPowerOutageChargePowerW",
        "chargeStartTimeH",
        "chargeStartTimeM",
        "chargeEndTimeH",
        "chargeEndTimeM",
        "dischargeStartTimeH",
        "dischargeStartTimeM",
        "dischargeEndTimeH",
        "dischargeEndTimeM",
        "agreementAmpere",
    ]
    for key in compare_keys:
        if str(current.get(key, "")) != payload[key]:
            changed_fields.append(key)
    return payload, changed_fields


