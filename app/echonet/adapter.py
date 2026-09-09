from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from .models import DeviceCapabilities, DeviceIdentity


class PychonetCompatibilityError(RuntimeError):
    """Installed pychonet state/API shape is incompatible with this adapter."""


class PychonetCommunicationError(RuntimeError):
    """A pychonet network operation failed or timed out."""


class PychonetReadAdapter:
    """Read-only adapter. This is the only production boundary that imports pychonet."""

    def __init__(self, listen_address: str = "0.0.0.0", timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        try:
            from pychonet import ECHONETAPIClient
            from pychonet.lib.udpserver import UDPServer
        except ImportError as exc:
            raise PychonetCompatibilityError("pychonet is not installed") from exc

        self._udp = UDPServer()
        loop = asyncio.get_running_loop()
        self._udp.run(listen_address, 3610, loop=loop)
        self._client = ECHONETAPIClient(server=self._udp)
        # pychonet uses 0.1-second ticks.
        self._client.configure(message_timeout=max(1, round(timeout_seconds * 10)))

    async def discover(self, host: str) -> list[DeviceIdentity]:
        try:
            success = await self._client.discover(host)
        except Exception as exc:
            raise PychonetCommunicationError(f"ECHONET discovery failed for {host}") from exc
        if not success:
            raise PychonetCommunicationError(f"ECHONET discovery timed out for {host}")
        return self._identities_from_state(host)

    async def capabilities(self, identity: DeviceIdentity) -> DeviceCapabilities:
        try:
            success = await self._client.getAllPropertyMaps(
                identity.host, identity.eojgc, identity.eojcc, identity.eojci
            )
        except Exception as exc:
            raise PychonetCommunicationError(
                f"property-map read failed for {identity.host} EOJ {identity.eoj}"
            ) from exc
        if not success:
            raise PychonetCommunicationError(
                f"property-map read timed out for {identity.host} EOJ {identity.eoj}"
            )
        state = self._instance_state(identity)
        # ECHONET superclass EPCs: 0x9D STATMAP, 0x9E SETMAP, 0x9F GETMAP.
        return DeviceCapabilities(
            gettable=frozenset(state.get(0x9F, ())),
            settable=frozenset(state.get(0x9E, ())),
            notify=frozenset(state.get(0x9D, ())),
        )

    async def read_properties(
        self, identity: DeviceIdentity, epcs: tuple[int, ...]
    ) -> Mapping[int, Any]:
        try:
            from pychonet import Factory

            device = Factory(
                identity.host,
                self._client,
                identity.eojgc,
                identity.eojcc,
                identity.eojci,
            )
            data = await device.update(list(epcs))
        except Exception as exc:
            raise PychonetCommunicationError(
                f"property read failed for {identity.host} EOJ {identity.eoj}"
            ) from exc
        if data is None or data is False:
            raise PychonetCommunicationError(
                f"property read timed out for {identity.host} EOJ {identity.eoj}"
            )
        if not isinstance(data, Mapping):
            raise PychonetCompatibilityError(
                f"unexpected update result for {identity.host} EOJ {identity.eoj}"
            )
        return dict(data)

    def register_multicast(self, host: str) -> None:
        try:
            self._client.register_multicast(host)
        except Exception as exc:
            raise PychonetCommunicationError(
                f"multicast registration failed for interface reaching {host}"
            ) from exc

    def _identities_from_state(self, host: str) -> list[DeviceIdentity]:
        try:
            instances = self._client._state[host]["instances"]
            result = [
                DeviceIdentity(host, int(gc), int(cc), int(ci))
                for gc, classes in instances.items()
                for cc, instance_map in classes.items()
                for ci in instance_map
            ]
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise PychonetCompatibilityError(
                f"unexpected pychonet discovery state shape for {host}"
            ) from exc
        return result

    def _instance_state(self, identity: DeviceIdentity) -> Mapping[int, Any]:
        try:
            state = self._client._state[identity.host]["instances"][identity.eojgc][
                identity.eojcc
            ][identity.eojci]
        except (AttributeError, KeyError, TypeError) as exc:
            raise PychonetCompatibilityError(
                f"missing pychonet state for {identity.host} EOJ {identity.eoj}"
            ) from exc
        if not isinstance(state, Mapping):
            raise PychonetCompatibilityError(
                f"unexpected pychonet instance state for {identity.host} EOJ {identity.eoj}"
            )
        return state
