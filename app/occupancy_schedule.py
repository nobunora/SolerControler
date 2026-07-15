from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.consumption_forecast import ConsumptionForecast
from app.utils import env_bool, to_float


OCCUPANCY_SCHEDULE_TAB = "occupancy_schedule"
OCCUPANCY_SCHEDULE_HEADERS = [
    "enabled",
    "start_date",
    "end_date",
    "status",
    "occupancy_factor",
    "morning_load_override_kwh",
    "daytime_load_override_kwh",
    "standby_floor_morning_kwh",
    "standby_floor_daytime_kwh",
    "include_in_training",
    "reason",
    "note",
]


@dataclass(frozen=True)
class OccupancyScheduleEvent:
    enabled: bool
    start_date: date
    end_date: date
    status: str
    occupancy_factor: float | None = None
    morning_load_override_kwh: float | None = None
    daytime_load_override_kwh: float | None = None
    standby_floor_morning_kwh: float | None = None
    standby_floor_daytime_kwh: float | None = None
    include_in_training: bool = False
    reason: str = ""
    note: str = ""
    row_number: int | None = None
    source: str = ""


@dataclass(frozen=True)
class OccupancyAdjustment:
    event: OccupancyScheduleEvent
    original_morning_load_kwh: float
    original_daytime_load_kwh: float
    adjusted_morning_load_kwh: float
    adjusted_daytime_load_kwh: float
    method: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["event"]["start_date"] = self.event.start_date.isoformat()
        payload["event"]["end_date"] = self.event.end_date.isoformat()
        return payload

def _to_bool(value: Any, default: bool) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on", "有効"}:
        return True
    if text in {"0", "false", "no", "n", "off", "無効"}:
        return False
    return default


def _to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if "T" in text:
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_header(value: Any) -> str:
    return str(value or "").strip().lower()


def _event_from_mapping(row: Mapping[str, Any], *, row_number: int | None = None, source: str = "") -> OccupancyScheduleEvent | None:
    start = _to_date(row.get("start_date"))
    if start is None:
        return None
    end = _to_date(row.get("end_date")) or start
    if end < start:
        start, end = end, start
    status = str(row.get("status") or "away").strip().lower() or "away"
    return OccupancyScheduleEvent(
        enabled=_to_bool(row.get("enabled"), True),
        start_date=start,
        end_date=end,
        status=status,
        occupancy_factor=to_float(row.get("occupancy_factor")),
        morning_load_override_kwh=to_float(row.get("morning_load_override_kwh")),
        daytime_load_override_kwh=to_float(row.get("daytime_load_override_kwh")),
        standby_floor_morning_kwh=to_float(row.get("standby_floor_morning_kwh")),
        standby_floor_daytime_kwh=to_float(row.get("standby_floor_daytime_kwh")),
        include_in_training=_to_bool(row.get("include_in_training"), False),
        reason=str(row.get("reason") or "").strip(),
        note=str(row.get("note") or "").strip(),
        row_number=row_number,
        source=source,
    )


def events_from_values(values: list[list[Any]], *, source: str = "") -> list[OccupancyScheduleEvent]:
    if not values:
        return []
    header = [_normalize_header(v) for v in values[0]]
    events: list[OccupancyScheduleEvent] = []
    for idx, raw_row in enumerate(values[1:], start=2):
        if not any(str(cell or "").strip() for cell in raw_row):
            continue
        row = {header[col]: raw_row[col] if col < len(raw_row) else "" for col in range(len(header))}
        event = _event_from_mapping(row, row_number=idx, source=source)
        if event is not None:
            events.append(event)
    return events


def _google_sheets_service() -> Any:
    import google.auth
    from googleapiclient.discovery import build

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def load_occupancy_events_from_sheet(
    *,
    spreadsheet_id: str,
    tab: str = OCCUPANCY_SCHEDULE_TAB,
) -> list[OccupancyScheduleEvent]:
    if not spreadsheet_id:
        return []
    try:
        sheets = _google_sheets_service()
        result = (
            sheets.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"{tab}!A:Z")
            .execute()
        )
    except Exception as exc:
        print(f"[occupancy_schedule] read skipped/failed: {exc}", flush=True)
        return []
    return events_from_values(result.get("values", []), source=f"sheet:{tab}")


def load_occupancy_events_from_path(path: Path) -> list[OccupancyScheduleEvent]:
    if not path.exists():
        return []
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else raw.get("events", [])
        events: list[OccupancyScheduleEvent] = []
        for idx, row in enumerate(items, start=1):
            if isinstance(row, Mapping):
                event = _event_from_mapping(row, row_number=idx, source=str(path))
                if event is not None:
                    events.append(event)
        return events

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        events = []
        for idx, row in enumerate(reader, start=2):
            event = _event_from_mapping(row, row_number=idx, source=str(path))
            if event is not None:
                events.append(event)
        return events


