from __future__ import annotations

import csv
import json
import logging
import math
import os
import re
import statistics
import time
from dataclasses import dataclass, replace
from datetime import datetime
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import matplotlib
import requests
from bs4 import BeautifulSoup

from app.constants import SOCBounds
from app.utils import env, env_bool, load_dotenv_if_present, parse_csv_float, to_float

LOGGER = logging.getLogger(__name__)

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

def _clean_filename(name: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", "_", name).strip() or "download.csv"


def _extract_csrf(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.select_one("meta[name='_csrf']")
    if meta and meta.get("content"):
        return str(meta["content"])
    hidden = soup.select_one("input[name='_csrf']")
    if hidden and hidden.get("value"):
        return str(hidden["value"])
    raise RuntimeError("_csrf をページから取得できませんでした")


def _extract_alert_message(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one("div.alert.alert-danger")
    if not node:
        return ""
    return node.get_text(" ", strip=True)


def _extract_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return ""


def _parse_har_credentials(har_path: Path) -> tuple[str, str]:
    har = json.loads(har_path.read_text(encoding="utf-8"))
    entries = har.get("log", {}).get("entries", [])
    for entry in entries:
        req = entry.get("request", {})
        if req.get("method") != "POST":
            continue
        if not str(req.get("url", "")).endswith("/processLogin"):
            continue
        post_text = req.get("postData", {}).get("text", "")
        parsed = parse_qs(post_text, keep_blank_values=True)
        login_id = parsed.get("loginid", [""])[0]
        login_password = parsed.get("loginpassword", [""])[0]
        if login_id and login_password:
            return login_id, login_password
    raise RuntimeError("HARから loginid / loginpassword を取得できませんでした")


def _validate_base_url(
    *,
    base_url: str,
    enforce_https: bool,
    allowed_hosts: list[str],
) -> None:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not host:
        raise RuntimeError(f"KP_BASE_URL が不正です: {base_url}")
    if enforce_https and parsed.scheme.lower() != "https":
        raise RuntimeError("KP_BASE_URL は https URL を指定してください")
    normalized_allowed = {h.strip().lower() for h in allowed_hosts if h.strip()}
    if normalized_allowed and host not in normalized_allowed:
        raise RuntimeError(
            "KP_BASE_URL のホストが許可リスト外です "
            f"(host={host}, allowed={sorted(normalized_allowed)})"
        )


def _month_key(month: str) -> tuple[int, int]:
    y, m = month.split("-")
    return int(y), int(m)


@dataclass(frozen=True)
class NightChargePlan:
    plan_path: Path
    forecast_date: str
    required_night_charge_kwh: float
    target_soc_7_percent: float
    soc_now_percent: float | None
    effective_capacity_kwh: float | None
    csv_paths: list[Path]


def _parse_hhmm(value: str, *, name: str) -> tuple[int, int]:
    match = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", value)
    if not match:
        raise RuntimeError(f"{name} は HH:MM 形式で指定してください: {value}")
    hh = int(match.group(1))
    mm = int(match.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise RuntimeError(f"{name} の値が不正です: {value}")
    return hh, mm


def _minutes_to_hm(total_minutes: int) -> tuple[int, int]:
    normalized = total_minutes % (24 * 60)
    return normalized // 60, normalized % 60


def _in_time_window(minute_of_day: int, start_minute: int, end_minute: int) -> bool:
    if start_minute == end_minute:
        return True
    if start_minute < end_minute:
        return start_minute <= minute_of_day < end_minute
    return minute_of_day >= start_minute or minute_of_day < end_minute


def _now_in_timezone(timezone_name: str) -> datetime:
    tz_name = timezone_name.strip() or "Asia/Tokyo"
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        LOGGER.warning("Invalid TIMEZONE=%s. Fallback to local system timezone.", tz_name)
        return datetime.now()


def _is_night_window_now(
    *,
    timezone_name: str,
    night_window_start: tuple[int, int],
    night_window_end: tuple[int, int],
) -> bool:
    now = _now_in_timezone(timezone_name)
    minute_of_day = now.hour * 60 + now.minute
    start_minute = night_window_start[0] * 60 + night_window_start[1]
    end_minute = night_window_end[0] * 60 + night_window_end[1]
    return _in_time_window(minute_of_day, start_minute, end_minute)


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
    if prefer_norm == "standby" and "0" in value_map:
        return "0"

    raise RuntimeError(
        "BatteryOperatingMode の候補から必要なモードを特定できませんでした "
        f"(prefer={prefer}, candidates={value_map})"
    )


def _extract_simple_visualization_soc_percent(html: str) -> float | None:
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.select("table.data_table_bt"):
        if table.select_one(".fa-battery-three-quarters, .fa-battery-full, .fa-battery-half, .fa-battery-empty"):
            cell = table.select_one("td.rb_cell")
            if cell:
                value = parse_csv_float(cell.get_text(" ", strip=True), default=None)
                if value is not None:
                    return SOCBounds.clamp(float(value))

    match = re.search(
        r"fa-battery[^<]*</i>.*?<td[^>]*class=[\"'][^\"']*rb_cell[^\"']*[\"'][^>]*>\s*([0-9]+(?:\.[0-9]+)?)\s*<span>\s*%",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return SOCBounds.clamp(float(match.group(1)))


def _load_night_charge_plan(plan_path: Path) -> NightChargePlan:
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

    required_night_charge_kwh = _required_plan_float(
        result,
        key="required_night_charge_kwh",
        min_value=0.0,
        name="result.required_night_charge_kwh",
    )
    target_soc_7_percent = _required_plan_float(
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
    csv_paths = [Path(str(p)) for p in raw.get("csv_paths", [])]
    if not csv_paths:
        raise RuntimeError("夜間充電計画にCSVパスが含まれていません")

    return NightChargePlan(
        plan_path=plan_path,
        forecast_date=forecast_date,
        required_night_charge_kwh=required_night_charge_kwh,
        target_soc_7_percent=target_soc_7_percent,
        soc_now_percent=soc_now_percent,
        effective_capacity_kwh=effective_capacity_kwh,
        csv_paths=csv_paths,
    )


def _required_plan_float(
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


def _iter_charge_soc_points(csv_paths: list[Path]) -> list[tuple[datetime, float, float]]:
    points: list[tuple[datetime, float, float]] = []
    for csv_path in csv_paths:
        if not csv_path.exists():
            continue
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date_text = (row.get("年月日") or "").strip()
                time_text = (row.get("時刻") or "").strip()
                soc_text = (row.get("蓄電残量(SOC)[%]") or "").strip()
                charge_text = (row.get("充電電力量[kWh]") or "").strip()
                if not date_text or not time_text or not soc_text:
                    continue
                try:
                    dt = datetime.strptime(f"{date_text} {time_text}", "%Y/%m/%d %H:%M")
                    soc = float(soc_text)
                    charge_kwh = float(charge_text) if charge_text else 0.0
                except (TypeError, ValueError):
                    continue
                points.append((dt, soc, charge_kwh))
    points.sort(key=lambda x: x[0])
    return points


def _estimate_charge_soc_rate_percent_per_hour(csv_paths: list[Path]) -> dict[str, float | int | str]:
    fallback = float(env("ADJUST03_FORCE_CHARGE_RATE_FALLBACK_PERCENT_PER_HOUR", default="40").strip() or "40")
    min_rate = float(env("ADJUST03_FORCE_CHARGE_RATE_MIN_PERCENT_PER_HOUR", default="25").strip() or "25")
    max_rate = float(env("ADJUST03_FORCE_CHARGE_RATE_MAX_PERCENT_PER_HOUR", default="50").strip() or "50")
    min_charge_kwh = float(env("ADJUST03_FORCE_CHARGE_SAMPLE_MIN_KWH", default="1.2").strip() or "1.2")
    if max_rate < min_rate:
        max_rate = min_rate

    samples: list[float] = []
    previous: tuple[datetime, float, float] | None = None
    for point in _iter_charge_soc_points(csv_paths):
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


@dataclass(frozen=True)
class ProfileOverrides:
    name: str
    battery_operating_mode: str
    soc_safety_mode: str
    soc_economy_mode: str
    soc_contact_input: str
    soc_charge_mode: str
    charge_start_h: str
    charge_start_m: str
    charge_end_h: str
    charge_end_m: str
    discharge_start_h: str
    discharge_start_m: str
    discharge_end_h: str
    discharge_end_m: str
    agreement_ampere: str
    on_power_outage_mode: str = "0"
    on_power_outage_charge_power_w: str = "65535"


FORCED_CHARGE_PROFILE = ProfileOverrides(
    name="night-green",
    battery_operating_mode="1",
    soc_safety_mode="50",
    soc_economy_mode="0",
    soc_contact_input="100",
    soc_charge_mode="50",
    charge_start_h="4",
    charge_start_m="30",
    charge_end_h="6",
    charge_end_m="30",
    discharge_start_h="7",
    discharge_start_m="0",
    discharge_end_h="23",
    discharge_end_m="0",
    agreement_ampere="50",
)

GREEN_MODE_PROFILE = ProfileOverrides(
    name="green-mode",
    battery_operating_mode="1",
    soc_safety_mode="0",
    soc_economy_mode="0",
    soc_contact_input="0",
    soc_charge_mode="0",
    charge_start_h="23",
    charge_start_m="0",
    charge_end_h="7",
    charge_end_m="0",
    discharge_start_h="7",
    discharge_start_m="0",
    discharge_end_h="23",
    discharge_end_m="0",
    agreement_ampere="50",
)

STANDBY_PROFILE = replace(
    GREEN_MODE_PROFILE,
    name="standby-mode",
    battery_operating_mode="0",
    soc_contact_input="0",
    soc_charge_mode="0",
)


@dataclass(frozen=True)
class KpNetConfig:
    base_url: str
    username: str
    password: str
    dry_run: bool
    timeout_sec: float
    csv_output_format: str
    csv_aggr_type: str
    csv_target_months: list[str]
    download_latest_month: bool
    workflow_mode: str
    settings_sequence: str
    force_settings_profile: str
    dynamic_forced_profile: bool
    dynamic_mode_switch_by_time: bool
    night_plan_path: Path
    default_charge_power_kw: float
    green_mode_max_charge_percent: float
    night_charge_window_start: str
    night_charge_window_end: str
    day_discharge_window_start: str
    day_discharge_window_end: str
    operation_conditions_path: Path
    timezone_name: str
    use_har_credentials: bool
    har_path: Path
    artifacts_dir: Path
    enforce_https: bool
    allowed_hosts: list[str]

    @staticmethod
    def from_env() -> "KpNetConfig":
        username = env("KP_MONITOR_USERNAME", default=env("MONITOR_USERNAME", default=""))
        password = env("KP_MONITOR_PASSWORD", default=env("MONITOR_PASSWORD", default=""))

        use_har_credentials = env_bool("KP_USE_HAR_CREDENTIALS", default=False)
        har_path = Path(env("KP_HAR_PATH", default=r""))
        if use_har_credentials and (not username or not password):
            username, password = _parse_har_credentials(har_path)

        if not username or not password:
            raise RuntimeError(
                "KP_MONITOR_USERNAME / KP_MONITOR_PASSWORD が未設定です "
                "(または HAR から取得できません)"
            )

        raw_months = env("KP_CSV_TARGET_MONTHS", default="")
        months = [m.strip() for m in raw_months.split(",") if m.strip()]
        if not months:
            months = _default_csv_target_months()

        workflow_mode = env("KP_WORKFLOW_MODE", default="all").strip().lower()
        if workflow_mode not in {"all", "csv", "settings"}:
            raise RuntimeError("KP_WORKFLOW_MODE は all / csv / settings のいずれかを指定してください")
        settings_sequence = env("KP_SETTINGS_SEQUENCE", default="forced-only").strip().lower()
        if settings_sequence not in {"forced-only", "forced-then-green"}:
            raise RuntimeError(
                "KP_SETTINGS_SEQUENCE は forced-only / forced-then-green のいずれかを指定してください"
            )
        force_settings_profile = env("KP_FORCE_SETTINGS_PROFILE", default="auto").strip().lower()
        if force_settings_profile not in {"auto", "forced", "green", "standby"}:
            raise RuntimeError("KP_FORCE_SETTINGS_PROFILE は auto / forced / green / standby のいずれかを指定してください")
        dynamic_forced_profile = env_bool("KP_DYNAMIC_FORCED_PROFILE", default=True)
        dynamic_mode_switch_by_time = env_bool("KP_DYNAMIC_MODE_SWITCH_BY_TIME", default=True)
        night_charge_window_start = env("KP_NIGHT_CHARGE_WINDOW_START", default="23:00").strip()
        night_charge_window_end = env("KP_NIGHT_CHARGE_WINDOW_END", default="07:00").strip()
        day_discharge_window_start = env("KP_DAY_DISCHARGE_WINDOW_START", default="07:00").strip()
        day_discharge_window_end = env("KP_DAY_DISCHARGE_WINDOW_END", default="23:00").strip()
        _parse_hhmm(night_charge_window_start, name="KP_NIGHT_CHARGE_WINDOW_START")
        _parse_hhmm(night_charge_window_end, name="KP_NIGHT_CHARGE_WINDOW_END")
        _parse_hhmm(day_discharge_window_start, name="KP_DAY_DISCHARGE_WINDOW_START")
        _parse_hhmm(day_discharge_window_end, name="KP_DAY_DISCHARGE_WINDOW_END")
        timezone_name = env("TIMEZONE", default="Asia/Tokyo").strip() or "Asia/Tokyo"

        default_charge_power_kw = float(env("KP_DEFAULT_CHARGE_POWER_KW", default="1.8"))
        if default_charge_power_kw <= 0:
            raise RuntimeError("KP_DEFAULT_CHARGE_POWER_KW は 0 より大きい値を指定してください")
        green_mode_max_charge_percent = float(env("KP_GREEN_MODE_MAX_CHARGE_PERCENT", default="50"))
        if green_mode_max_charge_percent < 0:
            raise RuntimeError("KP_GREEN_MODE_MAX_CHARGE_PERCENT は 0 以上を指定してください")

        base_url = env("KP_BASE_URL", default="https://ctrl.kp-net.com/settingcontrol").strip()
        enforce_https = env_bool("KP_ENFORCE_HTTPS", default=True)
        allowed_hosts_raw = env("KP_ALLOWED_HOSTS", default="ctrl.kp-net.com")
        allowed_hosts = [host.strip() for host in allowed_hosts_raw.split(",") if host.strip()]
        _validate_base_url(
            base_url=base_url,
            enforce_https=enforce_https,
            allowed_hosts=allowed_hosts,
        )

        artifacts_dir = Path(env("ARTIFACTS_DIR", default="artifacts"))
        night_plan_path = Path(env("KP_NIGHT_PLAN_PATH", default=str(artifacts_dir / "night_charge_plan.json")))
        operation_conditions_path = Path(
            env("KP_OPERATION_CONDITIONS_PATH", default="config/operation_conditions.json")
        )

        return KpNetConfig(
            base_url=base_url,
            username=username,
            password=password,
            dry_run=env_bool("DRY_RUN", default=True),
            timeout_sec=float(env("KP_TIMEOUT_SEC", default="60")),
            csv_output_format=env("KP_CSV_OUTPUT_FORMAT", default="太陽光発電＋蓄電池"),
            csv_aggr_type=env("KP_CSV_AGGR_TYPE", default="30分データ"),
            csv_target_months=months,
            download_latest_month=env_bool("KP_DOWNLOAD_LATEST_MONTH", default=True),
            workflow_mode=workflow_mode,
            settings_sequence=settings_sequence,
            force_settings_profile=force_settings_profile,
            dynamic_forced_profile=dynamic_forced_profile,
            dynamic_mode_switch_by_time=dynamic_mode_switch_by_time,
            night_plan_path=night_plan_path,
            default_charge_power_kw=default_charge_power_kw,
            green_mode_max_charge_percent=green_mode_max_charge_percent,
            night_charge_window_start=night_charge_window_start,
            night_charge_window_end=night_charge_window_end,
            day_discharge_window_start=day_discharge_window_start,
            day_discharge_window_end=day_discharge_window_end,
            operation_conditions_path=operation_conditions_path,
            timezone_name=timezone_name,
            use_har_credentials=use_har_credentials,
            har_path=har_path,
            artifacts_dir=artifacts_dir,
            enforce_https=enforce_https,
            allowed_hosts=allowed_hosts,
        )


def _build_dynamic_forced_profile(
    cfg: KpNetConfig,
    value_maps: dict[str, dict[str, str]],
    summary: dict[str, Any],
) -> ProfileOverrides:
    plan = _load_night_charge_plan(cfg.night_plan_path)
    conditions = _load_operation_conditions(cfg.operation_conditions_path)

    night_window_start = _parse_hhmm(cfg.night_charge_window_start, name="KP_NIGHT_CHARGE_WINDOW_START")
    night_window_end = _parse_hhmm(cfg.night_charge_window_end, name="KP_NIGHT_CHARGE_WINDOW_END")

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
    soc_charge_code = _pick_ceil_code(value_maps["SocChargeMode"], target_soc_7_percent)

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


class KpNetClient:
    def __init__(self, cfg: KpNetConfig) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/136.0.0.0 Safari/537.36"
                )
            }
        )
        self.csrf_top = ""
        self.csrf_setting = ""
        self.pcsid = ""

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.cfg.base_url.rstrip("/") + "/", path.lstrip("/"))

    def _ajax_headers(self, referer_path: str) -> dict[str, str]:
        return {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN": self.csrf_setting,
            "Referer": self._url(referer_path),
        }

    def _post(self, path: str, data: dict[str, Any] | None = None, **kwargs: Any) -> requests.Response:
        resp = self.session.post(
            self._url(path),
            data=data,
            timeout=self.cfg.timeout_sec,
            **kwargs,
        )
        resp.raise_for_status()
        return resp

    def _get(self, path: str, **kwargs: Any) -> requests.Response:
        resp = self.session.get(
            self._url(path),
            timeout=self.cfg.timeout_sec,
            **kwargs,
        )
        resp.raise_for_status()
        return resp

    def login(self) -> None:
        login_page = self._get("login")
        csrf = _extract_csrf(login_page.text)
        self._post(
            "processLogin",
            data={
                "_csrf": csrf,
                "loginid": self.cfg.username,
                "loginpassword": self.cfg.password,
            },
        )

        top = self._get("remotevisualization/simplevisualization/enduser")
        self.csrf_top = _extract_csrf(top.text)
        if "ログイン" in _extract_title(top.text) and "ユーザID" in top.text:
            raise RuntimeError("ログインに失敗しました。ユーザIDまたはパスワードをご確認ください。")
        LOGGER.info("Login success")

    def read_realtime_soc_percent(self) -> float | None:
        resp = self._get("remotevisualization/simplevisualization/enduser")
        return _extract_simple_visualization_soc_percent(resp.text)

    def logout(self) -> None:
        csrf = self.csrf_setting or self.csrf_top
        if not csrf:
            return
        self._post("logout", data={"_csrf": csrf})
        LOGGER.info("Logout success")

    def open_csv_measure_page(self) -> tuple[list[str], str]:
        self._post("remotevisualization/variousdataoutputselect", data={"_csrf": self.csrf_top})
        measure = self._post(
            "remotevisualization/variousdataoutputselect/measureoutput",
            data={"_csrf": self.csrf_top},
        )
        soup = BeautifulSoup(measure.text, "html.parser")
        month_options = [
            str(node.get("value", "")).strip()
            for node in soup.select("select[name='collectDate'] option")
            if str(node.get("value", "")).strip()
        ]
        pcsclass = "5"
        pcsclass_input = soup.select_one("input[name='pcsclass']")
        if pcsclass_input and pcsclass_input.get("value"):
            pcsclass = str(pcsclass_input["value"]).strip()
        return month_options, pcsclass

    def download_csv(self, month: str, pcsclass: str, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        resp = self._post(
            "remotevisualization/variousdataoutputselect/measureoutput/download",
            data={
                "_csrf": self.csrf_top,
                "pcsclass": pcsclass,
                "outputFormat": self.cfg.csv_output_format,
                "aggrType": self.cfg.csv_aggr_type,
                "collectDate": month,
            },
        )
        disp = resp.headers.get("Content-Disposition", "")
        msg = Message()
        if disp:
            msg["Content-Disposition"] = disp
        filename = msg.get_param("filename", header="Content-Disposition")
        if not filename:
            filename = f"measure_{month.replace('-', '')}.csv"
        path = out_dir / _clean_filename(filename)
        path.write_bytes(resp.content)
        LOGGER.info("CSV downloaded month=%s path=%s", month, path)
        return path

    def open_settings_page(self) -> None:
        gw = self._post("remotesetting/gwpcsmanage", data={"_csrf": self.csrf_top})
        soup = BeautifulSoup(gw.text, "html.parser")
        pcs_btn = soup.select_one("form[action='/settingcontrol/remotesetting/pcsselect/pcs'] button[name='pcsid']")
        if not pcs_btn or not pcs_btn.get("value"):
            raise RuntimeError("pcsid を取得できませんでした")
        self.pcsid = str(pcs_btn["value"]).strip()

        self._post("remotesetting/pcsselect/pcs", data={"_csrf": self.csrf_top, "pcsid": self.pcsid})
        setting = self._post(
            "remotesetting/pcssetting",
            data={"_csrf": self.csrf_top, "pcsid": self.pcsid, "pcsCategory": "BatterySetting"},
        )
        self.csrf_setting = _extract_csrf(setting.text)
        LOGGER.info("Settings page opened pcsid=%s", self.pcsid)

    def _poll_json(
        self,
        path: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        max_wait_sec: float = 60.0,
    ) -> dict[str, Any]:
        start = time.time()
        while time.time() - start < max_wait_sec:
            resp = self._post(path, data=payload, headers=headers)
            data = resp.json()
            if data.get("status") == 1:
                return data
            time.sleep(0.6)
        raise TimeoutError(f"Polling timeout: {path}")

    def read_current_settings(self) -> dict[str, Any]:
        headers = self._ajax_headers("remotesetting/pcssetting")
        req = self._post(
            "remotesetting/pcssetting/read/request",
            data={"_csrf": self.csrf_setting, "pcsCategory": "BatterySetting", "pcsid": self.pcsid},
            headers=headers,
        ).json()
        comm = req.get("data", {})
        result = self._poll_json(
            "remotesetting/pcssetting/read/response",
            {"communicationSequenceno": comm.get("communicationSequenceno", ""), "value": comm.get("value", "")},
            headers=headers,
        )
        return result.get("data", {})

    def candidate_map(self, candidate_type: str, value_list_path: str) -> dict[str, str]:
        headers = self._ajax_headers("remotesetting/pcssetting")
        req = self._post(
            "remotesetting/pcssetting/read/request/candidate",
            data={"candidateType": candidate_type},
            headers=headers,
        ).json()
        comm = req.get("data", {})
        self._poll_json(
            "remotesetting/pcssetting/read/response/candidate",
            {"communicationSequenceno": comm.get("communicationSequenceno", ""), "value": comm.get("value", "")},
            headers=headers,
        )
        list_resp = self._post(
            value_list_path,
            headers=headers,
        ).json()
        result: dict[str, str] = {}
        for item in list_resp.get("data", []):
            code = str(item.get("code", ""))
            value = str(item.get("value", ""))
            if code:
                result[code] = value
        return result

    def collect_candidate_maps(self) -> dict[str, dict[str, str]]:
        targets = {
            "BatteryOperatingMode": "remotesetting/pcssetting/valueList/batteryoperatingmode",
            "SocSafetyMode": "remotesetting/pcssetting/valueList/socsafetymode",
            "SocEconomyMode": "remotesetting/pcssetting/valueList/soceconomymode",
            "SocContactInput": "remotesetting/pcssetting/valueList/soccontactinput",
            "SocChargeMode": "remotesetting/pcssetting/valueList/socchargemode",
            "OnPowerOutageChargePowerW": "remotesetting/pcssetting/valueList/onpoweroutagechargepower",
            "AgreementAmpere": "remotesetting/pcssetting/valueList/agreementampere",
        }
        return {k: self.candidate_map(k, v) for k, v in targets.items()}

    def confirm_setting(self, payload: dict[str, str]) -> tuple[bool, str, str, str]:
        resp = self._post("remotesetting/pcssettingconfirm/batterysetting", data=payload)
        html = resp.text
        title = _extract_title(html)
        err = _extract_alert_message(html)
        has_complete_button = "id=\"pcs-input-complete\"" in html
        return has_complete_button, title, err, html

    def _extract_form_data(self, html: str) -> tuple[dict[str, str], str]:
        soup = BeautifulSoup(html, "html.parser")
        csrf = _extract_csrf(html)
        form = soup.select_one("form#itemForm, form#ItemForm")
        if form is None:
            raise RuntimeError("確認画面フォーム(ItemForm)を取得できませんでした")

        data: dict[str, str] = {}
        for input_node in form.select("input[name]"):
            if input_node.has_attr("disabled"):
                continue
            name = str(input_node.get("name", "")).strip()
            if not name:
                continue
            data[name] = str(input_node.get("value", ""))

        for select_node in form.select("select[name]"):
            if select_node.has_attr("disabled"):
                continue
            name = str(select_node.get("name", "")).strip()
            if not name:
                continue
            selected = select_node.select_one("option[selected]") or select_node.select_one("option")
            data[name] = str(selected.get("value", "")) if selected else ""

        data["_csrf"] = csrf
        return data, csrf

    def write_setting(self, confirm_html: str) -> dict[str, Any]:
        form_data, csrf = self._extract_form_data(confirm_html)
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN": csrf,
            "Referer": self._url("remotesetting/pcssettingconfirm/batterysetting"),
        }

        req = self._post("remotesetting/pcssetting/write/request", data=form_data, headers=headers).json()
        comm = req.get("data", {})
        self._poll_json(
            "remotesetting/pcssetting/write/response",
            {"communicationSequenceno": comm.get("communicationSequenceno", ""), "value": comm.get("value", "")},
            headers=headers,
            max_wait_sec=90.0,
        )

        self._post("remotesetting/pcssettingcomplete/", data={"_csrf": csrf})
        self._post("remotesetting/pcssetting/write/requestdevicedetail", headers=headers)
        return {"changed": True}


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


def _resolve_months(requested: list[str], available: list[str], include_latest: bool) -> list[str]:
    result: list[str] = []
    available_set = set(available)
    for month in requested:
        if month in available_set and month not in result:
            result.append(month)
    if include_latest and available:
        latest = sorted(available, key=_month_key, reverse=True)[0]
        if latest not in result:
            result.append(latest)
    return result


def _default_csv_target_months(now: datetime | None = None) -> list[str]:
    base = now or _now_in_timezone("Asia/Tokyo")
    current = base.strftime("%Y-%m")
    if base.month == 1:
        previous = f"{base.year - 1}-12"
    else:
        previous = f"{base.year}-{base.month - 1:02d}"
    return [previous, current]


def _parse_csv_points(csv_path: Path) -> tuple[list[datetime], list[float], list[float]]:
    datetimes: list[datetime] = []
    generation_values: list[float] = []
    soc_values: list[float] = []

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
            try:
                gen = float((row.get("発電電力量[kWh]") or "0").strip() or "0")
            except ValueError:
                gen = 0.0
            try:
                soc = float((row.get("蓄電残量(SOC)[%]") or "nan").strip())
            except ValueError:
                soc = float("nan")

            datetimes.append(dt)
            generation_values.append(gen)
            soc_values.append(soc)
    return datetimes, generation_values, soc_values


def _plot_csvs(csv_paths: list[Path], output_path: Path) -> dict[str, Any]:
    all_points: list[tuple[datetime, float, float]] = []
    for path in csv_paths:
        dts, gens, socs = _parse_csv_points(path)
        all_points.extend(zip(dts, gens, socs))
    if not all_points:
        raise RuntimeError("グラフ化できるCSVデータがありませんでした")

    all_points.sort(key=lambda x: x[0])
    xs = [p[0] for p in all_points]
    ys_gen = [p[1] for p in all_points]
    ys_soc = [p[2] for p in all_points]

    fig, ax1 = plt.subplots(figsize=(14, 6))
    ax1.plot(xs, ys_gen, color="#1f77b4", linewidth=1.2, label="PV kWh/30min")
    ax1.set_xlabel("Datetime")
    ax1.set_ylabel("Generation kWh/30min", color="#1f77b4")
    ax1.tick_params(axis="y", labelcolor="#1f77b4")
    ax1.grid(alpha=0.3)

    ax2 = ax1.twinx()
    ax2.plot(xs, ys_soc, color="#ff7f0e", linewidth=1.0, label="Battery SOC %")
    ax2.set_ylabel("SOC %", color="#ff7f0e")
    ax2.tick_params(axis="y", labelcolor="#ff7f0e")
    ax2.set_ylim(0, 100)

    fig.autofmt_xdate()
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=140)
    plt.close(fig)
    return {"points": len(xs), "plot_path": str(output_path)}


def _run_csv_phase(
    client: KpNetClient,
    cfg: KpNetConfig,
    run_dir: Path,
    summary: dict[str, Any],
) -> None:
    csv_dir = run_dir / "csv"
    plot_path = run_dir / "kpi_plot.png"

    available_months, pcsclass = client.open_csv_measure_page()
    target_months = _resolve_months(
        requested=cfg.csv_target_months,
        available=available_months,
        include_latest=cfg.download_latest_month,
    )
    LOGGER.info("Available months: %s", available_months)
    LOGGER.info("Target months: %s", target_months)

    csv_paths: list[Path] = []
    for month in target_months:
        csv_path = client.download_csv(month=month, pcsclass=pcsclass, out_dir=csv_dir)
        csv_paths.append(csv_path)
        summary["csv_downloads"].append({"month": month, "path": str(csv_path)})

    summary["plot"] = _plot_csvs(csv_paths, plot_path)
    LOGGER.info("Plot generated: %s", plot_path)


def _run_settings_phase(
    client: KpNetClient,
    cfg: KpNetConfig,
    run_dir: Path,
    summary: dict[str, Any],
) -> None:
    client.open_settings_page()
    current = client.read_current_settings()
    maps = client.collect_candidate_maps()
    conditions = _load_operation_conditions(cfg.operation_conditions_path)
    summary["operation_conditions"] = {
        "source": str(cfg.operation_conditions_path),
        "fixed": [
            {
                "id": str(rule.get("id", "")),
                "priority": int(rule.get("priority", 0)),
                "target": str(rule.get("target", "all")),
            }
            for rule in _enabled_sorted_rules(conditions, "fixed")
        ],
        "variable": [
            {
                "id": str(rule.get("id", "")),
                "priority": int(rule.get("priority", 0)),
            }
            for rule in _enabled_sorted_rules(conditions, "variable")
        ],
    }

    if cfg.dynamic_forced_profile:
        forced_profile = _build_dynamic_forced_profile(cfg=cfg, value_maps=maps, summary=summary)
    else:
        mode_preference = "green"
        required_charge_percent = None
        force_charge_mode = False
        try:
            plan = _load_night_charge_plan(cfg.night_plan_path)
            mode_preference, required_charge_percent, force_charge_mode = _pick_night_mode_preference(
                plan=plan,
                green_mode_max_charge_percent=cfg.green_mode_max_charge_percent,
            )
        except Exception as exc:
            LOGGER.warning("Night charge plan unavailable while selecting legacy forced profile mode: %s", exc)

        forced_profile = replace(
            FORCED_CHARGE_PROFILE,
            battery_operating_mode=_pick_battery_operating_mode_code(
                maps["BatteryOperatingMode"],
                prefer=mode_preference,
            ),
        )
        summary["night_charge_plan"] = {
            "status": "dynamic-profile-disabled",
            "legacy_mode_preference": mode_preference,
            "required_charge_percent": required_charge_percent,
            "green_mode_max_charge_percent": cfg.green_mode_max_charge_percent,
            "force_charge_mode": force_charge_mode,
        }

    if cfg.dynamic_forced_profile:
        green_profile = _build_dynamic_green_profile(
            cfg=cfg,
            value_maps=maps,
            forced_profile=forced_profile,
            summary=summary,
        )
    else:
        green_profile = GREEN_MODE_PROFILE
    profiles: tuple[ProfileOverrides, ...]
    if cfg.force_settings_profile == "forced":
        profiles = (forced_profile,)
        summary["time_based_mode_selection"] = {
            "enabled": False,
            "forced_profile": forced_profile.name,
            "selected_profile": forced_profile.name,
        }
        LOGGER.info("Forced settings profile selected: %s", forced_profile.name)
    elif cfg.force_settings_profile == "green":
        profiles = (green_profile,)
        summary["time_based_mode_selection"] = {
            "enabled": False,
            "forced_profile": "green-mode",
            "selected_profile": "green-mode",
        }
        LOGGER.info("Forced settings profile selected: green-mode")
    elif cfg.force_settings_profile == "standby":
        standby_profile = replace(
            STANDBY_PROFILE,
            battery_operating_mode=_pick_battery_operating_mode_code(
                maps["BatteryOperatingMode"],
                prefer="standby",
            ),
        )
        profiles = (standby_profile,)
        summary["time_based_mode_selection"] = {
            "enabled": False,
            "forced_profile": "standby-mode",
            "selected_profile": "standby-mode",
        }
        LOGGER.info("Forced settings profile selected: standby-mode")
    elif cfg.dynamic_mode_switch_by_time:
        night_window_start = _parse_hhmm(cfg.night_charge_window_start, name="KP_NIGHT_CHARGE_WINDOW_START")
        night_window_end = _parse_hhmm(cfg.night_charge_window_end, name="KP_NIGHT_CHARGE_WINDOW_END")
        is_night = _is_night_window_now(
            timezone_name=cfg.timezone_name,
            night_window_start=night_window_start,
            night_window_end=night_window_end,
        )
        current_phase = "night" if is_night else "day"
        profiles = (forced_profile,) if is_night else (green_profile,)
        summary["time_based_mode_selection"] = {
            "enabled": True,
            "timezone": cfg.timezone_name,
            "phase": current_phase,
            "selected_profile": profiles[0].name,
        }
        LOGGER.info("Time-based mode switch phase=%s timezone=%s profile=%s", current_phase, cfg.timezone_name, profiles[0].name)
    elif cfg.settings_sequence == "forced-only":
        profiles = (forced_profile,)
    else:
        profiles = (forced_profile, green_profile)

    LOGGER.info(
        "Settings sequence: %s force_settings_profile=%s dynamic_forced_profile=%s dynamic_mode_switch_by_time=%s",
        cfg.settings_sequence,
        cfg.force_settings_profile,
        cfg.dynamic_forced_profile,
        cfg.dynamic_mode_switch_by_time,
    )

    for profile in profiles:
        payload, changed_fields = _build_payload(
            csrf_setting=client.csrf_setting,
            pcsid=client.pcsid,
            current=current,
            overrides=profile,
            value_maps=maps,
        )
        if not changed_fields:
            summary["setting_results"].append(
                {
                    "profile": profile.name,
                    "changed_fields": [],
                    "status": "skipped-no-change",
                }
            )
            continue

        ok, title, err, confirm_html = client.confirm_setting(payload)
        confirm_path = run_dir / f"confirm_{profile.name}.html"
        confirm_path.write_text(confirm_html, encoding="utf-8")

        if not ok:
            summary["setting_results"].append(
                {
                    "profile": profile.name,
                    "changed_fields": changed_fields,
                    "status": "confirm-failed",
                    "title": title,
                    "error": err,
                    "confirm_path": str(confirm_path),
                }
            )
            raise RuntimeError(f"KP-NET setting confirmation failed for profile={profile.name}: {err or title}")

        if cfg.dry_run:
            summary["setting_results"].append(
                {
                    "profile": profile.name,
                    "changed_fields": changed_fields,
                    "status": "dry-run-confirmed",
                    "title": title,
                    "confirm_path": str(confirm_path),
                }
            )
            continue

        write_result = client.write_setting(confirm_html)
        summary["setting_results"].append(
            {
                "profile": profile.name,
                "changed_fields": changed_fields,
                "status": "applied",
                "write_result": write_result,
                "confirm_path": str(confirm_path),
            }
        )
        current = client.read_current_settings()


def run_kpnet_workflow() -> int:
    load_dotenv_if_present()
    _setup_logging()
    cfg = KpNetConfig.from_env()

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = cfg.artifacts_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    client = KpNetClient(cfg)

    summary: dict[str, Any] = {
        "run_id": run_id,
        "workflow_mode": cfg.workflow_mode,
        "settings_sequence": cfg.settings_sequence,
        "dry_run": cfg.dry_run,
        "csv_downloads": [],
        "setting_results": [],
        "plot": {},
    }

    try:
        client.login()
        if cfg.workflow_mode in {"all", "csv"}:
            _run_csv_phase(client=client, cfg=cfg, run_dir=run_dir, summary=summary)
        if cfg.workflow_mode in {"all", "settings"}:
            _run_settings_phase(client=client, cfg=cfg, run_dir=run_dir, summary=summary)

        return_code = 0
    except Exception as exc:
        LOGGER.exception("KP-NET workflow failed")
        summary["error"] = str(exc)
        return_code = 1
    finally:
        try:
            client.logout()
        except Exception:
            LOGGER.exception("Logout failed")

        summary_path = run_dir / "kpnet_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("Summary saved: %s", summary_path)

    return return_code


def main() -> int:
    return run_kpnet_workflow()
