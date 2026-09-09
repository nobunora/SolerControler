from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from .models import DeviceCapabilities, DeviceIdentity, SystemTopology

PV_CLASS = (0x02, 0x79)
BATTERY_CLASS = (0x02, 0x7D)


class EchonetError(RuntimeError):
    """Base error exposed by the project-owned ECHONET boundary."""


class TopologyError(EchonetError):
    """The discovered node does not match the required topology."""


class UnsupportedPropertyError(EchonetError):
    """A requested EPC is not advertised as gettable by the device."""


class ReadAdapter(Protocol):
    async def discover(self, host: str) -> list[DeviceIdentity]: ...

    async def capabilities(self, identity: DeviceIdentity) -> DeviceCapabilities: ...

    async def read_properties(
        self, identity: DeviceIdentity, epcs: tuple[int, ...]
    ) -> Mapping[int, Any]: ...


class EchonetReadService:
    def __init__(self, adapter: ReadAdapter) -> None:
        self._adapter = adapter

    async def discover_topology(self, host: str) -> SystemTopology:
        devices = await self._adapter.discover(host)
        return SystemTopology(
            pv=self._exactly_one(devices, PV_CLASS, "household solar generation"),
            battery=self._exactly_one(devices, BATTERY_CLASS, "storage battery"),
        )

    async def capabilities(self, identity: DeviceIdentity) -> DeviceCapabilities:
        return await self._adapter.capabilities(identity)

    async def read_supported(
        self, identity: DeviceIdentity, epcs: Iterable[int]
    ) -> Mapping[int, Any]:
        requested = tuple(dict.fromkeys(epcs))
        caps = await self._adapter.capabilities(identity)
        unsupported = set(requested).difference(caps.gettable)
        if unsupported:
            formatted = ", ".join(f"0x{epc:02X}" for epc in sorted(unsupported))
            raise UnsupportedPropertyError(
                f"{identity.host} EOJ {identity.eoj}: EPC(s) not gettable: {formatted}"
            )
        if not requested:
            return {}
        return await self._adapter.read_properties(identity, requested)

    @staticmethod
    def _exactly_one(
        devices: Iterable[DeviceIdentity], device_class: tuple[int, int], label: str
    ) -> DeviceIdentity:
        matches = [
            device
            for device in devices
            if (device.eojgc, device.eojcc) == device_class
        ]
        if len(matches) != 1:
            raise TopologyError(
                f"expected exactly one {label} ({device_class[0]:02X}{device_class[1]:02X}xx), "
                f"found {len(matches)}"
            )
        return matches[0]
