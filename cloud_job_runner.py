from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable


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
    _run(
        [sys.executable, "kpnet_main.py"],
        {
            "KP_WORKFLOW_MODE": "settings",
            "KP_FORCE_SETTINGS_PROFILE": "forced",
            "KP_DYNAMIC_FORCED_PROFILE": "true",
            "KP_DYNAMIC_MODE_SWITCH_BY_TIME": "false",
        },
    )
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


def _run_adjust_03() -> None:
    # 03:10 実行:
    # 1) CSVを1回取得
    # 2) 予報更新を確認しながら energy_model を最大3回再取得(10分間隔)
    # 3) 最終計画で夜間設定を再適用
    _run(
        [sys.executable, "kpnet_main.py"],
        {
            "KP_WORKFLOW_MODE": "csv",
        },
    )

    attempts_raw = os.getenv("ADJUST03_MAX_ATTEMPTS", "3").strip() or "3"
    wait_raw = os.getenv("ADJUST03_WAIT_SECONDS", "600").strip() or "600"
    sun_eps_raw = os.getenv("ADJUST03_SUN_EPSILON_H", "0.05").strip() or "0.05"
    temp_eps_raw = os.getenv("ADJUST03_TEMP_EPSILON_C", "0.2").strip() or "0.2"
    attempts = max(1, int(attempts_raw))
    wait_seconds = max(0, int(wait_raw))
    sun_epsilon_h = max(0.0, float(sun_eps_raw))
    temp_epsilon_c = max(0.0, float(temp_eps_raw))

    plan_path = Path(os.getenv("KP_NIGHT_PLAN_PATH", "artifacts/night_charge_plan.json"))
    baseline: tuple[str, float, float] | None = None
    latest: tuple[str, float, float] | None = None

    for attempt in range(1, attempts + 1):
        _run([sys.executable, "energy_model_main.py"])
        if not plan_path.exists():
            raise RuntimeError(f"night charge plan not found: {plan_path}")

        latest = _read_plan_snapshot(plan_path)
        print(
            "[cloud_job_runner] 03-adjust forecast "
            f"attempt={attempt}/{attempts} "
            f"date={latest[0]} sun_h={latest[1]:.3f} temp_c={latest[2]:.2f}",
            flush=True,
        )

        if baseline is None:
            baseline = latest
        elif _forecast_changed(
            baseline,
            latest,
            sun_epsilon_h=sun_epsilon_h,
            temp_epsilon_c=temp_epsilon_c,
        ):
            print("[cloud_job_runner] 03-adjust forecast updated. stop retry.", flush=True)
            break

        if attempt < attempts:
            print(
                f"[cloud_job_runner] 03-adjust no update. sleep {wait_seconds}s before retry.",
                flush=True,
            )
            time.sleep(wait_seconds)

    _run(
        [sys.executable, "kpnet_main.py"],
        {
            "KP_WORKFLOW_MODE": "settings",
            "KP_FORCE_SETTINGS_PROFILE": "forced",
            "KP_DYNAMIC_FORCED_PROFILE": "true",
            "KP_DYNAMIC_MODE_SWITCH_BY_TIME": "false",
        },
    )


def _run_day_07() -> None:
    # 07:00 実行:
    # 日中運用向けにグリーンモード設定のみ登録
    _run(
        [sys.executable, "kpnet_main.py"],
        {
            "KP_WORKFLOW_MODE": "settings",
            "KP_FORCE_SETTINGS_PROFILE": "green",
            "KP_DYNAMIC_FORCED_PROFILE": "false",
            "KP_DYNAMIC_MODE_SWITCH_BY_TIME": "false",
        },
    )


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
