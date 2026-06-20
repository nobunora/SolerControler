from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any, overload


TRUE_VALUES = {"1", "true", "yes", "on", "y"}
FALSE_VALUES = {"0", "false", "no", "off", "n"}
CSV_NUMBER_PATTERN = re.compile(r"[-+]?\d+(?:\.\d+)?")


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


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(str(value).strip() if isinstance(value, str) else value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def to_int(value: Any) -> int | None:
    number = to_float(value)
    if number is None:
        return None
    return int(number)


def clamp_percent(value: float, *, min_val: float = 0.0, max_val: float = 100.0) -> float:
    return max(min_val, min(max_val, float(value)))


@overload
def parse_csv_float(value: Any, *, default: None) -> float | None:
    ...


@overload
def parse_csv_float(value: Any, *, default: float = 0.0) -> float:
    ...


def parse_csv_float(value: Any, *, default: float | None = 0.0) -> float | None:
    if isinstance(value, str):
        match = CSV_NUMBER_PATTERN.search(value.replace(",", ""))
        parsed = float(match.group(0)) if match else None
    else:
        parsed = to_float(value)
    return default if parsed is None else parsed
