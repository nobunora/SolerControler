"""Compatibility exports for shared time-window rules."""

from app.domain.time_windows import DailyWindow, minute_of_day, parse_hhmm

__all__ = ["DailyWindow", "minute_of_day", "parse_hhmm"]
