from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from .models import DeviceIdentity, SystemTopology

PV_CLASS = 0x0279
BATTERY_CLASS = 0x027D


class EchonetError(RuntimeError):
    """Base error exposed by the project-owned ECHONET boundary."""


class TopologyError(EchonetError):
    """The discovered gateway state does not match the required topology."""


class GatewayPort(Protocol):
    async def list_devices(self) -> list[Mapping[str, Any]]: ...

    async def get_properties(
        self, identity: DeviceIdentity, epcs: tuple[int, ...]
    ) -> Mapping[str, Any]: ...

    def discovered_identities(self) -> list[DeviceIdentity]: ...


class EchonetReadService:
    def __init__(self, gateway: GatewayPort) -> None:
        self._gateway = gateway

    async def discover_topology(self) -> SystemTopology:
        await self._gateway.list_devices()
        devices = self._gateway.discovered_identities()
        return SystemTopology(
            pv=self._exactly_one(devices, PV_CLASS, "household solar generation"),
            battery=self._exactly_one(devices, BATTERY_CLASS, "storage battery"),
        )

    async def read_properties(
        self, identity: DeviceIdentity, epcs: Iterable[int]
    ) -> Mapping[str, Any]:
        requested = tuple(dict.fromkeys(epcs))
        if not requested:
            return {}
        for epc in requested:
            if not 0 <= epc <= 0xFF:
                raise ValueError(f"invalid EPC: {epc}")
        return await self._gateway.get_properties(identity, requested)

    @staticmethod
    def _exactly_one(
        devices: Iterable[DeviceIdentity], class_code: int, label: str
    ) -> DeviceIdentity:
        matches = [device for device in devices if device.class_code == class_code]
        if len(matches) != 1:
            raise TopologyError(
                f"expected exactly one {label} ({class_code:04X}:*), found {len(matches)}"
            )
        return matches[0]
