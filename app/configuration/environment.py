from __future__ import annotations

import os
from pathlib import Path


TRUE_VALUES = {"1", "true", "yes", "on", "y"}
FALSE_VALUES = {"0", "false", "no", "off", "n"}


def load_dotenv_if_present(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and (
            (value[0] == '"' and value[-1] == '"')
            or (value[0] == "'" and value[-1] == "'")
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env(name: str, *, required: bool = False, default: str | None = None) -> str:
    value = os.getenv(name)
    if value is not None:
        return value
    if required:
        raise ValueError(f"Missing required environment variable: {name}")
    return "" if default is None else default


def env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    text = raw.strip().lower()
    if not text:
        return default
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return default


def env_int(name: str, *, default: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def env_float(name: str, *, default: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def env_float_clamped(
    name: str,
    default: float,
    *,
    min_val: float = 0.0,
    max_val: float = 100.0,
) -> float:
    try:
        value = env_float(name, default=default)
    except ValueError:
        value = default
    return max(min_val, min(max_val, value))

