from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Mapping


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    host: str
    class_code: int
    instance: int

    @property
    def eoj(self) -> str:
        return f"{self.class_code:04X}:{self.instance}"

    @property
    def eoj6(self) -> str:
        return f"{self.class_code:04X}{self.instance:02X}"

    @property
    def target(self) -> str:
        return f"{self.host} {self.eoj}"

    @classmethod
    def from_gateway_device(cls, device: Mapping[str, Any]) -> DeviceIdentity | None:
        host = device.get("ip")
        eoj = device.get("eoj")
        if not isinstance(host, str) or not isinstance(eoj, str) or ":" not in eoj:
            return None
        class_text, instance_text = eoj.split(":", 1)
        try:
            return cls(host=host, class_code=int(class_text, 16), instance=int(instance_text, 10))
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class PropertyObservation:
    identity: DeviceIdentity
    epc: int
    value: Any | None
    observed_at: datetime
    source: Literal["live", "notification", "cache"] = "live"
    stale: bool = False


@dataclass(frozen=True, slots=True)
class SystemTopology:
    pv: DeviceIdentity
    battery: DeviceIdentity


class WriteOutcome(str, Enum):
    APPLIED = "applied"
    REJECTED = "rejected"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class WriteResult:
    outcome: WriteOutcome
    command_name: str
    target: DeviceIdentity
    epc: int
    requested_value: Any
    readback_value: Any | None = None
    detail: str | None = None
