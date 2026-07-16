from __future__ import annotations

from dataclasses import dataclass
from datetime import time


def parse_hhmm(value: str, *, name: str = "time") -> time:
    text = value.strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be HH:MM but got: {value}") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"{name} must be HH:MM but got: {value}")
    return time(hour=hour, minute=minute)


def minute_of_day(value: time) -> int:
    return value.hour * 60 + value.minute


@dataclass(frozen=True)
class DailyWindow:
    start: time
    end: time

    def contains(self, value: time) -> bool:
        start = minute_of_day(self.start)
        end = minute_of_day(self.end)
        current = minute_of_day(value)
        if start == end:
            return True
        if start < end:
            return start <= current < end
        return current >= start or current < end

