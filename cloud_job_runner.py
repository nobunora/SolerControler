from __future__ import annotations

import csv
import json
import math
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from app.soc_decision_feedback import build_soc_decision_feedback


_SECRET_KEYWORDS = ("password", "passwd", "secret", "token", "key")


@dataclass(frozen=True)
class SocReading:
    value_percent: float | None
    source: str
    error: str | None
    observed_at: datetime | None


def _mask_env_updates(env_updates: dict[str, str] | None) -> dict[str, str]:
    if not env_updates:
        return {}
    masked: dict[str, str] = {}
    for key, value in env_updates.items():
        lower_key = key.lower()
        if any(word in lower_key for word in _SECRET_KEYWORDS):
            masked[key] = "***"
        else:
            masked[key] = value
    return masked


def _run(command: Iterable[str], env_updates: dict[str, str] | None = None) -> None:
    env = os.environ.copy()
    if env_updates:
        env.update(env_updates)
    cmd = list(command)
    print(
        f"[cloud_job_runner] run: {' '.join(cmd)} env_updates={_mask_env_updates(env_updates)}",
        flush=True,
    )
    completed = subprocess.run(cmd, env=env, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed (rc={completed.returncode}): {' '.join(cmd)}")


def _run_optional(command: Iterable[str], env_updates: dict[str, str] | None = None, *, label: str) -> None:
    try:
        _run(command, env_updates)
    except Exception as exc:
        print(f"[cloud_job_runner] optional step failed ({label}): {exc}", flush=True)


def _to_float_or_none(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _read_plan_meta(plan_path: Path) -> dict[str, float | str | None]:
    obj = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"night plan root must be an object: {plan_path}")
    forecast = obj.get("forecast", {})
    result = obj.get("result", {})
    inputs = obj.get("inputs", {})
    plan_quality = obj.get("plan_quality", {})
    if not isinstance(forecast, dict):
        raise RuntimeError(f"night plan forecast must be an object: {plan_path}")
    if not isinstance(result, dict):
        raise RuntimeError(f"night plan result must be an object: {plan_path}")
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, dict):
        raise RuntimeError(f"night plan inputs must be an object: {plan_path}")
    if isinstance(plan_quality, dict) and plan_quality.get("should_apply") is False:
        raise RuntimeError(f"night plan is not safe to apply: plan_quality={plan_quality}")
    forecast_date = str(forecast.get("date", "")).strip()
    if not forecast_date:
        raise RuntimeError(f"night plan forecast.date is missing: {plan_path}")
    target_soc = _required_plan_float(
        result,
        key="target_soc_7_percent",
        min_value=0.0,
        max_value=100.0,
        plan_path=plan_path,
    )
    required_kwh = _required_plan_float(
        result,
        key="required_night_charge_kwh",
        min_value=0.0,
        plan_path=plan_path,
    )
    return {
        "date": forecast_date,
        "sun_hours": _to_float_or_none(forecast.get("sun_hours", 0.0)) or 0.0,
        "temp_c": _to_float_or_none(forecast.get("temp_c", 0.0)) or 0.0,
        "target_soc_7_percent": target_soc,
        "required_night_charge_kwh": required_kwh,
        "soc_now_percent": _to_float_or_none(inputs.get("soc_now_percent")),
        "effective_capacity_kwh": _to_float_or_none(result.get("effective_capacity_kwh")),
    }


