from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine, Mapping
from typing import Any, TypeVar

from app.echonet.adapter import GatewayError
from app.echonet.runtime import GatewayRuntime, RuntimeState

T = TypeVar("T")


class FakeRuntimeGateway:
    def __init__(self) -> None:
        self._connected: bool = False
        self._last_message_at: float | None = None
        self.connect_failures: int = 0
        self.list_failures: int = 0
        self.connect_calls: int = 0
        self.close_calls: int = 0
        self.list_calls: int = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_message_at(self) -> float | None:
        return self._last_message_at

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_failures:
            self.connect_failures -= 1
            raise GatewayError("connect failed")
        self._connected = True
        self._last_message_at = time.monotonic()

    async def close(self) -> None:
        self.close_calls += 1
        self._connected = False

    async def list_devices(self) -> list[Mapping[str, Any]]:
        self.list_calls += 1
        if self.list_failures:
            self.list_failures -= 1
            raise GatewayError("health failed")
        self._last_message_at = time.monotonic()
        return []


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def test_runtime_connects_and_becomes_write_healthy() -> None:
    async def scenario() -> tuple[FakeRuntimeGateway, bool, RuntimeState, RuntimeState]:
        gateway = FakeRuntimeGateway()
        runtime = GatewayRuntime(
            gateway,
            health_interval=0.01,
            stale_after=0.1,
            reconnect_min=0.01,
            reconnect_max=0.02,
        )
        await runtime.start()
        await asyncio.sleep(0.03)
        healthy = runtime.healthy_for_write
        state = runtime.state
        await runtime.stop()
        return gateway, healthy, state, runtime.state

    gateway, healthy, state, final_state = run(scenario())
    assert gateway.connect_calls >= 1
    assert healthy is True
    assert state is RuntimeState.ONLINE
    assert final_state is RuntimeState.STOPPED


def test_runtime_recovers_after_initial_connect_failure() -> None:
    async def scenario() -> tuple[int, RuntimeState, bool]:
        gateway = FakeRuntimeGateway()
        gateway.connect_failures = 1
        runtime = GatewayRuntime(
            gateway,
            health_interval=0.01,
            stale_after=0.1,
            reconnect_min=0.01,
            reconnect_max=0.02,
        )
        await runtime.start()
        await asyncio.sleep(0.05)
        result = (gateway.connect_calls, runtime.state, runtime.healthy_for_write)
        await runtime.stop()
        return result

    calls, state, healthy = run(scenario())
    assert calls >= 2
    assert state is RuntimeState.ONLINE
    assert healthy is True


def test_runtime_closes_and_reconnects_after_health_failure() -> None:
    async def scenario() -> tuple[int, int, RuntimeState]:
        gateway = FakeRuntimeGateway()
        gateway.list_failures = 1
        runtime = GatewayRuntime(
            gateway,
            health_interval=0.01,
            stale_after=0.1,
            reconnect_min=0.01,
            reconnect_max=0.02,
        )
        await runtime.start()
        await asyncio.sleep(0.06)
        result = (gateway.connect_calls, gateway.close_calls, runtime.state)
        await runtime.stop()
        return result

    connect_calls, close_calls, state = run(scenario())
    assert connect_calls >= 2
    assert close_calls >= 1
    assert state is RuntimeState.ONLINE
