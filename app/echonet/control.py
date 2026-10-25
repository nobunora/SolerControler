from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from .adapter import GatewayCommandError, GatewayError, GatewayTimeoutError
from .audit import AuditSink, NullAuditSink
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


class SafetyInterlockError(RuntimeError):
    """Current runtime/device state does not permit a write."""


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
    """Fail-closed semantic write transaction owner.

    Writes are serialized per target. A successful gateway SET is never treated
    as physical success until a live GET read-back matches the reviewed command.
    """

    def __init__(
        self,
        gateway: ControlGatewayPort,
        *,
        enabled: bool = False,
        ready_for_write: Callable[[], bool] | None = None,
        audit: AuditSink | None = None,
    ) -> None:
        self._gateway = gateway
        self._enabled = enabled
        self._ready_for_write = ready_for_write or (lambda: True)
        self._audit = audit or NullAuditSink()
        self._target_locks: dict[str, asyncio.Lock] = {}

    async def apply(self, command: VerifiedWriteCommand) -> WriteResult:
        self._validate(command)
        if not self._enabled:
            raise ControlDisabledError("ECHONET writes are disabled")
        if not self._ready_for_write():
            raise SafetyInterlockError("gateway/device state is not fresh and healthy for writes")

        lock = self._target_locks.setdefault(command.target.target, asyncio.Lock())
        async with lock:
            if not self._ready_for_write():
                raise SafetyInterlockError("write readiness was lost before transaction start")
            result = await self._apply_locked(command)
            self._audit.record(result)
            return result

    async def _apply_locked(self, command: VerifiedWriteCommand) -> WriteResult:
        try:
            await self._gateway.set_properties(
                command.target,
                {command.epc: dict(command.payload)},
            )
        except GatewayCommandError as exc:
            return self._result(command, WriteOutcome.REJECTED, detail=str(exc))
        except GatewayTimeoutError:
            return await self._reconcile(command, uncertain=True)
        except GatewayError as exc:
            return self._result(command, WriteOutcome.UNKNOWN, detail=f"SET transport failed: {exc}")
        return await self._reconcile(command, uncertain=False)

    async def _reconcile(
        self, command: VerifiedWriteCommand, *, uncertain: bool
    ) -> WriteResult:
        try:
            response = await self._gateway.get_properties(command.target, (command.epc,))
        except GatewayError as exc:
            return self._result(
                command,
                WriteOutcome.UNKNOWN,
                detail=f"read-back failed after {'uncertain' if uncertain else 'accepted'} SET: {exc}",
            )

        value = self._extract_property_value(response, command)
        if command.matches(value):
            return self._result(
                command,
                WriteOutcome.APPLIED,
                readback_value=value,
                detail="applied after reconciliation" if uncertain else None,
            )
        return self._result(
            command,
            WriteOutcome.UNKNOWN if uncertain else WriteOutcome.MISMATCH,
            readback_value=value,
            detail="read-back did not match requested state",
        )

    def _validate(self, command: VerifiedWriteCommand) -> None:
        if not command.name.strip():
            raise ValueError("command name is required")
        if not 0 <= command.epc <= 0xFF:
            raise ValueError("EPC must be one byte")
        if (command.expected_number is None) == (command.expected_string is None):
            raise ValueError("exactly one read-back expectation is required")
        if not command.payload:
            raise ValueError("verified write payload must not be empty")

    def _result(
        self,
        command: VerifiedWriteCommand,
        outcome: WriteOutcome,
        *,
        readback_value: Any | None = None,
        detail: str | None = None,
    ) -> WriteResult:
        return WriteResult(
            outcome=outcome,
            command_name=command.name,
            target=command.target,
            epc=command.epc,
            requested_value=self._requested_value(command),
            readback_value=readback_value,
            detail=detail,
        )

    @staticmethod
    def _requested_value(command: VerifiedWriteCommand) -> Any:
        return command.expected_number if command.expected_number is not None else command.expected_string

    @staticmethod
    def _extract_property_value(
        response: Mapping[str, Any], command: VerifiedWriteCommand
    ) -> Mapping[str, Any] | None:
        epc = f"{command.epc:02X}"
        direct = response.get(epc)
        if isinstance(direct, Mapping):
            return cast(Mapping[str, Any], direct)

        devices = response.get("devices")
        if isinstance(devices, Mapping):
            device = devices.get(command.target.target)
            if isinstance(device, Mapping):
                properties = device.get("properties")
                if isinstance(properties, Mapping):
                    value = properties.get(epc)
                    if isinstance(value, Mapping):
                        return cast(Mapping[str, Any], value)

        properties = response.get("properties")
        if isinstance(properties, Mapping):
            value = properties.get(epc)
            if isinstance(value, Mapping):
                return cast(Mapping[str, Any], value)
        return None