def _required_plan_float(
    source: dict,
    *,
    key: str,
    plan_path: Path,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    if key not in source:
        raise RuntimeError(f"night plan result.{key} is missing: {plan_path}")
    value = _to_float_or_none(source.get(key))
    if value is None:
        raise RuntimeError(f"night plan result.{key} is not a finite number: {plan_path}")
    if min_value is not None and value < min_value:
        raise RuntimeError(f"night plan result.{key} is below {min_value}: {value}")
    if max_value is not None and value > max_value:
        raise RuntimeError(f"night plan result.{key} is above {max_value}: {value}")
    return value


def _read_plan_json(plan_path: Path) -> dict:
    return json.loads(plan_path.read_text(encoding="utf-8"))


def _plan_date_from_json(plan: dict) -> str:
    forecast = plan.get("forecast", {}) if isinstance(plan.get("forecast"), dict) else {}
    return str(forecast.get("date", "")).strip()


def _open_firestore_for_plan():
    backend = os.getenv("DATA_BACKEND", "").strip().lower()
    project_id = os.getenv("FIRESTORE_PROJECT_ID", "").strip()
    if backend != "firestore" and not project_id:
        return None
    try:
        from google.cloud import firestore
    except Exception as exc:
        print(f"[cloud_job_runner] Firestore unavailable for plan persistence: {exc}", flush=True)
        return None
    database_id = os.getenv("FIRESTORE_DATABASE_ID", "").strip() or "(default)"
    if project_id:
        return firestore.Client(project=project_id, database=database_id)
    return firestore.Client(database=database_id)


def _persist_night_plan_to_firestore(plan_path: Path, *, source: str) -> bool:
    if not plan_path.exists():
        print(f"[cloud_job_runner] plan persistence skipped; missing: {plan_path}", flush=True)
        return False
    client = _open_firestore_for_plan()
    if client is None:
        return False
    try:
        from app.night_plan_archive import (
            build_night_plan_firestore_document,
            read_plan_file,
            upload_night_plan_to_gcs,
        )

        plan = read_plan_file(plan_path)
        plan_date = _plan_date_from_json(plan)
        if not plan_date:
            print("[cloud_job_runner] plan persistence skipped; forecast.date missing", flush=True)
            return False
        now = datetime.now(ZoneInfo("UTC")).isoformat()
        archive_info = upload_night_plan_to_gcs(plan, forecast_date=plan_date)
        doc = build_night_plan_firestore_document(
            plan,
            source=source,
            updated_at=now,
            archive_info=archive_info,
        )
        coll = client.collection("night_charge_plans")
        coll.document(plan_date).set(doc, merge=True)
        latest_doc = build_night_plan_firestore_document(
            plan,
            source=source,
            updated_at=now,
            force_inline_detail=True,
            archive_info=archive_info,
        )
        coll.document("latest").set(latest_doc, merge=True)
        print(f"[cloud_job_runner] persisted night plan to Firestore date={plan_date}", flush=True)
        return True
    except Exception as exc:
        print(f"[cloud_job_runner] plan persistence failed: {exc}", flush=True)
        return False


def _restore_night_plan_from_firestore(plan_path: Path, *, target_date: str) -> bool:
    client = _open_firestore_for_plan()
    if client is None:
        return False
    try:
        from app.night_plan_archive import load_night_plan_detail_from_firestore_doc

        candidates = [target_date] if target_date else []
        candidates.append("latest")
        for doc_id in candidates:
            snap = client.collection("night_charge_plans").document(doc_id).get()
            if not snap.exists:
                continue
            data = snap.to_dict() or {}
            plan = load_night_plan_detail_from_firestore_doc(data)
            if not plan:
                continue
            plan_date = _plan_date_from_json(plan)
            if target_date and plan_date and plan_date != target_date:
                continue
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[cloud_job_runner] restored night plan from Firestore date={plan_date}", flush=True)
            return True
    except Exception as exc:
        print(f"[cloud_job_runner] plan restore failed: {exc}", flush=True)
    return False


def _persist_previous_day_soc_feedback(*, target_date: str, csv_paths: list[Path]) -> bool:
    enabled = os.getenv("SOC_DECISION_FEEDBACK_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return False
    client = _open_firestore_for_plan()
    if client is None:
        return False
    try:
        previous_date = (datetime.fromisoformat(target_date).date() - timedelta(days=1)).isoformat()
    except ValueError:
        print(f"[cloud_job_runner] SOC feedback skipped: invalid target_date={target_date}", flush=True)
        return False
    try:
        snap = client.collection("night_charge_plans").document(previous_date).get()
        if not snap.exists:
            print(f"[cloud_job_runner] SOC feedback skipped: previous plan missing date={previous_date}", flush=True)
            return False
        data = snap.to_dict() or {}
        plan_text = str(data.get("plan_json") or "").strip()
        plan = json.loads(plan_text) if plan_text else data
        feedback = build_soc_decision_feedback(
            plan=plan,
            csv_paths=csv_paths,
            target_date=previous_date,
            created_at=datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z"),
        )
        if not feedback:
            print(f"[cloud_job_runner] SOC feedback skipped: insufficient actual data date={previous_date}", flush=True)
            return False
        client.collection("soc_decision_feedback").document(previous_date).set(feedback, merge=True)
        print(
            "[cloud_job_runner] persisted SOC decision feedback "
            f"date={previous_date} best={feedback.get('best_target_soc_percent')}%",
            flush=True,
        )
        return True
    except Exception as exc:
        print(f"[cloud_job_runner] SOC feedback persistence failed: {exc}", flush=True)
        return False


def _hhmm_after_delay(*, timezone_name: str, delay_seconds: int) -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    scheduled = now + timedelta(seconds=max(0, delay_seconds))
    return scheduled.strftime("%H:%M")


def _persist_03_monitor_schedule_to_firestore(
    *,
    plan_meta: dict[str, float | str | None],
    charge_start_time: str,
    charge_end_time: str,
    target_soc: float,
    latest_soc: float | None,
    required_kwh: float,
    estimated_charge_minutes: int,
    default_power_kw: float,
    charge_rate_info: dict[str, float | int | str | None] | None = None,
    soc_source: str = "unknown",
) -> bool:
    """Store the 03 controller decision so the dashboard never guesses it."""
    client = _open_firestore_for_plan()
    if client is None:
        return False
    plan_date = str(plan_meta.get("date") or "").strip()
    if not plan_date:
        return False

    now_utc = datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z")
    event_id = f"{plan_date}-03-monitor-schedule"
    day_start = os.getenv("KP_DAY_DISCHARGE_WINDOW_START", "07:00").strip() or "07:00"
    day_end = os.getenv("KP_DAY_DISCHARGE_WINDOW_END", "23:00").strip() or "23:00"
    detail = {
        "plan_date": plan_date,
        "charge_start_time": charge_start_time,
        "charge_end_time": charge_end_time,
        "night_window_start": os.getenv("KP_NIGHT_CHARGE_WINDOW_START", "23:00").strip() or "23:00",
        "night_window_end": os.getenv("KP_NIGHT_CHARGE_WINDOW_END", "07:00").strip() or "07:00",
        "day_discharge_window_start": day_start,
        "day_discharge_window_end": day_end,
        "discharge_fixed_window": f"{day_start}-{day_end}",
        "soc_charge_mode": str(int(round(target_soc))),
        "mode": "forced",
        "battery_operating_mode": "forced",
        "estimated_charge_power_kw": default_power_kw,
        "latest_soc_percent_at_schedule": latest_soc,
        "soc_source": soc_source,
        "required_night_charge_kwh_at_schedule": required_kwh,
        "estimated_charge_minutes": estimated_charge_minutes,
        "schedule_source": "03-monitor",
    }
    if charge_rate_info:
        detail.update(
            {
                "estimated_charge_rate_percent_per_hour": charge_rate_info.get("percent_per_hour"),
                "charge_rate_source": charge_rate_info.get("source"),
                "charge_rate_sample_count": charge_rate_info.get("sample_count"),
                "required_charge_percent_at_schedule": charge_rate_info.get("required_charge_percent"),
            }
        )
    try:
        client.collection("settings_events").document(event_id).set(
            {
                "event_id": event_id,
                "run_id": event_id,
                "slot": "03",
                "profile": "forced-monitor",
                "status": "forced-started",
                "changed_fields_json": [],
                "detail_json": detail,
                "recorded_at": now_utc,
            },
            merge=True,
        )
        client.collection("night_charge_plans").document(plan_date).set(
            {"monitor_schedule": detail, "monitor_schedule_updated_at": now_utc},
            merge=True,
        )
        client.collection("night_charge_plans").document("latest").set(
            {"monitor_schedule": detail, "monitor_schedule_updated_at": now_utc},
            merge=True,
        )
        print(
            "[cloud_job_runner] persisted 03-monitor schedule "
            f"date={plan_date} start={charge_start_time} end={charge_end_time}",
            flush=True,
        )
        return True
    except Exception as exc:
        print(f"[cloud_job_runner] 03-monitor schedule persistence failed: {exc}", flush=True)
        return False


def _persist_03_no_charge_decision_to_firestore(
    *,
    plan_meta: dict[str, float | str | None],
    target_soc: float,
    latest_soc: float | None,
    required_kwh: float,
    soc_source: str = "unknown",
) -> bool:
    client = _open_firestore_for_plan()
    if client is None:
        return False
    plan_date = str(plan_meta.get("date") or "").strip()
    if not plan_date:
        return False

    now_utc = datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z")
    event_id = f"{plan_date}-03-no-charge"
    detail = {
        "plan_date": plan_date,
        "charge_end_time": os.getenv("KP_NIGHT_CHARGE_WINDOW_END", "07:00").strip() or "07:00",
        "soc_charge_mode": str(int(round(target_soc))),
        "mode": "standby",
        "battery_operating_mode": "standby",
        "latest_soc_percent_at_schedule": latest_soc,
        "soc_source": soc_source,
        "required_night_charge_kwh_at_schedule": required_kwh,
        "schedule_source": "03-no-charge",
    }
    try:
        client.collection("settings_events").document(event_id).set(
            {
                "event_id": event_id,
                "run_id": event_id,
                "slot": "03",
                "profile": "standby",
                "status": "skipped-no-charge",
                "changed_fields_json": [],
                "detail_json": detail,
                "recorded_at": now_utc,
            },
            merge=True,
        )
        client.collection("night_charge_plans").document(plan_date).set(
            {"monitor_decision": detail, "monitor_decision_updated_at": now_utc},
            merge=True,
        )
        client.collection("night_charge_plans").document("latest").set(
            {"monitor_decision": detail, "monitor_decision_updated_at": now_utc},
            merge=True,
        )
        print(f"[cloud_job_runner] persisted 03 no-charge decision date={plan_date}", flush=True)
        return True
    except Exception as exc:
        print(f"[cloud_job_runner] 03 no-charge decision persistence failed: {exc}", flush=True)
        return False


def _required_charge_percent_from_plan(plan_meta: dict[str, float | str | None]) -> float:
    target_soc = max(0.0, float(plan_meta.get("target_soc_7_percent", 0.0) or 0.0))
    soc_now_raw = plan_meta.get("soc_now_percent", None)
    if isinstance(soc_now_raw, (int, float)):
        soc_now = max(0.0, min(100.0, float(soc_now_raw)))
        return max(0.0, target_soc - soc_now)

    cap_raw = plan_meta.get("effective_capacity_kwh", None)
    required_raw = plan_meta.get("required_night_charge_kwh", 0.0)
    if isinstance(cap_raw, (int, float)) and isinstance(required_raw, (int, float)) and cap_raw > 0 and required_raw > 0:
        return max(0.0, 100.0 * float(required_raw) / float(cap_raw))
    return target_soc


def _should_keep_standby_without_charge(
    *,
    required_charge_percent: float,
    required_charge_kwh: float,
) -> bool:
    percent_epsilon = _env_float("ADJUST03_NO_CHARGE_PERCENT_EPSILON", 0.5, min_value=0.0)
    kwh_epsilon = _env_float("ADJUST03_NO_CHARGE_KWH_EPSILON", 0.05, min_value=0.0)
    return required_charge_percent <= percent_epsilon and required_charge_kwh <= kwh_epsilon


def _estimate_required_charge_kwh(
    *,
    plan_meta: dict[str, float | str | None],
    latest_soc_percent: float | None,
) -> float:
    target_soc = max(0.0, min(100.0, float(plan_meta.get("target_soc_7_percent", 0.0) or 0.0)))
    cap_raw = plan_meta.get("effective_capacity_kwh")
    if latest_soc_percent is not None and isinstance(cap_raw, (int, float)) and cap_raw > 0:
        soc_now = max(0.0, min(100.0, latest_soc_percent))
        eta = max(0.7, float(os.getenv("KP_NIGHT_CHARGE_EFFICIENCY", "0.93").strip() or "0.93"))
        return max(0.0, ((target_soc - soc_now) / 100.0 * float(cap_raw)) / eta)
    return max(0.0, float(plan_meta.get("required_night_charge_kwh", 0.0) or 0.0))


def _latest_kpnet_csv_paths(artifacts_dir: Path) -> list[Path]:
    run_dirs = [p for p in artifacts_dir.glob("*") if p.is_dir() and p.name[:8].isdigit()]
    run_dirs.sort(key=lambda p: p.name, reverse=True)
    for run_dir in run_dirs:
        csv_dir = run_dir / "csv"
        csvs = sorted(csv_dir.glob("*.csv"))
        if csvs:
            return csvs
    return []


def _latest_csv_soc_reading(csv_paths: list[Path]) -> tuple[float | None, datetime | None]:
    latest_dt: datetime | None = None
    latest_soc: float | None = None
    for csv_path in csv_paths:
        if not csv_path.exists():
            continue
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date_text = (row.get("年月日") or "").strip()
                time_text = (row.get("時刻") or "").strip()
                soc_text = (row.get("蓄電残量(SOC)[%]") or "").strip()
                if not date_text or not time_text or not soc_text:
                    continue
                try:
                    dt = datetime.strptime(f"{date_text} {time_text}", "%Y/%m/%d %H:%M")
                    soc = float(soc_text)
                except (TypeError, ValueError):
                    continue
                if latest_dt is None or dt > latest_dt:
                    latest_dt = dt
                    latest_soc = soc
    return latest_soc, latest_dt


def _latest_soc_percent(csv_paths: list[Path]) -> float | None:
    return _latest_csv_soc_reading(csv_paths)[0]


def _latest_realtime_soc_percent() -> float | None:
    from app.kpnet_workflow import KpNetClient, KpNetConfig

    client = KpNetClient(KpNetConfig.from_env())
    client.login()
    try:
        return client.read_realtime_soc_percent()
    finally:
        client.logout()


def _read_soc_with_fallback(csv_paths: list[Path]) -> SocReading:
    attempts = _env_int("ADJUST03_REALTIME_SOC_RETRY_ATTEMPTS", 3, min_value=1)
    delay_seconds = _env_float("ADJUST03_REALTIME_SOC_RETRY_DELAY_SECONDS", 2.0, min_value=0.0)
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            value = _latest_realtime_soc_percent()
            if value is not None:
                return SocReading(value, "realtime", None, datetime.now(ZoneInfo("UTC")))
            errors.append("realtime returned no SOC")
        except Exception as exc:
            errors.append(str(exc))
        if attempt < attempts and delay_seconds > 0:
            time.sleep(delay_seconds)

    csv_value, csv_observed_at = _latest_csv_soc_reading(csv_paths)
    if csv_value is not None and csv_observed_at is not None:
        timezone_name = os.getenv("TIMEZONE", "Asia/Tokyo").strip() or "Asia/Tokyo"
        observed_local = csv_observed_at.replace(tzinfo=ZoneInfo(timezone_name))
        max_age_minutes = _env_int("ADJUST03_CSV_SOC_MAX_AGE_MINUTES", 120, min_value=0)
        age = datetime.now(ZoneInfo(timezone_name)) - observed_local
        if timedelta(0) <= age <= timedelta(minutes=max_age_minutes):
            return SocReading(csv_value, "csv", "; ".join(errors) or None, observed_local)
        errors.append(f"CSV SOC is stale: observed_at={csv_observed_at.isoformat()}")
    else:
        errors.append("CSV SOC unavailable")
    return SocReading(None, "unavailable", "; ".join(errors), csv_observed_at)


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


def _estimate_forced_charge_rate_percent_per_hour(csv_paths: list[Path]) -> dict[str, float | int | str]:
    """Estimate forced charging by observed SOC gain, not nominal kW.

    Forced mode starts charging immediately, so a slow estimate over-waits and
    misses the 07:00 target. We only use high charge-energy intervals to avoid
    mixing in green-mode/PV trickle charging.
    """
    fallback = float(os.getenv("ADJUST03_FORCE_CHARGE_RATE_FALLBACK_PERCENT_PER_HOUR", "35").strip() or "35")
    min_rate = float(os.getenv("ADJUST03_FORCE_CHARGE_RATE_MIN_PERCENT_PER_HOUR", "25").strip() or "25")
    max_rate = float(os.getenv("ADJUST03_FORCE_CHARGE_RATE_MAX_PERCENT_PER_HOUR", "50").strip() or "50")
    min_charge_kwh = float(os.getenv("ADJUST03_FORCE_CHARGE_SAMPLE_MIN_KWH", "1.2").strip() or "1.2")
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


def _estimate_required_charge_percent_for_schedule(
    *,
    plan_meta: dict[str, float | str | None],
    latest_soc_percent: float | None,
) -> float:
    target_soc = max(0.0, min(100.0, float(plan_meta.get("target_soc_7_percent", 0.0) or 0.0)))
    if latest_soc_percent is not None:
        soc_now = max(0.0, min(100.0, latest_soc_percent))
        return max(0.0, target_soc - soc_now)
    return _required_charge_percent_from_plan(plan_meta)


def _estimate_forced_charge_minutes(
    *,
    plan_meta: dict[str, float | str | None],
    latest_soc_percent: float | None,
    csv_paths: list[Path],
) -> tuple[int, dict[str, float | int | str]]:
    charge_rate_info = _estimate_forced_charge_rate_percent_per_hour(csv_paths)
    required_percent = _estimate_required_charge_percent_for_schedule(
        plan_meta=plan_meta,
        latest_soc_percent=latest_soc_percent,
    )
    rate = max(1.0, float(charge_rate_info["percent_per_hour"]))
    minutes = int(math.ceil((required_percent / rate) * 60.0)) if required_percent > 0 else 0
    charge_rate_info["required_charge_percent"] = required_percent
    return minutes, charge_rate_info


class ForcedChargeCompletionEstimator:
    """Estimate the next SOC confirmation time while forced charging is active."""

    def __init__(self, *, rate_percent_per_hour: float, confirm_before_minutes: int = 5) -> None:
        self.rate_percent_per_hour = max(1.0, float(rate_percent_per_hour))
        self.confirm_before_minutes = max(0, int(confirm_before_minutes))

    def remaining_minutes(self, *, target_soc: float, latest_soc: float) -> int:
        required_percent = max(0.0, min(100.0, target_soc) - max(0.0, min(100.0, latest_soc)))
        if required_percent <= 0:
            return 0
        return int(math.ceil((required_percent / self.rate_percent_per_hour) * 60.0))

    def next_check_seconds(
        self,
        *,
        target_soc: float,
        latest_soc: float | None,
        fallback_poll_seconds: int,
        cutoff_seconds: int,
    ) -> int:
        fallback = max(60, int(fallback_poll_seconds))
        cutoff = max(0, int(cutoff_seconds))
        if cutoff <= 0:
            return 0
        if latest_soc is None:
            return min(fallback, cutoff)
        remaining = self.remaining_minutes(target_soc=target_soc, latest_soc=latest_soc)
        if remaining <= 0:
            return 0
        check_seconds = max(60, (remaining - self.confirm_before_minutes) * 60)
        return min(check_seconds, fallback, cutoff)


def _seconds_until_cutoff(*, timezone_name: str, cutoff_hhmm: str) -> int:
    hhmm = cutoff_hhmm.strip()
    if not hhmm or ":" not in hhmm:
        return 0
    hh_text, mm_text = hhmm.split(":", 1)
    hh = int(hh_text)
    mm = int(mm_text)
    now = datetime.now(ZoneInfo(timezone_name))
    cutoff = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return max(0, int((cutoff - now).total_seconds()))


def _sleep_with_progress(total_seconds: int, *, label: str, chunk_seconds: int = 300) -> None:
    remaining = max(0, int(total_seconds))
    if remaining <= 0:
        return
    chunk = max(30, int(chunk_seconds))
    while remaining > 0:
        current = min(chunk, remaining)
        print(
            f"[cloud_job_runner] {label} sleep={current}s remaining_after={max(0, remaining - current)}s",
            flush=True,
        )
        time.sleep(current)
        remaining -= current


def _run_settings_profile(*, profile: str, dynamic_forced_profile: bool) -> None:
    _run(
        [sys.executable, "kpnet_main.py"],
        {
            "KP_WORKFLOW_MODE": "settings",
            "KP_FORCE_SETTINGS_PROFILE": profile,
            "KP_DYNAMIC_FORCED_PROFILE": "true" if dynamic_forced_profile else "false",
            "KP_DYNAMIC_MODE_SWITCH_BY_TIME": "false",
        },
    )


def _env_int(name: str, default: int, *, min_value: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip() or str(default))
    except ValueError:
        value = default
    return max(min_value, value)


def _env_float(name: str, default: float, *, min_value: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip() or str(default))
    except ValueError:
        value = default
    return max(min_value, value)


def _run_operation_with_retry(
    operation,
    *,
    label: str,
    attempts_env: str = "KP_COMMAND_RETRY_ATTEMPTS",
    delay_env: str = "KP_COMMAND_RETRY_DELAY_SECONDS",
    default_attempts: int = 3,
    default_delay_seconds: float = 20.0,
):
    attempts = _env_int(attempts_env, default_attempts, min_value=1)
    delay_seconds = _env_float(delay_env, default_delay_seconds, min_value=0.0)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            print(
                f"[cloud_job_runner] retry {label} attempt={attempt}/{attempts} failed: {exc}; "
                f"sleep={delay_seconds}s",
                flush=True,
            )
            if delay_seconds > 0:
                time.sleep(delay_seconds)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_exc}") from last_exc


def _run_with_retry(
    command: Iterable[str],
    env_updates: dict[str, str] | None = None,
    *,
    label: str,
    attempts_env: str = "KP_COMMAND_RETRY_ATTEMPTS",
    delay_env: str = "KP_COMMAND_RETRY_DELAY_SECONDS",
    default_attempts: int = 3,
    default_delay_seconds: float = 20.0,
) -> None:
    _run_operation_with_retry(
        lambda: _run(command, env_updates),
        label=label,
        attempts_env=attempts_env,
        delay_env=delay_env,
        default_attempts=default_attempts,
        default_delay_seconds=default_delay_seconds,
    )


def _run_settings_profile_with_retry(
    *,
    profile: str,
    dynamic_forced_profile: bool,
    label: str | None = None,
) -> None:
    _run_operation_with_retry(
        lambda: _run_settings_profile(profile=profile, dynamic_forced_profile=dynamic_forced_profile),
        label=label or f"settings-profile-{profile}",
        attempts_env="KP_SETTINGS_RETRY_ATTEMPTS",
        delay_env="KP_SETTINGS_RETRY_DELAY_SECONDS",
        default_attempts=3,
        default_delay_seconds=30.0,
    )


def _run_csv_with_retry(*, label: str = "kpnet-csv") -> None:
    _run_with_retry(
        [sys.executable, "kpnet_main.py"],
        {"KP_WORKFLOW_MODE": "csv"},
        label=label,
        attempts_env="KP_CSV_RETRY_ATTEMPTS",
        delay_env="KP_CSV_RETRY_DELAY_SECONDS",
        default_attempts=3,
        default_delay_seconds=20.0,
    )


def _parse_hhmm_minutes(value: str, *, default: str) -> int:
    text = value.strip() or default
    if ":" not in text:
        text = default
    hh_text, mm_text = text.split(":", 1)
    try:
        hh = max(0, min(23, int(hh_text)))
        mm = max(0, min(59, int(mm_text)))
    except ValueError:
        hh_text, mm_text = default.split(":", 1)
        hh = max(0, min(23, int(hh_text)))
        mm = max(0, min(59, int(mm_text)))
    return hh * 60 + mm


def _adjust03_target_date(*, now: datetime | None = None) -> str:
    explicit = os.getenv("FORECAST_DATE_OVERRIDE", "").strip()
    if explicit:
        return explicit
    timezone_name = os.getenv("TIMEZONE", "Asia/Tokyo").strip() or "Asia/Tokyo"
    current = now or datetime.now(ZoneInfo(timezone_name))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo(timezone_name))
    else:
        current = current.astimezone(ZoneInfo(timezone_name))
    return current.date().isoformat()


