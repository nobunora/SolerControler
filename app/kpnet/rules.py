from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TypedDict
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger(__name__)
class NightWindowContract(TypedDict):
    configured_window_start: str
    configured_window_end: str
    logical_window_duration_minutes: int
    logical_window_crosses_midnight: bool


def _parse_hhmm(value: str, *, name: str) -> tuple[int, int]:
    match = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", value)
    if not match:
        raise RuntimeError(f"{name} は HH:MM 形式で指定してください: {value}")
    hh = int(match.group(1))
    mm = int(match.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise RuntimeError(f"{name} の値が不正です: {value}")
    return hh, mm


def _minutes_to_hm(total_minutes: int) -> tuple[int, int]:
    normalized = total_minutes % (24 * 60)
    return normalized // 60, normalized % 60


def _in_time_window(minute_of_day: int, start_minute: int, end_minute: int) -> bool:
    if start_minute == end_minute:
        return True
    if start_minute < end_minute:
        return start_minute <= minute_of_day < end_minute
    return minute_of_day >= start_minute or minute_of_day < end_minute


def _night_window_contract(start: str, end: str) -> NightWindowContract:
    start_h, start_m = _parse_hhmm(start, name="KP_NIGHT_CHARGE_WINDOW_START")
    end_h, end_m = _parse_hhmm(end, name="KP_NIGHT_CHARGE_WINDOW_END")
    start_minute = start_h * 60 + start_m
    end_minute = end_h * 60 + end_m
    crosses_midnight = start_minute >= end_minute
    duration_minutes = (
        24 * 60
        if start_minute == end_minute
        else (end_minute - start_minute) % (24 * 60)
    )
    return {
        "configured_window_start": f"{start_h:02d}:{start_m:02d}",
        "configured_window_end": f"{end_h:02d}:{end_m:02d}",
        "logical_window_duration_minutes": duration_minutes,
        "logical_window_crosses_midnight": crosses_midnight,
    }


def _now_in_timezone(timezone_name: str) -> datetime:
    tz_name = timezone_name.strip() or "Asia/Tokyo"
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        LOGGER.warning("Invalid TIMEZONE=%s. Fallback to local system timezone.", tz_name)
        return datetime.now()


def _is_night_window_now(
    *,
    timezone_name: str,
    night_window_start: tuple[int, int],
    night_window_end: tuple[int, int],
) -> bool:
    now = _now_in_timezone(timezone_name)
    minute_of_day = now.hour * 60 + now.minute
    start_minute = night_window_start[0] * 60 + night_window_start[1]
    end_minute = night_window_end[0] * 60 + night_window_end[1]
    return _in_time_window(minute_of_day, start_minute, end_minute)


