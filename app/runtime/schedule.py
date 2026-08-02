from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from app.runtime import forced_charge_monitor
def _hhmm_after_delay(*, timezone_name: str, delay_seconds: int) -> str:
    return forced_charge_monitor.hhmm_after_delay(timezone_name=timezone_name, delay_seconds=delay_seconds)
def _seconds_until_cutoff(*, timezone_name: str, cutoff_hhmm: str) -> int:
    hhmm = cutoff_hhmm.strip()
    if not hhmm or ":" not in hhmm:
        return 0
    hh_text, mm_text = hhmm.split(":", 1)
    hh = int(hh_text)
    mm = int(mm_text)
    now = datetime.now(ZoneInfo(timezone_name))
    cutoff = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return max(0, int((cutoff - now).total_seconds()))


def _parse_hhmm_minutes(value: str, *, default: str) -> int:
    text = value.strip() or default
    if ":" not in text:
        text = default
    hh_text, mm_text = text.split(":", 1)
    try:
        hh = max(0, min(23, int(hh_text)))
        mm = max(0, min(59, int(mm_text)))
    except ValueError:
        hh_text, mm_text = default.split(":", 1)
        hh = max(0, min(23, int(hh_text)))
        mm = max(0, min(59, int(mm_text)))
    return hh * 60 + mm


def _adjust03_target_date(*, now: datetime | None = None) -> str:
    explicit = os.getenv("FORECAST_DATE_OVERRIDE", "").strip()
    if explicit:
        return explicit
    timezone_name = os.getenv("TIMEZONE", "Asia/Tokyo").strip() or "Asia/Tokyo"
    current = now or datetime.now(ZoneInfo(timezone_name))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo(timezone_name))
    else:
        current = current.astimezone(ZoneInfo(timezone_name))
    return current.date().isoformat()


