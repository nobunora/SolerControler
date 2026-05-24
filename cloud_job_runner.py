from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


_SECRET_KEYWORDS = ("password", "passwd", "secret", "token", "key")


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
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_plan_meta(plan_path: Path) -> dict[str, float | str | None]:
    obj = json.loads(plan_path.read_text(encoding="utf-8"))
    forecast = obj.get("forecast", {})
    result = obj.get("result", {})
    inputs = obj.get("inputs", {})
    return {
        "date": str(forecast.get("date", "")).strip(),
        "sun_hours": _to_float_or_none(forecast.get("sun_hours", 0.0)) or 0.0,
        "temp_c": _to_float_or_none(forecast.get("temp_c", 0.0)) or 0.0,
        "target_soc_7_percent": _to_float_or_none(result.get("target_soc_7_percent", 0.0)) or 0.0,
        "required_night_charge_kwh": _to_float_or_none(result.get("required_night_charge_kwh", 0.0)) or 0.0,
        "soc_now_percent": _to_float_or_none(inputs.get("soc_now_percent")) if isinstance(inputs, dict) else None,
        "effective_capacity_kwh": _to_float_or_none(result.get("effective_capacity_kwh")) if isinstance(result, dict) else None,
    }


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


def _force_partial_soc_window() -> tuple[float, float]:
    partial_min = float(os.getenv("KP_FORCE_PARTIAL_SOC_MIN_PERCENT", "51").strip() or "51")
    partial_max = float(os.getenv("KP_FORCE_PARTIAL_SOC_MAX_PERCENT", "100").strip() or "100")
    return partial_min, partial_max


def _should_stage_partial_forced(
    *,
    plan_meta: dict[str, float | str | None],
    green_mode_max_charge_percent: float,
) -> tuple[bool, float, float]:
    required_charge_percent = _required_charge_percent_from_plan(plan_meta)
    target_soc = max(0.0, float(plan_meta.get("target_soc_7_percent", 0.0) or 0.0))
    partial_min, partial_max = _force_partial_soc_window()
    force_charge = required_charge_percent >= green_mode_max_charge_percent
    stage_partial_forced = force_charge and partial_min <= target_soc <= partial_max
    return stage_partial_forced, required_charge_percent, target_soc


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


def _latest_soc_percent(csv_paths: list[Path]) -> float | None:
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
    return latest_soc


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


def _compute_force_activation_delay_seconds(
    *,
    cutoff_seconds: int,
    estimated_charge_minutes: int,
    start_advance_minutes: int,
) -> int:
    lead_seconds = max(0, (estimated_charge_minutes + start_advance_minutes) * 60)
    return max(0, cutoff_seconds - lead_seconds)


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


