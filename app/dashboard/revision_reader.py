"""Bounded, snapshot-consistent retries for read-only publication checks."""
from __future__ import annotations

from datetime import datetime
import json
import time
from typing import Any, Callable

from google.api_core.exceptions import DeadlineExceeded, InternalServerError, ServiceUnavailable, TooManyRequests
from google.api_core.retry import Retry

PAGE_SIZE = 512


def _no_sdk_retry(_: Exception) -> bool:
    return False


class RevisionReader:
    """Only complete reads escape; one owner controls the shared retry budget."""

    def __init__(self, client: Any, timeout: Callable[[], float], snapshot: RevisionSnapshot | None = None):
        self.timeout = timeout
        self.retry = Retry(predicate=_no_sdk_retry)
        if snapshot is not None and snapshot.read_time is not None:
            self.read_time = snapshot.read_time
            return
        anchor = self.run(lambda: client.collection("night_charge_plans").document("latest").get(
            field_paths=["date"], timeout=timeout(), retry=self.retry))
        stamp = anchor.read_time
        if not isinstance(stamp, datetime) or stamp.utcoffset() is None:
            raise ValueError("dashboard source read timestamp is unavailable")
        # RunQuery accepts microsecond precision; keep the read session outside
        # the semantic revision digest so identical sources retain identical hashes.
        self.read_time = datetime(stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute,
                                  stamp.second, stamp.microsecond, tzinfo=stamp.tzinfo)
        if snapshot is not None:
            snapshot.read_time = self.read_time

    def run(self, read: Callable[[], Any]) -> Any:
        for attempt in range(3):
            self.timeout()
            try:
                return read()
            except (DeadlineExceeded, ServiceUnavailable, InternalServerError, TooManyRequests) as error:
                print(json.dumps({"event": "dashboard_revision_read_retry", "attempt": attempt + 1,
                                  "error_type": type(error).__name__}), flush=True)
                if attempt == 2:
                    raise
                time.sleep(min(0.1 * (2 ** attempt), self.timeout()))
        raise AssertionError("unreachable revision retry")

    def query(self, query: Any, *, last: bool = False) -> list[Any]:
        def read() -> list[Any]:
            method = query.get if last else query.stream
            # A partial iterator is discarded on error. Its cursor is never committed.
            return list(method(timeout=self.timeout(), retry=self.retry, read_time=self.read_time))
        return list(self.run(read))

    def documents(self, client: Any, refs: list[Any], fields: list[str]) -> list[Any]:
        return list(self.run(lambda: list(client.get_all(refs, field_paths=fields, timeout=self.timeout(),
                                                       retry=self.retry, read_time=self.read_time))))


class RevisionSnapshot:
    """One in-memory read_time for adjacent views, never a cross-stage cache.

    Only fully validated source ranges are retained. A narrower view can reuse
    them; a wider range still reads Firestore at the same native read_time.
    """

    def __init__(self) -> None:
        self.read_time: datetime | None = None
        self.source_client: Any = None
        self.ranges: dict[tuple[str, str, bool], tuple[str, str, list[Any]]] = {}

    def find(self, name: str, field: str, start: str, end: str, *, inclusive: bool = True) -> list[list[Any]] | None:
        saved = self.ranges.get((name, field, inclusive))
        if saved is None or start < saved[0] or end > saved[1]:
            return None
        rows = []
        for row in saved[2]:
            value = str((row.to_dict() or {}).get(field, ""))
            if start <= value and (value < end or (inclusive and value == end)):
                rows.append(row)
        return [rows[index:index + PAGE_SIZE] for index in range(0, len(rows), PAGE_SIZE)] or [[]]

    def remember(self, name: str, field: str, start: str, end: str, pages: list[list[Any]], *, inclusive: bool = True) -> None:
        key = (name, field, inclusive)
        saved = self.ranges.get(key)
        if saved is None or (start <= saved[0] and end >= saved[1]):
            self.ranges[key] = (start, end, [row for page in pages for row in page])
