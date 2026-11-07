from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Mapping
from typing import Any

from .models import DeviceIdentity


class GatewayError(RuntimeError):
    """Base error for the echonet-list process boundary."""


class GatewayProtocolError(GatewayError):
    """The gateway returned malformed or incompatible protocol data."""


class GatewayCommandError(GatewayError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class GatewayTimeoutError(GatewayError):
    """No matching command_result arrived before the request timeout."""


class EchonetListGateway:
    """Persistent WebSocket client for echonet-list.

    This class owns only the gateway protocol boundary. It does not decide which
    battery action is safe or desirable.
    """

    def __init__(self, url: str, *, request_timeout: float = 10.0, ssl: Any = None) -> None:
        if request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        self._url = url
        self._request_timeout = request_timeout
        self._ssl = ssl
        self._socket: Any | None = None
        self._receiver_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[Mapping[str, Any]]] = {}
        self._devices: dict[str, Mapping[str, Any]] = {}
        self._state_ready = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._last_message_at: float | None = None
        self._disconnect_error: GatewayError | None = None

    @property
    def connected(self) -> bool:
        return self._socket is not None and self._receiver_task is not None and not self._receiver_task.done()

    @property
    def last_message_at(self) -> float | None:
        """Monotonic time of the most recent valid gateway message."""
        return self._last_message_at

    @property
    def disconnect_error(self) -> GatewayError | None:
        return self._disconnect_error

    async def connect(self) -> None:
        if self.connected:
            return
        await self.close()
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:
            raise GatewayProtocolError("websockets package is not installed") from exc
        try:
            self._socket = await connect(self._url, ssl=self._ssl)
        except Exception as exc:
            raise GatewayError(f"failed to connect to echonet-list: {self._url}") from exc
        self._disconnect_error = None
        self._receiver_task = asyncio.create_task(self._receiver(), name="echonet-list-receiver")
        try:
            await asyncio.wait_for(self._state_ready.wait(), timeout=self._request_timeout)
        except TimeoutError as exc:
            await self.close()
            raise GatewayTimeoutError("initial_state was not received") from exc

    async def close(self) -> None:
        task = self._receiver_task
        self._receiver_task = None
        socket = self._socket
        self._socket = None
        self._state_ready.clear()
        if socket is not None:
            try:
                await socket.close()
            except Exception:
                pass
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._fail_pending(GatewayError("echonet-list connection closed"))

    async def list_devices(self) -> list[Mapping[str, Any]]:
        data = await self.request("list_devices", {"targets": []})
        devices = self._extract_devices(data)
        for device in devices:
            self._cache_device(device)
        return devices

    async def get_properties(
        self, identity: DeviceIdentity, epcs: tuple[int, ...]
    ) -> Mapping[str, Any]:
        data = await self.request(
            "get_properties",
            {"targets": [identity.target], "epcs": [f"{epc:02X}" for epc in epcs]},
        )
        return data

    async def set_properties(
        self, identity: DeviceIdentity, properties: Mapping[int, Mapping[str, Any]]
    ) -> Mapping[str, Any]:
        payload = {
            "target": identity.target,
            "properties": {f"{epc:02X}": dict(value) for epc, value in properties.items()},
        }
        return await self.request("set_properties", payload)

    async def property_description(self, class_code: str) -> Mapping[str, Any]:
        return await self.request("get_property_description", {"classCode": class_code, "lang": "en"})

    async def request(self, message_type: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self.connected or self._socket is None:
            raise GatewayError("echonet-list is not connected")
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Mapping[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        message = {"type": message_type, "payload": dict(payload), "requestId": request_id}
        try:
            async with self._send_lock:
                await self._socket.send(json.dumps(message, separators=(",", ":")))
            return await asyncio.wait_for(future, timeout=self._request_timeout)
        except TimeoutError as exc:
            raise GatewayTimeoutError(f"gateway request timed out: {message_type}") from exc
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError(f"gateway send failed: {message_type}") from exc
        finally:
            self._pending.pop(request_id, None)

    async def _receiver(self) -> None:
        socket = self._socket
        assert socket is not None
        failure: GatewayError | None = None
        try:
            async for raw in socket:
                self._handle_message(raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = GatewayError("echonet-list receive loop stopped")
            failure.__cause__ = exc
        finally:
            if self._socket is socket:
                self._socket = None
                self._receiver_task = None
                self._state_ready.clear()
            if failure is not None:
                self._disconnect_error = failure
                self._fail_pending(failure)

    def _handle_message(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
            message_type = message["type"]
            payload = message["payload"]
            if not isinstance(payload, Mapping):
                raise TypeError("payload must be a mapping")
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise GatewayProtocolError("malformed echonet-list message") from exc

        self._last_message_at = time.monotonic()
        if message_type == "initial_state":
            devices = payload.get("devices", {})
            if not isinstance(devices, Mapping):
                raise GatewayProtocolError("initial_state.devices is not a mapping")
            self._devices = {str(key): value for key, value in devices.items() if isinstance(value, Mapping)}
            self._state_ready.set()
            return
        if message_type in {"device_added", "device_online"}:
            device = payload.get("device")
            if isinstance(device, Mapping):
                self._cache_device(device)
            return
        if message_type == "device_offline":
            target = self._target_from_payload(payload)
            current = self._devices.get(target)
            if current is not None:
                self._devices[target] = {**current, "isOffline": True}
            return
        if message_type == "property_changed":
            target = self._target_from_payload(payload)
            current = self._devices.get(target)
            if current is not None:
                properties = dict(current.get("properties", {}))
                properties[str(payload.get("epc", "")).upper()] = payload.get("value")
                self._devices[target] = {**current, "properties": properties}
            return
        if message_type != "command_result":
            return
        request_id = message.get("requestId")
        if not isinstance(request_id, str):
            raise GatewayProtocolError("command_result without requestId")
        future = self._pending.get(request_id)
        if future is None or future.done():
            return
        if payload.get("success") is True:
            data = payload.get("data")
            future.set_result(data if isinstance(data, Mapping) else {})
            return
        error = payload.get("error", {})
        future.set_exception(
            GatewayCommandError(str(error.get("code", "UNKNOWN")), str(error.get("message", "gateway command failed")))
        )

    def discovered_identities(self) -> list[DeviceIdentity]:
        result: list[DeviceIdentity] = []
        for device in self._devices.values():
            identity = DeviceIdentity.from_gateway_device(device)
            if identity is not None:
                result.append(identity)
        return result

    def cached_device(self, identity: DeviceIdentity) -> Mapping[str, Any] | None:
        return self._devices.get(identity.target)

    def _fail_pending(self, error: GatewayError) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    def _cache_device(self, device: Mapping[str, Any]) -> None:
        identity = DeviceIdentity.from_gateway_device(device)
        if identity is not None:
            self._devices[identity.target] = dict(device)

    @staticmethod
    def _target_from_payload(payload: Mapping[str, Any]) -> str:
        ip = payload.get("ip")
        eoj = payload.get("eoj")
        if not isinstance(ip, str) or not isinstance(eoj, str):
            return ""
        return f"{ip} {eoj}"

    @staticmethod
    def _extract_devices(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        raw = data.get("devices", data)
        if isinstance(raw, Mapping):
            return [value for value in raw.values() if isinstance(value, Mapping)]
        if isinstance(raw, list):
            return [value for value in raw if isinstance(value, Mapping)]
        raise GatewayProtocolError("list_devices returned an unsupported data shape")