def _ensure_night_plan_available(plan_path: Path) -> bool:
    target_date = _adjust03_target_date()
    regenerate = os.getenv("ADJUST03_REGENERATE_PLAN", "true").strip().lower() in {"1", "true", "yes", "on"}
    if regenerate:
        print(
            f"[cloud_job_runner] 03-plan regenerating target_date={target_date} path={plan_path}",
            flush=True,
        )
        try:
            _run_with_retry(
                [sys.executable, "energy_model_main.py"],
                {"FORECAST_DATE_OVERRIDE": target_date},
                label="03-regenerate-night-plan",
                attempts_env="ADJUST03_PLAN_RETRY_ATTEMPTS",
                delay_env="ADJUST03_PLAN_RETRY_DELAY_SECONDS",
                default_attempts=2,
                default_delay_seconds=30.0,
            )
            _persist_night_plan_to_firestore(plan_path, source="adjust03-regenerated")
            if plan_path.exists() and _night_plan_file_date(plan_path) == target_date:
                return True
        except Exception as exc:
            print(f"[cloud_job_runner] 03-plan regeneration failed; trying fallback plan: {exc}", flush=True)

    if plan_path.exists() and _night_plan_file_date(plan_path) == target_date:
        return True

    if _restore_night_plan_from_firestore(plan_path, target_date=target_date):
        return True

    print(
        f"[cloud_job_runner] 03-plan missing; regenerating target_date={target_date} path={plan_path}",
        flush=True,
    )
    _run_with_retry(
        [sys.executable, "energy_model_main.py"],
        {"FORECAST_DATE_OVERRIDE": target_date},
        label="03-regenerate-night-plan",
        attempts_env="ADJUST03_PLAN_RETRY_ATTEMPTS",
        delay_env="ADJUST03_PLAN_RETRY_DELAY_SECONDS",
        default_attempts=2,
        default_delay_seconds=30.0,
    )
    _persist_night_plan_to_firestore(plan_path, source="adjust03-regenerated")
    return plan_path.exists() and _night_plan_file_date(plan_path) == target_date


