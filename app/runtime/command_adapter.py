from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Any, Callable, Iterable, TypeVar, cast

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


def _run(
    command: Iterable[str],
    env_updates: dict[str, str] | None = None,
    *,
    timeout_seconds: float | None = None,
    deadline_monotonic: float | None = None,
) -> None:
    env = os.environ.copy()
    if env_updates:
        env.update(env_updates)
    cmd = list(command)
    print(
        f"[cloud_job_runner] run: {' '.join(cmd)} env_updates={_mask_env_updates(env_updates)}",
        flush=True,
    )
    if deadline_monotonic is not None:
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("command deadline expired before start")
        timeout_seconds = min(timeout_seconds or remaining, remaining)
    popen_kwargs: dict[str, Any] = {"env": env}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(cmd, **cast(Any, popen_kwargs))
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False)
        else:
            getattr(os, "killpg")(process.pid, getattr(signal, "SIGKILL"))
        process.wait()
        raise TimeoutError(f"command deadline expired: {' '.join(cmd)}") from error
    if return_code != 0:
        raise RuntimeError(f"Command failed (rc={return_code}): {' '.join(cmd)}")


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
    deadline_monotonic: float | None = None,
) -> _T:
    attempts = _env_int(attempts_env, default_attempts, min_value=1)
    delay_seconds = _env_float(delay_env, default_delay_seconds, min_value=0.0)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise TimeoutError(f"{label} deadline expired before attempt {attempt}")
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
            sleep_seconds = delay_seconds
            if deadline_monotonic is not None:
                sleep_seconds = min(sleep_seconds, max(0.0, deadline_monotonic - time.monotonic()))
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_exc}") from last_exc


