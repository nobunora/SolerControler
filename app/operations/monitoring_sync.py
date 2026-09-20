from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from app.operations.domain import iter_monitoring_rows


MONITORING_FIELDS = (
    "ts",
    "pv_kwh",
    "load_kwh",
    "sell_kwh",
    "buy_kwh",
    "charge_kwh",
    "discharge_kwh",
    "soc_percent",
)


@dataclass(frozen=True)
class MonitoringSyncWindow:
    start_date: date
    end_date: date
    start_ts: str
    end_ts: str
    months: tuple[str, ...]


@dataclass(frozen=True)
class PreparedMonitoringRows:
    rows: tuple[dict[str, Any], ...]
    rows_seen: int
    rows_in_window: int
    duplicate_same: int
    duplicate_conflict: int
    conflict_timestamps: tuple[str, ...]


@dataclass(frozen=True)
class MonitoringChangeSet:
    inserts: tuple[dict[str, Any], ...]
    updates: tuple[dict[str, Any], ...]
    unchanged: int
    rows_seen: int
    rows_in_window: int
    duplicate_same: int
    duplicate_conflict: int
    conflict_timestamps: tuple[str, ...]

    @property
    def changed_rows(self) -> tuple[dict[str, Any], ...]:
        return self.inserts + self.updates

    @property
    def changed_count(self) -> int:
        return len(self.inserts) + len(self.updates)

    @property
    def changed_timestamps(self) -> tuple[str, ...]:
        return tuple(sorted((str(row["ts"]) for row in self.changed_rows)))

    @property
    def calendar_dates(self) -> tuple[str, ...]:
        return tuple(sorted({ts[:10] for ts in self.changed_timestamps}))

    @property
    def dashboard_affected_dates(self) -> tuple[str, ...]:
        affected: set[str] = set()
        for ts in self.changed_timestamps:
            day = ts[:10]
            affected.add(day)
            if len(ts) >= 16 and ts[11:16] >= "23:00":
                affected.add((date.fromisoformat(day) + timedelta(days=1)).isoformat())
        return tuple(sorted(affected))


def monitoring_sync_window(
    now: datetime | None = None,
    *,
    timezone: str = "Asia/Tokyo",
    days: int = 4,
) -> MonitoringSyncWindow:
    if days < 1:
        raise ValueError("days must be >= 1")
    tz = ZoneInfo(timezone)
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    else:
        current = current.astimezone(tz)
    end_date = current.date()
    start_date = end_date - timedelta(days=days - 1)
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date + timedelta(days=1), time.min)
    months: list[str] = []
    cursor = start_date
    while cursor <= end_date:
        month = cursor.strftime("%Y-%m")
        if month not in months:
            months.append(month)
        cursor += timedelta(days=1)
    return MonitoringSyncWindow(
        start_date=start_date,
        end_date=end_date,
        start_ts=start_dt.isoformat(),
        end_ts=end_dt.isoformat(),
        months=tuple(months),
    )


def window_from_ingested_at(
    ingested_at: str,
    *,
    timezone: str = "Asia/Tokyo",
    days: int = 4,
) -> MonitoringSyncWindow:
    value = ingested_at.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    return monitoring_sync_window(parsed, timezone=timezone, days=days)


def meaningful_monitoring_payload(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in MONITORING_FIELDS)


def monitoring_rows_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return meaningful_monitoring_payload(left) == meaningful_monitoring_payload(right)


def _dedupe_rows(
    rows: Iterable[dict[str, Any]],
    *,
    start_ts: str | None,
    end_ts: str | None,
) -> PreparedMonitoringRows:
    rows_seen = 0
    rows_in_window = 0
    duplicate_same = 0
    conflicts: set[str] = set()
    by_ts: dict[str, dict[str, Any]] = {}

    for source in rows:
        rows_seen += 1
        row = dict(source)
        ts = str(row.get("ts", "")).strip()
        if not ts:
            continue
        if start_ts is not None and ts < start_ts:
            continue
        if end_ts is not None and ts >= end_ts:
            continue
        rows_in_window += 1
        if ts in conflicts:
            continue
        previous = by_ts.get(ts)
        if previous is None:
            by_ts[ts] = row
            continue
        if monitoring_rows_equal(previous, row):
            duplicate_same += 1
            continue
        conflicts.add(ts)
        by_ts.pop(ts, None)

    return PreparedMonitoringRows(
        rows=tuple(by_ts[ts] for ts in sorted(by_ts, reverse=True)),
        rows_seen=rows_seen,
        rows_in_window=rows_in_window,
        duplicate_same=duplicate_same,
        duplicate_conflict=len(conflicts),
        conflict_timestamps=tuple(sorted(conflicts)),
    )


def prepare_monitoring_csvs(
    csv_paths: list[Path],
    *,
    window: MonitoringSyncWindow | None,
) -> PreparedMonitoringRows:
    def rows() -> Iterable[dict[str, Any]]:
        for csv_path in csv_paths:
            yield from iter_monitoring_rows(csv_path)

    return _dedupe_rows(
        rows(),
        start_ts=window.start_ts if window is not None else None,
        end_ts=window.end_ts if window is not None else None,
    )


def prepare_monitoring_rows(
    rows: Iterable[dict[str, Any]],
    *,
    window: MonitoringSyncWindow | None,
) -> PreparedMonitoringRows:
    return _dedupe_rows(
        rows,
        start_ts=window.start_ts if window is not None else None,
        end_ts=window.end_ts if window is not None else None,
    )


def classify_monitoring_rows(
    prepared: PreparedMonitoringRows,
    *,
    existing_by_ts: dict[str, dict[str, Any]],
) -> MonitoringChangeSet:
    inserts: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    unchanged = 0
    for row in prepared.rows:
        ts = str(row["ts"])
        existing = existing_by_ts.get(ts)
        if existing is None:
            inserts.append(row)
        elif monitoring_rows_equal(existing, row):
            unchanged += 1
        else:
            updates.append(row)
    return MonitoringChangeSet(
        inserts=tuple(inserts),
        updates=tuple(updates),
        unchanged=unchanged,
        rows_seen=prepared.rows_seen,
        rows_in_window=prepared.rows_in_window,
        duplicate_same=prepared.duplicate_same,
        duplicate_conflict=prepared.duplicate_conflict,
        conflict_timestamps=prepared.conflict_timestamps,
    )


def earliest_changed_month_start(changes: MonitoringChangeSet) -> str | None:
    if not changes.calendar_dates:
        return None
    first = date.fromisoformat(changes.calendar_dates[0])
    return first.replace(day=1).isoformat()