def _night_plan_file_date(plan_path: Path) -> str:
    try:
        obj = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    forecast = obj.get("forecast", {})
    if not isinstance(forecast, dict):
        return ""
    return str(forecast.get("date", "")).strip()


def _run_db_pipeline_slot(
    slot: str,
    *,
    include_csv: bool = True,
    include_settings: bool = True,
    extra_env: dict[str, str] | None = None,
) -> None:
    env = {
        "CLOUD_JOB_SLOT": slot,
        "DATA_PIPELINE_INCLUDE_CSV": "true" if include_csv else "false",
        "DATA_PIPELINE_INCLUDE_SETTINGS": "true" if include_settings else "false",
    }
    if extra_env:
        env.update(extra_env)
    _run(
        [sys.executable, "db_pipeline_main.py"],
        env,
    )


def _run_03_settings_profile_with_db(
    *,
    profile: str,
    dynamic_forced_profile: bool,
    label: str,
) -> None:
    _run_settings_profile_with_retry(
        profile=profile,
        dynamic_forced_profile=dynamic_forced_profile,
        label=label,
    )
    _run_db_pipeline_slot(
        "03",
        include_csv=False,
        include_settings=True,
        extra_env={
            "DATA_DB_WRITE_ONLY_23": "false",
            "DATA_PREFER_NIGHT_PLAN_METRICS": "true",
        },
    )


