from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .adapter import GatewayCommandError, GatewayTimeoutError
from .models import DeviceIdentity, WriteOutcome, WriteResult


class ControlGatewayPort(Protocol):
    async def set_properties(
        self, identity: DeviceIdentity, properties: Mapping[int, Mapping[str, Any]]
    ) -> Mapping[str, Any]: ...

    async def get_properties(
        self, identity: DeviceIdentity, epcs: tuple[int, ...]
    ) -> Mapping[str, Any]: ...


class ControlDisabledError(RuntimeError):
    """Write control is not explicitly enabled."""


@dataclass(frozen=True, slots=True)
class VerifiedWriteCommand:
    """A reviewed semantic command definition.

    Concrete instances must be created only from verified RC-307A/ECHONET
    mappings. Raw arbitrary EPC writes are intentionally not exposed.
    """

    name: str
    target: DeviceIdentity
    epc: int
    payload: Mapping[str, Any]
    expected_number: int | float | None = None
    expected_string: str | None = None

    def matches(self, property_value: Mapping[str, Any] | None) -> bool:
        if property_value is None:
            return False
        if self.expected_number is not None:
            return property_value.get("number") == self.expected_number
        if self.expected_string is not None:
            return property_value.get("string") == self.expected_string
        return False


class SafeWriteService:
    """Serializes one write transaction and reconciles uncertain outcomes."""

    def __init__(self, gateway: ControlGatewayPort, *, enabled: bool = False) -> None:
        self._gateway = gateway
        self._enabled = enabled

    async def apply(self, command: VerifiedWriteCommand) -> WriteResult:
        if not self._enabled:
            raise ControlDisabledError("ECHONET writes are disabled")
        if not command.name.strip():
            raise ValueError("command name is required")
        if not 0 <= command.epc <= 0xFF:
            raise ValueError("EPC must be one byte")
        if (command.expected_number is None) == (command.expected_string is None):
            raise ValueError("exactly one read-back expectation is required")

        try:
            await self._gateway.set_properties(
                command.target,
                {command.epc: dict(command.payload)},
            )
        except GatewayCommandError as exc:
            return WriteResult(
                outcome=WriteOutcome.REJECTED,
                command_name=command.name,
                target=command.target,
                epc=command.epc,
                requested_value=self._requested_value(command),
                detail=str(exc),
            )
        except GatewayTimeoutError:
            return await self._reconcile(command, uncertain=True)

        return await self._reconcile(command, uncertain=False)

    async def _reconcile(
        self, command: VerifiedWriteCommand, *, uncertain: bool
    ) -> WriteResult:
        try:
            response = await self._gateway.get_properties(command.target, (command.epc,))
        except (GatewayTimeoutError, GatewayCommandError) as exc:
            return WriteResult(
                outcome=WriteOutcome.UNKNOWN,
                command_name=command.name,
                target=command.target,
                epc=command.epc,
                requested_value=self._requested_value(command),
                detail=f"read-back failed after {'uncertain' if uncertain else 'accepted'} SET: {exc}",
            )

        value = self._extract_property_value(response, command)
        if command.matches(value):
            return WriteResult(
                outcome=WriteOutcome.APPLIED,
                command_name=command.name,
                target=command.target,
                epc=command.epc,
                requested_value=self._requested_value(command),
                readback_value=value,
                detail="applied after reconciliation" if uncertain else None,
            )
        return WriteResult(
            outcome=WriteOutcome.MISMATCH if not uncertain else WriteOutcome.UNKNOWN,
            command_name=command.name,
            target=command.target,
            epc=command.epc,
            requested_value=self._requested_value(command),
            readback_value=value,
            detail="read-back did not match requested state",
        )

    @staticmethod
    def _requested_value(command: VerifiedWriteCommand) -> Any:
        return command.expected_number if command.expected_number is not None else command.expected_string

    @staticmethod
    def _extract_property_value(
        response: Mapping[str, Any], command: VerifiedWriteCommand
    ) -> Mapping[str, Any] | None:
        epc = f"{command.epc:02X}"
        # echonet-list response shapes may be device keyed or property keyed.
        if epc in response and isinstance(response[epc], Mapping):
            return response[epc]
        devices = response.get("devices")
        if isinstance(devices, Mapping):
            device = devices.get(command.target)
            if isinstance(device, Mapping):
                properties = device.get("properties")
                if isinstance(properties, Mapping):
                    value = properties.get(epc)
                    if isinstance(value, Mapping):
                        return value
        properties = response.get("properties")
        if isinstance(properties, Mapping):
            value = properties.get(epc)
            if isinstance(value, Mapping):
                return value
        return None
