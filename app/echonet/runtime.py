from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Protocol

from .adapter import GatewayError


class RuntimeState(str, Enum):
    STARTING = "starting"
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    RECOVERING = "recovering"
    STOPPED = "stopped"


class GatewayRuntimePort(Protocol):
    @property
    def connected(self) -> bool: ...

    @property
    def last_message_at(self) -> float | None: ...

    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def list_devices(self): ...


class GatewayRuntime:
    """Owns reconnect and health lifecycle for one echonet-list connection."""

    def __init__(
        self,
        gateway: GatewayRuntimePort,
        *,
        health_interval: float = 10.0,
        stale_after: float = 30.0,
        reconnect_min: float = 1.0,
        reconnect_max: float = 30.0,
    ) -> None:
        if min(health_interval, stale_after, reconnect_min, reconnect_max) <= 0:
            raise ValueError("runtime intervals must be positive")
        if reconnect_min > reconnect_max:
            raise ValueError("reconnect_min must be <= reconnect_max")
        self._gateway = gateway
        self._health_interval = health_interval
        self._stale_after = stale_after
        self._reconnect_min = reconnect_min
        self._reconnect_max = reconnect_max
        self._state = RuntimeState.STOPPED
        self._last_success_at: float | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def healthy_for_write(self) -> bool:
        if self._state is not RuntimeState.ONLINE or not self._gateway.connected:
            return False
        last = self._last_success_at or self._gateway.last_message_at
        return last is not None and (time.monotonic() - last) <= self._stale_after

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="echonet-list-runtime")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._gateway.close()
        self._state = RuntimeState.STOPPED

    async def _run(self) -> None:
        delay = self._reconnect_min
        self._state = RuntimeState.STARTING
        while not self._stop.is_set():
            if not self._gateway.connected:
                self._state = RuntimeState.RECOVERING if self._last_success_at is not None else RuntimeState.STARTING
                try:
                    await self._gateway.connect()
                    await self._gateway.list_devices()
                except GatewayError:
                    self._state = RuntimeState.OFFLINE
                    await self._gateway.close()
                    await asyncio.sleep(delay)
                    delay = min(self._reconnect_max, delay * 2)
                    continue
                self._last_success_at = time.monotonic()
                self._state = RuntimeState.ONLINE
                delay = self._reconnect_min

            try:
                await asyncio.sleep(self._health_interval)
                await self._gateway.list_devices()
                self._last_success_at = time.monotonic()
                self._state = RuntimeState.ONLINE
            except GatewayError:
                self._state = RuntimeState.DEGRADED
                await self._gateway.close()
