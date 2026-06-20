from __future__ import annotations


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
