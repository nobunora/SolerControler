from __future__ import annotations

import os
import subprocess
import sys
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
    if slot in {"7", "07", "day", "day07"}:
        _run_day_07()
        return 0
    raise RuntimeError("CLOUD_JOB_SLOT は 23/night または 07/day を指定してください")


if __name__ == "__main__":
    raise SystemExit(main())
