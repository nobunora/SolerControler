from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo


class MonitorClock(Protocol):
    def monotonic_seconds(self) -> float: ...

    def now(self, timezone: ZoneInfo) -> datetime: ...

    def sleep(self, seconds: int) -> None: ...


class MonitorDevicePort(Protocol):
    def read_soc(self, csv_paths: list[Path]) -> Any: ...

    def apply_profile(self, *, profile: str, dynamic_forced_profile: bool, label: str) -> None: ...


class MonitorStatusPort(Protocol):
    def persist_stop_reason(
        self, plan_meta: dict[str, Any], reason: str, *, soc_reading: Any | None = None
    ) -> bool: ...

    def persist_schedule(self, **values: Any) -> bool: ...

    def persist_no_charge(self, **values: Any) -> bool: ...