def load_occupancy_events_from_env() -> list[OccupancyScheduleEvent]:
    if not env_bool("OCCUPANCY_SCHEDULE_ENABLED", default=True):
        return []
    path_raw = os.getenv("OCCUPANCY_SCHEDULE_PATH", "").strip()
    if path_raw:
        return load_occupancy_events_from_path(Path(path_raw))
    spreadsheet_id = os.getenv("OCCUPANCY_SCHEDULE_SPREADSHEET_ID", "").strip()
    if not spreadsheet_id:
        spreadsheet_id = os.getenv("SHEETS_SPREADSHEET_ID", "").strip()
    tab = os.getenv("OCCUPANCY_SCHEDULE_TAB", OCCUPANCY_SCHEDULE_TAB).strip() or OCCUPANCY_SCHEDULE_TAB
    return load_occupancy_events_from_sheet(spreadsheet_id=spreadsheet_id, tab=tab)


def find_event_for_date(events: Iterable[OccupancyScheduleEvent], target_date: date) -> OccupancyScheduleEvent | None:
    matches = [
        event
        for event in events
        if event.enabled and event.start_date <= target_date <= event.end_date and event.status != "normal"
    ]
    if not matches:
        return None
    return matches[-1]


def should_include_training_date(events: Iterable[OccupancyScheduleEvent], target_date: date) -> bool:
    for event in events:
        if event.enabled and event.start_date <= target_date <= event.end_date and not event.include_in_training:
            return False
    return True


def filter_training_load_rows(
    load_rows: Iterable[Mapping[str, Any]],
    events: Iterable[OccupancyScheduleEvent],
) -> list[dict[str, Any]]:
    event_list = list(events)
    out: list[dict[str, Any]] = []
    for row in load_rows:
        dt_value = row.get("datetime", row.get("dt"))
        row_date = _to_date(dt_value)
        if row_date is not None and not should_include_training_date(event_list, row_date):
            continue
        out.append(dict(row))
    return out


def _event_factor(event: OccupancyScheduleEvent) -> float:
    if event.occupancy_factor is not None:
        return max(0.0, event.occupancy_factor)
    if event.status in {"away", "travel", "business_trip", "absent"}:
        try:
            return max(0.0, float(os.getenv("OCCUPANCY_AWAY_DEFAULT_FACTOR", "0.25")))
        except ValueError:
            return 0.25
    return 1.0


def _adjust_value(*, original: float, override: float | None, factor: float, floor: float | None) -> tuple[float, str]:
    if override is not None:
        return max(0.0, override), "override"
    adjusted = max(0.0, original * factor)
    if floor is not None:
        adjusted = max(max(0.0, floor), adjusted)
    return adjusted, "factor"


def apply_occupancy_event(
    forecast: ConsumptionForecast,
    event: OccupancyScheduleEvent | None,
) -> tuple[ConsumptionForecast, OccupancyAdjustment | None]:
    if event is None:
        return forecast, None

    factor = _event_factor(event)
    morning, morning_method = _adjust_value(
        original=forecast.morning_load_kwh,
        override=event.morning_load_override_kwh,
        factor=factor,
        floor=event.standby_floor_morning_kwh,
    )
    daytime, daytime_method = _adjust_value(
        original=forecast.daytime_load_kwh,
        override=event.daytime_load_override_kwh,
        factor=factor,
        floor=event.standby_floor_daytime_kwh,
    )
    method = "override" if "override" in {morning_method, daytime_method} else "factor"
    adjusted = ConsumptionForecast(
        target_date=forecast.target_date,
        morning_load_kwh=morning,
        daytime_load_kwh=daytime,
        source=f"{forecast.source}+occupancy_{event.status}",
        sample_count=forecast.sample_count,
        features=[*forecast.features, "occupancy_status", "occupancy_factor"],
    )
    return adjusted, OccupancyAdjustment(
        event=event,
        original_morning_load_kwh=forecast.morning_load_kwh,
        original_daytime_load_kwh=forecast.daytime_load_kwh,
        adjusted_morning_load_kwh=morning,
        adjusted_daytime_load_kwh=daytime,
        method=method,
    )


def apply_occupancy_schedule(
    forecast: ConsumptionForecast,
    events: Iterable[OccupancyScheduleEvent],
) -> tuple[ConsumptionForecast, OccupancyAdjustment | None]:
    event = find_event_for_date(events, forecast.target_date)
    return apply_occupancy_event(forecast, event)


__all__ = [
    "OCCUPANCY_SCHEDULE_HEADERS",
    "OCCUPANCY_SCHEDULE_TAB",
    "OccupancyAdjustment",
    "OccupancyScheduleEvent",
    "apply_occupancy_event",
    "apply_occupancy_schedule",
    "events_from_values",
    "filter_training_load_rows",
    "find_event_for_date",
    "load_occupancy_events_from_env",
    "load_occupancy_events_from_path",
    "load_occupancy_events_from_sheet",
    "should_include_training_date",
]
