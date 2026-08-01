from __future__ import annotations

import math


def validate_soc_percent(value: float, *, raw: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"SOC is not finite: raw={raw!r} value={parsed}")
    if parsed < 0.0 or parsed > 100.0:
        raise ValueError(f"SOC out of range: raw={raw!r} value={parsed}")
    return parsed


class SOCBounds:
    MIN_PERCENT = 0.0
    MAX_PERCENT = 100.0

    @staticmethod
    def clamp(value: float) -> float:
        return max(SOCBounds.MIN_PERCENT, min(SOCBounds.MAX_PERCENT, float(value)))


class TimeConstants:
    HOURS_PER_DAY = 24.0
    MINUTES_PER_HOUR = 60


class FileConstants:
    DEFAULT_CHUNK_SIZE_BYTES = 1024 * 1024


class PercentConstants:
    MIN_PERCENT = 0.0
    MAX_PERCENT = 100.0
