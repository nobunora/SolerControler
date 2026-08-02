from __future__ import annotations

import os
import subprocess
import time
from typing import Callable, Iterable, TypeVar

_SECRET_KEYWORDS = ("password", "passwd", "secret", "token", "key")
_T = TypeVar("_T")
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


def _env_int(name: str, default: int, *, min_value: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip() or str(default))
    except ValueError:
        value = default
    return max(min_value, value)


# readable-code-audit: skip DUP-01 — Cloud Job must clamp retry and delay values to safe minima after malformed input fallback.
def _env_float(name: str, default: float, *, min_value: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip() or str(default))
    except ValueError:
        value = default
    return max(min_value, value)


def _run_operation_with_retry(
    operation: Callable[[], _T],
    *,
    label: str,
    attempts_env: str = "KP_COMMAND_RETRY_ATTEMPTS",
    delay_env: str = "KP_COMMAND_RETRY_DELAY_SECONDS",
    default_attempts: int = 3,
    default_delay_seconds: float = 20.0,
) -> _T:
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


