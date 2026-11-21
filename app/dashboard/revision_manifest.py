"""Firestore-shaped, read-only revision queries over one coherent manifest.

Only the metadata query subset used by snapshots is supported. No operational
values, writes or fallback database reads belong to this adapter.
"""
from __future__ import annotations

from copy import deepcopy
from calendar import timegm
from dataclasses import dataclass, replace
from datetime import datetime
from threading import Lock
from typing import Any, Callable, Iterator

from google.api_core.datetime_helpers import DatetimeWithNanoseconds


# The SDK parser lacks a typed signature; retain its native nanosecond precision.
parse_timestamp: Callable[[str], datetime] = DatetimeWithNanoseconds.from_rfc3339


def timestamp_key(value: str) -> tuple[int, int]:
    parsed = parse_timestamp(value)
    return timegm(parsed.utctimetuple()), getattr(parsed, "nanosecond", 0) or parsed.microsecond * 1000


def wire_timestamp(value: str) -> dict[str, int]:
    seconds, nanos = timestamp_key(value)
    return {"seconds": seconds, "nanos": nanos}


@dataclass(frozen=True)
class RevisionDocument:
    id: str
    value: dict[str, Any] | None
    update_time: datetime | None
    read_time: datetime
    owner: ManifestClient

    @property
    def exists(self) -> bool:
        return self.value is not None

    def to_dict(self) -> dict[str, Any] | None:
        return deepcopy(self.value)

    def get(self, **_: Any) -> RevisionDocument:
        self.owner.count(1)
        return self


@dataclass(frozen=True)
class ManifestQuery:
    client: ManifestClient
    name: str
    filters: tuple[tuple[str, str, str], ...] = ()
    orders: tuple[str, ...] = ()
    cap: int | None = None
    tail: bool = False
    after: tuple[str, ...] | None = None

    def select(self, _: list[str]) -> ManifestQuery:
        return self

    def where(self, field: str, operator: str, value: str) -> ManifestQuery:
        if operator not in (">=", "<=", "<", "=="):
            raise ValueError("unsupported revision manifest filter")
        return replace(self, filters=(*self.filters, (field, operator, value)))

    def order_by(self, field: str) -> ManifestQuery:
        return replace(self, orders=(*self.orders, field))

    def limit(self, count: int) -> ManifestQuery:
        return replace(self, cap=count, tail=False)

    def limit_to_last(self, count: int) -> ManifestQuery:
        return replace(self, cap=count, tail=True)

    def key(self, row: RevisionDocument) -> tuple[str, ...]:
        orders = self.orders or ("__name__",)
        if "__name__" not in orders:
            orders = (*orders, "__name__")
        return tuple(row.id if field == "__name__" else str((row.value or {}).get(field, ""))
                     for field in orders)

    def start_after(self, row: RevisionDocument) -> ManifestQuery:
        return replace(self, after=self.key(row))

    def document(self, key: str) -> RevisionDocument:
        return self.client.rows[self.name].get(key) or RevisionDocument(key, None, None, self.client.read_time, self.client)

    def get(self, **_: Any) -> list[RevisionDocument]:
        rows = []
        for row in self.client.rows[self.name].values():
            if any(field != "__name__" and field not in (row.value or {}) for field in self.orders):
                continue
            accepted = True
            for field, operator, expected in self.filters:
                value = str((row.value or {}).get(field, ""))
                if not {">=": value >= expected, "<=": value <= expected,
                        "<": value < expected, "==": value == expected}[operator]:
                    accepted = False
                    break
            if accepted:
                rows.append(row)
        rows.sort(key=self.key)
        if self.after is not None:
            rows = [row for row in rows if self.key(row) > self.after]
        if self.cap is not None:
            rows = rows[-self.cap:] if self.tail else rows[:self.cap]
        self.client.count(len(rows))
        return rows

    def stream(self, **kwargs: Any) -> Iterator[RevisionDocument]:
        return iter(self.get(**kwargs))


class ManifestClient:
    def __init__(self, rows: dict[str, dict[str, Any]], read_time: str):
        self.read_time = parse_timestamp(read_time)
        self.documents_read = 0
        self._lock = Lock()
        self.rows = {name: {key: RevisionDocument(key, row["fields"],
                     parse_timestamp(row["updated"]), self.read_time, self)
                     for key, row in values.items()} for name, values in rows.items()}

    def count(self, count: int) -> None:
        with self._lock:
            self.documents_read += count

    def collection(self, name: str) -> ManifestQuery:
        if name not in self.rows:
            raise ValueError("revision manifest source is unavailable")
        return ManifestQuery(self, name)

    def get_all(self, refs: list[RevisionDocument], **_: Any) -> Iterator[RevisionDocument]:
        self.count(len(refs))
        return iter(refs)
