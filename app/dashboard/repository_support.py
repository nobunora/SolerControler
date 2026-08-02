"""Small backend-neutral conversion helpers for dashboard repositories."""

from __future__ import annotations

from datetime import date
from typing import Any


def rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def to_date_or_none(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def pick_min_max_dates(values: list[str | None]) -> tuple[str | None, str | None]:
    dates = [value for value in values if value]
    return (min(dates), max(dates)) if dates else (None, None)
