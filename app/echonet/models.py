from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    host: str
    eojgc: int
    eojcc: int
    eojci: int

    @property
    def eoj(self) -> str:
        return f"{self.eojgc:02X}{self.eojcc:02X}{self.eojci:02X}"


@dataclass(frozen=True, slots=True)
class DeviceCapabilities:
    gettable: frozenset[int]
    settable: frozenset[int]
    notify: frozenset[int]


@dataclass(frozen=True, slots=True)
class PropertyObservation:
    identity: DeviceIdentity
    epc: int
    value: Any | None
    observed_at: datetime
    source: Literal["poll", "notification", "cache"] = "poll"
    stale: bool = False


@dataclass(frozen=True, slots=True)
class SystemTopology:
    pv: DeviceIdentity
    battery: DeviceIdentity