def _monitor_partial_forced_and_stop(plan_path: Path) -> None:
    if not plan_path.exists():
        print(f"[cloud_job_runner] 03-monitor plan missing: {plan_path}", flush=True)
        return

    plan_meta = _read_plan_meta(plan_path)
    required_charge_percent = _required_charge_percent_from_plan(plan_meta)
    target_soc = max(0.0, float(plan_meta.get("target_soc_7_percent", 0.0) or 0.0))
    artifacts_dir = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
    csv_paths = _latest_kpnet_csv_paths(artifacts_dir)
    soc_reading = _read_soc_with_fallback(csv_paths)
    latest_soc = soc_reading.value_percent
    print(
        f"[cloud_job_runner] 03-monitor SOC source={soc_reading.source} "
        f"error={soc_reading.error or 'none'}",
        flush=True,
    )
    required_kwh = _estimate_required_charge_kwh(plan_meta=plan_meta, latest_soc_percent=latest_soc)
    if latest_soc is not None:
        required_charge_percent = max(0.0, target_soc - latest_soc)
    if _should_keep_standby_without_charge(
        required_charge_percent=required_charge_percent,
        required_charge_kwh=required_kwh,
    ):
        _persist_03_no_charge_decision_to_firestore(
            plan_meta=plan_meta,
            target_soc=target_soc,
            latest_soc=latest_soc,
            soc_source=soc_reading.source,
            required_kwh=required_kwh,
        )
        print(
            "[cloud_job_runner] 03-monitor charge not needed; keep standby until 07:00 green transition. "
            f"required={required_charge_percent:.2f}% required_kwh={required_kwh:.3f} "
            f"target_soc={target_soc:.2f}% latest_soc={latest_soc if latest_soc is not None else 'n/a'}",
            flush=True,
        )
        return
    default_power_kw = float(os.getenv("KP_DEFAULT_CHARGE_POWER_KW", "1.8").strip() or "1.8")
    if default_power_kw <= 0:
        default_power_kw = 1.8
    estimated_charge_minutes, charge_rate_info = _estimate_forced_charge_minutes(
        plan_meta=plan_meta,
        latest_soc_percent=latest_soc,
        csv_paths=csv_paths,
    )
    poll_seconds = max(60, int(os.getenv("ADJUST03_FORCE_MONITOR_POLL_SECONDS", "180").strip() or "180"))
    soc_margin = max(0.0, float(os.getenv("ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT", "1.0").strip() or "1.0"))
    timezone_name = os.getenv("TIMEZONE", "Asia/Tokyo").strip() or "Asia/Tokyo"
    cutoff_hhmm = os.getenv("ADJUST03_FORCE_MONITOR_CUTOFF_HHMM", "07:00").strip() or "07:00"
    cutoff_seconds = _seconds_until_cutoff(timezone_name=timezone_name, cutoff_hhmm=cutoff_hhmm)
    if cutoff_seconds <= 0:
        print("[cloud_job_runner] 03-monitor cutoff already reached; keep standby until 07:00 job.", flush=True)
        _run_03_settings_profile_with_db(
            profile="standby",
            dynamic_forced_profile=False,
            label="03-cutoff-standby",
        )
        return

    charge_start_hhmm = _hhmm_after_delay(timezone_name=timezone_name, delay_seconds=0)
    print(
        "[cloud_job_runner] 03-monitor immediate forced charge "
        f"target_soc={target_soc:.2f}% latest_soc={latest_soc if latest_soc is not None else 'n/a'} "
        f"required={required_kwh:.3f}kWh "
        f"estimated={estimated_charge_minutes}min "
        f"rate={charge_rate_info.get('percent_per_hour')}%/h "
        f"samples={charge_rate_info.get('sample_count')} "
        f"poll={poll_seconds}s cutoff={cutoff_hhmm}",
        flush=True,
    )
    _persist_03_monitor_schedule_to_firestore(
        plan_meta=plan_meta,
        charge_start_time=charge_start_hhmm,
        charge_end_time=cutoff_hhmm,
        target_soc=target_soc,
        latest_soc=latest_soc,
        soc_source=soc_reading.source,
        required_kwh=required_kwh,
        estimated_charge_minutes=estimated_charge_minutes,
        default_power_kw=default_power_kw,
        charge_rate_info=charge_rate_info,
    )

    _run_03_settings_profile_with_db(profile="forced", dynamic_forced_profile=True, label="03-forced-start")

    monitor_seconds = _seconds_until_cutoff(timezone_name=timezone_name, cutoff_hhmm=cutoff_hhmm)
    if monitor_seconds <= 0:
        print("[cloud_job_runner] 03-monitor no monitor window after forced-start; switch to standby.", flush=True)
        _run_03_settings_profile_with_db(
            profile="standby",
            dynamic_forced_profile=False,
            label="03-no-window-standby",
        )
        return

    print(
        f"[cloud_job_runner] 03-monitor forced-started monitor={monitor_seconds}s until cutoff={cutoff_hhmm}",
        flush=True,
    )
    started_at = time.time()
    previous_soc = latest_soc
    stagnant_polls = 0
    reapply_enabled = os.getenv("ADJUST03_FORCE_REAPPLY_IF_SOC_NOT_INCREASING", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    reapply_after_polls = _env_int("ADJUST03_FORCE_REAPPLY_AFTER_POLLS", 2, min_value=1)
    reapply_min_delta = _env_float("ADJUST03_FORCE_REAPPLY_MIN_SOC_DELTA_PERCENT", 0.1, min_value=0.0)
    confirm_before_minutes = _env_int("ADJUST03_COMPLETION_CONFIRM_BEFORE_MINUTES", 5, min_value=0)
    completion_estimator = ForcedChargeCompletionEstimator(
        rate_percent_per_hour=float(charge_rate_info.get("percent_per_hour") or 1.0),
        confirm_before_minutes=confirm_before_minutes,
    )
    while time.time() - started_at < monitor_seconds:
        soc_reading = _read_soc_with_fallback(csv_paths)
        latest_soc = soc_reading.value_percent
        if soc_reading.error:
            print(
                f"[cloud_job_runner] 03-monitor SOC source={soc_reading.source} error={soc_reading.error}",
                flush=True,
            )
        if latest_soc is not None:
            print(
                f"[cloud_job_runner] 03-monitor latest_soc={latest_soc:.2f}% "
                f"target={target_soc:.2f}% margin={soc_margin:.2f}%",
                flush=True,
            )
            if latest_soc >= (target_soc - soc_margin):
                print("[cloud_job_runner] 03-monitor target reached. switch to standby profile.", flush=True)
                _run_03_settings_profile_with_db(
                    profile="standby",
                    dynamic_forced_profile=False,
                    label="03-target-standby",
                )
                return
            if (
                reapply_enabled
                and previous_soc is not None
                and latest_soc < (target_soc - soc_margin)
            ):
                if latest_soc <= previous_soc + reapply_min_delta:
                    stagnant_polls += 1
                else:
                    stagnant_polls = 0
                if stagnant_polls >= reapply_after_polls:
                    print(
                        "[cloud_job_runner] 03-monitor SOC not increasing; reapply forced profile "
                        f"latest={latest_soc:.2f}% previous={previous_soc:.2f}%",
                        flush=True,
                    )
                    _run_03_settings_profile_with_db(
                        profile="forced",
                        dynamic_forced_profile=True,
                        label="03-forced-reapply",
                    )
                    stagnant_polls = 0
            previous_soc = latest_soc
        else:
            print("[cloud_job_runner] 03-monitor latest SOC unavailable.", flush=True)

        remaining = monitor_seconds - int(time.time() - started_at)
        if remaining <= 0:
            break
        next_check_seconds = completion_estimator.next_check_seconds(
            target_soc=target_soc,
            latest_soc=latest_soc,
            fallback_poll_seconds=poll_seconds,
            cutoff_seconds=remaining,
        )
        if next_check_seconds <= 0:
            break
        print(
            "[cloud_job_runner] 03-monitor next check "
            f"sleep={next_check_seconds}s remaining_to_cutoff={remaining}s",
            flush=True,
        )
        time.sleep(next_check_seconds)

    print("[cloud_job_runner] 03-monitor timer reached. switch to standby profile.", flush=True)
    _run_03_settings_profile_with_db(profile="standby", dynamic_forced_profile=False, label="03-timer-standby")


def _run_night_23() -> None:
    # 23:00 is only a mode-control guard. Forecast/data work is centralized in
    # the 04:00 controller, which still has enough time to reach 100% if needed.
    profile = os.getenv("NIGHT23_SETTINGS_PROFILE", "standby").strip() or "standby"
    _run_settings_profile_with_retry(
        profile=profile,
        dynamic_forced_profile=False,
        label=f"23-settings-{profile}",
    )


def _run_optional_04_exports_and_backups() -> None:
    _run_optional(
        [sys.executable, "sheets_export_main.py"],
        {
            "CLOUD_JOB_SLOT": "03",
        },
        label="sheets-export",
    )
    if os.getenv("DRIVE_BACKUP_FOLDER_ID", "").strip():
        _run_optional(
            [sys.executable, "scripts/backup_drive.py", "--mode", os.getenv("DRIVE_BACKUP_MODE", "data").strip() or "data"],
            {
                "CLOUD_JOB_SLOT": "03",
            },
            label="drive-backup",
        )
    else:
        print("[cloud_job_runner] drive-backup skipped: DRIVE_BACKUP_FOLDER_ID is empty", flush=True)


def _read_plan_snapshot(plan_path: Path) -> tuple[str, float, float]:
    obj = json.loads(plan_path.read_text(encoding="utf-8"))
    forecast = obj.get("forecast", {})
    date = str(forecast.get("date", "")).strip()
    sun_h = float(forecast.get("sun_hours", 0.0) or 0.0)
    temp_c = float(forecast.get("temp_c", 0.0) or 0.0)
    return date, sun_h, temp_c


def _read_plan_signature(plan_path: Path) -> dict[str, float | str]:
    obj = json.loads(plan_path.read_text(encoding="utf-8"))
    forecast = obj.get("forecast", {}) if isinstance(obj.get("forecast"), dict) else {}
    result = obj.get("result", {}) if isinstance(obj.get("result"), dict) else {}
    pv_forecast = obj.get("pv_array_forecast", {}) if isinstance(obj.get("pv_array_forecast"), dict) else {}
    pv_totals = pv_forecast.get("totals", {}) if isinstance(pv_forecast.get("totals"), dict) else {}
    return {
        "date": str(forecast.get("date", "")).strip(),
        "sun_hours": float(forecast.get("sun_hours", 0.0) or 0.0),
        "temp_c": float(forecast.get("temp_c", 0.0) or 0.0),
        "target_soc_7_percent": float(result.get("target_soc_7_percent", 0.0) or 0.0),
        "required_night_charge_kwh": float(result.get("required_night_charge_kwh", 0.0) or 0.0),
        "predicted_midday_surplus_kwh": float(result.get("predicted_midday_surplus_kwh", 0.0) or 0.0),
        "forecast_pv_total_kwh": float(pv_totals.get("total_kwh", 0.0) or 0.0),
    }


def _forecast_changed(
    base: tuple[str, float, float],
    current: tuple[str, float, float],
    *,
    sun_epsilon_h: float,
    temp_epsilon_c: float,
) -> bool:
    if base[0] != current[0]:
        return True
    if abs(base[1] - current[1]) >= sun_epsilon_h:
        return True
    if abs(base[2] - current[2]) >= temp_epsilon_c:
        return True
    return False


def _plan_signature_changed(
    base: dict[str, float | str],
    current: dict[str, float | str],
    *,
    sun_epsilon_h: float,
    temp_epsilon_c: float,
    soc_epsilon_percent: float,
    kwh_epsilon: float,
) -> bool:
    if str(base.get("date", "")) != str(current.get("date", "")):
        return True
    if abs(float(base.get("sun_hours", 0.0)) - float(current.get("sun_hours", 0.0))) >= sun_epsilon_h:
        return True
    if abs(float(base.get("temp_c", 0.0)) - float(current.get("temp_c", 0.0))) >= temp_epsilon_c:
        return True
    if abs(float(base.get("target_soc_7_percent", 0.0)) - float(current.get("target_soc_7_percent", 0.0))) >= soc_epsilon_percent:
        return True
    for key in ("required_night_charge_kwh", "predicted_midday_surplus_kwh", "forecast_pv_total_kwh"):
        if abs(float(base.get(key, 0.0)) - float(current.get(key, 0.0))) >= kwh_epsilon:
            return True
    return False


def _refresh_plan_for_same_date_if_changed(plan_path: Path) -> bool:
    if not plan_path.exists():
        return False
    base = _read_plan_signature(plan_path)
    target_date = str(base.get("date", "")).strip()
    if not target_date:
        return False

    _run_with_retry(
        [sys.executable, "energy_model_main.py"],
        {"FORECAST_DATE_OVERRIDE": target_date},
        label="03-refresh-night-plan",
        attempts_env="ADJUST03_PLAN_RETRY_ATTEMPTS",
        delay_env="ADJUST03_PLAN_RETRY_DELAY_SECONDS",
        default_attempts=2,
        default_delay_seconds=30.0,
    )
    current = _read_plan_signature(plan_path)
    _persist_night_plan_to_firestore(plan_path, source="adjust03-refresh")
    changed = _plan_signature_changed(
        base,
        current,
        sun_epsilon_h=max(0.0, float(os.getenv("ADJUST03_SUN_EPSILON_H", "0.05").strip() or "0.05")),
        temp_epsilon_c=max(0.0, float(os.getenv("ADJUST03_TEMP_EPSILON_C", "0.2").strip() or "0.2")),
        soc_epsilon_percent=max(0.0, float(os.getenv("ADJUST03_SOC_EPSILON_PERCENT", "1.0").strip() or "1.0")),
        kwh_epsilon=max(0.0, float(os.getenv("ADJUST03_KWH_EPSILON", "0.2").strip() or "0.2")),
    )
    print(f"[cloud_job_runner] 03-refresh target_date={target_date} changed={changed}", flush=True)
    if changed:
        _run(
            [sys.executable, "db_pipeline_main.py"],
            {
                "CLOUD_JOB_SLOT": "03",
                "DATA_DB_WRITE_ONLY_23": "false",
                "DATA_PIPELINE_INCLUDE_SETTINGS": "false",
                "DATA_PREFER_NIGHT_PLAN_METRICS": "true",
            },
        )
    return changed


def _run_adjust_03() -> None:
    # 夜間コントローラ:
    # 1) 04:00にCSVを取得して現在SOCを把握
    # 2) 当日分の最新予報を04:00時点で再生成
    # 3) すぐ強制充電を開始し、目標到達または7時まで監視
    _run_csv_with_retry(label="03-initial-csv")
    artifacts_dir = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
    _persist_previous_day_soc_feedback(
        target_date=_adjust03_target_date(),
        csv_paths=_latest_kpnet_csv_paths(artifacts_dir),
    )
    plan_path = Path(os.getenv("KP_NIGHT_PLAN_PATH", "artifacts/night_charge_plan.json"))
    if not _ensure_night_plan_available(plan_path):
        raise RuntimeError(f"night charge plan not found: {plan_path}")
    _run_db_pipeline_slot(
        "03",
        include_csv=True,
        include_settings=False,
        extra_env={
            "DATA_DB_WRITE_ONLY_23": "false",
            "DATA_PREFER_NIGHT_PLAN_METRICS": "true",
        },
    )
    _monitor_partial_forced_and_stop(plan_path)
    _run_optional_04_exports_and_backups()


def _run_day_07() -> None:
    # 07:00 実行:
    # 日中運用向けにグリーンモード設定のみ登録
    _run_settings_profile_with_retry(profile="green", dynamic_forced_profile=False, label="07-green")


def main() -> int:
    slot = os.getenv("CLOUD_JOB_SLOT", "").strip().lower()
    if slot in {"23", "night", "night23"}:
        _run_night_23()
        return 0
    if slot in {"3", "03", "adjust", "adjust03"}:
        _run_adjust_03()
        return 0
    if slot in {"7", "07", "day", "day07"}:
        _run_day_07()
        return 0
    raise RuntimeError("CLOUD_JOB_SLOT は 23/night, 03/adjust, 07/day のいずれかを指定してください")


if __name__ == "__main__":
    raise SystemExit(main())
