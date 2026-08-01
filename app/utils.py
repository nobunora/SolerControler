from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, overload


CSV_NUMBER_PATTERN = re.compile(r"[-+]?\d+(?:\.\d+)?")
from app.configuration.environment import (
    env,
    env_bool,
    env_float,
    env_float_clamped,
    env_int,
    load_dotenv_if_present,
)


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