def _monitor_partial_forced_and_stop(plan_path: Path) -> None:
    if not plan_path.exists():
        print(f"[cloud_job_runner] 03-monitor plan missing: {plan_path}", flush=True)
        return

    green_mode_max = float(os.getenv("KP_GREEN_MODE_MAX_CHARGE_PERCENT", "50").strip() or "50")
    plan_meta = _read_plan_meta(plan_path)
    stage_partial, required_charge_percent, target_soc = _should_stage_partial_forced(
        plan_meta=plan_meta,
        green_mode_max_charge_percent=green_mode_max,
    )
    if not stage_partial:
        print(
            "[cloud_job_runner] 03-monitor skip "
            f"required={required_charge_percent:.2f}% target_soc={target_soc:.2f}%",
            flush=True,
        )
        return

    default_power_kw = float(os.getenv("KP_DEFAULT_CHARGE_POWER_KW", "1.8").strip() or "1.8")
    if default_power_kw <= 0:
        default_power_kw = 1.8
    artifacts_dir = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
    csv_paths = _latest_kpnet_csv_paths(artifacts_dir)
    latest_soc = _latest_soc_percent(csv_paths)
    required_kwh = _estimate_required_charge_kwh(plan_meta=plan_meta, latest_soc_percent=latest_soc)
    estimated_charge_minutes = 0
    if required_kwh > 0:
        estimated_charge_minutes = int((required_kwh / default_power_kw) * 60.0 + 0.9999)
    start_advance_minutes = max(0, int(os.getenv("ADJUST03_FORCE_START_ADVANCE_MINUTES", "0").strip() or "0"))
    poll_seconds = max(60, int(os.getenv("ADJUST03_FORCE_MONITOR_POLL_SECONDS", "180").strip() or "180"))
    soc_margin = max(0.0, float(os.getenv("ADJUST03_FORCE_STOP_SOC_MARGIN_PERCENT", "1.0").strip() or "1.0"))
    timezone_name = os.getenv("TIMEZONE", "Asia/Tokyo").strip() or "Asia/Tokyo"
    cutoff_hhmm = os.getenv("ADJUST03_FORCE_MONITOR_CUTOFF_HHMM", "07:00").strip() or "07:00"
    cutoff_seconds = _seconds_until_cutoff(timezone_name=timezone_name, cutoff_hhmm=cutoff_hhmm)
    delay_seconds = _compute_force_activation_delay_seconds(
        cutoff_seconds=cutoff_seconds,
        estimated_charge_minutes=estimated_charge_minutes,
        start_advance_minutes=start_advance_minutes,
    )
    if cutoff_seconds <= 0:
        print("[cloud_job_runner] 03-monitor cutoff already reached; switch to green immediately.", flush=True)
        _run_settings_profile(profile="green", dynamic_forced_profile=False)
        return

    print(
        "[cloud_job_runner] 03-monitor schedule "
        f"target_soc={target_soc:.2f}% latest_soc={latest_soc if latest_soc is not None else 'n/a'} "
        f"required={required_kwh:.3f}kWh "
        f"estimated={estimated_charge_minutes}min advance={start_advance_minutes}min "
        f"delay_before_force={delay_seconds}s poll={poll_seconds}s cutoff={cutoff_hhmm}",
        flush=True,
    )
    refresh_hhmm = os.getenv("ADJUST03_REFRESH_HHMM", "03:10").strip() or "03:10"
    refresh_enabled = os.getenv("ADJUST03_REFRESH_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    if refresh_enabled and delay_seconds > 0:
        refresh_seconds = _seconds_until_cutoff(timezone_name=timezone_name, cutoff_hhmm=refresh_hhmm)
        if 0 < refresh_seconds < delay_seconds:
            _sleep_with_progress(refresh_seconds, label="03-monitor wait-for-refresh")
            _refresh_plan_for_same_date_if_changed(plan_path)
            plan_meta = _read_plan_meta(plan_path)
            csv_paths = _latest_kpnet_csv_paths(artifacts_dir)
            latest_soc = _latest_soc_percent(csv_paths)
            required_kwh = _estimate_required_charge_kwh(plan_meta=plan_meta, latest_soc_percent=latest_soc)
            estimated_charge_minutes = int((required_kwh / default_power_kw) * 60.0 + 0.9999) if required_kwh > 0 else 0
            cutoff_seconds = _seconds_until_cutoff(timezone_name=timezone_name, cutoff_hhmm=cutoff_hhmm)
            delay_seconds = _compute_force_activation_delay_seconds(
                cutoff_seconds=cutoff_seconds,
                estimated_charge_minutes=estimated_charge_minutes,
                start_advance_minutes=start_advance_minutes,
            )
            target_soc = max(0.0, float(plan_meta.get("target_soc_7_percent", 0.0) or 0.0))
            print(
                "[cloud_job_runner] 03-monitor refreshed schedule "
                f"target_soc={target_soc:.2f}% latest_soc={latest_soc if latest_soc is not None else 'n/a'} "
                f"required={required_kwh:.3f}kWh delay_before_force={delay_seconds}s",
                flush=True,
            )
    if delay_seconds > 0:
        _sleep_with_progress(delay_seconds, label="03-monitor wait-for-forced-start")

    # 強制充電モードは時間設定を無視して即時充電を開始するため、
    # 7時逆算時刻でのみ強制モードへ切り替える。
    _run_settings_profile(profile="forced", dynamic_forced_profile=True)

    monitor_seconds = _seconds_until_cutoff(timezone_name=timezone_name, cutoff_hhmm=cutoff_hhmm)
    if monitor_seconds <= 0:
        print("[cloud_job_runner] 03-monitor no monitor window after forced-start; switch to green.", flush=True)
        _run_settings_profile(profile="green", dynamic_forced_profile=False)
        return

    print(
        f"[cloud_job_runner] 03-monitor forced-started monitor={monitor_seconds}s until cutoff={cutoff_hhmm}",
        flush=True,
    )
    started_at = time.time()
    while time.time() - started_at < monitor_seconds:
        _run([sys.executable, "kpnet_main.py"], {"KP_WORKFLOW_MODE": "csv"})
        csv_paths = _latest_kpnet_csv_paths(artifacts_dir)
        latest_soc = _latest_soc_percent(csv_paths)
        if latest_soc is not None:
            print(
                f"[cloud_job_runner] 03-monitor latest_soc={latest_soc:.2f}% "
                f"target={target_soc:.2f}% margin={soc_margin:.2f}%",
                flush=True,
            )
            if latest_soc >= (target_soc - soc_margin):
                if target_soc >= 100.0:
                    print("[cloud_job_runner] 03-monitor target reached at 100%. keep forced until cutoff.", flush=True)
                else:
                    print("[cloud_job_runner] 03-monitor target reached. switch to green profile.", flush=True)
                    _run_settings_profile(profile="green", dynamic_forced_profile=False)
                    return
        else:
            print("[cloud_job_runner] 03-monitor latest SOC unavailable.", flush=True)

        remaining = monitor_seconds - int(time.time() - started_at)
        if remaining <= poll_seconds:
            break
        time.sleep(poll_seconds)

    print("[cloud_job_runner] 03-monitor timer reached. switch to green profile.", flush=True)
    _run_settings_profile(profile="green", dynamic_forced_profile=False)


def _run_night_23() -> None:
    # 23:00 実行:
    # 1) CSV取得 2) 夜間計画計算 3) 強制充電設定登録
    _run(
        [sys.executable, "kpnet_main.py"],
        {
            "KP_WORKFLOW_MODE": "csv",
        },
    )
    _run([sys.executable, "energy_model_main.py"])
    plan_path = Path(os.getenv("KP_NIGHT_PLAN_PATH", "artifacts/night_charge_plan.json"))
    profile = "forced"
    dynamic_forced_profile = True
    if plan_path.exists():
        try:
            green_mode_max = float(os.getenv("KP_GREEN_MODE_MAX_CHARGE_PERCENT", "50").strip() or "50")
            plan_meta = _read_plan_meta(plan_path)
            stage_partial, required_charge_percent, target_soc = _should_stage_partial_forced(
                plan_meta=plan_meta,
                green_mode_max_charge_percent=green_mode_max,
            )
            if stage_partial:
                # 強制充電モードは設定直後に充電開始するため、
                # 51-99% の部分強制充電は 23時にはグリーン待機へ寄せ、
                # 03時の再設定 + 監視停止処理で制御する。
                profile = "green"
                dynamic_forced_profile = True
            print(
                "[cloud_job_runner] 23-night plan "
                f"target_soc={target_soc:.2f}% required={required_charge_percent:.2f}% "
                f"stage_partial={stage_partial} profile={profile}",
                flush=True,
            )
        except Exception as exc:
            print(f"[cloud_job_runner] 23-night partial-force staging skipped: {exc}", flush=True)
    else:
        print(f"[cloud_job_runner] 23-night plan missing: {plan_path}", flush=True)

    _run_settings_profile(profile=profile, dynamic_forced_profile=dynamic_forced_profile)
    _run(
        [sys.executable, "db_pipeline_main.py"],
        {
            "CLOUD_JOB_SLOT": "23",
        },
    )
    _run_optional(
        [sys.executable, "sheets_export_main.py"],
        {
            "CLOUD_JOB_SLOT": "23",
        },
        label="sheets-export",
    )


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

    _run([sys.executable, "energy_model_main.py"], {"FORECAST_DATE_OVERRIDE": target_date})
    current = _read_plan_signature(plan_path)
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
                "DATA_PREFER_NIGHT_PLAN_METRICS": "true",
            },
        )
    return changed


def _run_adjust_03() -> None:
    # 夜間コントローラ:
    # 1) 00時台にCSVを取得して現在SOCを把握
    # 2) 必要なら3時台に23時計画と同じ対象日の予報だけ再確認
    # 3) 7時から逆算した時刻に強制充電を開始し、目標/7時まで監視
    _run(
        [sys.executable, "kpnet_main.py"],
        {
            "KP_WORKFLOW_MODE": "csv",
        },
    )
    plan_path = Path(os.getenv("KP_NIGHT_PLAN_PATH", "artifacts/night_charge_plan.json"))
    if not plan_path.exists():
        raise RuntimeError(f"night charge plan not found: {plan_path}")
    _monitor_partial_forced_and_stop(plan_path)


def _run_day_07() -> None:
    # 07:00 実行:
    # 日中運用向けにグリーンモード設定のみ登録
    _run_settings_profile(profile="green", dynamic_forced_profile=False)


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
