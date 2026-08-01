from __future__ import annotations

from app.configuration.environment import (
    env,
    env_bool,
    env_float,
    env_float_clamped,
    env_int,
    load_dotenv_if_present,
)
from app.parsing.numbers import parse_csv_float, to_float, to_int



def clamp_percent(value: float, *, min_val: float = 0.0, max_val: float = 100.0) -> float:
    return max(min_val, min(max_val, float(value)))
